# bot.py
import asyncio
import logging
from aiogram import Bot, Dispatcher
from config import TELEGRAM_TOKEN
from database import init_db
from handlers import common, nutrition, fitness
from utils.scheduler import schedule_weekly_analysis, scheduled_daily_greeting

logging.basicConfig(level=logging.DEBUG)

async def main():
    await init_db()
    
    bot = Bot(token=TELEGRAM_TOKEN)
    dp = Dispatcher()

    # Перевірка підключення до Telegram (для швидкої діагностики токена)
    try:
        me = await bot.get_me()
        logging.debug(f"Telegram bot loaded: {me.username} ({me.id})")
    except Exception as e:
        logging.error(f"Не вдалося підключитися до Telegram: {e}")
        raise
    
    # Реєструємо всі наші модулі
    dp.include_router(common.router)
    dp.include_router(nutrition.router)
    dp.include_router(fitness.router)
    
    # Запускаємо планувальники в фоні
    asyncio.create_task(scheduled_daily_greeting())
    # asyncio.create_task(schedule_weekly_analysis(123456789))  # Замінити на реальний chat_id
    
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Бот зупинений")
