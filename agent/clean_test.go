package main

import (
	"os"
	"path/filepath"
	"testing"
)

func TestComfyClean(t *testing.T) {
	tmp := t.TempDir()
	t.Setenv("LOCALAPPDATA", tmp)
	out := filepath.Join(comfyRoot(), "ComfyUI", "output", "rmt_x")
	in := filepath.Join(comfyRoot(), "ComfyUI", "input")
	os.MkdirAll(out, 0o755)
	os.MkdirAll(in, 0o755)
	secret := filepath.Join(tmp, "GPUPrestada", "config.json")
	for _, p := range []string{filepath.Join(out, "a.png"), filepath.Join(in, "b.png"), secret} {
		os.WriteFile(p, []byte("x"), 0o644)
	}
	n := comfyClean([]cleanFile{
		{"a.png", "rmt_x", "output"}, {"b.png", "", "input"},
		{"config.json", "../../../..", "output"}, {"../../../config.json", "", "input"}, {"x", "", "temp"},
	})
	if n != 2 {
		t.Fatalf("borrados %d, se esperaban 2", n)
	}
	if _, err := os.Stat(secret); err != nil {
		t.Fatalf("¡borró un fichero de fuera!")
	}
}
