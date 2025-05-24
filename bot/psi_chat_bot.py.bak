#!/usr/bin/env python3
import os, sys, io, random, base64, hashlib, asyncio, logging, requests, aiohttp
from datetime import datetime, timedelta
from typing import Dict, Tuple
from dotenv import load_dotenv; load_dotenv()
from PIL import Image, ImageDraw, ImageFont

from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    BotCommand, BotCommandScopeDefault,
    InlineKeyboardMarkup, InlineKeyboardButton,
    InputTextMessageContent,
    InlineQueryResultArticle, InlineQueryResultPhoto, InlineQueryResultCachedPhoto,
    BufferedInputFile, ChatMemberUpdated,
)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("psi_chat_bot")

API_TOKEN        = os.getenv("psi_chat_bot")
STORAGE_CHAT_ID  = int(os.getenv("STORAGE_CHAT_ID", "0"))
GEMINI_API_KEY   = os.getenv("GEMINI_API_KEY")
IMAGE_SERVER_URL = os.getenv("IMAGE_SERVER_URL", "").rstrip("/")

if not API_TOKEN or (not STORAGE_CHAT_ID and not IMAGE_SERVER_URL):
    sys.exit("❌ В .env нет psi_chat_bot и (STORAGE_CHAT_ID или IMAGE_SERVER_URL)")

bot = Bot(token=API_TOKEN,
          default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

TTL      = timedelta(hours=6)
EMPTY_PX = "https://upload.wikimedia.org/wikipedia/commons/2/2f/1x1px.png"

val_cache: Dict[str, Tuple[datetime,int,str]]       = {}
img_cache: Dict[int, Tuple[datetime,bytes,str,bool]] = {}

EMO = {
    "w": {"0":"🪶","1-49":"🦴","50-99":"⚖️","100-149":"🏋️‍♂️","150-199":"🐖","200-249":"🤯","250":"🐘"},
    "c": {"0":"🤤","1-9":"🤮","10-19":"🥴","20-29":"😐","30-39":"😲","40-49":"🤯","50":"🫡"},
    "iq": {"50-69":"🤡","70-89":"😕","90-109":"🙂","110-129":"😎","130-149":"🤓","150-200":"🧠"},
    "h": {"140-149":"🦗","150-169":"🙂","170-189":"😃","190-219":"🏀"},
}

def _emo(v:int,m:Dict[str,str]) -> str:
    for rng,e in m.items():
        if "-" in rng:
            a,b = map(int, rng.split("-"))
            if a <= v <= b:
                return e
        elif int(rng) == v:
            return e
    return ""


def gen_w():  v = random.randint(0,250); return v, _emo(v, EMO["w"])

def gen_c():  v = random.randint(0,50);  return v, _emo(v, EMO["c"])

def gen_iq(): v = random.randint(50,200); return v, _emo(v, EMO["iq"]) or ("👨‍🔬" if v==200 else "")

def gen_h():  v = random.randint(140,220);return v, _emo(v, EMO["h"]) or ("🇷🇸" if v==220 else "")

gens = {"weight": gen_w, "cock": gen_c, "iq": gen_iq, "height": gen_h}


def cached_val(uid:int,label:str) -> Tuple[int,str]:
    key = f"{label}_{uid}"
    now = datetime.now()
    if key in val_cache and now - val_cache[key][0] <= TTL:
        _, v, e = val_cache[key]
    else:
        v, e = gens[label]()
        val_cache[key] = (now, v, e)
    return v, e

KB = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="Вес", callback_data="weight"),
     InlineKeyboardButton(text="Хуеметр", callback_data="cock")],
    [InlineKeyboardButton(text="IQ",   callback_data="iq"),
     InlineKeyboardButton(text="Рост", callback_data="height")],
    [InlineKeyboardButton(text="Хто я?", callback_data="whoami")],
])

GEN_URL = (
    "https://generativelanguage.googleapis.com/v1beta/"
    "models/gemini-2.0-flash-preview-image-generation:generateContent"
)

def prompt_full(ctx: dict) -> str:
    return (
        "Draw a clean flat cartoon avatar, transparent PNG, full-body (head to toes). "
        f"Height {ctx['h']} cm, weight {ctx['w']} kg. "
        f"Thought bubble: “IQ {ctx['iq']}” illustrated as an oversized brain above the head. "
        f"Floating tape-measure on the right shows “{ctx['c']} cm”. "
        f"Write “{ctx['name']}” under the feet. Fully clothed, no nudity, simple lines, bright colors."
    )


def gemini_png(prompt:str) -> bytes:
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY не задан")
    r = requests.post(
        f"{GEN_URL}?key={GEMINI_API_KEY}",
        headers={"Content-Type": "application/json"},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseModalities": ["TEXT","IMAGE"]},
        },
        timeout=30
    )
    r.raise_for_status()
    j = r.json()
    if j["candidates"][0].get("finishReason") == "IMAGE_SAFETY":
        raise RuntimeError("IMAGE_SAFETY")
    for part in j["candidates"][0]["content"]["parts"]:
        if "inlineData" in part:
            return base64.b64decode(part["inlineData"]["data"])
    raise RuntimeError("нет изображения")

async def upload_image_http(image_bytes: bytes, filename: str) -> str:
    upload_url = IMAGE_SERVER_URL + "/upload.php"
    data = aiohttp.FormData()
    data.add_field('file', image_bytes, filename=filename, content_type="image/png")
    async with aiohttp.ClientSession() as session:
        async with session.post(upload_url, data=data, timeout=30) as resp:
            res = await resp.json()
    if "url" not in res:
        raise RuntimeError(f"Ошибка загрузки: {res.get('error','no url')}")
    url = res["url"].replace("\\/","/").replace("\\","")
    if url.startswith("/"):
        url = IMAGE_SERVER_URL + url
    return url


def render_pil(ctx:dict) -> io.BytesIO:
    font = ImageFont.load_default()
    img = Image.new("RGB", (400,400), "white")
    d = ImageDraw.Draw(img)
    d.text((10,5), ctx["name"], font=font, fill="black")
    bio = io.BytesIO()
    img.save(bio, "PNG")
    bio.seek(0)
    return bio

async def ensure_photo(uid:int,ctx:dict) -> Tuple[bytes,str,bool]:
    now = datetime.now()
    if uid in img_cache and now - img_cache[uid][0] <= TTL:
        return img_cache[uid][1], img_cache[uid][2], img_cache[uid][3]
    try:
        data = await asyncio.get_running_loop().run_in_executor(None, gemini_png, prompt_full(ctx))
    except Exception as e:
        log.error("Gemini→fallback: %s", e)
        bio = render_pil(ctx)
        data = bio.getvalue()
    filename = f"avatar_{uid}.png"
    if IMAGE_SERVER_URL:
        try:
            url = await upload_image_http(data, filename)
            img_cache[uid] = (now, data, url, True)
            return data, url, True
        except Exception as e:
            log.error("HTTP upload failed: %s", e)
    msg = await bot.send_photo(STORAGE_CHAT_ID, BufferedInputFile(data, filename))
    fid = msg.photo[-1].file_id
    img_cache[uid] = (now, data, fid, False)
    return data, fid, False


def make_caption_ru(w,wt,c,ct,iq,iqt,h,ht,mention):
    return (f"{mention}:\n"
            f"Вес: {w} кг {wt}\n"
            f"Хуй: {c} см {ct}\n"
            f"IQ: {iq} {iqt}\n"
            f"Рост: {h} см {ht}")

# Исправленная версия make_text_ru
def make_text_ru(label: str, value: int, emoji: str, mention: str) -> str:
    lm = {"weight":"Вес", "cock":"Хуй", "iq":"IQ", "height":"Рост"}
    units = {"weight":"кг", "cock":"см", "iq":"", "height":"см"}
    unit = units.get(label, "")
    unit_str = f" {unit}" if unit else ""
    return f"{mention}:\n{lm[label]}: {value}{unit_str} {emoji}"

@dp.message(CommandStart())
async def cmd_start(m: types.Message):
    await m.answer("Добро пожаловать!", reply_markup=KB)

@dp.message(Command("menu"))
async def cmd_menu(m: types.Message):
    await m.answer("Меню:", reply_markup=KB)

@dp.callback_query(F.data.in_({"weight","cock","iq","height","whoami"}))
async def cb_buttons(cb: types.CallbackQuery):
    uid = cb.from_user.id
    mention = f"@{cb.from_user.username}" if cb.from_user.username else cb.from_user.full_name
    act = cb.data
    chat = cb.message.chat.id
    if act != "whoami":
        v, e = cached_val(uid, act)
        await bot.send_message(chat, make_text_ru(act, v, e, mention))
        await cb.answer()
        return

    w, wt = cached_val(uid, "weight")
    c, ct = cached_val(uid, "cock")
    iq, iqt = cached_val(uid, "iq")
    h, ht = cached_val(uid, "height")
    caption = make_caption_ru(w, wt, c, ct, iq, iqt, h, ht, mention)
    _, fid, _ = await ensure_photo(uid, {"w":w,"c":c,"iq":iq,"h":h,"name":mention})
    await cb.message.answer_photo(photo=fid, caption=caption)
    await cb.answer()

@dp.inline_query()
async def inline(q: types.InlineQuery):
    uid = q.from_user.id
    mention = f"@{q.from_user.username}" if q.from_user.username else q.from_user.full_name
    w, wt = cached_val(uid, "weight")
    c, ct = cached_val(uid, "cock")
    iq, iqt = cached_val(uid, "iq")
    h, ht = cached_val(uid, "height")

    articles = [
        InlineQueryResultArticle(
            id=hashlib.md5(f"{label}{uid}".encode()).hexdigest(),
            title=btn_text,
            input_message_content=InputTextMessageContent(
                message_text=make_text_ru(label, val, emoji, mention)
            ),
            thumbnail_url=EMPTY_PX, thumbnail_width=1, thumbnail_height=1,
        )
        for label, val, emoji, btn_text in [
            ("weight", w, wt, "Вес"),
            ("cock",   c, ct, "Хуй"),
            ("iq",     iq, iqt, "IQ"),
            ("height", h, ht, "Рост"),
        ]
    ]

    if not IMAGE_SERVER_URL:
        _, fid, _ = await ensure_photo(uid, {"w":w,"c":c,"iq":iq,"h":h,"name":mention})
        caption = make_caption_ru(w, wt, c, ct, iq, iqt, h, ht, mention)
        articles.append(
            InlineQueryResultCachedPhoto(
                id=hashlib.md5(f"whoami{uid}".encode()).hexdigest(),
                photo_file_id=fid,
                title="Хто я?",
                description="Нажмите, чтобы увидеть аватар",
                caption=caption,
                parse_mode=ParseMode.HTML,
            )
        )

    await q.answer(results=articles, cache_time=1, is_personal=True)

@dp.message(Command(commands=["pizdica"]))
async def command_pizdica(message: types.Message):
    caller = message.from_user
    p1 = f"@{caller.username}" if caller.username else caller.full_name

    if message.reply_to_message:
        u2 = message.reply_to_message.from_user
        p2 = f"@{u2.username}" if u2.username else u2.full_name
    else:
        parts = message.text.strip().split(maxsplit=1)
        if len(parts) > 1:
            p2 = parts[1]
        else:
            await message.reply(
                "Ответьте на сообщение того, с кем хотите попиздиться, "
                "или сразу укажите его ник после команды, например:\n"
                "/pizdica @username"
            )
            return

    winner = random.choice([p1, p2])
    await message.reply(f"{p1} и {p2} пиздились за гаражами до первой крови\nПобедитель — {winner} 🏆🏆🏆")

@dp.chat_member(F.new_chat_members.is_bot & F.new_chat_members.id==bot.id)
async def on_added(ev: ChatMemberUpdated):
    await bot.send_message(ev.chat.id, "Привет! Я бот.", reply_markup=KB)

async def main():
    await bot.set_my_commands(
        commands=[
            BotCommand(command="start",   description="Начать"),
            BotCommand(command="menu",    description="Меню"),
            BotCommand(command="pizdica", description="Попиздица"),
        ], scope=BotCommandScopeDefault()
    )
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

