#!/usr/bin/env python3
"""Monitor VRAM: «Equipos esperando alta» (agente 1.10.0, 31-oct-2026). Aviso visible en todas las
pestañas con los PCs sin código que piden el alta; «Dar de alta» les manda el código solo.
Uso: python3 patch_dashboard5.py /opt/broker-dashboard/broker_dashboard.py"""
import sys

p = sys.argv[1]
s = open(p, encoding="utf-8").read()
if "/api/pending/" in s:
    sys.exit("ya parcheado")


def rep(a, b):
    global s
    assert s.count(a) == 1, a[:80]
    s = s.replace(a, b)


rep('''@app.post("/api/node/{name}/client")''', '''@app.post("/api/pending/{rid}/{action}")
def api_pending(rid: str, action: str):
    """Dar de alta / rechazar un PC que espera el código."""
    if action not in ("approve", "reject"):
        raise HTTPException(404, "acción desconocida")
    req = urllib.request.Request(NODES_URL + "/pending/" + urllib.parse.quote(rid) + "/" + action, method="POST", data=b"")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception as e:
        raise HTTPException(502, f"el proxy no aceptó la orden: {e}")


@app.post("/api/node/{name}/client")''')

rep('''<nav class="tabs" id="tabs" aria-label="Vistas"></nav>''', '''<nav class="tabs" id="tabs" aria-label="Vistas"></nav>
<div id="pendBox" style="display:none"></div>''')

rep(''' .sec-note{''', ''' #pendBox{margin:0 auto 14px;max-width:1060px;border:1px solid var(--warn);border-radius:12px;background:rgba(210,153,34,.08);padding:12px 16px}
 #pendBox h3{margin:0 0 8px;font-size:14px}#pendBox .pr{display:flex;flex-wrap:wrap;gap:8px 14px;align-items:center;padding:8px 0;border-top:1px solid var(--bd)}
 #pendBox .pr:first-of-type{border-top:0}#pendBox .pr b{min-width:150px}#pendBox .pr .mut{flex:1;min-width:180px;font-size:12px}
 #pendBox button{padding:7px 12px;font-weight:600}
 .sec-note{''')

rep('''function renderNode(d){''', '''// Equipos esperando alta (agente 1.10.0)
function renderPending(d){
  const pend=(d.remote&&d.remote.pending)||[],box=$("#pendBox");
  const sig=JSON.stringify(pend.map(p=>[p.request_id,p.name,p.gpu,Math.floor(p.waiting_s/60),p.name_taken]));
  if(box.dataset.sig===sig)return;box.dataset.sig=sig;
  box.style.display=pend.length?'block':'none';
  box.innerHTML='<h3>🆕 Equipos esperando alta ('+pend.length+')</h3>'+pend.map(p=>'<div class="pr"><b>'+esc(p.name||'sin nombre')+'</b>'+
    '<span class="mut">'+esc((p.gpu||'gráfica sin detectar').replace('NVIDIA GeForce ','').replace('(TM)',''))+(p.vram_total_mb?' · '+p.vram_total_mb+' MiB':'')+
    ' · GPU Prestada '+esc(p.version||'?')+' · esperando desde hace '+ago(p.waiting_s)+
    (p.name_taken?' · <b style="color:var(--warn)">ya hay otra tarjeta con ese nombre</b>':'')+'</span>'+
    '<button class="ok" data-pend="'+esc(p.request_id)+'" data-act="approve">✅ Dar de alta</button>'+
    '<button class="bad" data-pend="'+esc(p.request_id)+'" data-act="reject">✖ Rechazar</button></div>').join('');}
document.addEventListener('click',async ev=>{const b=ev.target.closest('#pendBox button[data-pend]');if(!b)return;b.disabled=true;
  try{const j=await post('/api/pending/'+encodeURIComponent(b.dataset.pend)+'/'+b.dataset.act);
    toast(j.name+': '+(j.action==='approve'?'dado de alta; se conecta en unos segundos':'rechazado'));$("#pendBox").dataset.sig='';load();}
  catch(e){toast('No se pudo: '+e.message,true);b.disabled=false;}});
function renderNode(d){''')

rep('''  try{STATE=d;renderTabs(d);''', '''  try{renderPending(d);}catch(e){console.error(e);}
  try{STATE=d;renderTabs(d);''')
open(p, "w", encoding="utf-8").write(s)
print("parcheado")
