package main

// «Programas con IA» (desde la 1.13.0): desde la ventana se abre cualquiera de los programas que admite
// `ollama launch` (Claude Code, Codex, OpenCode, VS Code…) o se crea un acceso directo en el escritorio.
// Se usa el Ollama combinado (127.0.0.1:11434) cuando está activo, así que con permiso del administrador
// también se pueden elegir los modelos del servidor («modelo@servidor»).

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"os/exec"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
)

type integration struct {
	ID   string `json:"id"`
	Name string `json:"name"`
}

// Lista de reserva (Ollama 0.40.13) por si no se puede leer la ayuda de `ollama launch`.
var fallbackIntegrations = []integration{
	{"claude", "Claude Code"}, {"codex", "Codex"}, {"chatgpt", "ChatGPT"}, {"opencode", "OpenCode"},
	{"copilot", "Copilot CLI"}, {"vscode", "VS Code"}, {"cline", "Cline"}, {"qwen", "Qwen Code"},
	{"dsh", "DeepSeek Harness"}, {"kimi", "Kimi Code CLI"}, {"droid", "Droid"}, {"pi", "Pi"},
	{"hermes", "Hermes Agent"}, {"hermes-desktop", "Hermes Desktop"}, {"openclaw", "OpenClaw"},
	{"omp", "OMP"}, {"muse", "Muse Code"}, {"pool", "Pool"},
}

var reBadName = regexp.MustCompile(`[\\/:*?"<>|]+`)

var (
	reSafe      = regexp.MustCompile(`^[A-Za-z0-9._:@/+-]{1,120}$`)
	reIntegLine = regexp.MustCompile(`^\s{2}([a-z0-9][a-z0-9._-]*)\s{2,}(.+)$`)
	integMu     sync.Mutex
	integCache  []integration
	integAt     time.Time
)

// launchIntegrations: los programas que admite el Ollama de este PC (de su propia ayuda; 1 h en caché).
func launchIntegrations() []integration {
	integMu.Lock()
	defer integMu.Unlock()
	if integCache != nil && time.Since(integAt) < time.Hour {
		return integCache
	}
	exe := ollamaExe()
	if exe == "" {
		return fallbackIntegrations
	}
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, exe, "launch", "--help")
	hideWindow(cmd)
	out, _ := cmd.CombinedOutput()
	var list []integration
	in := false
	for _, l := range strings.Split(strings.ReplaceAll(string(out), "\r", ""), "\n") {
		if strings.HasPrefix(strings.TrimSpace(l), "Supported integrations") {
			in = true
			continue
		}
		if in {
			if strings.TrimSpace(l) == "" {
				break
			}
			if m := reIntegLine.FindStringSubmatch(l); m != nil {
				name := strings.TrimSpace(m[2])
				if i := strings.Index(name, " ("); i > 0 {
					name = name[:i]
				}
				list = append(list, integration{m[1], name})
			}
		}
	}
	if len(list) == 0 {
		list = fallbackIntegrations
	}
	integCache, integAt = list, time.Now()
	return list
}

// launchHost: el Ollama al que hablan los programas (el combinado si está activo).
func (s *State) launchHost() string {
	if p := s.gatewayListenPort(); p > 0 && portOpen(p) {
		return fmt.Sprintf("127.0.0.1:%d", p)
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	return strings.TrimPrefix(strings.TrimPrefix(s.cfg.Ollama, "http://"), "https://")
}

func (s *State) launchModels() []string {
	c := &http.Client{Timeout: 15 * time.Second}
	resp, err := c.Get("http://" + s.launchHost() + "/api/tags")
	if err != nil {
		return nil
	}
	defer resp.Body.Close()
	var t struct {
		Models []struct {
			Name string `json:"name"`
		} `json:"models"`
	}
	json.NewDecoder(resp.Body).Decode(&t)
	// Solo los que saben usar herramientas: todos estos programas las necesitan (1.14.6). Se pregunta a
	// Ollama (/api/show → capabilities) y se guarda una hora. Si no se puede comprobar o está retirado, fuera.
	var out []string
	var mu sync.Mutex
	var wg sync.WaitGroup
	sem := make(chan struct{}, 1) // de una en una: cada PC tiene un máximo de peticiones a la vez al servidor
	hidden := 0
	for _, m := range t.Models {
		wg.Add(1)
		go func(name string) {
			defer wg.Done()
			sem <- struct{}{}
			ok := s.modelUsable(name)
			<-sem
			mu.Lock()
			if ok {
				out = append(out, name)
			} else {
				hidden++
			}
			mu.Unlock()
		}(m.Name)
	}
	wg.Wait()
	s.mu.Lock()
	s.launchHidden = hidden
	s.mu.Unlock()
	sort.SliceStable(out, func(i, j int) bool { // primero los de este PC, luego los del servidor
		return !strings.Contains(out[i], "@") && strings.Contains(out[j], "@")
	})
	return out
}

var (
	capMu    sync.Mutex
	capCache = map[string]capEntry{}
)

type capEntry struct {
	ok  bool
	ctx int // num_ctx del modelo (0 = desconocido)
	at  time.Time
}

// modelCtx: el contexto conocido de un modelo (lo guarda modelUsable).
func modelCtx(name string) int {
	capMu.Lock()
	defer capMu.Unlock()
	return capCache[name].ctx
}

// modelUsable: ¿sabe usar herramientas y tiene contexto suficiente? (si no se puede comprobar, no)
func (s *State) modelUsable(name string) bool {
	capMu.Lock()
	if e, found := capCache[name]; found && time.Since(e.at) < time.Hour {
		capMu.Unlock()
		return e.ok
	}
	capMu.Unlock()
	ok, known, nctx := false, false, 0 // si no se puede comprobar, no se ofrece (se vuelve a preguntar en el siguiente refresco)
	b, _ := json.Marshal(map[string]string{"model": name})
	c := &http.Client{Timeout: 15 * time.Second}
	for try := 0; try < 4 && !known; try++ {
		resp, err := c.Post("http://"+s.launchHost()+"/api/show", "application/json", strings.NewReader(string(b)))
		if err != nil {
			break
		}
		var d struct {
			Capabilities []string `json:"capabilities"`
			Parameters   string   `json:"parameters"`
		}
		switch {
		case resp.StatusCode == http.StatusTooManyRequests: // el servidor ya atiende otras de este PC: reintento
			time.Sleep(time.Duration(try+1) * time.Second)
		case resp.StatusCode == http.StatusGone: // retirado de la nube de Ollama
			ok, known = false, true
		case resp.StatusCode == http.StatusOK && json.NewDecoder(resp.Body).Decode(&d) == nil && d.Capabilities != nil:
			ok, known = false, true
			for _, k := range d.Capabilities {
				if k == "tools" {
					ok = true
				}
			}
			// Y con contexto suficiente: Claude Code y compañía mandan ~23.000 tokens solo de instrucciones;
			// con menos, Ollama recorta y el modelo contesta sin sentido (1.14.8). Los de la nube van sobrados.
			nctx = ctxOf(d.Parameters)
			if ok && nctx < minLaunchCtx && !strings.Contains(name, "cloud") {
				ok = false
			}
		default:
			try = 4
		}
		resp.Body.Close()
	}
	if known { // lo dudoso no se guarda: se vuelve a preguntar en el siguiente refresco
		capMu.Lock()
		capCache[name] = capEntry{ok, nctx, time.Now()}
		capMu.Unlock()
	}
	return ok
}

func integrationName(id string) string {
	for _, it := range launchIntegrations() {
		if it.ID == id {
			return it.Name
		}
	}
	return id
}

// launchCmdLine: la orden para cmd.exe. Programa y modelo ya validados con reSafe.
func (s *State) launchCmdLine(id, model string) (string, error) {
	if !reSafe.MatchString(id) || (model != "" && !reSafe.MatchString(model)) {
		return "", errors.New("nombre no válido")
	}
	exe := ollamaExe()
	if exe == "" { // sin el comando ollama: Claude Code se abre directamente contra el Ollama combinado (1.17.0)
		if line := nativeLaunchLine(s.launchHost(), id, model, modelCtx(model)); line != "" {
			return line, nil
		}
		if id == "claude" && model == "" {
			return "", errors.New("elige un modelo")
		}
		return "", errors.New("Ollama no está instalado")
	}
	return launchCommand(s.launchHost(), exe, id, model), nil
}

func (s *State) launchHandler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/api/launch", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		integ, viaOllama := launchIntegrations(), ollamaExe() != ""
		if !viaOllama {
			integ = nativeIntegrations() // sin el comando ollama solo lo que se sabe abrir directamente
		}
		models := s.launchModels()
		ctx := map[string]int{}
		for _, m := range models {
			ctx[m] = modelCtx(m)
		}
		json.NewEncoder(w).Encode(map[string]any{
			"available": canLaunch() && len(integ) > 0, "integrations": integ, "ollama": viaOllama,
			"models": models, "ctx": ctx, "host": s.launchHost(), "hidden": s.launchHiddenCount(),
		})
	})
	act := func(path string, f func(id, model string) (string, error)) {
		mux.HandleFunc(path, func(w http.ResponseWriter, r *http.Request) {
			w.Header().Set("Content-Type", "application/json")
			if r.Method != http.MethodPost || !sameOrigin(r) {
				http.Error(w, "no permitido", http.StatusForbidden)
				return
			}
			var b struct {
				ID    string `json:"id"`
				Model string `json:"model"`
			}
			json.NewDecoder(r.Body).Decode(&b)
			res, err := f(strings.TrimSpace(b.ID), strings.TrimSpace(b.Model))
			if err != nil {
				json.NewEncoder(w).Encode(map[string]any{"ok": false, "error": err.Error()})
				return
			}
			json.NewEncoder(w).Encode(map[string]any{"ok": true, "result": res})
		})
	}
	act("/api/launch/open", func(id, model string) (string, error) {
		line, err := s.launchCmdLine(id, model)
		if err != nil {
			return "", err
		}
		return "", openConsole(integrationName(id), line)
	})
	act("/api/launch/shortcut", func(id, model string) (string, error) {
		line, err := s.launchCmdLine(id, model)
		if err != nil {
			return "", err
		}
		name := integrationName(id)
		if model != "" {
			name += " · " + model
		}
		return createShortcut(name+" (Ollama)", line, ollamaExe())
	})
	return mux
}

func (s *State) launchHiddenCount() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.launchHidden
}

const minLaunchCtx = 24000

var reNumCtx = regexp.MustCompile(`(?m)^num_ctx\s+(\d+)`)

// ctxOf: el num_ctx de los parámetros del modelo (0 = no lo fija: se queda con el pequeño por defecto de Ollama).
func ctxOf(params string) int {
	if m := reNumCtx.FindStringSubmatch(params); m != nil {
		n, _ := strconv.Atoi(m[1])
		return n
	}
	return 0
}

// claudeEnv: lo mismo que pone «ollama launch claude»: Claude Code habla con el Ollama combinado
// (API de Anthropic en /v1/messages) y usa el modelo elegido para todo.
func claudeEnv(host, model string, ctx int) []string {
	env := []string{
		"ANTHROPIC_BASE_URL=http://" + host, "ANTHROPIC_AUTH_TOKEN=ollama", "ANTHROPIC_API_KEY=",
		"ANTHROPIC_DEFAULT_OPUS_MODEL=" + model, "ANTHROPIC_DEFAULT_SONNET_MODEL=" + model,
		"ANTHROPIC_DEFAULT_HAIKU_MODEL=" + model, "CLAUDE_CODE_AUTO_MODE_SERVER=0",
	}
	if ctx > 0 { // que compacte la conversación antes de pasarse del contexto del modelo
		env = append(env, fmt.Sprintf("CLAUDE_CODE_MAX_CONTEXT_TOKENS=%d", ctx))
	}
	return env
}
