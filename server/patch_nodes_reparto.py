#!/usr/bin/env python3
"""nodes.py: modelos por equipo y reparto automático solo hacia ciertos equipos (agente 1.11.0, 31-oct-2026).
nodes.json:
  "node_models": {"petercobo": [{"name": "gemma4:e4b"},
                                {"name": "gemma4:12b-3080", "from": "gemma4:12b-it-qat", "parameters": {...}}]}
     → además de want_models, ese equipo descarga «name» (o «from») y crea los alias que llevan «from».
  "prefer_nodes": ["petercobo"]  → en modo "prefer", solo esos equipos reciben trabajo automático.
Uso: python3 patch_nodes_reparto.py /opt/ollama-proxy/nodes.py"""
import sys

p = sys.argv[1]
s = open(p, encoding="utf-8").read()
if "def node_models" in s:
    sys.exit("ya parcheado")


def rep(a, b):
    global s
    assert s.count(a) == 1, a[:80]
    s = s.replace(a, b)


rep('''def pick(model: str, header_node: Optional[str]) -> Tuple[Optional[dict], str]:''', '''def node_models(name: str):
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


def pick(model: str, header_node: Optional[str]) -> Tuple[Optional[dict], str]:''')
rep('''            cands = [n for n in NODES.values() if _usable(n) and _has_model(n, clean)]''',
    '''            pn = {str(x).lower() for x in (c.get("prefer_nodes") or [])}
            cands = [n for n in NODES.values() if _usable(n) and _has_model(n, clean) and (not pn or n.get("name") in pn)]''')
rep('''    return {"ok": True, "mode": c["mode"], "want_models": c.get("want_models") or [], "client": dict(client_conf(name), cards=client_cards(name)),''',
    '''    want, create = node_models(name)
    return {"ok": True, "mode": c["mode"], "want_models": want, "create_models": create,
            "client": dict(client_conf(name), cards=client_cards(name)),''')
open(p, "w", encoding="utf-8").write(s)
print("parcheado")
