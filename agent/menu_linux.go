//go:build linux

package main

// Menú de texto (desde la 1.17.0) para servidores sin escritorio: `gpu-prestada` en una terminal (o `gpu-prestada menu`).
// Se maneja con las flechas (o j/k), Intro para elegir y q/Esc para volver. Habla con el servicio en marcha
// por su API local, como las demás órdenes, y abre los «Programas con IA» en la propia terminal.

import (
	"bufio"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"

	"golang.org/x/sys/unix"
)

const (
	keyNone = iota
	keyUp
	keyDown
	keyEnter
	keyBack
	keyQuit
)

type term struct {
	fd    int
	saved *unix.Termios
	buf   []byte // teclas leídas de golpe que aún no se han atendido
}

func isTerminal(fd int) bool {
	_, err := unix.IoctlGetTermios(fd, unix.TCGETS)
	return err == nil
}

// raw: sin eco ni líneas; la lectura vuelve sola a los 2 s para refrescar el estado.
func (t *term) raw() {
	st, err := unix.IoctlGetTermios(t.fd, unix.TCGETS)
	if err != nil {
		return
	}
	if t.saved == nil {
		c := *st
		t.saved = &c
	}
	st.Lflag &^= unix.ECHO | unix.ICANON | unix.ISIG
	st.Iflag &^= unix.ICRNL | unix.IXON
	st.Cc[unix.VMIN], st.Cc[unix.VTIME] = 0, 20
	unix.IoctlSetTermios(t.fd, unix.TCSETS, st)
	fmt.Print("\x1b[?25l")
}

func (t *term) cooked() {
	if t.saved != nil {
		unix.IoctlSetTermios(t.fd, unix.TCSETS, t.saved)
	}
	fmt.Print("\x1b[?25h")
}

// key: una tecla cada vez, aunque lleguen varias juntas (al pegar o con la terminal lenta).
func (t *term) key() int {
	if len(t.buf) == 0 {
		b := make([]byte, 64)
		n, _ := os.Stdin.Read(b)
		if n == 0 {
			return keyNone
		}
		t.buf = b[:n]
	}
	seqs := []struct {
		s string
		k int
	}{
		{"\x1b[A", keyUp}, {"\x1bOA", keyUp}, {"\x1b[B", keyDown}, {"\x1bOB", keyDown},
		{"\x1b[C", keyEnter}, {"\x1b[D", keyBack}, {"k", keyUp}, {"j", keyDown}, {"\r", keyEnter},
		{"\n", keyEnter}, {" ", keyEnter}, {"q", keyBack}, {"\x7f", keyBack}, {"\x03", keyQuit}, {"\x04", keyQuit},
	}
	for _, q := range seqs {
		if strings.HasPrefix(string(t.buf), q.s) {
			t.buf = t.buf[len(q.s):]
			return q.k
		}
	}
	if t.buf[0] == 0x1b && len(t.buf) > 2 && t.buf[1] == '[' { // otra secuencia de escape: se ignora entera
		i := 2
		for i < len(t.buf) && (t.buf[i] < 0x40 || t.buf[i] > 0x7e) {
			i++
		}
		t.buf = t.buf[min(i+1, len(t.buf)):]
		return keyNone
	}
	k := t.buf[0]
	t.buf = t.buf[1:]
	if k == 0x1b {
		return keyBack
	}
	return keyNone
}

type menuItem struct {
	label string
	off   bool // título de grupo: no se puede elegir
}

// choose: lista navegable; header se vuelve a pintar cada 2 s. Devuelve el índice, o -1 al volver.
func (t *term) choose(title string, header func() string, items []menuItem, sel int) int {
	next := func(i, d int) int {
		for j := 0; j < len(items); j++ {
			i = (i + d + len(items)) % len(items)
			if !items[i].off {
				return i
			}
		}
		return i
	}
	if sel < 0 || sel >= len(items) || items[sel].off {
		sel = next(-1, 1)
		if sel < 0 {
			sel = 0
		}
	}
	h := ""
	for {
		if header != nil {
			h = header()
		}
		var b strings.Builder
		b.WriteString("\x1b[H\x1b[2J\x1b[1m GPU Prestada · " + title + "\x1b[0m\r\n\r\n")
		for _, l := range strings.Split(strings.TrimRight(h, "\n"), "\n") {
			if l != "" {
				b.WriteString(" " + l + "\r\n")
			}
		}
		if h != "" {
			b.WriteString("\r\n")
		}
		for i, it := range items {
			switch {
			case it.off:
				b.WriteString("  \x1b[2m" + it.label + "\x1b[0m\r\n")
			case i == sel:
				b.WriteString(" \x1b[7m › " + it.label + " \x1b[0m\r\n")
			default:
				b.WriteString("   " + it.label + "\r\n")
			}
		}
		b.WriteString("\r\n \x1b[2m↑↓ moverse · Intro elegir · q volver\x1b[0m")
		fmt.Print(b.String())
		switch t.key() {
		case keyUp:
			sel = next(sel, -1)
		case keyDown:
			sel = next(sel, 1)
		case keyEnter:
			if len(items) > 0 {
				return sel
			}
		case keyBack:
			return -1
		case keyQuit:
			return -2
		}
	}
}

// pause: muestra un texto y espera Intro (en modo normal de la terminal).
func (t *term) pause(text string) {
	t.cooked()
	fmt.Print("\x1b[H\x1b[2J" + strings.TrimRight(text, "\n") + "\n\nPulsa Intro para volver al menú…")
	bufio.NewReader(os.Stdin).ReadString('\n')
	t.raw()
}

func (t *term) ask(prompt string) string {
	t.cooked()
	fmt.Print("\x1b[H\x1b[2J" + prompt)
	l, _ := bufio.NewReader(os.Stdin).ReadString('\n')
	t.raw()
	return strings.TrimSpace(l)
}

func statusHeader(d cliState, err error) string {
	if err != nil {
		return "\x1b[31mGPU Prestada no está en marcha\x1b[0m (systemctl --user start gpu-prestada)"
	}
	ok := func(b bool) string {
		if b {
			return "\x1b[32msí\x1b[0m"
		}
		return "\x1b[33mno\x1b[0m"
	}
	h := fmt.Sprintf("Versión %s · equipo «%s»\n", d.Version, d.Name)
	if d.GPU.OK {
		h += fmt.Sprintf("Tarjeta: %s · %d de %d MiB · %d %%\n", d.GPU.Name, d.GPU.Used, d.GPU.Total, d.GPU.Util)
	} else {
		h += "Tarjeta: ninguna (este equipo solo puede usar la IA del servidor)\n"
	}
	h += fmt.Sprintf("Conectado: %s · Alta: %s · Prestando: %s · IA del servidor: %s", ok(d.BrokerOK || d.TunnelOK),
		ok(d.HasToken), ok(d.Lending && d.GPU.OK), ok(d.ClientEnabled))
	if d.ClientEnabled {
		h += fmt.Sprintf(" (127.0.0.1:%d)", d.GatewayPort)
	}
	h += "\n"
	if d.Task != "" && d.Task != ollamaHint {
		h += "\x1b[33m" + d.Task + "\x1b[0m\n"
	}
	return h
}

func runMenu() int {
	if !isTerminal(0) {
		fmt.Println("El menú necesita una terminal. Mira: gpu-prestada ayuda")
		return 2
	}
	t := &term{fd: 0}
	t.raw()
	defer func() { t.cooked(); fmt.Print("\x1b[H\x1b[2J") }()
	var last cliState
	var lastErr error
	var lastAt time.Time
	header := func() string {
		if time.Since(lastAt) > 1500*time.Millisecond {
			last, lastErr = fetchStatus()
			lastAt = time.Now()
		}
		return statusHeader(last, lastErr)
	}
	sel := 0
	for {
		header()
		var items []menuItem
		var acts []string
		add := func(act, label string) { acts, items = append(acts, act), append(items, menuItem{label: label}) }
		add("programas", "Programas con IA (Claude Code, Codex, OpenCode…)")
		if last.GPU.OK { // sin tarjeta no hay nada que prestar
			if last.Lending {
				add("prestar", "Pausar el préstamo de la tarjeta")
			} else {
				add("prestar", "Prestar la tarjeta")
			}
		}
		if !last.HasToken {
			add("codigo", "Pegar el código de alta")
		}
		add("registro", "Ver el registro")
		add("ventana", "Cómo abrir la ventana desde otro equipo")
		add("parar", "Parar GPU Prestada")
		add("desinstalar", "Desinstalar")
		add("salir", "Salir del menú (GPU Prestada sigue funcionando)")
		sel = t.choose("menú", header, items, sel)
		lastAt = time.Time{}
		act := "salir"
		if sel >= 0 {
			act = acts[sel]
		}
		switch act {
		case "programas":
			t.programs()
		case "prestar":
			if err := cliPost("api/lending", map[string]bool{"on": !last.Lending}); err != nil {
				t.pause("Error: " + err.Error())
			}
		case "codigo":
			if c := t.ask("Código de alta (en el Monitor VRAM, Herramientas › «🔑 Código de alta»; vacío = cancelar):\n> "); c != "" {
				if err := cliPost("api/token", map[string]string{"token": c}); err != nil {
					t.pause("Error: " + err.Error())
				}
			}
		case "registro":
			t.cooked()
			cmd := exec.Command("sh", "-c", "tail -n 200 '"+filepath.Join(dataDir(), "gpu-prestada.log")+"' | less +G")
			cmd.Stdin, cmd.Stdout, cmd.Stderr = os.Stdin, os.Stdout, os.Stderr
			cmd.Run()
			t.raw()
		case "ventana":
			t.pause(fmt.Sprintf("Desde tu equipo abre un túnel:\n\n   ssh -L 11436:127.0.0.1:11436 %s@<este-servidor>\n\ny entra en tu navegador en %s", os.Getenv("USER"), panelURL))
		case "parar":
			if t.confirm("¿Parar GPU Prestada? Deja de prestar y de dar la IA del servidor hasta el próximo arranque del equipo.") {
				cliPost("api/quit", nil)
				return 0
			}
		case "desinstalar":
			if t.confirm("¿Desinstalar GPU Prestada de este equipo?") {
				t.cooked()
				cliUninstall()
				return 0
			}
		case "salir":
			return 0
		}
	}
}

func (t *term) confirm(q string) bool {
	return t.choose(q, nil, []menuItem{{label: "No"}, {label: "Sí"}}, 0) == 1
}

type launchInfo struct {
	Available    bool
	Ollama       bool
	Integrations []integration
	Models       []string
	Ctx          map[string]int
	Host         string
	Hidden       int
}

func fetchLaunch() (launchInfo, error) {
	var l launchInfo
	c := &http.Client{Timeout: 3 * time.Minute}
	resp, err := c.Get(panelURL + "api/launch")
	if err != nil {
		return l, err
	}
	defer resp.Body.Close()
	return l, json.NewDecoder(resp.Body).Decode(&l)
}

var reLauncherName = regexp.MustCompile(`[^a-z0-9]+`)

// programs: elegir programa → modelo → abrirlo aquí o dejar una orden para abrirlo directamente.
func (t *term) programs() {
	fmt.Print("\x1b[H\x1b[2J\x1b[1m GPU Prestada · Programas con IA\x1b[0m\r\n\r\n Buscando los modelos que valen (la primera vez tarda un poco)…")
	l, err := fetchLaunch()
	if err != nil {
		t.pause("Error: " + err.Error())
		return
	}
	if len(l.Integrations) == 0 {
		t.pause("No hay ningún programa que abrir.\n\nCon el comando «ollama» se abren todos (Claude Code, Codex, OpenCode…):\n   curl -fsSL https://ollama.com/install.sh | sh\n\nSin él, solo Claude Code, si está instalado (npm install -g @anthropic-ai/claude-code).")
		return
	}
	note := ""
	if !l.Ollama {
		note = "Sin el comando «ollama» solo se abre Claude Code. Para el resto: curl -fsSL https://ollama.com/install.sh | sh\n"
	}
	var items []menuItem
	for _, it := range l.Integrations {
		items = append(items, menuItem{label: it.Name})
	}
	sel := 0
	for {
		sel = t.choose("Programas con IA", func() string { return note }, items, sel)
		if sel < 0 {
			return
		}
		it := l.Integrations[sel]
		model, ok := t.pickModel(it, l)
		if !ok {
			continue
		}
		t.launchMenu(it, model, l)
	}
}

// pickModel: modelos agrupados por máquina (este equipo, el servidor y cada tarjeta prestada).
func (t *term) pickModel(it integration, l launchInfo) (string, bool) {
	if len(l.Models) == 0 {
		msg := "No hay modelos que valgan para " + it.Name + " (hace falta que sepan usar herramientas y tengan contexto suficiente)."
		if st, _ := fetchStatus(); !st.ClientEnabled {
			msg += "\n\nEste equipo no tiene permiso para usar la IA del servidor: actívalo en el Monitor VRAM,\nficha de «" + st.Name + "», «💻 Usar la IA del servidor desde este PC»."
		}
		t.pause(msg)
		return "", false
	}
	groups := map[string][]string{}
	for _, m := range l.Models {
		g := "este equipo"
		if _, node, remote := remoteModel(m); remote {
			g = node
		}
		groups[g] = append(groups[g], m)
	}
	var names []string
	for g := range groups {
		names = append(names, g)
	}
	sort.Slice(names, func(i, j int) bool {
		rank := func(s string) int {
			switch s {
			case "este equipo":
				return 0
			case serverSuffix:
				return 1
			}
			return 2
		}
		if rank(names[i]) != rank(names[j]) {
			return rank(names[i]) < rank(names[j])
		}
		return names[i] < names[j]
	})
	var items []menuItem
	var vals []string
	for _, g := range names {
		title := g
		if g == serverSuffix {
			title = "servidor (RTX 3080)"
		}
		items = append(items, menuItem{label: "── " + title + " ──", off: true})
		vals = append(vals, "")
		for _, m := range groups[g] {
			lbl := m
			if c := l.Ctx[m]; c > 0 {
				lbl += fmt.Sprintf("   \x1b[2m%dk de contexto\x1b[0m", c/1000)
			}
			items = append(items, menuItem{label: lbl})
			vals = append(vals, m)
		}
	}
	h := ""
	if l.Hidden > 0 {
		h = fmt.Sprintf("\x1b[2m(%d modelos ocultos: no saben usar herramientas o tienen poco contexto)\x1b[0m\n", l.Hidden)
	}
	i := t.choose(it.Name+" · elige el modelo", func() string { return h }, items, 1)
	if i < 0 {
		return "", false
	}
	return vals[i], true
}

// commandFor: programa, argumentos y entorno para abrir el programa con ese modelo.
func commandFor(it integration, model string, l launchInfo) (string, []string, []string) {
	if l.Ollama {
		if exe := ollamaExe(); exe != "" {
			return exe, []string{"launch", it.ID, "--model", model}, []string{"OLLAMA_HOST=" + l.Host}
		}
	}
	return claudePath(), []string{"--model", model}, claudeEnv(l.Host, model, l.Ctx[model])
}

func (t *term) launchMenu(it integration, model string, l launchInfo) {
	if !reSafe.MatchString(it.ID) || !reSafe.MatchString(model) {
		t.pause("Nombre no válido")
		return
	}
	exe, args, env := commandFor(it, model, l)
	name := "ia-" + strings.Trim(reLauncherName.ReplaceAllString(strings.ToLower(it.ID+"-"+model), "-"), "-")
	switch t.choose(it.Name+" · "+model, nil, []menuItem{
		{label: "Abrirlo ahora en esta terminal"},
		{label: "Crear la orden «" + name + "» para abrirlo directamente"},
	}, 0) {
	case 0:
		t.cooked()
		fmt.Print("\x1b[H\x1b[2J")
		cmd := exec.Command(exe, args...)
		cmd.Env = append(os.Environ(), env...)
		cmd.Dir = home()
		cmd.Stdin, cmd.Stdout, cmd.Stderr = os.Stdin, os.Stdout, os.Stderr
		if err := cmd.Run(); err != nil {
			fmt.Println("\n", it.Name, "terminó:", err)
		}
		fmt.Print("\nPulsa Intro para volver al menú…")
		bufio.NewReader(os.Stdin).ReadString('\n')
		t.raw()
	case 1:
		var b strings.Builder
		b.WriteString("#!/bin/sh\n# " + it.Name + " con " + model + " · creado por GPU Prestada\n")
		for _, e := range env {
			k, v, _ := strings.Cut(e, "=")
			fmt.Fprintf(&b, "export %s='%s'\n", k, v)
		}
		fmt.Fprintf(&b, "exec '%s'", exe)
		for _, a := range args {
			fmt.Fprintf(&b, " '%s'", a)
		}
		b.WriteString(" \"$@\"\n")
		p := filepath.Join(home(), ".local", "bin", name)
		if err := os.WriteFile(p, []byte(b.String()), 0o755); err != nil {
			t.pause("Error: " + err.Error())
			return
		}
		t.pause("Hecho. Desde cualquier terminal de este equipo:\n\n   " + name + "\n\n(está en " + p + "; bórralo para quitarlo)")
	}
}
