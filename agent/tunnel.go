package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"encoding/base64"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"time"

	"github.com/coder/websocket"
)

// Modo túnel: para PCs sin Tailscale. En vez de escuchar y esperar a que el
// servidor se conecte, el agente abre UNA conexión saliente (WebSocket sobre TLS)
// con el relé de LoMasTrend y por ella le llegan las peticiones. El PC no abre
// ningún puerto ni instala nada más. El certificado del relé es propio: aquí va
// compilada la huella SHA-256 de su clave pública y no se acepta otra.
//
// Mensajes: texto = JSON {"t": …}; binario = <id 4 bytes big-endian><trozo de respuesta>.
//   agente → relé  hb (latido; el primero autentica con el token), res, res_end, res_err
//   relé → agente  hb_ok (respuesta del servidor al latido), req, cancel

const (
	defaultRelay    = "wss://casa.lomastrend.com/ws"
	defaultRelayPin = "da500f818ee87a04f717b511563c21b8cb95b24ff95c348f806b5c4be2a2569f"
	tunnelReadLimit = 64 << 20
)

type tunnelMsg struct {
	T       string            `json:"t"`
	ID      uint32            `json:"id,omitempty"`
	Code    int               `json:"code,omitempty"`
	Body    json.RawMessage   `json:"body,omitempty"`
	Method  string            `json:"method,omitempty"`
	Path    string            `json:"path,omitempty"`
	Headers map[string]string `json:"headers,omitempty"`
	BodyB64 string            `json:"body_b64,omitempty"`
	Status  int               `json:"status,omitempty"`
	Error   string            `json:"error,omitempty"`
}

type tunnel struct {
	mu      sync.Mutex
	conn    *websocket.Conn
	cancels map[uint32]context.CancelFunc
}

var tun = &tunnel{cancels: map[uint32]context.CancelFunc{}}

func (t *tunnel) set(c *websocket.Conn) {
	t.mu.Lock()
	t.conn = c
	t.mu.Unlock()
}

func (t *tunnel) current() *websocket.Conn {
	t.mu.Lock()
	defer t.mu.Unlock()
	return t.conn
}

func (t *tunnel) track(id uint32, cancel context.CancelFunc) {
	t.mu.Lock()
	t.cancels[id] = cancel
	t.mu.Unlock()
}

func (t *tunnel) untrack(id uint32) {
	t.mu.Lock()
	delete(t.cancels, id)
	t.mu.Unlock()
}

func (t *tunnel) cancel(id uint32) {
	t.mu.Lock()
	c := t.cancels[id]
	t.mu.Unlock()
	if c != nil {
		c()
	}
}

// useTunnel decide el modo: "tunnel" o "tailscale" si se fuerza en la
// configuración; en "auto", túnel cuando el PC no tiene Tailscale.
func (s *State) useTunnel() bool {
	s.mu.Lock()
	mode, ts := s.cfg.Mode, s.tsIP
	s.mu.Unlock()
	switch mode {
	case "tunnel":
		return true
	case "tailscale":
		return false
	}
	return ts == ""
}

// relayHTTP devuelve la base https:// del relé (para descargas) y su nombre.
func relayHTTP(cfg Config) (base, host string) {
	u, err := url.Parse(cfg.Relay)
	if err != nil || u.Host == "" {
		return "", cfg.Relay
	}
	scheme := "https"
	if u.Scheme == "ws" {
		scheme = "http"
	}
	return scheme + "://" + u.Host, u.Hostname()
}

// pinnedClient: cliente HTTPS que solo acepta el certificado cuya clave pública
// tiene la huella indicada (no hace falta que lo firme ninguna autoridad).
func pinnedClient(pin, sni string, timeout time.Duration) *http.Client {
	pin = strings.ToLower(strings.TrimSpace(pin))
	tr := &http.Transport{
		Proxy: http.ProxyFromEnvironment,
		TLSClientConfig: &tls.Config{
			ServerName:         sni,
			InsecureSkipVerify: true, // la verificación la hace VerifyPeerCertificate con la huella
			MinVersion:         tls.VersionTLS12,
			VerifyPeerCertificate: func(raw [][]byte, _ [][]*x509.Certificate) error {
				if pin == "" {
					return errors.New("sin huella del relé configurada")
				}
				for _, b := range raw {
					c, err := x509.ParseCertificate(b)
					if err != nil {
						continue
					}
					sum := sha256.Sum256(c.RawSubjectPublicKeyInfo)
					if hex.EncodeToString(sum[:]) == pin {
						return nil
					}
				}
				return errors.New("el certificado del relé no coincide con la huella conocida")
			},
		},
		TLSHandshakeTimeout: 15 * time.Second,
	}
	return &http.Client{Transport: tr, Timeout: timeout}
}

// tunnelLoop mantiene el túnel abierto mientras toque usarlo y haya token.
func (s *State) tunnelLoop() {
	wait := 5 * time.Second
	for {
		s.mu.Lock()
		cfg := s.cfg
		s.mu.Unlock()
		if !s.useTunnel() {
			time.Sleep(3 * time.Second)
			continue
		}
		if cfg.Token == "" { // sin código: se pide el alta al Monitor VRAM (o se espera a que lo peguen)
			if !s.askEnrollment(cfg) {
				time.Sleep(20 * time.Second)
			}
			continue
		}
		started := time.Now()
		err := s.runTunnel(cfg)
		s.listening.Store(false)
		s.setBroker(false)
		if time.Since(started) > time.Minute {
			wait = 5 * time.Second // llevaba un rato bien: fue un corte puntual
		}
		if websocket.CloseStatus(err) == 4001 {
			log.Printf("el servidor rechaza el código de alta: hay que volver a introducirlo")
			s.mu.Lock()
			s.cfg.Token = ""
			s.mu.Unlock()
			s.saveConfig()
			continue
		}
		log.Printf("túnel cerrado: %v (reintento en %s)", err, wait)
		time.Sleep(wait)
		if wait < 60*time.Second {
			wait *= 2
		}
	}
}

func (s *State) runTunnel(cfg Config) error {
	_, host := relayHTTP(cfg)
	sni := cfg.RelaySNI
	if sni == "" {
		sni = host
	}
	hc := pinnedClient(cfg.RelayPin, sni, 0)
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	c, _, err := websocket.Dial(ctx, cfg.Relay, &websocket.DialOptions{HTTPClient: hc})
	cancel()
	if err != nil {
		return err
	}
	c.SetReadLimit(tunnelReadLimit)
	defer c.CloseNow()
	tun.set(c)
	defer tun.set(nil)
	if err := s.sendHeartbeat(c); err != nil { // el primer latido es la autenticación
		return err
	}
	s.listening.Store(true)
	log.Printf("túnel abierto con %s", host)
	handler := s.nodeHandler()
	for {
		typ, data, err := c.Read(context.Background())
		if err != nil {
			return err
		}
		if typ != websocket.MessageText {
			continue
		}
		var m tunnelMsg
		if json.Unmarshal(data, &m) != nil {
			continue
		}
		switch m.T {
		case "hb_ok":
			s.applyHeartbeat(m.Code, m.Body)
		case "req":
			go s.serveTunnelReq(c, handler, m)
		case "cancel":
			tun.cancel(m.ID)
		}
	}
}

func wsWrite(c *websocket.Conn, typ websocket.MessageType, data []byte) error {
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	return c.Write(ctx, typ, data)
}

func wsWriteJSON(c *websocket.Conn, v any) error {
	b, err := json.Marshal(v)
	if err != nil {
		return err
	}
	return wsWrite(c, websocket.MessageText, b)
}

// sendHeartbeat manda el latido por el túnel; la respuesta llega después como hb_ok.
func (s *State) sendHeartbeat(c *websocket.Conn) error {
	body := s.heartbeatBody()
	s.mu.Lock()
	body["token"] = s.cfg.Token
	s.mu.Unlock()
	body["t"] = "hb"
	return wsWriteJSON(c, body)
}

// tickTunnel: la parte del latido en modo túnel (lo demás del tick es común).
func (s *State) tickTunnel() {
	c := tun.current()
	s.mu.Lock()
	tok := s.cfg.Token
	s.mu.Unlock()
	if c == nil || tok == "" {
		s.setBroker(false)
		return
	}
	if err := s.sendHeartbeat(c); err != nil {
		log.Printf("latido por el túnel: %v", err)
		s.setBroker(false)
	}
}

// serveTunnelReq atiende una petición llegada por el túnel con el mismo
// manejador que usa la escucha de Tailscale, escribiendo la respuesta en el túnel.
func (s *State) serveTunnelReq(c *websocket.Conn, handler http.Handler, m tunnelMsg) {
	body, err := base64.StdEncoding.DecodeString(m.BodyB64)
	if err != nil {
		wsWriteJSON(c, tunnelMsg{T: "res_err", ID: m.ID, Error: "cuerpo ilegible"})
		return
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	tun.track(m.ID, cancel)
	defer tun.untrack(m.ID)
	req, err := http.NewRequestWithContext(ctx, m.Method, "http://tunel"+m.Path, bytes.NewReader(body))
	if err != nil {
		wsWriteJSON(c, tunnelMsg{T: "res_err", ID: m.ID, Error: err.Error()})
		return
	}
	for k, v := range m.Headers {
		req.Header.Set(k, v)
	}
	req.ContentLength = int64(len(body))
	req.RemoteAddr = "tunel:0"
	w := &tunnelWriter{c: c, id: m.ID, hdr: http.Header{}}
	handler.ServeHTTP(w, req)
	if !w.wrote {
		w.WriteHeader(http.StatusOK)
	}
	if w.err != nil {
		return // el túnel se cayó a medias: no hay a quién avisar
	}
	wsWriteJSON(c, tunnelMsg{T: "res_end", ID: m.ID})
}

// tunnelWriter es un http.ResponseWriter que escribe en el túnel.
type tunnelWriter struct {
	c     *websocket.Conn
	id    uint32
	hdr   http.Header
	wrote bool
	err   error
}

func (w *tunnelWriter) Header() http.Header { return w.hdr }

func (w *tunnelWriter) WriteHeader(code int) {
	if w.wrote {
		return
	}
	w.wrote = true
	h := make(map[string]string, len(w.hdr))
	for k, v := range w.hdr {
		h[k] = strings.Join(v, ", ")
	}
	w.err = wsWriteJSON(w.c, tunnelMsg{T: "res", ID: w.id, Status: code, Headers: h})
}

func (w *tunnelWriter) Write(p []byte) (int, error) {
	if !w.wrote {
		w.WriteHeader(http.StatusOK)
	}
	if w.err != nil {
		return 0, w.err
	}
	buf := make([]byte, 4+len(p))
	binary.BigEndian.PutUint32(buf, w.id)
	copy(buf[4:], p)
	if err := wsWrite(w.c, websocket.MessageBinary, buf); err != nil {
		w.err = fmt.Errorf("túnel: %w", err)
		return 0, w.err
	}
	return len(p), nil
}

func (w *tunnelWriter) Flush() {} // cada Write ya sale como un mensaje propio
