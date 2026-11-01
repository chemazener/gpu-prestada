#!/usr/bin/env python3
"""Monitor VRAM: en «Servicios del broker» de cada tarjeta prestada, también Whisper y ComfyUI (agente 1.12.0):
estado (instalando con su progreso, listo, error) y qué hacen. Uso: python3 patch_dashboard7.py broker_dashboard.py"""
import sys

p = sys.argv[1]
s = open(p, encoding="utf-8").read()
if "SVC_LABEL" in s:
    sys.exit("ya parcheado")
a = '''  $("#ndSvc").innerHTML=!n.alive?msg('tarjeta apagada'):old?msg(viejo):rows.join('')||msg('ningún modelo cargado');'''
assert s.count(a) == 1
s = s.replace(a, '''  const SVC_LABEL={whisper:['Whisper','transcripción de audio'],comfy:['ComfyUI','imágenes de Bicho Magic']};
  Object.entries(n.services||{}).forEach(([k,v])=>{const L=SVC_LABEL[k]||[k,''];
    const pill=v.state==='ready'?'<span class="pill on">listo</span>':v.state==='error'?'<span class="pill bad">error</span>'
      :'<span class="pill busy">'+(v.state==='starting'?'arrancando':'instalando')+'</span>';
    rows.push(`<tr>
      <td data-label="Servicio"><b>${L[0]}</b> <span class="mut">· ${L[1]}</span></td>
      <td data-label="Estado">${pill}</td>
      <td data-label="Trabajos" class="mut" colspan=3>${esc(v.detail||'')}${v.progress>=0&&v.state!=='ready'?' <span class="mono">'+v.progress+' %</span>':''}${(v.models||[]).length?' · modelos: '+esc(v.models.join(', ')):''}</td>
      <td></td></tr>`);});
''' + a)
open(p, "w", encoding="utf-8").write(s)
print("parcheado")
