# PSI Chat Bot v2.1

**PSI Chat Bot** — юмористический Telegram-бот, генерирующий случайные «измерения» (Вес, «Хуй», IQ, Рост) с эмодзи и создающий персональные аватары через Google Gemini API.

## 📦 Особенности

* **Генерация значений:**
  * Вес (кг)
  * «Хуй» (см)
  * IQ
  * Рост (см)

* **Функции:**
  * `/cock` — Хуеметр (то же, что кнопка)
  * `/proof` — проверка утверждения: Gemini сам ищет в Google и даёт вердикт со списком источников
  * `/pizdica` — дуэль между пользователями (с самим собой нельзя)
  * Inline-режим для быстрого доступа к характеристикам
  * Кэширование результатов на **6 часов** с персистентностью
  * Генерация аватаров через Gemini (с fallback на PIL)

* **Один Docker-контейнер** — Telegram-бот (Python/aiogram)

## 🔑 Настройка

### 1. Получение Telegram Token

1. Напишите [@BotFather](https://t.me/BotFather)
2. Используйте `/newbot`
3. Скопируйте полученный токен

### 2. Создание секретов

Создайте файлы в папке `secrets/`:

```bash
mkdir -p secrets
echo -n "YOUR_TELEGRAM_TOKEN" > secrets/psi_chat_bot.txt
echo -n "YOUR_GEMINI_KEY" > secrets/GEMINI_API_KEY.txt
echo -n "-100XXXXXXXXXX" > secrets/STORAGE_CHAT_ID.txt  # опционально, см. ниже
chmod 600 secrets/*.txt  # достаточно root: бот получит значения через entrypoint
```

> ⚠️ **Важно:** Файлы должны содержать ТОЛЬКО токен — без пробелов, кавычек и переносов строк!
>
> Docker Compose требует, чтобы существовали все файлы из `secrets:` в `docker-compose.yml`. `STORAGE_CHAT_ID.txt` можно оставить пустым.

### Хостинг картинок «Хто Я?»

* **Storage Mode** (`STORAGE_CHAT_ID` задан): картинка один раз загружается в служебный канал (бот должен быть в нём админом), дальше отправляется по `file_id`.
* **In-Memory** (`STORAGE_CHAT_ID` пуст): картинка шлётся байтами, кэш только в памяти.

### 3. /proof

Отдельный ключ поиска не нужен: `/proof` использует встроенный в Gemini поиск Google
(инструмент `google_search`) и тот же `GEMINI_API_KEY`. Google Custom Search JSON API
больше не используется (Google отключает его 1 января 2027).

## 🚀 Быстрый старт

### Docker Compose (рекомендуется)

```bash
# Клонируйте репозиторий
git clone https://github.com/susnake/psi_cock_size_bot.git
cd psi_cock_size_bot

# Заполните секреты (см. выше)

# Запустите
docker-compose up -d --build

# Проверьте логи
docker-compose logs -f
```

### Готовые образы

```bash
docker pull susnake/psi_cock_size_bot:latest
docker run -d --name psi-bot \
  --restart unless-stopped \
  -v psi-bot-data:/app/data \
  -e psi_chat_bot=YOUR_TOKEN \
  -e GEMINI_API_KEY=YOUR_KEY \
  susnake/psi_cock_size_bot:latest
```

### Локальная разработка

```bash
cd bot
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python psi_chat_bot.py
```

## 📁 Структура проекта

```
psi_cock_size_bot/
├── bot/
│   ├── Dockerfile
│   ├── docker-entrypoint.sh # секреты → окружение, chown /app/data, сброс привилегий
│   ├── psi_chat_bot.py      # Основной код бота
│   └── requirements.txt
├── secrets/                  # 🔒 Токены (не в git!), образец — secrets.example/
├── tests/                    # pytest
├── requirements-dev.txt      # pytest
├── docker-compose.yml
├── README.md
└── .gitignore
```

## 🔧 Технические детали

### Зависимости

* `aiogram>=3.7,<4` — Telegram Bot API
* `aiohttp>=3.9,<4` — асинхронные HTTP-запросы
* `Pillow>=10.0` — резервная генерация изображений

### Тесты

```bash
pip install -r bot/requirements.txt -r requirements-dev.txt
pytest tests
```

### Кэширование

* Значения и изображения кэшируются на 6 часов
* Кэш значений сохраняется в `/app/data/cache.json`, протухшие записи чистятся раз в 30 минут
* При перезапуске кэш значений восстанавливается (картинки — нет, они перегенерируются)

### Распределение значений

Значение «растёт» от минимума диапазона по одной единице. Каждый следующий шаг проходит
с шансом, который падает по прямой от 99% до 1%; где шаг не прошёл — там и остановились
(`FIRST_STEP_CHANCE = 0.99`, `LAST_STEP_CHANCE = 0.01` в `bot/psi_chat_bot.py`).

«Хуй» (0–50 см): 1-й сантиметр проходит с шансом 99%, 2-й — 97%, 3-й — 95%, ..., 50-й — 1%.

| см | 1 | 3 | 5 | 7 | 10 | 15 | 20 | 25 |
|---|---|---|---|---|---|---|---|---|
| шанс получить столько и больше | 100% | 92% | 78% | 60% | 34% | 8% | 0,9% | ~0% |

Обычно — около 8 см. Минимум (0 см) выпадает так же редко, как максимум (50 см), — практически никогда
(то же для минимума веса, IQ и роста).

Вес, IQ и рост устроены так же, шаг — 1 кг / 1 пункт / 1 см:

| | Диапазон | Обычно (половина не больше) | Примеры |
|---|---|---|---|
| Вес | 0–250 кг | 16 кг | 25 кг и больше ≈ 23%, 50 кг и больше ≈ 0,3% |
| IQ | 50–200 | 63 | 65 и больше ≈ 42%, 80 и больше ≈ 3,4% |
| Рост | 140–220 см | 150 см | 156 см и больше ≈ 17%, 164 см и больше ≈ 1,7% |

### Лимиты (антиспам и расходы на Gemini)

* `/proof`: не больше 100 проверок в сутки на весь бот и одна проверка в 30 секунд на пользователя
* Картинки «Хто Я?» через Gemini: не больше 200 в сутки; сверх лимита рисуется резервная картинка (PIL)
* Переопределяются переменными окружения `PROOF_DAILY_LIMIT`, `IMAGE_DAILY_LIMIT`, `PROOF_COOLDOWN_SECONDS`
* Счётчики хранятся в `/app/data/api_usage.json`, сброс в полночь UTC

### Graceful Shutdown

* Корректная обработка SIGTERM/SIGINT
* Сохранение кэша перед остановкой

## 🔒 Безопасность

* Токены хранятся в Docker Secrets
* Папка `secrets/` в `.gitignore`
* Ключ Gemini передаётся заголовком `x-goog-api-key`, а не в URL (не попадает в логи)
* Файлы секретов на хосте могут быть `600 root`: `docker-entrypoint.sh` читает их от root
  и передаёт боту через окружение процесса (не на диск и не в аргументы, без `eval`),
  затем `setpriv --no-new-privs` сбрасывает права до uid 1000
* `chown` в `/app/data` — через `find -xdev ... chown -h`: не идёт по символьным ссылкам и не выходит за volume
* В compose: `cap_drop: ALL` (оставлены только `CHOWN`, `SETUID`, `SETGID` для старта) и `no-new-privileges`

## ⚖️ Лицензия

MIT © susnake

---

*Спасибо за использование!*
