#!/usr/bin/env python3
"""Monitor VRAM: en la pestaña de cada tarjeta prestada, «Procesos en la GPU» y
«Servicios del broker» como en la de la 3080 (31-oct-2026, agente 1.6.0+).

Uso: python3 patch_dashboard3.py /opt/broker-dashboard/broker_dashboard.py
"""
import sys

p = sys.argv[1]
s = open(p, encoding="utf-8").read()
if "/api/node/{name}/unload" in s:
    sys.exit("ya parcheado")


def rep(a, b):
    global s
    assert s.count(a) == 1, a[:80]
    s = s.replace(a, b)


# ---------- backend: desalojar un modelo de una tarjeta prestada ----------
rep('''@app.post("/api/node/{name}/forget")''', '''NODES_CONF = os.getenv("NODES_CONF", "/etc/ollama-proxy/nodes.json")


@app.post("/api/node/{name}/unload")
def api_node_unload(name: str, model: str):
    """Pide al agente de la tarjeta prestada que Ollama suelte un modelo de la gráfica."""
    n = next((x for x in _gpu_nodes().get("nodes", []) if x.get("name") == name and x.get("alive")), None)
    if not n or not n.get("url"):
        raise HTTPException(404, "tarjeta no conectada")
    with open(NODES_CONF) as f:
        token = json.load(f).get("token", "")
    req = urllib.request.Request(n["url"].rstrip("/") + "/_agent/unload", method="POST",
                                 data=json.dumps({"model": model}).encode(),
                                 headers={"Content-Type": "application/json", "X-Node-Token": token})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            r.read()
    except Exception as e:
        raise HTTPException(502, f"el agente no respondió: {e}")
    return {"ok": True}


@app.post("/api/node/{name}/forget")''')

# ---------- HTML: dos secciones al final de la pestaña, como en la de la 3080 ----------
rep('''  <section id="localStatsSec" data-tab="local">''', '''  <section data-tab="node">
    <h2>Procesos en la GPU</h2>
    <div class="tw"><table><thead><tr><th>PID</th><th class="r">VRAM</th><th>Proceso</th><th>Para quién</th></tr></thead>
      <tbody id="ndProc"></tbody></table></div>
  </section>
  <section data-tab="node">
    <h2>Servicios del broker</h2>
    <div class="tw"><table><thead><tr><th>Servicio</th><th>Estado</th><th>Trabajos</th>
      <th class="r">VRAM</th><th>Se descarga</th><th class="r">Acción</th></tr></thead>
      <tbody id="ndSvc"></tbody></table></div>
  </section>
  <section id="localStatsSec" data-tab="local">''')

# ---------- ficha: tarjetas integradas (Radeon/Intel, agente 1.7.0+) ----------
rep("""kv('Tarjeta',esc((n.gpu||'—').replace('NVIDIA GeForce ','')),(n.vram_total_mb||0)+' MiB de memoria')""",
    """kv('Tarjeta',esc((n.gpu||'—').replace('NVIDIA GeForce ','').replace('(TM)','')),(n.vram_total_mb||0)+' MiB de memoria'+(n.shared_mem?' compartida con el equipo':''))""")

# ---------- JS: rellenar las tablas en renderNode ----------
rep('''  num1('nd-jobs',n.served_total||0,0,'');''', '''  renderNodeTables(n);
  num1('nd-jobs',n.served_total||0,0,'');''')
rep('''function drawNode(){''', '''let ND_OTROS=false;
function renderNodeTables(n){
  const old=n.alive&&n.loaded==null,
    msg=t=>`<tr><td colspan=6 class=mut>${t}</td></tr>`,
    viejo='la versión '+esc(n.version||'')+' del agente no lo informa; se actualizará sola a la 1.6.0';
  // procesos: los de cálculo (tipo C, p. ej. Ollama); los C+G/G de Windows (explorer, Telegram…) solo tienen
  // la gráfica abierta para dibujar y van plegados en una línea. Sin tipo (agente 1.6.0): todos.
  const ps=n.procs||[],calc=ps.filter(p=>!p.type||p.type==='C'),otros=ps.filter(p=>p.type&&p.type!=='C'),
    fila=(p,mut)=>{const ol=/^(ollama|llama)/i.test(p.name);return `<tr${mut?' class="mut"':''}>
     <td data-label="PID" class="mono">${p.pid}</td>
     <td data-label="VRAM" class="r mono">${p.mem_mb>=0?p.mem_mb+' MiB':'<span class="mut" title="Windows no da la memoria por proceso">—</span>'}</td>
     <td data-label="Proceso">${/^\\[.*\\]$/.test(p.name)?'<span class="mut" title="Windows no deja ver su nombre sin permisos de administrador">proceso protegido de Windows</span>':esc(p.name)}</td>
     <td data-label="Para quién">${ol?'<span class="pill on">servidor (Ollama)</span>':mut?'<span class="pill off">Windows · sin cálculo</span>':'<span class="pill off">su dueño</span>'}</td>
   </tr>`;};
  $("#ndProc").innerHTML=!n.alive?msg('tarjeta apagada'):old?msg(viejo):
    (calc.map(p=>fila(p,0)).join('')||(otros.length?msg('ningún proceso calculando en la GPU'):''))+
    (otros.length?`<tr><td colspan=4 class="mut"><a href="#" style="color:var(--acc)" onclick="ND_OTROS=!ND_OTROS;load();return false">${ND_OTROS?'▾ ocultar':'▸ ver'}</a>
       ${otros.length} programa${otros.length>1?'s':''} de Windows con la gráfica abierta solo para dibujar en pantalla (no calculan)</td></tr>`+
      (ND_OTROS?otros.map(p=>fila(p,1)).join(''):''):'')||msg('ningún proceso en la GPU');
  // servicios: modelos cargados en Ollama + trabajos en curso
  const jobs=n.jobs||[],rows=[];
  (n.loaded||[]).forEach(m=>{
    const js=jobs.filter(j=>j.model===m.name),cpu=m.size_mb-m.vram_mb;
    rows.push(`<tr>
      <td data-label="Servicio"><b>${esc(m.name)}</b> <span class="mut">· Ollama</span></td>
      <td data-label="Estado">${js.length?'<span class="pill busy">busy</span>':'<span class="pill on">cargado</span>'}</td>
      <td data-label="Trabajos" class="mono">${js.length?js.length+' · hace '+ago(Math.max(...js.map(j=>j.age_s))):'0'}</td>
      <td data-label="VRAM" class="r mono">${m.vram_mb} MiB${cpu>64?` <span class="mut" title="no cabe entero en la gráfica">+${cpu} en CPU</span>`:''}</td>
      <td data-label="Se descarga" class="mut">${m.expires_s<0?'nunca':'en '+ago(m.expires_s)}</td>
      <td data-label="" class="r"><button class="danger" onclick="nodeUnload('${esc(n.name)}','${esc(m.name)}')">desalojar</button></td>
    </tr>`);});
  const pend={};jobs.filter(j=>!(n.loaded||[]).some(m=>m.name===j.model)).forEach(j=>(pend[j.model||j.path]=pend[j.model||j.path]||[]).push(j));
  Object.entries(pend).forEach(([k,js])=>rows.push(`<tr>
      <td data-label="Servicio"><b>${esc(k)}</b> <span class="mut">· Ollama</span></td>
      <td data-label="Estado"><span class="pill busy">cargando</span></td>
      <td data-label="Trabajos" class="mono">${js.length} · hace ${ago(Math.max(...js.map(j=>j.age_s)))}</td>
      <td data-label="VRAM" class="r mono">—</td><td data-label="Se descarga" class="mut">—</td><td></td></tr>`));
  $("#ndSvc").innerHTML=!n.alive?msg('tarjeta apagada'):old?msg(viejo):rows.join('')||msg('ningún modelo cargado');
}
async function nodeUnload(n,m){
  try{await post('/api/node/'+encodeURIComponent(n)+'/unload?model='+encodeURIComponent(m));toast('desalojado: '+m+' en '+n);}
  catch(e){toast('error: '+e.message,1);}setTimeout(load,1500);}
function drawNode(){''')

open(p, "w", encoding="utf-8").write(s)
print("parcheado")
