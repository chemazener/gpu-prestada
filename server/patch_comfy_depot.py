#!/usr/bin/env python3
"""comfyui-proxy (CT 106): /_depot/{ruta} sirve los ficheros que descargan los PCs de GPU Prestada (1.12.0).
Solo los de /opt/depot/manifest.json y solo al relé de la CT 100. Admite reanudar (Range).
Uso: python3 patch_comfy_depot.py /opt/comfyui-proxy/proxy.py"""
import sys

p = sys.argv[1]
s = open(p, encoding="utf-8").read()
if "/_depot/" in s:
    sys.exit("ya parcheado")
a = '''# Catch-all para resto de rutas (proxy transparente)'''
assert s.count(a) == 1
s = s.replace(a, '''# ---- Almacén para GPU Prestada (31-oct-2026): modelos y programas que descargan los PCs prestados ----
DEPOT_MANIFEST = os.getenv("DEPOT_MANIFEST", "/opt/depot/manifest.json")
DEPOT_CLIENTS = {"100.100.84.72", "192.168.100.11", "127.0.0.1"}   # relé de la CT 100


@app.get("/_depot/{path:path}")
async def depot(path: str, request: Request):
    from fastapi.responses import FileResponse
    if (request.client.host if request.client else "") not in DEPOT_CLIENTS:
        raise HTTPException(403, "solo el relé")
    try:
        with open(DEPOT_MANIFEST) as f:
            ent = json.load(f).get(path)
    except Exception:
        ent = None
    if not ent:
        raise HTTPException(404, "no está en el almacén")
    return FileResponse(ent["file"], media_type="application/octet-stream")


''' + a)
open(p, "w", encoding="utf-8").write(s)
print("parcheado")
