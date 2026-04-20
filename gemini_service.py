# gemini_service.py

import google.ai.generativelanguage as gal
from google.api_core.client_options import ClientOptions
from datetime import date

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
