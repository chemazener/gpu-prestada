//go:build !linux

package main

// En Windows (y macOS) no hay órdenes de terminal: todo se hace desde la ventana y el icono de la bandeja.
func runCLI(args []string) int { return -1 }

func nativeIntegrations() []integration                       { return nil }
func nativeLaunchLine(host, id, model string, ctx int) string { return "" }
