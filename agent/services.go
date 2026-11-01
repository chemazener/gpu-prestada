package main

// Servicios además de Ollama (desde la 1.12.0): el servidor puede pedir a un PC que instale y
// mantenga en marcha Whisper (whisper.cpp, transcripción) y ComfyUI (imágenes para Bicho Magic).
// Todo vive en %LOCALAPPDATA%\GPUPrestada\svc\, solo escucha en 127.0.0.1 y el servidor le llega
// por el mismo camino que a Ollama: /_svc/whisper/... y /_svc/comfy/... (nodeHandler).
// Mientras ComfyUI genera, las peticiones de texto vuelven a la 3080 (no caben las dos cosas en la
// gráfica) y antes de cada imagen se sueltan los modelos de Ollama.

import (
	"archive/zip"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

const (
	portWhisperSmall = 8191
	portWhisperTurbo = 8192
	portComfy        = 8190
)

type fileSpec struct {
	URL    string `json:"url"` // «depot:…» = almacén del servidor (por el relé, con el código de alta)
	Size   int64  `json:"size"`
	SHA256 string `json:"sha256"`
	Path   string `json:"path"` // destino relativo (modelos de ComfyUI)
}

type whisperSpec struct {
	Zip    fileSpec            `json:"zip"`
	Models map[string]fileSpec `json:"models"` // "small", "turbo"
}

type customNodeSpec struct {
	Name         string   `json:"name"`
	Zip          fileSpec `json:"zip"`
	Requirements bool     `json:"requirements"`
}

type comfySpec struct {
	Version     string           `json:"version"`
	Portable    fileSpec         `json:"portable"`
	SevenZip    fileSpec         `json:"sevenzip"`
	CustomNodes []customNodeSpec `json:"custom_nodes"`
	Files       []fileSpec       `json:"files"`
	Args        []string         `json:"args"`
}

type servicesSpec struct {
	Whisper *whisperSpec `json:"whisper"`
	Comfy   *comfySpec   `json:"comfy"`
}

type svcState struct {
	State    string   `json:"state"` // installing, ready, error, starting
	Detail   string   `json:"detail"`
	Progress int      `json:"progress"`
	Models   []string `json:"models,omitempty"`
}

var (
	svcMu      sync.Mutex
	svcStatus  = map[string]*svcState{}
	svcRunning = map[string]bool{} // instalación en curso
	svcProcs   = map[string]*exec.Cmd{}
	svcNext    = map[string]time.Time{} // próximo intento tras un fallo
)

func svcDir(parts ...string) string {
	return filepath.Join(append([]string{dataDir(), "svc"}, parts...)...)
}

// svcBackground: el servicio ya está en marcha y lo que se instala (p. ej. un modelo más) va por detrás,
// sin dejar de atender (1.12.5).
var svcBackground = map[string]bool{}

func setSvc(name, state, detail string, progress int) {
	svcMu.Lock()
	if svcBackground[name] && state == "installing" {
		state, detail = "ready", "En marcha · "+detail
	}
	st := svcStatus[name]
	if st == nil {
		st = &svcState{}
		svcStatus[name] = st
	}
	st.State, st.Detail, st.Progress = state, detail, progress
	svcMu.Unlock()
}

func servicesReport() map[string]any {
	svcMu.Lock()
	defer svcMu.Unlock()
	out := map[string]any{}
	for k, v := range svcStatus {
		c := *v
		out[k] = c
	}
	return out
}

func svcReady(name string) bool {
	svcMu.Lock()
	defer svcMu.Unlock()
	st := svcStatus[name]
	return st != nil && st.State == "ready"
}

func portOpen(port int) bool {
	c, err := net.DialTimeout("tcp", fmt.Sprintf("127.0.0.1:%d", port), 700*time.Millisecond)
	if err != nil {
		return false
	}
	c.Close()
	return true
}

// resolveURL: «depot:ruta» → https://<relé>/depot/ruta (con el código de alta en la cabecera).
func (s *State) resolveURL(u string) (string, http.RoundTripper, map[string]string) {
	if !strings.HasPrefix(u, "depot:") {
		return u, http.DefaultTransport, nil
	}
	s.mu.Lock()
	cfg := s.cfg
	s.mu.Unlock()
	base, host := relayHTTP(cfg)
	sni := host
	if cfg.RelaySNI != "" {
		sni = cfg.RelaySNI
	}
	return base + "/depot/" + strings.TrimPrefix(u, "depot:"), relayTransport(cfg.RelayPin, sni),
		map[string]string{"X-Client-Node": strings.ToLower(cfg.Name), "X-Node-Token": cfg.Token}
}

// fetch descarga un fichero (reanudando si se cortó) y comprueba tamaño y huella. Si el destino ya
// existe con el tamaño correcto, no hace nada.
func (s *State) fetch(f fileSpec, dest string, label string, svc string) error {
	if st, err := os.Stat(dest); err == nil && (f.Size == 0 || st.Size() == f.Size) {
		return nil
	}
	os.MkdirAll(filepath.Dir(dest), 0o755)
	url, tr, hdr := s.resolveURL(f.URL)
	part := dest + ".part"
	hc := &http.Client{Transport: tr, Timeout: 0}
	for attempt := 1; attempt <= 6; attempt++ {
		var have int64
		if st, err := os.Stat(part); err == nil {
			have = st.Size()
		}
		req, _ := http.NewRequest(http.MethodGet, url, nil)
		for k, v := range hdr {
			req.Header.Set(k, v)
		}
		if have > 0 {
			req.Header.Set("Range", fmt.Sprintf("bytes=%d-", have))
		}
		resp, err := hc.Do(req)
		if err != nil {
			log.Printf("%s: %v (intento %d)", label, err, attempt)
			time.Sleep(time.Duration(attempt*10) * time.Second)
			continue
		}
		if resp.StatusCode == http.StatusOK {
			have = 0
		} else if resp.StatusCode != http.StatusPartialContent {
			resp.Body.Close()
			return fmt.Errorf("%s: HTTP %d", label, resp.StatusCode)
		}
		flag := os.O_CREATE | os.O_WRONLY
		if have > 0 {
			flag |= os.O_APPEND
		} else {
			flag |= os.O_TRUNC
		}
		out, err := os.OpenFile(part, flag, 0o644)
		if err != nil {
			resp.Body.Close()
			return err
		}
		total := f.Size
		if total == 0 && resp.ContentLength > 0 {
			total = have + resp.ContentLength
		}
		buf := make([]byte, 1<<20)
		done, last := have, time.Time{}
		var rerr error
		for {
			n, e := resp.Body.Read(buf)
			if n > 0 {
				if _, werr := out.Write(buf[:n]); werr != nil {
					rerr = werr
					break
				}
				done += int64(n)
				if total > 0 && time.Since(last) > time.Second {
					last = time.Now()
					pct := int(done * 100 / total)
					setSvc(svc, "installing", fmt.Sprintf("%s (%d %%)", label, pct), pct)
				}
			}
			if e == io.EOF {
				break
			}
			if e != nil {
				rerr = e
				break
			}
		}
		out.Close()
		resp.Body.Close()
		if rerr != nil {
			log.Printf("%s: corte (%v), reanudo", label, rerr)
			time.Sleep(time.Duration(attempt*5) * time.Second)
			continue
		}
		if f.Size > 0 && done != f.Size {
			return fmt.Errorf("%s: tamaño %d en vez de %d", label, done, f.Size)
		}
		if f.SHA256 != "" {
			setSvc(svc, "installing", label+" (comprobando)", 100)
			if got := fileSHA256(part); !strings.EqualFold(got, f.SHA256) {
				os.Remove(part)
				return fmt.Errorf("%s: la huella no coincide", label)
			}
		}
		return os.Rename(part, dest)
	}
	return fmt.Errorf("%s: no se pudo descargar", label)
}

// unzipTo extrae un zip; con strip=true quita la primera carpeta (los zip de GitHub la traen).
func unzipTo(zipPath, dest string, strip bool) error {
	z, err := zip.OpenReader(zipPath)
	if err != nil {
		return err
	}
	defer z.Close()
	for _, f := range z.File {
		name := f.Name
		if strip {
			if i := strings.IndexByte(name, '/'); i >= 0 {
				name = name[i+1:]
			} else {
				continue
			}
		}
		if name == "" {
			continue
		}
		p := filepath.Join(dest, filepath.FromSlash(name))
		if !strings.HasPrefix(p, filepath.Clean(dest)+string(os.PathSeparator)) {
			return errors.New("ruta no válida en el zip")
		}
		if f.FileInfo().IsDir() {
			os.MkdirAll(p, 0o755)
			continue
		}
		os.MkdirAll(filepath.Dir(p), 0o755)
		r, err := f.Open()
		if err != nil {
			return err
		}
		w, err := os.Create(p)
		if err != nil {
			r.Close()
			return err
		}
		_, err = io.Copy(w, r)
		r.Close()
		w.Close()
		if err != nil {
			return err
		}
	}
	return nil
}

func specHash(v any) string {
	b, _ := json.Marshal(v)
	h := sha256.Sum256(b)
	return hex.EncodeToString(h[:8])
}

func markerOK(dir, hash string) bool {
	b, err := os.ReadFile(filepath.Join(dir, "instalado.txt"))
	return err == nil && strings.TrimSpace(string(b)) == hash
}

// applyServices: lo que el servidor quiere en este PC (llega en cada latido).
func (s *State) applyServices(spec servicesSpec) {
	if runtime.GOOS != "windows" {
		return
	}
	// los servicios que el servidor ya no pide a este PC dejan de salir en el panel
	svcMu.Lock()
	for k := range svcStatus {
		if (k == "whisper" && spec.Whisper == nil) || (k == "comfy" && spec.Comfy == nil) {
			delete(svcStatus, k)
		}
	}
	svcMu.Unlock()
	if spec.Whisper != nil {
		s.ensureSvc("whisper", func() error { return s.installWhisper(*spec.Whisper) }, func() error { return startWhisper(*spec.Whisper) })
	}
	if spec.Comfy != nil {
		comfySweeper()
		s.ensureSvc("comfy", func() error { return s.installComfy(*spec.Comfy) }, func() error { return startComfy(*spec.Comfy) })
	}
}

// ensureSvc instala (si hace falta) y arranca un servicio, sin solaparse y con espera tras un fallo.
func (s *State) ensureSvc(name string, install, start func() error) {
	svcMu.Lock()
	if svcRunning[name] || time.Now().Before(svcNext[name]) {
		svcMu.Unlock()
		return
	}
	svcRunning[name] = true
	svcMu.Unlock()
	go func() {
		defer func() {
			if r := recover(); r != nil {
				setSvc(name, "error", fmt.Sprint(r), -1)
			}
			svcMu.Lock()
			svcRunning[name] = false
			svcMu.Unlock()
		}()
		fail := func(err error) {
			log.Printf("servicio %s: %v", name, err)
			setSvc(name, "error", err.Error(), -1)
			svcMu.Lock()
			svcNext[name] = time.Now().Add(10 * time.Minute)
			svcMu.Unlock()
		}
		if err := install(); err != nil {
			fail(err)
			return
		}
		if err := start(); err != nil {
			fail(err)
			return
		}
	}()
}

func (s *State) installWhisper(w whisperSpec) error {
	dir := svcDir("whisper")
	h := specHash(w)
	if !markerOK(dir, h) {
		log.Printf("instalo Whisper")
		zipPath := svcDir("descargas", "whisper.zip")
		if err := s.fetch(w.Zip, zipPath, "Descargando Whisper", "whisper"); err != nil {
			return err
		}
		setSvc("whisper", "installing", "Descomprimiendo Whisper", -1)
		os.RemoveAll(filepath.Join(dir, "bin"))
		if err := unzipTo(zipPath, filepath.Join(dir, "bin"), true); err != nil {
			return err
		}
		os.Remove(zipPath)
		keys := make([]string, 0, len(w.Models))
		for k := range w.Models {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		for _, k := range keys {
			if err := s.fetch(w.Models[k], filepath.Join(dir, "modelos", k+".bin"), "Descargando el modelo "+k+" de Whisper", "whisper"); err != nil {
				return err
			}
		}
		os.WriteFile(filepath.Join(dir, "instalado.txt"), []byte(h), 0o644)
		log.Printf("Whisper instalado")
	}
	return nil
}

func whisperPort(model string) int {
	if model == "turbo" {
		return portWhisperTurbo
	}
	return portWhisperSmall
}

func startWhisper(w whisperSpec) error {
	if comfyBusy() && whisperPausedForVideo.Load() { // la gráfica está con un vídeo: Whisper espera (1.14.2)
		setSvc("whisper", "paused", "En pausa: la gráfica está generando un vídeo", -1)
		return nil
	}
	whisperPausedForVideo.Store(false)
	dir := svcDir("whisper")
	var models []string
	for k := range w.Models {
		port := whisperPort(k)
		if !portOpen(port) {
			args := []string{"-m", filepath.Join(dir, "modelos", k+".bin"), "--host", "127.0.0.1", "--port", fmt.Sprint(port), "-l", "auto"}
			if k == "turbo" {
				args = append(args, "-bs", "5")
			}
			if err := startProc("whisper-"+k, filepath.Join(dir, "bin", "whisper-server.exe"), args, filepath.Join(dir, "bin")); err != nil {
				return err
			}
			if !waitPort(port, 90*time.Second) {
				return fmt.Errorf("Whisper %s no arranca (mira svc\\whisper-%s.log)", k, k)
			}
		}
		models = append(models, k)
	}
	sort.Strings(models)
	setSvc("whisper", "ready", "En marcha", -1)
	svcMu.Lock()
	svcStatus["whisper"].Models = models
	svcMu.Unlock()
	return nil
}

func comfyRoot() string { return svcDir("comfy", "ComfyUI_windows_portable") }

func (s *State) installComfy(c comfySpec) error {
	svcMu.Lock()
	svcBackground["comfy"] = portOpen(portComfy)
	svcMu.Unlock()
	defer func() { svcMu.Lock(); svcBackground["comfy"] = false; svcMu.Unlock() }()
	dir := svcDir("comfy")
	root := comfyRoot()
	base := specHash(map[string]any{"v": c.Version, "p": c.Portable})
	if !markerOK(root, base) {
		if err := diskOK(dir, c.Portable.Size*3); err != nil {
			return err
		}
		log.Printf("instalo ComfyUI %s", c.Version)
		sz := filepath.Join(dir, "7zr.exe")
		if err := s.fetch(c.SevenZip, sz, "Descargando el descompresor", "comfy"); err != nil {
			return err
		}
		arch := svcDir("descargas", "comfyui.7z")
		if err := s.fetch(c.Portable, arch, "Descargando ComfyUI", "comfy"); err != nil {
			return err
		}
		setSvc("comfy", "installing", "Descomprimiendo ComfyUI (unos minutos)", -1)
		os.RemoveAll(root)
		cmd := exec.Command(sz, "x", arch, "-o"+dir, "-y")
		hideWindow(cmd)
		if out, err := cmd.CombinedOutput(); err != nil {
			return fmt.Errorf("al descomprimir ComfyUI: %v %s", err, lastLine(string(out)))
		}
		os.Remove(arch)
		os.WriteFile(filepath.Join(root, "instalado.txt"), []byte(base), 0o644)
	}
	py := filepath.Join(root, "python_embeded", "python.exe")
	nodesChanged := false
	for _, n := range c.CustomNodes {
		nd := filepath.Join(root, "ComfyUI", "custom_nodes", n.Name)
		h := specHash(n)
		if markerOK(nd, h) {
			continue
		}
		zp := svcDir("descargas", n.Name+".zip")
		if err := s.fetch(n.Zip, zp, "Descargando "+n.Name, "comfy"); err != nil {
			return err
		}
		os.RemoveAll(nd)
		if err := unzipTo(zp, nd, true); err != nil {
			return err
		}
		os.Remove(zp)
		if n.Requirements {
			setSvc("comfy", "installing", "Instalando lo que necesita "+n.Name, -1)
			cmd := exec.Command(py, "-s", "-m", "pip", "install", "--no-warn-script-location", "-r", filepath.Join(nd, "requirements.txt"))
			hideWindow(cmd)
			if out, err := cmd.CombinedOutput(); err != nil {
				return fmt.Errorf("pip para %s: %v %s", n.Name, err, lastLine(string(out)))
			}
		}
		os.WriteFile(filepath.Join(nd, "instalado.txt"), []byte(h), 0o644)
		nodesChanged = true
	}
	// Nodos nuevos con ComfyUI ya en marcha: solo se cargan al arrancar, así que se reinicia (1.13.2).
	if nodesChanged && portOpen(portComfy) {
		log.Printf("nodos nuevos de ComfyUI: lo reinicio para cargarlos")
		stopComfy()
		if err := startComfy(c); err != nil {
			return err
		}
	}
	var need int64
	for _, f := range c.Files {
		if st, err := os.Stat(filepath.Join(root, "ComfyUI", filepath.FromSlash(f.Path))); err != nil || st.Size() != f.Size {
			need += f.Size
		}
	}
	if need > 0 {
		if err := diskOK(root, need); err != nil {
			return err
		}
	}
	// Ya instalado pero parado (p. ej. tras actualizarse el programa): primero se arranca, para seguir
	// atendiendo con los modelos que ya tiene, y lo que falte se baja por detrás (1.13.1).
	if need > 0 && !portOpen(portComfy) && markerOK(root, base) {
		if err := startComfy(c); err == nil {
			svcMu.Lock()
			svcBackground["comfy"] = true
			svcMu.Unlock()
		}
	}
	for i, f := range c.Files {
		dst := filepath.Join(root, "ComfyUI", filepath.FromSlash(f.Path))
		label := fmt.Sprintf("Descargando modelo de imagen %d de %d (%s)", i+1, len(c.Files), filepath.Base(f.Path))
		if err := s.fetch(f, dst, label, "comfy"); err != nil {
			return err
		}
	}
	return nil
}

func comfyArgsFile() string { return svcDir("comfy.args") }

func startComfy(c comfySpec) error {
	want := strings.Join(c.Args, " ")
	if portOpen(portComfy) {
		// Ajustes distintos de los de su arranque (o lo arrancó una versión anterior): se reinicia
		// cuando no esté trabajando, para aplicarlos sin tocar el PC (1.14.1).
		if b, err := os.ReadFile(comfyArgsFile()); err != nil || strings.TrimSpace(string(b)) != want {
			if comfyBusy() {
				return nil
			}
			log.Printf("ComfyUI con ajustes antiguos: lo reinicio con «%s»", want)
			stopComfy()
		} else {
			setSvc("comfy", "ready", "En marcha", -1)
			return nil
		}
	}
	setSvc("comfy", "starting", "Arrancando ComfyUI", -1)
	root := comfyRoot()
	args := append([]string{"-s", filepath.Join("ComfyUI", "main.py"), "--listen", "127.0.0.1", "--port", fmt.Sprint(portComfy),
		"--disable-auto-launch"}, c.Args...)
	if err := startProc("comfy", filepath.Join(root, "python_embeded", "python.exe"), args, root); err != nil {
		return err
	}
	os.WriteFile(comfyArgsFile(), []byte(want), 0o644)
	if !waitPort(portComfy, 4*time.Minute) {
		return errors.New("ComfyUI no arranca (mira svc\\comfy.log)")
	}
	setSvc("comfy", "ready", "En marcha", -1)
	return nil
}

func lastLine(s string) string {
	s = strings.TrimSpace(s)
	if i := strings.LastIndexByte(s, '\n'); i >= 0 {
		s = s[i+1:]
	}
	if len(s) > 200 {
		s = s[len(s)-200:]
	}
	return s
}

func waitPort(port int, d time.Duration) bool {
	end := time.Now().Add(d)
	for time.Now().Before(end) {
		if portOpen(port) {
			return true
		}
		time.Sleep(2 * time.Second)
	}
	return false
}

// startProc arranca un proceso oculto con su salida en svc\<nombre>.log y lo vigila: si se cae,
// el siguiente latido lo vuelve a arrancar (applyServices → start*).
func startProc(name, exe string, args []string, dir string) error {
	logf, err := os.Create(svcDir(name + ".log"))
	if err != nil {
		return err
	}
	cmd := exec.Command(exe, args...)
	cmd.Dir = dir
	cmd.Stdout, cmd.Stderr = logf, logf
	hideWindow(cmd)
	if err := cmd.Start(); err != nil {
		logf.Close()
		return err
	}
	svcMu.Lock()
	svcProcs[name] = cmd
	svcMu.Unlock()
	log.Printf("servicio %s arrancado (pid %d)", name, cmd.Process.Pid)
	go func() {
		err := cmd.Wait()
		logf.Close()
		log.Printf("servicio %s terminó: %v", name, err)
		svcMu.Lock()
		if svcProcs[name] == cmd {
			delete(svcProcs, name)
		}
		svcMu.Unlock()
	}()
	return nil
}

// stopServices: al cerrar el programa se paran los servicios (no se quedan ocupando la gráfica).
func stopServices() {
	svcMu.Lock()
	defer svcMu.Unlock()
	for _, c := range svcProcs {
		if c.Process != nil {
			c.Process.Kill()
		}
	}
}

// ---------- convivencia de ComfyUI con Ollama en la misma gráfica ----------

var (
	comfyBusyMu sync.Mutex
	comfyBusyAt time.Time
	comfyBusyV  bool
)

// comfyBusy: ¿hay imágenes en cola o generándose? (consulta cacheada 2 s)
func comfyBusy() bool {
	if !svcReady("comfy") {
		return false
	}
	comfyBusyMu.Lock()
	defer comfyBusyMu.Unlock()
	if time.Since(comfyBusyAt) < 2*time.Second {
		return comfyBusyV
	}
	comfyBusyAt = time.Now()
	ctx, cancel := context.WithTimeout(context.Background(), 1500*time.Millisecond)
	defer cancel()
	req, _ := http.NewRequestWithContext(ctx, http.MethodGet, fmt.Sprintf("http://127.0.0.1:%d/queue", portComfy), nil)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		comfyBusyV = false
		return false
	}
	defer resp.Body.Close()
	var q struct {
		Running []any `json:"queue_running"`
		Pending []any `json:"queue_pending"`
	}
	json.NewDecoder(resp.Body).Decode(&q)
	comfyBusyV = len(q.Running)+len(q.Pending) > 0
	return comfyBusyV
}

var comfyFreedAt time.Time

// comfyFree: antes de un trabajo de texto, que ComfyUI suelte la memoria de la gráfica.
func comfyFree() {
	if !svcReady("comfy") || time.Since(comfyFreedAt) < 30*time.Second {
		return
	}
	comfyFreedAt = time.Now()
	b := strings.NewReader(`{"unload_models":true,"free_memory":true}`)
	resp, err := http.Post(fmt.Sprintf("http://127.0.0.1:%d/free", portComfy), "application/json", b)
	if err == nil {
		resp.Body.Close()
	}
}

// ollamaUnloadAll: antes de una imagen, que Ollama suelte sus modelos.
func ollamaUnloadAll(base string) {
	for _, m := range ollamaLoaded(base) {
		unloadModel(base, m.Name)
	}
	comfyFreedAt = time.Time{}
}

// comfyBusyCached: el último valor conocido, sin consultar (para usar con el candado de State tomado).
func comfyBusyCached() bool {
	comfyBusyMu.Lock()
	defer comfyBusyMu.Unlock()
	return comfyBusyV && time.Since(comfyBusyAt) < 30*time.Second
}

// serveSvc: /_svc/whisper/<small|turbo>/... y /_svc/comfy/... (el token ya se comprobó).
func (s *State) serveSvc(w http.ResponseWriter, r *http.Request) {
	rest := strings.TrimPrefix(r.URL.Path, "/_svc/")
	var port int
	var path string
	switch {
	case strings.HasPrefix(rest, "whisper/"):
		p := strings.SplitN(strings.TrimPrefix(rest, "whisper/"), "/", 2)
		if !svcReady("whisper") || len(p) < 2 {
			http.Error(w, "Whisper no está listo en este PC", http.StatusServiceUnavailable)
			return
		}
		port, path = whisperPort(p[0]), "/"+p[1]
	case rest == "comfy/_clean" && r.Method == http.MethodPost: // el proxy ya recogió el resultado: fuera (1.14.0)
		var b struct {
			Files []cleanFile `json:"files"`
		}
		json.NewDecoder(io.LimitReader(r.Body, 1<<20)).Decode(&b)
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprintf(w, `{"ok":true,"borrados":%d}`, comfyClean(b.Files))
		return
	case strings.HasPrefix(rest, "comfy/"):
		if !svcReady("comfy") {
			http.Error(w, "ComfyUI no está listo en este PC", http.StatusServiceUnavailable)
			return
		}
		port, path = portComfy, "/"+strings.TrimPrefix(rest, "comfy/")
	default:
		http.NotFound(w, r)
		return
	}
	// Lo que pone a trabajar la gráfica exige que se esté prestando; consultar resultados, no.
	starts := r.Method == http.MethodPost && (strings.HasPrefix(rest, "whisper/") || path == "/prompt")
	if starts {
		if ok, why := s.lendingNow(); !ok {
			http.Error(w, why, http.StatusServiceUnavailable)
			return
		}
		if path == "/prompt" {
			s.mu.Lock()
			base := s.cfg.Ollama
			s.mu.Unlock()
			ollamaUnloadAll(base)
			// Un vídeo necesita toda la gráfica: fuera también Whisper (vuelve al acabar) (1.14.2)
			if body, err := io.ReadAll(io.LimitReader(r.Body, 64<<20)); err == nil {
				r.Body = io.NopCloser(bytes.NewReader(body))
				r.ContentLength = int64(len(body))
				if isVideoPrompt(body) {
					stopWhisper()
				}
			}
			comfyBusyMu.Lock()
			comfyBusyV, comfyBusyAt = true, time.Now()
			comfyBusyMu.Unlock()
		}
		atomic.AddInt32(&s.inflight, 1)
		s.lastReq.Store(time.Now().Unix())
		defer func() {
			atomic.AddInt32(&s.inflight, -1)
			s.lastReq.Store(time.Now().Unix())
			s.served.Add(1)
		}()
	}
	target, _ := url.Parse(fmt.Sprintf("http://127.0.0.1:%d", port))
	rp := httputil.NewSingleHostReverseProxy(target)
	rp.FlushInterval = -1
	r.URL.Path, r.URL.RawPath = path, ""
	r.Host = target.Host
	r.Header.Del("X-Node-Token")
	r.Header.Del("Origin") // ComfyUI comprueba Origin contra Host
	r.Header.Del("Referer")
	rp.ServeHTTP(w, r)
}

// stopComfy para ComfyUI, aunque lo arrancara una versión anterior del programa (se busca por su puerto).
func stopComfy() {
	svcMu.Lock()
	if c := svcProcs["comfy"]; c != nil && c.Process != nil {
		c.Process.Kill()
	}
	svcMu.Unlock()
	if pid := pidOnPort(portComfy); pid > 0 {
		if p, err := os.FindProcess(pid); err == nil {
			p.Kill()
		}
	}
	for i := 0; i < 30 && portOpen(portComfy); i++ {
		time.Sleep(time.Second)
	}
}

// ---------- limpieza de ComfyUI (1.14.0): nada de lo que se genera o se sube se queda en el PC ----------

type cleanFile struct {
	Filename  string `json:"filename"`
	Subfolder string `json:"subfolder"`
	Type      string `json:"type"` // output | input
}

// comfyDirFor: carpeta de ComfyUI para «output» o «input» (nada más).
func comfyDirFor(typ string) string {
	switch typ {
	case "output", "input":
		return filepath.Join(comfyRoot(), "ComfyUI", typ)
	}
	return ""
}

// comfyClean borra los ficheros pedidos, siempre dentro de input/ u output/. Devuelve cuántos borró.
func comfyClean(files []cleanFile) int {
	n := 0
	for _, f := range files {
		base := comfyDirFor(f.Type)
		if base == "" || f.Filename == "" {
			continue
		}
		p := filepath.Clean(filepath.Join(base, filepath.FromSlash(f.Subfolder), filepath.Base(f.Filename)))
		if !strings.HasPrefix(p, base+string(os.PathSeparator)) {
			continue
		}
		if os.Remove(p) == nil {
			n++
		}
	}
	return n
}

var sweepOnce sync.Once

// comfySweeper: cada 10 minutos borra de input/ y output/ lo que tenga más de una hora
// (trabajos cancelados, resultados que nadie recogió…).
func comfySweeper() {
	sweepOnce.Do(func() {
		go func() {
			for {
				time.Sleep(10 * time.Minute)
				if comfyBusy() {
					continue
				}
				n := 0
				for _, typ := range []string{"output", "input"} {
					base := comfyDirFor(typ)
					filepath.Walk(base, func(p string, fi os.FileInfo, err error) error {
						if err == nil && !fi.IsDir() && time.Since(fi.ModTime()) > time.Hour && os.Remove(p) == nil {
							n++
						}
						return nil
					})
				}
				if n > 0 {
					log.Printf("limpieza de ComfyUI: %d ficheros de más de una hora borrados", n)
				}
			}
		}()
	})
}

var whisperPausedForVideo atomic.Bool

// isVideoPrompt: ¿el flujo genera vídeo? (LTX, Wan, o guarda con VideoHelperSuite)
func isVideoPrompt(b []byte) bool {
	t := string(b)
	return strings.Contains(t, "LTXV") || strings.Contains(t, "WanVideo") || strings.Contains(t, "VHS_VideoCombine")
}

// stopWhisper para los servidores de Whisper para dejar la gráfica entera a un vídeo.
func stopWhisper() {
	svcMu.Lock()
	_, have := svcStatus["whisper"]
	svcMu.Unlock()
	if !have {
		return
	}
	whisperPausedForVideo.Store(true)
	for _, k := range []string{"small", "turbo"} {
		svcMu.Lock()
		if c := svcProcs["whisper-"+k]; c != nil && c.Process != nil {
			c.Process.Kill()
		}
		svcMu.Unlock()
		if pid := pidOnPort(whisperPort(k)); pid > 0 {
			if p, err := os.FindProcess(pid); err == nil {
				p.Kill()
			}
		}
	}
	setSvc("whisper", "paused", "En pausa: la gráfica está generando un vídeo", -1)
	log.Printf("vídeo en ComfyUI: Whisper en pausa para dejarle la gráfica entera")
}
