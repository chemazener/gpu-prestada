#!/usr/bin/env python3
"""nodes.py: un ejecutable del agente por sistema (1.15.0: Linux). El agente dice su «platform» en el latido
(«windows-amd64» si no lo dice, los antiguos) y recibe la huella del suyo; /_nodes/agent.json y /_nodes/agent.exe
admiten ?platform=. Ficheros: /opt/ollama-proxy/gpu-prestada.exe (Windows) y gpu-prestada-<platform> (el resto).
Uso: python3 patch_nodes_plataformas.py /opt/ollama-proxy/nodes.py"""
import sys

p = sys.argv[1]
s = open(p, encoding="utf-8").read()
if "def agent_file" in s:
    sys.exit("ya parcheado")


def rep(a, b):
    global s
    assert s.count(a) == 1, a[:90]
    s = s.replace(a, b)


rep('''def agent_sha256() -> str:
    """Huella del agente publicado. Los agentes la comparan con la suya y se actualizan solos."""
    try:
        mtime = os.path.getmtime(AGENT_EXE)
    except OSError:
        return ""
    if _exe_cache["mtime"] != mtime:
        h = hashlib.sha256()
        with open(AGENT_EXE, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        _exe_cache.update(mtime=mtime, sha=h.hexdigest())
    return _exe_cache["sha"]''', '''PLATFORMS = ("windows-amd64", "linux-amd64", "linux-arm64")
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
    return c[1]''')
rep('''        "exe_sha256": str(b.get("exe_sha256") or "")[:64],''',
    '''        "exe_sha256": str(b.get("exe_sha256") or "")[:64],
        "platform": str(b.get("platform") or "windows-amd64")[:20],''')
rep('''            "agent_sha256": agent_sha256()}''', '''            "agent_sha256": agent_sha256(NODES[name].get("platform") or "windows-amd64")}''')
rep('''        d["up_to_date"] = (n["exe_sha256"] == agent_sha256()) if n.get("exe_sha256") else None''',
    '''        d["up_to_date"] = (n["exe_sha256"] == agent_sha256(n.get("platform") or "windows-amd64")) if n.get("exe_sha256") else None''')
rep('''    if not os.path.exists(AGENT_EXE):
        raise HTTPException(404, "agente no publicado")
    st = os.stat(AGENT_EXE)
    return {"sha256": agent_sha256(), "bytes": st.st_size, "mtime": int(st.st_mtime)}''',
    '''    plat = request.query_params.get("platform") or "windows-amd64"
    f = agent_file(plat)
    if not f or not os.path.exists(f):
        raise HTTPException(404, "agente no publicado para " + plat)
    st = os.stat(f)
    return {"sha256": agent_sha256(plat), "bytes": st.st_size, "mtime": int(st.st_mtime), "platform": plat}''')
rep('''    if not os.path.exists(AGENT_EXE):
        raise HTTPException(404, "agente no publicado")
    return FileResponse(AGENT_EXE, filename="gpu-prestada.exe", media_type="application/octet-stream")''',
    '''    plat = request.query_params.get("platform") or "windows-amd64"
    f = agent_file(plat)
    if not f or not os.path.exists(f):
        raise HTTPException(404, "agente no publicado para " + plat)
    name = "gpu-prestada.exe" if plat == "windows-amd64" else "gpu-prestada-" + plat
    return FileResponse(f, filename=name, media_type="application/octet-stream")''')
open(p, "w", encoding="utf-8").write(s)
print("parcheado")
