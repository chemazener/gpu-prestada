package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"runtime"
	"sync/atomic"
	"time"
)

// Actualización automática. El servidor anuncia en cada latido la huella
// (SHA-256) del programa que tiene publicado; si no coincide con la de este
// ejecutable, se descarga el nuevo, se comprueba su huella, se sustituye y se
// relanza. Solo se hace cuando no hay trabajo prestado en curso.

func fileSHA256(path string) string {
	f, err := os.Open(path)
	if err != nil {
		return ""
	}
	defer f.Close()
	h := sha256.New()
	if _, err := io.Copy(h, f); err != nil {
		return ""
	}
	return hex.EncodeToString(h.Sum(nil))
}

// downloadFile descarga url en dest con el cliente dado, informando del avance; devuelve la huella.
func downloadFile(hc *http.Client, url, dest string, progress func(pct int)) (string, error) {
	resp, err := hc.Get(url)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return "", fmt.Errorf("HTTP %d", resp.StatusCode)
	}
	f, err := os.Create(dest)
	if err != nil {
		return "", err
	}
	h := sha256.New()
	buf := make([]byte, 256<<10)
	var done int64
	last := -1
	for {
		n, rerr := resp.Body.Read(buf)
		if n > 0 {
			if _, err = f.Write(buf[:n]); err != nil {
				break
			}
			h.Write(buf[:n])
			done += int64(n)
			if resp.ContentLength > 0 {
				if pct := int(done * 100 / resp.ContentLength); pct != last {
					last = pct
					progress(pct)
				}
			}
		}
		if rerr == io.EOF {
			break
		}
		if rerr != nil {
			err = rerr
			break
		}
	}
	if cerr := f.Close(); err == nil {
		err = cerr
	}
	if err != nil {
		os.Remove(dest)
		return "", err
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}

var (
	updating   atomic.Bool
	updateNext atomic.Int64 // no reintentar antes de este instante (unix)
)

// checkUpdateNow consulta la huella del agente publicado y se actualiza si hace
// falta, sin esperar al latido: así un ejecutable recién descargado (aunque aún
// no tenga código de alta) se pone al día nada más arrancar. Se repite cada hora.
func (s *State) checkUpdateNow() {
	s.mu.Lock()
	cfg := s.cfg
	s.mu.Unlock()
	var src string
	var hc *http.Client
	switch {
	case s.useTunnel():
		base, host := relayHTTP(cfg)
		sni := cfg.RelaySNI
		if sni == "" {
			sni = host
		}
		src, hc = base+"/agent.json"+platformQuery(), pinnedClient(cfg.RelayPin, sni, 20*time.Second)
	case tailscaleIP() != "":
		src, hc = cfg.Broker+"/_nodes/agent.json"+platformQuery(), &http.Client{Timeout: 20 * time.Second}
	default:
		return
	}
	resp, err := hc.Get(src)
	if err != nil {
		log.Printf("no puedo comprobar si hay versión nueva: %v", err)
		return
	}
	defer resp.Body.Close()
	var r struct {
		SHA string `json:"sha256"`
	}
	if resp.StatusCode == 200 && json.NewDecoder(resp.Body).Decode(&r) == nil {
		s.maybeUpdate(r.SHA)
	}
}

// maybeUpdate se llama tras cada latido con la huella publicada en el servidor.
func (s *State) maybeUpdate(serverSHA string) {
	if serverSHA == "" || s.exeSHA == "" || serverSHA == s.exeSHA {
		return
	}
	if atomic.LoadInt32(&s.inflight) > 0 || time.Now().Unix() < updateNext.Load() {
		return
	}
	if !updating.CompareAndSwap(false, true) {
		return
	}
	go func() {
		defer updating.Store(false)
		if err := s.selfUpdate(serverSHA); err != nil {
			log.Printf("actualización fallida: %v (reintento en 10 minutos)", err)
			updateNext.Store(time.Now().Add(10 * time.Minute).Unix())
			s.setTask("", -1)
		}
	}()
}

func (s *State) selfUpdate(serverSHA string) error {
	s.mu.Lock()
	cfg := s.cfg
	s.mu.Unlock()
	self, err := os.Executable()
	if err != nil {
		return err
	}
	target := updateTarget(self)
	log.Printf("hay una versión nueva publicada: la descargo")
	s.setTask("Actualizando GPU Prestada…", 0)
	tmp := target + ".new"
	src, hc := cfg.Broker+"/_nodes/agent.exe"+platformQuery(), &http.Client{Timeout: 2 * time.Hour}
	if s.useTunnel() { // sin Tailscale: por el relé, con su certificado fijado
		base, host := relayHTTP(cfg)
		sni := cfg.RelaySNI
		if sni == "" {
			sni = host
		}
		src, hc = base+"/agent.exe"+platformQuery(), pinnedClient(cfg.RelayPin, sni, 2*time.Hour)
	}
	got, err := downloadFile(hc, src, tmp, func(pct int) {
		s.setTask(fmt.Sprintf("Actualizando GPU Prestada (%d %%)", pct), pct)
	})
	if err != nil {
		return fmt.Errorf("descarga: %w", err)
	}
	if got != serverSHA {
		os.Remove(tmp)
		return fmt.Errorf("la huella de la descarga no coincide con la publicada")
	}
	os.Chmod(tmp, 0o755)
	// Un ejecutable en marcha no se puede sobrescribir, pero sí renombrar.
	old := target + ".old"
	os.Remove(old)
	if _, err := os.Stat(target); err == nil {
		if err := os.Rename(target, old); err != nil {
			os.Remove(tmp)
			return fmt.Errorf("no puedo apartar la versión actual: %w", err)
		}
	}
	if err := os.Rename(tmp, target); err != nil {
		os.Rename(old, target) // deshacer
		return fmt.Errorf("no puedo colocar la versión nueva: %w", err)
	}
	if err := startNewVersion(target); err != nil {
		os.Remove(target)
		os.Rename(old, target) // deshacer
		return fmt.Errorf("no puedo arrancar la versión nueva: %w", err)
	}
	log.Printf("versión nueva instalada y arrancada: cierro esta (%s)", version)
	s.shutdown()
	return nil
}

// platform: sistema y arquitectura de este ejecutable («windows-amd64», «linux-amd64»…): cada uno se actualiza
// con el suyo (1.15.0). Windows sigue usando las rutas de siempre, sin parámetro, para los agentes antiguos.
func platform() string { return runtime.GOOS + "-" + runtime.GOARCH }

func platformQuery() string {
	if platform() == "windows-amd64" {
		return ""
	}
	return "?platform=" + platform()
}
