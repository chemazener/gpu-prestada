#!/usr/bin/env python3
"""Proxy de Ollama (CT 101): equipos de GPU Prestada como CLIENTES (agente 1.8.0, 31-oct-2026).

El agente abre en el PC un puerto local (127.0.0.1:11444) y reenvía aquí con
X-Client-Node + X-Node-Token. Si el equipo tiene permiso (Monitor VRAM → nodes.json
«clients»), la petición sigue el camino normal (3080, «modelo@nodo», desbordamiento)
pero nunca vuelve al propio equipo, y cuenta para su máximo de peticiones a la vez.

Uso: python3 patch_proxy3.py /opt/ollama-proxy/proxy.py
"""
import sys

p = sys.argv[1]
s = open(p, encoding="utf-8").read()
if "_proxy_inner" in s:
    sys.exit("ya parcheado")


def rep(a, b):
    global s
    assert s.count(a) == 1, a[:80]
    s = s.replace(a, b)


rep('''@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"])
async def proxy(path: str, request: Request):''', '''async def _proxy_inner(path: str, request: Request):''')

rep('''if __name__ == "__main__":''', '''class _ClientDoneResponse(Response):
    """Envuelve la respuesta de un equipo cliente y descuenta su petición al terminar de enviarla."""

    def __init__(self, inner: Response, name: str):
        super().__init__(status_code=inner.status_code)
        self.inner, self.name = inner, name
        self.raw_headers = inner.raw_headers

    async def __call__(self, scope, receive, send):
        try:
            await self.inner(scope, receive, send)
        finally:
            nodes.client_leave(self.name)


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"])
async def proxy(path: str, request: Request):
    cn = request.headers.get("x-client-node")
    if not cn:
        return await _proxy_inner(path, request)
    name, err = nodes.client_check(request, cn)
    if err is not None:
        return err
    # las cabeceras internas no siguen hacia Ollama ni hacia otras tarjetas
    request.scope["headers"] = [(k, v) for k, v in request.scope["headers"]
                                if k.lower() not in (b"x-client-node", b"x-node-token")]
    if hasattr(request, "_headers"):
        del request._headers  # que Starlette vuelva a leerlas del scope
    nodes.client_enter(name)
    tok = nodes.EXCLUDE.set(name)
    try:
        resp = await _proxy_inner(path, request)
    except BaseException:
        nodes.client_leave(name)
        raise
    finally:
        nodes.EXCLUDE.reset(tok)
    return _ClientDoneResponse(resp, name)


if __name__ == "__main__":''')

open(p, "w", encoding="utf-8").write(s)
print("parcheado")
