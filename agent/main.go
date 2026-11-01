// GPU Prestada: agente que presta la tarjeta gráfica de este PC al stack de IA.
//
// Se da de alta en el proxy de Ollama del servidor (latido cada pocos segundos)
// y atiende las peticiones que este le reenvía, pasándolas al Ollama local.
// Dos modos: con Tailscale escucha en esa IP y exige el token de alta; sin
// Tailscale abre un túnel saliente hacia el relé de LoMasTrend (tunnel.go).
package main

import (
	"bytes"
	"context"
	"encoding/json"
	"flag"
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
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

// version puede fijarse al compilar con -ldflags "-X main.version=…".
var version = "1.17.1"

type Config struct {
	Broker  string `json:"broker"`  // proxy de Ollama del servidor
	Token   string `json:"token"`   // se obtiene solo en el primer arranque
	Name    string `json:"name"`    // nombre del nodo (por defecto, el del equipo)
	Port    int    `json:"port"`    // puerto en el que escucha el agente
	Ollama  string `json:"ollama"`  // Ollama local
	Lending bool   `json:"lending"` // interruptor "Prestar la tarjeta"
	// Cesión automática: si la gráfica pasa de BusyUtil % sin que el stack la esté
	// usando, se deja de prestar hasta que baje de IdleUtil % durante un minuto.
	BusyUtil int `json:"busy_util"`
	IdleUtil int `json:"idle_util"`
	// Forma de llegar al servidor: "auto" (Tailscale si lo hay; si no, túnel), "tailscale" o "tunnel".
	Mode     string `json:"mode"`
	Relay    string `json:"relay"`     // WebSocket del relé (modo túnel)
	RelayPin string `json:"relay_pin"` // huella SHA-256 (hex) de la clave pública del certificado del relé
	RelaySNI string `json:"relay_sni"` // nombre TLS si no es el del relé (pruebas)
	// Puerto local (127.0.0.1) para usar la IA del servidor desde este PC (1.8.0; 0 = 11444)
	ClientPort int    `json:"client_port"`
	AllowSleep bool   `json:"allow_sleep"` // true = dejar que Windows suspenda aunque esté prestando (1.10.2)
	RequestID  string `json:"request_id"`  // identificador para pedir el alta desde el Monitor VRAM (1.10.0)
}

type GPU struct {
	Name    string
	TotalMB int
	UsedMB  int
	Util    int
	// Datos que no todas las tarjetas dan (en portátiles suelen faltar): -1 = no disponible.
	Temp       int
	Power      int
	PowerLimit int
	Fan        int
	OK         bool
	Vendor     string // "NVIDIA", "AMD", "Intel"… (desde la 1.7.0)
	Shared     bool   // gráfica integrada: su memoria es RAM del equipo compartida
}

type State struct {
	mu           sync.Mutex
	cfg          Config
	cfgPath      string
	tsIP         string
	gpu          GPU
	models       []string
	procs        []GPUProc     // procesos en la gráfica (desde la 1.6.0)
	procsSrc     string        // de dónde salió el tipo de cada proceso (diagnóstico)
	clientPerm   clientPerm    // permiso para usar la IA del servidor desde este PC (lo da el servidor)
	enroll       string        // petición de alta: "esperando", "rechazada" o "" (1.10.0)
	launchHidden int           // modelos ocultos en «Programas con IA» por no saber usar herramientas (1.14.6)
	loaded       []LoadedModel // modelos cargados en Ollama (desde la 1.6.0)
	ollamaOK     bool
	brokerOK     bool
	autoPause    bool // cedida porque el usuario está usando la gráfica
	busyHits     int  // muestras seguidas con la gráfica ocupada por el usuario
	idleSince    time.Time
	task         string   // tarea en curso (instalando, descargando...)
	progress     int      // avance de la tarea en % (-1 = sin barra)
	wantModels   []string // modelos que el servidor quiere en este nodo
	quitting     bool
	exeSHA       string // huella de este ejecutable, para la actualización automática
	pulling      map[string]bool
	inflight     int32
	listening    atomic.Bool // el servidor local ya acepta conexiones
	lastReq      atomic.Int64
	served       atomic.Int64
}

var st = &State{pulling: map[string]bool{}, progress: -1}

// setTask fija la tarea en curso que se enseña en la bandeja y en el panel.
func (s *State) setTask(t string, progress int) {
	s.mu.Lock()
	s.task, s.progress = t, progress
	s.mu.Unlock()
}

func dataDir() string {
	if d := os.Getenv("LOCALAPPDATA"); d != "" {
		return filepath.Join(d, "GPUPrestada")
	}
	d, _ := os.UserConfigDir()
	return filepath.Join(d, "gpu-prestada")
}

func loadConfig(path string) Config {
	host, _ := os.Hostname()
	c := Config{
		Broker: "http://100.72.138.100:11434", Name: strings.ToLower(host), Port: 11435,
		Ollama: "http://127.0.0.1:11434", Lending: true, BusyUtil: 50, IdleUtil: 20,
		Mode: "auto", Relay: defaultRelay, RelayPin: defaultRelayPin,
	}
	if b, err := os.ReadFile(path); err == nil {
		if err := json.Unmarshal(b, &c); err != nil {
			log.Printf("config ilegible (%v), uso valores por defecto", err)
		}
	}
	return c
}

func (s *State) saveConfig() {
	s.mu.Lock()
	b, _ := json.MarshalIndent(s.cfg, "", "  ")
	path := s.cfgPath
	s.mu.Unlock()
	if err := os.WriteFile(path, b, 0o600); err != nil {
		log.Printf("no puedo guardar la configuración: %v", err)
	}
}

// tailscaleIP busca en las interfaces de red una dirección del rango de Tailscale.
func tailscaleIP() string {
	_, cgnat, _ := net.ParseCIDR("100.64.0.0/10")
	addrs, _ := net.InterfaceAddrs()
	for _, a := range addrs {
		if ipn, ok := a.(*net.IPNet); ok && ipn.IP.To4() != nil && cgnat.Contains(ipn.IP) {
			return ipn.IP.String()
		}
	}
	return ""
}

func readGPU() GPU {
	ctx, cancel := context.WithTimeout(context.Background(), 8*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, nvidiaSmiPath(),
		"--query-gpu=name,memory.total,memory.used,utilization.gpu,temperature.gpu,power.draw,power.limit,fan.speed",
		"--format=csv,noheader,nounits")
	hideWindow(cmd)
	out, err := cmd.Output()
	if err != nil {
		return readGPUGeneric() // sin NVIDIA: Radeon, Intel… (contadores de Windows)
	}
	f := strings.Split(strings.TrimSpace(strings.Split(string(out), "\n")[0]), ",")
	if len(f) < 4 {
		return readGPUGeneric()
	}
	// n devuelve el campo i como entero, o -1 si falta o viene como «[N/A]».
	n := func(i int) int {
		if i >= len(f) {
			return -1
		}
		v, err := strconv.ParseFloat(strings.TrimSpace(f[i]), 64)
		if err != nil {
			return -1
		}
		return int(v + 0.5)
	}
	g := GPU{Name: strings.TrimSpace(f[0]), TotalMB: n(1), UsedMB: n(2), Util: n(3),
		Temp: n(4), Power: n(5), PowerLimit: n(6), Fan: n(7), OK: true, Vendor: "NVIDIA"}
	if g.TotalMB < 0 || g.UsedMB < 0 {
		return GPU{}
	}
	if g.Util < 0 {
		g.Util = 0
	}
	return g
}

var httpc = &http.Client{Timeout: 10 * time.Second}

func ollamaModels(base string) ([]string, bool) {
	resp, err := httpc.Get(base + "/api/tags")
	if err != nil {
		return nil, false
	}
	defer resp.Body.Close()
	var r struct {
		Models []struct {
			Name string `json:"name"`
		} `json:"models"`
	}
	if json.NewDecoder(resp.Body).Decode(&r) != nil {
		return nil, false
	}
	names := make([]string, 0, len(r.Models))
	for _, m := range r.Models {
		names = append(names, m.Name)
	}
	return names, true
}

func postJSON(u string, token string, body any, out any) (int, error) {
	b, _ := json.Marshal(body)
	req, _ := http.NewRequest("POST", u, bytes.NewReader(b))
	req.Header.Set("Content-Type", "application/json")
	if token != "" {
		req.Header.Set("X-Node-Token", token)
	}
	resp, err := httpc.Do(req)
	if err != nil {
		return 0, err
	}
	defer resp.Body.Close()
	if out != nil && resp.StatusCode == 200 {
		_ = json.NewDecoder(resp.Body).Decode(out)
	}
	return resp.StatusCode, nil
}

// lendingNow indica si ahora mismo se aceptan trabajos, y si no, por qué.
func (s *State) lendingNow() (bool, string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	switch {
	case s.quitting:
		return false, "Cerrando"
	case !s.cfg.Lending:
		return false, "Préstamo desactivado"
	case !s.gpu.OK:
		return false, "No encuentro la tarjeta gráfica"
	case !s.ollamaOK:
		return false, "Ollama no responde"
	case !s.listening.Load():
		return false, "Arrancando"
	case s.autoPause:
		return false, "Cedida: estás usando la gráfica"
	}
	return true, ""
}

func (s *State) statusLine() string {
	s.mu.Lock()
	ts, task, broker := s.tsIP, s.task, s.brokerOK
	s.mu.Unlock()
	if task != "" {
		return task
	}
	if s.useTunnel() {
		s.mu.Lock()
		tok := s.cfg.Token
		s.mu.Unlock()
		if tok == "" {
			if s.enroll == "rechazada" {
				return "Alta rechazada por el administrador"
			}
			return "Esperando el alta del administrador (o pega el código)"
		}
		if !broker {
			return "Conectando con LoMasTrend…"
		}
	} else if ts == "" {
		return "Sin Tailscale: instálalo e inicia sesión"
	}
	if ok, why := s.lendingNow(); !ok {
		return why
	}
	if !broker {
		return "Sin conexión con el servidor"
	}
	if n := atomic.LoadInt32(&s.inflight); n > 0 {
		return fmt.Sprintf("Prestando: trabajando ahora (%d %% de la gráfica)", s.lentLoad())
	}
	return fmt.Sprintf("Prestando (%d trabajos atendidos)", s.served.Load())
}

// lentLoad: porcentaje de la gráfica que está usando el servidor ahora mismo
// (0 si la carga que haya no es de trabajos prestados).
func (s *State) lentLoad() int {
	ours := atomic.LoadInt32(&s.inflight) > 0 || time.Since(time.Unix(s.lastReq.Load(), 0)) < 10*time.Second || comfyBusyCached()
	s.mu.Lock()
	defer s.mu.Unlock()
	if !ours || !s.gpu.OK {
		return 0
	}
	return s.gpu.Util
}

// trayState: 0 = prestando, 1 = en pausa/cedida, 2 = con problemas.
func (s *State) trayState() int {
	s.mu.Lock()
	ts, broker, lend, auto := s.tsIP, s.brokerOK, s.cfg.Lending, s.autoPause
	s.mu.Unlock()
	if !lend || auto {
		return 1
	}
	if ok, _ := s.lendingNow(); !ok || (ts == "" && !s.useTunnel()) || !broker {
		return 2
	}
	return 0
}

// updateAutoPause decide si el usuario está usando la gráfica para lo suyo.
func (s *State) updateAutoPause() {
	s.mu.Lock()
	defer s.mu.Unlock()
	if !s.gpu.OK {
		return
	}
	ours := atomic.LoadInt32(&s.inflight) > 0 || time.Since(time.Unix(s.lastReq.Load(), 0)) < 20*time.Second || comfyBusyCached()
	if !s.autoPause {
		if !ours && s.gpu.Util >= s.cfg.BusyUtil {
			s.busyHits++
		} else {
			s.busyHits = 0
		}
		if s.busyHits >= 3 {
			s.autoPause, s.busyHits, s.idleSince = true, 0, time.Time{}
			log.Printf("cedo la tarjeta: la estás usando (%d %%)", s.gpu.Util)
		}
		return
	}
	if s.gpu.Util <= s.cfg.IdleUtil {
		if s.idleSince.IsZero() {
			s.idleSince = time.Now()
		} else if time.Since(s.idleSince) > 60*time.Second {
			s.autoPause = false
			log.Printf("la gráfica vuelve a estar libre: presto de nuevo")
		}
	} else {
		s.idleSince = time.Time{}
	}
}

func (s *State) pullModel(m string) {
	s.mu.Lock()
	if s.pulling[m] {
		s.mu.Unlock()
		return
	}
	s.pulling[m] = true
	base := s.cfg.Ollama
	s.mu.Unlock()
	s.setTask("Descargando modelo "+m, 0)
	log.Printf("descargando modelo %s", m)
	b, _ := json.Marshal(map[string]any{"model": m, "stream": true})
	c := &http.Client{Timeout: 6 * time.Hour}
	resp, err := c.Post(base+"/api/pull", "application/json", bytes.NewReader(b))
	if err != nil {
		log.Printf("descarga de %s falló: %v", m, err)
	} else {
		dec := json.NewDecoder(resp.Body)
		for {
			var ev struct {
				Status    string `json:"status"`
				Error     string `json:"error"`
				Total     int64  `json:"total"`
				Completed int64  `json:"completed"`
			}
			if dec.Decode(&ev) != nil {
				break
			}
			if ev.Error != "" {
				log.Printf("descarga de %s: %s", m, ev.Error)
			}
			if ev.Total > 0 {
				pct := int(ev.Completed * 100 / ev.Total)
				s.setTask(fmt.Sprintf("Descargando modelo %s (%d %%)", m, pct), pct)
			}
		}
		resp.Body.Close()
		log.Printf("descarga de %s terminada (HTTP %d)", m, resp.StatusCode)
	}
	s.setTask("", -1)
	// si falló, no reintentar hasta dentro de 10 minutos
	time.AfterFunc(10*time.Minute, func() { s.mu.Lock(); delete(s.pulling, m); s.mu.Unlock() })
}

func hasModel(list []string, m string) bool {
	for _, x := range list {
		if x == m || (!strings.Contains(m, ":") && x == m+":latest") {
			return true
		}
	}
	return false
}

// tick: una vuelta del bucle de latido.
func (s *State) tick() {
	gpu := readGPU()
	s.mu.Lock()
	base := s.cfg.Ollama
	s.mu.Unlock()
	models, ok := ollamaModels(base)
	ip := tailscaleIP()
	procs, procsSrc := readGPUProcs()
	var loaded []LoadedModel
	if ok {
		loaded = ollamaLoaded(base)
	}

	s.mu.Lock()
	s.gpu, s.ollamaOK, s.tsIP, s.procs, s.procsSrc, s.loaded = gpu, ok, ip, procs, procsSrc, loaded
	if ok {
		s.models = models
	}
	cfg := s.cfg
	s.mu.Unlock()
	s.updateAutoPause()
	if gpu.OK && gpu.Vendor != "NVIDIA" {
		ensureVulkan(s) // Ollama solo usa una Radeon/Intel con su modo Vulkan
	}

	if !ok {
		go ensureOllama(s) // la instala o la arranca si hace falta (según plataforma)
	} else {
		clearOllamaHint(s)
	}
	if s.useTunnel() { // sin Tailscale: el latido va por el túnel (tunnel.go)
		s.tickTunnel()
		return
	}
	if ip == "" {
		s.setBroker(false)
		return
	}
	if cfg.Token == "" {
		var r struct {
			Token string `json:"token"`
		}
		code, err := postJSON(cfg.Broker+"/_nodes/enroll", "", map[string]string{"name": cfg.Name}, &r)
		if err != nil || code != 200 || r.Token == "" {
			log.Printf("alta en el servidor pendiente (HTTP %d, %v)", code, err)
			s.setBroker(false)
			return
		}
		s.mu.Lock()
		s.cfg.Token = r.Token
		cfg = s.cfg
		s.mu.Unlock()
		s.saveConfig()
		log.Printf("alta en el servidor hecha")
	}
	var raw json.RawMessage
	code, err := postJSON(cfg.Broker+"/_nodes/heartbeat", cfg.Token, s.heartbeatBody(), &raw)
	if err != nil {
		s.setBroker(false)
		return
	}
	s.applyHeartbeat(code, raw)
}

// heartbeatBody: lo que se cuenta al servidor en cada latido (por HTTP o por el túnel).
func (s *State) heartbeatBody() map[string]any {
	ramTot, ramFree := sysMem()
	drive, diskTot, diskFree := sysDisk()
	lending, reason := s.lendingNow()
	s.mu.Lock()
	cfg, gpu, models, autoPause, procs, procsSrc, loaded := s.cfg, s.gpu, s.models, s.autoPause, s.procs, s.procsSrc, s.loaded
	s.mu.Unlock()
	return map[string]any{
		"name": cfg.Name, "port": cfg.Port, "gpu": gpu.Name, "platform": platform(), "vendor": gpu.Vendor, "shared_mem": gpu.Shared,
		"vram_total_mb": gpu.TotalMB, "vram_free_mb": gpu.TotalMB - gpu.UsedMB, "gpu_util": gpu.Util,
		"models": models, "lending": lending, "reason": reason, "version": version,
		"inflight": atomic.LoadInt32(&s.inflight), "exe_sha256": s.exeSHA,
		// datos para las estadísticas por tarjeta del Monitor VRAM
		"temp": gpu.Temp, "power": gpu.Power, "power_limit": gpu.PowerLimit, "fan": gpu.Fan,
		"lent_load": s.lentLoad(), "served_session": s.served.Load(), "auto_pause": autoPause,
		// pestaña de la tarjeta: procesos en la gráfica, modelos cargados y trabajos en curso
		"procs": procs, "procs_src": procsSrc, "loaded": loaded, "jobs": currentJobs(),
		"services": servicesReport(),
		// memoria y disco del PC (1.12.1), para su ficha en el Monitor VRAM
		"cpu": cpuNameCached(), "cpu_threads": runtime.NumCPU(),
		"ram_total_mb": ramTot, "ram_free_mb": ramFree, "disk_drive": drive, "disk_total_mb": diskTot, "disk_free_mb": diskFree,
	}
}

// applyHeartbeat procesa la respuesta del servidor a un latido.
func (s *State) applyHeartbeat(code int, raw json.RawMessage) {
	if code == 401 { // el token cambió en el servidor: volver a pedirlo (o a introducirlo)
		s.mu.Lock()
		s.cfg.Token = ""
		s.mu.Unlock()
	}
	s.setBroker(code == 200)
	if code != 200 {
		return
	}
	var r struct {
		WantModels []string      `json:"want_models"`
		Services   servicesSpec  `json:"services"`
		Create     []createModel `json:"create_models"`
		AgentSHA   string        `json:"agent_sha256"`
		Client     clientPerm    `json:"client"`
	}
	json.Unmarshal(raw, &r)
	s.mu.Lock()
	if r.Client.Enabled != s.clientPerm.Enabled {
		log.Printf("uso de la IA del servidor desde este PC: %v", map[bool]string{true: "activado", false: "desactivado"}[r.Client.Enabled])
	}
	s.clientPerm = r.Client
	s.wantModels = r.WantModels
	ok, models := s.ollamaOK, s.models
	s.mu.Unlock()
	s.maybeUpdate(r.AgentSHA)
	s.applyServices(r.Services)
	if ok {
		pending := false
		for _, m := range r.WantModels {
			if !hasModel(models, m) {
				go s.pullModel(m)
				pending = true
				break // de uno en uno
			}
		}
		if !pending { // con todo descargado, los alias que pida el servidor (1.11.0)
			for _, c := range r.Create {
				if !hasModel(models, c.Name) && hasModel(models, c.From) {
					go s.createModel(c)
					break
				}
			}
		}
	}
}

func (s *State) setBroker(ok bool) {
	s.mu.Lock()
	if s.brokerOK != ok {
		log.Printf("conexión con el servidor: %v", ok)
	}
	s.brokerOK = ok
	s.mu.Unlock()
}

// nodeHandler atiende una petición del servidor: comprueba el token y el estado
// del préstamo y la reenvía al Ollama local. Lo usan la escucha de Tailscale y el túnel.
func (s *State) nodeHandler() http.Handler {
	s.mu.Lock()
	target := s.cfg.Ollama
	s.mu.Unlock()
	u, _ := url.Parse(target)
	rp := httputil.NewSingleHostReverseProxy(u)
	rp.FlushInterval = -1 // respuestas en streaming sin retener
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		s.mu.Lock()
		tok := s.cfg.Token
		s.mu.Unlock()
		if tok == "" || r.Header.Get("X-Node-Token") != tok {
			http.Error(w, "token incorrecto", http.StatusUnauthorized)
			return
		}
		if r.URL.Path == "/_agent/unload" { // botón «desalojar» del Monitor VRAM: no cuenta como trabajo
			var b struct {
				Model string `json:"model"`
			}
			if json.NewDecoder(io.LimitReader(r.Body, 4096)).Decode(&b) != nil || b.Model == "" {
				http.Error(w, "falta model", http.StatusBadRequest)
				return
			}
			if err := unloadModel(target, b.Model); err != nil {
				http.Error(w, err.Error(), http.StatusBadGateway)
				return
			}
			w.Write([]byte(`{"ok":true}`))
			return
		}
		// Pausar/reanudar desde el Monitor VRAM (31-oct-2026): mueve el mismo interruptor «Prestar la tarjeta»
		// que la bandeja (se guarda en la config del PC y el icono pasa a ámbar). No cuenta como trabajo.
		if r.URL.Path == "/_agent/lending" && r.Method == http.MethodPost {
			var b struct {
				On *bool `json:"on"`
			}
			if json.NewDecoder(io.LimitReader(r.Body, 1024)).Decode(&b) != nil || b.On == nil {
				http.Error(w, "falta on", http.StatusBadRequest)
				return
			}
			s.setLending(*b.On)
			w.Write([]byte(`{"ok":true}`))
			return
		}
		if r.URL.Path == "/_agent/diag" { // diagnóstico remoto: gráfica, entorno y registro de Ollama
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(s.diag())
			return
		}
		if r.URL.Path == "/_agent/gwtest" && r.Method == http.MethodPost { // prueba del Ollama combinado desde dentro
			var b struct {
				Model  string `json:"model"`
				Prompt string `json:"prompt"`
			}
			json.NewDecoder(io.LimitReader(r.Body, 8192)).Decode(&b)
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(gatewaySelfTest(b.Model, b.Prompt))
			return
		}
		if r.URL.Path == "/_agent/nettest" { // diagnóstico: ¿llega el PC a los sitios de descarga?
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(netTest())
			return
		}
		if r.URL.Path == "/_agent/events" { // diagnóstico: por qué se suspendió/apagó el PC (solo lectura)
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(powerEvents())
			return
		}
		if r.URL.Path == "/_agent/reinstall-ollama" && r.Method == http.MethodPost {
			if atomic.LoadInt32(&s.inflight) > 0 {
				http.Error(w, "atendiendo un trabajo", http.StatusConflict)
				return
			}
			go reinstallOllama(s)
			w.Write([]byte(`{"ok":true}`))
			return
		}
		if r.URL.Path == "/_agent/restart-ollama" && r.Method == http.MethodPost {
			if atomic.LoadInt32(&s.inflight) > 0 {
				http.Error(w, "atendiendo un trabajo", http.StatusConflict)
				return
			}
			restartOllama()
			w.Write([]byte(`{"ok":true}`))
			return
		}
		if strings.HasPrefix(r.URL.Path, "/_svc/") { // Whisper y ComfyUI (1.12.0)
			s.serveSvc(w, r)
			return
		}
		if ok, why := s.lendingNow(); !ok {
			http.Error(w, why, http.StatusServiceUnavailable)
			return
		}
		if comfyBusy() { // la gráfica está con una imagen: el texto, a la 3080
			http.Error(w, "la tarjeta está generando una imagen", http.StatusServiceUnavailable)
			return
		}
		comfyFree()
		atomic.AddInt32(&s.inflight, 1)
		s.lastReq.Store(time.Now().Unix())
		defer startJob(r)()
		defer func() {
			atomic.AddInt32(&s.inflight, -1)
			s.lastReq.Store(time.Now().Unix())
			s.served.Add(1)
		}()
		r.Header.Del("X-Node-Token")
		r.Host = u.Host
		rp.ServeHTTP(w, r)
	})
}

// serve: en modo Tailscale escucha en esa IP y reenvía al Ollama local.
// En modo túnel no se escucha nada: las peticiones llegan por tunnel.go.
func (s *State) serve() {
	for {
		s.mu.Lock()
		ip, port := s.tsIP, s.cfg.Port
		s.mu.Unlock()
		if ip == "" || s.useTunnel() {
			time.Sleep(5 * time.Second)
			continue
		}
		h := s.nodeHandler()
		addr := net.JoinHostPort(ip, strconv.Itoa(port))
		ln, err := net.Listen("tcp", addr)
		if err == nil {
			log.Printf("escuchando en %s", addr)
			s.listening.Store(true)
			err = (&http.Server{Handler: h}).Serve(ln)
			s.listening.Store(false)
		}
		log.Printf("el servidor local no está disponible: %v (reintento)", err)
		time.Sleep(10 * time.Second) // p. ej. Tailscale cambió de IP o se apagó
	}
}

func main() {
	headless := flag.Bool("headless", false, "sin icono de bandeja ni ventana (pruebas)")
	autostart := flag.Bool("inicio", false, "arranque con la sesión: no abre la ventana")
	cfgFlag := flag.String("config", "", "ruta del fichero de configuración")
	diag := flag.String("diag", "", "escribe en ese fichero lo que se lee de la gráfica y sale (pruebas)")
	flag.Parse()
	if r := runCLI(flag.Args()); r >= 0 { // órdenes de terminal: estado, codigo, pausar… (1.16.0)
		os.Exit(r)
	}
	if *diag != "" {
		g := readGPU()
		time.Sleep(2 * time.Second) // segunda lectura: los porcentajes necesitan dos
		g = readGPU()
		procs, src := readGPUProcs()
		b, _ := json.MarshalIndent(map[string]any{"gpu": g, "procs": procs, "procs_src": src}, "", "  ")
		_ = os.WriteFile(*diag, b, 0o644)
		return
	}

	dir := dataDir()
	os.MkdirAll(dir, 0o755)
	out := []io.Writer{ring}
	if f, err := os.OpenFile(filepath.Join(dir, "gpu-prestada.log"), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644); err == nil {
		if fi, _ := f.Stat(); fi != nil && fi.Size() > 5<<20 {
			f.Truncate(0)
		}
		out = append(out, f)
	}
	if *headless {
		out = append(out, os.Stderr)
	}
	log.SetOutput(io.MultiWriter(out...))

	// Linux: abierto a mano se instala como servicio de usuario y deja el trabajo a ese servicio.
	if !*headless && linuxInstall(*autostart) {
		return
	}
	// Una sola copia: si ya hay otra, se le pide que enseñe su ventana (o se la
	// sustituye si es de otra versión).
	if !*headless && !takeOver(!*autostart) {
		return
	}

	st.cfgPath = filepath.Join(dir, "config.json")
	if *cfgFlag != "" {
		st.cfgPath = *cfgFlag
	}
	st.cfg = loadConfig(st.cfgPath)
	st.saveConfig()
	log.Printf("GPU Prestada %s · nodo %q · servidor %s", version, st.cfg.Name, st.cfg.Broker)

	if self, err := os.Executable(); err == nil {
		st.exeSHA = fileSHA256(self)
		os.Remove(updateTarget(self) + ".old") // resto de una actualización anterior
	}
	st.tsIP = tailscaleIP()
	go st.servePanel()
	if !*headless && !*autostart {
		go func() { time.Sleep(400 * time.Millisecond); openPanel() }()
	}
	firstRunSetup(st) // copia a su carpeta, arranque automático, regla de cortafuegos (Windows)

	go func() { // al arrancar (y cada hora): ¿hay una versión nueva publicada?
		time.Sleep(2 * time.Second)
		for {
			st.checkUpdateNow()
			time.Sleep(time.Hour)
		}
	}()

	go func() {
		for {
			st.tick()
			time.Sleep(8 * time.Second)
		}
	}()
	migrateOllamaPort(st) // Windows: el Ollama del PC pasa al 11439 y el 11434 es el Ollama combinado
	go st.serve()
	go st.serveClient()
	go st.serveGateway()
	go keepAwake(st)
	go st.tunnelLoop()
	go func() { // lectura rápida de la gráfica (cada 2 s) cuando hace falta
		for {
			time.Sleep(2 * time.Second)
			// también mientras la ventana está abierta, para que los medidores se muevan
			if atomic.LoadInt32(&st.inflight) > 0 || time.Now().Unix()-panelSeen.Load() < 5 {
				g := readGPU()
				st.mu.Lock()
				if g.OK {
					st.gpu = g
				}
				st.mu.Unlock()
			}
		}
	}()

	if *headless {
		select {}
	}
	runTray(st)
}

// createModel (1.11.0): crea en el Ollama local un alias que pide el servidor, p. ej.
// «gemma4:12b-3080» a partir de «gemma4:12b-it-qat» con los mismos ajustes que en la 3080,
// para que las aplicaciones usen el mismo nombre en cualquier tarjeta.
type createModel struct {
	Name       string         `json:"name"`
	From       string         `json:"from"`
	Parameters map[string]any `json:"parameters"`
}

func (s *State) createModel(c createModel) {
	key := "crear:" + c.Name
	s.mu.Lock()
	if s.pulling[key] {
		s.mu.Unlock()
		return
	}
	s.pulling[key] = true
	base := s.cfg.Ollama
	s.mu.Unlock()
	body := map[string]any{"model": c.Name, "from": c.From, "stream": false}
	if len(c.Parameters) > 0 {
		body["parameters"] = c.Parameters
	}
	b, _ := json.Marshal(body)
	cl := &http.Client{Timeout: 10 * time.Minute}
	resp, err := cl.Post(base+"/api/create", "application/json", bytes.NewReader(b))
	if err != nil {
		log.Printf("no pude crear %s: %v", c.Name, err)
	} else {
		msg, _ := io.ReadAll(io.LimitReader(resp.Body, 2048))
		resp.Body.Close()
		log.Printf("creado %s a partir de %s (HTTP %d) %s", c.Name, c.From, resp.StatusCode, strings.TrimSpace(string(msg)))
	}
	time.AfterFunc(10*time.Minute, func() { s.mu.Lock(); delete(s.pulling, key); s.mu.Unlock() })
}

var (
	cpuOnce sync.Once
	cpuStr  string
)

// cpuNameCached: el procesador no cambia; se lee una vez.
func cpuNameCached() string {
	cpuOnce.Do(func() { cpuStr = cpuName() })
	return cpuStr
}
