#!/usr/bin/env python3
"""Monitor VRAM: pestañas, historial y estadísticas por tarjeta (la 3080 y cada
tarjeta remota de GPU Prestada), y arreglo del texto cortado en las barras de
la cabecera. Idempotente."""
import shutil
import sys
import time

P = sys.argv[1] if len(sys.argv) > 1 else "/opt/broker-dashboard/broker_dashboard.py"
s = open(P).read()
if "def _nodes_sample" in s:
    print("ya aplicado")
    sys.exit(0)


def rep(old, new, count=1):
    global s
    if s.count(old) != count:
        sys.exit(f"ancla no encontrada o repetida ({s.count(old)}): {old[:80]!r}")
    s = s.replace(old, new)


# =============================== BACKEND ===============================
rep('''@app.get("/api/state")
def api_state():
''', '''# ---------- historial y estadísticas por tarjeta ----------
from collections import deque as _deque

NHIST_FILE = os.getenv("NHIST_FILE", "/var/lib/broker-dashboard/history_nodes.json")
NHIST_KEYS = ["ts", "vram", "util", "temp", "power", "lent", "state", "served"]
_NHIST: Dict[str, Any] = {}      # nombre -> deque de puntos [ts, vram, util, temp, power, lent, state, served]
_NSTAT: Dict[str, Dict[str, Any]] = {}   # nombre -> datos fijos y contador acumulado
_NLOCK = threading.Lock()
_NMAX = HIST_MAX_S // HIST_STEP + 10


def _nodes_load() -> None:
    try:
        with open(NHIST_FILE) as f:
            d = json.load(f)
        with _NLOCK:
            _NSTAT.update(d.get("stat", {}))
            for k, pts in d.get("hist", {}).items():
                _NHIST[k] = _deque(pts, maxlen=_NMAX)
    except Exception:
        pass


def _nodes_save() -> None:
    try:
        with _NLOCK:
            d = {"stat": _NSTAT, "hist": {k: list(v) for k, v in _NHIST.items()}}
            raw = json.dumps(d)
        os.makedirs(os.path.dirname(NHIST_FILE), exist_ok=True)
        tmp = NHIST_FILE + ".tmp"
        with open(tmp, "w") as f:
            f.write(raw)
        os.replace(tmp, NHIST_FILE)
    except Exception:
        pass


def _nodes_sample() -> None:
    """Una muestra por tarjeta remota viva. state: 0 no presta, 1 prestando, 2 trabajando."""
    rem = _gpu_nodes()
    now = int(time.time())

    def val(n, k):
        v = n.get(k)
        return v if isinstance(v, (int, float)) and v >= 0 else None

    with _NLOCK:
        for n in rem.get("nodes", []):
            name = n.get("name")
            if not name:
                continue
            st = _NSTAT.setdefault(name, {"first_seen": now, "served_total": 0, "last_counter": 0})
            c = int(n.get("served") or 0)
            # el contador del proxy vuelve a 0 cuando el proxy se reinicia
            st["served_total"] += c if c < st["last_counter"] else c - st["last_counter"]
            st["last_counter"] = c
            if n.get("gpu"):
                st["gpu"] = n["gpu"]
            if n.get("vram_total_mb"):
                st["vram_total"] = n["vram_total_mb"]
            if (n.get("power_limit") or 0) > 0:
                st["power_limit"] = n["power_limit"]
            if not n.get("alive"):
                continue
            st["last_seen"] = now
            state = 2 if n.get("inflight") else (1 if n.get("usable") else 0)
            used = max(0, (n.get("vram_total_mb") or 0) - (n.get("vram_free_mb") or 0))
            dq = _NHIST.get(name)
            if dq is None:
                dq = _NHIST[name] = _deque(maxlen=_NMAX)
            dq.append([now, used, val(n, "gpu_util"), val(n, "temp"), val(n, "power"),
                       n.get("lent_load") or 0, state, st["served_total"]])


def _bucketize(pts: List[List[Any]], keys: List[str], range_s: int, points: int) -> Dict[str, Any]:
    """Reduce los puntos a <= `points` cubos (media por cubo), en columnas paralelas."""
    bucket = max(HIST_STEP, range_s / points)
    cols: Dict[str, List[Any]] = {k: [] for k in keys}
    cur: List[List[Any]] = []

    def flush():
        if not cur:
            return
        cols["ts"].append(int(_avg([p[0] for p in cur])))
        for i, k in enumerate(keys[1:], start=1):
            cols[k].append(_avg([p[i] for p in cur]))

    if pts:
        b0 = pts[0][0]
        for p in pts:
            if p[0] - b0 >= bucket:
                flush()
                cur = []
                b0 = p[0]
            cur.append(p)
        flush()
    return {"step_s": round(bucket, 1), "series": cols}


def _agg(vals: List[Any]) -> Dict[str, Any]:
    v = [x for x in vals if x is not None]
    if not v:
        return {"avg": None, "max": None, "min": None}
    return {"avg": round(sum(v) / len(v), 1), "max": max(v), "min": min(v)}


def _range(range_s: int) -> int:
    return max(60, min(int(range_s), HIST_MAX_S))


@app.get("/api/card/local/stats")
def api_local_stats(range_s: int = 86400):
    """Estadísticas de la tarjeta del servidor en el periodo pedido."""
    range_s = _range(range_s)
    cutoff = time.time() - range_s
    with _HIST_LOCK:
        pts = [p for p in _HIST if p[0] >= cutoff]
    busy = sum(1 for p in pts if (p[2] or 0) >= 10)
    return {
        "range_s": range_s, "covered_s": min(range_s, len(pts) * HIST_STEP),
        "busy_s": busy * HIST_STEP,
        "vram": _agg([p[1] for p in pts]), "util": _agg([p[2] for p in pts]),
        "temp": _agg([p[3] for p in pts]), "power": _agg([p[4] for p in pts]),
        "cpu": _agg([p[5] for p in pts]), "ram": _agg([p[6] for p in pts]),
        "totals": _HIST_TOTAL,
    }


@app.get("/api/node/{name}/history")
def api_node_history(name: str, range_s: int = 3600, points: int = 360):
    range_s = _range(range_s)
    points = max(30, min(points, 2000))
    now = time.time()
    with _NLOCK:
        pts = [p for p in _NHIST.get(name, []) if p[0] >= now - range_s]
        st = dict(_NSTAT.get(name, {}))
    out = _bucketize(pts, NHIST_KEYS, range_s, points)
    out.update({"now": int(now), "range_s": range_s,
                "totals": {"vram_total": st.get("vram_total"), "power_limit": st.get("power_limit")}})
    return out


@app.get("/api/node/{name}/stats")
def api_node_stats(name: str, range_s: int = 86400):
    range_s = _range(range_s)
    now = time.time()
    with _NLOCK:
        pts = [p for p in _NHIST.get(name, []) if p[0] >= now - range_s]
        st = dict(_NSTAT.get(name, {}))
    work = [p for p in pts if p[6] == 2]
    return {
        "range_s": range_s,
        "online_s": min(range_s, len(pts) * HIST_STEP),
        "lending_s": min(range_s, sum(1 for p in pts if p[6] >= 1) * HIST_STEP),
        "working_s": min(range_s, len(work) * HIST_STEP),
        "vram": _agg([p[1] for p in pts]), "util": _agg([p[2] for p in pts]),
        "temp": _agg([p[3] for p in pts]), "power": _agg([p[4] for p in pts]),
        "lent": _agg([p[5] for p in work]),
        "served_range": (pts[-1][7] - pts[0][7]) if len(pts) > 1 else 0,
        "served_total": st.get("served_total", 0),
        "first_seen": st.get("first_seen"), "last_seen": st.get("last_seen"),
        "totals": {"vram_total": st.get("vram_total"), "power_limit": st.get("power_limit")},
    }


@app.post("/api/node/{name}/forget")
def api_node_forget(name: str):
    """Borra una tarjeta remota apagada de la lista y su historial."""
    with _NLOCK:
        _NSTAT.pop(name, None)
        _NHIST.pop(name, None)
    _nodes_save()
    return {"ok": True}


def _remote_merged() -> Dict[str, Any]:
    """Tarjetas del proxy + las ya conocidas que ahora no aparecen (apagadas)."""
    rem = _gpu_nodes()
    now = time.time()
    seen = set()
    with _NLOCK:
        for n in rem.get("nodes", []):
            seen.add(n.get("name"))
            n["served_total"] = _NSTAT.get(n.get("name"), {}).get("served_total", n.get("served") or 0)
        for name, st in _NSTAT.items():
            if name not in seen:
                rem.setdefault("nodes", []).append({
                    "name": name, "alive": False, "usable": False, "lending": False,
                    "gpu": st.get("gpu", ""), "vram_total_mb": st.get("vram_total", 0), "models": [],
                    "version": "", "served_total": st.get("served_total", 0),
                    "seen_s_ago": round(now - st["last_seen"], 1) if st.get("last_seen") else None,
                })
    return rem


@app.get("/api/state")
def api_state():
''')
rep('''        "remote": _gpu_nodes(),
''', '''        "remote": _remote_merged(),
''')
rep('''    _hist_load()
    _sys(_HIST_CPU)  # primar el contador de CPU
''', '''    _hist_load()
    _nodes_load()
    _sys(_HIST_CPU)  # primar el contador de CPU
''')
rep('''        try:
            _hist_sample()
        except Exception:
            pass
        n += 1
        if n % max(1, 30 // HIST_STEP) == 0:  # ~cada 30 s
            _hist_save()
''', '''        try:
            _hist_sample()
        except Exception:
            pass
        try:
            _nodes_sample()
        except Exception:
            pass
        n += 1
        if n % max(1, 30 // HIST_STEP) == 0:  # ~cada 30 s
            _hist_save()
            _nodes_save()
''')

# =============================== ESTILOS ===============================
# Barras de la cabecera: el texto ya no se parte en dos líneas ni se corta a medias.
rep(''' .bar>b{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:600;text-shadow:0 1px 2px #000}''',
    ''' .bar>b{position:absolute;inset:0;padding:0 8px;line-height:20px;text-align:center;font-size:12px;font-weight:600;
   text-shadow:0 1px 2px #000;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}''')
rep(''' .gpus{display:grid;''', ''' .tabs{display:flex;gap:6px;overflow-x:auto;max-width:1100px;margin:0 auto;padding:14px 20px 0;scrollbar-width:thin}
 .tabs .tab{flex:none;background:var(--card);color:var(--mut);border:1px solid var(--bd);border-radius:8px;padding:7px 14px;
   font:inherit;font-weight:600;cursor:pointer;white-space:nowrap}
 .tabs .tab.act{background:var(--acc);border-color:var(--acc);color:#fff}
 .tabs .tab .d{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;background:var(--mut)}
 .tabs .tab .d.on{background:var(--ok)} .tabs .tab .d.busy{background:var(--warn)}
 main>section{display:none} main>section.show{display:block}
 .kvs{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,135px),1fr));gap:10px;padding:16px}
 .kv{background:#0f141b;border:1px solid var(--bd);border-radius:10px;padding:10px 12px;min-width:0}
 .kv small{display:block;color:var(--mut);font-size:11px;text-transform:uppercase;letter-spacing:.4px}
 .kv b{font-size:16px;font-variant-numeric:tabular-nums;overflow-wrap:anywhere}
 .kv span{display:block;color:var(--mut);font-size:12px;overflow-wrap:anywhere}
 .sec-note{padding:0 16px 14px;color:var(--mut);font-size:12px;overflow-wrap:anywhere}
 .gp[data-go]{cursor:pointer} .gp[data-go]:hover{border-color:var(--acc)}
 @media (max-width:680px){.tabs{padding:10px 12px 0}}
 .gpus{display:grid;''')

# =============================== HTML ===============================
rep('''<main>
''', '''<nav class="tabs" id="tabs" aria-label="Vistas"></nav>
<main>
''')
rep('''  <section>
    <h2><span class="dot live" id="livedot"''', '''  <section data-tab="local">
    <h2><span class="dot live" id="livedot"''')
rep('''  <section id="gpus">''', '''  <section id="gpus" data-tab="resumen">''')
rep('''  <section id="hist">''', '''  <section id="sumsec" data-tab="resumen">
    <h2>Conjunto de tarjetas</h2>
    <div class="kvs" id="sumStats"></div>
  </section>
  <section id="nodeInfo" data-tab="node">
    <h2 id="ndTitle">Tarjeta remota</h2>
    <div class="kvs" id="ndInfo"></div>
    <div class="sec-note" id="ndNote"></div>
  </section>
  <section id="nodeLive" data-tab="node">
    <h2>Estadísticas en vivo</h2>
    <div class="stats">
      <div class="stat" id="nd-vram"><div class="viz liq"><div class="fill"></div><div class="shine"></div><div class="gc"><span class="num">—</span><span class="u">MiB</span></div></div><div class="k">VRAM usada</div><div class="sub">—</div></div>
      <div class="stat" id="nd-vfree"><div class="viz liq"><div class="fill"></div><div class="shine"></div><div class="gc"><span class="num">—</span><span class="u">MiB</span></div></div><div class="k">VRAM libre</div><div class="sub">—</div></div>
      <div class="stat" id="nd-util"><div class="viz eqw"><div class="eq"><b></b><b></b><b></b><b></b><b></b></div><div class="gc"><span class="num">—</span><span class="u">%</span></div></div><div class="k">Uso GPU</div><div class="sub">—</div></div>
      <div class="stat" id="nd-lent"><div class="viz eqw"><div class="eq"><b></b><b></b><b></b><b></b><b></b></div><div class="gc"><span class="num">—</span><span class="u">%</span></div></div><div class="k">Carga cedida</div><div class="sub">—</div></div>
      <div class="stat" id="nd-temp"><div class="viz flame"><div class="grp"><div class="f f1"></div><div class="f f2"></div><div class="f f3"></div></div><div class="gc"><span class="num">—</span><span class="u">°C</span></div></div><div class="k">Temperatura</div><div class="sub">—</div></div>
      <div class="stat" id="nd-power"><div class="viz bolt"><svg viewBox="0 0 24 24"><path d="M13 2 L4 14 h6 l-2 8 11-13 h-7 z"/></svg><div class="gc"><span class="num">—</span><span class="u">W</span></div></div><div class="k">Potencia</div><div class="sub">—</div></div>
      <div class="stat" id="nd-jobs"><div class="viz pulse"><span class="core"></span><span class="ring p"></span><span class="ring p2"></span><div class="gc"><span class="num">—</span><span class="u"></span></div></div><div class="k">Trabajos atendidos</div><div class="sub">—</div></div>
    </div>
  </section>
  <section id="nodeStats" data-tab="node">
    <h2>Estadísticas del periodo</h2>
    <div class="hist-head">
      <span class="lbl">Rango</span>
      <div class="seg" id="nrange">
        <button data-r="900">15 min</button><button data-r="3600">1 h</button><button data-r="10800">3 h</button>
        <button data-r="43200">12 h</button><button data-r="86400">24 h</button>
      </div>
      <span class="hist-meta" id="nmeta">—</span>
    </div>
    <div class="kvs" id="ndStats"></div>
  </section>
  <section id="nodeHist" data-tab="node">
    <h2>Evolución en el tiempo</h2>
    <div class="charts" id="ncharts">
      <div class="ch" data-k="vram"><div class="t"><b>VRAM usada</b><span>MiB</span></div><canvas></canvas></div>
      <div class="ch" data-k="util"><div class="t"><b>Uso GPU</b><span>%</span></div><canvas></canvas></div>
      <div class="ch" data-k="lent"><div class="t"><b>Carga cedida</b><span>%</span></div><canvas></canvas></div>
      <div class="ch" data-k="temp"><div class="t"><b>Temperatura</b><span>°C</span></div><canvas></canvas></div>
      <div class="ch" data-k="power"><div class="t"><b>Potencia</b><span>W</span></div><canvas></canvas></div>
    </div>
    <div class="hist-empty" id="nempty" style="display:none">Todavía no hay muestras de esta tarjeta en el periodo elegido.</div>
  </section>
  <section id="localStatsSec" data-tab="local">
    <h2>Estadísticas del periodo <span class="mut" id="lsMeta" style="font-weight:400;font-size:.8em;text-transform:none;letter-spacing:0"></span></h2>
    <div class="kvs" id="lsStats"></div>
  </section>
  <section id="hist" data-tab="local">''')
rep('''  <section>
    <h2>Procesos en la GPU</h2>''', '''  <section data-tab="local">
    <h2>Procesos en la GPU</h2>''')
rep('''  <section>
    <h2>Servicios del broker</h2>''', '''  <section data-tab="local">
    <h2>Servicios del broker</h2>''')
rep('''  <section id="bench">''', '''  <section id="bench" data-tab="tools">''')
rep('''  <section id="checks">''', '''  <section id="checks" data-tab="tools">''')

# =============================== JS ===============================
# Texto de las barras de la cabecera según el ancho disponible
rep('''  $("#vtxt").textContent="VRAM "+g.used+" / "+g.total+" MiB ("+pct+"%) · "+g.free+" libres";''',
    '''  const vw=$("#vtxt").clientWidth;
  $("#vtxt").textContent=vw>=300?"VRAM "+g.used+" / "+g.total+" MiB ("+pct+"%) · "+g.free+" libres"
    :vw>=210?"VRAM "+g.used+" / "+g.total+" MiB · "+pct+"%":"VRAM "+pct+"% · "+g.free+" MiB libres";''')
rep('''  try{renderGpus(d);}catch(e){console.error(e);}
''', '''  try{renderGpus(d);}catch(e){console.error(e);}
  try{STATE=d;renderTabs(d);renderSummary(d);renderNode(d);extrasTick();}catch(e){console.error(e);}
''')
# tarjetas del resumen: clic = ir a su pestaña
rep('''  let h='<div class="gp"><div class="gp-h"><div><b>RTX 3080</b>''',
    '''  let h='<div class="gp" data-go="local"><div class="gp-h"><div><b>RTX 3080</b>''')
rep('''    h+='<div class="gp'+(n.alive?'':' dead')+'"><div class="gp-h">''',
    '''    h+='<div class="gp'+(n.alive?'':' dead')+'" data-go="n:'+esc(n.name)+'"><div class="gp-h">''')
rep('''    h+='<div class="gp-r"><span>Trabajos atendidos</span><b>'+(n.served||0)+'</b></div>'+''',
    '''    h+='<div class="gp-r"><span>Trabajos atendidos</span><b>'+(n.served_total!=null?n.served_total:(n.served||0))+'</b></div>'+''')

rep('''async function load(){
  let d;''', '''// ================= pestañas y vistas por tarjeta =================
let STATE=null,TAB=decodeURIComponent(location.hash.slice(1))||'resumen',TABSIG='',NDATA=null,EXTRA_T=0;
function dur(sec){if(sec==null)return'—';sec=Math.round(sec);if(sec<60)return sec+' s';
  const m=Math.round(sec/60);if(m<60)return m+' min';const h=Math.floor(m/60);return h+' h '+(m%60)+' min';}
function pctOf(a,b){return b?Math.round(a/b*100):0;}
function am(o,u){return o&&o.avg!=null?Math.round(o.avg)+' '+u:'—';}
function mx(o,u){return o&&o.max!=null?'máx. '+Math.round(o.max)+' '+u:'sin datos';}
function kv(k,v,sub){return '<div class="kv"><small>'+k+'</small><b>'+v+'</b>'+(sub?'<span>'+sub+'</span>':'')+'</div>';}
function fecha(ts){return ts?new Date(ts*1000).toLocaleString([],{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}):'—';}
function nodeOf(d,tab){return ((d&&d.remote&&d.remote.nodes)||[]).find(n=>'n:'+n.name===tab);}
function renderTabs(d){
  const nodes=((d.remote&&d.remote.nodes)||[]).slice().sort((a,b)=>a.name.localeCompare(b.name));
  const tabs=[['resumen','Resumen',''],['local','RTX 3080','on']]
    .concat(nodes.map(n=>['n:'+n.name,n.name,!n.alive?'':n.inflight>0?'busy':n.usable?'on':'']))
    .concat([['tools','Herramientas',null]]);
  if(!tabs.some(t=>t[0]===TAB))TAB='resumen';
  const sig=JSON.stringify(tabs)+TAB;
  if(sig!==TABSIG){TABSIG=sig;
    $("#tabs").innerHTML=tabs.map(t=>'<button class="tab'+(t[0]===TAB?' act':'')+'" data-tab="'+esc(t[0])+'">'+
      (t[2]!=null&&t[0]!=='resumen'?'<span class="d '+t[2]+'"></span>':'')+esc(t[1])+'</button>').join('');}
  const kind=TAB.startsWith('n:')?'node':TAB;
  document.querySelectorAll('main>section').forEach(sec=>sec.classList.toggle('show',sec.dataset.tab===kind));
}
function goTab(t){TAB=t;history.replaceState(null,'','#'+encodeURIComponent(t));NDATA=null;EXTRA_T=0;
  if(STATE){renderTabs(STATE);renderNode(STATE);}
  window.scrollTo(0,0);drawAll();extrasTick();}
document.addEventListener('click',ev=>{
  const t=ev.target.closest('#tabs .tab');if(t){goTab(t.dataset.tab);return;}
  const g=ev.target.closest('.gp[data-go]');if(g&&!ev.target.closest('a'))goTab(g.dataset.go);});
function renderSummary(d){
  const g=d.gpu||{},nodes=(d.remote&&d.remote.nodes)||[],vivos=nodes.filter(n=>n.alive),prest=vivos.filter(n=>n.usable);
  const tot=(g.total||0)+vivos.reduce((a,n)=>a+(n.vram_total_mb||0),0);
  const lib=(g.free||0)+prest.reduce((a,n)=>a+(n.vram_free_mb||0),0);
  $("#sumStats").innerHTML=
    kv('Tarjetas en línea',(1+vivos.length)+' de '+(1+nodes.length),prest.length+' remota'+(prest.length===1?'':'s')+' prestando')+
    kv('Memoria total',tot+' MiB','entre todas las tarjetas en línea')+
    kv('Memoria libre',lib+' MiB','la 3080 y las que están prestando')+
    kv('Trabajando ahora',vivos.filter(n=>n.inflight>0).length+' remota'+(vivos.filter(n=>n.inflight>0).length===1?'':'s'),'3080 al '+(g.util!=null?g.util:'—')+' %')+
    kv('Trabajos en remotas',nodes.reduce((a,n)=>a+(n.served_total||0),0),'atendidos desde que se dieron de alta')+
    kv('Reparto',d.remote&&d.remote.mode==='prefer'?'Automático':'Manual',d.remote&&d.remote.mode==='prefer'?'modelos: '+esc(((d.remote.prefer_models)||[]).join(', ')||'—'):'solo con «modelo@tarjeta»');
}
function ndNum(id,v,unit){if(v==null||v<0){num1(id,'—',0,unit);return false;}num1(id,v,0,unit);return true;}
function renderNode(d){
  const n=nodeOf(d,TAB);if(!n)return;
  const used=Math.max(0,(n.vram_total_mb||0)-(n.vram_free_mb||0)),tot=n.vram_total_mb||1;
  const estado=!n.alive?'Apagada':n.inflight>0?'Trabajando para el servidor':n.usable?'Prestando':n.lending?'Sin respuesta':'No presta';
  $("#ndTitle").textContent=n.name+' · '+(n.gpu||'tarjeta remota');
  $("#ndInfo").innerHTML=
    kv('Estado',estado,n.alive?(n.reason?esc(n.reason):'señal hace '+ago(n.seen_s_ago)):'sin señal desde hace '+ago(n.seen_s_ago))+
    kv('Tarjeta',esc((n.gpu||'—').replace('NVIDIA GeForce ','')),(n.vram_total_mb||0)+' MiB de memoria')+
    kv('GPU Prestada',n.version?'versión '+esc(n.version):'—',n.up_to_date===false?'actualización pendiente':n.up_to_date===true?'al día':'')+
    kv('Dirección',esc(n.ip||'—'),'red privada Tailscale')+
    kv('Último trabajo',n.last_served_s_ago!=null?'hace '+ago(n.last_served_s_ago):'—',(n.served_total||0)+' atendidos en total')+
    kv('Modelos',(n.models||[]).length||0,esc((n.models||[]).join(', ')||'ninguno todavía'));
  let note=n.alive?'':'Esta tarjeta está apagada; sus trabajos los atiende la 3080. <a href="#" id="ndForget" style="color:var(--acc)">Quitarla de la lista</a>';
  if(n.alive&&n.last_error&&!n.usable&&n.lending)note='Último fallo de conexión: '+esc(n.last_error);
  if(n.alive&&n.auto_pause)note='Cedida a su dueño: la gráfica está en uso en ese equipo.';
  $("#ndNote").innerHTML=note;$("#ndNote").style.display=note?'block':'none';
  const fg=$("#ndForget");if(fg)fg.onclick=async ev=>{ev.preventDefault();await post('/api/node/'+encodeURIComponent(n.name)+'/forget');toast('tarjeta quitada');goTab('resumen');load();};
  $("#nodeLive").style.opacity=n.alive?1:.45;
  const upct=used/tot*100;
  num1('nd-vram',n.alive?used:'—',0,'MiB');sub1('nd-vram',n.alive?Math.round(upct)+'% de '+tot+' MiB':'apagada');
  upLiquid('nd-vram',n.alive?upct:0,HEX[toneBy(upct,70,90)]);
  num1('nd-vfree',n.alive?(n.vram_free_mb||0):'—',0,'MiB');sub1('nd-vfree','disponibles');
  upLiquid('nd-vfree',n.alive?100-upct:0,HEX.acc);
  num1('nd-util',n.alive?(n.gpu_util||0):'—',0,'%');sub1('nd-util',n.auto_pause?'la usa su dueño':'toda la gráfica');
  upEq('nd-util',n.alive?n.gpu_util:0,HEX[toneBy(n.gpu_util,60,90)]);
  num1('nd-lent',n.alive?(n.lent_load||0):'—',0,'%');sub1('nd-lent',n.inflight>0?'trabajando ahora':'sin trabajo del servidor');
  upEq('nd-lent',n.alive?n.lent_load:0,HEX[(n.lent_load||0)>75?'bad':(n.lent_load||0)>50?'warn':'ok']);
  const hasT=n.alive&&ndNum('nd-temp',n.temp,'°C');if(!hasT)num1('nd-temp','—',0,'°C');
  sub1('nd-temp',hasT?(n.fan>=0?'ventilador '+n.fan+' %':'GPU'):'no disponible');upFlame('nd-temp',hasT?n.temp:null);
  const hasP=n.alive&&ndNum('nd-power',n.power,'W');if(!hasP)num1('nd-power','—',0,'W');
  sub1('nd-power',hasP?(n.power_limit>0?'de '+n.power_limit+' W':'ahora'):'la tarjeta no lo informa');
  upBolt('nd-power',hasP&&n.power_limit>0?n.power/n.power_limit*100:(hasP?Math.min(100,n.power):0),HEX.warn);
  num1('nd-jobs',n.served_total||0,0,'');sub1('nd-jobs',n.inflight>0?'uno en curso':'en total');upPulse('nd-jobs',n.alive&&n.usable);
}
function drawNode(){if(!NDATA)return;document.querySelectorAll('#ncharts .ch').forEach(el=>drawChart(el,NDATA));}
async function loadNodeExtras(){
  const n=STATE&&nodeOf(STATE,TAB);if(!n)return;const tab=TAB,q='/api/node/'+encodeURIComponent(n.name);
  let hs,st;try{[hs,st]=await Promise.all([fetch(q+'/history?range_s='+HRANGE+'&points=360').then(r=>r.json()),
    fetch(q+'/stats?range_s='+HRANGE).then(r=>r.json())]);}catch(e){return;}
  if(tab!==TAB)return;
  NDATA=hs;const np=hs.series.ts.length;$("#nempty").style.display=np<2?'block':'none';drawNode();
  $("#nmeta").textContent=np?np+' puntos · 1 cada '+Math.round(hs.step_s)+' s':'sin muestras';
  const R=st.range_s;
  $("#ndStats").innerHTML=
    kv('En línea',pctOf(st.online_s,R)+' %',dur(st.online_s)+' de '+dur(R))+
    kv('Prestando',pctOf(st.lending_s,R)+' %',dur(st.lending_s)+' disponible para el servidor')+
    kv('Trabajando para el servidor',pctOf(st.working_s,R)+' %',dur(st.working_s))+
    kv('Trabajos atendidos',st.served_range,'en el periodo · '+st.served_total+' en total')+
    kv('Carga cedida media',am(st.lent,'%'),mx(st.lent,'%')+' · solo mientras trabaja')+
    kv('Uso de la gráfica',am(st.util,'%'),mx(st.util,'%')+' · incluye el uso de su dueño')+
    kv('Memoria usada',am(st.vram,'MiB'),mx(st.vram,'MiB'))+
    kv('Temperatura',am(st.temp,'°C'),mx(st.temp,'°C'))+
    kv('Potencia',am(st.power,'W'),st.power&&st.power.max!=null?mx(st.power,'W'):'la tarjeta no lo informa')+
    kv('Dada de alta',fecha(st.first_seen),'última señal: '+fecha(st.last_seen));
}
async function loadLocalStats(){
  let st;try{st=await fetch('/api/card/local/stats?range_s='+HRANGE).then(r=>r.json());}catch(e){return;}
  const R=st.range_s,t=st.totals||{};
  $("#lsMeta").textContent='· últimos '+dur(R)+' (mismo rango que las gráficas)';
  $("#lsStats").innerHTML=
    kv('Con carga',pctOf(st.busy_s,st.covered_s)+' %',dur(st.busy_s)+' con la gráfica por encima del 10 %')+
    kv('Uso de la gráfica',am(st.util,'%'),mx(st.util,'%'))+
    kv('Memoria usada',am(st.vram,'MiB'),mx(st.vram,'MiB')+(t.vram_total?' de '+t.vram_total:''))+
    kv('Temperatura',am(st.temp,'°C'),mx(st.temp,'°C'))+
    kv('Potencia',am(st.power,'W'),mx(st.power,'W')+(t.power_limit?' de '+t.power_limit:''))+
    kv('CPU del servidor de IA',am(st.cpu,'%'),mx(st.cpu,'%'))+
    kv('RAM del servidor de IA',am(st.ram,'%'),mx(st.ram,'%'))+
    kv('Periodo con datos',dur(st.covered_s),'de '+dur(R)+' pedidos');
}
function extrasTick(){
  if(Date.now()-EXTRA_T<15000)return;EXTRA_T=Date.now();
  if(TAB.startsWith('n:'))loadNodeExtras();else if(TAB==='local')loadLocalStats();
}
document.addEventListener('DOMContentLoaded',()=>{
  HCOL.lent='#3fb950';HDEF.lent={u:'%',dec:0,max:()=>100};  // HCOL/HDEF se definen más abajo en el script
  document.querySelectorAll('#nrange button').forEach(b=>{b.classList.toggle('act',+b.dataset.r===HRANGE);
    b.onclick=()=>{HRANGE=+b.dataset.r;localStorage.setItem('hrange',HRANGE);
      document.querySelectorAll('#nrange button,#hrange button').forEach(x=>x.classList.toggle('act',+x.dataset.r===HRANGE));
      EXTRA_T=0;extrasTick();loadHist();};});
  document.querySelectorAll('#hrange button').forEach(b=>b.addEventListener('click',()=>{
    document.querySelectorAll('#nrange button').forEach(x=>x.classList.toggle('act',+x.dataset.r===HRANGE));EXTRA_T=0;extrasTick();}));
  window.addEventListener('resize',drawNode);
});
async function load(){
  let d;''')

shutil.copy2(P, f"{P}.bak-pestanas-{time.strftime('%Y%m%d-%H%M%S')}")
open(P, "w").write(s)
print("aplicado")
