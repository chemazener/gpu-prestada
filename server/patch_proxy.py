#!/usr/bin/env python3
"""Aplica a /opt/ollama-proxy/proxy.py el enganche de nodos GPU remotos (idempotente)."""
import shutil
import sys
import time

P = sys.argv[1] if len(sys.argv) > 1 else "/opt/ollama-proxy/proxy.py"
s = open(P).read()
if "import nodes" in s:
    print("ya aplicado")
    sys.exit(0)


def rep(old, new):
    global s
    if s.count(old) != 1:
        sys.exit(f"ancla no encontrada o repetida: {old[:60]!r}")
    s = s.replace(old, new)


rep('''client = httpx.AsyncClient(base_url=UPSTREAM, timeout=httpx.Timeout(600.0, connect=10.0))
''', '''client = httpx.AsyncClient(base_url=UPSTREAM, timeout=httpx.Timeout(600.0, connect=10.0))

# Nodos GPU remotos ("GPU Prestada"): registro + rutas /_nodes/* (ver nodes.py)
import nodes
app.include_router(nodes.router)


async def _try_remote(node, request, full_path, body):
    """Atiende la peticion en un nodo GPU remoto. None => no se pudo, seguir en local."""
    headers = {k: v for k, v in request.headers.items()
               if k.lower() not in ("host", "content-length", "x-gpu-node")}
    headers.update(nodes.auth_headers())
    rc = nodes.client_for(node)
    try:
        req = rc.build_request(request.method, full_path, content=body, headers=headers,
                               params=request.query_params)
        upstream = await rc.send(req, stream=True)
    except Exception as e:
        nodes.mark_failed(node, repr(e))
        return None
    if upstream.status_code >= 500 or upstream.status_code in (401, 403, 404):
        await upstream.aclose()
        nodes.mark_failed(node, f"HTTP {upstream.status_code}")
        return None
    nodes.mark_served(node)

    async def remote_iter():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()

    out_headers = {k: v for k, v in upstream.headers.items()
                   if k.lower() not in ("content-length", "transfer-encoding")}
    out_headers["X-Served-By"] = node["name"]
    return StreamingResponse(remote_iter(), status_code=upstream.status_code,
                             headers=out_headers, media_type=upstream.headers.get("content-type"))
''')

rep('''    model = None
    if needs_lease and body:
''', '''    model = None
    node = None
    if needs_lease and body:
''')

rep('''            model = _which_model(payload)
''', '''            model = _which_model(payload)
            # Nodo GPU remoto: "modelo@nodo", cabecera X-GPU-Node o modo "prefer"
            node, clean = nodes.pick(model, request.headers.get("x-gpu-node"))
            if clean != model:
                model = clean
                payload["model"] = clean
                body = json.dumps(payload).encode()
''')

rep('''    token = None
    if needs_lease:
''', '''    if node is not None:
        remote = await _try_remote(node, request, full_path, body)
        if remote is not None:
            return remote

    token = None
    if needs_lease:
''')

shutil.copy2(P, f"{P}.bak-nodes-{time.strftime('%Y%m%d-%H%M%S')}")
open(P, "w").write(s)
print("aplicado")
