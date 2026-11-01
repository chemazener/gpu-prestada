#!/usr/bin/env python3
"""nodes.py: equipos de GPU Prestada como CLIENTES de la IA del servidor (agente 1.8.0, 31-oct-2026).
Permiso por equipo en nodes.json «clients» {nombre: {enabled, max_inflight}}, que se cambia desde el
Monitor VRAM (POST /_nodes/client/{nombre}, solo desde el propio servidor).
Uso: python3 patch_nodes_clientes.py /opt/ollama-proxy/nodes.py"""
import sys

p = sys.argv[1]
s = open(p, encoding="utf-8").read()
if "CLIENT_INFLIGHT" in s:
    sys.exit("ya parcheado")


def rep(a, b):
    global s
    assert s.count(a) == 1, a[:80]
    s = s.replace(a, b)


rep('import hashlib\n', 'import contextvars\nimport hashlib\n')
rep('from fastapi.responses import FileResponse', 'from fastapi.responses import FileResponse, JSONResponse')
rep('''        and n.get("name") not in _flag("paused_nodes") and n.get("name") not in _flag("disconnected_nodes")
''', '''        and n.get("name") not in _flag("paused_nodes") and n.get("name") not in _flag("disconnected_nodes") \\
        and n.get("name") != EXCLUDE.get()


# Equipo que hace la petición como cliente (puerto local del agente, 1.8.0): nunca se le
# devuelve su propia petición, ni pidiéndolo ni por desbordamiento.
EXCLUDE: contextvars.ContextVar = contextvars.ContextVar("gpu_prestada_excluir", default=None)
''')
rep('''def client_for(n: dict) -> httpx.AsyncClient:''', '''# ---------- equipos que usan la IA del servidor desde su puerto local (agente 1.8.0) ----------
CLIENT_INFLIGHT: dict = {}
CLIENT_SERVED: dict = {}


def client_conf(name: str) -> dict:
    d = (conf().get("clients") or {}).get(name) or {}
    return {"enabled": bool(d.get("enabled")), "max_inflight": max(1, min(8, _int(d.get("max_inflight"), 2)))}


def client_check(request: Request, name: str):
    """(nombre, None) si puede pasar; (None, respuesta de error al estilo Ollama) si no."""
    def err(code, msg):
        return None, JSONResponse({"error": msg}, status_code=code)
    if request.headers.get("x-node-token") != conf()["token"]:
        return err(401, "código de alta incorrecto")
    name = name.strip().lower()[:40]
    cc = client_conf(name)
    if not cc["enabled"]:
        return err(403, "este equipo no tiene permiso para usar la IA del servidor (se activa en el Monitor VRAM)")
    if CLIENT_INFLIGHT.get(name, 0) >= cc["max_inflight"]:
        return err(429, f"este equipo ya tiene {cc['max_inflight']} peticiones en curso; espera a que acabe alguna")
    return name, None


def client_enter(name: str):
    CLIENT_INFLIGHT[name] = CLIENT_INFLIGHT.get(name, 0) + 1


def client_leave(name: str):
    CLIENT_INFLIGHT[name] = max(0, CLIENT_INFLIGHT.get(name, 0) - 1)
    CLIENT_SERVED[name] = CLIENT_SERVED.get(name, 0) + 1


def _client_info(name: str) -> dict:
    return dict(client_conf(name), inflight=CLIENT_INFLIGHT.get(name, 0), served=CLIENT_SERVED.get(name, 0))


@router.post("/_nodes/client/{name}")
async def set_client(name: str, request: Request):
    """Activa/desactiva el uso como cliente de un equipo. Solo desde el propio servidor (Monitor VRAM)."""
    try:
        local = ipaddress.ip_address(_client_ip(request)).is_loopback
    except ValueError:
        local = False
    if not local:
        raise HTTPException(403, "solo desde el propio servidor")
    b = await request.json()
    name = name.strip().lower()[:40]
    if not name:
        raise HTTPException(400, "falta el nombre")
    data = dict(conf())
    clients = {k: dict(v) for k, v in (data.get("clients") or {}).items()}
    cur = clients.setdefault(name, {})
    if "enabled" in b:
        cur["enabled"] = bool(b["enabled"])
    if "max_inflight" in b:
        cur["max_inflight"] = max(1, min(8, _int(b["max_inflight"], 2)))
    data["clients"] = clients
    _conf_write(data)
    print(f"[nodes] {name}: cliente {cur} desde el Monitor VRAM", flush=True)
    return _client_info(name)


def client_for(n: dict) -> httpx.AsyncClient:''')
rep('''    return {"ok": True, "mode": c["mode"], "want_models": c.get("want_models") or [],''',
    '''    return {"ok": True, "mode": c["mode"], "want_models": c.get("want_models") or [], "client": client_conf(name),''')
rep('''        d["alive"] = _alive(n)''', '''        d["client"] = _client_info(n["name"])
        d["alive"] = _alive(n)''')
open(p, "w", encoding="utf-8").write(s)
print("parcheado")
