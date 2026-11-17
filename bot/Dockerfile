FROM python:3.11-slim

WORKDIR /app

# fonts-dejavu-core — шрифт с кириллицей для резервной картинки (PIL), в slim-образе шрифтов нет.
# setpriv и find нужны docker-entrypoint.sh. В части версий Debian setpriv
# вынесен в отдельный пакет — доставляем, если его нет; если не удалось, сборка упадёт.
RUN pkgs="fonts-dejavu-core"; \
    command -v setpriv >/dev/null || pkgs="$pkgs setpriv"; \
    command -v find >/dev/null || pkgs="$pkgs findutils"; \
    apt-get update && \
    apt-get install -y --no-install-recommends $pkgs && \
    rm -rf /var/lib/apt/lists/* && \
    command -v setpriv && command -v find && \
    test -f /usr/share/fonts/truetype/dejavu/DejaVuSans.ttf

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY psi_chat_bot.py docker-entrypoint.sh ./

# Непривилегированный пользователь; entrypoint стартует от root только чтобы
# прочитать секреты и поправить владельца /app/data, затем сбрасывает права (setpriv)
RUN useradd --uid 1000 --no-create-home --shell /usr/sbin/nologin bot && \
    mkdir -p /app/data && chown bot:bot /app/data && \
    sed -i 's/\r$//' docker-entrypoint.sh && chmod 755 docker-entrypoint.sh

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["python", "psi_chat_bot.py"]
