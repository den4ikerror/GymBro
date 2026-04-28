# gemini_service.py

import google.ai.generativelanguage as gal
from google.api_core.client_options import ClientOptions
from datetime import date
import asyncio
import time

from config import (
    GEMINI_API_KEY,
    GEMINI_MODEL_NAME,
    PROMPT_NUTRITION,
    PROMPT_BODY_ANALYSIS,
    PROMPT_IMAGE_ANALYSIS,
    PROMPT_CHAT,
    USER_BIRTHDATE,
    USER_SEX,
)

# Налаштування API (створюємо клієнта у контексті виклику, щоб уникнути проблем з різними циклами подій)

async def analyze_image(image_bytes: list[bytes], analysis_type: str, context_text: str | None = None) -> str:
    """Універсальна функція для аналізу фото.

    analysis_type: 'food' або 'body'
    context_text: додатковий текст, який користувач додав до фото (підпис)."""

    if analysis_type == 'food':
        prompt = PROMPT_NUTRITION
    elif analysis_type == 'body':
        prompt = PROMPT_BODY_ANALYSIS
    else:
        prompt = PROMPT_IMAGE_ANALYSIS

    # Додатковий контекст: інформація про користувача (вік/стать) та, за потреби, пояснення від користувача.
    try:
        today = date.today()
        age = today.year - USER_BIRTHDATE.year - (
            (today.month, today.day) < (USER_BIRTHDATE.month, USER_BIRTHDATE.day)
        )
        user_info = f"Користувач: {USER_SEX}, {age} років (нар. {USER_BIRTHDATE.isoformat()})."
    except Exception:
        user_info = ""

    if user_info:
        prompt += f"\n\n{user_info}"
    if context_text:
        prompt += f"\n\nДодатковий контекст: {context_text.strip()}"

    contents = [
        gal.Content(parts=[gal.Part(text=prompt)]),
    ]

    for img in image_bytes:
        contents.append(
            gal.Content(
                parts=[
                    gal.Part(
                        inline_data=gal.Blob(mime_type="image/jpeg", data=img)
                    )
                ]
            )
        )

    try:
        # Генерація відповіді
        async with gal.GenerativeServiceAsyncClient(
            client_options=ClientOptions(api_key=GEMINI_API_KEY)
        ) as client:
            response = await client.generate_content(
                model=GEMINI_MODEL_NAME,
                contents=contents,
            )
        if not response.candidates:
            return "Gemini не повернув жодних результатів."

        first = response.candidates[0]
        if not first.content or not first.content.parts:
            return "Отримано некоректну відповідь від Gemini."

        text_parts = [p.text for p in first.content.parts if getattr(p, "text", None)]
        return "\n".join(text_parts).strip() or "Gemini повернув пусту відповідь."
    except Exception as e:
        return f"Помилка при зверненні до Gemini: {e}"


async def chat(message_text: str, history: list[str] | None = None) -> str:
    """Чат-інтерфейс: відповідає на запитання користувача на основі історії діалогу."""

    prompt = PROMPT_CHAT
    if history:
        prompt += "\n\nІсторія діалогу:\n" + "\n".join(history)

    prompt += f"\n\nКористувач: {message_text}\nАсистент:"

    try:
        async with gal.GenerativeServiceAsyncClient(
            client_options=ClientOptions(api_key=GEMINI_API_KEY)
        ) as client:
            response = await client.generate_content(
                model=GEMINI_MODEL_NAME,
                contents=[gal.Content(parts=[gal.Part(text=prompt)])],
            )
        if not response.candidates:
            return "Gemini не повернув жодних результатів."

        first = response.candidates[0]
        if not first.content or not first.content.parts:
            return "Отримано некоректну відповідь від Gemini."

        text_parts = [p.text for p in first.content.parts if getattr(p, "text", None)]
        return "\n".join(text_parts).strip() or "Gemini повернув пусту відповідь."
    except Exception as e:
        return f"Помилка при зверненні до Gemini: {e}"


async def show_progress(msg, estimated_seconds: int = 10):
    """Показує прогресбар у відсотках під час аналізу"""
    start = time.monotonic()
    try:
        while True:
            elapsed = time.monotonic() - start
            percent = min(99, int((elapsed / estimated_seconds) * 100))
            bar_fill = int(percent / 5)
            bar = "█" * bar_fill + "░" * (20 - bar_fill)
            remaining = max(0, int(estimated_seconds - elapsed))
            
            text = (
                f"🔎 Аналізую...\n\n"
                f"[{bar}] {percent}%\n"
                f"⏳ ~{remaining}s"
            )
            try:
                await msg.edit_text(text)
            except:
                pass
            await asyncio.sleep(0.5)
    except asyncio.CancelledError:
        return


async def analyze_image_with_progress(image_bytes: list[bytes], analysis_type: str, msg, context_text: str | None = None) -> str:
    """Аналізує фото з показом прогресбара"""
    estimated_secs = max(5, len(image_bytes) * 4)
    progress_task = asyncio.create_task(show_progress(msg, estimated_secs))
    
    try:
        result = await analyze_image(image_bytes, analysis_type, context_text)
        await msg.edit_text(f"✅ Аналіз:\n\n{result}")
        return result
    except Exception as e:
        await msg.edit_text(f"❌ Помилка: {e}")
        raise
    finally:
        progress_task.cancel()
        try:
            await progress_task
        except:
            pass
