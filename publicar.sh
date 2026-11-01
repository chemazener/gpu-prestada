#!/bin/bash
# Compila GPU Prestada para Windows y la publica en la CT 101.
# Los agentes en marcha (1.3.0 o posterior) ven la huella nueva en su siguiente
# latido y se actualizan solos cuando no están atendiendo un trabajo.
set -euo pipefail
cd "$(dirname "$0")/agent"
export PATH="$HOME/.local/go-dist/go/bin:$PATH" GOFLAGS=-mod=mod
go vet ./... && GOOS=windows GOARCH=amd64 go vet ./...
GOOS=windows GOARCH=amd64 go build -trimpath -ldflags "-H windowsgui -s -w" -o gpu-prestada.exe .
# Linux (1.15.0): un ejecutable por arquitectura; cada agente se actualiza con el de su sistema
GOOS=linux GOARCH=amd64 go vet ./...
for A in amd64 arm64; do GOOS=linux GOARCH=$A go build -trimpath -ldflags "-s -w" -o gpu-prestada-linux-$A .; done
echo "versión: $(grep -oP 'var version = "\K[^"]+' main.go) · sha256: $(sha256sum gpu-prestada.exe | cut -c1-16)…"
scp -q gpu-prestada.exe root@192.168.100.10:/tmp/gpu-prestada.exe
# se copia con otro nombre y se renombra: los agentes nunca ven un fichero a medias
ssh root@192.168.100.10 'pct push 101 /tmp/gpu-prestada.exe /opt/ollama-proxy/gpu-prestada.exe.new && rm /tmp/gpu-prestada.exe && pct exec 101 -- mv /opt/ollama-proxy/gpu-prestada.exe.new /opt/ollama-proxy/gpu-prestada.exe'
for A in amd64 arm64; do
  scp -q gpu-prestada-linux-$A root@192.168.100.10:/tmp/gpu-prestada-linux-$A
  ssh root@192.168.100.10 "pct push 101 /tmp/gpu-prestada-linux-$A /opt/ollama-proxy/gpu-prestada-linux-$A.new && rm /tmp/gpu-prestada-linux-$A && pct exec 101 -- mv /opt/ollama-proxy/gpu-prestada-linux-$A.new /opt/ollama-proxy/gpu-prestada-linux-$A"
done

# Copia para la descarga desde lomastrend.com/gpu-prestada/ (solo se edita nanabuntu; Unison la lleva al VPS)
V=$(grep -oP 'var version = "\K[^"]+' main.go)
MES=(enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre diciembre)
printf '{"version":"%s","sha256":"%s","bytes":%s,"fecha":"%s","linux_amd64":{"sha256":"%s","bytes":%s},"linux_arm64":{"sha256":"%s","bytes":%s}}\n' \
  "$V" "$(sha256sum gpu-prestada.exe | cut -d' ' -f1)" "$(stat -c %s gpu-prestada.exe)" "$(date +%-d) de ${MES[$(date +%-m)-1]} de $(date +%Y)" \
  "$(sha256sum gpu-prestada-linux-amd64 | cut -d' ' -f1)" "$(stat -c %s gpu-prestada-linux-amd64)" \
  "$(sha256sum gpu-prestada-linux-arm64 | cut -d' ' -f1)" "$(stat -c %s gpu-prestada-linux-arm64)" > /tmp/gpu-prestada-version.json
WEB=chemazener@100.104.138.40:/var/www/html/lomastrend/gpu-prestada
scp -q gpu-prestada.exe "$WEB/gpu-prestada.exe.new" && scp -q gpu-prestada-linux-amd64 "$WEB/gpu-prestada-linux-amd64.new" \
  && scp -q gpu-prestada-linux-arm64 "$WEB/gpu-prestada-linux-arm64.new" && scp -q /tmp/gpu-prestada-version.json "$WEB/version.json" \
  && ssh chemazener@100.104.138.40 'cd /var/www/html/lomastrend/gpu-prestada && mv gpu-prestada.exe.new gpu-prestada.exe && mv gpu-prestada-linux-amd64.new gpu-prestada-linux-amd64 && mv gpu-prestada-linux-arm64.new gpu-prestada-linux-arm64' \
  && echo "web actualizada" || echo "AVISO: no se pudo actualizar la descarga de lomastrend.com"
rm -f /tmp/gpu-prestada-version.json
echo "publicada"
