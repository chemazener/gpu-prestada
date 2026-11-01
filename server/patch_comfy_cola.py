#!/usr/bin/env python3
"""comfyui-proxy (CT 106): reparto CONTINUO de la cola (1-nov-2026, chema: «hay muchos trabajos en cola y no se
reparten»). Antes solo se decidía al llegar cada trabajo; ahora, cada 10 s, si un PC prestado está libre y en la
cola de la 3080 hay trabajos PENDIENTES que ese PC puede hacer (mismos nodos y ficheros), se le pasa el más
antiguo: se quita de la cola local y se manda al PC. Bicho Magic lo sigue con su mismo identificador (REMOTE[id]
guarda el del PC en «rpid»). Si el PC lo rechaza, vuelve a la 3080.
Uso: python3 patch_comfy_cola.py /opt/comfyui-proxy/proxy.py   (sobre el de patch_comfy_limpieza)"""
import sys

p = sys.argv[1]
s = open(p, encoding="utf-8").read()
if "_steal_loop" in s:
    sys.exit("ya parcheado")
i = s.index("async def _try_remote(body: bytes):")
j = s.index("# ---- Almacén para GPU Prestada")
NEW = r'''async def _send_remote(node: str, body: bytes):
    """Manda un flujo a un PC: sube sus imágenes de entrada, guarda las salidas en rmt_<pc>. Devuelve (id, respuesta) o None."""
    pl = json.loads(body)
    uploaded = []
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
                uploaded.append({"filename": base, "subfolder": sub, "type": "input"})
            if nd.get("class_type") in _SAVE_NODES and "filename_prefix" in (nd.get("inputs") or {}):
                nd["inputs"]["filename_prefix"] = f"rmt_{node}/" + str(nd["inputs"]["filename_prefix"])
        r = await c.post(SVC_URL + "/prompt", params={"_node": node}, json=pl)
    try:
        rpid = r.json().get("prompt_id")
    except Exception:
        rpid = None
    if r.status_code != 200 or not rpid:
        print(f"[comfy-proxy] {node} rechazó el flujo: HTTP {r.status_code} {r.text[:200]}", flush=True)
        return None
    return rpid, r.content, uploaded


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
        res = await _send_remote(node, body)
        if not res:
            continue
        rpid, content, uploaded = res
        REMOTE[rpid] = {"node": node, "rpid": rpid, "t": time.time(), "body": body, "local": None, "state": "running", "inputs": uploaded}
        asyncio.create_task(_watch_remote(rpid))
        print(f"[comfy-proxy] 3080 {'ocupada' if mode == 'spill' else 'reservada'}: trabajo {rpid[:8]} a {node}", flush=True)
        return Response(content=content, status_code=200, media_type="application/json")
    return None


async def _node_idle(node: str) -> bool:
    if any(e["node"] == node and e["state"] == "running" for e in REMOTE.values()):
        return False
    async with httpx.AsyncClient(timeout=10) as c:
        rq = (await c.get(SVC_URL + "/queue", params={"_node": node})).json()
    return not (rq.get("queue_running") or rq.get("queue_pending"))


async def _steal_loop():
    """Reparto continuo: trabajos PENDIENTES de la 3080 a PCs prestados libres que puedan hacerlos."""
    while True:
        await asyncio.sleep(10)
        try:
            if _remote_mode() not in ("spill", "prefer"):
                continue
            async with httpx.AsyncClient(timeout=15) as c:
                q = (await c.get(UPSTREAM + "/queue")).json()
            pend = sorted(q.get("queue_pending") or [], key=lambda it: it[0])
            if not pend:
                continue
            for n in await _ready_nodes():
                node = n["name"]
                if not pend or not await _node_idle(node):
                    continue
                info = await _object_info(node)
                for it in list(pend):
                    lpid, prompt, extra = it[1], it[2], (it[3] if len(it) > 3 else {}) or {}
                    if lpid in REMOTE or not _compatible(prompt, info)[0]:
                        continue
                    async with httpx.AsyncClient(timeout=15) as c:
                        await c.post(UPSTREAM + "/queue", json={"delete": [lpid]})
                        q2 = (await c.get(UPSTREAM + "/queue")).json()
                    if any(x[1] == lpid for x in (q2.get("queue_running") or []) + (q2.get("queue_pending") or [])):
                        continue   # empezó en la 3080 justo ahora: se queda allí
                    body = json.dumps({"prompt": prompt, "client_id": extra.get("client_id")}).encode()
                    res = await _send_remote(node, body)
                    if not res:   # el PC no lo acepta: de vuelta a la 3080, con el mismo id para Bicho Magic
                        r = await _post_local(body)
                        try:
                            newl = json.loads(r.body).get("prompt_id")
                        except Exception:
                            newl = None
                        REMOTE[lpid] = {"node": node, "rpid": None, "t": time.time(), "body": body, "local": newl,
                                        "state": "local", "inputs": []}
                        pend.remove(it)
                        break
                    rpid, _, uploaded = res
                    REMOTE[lpid] = {"node": node, "rpid": rpid, "t": time.time(), "body": body, "local": None,
                                    "state": "running", "inputs": uploaded}
                    asyncio.create_task(_watch_remote(lpid))
                    print(f"[comfy-proxy] cola: trabajo {lpid[:8]} pasado de la 3080 a {node} (allí {rpid[:8]})", flush=True)
                    pend.remove(it)
                    break
        except Exception as e:
            print(f"[comfy-proxy] reparto de la cola: {e!r}", flush=True)


@app.on_event("startup")
async def _start_steal_loop():
    asyncio.create_task(_steal_loop())


_CLEANING: set = set()


async def _clean_remote(node: str, files: list, delay: float = 120):
    """Pide al PC prestado que borre esos ficheros (agente 1.14.0+; los anteriores responden 404 y no pasa nada)."""
    files = [f for f in files if f.get("filename")]
    if not files:
        return
    await asyncio.sleep(delay)
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(SVC_URL + "/_clean", params={"_node": node}, json={"files": files})
        print(f"[comfy-proxy] limpieza en {node}: {len(files)} pedidos → HTTP {r.status_code} {r.text[:80]}", flush=True)
    except Exception as e:
        print(f"[comfy-proxy] limpieza en {node} falló: {e!r}", flush=True)


async def _watch_remote(pid: str):
    e = REMOTE[pid]
    rpid = e["rpid"]
    fails, end = 0, time.time() + 6 * 3600
    async with httpx.AsyncClient(timeout=20) as c:
        while time.time() < end:
            await asyncio.sleep(3)
            try:
                h = (await c.get(f"{SVC_URL}/history/{rpid}", params={"_node": e["node"]})).json()
                fails = 0
                if rpid in h:
                    st = ((h[rpid].get("status") or {}).get("status_str") or "success")
                    if st == "success":
                        e["state"] = "done"
                        asyncio.create_task(_clean_remote(e["node"], e.get("inputs") or []))
                        return
                    print(f"[comfy-proxy] {e['node']} falló con {pid[:8]}: se repite en la 3080", flush=True)
                    break
            except Exception:
                fails += 1
                if fails * 3 >= 180:
                    print(f"[comfy-proxy] {e['node']} no responde con {pid[:8]}: se repite en la 3080", flush=True)
                    break
    asyncio.create_task(_clean_remote(e["node"], e.get("inputs") or []))
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
            if e["state"] != "done":
                return Response(content=b"{}", media_type="application/json")   # aún trabajando (o repitiéndose)
            r = await c.get(f"{SVC_URL}/history/{e['rpid']}", params={"_node": e["node"]})
            if r.status_code != 200:
                return Response(content=b"{}", media_type="application/json")
            h = r.json()
            return Response(content=json.dumps({pid: h[e["rpid"]]} if e["rpid"] in h else {}), media_type="application/json")
    if request.method == "GET" and full_path in ("/view", "/api/view"):
        sub = str(request.query_params.get("subfolder") or "")
        if sub.startswith("rmt_"):
            node, _, rest = sub[4:].partition("/")
            params = dict(request.query_params)
            params["subfolder"] = "rmt_" + node + ("/" + rest if rest else "")
            params["_node"] = node
            async with httpx.AsyncClient(timeout=300) as c:
                r = await c.get(SVC_URL + "/view", params=params)
            key = (node, params["subfolder"], params.get("filename"))
            if r.status_code == 200 and key not in _CLEANING:
                _CLEANING.add(key)
                asyncio.create_task(_clean_remote(node, [{"filename": params.get("filename"), "subfolder": params["subfolder"],
                                                          "type": params.get("type") or "output"}]))
            return Response(content=r.content, status_code=r.status_code, media_type=r.headers.get("content-type"))
    if full_path in ("/queue", "/api/queue") and request.method == "GET":
        back = {e["rpid"]: k for k, e in REMOTE.items() if e.get("rpid") and e["state"] == "running"}
        async with httpx.AsyncClient(timeout=10) as c:
            q = (await c.get(UPSTREAM + "/queue")).json()
            for node in {e["node"] for e in REMOTE.values() if e["state"] == "running"}:
                try:
                    rq = (await c.get(SVC_URL + "/queue", params={"_node": node})).json()
                    for part in ("queue_running", "queue_pending"):
                        items = rq.get(part) or []
                        for it in items:   # con el id que conoce Bicho Magic
                            if it[1] in back:
                                it[1] = back[it[1]]
                        q[part] = (q.get(part) or []) + items
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
                    e = REMOTE[i]
                    b2 = json.dumps({"prompt_id": e["rpid"]} if d.get("prompt_id") else {"delete": [e["rpid"]]})
                    await c.post(SVC_URL + full_path.replace("/api", ""), params={"_node": e["node"]}, content=b2,
                                 headers={"Content-Type": "application/json"})
            return Response(content=b"{}", media_type="application/json")
    return None


'''
s = s[:i] + NEW + s[j:]
open(p, "w", encoding="utf-8").write(s)
print("parcheado")
