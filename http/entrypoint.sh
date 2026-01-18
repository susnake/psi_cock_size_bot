#!/bin/bash
set -e

# Функция для логирования с timestamp
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

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
    log "FATAL: Переменная IMAGE_SERVER_URL не задана."
    exit 1
fi

DOMAIN=$(echo "$IMAGE_SERVER_URL" | sed -E 's~https?://([^/]+)/?.*~\1~')
log "Используемый домен: $DOMAIN"

# Пути к сертификатам
CERT_DIR="/etc/letsencrypt"
LIVE_CERT_DIR="$CERT_DIR/live/$DOMAIN"
CERT_FILE="$LIVE_CERT_DIR/fullchain.pem"
KEY_FILE="$LIVE_CERT_DIR/privkey.pem"

# 2. Записываем ServerName в Apache конфиг
log "Настройка ServerName..."
echo "ServerName $DOMAIN" > /etc/apache2/conf-available/servername.conf
a2enconf servername 2>/dev/null || true

# 3. Останавливаем Apache если запущен
log "Остановка Apache..."
apache2ctl stop 2>/dev/null || true

# 4. Проверяем наличие сертификата
CERTIFICATE_SETUP_SUCCESS=false

if [ -f "$CERT_FILE" ] && [ -f "$KEY_FILE" ]; then
    log "Сертификат найден: $CERT_FILE"
    if openssl x509 -checkend 0 -noout -in "$CERT_FILE" 2>/dev/null; then
        log "Сертификат действителен."
        CERTIFICATE_SETUP_SUCCESS=true
    else
        log "Сертификат истёк, нужен новый."
    fi
else
    log "Сертификат не найден, нужно получить."
fi

# 5. Получаем сертификат если его нет или он невалиден
if [ "$CERTIFICATE_SETUP_SUCCESS" = false ]; then
    log "Получение сертификата Let's Encrypt..."
    
    CERT_ARGS="--standalone -n --agree-tos -d $DOMAIN"
    if [ -n "$SSL_EMAIL" ]; then
        CERT_ARGS="--email $SSL_EMAIL $CERT_ARGS"
    else
        CERT_ARGS="--register-unsafely-without-email $CERT_ARGS"
    fi

    log "Выполняется: certbot certonly $CERT_ARGS"
    
    set +e
    certbot certonly $CERT_ARGS
    CERTBOT_EXIT_CODE=$?
    set -e

    if [ $CERTBOT_EXIT_CODE -ne 0 ]; then
        log "ОШИБКА: certbot завершился с кодом $CERTBOT_EXIT_CODE"
        log "Возможные причины: rate limit, DNS, firewall."
        exit 1
    fi

    log "Сертификат успешно получен."
    CERTIFICATE_SETUP_SUCCESS=true
fi

# 6. Создаём свой SSL конфиг вместо изменения default-ssl
log "Создание SSL конфигурации..."

cat > /etc/apache2/sites-available/ssl-site.conf << EOF
<VirtualHost *:443>
    ServerName $DOMAIN
    DocumentRoot /var/www/html
    
    SSLEngine on
    SSLCertificateFile $CERT_FILE
    SSLCertificateKeyFile $KEY_FILE
    
    <Directory /var/www/html>
        Options Indexes FollowSymLinks
        AllowOverride All
        Require all granted
    </Directory>
    
    ErrorLog \${APACHE_LOG_DIR}/error.log
    CustomLog \${APACHE_LOG_DIR}/access.log combined
</VirtualHost>
EOF

# 7. Включаем модули и сайт
log "Включение модулей Apache..."
a2enmod ssl headers rewrite socache_shmcb 2>/dev/null || true

log "Отключение default-ssl, включение ssl-site..."
a2dissite default-ssl 2>/dev/null || true
a2ensite ssl-site 2>/dev/null || true

# 8. Проверяем конфигурацию
log "Проверка конфигурации Apache..."
if ! apache2ctl configtest; then
    log "ОШИБКА: Конфигурация Apache невалидна!"
    exit 1
fi

# 9. Запускаем cron для обновления сертификатов
log "Запуск cron..."
cron 2>/dev/null || true

# 10. Запускаем Apache
log "Запуск Apache..."
exec apache2-foreground
