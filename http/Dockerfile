FROM php:8.2-apache

# Устанавливаем certbot + Apache-плагин + пакет ssl-cert
RUN apt-get update && \
    apt-get install -y certbot python3-certbot-apache ssl-cert && \
    rm -rf /var/lib/apt/lists/*

# Включаем SSL-модуль и стандартный SSL-виртуалхост
RUN a2enmod ssl headers rewrite && \
    a2ensite default-ssl

# Копируем остальные файлы
COPY . /var/www/html
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh /var/www/html/cleanup_images.sh

EXPOSE 80 443
ENTRYPOINT ["/entrypoint.sh"]

