package main

import (
	"os"
	"path/filepath"
	"testing"
)

func TestReadGPUProcsTipo(t *testing.T) {
	bin, _ := filepath.Abs("../pruebas/bin")
	t.Setenv("PATH", bin+string(os.PathListSeparator)+os.Getenv("PATH"))
	for _, modo := range []string{"q", "tabla", "xml"} {
		t.Setenv("FAKE_SMI", modo)
		p, src := readGPUProcs()
		if src != modo || len(p) != 3 || p[0].Name != "ollama.exe" || p[0].Type != "C" || p[0].MemMB != 3120 ||
			p[1].Type != "C+G" || p[1].MemMB != -1 || p[2].Name != "Telegram.exe" || p[2].Type != "C+G" {
			t.Fatalf("modo %s: src=%q procesos inesperados: %+v", modo, src, p)
		}
	}
}
