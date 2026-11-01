#!/usr/bin/env python3
"""comfyui-proxy (CT 106): al llegar, un trabajo solo va a un PC prestado LIBRE (1-nov-2026). Si todos están
ocupados se queda en la cola de la 3080 y se lo lleva la primera gráfica que quede libre (la 3080 o, con el
reparto continuo, un PC). Antes se le mandaba aunque estuviera ocupado y se encolaba allí.
Uso: python3 patch_comfy_libre.py /opt/comfyui-proxy/proxy.py"""
import sys
p = sys.argv[1]
s = open(p, encoding="utf-8").read()
if "# solo a un PC libre" in s:
    sys.exit("ya parcheado")
a = '''    for n in await _ready_nodes():
        node = n["name"]
        try:
            ok, why = _compatible(prompt, await _object_info(node))'''
assert s.count(a) == 1
s = s.replace(a, '''    for n in await _ready_nodes():
        node = n["name"]
        try:
            if not await _node_idle(node):   # solo a un PC libre: si no, a la cola y la primera que quede libre
                continue
            ok, why = _compatible(prompt, await _object_info(node))''')
open(p, "w", encoding="utf-8").write(s)
print("parcheado")
