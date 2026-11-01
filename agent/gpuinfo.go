package main

// Datos para la pestaña de esta tarjeta en el Monitor VRAM (desde la 1.6.0):
// procesos que usan la gráfica, modelos que Ollama tiene cargados y trabajos
// del servidor en curso. Viajan en el latido.

import (
	"bytes"
	"context"
	"encoding/json"
	"encoding/xml"
	"io"
	"net/http"
	"os/exec"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
)

type GPUProc struct {
	PID   int    `json:"pid"`
	Name  string `json:"name"`   // solo el nombre del ejecutable, nunca la ruta (lleva el usuario de Windows)
	MemMB int    `json:"mem_mb"` // -1 = Windows no lo informa (habitual con WDDM)
	// "C" = cálculo (CUDA: Ollama...), "G" = solo gráficos, "C+G" = ambos; en Windows casi
	// cualquier programa con ventana sale como C+G sin hacer trabajo real. "" = no se sabe.
	Type string `json:"type"`
}

// readGPUProcs: procesos con contexto en la gráfica según nvidia-smi y su tipo (C/G/C+G).
// La lista sale de la consulta CSV; el tipo, de la primera fuente que lo dé
// (texto de «-q -d PIDS», tabla normal, XML). src dice cuál sirvió, para diagnosticar.
func readGPUProcs() (procs []GPUProc, src string) {
	procs = readGPUProcsCSV()
	if procs == nil {
		if procs = readGPUProcsXML(); procs != nil {
			src = "xml"
		} else if procs = readGPUProcsGeneric(); procs != nil {
			src = "windows" // gráfica sin nvidia-smi: el tipo ya viene puesto
		} else {
			return nil, "sin datos"
		}
	} else {
		types, from := map[int]string{}, ""
		for _, f := range []struct {
			name string
			fn   func() map[int]string
		}{{"q", typesFromQuery}, {"tabla", typesFromTable}, {"xml", typesFromXML}} {
			if t := f.fn(); len(t) > 0 {
				types, from = t, f.name
				break
			}
		}
		for i := range procs {
			procs[i].Type = types[procs[i].PID]
		}
		src = from
		if src == "" {
			src = "sin tipo"
		}
	}
	// primero los de cálculo, luego por memoria
	sort.SliceStable(procs, func(i, j int) bool {
		ci, cj := procs[i].Type == "C", procs[j].Type == "C"
		if ci != cj {
			return ci
		}
		return procs[i].MemMB > procs[j].MemMB
	})
	if len(procs) > 40 {
		procs = procs[:40]
	}
	return procs, src
}

var (
	reQPID  = regexp.MustCompile(`^\s*Process ID\s*:\s*(\d+)`)
	reQType = regexp.MustCompile(`^\s*Type\s*:\s*(C\+G|C|G)\b`)
	// «|    0   N/A  N/A      5740    C+G   C:\Windows\explorer.exe   N/A |» (o sin GI/CI en drivers viejos)
	reTabla = regexp.MustCompile(`^\|\s+\d+(?:\s+\S+\s+\S+)?\s+(\d+)\s+(C\+G|C|G)\s`)
)

func typesFromQuery() map[int]string {
	out, err := smi("-q", "-d", "PIDS")
	if err != nil {
		return nil
	}
	t, pid := map[int]string{}, 0
	for _, l := range strings.Split(string(out), "\n") {
		if m := reQPID.FindStringSubmatch(l); m != nil {
			pid, _ = strconv.Atoi(m[1])
		} else if m := reQType.FindStringSubmatch(l); m != nil && pid > 0 {
			t[pid], pid = m[1], 0
		}
	}
	return t
}

func typesFromTable() map[int]string {
	out, err := smi()
	if err != nil {
		return nil
	}
	t := map[int]string{}
	for _, l := range strings.Split(string(out), "\n") {
		if m := reTabla.FindStringSubmatch(strings.TrimRight(l, "\r")); m != nil {
			pid, _ := strconv.Atoi(m[1])
			t[pid] = m[2]
		}
	}
	return t
}

func typesFromXML() map[int]string {
	t := map[int]string{}
	for _, p := range readGPUProcsXML() {
		if p.Type != "" {
			t[p.PID] = p.Type
		}
	}
	return t
}

func smi(args ...string) ([]byte, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 8*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, nvidiaSmiPath(), args...)
	hideWindow(cmd)
	return cmd.Output()
}

func exeName(p string) string {
	return filepath.Base(strings.ReplaceAll(strings.TrimSpace(p), `\`, "/"))
}

func memMB(v string) int {
	n, err := strconv.Atoi(strings.TrimSpace(strings.TrimSuffix(strings.TrimSpace(v), "MiB")))
	if err != nil {
		return -1
	}
	return n
}

func readGPUProcsXML() []GPUProc {
	out, err := smi("-q", "-x", "-d", "PIDS")
	if err != nil {
		return nil
	}
	var r struct {
		GPUs []struct {
			Procs []struct {
				PID  int    `xml:"pid"`
				Type string `xml:"type"`
				Name string `xml:"process_name"`
				Mem  string `xml:"used_memory"`
			} `xml:"processes>process_info"`
		} `xml:"gpu"`
	}
	d := xml.NewDecoder(bytes.NewReader(out))
	d.Strict = false // el XML de nvidia-smi trae DOCTYPE con DTD externa
	if d.Decode(&r) != nil || len(r.GPUs) == 0 {
		return nil
	}
	procs := []GPUProc{}
	for _, p := range r.GPUs[0].Procs {
		if p.PID > 0 {
			procs = append(procs, GPUProc{PID: p.PID, Name: exeName(p.Name), MemMB: memMB(p.Mem), Type: strings.TrimSpace(p.Type)})
		}
	}
	return procs
}

func readGPUProcsCSV() []GPUProc {
	out, err := smi("--query-compute-apps=pid,process_name,used_memory", "--format=csv,noheader,nounits")
	if err != nil {
		return nil
	}
	procs := []GPUProc{}
	for _, line := range strings.Split(strings.TrimSpace(string(out)), "\n") {
		f := strings.Split(line, ",")
		if len(f) < 3 {
			continue
		}
		pid, err := strconv.Atoi(strings.TrimSpace(f[0]))
		if err != nil {
			continue
		}
		procs = append(procs, GPUProc{PID: pid, Name: exeName(strings.Join(f[1:len(f)-1], ",")), MemMB: memMB(f[len(f)-1])})
	}
	return procs
}

type LoadedModel struct {
	Name     string `json:"name"`
	VRAMMB   int    `json:"vram_mb"`
	SizeMB   int    `json:"size_mb"`   // total; si es mayor que vram_mb, parte va en la CPU
	ExpiresS int    `json:"expires_s"` // segundos hasta que Ollama lo descargue solo (-1 = no caduca)
}

// ollamaLoaded: modelos que Ollama tiene en memoria ahora (/api/ps).
func ollamaLoaded(base string) []LoadedModel {
	resp, err := httpc.Get(base + "/api/ps")
	if err != nil {
		return nil
	}
	defer resp.Body.Close()
	var r struct {
		Models []struct {
			Name      string    `json:"name"`
			Size      int64     `json:"size"`
			SizeVRAM  int64     `json:"size_vram"`
			ExpiresAt time.Time `json:"expires_at"`
		} `json:"models"`
	}
	if json.NewDecoder(resp.Body).Decode(&r) != nil {
		return nil
	}
	out := []LoadedModel{}
	for _, m := range r.Models {
		exp := -1
		if !m.ExpiresAt.IsZero() && m.ExpiresAt.Year() < 2200 {
			exp = max(0, int(time.Until(m.ExpiresAt).Seconds()))
		}
		out = append(out, LoadedModel{Name: m.Name, VRAMMB: int(m.SizeVRAM >> 20), SizeMB: int(m.Size >> 20), ExpiresS: exp})
	}
	return out
}

// Trabajos del servidor que este agente está atendiendo ahora.
type job struct {
	Model   string
	Path    string
	Started time.Time
}

var (
	jobsMu sync.Mutex
	jobs   = map[int64]*job{}
	jobSeq int64
)

// startJob apunta un trabajo (leyendo el modelo del cuerpo JSON sin consumirlo) y devuelve su cierre.
func startJob(r *http.Request) func() {
	model := ""
	if r.Body != nil && r.Method == http.MethodPost {
		body, err := io.ReadAll(r.Body)
		r.Body.Close()
		r.Body = io.NopCloser(bytes.NewReader(body))
		r.ContentLength = int64(len(body))
		if err == nil {
			var m struct {
				Model string `json:"model"`
			}
			_ = json.Unmarshal(body, &m)
			model = m.Model
		}
	}
	jobsMu.Lock()
	jobSeq++
	id := jobSeq
	jobs[id] = &job{Model: model, Path: r.URL.Path, Started: time.Now()}
	jobsMu.Unlock()
	return func() {
		jobsMu.Lock()
		delete(jobs, id)
		jobsMu.Unlock()
	}
}

func currentJobs() []map[string]any {
	jobsMu.Lock()
	defer jobsMu.Unlock()
	out := []map[string]any{}
	for _, j := range jobs {
		out = append(out, map[string]any{"model": j.Model, "path": j.Path, "age_s": int(time.Since(j.Started).Seconds())})
	}
	sort.Slice(out, func(i, k int) bool { return out[i]["age_s"].(int) > out[k]["age_s"].(int) })
	return out
}

// unloadModel: el servidor pide soltar un modelo de la memoria de la gráfica (botón «desalojar»).
func unloadModel(base, model string) error {
	b, _ := json.Marshal(map[string]any{"model": model, "keep_alive": 0})
	resp, err := httpc.Post(base+"/api/generate", "application/json", bytes.NewReader(b))
	if err != nil {
		return err
	}
	resp.Body.Close()
	return nil
}
