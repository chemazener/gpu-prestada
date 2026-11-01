"""
Nodos GPU remotos para ollama-proxy ("GPU Prestada").

Un PC con tarjeta gráfica ejecuta el agente gpu-prestada.exe, que se da de alta
aquí con un latido cada pocos segundos. El proxy puede entonces mandar peticiones
de Ollama a ese nodo en vez de a la 3080 local.

Modos (en /etc/ollama-proxy/nodes.json, se relee solo al cambiar):
  "explicit" -> solo va al nodo si el cliente lo pide: modelo "gemma4:e2b@nodo"
                o cabecera "X-GPU-Node: nodo".
  "prefer"   -> ademas, los modelos de "prefer_models" (o "*") van al nodo
                siempre que este vivo, prestando y tenga el modelo.
Si el nodo no esta disponible o falla, la peticion se atiende en local.

Dos formas de llegar al nodo:
  * Tailscale: el agente escucha en su IP de la tailnet y el proxy se conecta a el.
  * Tunel (PCs sin Tailscale): el agente abre un WebSocket saliente hacia el rele de la
    CT 100 (~/gpu-prestada/relay); el rele reenvia aqui sus latidos con "relay_url" y
    el proxy habla con esa URL (http://192.168.100.11:11437/n/<nodo>) como si fuera el nodo.
    Solo se acepta "relay_url" si el latido llega desde la LAN, la tailnet o loopback.
"""
import contextvars
import hashlib
import ipaddress
import json
import os
import secrets
import time
from typing import Optional, Tuple

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

CONF_PATH = os.getenv("NODES_CONF", "/etc/ollama-proxy/nodes.json")
AGENT_EXE = os.getenv("NODES_AGENT_EXE", "/opt/ollama-proxy/gpu-prestada.exe")
NODE_TTL_S = 30          # sin latido en este tiempo -> nodo caido
FAIL_COOLDOWN_S = 20     # tras un fallo de conexion, no reintentar durante este tiempo
TAILNET = ipaddress.ip_network("100.64.0.0/10")
LAN = ipaddress.ip_network("192.168.100.0/24")   # desde aqui puede hablar el rele (CT 100)

router = APIRouter()
NODES: dict = {}
_conf_cache = {"mtime": None, "data": None}
_clients: dict = {}

DEFAULT_CONF = {"mode": "explicit", "prefer_models": [], "want_models": []}


def conf() -> dict:
    try:
        mtime = os.path.getmtime(CONF_PATH)
    except OSError:
        os.makedirs(os.path.dirname(CONF_PATH), exist_ok=True)
        data = dict(DEFAULT_CONF, token=secrets.token_urlsafe(24))
        with open(CONF_PATH, "w") as f:
            json.dump(data, f, indent=2)
        os.chmod(CONF_PATH, 0o600)
        mtime = os.path.getmtime(CONF_PATH)
    if _conf_cache["mtime"] != mtime:
        with open(CONF_PATH) as f:
            _conf_cache["data"] = dict(DEFAULT_CONF, **json.load(f))
        _conf_cache["mtime"] = mtime
    return _conf_cache["data"]


_exe_cache = {"mtime": None, "sha": ""}


PLATFORMS = ("windows-amd64", "linux-amd64", "linux-arm64")
_sha_cache: dict = {}


def agent_file(platform: str = "windows-amd64") -> str:
    """Ejecutable publicado para ese sistema ('' si no es uno conocido)."""
    if platform not in PLATFORMS:
        return ""
    if platform == "windows-amd64":
        return AGENT_EXE
    return os.path.join(os.path.dirname(AGENT_EXE), "gpu-prestada-" + platform)


def agent_sha256(platform: str = "windows-amd64") -> str:
    """Huella del agente publicado para ese sistema. Los agentes la comparan con la suya y se actualizan solos."""
    f = agent_file(platform)
    try:
        mtime = os.path.getmtime(f)
    except (OSError, TypeError):
        return ""
    c = _sha_cache.get(f)
    if not c or c[0] != mtime:
        h = hashlib.sha256()
        with open(f, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        c = (mtime, h.hexdigest())
        _sha_cache[f] = c
    return c[1]


def _int(v, default: int) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _list(b: dict, key: str, limit: int, fn):
    v = b.get(key)
    if not isinstance(v, list):
        return None
    return [fn(x) for x in v[:limit] if isinstance(x, dict)]


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


def _from_tailnet(request: Request) -> bool:
    try:
        ip = ipaddress.ip_address(_client_ip(request))
    except ValueError:
        return False
    return ip.is_loopback or ip in TAILNET


def _from_relay(request: Request) -> bool:
    """El rele de la CT 100 (LAN o tailnet) o este mismo equipo."""
    try:
        ip = ipaddress.ip_address(_client_ip(request))
    except ValueError:
        return False
    return ip.is_loopback or ip in TAILNET or ip in LAN


def _alive(n: dict) -> bool:
    return time.time() - n["seen"] < NODE_TTL_S


def _flag(key: str) -> set:
    return {str(x).lower() for x in (conf().get(key) or [])}


def _usable(n: dict) -> bool:
    return _alive(n) and n.get("lending") and time.time() >= n.get("fail_until", 0) \
        and n.get("name") not in _flag("paused_nodes") and n.get("name") not in _flag("disconnected_nodes") \
        and n.get("name") != EXCLUDE.get()


# Equipo que hace la petición como cliente (puerto local del agente, 1.8.0): nunca se le
# devuelve su propia petición, ni pidiéndolo ni por desbordamiento.
EXCLUDE: contextvars.ContextVar = contextvars.ContextVar("gpu_prestada_excluir", default=None)


def _conf_write(data: dict):
    """Guarda nodes.json de forma atómica (conf() lo relee al cambiar la fecha)."""
    tmp = CONF_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.chmod(tmp, 0o600)
    os.replace(tmp, CONF_PATH)


def _has_model(n: dict, model: str) -> bool:
    names = n.get("models") or []
    return model in names or (":" not in model and model + ":latest" in names)


def node_models(name: str):
    """(modelos a descargar, alias a crear) para un equipo: want_models + los suyos de node_models."""
    c = conf()
    want = list(c.get("want_models") or [])
    create = []
    for e in (c.get("node_models") or {}).get(name) or []:
        if not isinstance(e, dict) or not e.get("name"):
            continue
        base = e.get("from") or e["name"]
        if base not in want:
            want.append(base)
        if e.get("from"):
            create.append({"name": e["name"], "from": e["from"], "parameters": e.get("parameters") or {}})
    return want, create


def pick(model: str, header_node: Optional[str]) -> Tuple[Optional[dict], str]:
    """Devuelve (nodo o None, nombre de modelo sin el sufijo @nodo)."""
    wanted = (header_node or "").strip().lower() or None
    clean = model
    if "@" in model:
        clean, _, suffix = model.rpartition("@")
        wanted = suffix.strip().lower() or wanted
    if wanted:
        n = NODES.get(wanted)
        return (n if n and _usable(n) and _has_model(n, clean) else None), clean
    c = conf()
    if c["mode"] == "prefer":
        pm = c.get("prefer_models") or []
        if "*" in pm or clean in pm:
            pn = {str(x).lower() for x in (c.get("prefer_nodes") or [])}
            cands = [n for n in NODES.values() if _usable(n) and _has_model(n, clean) and (not pn or n.get("name") in pn)]
            if cands:
                return max(cands, key=lambda n: n.get("vram_free_mb", 0)), clean
    return None, clean


def spill_target(model: str) -> Tuple[Optional[dict], Optional[str]]:
    """Desbordamiento (31-oct-2026): si la 3080 no da turno enseguida, ¿a qué nodo prestado y con qué modelo
    se puede mandar? El mismo modelo si el nodo lo tiene; si no, el equivalente de spill_models
    (p. ej. gemma4:e4b -> gemma4:e2b). Sin equivalente, no se desborda (se espera a la 3080)."""
    c = conf()
    if not c.get("spill"):
        return None, None
    alt = (c.get("spill_models") or {}).get(model)
    best = None
    for nd in NODES.values():
        if not _usable(nd):
            continue
        m = model if _has_model(nd, model) else (alt if alt and _has_model(nd, alt) else None)
        if m and (best is None or nd.get("vram_free_mb", 0) > best[0].get("vram_free_mb", 0)):
            best = (nd, m)
    return best if best else (None, None)


# ---------- equipos que usan la IA del servidor desde su puerto local (agente 1.8.0) ----------
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


def client_cards(name: str) -> dict:
    """Modelos de cada otra tarjeta prestada que ahora puede atender (sin la del propio equipo)."""
    if not client_conf(name)["enabled"]:
        return {}
    return {n["name"]: list(n.get("models") or []) for n in NODES.values() if n.get("name") != name and _usable(n)}


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


# ---------- equipos esperando alta (agente 1.10.0) ----------
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


# ---------- servicios además de Ollama: Whisper y ComfyUI (agente 1.12.0) ----------
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


def client_for(n: dict) -> httpx.AsyncClient:
    c = _clients.get(n["url"])
    if c is None:
        c = httpx.AsyncClient(base_url=n["url"], timeout=httpx.Timeout(600.0, connect=8.0))
        _clients[n["url"]] = c
    return c


def mark_failed(n: dict, why: str):
    n["fail_until"] = time.time() + FAIL_COOLDOWN_S
    n["last_error"] = why[:200]
    print(f"[nodes] {n['name']} fallo ({why[:120]}), se atiende en local", flush=True)


def mark_served(n: dict):
    n["served"] = n.get("served", 0) + 1
    n["last_served"] = time.time()


def auth_headers() -> dict:
    return {"X-Node-Token": conf()["token"]}


@router.post("/_nodes/enroll")
async def enroll(request: Request):
    """Entrega el token de alta. Solo a equipos de la tailnet."""
    if not _from_tailnet(request):
        raise HTTPException(403, "solo desde la tailnet")
    return {"token": conf()["token"]}


@router.post("/_nodes/heartbeat")
async def heartbeat(request: Request):
    if request.headers.get("x-node-token") != conf()["token"]:
        raise HTTPException(401, "token incorrecto")
    b = await request.json()
    name = str(b.get("name") or "").strip().lower()[:40]
    port = int(b.get("port") or 0)
    if not name or not (1024 <= port <= 65535):
        raise HTTPException(400, "faltan name/port")
    if name in _flag("disconnected_nodes"):
        NODES.pop(name, None)
        raise HTTPException(403, "desconectada desde el Monitor VRAM")
    ip = _client_ip(request)
    relay_url = str(b.get("relay_url") or "")[:200]
    relayed = relay_url.startswith("http://") and _from_relay(request)
    # se actualiza el mismo dict: una peticion en curso puede tener una referencia a el
    NODES.setdefault(name, {}).update({
        "name": name,
        # la IP real, no la que diga el agente; por tunel, la URL del rele para ese nodo
        "url": relay_url if relayed else f"http://{ip}:{port}",
        "via": "túnel" if relayed else "tailscale",
        "seen": time.time(),
        "gpu": str(b.get("gpu") or "")[:80],
        "vendor": str(b.get("vendor") or "")[:12], "shared_mem": bool(b.get("shared_mem")),   # desde la 1.7.0
        "vram_total_mb": int(b.get("vram_total_mb") or 0),
        "vram_free_mb": int(b.get("vram_free_mb") or 0),
        "gpu_util": int(b.get("gpu_util") or 0),
        "models": [str(m) for m in (b.get("models") or [])][:200],
        "lending": bool(b.get("lending")),
        "reason": str(b.get("reason") or "")[:120],
        "version": str(b.get("version") or "")[:20],
        "inflight": int(b.get("inflight") or 0),
        "exe_sha256": str(b.get("exe_sha256") or "")[:64],
        "platform": str(b.get("platform") or "windows-amd64")[:20],
        # -1 = la tarjeta no informa de ese dato (o agente anterior a la 1.4.3)
        "temp": _int(b.get("temp"), -1), "power": _int(b.get("power"), -1),
        "power_limit": _int(b.get("power_limit"), -1), "fan": _int(b.get("fan"), -1),
        "lent_load": _int(b.get("lent_load"), 0), "auto_pause": bool(b.get("auto_pause")),
        "ip": str(b.get("remote_ip") or "por túnel")[:45] if relayed else ip,
        # desde la 1.6.0: procesos en la gráfica, modelos cargados en Ollama y trabajos en curso
        # (None = agente anterior, que no informa)
        "procs": _list(b, "procs", 30, lambda p: {"pid": _int(p.get("pid"), 0), "name": str(p.get("name") or "")[:80],
                                                   "mem_mb": _int(p.get("mem_mb"), -1),
                                                   "type": str(p.get("type") or "")[:4]}),
        "procs_src": str(b.get("procs_src") or "")[:12],   # de dónde sacó el agente el tipo (diagnóstico)
        "loaded": _list(b, "loaded", 20, lambda m: {"name": str(m.get("name") or "")[:80], "vram_mb": _int(m.get("vram_mb"), 0),
                                                    "size_mb": _int(m.get("size_mb"), 0), "expires_s": _int(m.get("expires_s"), -1)}),
        "services": {k: {"state": str((v or {}).get("state") or "")[:12], "detail": str((v or {}).get("detail") or "")[:160],
                         "progress": _int((v or {}).get("progress"), -1),
                         "models": [str(m)[:20] for m in ((v or {}).get("models") or [])][:8]}
                     for k, v in (b.get("services") or {}).items() if k in ("whisper", "comfy") and isinstance(v, dict)},
        # -1 = el agente no informa (anterior a la 1.12.1)
        "cpu": str(b.get("cpu") or "")[:80], "cpu_threads": _int(b.get("cpu_threads"), 0),
        "ram_total_mb": _int(b.get("ram_total_mb"), -1), "ram_free_mb": _int(b.get("ram_free_mb"), -1),
        "disk_drive": str(b.get("disk_drive") or "")[:8],
        "disk_total_mb": _int(b.get("disk_total_mb"), -1), "disk_free_mb": _int(b.get("disk_free_mb"), -1),
        "jobs": _list(b, "jobs", 20, lambda j: {"model": str(j.get("model") or "")[:80], "path": str(j.get("path") or "")[:60],
                                                "age_s": _int(j.get("age_s"), 0)}),
    })
    n = NODES[name]
    n.setdefault("first_seen", time.time())
    c = conf()
    want, create = node_models(name)
    return {"ok": True, "mode": c["mode"], "want_models": want, "create_models": create, "services": node_services(name),
            "client": dict(client_conf(name), cards=client_cards(name)),
            "agent_sha256": agent_sha256(NODES[name].get("platform") or "windows-amd64")}


@router.get("/_nodes")
async def list_nodes():
    now = time.time()
    c = conf()
    for k in [k for k, n in NODES.items() if now - n["seen"] > 3600]:
        del NODES[k]   # nodos apagados hace mas de una hora: fuera de la lista
    out = []
    for n in NODES.values():
        d = {k: v for k, v in n.items() if k not in ("seen", "fail_until", "last_served", "exe_sha256")}
        # None = agente antiguo que no informa de su huella (no se actualiza solo)
        d["up_to_date"] = (n["exe_sha256"] == agent_sha256(n.get("platform") or "windows-amd64")) if n.get("exe_sha256") else None
        d["client"] = _client_info(n["name"])
        d["alive"] = _alive(n)
        d["usable"] = _usable(n)
        d["seen_s_ago"] = round(now - n["seen"], 1)
        d["last_served_s_ago"] = round(now - n["last_served"], 1) if n.get("last_served") else None
        out.append(d)
    for d in out:
        d["paused"] = d["name"] in _flag("paused_nodes")
    return {"mode": c["mode"], "prefer_models": c.get("prefer_models") or [],
            "want_models": c.get("want_models") or [], "nodes": out,
            "paused": sorted(_flag("paused_nodes")), "disconnected": sorted(_flag("disconnected_nodes")),
            "pending": pending_altas()}


ADMIN_ACTIONS = {"pause": ("paused_nodes", True), "resume": ("paused_nodes", False),
                 "disconnect": ("disconnected_nodes", True), "reconnect": ("disconnected_nodes", False)}


@router.post("/_nodes/admin/{name}/{action}")
async def node_admin(name: str, action: str, request: Request):
    """Pausar / reanudar / desconectar / volver a conectar una máquina (botones del Monitor VRAM, 31-oct-2026).
    Solo desde este mismo equipo (el panel corre aquí). Se guarda en nodes.json: sobrevive a reinicios."""
    try:
        local = ipaddress.ip_address(_client_ip(request)).is_loopback
    except ValueError:
        local = False
    if not local:
        raise HTTPException(403, "solo desde el propio servidor")
    if action not in ADMIN_ACTIONS:
        raise HTTPException(400, "acción desconocida")
    name = name.strip().lower()[:40]
    if not name:
        raise HTTPException(400, "falta el nombre")
    key, on = ADMIN_ACTIONS[action]
    data = dict(conf())
    cur = [x for x in (data.get(key) or []) if str(x).lower() != name]
    if on:
        cur.append(name)
    data[key] = cur
    _conf_write(data)
    if action == "disconnect":
        NODES.pop(name, None)
    print(f"[nodes] {name}: {action} desde el Monitor VRAM", flush=True)
    return {"ok": True, "name": name, "action": action}


@router.get("/_nodes/agent.json")
async def agent_json(request: Request):
    """Huella y tamaño del agente publicado: el agente lo consulta al arrancar para actualizarse
    sin esperar al latido (y sin necesitar token). Solo tailnet; el rele lo expone en /agent.json."""
    if not _from_tailnet(request):
        raise HTTPException(403, "solo desde la tailnet")
    plat = request.query_params.get("platform") or "windows-amd64"
    f = agent_file(plat)
    if not f or not os.path.exists(f):
        raise HTTPException(404, "agente no publicado para " + plat)
    st = os.stat(f)
    return {"sha256": agent_sha256(plat), "bytes": st.st_size, "mtime": int(st.st_mtime), "platform": plat}


@router.get("/_nodes/agent.exe")
async def agent_exe(request: Request):
    if not _from_tailnet(request):
        raise HTTPException(403, "solo desde la tailnet")
    plat = request.query_params.get("platform") or "windows-amd64"
    f = agent_file(plat)
    if not f or not os.path.exists(f):
        raise HTTPException(404, "agente no publicado para " + plat)
    name = "gpu-prestada.exe" if plat == "windows-amd64" else "gpu-prestada-" + plat
    return FileResponse(f, filename=name, media_type="application/octet-stream")
