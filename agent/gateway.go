package main

// «Ollama combinado» (desde la 1.9.0): el agente escucha en el puerto normal de Ollama
// (127.0.0.1:11434) y el Ollama propio del PC pasa a 127.0.0.1:11439 (ver
// migrateOllamaPort). Así cualquier programa que use Ollama ve, sin configurar nada,
// sus modelos de siempre y además los del broker:
//   - «modelo@servidor»  → la RTX 3080 del servidor
//   - «modelo@tarjeta»   → otra tarjeta prestada (p. ej. «gemma3:1b@nitro5-chem»)
// Lo que lleva «@» va al servidor por el mismo camino que el puerto 11444 (client.go);
// lo demás, al Ollama local. Sin permiso del administrador solo se ven los modelos locales.

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"runtime"
	"sort"
	"strings"
	"time"
)

const (
	gatewayPort      = 11434 // el puerto normal de Ollama
	linuxGatewayPort = 11438 // Linux: Ollama combinado sin tocar el Ollama del sistema (1.15.0)
	movedOllama      = "127.0.0.1:11439"
	serverSuffix     = "servidor"
)

// remoteModel: «modelo@algo» → (modelo, algo).
func remoteModel(m string) (string, string, bool) {
	i := strings.LastIndex(m, "@")
	if i <= 0 || i == len(m)-1 {
		return m, "", false
	}
	return m[:i], strings.ToLower(m[i+1:]), true
}

func (s *State) gatewayHandler() http.Handler {
	remote := s.clientHandler()
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		s.mu.Lock()
		local := s.cfg.Ollama
		s.mu.Unlock()
		switch {
		case r.Method == http.MethodGet && r.URL.Path == "/api/tags":
			s.mergedTags(w, local)
			return
		case r.Method == http.MethodGet && r.URL.Path == "/v1/models":
			s.mergedOpenAIModels(w, local)
			return
		}
		if r.Method == http.MethodPost && r.Body != nil {
			body, err := io.ReadAll(io.LimitReader(r.Body, 256<<20))
			r.Body.Close()
			if err != nil {
				jsonError(w, http.StatusBadRequest, "no pude leer la petición")
				return
			}
			var m map[string]any
			if json.Unmarshal(body, &m) == nil {
				name, _ := m["model"].(string)
				if name == "" {
					name, _ = m["name"].(string) // /api/show antiguo
				}
				if clean, card, ok := remoteModel(name); ok {
					// «@servidor» = la 3080: el servidor recibe el nombre limpio. «@tarjeta» se deja
					// (el proxy lo entiende en chat/generate/embed); en lo demás se limpia.
					gen := strings.HasPrefix(r.URL.Path, "/api/chat") || strings.HasPrefix(r.URL.Path, "/api/generate") ||
						strings.HasPrefix(r.URL.Path, "/api/embed") || strings.HasPrefix(r.URL.Path, "/v1/")
					if card == serverSuffix || !gen {
						if _, ok := m["model"]; ok {
							m["model"] = clean
						}
						if _, ok := m["name"]; ok {
							m["name"] = clean
						}
						body, _ = json.Marshal(m)
					}
					r.Body = io.NopCloser(bytes.NewReader(body))
					r.ContentLength = int64(len(body))
					r.Header.Del("Content-Length")
					remote.ServeHTTP(w, r)
					return
				}
			}
			r.Body = io.NopCloser(bytes.NewReader(body))
			r.ContentLength = int64(len(body))
		}
		u, err := url.Parse(local)
		if err != nil {
			jsonError(w, http.StatusBadGateway, "Ollama local mal configurado")
			return
		}
		rp := httputil.NewSingleHostReverseProxy(u)
		rp.FlushInterval = -1
		rp.ErrorHandler = func(w http.ResponseWriter, r *http.Request, err error) {
			jsonError(w, http.StatusBadGateway, "el Ollama de este PC no responde: "+err.Error())
		}
		r.Host = u.Host
		rp.ServeHTTP(w, r)
	})
}

// remoteNames: modelos del broker que este PC puede usar, ya con su «@…».
func (s *State) remoteNames() (server []map[string]any, cards []string) {
	s.mu.Lock()
	perm, cfg := s.clientPerm, s.cfg
	s.mu.Unlock()
	if !perm.Enabled || cfg.Token == "" {
		return nil, nil
	}
	for node, models := range perm.Cards {
		for _, m := range models {
			cards = append(cards, m+"@"+node)
		}
	}
	sort.Strings(cards)
	// lista de la 3080: se pide al servidor por el mismo camino que las peticiones
	rec := &bufWriter{hdr: http.Header{}}
	req, _ := http.NewRequest(http.MethodGet, "http://local/api/tags", nil)
	s.clientHandler().ServeHTTP(rec, req)
	if rec.code == 0 || rec.code == http.StatusOK {
		var t struct {
			Models []map[string]any `json:"models"`
		}
		if json.Unmarshal(rec.buf.Bytes(), &t) == nil {
			for _, m := range t.Models {
				for _, k := range []string{"name", "model"} {
					if v, ok := m[k].(string); ok {
						m[k] = v + "@" + serverSuffix
					}
				}
				server = append(server, m)
			}
		}
	}
	return server, cards
}

func (s *State) mergedTags(w http.ResponseWriter, local string) {
	out := []map[string]any{}
	if resp, err := httpc.Get(local + "/api/tags"); err == nil {
		var t struct {
			Models []map[string]any `json:"models"`
		}
		json.NewDecoder(resp.Body).Decode(&t)
		resp.Body.Close()
		out = append(out, t.Models...)
	}
	server, cards := s.remoteNames()
	out = append(out, server...)
	now := time.Now().UTC().Format(time.RFC3339)
	for _, c := range cards {
		out = append(out, map[string]any{"name": c, "model": c, "modified_at": now, "size": 0, "digest": "",
			"details": map[string]any{"format": "gguf"}})
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]any{"models": out})
}

func (s *State) mergedOpenAIModels(w http.ResponseWriter, local string) {
	data := []map[string]any{}
	if resp, err := httpc.Get(local + "/v1/models"); err == nil {
		var t struct {
			Data []map[string]any `json:"data"`
		}
		json.NewDecoder(resp.Body).Decode(&t)
		resp.Body.Close()
		data = append(data, t.Data...)
	}
	server, cards := s.remoteNames()
	for _, m := range server {
		data = append(data, map[string]any{"id": m["name"], "object": "model", "created": 0, "owned_by": "servidor"})
	}
	for _, c := range cards {
		data = append(data, map[string]any{"id": c, "object": "model", "created": 0, "owned_by": "gpu-prestada"})
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]any{"object": "list", "data": data})
}

// bufWriter: http.ResponseWriter en memoria (para pedir la lista al servidor).
type bufWriter struct {
	hdr  http.Header
	code int
	buf  bytes.Buffer
}

func (b *bufWriter) Header() http.Header         { return b.hdr }
func (b *bufWriter) WriteHeader(c int)           { b.code = c }
func (b *bufWriter) Write(p []byte) (int, error) { return b.buf.Write(p) }
func (b *bufWriter) Flush()                      {}

// serveGateway ocupa el puerto normal de Ollama cuando el Ollama del PC ya se ha movido.
// gatewayListenPort: 11434 si el Ollama del PC se movió (Windows); en Linux, 11438 sin tocar el del sistema.
func (s *State) gatewayListenPort() int {
	if s.ollamaMoved() {
		return gatewayPort
	}
	if runtime.GOOS == "linux" {
		return linuxGatewayPort
	}
	return 0
}

func (s *State) serveGateway() {
	port := s.gatewayListenPort()
	if port == 0 {
		return
	}
	addr := fmt.Sprintf("127.0.0.1:%d", port)
	for {
		ln, err := net.Listen("tcp", addr)
		if err != nil { // el Ollama de antes aún no ha soltado el puerto
			time.Sleep(10 * time.Second)
			continue
		}
		log.Printf("Ollama combinado en http://%s (modelos de este PC + los del broker con «@»)", addr)
		srv := &http.Server{Handler: s.gatewayHandler(), ReadHeaderTimeout: 15 * time.Second}
		err = srv.Serve(ln)
		log.Printf("Ollama combinado cerrado: %v", err)
		time.Sleep(5 * time.Second)
	}
}

func (s *State) ollamaMoved() bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	return strings.Contains(s.cfg.Ollama, movedOllama)
}
