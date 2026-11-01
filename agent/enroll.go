package main

// Alta desde el Monitor VRAM (desde la 1.10.0): un PC sin Tailscale y sin código pide el alta por el
// relé con un identificador propio. Sale en «Equipos esperando alta» del Monitor VRAM y, cuando el
// administrador pulsa «Dar de alta», la respuesta trae el código y el agente lo guarda solo. Pegar el
// código a mano en la ventana sigue funcionando igual.

import (
	"bytes"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"log"
	"net/http"
	"time"
)

func (s *State) requestID() string {
	s.mu.Lock()
	defer s.mu.Unlock()
	if len(s.cfg.RequestID) < 16 {
		b := make([]byte, 16)
		rand.Read(b)
		s.cfg.RequestID = hex.EncodeToString(b)
		go s.saveConfig()
	}
	return s.cfg.RequestID
}

// askEnrollment: una petición de alta. Devuelve true si ya tiene código.
func (s *State) askEnrollment(cfg Config) bool {
	base, host := relayHTTP(cfg)
	if base == "" {
		return false
	}
	sni := host
	if cfg.RelaySNI != "" {
		sni = cfg.RelaySNI
	}
	s.mu.Lock()
	gpu := s.gpu
	s.mu.Unlock()
	body, _ := json.Marshal(map[string]any{"request_id": s.requestID(), "name": cfg.Name, "gpu": gpu.Name,
		"vram_total_mb": gpu.TotalMB, "version": version})
	c := &http.Client{Transport: relayTransport(cfg.RelayPin, sni), Timeout: 20 * time.Second}
	resp, err := c.Post(base+"/hello", "application/json", bytes.NewReader(body))
	if err != nil {
		return false
	}
	defer resp.Body.Close()
	var r struct {
		Token    string `json:"token"`
		Pending  bool   `json:"pending"`
		Rejected bool   `json:"rejected"`
	}
	if json.NewDecoder(resp.Body).Decode(&r) != nil {
		return false
	}
	s.mu.Lock()
	switch {
	case r.Token != "":
		s.cfg.Token, s.enroll = r.Token, ""
	case r.Rejected:
		s.enroll = "rechazada"
	case r.Pending:
		s.enroll = "esperando"
	}
	s.mu.Unlock()
	if r.Token != "" {
		s.saveConfig()
		log.Printf("alta concedida desde el Monitor VRAM")
		return true
	}
	return false
}
