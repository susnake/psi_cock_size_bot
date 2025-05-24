# PSI Chat Bot v2.0

**PSI Chat Bot** — это юмористический Telegram-бот, который генерирует случайные «измерения» (Вес, «Хуй», IQ, Рост) с забавными эмодзи и создаёт персональные аватары через Google Gemini API.

## 📦 Особенности

* Генерация значений:

  * Вес (кг)
  * «Хуй» (см)
  * IQ (без единиц)
  * Рост (см)

![image](https://github.com/user-attachments/assets/290c69e6-91d4-4541-9eb4-9c8e77b9604b)

    
* Поддержка двух режимов работы:

  1. **Storage Mode** — картинки сохраняются в Telegram-канале (используется `STORAGE_CHAT_ID`), не требуется внешнего HTTP-сервера.
  2. **HTTP Mode** — картинки выгружаются на собственный PHP-сервер (`upload.php`) с автоматическим SSL через Let’s Encrypt (используется `IMAGE_SERVER_URL`).
* Кэширование результатов и изображений на **6 часов**
* Два Docker-контейнера:

  * **bot** — сам Telegram-бот на Python/aiogram
  * **http** — PHP-сервер с автоматическим SSL для хранения аватаров

## 🔑 Настройка переменных окружения

### Получение Telegram API Token

1. Напишите боту **@BotFather** в Telegram.
2. Используйте команду `/newbot` и следуйте инструкциям.
3. Скопируйте полученный **API Token**.

### Конфигурация Storage Mode и HTTP Mode

```bash
cp bot/.env.sample bot/.env
```

Пример содержимого `bot/.env`:

```dotenv
psi_chat_bot=YOUR_TELEGRAM_TOKEN      # токен, полученный у BotFather (@BotFather)
STORAGE_CHAT_ID=YOUR_CHAT_ID         # ID канала или чата для хранения изображений
IMAGE_SERVER_URL=https://your.domain # URL вашего HTTP-сервера (без слэша в конце)
GEMINI_API_KEY=YOUR_GEMINI_KEY       # Ключ Google Gemini API
```

* **Storage Mode**:

  * Сохраняет аватары в Telegram (возвращает `file_id`).
  * Прост в настройке, но имеет ограничения Telegram (quota, удаление).
* **HTTP Mode**:

  * Загружает файлы на ваш PHP-сервер с `upload.php`.
  * SSL через Let’s Encrypt.
  * Не ограничен Telegram, возвращает прямой URL.
  * **inline**-режим выдаёт только текстовые результаты; аватар в inline недоступен.

## 🚀 Быстрый старт (из готовых образов)

Самый простой способ — использовать уже опубликованные в hub.docker.com образы:

```bash
# HTTP-сервер (для HTTP Mode)
docker pull susnake/psi_http_image_server:latest
docker run -d --name psi-http \
  --restart unless-stopped \
  -p 80:80 -p 443:443 \
  --env-file http/.env \
  susnake/psi_http_image_server:latest

# Telegram-бот
docker pull susnake/psi_cock_size_bot:latest
docker run -d --name psi-bot \
  --restart unless-stopped \
  --env-file bot/.env \
  susnake/psi_cock_size_bot:latest
```

Откройте бота в Telegram и начните диалог.

## ⚙️ Развертывание из исходников

Если вы клонировали репозиторий и хотите собрать локально:

```bash
# Перейдите в папку проекта
git clone https://github.com/susnake/psi_cock_size_bot.git
cd psi_cock_size_bot

# Сборка образов
docker build -t susnake/psi_cock_size_bot:latest bot
docker build -t susnake/psi_http_image_server:latest http

# Запуск
docker run -d --name psi-http \
  --restart unless-stopped \
  -p 80:80 -p 443:443 \
  --env-file http/.env \
  susnake/psi_http_image_server:latest

docker run -d --name psi-bot \
  --restart unless-stopped \
  --env-file bot/.env \
  susnake/psi_cock_size_bot:latest
```

## 🧑‍💻 Локальная разработка

```bash
cd bot
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
python psi_chat_bot.py
```

## 🔒 Безопасность

* Никогда не храните реальные токены в публичных репозиториях.
* Файл `bot/.env` включён в `.gitignore`.

## ⚖️ Лицензия

Проект распространяется под лицензией **MIT** © susnake

---

*Спасибо за использование!*
