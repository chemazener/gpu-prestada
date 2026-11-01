//go:build linux

package main

// Órdenes de terminal (desde la 1.16.0), para manejar GPU Prestada en un servidor sin escritorio:
//   gpu-prestada estado | codigo <CÓDIGO> | pausar | reanudar | parar | desinstalar | ayuda
// Hablan con el servicio en marcha por su API local (127.0.0.1:11436).

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

func cliPost(path string, body any) error {
	b, _ := json.Marshal(body)
	c := &http.Client{Timeout: 10 * time.Second}
	resp, err := c.Post(panelURL+strings.TrimPrefix(path, "/"), "application/json", bytes.NewReader(b))
	if err != nil {
		return fmt.Errorf("GPU Prestada no está en marcha (%v). Arráncala con: systemctl --user start gpu-prestada", err)
	}
	resp.Body.Close()
	if resp.StatusCode != 200 {
		return fmt.Errorf("respuesta %d", resp.StatusCode)
	}
	return nil
}

type cliState struct {
	Version, Name, Status, Mode string
	HasToken                    bool `json:"has_token"`
	TunnelOK                    bool `json:"tunnel_ok"`
	OllamaOK                    bool `json:"ollama_ok"`
	BrokerOK                    bool `json:"broker_ok"`
	Lending                     bool
	ClientEnabled               bool `json:"client_enabled"`
	GatewayPort                 int  `json:"gateway_port"`
	Models                      []string
	Task                        string
	GPU                         struct {
		OK                bool
		Name              string
		Total, Used, Util int
	}
}

func fetchStatus() (cliState, error) {
	var d cliState
	c := &http.Client{Timeout: 10 * time.Second}
	resp, err := c.Get(panelURL + "api/status")
	if err != nil {
		return d, err
	}
	defer resp.Body.Close()
	return d, json.NewDecoder(resp.Body).Decode(&d)
}

func si(b bool) string {
	if b {
		return "sí"
	}
	return "no"
}

func cliStatus() int {
	d, err := fetchStatus()
	if err != nil {
		fmt.Println("GPU Prestada no está en marcha. Arráncala con: systemctl --user start gpu-prestada")
		return 1
	}
	fmt.Printf("GPU Prestada %s · equipo «%s»\n", d.Version, d.Name)
	fmt.Printf("  Estado:             %s\n", d.Status)
	if d.GPU.OK {
		fmt.Printf("  Tarjeta:            %s · %d de %d MiB · %d %% de uso\n", d.GPU.Name, d.GPU.Used, d.GPU.Total, d.GPU.Util)
	} else {
		fmt.Println("  Tarjeta:            no detectada (hacen falta nvidia-smi o rocm-smi)")
	}
	fmt.Printf("  Ollama:             %s · modelos: %s\n", si(d.OllamaOK), strings.Join(d.Models, ", "))
	via := map[string]string{"tunnel": "túnel cifrado", "tailscale": "Tailscale", "auto": "automática"}[d.Mode]
	fmt.Printf("  Conexión:           %s (%s)\n", si(d.BrokerOK || d.TunnelOK), via)
	fmt.Printf("  Dado de alta:       %s\n", si(d.HasToken))
	fmt.Printf("  Prestando:          %s\n", si(d.Lending))
	fmt.Printf("  IA del servidor:    %s", si(d.ClientEnabled))
	if d.ClientEnabled {
		fmt.Printf(" · Ollama combinado en 127.0.0.1:%d", d.GatewayPort)
	}
	fmt.Println()
	if d.Task != "" {
		fmt.Printf("  Aviso:              %s\n", d.Task)
	}
	fmt.Printf("  Ventana:            %s (desde otro equipo: ssh -L 11436:127.0.0.1:11436 %s@<este-equipo>)\n", panelURL, os.Getenv("USER"))
	return 0
}

func cliUninstall() int {
	exec.Command("systemctl", "--user", "disable", "--now", linuxUnit).Run()
	h, _ := os.UserHomeDir()
	for _, p := range []string{
		filepath.Join(h, ".config", "systemd", "user", linuxUnit),
		filepath.Join(h, ".local", "bin", "gpu-prestada"),
		filepath.Join(h, ".local", "share", "applications", "gpu-prestada.desktop"),
		filepath.Join(h, ".local", "share", "icons", "gpu-prestada.png"),
	} {
		os.Remove(p)
	}
	matches, _ := filepath.Glob(filepath.Join(h, ".local", "share", "applications", "gpu-prestada-*.desktop"))
	for _, m := range matches {
		os.Remove(m)
	}
	launchers, _ := filepath.Glob(filepath.Join(h, ".local", "bin", "ia-*")) // órdenes creadas desde el menú
	for _, l := range launchers {
		if b, err := os.ReadFile(l); err == nil && strings.Contains(string(b), "creado por GPU Prestada") {
			os.Remove(l)
		}
	}
	exec.Command("systemctl", "--user", "daemon-reload").Run()
	fmt.Println("GPU Prestada desinstalada. Su configuración sigue en", dataDir(), "(bórrala si no la vas a volver a usar).")
	return 0
}

// runCLI: devuelve el código de salida, o -1 si no es una orden (seguir con el arranque normal).
func runCLI(args []string) int {
	if len(args) == 0 {
		return -1
	}
	fail := func(err error) int {
		if err != nil {
			fmt.Println("Error:", err)
			return 1
		}
		return 0
	}
	switch args[0] {
	case "menu", "menú":
		return runMenu()
	case "estado", "status":
		return cliStatus()
	case "codigo", "código", "code":
		if len(args) < 2 {
			fmt.Println("Uso: gpu-prestada codigo <CÓDIGO-DE-ALTA>")
			return 2
		}
		if r := fail(cliPost("api/token", map[string]string{"token": args[1]})); r != 0 {
			return r
		}
		fmt.Println("Código guardado; en unos segundos se conecta al servidor (mira: gpu-prestada estado).")
		return 0
	case "pausar":
		return fail(cliPost("api/lending", map[string]bool{"on": false}))
	case "reanudar", "prestar":
		return fail(cliPost("api/lending", map[string]bool{"on": true}))
	case "parar", "salir":
		return fail(cliPost("api/quit", nil))
	case "desinstalar":
		return cliUninstall()
	case "ayuda", "help", "-h", "--help":
		fmt.Print(`GPU Prestada · órdenes:
  gpu-prestada               instala (la primera vez) y abre la ventana (sin escritorio: el menú)
  gpu-prestada menu          menú de texto: estado, préstamo, programas con IA, alta…
  gpu-prestada estado        cómo está: tarjeta, Ollama, conexión, alta…
  gpu-prestada codigo XXXX   pega el código de alta (si no te dan de alta desde el Monitor VRAM)
  gpu-prestada pausar        deja de prestar la tarjeta   ·   gpu-prestada reanudar
  gpu-prestada parar         cierra el programa (el servicio no vuelve a arrancar hasta el próximo inicio)
  gpu-prestada desinstalar   quita el servicio y el programa
`)
		return 0
	}
	fmt.Println("Orden desconocida:", args[0], "· mira: gpu-prestada ayuda")
	return 2
}
