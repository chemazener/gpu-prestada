#!/usr/bin/env python3
"""comfyui-proxy (CT 106): que no quede nada en el PC prestado (1-nov-2026, agente 1.14.0).
- Cuando Bicho Magic recoge (/view) una imagen o vídeo hecho fuera, el PC lo borra a los 2 minutos.
- Al acabar un trabajo remoto (bien, mal o repetido en la 3080), el PC borra las fotos de entrada que se le subieron.
(Lo que nadie recoja lo borra el propio agente pasada una hora.)
Uso: python3 patch_comfy_limpieza.py /opt/comfyui-proxy/proxy.py   (sobre el que ya tiene patch_comfy_remoto)"""
import sys

p = sys.argv[1]
s = open(p, encoding="utf-8").read()
if "_clean_remote" in s:
    sys.exit("ya parcheado")


def rep(a, b):
    global s
    assert s.count(a) == 1, a[:90]
    s = s.replace(a, b)


rep('''async def _watch_remote(pid: str):''', '''_CLEANING: set = set()


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


async def _watch_remote(pid: str):''')

# registrar las entradas subidas y limpiarlas al acabar
rep('''        pl = json.loads(body)
        async with httpx.AsyncClient(timeout=120) as c:''', '''        pl = json.loads(body)
        uploaded = []
        async with httpx.AsyncClient(timeout=120) as c:''')
rep('''                    up.raise_for_status()''', '''                    up.raise_for_status()
                    uploaded.append({"filename": base, "subfolder": sub, "type": "input"})''')
rep('''        REMOTE[pid] = {"node": node, "t": time.time(), "body": body, "local": None, "state": "running"}''',
    '''        REMOTE[pid] = {"node": node, "t": time.time(), "body": body, "local": None, "state": "running", "inputs": uploaded}''')
rep('''                    if st == "success":
                        e["state"] = "done"
                        return''', '''                    if st == "success":
                        e["state"] = "done"
                        asyncio.create_task(_clean_remote(e["node"], e.get("inputs") or []))
                        return''')
rep('''    # repetir en la 3080, sin las rutas rmt_ (la salida vuelve a ser local)''', '''    asyncio.create_task(_clean_remote(e["node"], e.get("inputs") or []))
    # repetir en la 3080, sin las rutas rmt_ (la salida vuelve a ser local)''')

# al recoger una salida remota, borrarla del PC
rep('''            async with httpx.AsyncClient(timeout=120) as c:
                r = await c.get(SVC_URL + "/view", params=params)
            return Response(content=r.content, status_code=r.status_code, media_type=r.headers.get("content-type"))''',
    '''            async with httpx.AsyncClient(timeout=300) as c:
                r = await c.get(SVC_URL + "/view", params=params)
            key = (node, params["subfolder"], params.get("filename"))
            if r.status_code == 200 and key not in _CLEANING:
                _CLEANING.add(key)
                asyncio.create_task(_clean_remote(node, [{"filename": params.get("filename"), "subfolder": params["subfolder"],
                                                          "type": params.get("type") or "output"}]))
            return Response(content=r.content, status_code=r.status_code, media_type=r.headers.get("content-type"))''')
open(p, "w", encoding="utf-8").write(s)
print("parcheado")
