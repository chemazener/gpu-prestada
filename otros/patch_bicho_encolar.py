#!/usr/bin/env python3
"""Bicho Magic: al encolar se espera 90 s (antes 45) y, si el proxy no contesta a tiempo,
el trabajo no se da por fallido: queda «pendiente de encolar», el vigilante comprueba si
ComfyUI lo tiene y, si no, lo vuelve a mandar (hasta 4 intentos). Idempotente."""
import shutil, sys, time
D = sys.argv[1] if len(sys.argv) > 1 else "/var/www/html/lomastrend/bichocomfyui"
TS = time.strftime("%Y%m%d-%H%M%S")

def patch(path, edits):
    s = open(path).read()
    if "job_pending" in s or "d.enqueue" in s:
        print("ya aplicado:", path); return
    for a, b in edits:
        if callable(a):
            s = a(s)
        else:
            assert s.count(a) == 1, (path, s.count(a), a[:70])
            s = s.replace(a, b)
    shutil.copy2(path, f"{path}.bak-encolar-{TS}")
    open(path, "w").write(s); print("ok:", path)

# ---------------- api/comfy.php ----------------
def enqueue_block(s):
    i = s.index("    // ComfyUI v0.20.1 encola en /api/prompt")
    end_marker = "    $d = json_decode($r['body'], true);\n    if (!$d || empty($d['prompt_id'])) {\n"
    j = s.index(end_marker, i) + len(end_marker)
    new = """    // ComfyUI encola en /api/prompt (el proxy lo trata como broker-aware). El identificador lo
    // fija este servidor (ComfyUI lo admite): si el proxy tarda en contestar porque el broker de
    // GPU aún no da turno, se sabe qué trabajo buscar después y se puede reenviar sin duplicarlo.
    $pid = job_new_id();
    $payload = json_encode(['prompt' => $workflow, 'client_id' => 'bcu_' . $uid, 'prompt_id' => $pid]);
    job_pending_save($pid, $payload);
    $r = comfy_http($base, '/api/prompt', ['post_json' => $payload, 'timeout' => 90]);
    // Sin respuesta en 90 s: no es un fallo. Queda «pendiente de encolar» y el vigilante
    // (job_watch.php) comprueba si ComfyUI lo tiene y, si no, lo vuelve a mandar.
    $slow = ($r['code'] === 0);
    $d = $slow ? ['prompt_id' => $pid] : json_decode($r['body'], true);
    if (!$d || empty($d['prompt_id'])) {
        job_pending_drop($pid);
"""
    return s[:i] + new + s[j:]

comfy = [
    (enqueue_block, None),
    ("""    // Vigilante en el servidor: recoge el resultado en cuanto termine, aunque no haya ninguna página
""", """    if ($slow) jobs_update(function (&$j) use ($pid) { if (isset($j[$pid])) $j[$pid]['enq'] = ['tries' => 1, 'last' => time()]; });
    else job_pending_drop($pid);
    // Vigilante en el servidor: recoge el resultado en cuanto termine, aunque no haya ninguna página
"""),
    ("""    json_out(['ok' => true, 'prompt_id' => $d['prompt_id'], 'number' => $d['number'] ?? null, 'prompt_used' => $promptUsed]);""",
     """    json_out(['ok' => true, 'prompt_id' => $d['prompt_id'], 'number' => $d['number'] ?? null, 'prompt_used' => $promptUsed, 'slow' => $slow]);"""),
    ("""            if ($hr['code'] === 200 && is_array($hist) && empty($hist[$id])) {
                jobs_update(function (&$j) use ($id) { unset($j[$id]); });
                json_out(['status' => 'error', 'error' => 'La tarea ya no está en la cola (el servidor se reinició o se canceló). Vuelve a lanzarla.']);""",
     """            if ($hr['code'] === 200 && is_array($hist) && empty($hist[$id])) {
                // Pendiente de encolar (el broker de GPU aún no dio turno): no está perdida.
                $pend = jobs_update(null)[$id] ?? [];
                if (!empty($pend['enq_error'])) {
                    jobs_update(function (&$j) use ($id) { unset($j[$id]); });
                    job_pending_drop($id);
                    json_out(['status' => 'error', 'error' => $pend['enq_error']]);
                }
                if (!empty($pend['enq'])) json_out(['status' => 'pending', 'ahead' => count($qRun) + count($qPend), 'queue' => count($qPend), 'enqueue' => (int)$pend['enq']['tries']]);
                jobs_update(function (&$j) use ($id) { unset($j[$id]); });
                json_out(['status' => 'error', 'error' => 'La tarea ya no está en la cola (el servidor se reinició o se canceló). Vuelve a lanzarla.']);"""),
    ("""        } elseif (time() - (int)($meta['ts'] ?? 0) > 120) {""",
     """        } elseif (empty($meta['enq']) && empty($meta['enq_error']) && time() - (int)($meta['ts'] ?? 0) > 120) {"""),
    ("""/** Recoge los trabajos apuntados que ya han terminado y descarta los que fallaron o se perdieron. */""",
     """/** Identificador de trabajo (UUID v4) fijado por este servidor al encolar. */
function job_new_id() {
    $b = random_bytes(16);
    $b[6] = chr((ord($b[6]) & 0x0f) | 0x40);
    $b[8] = chr((ord($b[8]) & 0x3f) | 0x80);
    return vsprintf('%s%s-%s-%s-%s-%s%s%s', str_split(bin2hex($b), 4));
}

/** Copia de lo enviado a ComfyUI, para poder reenviarlo si no llegó a encolarse. */
function job_pending_path($id) {
    return OUT_USER_DIR . '/.pending/' . preg_replace('/[^a-zA-Z0-9\\-]/', '', (string)$id) . '.json';
}
function job_pending_save($id, $payload) {
    ensure_user_dir();
    $d = OUT_USER_DIR . '/.pending';
    if (!is_dir($d)) @mkdir($d, 0775, true);
    foreach ((array)glob($d . '/*.json') as $old) if (@filemtime($old) < time() - 86400) @unlink($old);
    @file_put_contents(job_pending_path($id), $payload);
}
function job_pending_drop($id) { @unlink(job_pending_path($id)); }

/** ComfyUI ya tiene el trabajo: deja de estar «pendiente de encolar». */
function job_enqueue_confirm($id) {
    jobs_update(function (&$j) use ($id) { if (isset($j[$id])) unset($j[$id]['enq']); });
    job_pending_drop($id);
}

/** El trabajo no aparece en ComfyUI y nunca se confirmó que entrara: se vuelve a mandar con el
 *  mismo identificador. Se esperan 5 min entre intentos porque el proxy puede seguir aguardando
 *  turno del broker de GPU (hasta 4 min) y encolarlo él; así no se duplica. Máximo 4 intentos. */
function job_enqueue_retry($base, $id, $meta) {
    $enq = $meta['enq'] ?? null;
    if (!$enq || time() - (int)($enq['last'] ?? 0) < 300) return;
    $fail = function ($msg) use ($id) {
        jobs_update(function (&$j) use ($id, $msg) { if (isset($j[$id])) { unset($j[$id]['enq']); $j[$id]['enq_error'] = $msg; } });
        job_pending_drop($id);
    };
    $payload = @file_get_contents(job_pending_path($id));
    if ((int)($enq['tries'] ?? 1) >= 4 || !$payload) {
        $fail('No se pudo encolar tras varios intentos: la GPU ha estado ocupada por otro servicio todo el rato. Vuelve a lanzarla más tarde.');
        return;
    }
    jobs_update(function (&$j) use ($id) { if (isset($j[$id]['enq'])) { $j[$id]['enq']['tries']++; $j[$id]['enq']['last'] = time(); } });
    $r = comfy_http($base, '/api/prompt', ['post_json' => $payload, 'timeout' => 90]);
    if ($r['code'] === 0) return;                       // sigue sin contestar: se mirará en el próximo ciclo
    $d = json_decode($r['body'], true);
    if (!$d || empty($d['prompt_id'])) {
        $msg = $d['error']['message'] ?? ($d['error'] ?? 'Error encolando');
        $fail('ComfyUI rechazó la tarea al reenviarla: ' . (is_array($msg) ? json_encode($msg) : $msg));
    }
}

/** Recoge los trabajos apuntados que ya han terminado y descarta los que fallaron o se perdieron. */"""),
]
patch(D + "/api/comfy.php", comfy)

# ---------------- api/job_watch.php ----------------
patch(D + "/api/job_watch.php", [
    ("""    if (!empty($hist[$id])) {
        if (($hist[$id]['status']['status_str'] ?? '') !== 'error') job_collect($base, $id, $hist[$id], $meta);""",
     """    if (!empty($hist[$id])) {
        job_pending_drop($id);
        if (($hist[$id]['status']['status_str'] ?? '') !== 'error') job_collect($base, $id, $hist[$id], $meta);"""),
    ("""        $missing = in_array($id, $ids, true) ? 0 : $missing + 1;""",
     """        $present = in_array($id, $ids, true);
        if (!empty($meta['enq'])) {
            // Se mandó pero el proxy no confirmó a tiempo: o ya está en ComfyUI, o se reenvía.
            if ($present) job_enqueue_confirm($id);
            else { job_enqueue_retry($base, $id, $meta); continue; }
        }
        $missing = $present ? 0 : $missing + 1;"""),
])

# ---------------- navegador ----------------
patch(D + "/index.php", [
    ("""      if(d.status==='pending'){ const a=d.ahead||0; runTxt(progId,id,progTxtId, a>0 ?""",
     """      if(d.status==='pending'&&d.enqueue){ runTxt(progId,id,progTxtId,'Esperando a que la GPU quede libre para encolar (intento '+d.enqueue+' de 4)…'); }
      else if(d.status==='pending'){ const a=d.ahead||0; runTxt(progId,id,progTxtId, a>0 ?"""),
])
patch(D + "/m.php", [
    ("""      if(d.status==='pending'){ const a=d.ahead||0; runTxt(progId,id,txtId, a>0 ?""",
     """      if(d.status==='pending'&&d.enqueue){ runTxt(progId,id,txtId,'⏳ Esperando a que la GPU quede libre (intento '+d.enqueue+' de 4)'); }
      else if(d.status==='pending'){ const a=d.ahead||0; runTxt(progId,id,txtId, a>0 ?"""),
])
