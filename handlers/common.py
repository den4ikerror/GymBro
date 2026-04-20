# handlers/common.py
import aiosqlite
import asyncio
from datetime import datetime

from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.utils.keyboard import ReplyKeyboardBuilder

from gemini_service import analyze_image, chat
from database import (
    get_recent_activities,
    get_user_data,
    log_activity,
    set_user_height,
    set_user_weight,
    DB_NAME,
)

router = Router()

# Простий стан, щоб знати, що користувач хоче аналізувати (їжу чи тіло).
# Використовується тільки для одного чату, без збереження в БД.
user_context: dict[int, str] = {}

# Можемо зберігати текстовий коментар, який користувач надіслав перед фото.
# Використовується як підпис, якщо самі фото не містять caption.
user_caption: dict[int, str] = {}

# Історія діалогу для режиму чат (щоб асистент пам'ятав попередні питання).
user_chat_history: dict[int, list[str]] = {}

# Тимчасово зберігаємо введені метрики (вага/ріст) перед їх підтвердженням.
user_pending_metrics: dict[int, dict[str, float]] = {}

# Збереження фото тіла під час режиму аналізу
user_body_photos: dict[int, list[dict]] = {}

def get_main_menu():
    builder = ReplyKeyboardBuilder()
    builder.button(text="💬 Чат")
    builder.button(text="📸 Аналіз тіла")
    builder.button(text="🥗 Аналіз їжі")
    builder.button(text="🥦 План харчування")
    builder.button(text="📋 План тренувань")
    builder.button(text="🏋️ Аналіз тренажера")
    builder.button(text="⚖️ Оновити вагу")
    builder.button(text="📏 Оновити ріст")
    builder.button(text="🦵 День ніг")
    builder.button(text="📊 Мій прогрес")
    builder.button(text="🗑️ Очистити дані")
    builder.adjust(3)  # Кнопки по 3 у рядку
    return builder.as_markup(resize_keyboard=True)

@router.message(Command("start"))
async def start_cmd(message: types.Message):
    welcome_text = (
        "👋 Привіт! Я твій фітнес-друг!\n\n"
        "Я допоможу тобі стати здоровішим та сильнішим.\n"
        "Обери, що хочеш зробити, натискаючи кнопки нижче.\n\n"
        "Почни з '🗑️ Очистити дані' якщо хочеш почати заново, або обери інше."
    )
    await message.answer(welcome_text, reply_markup=get_main_menu())


@router.message(lambda message: message.text == "💬 Чат")
async def chat_mode(message: types.Message):
    user_context[message.chat.id] = "chat"
    user_caption.pop(message.chat.id, None)
    user_chat_history.pop(message.chat.id, None)

    stop_kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📜 Підсумок чату"), KeyboardButton(text="⏹️ Зупинити чат")],
            [KeyboardButton(text="🏠 Головне меню")]
        ],
        resize_keyboard=True,
    )

    await message.answer(
        "💬 Чат з асистентом!\n\nНапиши мені будь-що про фітнес, харчування, мотивацію.\n"
        "Я відповім і запам'ятаю розмову.\n\n"
        "Натисни '⏹️ Зупинити чат' щоб вийти, або '📜 Підсумок чату' для переказу.",
        reply_markup=stop_kb,
    )


@router.message(lambda message: message.text and user_context.get(message.chat.id) == "chat")
async def chat_handler(message: types.Message):
    chat_id = message.chat.id
    history = user_chat_history.get(chat_id, []) or []

    response = await chat(message.text, history=history)

    # Зберігаємо діалог (останні 10 рядків), щоб підтримувати контекст
    history.append(f"Користувач: {message.text}")
    history.append(f"Асистент: {response}")
    user_chat_history[chat_id] = history[-10:]

    await message.answer(response)


@router.message(lambda message: message.text == "⏹️ Зупинити чат")
async def stop_chat(message: types.Message):
    """Вихід із режиму чат та повернення до головного меню."""
    user_context.pop(message.chat.id, None)
    user_chat_history.pop(message.chat.id, None)
    await message.answer(
        "Чат зупинено. Повернувся до головного меню.",
        reply_markup=get_main_menu(),
    )


@router.message(lambda message: message.text == "📜 Підсумок чату")
async def chat_summary(message: types.Message):
    chat_id = message.chat.id
    history = user_chat_history.get(chat_id, [])
    if not history:
        await message.answer("Історія чату порожня.")
        return

    # Останні 20 повідомлень
    recent_history = history[-20:]
    prompt = f"Коротко перекажи діалог: {' '.join(recent_history)}"
    try:
        summary = await chat(prompt, history=None)
        await message.answer(f"📜 **Підсумок чату:**\n{summary}", parse_mode="Markdown")
    except Exception as e:
        await message.answer(f"❌ Помилка: {e}")


@router.message(Command("stop"))
async def stop_command(message: types.Message):
    """Команда /stop зупиняє режим чат (якщо було активовано)."""
    await stop_chat(message)


@router.message(Command("set_weight"))
async def set_weight_command(message: types.Message):
    parts = message.text.strip().split()
    if len(parts) != 2:
        return await message.answer("Використовуй: /set_weight 78.5")
    try:
        weight = float(parts[1].replace(",", "."))
    except ValueError:
        return await message.answer("Невірний формат ваги. Напиши, наприклад: /set_weight 78.5")

    await _handle_metric_update(message, "weight", weight)


@router.message(Command("set_height"))
async def set_height_command(message: types.Message):
    parts = message.text.strip().split()
    if len(parts) != 2:
        return await message.answer("Використовуй: /set_height 178")
    try:
        height = float(parts[1].replace(",", "."))
    except ValueError:
        return await message.answer("Невірний формат росту. Напиши, наприклад: /set_height 178")

    await _handle_metric_update(message, "height", height)

@router.message(Command("history"))
async def history_command(message: types.Message):
    entries = await get_recent_activities(6)
    if not entries:
        return await message.answer(
            "Поки що історія порожня. Надішли фото для аналізу, і я збережу результати."
        )

    lines = ["📜 Останні аналізи:"]
    for entry_type, ts, desc, result in entries:
        when = ts.split("T")[0]
        desc_text = desc or "без підпису"
        result_snippet = (result or "").split("\n")[0]
        if result_snippet:
            lines.append(f"- {when} | {entry_type} | {desc_text}\n  {result_snippet}")
        else:
            lines.append(f"- {when} | {entry_type} | {desc_text}")

    await message.answer("\n".join(lines))

@router.message(lambda message: message.text == "⚖️ Оновити вагу")
async def update_weight_button(message: types.Message):
    user_context[message.chat.id] = "update_weight"
    user_caption.pop(message.chat.id, None)
    await message.answer(
        "Введи свою вагу в кілограмах (наприклад: 78.5). Я запам’ятаю і порівняю з попередньою.",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(lambda message: message.text == "📏 Оновити ріст")
async def update_height_button(message: types.Message):
    user_context[message.chat.id] = "update_height"
    user_caption.pop(message.chat.id, None)
    await message.answer(
        "Введи свій ріст у сантиметрах (наприклад: 178).",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(lambda message: message.text and not message.text.startswith("/") and user_context.get(message.chat.id) in ("update_weight", "update_height"))
async def handle_metric_input(message: types.Message):
    mode = user_context.get(message.chat.id)
    metric = "weight" if mode == "update_weight" else "height"

    try:
        value = float(message.text.strip().replace(",", "."))
    except ValueError:
        return await message.answer(
            "Невірний формат. Введи число (наприклад 78.5 для ваги або 178 для росту)."
        )

    await _handle_metric_update(message, metric, value)


async def _handle_metric_update(message: types.Message, metric: str, new_value: float):
    """Оновлює метрику (вага/ріст), зберігає у БД та робить короткий аналіз прогресу."""
    chat_id = message.chat.id

    # Поточні збережені дані
    current_weight, current_height, _, _ = await get_user_data()
    previous_value = current_weight if metric == "weight" else current_height

    # Інформація про останнє оновлення метрики
    recent = await get_recent_activities(1, activity_type=metric)
    last_update_ts = recent[0][1] if recent else None
    days_since = None
    if last_update_ts:
        try:
            days_since = (datetime.now() - datetime.fromisoformat(last_update_ts)).days
        except Exception:
            days_since = None

    # Оновлюємо базу
    if metric == "weight":
        await set_user_weight(new_value)
    else:
        await set_user_height(new_value)

    # Аналіз змін
    delta = None
    if previous_value is not None:
        try:
            delta = new_value - float(previous_value)
        except Exception:
            delta = None

    unit = "кг" if metric == "weight" else "см"
    sign = "+" if delta and delta > 0 else "" if delta is not None else ""

    text_lines = [f"✅ { 'Вага' if metric == 'weight' else 'Ріст' } оновлено: {new_value:.1f} {unit}"]
    if delta is not None:
        text_lines.append(f"🧮 Зміна: {sign}{delta:.1f} {unit}")
    if days_since is not None:
        when = "сьогодні" if days_since == 0 else ("вчора" if days_since == 1 else f"{days_since} днів тому")
        text_lines.append(f"📅 Останнє оновлення метрики було: {when}.")

    if metric == "weight":
        if delta is not None:
            if delta < 0:
                text_lines.append("💪 Гарний прогрес! Продовжуй тримати темп.")
            elif delta > 0:
                text_lines.append("📝 Помітив(ла) збільшення ваги — можеш звернути увагу на калорійність харчування.")
    else:
        text_lines.append("📌 Пам'ятай, що ріст змінюється повільно — фіксуй результат раз на кілька тижнів.")

    analysis_text = "\n".join(text_lines)

    # Логування, щоб бот «пам'ятав» історію
    await log_activity(metric, f"{new_value:.1f}", analysis_text)

    # Очистити стан та повернути меню
    user_context.pop(chat_id, None)
    await message.answer(analysis_text, reply_markup=get_main_menu())


@router.message(lambda message: message.text == "🦵 День ніг")
async def leg_day_handler(message: types.Message):
    # Переключаємось у нейтральний режим (не аналізуємо фото як їжу/тіло)
    user_context.pop(message.chat.id, None)
    user_caption.pop(message.chat.id, None)

    # Тут ми пізніше додамо логіку запису в базу даних
    from database import log_activity
    await log_activity("leg_day", "Тренування ніг")
    await message.answer("✅ День ніг зараховано! Красава. М'язи ростять, коли ти їх навантажуєш! 🔥")


@router.message(lambda message: message.text and not message.text.startswith("/") and user_context.get(message.chat.id) in ("body", "food"))
async def store_photo_caption(message: types.Message):
    """Зберігаємо текст, який користувач написав перед фото, як підпис для наступного аналізу."""
    user_caption[message.chat.id] = message.text.strip()
    await message.answer(
        "✅ Готово! Я використаю це як підпис до наступного фото. Надішли фото, щоб я міг аналізувати."
    )


@router.message(F.text == "📋 План тренувань")
async def workout_plan(message: types.Message):
    user_context[message.chat.id] = "workout_plan"
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🏠 Головне меню")]
        ],
        resize_keyboard=True,
    )
    await message.answer(
        "📋 План тренувань!\n\nОпиши свої цілі та рівень досвіду (наприклад: 'набрати м'язи, початківець').\n"
        "Напиши текстом, і я складу план.",
        reply_markup=kb,
    )


@router.message(lambda m: m.text and user_context.get(m.chat.id) == "workout_plan")
async def handle_workout_plan_input(message: types.Message):
    chat_id = message.chat.id
    goals = message.text.strip()

    # Отримуємо дані користувача
    weight, height, streak, _ = await get_user_data()
    if not weight or not height:
        await message.answer("Спочатку онови свою вагу та ріст у меню.")
        user_context.pop(chat_id, None)
        return

    prompt = (
        f"Створи персональний план тренувань для користувача: вага {weight} кг, ріст {height} см, стрік тренувань {streak} днів. "
        f"Цілі: {goals}. "
        "План має включати: дні тижня, вправи, підходи/повторення, відпочинок. "
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
        await message.answer(f"📋 **Твій план тренувань:**\n\n{plan}", parse_mode="Markdown", reply_markup=kb)
        await log_activity("workout_plan", goals, plan)
    except Exception as e:
        await message.answer(f"❌ Помилка: {e}")

    user_context.pop(chat_id, None)


@router.message(F.text == "🏋️ Аналіз тренажера")
async def equipment_analysis(message: types.Message):
    user_context[message.chat.id] = "equipment"
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🏠 Головне меню")]
        ],
        resize_keyboard=True,
    )
    await message.answer(
        "🏋️ Аналіз тренажера!\n\nНадішли фото тренажера або вправи.\n"
        "Напиши підпис, що це, і я скажу, як правильно робити та чи підходить тобі.",
        reply_markup=kb,
    )


@router.message(F.photo, lambda m: user_context.get(m.chat.id) == "equipment")
async def handle_equipment_photo(message: types.Message, bot):
    chat_id = message.chat.id
    caption = message.caption or ""

    file_item = message.photo[-1]
    file = await bot.get_file(file_item.file_id)
    file_bytes = await bot.download_file(file.file_path)

    # Отримуємо дані користувача для персоналізації
    weight, height, *_ = await get_user_data()
    user_info = f"Користувач: вага {weight} кг, ріст {height} см." if weight and height else ""

    prompt = (
        f"{user_info} Проаналізуй фото тренажера або вправи. "
        "Скажи: що за тренажер/вправа, як правильно виконувати, чи ефективний для цього користувача, альтернативи. "
        "Відповідь у Markdown."
    )

    try:
        analysis = await analyze_image([file_bytes.read()], "image", context_text=caption + "\n" + prompt)
        kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="🏠 Головне меню")]
            ],
            resize_keyboard=True,
        )
        await message.answer(analysis, parse_mode="Markdown", reply_markup=kb)
        await log_activity("equipment", caption, analysis)
    except Exception as e:
        await message.answer(f"❌ Помилка: {e}")

    user_context.pop(chat_id, None)


@router.message(F.text == "🗑️ Очистити дані")
async def clear_data_confirm(message: types.Message):
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="✅ Так, очистити"), KeyboardButton(text="❌ Скасувати")],
            [KeyboardButton(text="🏠 Головне меню")]
        ],
        resize_keyboard=True,
    )
    await message.answer(
        "🗑️ Очистити усі дані?\n\nЦе видалить всю твою вагу, ріст, історію аналізів та прогрес.\n"
        "Після цього ти зможеш почати заново.\n\n"
        "Натисни '✅ Так, очистити' щоб підтвердити, або '❌ Скасувати' щоб залишити все як є.\n"
        "Або повернися до '🏠 Головне меню'.",
        reply_markup=kb,
    )


@router.message(F.text == "✅ Так, очистити")
async def clear_data(message: types.Message):
    chat_id = message.chat.id
    # Очистити БД
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM user_profile")
        await db.execute("DELETE FROM activity_logs")
        await db.execute(
            "INSERT INTO user_profile (id, weight, height, streak) VALUES (1, NULL, NULL, 0)"
        )
        await db.commit()

    # Очистити стани
    user_context.clear()
    user_caption.clear()
    user_chat_history.clear()
    user_body_photos.clear()
    user_pending_metrics.clear()

    await message.answer(
        "✅ Усі дані очищено!\n\nТепер давайте почнемо заново. Я допоможу вам створити персональний план.\n"
        "Спочатку введіть вашу вагу в кг (наприклад: 78.5):",
        reply_markup=ReplyKeyboardRemove(),
    )
    user_context[chat_id] = "onboarding_weight"

    # Запустити таймер на 7 днів для аналізу
    from utils.scheduler import schedule_weekly_analysis
    asyncio.create_task(schedule_weekly_analysis(chat_id))


@router.message(F.text == "❌ Скасувати")
async def cancel_clear(message: types.Message):
    await message.answer("❌ Очищення скасовано. Повернувся до головного меню.", reply_markup=get_main_menu())


@router.message(F.text == "⬅️ Назад")
async def go_back(message: types.Message):
    chat_id = message.chat.id
    mode = user_context.get(chat_id)
    if mode == "onboarding_height":
        user_context[chat_id] = "onboarding_weight"
        kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="🏠 Головне меню")]
            ],
            resize_keyboard=True,
        )
        await message.answer(
            "⬅️ Повернувся до ваги.\n\nВведи свою вагу в кг (наприклад: 78.5).",
            reply_markup=kb,
        )
    elif mode == "onboarding_age":
        user_context[chat_id] = "onboarding_height"
        kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="⬅️ Назад"), KeyboardButton(text="🏠 Головне меню")]
            ],
            resize_keyboard=True,
        )
        await message.answer(
            "⬅️ Повернувся до росту.\n\nВведи свій ріст у см (наприклад: 178).",
            reply_markup=kb,
        )
    elif mode == "onboarding_sex":
        user_context[chat_id] = "onboarding_age"
        kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="⬅️ Назад"), KeyboardButton(text="🏠 Головне меню")]
            ],
            resize_keyboard=True,
        )
        await message.answer(
            "⬅️ Повернувся до віку.\n\nВведи свій вік (наприклад: 25).",
            reply_markup=kb,
        )
    elif mode == "onboarding_goals":
        user_context[chat_id] = "onboarding_sex"
        kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="Чоловік"), KeyboardButton(text="Жінка")],
                [KeyboardButton(text="⬅️ Назад"), KeyboardButton(text="🏠 Головне меню")]
            ],
            resize_keyboard=True,
        )
        await message.answer(
            "⬅️ Повернувся до статі.\n\nОбери свою стать.",
            reply_markup=kb,
        )
    else:
        await message.answer("Немає куди повертатися.", reply_markup=get_main_menu())


@router.message(F.text == "🏠 Головне меню")
async def go_home_common(message: types.Message):
    user_context.pop(message.chat.id, None)
    user_caption.pop(message.chat.id, None)
    user_pending_metrics.pop(message.chat.id, None)
    await message.answer("🏠 Повернувся до головного меню.", reply_markup=get_main_menu())


@router.message(lambda m: m.text and user_context.get(m.chat.id) == "onboarding_weight")
async def onboarding_weight(message: types.Message):
    chat_id = message.chat.id
    try:
        weight = float(message.text.strip().replace(",", "."))
        user_pending_metrics[chat_id] = {"weight": weight}
        kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="⬅️ Назад"), KeyboardButton(text="🏠 Головне меню")]
            ],
            resize_keyboard=True,
        )
        await message.answer(
            "✅ Вага збережена!\n\nТепер введи свій ріст у сантиметрах (наприклад: 178).\n"
            "Просто напиши число, як 178.",
            reply_markup=kb,
        )
        user_context[chat_id] = "onboarding_height"
    except ValueError:
        kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="⬅️ Назад"), KeyboardButton(text="🏠 Головне меню")]
            ],
            resize_keyboard=True,
        )
        await message.answer(
            "❌ Невірний формат. Введи число, наприклад 78.5 для ваги.\n"
            "Спробуй ще раз!",
            reply_markup=kb,
        )


@router.message(lambda m: m.text and user_context.get(m.chat.id) == "onboarding_height")
async def onboarding_height(message: types.Message):
    chat_id = message.chat.id
    try:
        height = float(message.text.strip().replace(",", "."))
        user_pending_metrics[chat_id]["height"] = height
        kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="⬅️ Назад"), KeyboardButton(text="🏠 Головне меню")]
            ],
            resize_keyboard=True,
        )
        await message.answer(
            "✅ Ріст збережений!\n\nТепер введи свій вік (наприклад: 25).\n"
            "Просто напиши число, як 25.",
            reply_markup=kb,
        )
        user_context[chat_id] = "onboarding_age"
    except ValueError:
        kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="⬅️ Назад"), KeyboardButton(text="🏠 Головне меню")]
            ],
            resize_keyboard=True,
        )
        await message.answer(
            "❌ Невірний формат. Введи число, наприклад 178 для росту.\n"
            "Спробуй ще раз!",
            reply_markup=kb,
        )


@router.message(lambda m: m.text and user_context.get(m.chat.id) == "onboarding_age")
async def onboarding_age(message: types.Message):
    chat_id = message.chat.id
    try:
        age = int(message.text.strip())
        user_pending_metrics[chat_id]["age"] = age
        kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="Чоловік"), KeyboardButton(text="Жінка")],
                [KeyboardButton(text="⬅️ Назад"), KeyboardButton(text="🏠 Головне меню")]
            ],
            resize_keyboard=True,
        )
        await message.answer(
            "✅ Вік збережений!\n\nТепер обери свою стать.\n"
            "Натисни 'Чоловік' або 'Жінка'.",
            reply_markup=kb,
        )
        user_context[chat_id] = "onboarding_sex"
    except ValueError:
        kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="⬅️ Назад"), KeyboardButton(text="🏠 Головне меню")]
            ],
            resize_keyboard=True,
        )
        await message.answer(
            "❌ Невірний формат. Введи число, наприклад 25 для віку.\n"
            "Спробуй ще раз!",
            reply_markup=kb,
        )


@router.message(lambda m: m.text and user_context.get(m.chat.id) == "onboarding_sex")
async def onboarding_sex(message: types.Message):
    chat_id = message.chat.id
    sex = message.text.strip().lower()
    if sex in ["чоловік", "чоловік"]:
        user_pending_metrics[chat_id]["sex"] = "male"
    elif sex in ["жінка", "жінка"]:
        user_pending_metrics[chat_id]["sex"] = "female"
    else:
        kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="Чоловік"), KeyboardButton(text="Жінка")],
                [KeyboardButton(text="⬅️ Назад"), KeyboardButton(text="🏠 Головне меню")]
            ],
            resize_keyboard=True,
        )
        await message.answer(
            "❌ Обери 'Чоловік' або 'Жінка'.\n"
            "Натисни кнопку!",
            reply_markup=kb,
        )
        return

    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="⬅️ Назад"), KeyboardButton(text="🏠 Головне меню")]
        ],
        resize_keyboard=True,
    )
    await message.answer(
        "✅ Стать збережена!\n\nТепер опиши свої цілі.\n"
        "Наприклад: 'схуднути на 5 кг', 'набрати м'язи', 'покращити витривалість'.\n"
        "Напиши текстом, що хочеш досягти.",
        reply_markup=kb,
    )
    user_context[chat_id] = "onboarding_goals"


@router.message(lambda m: m.text and user_context.get(m.chat.id) == "onboarding_goals")
async def onboarding_goals(message: types.Message):
    chat_id = message.chat.id
    goals = message.text.strip()
    metrics = user_pending_metrics[chat_id]
    metrics["goals"] = goals

    # Розрахунок BMI
    weight = metrics["weight"]
    height_m = metrics["height"] / 100
    bmi = weight / (height_m ** 2)
    bmi_category = "Норма" if 18.5 <= bmi < 25 else "Надлишок" if bmi >= 25 else "Недолік"

    # Зберегти в БД
    await set_user_weight(weight)
    await set_user_height(metrics["height"])

    # Аналіз ШІ
    prompt = (
        f"Користувач: {metrics['sex']}, {metrics['age']} років, вага {weight} кг, ріст {metrics['height']} см, BMI {bmi:.1f} ({bmi_category}). "
        f"Цілі: {goals}. "
        "Дай короткий аналіз та мотивацію. Потім інструкції для тижневого трекінгу."
    )
    try:
        analysis = await chat(prompt, history=None)
        kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="⬅️ Назад"), KeyboardButton(text="🏠 Головне меню")]
            ],
            resize_keyboard=True,
        )
        await message.answer(
            f"📊 **Твій профіль готовий!**\n\n{analysis}\n\n"
            "🔄 Протягом 7 днів збирай дані:\n"
            "- Фото їжі з підписом 'їжа'\n"
            "- Повідомлення про тренування\n"
            "- Фото тіла з підписом 'тіло'\n\n"
            "Після тижня отримаєш персональний план!\n\n"
            "Натисни '🏠 Головне меню' щоб почати.",
            parse_mode="Markdown",
            reply_markup=kb,
        )
    except Exception as e:
        await message.answer(f"❌ Помилка: {e}")

    user_context.pop(chat_id, None)
    user_pending_metrics.pop(chat_id, None)
    await message.answer("Почни збирати дані!", reply_markup=get_main_menu())

    # Запустити таймер
    from utils.scheduler import schedule_weekly_analysis
    asyncio.create_task(schedule_weekly_analysis(chat_id))


# Режим трекінгу (автоматичний після onboarding)
@router.message(lambda m: m.text and not m.text.startswith("/") and user_context.get(m.chat.id) is None)
async def track_workout(message: types.Message):
    text = message.text.lower()
    if any(word in text for word in ["тренування", "workout", "вправа", "exercise"]):
        await log_activity("workout", message.text, "")
        await message.answer("✅ Тренування записано! Молодець, продовжуй!")


@router.message(F.photo, lambda m: user_context.get(m.chat.id) is None)
async def track_food_body(message: types.Message, bot):
    caption = message.caption or ""
    if any(word in caption.lower() for word in ["їжа", "food", "їсти", "eat"]):
        # Аналіз їжі
        file_item = message.photo[-1]
        file = await bot.get_file(file_item.file_id)
        file_bytes = await bot.download_file(file.file_path)
        try:
            analysis = await analyze_image([file_bytes.read()], "food", context_text=caption)
            await message.answer(f"🍽️ **Аналіз їжі:**\n\n{analysis}", parse_mode="Markdown")
            await log_activity("food", caption, analysis)
        except Exception as e:
            await message.answer(f"❌ Помилка аналізу їжі: {e}")
    elif any(word in caption.lower() for word in ["тіло", "body", "форма", "progress"]):
        # Аналіз тіла
        file_item = message.photo[-1]
        file = await bot.get_file(file_item.file_id)
        file_bytes = await bot.download_file(file.file_path)
        try:
            analysis = await analyze_image([file_bytes.read()], "body", context_text=caption)
            await message.answer(f"🏋️ **Аналіз тіла:**\n\n{analysis}", parse_mode="Markdown")
            await log_activity("body", caption, analysis)
        except Exception as e:
            await message.answer(f"❌ Помилка аналізу тіла: {e}")
    else:
        await message.answer(
            "🤔 Не зрозумів, що це фото.\n\n"
            "Якщо це їжа — додай підпис 'їжа'.\n"
            "Якщо тіло — 'тіло'.\n"
            "Інакше надішли текстом про тренування."
        )


@router.message(lambda m: m.text and "вільні" in m.text.lower() or "час" in m.text.lower())
async def handle_schedule_input(message: types.Message):
    schedule_info = message.text
    # Зберегти або проаналізувати
    prompt = f"Користувач повідомив про графік: {schedule_info}. Покращити план тренувань."
    try:
        refined_plan = await chat(prompt, history=None)
        await message.answer(f"📅 **Оновлений план:**\n\n{refined_plan}", parse_mode="Markdown")
    except Exception as e:
        await message.answer(f"❌ Помилка: {e}")