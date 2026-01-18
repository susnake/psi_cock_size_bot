FROM php:8.2-apache

# Устанавливаем системные зависимости
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
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

# Копируем файлы сайта
COPY . /var/www/html/

# Включаем модули Apache и SSL сайт
RUN a2enmod rewrite ssl headers socache_shmcb && \
    a2ensite default-ssl

# Копируем скрипты и конвертируем line endings
COPY entrypoint.sh /
COPY cleanup_images.sh /
RUN dos2unix /entrypoint.sh /cleanup_images.sh && \
    chmod +x /entrypoint.sh /cleanup_images.sh

EXPOSE 80
EXPOSE 443

CMD ["/entrypoint.sh"]
