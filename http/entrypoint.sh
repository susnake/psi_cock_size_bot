#!/bin/bash
set -e

# 1. DOMAIN из IMAGE_SERVER_URL
if [ -z "$IMAGE_SERVER_URL" ]; then
  echo "ERROR: IMAGE_SERVER_URL не задан"
  exit 1
fi
DOMAIN=$(echo "$IMAGE_SERVER_URL" | sed -E 's~https?://([^/]+)/?.*~\1~')
echo "==> Using domain: $DOMAIN"

# 2. Пишем ServerName в главный конфиг, чтобы apache2ctl configtest не ругался
echo "ServerName $DOMAIN" >> /etc/apache2/apache2.conf

# 3. Останавливаем Apache, чтобы Certbot мог забиндить порт 80
apache2ctl stop || true

# 4. Получаем сертификат через standalone
CERT_ARGS="--standalone -n --agree-tos -d $DOMAIN"
if [ -n "$SSL_EMAIL" ]; then
  CERT_ARGS="--email $SSL_EMAIL $CERT_ARGS"
else
  CERT_ARGS="--register-unsafely-without-email $CERT_ARGS"
fi

echo "==> Running certbot certonly $CERT_ARGS"
certbot certonly $CERT_ARGS

# 5. Правим default-ssl.conf — ставим пути к реальным cert
SSL_CONF=/etc/apache2/sites-enabled/default-ssl.conf
sed -i "s|/etc/ssl/certs/ssl-cert-snakeoil.pem|/etc/letsencrypt/live/$DOMAIN/fullchain.pem|g" $SSL_CONF
sed -i "s|/etc/ssl/private/ssl-cert-snakeoil.key|/etc/letsencrypt/live/$DOMAIN/privkey.pem|g" $SSL_CONF

# 6. Включаем SSL-модуль (если вдруг не включён) и запускаем Apache
a2enmod ssl headers rewrite || true
a2ensite default-ssl 2>/dev/null || true

echo "==> Starting Apache"
apache2-foreground

