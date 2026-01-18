<?php
header('Content-Type: application/json');

if (!empty($_FILES['file'])) {
    $timestamp = time();
    $filename  = $timestamp . "_" . basename($_FILES["file"]["name"]);
    $target = __DIR__ . "/images/" . $filename;

    if (move_uploaded_file($_FILES["file"]["tmp_name"], $target)) {
        // строим полный URL
        $scheme = (!empty($_SERVER['HTTPS']) && $_SERVER['HTTPS'] !== 'off') ? 'https://' : 'http://';
        $host   = $_SERVER['HTTP_HOST'];             // например "185.220.204.236" или ваш домен
        $url    = $scheme . $host . "/images/" . $filename;

        echo json_encode(['url' => $url]);
    } else {
        echo json_encode(['error' => 'Ошибка загрузки файла']);
    }
} else {
    echo json_encode(['error' => 'Файл не передан']);
}
