//go:build windows

package main

// Gráficas sin nvidia-smi (Radeon, Intel…), desde la 1.7.0: se leen como el
// Administrador de tareas. DXGI da el nombre, el fabricante y la memoria; los
// contadores de rendimiento «GPU Engine» y «GPU Adapter/Process Memory» dan el uso,
// la memoria ocupada y los procesos. Los contadores se piden por su nombre en inglés
// (PdhAddEnglishCounter), así que funciona con Windows en cualquier idioma.

import (
	"context"
	"fmt"
	"log"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"
	"unsafe"

	"golang.org/x/sys/windows"
)

var (
	modDXGI             = windows.NewLazySystemDLL("dxgi.dll")
	pCreateDXGIFactory1 = modDXGI.NewProc("CreateDXGIFactory1")
	modPDH              = windows.NewLazySystemDLL("pdh.dll")
	pPdhOpenQuery       = modPDH.NewProc("PdhOpenQueryW")
	pPdhAddEnglish      = modPDH.NewProc("PdhAddEnglishCounterW")
	pPdhCollect         = modPDH.NewProc("PdhCollectQueryData")
	pPdhGetArray        = modPDH.NewProc("PdhGetFormattedCounterArrayW")

	iidIDXGIFactory1 = windows.GUID{Data1: 0x770aae78, Data2: 0xf26f, Data3: 0x4dba,
		Data4: [8]byte{0xa8, 0x29, 0x25, 0x3c, 0x83, 0xd1, 0xb3, 0x87}}
)

type dxgiAdapterDesc1 struct {
	Description           [128]uint16
	VendorID              uint32
	DeviceID              uint32
	SubSysID              uint32
	Revision              uint32
	DedicatedVideoMemory  uintptr
	DedicatedSystemMemory uintptr
	SharedSystemMemory    uintptr
	LuidLow               uint32
	LuidHigh              int32
	Flags                 uint32
}

type adapterInfo struct {
	Name        string
	Vendor      string // "AMD", "Intel", "NVIDIA", "Qualcomm" o el código en hex
	DedicatedMB int
	SharedMB    int
	LUID        string // como en los contadores: «luid_0x00000000_0x0000d1a5» (minúsculas)
}

// comCall llama al método número idx de la tabla virtual de un objeto COM.
func comCall(obj unsafe.Pointer, idx int, args ...uintptr) uintptr {
	vtbl := (*[32]uintptr)(*(*unsafe.Pointer)(obj))
	r, _, _ := syscall.SyscallN(vtbl[idx], append([]uintptr{uintptr(obj)}, args...)...)
	return r
}

func vendorName(id uint32) string {
	switch id {
	case 0x1002, 0x1022:
		return "AMD"
	case 0x8086:
		return "Intel"
	case 0x10de:
		return "NVIDIA"
	case 0x5143:
		return "Qualcomm"
	}
	return fmt.Sprintf("0x%04x", id)
}

// listAdapters: gráficas físicas (sin el «Microsoft Basic Render Driver»).
func listAdapters() (out []adapterInfo) {
	if pCreateDXGIFactory1.Find() != nil {
		return nil
	}
	var factory unsafe.Pointer
	if hr, _, _ := pCreateDXGIFactory1.Call(uintptr(unsafe.Pointer(&iidIDXGIFactory1)), uintptr(unsafe.Pointer(&factory))); hr != 0 || factory == nil {
		return nil
	}
	defer comCall(factory, 2) // Release
	for i := 0; i < 16; i++ {
		var ad unsafe.Pointer
		if hr := comCall(factory, 12, uintptr(i), uintptr(unsafe.Pointer(&ad))); hr != 0 || ad == nil { // EnumAdapters1
			break
		}
		var d dxgiAdapterDesc1
		hr := comCall(ad, 10, uintptr(unsafe.Pointer(&d))) // GetDesc1
		comCall(ad, 2)
		if hr != 0 || d.Flags&2 != 0 || d.VendorID == 0x1414 { // software / Microsoft
			continue
		}
		out = append(out, adapterInfo{
			Name: strings.TrimSpace(windows.UTF16ToString(d.Description[:])), Vendor: vendorName(d.VendorID),
			DedicatedMB: int(d.DedicatedVideoMemory >> 20), SharedMB: int(d.SharedSystemMemory >> 20),
			LUID: fmt.Sprintf("luid_0x%08x_0x%08x", uint32(d.LuidHigh), d.LuidLow),
		})
	}
	return out
}

type pdhItem struct {
	Name   *uint16
	Status uint32
	_      uint32
	Value  float64
}

type pdhQuery struct {
	q                                  uintptr
	engine, pDed, pShared, aDed, aShrd uintptr
}

func (p *pdhQuery) add(path string) uintptr {
	var h uintptr
	s, _ := windows.UTF16PtrFromString(path)
	if r, _, _ := pPdhAddEnglish.Call(p.q, uintptr(unsafe.Pointer(s)), 0, uintptr(unsafe.Pointer(&h))); r != 0 {
		log.Printf("contador %s no disponible (0x%x)", path, r)
		return 0
	}
	return h
}

// values devuelve instancia → valor de un contador con comodín.
func (p *pdhQuery) values(h uintptr) map[string]float64 {
	out := map[string]float64{}
	if h == 0 {
		return out
	}
	const fmtDouble, noCap = 0x200, 0x8000
	var size, count uint32
	r, _, _ := pPdhGetArray.Call(h, fmtDouble|noCap, uintptr(unsafe.Pointer(&size)), uintptr(unsafe.Pointer(&count)), 0)
	if uint32(r) != 0x800007d2 || size == 0 { // PDH_MORE_DATA
		return out
	}
	buf := make([]uint64, (size+7)/8)
	r, _, _ = pPdhGetArray.Call(h, fmtDouble|noCap, uintptr(unsafe.Pointer(&size)), uintptr(unsafe.Pointer(&count)), uintptr(unsafe.Pointer(&buf[0])))
	if r != 0 || count == 0 {
		return out
	}
	for _, it := range unsafe.Slice((*pdhItem)(unsafe.Pointer(&buf[0])), count) {
		if it.Status <= 1 && it.Name != nil { // PDH_CSTATUS_VALID_DATA / NEW_DATA
			out[strings.ToLower(windows.UTF16PtrToString(it.Name))] += it.Value
		}
	}
	return out
}

type genericSample struct {
	at    time.Time
	gpu   GPU
	procs []GPUProc
}

var (
	genMu      sync.Mutex
	genAdapter *adapterInfo
	genTried   bool
	genQuery   *pdhQuery
	genLast    genericSample
	reEngine   = regexp.MustCompile(`^pid_(\d+)_(luid_0x[0-9a-f]+_0x[0-9a-f]+)_phys_\d+_eng_\d+_engtype_(.*)$`)
	reProcMem  = regexp.MustCompile(`^pid_(\d+)_(luid_0x[0-9a-f]+_0x[0-9a-f]+)_phys_\d+$`)
)

// sampleGeneric lee la gráfica sin nvidia-smi. Guarda la última lectura 1,5 s para
// que readGPU y readGPUProcs del mismo latido no repitan la consulta.
func sampleGeneric() (res genericSample) {
	defer func() {
		if r := recover(); r != nil { // nunca tumbar el agente por esto
			log.Printf("lectura genérica de la gráfica falló: %v", r)
			res = genericSample{}
		}
	}()
	genMu.Lock()
	defer genMu.Unlock()
	if time.Since(genLast.at) < 1500*time.Millisecond {
		return genLast
	}
	if !genTried {
		genTried = true
		ads := listAdapters()
		for _, a := range ads {
			if a.Vendor == "NVIDIA" { // con una NVIDIA manda nvidia-smi, aunque falle un momento
				log.Printf("hay una NVIDIA (%s): no uso la lectura genérica", a.Name)
				return genericSample{}
			}
		}
		sort.SliceStable(ads, func(i, j int) bool { return ads[i].DedicatedMB > ads[j].DedicatedMB })
		for i := range ads {
			if ads[i].Vendor != "NVIDIA" { // con NVIDIA manda nvidia-smi
				genAdapter = &ads[i]
				break
			}
		}
		if genAdapter != nil {
			log.Printf("gráfica sin nvidia-smi: %s (%s, %d MiB propios + %d MiB compartidos, %s)",
				genAdapter.Name, genAdapter.Vendor, genAdapter.DedicatedMB, genAdapter.SharedMB, genAdapter.LUID)
		}
		var q uintptr
		if pPdhOpenQuery.Find() == nil {
			if r, _, _ := pPdhOpenQuery.Call(0, 0, uintptr(unsafe.Pointer(&q))); r == 0 {
				p := &pdhQuery{q: q}
				p.engine = p.add(`\GPU Engine(*)\Utilization Percentage`)
				p.pDed = p.add(`\GPU Process Memory(*)\Dedicated Usage`)
				p.pShared = p.add(`\GPU Process Memory(*)\Shared Usage`)
				p.aDed = p.add(`\GPU Adapter Memory(*)\Dedicated Usage`)
				p.aShrd = p.add(`\GPU Adapter Memory(*)\Shared Usage`)
				genQuery = p
				pPdhCollect.Call(q) // los porcentajes necesitan dos lecturas
				time.Sleep(300 * time.Millisecond)
			}
		}
	}
	if genAdapter == nil || genQuery == nil {
		return genericSample{}
	}
	ad, p := genAdapter, genQuery
	if r, _, _ := pPdhCollect.Call(p.q); r != 0 {
		return genericSample{}
	}

	// uso: por tipo de motor se suman los procesos y se toma el motor más cargado
	perEng, perPID, compute := map[string]float64{}, map[int]float64{}, map[int]bool{}
	for inst, v := range p.values(p.engine) {
		m := reEngine.FindStringSubmatch(inst)
		if m == nil || m[2] != ad.LUID {
			continue
		}
		pid, _ := strconv.Atoi(m[1])
		perEng[m[3]] += v
		perPID[pid] += v
		if v > 0 && (strings.HasPrefix(m[3], "compute") || m[3] == "cuda") {
			compute[pid] = true
		}
	}
	util := 0.0
	for _, v := range perEng {
		util = max(util, v)
	}

	// memoria: propia (dedicada) + compartida; en una integrada casi todo es compartida
	mib := func(v float64) int { return int(v / (1 << 20)) }
	used := 0
	for inst, v := range p.values(p.aDed) {
		if strings.HasPrefix(inst, ad.LUID+"_") {
			used += mib(v)
		}
	}
	for inst, v := range p.values(p.aShrd) {
		if strings.HasPrefix(inst, ad.LUID+"_") {
			used += mib(v)
		}
	}
	mem := map[int]int{}
	for _, h := range []uintptr{p.pDed, p.pShared} {
		for inst, v := range p.values(h) {
			if m := reProcMem.FindStringSubmatch(inst); m != nil && m[2] == ad.LUID {
				pid, _ := strconv.Atoi(m[1])
				mem[pid] += mib(v)
			}
		}
	}

	procs := []GPUProc{}
	seen := map[int]bool{}
	for _, src := range []map[int]int{mem, toIntMap(perPID)} {
		for pid := range src {
			if seen[pid] || pid == 0 || (mem[pid] < 1 && perPID[pid] <= 0) {
				continue
			}
			seen[pid] = true
			name := processName(uint32(pid))
			typ := "G"
			if compute[pid] || isOllamaProc(name) {
				typ = "C"
			}
			procs = append(procs, GPUProc{PID: pid, Name: name, MemMB: mem[pid], Type: typ})
		}
	}
	total := ad.DedicatedMB + ad.SharedMB
	genLast = genericSample{at: time.Now(), procs: procs, gpu: GPU{
		Name: ad.Name, Vendor: ad.Vendor, Shared: ad.DedicatedMB < 1024, TotalMB: total, UsedMB: min(used, total),
		Util: min(100, int(util+0.5)), Temp: -1, Power: -1, PowerLimit: -1, Fan: -1, OK: true,
	}}
	return genLast
}

func toIntMap(m map[int]float64) map[int]int {
	out := make(map[int]int, len(m))
	for k := range m {
		out[k] = 0
	}
	return out
}

func isOllamaProc(name string) bool {
	n := strings.ToLower(name)
	return strings.HasPrefix(n, "ollama") || strings.HasPrefix(n, "llama")
}

func processName(pid uint32) string {
	h, err := windows.OpenProcess(windows.PROCESS_QUERY_LIMITED_INFORMATION, false, pid)
	if err != nil {
		return "[protegido]"
	}
	defer windows.CloseHandle(h)
	buf := make([]uint16, 1024)
	n := uint32(len(buf))
	if windows.QueryFullProcessImageName(h, 0, &buf[0], &n) != nil {
		return "[protegido]"
	}
	return exeName(windows.UTF16ToString(buf[:n]))
}

var (
	probeOnce sync.Once
	probeOK   bool
)

// genericProbe: antes de usar estas llamadas de bajo nivel en el propio agente, se
// prueban una vez en un proceso hijo («-diag»). Si el hijo se cae, el agente sigue
// como antes (sin leer la gráfica) y puede seguir actualizándose.
func genericProbe() bool {
	if os.Getenv("GPU_PRESTADA_SONDA") == "1" { // somos el hijo
		return true
	}
	probeOnce.Do(func() {
		exe, err := os.Executable()
		if err != nil {
			return
		}
		out := filepath.Join(os.TempDir(), "gpu-prestada-sonda.json")
		ctx, cancel := context.WithTimeout(context.Background(), 40*time.Second)
		defer cancel()
		cmd := exec.CommandContext(ctx, exe, "-diag", out)
		cmd.Env = append(os.Environ(), "GPU_PRESTADA_SONDA=1")
		hideWindow(cmd)
		err = cmd.Run()
		probeOK = err == nil
		log.Printf("sonda de la lectura genérica de la gráfica: correcta=%v (%v)", probeOK, err)
	})
	return probeOK
}

func readGPUGeneric() GPU {
	if !genericProbe() {
		return GPU{}
	}
	return sampleGeneric().gpu
}

func readGPUProcsGeneric() []GPUProc {
	if !genericProbe() {
		return nil
	}
	return sampleGeneric().procs
}
