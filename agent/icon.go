package main

import (
	"bytes"
	_ "embed"
	"encoding/binary"
	"image"
	"image/color"
	"image/draw"
	"image/png"
	"sync"
)

var (
	//go:embed assets/tray_logo.png
	trayLogoPNG []byte // logotipo de LoMasTrend, 32×32 con hueco a la derecha
	//go:embed assets/app.ico
	appIcon []byte
	//go:embed assets/logo.png
	logoPNG []byte
	//go:embed assets/panel.html
	panelHTML []byte
)

var (
	iconMu    sync.Mutex
	iconCache = map[int][]byte{}
	// marco del medidor según el estado: prestando, en pausa, con problemas
	// (gris medio: se distingue tanto en barras de tareas claras como oscuras)
	frameCol = []color.NRGBA{{128, 134, 150, 255}, {245, 158, 11, 255}, {128, 134, 150, 255}}
	// relleno según la carga cedida: baja (verde), media (naranja), alta (rojo)
	loadCol = []color.NRGBA{{}, {16, 185, 129, 255}, {249, 115, 22, 255}, {239, 68, 68, 255}}
	// altura del relleno en píxeles (de 32): pequeña, mitad, alta
	loadFill = []int{0, 8, 14, 28}
)

// gaugeLevel pasa el porcentaje de carga cedida a los niveles del medidor:
// 0 sin carga (hueco), 1 hasta el 50 %, 2 por encima del 50 %, 3 por encima del 75 %.
func gaugeLevel(pct int) int {
	switch {
	case pct <= 0:
		return 0
	case pct <= 50:
		return 1
	case pct <= 75:
		return 2
	}
	return 3
}

// trayIcon compone el icono de la bandeja: el logotipo y, a la derecha, un
// medidor vertical con la carga que se está cediendo. Sin carga queda hueco
// (solo el marco); con carga se rellena de abajo arriba en verde, naranja o
// rojo. El estado se ve en el marco (gris prestando, ámbar en pausa) y en el
// logotipo, que se atenúa si hay problemas. Todo va en bloques de 2 px para que se distinga a 16 px.
func trayIcon(state, level int) []byte {
	iconMu.Lock()
	defer iconMu.Unlock()
	key := state*100 + level
	if b, ok := iconCache[key]; ok {
		return b
	}
	const n = 32
	img := image.NewNRGBA(image.Rect(0, 0, n, n))
	if logo, err := png.Decode(bytes.NewReader(trayLogoPNG)); err == nil {
		draw.Draw(img, img.Bounds(), logo, image.Point{}, draw.Over)
	}
	if state == 2 { // con problemas: el logotipo se atenúa
		for i := 3; i < len(img.Pix); i += 4 {
			img.Pix[i] = uint8(int(img.Pix[i]) * 35 / 100)
		}
	}
	// El logotipo ocupa todo el icono; el medidor va encima, en las columnas
	// 26..31 (marco de 2 px, interior hueco), con 1 px de separación a su izquierda.
	for y := 0; y < n; y++ {
		img.SetNRGBA(25, y, color.NRGBA{})
		for x := 26; x < n; x++ {
			if x <= 27 || x >= n-2 || y <= 1 || y >= n-2 {
				img.SetNRGBA(x, y, frameCol[state])
			} else {
				img.SetNRGBA(x, y, color.NRGBA{})
			}
		}
	}
	// el relleno ocupa todo el ancho del medidor para que se vea a 16 px
	for y := n - 2 - loadFill[level]; y < n && level > 0; y++ {
		for x := 26; x < n; x++ {
			img.SetNRGBA(x, y, loadCol[level])
		}
	}
	b := encodeICO(img)
	iconCache[key] = b
	return b
}

// encodeICO escribe la imagen como .ico clásico de 32 bits (DIB + máscara).
func encodeICO(img *image.NRGBA) []byte {
	n := img.Bounds().Dx()
	px := make([]byte, n*n*4)
	for y := 0; y < n; y++ {
		for x := 0; x < n; x++ {
			c := img.NRGBAAt(x, y)
			o := ((n-1-y)*n + x) * 4 // los DIB van de abajo arriba
			px[o], px[o+1], px[o+2], px[o+3] = c.B, c.G, c.R, c.A
		}
	}
	mask := make([]byte, n*((n+31)/32*4))
	var buf bytes.Buffer
	w := func(v any) { binary.Write(&buf, binary.LittleEndian, v) }
	w([]uint16{0, 1, 1})                                    // ICONDIR
	w([]byte{byte(n), byte(n), 0, 0})                       // ancho, alto, colores, reservado
	w([]uint16{1, 32})                                      // planos, bits por píxel
	w([]uint32{uint32(40 + len(px) + len(mask)), 22})       // tamaño y desplazamiento
	w([]uint32{40, uint32(n), uint32(n * 2)})               // BITMAPINFOHEADER
	w([]uint16{1, 32})                                      // planos, bits
	w([]uint32{0, uint32(len(px) + len(mask)), 0, 0, 0, 0}) // sin compresión
	buf.Write(px)
	buf.Write(mask)
	return buf.Bytes()
}
