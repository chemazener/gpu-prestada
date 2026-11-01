#!/usr/bin/env python3
"""Añade al panel de VRAM (broker_dashboard.py) la sección «Tarjetas gráficas»:
la 3080 del servidor y los nodos remotos de GPU Prestada. Idempotente."""
import shutil
import sys
import time

P = sys.argv[1] if len(sys.argv) > 1 else "/opt/broker-dashboard/broker_dashboard.py"
s = open(P).read()
if "def _gpu_nodes" in s:
    print("ya aplicado")
    sys.exit(0)


def rep(old, new):
    global s
    if s.count(old) != 1:
        sys.exit(f"ancla no encontrada o repetida ({s.count(old)}): {old[:70]!r}")
    s = s.replace(old, new)


# ---------- backend ----------
rep('''@app.get("/api/state")
def api_state():
''', '''NODES_URL = os.getenv("NODES_URL", "http://127.0.0.1:11434/_nodes")


def _gpu_nodes() -> Dict[str, Any]:
    """Tarjetas remotas dadas de alta en el proxy de Ollama (GPU Prestada)."""
    try:
        with urllib.request.urlopen(NODES_URL, timeout=2) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        return {"nodes": [], "error": str(e)}


@app.get("/api/state")
def api_state():
''')
rep('''        "procs": _gpu_procs(),
        "sys": _sys(),
    }
''', '''        "procs": _gpu_procs(),
        "sys": _sys(),
        "remote": _gpu_nodes(),
    }
''')

# ---------- estilos ----------
rep(''' .mut{color:var(--mut)} .r{text-align:right}''', ''' .gpus{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(280px,100%),1fr));gap:12px}
 .gp{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:12px 14px;min-width:0}
 .gp.dead{opacity:.6}
 .gp-h{display:flex;align-items:flex-start;justify-content:space-between;gap:8px;margin-bottom:8px}
 .gp-h b{font-size:15px;overflow-wrap:anywhere} .gp-h small{display:block;color:var(--mut);font-size:12px;overflow-wrap:anywhere}
 .gp-r{display:flex;justify-content:space-between;gap:8px;font-size:12px;color:var(--mut);margin-top:8px}
 .gp-r b{color:var(--fg);font-weight:600;font-variant-numeric:tabular-nums}
 .gp-m{height:8px;border-radius:5px;background:#21262d;overflow:hidden;margin-top:4px}
 .gp-m i{display:block;height:100%;background:var(--acc);width:0;transition:width .5s}
 .gp-m i.load{background:var(--ok)}
 .gp-f{font-size:12px;color:var(--mut);margin-top:10px;overflow-wrap:anywhere}
 .gp-f code{color:var(--fg);background:#21262d;padding:1px 5px;border-radius:4px}
 .gp-note{font-size:12px;color:var(--mut);margin-top:10px;overflow-wrap:anywhere}
 .mut{color:var(--mut)} .r{text-align:right}''')

# ---------- sección ----------
rep('''  <section id="hist">''', '''  <section id="gpus">
    <h2>Tarjetas gráficas <span class="mut" id="gpuMode" style="font-weight:400;font-size:.8em"></span></h2>
    <div class="gpus" id="gpuCards"></div>
    <div class="gp-note" id="gpuNote"></div>
  </section>
  <section id="hist">''')

# ---------- JS ----------
rep('''async function load(){
  let d;''', '''function esc(x){return String(x==null?"":x).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));}
function gpMeter(label,val,pct,load){
  return '<div class="gp-r"><span>'+label+'</span><b>'+val+'</b></div><div class="gp-m"><i'+(load?' class="load"':'')+
         ' style="width:'+clamp(pct)+'%"></i></div>';}
function renderGpus(d){
  const g=d.gpu||{},rem=d.remote||{},nodes=(rem.nodes||[]).slice().sort((a,b)=>(b.alive-a.alive)||a.name.localeCompare(b.name));
  const auto=rem.mode==="prefer";
  const vivos=nodes.filter(n=>n.alive).length;
  $("#gpuMode").textContent=(1+vivos)+" en línea · reparto "+(auto?"automático":"manual");
  let h='<div class="gp"><div class="gp-h"><div><b>RTX 3080</b><small>Servidor · tarjeta principal</small></div>'+
        '<span class="pill on">principal</span></div>'+
        gpMeter("Memoria",(g.used||0)+" / "+(g.total||0)+" MiB",g.total?g.used/g.total*100:0)+
        gpMeter("Carga",(g.util!=null?g.util:"—")+" %",g.util,1)+
        '<div class="gp-f">'+(g.temp!=null?g.temp+" °C":"")+(g.power!=null?" · "+g.power+(g.power_limit?" / "+g.power_limit:"")+" W":"")+
        ' · atiende todo lo que no va a otra tarjeta</div></div>';
  for(const n of nodes){
    const used=Math.max(0,(n.vram_total_mb||0)-(n.vram_free_mb||0));
    let pill;
    if(!n.alive)pill='<span class="pill off">apagada</span>';
    else if(n.inflight>0)pill='<span class="pill busy">trabajando</span>';
    else if(n.usable)pill='<span class="pill on">prestando</span>';
    else if(n.lending)pill='<span class="pill bad">sin respuesta</span>';
    else pill='<span class="pill off">no presta</span>';
    const modelos=(n.models||[]).length?(n.models||[]).map(esc).join(", "):"ninguno todavía";
    h+='<div class="gp'+(n.alive?'':' dead')+'"><div class="gp-h"><div><b>'+esc(n.name)+'</b><small>'+
       esc((n.gpu||"").replace("NVIDIA GeForce ",""))+' · GPU Prestada '+esc(n.version||"")+
       (n.up_to_date===false?' · actualización pendiente':n.up_to_date===null&&n.alive?' · sin actualización automática':'')+'</small></div>'+pill+'</div>';
    if(n.alive){
      h+=gpMeter("Memoria",used+" / "+(n.vram_total_mb||0)+" MiB",n.vram_total_mb?used/n.vram_total_mb*100:0)+
         gpMeter(n.inflight>0?"Carga cedida":"Carga",(n.gpu_util||0)+" %",n.gpu_util,1);
      if(!n.usable&&n.reason)h+='<div class="gp-f">'+esc(n.reason)+'</div>';
      if(!n.usable&&n.last_error&&n.lending)h+='<div class="gp-f">Último fallo: '+esc(n.last_error)+'</div>';
    }else{
      h+='<div class="gp-f">Sin señal desde hace '+ago(n.seen_s_ago)+'. Sus trabajos los atiende la 3080.</div>';
    }
    h+='<div class="gp-r"><span>Trabajos atendidos</span><b>'+(n.served||0)+'</b></div>'+
       '<div class="gp-f">Modelos: '+modelos+'</div>';
    if(!auto&&n.alive&&(n.models||[]).length)
      h+='<div class="gp-f">Para usarla: <code>'+esc(n.models[0])+'@'+esc(n.name)+'</code></div>';
    h+='</div>';
  }
  $("#gpuCards").innerHTML=h;
  let note=auto
    ?"Reparto automático: los modelos "+esc((rem.prefer_models||[]).join(", ")||"—")+" van a una tarjeta remota si está prestando; si no, a la 3080."
    :"Reparto manual: una petición solo va a una tarjeta remota si se pide con «modelo@tarjeta»; todo lo demás lo atiende la 3080.";
  if(rem.error)note="No se pudo consultar el registro de tarjetas remotas ("+esc(rem.error)+").";
  else if(!nodes.length)note+=" Aún no hay tarjetas remotas dadas de alta.";
  note+=' Para añadir un PC con Windows: desde la red Tailscale, descargar <a href="http://100.72.138.100:11434/_nodes/agent.exe" style="color:var(--acc)">GPU Prestada</a> y ejecutarlo.';
  $("#gpuNote").innerHTML=note;
}
async function load(){
  let d;''')
rep('''  $("#meta").textContent=(d.broker_ok?"broker OK":"BROKER KO")+" · up "+ago(d.uptime_s)+" · "+new Date().toLocaleTimeString();
''', '''  $("#meta").textContent=(d.broker_ok?"broker OK":"BROKER KO")+" · up "+ago(d.uptime_s)+" · "+new Date().toLocaleTimeString();
  try{renderGpus(d);}catch(e){console.error(e);}
''')

shutil.copy2(P, f"{P}.bak-tarjetas-{time.strftime('%Y%m%d-%H%M%S')}")
open(P, "w").write(s)
print("aplicado")
