import os
import random
import hashlib
import asyncio
import sys
import logging
import io
import tempfile
from datetime import datetime, timedelta
from dotenv import load_dotenv

from PIL import Image, ImageDraw, ImageFont

from aiogram import Bot, Dispatcher, types
from aiogram.types import (
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ChatMemberUpdated
)
from aiogram.types.input_file import FSInputFile
from aiogram.filters import CommandStart, Command
from aiogram.dispatcher.middlewares.base import BaseMiddleware
from aiogram import F

# Загрузка переменных окружения из .env файла
load_dotenv()

# Настройка логирования: вывод в файл и на консоль
LOG_DIR = "/var/log/psi_chat_bot"
LOG_FILE = os.path.join(LOG_DIR, "psi_chat_bot.log")
try:
    os.makedirs(LOG_DIR, exist_ok=True)
except Exception as e:
    print(f"Не удалось создать каталог {LOG_DIR}: {e}. Будет использован /tmp для логирования.")
    LOG_FILE = "/tmp/psi_chat_bot.log"

LOG_FORMAT = '%(asctime)s - %(levelname)s - %(name)s - %(message)s (%(filename)s:%(lineno)d)'
DATE_FORMAT = '%Y-%m-%d %H:%M:%S'
logging.basicConfig(
    level=logging.DEBUG,
    format=LOG_FORMAT,
    datefmt=DATE_FORMAT,
    handlers=[
        logging.FileHandler(LOG_FILE, mode='a', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("psi_chat_bot")
logger.info("Запуск бота")

API_TOKEN = os.getenv("psi_chat_bot")
if not API_TOKEN:
    logger.error("Нет переменной окружения psi_chat_bot")
    raise ValueError("Нет переменной окружения psi_chat_bot")

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# Время хранения результатов в кэше
CACHE_EXPIRATION = timedelta(hours=6)
cache = {}

# Словари с эмодзи для параметров
weight_messages = {
    "0": ["🪶"],
    "1-49": ["🦴"],
    "50-99": ["⚖️"],
    "100-149": ["🏋️‍♂️"],
    "150-199": ["🐖"],
    "200-249": ["🤯"],
    "250": ["🐘"]
}
cock_size_messages = {
    "0": ["🤤"],
    "1-9": ["🤮"],
    "10-19": ["🥴"],
    "20-29": ["😐"],
    "30-39": ["😲"],
    "40-49": ["🤯"],
    "50": ["🫡"]
}
iq_messages = {
    "50-69": ["🤡", "😞"],
    "70-89": ["😕", "🤔"],
    "90-109": ["🙂", "😌"],
    "110-129": ["😎", "💡"],
    "130-149": ["🤓", "🔥"],
    "150-200": ["🧠", "🚀"]
}
height_messages = {
    "140-149": ["🦗", "🐜"],
    "150-169": ["🙂", "👍"],
    "170-189": ["😃", "👌"],
    "190-219": ["🏀", "🚀"]
}

def generate_weight_message():
    weight = random.randint(0, 250)
    if weight == 0:
        category = "0"
    elif weight <= 49:
        category = "1-49"
    elif weight <= 99:
        category = "50-99"
    elif weight <= 149:
        category = "100-149"
    elif weight <= 199:
        category = "150-199"
    elif weight <= 249:
        category = "200-249"
    else:
        category = "250"
    message = random.choice(weight_messages[category])
    return weight, message

def generate_cock_size_message():
    size = random.randint(0, 50)
    if size == 0:
        category = "0"
    elif size <= 9:
        category = "1-9"
    elif size <= 19:
        category = "10-19"
    elif size <= 29:
        category = "20-29"
    elif size <= 39:
        category = "30-39"
    elif size <= 49:
        category = "40-49"
    else:
        category = "50"
    message = random.choice(cock_size_messages[category])
    return size, message

def generate_iq_message():
    iq = random.randint(50, 200)
    if iq == 200:
        return iq, "👨‍🔬"
    if iq < 70:
        category = "50-69"
    elif iq < 90:
        category = "70-89"
    elif iq < 110:
        category = "90-109"
    elif iq < 130:
        category = "110-129"
    elif iq < 150:
        category = "130-149"
    else:
        category = "150-200"
    message = random.choice(iq_messages[category])
    return iq, message

def generate_height_message():
    height = random.randint(140, 220)
    if height == 220:
        return height, "🇷🇸"
    elif height < 150:
        category = "140-149"
    elif height < 170:
        category = "150-169"
    elif height < 190:
        category = "170-189"
    else:
        category = "190-219"
    message = random.choice(height_messages[category])
    return height, message

def clean_cache():
    now = datetime.now()
    for key, (timestamp, _, _) in list(cache.items()):
        if now - timestamp > CACHE_EXPIRATION:
            del cache[key]
            logger.debug(f"Удалён ключ из кэша: {key}")

def get_main_inline_menu():
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="Вес", callback_data="weight"),
         types.InlineKeyboardButton(text="хуеметр", callback_data="cock")],
        [types.InlineKeyboardButton(text="IQ", callback_data="iq"),
         types.InlineKeyboardButton(text="Рост", callback_data="height")],
        [types.InlineKeyboardButton(text="Хто Я?", callback_data="whoami")]
    ])
    return keyboard

# Функция генерации изображения (содержит caller-информацию в верхней части)
def generate_whoami_image(weight: int, cock_size: int, iq: int, height: int, caller: str) -> io.BytesIO:
    try:
        try:
            font_big = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", size=24)
            font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", size=14)
        except Exception:
            font_big = ImageFont.load_default()
            font_small = ImageFont.load_default()

        img = Image.new("RGB", (400, 400), "white")
        draw = ImageDraw.Draw(img)

        # Верхняя надпись: caller-информация, центрированная
        caller_text = caller
        bbox = draw.textbbox((0, 0), caller_text, font=font_big)
        text_width = bbox[2] - bbox[0]
        draw.text(((400 - text_width) / 2, 5), caller_text, fill="black", font=font_big)

        # Отрисовка стикмена
        # Голова
        head_center = (200, 100)
        head_radius = 40
        draw.ellipse((head_center[0] - head_radius, head_center[1] - head_radius,
                      head_center[0] + head_radius, head_center[1] + head_radius),
                     outline="black", width=2)
        # Глаза
        eye_radius = 5
        draw.ellipse((head_center[0] - 15 - eye_radius, head_center[1] - 10 - eye_radius,
                      head_center[0] - 15 + eye_radius, head_center[1] - 10 + eye_radius),
                     fill="black")
        draw.ellipse((head_center[0] + 15 - eye_radius, head_center[1] - 10 - eye_radius,
                      head_center[0] + 15 + eye_radius, head_center[1] - 10 + eye_radius),
                     fill="black")
        # Грустный рот
        draw.arc((head_center[0] - 20, head_center[1], head_center[0] + 20, head_center[1] + 30),
                 start=210, end=330, fill="black", width=2)

        # Туловище (прямоугольник)
        torso_top = 140
        torso_bottom = 250
        torso_width = 20
        draw.rectangle((200 - torso_width, torso_top, 200 + torso_width, torso_bottom),
                       outline="black", width=2)

        # Руки (от левого и правого углов туловища)
        left_arm_start = (200 - torso_width, torso_top)
        right_arm_start = (200 + torso_width, torso_top)
        left_arm_end = (left_arm_start[0] - 40, left_arm_start[1] + 40)
        right_arm_end = (right_arm_start[0] + 40, right_arm_start[1] + 40)
        draw.line((left_arm_start, left_arm_end), fill="black", width=2)
        draw.line((right_arm_start, right_arm_end), fill="black", width=2)

        # Ноги (от центра нижней части туловища)
        leg_y = torso_bottom
        left_leg_end = (200 - 30, leg_y + 70)
        right_leg_end = (200 + 30, leg_y + 70)
        draw.line((200, leg_y, left_leg_end[0], left_leg_end[1]), fill="black", width=2)
        draw.line((200, leg_y, right_leg_end[0], right_leg_end[1]), fill="black", width=2)

        # "Длина хуя": вертикальная линия от нижней части туловища
        cock_length = cock_size
        draw.line((200, torso_bottom, 200, torso_bottom + cock_length),
                  fill="black", width=2)

        # Текст с параметрами в нижней части
        text_lines = [
            f"Вес: {weight} кг",
            f"Длина хуя: {cock_size} см",
            f"IQ: {iq}",
            f"Рост: {height} см"
        ]
        y_text = 330
        for line in text_lines:
            draw.text((10, y_text), line, fill="black", font=font_small)
            y_text += 15

        bio = io.BytesIO()
        img.save(bio, format="PNG")
        bio.seek(0)
        return bio
    except Exception as e:
        logger.exception("Ошибка при генерации изображения")
        raise

# Класс-обертка для FSInputFile
class MyFSInputFile(FSInputFile):
    def read(self, *args, **kwargs):
        with open(self.path, "rb") as f:
            return f.read()

@dp.message(CommandStart())
async def send_welcome(message: types.Message):
    logger.info(f"Получена команда /start от пользователя {message.from_user.id}")
    await message.answer("Добро пожаловать!", reply_markup=get_main_inline_menu())

@dp.message(Command(commands=["menu"]))
async def send_menu(message: types.Message):
    logger.info(f"Получена команда /menu от пользователя {message.from_user.id}")
    await message.answer("Выберите действие:", reply_markup=get_main_inline_menu())

@dp.callback_query(F.data.in_({"weight", "cock", "iq", "height", "whoami"}))
async def process_callback(callback_query: types.CallbackQuery):
    now = datetime.now()
    user_id = callback_query.from_user.id
    chat_id = callback_query.message.chat.id
    action = callback_query.data
    logger.debug(f"Callback '{action}' от пользователя {user_id} в чате {chat_id}")

    # Формируем caller-информацию для всех не-inline кнопок
    caller = callback_query.from_user.full_name or callback_query.from_user.username or str(user_id)

    if action == "weight":
        key = f"weight_{user_id}"
        if key in cache and now - cache[key][0] <= CACHE_EXPIRATION:
            _, weight, emoji = cache[key]
        else:
            weight, emoji = generate_weight_message()
            cache[key] = (now, weight, emoji)
        # Сообщение вида: <caller>'s weight is <value> <emoji>
        await bot.send_message(chat_id, f"{caller}'s weight is {weight} kg {emoji}")
        await callback_query.answer()

    elif action == "cock":
        key = f"cock_{user_id}"
        if key in cache and now - cache[key][0] <= CACHE_EXPIRATION:
            _, size, emoji = cache[key]
        else:
            size, emoji = generate_cock_size_message()
            cache[key] = (now, size, emoji)
        await bot.send_message(chat_id, f"{caller}'s cock size is {size} cm {emoji}")
        await callback_query.answer()

    elif action == "iq":
        key = f"iq_{user_id}"
        if key in cache and now - cache[key][0] <= CACHE_EXPIRATION:
            _, iq, emoji = cache[key]
        else:
            iq, emoji = generate_iq_message()
            cache[key] = (now, iq, emoji)
        await bot.send_message(chat_id, f"{caller}'s IQ is {iq} {emoji}")
        await callback_query.answer()

    elif action == "height":
        key = f"height_{user_id}"
        if key in cache and now - cache[key][0] <= CACHE_EXPIRATION:
            _, height, emoji = cache[key]
        else:
            height, emoji = generate_height_message()
            cache[key] = (now, height, emoji)
        await bot.send_message(chat_id, f"{caller}'s height is {height} cm {emoji}")
        await callback_query.answer()

    elif action == "whoami":
        param_funcs = {
            "weight": generate_weight_message,
            "cock": generate_cock_size_message,
            "iq": generate_iq_message,
            "height": generate_height_message
        }
        results = {}
        for attr, func in param_funcs.items():
            key = f"{attr}_{user_id}"
            if key in cache and now - cache[key][0] <= CACHE_EXPIRATION:
                _, val, _ = cache[key]
            else:
                val, _ = func()
                cache[key] = (now, val, _)
            results[attr] = val

        weight = results["weight"]
        cock_size = results["cock"]
        iq_val = results["iq"]
        height_val = results["height"]

        # Генерируем изображение с caller, которое отображается на картинке (в верхней части)
        photo_bytes = generate_whoami_image(weight, cock_size, iq_val, height_val, caller)

        # Подпись к фото: ровно "Хто я?"
        caption = "Хто я?"

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp.write(photo_bytes.getvalue())
            tmp_path = tmp.name
        try:
            photo_file = MyFSInputFile(tmp_path, filename="whoami.png")
            await bot.send_photo(chat_id, photo_file, caption=caption)
        finally:
            os.remove(tmp_path)

        await callback_query.answer()

@dp.chat_member(F.new_chat_members.is_bot & F.new_chat_members.id == bot.id)
async def on_bot_added(event: ChatMemberUpdated):
    chat_id = event.chat.id
    logger.info(f"Бот добавлен в чат {chat_id}")
    await bot.send_message(chat_id, "Привет! Я ваш бот. Выберите действие:", reply_markup=get_main_inline_menu())

@dp.inline_query()
async def inline_mode(query: types.InlineQuery):
    user_id = query.from_user.id
    now = datetime.now()
    clean_cache()
    articles = []

    key = f"weight_{user_id}"
    if key in cache and now - cache[key][0] <= CACHE_EXPIRATION:
        _, weight, weight_text = cache[key]
    else:
        weight, weight_text = generate_weight_message()
        cache[key] = (now, weight, weight_text)
    articles.append(InlineQueryResultArticle(
        id=hashlib.md5(f"weight_{weight_text}".encode()).hexdigest(),
        title="Проверь свой вес",
        input_message_content=InputTextMessageContent(
            message_text=f"My weight is {weight} kg {weight_text}"
        ),
        description="Получите ваш случайный вес"
    ))

    key = f"cock_{user_id}"
    if key in cache and now - cache[key][0] <= CACHE_EXPIRATION:
        _, cock_size, cock_text = cache[key]
    else:
        cock_size, cock_text = generate_cock_size_message()
        cache[key] = (now, cock_size, cock_text)
    articles.append(InlineQueryResultArticle(
        id=hashlib.md5(f"cock_{cock_text}".encode()).hexdigest(),
        title="Проверь свой хуеметр",
        input_message_content=InputTextMessageContent(
            message_text=f"My cock size is {cock_size} cm {cock_text}"
        ),
        description="Получите ваш случайный размер"
    ))

    key = f"iq_{user_id}"
    if key in cache and now - cache[key][0] <= CACHE_EXPIRATION:
        _, iq, iq_text = cache[key]
    else:
        iq, iq_text = generate_iq_message()
        cache[key] = (now, iq, iq_text)
    articles.append(InlineQueryResultArticle(
        id=hashlib.md5(f"iq_{iq_text}".encode()).hexdigest(),
        title="Проверь свой IQ",
        input_message_content=InputTextMessageContent(
            message_text=f"My IQ is {iq} {iq_text}"
        ),
        description="Получите ваш случайный IQ"
    ))

    key = f"height_{user_id}"
    if key in cache and now - cache[key][0] <= CACHE_EXPIRATION:
        _, height, height_text = cache[key]
    else:
        height, height_text = generate_height_message()
        cache[key] = (now, height, height_text)
    articles.append(InlineQueryResultArticle(
        id=hashlib.md5(f"height_{height_text}".encode()).hexdigest(),
        title="Проверь свой рост",
        input_message_content=InputTextMessageContent(
            message_text=f"My height is {height} cm {height_text}"
        ),
        description="Получите ваш случайный рост"
    ))

    combined_message = (
        f"My weight is {weight} kg {weight_text}\n"
        f"My cock size is {cock_size} cm {cock_text}\n"
        f"My IQ is {iq} {iq_text}\n"
        f"My height is {height} cm {height_text}"
    )
    articles.append(InlineQueryResultArticle(
        id=hashlib.md5(f"whoami_{user_id}".encode()).hexdigest(),
        title="Хто я?",
        input_message_content=InputTextMessageContent(
            message_text=combined_message
        ),
        description="Получите все характеристики сразу"
    ))

    await query.answer(articles, cache_time=1)

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
