#!/usr/bin/env python3
"""nodes.py: procesador de cada PC prestado (agente 1.12.4). Uso: python3 patch_nodes_cpu.py nodes.py"""
import sys
p = sys.argv[1]
s = open(p, encoding="utf-8").read()
if '"cpu_threads"' in s:
    sys.exit("ya parcheado")
a = '''        "ram_total_mb": _int(b.get("ram_total_mb"), -1),'''
assert s.count(a) == 1
s = s.replace(a, '''        "cpu": str(b.get("cpu") or "")[:80], "cpu_threads": _int(b.get("cpu_threads"), 0),
''' + a)
open(p, "w", encoding="utf-8").write(s)
print("parcheado")
