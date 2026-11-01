//go:build windows

package main

import (
	"encoding/base64"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"
	"unicode/utf16"
	"unsafe"

	"fyne.io/systray"
	"golang.org/x/sys/windows"
	"golang.org/x/sys/windows/registry"
)

const (
	appName      = "GPU Prestada"
	ollamaSetup  = "https://ollama.com/download/OllamaSetup.exe"
	tailscaleURL = "https://tailscale.com/download/windows"
)

func hideWindow(cmd *exec.Cmd) {
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: 0x08000000} // CREATE_NO_WINDOW
}

func nvidiaSmiPath() string {
	if p, err := exec.LookPath("nvidia-smi"); err == nil {
		return p
	}
	return filepath.Join(os.Getenv("SystemRoot"), "System32", "nvidia-smi.exe")
}

func run(name string, args ...string) error {
	cmd := exec.Command(name, args...)
	hideWindow(cmd)
	return cmd.Run()
}

func ollamaExe() string {
	p := filepath.Join(os.Getenv("LOCALAPPDATA"), "Programs", "Ollama", "ollama.exe")
	if _, err := os.Stat(p); err == nil {
		return p
	}
	if p, err := exec.LookPath("ollama"); err == nil {
		return p
	}
	return ""
}

var (
	ollamaMu   sync.Mutex
	ollamaNext time.Time
)

// wantVulkan: con una Radeon/Intel, Ollama necesita su modo Vulkan para usar la gráfica.
func (s *State) wantVulkan() bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.gpu.OK && s.gpu.Vendor != "NVIDIA"
}

var vulkanDone bool

// vulkanEnv: Vulkan (las versiones nuevas ya lo traen activo). La gráfica integrada NO se
// activa (OLLAMA_IGPU_ENABLE): con Ollama 0.40, gemma4:e2b tumba llama-server en la Radeon
// del Surface (0xe06d7363) y en el procesador va a ~29 tokens/s (31-oct-2026).
var vulkanEnv = []string{"OLLAMA_VULKAN=1"}

// ensureVulkan deja OLLAMA_VULKAN=1 en las variables del usuario (para cuando Ollama
// arranque con Windows) y, la primera vez, reinicia el Ollama que ya estuviera en marcha
// sin ella, si no está atendiendo nada. Una sola vez por ejecución.
func ensureVulkan(s *State) {
	if vulkanDone {
		return
	}
	vulkanDone = true
	k, err := registry.OpenKey(registry.CURRENT_USER, "Environment", registry.QUERY_VALUE|registry.SET_VALUE)
	if err != nil {
		return
	}
	defer k.Close()
	changed := false
	for _, kv := range vulkanEnv {
		name := strings.SplitN(kv, "=", 2)[0]
		if v, _, _ := k.GetStringValue(name); v != "1" {
			if k.SetStringValue(name, "1") != nil {
				return
			}
			changed = true
		}
	}
	if _, _, err := k.GetStringValue("OLLAMA_IGPU_ENABLE"); err == nil { // lo puso la 1.7.2: fuera
		if k.DeleteValue("OLLAMA_IGPU_ENABLE") == nil {
			changed = true
		}
	}
	if !changed {
		return
	}
	log.Printf("gráfica no NVIDIA: Ollama con Vulkan y sin usar la gráfica integrada")
	if atomic.LoadInt32(&s.inflight) > 0 {
		return // se aplicará la próxima vez que arranque
	}
	restartOllama() // que ensureOllama lo vuelva a arrancar ya, con Vulkan
}

// ollamaBaseEnv: entorno para «ollama serve». Quita OLLAMA_IGPU_ENABLE (lo dejó la 1.7.2) y,
// si el Ollama del PC se movió a 11439 para dejar el 11434 al Ollama combinado, se lo dice.
func ollamaBaseEnv(s *State) []string {
	env := []string{}
	for _, e := range os.Environ() {
		u := strings.ToUpper(e)
		if strings.HasPrefix(u, "OLLAMA_IGPU_ENABLE=") || (s.ollamaMoved() && strings.HasPrefix(u, "OLLAMA_HOST=")) {
			continue
		}
		env = append(env, e)
	}
	if s.ollamaMoved() {
		env = append(env, "OLLAMA_HOST="+movedOllama)
	}
	return env
}

// migrateOllamaPort (1.9.0): mueve el Ollama de este PC a 127.0.0.1:11439 (variable OLLAMA_HOST del
// usuario, la que lee también la aplicación de Ollama al iniciar sesión) para que el agente ocupe el
// puerto normal 11434 con el Ollama combinado. Si el dueño ya tenía su propio OLLAMA_HOST, no se toca.
// Se hace al arrancar, antes de atender nada.
func migrateOllamaPort(s *State) {
	s.mu.Lock()
	cur := s.cfg.Ollama
	s.mu.Unlock()
	if strings.Contains(cur, movedOllama) {
		os.Setenv("OLLAMA_HOST", movedOllama) // que lo hereden el instalador y la aplicación de Ollama
		return
	}
	if cur != "http://127.0.0.1:11434" && cur != "http://localhost:11434" {
		return // Ollama en otra dirección configurada a mano
	}
	k, err := registry.OpenKey(registry.CURRENT_USER, "Environment", registry.QUERY_VALUE|registry.SET_VALUE)
	if err != nil {
		return
	}
	defer k.Close()
	if v, _, err := k.GetStringValue("OLLAMA_HOST"); err == nil && v != "" && v != movedOllama {
		log.Printf("OLLAMA_HOST ya está puesto por el dueño (%s): no muevo Ollama; la IA del servidor queda en el 11444", v)
		return
	}
	if k.SetStringValue("OLLAMA_HOST", movedOllama) != nil {
		return
	}
	os.Setenv("OLLAMA_HOST", movedOllama)
	s.mu.Lock()
	s.cfg.Ollama = "http://" + movedOllama
	s.mu.Unlock()
	s.saveConfig()
	log.Printf("Ollama de este PC movido a %s; el 11434 pasa a ser el Ollama combinado", movedOllama)
	restartOllama() // se vuelve a arrancar ya en el puerto nuevo
}

func ollamaLogPath() string { return filepath.Join(dataDir(), "ollama-serve.log") }

// ollamaAppLog: el registro que escribe Ollama cuando lo arranca su propia aplicación.
func ollamaAppLog() string { return filepath.Join(os.Getenv("LOCALAPPDATA"), "Ollama", "server.log") }

func killOllama() {
	// /T: con sus procesos hijos (su llama-server); no se toca ningún llama-server de otros programas
	for _, im := range []string{"ollama app.exe", "ollama.exe"} {
		cmd := exec.Command("taskkill", "/F", "/T", "/IM", im)
		hideWindow(cmd)
		_ = cmd.Run()
	}
}

// restartOllama: lo pide el servidor (/_agent/restart-ollama); ensureOllama lo vuelve a arrancar.
func restartOllama() {
	killOllama()
	ollamaMu.Lock()
	ollamaNext = time.Time{}
	ollamaMu.Unlock()
}

// ensureOllama arranca Ollama si está instalado y parado; si no está, lo descarga
// e instala en silencio. Como mucho un intento cada cierto tiempo.
func ensureOllama(s *State) {
	if !ollamaMu.TryLock() {
		return
	}
	defer ollamaMu.Unlock()
	if time.Now().Before(ollamaNext) {
		return
	}
	if exe := ollamaExe(); exe != "" {
		ollamaNext = time.Now().Add(45 * time.Second)
		log.Printf("Ollama instalado pero parado: lo arranco")
		cmd := exec.Command(exe, "serve")
		cmd.Env = ollamaBaseEnv(s)
		if s.wantVulkan() {
			cmd.Env = append(ollamaBaseEnv(s), vulkanEnv...)
		}
		// su salida queda en un fichero para el diagnóstico remoto (/_agent/diag)
		if f, err := os.Create(ollamaLogPath()); err == nil {
			cmd.Stdout, cmd.Stderr = f, f
			defer f.Close() // el proceso hijo conserva su propia copia del descriptor
		}
		hideWindow(cmd)
		if err := cmd.Start(); err != nil {
			log.Printf("no puedo arrancar Ollama: %v", err)
		}
		return
	}
	ollamaNext = time.Now().Add(30 * time.Minute)
	log.Printf("Ollama no está instalado: lo descargo")
	installOllama(s)
}

// reinstallOllama: lo pide el servidor (/_agent/reinstall-ollama) cuando la instalación de Ollama
// se ha quedado rota (p. ej. «llama-server binary not found» tras una actualización a medias).
// Los modelos no se tocan (viven en .ollama, fuera de la carpeta del programa).
func reinstallOllama(s *State) {
	ollamaMu.Lock()
	defer ollamaMu.Unlock()
	log.Printf("reinstalo Ollama a petición del servidor")
	killOllama()
	installOllama(s)
}

// installOllama descarga el instalador oficial y lo ejecuta en silencio. Con ollamaMu tomado.
func installOllama(s *State) {
	defer s.setTask("", -1)
	s.setTask("Descargando Ollama…", 0)
	tmp := filepath.Join(os.TempDir(), "OllamaSetup.exe")
	_, err := downloadFile(&http.Client{Timeout: 2 * time.Hour}, ollamaSetup, tmp, func(pct int) {
		s.setTask(fmt.Sprintf("Descargando Ollama (%d %%)", pct), pct)
	})
	if err != nil {
		log.Printf("descarga de Ollama falló: %v", err)
		return
	}
	s.setTask("Instalando Ollama…", -1)
	if err := run(tmp, "/SP-", "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"); err != nil {
		log.Printf("instalación de Ollama falló: %v", err)
		return
	}
	os.Remove(tmp)
	log.Printf("Ollama instalado")
	ollamaNext = time.Now().Add(20 * time.Second)
}

// updateTarget: la copia instalada, que es la que arranca con Windows.
func updateTarget(self string) string { return filepath.Join(dataDir(), "gpu-prestada.exe") }
func relaunchArgs() []string          { return []string{"-inicio"} }

// firstRunSetup: copia del programa a su carpeta, arranque con
// la sesión y regla del cortafuegos (limitada a la red de Tailscale).
func firstRunSetup(s *State) {
	// Copias antiguas (anteriores a la 1.1, sin ventana) que sigan en marcha: fuera.
	run("taskkill", "/F", "/IM", "gpu-prestada.exe", "/FI", fmt.Sprintf("PID ne %d", os.Getpid()))

	self, _ := os.Executable()
	target := filepath.Join(dataDir(), "gpu-prestada.exe")
	if !strings.EqualFold(self, target) {
		if b, err := os.ReadFile(self); err == nil {
			if err := os.WriteFile(target, b, 0o755); err != nil {
				log.Printf("no puedo copiarme a %s: %v", target, err)
				target = self
			}
		}
	}
	if err := run("reg", "add", `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`,
		"/v", "GPUPrestada", "/t", "REG_SZ", "/d", `"`+target+`" -inicio`, "/f"); err != nil {
		log.Printf("no puedo registrar el arranque automático: %v", err)
	}

	if s.useTunnel() {
		return // por túnel no entra nada: no hace falta abrir el cortafuegos (ni pedir administrador)
	}
	rule := "name=" + appName
	if run("netsh", "advfirewall", "firewall", "show", "rule", rule) != nil {
		log.Printf("creo la regla del cortafuegos (pedirá permiso de administrador)")
		args := fmt.Sprintf(`advfirewall firewall add rule name="%s" dir=in action=allow protocol=TCP localport=%d remoteip=100.64.0.0/10`,
			appName, s.cfg.Port)
		ps := fmt.Sprintf(`Start-Process netsh -Verb RunAs -WindowStyle Hidden -ArgumentList '%s'`, args)
		if err := run("powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps); err != nil {
			log.Printf("no se creó la regla del cortafuegos: %v", err)
		}
	}
}

func openWith(target string) {
	cmd := exec.Command("rundll32", "url.dll,FileProtocolHandler", target)
	hideWindow(cmd)
	cmd.Start()
}

// panelTitle: comienzo del título de la ventana de estado (el <title> de panel.html).
const panelTitle = "GPU Prestada — tarjeta local"

var (
	user32                  = syscall.NewLazyDLL("user32.dll")
	procEnumWindows         = user32.NewProc("EnumWindows")
	procGetWindowTextW      = user32.NewProc("GetWindowTextW")
	procIsWindowVisible     = user32.NewProc("IsWindowVisible")
	procIsIconic            = user32.NewProc("IsIconic")
	procShowWindow          = user32.NewProc("ShowWindow")
	procSetForegroundWindow = user32.NewProc("SetForegroundWindow")

	findMu    sync.Mutex
	findCB    uintptr
	findFound uintptr
	lastOpen  time.Time
)

// findPanelWindow busca una ventana visible cuyo título empiece por panelTitle.
func findPanelWindow() uintptr {
	findMu.Lock()
	defer findMu.Unlock()
	if findCB == 0 { // las retrollamadas de Windows no se liberan: se crea una sola
		findCB = syscall.NewCallback(func(hwnd, _ uintptr) uintptr {
			if v, _, _ := procIsWindowVisible.Call(hwnd); v == 0 {
				return 1
			}
			buf := make([]uint16, 256)
			n, _, _ := procGetWindowTextW.Call(hwnd, uintptr(unsafe.Pointer(&buf[0])), uintptr(len(buf)))
			if n > 0 && strings.HasPrefix(syscall.UTF16ToString(buf[:n]), panelTitle) {
				findFound = hwnd
				return 0 // encontrada: dejar de enumerar
			}
			return 1
		})
	}
	findFound = 0
	procEnumWindows.Call(findCB, 0)
	return findFound
}

// openPanel muestra la ventana de estado. Si ya está abierta (aunque esté
// minimizada) la trae al frente en vez de abrir otra; si no, la abre con Edge
// en modo aplicación (sin barra de direcciones) o, si no hay Edge, con el
// navegador predeterminado.
func openPanel() {
	if h := findPanelWindow(); h != 0 {
		if min, _, _ := procIsIconic.Call(h); min != 0 {
			procShowWindow.Call(h, 9) // SW_RESTORE
		}
		procSetForegroundWindow.Call(h)
		return
	}
	findMu.Lock()
	recent := time.Since(lastOpen) < 5*time.Second // se está abriendo: ignorar clics repetidos
	if !recent {
		lastOpen = time.Now()
	}
	findMu.Unlock()
	if recent {
		return
	}
	for _, base := range []string{os.Getenv("ProgramFiles(x86)"), os.Getenv("ProgramFiles")} {
		edge := filepath.Join(base, "Microsoft", "Edge", "Application", "msedge.exe")
		if _, err := os.Stat(edge); err == nil && base != "" {
			if exec.Command(edge, "--app="+panelURL, "--window-size=600,860").Start() == nil {
				return
			}
		}
	}
	openWith(panelURL)
}

func quitApp() { systray.Quit() }

func runTray(s *State) {
	systray.Run(func() {
		systray.SetIcon(trayIcon(2, 0))
		systray.SetTitle(appName)
		systray.SetTooltip(appName)
		systray.SetOnTapped(openPanel) // clic izquierdo en el icono: abrir la ventana
		mOpen := systray.AddMenuItem("Abrir GPU Prestada", "Muestra la ventana de estado")
		systray.AddSeparator()
		mStatus := systray.AddMenuItem("Arrancando…", "")
		mStatus.Disable()
		mGPU := systray.AddMenuItem("", "")
		mGPU.Disable()
		systray.AddSeparator()
		mLend := systray.AddMenuItemCheckbox("Prestar la tarjeta", "Permite que el servidor use esta gráfica", s.cfg.Lending)
		mTS := systray.AddMenuItem("Instalar Tailscale…", "Abre la página de descarga de Tailscale")
		mLog := systray.AddMenuItem("Ver registro", "")
		systray.AddSeparator()
		mQuit := systray.AddMenuItem("Salir", "Deja de prestar la tarjeta y cierra")

		go func() {
			last := -1
			for {
				line := s.statusLine()
				mStatus.SetTitle(line)
				systray.SetTooltip(appName + ": " + line)
				s.mu.Lock()
				g, ts, lend := s.gpu, s.tsIP, s.cfg.Lending
				s.mu.Unlock()
				if lend != mLend.Checked() { // por si se cambió desde la ventana
					if lend {
						mLend.Check()
					} else {
						mLend.Uncheck()
					}
				}
				if g.OK {
					mGPU.SetTitle(fmt.Sprintf("%s · %d de %d MB libres · %d %%", g.Name, g.TotalMB-g.UsedMB, g.TotalMB, g.Util))
				} else {
					mGPU.SetTitle("Sin tarjeta NVIDIA detectada")
				}
				if ts == "" && !s.useTunnel() {
					mTS.Show()
				} else {
					mTS.Hide()
				}
				// el icono lleva un medidor con la carga que se está cediendo
				if key := s.trayState()*100 + gaugeLevel(s.lentLoad()); key != last {
					systray.SetIcon(trayIcon(key/100, key%100))
					last = key
				}
				time.Sleep(time.Second)
			}
		}()

		for {
			select {
			case <-mOpen.ClickedCh:
				openPanel()
			case <-mLend.ClickedCh:
				s.mu.Lock()
				on := !s.cfg.Lending
				s.mu.Unlock()
				s.setLending(on)
			case <-mTS.ClickedCh:
				openWith(tailscaleURL)
			case <-mLog.ClickedCh:
				openWith(filepath.Join(dataDir(), "gpu-prestada.log"))
			case <-mQuit.ClickedCh:
				s.shutdown()
				return
			}
		}
	}, func() { os.Exit(0) })
}

// powerEvents: diagnóstico de energía, solo lectura. Eventos del registro «System» que explican
// suspensiones, reanudaciones, apagados y reinicios, y la configuración de energía activa.
func powerEvents() map[string]string {
	q := `*[System[(EventID=1 or EventID=12 or EventID=13 or EventID=41 or EventID=42 or EventID=107 or EventID=187 or ` +
		`EventID=506 or EventID=507 or EventID=566 or EventID=1074 or EventID=6005 or EventID=6006 or EventID=6008) ` +
		`and TimeCreated[timediff(@SystemTime) <= 21600000]]]`
	cmds := map[string][]string{
		"eventos":          {"wevtutil", "qe", "System", "/q:" + q, "/rd:true", "/c:60", "/f:text"},
		"estados":          {"powercfg", "/a"},
		"ultimo_despertar": {"powercfg", "/lastwake"},
		"esquema":          {"powercfg", "/getactivescheme"},
		"tapa_y_reposo":    {"powercfg", "/query", "SCHEME_CURRENT", "SUB_SLEEP"},
		"tapa":             {"powercfg", "/query", "SCHEME_CURRENT", "SUB_BUTTONS"},
	}
	out := map[string]string{}
	for k, c := range cmds {
		cmd := exec.Command(c[0], c[1:]...)
		hideWindow(cmd)
		b, err := cmd.CombinedOutput()
		if err != nil {
			out[k] = "ERROR: " + err.Error() + "\n"
		}
		out[k] += string(b)
		if len(out[k]) > 60000 {
			out[k] = out[k][:60000]
		}
	}
	return out
}

var (
	pSetThreadExecutionState = windows.NewLazySystemDLL("kernel32.dll").NewProc("SetThreadExecutionState")
	pGetSystemPowerStatus    = windows.NewLazySystemDLL("kernel32.dll").NewProc("GetSystemPowerStatus")
)

// onACPower: true si el portátil está enchufado (o es un sobremesa).
func onACPower() bool {
	var st struct {
		ACLineStatus, BatteryFlag, BatteryLifePercent, SystemStatusFlag byte
		BatteryLifeTime, BatteryFullLifeTime                            uint32
	}
	if r, _, _ := pGetSystemPowerStatus.Call(uintptr(unsafe.Pointer(&st))); r == 0 {
		return true
	}
	return st.ACLineStatus != 0 // 1 = enchufado, 255 = desconocido (sobremesa)
}

// keepAwake (1.10.2): mientras el PC presta su tarjeta y está ENCHUFADO, pide a Windows que no se
// suspenda por inactividad (como un reproductor de vídeo; la pantalla se sigue apagando). Con batería,
// o con el préstamo apagado, Windows vuelve a su comportamiento normal. Hace falta en los portátiles con
// «modo de espera moderno» (S0): ahí la espera congela el agente aunque la red siga conectada, y algunos
// fabricantes (Intel DPTF, esif_uf.exe) restablecen el plan de energía al arrancar.
func keepAwake(s *State) {
	runtime.LockOSThread() // el estado es por hilo: siempre desde este mismo
	const esContinuous, esSystemRequired = 0x80000000, 0x00000001
	held := false
	for {
		s.mu.Lock()
		want := s.cfg.Lending && !s.quitting && !s.cfg.AllowSleep
		s.mu.Unlock()
		want = want && onACPower()
		if want != held {
			flags := uintptr(esContinuous)
			if want {
				flags |= esSystemRequired
			}
			pSetThreadExecutionState.Call(flags)
			held = want
			log.Printf("mantener el PC despierto mientras presta (enchufado): %v", want)
		}
		time.Sleep(30 * time.Second)
	}
}

// diskOK: ¿hay sitio para «need» bytes más (con 5 GB de margen) en la unidad de «dir»?
func diskOK(dir string, need int64) error {
	os.MkdirAll(dir, 0o755)
	p, _ := windows.UTF16PtrFromString(dir)
	var free, total, totalFree uint64
	if err := windows.GetDiskFreeSpaceEx(p, &free, &total, &totalFree); err != nil {
		return nil
	}
	if int64(free) < need+5<<30 {
		return fmt.Errorf("falta espacio en disco: hacen falta %d GB y hay %d GB libres", (need+5<<30)>>30, free>>30)
	}
	return nil
}

var pGlobalMemoryStatusEx = windows.NewLazySystemDLL("kernel32.dll").NewProc("GlobalMemoryStatusEx")

// sysMem: RAM total y disponible del PC, en MB (1.12.1).
func sysMem() (total, avail int64) {
	var m struct {
		Length, MemoryLoad                         uint32
		TotalPhys, AvailPhys, TotalPage, AvailPage uint64
		TotalVirtual, AvailVirtual, AvailExtended  uint64
	}
	m.Length = uint32(unsafe.Sizeof(m))
	if r, _, _ := pGlobalMemoryStatusEx.Call(uintptr(unsafe.Pointer(&m))); r == 0 {
		return -1, -1
	}
	return int64(m.TotalPhys >> 20), int64(m.AvailPhys >> 20)
}

// sysDisk: tamaño y espacio libre (MB) de la unidad donde guarda sus cosas el programa (1.12.1).
func sysDisk() (drive string, total, free int64) {
	dir := dataDir()
	os.MkdirAll(dir, 0o755)
	p, _ := windows.UTF16PtrFromString(dir)
	var fr, tot, totFree uint64
	if err := windows.GetDiskFreeSpaceEx(p, &fr, &tot, &totFree); err != nil {
		return "", -1, -1
	}
	return filepath.VolumeName(dir), int64(tot >> 20), int64(fr >> 20)
}

// cpuName: nombre comercial del procesador, del registro de Windows (1.12.4).
func cpuName() string {
	k, err := registry.OpenKey(registry.LOCAL_MACHINE, `HARDWARE\DESCRIPTION\System\CentralProcessor\0`, registry.QUERY_VALUE)
	if err != nil {
		return ""
	}
	defer k.Close()
	v, _, _ := k.GetStringValue("ProcessorNameString")
	return strings.Join(strings.Fields(v), " ")
}

// ---------- «Programas con IA» (1.13.0) ----------

func canLaunch() bool { return true }

// openConsole abre una consola nueva y visible que ejecuta la orden (ollama launch es interactivo).
func openConsole(title, line string) error {
	title = reBadName.ReplaceAllString(title, "-")
	cmd := exec.Command(os.Getenv("ComSpec"))
	if cmd.Path == "" {
		cmd = exec.Command("cmd.exe")
	}
	// línea de órdenes literal: cmd.exe no entiende las comillas escapadas al estilo de Go
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true,
		CmdLine: fmt.Sprintf(`cmd.exe /c start "%s" cmd.exe /k "%s"`, title, line)}
	// en la carpeta del usuario, nunca en la del programa (podía acabar en C:\Windows\System32) (1.14.3)
	if home := os.Getenv("USERPROFILE"); home != "" {
		cmd.Dir = home
	}
	return cmd.Start()
}

// createShortcut crea en el escritorio un acceso directo que abre una consola con la orden.
func createShortcut(name, line, icon string) (string, error) {
	name = strings.TrimSpace(reBadName.ReplaceAllString(name, "-"))
	q := func(s string) string { return "'" + strings.ReplaceAll(s, "'", "''") + "'" }
	ps := fmt.Sprintf(`$d=[Environment]::GetFolderPath('Desktop'); $p=Join-Path $d (%s + '.lnk');
$s=(New-Object -ComObject WScript.Shell).CreateShortcut($p); $s.TargetPath=$env:ComSpec;
$s.Arguments=%s; $s.WorkingDirectory=$env:USERPROFILE; $s.IconLocation=%s;
$s.Description='Abre con Ollama (GPU Prestada)'; $s.Save(); Write-Output $p`,
		q(name), q(`/k "`+line+`"`), q(icon+",0"))
	u := utf16.Encode([]rune(ps))
	b := make([]byte, len(u)*2)
	for i, c := range u {
		b[2*i], b[2*i+1] = byte(c), byte(c>>8)
	}
	cmd := exec.Command("powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
		"-EncodedCommand", base64.StdEncoding.EncodeToString(b))
	hideWindow(cmd)
	out, err := cmd.CombinedOutput()
	if err != nil {
		return "", fmt.Errorf("no se pudo crear el acceso directo: %v %s", err, lastLine(string(out)))
	}
	return strings.TrimSpace(lastLine(string(out))), nil
}

// pidOnPort: proceso que escucha en 127.0.0.1:<port> (netstat; vale con Windows en cualquier idioma).
func pidOnPort(port int) int {
	cmd := exec.Command("netstat", "-ano", "-p", "TCP")
	hideWindow(cmd)
	out, err := cmd.Output()
	if err != nil {
		return 0
	}
	want := fmt.Sprintf("127.0.0.1:%d", port)
	for _, l := range strings.Split(string(out), "\n") {
		f := strings.Fields(l)
		if len(f) >= 5 && f[1] == want && (f[2] == "0.0.0.0:0" || strings.HasSuffix(f[2], ":0")) {
			if pid, err := strconv.Atoi(f[len(f)-1]); err == nil {
				return pid
			}
		}
	}
	return 0
}

func startNewVersion(target string) error { return exec.Command(target, relaunchArgs()...).Start() }
func linuxInstall(autostart bool) bool    { return false }
func clearOllamaHint(s *State)            {}

// launchCommand: orden para cmd.exe (programa y modelo ya validados con reSafe).
func launchCommand(host, exe, id, model string) string {
	line := fmt.Sprintf(`set OLLAMA_HOST=%s&& "%s" launch %s`, host, exe, id)
	if model != "" {
		line += " --model " + model
	}
	return line
}
