<?php
// Descarga del agente (Windows o Linux), solo para usuarios con acceso a la app.
require_once __DIR__ . '/../shared/auth.php';
$user = lomastrend_require_auth('gpu-prestada', 'GPU Prestada', '/img/logo-small.png');
$os = $_GET['os'] ?? 'windows';
$files = [
    'windows'     => ['gpu-prestada.exe', 'application/vnd.microsoft.portable-executable'],
    'linux-amd64' => ['gpu-prestada-linux-amd64', 'application/octet-stream'],
    'linux-arm64' => ['gpu-prestada-linux-arm64', 'application/octet-stream'],
];
if (!isset($files[$os])) { http_response_code(400); exit('Sistema no válido.'); }
[$name, $type] = $files[$os];
$f = __DIR__ . '/' . $name;
if (!is_file($f)) { http_response_code(404); exit('No hay ninguna versión publicada para este sistema.'); }
header('Content-Type: ' . $type);
header('Content-Disposition: attachment; filename="' . ($os === 'windows' ? 'gpu-prestada.exe' : 'gpu-prestada') . '"');
header('Content-Length: ' . filesize($f));
header('Cache-Control: no-store');
header('X-Content-Type-Options: nosniff');
readfile($f);
