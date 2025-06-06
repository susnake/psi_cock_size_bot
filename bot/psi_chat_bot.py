#!/usr/bin/env python3
# psi_chat_bot.py — Telegram-бот «Кто я?» с кэшированием картинки на 6 часов
# Python 3.10+, aiogram 3.7+, Pydantic v2

import os, sys, io, random, base64, hashlib, asyncio, logging, requests
import html 
import re 
import json 
from datetime import datetime, timedelta
from typing import Dict, Tuple, Callable, List, Optional

# --- НОВЫЙ ИМПОРТ ДЛЯ ПАРСИНГА СТРАНИЦ ---
try:
    from bs4 import BeautifulSoup
except ImportError:
    sys.exit("Библиотека BeautifulSoup4 не установлена. Пожалуйста, установите ее: pip install beautifulsoup4 lxml")


from dotenv import load_dotenv; load_dotenv()
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
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("psi_chat_bot")

API_TOKEN        = os.getenv("psi_chat_bot")
GEMINI_API_KEY   = os.getenv("GEMINI_API_KEY")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY") 
GOOGLE_CSE_ID    = os.getenv("GOOGLE_CSE_ID")    

if not API_TOKEN:
    sys.exit("❌ psi_chat_bot не найден в .env")
if not GEMINI_API_KEY:
    log.warning("⚠️ GEMINI_API_KEY не найден в .env. Функции, использующие Gemini, не будут работать.")


bot = Bot(token=API_TOKEN,
          default=DefaultBotProperties(parse_mode=ParseMode.HTML)) 
dp  = Dispatcher()

BOT_USERNAME = ""

# ─────────── Константы и глобальные переменные для лимитов API ───────────
SEARCH_API_DAILY_LIMIT = 100
API_USAGE_FILE = "api_usage.json"
api_usage_lock = asyncio.Lock()


# ─────────── Кэш чисел и картинок ───────────
TTL = timedelta(hours=6)
cache: Dict[str, Tuple[datetime, int, str]] = {}
img_cache: Dict[int, Tuple[datetime, bytes]] = {}

# ─────────── Генераторы значений ───────────
# ИСПРАВЛЕНО: Все значения теперь в словаре EMO
EMO = {
    "w":{"0":"🪶","1-49":"🦴","50-99":"⚖️","100-149":"🏋️‍♂️",
         "150-199":"🐖","200-249":"🤯","250":"🐘"},
    "c":{"0":"🤤","1-9":"🤮","10-19":"🥴","20-29":"😐",
         "30-39":"😲","40-49":"🤯","50":"🫡"},
    "iq":{"50-69":"🤡","70-89":"😕","90-109":"🙂",
          "110-129":"😎","130-149":"🤓","150-199":"🧠", "200":"👨‍🔬"},
    "h":{"140-149":"🦗","150-169":"🙂","170-189":"😃","190-219":"🏀", "220":"🇷🇸"}
}
def _emo(val:int, tbl):
    for rng,e in tbl.items():
        if "-" in rng:
            a,b = map(int,rng.split("-"));
            if a<=val<=b: return e
        elif int(rng)==val:
            return e
    return ""

# ИСПРАВЛЕНО: Функции-генераторы приведены к единому виду
def gen_w():  v=random.randint(0,250);  return v,_emo(v,EMO["w"])
def gen_c():  v=random.randint(0,50);   return v,_emo(v,EMO["c"])
def gen_iq(): v=random.randint(50,200); return v,_emo(v,EMO["iq"])
def gen_h():  v=random.randint(140,220);return v,_emo(v,EMO["h"])

gens = {"weight":gen_w, "cock":gen_c, "iq":gen_iq, "height":gen_h}

def cached_val(uid:int,label:str):
    now=datetime.now(); key=f"{label}_{uid}"
    if key in cache and now-cache[key][0] <= TTL:
        _,v,e = cache[key]
    else:
        v,e = gens[label](); cache[key]=(now,v,e)
    return v,e

KB = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="Вес",       callback_data="weight"),
     InlineKeyboardButton(text="Хуеметр",   callback_data="cock")],
    [InlineKeyboardButton(text="IQ",        callback_data="iq"),
     InlineKeyboardButton(text="Рост",      callback_data="height")],
    [InlineKeyboardButton(text="Хто Я?",    callback_data="whoami")],
    [InlineKeyboardButton(text="Пруф?",     callback_data="proof_help")]
])

IMG_GEN_URL="https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash-preview-image-generation:generateContent"
def gemini_png(prompt:str)->bytes:
    if not GEMINI_API_KEY: raise RuntimeError("GEMINI_API_KEY не задан для генерации изображений.")
    r=requests.post(f"{IMG_GEN_URL}?key={GEMINI_API_KEY}",headers={"Content-Type":"application/json"},json={"contents":[{"parts":[{"text":prompt}]}],"generationConfig":{"responseModalities":["TEXT","IMAGE"]}}, timeout=30)
    if r.status_code!=200: log.error(f"Gemini image API HTTP Error {r.status_code}: {r.text}"); raise RuntimeError(f"Ошибка API генерации изображений: HTTP {r.status_code}")
    data=r.json()
    if data["candidates"][0].get("finishReason")=="IMAGE_SAFETY": raise RuntimeError("IMAGE_SAFETY")
    for part in data["candidates"][0]["content"]["parts"]:
        if "inlineData" in part: return base64.b64decode(part["inlineData"]["data"])
    raise RuntimeError("Нет изображения в ответе Gemini")
def prompt_primary(ctx:dict)->str: return ("Draw a clean flat cartoon avatar, transparent PNG. " f"Height {ctx['h']} cm, weight {ctx['w']} kg. " f"Floating yellow tape-measure on the right shows “{ctx['c']} cm”. " f"Thought bubble: “IQ {ctx['iq']}”. " f"Write “{ctx['name']}” under the feet. Fully clothed. No nudity.")
def prompt_safe(ctx:dict)->str: return ("Draw a clean flat cartoon avatar, transparent PNG. " f"Height {ctx['h']} cm, weight {ctx['w']} kg. " f"Thought bubble: “IQ {ctx['iq']}”. " f"Write “{ctx['name']}” under the feet. Fully clothed.")
async def make_image(ctx:dict)->io.BytesIO:
    loop=asyncio.get_running_loop()
    try: data=await loop.run_in_executor(None, gemini_png, prompt_primary(ctx))
    except RuntimeError as e:
        if "IMAGE_SAFETY" in str(e): log.warning("Основной промпт для Gemini (изображение) не прошел (IMAGE_SAFETY), пробуем безопасный промпт."); data=await loop.run_in_executor(None, gemini_png, prompt_safe(ctx))
        else: raise 
    bio=io.BytesIO(data); bio.seek(0); return bio
def render_pil(ctx:dict)->io.BytesIO:
    try: font=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",18)
    except IOError: font=ImageFont.load_default()
    img=Image.new("RGB",(400,400),"white"); d=ImageDraw.Draw(img); d.text((10,5),ctx["name"],font=font,fill="black"); head,r=(200,100),40; d.ellipse((head[0]-r,head[1]-r,head[0]+r,head[1]+r),outline="black",width=2); d.rectangle((180,140,220,250),outline="black",width=2); d.line((180,140,140,180),fill="black",width=2); d.line((220,140,260,180),fill="black",width=2); d.line((200,250,170,320),fill="black",width=2); d.line((200,250,230,320),fill="black",width=2); d.line((200,250,200,250+ctx['c']),fill="black",width=2); y=330
    for t in (f"Вес: {ctx['w']} кг",f"Длина: {ctx['c']} см",f"IQ: {ctx['iq']}",f"Рост: {ctx['h']} см"): d.text((10,y),t,font=font,fill="black"); y+=18
    bio=io.BytesIO(); img.save(bio,"PNG"); bio.seek(0); return bio

# --- НОВАЯ ФУНКЦИЯ ДЛЯ УЧЕТА ЛИМИТОВ API ---
async def check_api_limit_and_increment() -> Tuple[bool, str]:
    """Проверяет и обновляет дневной лимит использования API."""
    async with api_usage_lock:
        today_str = datetime.utcnow().strftime("%Y-%m-%d")
        usage_data = {"date": today_str, "count": 0}
        
        try:
            with open(API_USAGE_FILE, 'r') as f:
                usage_data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            log.info(f"Файл {API_USAGE_FILE} не найден или пуст. Создаем новый.")
        
        if usage_data.get("date") != today_str:
            log.info(f"Новый день. Сбрасываем счетчик API. Старая дата: {usage_data.get('date')}, новая: {today_str}")
            usage_data = {"date": today_str, "count": 0}
            
        if usage_data["count"] >= SEARCH_API_DAILY_LIMIT:
            log.warning(f"Дневной лимит поиска ({SEARCH_API_DAILY_LIMIT}) исчерпан. Запросов сегодня: {usage_data['count']}")
            return False, f"Дневной лимит запросов к поисковому API ({SEARCH_API_DAILY_LIMIT}) исчерпан. Попробуйте завтра."
            
        usage_data["count"] += 1
        
        with open(API_USAGE_FILE, 'w') as f:
            json.dump(usage_data, f)
            
        log.info(f"Запрос к поисковому API. Использовано сегодня: {usage_data['count']}/{SEARCH_API_DAILY_LIMIT}")
        return True, ""

# --- НОВЫЕ ФУНКЦИИ ДЛЯ ПОИСКА И ПАРСИНГА ---
def _fetch_and_parse_url(url: str) -> str:
    """Скачивает и парсит HTML-страницу, возвращая чистый текст."""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'lxml')
        
        for tag in soup(['script', 'style', 'header', 'footer', 'nav', 'aside']):
            tag.decompose()
        
        text = soup.get_text(separator='\n', strip=True)
        return "\n".join(line.strip() for line in text.splitlines() if line.strip())
    except Exception as e:
        log.error(f"Не удалось получить или распарсить контент со страницы {url}: {e}")
        return ""

def search_google(query: str) -> list[dict]:
    """Выполняет поиск и возвращает список словарей с результатами."""
    if not GOOGLE_API_KEY or not GOOGLE_CSE_ID:
        raise RuntimeError("GOOGLE_API_KEY или GOOGLE_CSE_ID не настроены.")
    
    search_url = "https://www.googleapis.com/customsearch/v1"
    params = { "key": GOOGLE_API_KEY, "cx": GOOGLE_CSE_ID, "q": query, "num": 3, "sort": "date", "dateRestrict": "d1" }

    try:
        response = requests.get(search_url, params=params, timeout=10)
        response.raise_for_status()
        search_results = response.json()
        
        if "items" in search_results:
            log.info(f"Найденные источники для передачи в Gemini: {[item['link'] for item in search_results['items']]}")
            return search_results["items"]
        else:
            log.info("Релевантных веб-страниц не найдено.")
            return []
    except Exception as e:
        log.error(f"Ошибка при поиске в Google: {e}", exc_info=True)
        return []

# ─────────── Gemini (Генерация текста для Пруф?) ───────────
TEXT_GEN_URL_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
DEFAULT_TEXT_MODEL = "gemini-1.5-flash-latest"

def _get_clean_search_query(text: str, model_name: str = DEFAULT_TEXT_MODEL) -> str:
    """Использует Gemini для извлечения ключевой поисковой фразы из текста."""
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY не задан для извлечения поисковой фразы.")
    
    prompt = (
        "Из следующего текста выдели главную тему в виде короткого, но информативного поискового запроса из 3-6 слов. "
        "Убери любой шум (например, 'пишет канал', 'сообщили', 'по словам очевидцев'). "
        "Верни только сам поисковый запрос, без кавычек и лишних пояснений.\n\n"
        f"Текст для анализа: \"{text}\"\n\n"
        "Очищенный поисковый запрос:"
    )

    target_url = TEXT_GEN_URL_TEMPLATE.format(model_name=model_name) + f"?key={GEMINI_API_KEY}"
    headers = {"Content-Type": "application/json"}
    payload = {"contents": [{"parts": [{"text": prompt}]}],"generationConfig": {"temperature": 0.0}} 
    
    try:
        r = requests.post(target_url, headers=headers, json=payload, timeout=10)
        r.raise_for_status()
        data = r.json()
        if data.get("candidates") and data["candidates"][0].get("content"):
            return data["candidates"][0]["content"]["parts"][0]["text"].strip()
        return text 
    except Exception as e:
        log.error(f"Ошибка при очистке поискового запроса через Gemini: {e}")
        return text

def _summarize_with_gemini(original_query: str, search_context: Optional[str], model_name: str = DEFAULT_TEXT_MODEL) -> str:
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY не задан для генерации текста.")

    if search_context:
        prompt_instruction = (
            "Ты — ИИ-ассистент, анализирующий поисковую выдачу. Тебе предоставлен оригинальный запрос пользователя и ПОЛНЫЙ текст нескольких найденных по этому запросу веб-страниц.\n"
            "Твоя задача:\n"
            "1. Внимательно изучи предоставленный текст со страниц. **Оцени релевантность каждого источника. Если источник очевидно не относится к теме запроса или его описание (сниппет) не содержит полезной информации, мысленно проигнорируй его.**\n"
            "2. На основе **только релевантных источников** напиши обобщенный и структурированный ответ на оригинальный запрос пользователя.\n"
            "3. **Не упоминай в своем ответе источники, которые ты счел нерелевантными или проигнорировал.** Не пиши фразы вроде 'Источник X не содержит информации' или 'этот источник нерелевантен'.\n"
            "4. **Если ни одна из страниц не содержит релевантной информации, просто напиши, что по данному запросу не удалось найти подтверждающей информации в найденных источниках.**\n"
            "5. В своем ответе обязательно ссылайся на те источники, которые ты **использовал**, используя HTML-тег `<a href='URL'>название источника</a>`. Твои утверждения должны быть подкреплены найденной информацией.\n"
            "6. **Критически важно: не упоминай названия организаций или изданий как источники, если ты не можешь предоставить на них прямую ссылку из найденной информации.** Вместо того чтобы писать 'сообщили в пресс-службе', перефразируй это как 'согласно одному из источников, ...' и поставь ссылку на этот источник. Любое упоминание источника должно сопровождаться HTML-ссылкой.\n"
            "7. **НЕ пересказывай просто текст со страниц.** Твоя цель — синтезировать информацию в связный и полезный ответ.\n"
            "Форматируй свой ответ, используя HTML-теги, поддерживаемые Telegram: `<b>`, `<i>`, `<u>`, `<s>`, `<tg-spoiler>`, `<code>`, `<pre>`, `<a href='URL'>`.\n"
            "Отвечай на языке оригинального запроса пользователя.\n"
            "Вот данные для анализа:\n"
            "--- НАЧАЛО ДАННЫХ ---\n"
            f"<b>Оригинальный запрос пользователя:</b> {html.escape(original_query)}\n\n"
            "<b>Найденная в поиске информация:</b>\n{search_context}\n"
            "--- КОНЕЦ ДАННЫХ ---\n\n"
            "Твой обобщенный ответ (сформированный только на основе релевантных источников):"
        )
        full_prompt = prompt_instruction.format(search_context=search_context)
    else:
        prompt_instruction = (
            "Ты — информационный ассистент и исследователь. Твоя задача — предоставить точный и информативный ответ на запрос пользователя. "
            "Постарайся подкрепить ключевые утверждения ссылками на общеизвестные авторитетные веб-ресурсы, если это возможно и релевантно. Используй HTML-тег `<a href='URL'>текст ссылки</a>` для всех URL. "
            "Текст пользователя: "
        )
        full_prompt = f"{prompt_instruction}\"{original_query}\""
    
    target_url = TEXT_GEN_URL_TEMPLATE.format(model_name=model_name) + f"?key={GEMINI_API_KEY}"
    headers = {"Content-Type": "application/json"}
    payload = {
        "contents": [{"parts": [{"text": full_prompt}]}],"generationConfig": {"temperature": 0.3},
        "safetySettings": [
            { "category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE" },
            { "category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE" },
            { "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE" },
            { "category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE" }
        ]
    }
    
    try:
        r = requests.post(target_url, headers=headers, json=payload, timeout=90) 
        r.raise_for_status()
        data = r.json()
        if data.get("candidates") and data["candidates"][0].get("content") and data["candidates"][0]["content"].get("parts") and data["candidates"][0]["content"]["parts"][0].get("text"): 
            return data["candidates"][0]["content"]["parts"][0]["text"].strip()
        log.error(f"Неожиданный формат ответа от Gemini (текст): {data}"); return "Не удалось получить структурированный ответ от Gemini."
    except requests.exceptions.HTTPError as e: error_text_short = f"HTTP {e.response.status_code}" if e.response else "HTTP ошибка"; log.error(f"HTTP ошибка при запросе к Gemini API (текст): {e}. Ответ сервера: {e.response.text if e.response else 'Нет ответа'}"); return f"Ошибка API ({error_text_short}) при обращении к сервису."
    except requests.exceptions.RequestException as e: log.error(f"Сетевая ошибка при запросе к Gemini API (текст): {e}"); return "Сетевая ошибка при обращении к сервису. Пожалуйста, проверьте ваше соединение или попробуйте позже."
    except Exception as e: log.error(f"Неожиданная ошибка в _summarize_with_gemini: {e}", exc_info=True); return "Произошла неожиданная ошибка при обработке вашего запроса."

# ─────────── Обработчики ───────────
@dp.message(CommandStart())
async def start(m:types.Message):
    await m.answer("Добро пожаловать!", reply_markup=KB, parse_mode=None)

@dp.message(Command("menu"))
async def menu(m:types.Message):
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
            "Для дуэли используйте команду в ответ на чье-либо сообщение или укажите оппонента после команды, например:\n"
            "`/pizdica @username`\n"
            "`/pizdica Текст`",
            parse_mode=None
        )


@dp.callback_query(F.data.in_({"weight","cock","iq","height","whoami", "proof_help"}))
async def callbacks(cb:types.CallbackQuery):
    uid  = cb.from_user.id; name = cb.from_user.full_name or cb.from_user.username or str(uid); chat_id = cb.message.chat.id; act  = cb.data
    if act == "proof_help": await cb.message.answer("Чтобы я поискал информацию по вашему тексту или вопросу:\n— Отправьте команду: `/proof ваш текст или вопрос`\n— Или ответьте на нужное сообщение командой `/proof`", parse_mode=None); await cb.answer(); return
    
    if act in ("weight", "cock", "iq", "height"):
        act_rus_map = {"weight": "вес", "cock": "хуй", "iq": "IQ", "height": "рост"}
        act_display_name = act_rus_map.get(act, act)
        val,emo = cached_val(uid,act)
        unit="кг" if act=="weight" else "см"
        await bot.send_message(chat_id,f"{name}, ваш {act_display_name}: {val} {unit} {emo}", parse_mode=None)
        await cb.answer()
        return

    if act == "whoami":
        w, wt  = cached_val(uid,"weight")
        c, ct  = cached_val(uid,"cock")
        iq, iqt = cached_val(uid,"iq")
        h, ht  = cached_val(uid,"height")
        ctx  = {"w":w,"c":c,"iq":iq,"h":h,"name":name}

        now=datetime.now()
        img_data_to_send: bytes
        if uid in img_cache and now-img_cache[uid][0] <= TTL: log.info(f"Изображение для UID {uid} найдено в кэше."); img_data_to_send = img_cache[uid][1]
        else: log.info(f"Генерация изображения для UID {uid}..."); bio_result: io.BytesIO;
        try: bio_result = await make_image(ctx); img_data_to_send = bio_result.getvalue()
        except Exception as e: log.error("Ошибка Gemini (изображение) → резервный PIL: %s", e, exc_info=True); bio_result = render_pil(ctx); img_data_to_send = bio_result.getvalue()
        img_cache[uid]=(now,img_data_to_send); log.info(f"Изображение для UID {uid} сгенерировано и закэшировано ({len(img_data_to_send)} байт).")
        
        caption_text = (f"Мой вес: {w} кг {wt}\n" f"Мой хуй: {c} см {ct}\n" f"Мой IQ: {iq} {iqt}\n" f"Мой рост: {h} см {ht}")
        await bot.send_photo(chat_id,BufferedInputFile(img_data_to_send,"whoami.png"), caption=caption_text, parse_mode=None); await cb.answer()


@dp.message(Command("proof"))
async def proof_command_handler(message: types.Message, command: CommandObject):
    loop = asyncio.get_running_loop()

    if not GEMINI_API_KEY:
        await message.reply("Функция анализа недоступна: GEMINI_API_KEY не настроен.", parse_mode=None)
        return
    
    text_to_proof = None 
    log.info(f"Proof command received. Message ID: {message.message_id}, Chat ID: {message.chat.id}, User ID: {message.from_user.id}")

    if command.args: text_to_proof = command.args.strip(); log.info(f"/proof: Используем аргументы команды: '{text_to_proof}'")
    elif message.quote and isinstance(message.quote, TextQuote) and message.quote.text: text_to_proof = message.quote.text.strip(); log.info(f"/proof: Используем текст из ЯВНОЙ ЦИТАТЫ (message.quote.text): '{text_to_proof}'")
    elif message.reply_to_message: 
        replied_msg = message.reply_to_message; log.info(f"/proof: Используем как ответ на сообщение ID: {replied_msg.message_id}, тип контента: {replied_msg.content_type}");
        if replied_msg.text: text_to_proof = replied_msg.text.strip(); log.info(f"/proof: Извлечен текст из reply_to_message.text: '{text_to_proof}'")
        if (not text_to_proof) and replied_msg.caption: text_to_proof = replied_msg.caption.strip(); log.info(f"/proof: Извлечен текст из reply_to_message.caption: '{text_to_proof}'")
        if not text_to_proof: log.warning("Сообщение, на которое ответили, не содержит ни .text, ни .caption.")
    else: log.info("/proof использован без аргументов и не как ответ.")

    if not text_to_proof:
        log.info("/proof: Текст для обработки не найден, отправляем сообщение помощи.")
        await message.reply("Пожалуйста, укажите текст (через аргумент команды, цитату или ответом на сообщение с текстом/подписью).", parse_mode=None)
        return

    log.info(f"/proof: Исходный текст для обработки: '{text_to_proof}'")

    MIN_PROOF_TEXT_LENGTH = 10 
    if len(text_to_proof) < MIN_PROOF_TEXT_LENGTH:
        await message.reply(f"Текст для поиска информации слишком короткий (минимум {MIN_PROOF_TEXT_LENGTH} символов).", parse_mode=None)
        return
    
    can_use_search = GOOGLE_API_KEY and GOOGLE_CSE_ID
    
    search_context = None
    if can_use_search:
        is_limit_ok, limit_message = await check_api_limit_and_increment()
        if not is_limit_ok: await message.reply(limit_message, parse_mode=None); return
        
        processing_message = await message.reply("Формирую поисковый запрос...", parse_mode=None)
        
        clean_query = await loop.run_in_executor(None, _get_clean_search_query, text_to_proof)
        log.info(f"/proof: Очищенный поисковый запрос: '{clean_query}'")

        await processing_message.edit_text(f"Ищу в Google по запросу: \"{clean_query}\"...", parse_mode=None)
        search_results = await loop.run_in_executor(None, search_google, clean_query)
        
        search_context = ""
        if isinstance(search_results, list) and search_results:
            await processing_message.edit_text("Загружаю и анализирую найденные страницы...", parse_mode=None)
            
            tasks = []
            for result in search_results: 
                tasks.append(loop.run_in_executor(None, _fetch_and_parse_url, result['link']))
            
            fetched_contents = await asyncio.gather(*tasks)

            context_parts = []
            for i, (result, content) in enumerate(zip(search_results, fetched_contents)):
                if content: 
                    context_parts.append(
                        f"<b>Источник {i+1}:</b> <a href='{result['link']}'>{html.escape(result['title'])}</a>\n"
                        f"<i>Сниппет:</i> {html.escape(result.get('snippet', ''))}\n"
                        f"<b>Полный текст источника {i+1}:</b>\n{html.escape(content[:1500])}...\n" 
                    )
            search_context = "\n\n---\n\n".join(context_parts) if context_parts else "По вашему запросу не найдено релевантных веб-страниц."
        
        else: 
            search_context = "Поиск по вашему запросу не дал результатов."
    else:
        log.info("Ключи Google Search не найдены. Выполняю анализ без поиска.")
        processing_message = await message.reply("Анализирую текст...", parse_mode=None)

    await processing_message.edit_text("Формирую итоговый ответ...", parse_mode=None)
    final_answer = await loop.run_in_executor(None, _summarize_with_gemini, text_to_proof, search_context)
    log.info(f"Сырой ответ Gemini для /proof: |||{final_answer}|||")
    
    final_proof_output = html.unescape(final_answer).strip()
    final_proof_output = final_proof_output.replace('\\n', '\n').strip()
    log.info(f"Обработанный ответ Gemini для /proof: |||{final_proof_output}|||")

    await processing_message.delete()
    
    TELEGRAM_MSG_CHUNK_LIMIT = 4096 
    if not final_proof_output: await message.answer("Не удалось получить ответ или ответ пуст.", parse_mode=None); return

    try:
        if len(final_proof_output) > TELEGRAM_MSG_CHUNK_LIMIT:
            for i in range(0, len(final_proof_output), TELEGRAM_MSG_CHUNK_LIMIT): await message.answer(final_proof_output[i:i+TELEGRAM_MSG_CHUNK_LIMIT], parse_mode=ParseMode.HTML) 
        else: await message.answer(final_proof_output, parse_mode=ParseMode.HTML)
    except TelegramBadRequest as e:
        log.warning(f"Ошибка парсинга HTML при отправке ответа Gemini: {e}. Попытка отправить как простой текст.")
        if len(final_proof_output) > TELEGRAM_MSG_CHUNK_LIMIT: 
            for i in range(0, len(final_proof_output), TELEGRAM_MSG_CHUNK_LIMIT): await message.answer(final_proof_output[i:i+TELEGRAM_MSG_CHUNK_LIMIT], parse_mode=None)
        else: await message.answer(final_proof_output, parse_mode=None)

@dp.chat_member(F.new_chat_members.is_bot & F.new_chat_members.id == bot.id) # type: ignore
async def added(ev:ChatMemberUpdated):
    await bot.send_message(ev.chat.id,"Привет! Я бот.",reply_markup=KB, parse_mode=None)

@dp.inline_query()
async def inline(q:types.InlineQuery):
    global BOT_USERNAME;
    if not BOT_USERNAME: 
        try: me = await bot.get_me(); BOT_USERNAME = me.username or "имя_вашего_бота"
        except Exception as e: log.error(f"Не удалось получить имя бота в инлайн-режиме: {e}"); BOT_USERNAME = "имя_вашего_бота"
    log.info(f"======== НОВЫЙ ИНЛАЙН-ЗАПРОС от @{BOT_USERNAME}========")
    uid=q.from_user.id; query_text = q.query.strip()
    def art(id_suffix: str, title_text: str, message_text: str, description: str = None, parse_mode: str = None): 
        final_id = hashlib.md5(f"{uid}_{id_suffix}_{hashlib.md5(message_text.encode()).hexdigest()}".encode()).hexdigest()
        return InlineQueryResultArticle(id=final_id,title=title_text,input_message_content=InputTextMessageContent(message_text=message_text,parse_mode=parse_mode),description=description)
    results = []; w,wt=cached_val(uid,"weight"); c,ct=cached_val(uid,"cock"); iq,iqt=cached_val(uid,"iq"); h,ht=cached_val(uid,"height")
    results.extend([art("w", "Вес", f"Мой вес: {w} кг {wt}", parse_mode=None),art("c", "Мой хуй", f"Мой хуй: {c} см {ct}", parse_mode=None),art("i", "IQ", f"Мой IQ: {iq} {iqt}", parse_mode=None),art("h", "Рост", f"Мой рост: {h} см {ht}", parse_mode=None),art("all", "Хто я?", (f"Мой вес: {w} кг {wt}\nМой хуй: {c} см {ct}\nМой IQ: {iq} {iqt}\nМой рост: {h} см {ht}"), description="Сводка характеристик", parse_mode=None),])
    if query_text: results.append(art(id_suffix="proof_query", title_text=f"Искать инфо: \"{html.escape(query_text[:40])}{'...' if len(query_text)>40 else ''}\"", message_text=f"/proof {query_text}", description="Отправить запрос боту", parse_mode=None))
    else: help_text = (f"Чтобы я поискал информацию по вашему тексту или вопросу, используйте команду `/proof ваш текст или вопрос` или ответьте на сообщение командой `/proof` в чате со мной (@{BOT_USERNAME})."); results.append(art(id_suffix="proof_help_inline",title_text="Пруф? (Как использовать)",message_text=help_text,description="Инструкция по команде /proof",parse_mode=None))
    await q.answer(results, cache_time=1, is_personal=True)

# ─────────── Запуск ───────────
async def main():
    global BOT_USERNAME
    try:
        me = await bot.get_me()
        BOT_USERNAME = me.username or "имя_вашего_бота"
        log.info(f"Бот запущен с именем @{BOT_USERNAME}")
    except Exception as e:
        log.error(f"Не удалось получить информацию о боте: {e}")
        BOT_USERNAME = "имя_вашего_бота"

    await bot.set_my_commands(
        commands=[
            BotCommand(command="start", description="Начать"),
            BotCommand(command="menu",  description="Меню"),
            BotCommand(command="pizdica", description="Дуэль"),
            BotCommand(command="proof", description="Проверить информацию"),
        ], scope=BotCommandScopeDefault()
    )

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__=="__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
