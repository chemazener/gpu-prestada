#!/usr/bin/env python3
"""Monitor VRAM: procesador del PC en la ficha de cada tarjeta prestada (agente 1.12.4).
Uso: python3 patch_dashboard10.py broker_dashboard.py"""
import sys
p = sys.argv[1]
s = open(p, encoding="utf-8").read()
if "function cpuShort" in s:
    sys.exit("ya parcheado")


def rep(a, b):
    global s
    assert s.count(a) == 1, a[:80]
    s = s.replace(a, b)


rep('''    kv('GPU Prestada',n.version?''', '''    kv('Procesador',n.cpu?esc(cpuShort(n.cpu)):'—',n.cpu?(n.cpu_threads?n.cpu_threads+' hilos':'')+(cpuShort(n.cpu)!==n.cpu?' · '+esc(n.cpu):'')
      :(n.alive?'con GPU Prestada 1.12.4 o posterior':'equipo apagado'))+
    kv('GPU Prestada',n.version?''')
rep('''function renderNode(d){''', '''// Nombre corto del procesador: sin marcas registradas, «CPU», frecuencias ni el sufijo de núcleos.
function cpuShort(c){return String(c||'').replace(/\\((R|TM|tm|r)\\)/g,'').replace(/\\bCPU\\b|\\bProcessor\\b/gi,'')
  .replace(/@\\s*[\\d.]+\\s*GHz/i,'').replace(/\\b\\d+-Core\\b/i,'').replace(/with Radeon.*$/i,'').replace(/\\s+/g,' ').trim();}
function renderNode(d){''')
open(p, "w", encoding="utf-8").write(s)
print("parcheado")
