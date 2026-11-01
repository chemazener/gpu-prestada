#!/usr/bin/env python3
"""nodes.py: servicios además de Ollama en los PCs prestados (agente 1.12.0, 31-oct-2026): Whisper y ComfyUI.
nodes.json:
  "service_specs": {"whisper": {...}, "comfy": {...}}   → qué instalar (ficheros con tamaño y huella)
  "node_services": {"petercobo": ["whisper", "comfy"]}  → qué servicios quiere cada PC
El latido devuelve «services» (las especificaciones de ese PC) y guarda lo que el PC informa («services»).
/_nodes/svc/{svc}/{ruta}: reenvía al PC (el indicado en ?_node= o cabecera X-GPU-Node, o el mejor con el
servicio listo). Solo desde este equipo, la LAN o Tailscale (el servicio de Whisper y el proxy de ComfyUI).
Uso: python3 patch_nodes_servicios.py /opt/ollama-proxy/nodes.py"""
import sys

p = sys.argv[1]
s = open(p, encoding="utf-8").read()
if "def node_services" in s:
    sys.exit("ya parcheado")


def rep(a, b):
    global s
    assert s.count(a) == 1, a[:80]
    s = s.replace(a, b)


rep('from fastapi.responses import FileResponse, JSONResponse', 'from fastapi.responses import FileResponse, JSONResponse, StreamingResponse')
rep('''def client_for(n: dict) -> httpx.AsyncClient:''', '''# ---------- servicios además de Ollama: Whisper y ComfyUI (agente 1.12.0) ----------
def node_services(name: str) -> dict:
    c = conf()
    specs = c.get("service_specs") or {}
    return {k: specs[k] for k in ((c.get("node_services") or {}).get(name) or []) if k in specs}


def _svc_ready(n: dict, svc: str, sub: str = "") -> bool:
    st = (n.get("services") or {}).get(svc) or {}
    if st.get("state") != "ready":
        return False
    return not (svc == "whisper" and sub and sub not in (st.get("models") or []))


@router.api_route("/_nodes/svc/{svc}/{path:path}", methods=["GET", "POST"])
async def svc_forward(svc: str, path: str, request: Request):
    if not _from_relay(request):
        raise HTTPException(403, "solo desde casa o la red privada")
    if svc not in ("whisper", "comfy"):
        raise HTTPException(404, "servicio desconocido")
    want = (request.query_params.get("_node") or request.headers.get("x-gpu-node") or "").strip().lower()
    sub = path.split("/", 1)[0]
    if want:
        n = NODES.get(want)
        if not n or not _alive(n):
            raise HTTPException(503, f"{want} no está conectada")
    else:
        cands = [n for n in NODES.values() if _usable(n) and _svc_ready(n, svc, sub)]
        if not cands:
            raise HTTPException(503, f"ninguna tarjeta prestada tiene {svc} listo")
        n = max(cands, key=lambda x: x.get("vram_free_mb", 0))
    params = [(k, v) for k, v in request.query_params.multi_items() if k != "_node"]
    headers = {k: v for k, v in request.headers.items() if k.lower() in ("content-type", "accept", "range")}
    headers.update(auth_headers())
    rc = client_for(n)
    try:
        req = rc.build_request(request.method, f"/_svc/{svc}/{path}", params=params, headers=headers,
                               content=await request.body())
        up = await rc.send(req, stream=True)
    except Exception as e:
        raise HTTPException(502, f"{n['name']} no responde: {e}")

    async def it():
        try:
            async for chunk in up.aiter_raw():
                yield chunk
        finally:
            await up.aclose()
    out = {k: v for k, v in up.headers.items() if k.lower() in ("content-type", "content-disposition", "content-range")}
    out["X-Served-By"] = n["name"]
    return StreamingResponse(it(), status_code=up.status_code, headers=out)


@router.get("/_nodes/check")
async def check_token(request: Request):
    """El relé pregunta si un código de alta es válido (almacén de ficheros, PCs con o sin túnel)."""
    if not _from_relay(request):
        raise HTTPException(403, "solo por el relé")
    if request.headers.get("x-node-token") != conf()["token"]:
        raise HTTPException(401, "código incorrecto")
    return {"ok": True}


def client_for(n: dict) -> httpx.AsyncClient:''')
rep('''    want, create = node_models(name)
    return {"ok": True, "mode": c["mode"], "want_models": want, "create_models": create,''',
    '''    want, create = node_models(name)
    return {"ok": True, "mode": c["mode"], "want_models": want, "create_models": create, "services": node_services(name),''')
# guardar lo que el PC informa de sus servicios
rep('''        "jobs": _list(b, "jobs", 20,''', '''        "services": {k: {"state": str((v or {}).get("state") or "")[:12], "detail": str((v or {}).get("detail") or "")[:160],
                         "progress": _int((v or {}).get("progress"), -1),
                         "models": [str(m)[:20] for m in ((v or {}).get("models") or [])][:8]}
                     for k, v in (b.get("services") or {}).items() if k in ("whisper", "comfy") and isinstance(v, dict)},
        "jobs": _list(b, "jobs", 20,''')
open(p, "w", encoding="utf-8").write(s)
print("parcheado")
