#!/usr/bin/env python3
"""comfyui-proxy (CT 106): imágenes en un PC de GPU Prestada cuando la 3080 está ocupada (31-oct-2026).

/opt/comfyui-proxy/remoto.json  {"mode": "spill"}   spill = solo si la 3080 está ocupada (por defecto)
                                                     prefer = siempre que haya un PC listo · off = nunca
Un /prompt va a un PC si: hay PCs con ComfyUI listo (según el proxy de Ollama, /_nodes), el flujo solo usa
nodos y ficheros que ese PC tiene (se comprueba con su /object_info) y, en modo spill, la 3080 está ocupada
(cola de ComfyUI con trabajo u otro servicio con el turno del broker).
- Las imágenes de entrada (LoadImage) se suben antes al PC.
- Las salidas se guardan allí en la subcarpeta «rmt_<pc>»: /view con esa subcarpeta va al PC.
- /history/<id>, /queue, /interrupt y el borrado de la cola de un trabajo remoto van al PC.
- Si el PC falla (error, o deja de responder 3 min), el trabajo se repite en la 3080 con el mismo id
  para Bicho Magic.
Uso: python3 patch_comfy_remoto.py /opt/comfyui-proxy/proxy.py   (sobre el que ya tiene /_depot/)
"""
import sys

p = sys.argv[1]
s = open(p, encoding="utf-8").read()
if "_try_remote" in s:
    sys.exit("ya parcheado")


def rep(a, b):
    global s
    assert s.count(a) == 1, a[:90]
    s = s.replace(a, b)


# 1) /prompt: primero se intenta en un PC prestado; la lógica de siempre pasa a _post_local
rep('''async def post_prompt(request: Request):
    body = await request.body()
    async with LEASE_LOCK:''', '''async def post_prompt(request: Request):
    body = await request.body()
    try:
        rem = await _try_remote(body)
    except Exception as e:
        print(f"[comfy-proxy] reparto a PC prestado falló, se hace en la 3080: {e!r}", flush=True)
        rem = None
    if rem is not None:
        return rem
    return await _post_local(body, request.headers.get("content-type", "application/json"))


async def _post_local(body: bytes, ctype: str = "application/json"):
    async with LEASE_LOCK:''')
rep('''                r = await c.post(UPSTREAM + "/prompt", content=body,
                                 headers={"Content-Type": request.headers.get("content-type", "application/json")})''',
    '''                r = await c.post(UPSTREAM + "/prompt", content=body, headers={"Content-Type": ctype})''')

# 2) rutas de un trabajo remoto, antes del proxy transparente
rep('''@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"])
async def proxy(path: str, request: Request):
    full_path = "/" + path
    body = await request.body()''', '''@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"])
async def proxy(path: str, request: Request):
    full_path = "/" + path
    body = await request.body()
    if REMOTE:
        try:
            r = await _remote_route(full_path, request, body)
        except Exception as e:
            print(f"[comfy-proxy] ruta remota {full_path}: {e!r}", flush=True)
            r = None
        if r is not None:
            return r''')

# 3) el reparto en sí
rep('''# ---- Almacén para GPU Prestada''', '''# ---- Imágenes en PCs de GPU Prestada (31-oct-2026) ----
import re
import time
NODES_URL = os.getenv("NODES_URL", "http://100.72.138.100:11434/_nodes")
SVC_URL = NODES_URL + "/svc/comfy"
REMOTE_CONF = os.getenv("REMOTE_CONF", "/opt/comfyui-proxy/remoto.json")
REMOTE: dict = {}     # prompt_id -> {"node", "t", "body", "local", "state"}
_OBJ: dict = {}       # pc -> (cuándo, object_info)
_SAVE_NODES = ("SaveImage",)


def _remote_mode() -> str:
    try:
        with open(REMOTE_CONF) as f:
            return str(json.load(f).get("mode") or "off")
    except Exception:
        return "off"


async def _ready_nodes() -> list:
    async with httpx.AsyncClient(timeout=8) as c:
        d = (await c.get(NODES_URL)).json()
    out = [n for n in d.get("nodes") or [] if n.get("alive") and n.get("usable") and not n.get("paused")
           and ((n.get("services") or {}).get("comfy") or {}).get("state") == "ready"]
    return sorted(out, key=lambda n: -(n.get("vram_free_mb") or 0))


async def _object_info(node: str) -> dict:
    t, info = _OBJ.get(node, (0, None))
    if info is None or time.time() - t > 600:
        async with httpx.AsyncClient(timeout=90) as c:
            r = await c.get(SVC_URL + "/object_info", params={"_node": node})
        r.raise_for_status()
        info = r.json()
        _OBJ[node] = (time.time(), info)
    return info


def _compatible(prompt: dict, info: dict):
    for node in prompt.values():
        ct = node.get("class_type")
        if ct not in info:
            return False, f"falta el nodo {ct}"
        spec = {}
        for part in ("required", "optional"):
            spec.update((info[ct].get("input") or {}).get(part) or {})
        for k, v in (node.get("inputs") or {}).items():
            opt = spec.get(k)
            if not isinstance(v, str) or not opt or not isinstance(opt[0], list):
                continue
            if ct == "LoadImage" and k == "image":
                continue
            if v not in opt[0]:
                return False, f"falta {v} ({ct}.{k})"
    return True, ""


async def _local_busy() -> bool:
    if ACTIVE_PIDS:
        return True
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            st = (await c.get(BROKER_URL + "/state")).json()
        return any(v.get("busy") for k, v in (st.get("services") or {}).items() if k != "comfyui")
    except Exception:
        return False


async def _try_remote(body: bytes):
    for k in [k for k, v in REMOTE.items() if time.time() - v["t"] > 86400]:
        REMOTE.pop(k, None)   # el historial de un día es suficiente
    mode = _remote_mode()
    if mode not in ("spill", "prefer"):
        return None
    payload = json.loads(body)
    prompt = payload.get("prompt")
    if not isinstance(prompt, dict):
        return None
    if mode == "spill" and not await _local_busy():
        return None
    for n in await _ready_nodes():
        node = n["name"]
        try:
            ok, why = _compatible(prompt, await _object_info(node))
        except Exception as e:
            print(f"[comfy-proxy] {node}: sin object_info ({e!r})", flush=True)
            continue
        if not ok:
            print(f"[comfy-proxy] {node} no puede con este flujo: {why}", flush=True)
            continue
        pl = json.loads(body)
        async with httpx.AsyncClient(timeout=120) as c:
            for nd in pl["prompt"].values():
                if nd.get("class_type") == "LoadImage":
                    name = str((nd.get("inputs") or {}).get("image") or "")
                    sub, _, base = name.rpartition("/")
                    img = await c.get(UPSTREAM + "/view", params={"filename": base, "subfolder": sub, "type": "input"})
                    img.raise_for_status()
                    up = await c.post(SVC_URL + "/upload/image", params={"_node": node},
                                      files={"image": (base, img.content, "application/octet-stream")},
                                      data={"subfolder": sub, "overwrite": "true"})
                    up.raise_for_status()
                if nd.get("class_type") in _SAVE_NODES and "filename_prefix" in (nd.get("inputs") or {}):
                    nd["inputs"]["filename_prefix"] = f"rmt_{node}/" + str(nd["inputs"]["filename_prefix"])
            r = await c.post(SVC_URL + "/prompt", params={"_node": node}, json=pl)
        try:
            pid = r.json().get("prompt_id")
        except Exception:
            pid = None
        if r.status_code != 200 or not pid:
            print(f"[comfy-proxy] {node} rechazó el flujo: HTTP {r.status_code} {r.text[:200]}", flush=True)
            continue
        REMOTE[pid] = {"node": node, "t": time.time(), "body": body, "local": None, "state": "running"}
        asyncio.create_task(_watch_remote(pid))
        print(f"[comfy-proxy] 3080 {'ocupada' if mode == 'spill' else 'reservada'}: imagen {pid[:8]} a {node}", flush=True)
        return Response(content=r.content, status_code=200, media_type="application/json")
    return None


async def _watch_remote(pid: str):
    e = REMOTE[pid]
    fails, end = 0, time.time() + 6 * 3600
    async with httpx.AsyncClient(timeout=20) as c:
        while time.time() < end:
            await asyncio.sleep(3)
            try:
                h = (await c.get(f"{SVC_URL}/history/{pid}", params={"_node": e["node"]})).json()
                fails = 0
                if pid in h:
                    st = ((h[pid].get("status") or {}).get("status_str") or "success")
                    if st == "success":
                        e["state"] = "done"
                        return
                    print(f"[comfy-proxy] {e['node']} falló con {pid[:8]}: se repite en la 3080", flush=True)
                    break
            except Exception:
                fails += 1
                if fails * 3 >= 180:
                    print(f"[comfy-proxy] {e['node']} no responde con {pid[:8]}: se repite en la 3080", flush=True)
                    break
    # repetir en la 3080, sin las rutas rmt_ (la salida vuelve a ser local)
    r = await _post_local(e["body"])
    try:
        e["local"] = json.loads(r.body).get("prompt_id")
    except Exception:
        e["local"] = None
    e["state"] = "local"


async def _remote_route(full_path: str, request: Request, body: bytes):
    """Trabajos que se hicieron fuera: su historial, sus imágenes, la cola y cancelar."""
    m = re.match(r"^/(?:api/)?history/([0-9a-fA-F-]{8,})$", full_path)
    if request.method == "GET" and m and m.group(1) in REMOTE:
        pid = m.group(1)
        e = REMOTE[pid]
        async with httpx.AsyncClient(timeout=30) as c:
            if e["state"] == "local":
                if not e["local"]:
                    return Response(content=b"{}", media_type="application/json")
                h = (await c.get(f"{UPSTREAM}/history/{e['local']}")).json()
                return Response(content=json.dumps({pid: h[e["local"]]} if e["local"] in h else {}), media_type="application/json")
            r = await c.get(f"{SVC_URL}/history/{pid}", params={"_node": e["node"]})
            if r.status_code != 200 or e["state"] != "done":
                return Response(content=b"{}", media_type="application/json")   # aún trabajando (o repitiéndose)
            return Response(content=r.content, media_type="application/json")
    if request.method == "GET" and full_path in ("/view", "/api/view"):
        sub = str(request.query_params.get("subfolder") or "")
        if sub.startswith("rmt_"):
            node, _, rest = sub[4:].partition("/")
            params = dict(request.query_params)
            params["subfolder"] = "rmt_" + node + ("/" + rest if rest else "")
            params["_node"] = node
            async with httpx.AsyncClient(timeout=120) as c:
                r = await c.get(SVC_URL + "/view", params=params)
            return Response(content=r.content, status_code=r.status_code, media_type=r.headers.get("content-type"))
    if full_path in ("/queue", "/api/queue") and request.method == "GET":
        async with httpx.AsyncClient(timeout=10) as c:
            q = (await c.get(UPSTREAM + "/queue")).json()
            for node in {e["node"] for e in REMOTE.values() if e["state"] == "running"}:
                try:
                    rq = (await c.get(SVC_URL + "/queue", params={"_node": node})).json()
                    q["queue_running"] = (q.get("queue_running") or []) + (rq.get("queue_running") or [])
                    q["queue_pending"] = (q.get("queue_pending") or []) + (rq.get("queue_pending") or [])
                except Exception:
                    pass
        return Response(content=json.dumps(q), media_type="application/json")
    if request.method == "POST" and full_path in ("/interrupt", "/api/interrupt", "/queue", "/api/queue") and body:
        try:
            d = json.loads(body)
        except Exception:
            return None
        ids = [d.get("prompt_id")] if d.get("prompt_id") else list(d.get("delete") or [])
        rem = [i for i in ids if i in REMOTE and REMOTE[i]["state"] == "running"]
        if rem:
            async with httpx.AsyncClient(timeout=15) as c:
                for i in rem:
                    await c.post(SVC_URL + full_path.replace("/api", ""), params={"_node": REMOTE[i]["node"]}, content=body,
                                 headers={"Content-Type": "application/json"})
            return Response(content=b"{}", media_type="application/json")
    return None


# ---- Almacén para GPU Prestada''')
open(p, "w", encoding="utf-8").write(s)
print("parcheado")
