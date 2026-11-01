#!/usr/bin/env python3
"""Relé de GPU Prestada: túnel para PCs sin Tailscale.

El agente del PC abre UNA conexión saliente (WebSocket sobre TLS) hacia aquí y
por ella le llegan las peticiones de Ollama que el proxy de la CT 101 quiera
enviarle. Así el PC no necesita Tailscale ni abrir puertos: solo salir a Internet.

Dos escuchas en el mismo proceso (comparten las sesiones):
  * Pública  (TLS, 127.0.0.1:8445, detrás del proxy-casa que reparte por SNI):
      GET /ws         WebSocket del agente. Primer mensaje: latido con el token.
      GET /agent.exe  descarga del agente publicado (para la autoactualización).
      GET /agent.json huella del agente publicado (el agente la consulta al arrancar).
      GET /           texto de cortesía.
  * Interna (100.100.84.72:11437 por Tailscale, también LAN y loopback; solo para la CT 101):
      *   /n/{nodo}/{ruta}  el proxy de la CT 101 habla aquí como si fuera el nodo;
                            la petición viaja por el túnel y la respuesta vuelve en streaming.

El certificado es propio (autofirmado, 10 años); el agente lleva compilada la
huella SHA-256 de su clave pública y no acepta otra. Un cliente cualquiera que
llegue aquí no puede hacer nada sin el token de alta, que valida la CT 101.

Protocolo sobre el WebSocket (texto = JSON, binario = trozos de respuesta):
  agente -> relé   {"t":"hb", "token":…, …latido…}       cada 8 s (el primero autentica)
  relé -> agente   {"t":"hb_ok", "code":200, "body":{…}} respuesta de la CT 101 al latido
  relé -> agente   {"t":"req", "id":N, "method", "path", "headers", "body_b64"}
  agente -> relé   {"t":"res", "id":N, "status", "headers"}
  agente -> relé   <4 bytes id big-endian><datos>          trozos del cuerpo
  agente -> relé   {"t":"res_end", "id":N} | {"t":"res_err", "id":N, "error":…}
  relé -> agente   {"t":"cancel", "id":N}                  el cliente colgó
"""
import asyncio
import base64
import ipaddress
import json
import logging
import os
import pathlib
import ssl
import struct
import time

import aiohttp
from aiohttp import WSMsgType, web

HERE = pathlib.Path(__file__).resolve().parent
BROKER = os.environ.get("GPU_RELAY_BROKER", "http://100.72.138.100:11434")  # proxy de Ollama (CT 101) por Tailscale
# Cómo nos ve la CT 101: por Tailscale (su cortafuegos PVE no deja pasar a la LAN de la CT 100).
RELAY_LAN_IP = os.environ.get("GPU_RELAY_LAN", "100.100.84.72")
# Direcciones en las que escucha el lado interno (Tailscale, LAN y loopback).
LAN_BIND = os.environ.get("GPU_RELAY_LAN_BIND", "100.100.84.72,192.168.100.11,127.0.0.1").split(",")
LAN_PORT = int(os.environ.get("GPU_RELAY_LAN_PORT", "11437"))
PUB_PORT = int(os.environ.get("GPU_RELAY_PUB_PORT", "8445"))
CERT = os.environ.get("GPU_RELAY_CERT", str(HERE / "tls" / "cert.pem"))
KEY = os.environ.get("GPU_RELAY_KEY", str(HERE / "tls" / "key.pem"))
# Quién puede usar la escucha LAN: la CT 101 (LAN y Tailscale) y este equipo.
LAN_ALLOWED = [ipaddress.ip_network(x) for x in
               os.environ.get("GPU_RELAY_LAN_ALLOW", "192.168.100.12/32,100.72.138.100/32,127.0.0.1/32").split(",")]
REQ_TIMEOUT = 600          # s esperando la cabecera o el siguiente trozo de respuesta
HELLO_TIMEOUT = 20         # s para que el agente mande su primer latido
MAX_MSG = 64 << 20         # tamaño máximo de un mensaje del WebSocket

log = logging.getLogger("gpu-relay")
SESSIONS: dict = {}        # nombre del nodo -> Session
http: aiohttp.ClientSession


class Session:
    def __init__(self, name: str, ws: web.WebSocketResponse, token: str):
        self.name, self.ws, self.token = name, ws, token
        self.since = time.time()
        self.last_hb = time.time()
        self.next_id = 0
        self.pending: dict = {}   # id -> asyncio.Queue de ("head"|"data"|"end"|"err", …)
        self.served = 0

    def new_request(self):
        self.next_id += 1
        q: asyncio.Queue = asyncio.Queue()
        self.pending[self.next_id] = q
        return self.next_id, q

    async def send(self, obj: dict):
        await self.ws.send_str(json.dumps(obj, ensure_ascii=False))

    def fail_all(self, why: str):
        for q in self.pending.values():
            q.put_nowait(("err", why))


def _strip(headers, drop):
    return {k: v for k, v in headers.items() if k.lower() not in drop}


# ----------------------------------------------------------------- lado público

async def forward_heartbeat(s: Session, msg: dict):
    """Reenvía un latido del agente a la CT 101 y le devuelve la respuesta."""
    body = {k: v for k, v in msg.items() if k not in ("t", "token")}
    body["relay_url"] = f"http://{RELAY_LAN_IP}:{LAN_PORT}/n/{s.name}"
    body["via"] = "túnel"
    code, data = 0, {}
    try:
        async with http.post(BROKER + "/_nodes/heartbeat", json=body,
                             headers={"X-Node-Token": msg.get("token", "")},
                             timeout=aiohttp.ClientTimeout(total=10)) as r:
            code = r.status
            if r.status == 200:
                data = await r.json()
    except Exception as e:  # la CT 101 no responde: el agente lo verá como "sin servidor"
        log.warning("latido de %s: la CT 101 no responde (%r)", s.name, e)
    s.last_hb = time.time()
    try:
        await s.send({"t": "hb_ok", "code": code, "body": data})
    except Exception:
        pass
    return code


async def ws_handler(request: web.Request):
    ws = web.WebSocketResponse(heartbeat=20, max_msg_size=MAX_MSG)
    await ws.prepare(request)
    # El primer mensaje tiene que ser un latido con el token: es lo que autentica.
    try:
        first = await asyncio.wait_for(ws.receive(), HELLO_TIMEOUT)
    except asyncio.TimeoutError:
        await ws.close(code=4000, message=b"sin latido")
        return ws
    if first.type != WSMsgType.TEXT:
        await ws.close(code=4000, message=b"se esperaba un latido")
        return ws
    try:
        msg = json.loads(first.data)
        name = str(msg.get("name") or "").strip().lower()[:40]
        token = str(msg.get("token") or "")
        assert msg.get("t") == "hb" and name and token
    except Exception:
        await ws.close(code=4000, message=b"latido ilegible")
        return ws
    s = Session(name, ws, token)
    code = await forward_heartbeat(s, msg)
    if code == 401:
        log.warning("nodo %s rechazado: token incorrecto", name)
        await ws.close(code=4001, message=b"token incorrecto")
        return ws
    if code != 200:
        await ws.close(code=4002, message=b"servidor no disponible")
        return ws
    old = SESSIONS.get(name)
    if old is not None and old.ws is not ws:
        log.info("nodo %s: sustituye a una conexión anterior", name)
        old.fail_all("túnel sustituido")
        asyncio.create_task(old.ws.close(code=4003, message=b"sustituido por otra conexion"))
    SESSIONS[name] = s
    log.info("nodo %s conectado por túnel", name)
    try:
        async for m in ws:
            if m.type == WSMsgType.TEXT:
                try:
                    msg = json.loads(m.data)
                except ValueError:
                    continue
                t = msg.get("t")
                if t == "hb":
                    if str(msg.get("token") or "") != s.token:
                        break
                    asyncio.create_task(forward_heartbeat(s, msg))
                elif t == "res":
                    q = s.pending.get(msg.get("id"))
                    if q is not None:
                        q.put_nowait(("head", int(msg.get("status") or 502), msg.get("headers") or {}))
                elif t == "res_end":
                    q = s.pending.get(msg.get("id"))
                    if q is not None:
                        q.put_nowait(("end",))
                elif t == "res_err":
                    q = s.pending.get(msg.get("id"))
                    if q is not None:
                        q.put_nowait(("err", str(msg.get("error") or "error en el nodo")))
            elif m.type == WSMsgType.BINARY:
                if len(m.data) >= 4:
                    (rid,) = struct.unpack(">I", m.data[:4])
                    q = s.pending.get(rid)
                    if q is not None:
                        q.put_nowait(("data", m.data[4:]))
            elif m.type in (WSMsgType.ERROR, WSMsgType.CLOSE, WSMsgType.CLOSING, WSMsgType.CLOSED):
                break
    finally:
        if SESSIONS.get(name) is s:
            del SESSIONS[name]
            # Que la CT 101 lo dé por caído ya, sin esperar los 30 s del TTL.
            try:
                await http.post(BROKER + "/_nodes/heartbeat",
                                json={"name": name, "port": 11435, "lending": False,
                                      "reason": "Túnel desconectado", "relay_url": f"http://{RELAY_LAN_IP}:{LAN_PORT}/n/{name}",
                                      "via": "túnel"},
                                headers={"X-Node-Token": s.token}, timeout=aiohttp.ClientTimeout(total=5))
            except Exception:
                pass
        s.fail_all("túnel cerrado")
        log.info("nodo %s desconectado (%d trabajos por este túnel)", name, s.served)
    return ws


async def agent_exe(request: web.Request):
    """Descarga del agente publicado en la CT 101 (la ruta de allí solo admite la tailnet; nosotros estamos en ella)."""
    try:
        plat = request.query.get("platform", "windows-amd64")   # 1.15.0: un ejecutable por sistema
        name = "gpu-prestada.exe" if plat == "windows-amd64" else "gpu-prestada-" + plat
        async with http.get(BROKER + "/_nodes/agent.exe", params={"platform": plat}, timeout=aiohttp.ClientTimeout(total=600)) as r:
            if r.status != 200:
                raise web.HTTPNotFound(text="agente no publicado")
            resp = web.StreamResponse(status=200, headers={
                "Content-Type": "application/octet-stream",
                "Content-Disposition": f'attachment; filename="{name}"',
                **({"Content-Length": r.headers["Content-Length"]} if "Content-Length" in r.headers else {})})
            await resp.prepare(request)
            async for chunk in r.content.iter_chunked(256 << 10):
                await resp.write(chunk)
            await resp.write_eof()
            return resp
    except aiohttp.ClientError as e:
        raise web.HTTPBadGateway(text=f"servidor no disponible: {e}")


async def agent_json(request: web.Request):
    try:
        async with http.get(BROKER + "/_nodes/agent.json", params={"platform": request.query.get("platform", "windows-amd64")},
                            timeout=aiohttp.ClientTimeout(total=10)) as r:
            if r.status != 200:
                raise web.HTTPNotFound(text="agente no publicado")
            return web.json_response(await r.json())
    except aiohttp.ClientError as e:
        raise web.HTTPBadGateway(text=f"servidor no disponible: {e}")


async def root(request: web.Request):
    return web.Response(text="GPU Prestada · relé de LoMasTrend\n", content_type="text/plain")


# ------------------------------------------------------------------- lado LAN

def _lan_allowed(request: web.Request) -> bool:
    try:
        ip = ipaddress.ip_address(request.remote or "")
    except ValueError:
        return False
    return any(ip in n for n in LAN_ALLOWED)


async def lan_status(request: web.Request):
    if not _lan_allowed(request):
        raise web.HTTPForbidden(text="solo la CT 101")
    now = time.time()
    return web.json_response({"nodes": [
        {"name": s.name, "since_s": round(now - s.since), "last_hb_s": round(now - s.last_hb),
         "inflight": len(s.pending), "served": s.served} for s in SESSIONS.values()]})


async def lan_relay(request: web.Request):
    if not _lan_allowed(request):
        raise web.HTTPForbidden(text="solo la CT 101")
    name = request.match_info["name"].lower()
    s = SESSIONS.get(name)
    if s is None or s.ws.closed:
        raise web.HTTPBadGateway(text="el nodo no tiene túnel abierto")
    if request.headers.get("X-Node-Token") != s.token:
        raise web.HTTPUnauthorized(text="token incorrecto")
    body = await request.read()
    path = "/" + request.match_info["tail"]
    if request.query_string:
        path += "?" + request.query_string
    rid, q = s.new_request()
    headers = _strip(request.headers, ("host", "content-length", "connection", "transfer-encoding"))
    t0 = time.time()
    try:
        await s.send({"t": "req", "id": rid, "method": request.method, "path": path,
                      "headers": headers, "body_b64": base64.b64encode(body).decode()})
        item = await asyncio.wait_for(q.get(), REQ_TIMEOUT)
        if item[0] == "err":
            raise web.HTTPBadGateway(text=item[1])
        if item[0] != "head":
            raise web.HTTPBadGateway(text="respuesta desordenada del nodo")
        _, status, rheaders = item
        resp = web.StreamResponse(status=status, headers=_strip(rheaders, ("content-length", "transfer-encoding", "connection")))
        await resp.prepare(request)
        while True:
            item = await asyncio.wait_for(q.get(), REQ_TIMEOUT)
            if item[0] == "data":
                await resp.write(item[1])
            else:   # end o err: en ambos casos se termina lo que haya
                break
        await resp.write_eof()
        s.served += 1
        log.info("%s %s -> %s %d (%.1fs)", request.method, path.split("?")[0], name, status, time.time() - t0)
        return resp
    except (asyncio.CancelledError, ConnectionResetError, asyncio.TimeoutError) as e:
        # el cliente colgó o el nodo no contesta: que el agente corte lo suyo
        try:
            await s.send({"t": "cancel", "id": rid})
        except Exception:
            pass
        if isinstance(e, asyncio.TimeoutError):
            raise web.HTTPGatewayTimeout(text="el nodo no respondió a tiempo")
        raise
    finally:
        s.pending.pop(rid, None)


# ------------------------------------------- equipos como clientes (agente 1.8.0)

async def pub_client(request: web.Request):
    """Puerto local del agente → IA del servidor, para PCs sin Tailscale. Solo para agentes con
    el túnel abierto y su código de alta; el permiso y los límites los decide la CT 101."""
    name = (request.headers.get("X-Client-Node") or "").strip().lower()
    s = SESSIONS.get(name)
    if not name or s is None or s.ws.closed or request.headers.get("X-Node-Token") != s.token:
        raise web.HTTPUnauthorized(text="solo para equipos de GPU Prestada conectados")
    path = "/" + request.match_info["tail"]
    if request.query_string:
        path += "?" + request.query_string
    body = await request.read()
    headers = {k: v for k, v in request.headers.items()
               if k.lower() in ("content-type", "accept", "x-client-node", "x-node-token", "x-gpu-node", "authorization")}
    t0 = time.time()
    async with http.request(request.method, BROKER + path, data=body or None, headers=headers,
                            timeout=aiohttp.ClientTimeout(total=None, sock_connect=15, sock_read=900)) as up:
        resp = web.StreamResponse(status=up.status, headers=_strip(up.headers, ("content-length", "transfer-encoding", "connection", "content-encoding")))
        await resp.prepare(request)
        async for chunk in up.content.iter_any():
            await resp.write(chunk)
        await resp.write_eof()
    log.info("cliente %s: %s %s -> %d (%.1fs)", name, request.method, path.split("?")[0], up.status, time.time() - t0)
    return resp


# ------------------------------------------- solicitudes de alta (agente 1.10.0)

async def pub_hello(request: web.Request):
    """Un PC sin código pide el alta; la CT 101 lo enseña en el Monitor VRAM y, si se aprueba,
    devuelve el código en la siguiente petición."""
    try:
        b = await request.json()
    except Exception:
        raise web.HTTPBadRequest(text="JSON")
    body = {k: b.get(k) for k in ("request_id", "name", "gpu", "vram_total_mb", "version")}
    async with http.post(BROKER + "/_nodes/hello", json=body, timeout=aiohttp.ClientTimeout(total=15)) as r:
        data = await r.json(content_type=None)
        if data.get("token"):
            log.info("alta concedida a %s", body.get("name"))
        return web.json_response(data, status=r.status)


# ------------------------------------------- almacén de ficheros (agente 1.12.0)
# Los PCs prestados descargan aquí Whisper, ComfyUI-GGUF y los modelos de imagen: los sirve el proxy de
# ComfyUI de la CT 106 (lista cerrada en /opt/depot/manifest.json). Solo con un código de alta válido.
DEPOT = os.environ.get("GPU_RELAY_DEPOT", "http://100.66.65.57:8188/_depot/")
_TOKEN_OK: dict = {}


async def _token_valid(tok: str) -> bool:
    if not tok:
        return False
    if any(s.token == tok for s in SESSIONS.values()) or time.time() - _TOKEN_OK.get(tok, 0) < 600:
        return True
    try:
        async with http.get(BROKER + "/_nodes/check", headers={"X-Node-Token": tok},
                            timeout=aiohttp.ClientTimeout(total=10)) as r:
            if r.status == 200:
                _TOKEN_OK[tok] = time.time()
                return True
    except Exception as e:
        log.warning("no pude comprobar el código: %s", e)
    return False


async def pub_depot(request: web.Request):
    if not await _token_valid(request.headers.get("X-Node-Token", "")):
        raise web.HTTPUnauthorized(text="código de alta incorrecto")
    path = request.match_info["tail"]
    hdr = {"Range": request.headers["Range"]} if "Range" in request.headers else {}
    t0 = time.time()
    async with http.get(DEPOT + path, headers=hdr, timeout=aiohttp.ClientTimeout(total=None, sock_connect=15, sock_read=300)) as up:
        resp = web.StreamResponse(status=up.status, headers={k: v for k, v in up.headers.items()
                                                             if k.lower() in ("content-type", "content-length", "content-range", "accept-ranges")})
        await resp.prepare(request)
        async for chunk in up.content.iter_chunked(1 << 20):
            await resp.write(chunk)
        await resp.write_eof()
    log.info("almacén: %s -> %s %d (%.0fs)", path, request.headers.get("X-Client-Node", "?"), up.status, time.time() - t0)
    return resp


# ------------------------------------------------------------------- arranque

async def main():
    global http
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    http = aiohttp.ClientSession()

    pub = web.Application(client_max_size=32 << 20)   # 32 MiB: peticiones de clientes con imágenes
    pub.add_routes([web.get("/ws", ws_handler), web.get("/agent.exe", agent_exe), web.get("/agent.json", agent_json), web.get("/", root),
                    web.route("*", "/c/{tail:.*}", pub_client), web.post("/hello", pub_hello),
                    web.get("/depot/{tail:.*}", pub_depot)])
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(CERT, KEY)
    pub_runner = web.AppRunner(pub, access_log=None)
    await pub_runner.setup()
    await web.TCPSite(pub_runner, "127.0.0.1", PUB_PORT, ssl_context=ctx).start()

    lan = web.Application(client_max_size=MAX_MSG)
    lan.add_routes([web.get("/_relay", lan_status), web.route("*", "/n/{name}/{tail:.*}", lan_relay)])
    lan_runner = web.AppRunner(lan, access_log=None, handler_cancellation=True)
    await lan_runner.setup()
    for addr in LAN_BIND:
        try:
            await web.TCPSite(lan_runner, addr.strip(), LAN_PORT).start()
        except OSError as e:
            log.warning("no puedo escuchar en %s:%d (%s)", addr, LAN_PORT, e)
    log.info("relé en marcha: público TLS 127.0.0.1:%d · LAN %s:%d · CT 101 %s", PUB_PORT, RELAY_LAN_IP, LAN_PORT, BROKER)
    try:
        await asyncio.Event().wait()
    finally:
        await http.close()


if __name__ == "__main__":
    asyncio.run(main())
