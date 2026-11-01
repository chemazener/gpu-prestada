//go:build !windows

package main

import (
	"bufio"
	"os"
	"strconv"
	"strings"
	"syscall"
)

// sysMem: RAM total y disponible (MB), de /proc/meminfo.
func sysMem() (total, avail int64) {
	f, err := os.Open("/proc/meminfo")
	if err != nil {
		return -1, -1
	}
	defer f.Close()
	total, avail = -1, -1
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		k := strings.Fields(sc.Text())
		if len(k) < 2 {
			continue
		}
		v, _ := strconv.ParseInt(k[1], 10, 64)
		switch k[0] {
		case "MemTotal:":
			total = v >> 10
		case "MemAvailable:":
			avail = v >> 10
		}
	}
	return
}

// sysDisk: tamaño y espacio libre (MB) del disco de la carpeta de datos.
func sysDisk() (drive string, total, free int64) {
	dir := dataDir()
	os.MkdirAll(dir, 0o755)
	var st syscall.Statfs_t
	if syscall.Statfs(dir, &st) != nil {
		return "", -1, -1
	}
	return "/", int64(st.Blocks*uint64(st.Bsize)) >> 20, int64(st.Bavail*uint64(st.Bsize)) >> 20
}

// cpuName: nombre del procesador, de /proc/cpuinfo.
func cpuName() string {
	b, err := os.ReadFile("/proc/cpuinfo")
	if err != nil {
		return ""
	}
	for _, l := range strings.Split(string(b), "\n") {
		if strings.HasPrefix(l, "model name") {
			if i := strings.IndexByte(l, ':'); i >= 0 {
				return strings.Join(strings.Fields(l[i+1:]), " ")
			}
		}
	}
	return ""
}
