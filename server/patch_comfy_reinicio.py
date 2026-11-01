#!/usr/bin/env python3
"""comfyui-proxy (CT 106): se puede reiniciar con trabajo en marcha sin perder nada (1-nov-2026).
- Al arrancar, si ComfyUI tiene cola y el broker tiene un turno de «comfyui», lo ADOPTA (lo renueva y lo suelta al
  acabar la cola) en vez de pedir otro: antes, un reinicio dejaba el turno huérfano y la cola parada hasta 10 min.
- Los trabajos mandados a PCs prestados (REMOTE) se guardan en /var/lib/comfyui-proxy/remote.json cada 10 s y se
  recuperan al arrancar (se sigue vigilándolos).
Uso: python3 patch_comfy_reinicio.py /opt/comfyui-proxy/proxy.py   (sobre el de patch_comfy_cola)"""
import sys

p = sys.argv[1]
s = open(p, encoding="utf-8").read()
if "_adopt_on_start" in s:
    sys.exit("ya parcheado")


def rep(a, b):
    global s
    assert s.count(a) == 1, a[:90]
    s = s.replace(a, b)


rep('''@app.on_event("startup")
async def _start_steal_loop():
    asyncio.create_task(_steal_loop())''', '''REMOTE_STATE = os.getenv("REMOTE_STATE", "/var/lib/comfyui-proxy/remote.json")


def _save_remote():
    try:
        os.makedirs(os.path.dirname(REMOTE_STATE), exist_ok=True)
        data = {k: dict(v, body=v["body"].decode() if isinstance(v.get("body"), bytes) else v.get("body"))
                for k, v in REMOTE.items()}
        tmp = REMOTE_STATE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, REMOTE_STATE)
    except Exception as e:
        print(f"[comfy-proxy] no pude guardar los trabajos remotos: {e!r}", flush=True)


async def _saver_loop():
    while True:
        await asyncio.sleep(10)
        _save_remote()


@app.on_event("startup")
async def _adopt_on_start():
    """Recupera los trabajos remotos y, si ComfyUI tiene cola, adopta el turno que ya tiene en el broker."""
    try:
        with open(REMOTE_STATE) as f:
            for k, v in json.load(f).items():
                if time.time() - v.get("t", 0) < 86400:
                    REMOTE[k] = v
        for k, v in REMOTE.items():
            if v.get("state") == "running":
                asyncio.create_task(_watch_remote(k))
        if REMOTE:
            print(f"[comfy-proxy] recuperados {len(REMOTE)} trabajos de PCs prestados", flush=True)
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[comfy-proxy] no pude recuperar los trabajos remotos: {e!r}", flush=True)
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            q = (await c.get(UPSTREAM + "/queue")).json()
            ids = [it[1] for it in (q.get("queue_running") or []) + (q.get("queue_pending") or [])]
            if ids:
                st = (await c.get(BROKER_URL + "/state")).json()
                toks = ((st.get("services") or {}).get("comfyui") or {}).get("active_tokens") or []
                if toks:
                    async with LEASE_LOCK:
                        LEASE["token"] = toks[0]
                        ACTIVE_PIDS.update(ids)
                    for pid in ids:
                        asyncio.create_task(_watch_pid(pid))
                    asyncio.create_task(_keep_lease(toks[0]))
                    print(f"[comfy-proxy] turno del broker adoptado al arrancar ({len(ids)} trabajos en la cola)", flush=True)
    except Exception as e:
        print(f"[comfy-proxy] no pude adoptar el turno al arrancar: {e!r}", flush=True)
    asyncio.create_task(_steal_loop())
    asyncio.create_task(_saver_loop())''')
open(p, "w", encoding="utf-8").write(s)
print("parcheado")
