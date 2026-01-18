#!/usr/bin/env python3
# psi_chat_bot.py — Telegram-бот «Кто я?» с кэшированием картинки на 6 часов
# Python 3.10+, aiogram 3.7+, Pydantic v2

import os
import sys
import io
import random
import base64
import hashlib
import asyncio
import logging
import json
import signal
import html
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, Tuple, Optional

# --- aiohttp для асинхронных HTTP-запросов ---
import aiohttp

# --- Парсинг HTML-страниц ---
try:
    from bs4 import BeautifulSoup
except ImportError:
    sys.exit("Библиотека BeautifulSoup4 не установлена. pip install beautifulsoup4 lxml")

from dotenv import load_dotenv
load_dotenv()

from PIL import Image, ImageDraw, ImageFont

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command, CommandObject
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    InputTextMessageContent, InlineQueryResultArticle,
    BufferedInputFile, ChatMemberUpdated, TextQuote,
    BotCommand, BotCommandScopeDefault
)
from aiogram.exceptions import TelegramBadRequest

# ─────────── Базовая настройка ───────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("psi_chat_bot")


def get_secret(name: str, default: str = None) -> str:
    """Читает секрет из Docker Secrets файла или переменной окружения."""
    # Сначала пробуем Docker Secrets (файл)
    secret_path = f"/run/secrets/{name}"
    try:
        with open(secret_path, 'r') as f:
            value = f.read().strip()
            if value:
                log.info(f"Секрет '{name}' загружен из Docker Secrets")
                return value
    except FileNotFoundError:
        pass
    except Exception as e:
        log.warning(f"Ошибка чтения секрета '{name}' из файла: {e}")

    # Fallback на переменную окружения
    value = os.getenv(name)
    if value:
        log.info(f"Секрет '{name}' загружен из переменной окружения")
    return value or default


# ─────────── Загрузка конфигурации ───────────
API_TOKEN = get_secret("psi_chat_bot")
GEMINI_API_KEY = get_secret("GEMINI_API_KEY")
GOOGLE_API_KEY = get_secret("GOOGLE_API_KEY")
GOOGLE_CSE_ID = get_secret("GOOGLE_CSE_ID")

if not API_TOKEN:
    sys.exit("❌ psi_chat_bot не найден (ни в Docker Secrets, ни в переменных окружения)")

if not GEMINI_API_KEY:
    log.warning("⚠️ GEMINI_API_KEY не найден. Функции Gemini не будут работать.")

# ─────────── Пути для данных ───────────
DATA_DIR = Path("/app/data") if os.path.exists("/app") else Path("./data")
DATA_DIR.mkdir(parents=True, exist_ok=True)

CACHE_FILE = DATA_DIR / "cache.json"
API_USAGE_FILE = DATA_DIR / "api_usage.json"

# ─────────── Инициализация бота ───────────
bot = Bot(
    token=API_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()
BOT_USERNAME = ""

# ─────────── Константы ───────────
SEARCH_API_DAILY_LIMIT = 100
TTL = timedelta(hours=6)
TTL_SECONDS = int(TTL.total_seconds())

# Locks для потокобезопасности
api_usage_lock = asyncio.Lock()
cache_lock = asyncio.Lock()

# ─────────── Кэш с персистентностью ───────────
cache: Dict[str, Tuple[datetime, int, str]] = {}
img_cache: Dict[int, Tuple[datetime, bytes]] = {}


def load_cache_from_disk():
    """Загружает кэш из файла при старте."""
    global cache
    try:
        if CACHE_FILE.exists():
            with open(CACHE_FILE, 'r') as f:
                data = json.load(f)
                for key, (timestamp_str, val, emo) in data.items():
                    timestamp = datetime.fromisoformat(timestamp_str)
                    if datetime.now() - timestamp <= TTL:
                        cache[key] = (timestamp, val, emo)
            log.info(f"Загружено {len(cache)} записей из кэша")
    except Exception as e:
        log.warning(f"Не удалось загрузить кэш: {e}")


async def save_cache_to_disk():
    """Сохраняет кэш в файл."""
    async with cache_lock:
        try:
            data = {
                key: (timestamp.isoformat(), val, emo)
                for key, (timestamp, val, emo) in cache.items()
            }
            with open(CACHE_FILE, 'w') as f:
                json.dump(data, f)
        except Exception as e:
            log.warning(f"Не удалось сохранить кэш: {e}")


# ─────────── Генераторы значений ───────────
EMO = {
    "w": {
        "0": "🪶", "1-49": "🦴", "50-99": "⚖️", "100-149": "🏋️‍♂️",
        "150-199": "🐖", "200-249": "🤯", "250": "🐘"
    },
    "c": {
        "0": "🤤", "1-9": "🤮", "10-19": "🥴", "20-29": "😐",
        "30-39": "😲", "40-49": "🤯", "50": "🫡"
    },
    "iq": {
        "50-69": "🤡", "70-89": "😕", "90-109": "🙂",
        "110-129": "😎", "130-149": "🤓", "150-199": "🧠", "200": "👨‍🔬"
    },
    "h": {
        "140-149": "🦗", "150-169": "🙂", "170-189": "😃",
        "190-219": "🏀", "220": "🇷🇸"
    }
}


def _emo(val: int, tbl: dict) -> str:
    """Возвращает эмодзи для значения по таблице диапазонов."""
    for rng, e in tbl.items():
        if "-" in rng:
            a, b = map(int, rng.split("-"))
            if a <= val <= b:
                return e
        elif int(rng) == val:
            return e
    return ""


def gen_w():
    v = random.randint(0, 250)
    return v, _emo(v, EMO["w"])


def gen_c():
    v = random.randint(0, 50)
    return v, _emo(v, EMO["c"])


def gen_iq():
    v = random.randint(50, 200)
    return v, _emo(v, EMO["iq"])


def gen_h():
    v = random.randint(140, 220)
    return v, _emo(v, EMO["h"])


gens = {"weight": gen_w, "cock": gen_c, "iq": gen_iq, "height": gen_h}


async def cached_val(uid: int, label: str) -> Tuple[int, str]:
    """Возвращает кэшированное или новое значение."""
    async with cache_lock:
        now = datetime.now()
        key = f"{label}_{uid}"

        if key in cache and now - cache[key][0] <= TTL:
            _, v, e = cache[key]
        else:
            v, e = gens[label]()
            cache[key] = (now, v, e)
            # Сохраняем кэш асинхронно (не блокируя)
            asyncio.create_task(save_cache_to_disk())

        return v, e


# ─────────── Клавиатура ───────────
KB = InlineKeyboardMarkup(inline_keyboard=[
    [
        InlineKeyboardButton(text="Вес", callback_data="weight"),
        InlineKeyboardButton(text="Хуеметр", callback_data="cock")
    ],
    [
        InlineKeyboardButton(text="IQ", callback_data="iq"),
        InlineKeyboardButton(text="Рост", callback_data="height")
    ],
    [InlineKeyboardButton(text="Хто Я?", callback_data="whoami")],
    [InlineKeyboardButton(text="Пруф?", callback_data="proof_help")]
])

# ─────────── Генерация изображений ───────────
IMG_GEN_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash-preview-image-generation:generateContent"


async def gemini_png(session: aiohttp.ClientSession, prompt: str) -> bytes:
    """Генерирует изображение через Gemini API."""
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY не задан для генерации изображений.")

    url = f"{IMG_GEN_URL}?key={GEMINI_API_KEY}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]}
    }

    async with session.post(url, json=payload, timeout=30) as resp:
        if resp.status != 200:
            text = await resp.text()
            log.error(f"Gemini image API HTTP Error {resp.status}: {text}")
            raise RuntimeError(f"Ошибка API: HTTP {resp.status}")

        data = await resp.json()

    if data["candidates"][0].get("finishReason") == "IMAGE_SAFETY":
        raise RuntimeError("IMAGE_SAFETY")

    for part in data["candidates"][0]["content"]["parts"]:
        if "inlineData" in part:
            return base64.b64decode(part["inlineData"]["data"])

    raise RuntimeError("Нет изображения в ответе Gemini")


def prompt_primary(ctx: dict) -> str:
    return (
        f"Draw a clean flat cartoon avatar, transparent PNG. "
        f"Height {ctx['h']} cm, weight {ctx['w']} kg. "
        f"Floating yellow tape-measure on the right shows \"{ctx['c']} cm\". "
        f"Thought bubble: \"IQ {ctx['iq']}\". "
        f"Write \"{ctx['name']}\" under the feet. Fully clothed. No nudity."
    )


def prompt_safe(ctx: dict) -> str:
    return (
        f"Draw a clean flat cartoon avatar, transparent PNG. "
        f"Height {ctx['h']} cm, weight {ctx['w']} kg. "
        f"Thought bubble: \"IQ {ctx['iq']}\". "
        f"Write \"{ctx['name']}\" under the feet. Fully clothed."
    )


async def make_image(ctx: dict) -> io.BytesIO:
    """Создаёт изображение через Gemini с fallback на безопасный промпт."""
    async with aiohttp.ClientSession() as session:
        try:
            data = await gemini_png(session, prompt_primary(ctx))
        except RuntimeError as e:
            if "IMAGE_SAFETY" in str(e):
                log.warning("Основной промпт не прошел (IMAGE_SAFETY), пробуем безопасный.")
                data = await gemini_png(session, prompt_safe(ctx))
            else:
                raise

    bio = io.BytesIO(data)
    bio.seek(0)
    return bio


def render_pil(ctx: dict) -> io.BytesIO:
    """Резервная генерация изображения через PIL."""
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 18)
    except IOError:
        font = ImageFont.load_default()

    img = Image.new("RGB", (400, 400), "white")
    d = ImageDraw.Draw(img)

    # Имя
    d.text((10, 5), ctx["name"], font=font, fill="black")

    # Голова
    head, r = (200, 100), 40
    d.ellipse((head[0] - r, head[1] - r, head[0] + r, head[1] + r), outline="black", width=2)

    # Тело
    d.rectangle((180, 140, 220, 250), outline="black", width=2)

    # Руки
    d.line((180, 140, 140, 180), fill="black", width=2)
    d.line((220, 140, 260, 180), fill="black", width=2)

    # Ноги
    d.line((200, 250, 170, 320), fill="black", width=2)
    d.line((200, 250, 230, 320), fill="black", width=2)

    # Член (условно)
    d.line((200, 250, 200, 250 + ctx['c']), fill="black", width=2)

    # Статы
    y = 330
    for t in (f"Вес: {ctx['w']} кг", f"Длина: {ctx['c']} см",
              f"IQ: {ctx['iq']}", f"Рост: {ctx['h']} см"):
        d.text((10, y), t, font=font, fill="black")
        y += 18

    bio = io.BytesIO()
    img.save(bio, "PNG")
    bio.seek(0)
    return bio


# ─────────── Лимиты API ───────────
async def check_api_limit_and_increment() -> Tuple[bool, str]:
    """Проверяет и обновляет дневной лимит использования API."""
    async with api_usage_lock:
        today_str = datetime.utcnow().strftime("%Y-%m-%d")
        usage_data = {"date": today_str, "count": 0}

        try:
            if API_USAGE_FILE.exists():
                with open(API_USAGE_FILE, 'r') as f:
                    usage_data = json.load(f)
        except (json.JSONDecodeError, Exception) as e:
            log.info(f"Файл {API_USAGE_FILE} не найден или пуст: {e}")

        if usage_data.get("date") != today_str:
            log.info(f"Новый день. Сбрасываем счетчик API.")
            usage_data = {"date": today_str, "count": 0}

        if usage_data["count"] >= SEARCH_API_DAILY_LIMIT:
            log.warning(f"Дневной лимит ({SEARCH_API_DAILY_LIMIT}) исчерпан.")
            return False, f"Дневной лимит ({SEARCH_API_DAILY_LIMIT}) исчерпан. Попробуйте завтра."

        usage_data["count"] += 1

        with open(API_USAGE_FILE, 'w') as f:
            json.dump(usage_data, f)

        log.info(f"API: {usage_data['count']}/{SEARCH_API_DAILY_LIMIT}")
        return True, ""


# ─────────── Поиск и парсинг ───────────
async def fetch_and_parse_url(session: aiohttp.ClientSession, url: str) -> str:
    """Скачивает и парсит HTML-страницу, возвращая чистый текст."""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
    try:
        async with session.get(url, headers=headers, timeout=10) as resp:
            resp.raise_for_status()
            html_text = await resp.text()

        soup = BeautifulSoup(html_text, 'lxml')

        for tag in soup(['script', 'style', 'header', 'footer', 'nav', 'aside']):
            tag.decompose()

        text = soup.get_text(separator='\n', strip=True)
        return "\n".join(line.strip() for line in text.splitlines() if line.strip())

    except Exception as e:
        log.error(f"Ошибка парсинга {url}: {e}")
        return ""


async def search_google(session: aiohttp.ClientSession, query: str) -> list:
    """Выполняет поиск в Google и возвращает результаты."""
    if not GOOGLE_API_KEY or not GOOGLE_CSE_ID:
        raise RuntimeError("GOOGLE_API_KEY или GOOGLE_CSE_ID не настроены.")

    search_url = "https://www.googleapis.com/customsearch/v1"
    params = {
        "key": GOOGLE_API_KEY,
        "cx": GOOGLE_CSE_ID,
        "q": query,
        "num": 3,
        "sort": "date",
        "dateRestrict": "d1"
    }

    try:
        async with session.get(search_url, params=params, timeout=10) as resp:
            resp.raise_for_status()
            search_results = await resp.json()

        if "items" in search_results:
            log.info(f"Найдено: {[item['link'] for item in search_results['items']]}")
            return search_results["items"]
        else:
            log.info("Релевантных страниц не найдено.")
            return []

    except Exception as e:
        log.error(f"Ошибка поиска Google: {e}", exc_info=True)
        return []


# ─────────── Gemini текстовая генерация ───────────
TEXT_GEN_URL_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
DEFAULT_TEXT_MODEL = "gemini-1.5-flash-latest"


async def get_clean_search_query(
    session: aiohttp.ClientSession,
    text: str,
    model_name: str = DEFAULT_TEXT_MODEL
) -> str:
    """Использует Gemini для извлечения ключевой поисковой фразы."""
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY не задан.")

    prompt = (
        "Из следующего текста выдели главную тему в виде короткого запроса из 3-6 слов. "
        "Убери шум. Верни только сам запрос.\n\n"
        f"Текст: \"{text}\"\n\n"
        "Поисковый запрос:"
    )

    url = TEXT_GEN_URL_TEMPLATE.format(model_name=model_name) + f"?key={GEMINI_API_KEY}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.0}
    }

    try:
        async with session.post(url, json=payload, timeout=10) as resp:
            resp.raise_for_status()
            data = await resp.json()

        if data.get("candidates") and data["candidates"][0].get("content"):
            return data["candidates"][0]["content"]["parts"][0]["text"].strip()
        return text

    except Exception as e:
        log.error(f"Ошибка очистки запроса: {e}")
        return text


async def summarize_with_gemini(
    session: aiohttp.ClientSession,
    original_query: str,
    search_context: Optional[str],
    model_name: str = DEFAULT_TEXT_MODEL
) -> str:
    """Суммаризирует информацию с Gemini."""
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY не задан.")

    if search_context:
        prompt = (
            "Ты — ИИ-ассистент, анализирующий поисковую выдачу.\n"
            "Задача:\n"
            "1. Изучи текст страниц и оцени релевантность.\n"
            "2. Напиши структурированный ответ на основе релевантных источников.\n"
            "3. Не упоминай нерелевантные источники.\n"
            "4. Ссылайся на использованные источники: <a href='URL'>название</a>.\n"
            "5. Форматируй HTML: <b>, <i>, <a href>.\n"
            "6. Отвечай на языке запроса.\n\n"
            f"<b>Запрос:</b> {html.escape(original_query)}\n\n"
            f"<b>Источники:</b>\n{search_context}\n\n"
            "Ответ:"
        )
    else:
        prompt = (
            "Ты — информационный ассистент. Предоставь точный ответ. "
            "Используй <a href='URL'>ссылки</a> на авторитетные источники.\n\n"
            f"Запрос: \"{original_query}\""
        )

    url = TEXT_GEN_URL_TEMPLATE.format(model_name=model_name) + f"?key={GEMINI_API_KEY}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.3},
        "safetySettings": [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}
        ]
    }

    try:
        async with session.post(url, json=payload, timeout=90) as resp:
            resp.raise_for_status()
            data = await resp.json()

        candidates = data.get("candidates", [])
        if candidates and candidates[0].get("content"):
            parts = candidates[0]["content"].get("parts", [])
            if parts and parts[0].get("text"):
                return parts[0]["text"].strip()

        log.error(f"Неожиданный ответ Gemini: {data}")
        return "Не удалось получить ответ от Gemini."

    except aiohttp.ClientResponseError as e:
        log.error(f"HTTP ошибка Gemini: {e}")
        return f"Ошибка API ({e.status})."

    except Exception as e:
        log.error(f"Ошибка Gemini: {e}", exc_info=True)
        return "Произошла ошибка при обработке запроса."


# ─────────── Обработчики команд ───────────
@dp.message(CommandStart())
async def start(m: types.Message):
    await m.answer("Добро пожаловать!", reply_markup=KB, parse_mode=None)


@dp.message(Command("menu"))
async def menu(m: types.Message):
    await m.answer("Выберите действие:", reply_markup=KB, parse_mode=None)


@dp.message(Command("pizdica", "cock"))
async def cmd_pizdica(message: types.Message, command: CommandObject):
    p1_user = message.from_user
    p1_name = f"@{p1_user.username}" if p1_user.username else p1_user.full_name

    p2_name = None
    if message.reply_to_message and message.reply_to_message.from_user:
        p2_user = message.reply_to_message.from_user
        p2_name = f"@{p2_user.username}" if p2_user.username else p2_user.full_name
    elif command.args:
        p2_name = command.args.strip()

    if p2_name:
        winner = random.choice([p1_name, p2_name])
        winner_display = winner.lstrip('@')
        await message.reply(
            f"{p1_name} и {p2_name} пиздились за гаражами до первой крови\n"
            f"Победитель — {winner_display} 🏆🏆🏆",
            parse_mode=None
        )
    else:
        await message.reply(
            "Для дуэли ответьте на сообщение или укажите оппонента:\n"
            "/pizdica @username\n"
            "/pizdica Текст",
            parse_mode=None
        )


# ─────────── Обработчики callback ───────────
@dp.callback_query(F.data.in_({"weight", "cock", "iq", "height", "whoami", "proof_help"}))
async def callbacks(cb: types.CallbackQuery):
    uid = cb.from_user.id
    name = cb.from_user.full_name or cb.from_user.username or str(uid)
    chat_id = cb.message.chat.id
    act = cb.data

    if act == "proof_help":
        await cb.message.answer(
            "Чтобы я поискал информацию:\n"
            "— /proof ваш текст\n"
            "— Или ответьте на сообщение командой /proof",
            parse_mode=None
        )
        await cb.answer()
        return

    if act in ("weight", "cock", "iq", "height"):
        act_rus = {"weight": "вес", "cock": "хуй", "iq": "IQ", "height": "рост"}
        val, emo = await cached_val(uid, act)
        unit = "кг" if act == "weight" else "см"
        await bot.send_message(
            chat_id,
            f"{name}, ваш {act_rus[act]}: {val} {unit} {emo}",
            parse_mode=None
        )
        await cb.answer()
        return

    if act == "whoami":
        w, wt = await cached_val(uid, "weight")
        c, ct = await cached_val(uid, "cock")
        iq, iqt = await cached_val(uid, "iq")
        h, ht = await cached_val(uid, "height")
        ctx = {"w": w, "c": c, "iq": iq, "h": h, "name": name}

        now = datetime.now()
        img_data: bytes

        if uid in img_cache and now - img_cache[uid][0] <= TTL:
            log.info(f"Изображение для UID {uid} из кэша.")
            img_data = img_cache[uid][1]
        else:
            log.info(f"Генерация изображения для UID {uid}...")
            try:
                bio = await make_image(ctx)
                img_data = bio.getvalue()
            except Exception as e:
                log.error(f"Ошибка Gemini → резервный PIL: {e}")
                bio = render_pil(ctx)
                img_data = bio.getvalue()

            img_cache[uid] = (now, img_data)
            log.info(f"Изображение сгенерировано ({len(img_data)} байт).")

        caption = (
            f"Мой вес: {w} кг {wt}\n"
            f"Мой хуй: {c} см {ct}\n"
            f"Мой IQ: {iq} {iqt}\n"
            f"Мой рост: {h} см {ht}"
        )

        await bot.send_photo(
            chat_id,
            BufferedInputFile(img_data, "whoami.png"),
            caption=caption,
            parse_mode=None
        )
        await cb.answer()


# ─────────── Команда /proof ───────────
@dp.message(Command("proof"))
async def proof_command_handler(message: types.Message, command: CommandObject):
    if not GEMINI_API_KEY:
        await message.reply("Функция недоступна: GEMINI_API_KEY не настроен.", parse_mode=None)
        return

    text_to_proof = None
    log.info(f"Proof: msg={message.message_id}, chat={message.chat.id}")

    # Извлечение текста
    if command.args:
        text_to_proof = command.args.strip()
        log.info(f"/proof: аргументы: '{text_to_proof}'")
    elif message.quote and isinstance(message.quote, TextQuote) and message.quote.text:
        text_to_proof = message.quote.text.strip()
        log.info(f"/proof: цитата: '{text_to_proof}'")
    elif message.reply_to_message:
        replied = message.reply_to_message
        log.info(f"/proof: ответ на msg={replied.message_id}")
        if replied.text:
            text_to_proof = replied.text.strip()
        elif replied.caption:
            text_to_proof = replied.caption.strip()

    if not text_to_proof:
        await message.reply(
            "Укажите текст (аргумент, цитата или ответ на сообщение).",
            parse_mode=None
        )
        return

    MIN_LENGTH = 10
    if len(text_to_proof) < MIN_LENGTH:
        await message.reply(
            f"Текст слишком короткий (минимум {MIN_LENGTH} символов).",
            parse_mode=None
        )
        return

    can_search = GOOGLE_API_KEY and GOOGLE_CSE_ID
    search_context = None

    async with aiohttp.ClientSession() as session:
        if can_search:
            is_ok, limit_msg = await check_api_limit_and_increment()
            if not is_ok:
                await message.reply(limit_msg, parse_mode=None)
                return

            processing_msg = await message.reply("Формирую запрос...", parse_mode=None)

            clean_query = await get_clean_search_query(session, text_to_proof)
            log.info(f"Очищенный запрос: '{clean_query}'")

            await processing_msg.edit_text(f"Ищу: \"{clean_query}\"...", parse_mode=None)
            results = await search_google(session, clean_query)

            if results:
                await processing_msg.edit_text("Анализирую страницы...", parse_mode=None)

                tasks = [fetch_and_parse_url(session, r['link']) for r in results]
                contents = await asyncio.gather(*tasks)

                parts = []
                for i, (result, content) in enumerate(zip(results, contents)):
                    if content:
                        parts.append(
                            f"<b>Источник {i + 1}:</b> "
                            f"<a href='{result['link']}'>{html.escape(result['title'])}</a>\n"
                            f"<i>Сниппет:</i> {html.escape(result.get('snippet', ''))}\n"
                            f"<b>Текст:</b>\n{html.escape(content[:1500])}...\n"
                        )
                search_context = "\n\n---\n\n".join(parts) if parts else None
        else:
            log.info("Google Search не настроен. Анализ без поиска.")
            processing_msg = await message.reply("Анализирую...", parse_mode=None)

        await processing_msg.edit_text("Формирую ответ...", parse_mode=None)
        answer = await summarize_with_gemini(session, text_to_proof, search_context)

    log.info(f"Ответ Gemini: {answer[:100]}...")

    final = html.unescape(answer).strip().replace('\\n', '\n')
    await processing_msg.delete()

    if not final:
        await message.answer("Не удалось получить ответ.", parse_mode=None)
        return

    LIMIT = 4096
    try:
        if len(final) > LIMIT:
            for i in range(0, len(final), LIMIT):
                await message.answer(final[i:i + LIMIT], parse_mode=ParseMode.HTML)
        else:
            await message.answer(final, parse_mode=ParseMode.HTML)
    except TelegramBadRequest as e:
        log.warning(f"Ошибка HTML: {e}. Отправляю как текст.")
        if len(final) > LIMIT:
            for i in range(0, len(final), LIMIT):
                await message.answer(final[i:i + LIMIT], parse_mode=None)
        else:
            await message.answer(final, parse_mode=None)


# ─────────── Обработчик добавления в чат ───────────
@dp.chat_member(F.new_chat_members.is_bot & F.new_chat_members.id == bot.id)
async def added(ev: ChatMemberUpdated):
    await bot.send_message(ev.chat.id, "Привет! Я бот.", reply_markup=KB, parse_mode=None)


# ─────────── Inline-режим ───────────
@dp.inline_query()
async def inline(q: types.InlineQuery):
    global BOT_USERNAME

    if not BOT_USERNAME:
        try:
            me = await bot.get_me()
            BOT_USERNAME = me.username or "bot"
        except Exception as e:
            log.error(f"Ошибка получения имени бота: {e}")
            BOT_USERNAME = "bot"

    uid = q.from_user.id
    query_text = q.query.strip()

    def art(id_suffix: str, title: str, text: str, desc: str = None):
        final_id = hashlib.md5(
            f"{uid}_{id_suffix}_{hashlib.md5(text.encode()).hexdigest()}".encode()
        ).hexdigest()
        return InlineQueryResultArticle(
            id=final_id,
            title=title,
            input_message_content=InputTextMessageContent(message_text=text),
            description=desc
        )

    w, wt = await cached_val(uid, "weight")
    c, ct = await cached_val(uid, "cock")
    iq, iqt = await cached_val(uid, "iq")
    h, ht = await cached_val(uid, "height")

    results = [
        art("w", "Вес", f"Мой вес: {w} кг {wt}"),
        art("c", "Мой хуй", f"Мой хуй: {c} см {ct}"),
        art("i", "IQ", f"Мой IQ: {iq} {iqt}"),
        art("h", "Рост", f"Мой рост: {h} см {ht}"),
        art("all", "Хто я?",
            f"Мой вес: {w} кг {wt}\nМой хуй: {c} см {ct}\n"
            f"Мой IQ: {iq} {iqt}\nМой рост: {h} см {ht}",
            desc="Сводка характеристик"),
    ]

    if query_text:
        short = html.escape(query_text[:40])
        ellipsis = '...' if len(query_text) > 40 else ''
        results.append(art(
            "proof_query",
            f"Искать: \"{short}{ellipsis}\"",
            f"/proof {query_text}",
            desc="Отправить запрос боту"
        ))
    else:
        results.append(art(
            "proof_help",
            "Пруф? (Как использовать)",
            f"Используйте /proof в чате с @{BOT_USERNAME}",
            desc="Инструкция"
        ))

    await q.answer(results, cache_time=1, is_personal=True)


# ─────────── Graceful shutdown ───────────
shutdown_event = asyncio.Event()


async def shutdown_handler(sig: signal.Signals):
    log.info(f"Получен сигнал {sig.name}. Завершение...")
    shutdown_event.set()


# ─────────── Запуск ───────────
async def main():
    global BOT_USERNAME

    # Загружаем кэш с диска
    load_cache_from_disk()

    # Обработка сигналов для graceful shutdown
    loop = asyncio.get_event_loop()
    if sys.platform != "win32":
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(
                sig,
                lambda s=sig: asyncio.create_task(shutdown_handler(s))
            )

    try:
        me = await bot.get_me()
        BOT_USERNAME = me.username or "bot"
        log.info(f"Бот запущен: @{BOT_USERNAME}")
    except Exception as e:
        log.error(f"Ошибка получения информации о боте: {e}")
        BOT_USERNAME = "bot"

    await bot.set_my_commands(
        commands=[
            BotCommand(command="start", description="Начать"),
            BotCommand(command="menu", description="Меню"),
            BotCommand(command="pizdica", description="Дуэль"),
            BotCommand(command="proof", description="Проверить информацию"),
        ],
        scope=BotCommandScopeDefault()
    )

    await bot.delete_webhook(drop_pending_updates=True)

    # Polling с graceful shutdown
    polling_task = asyncio.create_task(dp.start_polling(bot))

    # Ждём сигнал завершения или окончание polling
    done, pending = await asyncio.wait(
        [polling_task, asyncio.create_task(shutdown_event.wait())],
        return_when=asyncio.FIRST_COMPLETED
    )

    # Отменяем pending задачи
    for task in pending:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # Сохраняем кэш перед выходом
    await save_cache_to_disk()
    log.info("Кэш сохранён. Бот остановлен.")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
