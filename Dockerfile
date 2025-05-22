# ---------- builder ----------
FROM python:3.12-slim AS builder
WORKDIR /usr/src/app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt    # пакеты → /usr/local

# ---------- runtime ----------
FROM python:3.12-slim

RUN adduser --disabled-password --gecos "" appuser
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /home/appuser/app

# Создать каталог логов
RUN mkdir -p /var/log/psi_chat_bot \
    && chown appuser:appuser /var/log/psi_chat_bot

# копируем готовые site-packages
COPY --from=builder /usr/local /usr/local

COPY --chown=appuser:appuser . .
USER appuser
ENTRYPOINT ["python", "psi_chat_bot.py"]

