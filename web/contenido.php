<?php
// Contenido de la página de GPU Prestada. Lo incluye index.php tras el login;
// el acceso directo a este fichero está denegado en .htaccess.
$ver = @json_decode(@file_get_contents(__DIR__ . '/version.json'), true) ?: [];
$version = htmlspecialchars($ver['version'] ?? '—', ENT_QUOTES, 'UTF-8');
$fecha   = htmlspecialchars($ver['fecha'] ?? '', ENT_QUOTES, 'UTF-8');
$mb      = isset($ver['bytes']) ? number_format($ver['bytes'] / 1048576, 1, ',', '.') . ' MB' : '';
$sha     = htmlspecialchars(substr($ver['sha256'] ?? '', 0, 16), ENT_QUOTES, 'UTF-8');
?>
<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>GPU Prestada · LoMasTrend</title>
<link rel="icon" href="/img/favicon-32.png">
<style>
  :root { --bg:#06060a; --card:#12121a; --line:#24243a; --text:#f0f0f5; --dim:#9a9aad;
          --violet:#8b5cf6; --emerald:#10b981; --amber:#f59e0b; --orange:#f97316; --red:#ef4444; }
  * { box-sizing:border-box; }
  html { scroll-behavior:smooth; }
  body { margin:0; background:var(--bg); color:var(--text); font:16px/1.6 "Segoe UI",system-ui,-apple-system,sans-serif; }
  a { color:#a78bfa; } a:focus-visible, summary:focus-visible { outline:2px solid #fff; outline-offset:2px; }
  .wrap { max-width:1000px; margin:0 auto; padding:0 clamp(14px,3vw,28px) 48px; }
  .top { display:flex; justify-content:space-between; align-items:center; gap:10px; padding:14px 0; font-size:14px; flex-wrap:wrap; }
  .top a { color:var(--dim); text-decoration:none; } .top a:hover { color:var(--text); }
  .hero { display:grid; grid-template-columns:auto 1fr; gap:clamp(14px,3vw,28px); align-items:center; padding:clamp(12px,3vw,28px) 0 8px; }
  .hero img { width:clamp(84px,16vw,150px); height:auto; }
  h1 { font-size:clamp(28px,5vw,44px); line-height:1.1; margin:0 0 6px; }
  .hero p { margin:0; color:#d5d5e2; font-size:clamp(15px,2vw,18px); }
  .cta { display:flex; flex-wrap:wrap; gap:12px 18px; align-items:center; margin:20px 0 6px; }
  .btn { display:inline-block; background:var(--violet); color:#fff; text-decoration:none; font-weight:600; padding:13px 22px; border-radius:11px; }
  .btn:hover { filter:brightness(1.1); }
  .btn.sec { background:#22223a; }
  .meta { color:var(--dim); font-size:13px; overflow-wrap:anywhere; }
  nav.idx { display:flex; flex-wrap:wrap; gap:8px; margin:22px 0 4px; }
  nav.idx a { font-size:13px; color:var(--text); text-decoration:none; background:var(--card); border:1px solid var(--line); border-radius:20px; padding:5px 12px; }
  h2 { font-size:clamp(20px,3vw,26px); margin:38px 0 12px; padding-top:8px; }
  h3 { font-size:17px; margin:22px 0 8px; }
  p { margin:0 0 12px; } li { margin-bottom:6px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:16px 18px; margin:12px 0; }
  .steps { counter-reset:s; list-style:none; padding:0; margin:0; display:grid; gap:10px; }
  .steps li { counter-increment:s; background:var(--card); border:1px solid var(--line); border-radius:12px; padding:14px 16px 14px 56px; position:relative; margin:0; }
  .steps li::before { content:counter(s); position:absolute; left:14px; top:14px; width:28px; height:28px; border-radius:50%;
    background:var(--violet); color:#fff; font-weight:700; display:grid; place-items:center; font-size:14px; }
  figure { margin:14px 0; } figure img { max-width:100%; height:auto; border-radius:10px; border:1px solid var(--line); display:block; }
  figcaption { color:var(--dim); font-size:13px; margin-top:6px; }
  code { background:#1c1c2b; border-radius:5px; padding:1px 6px; font:13.5px/1.5 Consolas,"SF Mono",monospace; overflow-wrap:anywhere; }
  pre { background:#0c0c13; border:1px solid var(--line); border-radius:10px; padding:12px 14px; overflow-x:auto; font:13px/1.5 Consolas,"SF Mono",monospace; margin:10px 0; }
  .tw { overflow-x:auto; margin:12px 0; border:1px solid var(--line); border-radius:10px; }
  table { border-collapse:collapse; width:100%; font-size:14.5px; min-width:440px; }
  th, td { text-align:left; padding:8px 12px; border-bottom:1px solid var(--line); vertical-align:top; }
  th { color:var(--dim); font-weight:600; font-size:13px; background:#0e0e16; } tr:last-child td { border-bottom:0; }
  td.n { font-variant-numeric:tabular-nums; white-space:nowrap; }
  .sw { display:inline-block; width:11px; height:11px; border-radius:3px; margin-right:7px; vertical-align:-1px; }
  .warn { border-left:3px solid var(--amber); }
  .two { display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,400px),1fr)); gap:12px; }
  .two .card { margin:0; }
  footer { color:var(--dim); font-size:13px; margin-top:40px; border-top:1px solid var(--line); padding-top:14px; }
  @media (max-width:520px) { .hero { grid-template-columns:1fr; } body { font-size:15px; } .steps li { padding-left:50px; } }
</style>
</head>
<body>
<div class="wrap">
  <div class="top"><a href="/">← LoMasTrend</a><a href="/vram/">Monitor VRAM →</a></div>

  <header class="hero">
    <img src="img/logo.png" alt="LoMasTrend">
    <div>
      <h1>GPU Prestada</h1>
      <p>Convierte cualquier PC con Windows o Linux (tarjeta NVIDIA, AMD Radeon o Intel) en una gráfica más para la inteligencia artificial de LoMasTrend y, a la vez, deja que ese PC use las gráficas del servidor desde su propio Ollama.</p>
    </div>
  </header>
  <div class="cta">
    <a class="btn" href="descargar.php">Descargar GPU Prestada para Windows</a>
    <a class="btn" href="descargar.php?os=linux-amd64" style="background:#1f2937;border:1px solid #444">Descargar para Linux (64 bits)</a>
    <a class="btn" href="descargar.php?os=linux-arm64" style="background:#111;border:1px solid #444">Linux ARM (Raspberry Pi 5, Jetson…)</a>
    <a class="btn" href="https://ollama.com/download/windows" rel="noopener" style="background:#111;border:1px solid #444">Descargar Ollama para Windows</a>
    <a class="btn sec" href="/vram/">Ver las tarjetas en el Monitor VRAM</a>
    <span class="meta">Versión <?= $version ?><?= $mb ? " · $mb" : '' ?><?= $fecha ? " · publicada el $fecha" : '' ?><?= $sha ? " · SHA-256 {$sha}…" : '' ?></span>
  </div>

  <nav class="idx" aria-label="Contenido">
    <a href="#que">Qué hace</a><a href="#instalar">Instalación</a><a href="#icono">Ventana e icono</a>
    <a href="#linux">Linux</a><a href="#cliente">Usar la IA del servidor</a><a href="#alta">Alta de un PC</a><a href="#monitor">Control desde el Monitor</a>
    <a href="#radeon">AMD e Intel</a><a href="#energia">Energía</a><a href="#reparto">Reparto del trabajo</a><a href="#actualizar">Actualizaciones</a>
    <a href="#gestor">Gestor de VRAM</a><a href="#donde">Dónde está cada cosa</a>
  </nav>

  <h2 id="que">Qué hace</h2>
  <p>El servidor de IA tiene una sola tarjeta, una RTX 3080 de 10 GB, que se reparten los modelos de lenguaje, la voz, la transcripción y la generación de imágenes y vídeo. GPU Prestada añade las tarjetas de otros equipos: cuando el servidor tiene trabajo de lenguaje (Ollama), puede enviárselo a ese PC para que lo resuelva su gráfica.</p>
  <p>Y funciona <b>en los dos sentidos</b>: si el administrador lo permite, el PC que presta su tarjeta también puede usar las del servidor. Los modelos de la 3080 y de las demás tarjetas prestadas aparecen en el Ollama de ese PC como si estuvieran instalados en él.</p>
  <div class="two">
    <div class="card"><h3 style="margin-top:0">No te quita la tarjeta</h3><p style="margin:0">Si la gráfica pasa del 50 % de uso por algo tuyo (un juego, edición de vídeo…), deja de prestarla sola y vuelve cuando lleva un minuto libre. También se puede pausar con un botón.</p></div>
    <div class="card"><h3 style="margin-top:0">Es privado</h3><p style="margin:0">Con Tailscale, solo acepta conexiones de esa red privada y con una clave que obtiene sola del servidor. Sin Tailscale, es el programa el que se conecta a LoMasTrend por un canal cifrado con un código de alta. En ningún caso abre puertos a Internet.</p></div>
    <div class="card"><h3 style="margin-top:0">Nunca rompe nada</h3><p style="margin:0">Si el PC está apagado, en pausa o falla, la petición la atiende la 3080 como siempre, sin error para la aplicación que la pidió.</p></div>
    <div class="card"><h3 style="margin-top:0">Presta y usa</h3><p style="margin:0">Cualquier programa del PC que hable con Ollama (Open WebUI, extensiones de programación, tus scripts) ve sus modelos de siempre y, además, los del servidor, acabados en <code>@servidor</code> o en el nombre de otra tarjeta.</p></div>
    <div class="card"><h3 style="margin-top:0">NVIDIA, AMD e Intel</h3><p style="margin:0">Con NVIDIA lee la tarjeta con sus herramientas. Con Radeon o Intel la lee como el Administrador de tareas de Windows, así que sirve cualquier gráfica, también las integradas de los portátiles.</p></div>
    <div class="card"><h3 style="margin-top:0">Las tarjetas no se suman</h3><p style="margin:0">Un modelo corre entero en una tarjeta o en otra. Lo que se gana es hacer dos cosas a la vez y, si la otra tarjeta tiene más memoria, modelos más grandes.</p></div>
  </div>

  <h2 id="instalar">Instalación</h2>
  <p>Hacen falta dos programas: el motor de IA <b>Ollama</b> y <b>GPU Prestada</b>.</p>
  <ol class="steps">
    <li><b>Instala Ollama</b>, el motor que ejecuta los modelos en la tarjeta: <a href="https://ollama.com/download/windows" rel="noopener">Descargar Ollama para Windows</a> (OllamaSetup.exe; siguiente, siguiente y listo). Si te lo saltas, GPU Prestada lo descarga e instala sola en el primer arranque, pero tarda unos minutos más.</li>
    <li><b>Descarga y ejecuta GPU Prestada</b> con el botón de arriba. Windows avisará de que es un programa sin firmar («Más información» → «Ejecutar de todas formas»). No hay que instalar nada más.</li>
    <li><b>Espera el alta.</b> El programa avisa al servidor de que quiere entrar y el PC aparece en el Monitor VRAM, en «Equipos esperando alta». En cuanto el administrador pulsa «Dar de alta», el código llega solo y la ventana pasa al paso siguiente. También vale pegar el código a mano en la casilla de la ventana y pulsar «Guardar» (ver <a href="#alta">Alta de un PC</a>). Todo va por un canal cifrado saliente (puerto 443, como una web): no hay que tocar el router ni el cortafuegos.</li>
    <li><b>Espera a que termine sola.</b> Comprueba Ollama (lo instala si falta), descarga los modelos que el servidor le pida, se da de alta y queda configurada para arrancar con Windows. La ventana va mostrando cada paso con su porcentaje.</li>
  </ol>
  <p class="meta" style="margin-top:10px"><b>Si el PC ya está en la red Tailscale</b>, no hace falta código: el programa lo detecta, se da de alta solo y el servidor se conecta a él directamente por esa red. En ese caso Windows pedirá permiso de administrador una vez, para abrir el cortafuegos solo a la red Tailscale.</p>
  <p class="meta" style="margin-top:10px">Requisitos: Windows 10 u 11 de 64 bits, una tarjeta gráfica NVIDIA, AMD Radeon o Intel con sus controladores, <a href="https://ollama.com/download/windows" rel="noopener">Ollama</a> y unos 10 GB libres en disco para el motor y un modelo pequeño.</p>

  <h2 id="linux">En Linux</h2>
  <p>La versión de Linux es un solo programa, sin nada más que instalar aparte de Ollama. Hace lo mismo que la de Windows (prestar la tarjeta, usar la IA del servidor, alta desde el Monitor VRAM y actualizaciones automáticas), salvo Whisper y la generación de imágenes, que de momento solo van en Windows.</p>
  <ol class="steps">
    <li><b>Instala Ollama</b> si no lo tienes (pide la contraseña de administrador): <code>curl -fsSL https://ollama.com/install.sh | sh</code></li>
    <li><b>Descarga GPU Prestada</b> con el botón de arriba (64 bits para un PC normal, ARM para una Raspberry Pi 5 o similar) y ábrelo una vez desde la terminal: <code>chmod +x gpu-prestada &amp;&amp; ./gpu-prestada</code></li>
    <li><b>Ya está.</b> Se instala solo en <code>~/.local/bin</code>, queda como servicio de tu sesión (arranca al entrar, sin abrir nada), aparece «GPU Prestada» en el menú de aplicaciones y abre su ventana en el navegador. Si el PC no está en Tailscale, sale en «Equipos esperando alta» del Monitor VRAM.</li>
  </ol>
  <div class="tw"><table>
    <tr><th>En Linux</th><th>Cómo</th></tr>
    <tr><td>La ventana</td><td>En el navegador: <code>http://127.0.0.1:11436</code>, o desde «GPU Prestada» en el menú de aplicaciones</td></tr>
    <tr><td>Tarjeta</td><td>NVIDIA con <code>nvidia-smi</code> y AMD con <code>rocm-smi</code> (sus controladores)</td></tr>
    <tr><td>Ollama combinado</td><td><code>127.0.0.1:11438</code> (no toca el Ollama del sistema, que sigue en el 11434)</td></tr>
    <tr><td>Programas con IA</td><td>Se abren en la terminal del escritorio; «Acceso directo» crea un lanzador en el menú</td></tr>
    <tr><td>Cerrarlo del todo</td><td>«Cerrar el programa» en la ventana, o <code>systemctl --user stop gpu-prestada</code></td></tr>
    <tr><td>Desinstalar</td><td><code>gpu-prestada desinstalar</code> (y borrar <code>~/.config/gpu-prestada</code> si no se va a volver a usar)</td></tr>
  </table></div>

  <h3 id="shell">Desde la terminal, también en un servidor sin escritorio</h3>
  <p>Sirve igual por SSH en un servidor sin pantalla. Una sola orden, con tu usuario normal (no como root), descarga el programa, comprueba que viene del servidor de LoMasTrend y que no está dañado, lo instala y lo deja en marcha:</p>
<pre>curl -fsSL https://lomastrend.com/gpu-prestada/instalar.sh | sh</pre>
  <p>Si ya tienes el código de alta, se puede pasar al final y queda dado de alta a la vez:</p>
<pre>curl -fsSL https://lomastrend.com/gpu-prestada/instalar.sh | sh -s -- CÓDIGO</pre>
  <ul>
    <li><b>Sigue en marcha al cerrar la sesión SSH</b> y arranca con el equipo. Si el sistema no deja activarlo solo, el instalador dice la orden que falta: <code>sudo loginctl enable-linger $USER</code>.</li>
    <li><b>Ollama</b> se instala aparte, como siempre: <code>curl -fsSL https://ollama.com/install.sh | sh</code>.</li>
    <li><b>Para ver la ventana</b> desde otro equipo, abre un túnel y entra en <code>http://127.0.0.1:11436</code> en tu navegador: <code>ssh -L 11436:127.0.0.1:11436 usuario@servidor</code>.</li>
    <li><b>Menú de texto:</b> <code>gpu-prestada</code> en la terminal abre un menú que se maneja con las flechas: estado al momento, prestar o pausar, código de alta, registro y <b>«Programas con IA»</b>. Ahí se elige el programa (Claude Code, Codex, OpenCode…) y el modelo, agrupado por máquina, y se abre en la misma terminal. También puede dejar una orden fija, por ejemplo <code>ia-claude-qwen3-5-9b-32k-servidor</code>, para abrirlo directamente la próxima vez.</li>
    <li><b>Sin Ollama en el equipo</b> se puede usar igual la IA del servidor: solo hace falta para prestar la tarjeta. Sin el comando <code>ollama</code>, el menú abre Claude Code directamente; con él, todos los programas de <code>ollama launch</code>.</li>
  </ul>
  <div class="tw"><table>
    <tr><th>Orden</th><th>Qué hace</th></tr>
    <tr><td><code>gpu-prestada</code></td><td>El menú de texto (en un equipo con escritorio, la ventana)</td></tr>
    <tr><td><code>gpu-prestada estado</code></td><td>Cómo está: tarjeta, Ollama, conexión, si está dado de alta y si está prestando</td></tr>
    <tr><td><code>gpu-prestada codigo XXXX</code></td><td>Pega el código de alta (si no te dan de alta desde el Monitor VRAM)</td></tr>
    <tr><td><code>gpu-prestada pausar</code> · <code>reanudar</code></td><td>Deja de prestar la tarjeta, o vuelve a prestarla</td></tr>
    <tr><td><code>gpu-prestada parar</code></td><td>Cierra el programa hasta el próximo arranque del equipo</td></tr>
    <tr><td><code>gpu-prestada desinstalar</code></td><td>Quita el servicio y el programa</td></tr>
    <tr><td><code>gpu-prestada ayuda</code></td><td>La lista de órdenes</td></tr>
  </table></div>

  <h2 id="github">Instalar desde GitHub</h2>
  <p>El código está publicado en <a href="https://github.com/chemazener/gpu-prestada">github.com/chemazener/gpu-prestada</a> (README en inglés y en español). Hay una orden para cada lado:</p>
  <h3>El cliente (el PC que presta o usa las tarjetas) · Linux</h3>
<pre>curl -fsSL https://raw.githubusercontent.com/chemazener/gpu-prestada/main/web/instalar.sh | sh</pre>
  <p>Es el mismo instalador de arriba: baja el programa, comprueba su huella, lo instala como servicio de tu usuario y lo deja en marcha. Con el código de alta ya a mano: <code>… | sh -s -- CÓDIGO</code>. En Windows se descarga el instalador desde esta página.</p>
  <h3>El servidor (el relé para PCs sin Tailscale) · cualquier Linux</h3>
<pre>curl -fsSL https://raw.githubusercontent.com/chemazener/gpu-prestada/main/relay/instalar-relay.sh \
  | GPU_RELAY_BROKER=http://IP-DEL-PROXY-OLLAMA:11434 GPU_RELAY_NAME=relay.midominio.com sh</pre>
  <p>Instala el relé para tu usuario (entorno de Python, certificado propio de 10 años y servicio de systemd) y al terminar enseña la <b>huella</b> del certificado. Solo <code>GPU_RELAY_BROKER</code> es obligatoria; el resto de variables (puertos, direcciones y redes permitidas) están descritas en la cabecera del script.</p>
  <ul>
    <li><b>Es el único trozo del servidor que se instala solo.</b> El registro de equipos (<code>server/nodes.py</code>) y los parches se aplican sobre el proxy de Ollama, el vram-broker y el proxy de ComfyUI de este laboratorio, que no están en el repositorio; se despliegan a mano.</li>
    <li><b>Para usar tu propio relé</b> hay que compilar el agente con su huella (<code>defaultRelayPin</code>) y su dirección (<code>defaultRelay</code>) en <code>agent/tunnel.go</code>. El instalador del cliente de arriba descarga el programa de <i>este</i> servidor de LoMasTrend, no del tuyo.</li>
    <li><b>El relé escucha solo en el propio equipo</b> (puerto 8445): hay que publicarlo en el 443 con un proxy inverso o una redirección.</li>
  </ul>

  <h2 id="icono">La ventana y el icono</h2>
  <figure>
    <img src="img/ventana.png" alt="Ventana de GPU Prestada con el estado, los pasos de puesta en marcha y los medidores de la tarjeta" width="1500" height="960" loading="lazy">
    <figcaption>La ventana se adapta al tamaño. Se puede minimizar o cerrar: el programa sigue en el icono junto al reloj, y un clic en él la vuelve a abrir.</figcaption>
  </figure>
  <p>Los medidores son los mismos del Monitor VRAM, con los datos de la tarjeta de ese equipo: memoria usada y libre, carga, temperatura, consumo y trabajos atendidos. En muchos portátiles la tarjeta no informa del consumo ni del ventilador; esa casilla sale atenuada.</p>
  <h3>Cómo se lee el icono de la bandeja</h3>
  <figure>
    <img src="img/icono-bandeja.png" alt="Los seis estados del icono de bandeja sobre barra de tareas oscura y clara" width="610" height="300" loading="lazy">
    <figcaption>De izquierda a derecha: sin carga, carga baja, media, alta, en pausa y con problemas. Debajo de cada uno, a tamaño real.</figcaption>
  </figure>
  <div class="tw"><table>
    <tr><th>Barra de la derecha</th><th>Significado</th></tr>
    <tr><td>Hueca</td><td>El servidor no está usando la tarjeta</td></tr>
    <tr><td><span class="sw" style="background:var(--emerald)"></span>Pequeña, verde</td><td>Carga cedida de hasta el 50 %</td></tr>
    <tr><td><span class="sw" style="background:var(--orange)"></span>Media, naranja</td><td>Más del 50 %</td></tr>
    <tr><td><span class="sw" style="background:var(--red)"></span>Alta, roja</td><td>Más del 75 %</td></tr>
    <tr><td><span class="sw" style="background:var(--amber)"></span>Marco ámbar</td><td>En pausa, o cedida a ti porque la estás usando</td></tr>
    <tr><td>Logotipo atenuado</td><td>Falta algo: el alta en el servidor, Ollama o la conexión</td></tr>
  </table></div>
  <p>La barra solo sube con trabajo del servidor. Si la gráfica está ocupada por algo tuyo, no cuenta como carga cedida.</p>
  <p>La lista de pasos de la ventana incluye «IA del servidor en este PC»: dice si el administrador ha permitido que este PC use las gráficas del servidor y cómo usarlas.</p>

  <h2 id="cliente">Usar la IA del servidor desde tu PC</h2>
  <p>GPU Prestada ocupa el puerto normal de Ollama del PC, <code>127.0.0.1:11434</code>, y mueve el Ollama propio del equipo al <code>127.0.0.1:11439</code>. Así cualquier programa que ya use Ollama lo encuentra sin configurar nada, y ve una lista combinada:</p>
  <div class="tw"><table>
    <tr><th>En la lista de modelos</th><th>Quién lo atiende</th></tr>
    <tr><td><code>gemma4:e2b</code> (sin nada más)</td><td>La gráfica de tu propio PC, como siempre</td></tr>
    <tr><td><code>gemma4:e4b@servidor</code></td><td>La RTX 3080 del servidor</td></tr>
    <tr><td><code>gemma3:1b@nitro5-chem</code></td><td>Otra tarjeta prestada, la de ese PC</td></tr>
  </table></div>
  <p>Basta con elegir el modelo con su terminación. Ejemplo desde una terminal del propio PC:</p>
<pre>curl http://127.0.0.1:11434/api/generate -d "{\"model\":\"gemma4:e4b@servidor\",\"prompt\":\"Hola\"}"</pre>
  <ul>
    <li><b>Se activa por equipo</b>, desde la ficha de cada tarjeta en el Monitor VRAM («💻 Usar la IA del servidor desde este PC»). Viene <b>apagado</b>: mientras lo esté, el PC solo ve sus propios modelos.</li>
    <li><b>Tiene un límite</b> de peticiones a la vez por equipo (de 1 a 4, se elige en la misma ficha).</li>
    <li><b>Hace cola como cualquier aplicación.</b> Si la 3080 está ocupada (por ejemplo, generando un vídeo), la petición espera su turno; puede tardar unos minutos en empezar.</li>
    <li><b>Nunca se manda a sí misma.</b> Una petición que sale de un PC no vuelve a la tarjeta de ese mismo PC, ni pidiéndolo ni por desbordamiento.</li>
    <li><b>Funciona igual con o sin Tailscale.</b> Sin Tailscale va por el mismo canal cifrado del préstamo.</li>
    <li><b>El comando <code>ollama</code> de la terminal</b> habla con el Ollama propio (11439) y solo ve los modelos locales. Para que vea también los del servidor: <code>set OLLAMA_HOST=127.0.0.1:11434</code> antes de usarlo.</li>
    <li><b>Si el dueño del PC ya tenía su propio <code>OLLAMA_HOST</code></b>, GPU Prestada no toca nada y la IA del servidor queda aparte, en <code>http://127.0.0.1:11444</code>.</li>
    <li><b>Ojo si el Ollama del PC estaba abierto a la red</b> (opción «Expose Ollama to the network»): tras el cambio solo escucha dentro del propio equipo, y otras máquinas de la red dejan de llegar a él.</li>
  </ul>

  <h2 id="alta">Alta de un PC</h2>
  <p>Un PC con Tailscale se da de alta solo. Uno sin Tailscale necesita el visto bueno del administrador, de una de estas dos formas:</p>
  <ol class="steps">
    <li><b>Desde el Monitor VRAM (versión 1.10 o posterior).</b> Al abrir GPU Prestada, el PC sale arriba en el Monitor, en un aviso amarillo «🆕 Equipos esperando alta», con su nombre, su gráfica y su versión. «✅ Dar de alta» le envía el código y se conecta en unos segundos; «✖ Rechazar» lo ignora durante una hora. Si ya hay otra tarjeta con el mismo nombre, el aviso lo indica.</li>
    <li><b>Con el código a mano.</b> En el Monitor VRAM, pestaña Herramientas, sección «🔑 Código de alta»: se muestra con «Mostrar» y se copia con «Copiar». Se pega en la casilla «código de alta» de la ventana del PC y se pulsa «Guardar». En Linux sin escritorio: <code>gpu-prestada codigo XXXX</code>. Es la vía para un PC con una versión antigua, aunque basta con cerrar y volver a abrir GPU Prestada para que se ponga al día sola.</li>
  </ol>

  <h2 id="monitor">Control desde el Monitor VRAM</h2>
  <p>Cada tarjeta prestada tiene su pestaña en <a href="/vram/">vram.lomastrend.com</a>, con lo mismo que la de la 3080:</p>
  <ul>
    <li><b>Ficha</b>: estado, gráfica (indica si la memoria es compartida con el equipo), versión, dirección y modelos.</li>
    <li><b>Botones</b>: <b>⏸ Pausar / ▶ Reanudar</b> (deja de recibir trabajos sin desconectarse; mueve también el interruptor de la bandeja del PC), <b>⛔ Desconectar / 🔌 Volver a conectar</b> (el servidor deja de aceptarla) y <b>💻 Usar la IA del servidor desde este PC</b>, con su límite de peticiones a la vez.</li>
    <li><b>Estadísticas en vivo y del periodo</b>, y la evolución de las últimas 24 horas.</li>
    <li><b>Procesos en la GPU</b>: arriba solo los que de verdad calculan en la tarjeta (los de Ollama salen como «servidor»). Los programas de Windows que solo tienen la gráfica abierta para dibujar la pantalla (el explorador, el navegador…) quedan plegados en una línea que se puede desplegar.</li>
    <li><b>Servicios del broker</b>: los modelos que ese PC tiene cargados, si están trabajando, cuánta memoria usan (y cuánta va al procesador si no caben), cuándo se descargarán solos y un botón <b>«desalojar»</b> para soltarlos al momento.</li>
  </ul>

  <h2 id="radeon">Gráficas AMD Radeon e Intel</h2>
  <p>Sin una NVIDIA, GPU Prestada lee la gráfica como el Administrador de tareas: nombre, memoria, uso y procesos, en cualquier idioma de Windows. Temperatura y consumo no están disponibles y salen como «no disponible». En las gráficas integradas la memoria es RAM del equipo compartida, y así lo indica el Monitor.</p>
  <p>Ollama usa una Radeon o una Intel a través de Vulkan, que GPU Prestada deja activado. En las <b>gráficas integradas</b> de los portátiles, sin embargo, los modelos corren en el <b>procesador</b>, a propósito: en la prueba con un Surface Laptop 4 (Ryzen 7 con Radeon integrada), la versión de Ollama de ahora cerraba el motor al cargar <code>gemma4:e2b</code> en la gráfica, mientras que en el procesador da unos 26–29 tokens por segundo con <code>gemma3:1b</code>, casi como la GTX 1650 de 4 GB. En una gráfica integrada, además, la memoria es la misma RAM, así que no se gana espacio.</p>

  <h2 id="energia">Energía</h2>
  <p>Mientras el PC presta su tarjeta y está <b>enchufado</b>, GPU Prestada le pide a Windows que no se suspenda por inactividad, como hace un reproductor de vídeo; la pantalla se sigue apagando con normalidad. Con batería, o con el préstamo pausado, Windows vuelve a su comportamiento habitual.</p>
  <p>Hace falta porque muchos portátiles modernos no tienen la suspensión clásica sino el «modo de espera moderno»: parecen encendidos y con la red conectada, pero Windows congela los programas. Además, algunos fabricantes restablecen el plan de energía al arrancar (en el Acer Nitro 5 lo hace el servicio de Intel <code>esif_uf.exe</code>), y se pierde el «Suspender: nunca» que hubiera puesto el usuario. Si alguna vez interesa que se suspenda aunque esté prestando, se pone <code>"allow_sleep": true</code> en su <code>config.json</code>.</p>

  <h2 id="reparto">Cómo se reparte el trabajo</h2>
  <p>Todas las aplicaciones hablan con un único punto de entrada, el proxy de Ollama del servidor. Él decide a qué tarjeta va cada petición, así que las aplicaciones no cambian.</p>
  <div class="tw"><table>
    <tr><th>Modo</th><th>Qué va a una tarjeta remota</th></tr>
    <tr><td><b>Manual</b> (el actual)</td><td>Solo lo que se pide expresamente: el modelo escrito como <code>gemma4:e2b@nombre-del-pc</code>, o la cabecera <code>X-GPU-Node: nombre-del-pc</code>. Todo lo demás lo atiende la 3080.</td></tr>
    <tr><td><b>Automático</b></td><td>Los modelos de una lista (o todos) van a una tarjeta remota siempre que esté prestando y tenga el modelo. Si hay varias, a la que más memoria libre tenga.</td></tr>
  </table></div>
  <p><b>Desbordamiento automático.</b> Aunque el modo sea manual, si una conversación o generación de texto llega con la 3080 ocupada (por ejemplo, con un vídeo de ComfyUI) y no le dan turno en 4 segundos, la atiende una tarjeta prestada libre que tenga el mismo modelo o su equivalente pequeño (<code>gemma4:e4b</code> → <code>gemma4:e2b</code>, <code>gemma3:4b</code> → <code>gemma3:1b</code>). Lo decide la cola, no cada aplicación. La respuesta lleva además la cabecera <code>X-Spilled-From</code> con el modelo pedido. Se activa con <code>"spill": true</code> en <code>nodes.json</code>.</p>
  <p>En los dos modos, si la tarjeta elegida no responde en 8 segundos o devuelve un error, la petición se atiende en la 3080 y esa tarjeta se deja de intentar durante 20 segundos. La respuesta lleva la cabecera <code>X-Served-By</code> con el nombre de la tarjeta que la resolvió.</p>
  <p>El modo se cambia en el servidor, en <code>/etc/ollama-proxy/nodes.json</code>, y se aplica al instante:</p>
<pre>{
  "mode": "prefer",                 // "explicit" = manual
  "prefer_models": ["gemma4:12b"],  // o ["*"] para todos
  "want_models": ["gemma4:12b"]     // modelos que cada PC debe descargar
}</pre>
  <p>Referencia de velocidad con <code>gemma4:e2b</code>: unos 176 tokens por segundo en la RTX 3080 frente a unos 28 en una GTX 1650 de 4 GB, donde el modelo no cabe entero. Con una tarjeta tan pequeña compensa el modo manual; el automático tiene sentido a partir de 12 GB.</p>

  <h2 id="actualizar">Actualizaciones automáticas</h2>
  <p>Cada pocos segundos el programa compara su huella con la de la versión publicada en el servidor. Si difieren y no está atendiendo un trabajo, descarga la nueva, comprueba que es exactamente la publicada, se sustituye y se reinicia en la bandeja. Tarda menos de 10 segundos. Si la descarga falla, sigue con la versión que tenía y reintenta a los 10 minutos.</p>
  <p>Además, <b>nada más arrancar</b> (y luego cada hora) consulta la huella publicada y se actualiza aunque todavía no tenga código de alta: un ejecutable descargado hace meses se pone al día en el primer arranque. Los equipos apagados se actualizan al encenderse. El Monitor VRAM marca cada tarjeta remota con «actualización pendiente» mientras no esté al día.</p>

  <h2 id="gestor">El gestor de VRAM</h2>
  <p>La 3080 tiene 10 GB y una docena de servicios que quieren usarla. Sin coordinación, dos cargarían su modelo a la vez y uno fallaría por falta de memoria. El gestor (<i>vram-broker</i>) es el árbitro: ningún servicio carga un modelo sin pedirle turno antes.</p>

  <h3>Cómo funciona</h3>
  <ol>
    <li><b>Pedir turno.</b> Antes de usar la tarjeta, el servicio llama a <code>/acquire</code> diciendo quién es y cuánta memoria necesita. Recibe una clave (<i>token</i>).</li>
    <li><b>Desalojar.</b> Si no hay memoria libre suficiente, el gestor pide a otros servicios que descarguen su modelo, empezando por el que lleva más tiempo sin usarse.</li>
    <li><b>Trabajar y soltar.</b> Al terminar, el servicio llama a <code>/release</code> con su clave. Su modelo puede quedarse cargado: sigue siendo desalojable si otro lo necesita.</li>
  </ol>
  <p>Cada servicio tiene su propia cola de un solo turno: dos peticiones al mismo servicio se atienden una detrás de otra, y servicios distintos pueden convivir si caben.</p>

  <h3>Reglas especiales</h3>
  <ul>
    <li><b>Los modelos de lenguaje son exclusivos.</b> Al arrancar en frío desalojan todo lo demás, porque un modelo de 12B ocupa casi toda la tarjeta. Si el modelo se usó hace menos de 280 segundos se considera caliente y el turno se concede sin desalojar a nadie, para no añadir segundos a cada mensaje de una conversación.</li>
    <li><b>Turnos huérfanos.</b> Si un cliente muere sin soltar su turno, un vigilante lo libera al pasar el tiempo máximo de ese servicio (tabla de abajo). Revisa cada 30 segundos.</li>
    <li><b>Trabajos largos.</b> Un trabajo que dura más que ese tiempo máximo debe renovar su turno con <code>/renew</code>.</li>
    <li><b>Peticiones imposibles.</b> Lo que pide más memoria de la que tiene la tarjeta se recorta al total: o cabe tras desalojar, o falla con un error claro en vez de esperar para siempre.</li>
  </ul>

  <h3>Servicios registrados</h3>
  <div class="tw"><table>
    <tr><th>Servicio</th><th>Qué es</th><th>Memoria por defecto</th><th>Tiempo máximo del turno</th></tr>
    <tr><td><code>ollama</code></td><td>Modelos de lenguaje · exclusivo</td><td class="n">8.000 MB</td><td class="n">10 min</td></tr>
    <tr><td><code>bonsai</code></td><td>llama.cpp · exclusivo</td><td class="n">9.500 MB</td><td class="n">10 min</td></tr>
    <tr><td><code>freetoken</code>, <code>freetoken-qwen</code></td><td>FreeToken · exclusivos</td><td class="n">9.500 MB</td><td class="n">5 min</td></tr>
    <tr><td><code>comfyui</code></td><td>Imágenes (otra máquina, la CT 106)</td><td class="n">10.000 MB</td><td class="n">10 min</td></tr>
    <tr><td><code>wan2gp</code></td><td>Vídeo</td><td class="n">9.000 MB</td><td class="n">30 min</td></tr>
    <tr><td><code>indextts2</code></td><td>Voz</td><td class="n">7.500 MB</td><td class="n">5 min</td></tr>
    <tr><td><code>vibevoice_news</code></td><td>Voz para noticias</td><td class="n">4.500 MB</td><td class="n">5 min</td></tr>
    <tr><td><code>vibevoice</code></td><td>Voz</td><td class="n">4.000 MB</td><td class="n">5 min</td></tr>
    <tr><td><code>xtts</code></td><td>Voz clonada</td><td class="n">3.000 MB</td><td class="n">3 min</td></tr>
    <tr><td><code>f5</code></td><td>Voz</td><td class="n">2.200 MB</td><td class="n">3 min</td></tr>
    <tr><td><code>whisper</code></td><td>Transcripción</td><td class="n">1.000 MB</td><td class="n">3 min</td></tr>
  </table></div>
  <p class="meta">La memoria por defecto es una pista: cada petición puede pedir otra cantidad.</p>

  <h3>La API</h3>
  <p>Escucha en la CT 101, puerto <code>8090</code> (<code>http://100.72.138.100:8090</code> por Tailscale).</p>
  <div class="tw"><table>
    <tr><th>Llamada</th><th>Para qué</th></tr>
    <tr><td><code>POST /acquire</code> <code>{service, need_mb, timeout_s}</code></td><td>Pedir turno. Devuelve la clave, lo que esperó y qué servicios desalojó. Si no llega a tiempo, error 503.</td></tr>
    <tr><td><code>POST /release</code> <code>{token}</code></td><td>Soltar el turno. Repetirla no hace daño.</td></tr>
    <tr><td><code>POST /renew</code> <code>{token}</code></td><td>Alargar un turno en un trabajo largo.</td></tr>
    <tr><td><code>GET /state</code></td><td>Foto del estado: memoria libre y reservada, y por servicio si está cargado, ocupado y su cola.</td></tr>
    <tr><td><code>POST /preempt</code> <code>{service}</code></td><td>Forzar que un servicio descargue su modelo.</td></tr>
    <tr><td><code>POST /reset</code> <code>{confirm:true}</code></td><td>Purga total, para emergencias, sin reiniciar el servicio.</td></tr>
  </table></div>

  <h3>Quién pide turno por ti</h3>
  <p>Casi ninguna aplicación habla con el gestor directamente. Lo hacen dos intermediarios:</p>
  <ul>
    <li><b>Proxy de Ollama</b> (CT 101, puerto <code>11434</code>). Es la dirección que usan todas las aplicaciones. Pide turno antes de cada generación y lo suelta al acabar, desactiva el modo «pensar» de los modelos salvo que se pida, y decide si la petición va a una tarjeta remota. El Ollama real está detrás, en el puerto <code>11444</code>.</li>
    <li><b>Proxy de ComfyUI</b> (CT 106, puerto <code>8188</code>). Hace lo mismo para la generación de imágenes, calculando la memoria según el flujo de trabajo.</li>
  </ul>

  <h3>El Monitor VRAM</h3>
  <p>En <a href="/vram/">vram.lomastrend.com</a> se ve todo en vivo: memoria y carga de la 3080, las tarjetas remotas, los procesos que ocupan la gráfica, los servicios con su estado y una gráfica de las últimas 24 horas. Desde ahí se puede desalojar un servicio, arrancarlo o pararlo, y liberar toda la tarjeta de golpe. Para las tarjetas prestadas, ver <a href="#monitor">Control desde el Monitor VRAM</a>.</p>

  <h3>Reglas prácticas</h3>
  <div class="card warn">
    <ul style="margin:0;padding-left:20px">
      <li><b>Usar siempre el puerto 11434</b>, nunca el 11444. El 11444 se salta el arbitraje y da error de memoria si otro servicio tiene la tarjeta.</li>
      <li><b>La excepción:</b> para descargar o crear modelos (<code>ollama pull</code>, <code>ollama create</code>) hay que apuntar al 11444 con <code>OLLAMA_HOST=127.0.0.1:11444</code>; el proxy no reenvía la subida de ficheros.</li>
      <li><b>No probar el gestor con guiones que piden turno y mueren sin soltarlo.</b> El servicio queda ocupado hasta que caduca el turno y su cola espera.</li>
      <li><b>Si un servicio queda «ocupado» sin estar trabajando:</b> <code>systemctl restart vram-broker</code> en la CT 101 lo limpia al instante.</li>
      <li><b>Un modelo de 12B solo cabe forzando toda la carga en la tarjeta.</b> Usar las variantes con sufijo <code>-3080</code>; el nombre pelado deja parte en el procesador y va a 2–3 tokens por segundo.</li>
    </ul>
  </div>

  <h2 id="donde">Dónde está cada cosa</h2>
  <div class="tw"><table>
    <tr><th>Pieza</th><th>Máquina</th><th>Ruta o dirección</th></tr>
    <tr><td>Gestor de VRAM</td><td>CT 101</td><td><code>/opt/vram-broker/broker.py</code> · servicio <code>vram-broker</code> · puerto 8090</td></tr>
    <tr><td>Proxy de Ollama</td><td>CT 101</td><td><code>/opt/ollama-proxy/proxy.py</code> · servicio <code>ollama-proxy</code> · puerto 11434</td></tr>
    <tr><td>Registro de tarjetas remotas</td><td>CT 101</td><td><code>/opt/ollama-proxy/nodes.py</code> · estado en <code>:11434/_nodes</code> · ajustes en <code>/etc/ollama-proxy/nodes.json</code></td></tr>
    <tr><td>Monitor VRAM</td><td>CT 101</td><td><code>/opt/broker-dashboard/broker_dashboard.py</code> · puerto 8099</td></tr>
    <tr><td>Código de GPU Prestada</td><td>CT 100</td><td><code>~/gpu-prestada/</code> · se publica con <code>publicar.sh</code></td></tr>
    <tr><td>Relé para PCs sin Tailscale</td><td>CT 100</td><td><code>~/gpu-prestada/relay/relay.py</code> · servicio <code>gpu-relay</code> · entrada <code>casa.lomastrend.com:443</code> (SNI en <code>proxy-casa</code>) · lado interno <code>100.100.84.72:11437</code></td></tr>
    <tr><td>En el PC con Windows</td><td>—</td><td><code>%LOCALAPPDATA%\GPUPrestada\</code>: el programa, <code>config.json</code>, <code>gpu-prestada.log</code> y <code>ollama-serve.log</code> (lo que dice Ollama al arrancar)</td></tr>
    <tr><td>Puertos en ese PC (solo dentro del propio equipo)</td><td>—</td><td><code>11434</code> Ollama combinado (sus modelos + los del servidor) · <code>11439</code> el Ollama propio del PC · <code>11444</code> la IA del servidor sola. Con Tailscale, además, <code>11435</code> para que el servidor le mande trabajo (solo desde la red Tailscale).</td></tr>
    <tr><td>Permisos de uso y altas</td><td>CT 101</td><td><code>nodes.json</code> → <code>clients</code> (quién puede usar la IA del servidor y cuántas peticiones a la vez); las altas pendientes viven en memoria del proxy</td></tr>
  </table></div>
  <p class="meta">La CT 101 es «ia-llamacpp-ollama-xtts», la CT 106 la de ComfyUI y la CT 100 «claude-code». No confundir el puerto <code>11444</code> del servidor (el Ollama real detrás del proxy) con el <code>11444</code> de un PC (la IA del servidor sola): son máquinas distintas.</p>
  <p class="meta"><b>Diagnóstico a distancia</b> (desde el servidor, con el código de alta): el agente de cada PC responde a <code>/_agent/diag</code> (gráfica y registro de arranque de Ollama), <code>/_agent/events</code> (por qué se suspendió o apagó el PC, según el registro de Windows), <code>/_agent/gwtest</code> (prueba su propio Ollama combinado), <code>/_agent/restart-ollama</code> y <code>/_agent/reinstall-ollama</code> (reinstala Ollama si su instalación se ha quedado rota, sin tocar los modelos).</p>

  <footer>GPU Prestada <?= $version ?> · LoMasTrend · página privada, no enlazar fuera.</footer>
</div>
</body>
</html>
