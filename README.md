# GPU Prestada ("Lent GPU")

**English** | [Español](README.es.md)

Lends the graphics card of a **Windows or Linux** PC to a home-lab AI stack (Ollama, ComfyUI, Whisper), and lets that PC use the lab's cards in return. Built for the author's own lab, published as a reference.

## Layout

- `agent/` — the Go agent (`gpu-prestada.exe` with a tray icon on Windows; a single binary + systemd user service on Linux). Build:
  ```sh
  cd agent
  GOOS=windows GOARCH=amd64 go build -trimpath -ldflags "-H windowsgui -s -w" -o gpu-prestada.exe .
  GOOS=linux   GOARCH=amd64 go build -trimpath -ldflags "-s -w" -o gpu-prestada-linux-amd64 .
  ```
  On Linux, `-headless -config file.json` is handy for tests (`pruebas/bin/nvidia-smi` is a fake `nvidia-smi` to put in `PATH`).
- `server/nodes.py` — node registry and `/_nodes/*` routes; runs inside the Ollama proxy. `server/patch_*.py` are idempotent patches applied to the lab's services (Ollama proxy, ComfyUI proxy, VRAM monitor), kept as a change history.
- `relay/relay.py` — relay / tunnel for PCs without Tailscale (WebSocket tunnel, pinned self-signed TLS).
- `web/` — the product page and download endpoints (`instalar.sh`, `descargar.php`).
- `publicar.sh` — builds all platforms, uploads them and publishes; agents self-update from the SHA in their heartbeat.

## Two ways to reach a PC

1. **Tailscale**: the agent listens on its tailnet IP and the proxy connects to it. Enrolment is automatic inside the tailnet.
2. **Tunnel** (no extra software): the agent opens an outgoing WebSocket to a relay; the owner pastes an *enrolment code* (or the admin approves the PC from the VRAM monitor). The agent embeds the SHA-256 pin of the relay's public key (`defaultRelayPin` in `agent/tunnel.go`): if the certificate is regenerated, publish an agent with the new pin first.

`mode: auto` picks Tailscale if present, tunnel otherwise.

## Features

Lend the GPU with automatic pause when you use it · use the lab's cards from your own Ollama (`model@server`, `model@card`) via a combined local endpoint · RAM/disk/CPU meters · Whisper and ComfyUI (images, video) as lent services with queue redistribution across cards · "AI programs" launcher (`ollama launch`: Claude Code, Codex, OpenCode…) with models grouped by machine and filtered to those that support tools and enough context.

## Linux

One binary, no desktop required. One-line install (as your normal user, not root):

```sh
curl -fsSL https://lomastrend.com/gpu-prestada/instalar.sh | sh
```

It installs to `~/.local/bin`, runs as a systemd user service (with *linger*, so it survives SSH logout) and is driven with `gpu-prestada` (navigable text menu), `estado`, `codigo XXXX`, `pausar`, `reanudar`, `parar`, `desinstalar`. The menu opens AI programs against the combined Ollama (`127.0.0.1:11438`), choosing program and model.

## Install from GitHub (one command each)

**Client** (Linux, the PC that lends or uses the cards):

```sh
curl -fsSL https://raw.githubusercontent.com/chemazener/gpu-prestada/main/web/instalar.sh | sh
```

**Server** (the relay for PCs without Tailscale, any Linux): installs `relay.py` for your user (Python venv, 10-year self-signed certificate, systemd user service) and prints the certificate **pin**:

```sh
curl -fsSL https://raw.githubusercontent.com/chemazener/gpu-prestada/main/relay/instalar-relay.sh \
  | GPU_RELAY_BROKER=http://YOUR-OLLAMA-PROXY:11434 GPU_RELAY_NAME=relay.example.com sh
```

Only `GPU_RELAY_BROKER` is required (other variables are documented at the top of the script). The relay is the only server piece that installs on its own: `server/nodes.py` and the patches apply to the author's Ollama proxy / vram-broker / ComfyUI proxy, which are not in this repository. To use your own relay, build the agent with its pin and address (`defaultRelayPin`, `defaultRelay` in `agent/tunnel.go`); the client installer downloads the binary from the author's server.

## Notes

- This is the author's lab code: it contains private-network addresses (Tailscale/LAN) and machine names. They are not reachable from outside, but change them for your environment.
- No binaries or secrets are included: the relay private key, tokens and test `config.json` files are excluded.
- UI text and docs are mostly in Spanish.
