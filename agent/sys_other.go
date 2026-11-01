//go:build !windows && !linux

package main

import (
	"errors"
	"os"
	"os/exec"
)

func hideWindow(cmd *exec.Cmd)            {}
func nvidiaSmiPath() string               { return "nvidia-smi" }
func ensureOllama(s *State)               {}
func firstRunSetup(s *State)              {}
func ensureVulkan(s *State)               {}
func restartOllama()                      {}
func diskOK(dir string, need int64) error { return nil }
func pidOnPort(port int) int              { return 0 }
func canLaunch() bool                     { return false }
func ollamaExe() string {
	p, _ := exec.LookPath("ollama")
	return p
}
func openConsole(title, line string) error { return errors.New("solo en Windows") }
func createShortcut(name, line, icon string) (string, error) {
	return "", errors.New("solo en Windows")
}
func keepAwake(s *State)             {}
func reinstallOllama(s *State)       {}
func powerEvents() map[string]string { return map[string]string{"error": "solo en Windows"} }
func migrateOllamaPort(s *State)     {}
func ollamaLogPath() string          { return "" }
func ollamaAppLog() string           { return "" }

func readGPUGeneric() GPU            { return GPU{} }
func readGPUProcsGeneric() []GPUProc { return nil }
func runTray(s *State)               { select {} }
func openPanel()                     {}
func quitApp()                       { os.Exit(0) }

func updateTarget(self string) string { return self }
func relaunchArgs() []string          { return os.Args[1:] }

func startNewVersion(target string) error { return exec.Command(target, relaunchArgs()...).Start() }
func linuxInstall(autostart bool) bool    { return false }
func clearOllamaHint(s *State)            {}
func launchCommand(host, exe, id, model string) string {
	return "OLLAMA_HOST=" + host + " " + exe + " launch " + id
}
