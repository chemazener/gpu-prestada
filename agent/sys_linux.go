//go:build linux

package main

// GPU Prestada para Linux (desde la 1.15.0). Un solo ejecutable: la primera vez se instala en
// ~/.local/bin, crea un servicio de usuario de systemd (arranca con la sesión y se reinicia solo),
// añade «GPU Prestada» al menú de aplicaciones y abre la ventana en el navegador (es la misma
// página local que en Windows). Presta NVIDIA (nvidia-smi) y AMD (rocm-smi) con el Ollama del PC.
// Whisper e imágenes (ComfyUI) siguen siendo solo de Windows.

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"
)

const linuxUnit = "gpu-prestada.service"

func hideWindow(cmd *exec.Cmd)            {}
func nvidiaSmiPath() string               { return "nvidia-smi" }
func ensureVulkan(s *State)               {}
func restartOllama()                      {}
func reinstallOllama(s *State)            {}
func diskOK(dir string, need int64) error { return nil }
func pidOnPort(port int) int              { return 0 }
func keepAwake(s *State)                  {}
func migrateOllamaPort(s *State)          {}
func ollamaLogPath() string               { return filepath.Join(dataDir(), "ollama-serve.log") }
func ollamaAppLog() string                { return "" }
func readGPUProcsGeneric() []GPUProc      { return nil }
func powerEvents() map[string]string {
	return map[string]string{"error": "solo en Windows"}
}
func runTray(s *State) { select {} } // sin icono de bandeja: la ventana es la del navegador

func underSystemd() bool { return os.Getenv("INVOCATION_ID") != "" }

func home() string { h, _ := os.UserHomeDir(); return h }

func installedPath() string { return filepath.Join(home(), ".local", "bin", "gpu-prestada") }

func updateTarget(self string) string {
	if _, err := os.Stat(installedPath()); err == nil {
		return installedPath()
	}
	return self
}

func relaunchArgs() []string { return []string{"-inicio"} }

var restartForUpdate bool

// startNewVersion: con el servicio de systemd basta con salir (Restart=always arranca el fichero nuevo).
func startNewVersion(target string) error {
	if underSystemd() {
		restartForUpdate = true
		return nil
	}
	return exec.Command(target, relaunchArgs()...).Start()
}

func quitApp() {
	if !restartForUpdate && underSystemd() { // «Cerrar el programa»: que systemd no lo vuelva a arrancar
		exec.Command("systemctl", "--user", "stop", linuxUnit).Start()
		time.Sleep(time.Second)
	}
	os.Exit(0)
}

// openURL abre una página en el navegador del escritorio.
func openURL(u string) {
	for _, c := range [][]string{{"xdg-open", u}, {"gio", "open", u}, {"sensible-browser", u}} {
		if p, err := exec.LookPath(c[0]); err == nil {
			cmd := exec.Command(p, c[1:]...)
			cmd.SysProcAttr = &syscall.SysProcAttr{Setsid: true}
			if cmd.Start() == nil {
				return
			}
		}
	}
	log.Printf("abre en el navegador: %s", u)
}

func openPanel() { openURL(panelURL) }

// linuxInstall: al ejecutarlo a mano (no como servicio) se instala y deja el trabajo al servicio de
// systemd; si ya está instalado y en marcha, solo abre la ventana. Devuelve true si este proceso debe salir.
func linuxInstall(autostart bool) bool {
	if underSystemd() || autostart {
		return false
	}
	if exec.Command("systemctl", "--user", "show-environment").Run() != nil {
		return false // sin systemd de usuario: se queda en marcha en primer plano
	}
	self, _ := os.Executable()
	dst := installedPath()
	same := fileSHA256(self) == fileSHA256(dst)
	if !same {
		os.MkdirAll(filepath.Dir(dst), 0o755)
		b, err := os.ReadFile(self)
		if err != nil || os.WriteFile(dst+".new", b, 0o755) != nil || os.Rename(dst+".new", dst) != nil {
			fmt.Println("No puedo instalarme en", dst, err)
			return false
		}
	}
	unit := filepath.Join(home(), ".config", "systemd", "user", linuxUnit)
	os.MkdirAll(filepath.Dir(unit), 0o755)
	os.WriteFile(unit, []byte(`[Unit]
Description=GPU Prestada (presta la tarjeta gráfica a la IA de LoMasTrend)
After=network-online.target

[Service]
ExecStart=`+dst+` -inicio
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
`), 0o644)
	icon := filepath.Join(home(), ".local", "share", "icons", "gpu-prestada.png")
	os.MkdirAll(filepath.Dir(icon), 0o755)
	os.WriteFile(icon, logoPNG, 0o644)
	desk := filepath.Join(home(), ".local", "share", "applications", "gpu-prestada.desktop")
	os.MkdirAll(filepath.Dir(desk), 0o755)
	os.WriteFile(desk, []byte(`[Desktop Entry]
Type=Application
Name=GPU Prestada
Comment=Presta tu tarjeta gráfica a la IA de LoMasTrend
Exec=`+dst+`
Icon=`+icon+`
Terminal=false
Categories=Utility;
`), 0o644)
	exec.Command("systemctl", "--user", "daemon-reload").Run()
	exec.Command("systemctl", "--user", "enable", linuxUnit).Run()
	active := exec.Command("systemctl", "--user", "is-active", "--quiet", linuxUnit).Run() == nil
	if !active || !same {
		fmt.Println("GPU Prestada instalada en", dst, "· arranca con tu sesión (servicio", linuxUnit+")")
		exec.Command("systemctl", "--user", "restart", linuxUnit).Run()
	}
	for i := 0; i < 40; i++ { // que la ventana esté lista antes de abrirla
		if resp, err := http.Get(panelURL + "api/status"); err == nil {
			resp.Body.Close()
			break
		}
		time.Sleep(500 * time.Millisecond)
	}
	ensureLinger()
	if os.Getenv("DISPLAY") == "" && os.Getenv("WAYLAND_DISPLAY") == "" { // servidor sin escritorio (SSH)
		fmt.Println("Sin escritorio: manéjalo con «gpu-prestada» (menú) o «gpu-prestada ayuda».")
		if isTerminal(0) { // abierto a mano en una terminal: el menú de texto
			runMenu()
			return true
		}
		fmt.Println("Ventana desde otro equipo: ssh -L 11436:127.0.0.1:11436 "+os.Getenv("USER")+"@<este-equipo>  y abre", panelURL)
		return true
	}
	openPanel()
	fmt.Println("Ventana:", panelURL)
	return true
}

// ensureLinger: que el servicio de usuario siga en marcha sin sesión abierta (servidores por SSH) y arranque
// con el equipo. Si systemd no deja activarlo sin permisos, se explica cómo.
func ensureLinger() {
	u := os.Getenv("USER")
	if out, _ := exec.Command("loginctl", "show-user", u, "-p", "Linger", "--value").Output(); strings.TrimSpace(string(out)) == "yes" {
		return
	}
	if exec.Command("loginctl", "enable-linger").Run() == nil {
		return
	}
	fmt.Println("Para que siga en marcha al cerrar la sesión (y arranque con el equipo): sudo loginctl enable-linger " + u)
}

// firstRunSetup: en Linux lo hace linuxInstall al abrirlo a mano.
func firstRunSetup(s *State) {}

func ollamaExe() string {
	p, _ := exec.LookPath("ollama")
	return p
}

const ollamaHint = "Sin Ollama no se presta la tarjeta (para usar la IA del servidor no hace falta). Para prestarla: curl -fsSL https://ollama.com/install.sh | sh"

var ollamaNextTry time.Time

// ensureOllama: si Ollama está instalado pero parado (y no lo lleva un servicio del sistema), lo arranca.
func ensureOllama(s *State) {
	exe := ollamaExe()
	if exe == "" {
		s.setTask(ollamaHint, -1)
		return
	}
	if time.Now().Before(ollamaNextTry) {
		return
	}
	ollamaNextTry = time.Now().Add(45 * time.Second)
	if exec.Command("systemctl", "is-enabled", "--quiet", "ollama").Run() == nil {
		log.Printf("Ollama del sistema parado: arráncalo con «sudo systemctl start ollama»")
		return
	}
	log.Printf("Ollama instalado pero parado: lo arranco")
	cmd := exec.Command(exe, "serve")
	if f, err := os.Create(ollamaLogPath()); err == nil {
		cmd.Stdout, cmd.Stderr = f, f
		defer f.Close()
	}
	cmd.SysProcAttr = &syscall.SysProcAttr{Setsid: true}
	cmd.Start()
}

func clearOllamaHint(s *State) {
	s.mu.Lock()
	if s.task == ollamaHint {
		s.task, s.progress = "", -1
	}
	s.mu.Unlock()
}

// readGPUGeneric: AMD con rocm-smi (NVIDIA ya va por nvidia-smi).
func readGPUGeneric() GPU {
	ctx, cancel := context.WithTimeout(context.Background(), 8*time.Second)
	defer cancel()
	out, err := exec.CommandContext(ctx, "rocm-smi", "--showproductname", "--showuse", "--showmeminfo", "vram",
		"--showtemp", "--showpower", "--json").Output()
	if err != nil {
		return GPU{}
	}
	var d map[string]map[string]any
	if json.Unmarshal(out, &d) != nil {
		return GPU{}
	}
	num := func(m map[string]any, keys ...string) float64 {
		for k, v := range m {
			for _, want := range keys {
				if strings.Contains(strings.ToLower(k), strings.ToLower(want)) {
					if f, err := strconv.ParseFloat(strings.TrimSpace(fmt.Sprint(v)), 64); err == nil {
						return f
					}
				}
			}
		}
		return -1
	}
	for card, m := range d {
		if !strings.HasPrefix(card, "card") {
			continue
		}
		total, used := num(m, "VRAM Total Memory"), num(m, "VRAM Total Used")
		if total <= 0 {
			continue
		}
		name := "AMD Radeon"
		for k, v := range m {
			if strings.Contains(k, "Card Series") || strings.Contains(k, "Card SKU") {
				name = strings.TrimSpace(fmt.Sprint(v))
				break
			}
		}
		t, p := num(m, "Temperature (Sensor edge)", "Temperature"), num(m, "Package Power", "Power (W)")
		return GPU{Name: name, Vendor: "AMD", TotalMB: int(total / (1 << 20)), UsedMB: int(used / (1 << 20)),
			Util: int(max(0, num(m, "GPU use"))), Temp: int(t), Power: int(p), PowerLimit: -1, Fan: -1, OK: true}
	}
	return GPU{}
}

// ---------- «Programas con IA» en Linux: terminal del escritorio y lanzadores del menú ----------

var terminals = [][]string{
	{"x-terminal-emulator", "-e", "bash", "-lc"}, {"gnome-terminal", "--", "bash", "-lc"}, {"konsole", "-e", "bash", "-lc"},
	{"xfce4-terminal", "-x", "bash", "-lc"}, {"kgx", "--", "bash", "-lc"}, {"tilix", "-e", "bash", "-lc"},
	{"kitty", "bash", "-lc"}, {"alacritty", "-e", "bash", "-lc"}, {"xterm", "-e", "bash", "-lc"},
}

func findTerminal() []string {
	for _, t := range terminals {
		if _, err := exec.LookPath(t[0]); err == nil {
			return t
		}
	}
	return nil
}

func canLaunch() bool { return findTerminal() != nil }

// launchCommand: orden de shell (programa y modelo ya validados con reSafe).
func launchCommand(host, exe, id, model string) string {
	line := fmt.Sprintf("export OLLAMA_HOST=%s; '%s' launch %s", host, exe, id)
	if model != "" {
		line += " --model " + model
	}
	return line + "; echo; read -r -p 'Pulsa Intro para cerrar' _"
}

func openConsole(title, line string) error {
	t := findTerminal()
	if t == nil {
		return errors.New("no encuentro ningún terminal")
	}
	cmd := exec.Command(t[0], append(t[1:], line)...)
	cmd.Dir = home()
	cmd.SysProcAttr = &syscall.SysProcAttr{Setsid: true}
	return cmd.Start()
}

// createShortcut: lanzador en el menú de aplicaciones (y en el Escritorio si existe).
func createShortcut(name, line, icon string) (string, error) {
	safe := reBadName.ReplaceAllString(strings.ToLower(name), "-")
	safe = strings.Trim(strings.Join(strings.Fields(safe), "-"), "-")
	body := "[Desktop Entry]\nType=Application\nName=" + name + "\nComment=Abre con Ollama (GPU Prestada)\n" +
		"Exec=bash -lc \"" + strings.NewReplacer(`\`, `\\`, `"`, `\"`, "`", "\\`", "$", `\$`).Replace(line) + "\"\n" +
		"Icon=" + filepath.Join(home(), ".local", "share", "icons", "gpu-prestada.png") + "\nTerminal=true\nCategories=Development;\n"
	p := filepath.Join(home(), ".local", "share", "applications", "gpu-prestada-"+safe+".desktop")
	os.MkdirAll(filepath.Dir(p), 0o755)
	if err := os.WriteFile(p, []byte(body), 0o755); err != nil {
		return "", err
	}
	for _, d := range []string{"Escritorio", "Desktop"} {
		if st, err := os.Stat(filepath.Join(home(), d)); err == nil && st.IsDir() {
			os.WriteFile(filepath.Join(home(), d, "gpu-prestada-"+safe+".desktop"), []byte(body), 0o755)
		}
	}
	return p, nil
}

// claudePath: Claude Code, también si está instalado con npm en el directorio del usuario.
func claudePath() string {
	if p, err := exec.LookPath("claude"); err == nil {
		return p
	}
	for _, p := range []string{
		filepath.Join(home(), ".npm-global", "bin", "claude"), filepath.Join(home(), ".local", "bin", "claude"),
		filepath.Join(home(), ".claude", "local", "claude"),
	} {
		if fi, err := os.Stat(p); err == nil && !fi.IsDir() {
			return p
		}
	}
	return ""
}

// nativeIntegrations: lo que se abre sin el comando ollama (1.17.0).
func nativeIntegrations() []integration {
	if claudePath() != "" {
		return []integration{{"claude", "Claude Code"}}
	}
	return nil
}

// nativeLaunchLine: orden de shell para abrir Claude Code sin «ollama launch» (modelo ya validado con reSafe).
func nativeLaunchLine(host, id, model string, ctx int) string {
	exe := claudePath()
	if id != "claude" || model == "" || exe == "" {
		return ""
	}
	line := ""
	for _, e := range claudeEnv(host, model, ctx) {
		k, v, _ := strings.Cut(e, "=")
		line += fmt.Sprintf("export %s='%s'; ", k, v)
	}
	return line + fmt.Sprintf("cd; '%s' --model '%s'; echo; read -r -p 'Pulsa Intro para cerrar' _", exe, model)
}
