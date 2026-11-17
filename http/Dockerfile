FROM php:8.2-apache

# Устанавливаем системные зависимости (cron — для очистки картинок и продления сертификата)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    cron \
    python3-pip \
    python3-venv \
    build-essential \
    libaugeas0 \
    libssl-dev \
    libffi-dev \
    libaugeas-dev \
    libxml2-dev \
    python3-dev \
    dos2unix \
    && rm -rf /var/lib/apt/lists/*

# Certbot
RUN python3 -m venv /opt/certbot/ && \
    /opt/certbot/bin/pip install --no-cache-dir certbot certbot-apache && \
    ln -s /opt/certbot/bin/certbot /usr/bin/certbot

# В webroot кладём только upload.php (раньше туда копировалась вся папка,
# включая Dockerfile и скрипты)
COPY upload.php /var/www/html/
RUN mkdir -p /var/www/html/images && chown www-data:www-data /var/www/html/images

# Включаем модули Apache и SSL сайт, общий hardening-конфиг
COPY hardening.conf /etc/apache2/conf-available/hardening.conf
RUN a2enmod rewrite ssl headers socache_shmcb && \
    a2ensite default-ssl && \
    a2enconf hardening

# Скрипты и cron; убираем CRLF/BOM
COPY entrypoint.sh /
COPY cleanup_images.sh /
COPY crontab /etc/cron.d/psi-http
RUN dos2unix /entrypoint.sh /cleanup_images.sh /etc/cron.d/psi-http && \
    chmod +x /entrypoint.sh /cleanup_images.sh && \
    chmod 0644 /etc/cron.d/psi-http

EXPOSE 80
EXPOSE 443

CMD ["/entrypoint.sh"]
