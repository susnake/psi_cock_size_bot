<?php
// Приём картинок от бота.
// Защита: обязательный токен (X-Upload-Token == секрет UPLOAD_TOKEN),
// только PNG/JPEG (проверка содержимого, а не имени), имя файла генерирует сервер.
header('Content-Type: application/json; charset=utf-8');

const MAX_SIZE = 10 * 1024 * 1024;

function read_secret(string $name): string {
    $path = "/run/secrets/$name";
    if (is_readable($path)) {
        $v = trim((string)file_get_contents($path));
        if ($v !== '') return $v;
    }
    $v = getenv($name);
    return $v === false ? '' : trim($v);
}

function fail(int $code, string $msg): void {
    http_response_code($code);
    echo json_encode(['success' => false, 'error' => $msg], JSON_UNESCAPED_UNICODE);
    exit;
}

if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    fail(405, 'Method not allowed');
}

$token = read_secret('UPLOAD_TOKEN');
if ($token === '') {
    fail(503, 'Загрузка отключена: UPLOAD_TOKEN не настроен');
}
$given = $_SERVER['HTTP_X_UPLOAD_TOKEN'] ?? '';
if (!hash_equals($token, $given)) {
    fail(403, 'Forbidden');
}

if (empty($_FILES['image']) || $_FILES['image']['error'] !== UPLOAD_ERR_OK) {
    fail(400, 'Файл не передан');
}
$file = $_FILES['image'];

if ($file['size'] <= 0 || $file['size'] > MAX_SIZE) {
    fail(413, 'Недопустимый размер файла');
}

$info = @getimagesize($file['tmp_name']);
$allowed = [IMAGETYPE_PNG => 'png', IMAGETYPE_JPEG => 'jpg'];
if ($info === false || !isset($allowed[$info[2]])) {
    fail(415, 'Разрешены только PNG и JPEG');
}

$dir = __DIR__ . '/images';
if (!is_dir($dir) && !mkdir($dir, 0755, true)) {
    fail(500, 'Нет каталога для загрузки');
}

$filename = bin2hex(random_bytes(16)) . '.' . $allowed[$info[2]];
if (!move_uploaded_file($file['tmp_name'], "$dir/$filename")) {
    fail(500, 'Ошибка сохранения файла');
}

// Базовый URL берём из конфигурации, а не из заголовка Host (его задаёт клиент)
$base = rtrim(read_secret('IMAGE_SERVER_URL'), '/');
if ($base === '') {
    fail(500, 'IMAGE_SERVER_URL не настроен');
}

echo json_encode(['success' => true, 'url' => "$base/images/$filename"], JSON_UNESCAPED_SLASHES);
