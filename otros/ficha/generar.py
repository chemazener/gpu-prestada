#!/usr/bin/env python3
"""Genera la ficha pública «Qué modelo de IA local elegir para una RTX 3080»
(lomastrend.com/proyectos/freetoken-benchmark.html) a partir de la versión anterior,
de la que reutiliza los estilos y las dos gráficas de FreeToken frente a Ollama.

Uso: generar.py <ficha_anterior.html> <salida.html>
"""
import sys

old = open(sys.argv[1]).read()
style = old[old.index('<style>'):old.index('</style>') + len('</style>')]


def block(title):
    i = old.index('<div class="chart">\n      <h3>' + title)
    j = old.index('</div>\n    </div>', i) + len('</div>\n    </div>')
    return old[i:j]


ch_motor = block('Velocidad de generación <span class="hint">(tokens/s · más es mejor)</span>').replace(
    '<h3>Velocidad de generación <span class="hint">(tokens/s · más es mejor)</span></h3>',
    '<h3>gpt-oss-20B: mismo modelo, dos motores <span class="hint">(tokens/s · más es mejor)</span></h3>')
ch_reparto = block('Dónde se ejecuta el modelo')


def barras(filas, px_por_unidad, marcas, aria, limite=None, dentro=False):
    """Barras horizontales de una sola serie: un solo color, valor rotulado, eje discreto."""
    alto = 22 + 40 * len(filas) + 28
    o = [f'<svg viewBox="0 0 600 {alto}" width="100%" role="img" aria-label="{aria}">',
         f'  <line x1="190" y1="14" x2="190" y2="{alto - 31}" stroke="#22304a" stroke-width="1"/>']
    for i, (nombre, valor, rotulo, ayuda) in enumerate(filas):
        y = 22 + 40 * i
        w = round(valor * px_por_unidad)
        o += [f'  <text x="180" y="{y + 17}" text-anchor="end" fill="#e5eef7" font-size="14" font-weight="700">{nombre}</text>',
              f'  <rect x="190" y="{y}" width="{w}" height="24" rx="4" fill="#38bdf8"><title>{ayuda}</title></rect>',
              # con línea de límite, el valor va dentro de la barra para no chocar con ella
              (f'  <text x="{190 + w - 9}" y="{y + 17}" text-anchor="end" fill="#052537" font-size="14" font-weight="800">{rotulo}</text>' if dentro
               else f'  <text x="{190 + w + 8}" y="{y + 17}" fill="#e5eef7" font-size="14" font-weight="800">{rotulo}</text>')]
    eje = ''.join(f'<text x="{190 + round(t * px_por_unidad)}" y="{alto - 15}">{t}</text>' for t in marcas)
    if limite:
        valor, rotulo = limite
        x = 190 + round(valor * px_por_unidad)
        o.append(f'  <line x1="{x}" y1="8" x2="{x}" y2="{alto - 31}" stroke="#8b98b0" stroke-width="1" stroke-dasharray="4 4"><title>Límite de la tarjeta: {rotulo}</title></line>')
        eje += f'<text x="{x}" y="{alto - 15}" fill="#8b98b0">{rotulo}</text>'
    o.append(f'  <g fill="#4a5670" font-size="10" text-anchor="middle">{eje}</g>')
    return '\n        '.join(o) + '\n      </svg>'


svg_vel = barras(
    [('Bonsai 2 27B', 77.3, '77', 'Bonsai 2 27B: 77,3 tokens/s'),
     ('Gemma 4 12B', 76.5, '77', 'Gemma 4 12B: 76,5 tokens/s'),
     ('gpt-oss-20B · FreeToken', 63.6, '64*', 'gpt-oss-20B con FreeToken: 63,6 tokens/s de extremo a extremo'),
     ('Bonsai 27B', 56.3, '56', 'Bonsai 27B: 56,3 tokens/s'),
     ('Saluki 27B', 43.1, '43', 'Saluki 27B: 43,1 tokens/s')],
    390 / 80, (0, 20, 40, 60, 80),
    'Velocidad de generación: Bonsai 2 y Gemma 4 12B 77 tokens por segundo, gpt-oss-20B con FreeToken 64, Bonsai 27B 56 y Saluki 27B 43')

svg_mem = barras(
    [('Bonsai 2 27B', 7.0, '7,0 GB', 'Bonsai 2 27B: 7,0 GB'),
     ('Gemma 4 12B', 7.9, '7,9 GB', 'Gemma 4 12B: 7,9 GB'),
     ('Bonsai 27B', 8.1, '8,1 GB', 'Bonsai 27B: 8,1 GB'),
     ('gpt-oss-20B · FreeToken', 9.3, '9,3 GB', 'gpt-oss-20B con FreeToken: 9,3 GB'),
     ('Saluki 27B', 9.4, '9,4 GB', 'Saluki 27B: entre 9,3 y 9,5 GB')],
    330 / 10, (0, 2, 4, 6, 8),
    'Memoria de la gráfica ocupada, sobre 10 GB: Bonsai 2 7,0, Gemma 4 12B 7,9, Bonsai 27B 8,1, gpt-oss-20B con FreeToken 9,3 y Saluki 27B 9,4',
    limite=(10, '10 GB'), dentro=True)

html = f'''<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Qué modelo de IA local elegir para una RTX 3080 — Lomastrend</title>
<meta name="description" content="Comparativa medida en casa de cinco modelos de IA local en una RTX 3080 de 10 GB: Gemma 4 12B, Bonsai 2 27B, Saluki 27B, Bonsai 27B y gpt-oss-20B con FreeToken. Velocidad, memoria, llamadas a herramientas, extracción de datos y razonamiento.">
<link rel="stylesheet" href="/proyectos/proyecto.css">
{style}
</head>
<body>
<div class="wrap">
  <a class="back" href="/#portfolio">&larr; Volver al portfolio</a>

  <header class="hero">
    <div class="eyebrow">Banco de pruebas · IA local</div>
    <h1>Qué modelo de IA local elegir para una RTX 3080</h1>
    <p class="lead">Cinco modelos, la <strong>misma tarjeta de 10 GB</strong> y las <strong>mismas seis peticiones</strong> a cada uno. El resultado no es el esperado: un modelo clásico de 12.000 millones de parámetros <strong>empata en velocidad</strong> con el mejor de los nuevos de 27.000 millones comprimidos a 2 bits, y el más comentado de estos últimos es el más lento y el que más memoria gasta.</p>
    <div class="tags">
      <span class="tag">RTX 3080 (10 GB)</span><span class="tag">Gemma 4 12B</span><span class="tag">Bonsai 2 27B</span><span class="tag">Saluki 27B</span>
      <span class="tag">gpt-oss-20B</span><span class="tag">llama.cpp</span><span class="tag">FreeToken</span><span class="tag">Medido en casa</span>
    </div>
  </header>

  <div class="metrics">
    <div class="metric"><div class="k">Los más rápidos</div><div class="v win">77<small> tok/s</small></div><div class="s">Bonsai 2 y Gemma 4 12B</div></div>
    <div class="metric"><div class="k">Menos memoria</div><div class="v win">7,0<small> GB</small></div><div class="s">Bonsai 2, con 27B</div></div>
    <div class="metric"><div class="k">Sin fallos</div><div class="v">3<small> de 5</small></div><div class="s">en herramientas, datos y razonamiento</div></div>
    <div class="metric"><div class="k">Fiables de cara al público</div><div class="v">0<small> de 5</small></div><div class="s">sin darles los datos reales</div></div>
  </div>

  <section>
    <h2>El problema</h2>
    <p>Una <strong>RTX 3080 tiene 10 GB de memoria</strong>, y eso decide casi todo. Un modelo de lenguaje solo va rápido si cabe entero en la tarjeta, junto con el espacio que necesita para recordar la conversación (el <em>contexto</em>). Lo que no cabe se ejecuta en el procesador, mucho más despacio.</p>
    <p>Hasta hace poco, eso dejaba el techo en modelos de unos 12.000 millones de parámetros. En los últimos meses han aparecido dos caminos para saltárselo:</p>
    <ul>
      <li><strong>Comprimir más.</strong> Modelos de 27.000 millones de parámetros reducidos a unos 2 bits por peso: pasan de 54 GB a entre 6 y 8 GB y entran enteros en la tarjeta.</li>
      <li><strong>Cambiar el motor.</strong> Dejar el modelo en la memoria del sistema y llevar a la tarjeta solo las partes que hacen falta en cada momento.</li>
    </ul>
    <div class="callout">La pregunta: con 10 GB, ¿compensa un modelo grande muy comprimido frente a uno mediano bien hecho? ¿Y cuál de todos conviene para cada tarea?</div>
  </section>

  <section>
    <h2>Los cinco candidatos</h2>
    <div class="tw">
      <table class="cmp">
        <thead><tr><th></th><th>Qué es</th><th>Tamaño</th><th>Motor</th></tr></thead>
        <tbody>
          <tr><th>Gemma 4 12B</th><td>12.000 millones de parámetros, cuantización clásica de 4 bits<small>el modelo «de siempre» para esta tarjeta</small></td><td>6,7 GB</td><td>llama.cpp</td></tr>
          <tr><th>Bonsai 2 27B</th><td>Qwen3.8-27B con pesos ternarios, unos 1,75 bits por peso</td><td>5,95 GB</td><td>llama.cpp<small>versión de PrismML</small></td></tr>
          <tr><th>Saluki 27B</th><td>Qwen3.8-27B a unos 2 bits, afinado para llamar a herramientas</td><td>7,9 GB</td><td>llama.cpp</td></tr>
          <tr><th>Bonsai 27B</th><td>La generación anterior de Bonsai, sobre Qwen3.6-27B</td><td>7,2 GB</td><td>llama.cpp<small>versión de PrismML</small></td></tr>
          <tr><th>gpt-oss-20B</th><td>20.000 millones de parámetros, de tipo «mezcla de expertos»<small>no cabe en la tarjeta</small></td><td>~13 GB</td><td>FreeToken</td></tr>
        </tbody>
      </table>
    </div>
  </section>

  <section>
    <h2>La prueba</h2>
    <p>A cada modelo, en solitario y con la tarjeta para él solo, las mismas seis peticiones:</p>
    <ul>
      <li><strong>Un texto largo</strong> en español, para medir la velocidad.</li>
      <li><strong>Atención al cliente:</strong> un huésped que llega con la recepción cerrada y pide factura de empresa.</li>
      <li><strong>Dos herramientas en una frase:</strong> consultar disponibilidad y, de paso, enviar un mensaje.</li>
      <li><strong>Un problema de razonamiento</strong> con porcentajes y redondeos.</li>
      <li><strong>Una factura a JSON:</strong> proveedor, CIF, fecha, base, IVA y total.</li>
      <li>Y un saludo corto de calentamiento.</li>
    </ul>
  </section>

  <section>
    <h2>Velocidad y memoria</h2>

    <div class="chart">
      <h3>Velocidad de generación <span class="hint">(tokens/s en el texto largo · más es mejor)</span></h3>
      {svg_vel}
      <div class="legend"><span>* FreeToken se midió de extremo a extremo, incluido su razonamiento interno; su velocidad real al generar es algo mayor.</span></div>
    </div>

    <div class="chart">
      <h3>Memoria de la gráfica ocupada <span class="hint">(GB con un contexto de 8.192 tokens · menos es mejor)</span></h3>
      {svg_mem}
      <div class="legend"><span>La línea discontinua es el límite de la tarjeta. Lo que queda hasta ella es margen para un contexto más largo.</span></div>
    </div>

    <p>Dos lecturas. La primera: <strong>Bonsai 2 y Gemma 4 12B empatan a 77 tokens por segundo</strong>, aunque uno tiene más del doble de parámetros que el otro. La segunda: <strong>la memoria separa más que la velocidad</strong>. Bonsai 2 deja 3 GB libres; Saluki ocupa casi toda la tarjeta y con un contexto de 16.384 tokens se quedó sin memoria en la primera petición.</p>
  </section>

  <section>
    <h2>Quién acierta</h2>
    <div class="tw">
      <table class="cmp">
        <thead><tr><th></th><th>Gemma 4 12B</th><th>Bonsai 2 27B</th><th>Saluki 27B</th><th>Bonsai 27B</th><th>gpt-oss-20B</th></tr></thead>
        <tbody>
          <tr><th>Velocidad</th><td>77 tok/s</td><td>77 tok/s</td><td>43 tok/s</td><td>56 tok/s</td><td>64 tok/s*</td></tr>
          <tr><th>Arranque en frío</th><td>14 s</td><td>10 s</td><td>35 s</td><td>12 s</td><td>95 s</td></tr>
          <tr><th>Memoria de la gráfica</th><td>7,9 GB</td><td>7,0 GB</td><td>9,3–9,5 GB</td><td>8,1 GB</td><td>9,3 GB</td></tr>
          <tr><th>Dos herramientas en una frase</th><td><span class="ok">Las dos</span></td><td><span class="ok">Las dos</span></td><td><span class="ok">Las dos</span></td><td><span class="ok">Las dos</span></td><td><span class="no">Solo una</span><small>olvidó el segundo encargo</small></td></tr>
          <tr><th>Factura a JSON</th><td><span class="ok">Exacta</span><small>importes como números</small></td><td><span class="ok">Exacta</span><small>importes como números</small></td><td><span class="ok">Exacta</span><small>importes como números</small></td><td>Correcta<small>importes como texto («412,40 €»)</small></td><td>Correcta<small>importes como texto («412,40 €»)</small></td></tr>
          <tr><th>Problema de razonamiento</th><td><span class="ok">Correcto</span><small>y explica los pasos</small></td><td><span class="ok">Correcto</span><small>y explica los pasos</small></td><td><span class="ok">Correcto</span></td><td><span class="no">Sin respuesta</span><small>agotó 1.500 tokens pensando</small></td><td><span class="ok">Correcto</span></td></tr>
          <tr><th>Atención al cliente en español</th><td>Da por hecho un código de acceso</td><td>Da por hecho un código de acceso</td><td>Se inventa una caja fuerte con códigos</td><td>Prudente<small>remite a recepción</small></td><td>Se inventa una recepción nocturna de pago</td></tr>
        </tbody>
      </table>
    </div>
    <p>Tres modelos pasan sin fallos las pruebas estructuradas: <strong>Gemma 4 12B, Bonsai 2 y Saluki</strong>. Los otros dos tropiezan en algo concreto: el Bonsai antiguo se quedó pensando sin llegar a contestar, y gpt-oss-20B hizo una de las dos tareas que se le pidieron y dejó la otra sin hacer.</p>
  </section>

  <section>
    <h2>Cuál elegir</h2>
    <ul>
      <li><strong>Para casi todo: Gemma 4 12B.</strong> Igual de rápido que el mejor de los grandes, sin fallos en las pruebas y con el motor estándar, sin instalar nada especial.</li>
      <li><strong>Para agentes y conversaciones largas: Bonsai 2.</strong> Un modelo de 27B que ocupa menos que el de 12B y deja 3 GB para contexto. A cambio necesita una versión propia de llama.cpp.</li>
      <li><strong>Saluki</strong> acierta lo mismo que Bonsai 2, pero va casi a la mitad de velocidad y sin margen de memoria. En una tarjeta de 10 GB no hay motivo para preferirlo; con 12 o 16 GB la cuenta puede cambiar.</li>
      <li><strong>gpt-oss-20B con FreeToken</strong> es la opción cuando interesa ese modelo en concreto o un contexto largo (20.480 tokens en esta prueba), sabiendo que tarda más de un minuto y medio en arrancar.</li>
      <li><strong>Bonsai 27B</strong> queda superado por su sucesor en todo lo medido salvo en prudencia.</li>
    </ul>
    <div class="callout">La conclusión incómoda: comprimir un modelo grande a 2 bits <strong>ya no es peor</strong> que usar uno mediano, pero en una tarjeta de 10 GB <strong>tampoco es claramente mejor</strong>. Lo que se gana es margen de memoria, no velocidad ni acierto.</div>
  </section>

  <section>
    <h2>Lo que ninguno resuelve</h2>
    <p>Como asistente de un alojamiento, <strong>cuatro de los cinco respondieron con algo que nadie les había dicho</strong>: un código de acceso, una caja fuerte, una recepción nocturna de pago. Solo uno remitió a recepción. Sin los datos reales delante, ninguno es fiable de cara al público; la solución no es cambiar de modelo, sino darle esos datos en la petición.</p>
    <p>En el texto histórico pasó algo parecido: los tres modelos de 2 bits colaron un dato falso ya en el primer párrafo, sea un apodo de la ciudad que no existe o una etimología inventada.</p>
  </section>

  <section>
    <h2>El motor también cuenta</h2>
    <p>El mismo modelo no rinde igual en todos los programas. Lo medimos de dos formas.</p>
    <p><strong>Cuando el modelo no cabe.</strong> gpt-oss-20B ocupa unos 13 GB. Ollama lo reparte entre la tarjeta y el procesador, y ese 43 % que cae en el procesador frena todo. FreeToken deja todos los pesos en la memoria del sistema y lleva a la tarjeta solo los «expertos» que hacen falta en cada paso, de modo que el cálculo se queda entero en la gráfica.</p>
    {ch_motor}
    {ch_reparto}
    <p><strong>Cuando el modelo sí cabe.</strong> Con Gemma 4 12B y exactamente el mismo fichero, llama.cpp dio 74 tokens por segundo frente a 63 de Ollama: un <strong>18 % más</strong>. Ollama usa llama.cpp por dentro, pero con ajustes más conservadores.</p>
  </section>

  <section>
    <h2>Ficha técnica</h2>
    <table class="spec">
      <tr><td>Tarjeta</td><td>NVIDIA RTX 3080 · 10 GB</td></tr>
      <tr><td>Motores</td><td>llama.cpp · llama.cpp de PrismML · FreeToken 0.1.3 · Ollama 0.35</td></tr>
      <tr><td>Contexto</td><td>8.192 tokens en los cuatro modelos de llama.cpp · 20.480 en FreeToken</td></tr>
      <tr><td>Muestreo</td><td>Temperatura 0,6 · 0 en herramientas y JSON</td></tr>
      <tr><td>Razonamiento</td><td>Desactivado, salvo en la prueba de razonamiento</td></tr>
      <tr><td>FreeToken frente a Ollama</td><td>51 frente a 24 tokens/s con gpt-oss-20B (agosto de 2026)</td></tr>
      <tr><td>llama.cpp frente a Ollama</td><td>74 frente a 63 tokens/s con Gemma 4 12B (octubre de 2026)</td></tr>
    </table>
    <p class="figcap">Medición propia en el laboratorio de Lomastrend. La comparativa de los cinco modelos es de octubre de 2026 y consta de <strong>una sola pasada por prueba</strong>: es un indicio, no una evaluación exhaustiva. Las comparativas de motores usan la mediana de tres pasadas con 256 tokens de salida. Los números dependen del equipo; en tarjetas con más memoria el orden puede cambiar.</p>
  </section>

  <div class="cta">
    <h2>¿El objetivo?</h2>
    <p>Ejecutar modelos grandes de verdad en hardware de andar por casa, sin pagar por token a una nube. Este banco de pruebas forma parte del laboratorio de IA local de Lomastrend.</p>
    <a class="btn" href="/#portfolio">Ver más proyectos</a>
  </div>

  <footer>Lomastrend · Laboratorio de IA local · Medición propia, agosto y octubre de 2026</footer>
</div>
</body>
</html>
'''
open(sys.argv[2], 'w').write(html)
print(len(html), 'bytes ·', html.count('<section'), 'secciones ·', html.count('<svg'), 'gráficas')
