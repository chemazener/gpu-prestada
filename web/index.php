<?php
// GPU Prestada — descarga y documentación (incluye el gestor de VRAM). Login Google de lomastrend.
require_once __DIR__ . '/../shared/auth.php';
$user = lomastrend_require_auth('gpu-prestada', 'GPU Prestada', '/img/logo-small.png');
require __DIR__ . '/contenido.php';
