#!/usr/bin/env python3
"""Monitor VRAM: «🔑 Código de alta» en Herramientas (31-oct-2026), para pasárselo a un PC con un agente
anterior a la 1.10.0 (que no sale en «Equipos esperando alta»). Oculto hasta pulsar «Mostrar».
El panel ya está detrás del acceso con Google (vram.lomastrend.com).
Uso: python3 patch_dashboard6.py /opt/broker-dashboard/broker_dashboard.py"""
import sys

p = sys.argv[1]
s = open(p, encoding="utf-8").read()
if "/api/enroll-code" in s:
    sys.exit("ya parcheado")


def rep(a, b):
    global s
    assert s.count(a) == 1, a[:80]
    s = s.replace(a, b)


rep('''@app.post("/api/pending/{rid}/{action}")''', '''@app.get("/api/enroll-code")
def api_enroll_code():
    """Código de alta de GPU Prestada (el token de nodes.json)."""
    with open(NODES_CONF) as f:
        return {"code": json.load(f).get("token", "")}


@app.post("/api/pending/{rid}/{action}")''')
rep('''  <section id="bench" data-tab="tools">''', '''  <section id="enrollSec" data-tab="tools">
    <h2>🔑 Código de alta de GPU Prestada</h2>
    <div class="ndacts" style="padding-top:12px">
      <span class="mono" id="enrollCode" style="font-size:15px;letter-spacing:.5px;overflow-wrap:anywhere">••••••••••••••••••••••••••••••••</span>
      <button class="ok" id="enrollShow" type="button">👁 Mostrar</button>
      <button id="enrollCopy" type="button">📋 Copiar</button>
    </div>
    <div class="sec-note">Para un PC sin Tailscale: pégalo en su ventana de GPU Prestada, en «código de alta», y pulsa Guardar.
      Con la versión 1.10.0 o posterior no hace falta: el PC sale arriba en «Equipos esperando alta» y basta con «Dar de alta».
      Un PC con una versión anterior se pone al día solo al cerrar y volver a abrir GPU Prestada.</div>
  </section>
  <section id="bench" data-tab="tools">''')
rep('''// Equipos esperando alta (agente 1.10.0)''', '''// Código de alta (Herramientas)
async function enrollCode(){const j=await (await fetch('/api/enroll-code')).json();return j.code||'';}
document.addEventListener('click',async ev=>{
  if(ev.target.closest('#enrollShow')){const el=$("#enrollCode"),b=$("#enrollShow");
    if(b.dataset.on){el.textContent='••••••••••••••••••••••••••••••••';b.textContent='👁 Mostrar';delete b.dataset.on;}
    else{try{el.textContent=await enrollCode();b.textContent='🙈 Ocultar';b.dataset.on=1;}catch(e){toast('No se pudo leer el código',true);}}}
  if(ev.target.closest('#enrollCopy')){try{const c=await enrollCode();
      if(navigator.clipboard&&window.isSecureContext)await navigator.clipboard.writeText(c);
      else{const t=document.createElement('textarea');t.value=c;document.body.appendChild(t);t.select();document.execCommand('copy');t.remove();}
      toast('Código de alta copiado');}catch(e){toast('No se pudo copiar',true);}}});
// Equipos esperando alta (agente 1.10.0)''')
open(p, "w", encoding="utf-8").write(s)
print("parcheado")
