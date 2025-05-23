#!/usr/bin/env python3
# psi_chat_bot.py — Telegram-бот «Кто я?» с кэшированием картинки на 6 часов
# Python 3.10+, aiogram 3.7+, Pydantic v2

import os, sys, io, random, base64, hashlib, asyncio, logging, requests
from datetime import datetime, timedelta
from typing import Dict, Tuple, Callable

from dotenv import load_dotenv; load_dotenv()
from PIL import Image, ImageDraw, ImageFont

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    InputTextMessageContent, InlineQueryResultArticle,
    BufferedInputFile, ChatMemberUpdated,
)

# ─────────── базовое ───────────
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("psi_chat_bot")

API_TOKEN      = os.getenv("psi_chat_bot")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not API_TOKEN:
    sys.exit("❌ В .env нет psi_chat_bot")

bot = Bot(token=API_TOKEN,
          default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp  = Dispatcher()

# ─────────── кэш чисел и картинок ───────────
TTL = timedelta(hours=6)

# числовые параметры  key → (ts, value, emoji)
cache: Dict[str, Tuple[datetime, int, str]] = {}
# PNG-картинки        uid → (ts, bytes)
img_cache: Dict[int, Tuple[datetime, bytes]] = {}

# ─────────── генераторы значений ───────────
EMO = {
    "w":{"0":"🪶","1-49":"🦴","50-99":"⚖️","100-149":"🏋️‍♂️",
         "150-199":"🐖","200-249":"🤯","250":"🐘"},
    "c":{"0":"🤤","1-9":"🤮","10-19":"🥴","20-29":"😐",
         "30-39":"😲","40-49":"🤯","50":"🫡"},
    "iq":{"50-69":"🤡","70-89":"😕","90-109":"🙂",
          "110-129":"😎","130-149":"🤓","150-200":"🧠"},
    "h":{"140-149":"🦗","150-169":"🙂","170-189":"😃","190-219":"🏀"}
}
def _emo(val:int, tbl):                       # подобрать эмодзи
    for rng,e in tbl.items():
        if "-" in rng:
            a,b = map(int,rng.split("-"));      # type: ignore
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

# ─────────── клавиатура ───────────
KB = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="Вес",      callback_data="weight"),
     InlineKeyboardButton(text="хуеметр",  callback_data="cock")],
    [InlineKeyboardButton(text="IQ",       callback_data="iq"),
     InlineKeyboardButton(text="Рост",     callback_data="height")],
    [InlineKeyboardButton(text="Хто Я?",   callback_data="whoami")]
])

# ─────────── Gemini REST ───────────
GEN_URL=("https://generativelanguage.googleapis.com/v1beta/"
         "models/gemini-2.0-flash-preview-image-generation:generateContent")

def gemini_png(prompt:str)->bytes:
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY не задан")
    r=requests.post(
        f"{GEN_URL}?key={GEMINI_API_KEY}",
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
        raise RuntimeError("IMAGE_SAFETY")
    for part in data["candidates"][0]["content"]["parts"]:
        if "inlineData" in part:
            return base64.b64decode(part["inlineData"]["data"])
    raise RuntimeError("нет изображения")

def prompt_primary(ctx:dict)->str:
    return (
        "Draw a clean flat cartoon avatar, transparent PNG. "
        f"Height {ctx['h']} cm, weight {ctx['w']} kg. "
        f"Floating yellow tape-measure on the right shows “{ctx['c']} cm”. "
        f"Thought bubble: “IQ {ctx['iq']}”. "
        f"Write “{ctx['name']}” under the feet. Fully clothed. No nudity."
    )

def prompt_safe(ctx:dict)->str:
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
            data=await loop.run_in_executor(None, gemini_png, prompt_safe(ctx))
        else:
            raise
    bio=io.BytesIO(data); bio.seek(0); return bio

# ─────────── PIL fallback ───────────
def render_pil(ctx:dict)->io.BytesIO:
    try: font=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",18)
    except IOError: font=ImageFont.load_default()
    img=Image.new("RGB",(400,400),"white")
    d=ImageDraw.Draw(img)
    d.text((10,5),ctx["name"],font=font,fill="black")
    head,r=(200,100),40
    d.ellipse((head[0]-r,head[1]-r,head[0]+r,head[1]+r),outline="black",width=2)
    d.rectangle((180,140,220,250),outline="black",width=2)
    d.line((180,140,140,180),fill="black",width=2)
    d.line((220,140,260,180),fill="black",width=2)
    d.line((200,250,170,320),fill="black",width=2)
    d.line((200,250,230,320),fill="black",width=2)
    d.line((200,250,200,250+ctx['c']),fill="black",width=2)
    y=330
    for t in (f"Вес: {ctx['w']} кг",
              f"Длина: {ctx['c']} см",
              f"IQ: {ctx['iq']}",
              f"Рост: {ctx['h']} см"):
        d.text((10,y),t,font=font,fill="black"); y+=18
    bio=io.BytesIO(); img.save(bio,"PNG"); bio.seek(0); return bio

# ─────────── handlers ───────────
@dp.message(CommandStart())
async def start(m:types.Message):
    await m.answer("Добро пожаловать!", reply_markup=KB)

@dp.message(Command("menu"))
async def menu(m:types.Message):
    await m.answer("Выберите действие:", reply_markup=KB)

@dp.callback_query(F.data.in_({"weight","cock","iq","height","whoami"}))
async def callbacks(cb:types.CallbackQuery):
    uid  = cb.from_user.id
    name = cb.from_user.full_name or cb.from_user.username or str(uid)
    chat = cb.message.chat.id
    act  = cb.data

    if act!="whoami":
        val,emo = cached_val(uid,act)
        unit="kg" if act=="weight" else "cm"
        await bot.send_message(chat,f"{name}'s {act} is {val} {unit} {emo}")
        await cb.answer(); return

    # --- ХТО Я? ---
    w,_  = cached_val(uid,"weight")
    c,_  = cached_val(uid,"cock")
    iq,_ = cached_val(uid,"iq")
    h,_  = cached_val(uid,"height")
    ctx  = {"w":w,"c":c,"iq":iq,"h":h,"name":name}

    now=datetime.now()
    if uid in img_cache and now-img_cache[uid][0] <= TTL:
        bio = io.BytesIO(img_cache[uid][1])
    else:
        try:
            bio = await make_image(ctx)
        except Exception as e:
            log.error("Gemini error → PIL fallback: %s", e)
            bio = render_pil(ctx)
        img_cache[uid]=(now,bio.getvalue())

    await bot.send_photo(chat,BufferedInputFile(bio.getvalue(),"whoami.png"),
                         caption="Хто я?")
    await cb.answer()

@dp.chat_member(F.new_chat_members.is_bot & F.new_chat_members.id == bot.id)
async def added(ev:ChatMemberUpdated):
    await bot.send_message(ev.chat.id,"Привет! Я бот.",reply_markup=KB)

@dp.inline_query()
async def inline(q:types.InlineQuery):
    uid=q.from_user.id
    w,wt=cached_val(uid,"weight")
    c,ct=cached_val(uid,"cock")
    iq,iqt=cached_val(uid,"iq")
    h,ht=cached_val(uid,"height")
    def art(t,msg,sfx):
        return InlineQueryResultArticle(
            id=hashlib.md5(f"{t}{sfx}".encode()).hexdigest(),
            title=t,input_message_content=InputTextMessageContent(message_text=msg))
    await q.answer([
        art("Вес",     f"My weight is {w} kg {wt}","w"),
        art("Хуеметр", f"My cock size is {c} cm {ct}","c"),
        art("IQ",      f"My IQ is {iq} {iqt}","i"),
        art("Рост",    f"My height is {h} cm {ht}","h"),
        art("Хто я?",  (f"My weight is {w} kg {wt}\n"
                        f"My cock size is {c} cm {ct}\n"
                        f"My IQ is {iq} {iqt}\n"
                        f"My height is {h} cm {ht}"),"all")
    ],cache_time=1)

# ─────────── run ───────────
async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__=="__main__":
    asyncio.run(main())

