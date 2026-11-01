#!/usr/bin/env python3
"""Añade a la ficha pública freetoken-benchmark.html la comparativa de octubre de 2026
(Saluki 27B, Bonsai 2, Bonsai 27B y gpt-oss-20B en FreeToken). Idempotente."""
import shutil, sys, time
P = sys.argv[1] if len(sys.argv) > 1 else "/var/www/html/lomastrend/proyectos/freetoken-benchmark.html"
s = open(P).read()
if 'id="octubre"' in s:
    print("ya aplicado"); sys.exit(0)
def rep(a, b):
    global s
    assert s.count(a) == 1, (s.count(a), a[:60])
    s = s.replace(a, b)

rep("""  @media(max-width:640px){.metrics{grid-template-columns:1fr 1fr}}""",
"""  .tw{overflow-x:auto;margin:1.2rem 0;border:1px solid var(--bd);border-radius:14px;background:var(--card)}
  table.cmp{width:100%;min-width:640px;border-collapse:collapse;font-size:.9rem}
  table.cmp th,table.cmp td{padding:.6rem .7rem;border-bottom:1px solid var(--bd);text-align:left;vertical-align:top;color:#cdd7e6}
  table.cmp thead th{font-size:.78rem;text-transform:uppercase;letter-spacing:.06em;color:var(--faint);white-space:nowrap}
  table.cmp tbody th{color:var(--dim);font-weight:600;width:24%}
  table.cmp tr:last-child th,table.cmp tr:last-child td{border-bottom:0}
  table.cmp .ok{color:var(--green);font-weight:700} table.cmp .no{color:#fca5a5;font-weight:700}
  table.cmp td small{display:block;color:var(--faint);font-size:.8rem}
  @media(max-width:640px){.metrics{grid-template-columns:1fr 1fr}}""")

rep("""  <section>
    <h2>Ficha técnica</h2>""",
"""  <section id="octubre">
    <h2>Actualización de octubre: cuatro modelos, seis pruebas</h2>
    <p>Desde agosto han salido modelos de <strong>27.000 millones de parámetros comprimidos a unos 2 bits por peso</strong>, que caben enteros en una gráfica de 10 GB. Les hemos pasado a todos las mismas seis peticiones, en la misma RTX 3080, junto al gpt-oss-20B servido con FreeToken: un texto largo, una consulta de atención al cliente, un encargo con dos herramientas en la misma frase, un problema de razonamiento y la extracción de una factura a JSON.</p>

    <div class="chart">
      <h3>Velocidad de generación <span class="hint">(tokens/s en un texto largo · más es mejor)</span></h3>
      <svg viewBox="0 0 600 205" width="100%" role="img" aria-label="Velocidad de generación: Bonsai 2 77 tokens por segundo, gpt-oss-20B con FreeToken 64, Bonsai 27B 56 y Saluki 27B 43">
        <line x1="190" y1="14" x2="190" y2="174" stroke="#22304a" stroke-width="1"/>
        <g font-size="14" font-weight="700" fill="#e5eef7" text-anchor="end">
          <text x="180" y="39">Bonsai 2 27B</text>
          <text x="180" y="79">gpt-oss-20B · FreeToken</text>
          <text x="180" y="119">Bonsai 27B</text>
          <text x="180" y="159">Saluki 27B</text>
        </g>
        <g fill="#38bdf8">
          <rect x="190" y="22" width="375" height="24" rx="4"><title>Bonsai 2 27B: 77 tokens/s</title></rect>
          <rect x="190" y="62" width="312" height="24" rx="4"><title>gpt-oss-20B con FreeToken: 64 tokens/s de extremo a extremo</title></rect>
          <rect x="190" y="102" width="273" height="24" rx="4"><title>Bonsai 27B: 56 tokens/s</title></rect>
          <rect x="190" y="142" width="210" height="24" rx="4"><title>Saluki 27B: 43 tokens/s</title></rect>
        </g>
        <g font-size="14" font-weight="800" fill="#e5eef7">
          <text x="573" y="39">77</text>
          <text x="510" y="79">64*</text>
          <text x="471" y="119">56</text>
          <text x="408" y="159">43</text>
        </g>
        <g fill="#4a5670" font-size="10" text-anchor="middle">
          <text x="190" y="190">0</text><text x="288" y="190">20</text><text x="385" y="190">40</text><text x="483" y="190">60</text><text x="580" y="190">80</text>
        </g>
      </svg>
      <div class="legend"><span>* FreeToken se midió de extremo a extremo, incluido su razonamiento interno; su velocidad real al generar es algo mayor.</span></div>
    </div>

    <div class="tw">
      <table class="cmp">
        <thead><tr><th></th><th>Saluki 27B</th><th>Bonsai 2 27B</th><th>Bonsai 27B</th><th>gpt-oss-20B</th></tr></thead>
        <tbody>
          <tr><th>Modelo base</th><td>Qwen3.8-27B</td><td>Qwen3.8-27B</td><td>Qwen3.6-27B</td><td>gpt-oss-20B (MoE)</td></tr>
          <tr><th>Tamaño en disco</th><td>7,9 GB</td><td>5,95 GB</td><td>7,2 GB</td><td>~13 GB<small>no cabe en la gráfica</small></td></tr>
          <tr><th>Motor</th><td>llama.cpp</td><td>llama.cpp<small>versión de PrismML</small></td><td>llama.cpp<small>versión de PrismML</small></td><td>FreeToken</td></tr>
          <tr><th>Velocidad</th><td>43 tok/s</td><td>77 tok/s</td><td>56 tok/s</td><td>64 tok/s*</td></tr>
          <tr><th>Arranque en frío</th><td>35 s</td><td>10 s</td><td>12 s</td><td>95 s</td></tr>
          <tr><th>Memoria de la gráfica</th><td>9,3–9,5 GB</td><td>7,0 GB</td><td>8,1 GB</td><td>9,3 GB</td></tr>
          <tr><th>Dos herramientas en una frase</th><td><span class="ok">Las dos</span></td><td><span class="ok">Las dos</span></td><td><span class="ok">Las dos</span></td><td><span class="no">Solo una</span><small>olvidó el segundo encargo</small></td></tr>
          <tr><th>Factura a JSON</th><td><span class="ok">Exacta</span><small>importes como números</small></td><td><span class="ok">Exacta</span><small>importes como números</small></td><td>Correcta<small>importes como texto («412,40 €»)</small></td><td>Correcta<small>importes como texto («412,40 €»)</small></td></tr>
          <tr><th>Problema de razonamiento</th><td><span class="ok">Correcto</span></td><td><span class="ok">Correcto</span><small>y explica los pasos</small></td><td><span class="no">Sin respuesta</span><small>agotó 1.500 tokens pensando</small></td><td><span class="ok">Correcto</span></td></tr>
          <tr><th>Atención al cliente en español</th><td>Inventa detalles</td><td>Inventa detalles</td><td>Prudente<small>remite a recepción</small></td><td>Inventa detalles</td></tr>
        </tbody>
      </table>
    </div>

    <div class="callout">En esta pasada el mejor fue <strong>Bonsai 2</strong>: el más rápido, el que menos memoria ocupa y sin un solo fallo en las pruebas estructuradas. <strong>Saluki</strong> fue igual de preciso, pero más lento y tan justo de memoria que solo cupo con un contexto de 8.192 tokens. <strong>gpt-oss-20B con FreeToken</strong> sigue siendo rápido y admite más contexto, pero se dejó una herramienta sin llamar.</div>

    <p>Dos cosas no las resuelve ninguno. Como asistente de un alojamiento, tres de los cuatro <strong>se inventaron cómo entrar con la recepción cerrada</strong>: sin los datos reales delante no son fiables de cara al público. Y en el texto histórico, los tres modelos de 2 bits colaron algún dato falso.</p>
    <p class="figcap">Medición propia (octubre de 2026). Una sola pasada por prueba, así que es un indicio y no una evaluación. Contexto de 8.192 tokens en los tres modelos de llama.cpp y de 20.480 en FreeToken; temperatura 0,6, y 0 en herramientas y JSON; razonamiento desactivado salvo en la prueba de razonamiento. Bonsai 27B es la versión anterior de Bonsai 2 y está basada en un modelo más antiguo.</p>
  </section>

  <section>
    <h2>Ficha técnica</h2>""")

rep("""<footer>Lomastrend · Laboratorio de IA local · Medición propia, agosto 2026</footer>""",
    """<footer>Lomastrend · Laboratorio de IA local · Medición propia, agosto y octubre de 2026</footer>""")
shutil.copy2(P, f"{P}.bak-octubre-{time.strftime('%Y%m%d-%H%M%S')}")
open(P, "w").write(s); print("aplicado")
