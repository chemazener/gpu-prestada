#!/usr/bin/env python3
"""Monitor VRAM: RAM y disco del PC prestado como MEDIDORES en la ficha (sustituye las cuatro casillas de la 1.12.1).
Forma «meter» de la guía de visualización: valor frente a su límite; el relleno lleva la gravedad (azul → ámbar ≥70 %
→ rojo ≥90 %, siempre con icono y palabra), el carril es un tono suave del mismo color, el texto en tinta normal.
Uso: python3 patch_dashboard9.py broker_dashboard.py   (sobre el que ya tiene patch_dashboard8)"""
import sys

p = sys.argv[1]
s = open(p, encoding="utf-8").read()
if "ndSysPaint" in s:
    sys.exit("ya parcheado")


def rep(a, b):
    global s
    assert s.count(a) == 1, a[:80]
    s = s.replace(a, b)


rep('''    kv('Modelos',(n.models||[]).length||0,esc((n.models||[]).join(', ')||'ninguno todavía'))+
    ndSys(n);''', '''    kv('Modelos',(n.models||[]).length||0,esc((n.models||[]).join(', ')||'ninguno todavía'));
  ndSysPaint(n);''')
rep('''    <div class="kvs" id="ndInfo"></div>''', '''    <div class="kvs" id="ndInfo"></div>
    <div class="ndsys" id="ndSysBox"></div>''')
rep(''' .ndacts{''', ''' .ndsys{display:grid;grid-template-columns:1fr 1fr;gap:12px;padding:0 16px 16px}
 .mtr{background:#0d1117;border:1px solid var(--bd);border-radius:10px;padding:14px 16px 12px;min-width:0}
 .mtr-h{display:flex;align-items:center;gap:8px;font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--mut)}
 .mtr-h .ic{font-size:15px;letter-spacing:0}.mtr-st{margin-left:auto;text-transform:none;letter-spacing:0;font-size:12px;font-weight:600;color:var(--fg)}
 .mtr-v{margin:6px 0 10px;display:flex;align-items:baseline;gap:8px;flex-wrap:wrap}
 .mtr-v .big{font-size:26px;font-weight:650;color:var(--fg);font-variant-numeric:tabular-nums}.mtr-v .mut{font-size:13px}
 .mtr-bar{position:relative;height:14px;border-radius:7px;overflow:hidden;background:var(--trk,rgba(47,129,247,.18))}
 .mtr-bar>i{position:absolute;left:0;top:0;bottom:0;width:0;border-radius:7px;background:var(--fil,#2f81f7);
   transition:width .9s cubic-bezier(.2,.8,.2,1),background-color .4s}
 .mtr-bar>i::after{content:"";position:absolute;inset:0;border-radius:7px;
   background:linear-gradient(90deg,transparent 0,rgba(255,255,255,.22) 50%,transparent 100%);background-size:200% 100%;animation:mtrsh 3.2s linear infinite}
 @keyframes mtrsh{from{background-position:200% 0}to{background-position:-200% 0}}
 @media (prefers-reduced-motion:reduce){.mtr-bar>i{transition:none}.mtr-bar>i::after{animation:none}}
 .mtr-f{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;margin-top:9px;font-size:12px;color:var(--mut)}
 .mtr-f b{color:var(--fg);font-weight:600;font-variant-numeric:tabular-nums}
 .mtr-f .sw{display:inline-block;width:9px;height:9px;border-radius:3px;margin-right:6px;vertical-align:-1px}
 .mtr.na .mtr-bar>i{width:0!important}.mtr.na .mtr-v .big{color:var(--mut)}
 @media (max-width:680px){.ndsys{grid-template-columns:1fr}}
 .ndacts{''')
rep('''function renderNode(d){''', '''// Medidores de RAM y disco del PC (forma «meter»): se construyen una vez y luego solo se actualizan,
// para que la barra se deslice al valor nuevo en vez de volver a empezar en cada refresco.
const MTR_TONE=p=>p>=90?['bad','⛔ casi lleno']:p>=70?['warn','⚠ justo']:['acc',''];
function ndSysPaint(n){
  const box=$("#ndSysBox");
  if(box.dataset.node!==n.name){box.dataset.node=n.name;
    box.innerHTML=[['ram','🧠','Memoria RAM'],['disk','💾','Disco']].map(([k,ic,t])=>`<div class="mtr" data-k="${k}">
      <div class="mtr-h"><span class="ic">${ic}</span>${t}<span class="mtr-st"></span></div>
      <div class="mtr-v"><span class="big">—</span><span class="mut"></span></div>
      <div class="mtr-bar" role="meter" aria-valuemin="0" aria-valuemax="100" aria-label="${t} ocupada"><i></i></div>
      <div class="mtr-f"><span><i class="sw su"></i>Ocupado <b class="fu">—</b></span><span><i class="sw sf"></i>Disponible <b class="ff">—</b></span></div></div>`).join('');}
  const put=(k,tot,free,extra)=>{
    const el=box.querySelector(`.mtr[data-k="${k}"]`),ok=n.alive&&tot>0&&free>=0;
    el.classList.toggle('na',!ok);
    const big=el.querySelector('.big'),sub=el.querySelector('.mtr-v .mut'),st=el.querySelector('.mtr-st'),bar=el.querySelector('.mtr-bar');
    if(!ok){big.textContent='—';sub.textContent=n.alive?'con GPU Prestada 1.12.1 o posterior':'equipo apagado';st.textContent='';
      el.querySelector('.fu').textContent='—';el.querySelector('.ff').textContent='—';bar.title='';return;}
    const used=tot-free,pct=Math.round(used/tot*100),[tone,lbl]=MTR_TONE(pct),col=HEX[tone];
    bar.style.setProperty('--fil',col);bar.style.setProperty('--trk',col+'2e');
    el.querySelector('.sw.su').style.background=col;el.querySelector('.sw.sf').style.background=col+'2e';
    bar.querySelector('i').style.width=Math.max(pct,1)+'%';
    bar.setAttribute('aria-valuenow',pct);
    bar.title=`${gbOf(used)} ocupados (${pct} %) · ${gbOf(free)} disponibles · total ${gbOf(tot)}`;
    big.textContent=pct+' %';sub.textContent='ocupado de '+gbOf(tot)+(extra||'');st.textContent=lbl;
    el.querySelector('.fu').textContent=gbOf(used);el.querySelector('.ff').textContent=gbOf(free);};
  put('ram',n.ram_total_mb,n.ram_free_mb,'');
  put('disk',n.disk_total_mb,n.disk_free_mb,n.disk_drive?' · unidad '+n.disk_drive:'');}
function renderNode(d){''')
open(p, "w", encoding="utf-8").write(s)
print("parcheado")
