#!/bin/sh
# GPU Prestada para Linux · instalación desde el terminal (también en servidores sin escritorio, por SSH).
#   curl -fsSL https://lomastrend.com/gpu-prestada/instalar.sh | sh
#   curl -fsSL https://lomastrend.com/gpu-prestada/instalar.sh | sh -s -- CÓDIGO-DE-ALTA   (si ya tienes el código)
# Descarga el programa del servidor de LoMasTrend (comprobando su certificado y la huella del fichero),
# lo instala para tu usuario como servicio (~/.local/bin + systemd de usuario) y lo deja en marcha.
set -eu

RELAY="https://casa.lomastrend.com"
PIN="sha256//2lAPgY7oegT3F7URVjwhuMuVsk/5XDSPgGtcS+KiVp8="  # clave pública del certificado del servidor
CODIGO="${1:-}"

fallo() { echo "✗ $*" >&2; exit 1; }

[ "$(uname -s)" = "Linux" ] || fallo "Este instalador es para Linux (en Windows, descárgalo de la web)."
case "$(uname -m)" in
  x86_64|amd64) ARCH=amd64 ;;
  aarch64|arm64) ARCH=arm64 ;;
  *) fallo "Arquitectura $(uname -m) no soportada (solo x86_64 y arm64)." ;;
esac
[ "$(id -u)" -ne 0 ] || echo "⚠ Lo estás instalando como root: mejor con tu usuario normal (se instala solo para ese usuario)."
command -v curl >/dev/null || fallo "Falta curl."
command -v sha256sum >/dev/null || fallo "Falta sha256sum (paquete coreutils)."
systemctl --user show-environment >/dev/null 2>&1 || fallo "No hay systemd de usuario en esta sesión (entra por SSH o con sesión gráfica normal; con «su» o «sudo -u» no vale)."

echo "→ Descargando GPU Prestada (linux-$ARCH)…"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
C="curl -fsSL --proto =https -k --pinnedpubkey $PIN"
$C "$RELAY/agent.json?platform=linux-$ARCH" -o "$TMP/agent.json" || fallo "No puedo hablar con el servidor de LoMasTrend."
SHA=$(sed -n 's/.*"sha256": *"\([0-9a-f]\{64\}\)".*/\1/p' "$TMP/agent.json")
[ -n "$SHA" ] || fallo "El servidor no ha dado la huella del programa."
$C "$RELAY/agent.exe?platform=linux-$ARCH" -o "$TMP/gpu-prestada" || fallo "Falló la descarga."
echo "$SHA  $TMP/gpu-prestada" | sha256sum -c --quiet - || fallo "La huella del fichero no coincide: descarga dañada o manipulada."
chmod +x "$TMP/gpu-prestada"

"$TMP/gpu-prestada" </dev/null   # se copia a ~/.local/bin, crea el servicio y lo arranca
BIN="$HOME/.local/bin/gpu-prestada"

if [ -n "$CODIGO" ]; then
  sleep 2
  "$BIN" codigo "$CODIGO"
fi

command -v ollama >/dev/null || echo "⚠ Falta Ollama (sin él no se presta la tarjeta): curl -fsSL https://ollama.com/install.sh | sh"
case ":$PATH:" in *":$HOME/.local/bin:"*) G=gpu-prestada ;; *) G="$BIN" ;; esac
echo
echo "✓ Listo. Órdenes útiles:"
echo "   $G estado        cómo está (y si ya está dado de alta)"
echo "   $G codigo XXXX   pega el código de alta, si no te dan de alta desde el Monitor VRAM"
echo "   $G ayuda         el resto (pausar, reanudar, parar, desinstalar)"
