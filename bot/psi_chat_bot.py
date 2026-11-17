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
import re
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Dict, Tuple, Optional, List

# --- aiohttp для асинхронных HTTP-запросов ---
import aiohttp

from dotenv import load_dotenv
load_dotenv()

from PIL import Image, ImageDraw, ImageFont

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import (
    CommandStart, Command, CommandObject,
    ChatMemberUpdatedFilter, JOIN_TRANSITION
)
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
    """
    Читает секрет из Docker Secrets файла или переменной окружения.
    В контейнере бот работает от uid 1000 и файлы с правами 600 root прочитать не может —
    их заранее (от root) переносит в окружение docker-entrypoint.sh. Это штатный путь.
    """
    secret_path = f"/run/secrets/{name}"
    try:
        with open(secret_path, 'r') as f:
            value = f.read().strip()
            if value:
                log.info(f"Секрет '{name}' загружен из Docker Secrets")
                return value
    except (FileNotFoundError, PermissionError):
        pass
    except Exception as e:
        log.warning(f"Ошибка чтения секрета '{name}' из файла: {e}")

    # Переменная окружения (в т.ч. выставленная docker-entrypoint.sh)
    value = (os.getenv(name) or "").strip()
    if value:
        log.info(f"Секрет '{name}' загружен из переменной окружения")
    return value or default


# ─────────── Загрузка конфигурации ───────────
API_TOKEN = get_secret("psi_chat_bot")
GEMINI_API_KEY = get_secret("GEMINI_API_KEY")

# Хостинг изображений: служебный канал Telegram (Storage Mode) или только память
STORAGE_CHAT_ID = get_secret("STORAGE_CHAT_ID")

if not API_TOKEN:
    sys.exit("❌ psi_chat_bot не найден (ни в Docker Secrets, ни в переменных окружения)")

if not GEMINI_API_KEY:
    log.warning("⚠️ GEMINI_API_KEY не найден. Функции Gemini не будут работать.")

if STORAGE_CHAT_ID:
    log.info("Режим хостинга: Storage Mode (служебный канал)")
else:
    log.info("Режим хостинга: In-Memory (изображения не сохраняются)")

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
# Дневные лимиты платных вызовов Gemini (сутки по UTC), можно переопределить env.
# Превышение лимита картинок не ломает «Хто Я?» — рисуется резервная картинка PIL.
DAILY_LIMITS = {
    "proof": int(os.getenv("PROOF_DAILY_LIMIT", "100")),
    "image": int(os.getenv("IMAGE_DAILY_LIMIT", "200")),
}
# Пауза между /proof одного пользователя
PROOF_COOLDOWN = timedelta(seconds=int(os.getenv("PROOF_COOLDOWN_SECONDS", "30")))
PROOF_MAX_CHARS = 2000  # длиннее — обрезаем перед отправкой в Gemini
TTL = timedelta(hours=6)
TTL_SECONDS = int(TTL.total_seconds())

CLEANUP_INTERVAL = 1800  # секунд между чистками кэша

# Locks для потокобезопасности
api_usage_lock = asyncio.Lock()
cache_lock = asyncio.Lock()

# Ссылки на фоновые задачи, чтобы их не собрал GC до завершения
_bg_tasks: set = set()


def spawn(coro) -> asyncio.Task:
    """create_task с удержанием ссылки на задачу."""
    task = asyncio.create_task(coro)
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)
    return task


# ─────────── Кэш с персистентностью ───────────
cache: Dict[str, Tuple[datetime, int, str]] = {}
# uid -> (время, (w, c, iq, h) на картинке, байты PNG, file_id/URL или None)
img_cache: Dict[int, Tuple[datetime, Tuple[int, int, int, int], bytes, Optional[str]]] = {}
# uid -> lock, чтобы двойное нажатие «Хто Я?» не запускало две генерации
img_locks: Dict[int, asyncio.Lock] = {}


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
    """Сохраняет кэш в файл (только не протухшие записи)."""
    async with cache_lock:
        try:
            now = datetime.now()
            data = {
                key: (timestamp.isoformat(), val, emo)
                for key, (timestamp, val, emo) in cache.items()
                if now - timestamp <= TTL
            }
            with open(CACHE_FILE, 'w') as f:
                json.dump(data, f)
        except Exception as e:
            log.warning(f"Не удалось сохранить кэш: {e}")


async def prune_caches():
    """Удаляет протухшие записи из кэша значений и картинок."""
    now = datetime.now()
    async with cache_lock:
        for key in [k for k, (ts, _, _) in cache.items() if now - ts > TTL]:
            del cache[key]
    for uid in [u for u, entry in img_cache.items() if now - entry[0] > TTL]:
        del img_cache[uid]
    for uid in [u for u, lock in img_locks.items() if not lock.locked() and u not in img_cache]:
        del img_locks[uid]
    for uid in [u for u, ts in _last_proof.items() if now - ts > PROOF_COOLDOWN]:
        del _last_proof[uid]
    log.info(f"Чистка кэша: значений {len(cache)}, картинок {len(img_cache)}")


async def periodic_cleanup():
    """Фоновая задача: чистка кэшей и сохранение на диск."""
    while True:
        await asyncio.sleep(CLEANUP_INTERVAL)
        try:
            await prune_caches()
            await save_cache_to_disk()
        except Exception as e:
            log.warning(f"Ошибка периодической чистки: {e}")


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


def _bell(mu: float, sigma: float, lo: int, hi: int, tail: float = 0.03) -> int:
    """
    Обычно значение около mu (нормальное распределение, обрезанное до [lo, hi]).
    С шансом tail — любое значение из [lo, hi], чтобы крайности были редкими, но возможными.
    """
    if random.random() < tail:
        return random.randint(lo, hi)
    return min(max(round(random.gauss(mu, sigma)), lo), hi)


def gen_w():
    v = _bell(78, 18, 0, 250)
    return v, _emo(v, EMO["w"])


# Чем длиннее, тем реже: вес каждого следующего сантиметра в COCK_DECAY раз меньше.
# При 0.5 каждый следующий сантиметр вдвое реже: 0 см 50%, 1 см 25%, 2 см 12.5%, ...
# (таблица — в README). 5 см и больше ≈ 3.1%, 10 см и больше ≈ 0.1%.
# Больше COCK_DECAY (напр. 0.7) — длинные чаще, меньше — реже.
COCK_DECAY = 0.5
COCK_VALUES = list(range(0, 51))
COCK_WEIGHTS = [COCK_DECAY ** v for v in COCK_VALUES]


def gen_c():
    v = random.choices(COCK_VALUES, weights=COCK_WEIGHTS)[0]
    return v, _emo(v, EMO["c"])


def gen_iq():
    v = _bell(100, 15, 50, 200)
    return v, _emo(v, EMO["iq"])


def gen_h():
    v = _bell(176, 9, 140, 220)
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
            spawn(save_cache_to_disk())

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
#    [InlineKeyboardButton(text="Пруф?", callback_data="proof_help")]
])

# ─────────── Генерация изображений ───────────
IMG_GEN_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-image:generateContent"


def gemini_headers() -> dict:
    """Ключ передаём заголовком, а не в URL: URL попадает в тексты исключений и логи."""
    return {"x-goog-api-key": GEMINI_API_KEY}


async def gemini_png(session: aiohttp.ClientSession, prompt: str) -> bytes:
    """Генерирует изображение через Gemini API."""
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY не задан для генерации изображений.")

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]}
    }

    async with session.post(IMG_GEN_URL, json=payload, headers=gemini_headers(), timeout=30) as resp:
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
        log.warning("Шрифт DejaVuSans не найден — кириллица на резервной картинке будет квадратами")
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
    y = 306
    for t in (f"Вес: {ctx['w']} кг", f"Длина: {ctx['c']} см",
              f"IQ: {ctx['iq']}", f"Рост: {ctx['h']} см"):
        d.text((10, y), t, font=font, fill="black")
        y += 22

    bio = io.BytesIO()
    img.save(bio, "PNG")
    bio.seek(0)
    return bio


# ─────────── Хостинг изображений ───────────
def image_mime(data: bytes) -> str:
    """Определяет тип картинки по сигнатуре (Gemini может вернуть JPEG)."""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    return "image/png"


async def upload_to_storage(img_data: bytes, filename: str) -> Optional[str]:
    """
    Storage Mode: Загружает изображение в Telegram канал.
    Возвращает file_id для последующего использования.
    """
    if not STORAGE_CHAT_ID:
        return None

    try:
        chat_id = int(STORAGE_CHAT_ID)
        msg = await bot.send_photo(
            chat_id,
            BufferedInputFile(img_data, filename),
            caption=f"🖼 {filename}"
        )
        file_id = msg.photo[-1].file_id
        log.info(f"Изображение загружено в Storage (file_id: {file_id[:20]}...)")
        return file_id
    except Exception as e:
        log.error(f"Ошибка загрузки в Storage: {e}")
        return None


async def store_image(img_data: bytes, uid: int) -> Optional[str]:
    """
    Storage Mode: загружает картинку в служебный канал и возвращает file_id.
    In-Memory (STORAGE_CHAT_ID не задан): None, картинка шлётся байтами.
    """
    if not STORAGE_CHAT_ID:
        return None
    ext = "jpg" if image_mime(img_data) == "image/jpeg" else "png"
    filename = f"whoami_{uid}_{int(datetime.now().timestamp())}.{ext}"
    return await upload_to_storage(img_data, filename)



# ─────────── Дневные лимиты и антиспам ───────────
# Счётчики в памяти — источник истины; файл нужен, чтобы пережить рестарт.
# Формат файла: {"date": "YYYY-MM-DD", "proof": N, "image": M}
_api_usage: Optional[dict] = None
_last_proof: Dict[int, datetime] = {}


def _load_usage(today_str: str) -> dict:
    try:
        if API_USAGE_FILE.exists():
            with open(API_USAGE_FILE, 'r') as f:
                loaded = json.load(f)
            if isinstance(loaded, dict) and isinstance(loaded.get("date"), str):
                return loaded
    except Exception as e:
        log.info(f"Файл {API_USAGE_FILE} не прочитан: {e}")
    return {"date": today_str}


async def consume_daily_quota(kind: str) -> bool:
    """Списывает один вызов из дневного лимита kind ("proof"/"image"). False — лимит исчерпан."""
    global _api_usage
    limit = DAILY_LIMITS[kind]
    async with api_usage_lock:
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        if _api_usage is None:
            _api_usage = _load_usage(today_str)
        if _api_usage.get("date") != today_str:
            log.info("Новый день. Сбрасываем счётчики лимитов.")
            _api_usage = {"date": today_str}

        used = int(_api_usage.get(kind, 0))
        if used >= limit:
            log.warning(f"Дневной лимит '{kind}' ({limit}) исчерпан.")
            return False

        _api_usage[kind] = used + 1
        try:
            with open(API_USAGE_FILE, 'w') as f:
                json.dump(_api_usage, f)
        except Exception as e:
            log.warning(f"Не удалось сохранить {API_USAGE_FILE}: {e}")

        log.info(f"Лимит '{kind}': {used + 1}/{limit}")
        return True


def proof_cooldown_left(uid: int) -> int:
    """Сколько секунд пользователю ждать до следующего /proof (0 — можно, время запоминается)."""
    now = datetime.now()
    last = _last_proof.get(uid)
    if last and now - last < PROOF_COOLDOWN:
        return int((PROOF_COOLDOWN - (now - last)).total_seconds()) + 1
    _last_proof[uid] = now
    return 0


# ─────────── Gemini: проверка фактов с поиском Google ───────────
# Поиск делает сам Gemini (инструмент google_search), отдельный Custom Search API
# не нужен — он закрывается 1 января 2027.
TEXT_GEN_URL_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
DEFAULT_TEXT_MODEL = "gemini-2.5-flash"
MAX_SOURCES = 5


def build_proof_prompt(text: str) -> str:
    return (
        "Проверь утверждение ниже, используя поиск Google.\n"
        "Сначала одной строкой вердикт: правда, неправда, частично правда или не удалось проверить. "
        "Затем коротко объясни почему, опираясь на найденные источники.\n"
        "Отвечай на языке утверждения. Форматирование — только простой Markdown: "
        "**жирный**, *курсив*, списки через «- ». Без таблиц, без заголовков и без ссылок в тексте — "
        "список источников добавляется автоматически.\n\n"
        f"Утверждение:\n\"\"\"\n{text}\n\"\"\""
    )


def extract_sources(candidate: dict) -> List[Tuple[str, str]]:
    """(название, URL) из groundingMetadata ответа Gemini, без дублей."""
    sources: List[Tuple[str, str]] = []
    seen = set()
    chunks = (candidate.get("groundingMetadata") or {}).get("groundingChunks") or []
    for ch in chunks:
        web = ch.get("web") or {}
        uri, title = web.get("uri"), web.get("title") or web.get("uri")
        if not uri or title in seen:
            continue
        seen.add(title)
        sources.append((title, uri))
        if len(sources) >= MAX_SOURCES:
            break
    return sources


async def proof_with_gemini(
    session: aiohttp.ClientSession,
    text: str,
    model_name: str = DEFAULT_TEXT_MODEL
) -> Tuple[bool, str, List[Tuple[str, str]]]:
    """
    Проверяет утверждение через Gemini с поиском Google.
    Возвращает (успех, текст ответа в Markdown или сообщение об ошибке, источники).
    """
    if not GEMINI_API_KEY:
        return False, "Функция недоступна: GEMINI_API_KEY не настроен.", []

    url = TEXT_GEN_URL_TEMPLATE.format(model_name=model_name)
    payload = {
        "contents": [{"parts": [{"text": build_proof_prompt(text)}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {"temperature": 0.3},
        "safetySettings": [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}
        ]
    }

    try:
        async with session.post(url, json=payload, headers=gemini_headers(), timeout=90) as resp:
            if resp.status != 200:
                body = (await resp.text())[:500]
                log.error(f"HTTP ошибка Gemini: {resp.status} {body}")
                return False, f"Ошибка API Gemini ({resp.status}). Попробуйте позже.", []
            data = await resp.json()
    except asyncio.TimeoutError:
        log.error("Gemini: таймаут")
        return False, "Gemini не ответил вовремя. Попробуйте позже.", []
    except aiohttp.ClientError as e:
        log.error(f"Gemini: сетевая ошибка {type(e).__name__}")
        return False, "Не удалось связаться с Gemini. Попробуйте позже.", []

    candidates = data.get("candidates") or []
    if candidates:
        cand = candidates[0]
        parts = (cand.get("content") or {}).get("parts") or []
        answer = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
        if answer:
            return True, answer, extract_sources(cand)

    log.error(f"Неожиданный ответ Gemini: {str(data)[:500]}")
    return False, "Gemini не вернул ответ.", []


# ─────────── Markdown → Telegram HTML ───────────
def md_to_tg_html(text: str) -> str:
    """
    Переводит простой Markdown от Gemini в HTML, который понимает Telegram.
    Сначала экранируем всё (знаки < > & в тексте не ломают разметку),
    затем превращаем разметку в теги. Теги не пересекают границы строк,
    поэтому split_message по переводам строк их не разрывает.
    """
    s = html.escape(text, quote=False)
    s = re.sub(r"^#{1,6}\s*(.+)$", r"<b>\1</b>", s, flags=re.M)       # заголовки → жирный
    s = re.sub(r"^(\s*)[-*+]\s+", r"\1• ", s, flags=re.M)               # списки
    s = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^\n*]+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"__([^\n_]+?)__", r"<b>\1</b>", s)
    s = re.sub(r"(?<![\w*])\*(?![\s*])([^\n*]+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", s)
    s = re.sub(r"\[([^\]\n]+)\]\((https?://[^\s)\"<>]+)\)", r'<a href="\2">\1</a>', s)
    return s


def format_sources(sources: List[Tuple[str, str]]) -> str:
    if not sources:
        return ""
    lines = [
        f'{i}. <a href="{html.escape(uri, quote=True)}">{html.escape(title)}</a>'
        for i, (title, uri) in enumerate(sources, 1)
    ]
    return "\n\n<b>Источники:</b>\n" + "\n".join(lines)


def html_to_plain(text: str) -> str:
    """Запасной вариант, если Telegram не принял HTML: убираем теги."""
    return html.unescape(re.sub(r"<[^>]+>", "", text))


# ─────────── Обработчики команд ───────────
@dp.message(CommandStart())
async def start(m: types.Message):
    await m.answer("Добро пожаловать!", reply_markup=KB, parse_mode=None)


@dp.message(Command("menu"))
async def menu(m: types.Message):
    await m.answer("Выберите действие:", reply_markup=KB, parse_mode=None)


@dp.message(Command("cock"))
async def cmd_cock(message: types.Message):
    """То же, что кнопка «Хуеметр»."""
    user = message.from_user
    if not user:
        return
    name = user.full_name or user.username or str(user.id)
    val, emo = await cached_val(user.id, "cock")
    await message.reply(f"{name}, ваш хуй: {val} см {emo}", parse_mode=None)


def _display_name(user: types.User) -> str:
    return f"@{user.username}" if user.username else user.full_name


@dp.message(Command("pizdica"))
async def cmd_pizdica(message: types.Message, command: CommandObject):
    p1_user = message.from_user
    if not p1_user:
        return
    p1_name = _display_name(p1_user)

    p2_name = None
    is_self = False
    if message.reply_to_message and message.reply_to_message.from_user:
        p2_user = message.reply_to_message.from_user
        p2_name = _display_name(p2_user)
        is_self = p2_user.id == p1_user.id
    elif command.args:
        p2_name = command.args.strip()
        is_self = bool(p1_user.username) and \
            p2_name.lstrip('@').lower() == p1_user.username.lower()

    if is_self:
        await message.reply("С самим собой не пиздятся. Найди соперника.", parse_mode=None)
        return

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
    if cb.message is None:
        # Сообщение недоступно (слишком старое или inline) — отвечать некуда
        await safe_cb_answer(cb, "Сообщение недоступно, вызовите /menu")
        return
    chat_id = cb.message.chat.id
    act = cb.data

    # Отвечаем на callback сразу: у Telegram на это ~15 секунд,
    # а генерация картинки в Gemini может идти до 30 секунд
    await safe_cb_answer(cb, "Рисую портрет…" if act == "whoami" else None)

    if act == "proof_help":
        await bot.send_message(
            chat_id,
            "Чтобы я поискал информацию:\n"
            "— /proof ваш текст\n"
            "— Или ответьте на сообщение командой /proof",
            parse_mode=None
        )
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
        return

    if act == "whoami":
        w, wt = await cached_val(uid, "weight")
        c, ct = await cached_val(uid, "cock")
        iq, iqt = await cached_val(uid, "iq")
        h, ht = await cached_val(uid, "height")
        ctx = {"w": w, "c": c, "iq": iq, "h": h, "name": name}
        stats = (w, c, iq, h)

        # Один lock на пользователя: повторное нажатие ждёт первую генерацию
        # и берёт готовую картинку из кэша
        lock = img_locks.setdefault(uid, asyncio.Lock())
        async with lock:
            now = datetime.now()
            entry = img_cache.get(uid)
            # Картинка годится, только если на ней те же цифры, что в подписи
            if entry and now - entry[0] <= TTL and entry[1] == stats:
                log.info(f"Изображение для UID {uid} из кэша.")
                _, _, img_data, img_ref = entry
            else:
                log.info(f"Генерация изображения для UID {uid}...")
                img_data = None
                if GEMINI_API_KEY and await consume_daily_quota("image"):
                    try:
                        img_data = (await make_image(ctx)).getvalue()
                    except Exception as e:
                        log.error(f"Ошибка Gemini → резервный PIL: {e}")
                if img_data is None:
                    img_data = render_pil(ctx).getvalue()
                log.info(f"Изображение сгенерировано ({len(img_data)} байт).")

                img_ref = await store_image(img_data, uid)
                img_cache[uid] = (now, stats, img_data, img_ref)

        caption = (
            f"Мой вес: {w} кг {wt}\n"
            f"Мой хуй: {c} см {ct}\n"
            f"Мой IQ: {iq} {iqt}\n"
            f"Мой рост: {h} см {ht}"
        )
        await send_whoami_photo(chat_id, img_data, img_ref, caption)


async def safe_cb_answer(cb: types.CallbackQuery, text: Optional[str] = None):
    """cb.answer(), не падающий на устаревшем callback."""
    try:
        await cb.answer(text)
    except TelegramBadRequest as e:
        log.info(f"cb.answer не выполнен: {e}")


async def send_whoami_photo(chat_id: int, img_data: bytes, img_ref: Optional[str], caption: str):
    """
    Отправляет картинку по file_id (Storage Mode), а если его нет
    или он не сработал — загружает байты напрямую.
    """
    if img_ref:
        try:
            await bot.send_photo(chat_id, img_ref, caption=caption, parse_mode=None)
            return
        except Exception as e:
            log.warning(f"Не удалось отправить по ссылке ({img_ref[:60]}): {e}. Шлю байты.")
    ext = "jpg" if image_mime(img_data) == "image/jpeg" else "png"
    await bot.send_photo(
        chat_id,
        BufferedInputFile(img_data, f"whoami.{ext}"),
        caption=caption,
        parse_mode=None
    )


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
    text_to_proof = text_to_proof[:PROOF_MAX_CHARS]

    # Антиспам: пауза на пользователя и дневной лимит на весь бот
    if message.from_user:
        wait = proof_cooldown_left(message.from_user.id)
        if wait:
            await message.reply(f"Подождите {wait} с перед следующим /proof.", parse_mode=None)
            return
    if not await consume_daily_quota("proof"):
        await message.reply(
            f"Дневной лимит проверок ({DAILY_LIMITS['proof']}) исчерпан. Попробуйте завтра.",
            parse_mode=None
        )
        return

    processing_msg = None
    try:
        processing_msg = await message.reply("Ищу и проверяю…", parse_mode=None)

        async with aiohttp.ClientSession() as session:
            ok, answer, sources = await proof_with_gemini(session, text_to_proof)

        if not ok:
            await message.reply(answer, parse_mode=None)
            return

        log.info(f"Ответ Gemini: {answer[:100]}... (источников: {len(sources)})")
        final = md_to_tg_html(answer) + format_sources(sources)

        # Каждую часть шлём отдельно и откатываемся на plain text только для неё,
        # чтобы уже отправленные части не дублировались
        for chunk in split_message(final):
            try:
                await message.answer(chunk, parse_mode=ParseMode.HTML,
                                     link_preview_options=types.LinkPreviewOptions(is_disabled=True))
            except TelegramBadRequest as e:
                log.warning(f"Ошибка HTML: {e}. Отправляю часть как текст.")
                await message.answer(html_to_plain(chunk), parse_mode=None)

    except Exception as e:
        log.error(f"/proof упал: {type(e).__name__}: {e}", exc_info=True)
        try:
            await message.reply("Не удалось выполнить проверку, попробуйте позже.", parse_mode=None)
        except Exception:
            pass
    finally:
        # Служебное сообщение убираем в любом случае, даже если всё упало
        if processing_msg:
            try:
                await processing_msg.delete()
            except Exception:
                pass


def split_message(text: str, limit: int = 4096) -> List[str]:
    """
    Режет текст на части ≤ limit, по возможности по границе абзаца/строки,
    чтобы не разрывать HTML-теги посередине.
    """
    chunks = []
    while len(text) > limit:
        cut = text.rfind("\n\n", 0, limit)
        if cut <= 0:
            cut = text.rfind("\n", 0, limit)
        if cut <= 0:
            cut = text.rfind(" ", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        chunks.append(text)
    return chunks


# ─────────── Обработчик добавления в чат ───────────
# my_chat_member приходит, когда меняется статус самого бота в чате
@dp.my_chat_member(ChatMemberUpdatedFilter(member_status_changed=JOIN_TRANSITION))
async def added(ev: ChatMemberUpdated):
    if ev.chat.type == "private":
        return  # в личке /start уже показывает меню
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
    loop = asyncio.get_running_loop()
    if sys.platform != "win32":
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(
                sig,
                lambda s=sig: spawn(shutdown_handler(s))
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
            BotCommand(command="cock", description="Хуеметр"),
            BotCommand(command="pizdica", description="Дуэль"),
            BotCommand(command="proof", description="Проверить информацию"),
        ],
        scope=BotCommandScopeDefault()
    )

    await bot.delete_webhook(drop_pending_updates=True)

    cleanup_task = spawn(periodic_cleanup())

    # Polling с graceful shutdown; явно подписываемся на используемые типы апдейтов
    # (в т.ч. my_chat_member для приветствия при добавлении в группу)
    polling_task = asyncio.create_task(
        dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    )

    # Ждём сигнал завершения или окончание polling
    done, pending = await asyncio.wait(
        [polling_task, asyncio.create_task(shutdown_event.wait())],
        return_when=asyncio.FIRST_COMPLETED
    )

    # Отменяем pending задачи
    for task in [*pending, cleanup_task]:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # Сохраняем кэш перед выходом
    await save_cache_to_disk()
    await bot.session.close()
    log.info("Кэш сохранён. Бот остановлен.")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
