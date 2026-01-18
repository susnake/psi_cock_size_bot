FROM php:8.2-apache

# Устанавливаем системные зависимости, включая те, что нужны для сборки python-augeas
# и работы certbot
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
    # ----- КОНЕЦ ИСПРАВЛЕНИЯ -----
    && rm -rf /var/lib/apt/lists/*

# Создаем виртуальное окружение для certbot и устанавливаем certbot и плагин для Apache
# Этот шаг теперь должен выполняться без ошибок
RUN python3 -m venv /opt/certbot/ && \
    /opt/certbot/bin/pip install --no-cache-dir certbot certbot-apache && \
    ln -s /opt/certbot/bin/certbot /usr/bin/certbot

# Копируем скрипты и файлы вашего сайта
# Предполагается, что они должны находиться в /var/www/html/
COPY . /var/www/html/

# Включаем необходимые модули Apache
RUN a2enmod rewrite
RUN a2enmod ssl

# Копируем и делаем исполняемыми скрипты
COPY entrypoint.sh /
COPY cleanup_images.sh /
RUN chmod +x /entrypoint.sh /cleanup_images.sh

# Открываем порты для HTTP и HTTPS
EXPOSE 80
EXPOSE 443

# Запускаем entrypoint скрипт
CMD ["/entrypoint.sh"]

