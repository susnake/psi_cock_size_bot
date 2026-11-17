FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY psi_chat_bot.py docker-entrypoint.sh ./

# Непривилегированный пользователь; entrypoint стартует от root только для того,
# чтобы поправить владельца /app/data (старые volume создавались от root),
# и сразу понижает привилегии через setpriv
RUN useradd --uid 1000 --no-create-home --shell /usr/sbin/nologin bot && \
    mkdir -p /app/data && chown bot:bot /app/data && \
    sed -i 's/\r$//' docker-entrypoint.sh && chmod +x docker-entrypoint.sh

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["python", "psi_chat_bot.py"]
