FROM php:8.2-apache

# Устанавливаем переменные окружения для неинтерактивной установки
ENV DEBIAN_FRONTEND=noninteractive

# Системные зависимости:
# - python3-pip и python3-venv для установки certbot через pip в виртуальном окружении
# - build-essential и другие *-dev пакеты, необходимые для сборки некоторых зависимостей certbot
# - cron для автоматического обновления сертификатов
# - ssl-cert для самоподписанных сертификатов (snakeoil), которые могут использоваться по умолчанию
RUN apt-get update && \
    apt-get install -y \
        python3-pip \
        python3-venv \
        build-essential \
        libaugeas0 \
        libssl-dev \
        libffi-dev \
        python3-dev \
        ca-certificates \
        pkg-config \
        cron \
        ssl-cert \
    && rm -rf /var/lib/apt/lists/*

# Создаем виртуальное окружение для certbot и устанавливаем certbot и плагин для Apache
RUN python3 -m venv /opt/certbot/ && \
    /opt/certbot/bin/pip install --no-cache-dir certbot certbot-apache && \
    ln -s /opt/certbot/bin/certbot /usr/bin/certbot

# Настраиваем cron для автоматического обновления сертификатов Let's Encrypt
# Эта задача будет пытаться обновить сертификаты дважды в день (в 0:00 и 12:00)
# и перезагружать Apache при успешном обновлении. Логи будут писаться в /var/log/cron.log
RUN echo "0 */12 * * * root certbot renew --quiet --deploy-hook 'apache2ctl graceful' >> /var/log/cron.log 2>&1" > /etc/cron.d/certbot && \
    chmod 0644 /etc/cron.d/certbot

# Включаем SSL-модуль, заголовки, перезапись URL и стандартный SSL-виртуальный хост Apache
# socache_shmcb часто нужен для SSL Stapling
RUN a2enmod ssl headers rewrite socache_shmcb && \
    a2ensite default-ssl

# Копируем ваше приложение (PHP-скрипты, статику и т.д.)
# Убедитесь, что в корне этой директории есть index.html или index.php для главной страницы!
COPY . /var/www/html

# Копируем и делаем исполняемыми entrypoint.sh и другие необходимые скрипты
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh && \
    # Если cleanup_images.sh существует в вашем контексте сборки в /var/www/html/, делаем его исполняемым
    if [ -f /var/www/html/cleanup_images.sh ]; then chmod +x /var/www/html/cleanup_images.sh; fi

# Открываем порты 80 (HTTP) и 443 (HTTPS)
EXPOSE 80 443

# Устанавливаем entrypoint
ENTRYPOINT ["/entrypoint.sh"]
