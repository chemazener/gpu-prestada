# GPU Prestada

[English](README.md) | **Español**

Presta la tarjeta gráfica de un PC con **Windows o Linux** al stack de IA (Ollama) del lab, y deja que ese PC use las tarjetas del lab.

- `agent/` — agente en Go (`gpu-prestada.exe`, icono de bandeja). Compilar:
  `cd agent && GOOS=windows GOARCH=amd64 ~/.local/go-dist/go/bin/go build -trimpath -ldflags "-H windowsgui -s -w" -o gpu-prestada.exe .`
  En Linux, `go build` + `-headless -config fichero.json` sirve para pruebas (con un `nvidia-smi` falso en el PATH,
  hay uno en `pruebas/bin/`).
- `server/nodes.py` — registro de nodos y rutas `/_nodes/*`; va en `/opt/ollama-proxy/` de la CT 101.
- `server/patch_proxy.py`, `patch_proxy2.py` — enganche en `proxy.py` y corrección de la fuga de turnos (idempotentes).
- `relay/relay.py` — **relé/túnel para PCs sin Tailscale** (servicio de usuario `gpu-relay` en la CT 100, venv `relay/.venv`).
- `web/` — página `lomastrend.com/gpu-prestada/` (se sube a nanabuntu; Unison la lleva al VPS).

## Dos formas de llegar al PC

1. **Tailscale** (la original): el agente escucha en su IP de la tailnet (`:11435`) y el proxy de la CT 101 se conecta a él.
   Se da de alta solo (`/_nodes/enroll` solo admite la tailnet).
2. **Túnel** (desde la 1.5.0, para PCs donde no se quiere instalar nada más): el agente abre un WebSocket saliente a
   `wss://casa.lomastrend.com/ws` (443 de casa → `proxy-casa` reparte por SNI → relé TLS en `127.0.0.1:8445`). El relé
   reenvía los latidos a la CT 101 añadiendo `relay_url`, y la CT 101 habla con `http://100.100.84.72:11437/n/<nodo>`
   (lado interno del relé, por Tailscale: el cortafuegos PVE de la CT 101 no deja pasar a la LAN de la CT 100) como si
   fuera el nodo. El certificado del relé es propio (`relay/tls/`, 10 años) y el agente lleva compilada la huella SHA-256
   de su clave pública (`defaultRelayPin` en `agent/tunnel.go`): si se regenera el certificado hay que publicar antes un
   agente con la huella nueva. En este modo no hay alta automática: el dueño del PC pega el **código de alta** (el
   `token` de `/etc/ollama-proxy/nodes.json` de la CT 101) en la ventana del agente. No se abre el cortafuegos de Windows.
   La elección es automática (`mode: auto`: Tailscale si lo hay, si no túnel); `mode` puede forzarse en `config.json`.

Publicar una versión nueva del agente: `./publicar.sh` (compila, sube a la CT 101 y a lomastrend.com; los agentes se
actualizan solos: por Tailscale desde `/_nodes/agent.exe`, por túnel desde `https://casa.lomastrend.com/agent.exe`).

Configuración del reparto: `/etc/ollama-proxy/nodes.json` en la CT 101 (`mode`: `explicit` | `prefer`,
`prefer_models`, `want_models`). Estado: `GET /_nodes` (campo `via`: `tailscale` o `túnel`) y, en la CT 100,
`GET http://127.0.0.1:11437/_relay` (túneles abiertos).

## Linux (desde la 1.15)

Un solo binario, sin escritorio obligatorio. Instalación en una línea (con tu usuario, no root):

```sh
curl -fsSL https://lomastrend.com/gpu-prestada/instalar.sh | sh
```

Se instala en `~/.local/bin`, queda como servicio de usuario de systemd (con *linger* para seguir al cerrar la sesión SSH) y se maneja con `gpu-prestada` (menú de texto navegable), `estado`, `codigo XXXX`, `pausar`, `reanudar`, `parar`, `desinstalar`. Desde el menú se abren los «Programas con IA» (Claude Code, Codex, OpenCode…) contra el Ollama combinado del PC (`127.0.0.1:11438`), eligiendo programa y modelo.

## Instalar desde GitHub (una orden para cada lado)

**Cliente** (Linux, el PC que presta o usa las tarjetas):

```sh
curl -fsSL https://raw.githubusercontent.com/chemazener/gpu-prestada/main/web/instalar.sh | sh
```

**Servidor** (el relé para PCs sin Tailscale, cualquier Linux): instala `relay.py` para tu usuario (entorno de Python, certificado propio de 10 años, servicio de usuario de systemd) y muestra la **huella** del certificado:

```sh
curl -fsSL https://raw.githubusercontent.com/chemazener/gpu-prestada/main/relay/instalar-relay.sh \
  | GPU_RELAY_BROKER=http://TU-PROXY-OLLAMA:11434 GPU_RELAY_NAME=relay.midominio.com sh
```

Solo `GPU_RELAY_BROKER` es obligatoria (las demás variables están en la cabecera del script). El relé es la única pieza del servidor que se instala sola: `server/nodes.py` y los parches se aplican sobre el proxy de Ollama, el vram-broker y el proxy de ComfyUI del autor, que no están en este repositorio. Para usar tu propio relé, compila el agente con su huella y dirección (`defaultRelayPin`, `defaultRelay` en `agent/tunnel.go`); el instalador del cliente baja el binario del servidor del autor.

## Notas para publicar / reutilizar

- Este repositorio es el código del lab de chema: contiene direcciones de su red privada (Tailscale/LAN) y nombres de máquinas. No son accesibles desde fuera, pero habrá que cambiarlas para otro entorno.
- No incluye binarios ni secretos: la clave privada del relé (`relay/tls/key.pem`), los tokens y los `config.json` de prueba están excluidos. El agente lleva compilada la huella del certificado del relé (`defaultRelayPin` en `agent/tunnel.go`).
- `server/patch_*.py` son parches idempotentes aplicados sobre los servicios del lab (proxy de Ollama, ComfyUI, Monitor VRAM); se conservan como historial de cambios.
