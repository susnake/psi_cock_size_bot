#!/bin/bash
set -e # Любая команда, завершившаяся с ошибкой, немедленно прервет выполнение скрипта

# Функция для чтения секрета (Docker Secrets или env variable)
get_secret() {
  local name="$1"
  local secret_file="/run/secrets/$name"
  if [ -f "$secret_file" ]; then
    cat "$secret_file"
  else
    printenv "$name" 2>/dev/null || echo ""
  fi
}

# 1. DOMAIN из IMAGE_SERVER_URL (из Docker Secrets или env)
IMAGE_SERVER_URL=$(get_secret "IMAGE_SERVER_URL")
SSL_EMAIL=$(get_secret "SSL_EMAIL")

if [ -z "$IMAGE_SERVER_URL" ]; then
  echo "FATAL: Переменная IMAGE_SERVER_URL не задана (ни в Docker Secrets, ни в env)."
  exit 1
fi
DOMAIN=$(echo "$IMAGE_SERVER_URL" | sed -E 's~https?://([^/]+)/?.*~\1~')
echo "==> Используемый домен: $DOMAIN"

# Пути к сертификатам
CERT_DIR="/etc/letsencrypt"
LIVE_CERT_DIR="$CERT_DIR/live/$DOMAIN"
CERT_FILE="$LIVE_CERT_DIR/fullchain.pem"
KEY_FILE="$LIVE_CERT_DIR/privkey.pem"

CERTIFICATE_SETUP_SUCCESS=false # Флаг успешной настройки SSL с LE сертификатом

# 2. Пишем ServerName в главный конфиг Apache
# (Убедимся, что не дублируем запись, если скрипт перезапускается)
if ! grep -q "ServerName $DOMAIN" /etc/apache2/apache2.conf; then
  echo "ServerName $DOMAIN" >> /etc/apache2/apache2.conf
fi

# 3. Останавливаем Apache, если он вдруг запущен (для certbot --standalone)
echo "==> Остановка Apache (если запущен)..."
apache2ctl stop || true # Игнорируем ошибку, если Apache не был запущен

# 4. Проверяем наличие сертификата или пытаемся его получить
# ВАЖНО: Директория /etc/letsencrypt ДОЛЖНА быть смонтирована как volume,
# чтобы сертификаты сохранялись между перезапусками контейнера!

if [ -f "$CERT_FILE" ] && [ -f "$KEY_FILE" ]; then
    echo "==> Сертификат для $DOMAIN уже существует ($CERT_FILE)."
    echo "==> Проверка срока действия сертификата..."
    # Проверяем, не истек ли сертификат (0 секунд до истечения)
    if openssl x509 -checkend 0 -noout -in "$CERT_FILE"; then
        echo "==> Существующий сертификат действителен."
        CERTIFICATE_SETUP_SUCCESS=true
    else
        echo "==> Срок действия существующего сертификата ИСТЕК или он поврежден. Требуется новый сертификат."
        # CERTIFICATE_SETUP_SUCCESS остается false, т.к. ниже будет попытка получить новый
    fi
else
    echo "==> Сертификат для $DOMAIN не найден. Требуется новый сертификат."
    # CERTIFICATE_SETUP_SUCCESS остается false
fi

# Если сертификат не найден, недействителен или требует первоначального получения
if [ "$CERTIFICATE_SETUP_SUCCESS" = false ]; then
    echo "==> Попытка получить/обновить сертификат для $DOMAIN..."
    CERT_ARGS="--standalone -n --agree-tos -d $DOMAIN"
    if [ -n "$SSL_EMAIL" ]; then
        CERT_ARGS="--email $SSL_EMAIL $CERT_ARGS"
    else
        CERT_ARGS="--register-unsafely-without-email $CERT_ARGS"
    fi

    echo "==> Выполняется: certbot certonly $CERT_ARGS"

    set +e # Временно отключаем выход по ошибке для вызова certbot
    certbot certonly $CERT_ARGS
    CERTBOT_EXIT_CODE=$?
    set -e # Включаем обратно немедленно

    if [ $CERTBOT_EXIT_CODE -eq 0 ]; then
        echo "==> certbot certonly успешно завершен. Новый сертификат получен."
        CERTIFICATE_SETUP_SUCCESS=true
    else
        echo "ОШИБКА: certbot certonly завершился с кодом $CERTBOT_EXIT_CODE."
        echo "Подробности смотрите в логе /var/log/letsencrypt/letsencrypt.log внутри контейнера."
        echo "Возможные причины: достигнут лимит запросов Let's Encrypt, проблемы с DNS/сетью, или ошибка в самом certbot (AttributeError)."
        # CERTIFICATE_SETUP_SUCCESS остается false
    fi
fi

# 5. Если настройка SSL с Let's Encrypt сертификатом не удалась, ПРЕКРАЩАЕМ РАБОТУ
if [ "$CERTIFICATE_SETUP_SUCCESS" = false ]; then
    echo "FATAL: Не удалось получить или подтвердить валидный SSL-сертификат от Let's Encrypt для домена $DOMAIN."
    echo "Приложение требует HTTPS для корректной работы. Запуск Apache отменен."
    exit 1 # Завершаем скрипт с ошибкой. Контейнер остановится.
fi

# --- Если дошли до сюда, значит CERTIFICATE_SETUP_SUCCESS = true ---

# 6. Настраиваем Apache SSL, так как сертификат успешно получен/существует и валиден
echo "==> Конфигурируем Apache для SSL с сертификатами Let's Encrypt..."
SSL_CONF_FILE="/etc/apache2/sites-enabled/default-ssl.conf"

if [ ! -f "$SSL_CONF_FILE" ]; then
    echo "FATAL: Файл конфигурации SSL $SSL_CONF_FILE не найден. Невозможно настроить SSL."
    exit 1
fi

# Заменяем пути к snakeoil сертификатам на пути к сертификатам Let's Encrypt
sed -i "s|SSLCertificateFile /etc/ssl/certs/ssl-cert-snakeoil.pem|SSLCertificateFile $CERT_FILE|g" "$SSL_CONF_FILE"
sed -i "s|SSLCertificateKeyFile /etc/ssl/private/ssl-cert-snakeoil.key|SSLCertificateKeyFile $KEY_FILE|g" "$SSL_CONF_FILE"
echo "==> Конфигурация Apache SSL обновлена для использования сертификатов Let's Encrypt."

# 7. Включаем необходимые модули Apache и SSL сайт
echo "==> Включение модулей Apache: ssl, headers, rewrite, socache_shmcb..."
a2enmod ssl headers rewrite socache_shmcb || true # Игнорируем ошибки, если модули уже включены

echo "==> Включение SSL сайта (default-ssl)..."
a2ensite default-ssl 2>/dev/null || true # Игнорируем ошибку, если сайт уже включен

# Перед запуском Apache ОБЯЗАТЕЛЬНО проверяем конфигурацию
echo "==> Проверка конфигурации Apache..."
apache2ctl configtest # Если здесь ошибка, скрипт завершится из-за 'set -e'

# === ДОБАВЛЕННЫЙ БЛОК ДЛЯ ЗАПУСКА CRON ===
echo "==> Запуск службы cron для автоматического обновления сертификатов..."
# Запускаем cron в фоновом режиме. Ключ -f заставляет его оставаться на переднем плане,
# но так как мы запускаем его с '&', он уйдет в фон.
# Альтернативно, некоторые системы используют `service cron start`.
# Для стандартного Debian-окружения `cron` без аргументов или `cron -f &` должно работать.
cron
# Если cron не уходит в фон сам или возникают проблемы, можно попробовать:
# (crond -n &) # для некоторых систем, где cron демон называется crond
# service cron start # если systemV init скрипты используются
# === КОНЕЦ ДОБАВЛЕННОГО БЛОКА ===

echo "==> Запуск Apache с настроенным HTTPS..."
apache2-foreground
