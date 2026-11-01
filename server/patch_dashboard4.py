#!/usr/bin/env python3
"""Monitor VRAM: interruptor «Usar la IA del servidor desde este PC» en la ficha de cada tarjeta
prestada (agente 1.8.0, 31-oct-2026). Activa/desactiva el puerto local 127.0.0.1:11444 de ese
equipo y fija cuántas peticiones puede tener a la vez; lo guarda el proxy en nodes.json «clients».

Uso: python3 patch_dashboard4.py /opt/broker-dashboard/broker_dashboard.py
"""
import sys

p = sys.argv[1]
s = open(p, encoding="utf-8").read()
if "/api/node/{name}/client" in s:
    sys.exit("ya parcheado")


def rep(a, b):
    global s
    assert s.count(a) == 1, a[:80]
    s = s.replace(a, b)


# ---------- backend (antes de la ruta comodín /api/node/{name}/{action}) ----------
rep('''@app.post("/api/node/{name}/{action}")''', '''@app.post("/api/node/{name}/client")
def api_node_client(name: str, enabled: int = -1, max: int = 0):
    """Permite (o no) que ese PC use la IA del servidor desde su puerto local, y cuántas peticiones a la vez."""
    body = {}
    if enabled in (0, 1):
        body["enabled"] = bool(enabled)
    if max > 0:
        body["max_inflight"] = max
    req = urllib.request.Request(NODES_URL + "/client/" + urllib.parse.quote(name), method="POST",
                                 data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception as e:
        raise HTTPException(502, f"el proxy no aceptó el cambio: {e}")


@app.post("/api/node/{name}/{action}")''')

# ---------- HTML ----------
rep('''    <div class="ndacts" id="ndActs"></div>
  </section>''', '''    <div class="ndacts" id="ndActs"></div>
    <div class="ndacts ndcli" id="ndCli"></div>
  </section>''')
rep(''' .ndacts button.off{''', ''' .ndcli{border-top:1px solid var(--bd);padding-top:14px}
 .ndcli b{font-size:.92em}.ndcli select{padding:6px 8px}
 .ndacts button.off{''')

# ---------- JS ----------
rep('''function renderNode(d){''', '''// «Usar la IA del servidor desde este PC» (agente 1.8.0): puerto local 127.0.0.1:11444 de ese equipo.
function vnum(v){return (v||'0').split('.').map(Number).reduce((a,x)=>a*1000+x,0);}
function ndCliPaint(n){
  const c=n.client||{enabled:false,max_inflight:2,inflight:0,served:0},nueva=vnum(n.version)>=vnum('1.9.0');
  const sig=n.name+JSON.stringify(c)+nueva,box=$("#ndCli");if(box.dataset.sig===sig)return;box.dataset.sig=sig;
  box.innerHTML='<b>💻 Usar la IA del servidor desde este PC</b>'+
    '<button class="'+(c.enabled?'ok':'warn')+'" data-cli="'+(c.enabled?0:1)+'">'+(c.enabled?'✅ Activado':'⛔ Desactivado')+'</button>'+
    '<label class="mut">a la vez <select id="ndCliMax">'+[1,2,3,4].map(k=>'<option'+(k===c.max_inflight?' selected':'')+'>'+k+'</option>').join('')+'</select></label>'+
    '<small>'+(c.enabled?'En ese PC, su Ollama de siempre (127.0.0.1:11434) enseña también los modelos del servidor: «modelo@servidor» los atiende la 3080 '+
      'y «modelo@tarjeta» otra tarjeta prestada, nunca la suya. '+c.inflight+' en curso · '+c.served+' atendidas desde el último reinicio del proxy.'
      :'Apagado: en ese PC su Ollama solo enseña sus propios modelos.')+
    (nueva?'':' <b>Necesita GPU Prestada 1.9.0</b> en ese PC (se actualiza sola).')+'</small>';}
document.addEventListener('click',async ev=>{const b=ev.target.closest('#ndCli button[data-cli]');if(!b||!TAB.startsWith('n:'))return;
  const name=TAB.slice(2);b.disabled=true;
  try{const j=await post('/api/node/'+encodeURIComponent(name)+'/client?enabled='+b.dataset.cli);
    toast(name+': uso de la IA del servidor '+(j.enabled?'activado':'desactivado'));$("#ndCli").dataset.sig='';load();}
  catch(e){toast('No se pudo: '+e.message,true);b.disabled=false;}});
document.addEventListener('change',async ev=>{if(ev.target.id!=='ndCliMax'||!TAB.startsWith('n:'))return;
  const name=TAB.slice(2);
  try{await post('/api/node/'+encodeURIComponent(name)+'/client?max='+ev.target.value);toast(name+': hasta '+ev.target.value+' a la vez');$("#ndCli").dataset.sig='';load();}
  catch(e){toast('No se pudo: '+e.message,true);}});
function renderNode(d){''')
rep('''  ndActsPaint(n,n.paused||pcPausa);''', '''  ndActsPaint(n,n.paused||pcPausa);
  ndCliPaint(n);''')

open(p, "w", encoding="utf-8").write(s)
print("parcheado")
