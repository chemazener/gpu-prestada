package main

// Al revés que el préstamo (desde la 1.8.0): un puerto local (127.0.0.1:11444) que se
// comporta como un Ollama, para que los programas de este PC usen la IA del servidor
// (la RTX 3080 o cualquier otra tarjeta prestada, con «modelo@tarjeta» o la cabecera
// X-GPU-Node). Solo funciona si el administrador lo ha activado para este equipo en el
// Monitor VRAM; el servidor lo comprueba en cada petición y nunca devuelve la petición
// a este mismo equipo. Con Tailscale va directo al proxy; sin él, por el relé (HTTPS
// con el certificado fijado, como el túnel).

import (
	"fmt"
	"log"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"strings"
	"sync"
	"time"
)

const defaultClientPort = 11444

type clientPerm struct {
	Enabled     bool                `json:"enabled"`
	MaxInflight int                 `json:"max_inflight"`
	Cards       map[string][]string `json:"cards"` // modelos de cada otra tarjeta prestada usable (1.9.0)
}

func clientPortOf(c Config) int {
	if c.ClientPort > 0 {
		return c.ClientPort
	}
	return defaultClientPort
}

func (s *State) clientPort() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return clientPortOf(s.cfg)
}

func jsonError(w http.ResponseWriter, code int, msg string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	fmt.Fprintf(w, `{"error":%q}`, msg)
}

func (s *State) clientHandler() http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		s.mu.Lock()
		cfg, perm, broker := s.cfg, s.clientPerm, s.brokerOK
		s.mu.Unlock()
		switch {
		case cfg.Token == "":
			jsonError(w, http.StatusServiceUnavailable, "GPU Prestada aún no está dada de alta en el servidor")
			return
		case !broker:
			jsonError(w, http.StatusServiceUnavailable, "sin conexión con el servidor")
			return
		case !perm.Enabled:
			jsonError(w, http.StatusForbidden, "este equipo no tiene permiso para usar la IA del servidor (se activa en el Monitor VRAM)")
			return
		}
		var target *url.URL
		var transport http.RoundTripper = http.DefaultTransport
		if s.useTunnel() {
			base, host := relayHTTP(cfg)
			sni := host
			if cfg.RelaySNI != "" {
				sni = cfg.RelaySNI
			}
			target, _ = url.Parse(base + "/c")
			transport = relayTransport(cfg.RelayPin, sni)
		} else {
			target, _ = url.Parse(cfg.Broker)
		}
		if target == nil || target.Host == "" {
			jsonError(w, http.StatusServiceUnavailable, "servidor mal configurado")
			return
		}
		rp := &httputil.ReverseProxy{
			Rewrite: func(pr *httputil.ProxyRequest) {
				pr.SetURL(target)
				pr.Out.Header.Set("X-Client-Node", strings.ToLower(cfg.Name))
				pr.Out.Header.Set("X-Node-Token", cfg.Token)
				pr.Out.Header.Del("X-Forwarded-For")
			},
			Transport:     transport,
			FlushInterval: -1, // respuestas en streaming sin retener
			ErrorHandler: func(w http.ResponseWriter, r *http.Request, err error) {
				jsonError(w, http.StatusBadGateway, "el servidor no responde: "+err.Error())
			},
		}
		rp.ServeHTTP(w, r)
	})
}

var (
	rtMu    sync.Mutex
	rtCache = map[string]http.RoundTripper{}
)

// relayTransport reutiliza la conexión TLS con el relé entre peticiones.
func relayTransport(pin, sni string) http.RoundTripper {
	rtMu.Lock()
	defer rtMu.Unlock()
	k := pin + "|" + sni
	if rtCache[k] == nil {
		rtCache[k] = pinnedClient(pin, sni, 0).Transport
	}
	return rtCache[k]
}

// serveClient escucha solo en este equipo (127.0.0.1), nunca en su red.
func (s *State) serveClient() {
	for {
		addr := fmt.Sprintf("127.0.0.1:%d", s.clientPort())
		ln, err := net.Listen("tcp", addr)
		if err != nil {
			log.Printf("no puedo abrir el puerto local %s: %v (reintento en 1 min)", addr, err)
			time.Sleep(time.Minute)
			continue
		}
		log.Printf("IA del servidor para este PC en http://%s", addr)
		srv := &http.Server{Handler: s.clientHandler(), ReadHeaderTimeout: 15 * time.Second}
		err = srv.Serve(ln)
		log.Printf("puerto local cerrado: %v", err)
		time.Sleep(5 * time.Second)
	}
}
