from aiogram import Bot, Dispatcher
from aiogram.types import InlineQuery, InlineQueryResultArticle, InputTextMessageContent, Message
from aiogram.filters import CommandStart
import random
import hashlib
import asyncio
from datetime import datetime, timedelta
from dotenv import load_dotenv
import os

# Загрузить переменные окружения
load_dotenv()

API_TOKEN = os.getenv("API_TOKEN")

# Имя бота
BOT_USERNAME = "psi_cock_size_bot"

# Инициализация бота и диспетчера
bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# Кэш для хранения результатов
cache = {}
CACHE_EXPIRATION = timedelta(hours=6)

# Функция для генерации сообщения
def generate_message():
    size = random.randint(0, 50)
    if size <= 9:
        emoji = "🤮"
    elif size <= 19:
        emoji = "🥴"
    elif size <= 29:
        emoji = "😐"
    elif size <= 39:
        emoji = "😲"
    elif size <= 49:
        emoji = "🤯"
    else:
        emoji = "🫡"
    return size, f"My cock size is {size}cm {emoji}"

# Очистка устаревших записей в кэше
def clean_cache():
    now = datetime.now()
    keys_to_delete = [key for key, (timestamp, _) in cache.items() if now - timestamp > CACHE_EXPIRATION]
    for key in keys_to_delete:
        del cache[key]

# Обработчик команды /start
@dp.message(CommandStart())
async def send_welcome(message: Message):
    await message.answer("Welcome! Use inline mode by typing @psi_cock_size_bot in any chat.")

# Обработчик Inline Query
@dp.inline_query()
async def inline_mode(query: InlineQuery):
    user_id = query.from_user.id
    now = datetime.now()

    # Очистка устаревших записей
    clean_cache()

    # Проверка кэша для текущего пользователя
    if user_id in cache:
        timestamp, text = cache[user_id]
        if now - timestamp <= CACHE_EXPIRATION:
            # Возвращаем сохранённое значение
            result_id = hashlib.md5(text.encode()).hexdigest()
            result = InlineQueryResultArticle(
                id=result_id,
                title="Check your size",
                input_message_content=InputTextMessageContent(
                    message_text=f"{text}"
                ),
                description="Get your random size",
            )
            await query.answer([result], cache_time=1)
            return

    # Генерация нового значения
    size, text = generate_message()
    cache[user_id] = (now, text)

    # Создание уникального ID для ответа (hash)
    result_id = hashlib.md5(text.encode()).hexdigest()

    # Формируем ответ
    result = InlineQueryResultArticle(
        id=result_id,
        title="Check your size",
        input_message_content=InputTextMessageContent(
            message_text=f"{text} "
        ),
        description="Get your random size",
    )

    # Отправляем результат
    await query.answer([result], cache_time=1)

# Запуск бота
async def main():
    # Удаляем старый вебхук, если он существует
    await bot.delete_webhook(drop_pending_updates=True)

    # Запуск поллинга
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

