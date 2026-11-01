#!/usr/bin/env python3
"""Monitor VRAM: en la ficha de cada tarjeta prestada, memoria RAM ocupada/disponible y disco ocupado/disponible
del PC (agente 1.12.1). Uso: python3 patch_dashboard8.py broker_dashboard.py"""
import sys

p = sys.argv[1]
s = open(p, encoding="utf-8").read()
if "function gbOf" in s:
    sys.exit("ya parcheado")


def rep(a, b):
    global s
    assert s.count(a) == 1, a[:80]
    s = s.replace(a, b)


rep('''    kv('Modelos',(n.models||[]).length||0,esc((n.models||[]).join(', ')||'ninguno todavía'));''',
    '''    kv('Modelos',(n.models||[]).length||0,esc((n.models||[]).join(', ')||'ninguno todavía'))+
    ndSys(n);''')
rep('''function renderNode(d){''', '''// RAM y disco del PC (agente 1.12.1)
function gbOf(mb){return (mb/1024).toLocaleString('es-ES',{minimumFractionDigits:1,maximumFractionDigits:1})+' GB';}
function ndSys(n){
  const rt=n.ram_total_mb,rf=n.ram_free_mb,dt=n.disk_total_mb,df=n.disk_free_mb,drv=n.disk_drive?' (unidad '+esc(n.disk_drive)+')':'';
  if(!n.alive||rt==null||rt<0){const s=n.alive?'con GPU Prestada 1.12.1 o posterior':'equipo apagado';
    return kv('Memoria RAM','—',s)+kv('Disco','—',s);}
  const pc=(a,b)=>Math.round(a/b*100)+' %';
  return kv('Memoria RAM ocupada',gbOf(rt-rf),pc(rt-rf,rt)+' de '+gbOf(rt))+
         kv('Memoria RAM disponible',gbOf(rf),pc(rf,rt)+' libre')+
         (dt>0?kv('Disco ocupado',gbOf(dt-df),pc(dt-df,dt)+' de '+gbOf(dt)+drv)+
               kv('Disco disponible',gbOf(df),pc(df,dt)+' libre'+drv):'');}
function renderNode(d){''')
open(p, "w", encoding="utf-8").write(s)
print("parcheado")
