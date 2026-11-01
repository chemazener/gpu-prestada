#!/usr/bin/env python3
"""nodes.py: «Equipos esperando alta» (agente 1.10.0, 31-oct-2026). Un PC sin Tailscale y sin código
pide el alta por el relé (POST /_nodes/hello con un request_id propio); sale en el Monitor VRAM y,
al pulsar «Dar de alta», su siguiente petición recibe el código. Rechazado: se le ignora 1 hora.
Uso: python3 patch_nodes_altas.py /opt/ollama-proxy/nodes.py"""
import sys

p = sys.argv[1]
s = open(p, encoding="utf-8").read()
if "PENDING_ALTAS" in s:
    sys.exit("ya parcheado")


def rep(a, b):
    global s
    assert s.count(a) == 1, a[:80]
    s = s.replace(a, b)


rep('''def client_for(n: dict) -> httpx.AsyncClient:''', '''# ---------- equipos esperando alta (agente 1.10.0) ----------
PENDING_ALTAS: dict = {}   # request_id -> datos del PC
APPROVED_ALTAS: dict = {}  # request_id -> cuándo se aprobó
REJECTED_ALTAS: dict = {}  # request_id -> cuándo se rechazó


@router.post("/_nodes/hello")
async def hello(request: Request):
    """Un PC sin código pide el alta (llega por el relé)."""
    if not _from_relay(request):
        raise HTTPException(403, "solo por el relé")
    b = await request.json()
    rid = str(b.get("request_id") or "")[:64]
    if len(rid) < 16:
        raise HTTPException(400, "falta request_id")
    now = time.time()
    if rid in APPROVED_ALTAS:
        APPROVED_ALTAS.pop(rid, None)
        PENDING_ALTAS.pop(rid, None)
        return {"token": conf()["token"]}
    if now - REJECTED_ALTAS.get(rid, 0) < 3600:
        return {"pending": False, "rejected": True}
    prev = PENDING_ALTAS.get(rid) or {}
    PENDING_ALTAS[rid] = {
        "request_id": rid, "name": str(b.get("name") or "")[:40].strip().lower(), "gpu": str(b.get("gpu") or "")[:80],
        "vram_total_mb": _int(b.get("vram_total_mb"), 0), "version": str(b.get("version") or "")[:20],
        "first": prev.get("first", now), "seen": now,
    }
    return {"pending": True}


def pending_altas() -> list:
    now = time.time()
    for k in [k for k, v in PENDING_ALTAS.items() if now - v["seen"] > 600]:
        del PENDING_ALTAS[k]   # sin noticias en 10 min: se apagó o ya no espera
    return sorted(({**v, "waiting_s": round(now - v["first"]), "seen_s_ago": round(now - v["seen"]),
                    "name_taken": v["name"] in NODES} for v in PENDING_ALTAS.values()), key=lambda v: v["first"])


@router.post("/_nodes/pending/{rid}/{action}")
async def pending_action(rid: str, action: str, request: Request):
    """«Dar de alta» / «Rechazar» desde el Monitor VRAM. Solo desde el propio servidor."""
    try:
        local = ipaddress.ip_address(_client_ip(request)).is_loopback
    except ValueError:
        local = False
    if not local:
        raise HTTPException(403, "solo desde el propio servidor")
    if rid not in PENDING_ALTAS:
        raise HTTPException(404, "ese equipo ya no está esperando")
    name = PENDING_ALTAS[rid]["name"]
    if action == "approve":
        APPROVED_ALTAS[rid] = time.time()
    elif action == "reject":
        REJECTED_ALTAS[rid] = time.time()
        PENDING_ALTAS.pop(rid, None)
    else:
        raise HTTPException(400, "acción desconocida")
    print(f"[nodes] alta de {name}: {action} desde el Monitor VRAM", flush=True)
    return {"ok": True, "name": name, "action": action}


def client_for(n: dict) -> httpx.AsyncClient:''')
rep('''            "paused": sorted(_flag("paused_nodes")), "disconnected": sorted(_flag("disconnected_nodes"))}''',
    '''            "paused": sorted(_flag("paused_nodes")), "disconnected": sorted(_flag("disconnected_nodes")),
            "pending": pending_altas()}''')
open(p, "w", encoding="utf-8").write(s)
print("parcheado")
