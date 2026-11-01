#!/usr/bin/env python3
"""nodes.py: el latido de un equipo con permiso de cliente lleva qué modelos tiene cada OTRA tarjeta
prestada usable («cards»), para que su agente los liste en su Ollama como «modelo@tarjeta» (1.9.0).
Uso: python3 patch_nodes_clientes2.py /opt/ollama-proxy/nodes.py"""
import sys

p = sys.argv[1]
s = open(p, encoding="utf-8").read()
if "def client_cards" in s:
    sys.exit("ya parcheado")


def rep(a, b):
    global s
    assert s.count(a) == 1, a[:80]
    s = s.replace(a, b)


rep('''def _client_info(name: str) -> dict:''', '''def client_cards(name: str) -> dict:
    """Modelos de cada otra tarjeta prestada que ahora puede atender (sin la del propio equipo)."""
    if not client_conf(name)["enabled"]:
        return {}
    return {n["name"]: list(n.get("models") or []) for n in NODES.values() if n.get("name") != name and _usable(n)}


def _client_info(name: str) -> dict:''')
rep('''"client": client_conf(name),''', '''"client": dict(client_conf(name), cards=client_cards(name)),''')
open(p, "w", encoding="utf-8").write(s)
print("parcheado")
