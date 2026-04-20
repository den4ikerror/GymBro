import asyncio
from datetime import datetime, timedelta
from aiogram import Bot
from config import TELEGRAM_TOKEN
from database import get_recent_activities, get_user_data
from gemini_service import chat

bot = Bot(token=TELEGRAM_TOKEN)

async def weekly_summary(chat_id: int):
    """Надсилає щотижневий підсумок досягнень та падінь."""
    # Отримуємо дані за тиждень
    week_ago = (datetime.now() - timedelta(days=7)).isoformat()
    activities = await get_recent_activities(50)  # Багато, щоб покрити тиждень
    recent = [a for a in activities if a[1] >= week_ago]

    weight_changes = [a for a in recent if a[0] == "weight"]
    body_analyses = [a for a in recent if a[0] == "body"]
    workouts = [a for a in recent if a[0] in ("leg_day", "workout_plan")]

    summary = "📊 **Щотижневий підсумок:**\n\n"

    if weight_changes:
        last_weight = float(weight_changes[0][2])
        first_weight = float(weight_changes[-1][2]) if len(weight_changes) > 1 else last_weight
        delta = last_weight - first_weight
        summary += f"⚖️ Зміна ваги: {delta:+.1f} кг\n"

    summary += f"🏋️ Тренувань: {len(workouts)}\n"
    summary += f"📸 Аналізів тіла: {len(body_analyses)}\n"

    # AI-підсумок
    prompt = f"Підсумуй досягнення та падіння за тиждень на основі: {summary}. Дай мотивацію."
    try:
        ai_summary = await chat(prompt, history=None)
        summary += f"\n🤖 AI-аналіз: {ai_summary}"
    except:
        pass

    await bot.send_message(chat_id, summary, parse_mode="Markdown")

async def schedule_weekly_analysis(chat_id: int):
    """Аналіз після 7 днів трекінгу."""
    await asyncio.sleep(7 * 24 * 3600)  # 7 днів
    await weekly_analysis(chat_id)


async def weekly_analysis(chat_id: int):
    """Аналіз тижневих даних та створення планів."""
    # Отримати дані за тиждень
    week_ago = (datetime.now() - timedelta(days=7)).isoformat()
    activities = await get_recent_activities(100)  # Багато
    recent = [a for a in activities if a[1] >= week_ago]

    foods = [a for a in recent if a[0] == "food"]
    workouts = [a for a in recent if a[0] == "workout"]
    bodies = [a for a in recent if a[0] == "body"]

    summary = f"За тиждень: {len(foods)} аналізів їжі, {len(workouts)} тренувань, {len(bodies)} фото тіла."

    # ШІ аналіз
    prompt = (
        f"На основі даних за тиждень: {summary}. "
        "Створи план дієти, тренувань. Запитай про вільні дні та час."
    )
    try:
        plan = await chat(prompt, history=None)
        await bot.send_message(chat_id, f"📋 **Тижневий аналіз завершено!**\n\n{plan}\n\n"
                                        "Які дні у вас вільні для тренувань? Скільки часу можете витрачати щодня?",
                             parse_mode="Markdown")
    except Exception as e:
        await bot.send_message(chat_id, f"❌ Помилка аналізу: {e}")