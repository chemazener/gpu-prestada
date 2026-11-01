#!/usr/bin/env python3
"""Corrige la fuga de turnos del broker en respuestas en streaming (idempotente).

Si el cliente cortaba la conexión antes de que el generador terminase, el
`finally` del generador no llegaba a ejecutarse y el token quedaba huérfano:
Ollama figuraba ocupado y toda la cola esperaba hasta el TTL del broker.
Ahora la liberación cuelga de la propia respuesta, que siempre se cierra.
"""
import shutil, sys, time

P = sys.argv[1] if len(sys.argv) > 1 else "/opt/ollama-proxy/proxy.py"
s = open(P).read()
if "class LeasedStreamingResponse" in s:
    print("ya aplicado"); sys.exit(0)

def rep(old, new):
    global s
    if s.count(old) != 1:
        sys.exit(f"ancla no encontrada o repetida: {old[:70]!r}")
    s = s.replace(old, new)

rep('''# Nodos GPU remotos ("GPU Prestada")''', '''class LeasedStreamingResponse(StreamingResponse):
    """StreamingResponse que ejecuta on_close pase lo que pase (fin normal,
    cliente que corta, cancelacion). Evita turnos huerfanos en el broker."""

    def __init__(self, *args, on_close=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._on_close = on_close

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            if self._on_close is not None:
                await asyncio.shield(asyncio.ensure_future(self._on_close()))


# Nodos GPU remotos ("GPU Prestada")''')

# ruta remota
rep('''    return StreamingResponse(remote_iter(), status_code=upstream.status_code,
                             headers=out_headers, media_type=upstream.headers.get("content-type"))''',
    '''    return LeasedStreamingResponse(remote_iter(), status_code=upstream.status_code,
                                   headers=out_headers, media_type=upstream.headers.get("content-type"),
                                   on_close=upstream.aclose)''')

# ruta local en streaming
rep('''            return StreamingResponse(
                stream_iter(),''', '''            async def _close_stream():
                await upstream.aclose()
                if token:
                    await _broker_release(token)   # idempotente en el broker

            return LeasedStreamingResponse(
                stream_iter(),
                on_close=_close_stream,''')

shutil.copy2(P, f"{P}.bak-leak-{time.strftime('%Y%m%d-%H%M%S')}")
open(P, "w").write(s)
print("aplicado")
