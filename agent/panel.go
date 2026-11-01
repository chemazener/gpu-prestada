package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"runtime"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

// Panel: ventana de estado. Es una página servida solo en este equipo
// (127.0.0.1) que se abre como ventana de aplicación.
const panelAddr = "127.0.0.1:11436"

var panelURL = "http://" + panelAddr + "/"

// logRing guarda las últimas líneas del registro para enseñarlas en el panel.
type logRing struct {
	mu    sync.Mutex
	lines []string
}

func (r *logRing) Write(p []byte) (int, error) {
	r.mu.Lock()
	for _, l := range strings.Split(strings.TrimRight(string(p), "\n"), "\n") {
		r.lines = append(r.lines, l)
	}
	if len(r.lines) > 60 {
		r.lines = r.lines[len(r.lines)-60:]
	}
	r.mu.Unlock()
	return len(p), nil
}

func (r *logRing) tail() []string {
	r.mu.Lock()
	defer r.mu.Unlock()
	return append([]string(nil), r.lines...)
}

var ring = &logRing{}

// panelSeen: última vez que la ventana pidió el estado (hay alguien mirando).
var panelSeen atomic.Int64

func (s *State) setLending(on bool) {
	s.mu.Lock()
	s.cfg.Lending = on
	s.autoPause, s.busyHits = false, 0
	s.mu.Unlock()
	s.saveConfig()
	log.Printf("préstamo activado: %v", on)
	go s.tick() // que el servidor se entere ya
}

// setToken guarda el código de alta introducido en la ventana (modo túnel).
func (s *State) setToken(tok string) {
	s.mu.Lock()
	s.cfg.Token = tok
	s.mu.Unlock()
	s.saveConfig()
	log.Printf("código de alta guardado")
}

func (s *State) statusJSON() map[string]any {
	line, state, lent, tunnel := s.statusLine(), s.trayState(), s.lentLoad(), s.useTunnel()
	s.mu.Lock()
	defer s.mu.Unlock()
	mode := "tailscale"
	if tunnel {
		mode = "tunnel"
	}
	_, relayHost := relayHTTP(s.cfg)
	return map[string]any{
		"version": version, "name": s.cfg.Name, "broker": s.cfg.Broker,
		"mode": mode, "relay": relayHost, "has_token": s.cfg.Token != "", "tunnel_ok": tunnel && s.listening.Load(),
		"state": state, "status": line, "lending": s.cfg.Lending, "auto_pause": s.autoPause,
		"gpu": map[string]any{"ok": s.gpu.OK, "name": s.gpu.Name, "total": s.gpu.TotalMB, "used": s.gpu.UsedMB, "util": s.gpu.Util,
			"temp": s.gpu.Temp, "power": s.gpu.Power, "power_limit": s.gpu.PowerLimit, "fan": s.gpu.Fan},
		"tailscale_ip": s.tsIP, "ollama_ok": s.ollamaOK, "broker_ok": s.brokerOK,
		"client_enabled": s.clientPerm.Enabled, "client_port": clientPortOf(s.cfg),
		"client_gateway": strings.Contains(s.cfg.Ollama, movedOllama) || runtime.GOOS == "linux",
		"gateway_port":   map[bool]int{true: gatewayPort, false: linuxGatewayPort}[strings.Contains(s.cfg.Ollama, movedOllama)],
		"models":         s.models, "want_models": s.wantModels, "task": s.task, "progress": s.progress,
		"lent_load": lent, "served": s.served.Load(), "inflight": atomic.LoadInt32(&s.inflight), "log": ring.tail(),
	}
}

// sameOrigin evita que otra página abierta en el navegador accione el panel.
func sameOrigin(r *http.Request) bool {
	o := r.Header.Get("Origin")
	return r.Host == panelAddr && (o == "" || o == "http://"+panelAddr)
}

// takeOver: si ya hay otra copia en marcha, o le pide que enseñe su ventana
// (misma versión) y devuelve false, o le pide que se cierre para sustituirla.
func takeOver(show bool) bool {
	c := &http.Client{Timeout: 2 * time.Second}
	resp, err := c.Get(panelURL + "api/status")
	if err != nil {
		return true // no hay otra copia
	}
	var st struct {
		Version string `json:"version"`
	}
	json.NewDecoder(resp.Body).Decode(&st)
	resp.Body.Close()
	if st.Version == version {
		if show {
			c.Post(panelURL+"api/show", "application/json", nil)
		}
		return false
	}
	log.Printf("hay en marcha la versión %s: la sustituyo por la %s", st.Version, version)
	c.Post(panelURL+"api/quit", "application/json", nil)
	for i := 0; i < 20; i++ {
		time.Sleep(300 * time.Millisecond)
		if _, err := c.Get(panelURL + "api/status"); err != nil {
			break
		}
	}
	return true
}

func (s *State) servePanel() {
	mux := http.NewServeMux()
	static := func(ctype string, body []byte) http.HandlerFunc {
		return func(w http.ResponseWriter, r *http.Request) {
			w.Header().Set("Content-Type", ctype)
			w.Header().Set("Cache-Control", "no-cache")
			http.ServeContent(w, r, "", time.Time{}, bytes.NewReader(body))
		}
	}
	mux.HandleFunc("/logo.png", static("image/png", logoPNG))
	mux.HandleFunc("/favicon.ico", static("image/x-icon", appIcon))
	mux.HandleFunc("/api/status", func(w http.ResponseWriter, r *http.Request) {
		panelSeen.Store(time.Now().Unix())
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(s.statusJSON())
	})
	post := func(path string, f func(r *http.Request)) {
		mux.HandleFunc(path, func(w http.ResponseWriter, r *http.Request) {
			if r.Method != http.MethodPost || !sameOrigin(r) {
				http.Error(w, "no permitido", http.StatusForbidden)
				return
			}
			f(r)
			fmt.Fprint(w, `{"ok":true}`)
		})
	}
	post("/api/lending", func(r *http.Request) {
		var b struct {
			On bool `json:"on"`
		}
		if json.NewDecoder(r.Body).Decode(&b) == nil {
			s.setLending(b.On)
		}
	})
	post("/api/token", func(r *http.Request) {
		var b struct {
			Token string `json:"token"`
		}
		if json.NewDecoder(r.Body).Decode(&b) == nil && strings.TrimSpace(b.Token) != "" {
			s.setToken(strings.TrimSpace(b.Token))
		}
	})
	lh := s.launchHandler() // «Programas con IA» (1.13.0)
	mux.Handle("/api/launch", lh)
	mux.Handle("/api/launch/", lh)
	post("/api/show", func(r *http.Request) { go openPanel() })
	post("/api/quit", func(r *http.Request) { go s.shutdown() })
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/" {
			http.NotFound(w, r)
			return
		}
		static("text/html; charset=utf-8", panelHTML)(w, r)
	})
	for { // al sustituir a otra copia, el puerto puede tardar un instante en quedar libre
		err := http.ListenAndServe(panelAddr, mux)
		log.Printf("no puedo abrir el panel en %s: %v (reintento)", panelAddr, err)
		time.Sleep(3 * time.Second)
	}
}

// shutdown: último latido avisando de que ya no se presta, y fuera.
func (s *State) shutdown() {
	shutdownOnce.Do(func() {
		s.mu.Lock()
		s.quitting = true
		s.mu.Unlock()
		s.tick()
		stopServices()
		quitApp()
	})
}

var shutdownOnce sync.Once
