#!/usr/bin/env python3
"""nodes.py: RAM y disco de cada PC prestado (agente 1.12.1), para su ficha en el Monitor VRAM.
Uso: python3 patch_nodes_sistema.py /opt/ollama-proxy/nodes.py"""
import sys

p = sys.argv[1]
s = open(p, encoding="utf-8").read()
if '"ram_total_mb"' in s:
    sys.exit("ya parcheado")
a = '''        "jobs": _list(b, "jobs", 20,'''
assert s.count(a) == 1
s = s.replace(a, '''        # -1 = el agente no informa (anterior a la 1.12.1)
        "ram_total_mb": _int(b.get("ram_total_mb"), -1), "ram_free_mb": _int(b.get("ram_free_mb"), -1),
        "disk_drive": str(b.get("disk_drive") or "")[:8],
        "disk_total_mb": _int(b.get("disk_total_mb"), -1), "disk_free_mb": _int(b.get("disk_free_mb"), -1),
''' + a)
open(p, "w", encoding="utf-8").write(s)
print("parcheado")
