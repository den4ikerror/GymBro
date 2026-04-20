# handlers/nutrition.py
import asyncio
import contextlib
import time

from aiogram import Router, types, F
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from handlers.common import user_context, user_caption
from gemini_service import analyze_image
from database import get_user_data, log_activity

import logging

router = Router()


def estimate_daily_calories(weight: float, height: float, age: int = 25, sex: str = "male", activity_factor: float = 1.4) -> int:
    """Простий орієнтир щоденної норми калорій (TDEE) на основі Mifflin-St Jeor."""
    if sex.lower().startswith("f"):
        bmr = 10 * weight + 6.25 * height - 5 * age - 161
    else:
        bmr = 10 * weight + 6.25 * height - 5 * age + 5
    return int(bmr * activity_factor)

@router.message(F.text == "🥗 Аналіз їжі")
async def nutrition_info(message: types.Message):
    user_context[message.chat.id] = "food"
    await message.answer(
        "Надішліть мені фото вашої страви (можете додати підпис для контексту), "
        "і я проаналізую її склад та корисність. 📸"
    )

@router.message(lambda m: bool(m.photo) and (user_context.get(m.chat.id) == "food" or (m.caption and "їжа" in m.caption.lower())))
# Обробник фото для аналізу їжі/тіла (визначаємо за останньою кнопкою, яку натиснули)
async def handle_food_photo(message: types.Message, bot):
    # Отримуємо фото у найвищій якості
    photo = message.photo[-1]
    file = await bot.get_file(photo.file_id)
    file_bytes = await bot.download_file(file.file_path)

    # Якщо остання кнопка була "Аналіз тіла" — нічого не робимо (обробляє fitness.py)
    mode = user_context.get(message.chat.id)
    if mode == "body":
        return

    caption = message.caption or user_caption.pop(message.chat.id, None)

    # Орієнтовна добова норма калорій (для підтримки ваги)
    norm_text = ""
    try:
        weight, height, *_ = await get_user_data()
        if weight and height:
            norm = estimate_daily_calories(weight, height)
            norm_text = (
                f"📌 Орієнтовна добова норма (підтримка ваги): ~{norm} ккал.\n"
                f"(Враховано вагу {weight:.1f} кг, ріст {height:.0f} см, вік ~25, активність помірна)\n\n"
            )
    except Exception:
        pass

    msg = await message.answer("🔎 Аналізую вашу тарілку... Зачекайте, будь ласка.")

    estimated_secs = 6

    async def _progress():
        start = time.monotonic()
        last_text = None
        try:
            while True:
                elapsed = time.monotonic() - start
                percent = min(99, int((elapsed / estimated_secs) * 100))
                remaining = max(0, int(estimated_secs - elapsed))
                dots = "." * ((int(elapsed) % 3) + 1)
                text = (
                    f"🔎 Аналізую вашу тарілку {dots}\n"
                    f"⏳ {percent}% (≈{remaining}s залишилось)"
                )
                if text != last_text:
                    try:
                        await msg.edit_text(text)
                    except Exception:
                        pass
                    last_text = text
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            return

    progress_task = asyncio.create_task(_progress())

    def _sync_analyze(img_bytes, ctx_text):
        # Виконати асинхронний виклик у потоці, щоб не блокувати основний цикл
        return asyncio.new_event_loop().run_until_complete(
            analyze_image([img_bytes], "food", context_text=ctx_text)
        )

    try:
        # Читаємо байти фото
        img_data = file_bytes.read()

        # Викликаємо Gemini (через потік, щоб прогрес жив)
        analysis = await asyncio.to_thread(_sync_analyze, img_data, caption)

        await msg.edit_text(f"{norm_text}✅ Аналіз завершено:\n\n{analysis}")

        # Зберігаємо лог аналізу
        await log_activity("food", caption or "", analysis)
    except Exception as e:
        await msg.edit_text(f"{norm_text}❌ Помилка при аналізі: {e}")
        analysis = f"Помилка: {e}"
    finally:
        progress_task.cancel()
        with contextlib.suppress(Exception):
            await progress_task

    # Скидаємо стан, щоб наступне фото не аналізувалось як їжа, якщо користувач не натисне кнопку
    user_context.pop(message.chat.id, None)
    user_caption.pop(message.chat.id, None)


@router.message(F.text == "🥦 План харчування")
async def nutrition_plan(message: types.Message):
    user_context[message.chat.id] = "nutrition_plan"
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🏠 Головне меню")]
        ],
        resize_keyboard=True,
    )
    await message.answer(
        "🥦 План харчування!\n\nОпиши свої цілі та обмеження (наприклад: 'схуднути, алергія на горіхи').\n"
        "Напиши текстом, і я складу план.",
        reply_markup=kb,
    )


@router.message(lambda m: m.text and user_context.get(m.chat.id) == "nutrition_plan")
async def handle_nutrition_plan_input(message: types.Message):
    chat_id = message.chat.id
    goals = message.text.strip()

    # Отримуємо дані користувача
    weight, height, *_ = await get_user_data()
    if not weight or not height:
        await message.answer("Спочатку онови свою вагу та ріст у меню.")
        user_context.pop(chat_id, None)
        return

    # Розрахунок BMR та TDEE
    age = 25  # Можна додати в конфіг
    sex = "male"
    activity_factor = 1.4  # Помірна активність
    if sex.lower().startswith("f"):
        bmr = 10 * weight + 6.25 * height - 5 * age - 161
    else:
        bmr = 10 * weight + 6.25 * height - 5 * age + 5
    tdee = int(bmr * activity_factor)

    prompt = (
        f"Створи персональний план харчування для користувача: {sex}, {age} років, вага {weight} кг, ріст {height} см. "
        f"Щоденна норма калорій: ~{tdee} ккал для підтримки ваги. "
        f"Цілі: {goals}. "
        "План має включати: загальну калорійність, макронутрієнти (білки, жири, вуглеводи), приклади страв на день, поради. "
        "Відповідь у Markdown, коротко."
    )

    try:
        from gemini_service import chat
        plan = await chat(prompt, history=None)
        kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="🏠 Головне меню")]
            ],
            resize_keyboard=True,
        )
        await message.answer(f"🥦 **Твій план харчування:**\n\n{plan}", parse_mode="Markdown", reply_markup=kb)
        await log_activity("nutrition_plan", goals, plan)
    except Exception as e:
        await message.answer(f"❌ Помилка: {e}")

    user_context.pop(chat_id, None)
