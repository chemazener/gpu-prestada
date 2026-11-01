#!/bin/sh
# GPU Prestada · instalación del RELÉ (túnel para PCs sin Tailscale) en cualquier Linux, desde GitHub.
#
#   curl -fsSL https://raw.githubusercontent.com/chemazener/gpu-prestada/main/relay/instalar-relay.sh \
#     | GPU_RELAY_BROKER=http://IP-DEL-PROXY-OLLAMA:11434 sh
#
# Variables (todas opcionales salvo la primera):
#   GPU_RELAY_BROKER    proxy de Ollama que valida los códigos de alta (el que lleva server/nodes.py)  [obligatoria]
#   GPU_RELAY_NAME      nombre DNS con el que llegarán los PCs (va en el certificado)      [por defecto: el del equipo]
#   GPU_RELAY_PUB_PORT  puerto TLS público, solo en 127.0.0.1 (detrás de tu proxy inverso o redirección)  [8445]
#   GPU_RELAY_LAN_BIND  direcciones donde escucha el lado interno, separadas por comas      [127.0.0.1]
#   GPU_RELAY_LAN_PORT  puerto del lado interno (el proxy de Ollama habla aquí)              [11437]
#   GPU_RELAY_LAN_IP    dirección con la que el proxy de Ollama ve este relé                 [127.0.0.1]
#   GPU_RELAY_LAN_ALLOW redes que pueden usar el lado interno (CIDR, comas)                  [127.0.0.1/32]
#   GPU_RELAY_DIR       carpeta de instalación                                               [~/gpu-relay]
# Instala para tu usuario (venv de Python, certificado propio de 10 años, servicio de usuario de systemd).
set -eu

fallo() { echo "✗ $*" >&2; exit 1; }
[ -n "${GPU_RELAY_BROKER:-}" ] || fallo "Falta GPU_RELAY_BROKER (p. ej. GPU_RELAY_BROKER=http://192.168.1.10:11434)."
[ "$(id -u)" -ne 0 ] || echo "⚠ Mejor con un usuario normal que como root."
for c in python3 openssl curl tar; do command -v "$c" >/dev/null || fallo "Falta $c."; done
systemctl --user show-environment >/dev/null 2>&1 || fallo "No hay systemd de usuario en esta sesión."

DIR="${GPU_RELAY_DIR:-$HOME/gpu-relay}"
NAME="${GPU_RELAY_NAME:-$(hostname -f 2>/dev/null || hostname)}"
PUB="${GPU_RELAY_PUB_PORT:-8445}"
LAN_BIND="${GPU_RELAY_LAN_BIND:-127.0.0.1}"
LAN_PORT="${GPU_RELAY_LAN_PORT:-11437}"
LAN_IP="${GPU_RELAY_LAN_IP:-127.0.0.1}"
LAN_ALLOW="${GPU_RELAY_LAN_ALLOW:-127.0.0.1/32}"

echo "→ Descargando el relé desde GitHub…"
mkdir -p "$DIR/tls"
curl -fsSL https://raw.githubusercontent.com/chemazener/gpu-prestada/main/relay/relay.py -o "$DIR/relay.py" || fallo "No puedo bajar relay.py."

echo "→ Preparando Python (aiohttp)…"
python3 -m venv "$DIR/.venv" || fallo "Falta el módulo venv (en Debian/Ubuntu: apt install python3-venv)."
"$DIR/.venv/bin/pip" install -q aiohttp || fallo "No pude instalar aiohttp."

if [ ! -f "$DIR/tls/key.pem" ]; then
  echo "→ Creando el certificado propio de «$NAME» (10 años)…"
  openssl ecparam -genkey -name prime256v1 -noout -out "$DIR/tls/key.pem" 2>/dev/null
  chmod 600 "$DIR/tls/key.pem"
  openssl req -new -x509 -key "$DIR/tls/key.pem" -out "$DIR/tls/cert.pem" -days 3650 -subj "/CN=$NAME" \
    -addext "subjectAltName=DNS:$NAME" 2>/dev/null || fallo "No pude crear el certificado."
else
  echo "→ Certificado existente: se conserva (cambiarlo obliga a publicar un agente con la huella nueva)."
fi
PIN=$(openssl x509 -in "$DIR/tls/cert.pem" -pubkey -noout | openssl pkey -pubin -outform der | openssl dgst -sha256 -hex | sed 's/.* //')

mkdir -p "$HOME/.config/systemd/user"
cat > "$HOME/.config/systemd/user/gpu-relay.service" <<EOF
[Unit]
Description=GPU Prestada · relé (túnel para PCs sin Tailscale)
After=network-online.target

[Service]
Environment=GPU_RELAY_BROKER=$GPU_RELAY_BROKER
Environment=GPU_RELAY_PUB_PORT=$PUB
Environment=GPU_RELAY_LAN_BIND=$LAN_BIND
Environment=GPU_RELAY_LAN_PORT=$LAN_PORT
Environment=GPU_RELAY_LAN=$LAN_IP
Environment=GPU_RELAY_LAN_ALLOW=$LAN_ALLOW
ExecStart=$DIR/.venv/bin/python $DIR/relay.py
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable gpu-relay.service >/dev/null 2>&1
systemctl --user restart gpu-relay.service
loginctl enable-linger >/dev/null 2>&1 || echo "⚠ Para que siga al cerrar la sesión: sudo loginctl enable-linger $USER"

sleep 2
systemctl --user is-active --quiet gpu-relay.service || { journalctl --user -u gpu-relay -n 15 --no-pager; fallo "El relé no arranca."; }

echo
echo "✓ Relé en marcha"
echo "   Público (TLS):   https://127.0.0.1:$PUB   → publícalo en el 443 de «$NAME» con tu proxy inverso o redirección"
echo "   Interno:         http://$LAN_IP:$LAN_PORT/n/<nodo>/…   (lo usa el proxy de Ollama)"
echo "   Huella (SHA-256 de la clave pública):"
echo "      $PIN"
echo "   Para que los agentes se fíen de este relé, compila el agente con esa huella en «defaultRelayPin»"
echo "   (agent/tunnel.go) y con tu dirección en «defaultRelay»; o ponlos en config.json (relay, relay_pin)."
echo "   Ver el registro:  journalctl --user -u gpu-relay -f"
