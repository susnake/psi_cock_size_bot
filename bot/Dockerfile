FROM python:3.11-slim

WORKDIR /app

# setpriv и find нужны docker-entrypoint.sh. В части версий Debian setpriv
# вынесен в отдельный пакет — доставляем, если его нет; если не удалось, сборка упадёт.
RUN if ! command -v setpriv >/dev/null || ! command -v find >/dev/null; then \
        apt-get update && \
        apt-get install -y --no-install-recommends setpriv findutils && \
        rm -rf /var/lib/apt/lists/*; \
    fi && \
    command -v setpriv && command -v find

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
