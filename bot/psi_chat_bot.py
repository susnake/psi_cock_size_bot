#!/usr/bin/env python3
# psi_chat_bot.py — Telegram-бот «Кто я?» с кэшированием картинки на 6 часов
# Python 3.10+, aiogram 3.7+, Pydantic v2

import os, sys, io, random, base64, hashlib, asyncio, logging, requests
from datetime import datetime, timedelta
from typing import Dict, Tuple, Callable

from dotenv import load_dotenv; load_dotenv()
from PIL import Image, ImageDraw, ImageFont

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command, CommandObject
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    InputTextMessageContent, InlineQueryResultArticle,
    BufferedInputFile, ChatMemberUpdated,
)

# ─────────── Базовая настройка ───────────
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("psi_chat_bot")

API_TOKEN      = os.getenv("psi_chat_bot")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not API_TOKEN:
    sys.exit("❌ psi_chat_bot не найден в .env")
if not GEMINI_API_KEY: # Добавлена явная проверка ключа Gemini при старте
    log.warning("⚠️ GEMINI_API_KEY не найден в .env. Функции, использующие Gemini, не будут работать.")


bot = Bot(token=API_TOKEN,
          default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp  = Dispatcher()

# ─────────── Кэш чисел и картинок ───────────
TTL = timedelta(hours=6)

# числовые параметры  key → (ts, value, emoji)
cache: Dict[str, Tuple[datetime, int, str]] = {}
# PNG-картинки        uid → (ts, bytes)
img_cache: Dict[int, Tuple[datetime, bytes]] = {}

# ─────────── Генераторы значений ───────────
EMO = {
    "w":{"0":"🪶","1-49":"🦴","50-99":"⚖️","100-149":"🏋️‍♂️",
         "150-199":"🐖","200-249":"🤯","250":"🐘"},
    "c":{"0":"🤤","1-9":"🤮","10-19":"🥴","20-29":"😐",
         "30-39":"😲","40-49":"🤯","50":"🫡"},
    "iq":{"50-69":"🤡","70-89":"😕","90-109":"🙂",
          "110-129":"😎","130-149":"🤓","150-200":"🧠"},
    "h":{"140-149":"🦗","150-169":"🙂","170-189":"😃","190-219":"🏀"}
}
def _emo(val:int, tbl):                      # подобрать эмодзи
    for rng,e in tbl.items():
        if "-" in rng:
            a,b = map(int,rng.split("-"));       # type: ignore # Игнорируем проверку типов для этой строки
            if a<=val<=b: return e
        elif int(rng)==val:
            return e
    return ""

def gen_w():  v=random.randint(0,250);  return v,_emo(v,EMO["w"])
def gen_c():  v=random.randint(0,50);   return v,_emo(v,EMO["c"])
def gen_iq(): v=random.randint(50,200); return v,_emo(v,EMO["iq"]) or ("👨‍🔬" if v==200 else "")
def gen_h():  v=random.randint(140,220);return v,_emo(v,EMO["h"]) or ("🇷🇸" if v==220 else "")

gens = {"weight":gen_w, "cock":gen_c, "iq":gen_iq, "height":gen_h}

def cached_val(uid:int,label:str):
    now=datetime.now(); key=f"{label}_{uid}"
    if key in cache and now-cache[key][0] <= TTL:
        _,v,e = cache[key]
    else:
        v,e = gens[label](); cache[key]=(now,v,e)
    return v,e

# ─────────── Клавиатура ───────────
KB = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="Вес",       callback_data="weight"),
     InlineKeyboardButton(text="Хуеметр",   callback_data="cock")], # Название кнопки "хуеметр"
    [InlineKeyboardButton(text="IQ",        callback_data="iq"),
     InlineKeyboardButton(text="Рост",      callback_data="height")],
    [InlineKeyboardButton(text="Хто Я?",    callback_data="whoami")],
    [InlineKeyboardButton(text="Пруф?",     callback_data="proof_help")] # Новая кнопка
])

# ─────────── Gemini (Генерация изображений) ───────────
# Промпты для Gemini API (генерация изображений) остаются на английском,
# так как модели часто лучше обучены на английских промптах.
IMG_GEN_URL=("https://generativelanguage.googleapis.com/v1beta/"
             "models/gemini-2.0-flash-preview-image-generation:generateContent") # Ваша модель для изображений

def gemini_png(prompt:str)->bytes:
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY не задан для генерации изображений.")
    r=requests.post(
        f"{IMG_GEN_URL}?key={GEMINI_API_KEY}",
        headers={"Content-Type":"application/json"},
        json={
            "contents":[{"parts":[{"text":prompt}]}],
            "generationConfig":{"responseModalities":["TEXT","IMAGE"]}
        }, timeout=30
    )
    if r.status_code!=200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text}")
    data=r.json()
    if data["candidates"][0].get("finishReason")=="IMAGE_SAFETY":
        raise RuntimeError("IMAGE_SAFETY") # Изображение заблокировано по соображениям безопасности
    for part in data["candidates"][0]["content"]["parts"]:
        if "inlineData" in part:
            return base64.b64decode(part["inlineData"]["data"])
    raise RuntimeError("Нет изображения в ответе Gemini")

def prompt_primary(ctx:dict)->str: # Промпт для Gemini (англ.)
    return (
        "Draw a clean flat cartoon avatar, transparent PNG. "
        f"Height {ctx['h']} cm, weight {ctx['w']} kg. "
        f"Floating yellow tape-measure on the right shows “{ctx['c']} cm”. "
        f"Thought bubble: “IQ {ctx['iq']}”. "
        f"Write “{ctx['name']}” under the feet. Fully clothed. No nudity."
    )

def prompt_safe(ctx:dict)->str: # Промпт для Gemini (англ.)
    return (
        "Draw a clean flat cartoon avatar, transparent PNG. "
        f"Height {ctx['h']} cm, weight {ctx['w']} kg. "
        f"Thought bubble: “IQ {ctx['iq']}”. "
        f"Write “{ctx['name']}” under the feet. Fully clothed."
    )

async def make_image(ctx:dict)->io.BytesIO:
    loop=asyncio.get_running_loop()
    try:
        data=await loop.run_in_executor(None, gemini_png, prompt_primary(ctx))
    except RuntimeError as e:
        if "IMAGE_SAFETY" in str(e):
            log.warning("Основной промпт для Gemini (изображение) не прошел (IMAGE_SAFETY), пробуем безопасный промпт.")
            data=await loop.run_in_executor(None, gemini_png, prompt_safe(ctx))
        else:
            raise
    bio=io.BytesIO(data); bio.seek(0); return bio

# ─────────── Резервная генерация PIL ───────────
def render_pil(ctx:dict)->io.BytesIO:
    try: font=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",18)
    except IOError: font=ImageFont.load_default() # Загрузка шрифта по умолчанию, если основной не найден
    img=Image.new("RGB",(400,400),"white")
    d=ImageDraw.Draw(img)
    d.text((10,5),ctx["name"],font=font,fill="black")
    head,r=(200,100),40
    d.ellipse((head[0]-r,head[1]-r,head[0]+r,head[1]+r),outline="black",width=2) # Голова
    d.rectangle((180,140,220,250),outline="black",width=2) # Тело
    d.line((180,140,140,180),fill="black",width=2) # Левая рука
    d.line((220,140,260,180),fill="black",width=2) # Правая рука
    d.line((200,250,170,320),fill="black",width=2) # Левая нога
    d.line((200,250,230,320),fill="black",width=2) # Правая нога
    d.line((200,250,200,250+ctx['c']),fill="black",width=2) # Линия для 'c' (длина)
    y=330
    for t in (f"Вес: {ctx['w']} кг",
              f"Длина: {ctx['c']} см", # Отображение 'c' в PIL
              f"IQ: {ctx['iq']}",
              f"Рост: {ctx['h']} см"):
        d.text((10,y),t,font=font,fill="black"); y+=18
    bio=io.BytesIO(); img.save(bio,"PNG"); bio.seek(0); return bio

# ─────────── Gemini (Генерация текста для Пруф?) ───────────
TEXT_GEN_URL_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
DEFAULT_TEXT_MODEL = "gemini-1.5-flash-latest" # Или "gemini-pro", или "gemini-2.0-flash", если доступен

# Эта функция ДОЛЖНА БЫТЬ СИНХРОННОЙ (def), так как использует requests
def _generate_text_proof_sync(user_text: str, model_name: str = DEFAULT_TEXT_MODEL) -> str:
    if not GEMINI_API_KEY:
        # Эта ошибка будет поймана в generate_text_proof и возвращена пользователю
        raise RuntimeError("GEMINI_API_KEY не задан для генерации текста.")

    # ИЗМЕНЕННЫЙ ПРОМПТ для Gemini API (генерация текста)
    prompt_instruction = (
        "Ты — информационный ассистент. Твоя задача — найти и предоставить информацию по тексту пользователя.\n"
        "Если текст пользователя — это вопрос, дай на него как можно более полный ответ, основываясь на общедоступных знаниях.\n"
        "Если текст пользователя — это утверждение или тема, предоставь развернутую информацию, дополнительный контекст, интересные факты или связанные детали по этой теме.\n"
        "Старайся отвечать содержательно и по существу.\n"
        "Отвечай на языке оригинального текста пользователя.\n"
        "Текст пользователя: "
    )
    full_prompt = f"{prompt_instruction}\"{user_text}\""
    
    target_url = TEXT_GEN_URL_TEMPLATE.format(model_name=model_name) + f"?key={GEMINI_API_KEY}"
    headers = {"Content-Type": "application/json"}
    payload = {
        "contents": [{"parts": [{"text": full_prompt}]}]
        # generationConfig можно добавить при необходимости
        # "generationConfig": {
        #     "temperature": 0.7,
        #     "maxOutputTokens": 800 # Можно увеличить, если нужны более длинные ответы
        # }
    }

    try:
        r = requests.post(target_url, headers=headers, json=payload, timeout=60)
        r.raise_for_status() # Вызовет исключение для HTTP ошибок 4xx/5xx
        data = r.json()

        if data.get("candidates") and data["candidates"][0].get("content") and \
           data["candidates"][0]["content"].get("parts") and \
           data["candidates"][0]["content"]["parts"][0].get("text"):
            return data["candidates"][0]["content"]["parts"][0]["text"].strip()
        
        # Обработка случаев, когда Gemini возвращает ответ, но без текста (например, из-за фильтров безопасности)
        if data.get("candidates") and data["candidates"][0].get("finishReason") == "SAFETY":
            reason_detail = "Причина: "
            for rating in data["candidates"][0].get("safetyRatings", []):
                reason_detail += f"{rating['category'].replace('HARM_CATEGORY_', '')}: {rating['probability']}. "
            log.warning(f"Генерация текста Gemini заблокирована из-за SAFETY. Детали: {reason_detail}")
            return f"Не удалось обработать запрос из-за ограничений безопасности (контент отфильтрован). {reason_detail.strip()}"
        
        if data.get("promptFeedback") and data["promptFeedback"].get("blockReason"):
            block_reason = data["promptFeedback"]["blockReason"]
            log.warning(f"Генерация текста Gemini заблокирована promptFeedback. Причина: {block_reason}")
            safety_ratings_info = ""
            if data["promptFeedback"].get("safetyRatings"):
                for rating in data["promptFeedback"]["safetyRatings"]:
                     safety_ratings_info += f"{rating['category'].replace('HARM_CATEGORY_', '')}: {rating['probability']}; "
            return (f"Не удалось обработать запрос из-за ограничений (промпт заблокирован): {block_reason}. "
                    f"{('Детали: ' + safety_ratings_info.strip()) if safety_ratings_info else ''} Попробуйте переформулировать.")
        
        log.error(f"Неожиданный формат ответа от Gemini (текст): {data}")
        return "Не удалось получить структурированный ответ от Gemini."

    except requests.exceptions.HTTPError as e:
        error_text = e.response.text if e.response else "Нет текста ответа"
        log.error(f"HTTP ошибка при запросе к Gemini API (текст): {e}. Ответ: {error_text}")
        try:
            error_data = e.response.json()
            if "error" in error_data and "message" in error_data["error"]:
                return f"Ошибка от Gemini: {error_data['error']['message']}"
        except ValueError: # Не JSON ответ
            pass 
        return f"Ошибка ({e.response.status_code if e.response else 'N/A'}) при обращении к сервису."
    except requests.exceptions.RequestException as e: # Ошибки сети, таймауты и т.д.
        log.error(f"Сетевая ошибка при запросе к Gemini API (текст): {e}")
        return f"Сетевая ошибка при обращении к сервису: {e}"
    except Exception as e: # Другие неожиданные ошибки
        log.error(f"Неожиданная ошибка в _generate_text_proof_sync: {e}", exc_info=True)
        return "Произошла неожиданная ошибка при обработке вашего запроса."

async def generate_text_proof(user_text: str, model_name: str = DEFAULT_TEXT_MODEL) -> str:
    loop = asyncio.get_running_loop()
    try:
        # _generate_text_proof_sync является СИНХРОННОЙ функцией
        result = await loop.run_in_executor(None, _generate_text_proof_sync, user_text, model_name)
        return result
    except RuntimeError as e: # Отлавливаем ошибку отсутствия GEMINI_API_KEY из _generate_text_proof_sync
        log.error(f"RuntimeError в generate_text_proof: {e}")
        return str(e) # Возвращаем сообщение об ошибке пользователю
    except Exception as e:
        log.error(f"Ошибка при запуске _generate_text_proof_sync в executor: {e}", exc_info=True)
        return "Внутренняя ошибка сервера при обработке запроса на поиск информации."

# ─────────── Обработчики ───────────
@dp.message(CommandStart())
async def start(m:types.Message):
    await m.answer("Добро пожаловать!", reply_markup=KB) # Сообщение пользователю (рус.)

@dp.message(Command("menu"))
async def menu(m:types.Message):
    await m.answer("Выберите действие:", reply_markup=KB) # Сообщение пользователю (рус.)

@dp.callback_query(F.data.in_({"weight","cock","iq","height","whoami", "proof_help"}))
async def callbacks(cb:types.CallbackQuery):
    uid  = cb.from_user.id
    name = cb.from_user.full_name or cb.from_user.username or str(uid)
    chat_id = cb.message.chat.id
    act  = cb.data

    if act == "proof_help":
        await cb.message.answer( # Сообщение пользователю (рус.)
            "Чтобы я поискал информацию по вашему тексту или вопросу:\n"
            "— Отправьте команду: `/proof ваш текст или вопрос`\n"
            "— Или ответьте на нужное сообщение командой `/proof`"
        )
        await cb.answer()
        return
    
    if act!="whoami": # Обработка для weight, cock, iq, height
        act_rus_map = { # Карта для перевода названий атрибутов в сообщениях
            "weight": "вес",
            "cock": "хуеметр", 
            "iq": "IQ",
            "height": "рост"
        }
        act_display_name = act_rus_map.get(act, act) # Если вдруг act не в карте, используем сам act
        val,emo = cached_val(uid,act)
        unit="кг" if act=="weight" else "см"
        await bot.send_message(chat_id,f"{name}, ваш {act_display_name}: {val} {unit} {emo}") # Сообщение пользователю (рус.)
        await cb.answer()
        return

    # --- Хто Я? --- (act == "whoami")
    w,_  = cached_val(uid,"weight")
    c,_  = cached_val(uid,"cock")
    iq,_ = cached_val(uid,"iq")
    h,_  = cached_val(uid,"height")
    ctx  = {"w":w,"c":c,"iq":iq,"h":h,"name":name}

    now=datetime.now()
    img_data_to_send: bytes
    if uid in img_cache and now-img_cache[uid][0] <= TTL:
        log.info(f"Изображение для UID {uid} найдено в кэше.")
        img_data_to_send = img_cache[uid][1]
    else:
        log.info(f"Генерация изображения для UID {uid}...")
        try:
            bio_result = await make_image(ctx)
            img_data_to_send = bio_result.getvalue()
        except Exception as e:
            log.error("Ошибка Gemini (изображение) → резервный PIL: %s", e, exc_info=True)
            bio_result = render_pil(ctx) # Резервный PIL
            img_data_to_send = bio_result.getvalue()
        img_cache[uid]=(now,img_data_to_send)
        log.info(f"Изображение для UID {uid} сгенерировано и закэшировано ({len(img_data_to_send)} байт).")

    await bot.send_photo(chat_id,BufferedInputFile(img_data_to_send,"whoami.png"),
                         caption="Хто я?") # Сообщение пользователю (рус.)
    await cb.answer()

# Новый обработчик для команды /proof
@dp.message(Command("proof"))
async def proof_command_handler(message: types.Message, command: CommandObject):
    if not GEMINI_API_KEY: # Проверка наличия ключа перед использованием
        await message.reply("Функция поиска информации недоступна: GEMINI_API_KEY не настроен.") # Сообщение пользователю (рус.)
        return

    text_to_proof = ""
    if command.args:
        text_to_proof = command.args.strip()
    elif message.reply_to_message and message.reply_to_message.text:
        text_to_proof = message.reply_to_message.text.strip()
    
    if not text_to_proof:
        await message.reply( # Сообщение пользователю (рус.)
            "Пожалуйста, укажите текст или вопрос для поиска информации.\n"
            "Пример: `/proof ваш текст или вопрос`\n"
            "Или ответьте на сообщение, по которому нужно найти информацию, командой `/proof`."
        )
        return

    # Вот эта проверка вызывает сообщение "The text to verify is too short..."
    # Если хотите изменить минимальную длину, измените число 3.
    if len(text_to_proof) < 3: 
        await message.reply("Текст для поиска информации слишком короткий. Пожалуйста, предоставьте более развернутый запрос, более 3-х символов") # Сообщение пользователю (рус.)
        return
    
    if len(text_to_proof) > 4096: # Ограничение на длину входного текста
        await message.reply("Текст для поиска информации слишком длинный (макс. 4096 символов). Пожалуйста, сократите его.") # Сообщение пользователю (рус.)
        return
    
    # Уведомление пользователя о начале работы
    processing_message = await message.reply("Идет поиск информации... Пожалуйста, подождите. ⌛") # Сообщение пользователю (рус.)
    
    proof_result = await generate_text_proof(text_to_proof) # Здесь proof_result будет строкой
    
    # Удаляем сообщение "Пожалуйста, подождите"
    try:
        await bot.delete_message(chat_id=message.chat.id, message_id=processing_message.message_id)
    except Exception as e:
        log.warning(f"Не удалось удалить сообщение о процессе: {e}")

    # Отправка результата частями, если он слишком длинный
    max_length = 4096 # Лимит Telegram на длину сообщения
    if len(proof_result) > max_length: # Эта проверка теперь должна работать корректно
        for i in range(0, len(proof_result), max_length):
            await message.answer(proof_result[i:i+max_length])
    else:
        await message.answer(proof_result)

@dp.chat_member(F.new_chat_members.is_bot & F.new_chat_members.id == bot.id) # type: ignore # Игнорируем проверку типов для этой строки
async def added(ev:ChatMemberUpdated):
    await bot.send_message(ev.chat.id,"Привет! Я бот.",reply_markup=KB) # Сообщение пользователю (рус.)

@dp.inline_query()
async def inline(q:types.InlineQuery):
    uid=q.from_user.id
    w,wt=cached_val(uid,"weight")
    c,ct=cached_val(uid,"cock")
    iq,iqt=cached_val(uid,"iq")
    h,ht=cached_val(uid,"height")
    
    # Функция для создания InlineQueryResultArticle, как в оригинальном коде
    def art(t,msg,sfx):
        return InlineQueryResultArticle(
            id=hashlib.md5(f"{uid}{t}{sfx}".encode()).hexdigest(), # Добавлен uid в id для большей уникальности
            title=t,input_message_content=InputTextMessageContent(message_text=msg))
    
    results = [ # Сообщения пользователю в инлайн-режиме (рус.)
        art("Вес",       f"Мой вес: {w} кг {wt}","w"),
        art("Хуеметр",   f"Мой хуеметр: {c} см {ct}","c"), 
        art("IQ",        f"Мой IQ: {iq} {iqt}","i"),
        art("Рост",      f"Мой рост: {h} см {ht}","h"),
        art("Хто я?",    (f"Мой вес: {w} кг {wt}\n"
                         f"Мой хуеметр: {c} см {ct}\n"
                         f"Мой IQ: {iq} {iqt}\n"
                         f"Мой рост: {h} см {ht}"),"all"),
        art("Пруф? (Инфо)", # Новый пункт для инлайн-режима (рус.)
            ("Чтобы я поискал информацию по вашему тексту или вопросу, "
             "используйте команду `/proof ваш текст или вопрос` или ответьте на сообщение командой `/proof` в чате со мной (@имя_вашего_бота)."), # Замените @имя_вашего_бота
            "proof_help_inline"
        )
    ]
    await q.answer(results,cache_time=10) # Немного увеличен cache_time для инлайн результатов

# ─────────── Запуск ───────────
async def main():
    # Важно: перед запуском поллинга удаляем вебхук, если он был установлен
    await bot.delete_webhook(drop_pending_updates=True)
    # Запуск поллинга
    await dp.start_polling(bot)

if __name__=="__main__":
    if sys.platform == "win32": # Для Windows, если есть проблемы с asyncio политикой событий
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())

