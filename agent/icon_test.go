package main

import (
	"fmt"
	"os"
	"testing"
)

func TestTrayIcon(t *testing.T) {
	for pct, want := range map[int]int{-1: 0, 0: 0, 1: 1, 50: 1, 51: 2, 75: 2, 76: 3, 100: 3} {
		if got := gaugeLevel(pct); got != want {
			t.Errorf("gaugeLevel(%d) = %d, esperaba %d", pct, got, want)
		}
	}
	if d := os.Getenv("ICON_OUT"); d != "" {
		for _, c := range [][2]int{{0, 0}, {0, 1}, {0, 2}, {0, 3}, {1, 0}, {2, 0}} {
			os.WriteFile(fmt.Sprintf("%s/t_%d_%d.ico", d, c[0], c[1]), trayIcon(c[0], c[1]), 0o644)
		}
	}
}
