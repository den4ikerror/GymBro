# handlers/fitness.py
import asyncio
import contextlib
import difflib
import time
from datetime import datetime

from aiogram import Bot, Router, types, F
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from handlers.common import user_context, user_caption, user_body_photos, get_main_menu
from gemini_service import analyze_image
from database import get_user_data, get_recent_activities, log_activity

import logging

router = Router()

# Форматує ISO-дату у текст типу: сьогодні / вчора / 3 дні тому / 2 тижні тому
def _relative_date_label(iso_ts: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_ts)
    except Exception:
        return iso_ts

    delta = datetime.now() - dt
    days = delta.days
    if days == 0:
        return "сьогодні"
    if days == 1:
        return "вчора"
    if days < 7:
        return f"{days} днів тому"
    if days < 30:
        weeks = days // 7
        return f"{weeks} тижні тому" if weeks == 1 else f"{weeks} тижнів тому"
    months = days // 30
    return f"{months} місяць тому" if months == 1 else f"{months} місяців тому"


# Збереження фото та приписів під час режиму "аналіз тіла" (натиснута кнопка)
# Формат: {chat_id: [ {"bytes": ..., "caption": ...}, ... ]}

async def _progress_indicator(msg: types.Message, estimated_seconds: int):
    """Оновлюємо повідомлення з прогресом (відсотки + приблизний час)."""
    start = time.monotonic()
    last_text = None
    try:
        while True:
            elapsed = time.monotonic() - start
            percent = min(99, int((elapsed / estimated_seconds) * 100))
            remaining = max(0, int(estimated_seconds - elapsed))
            dots = "." * ((int(elapsed) % 3) + 1)
            text = (
                f"🔎 Аналізую твою форму {dots}\n"
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


async def _run_body_analysis(chat_id: int, bot: Bot, caption: str | None):
    photo_entries = user_body_photos.pop(chat_id, [])
    if not photo_entries:
        await bot.send_message(chat_id, "📷 Я не знайшов жодного фото для аналізу. Надішли, будь ласка, фото перед запуском аналізу.")
        return

    # Підготуємо байти фото та збір контексту (додаємо підписи до фото як окремі рядки)
    image_bytes = [entry.get("bytes") for entry in photo_entries if entry.get("bytes")]
    photo_captions = [entry.get("caption") for entry in photo_entries if entry.get("caption")]

    context_parts: list[str] = []
    if caption:
        context_parts.append(caption)
    if photo_captions:
        context_parts.append("\n".join(f"Фото {i+1}: {c}" for i, c in enumerate(photo_captions)))
    context_text = "\n\n".join(context_parts) if context_parts else None

    msg = await bot.send_message(chat_id, "🔎 Розпочинаю аналіз... Зачекай, будь ласка.")
    estimated_secs = max(5, len(image_bytes) * 6)
    progress_task = asyncio.create_task(_progress_indicator(msg, estimated_secs))

    try:
        analysis = await analyze_image(image_bytes, "body", context_text=context_text)
        await msg.edit_text(f"✅ Аналіз завершено:\n\n{analysis}")
    except Exception as e:
        await msg.edit_text(f"❌ Помилка при аналізі: {e}")
        analysis = f"Помилка: {e}"
    finally:
        progress_task.cancel()
        with contextlib.suppress(Exception):
            await progress_task

    # Зберігаємо лог аналізу тіла, але перед цим отримуємо попередній аналіз для порівняння
    previous_entries = await get_recent_activities(2, activity_type="body")
    compare_text = ""
    if previous_entries:
        prev_ts = previous_entries[0][1]
        prev_result = previous_entries[0][3] or ""
        prev_label = _relative_date_label(prev_ts)
        today_label = _relative_date_label(datetime.now().isoformat())

        # Формуємо різницю між попереднім аналізом та поточним
        diff_lines = list(
            difflib.unified_diff(
                prev_result.splitlines(),
                analysis.splitlines(),
                fromfile=f"{prev_label}",
                tofile=f"{today_label}",
                lineterm="",
            )
        )
        if diff_lines:
            formatted = []
            for l in diff_lines:
                # Пропускаємо технічні рядки diff
                if l.startswith(("+++", "---", "@@")):
                    continue
                if l.startswith("+"):
                    formatted.append("✅ " + l[1:].strip())
                elif l.startswith("-"):
                    formatted.append("❌ " + l[1:].strip())
            diff_summary = "\n".join(formatted[:8])
            if diff_summary:
                compare_text = (
                    "\n\n🔎 Я бачу різницю між "
                    f"{prev_label} та {today_label}:\n{diff_summary}"
                )

    log_desc = caption or ""
    if not log_desc and photo_captions:
        log_desc = photo_captions[0]
    await log_activity("body", log_desc, analysis)

    # Скидаємо стан
    user_context.pop(chat_id, None)
    user_caption.pop(chat_id, None)

    if compare_text:
        await bot.send_message(chat_id, compare_text, parse_mode="Markdown")

@router.message(F.text == "📸 Аналіз тіла")
async def body_info(message: types.Message):
    user_context[message.chat.id] = "body_collect"
    user_body_photos.pop(message.chat.id, None)
    user_caption.pop(message.chat.id, None)

    logging.debug(f"[body_info] user={message.chat.id} mode=body_collect")

    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="✅ Проаналізувати"), KeyboardButton(text="❌ Скасувати")],
            [KeyboardButton(text="🏠 Головне меню")]
        ],
        resize_keyboard=True,
    )

    await message.answer(
        "📸 Аналіз тіла!\n\nНадішли мені кілька фото свого тіла (по одному).\n"
        "Після кожного фото можеш написати коментар, або надіслати текст окремо — я його прив'яжу до фото.\n\n"
        "Коли надішлеш усі фото, натисни '✅ Проаналізувати'.\n"
        "Якщо передумав, натисни '❌ Скасувати' або '🏠 Головне меню'.",
        reply_markup=kb,
    )


@router.message(F.text == "✅ Проаналізувати")
async def run_body_analysis(message: types.Message, bot: Bot):
    mode = user_context.get(message.chat.id)
    logging.debug(f"[run_body_analysis] user={message.chat.id} mode={mode}")
    if mode != "body_collect":
        await message.answer("Спочатку натисни «📸 Аналіз тіла», щоб зберегти фото для аналізу.")
        return

    caption = user_caption.pop(message.chat.id, None)
    await _run_body_analysis(message.chat.id, bot, caption)

    # Повертаємо головне меню після аналізу
    await message.answer("Готово!", reply_markup=get_main_menu())


@router.message(F.text == "❌ Скасувати")
async def cancel_body_analysis(message: types.Message):
    mode = user_context.get(message.chat.id)
    if mode != "body_collect":
        return

    user_context.pop(message.chat.id, None)
    user_body_photos.pop(message.chat.id, None)
    user_caption.pop(message.chat.id, None)
    await message.answer(
        "✅ Режим аналізу скасовано.",
        reply_markup=get_main_menu(),
    )


@router.message(F.text == "🏠 Головне меню")
async def go_home(message: types.Message):
    user_context.pop(message.chat.id, None)
    user_body_photos.pop(message.chat.id, None)
    user_caption.pop(message.chat.id, None)
    await message.answer("🏠 Повернувся до головного меню.", reply_markup=get_main_menu())


@router.message(Command("debug"))
async def debug_status(message: types.Message):
    """Показує поточний стан збору фото та режим користувача."""
    mode = user_context.get(message.chat.id)
    photo_count = len(user_body_photos.get(message.chat.id, []))
    caption = user_caption.get(message.chat.id)

    await message.answer(
        f"🧪 DEBUG:\n"
        f"- режим: {mode}\n"
        f"- фото збережено: {photo_count}\n"
        f"- підпис: {caption or 'немає'}\n"
        f"\nЯкщо бот не реагує на фото, перевір лог в консолі (DEBUG) або перезапусти бота."
    )


@router.message(lambda message: message.text and user_context.get(message.chat.id) == "body_collect")
async def body_collect_text(message: types.Message):
    """Прив'язуємо текст до останнього надісланого фото або зберігаємо як підпис для наступного."""
    chat_id = message.chat.id
    text = message.text.strip()

    photos = user_body_photos.get(chat_id, [])
    if photos:
        # Якщо останнє фото без підпису, додаємо до нього
        for entry in reversed(photos):
            if not entry.get("caption"):
                entry["caption"] = text
                await message.answer("✅ Опис додано до останнього фото.")
                return

    # Інакше зберігаємо як підпис для наступного фото
    user_caption[chat_id] = text
    await message.answer(
        "✅ Готово! Цей текст буде використано як підпис до наступного фото."
    )


@router.message(F.text == "📊 Мій прогрес")
async def show_progress(message: types.Message):
    # Знімаємо режим аналізу фото
    user_context.pop(message.chat.id, None)

    data = await get_user_data()  # (weight, height, streak, last_photo_date)
    weight, height, streak, last_date = data
    last_date_str = last_date.split("T")[0] if last_date else "ще немає фото"

    # Аналіз тренду ваги (останні 3 оновлення)
    weight_entries = await get_recent_activities(3, activity_type="weight")
    weight_text = ""
    if weight_entries:
        last_weight_ts = weight_entries[0][1]
        last_weight_label = _relative_date_label(last_weight_ts)
        weight_text = f" (ост. оновлення: {last_weight_label})"

        if len(weight_entries) >= 2:
            try:
                last_val = float(weight_entries[0][2])
                prev_val = float(weight_entries[1][2])
                delta = last_val - prev_val
                trend_icon = "📉" if delta < 0 else "📈" if delta > 0 else "➡️"
                weight_text += f"\n  • {trend_icon} зміна за останній запис: {delta:+.1f} кг"

                if len(weight_entries) == 3:
                    first_val = float(weight_entries[2][2])
                    total_delta = last_val - first_val
                    weight_text += f"\n  • Загальна зміна: {total_delta:+.1f} кг"
            except Exception:
                pass

    # Аналіз оновлення росту
    height_entries = await get_recent_activities(3, activity_type="height")
    height_text = ""
    if height_entries:
        last_height_ts = height_entries[0][1]
        last_height_label = _relative_date_label(last_height_ts)
        height_text = f" (ост. оновлення: {last_height_label})"

    # Порівняння останніх аналізів тіла
    compare_text = ""
    body_entries = await get_recent_activities(2, activity_type="body")
    if len(body_entries) >= 2:
        prev_ts = body_entries[1][1]
        prev_result = body_entries[1][3] or ""
        prev_date = prev_ts.split("T")[0]
        today_date = body_entries[0][1].split("T")[0]

        diff_lines = list(
            difflib.unified_diff(
                prev_result.splitlines(),
                body_entries[0][3].splitlines() if body_entries[0][3] else [],
                fromfile=f"{prev_date}",
                tofile=f"{today_date}",
                lineterm="",
            )
        )
        if diff_lines:
            formatted = []
            for l in diff_lines:
                if l.startswith(("+++", "---", "@@")):
                    continue
                if l.startswith("+"):
                    formatted.append("✅ " + l[1:].strip())
                elif l.startswith("-"):
                    formatted.append("❌ " + l[1:].strip())
            diff_str = "\n".join(formatted[:8])
            if diff_str:
                compare_text = (
                    "\n\n🔎 **Що змінилося з останнього аналізу:**\n"
                    f"{diff_str}"
                )

    text = (
        f"📈 **Твій прогрес:**\n\n"
        f"⚖️ Вага: {weight} кг{weight_text}\n"
        f"📏 Ріст: {height} см{height_text}\n"
        f"🔥 Стрік тренувань: {streak} днів\n"
        f"📅 Останній аналіз тіла: {last_date_str}"
        f"{compare_text}\n\n"
        "📌 Порада: надсилай фото хоча б раз на 2–3 дні, щоб бачити справжні зміни."
    )
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🏠 Головне меню")]
        ],
        resize_keyboard=True,
    )
    await message.answer(text, parse_mode="Markdown", reply_markup=kb)

# Обробник для фото тіла (як фото, так і документ-імідж)
@router.message(F.content_type.in_(["photo", "document"]))
async def handle_body_photos(message: types.Message, bot):
    mode = user_context.get(message.chat.id)
    logging.debug(f"[handle_body_photos] user={message.chat.id} mode={mode} caption={bool(message.caption)}")

    if mode == "chat":
        # У режимі чат бот може одночасно аналізувати будь-яке фото.
        # Це не зберігається у логах прогресу тіла.
        caption = message.caption

        if message.photo:
            file_item = message.photo[-1]
            file = await bot.get_file(file_item.file_id)
        elif message.document and message.document.mime_type and message.document.mime_type.startswith("image/"):
            file = await bot.get_file(message.document.file_id)
        else:
            await message.answer("Я отримав повідомлення, але не знайшов в ньому фото. Спробуй надіслати як зображення (не як файл).")
            return

        file_bytes = await bot.download_file(file.file_path)
        analysis = await analyze_image([file_bytes.read()], "image", context_text=caption)
        await message.answer(analysis)
        return

    if mode != "body_collect":
        await message.answer(
            "Щоб я проаналізував твоє тіло, спочатку натисни кнопку «📸 Аналіз тіла».\n"
            "Потім надішли фото (по одному). Коли будеш готовий, натисни «✅ Проаналізувати»."
        )
        return

    chat_id = message.chat.id

    # Якщо користувач додав підпис разом із фото — додамо його, інакше спробуємо взяти попередній коментар
    caption = message.caption or user_caption.pop(chat_id, None)

    # Отримуємо bytes з фото або документу (якщо фото було надіслано як файл)
    if message.photo:
        file_item = message.photo[-1]
        file = await bot.get_file(file_item.file_id)
    elif message.document and message.document.mime_type and message.document.mime_type.startswith("image/"):
        file = await bot.get_file(message.document.file_id)
    else:
        await message.answer("Я отримав повідомлення, але не знайшов в ньому фото. Спробуй надіслати як зображення (не як файл).")
        return

    file_bytes = await bot.download_file(file.file_path)
    user_body_photos.setdefault(chat_id, []).append({"bytes": file_bytes.read(), "caption": caption})

    logging.debug(
        f"[handle_body_photos] saved photo for user={chat_id} total={len(user_body_photos.get(chat_id, []))}"
    )

    await message.answer(
        "✅ Фото збережено. Якщо хочеш додати опис до цього фото, просто напиши текст. "
        "Надішли ще або натисни «✅ Проаналізувати», коли будеш готовий."
    )
