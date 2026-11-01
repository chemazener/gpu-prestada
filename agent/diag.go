package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"sync"
	"time"
)

// tailFile: las últimas n líneas de un fichero de texto (lee como mucho 256 KiB del final).
func tailFile(path string, n int) []string {
	if path == "" {
		return nil
	}
	f, err := os.Open(path)
	if err != nil {
		return nil
	}
	defer f.Close()
	st, _ := f.Stat()
	off := max(0, st.Size()-256<<10)
	buf := make([]byte, st.Size()-off)
	if _, err := f.ReadAt(buf, off); err != nil && len(buf) == 0 {
		return nil
	}
	lines := strings.Split(strings.ReplaceAll(string(buf), "\r", ""), "\n")
	return lines[max(0, len(lines)-n):]
}

// diag: lo que el servidor necesita para averiguar por qué Ollama no usa la gráfica.
func (s *State) diag() map[string]any {
	s.mu.Lock()
	gpu := s.gpu
	s.mu.Unlock()
	return map[string]any{
		"version": version, "gpu": gpu, "ollama_vulkan_env": os.Getenv("OLLAMA_VULKAN"), "ollama_igpu_env": os.Getenv("OLLAMA_IGPU_ENABLE"),
		"serve_log": tailFile(ollamaLogPath(), 200), "app_log": tailFile(ollamaAppLog(), 200),
		"agent_log": ring.tail(),
		"svc_logs": map[string][]string{"comfy": tailFile(svcDir("comfy.log"), 120),
			"whisper-small": tailFile(svcDir("whisper-small.log"), 40), "whisper-turbo": tailFile(svcDir("whisper-turbo.log"), 40)},
	}
}

// gatewaySelfTest: el agente se pregunta a sí mismo en 127.0.0.1:11434 (lo que vería cualquier
// programa del PC). Sin modelo, solo la lista; con modelo, además una generación corta.
func gatewaySelfTest(model, prompt string) map[string]any {
	base := fmt.Sprintf("http://127.0.0.1:%d", gatewayPort)
	out := map[string]any{}
	c := &http.Client{Timeout: 10 * time.Minute}
	if resp, err := c.Get(base + "/api/tags"); err != nil {
		out["tags_error"] = err.Error()
	} else {
		var t struct {
			Models []struct {
				Name string `json:"name"`
			} `json:"models"`
		}
		json.NewDecoder(resp.Body).Decode(&t)
		resp.Body.Close()
		var local, remote []string
		for _, m := range t.Models {
			if strings.Contains(m.Name, "@") {
				remote = append(remote, m.Name)
			} else {
				local = append(local, m.Name)
			}
		}
		out["tags_status"], out["local"], out["remote"] = resp.StatusCode, local, remote
	}
	if model == "" {
		return out
	}
	if prompt == "" {
		prompt = "Di hola en una palabra."
	}
	body, _ := json.Marshal(map[string]any{"model": model, "prompt": prompt, "stream": false, "keep_alive": "1m"})
	t0 := time.Now()
	resp, err := c.Post(base+"/api/generate", "application/json", bytes.NewReader(body))
	out["gen_seconds"] = time.Since(t0).Seconds()
	if err != nil {
		out["gen_error"] = err.Error()
		return out
	}
	defer resp.Body.Close()
	b, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	out["gen_status"], out["served_by"] = resp.StatusCode, resp.Header.Get("X-Served-By")
	var g map[string]any
	if json.Unmarshal(b, &g) == nil {
		out["response"], out["eval_count"], out["eval_duration"], out["error"] = g["response"], g["eval_count"], g["eval_duration"], g["error"]
	} else {
		out["raw"] = string(b[:min(len(b), 300)])
	}
	return out
}

// netTest (1.12.2): ¿llega este PC a lo que necesita descargar? Estado HTTP y tiempo de cada sitio.
func netTest() map[string]string {
	sites := map[string]string{
		"catalogo_ollama": "https://registry.ollama.ai/v2/library/gemma3/manifests/1b",
		"ollama_com":      "https://ollama.com/",
		"huggingface":     "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/README.md",
		"github":          "https://github.com/",
		"servidor_casa":   "https://casa.lomastrend.com/agent.json",
	}
	out := map[string]string{}
	var mu sync.Mutex
	var wg sync.WaitGroup
	for k, u := range sites {
		wg.Add(1)
		go func(k, u string) {
			defer wg.Done()
			c := &http.Client{Timeout: 20 * time.Second}
			req, _ := http.NewRequest(http.MethodGet, u, nil)
			req.Header.Set("Accept", "application/vnd.docker.distribution.manifest.v2+json")
			t0 := time.Now()
			resp, err := c.Do(req)
			r := ""
			if err != nil {
				r = "ERROR " + err.Error()
			} else {
				resp.Body.Close()
				r = fmt.Sprintf("HTTP %d", resp.StatusCode)
			}
			mu.Lock()
			out[k] = fmt.Sprintf("%s (%.1f s)", r, time.Since(t0).Seconds())
			mu.Unlock()
		}(k, u)
	}
	wg.Wait()
	for _, e := range []string{"HTTPS_PROXY", "HTTP_PROXY", "OLLAMA_HOST", "OLLAMA_MODELS"} {
		out["env_"+e] = os.Getenv(e)
	}
	return out
}
