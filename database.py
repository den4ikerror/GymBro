# database.py
import aiosqlite
from datetime import datetime

DB_NAME = "fitness_bot.db"

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        # Таблиця користувача
        await db.execute('''
            CREATE TABLE IF NOT EXISTS user_profile (
                id INTEGER PRIMARY KEY,
                chat_id INTEGER UNIQUE,
                weight REAL,
                height REAL,
                last_photo_date TEXT,
                last_greeting_date TEXT,
                streak INTEGER DEFAULT 0,
                last_workout_date TEXT,
                mode TEXT DEFAULT 'cut'
            )
        ''')
        
        # Таблиця логів (їжа та тренування)
        await db.execute('''
            CREATE TABLE IF NOT EXISTS activity_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT,
                timestamp TEXT,
                description TEXT,
                result TEXT
            )
        ''')

        # Таблиця повної історії діалогів
        await db.execute('''
            CREATE TABLE IF NOT EXISTS chat_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                user_message TEXT,
                bot_response TEXT,
                analysis_type TEXT,
                context TEXT
            )
        ''')
        
        # Перевірка та додавання нових колонок
        async with db.execute("PRAGMA table_info(user_profile)") as cursor:
            columns = [row[1] for row in await cursor.fetchall()]
        if "chat_id" not in columns:
            await db.execute("ALTER TABLE user_profile ADD COLUMN chat_id INTEGER")
            
        async with db.execute("PRAGMA table_info(activity_logs)") as cursor:
            columns = [row[1] for row in await cursor.fetchall()]
        if "result" not in columns:
            await db.execute("ALTER TABLE activity_logs ADD COLUMN result TEXT")
        
        async with db.execute("PRAGMA table_info(user_profile)") as cursor:
            columns = [row[1] for row in await cursor.fetchall()]
        if "last_greeting_date" not in columns:
            await db.execute("ALTER TABLE user_profile ADD COLUMN last_greeting_date TEXT")
        
        async with db.execute("PRAGMA table_info(user_profile)") as cursor:
            columns = [row[1] for row in await cursor.fetchall()]
        if "mode" not in columns:
            await db.execute("ALTER TABLE user_profile ADD COLUMN mode TEXT DEFAULT 'cut'")
        
        # Ініціалізація початкових даних
        async with db.execute("SELECT COUNT(*) FROM user_profile") as cursor:
            count = await cursor.fetchone()
            if count[0] == 0:
                await db.execute(
                    "INSERT INTO user_profile (id, weight, height, streak) VALUES (?, ?, ?, ?)",
                    (1, 78.8, 178.0, 0)
                )
        
        await db.commit()

async def log_activity(activity_type: str, description: str = "", result: str | None = None):
    async with aiosqlite.connect(DB_NAME) as db:
        now = datetime.now().isoformat()
        await db.execute(
            "INSERT INTO activity_logs (type, timestamp, description, result) VALUES (?, ?, ?, ?)",
            (activity_type, now, description, result),
        )
        if activity_type == 'leg_day':
            await db.execute(
                "UPDATE user_profile SET last_workout_date = ?, streak = streak + 1 WHERE id = 1",
                (now,)
            )
        elif activity_type in ('body', 'food'):
            # Оновлюємо дату останнього фото
            await db.execute(
                "UPDATE user_profile SET last_photo_date = ? WHERE id = 1",
                (now,)
            )
        await db.commit()

async def get_user_data():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT weight, height, streak, last_photo_date FROM user_profile WHERE id = 1") as cursor:
            return await cursor.fetchone()

async def get_recent_activities(limit: int = 5, activity_type: str | None = None):
    async with aiosqlite.connect(DB_NAME) as db:
        if activity_type:
            query = "SELECT type, timestamp, description, result FROM activity_logs WHERE type = ? ORDER BY timestamp DESC LIMIT ?"
            params = (activity_type, limit)
        else:
            query = "SELECT type, timestamp, description, result FROM activity_logs ORDER BY timestamp DESC LIMIT ?"
            params = (limit,)

        async with db.execute(query, params) as cursor:
            return await cursor.fetchall()

async def set_user_weight(weight: float):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE user_profile SET weight = ? WHERE id = 1", (weight,))
        await db.commit()


async def set_user_height(height: float):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE user_profile SET height = ? WHERE id = 1", (height,))
        await db.commit()


async def save_chat_history(user_message: str, bot_response: str, analysis_type: str = "chat", context: str = ""):
    """Зберігає весь чат + відповіді до БД"""
    async with aiosqlite.connect(DB_NAME) as db:
        now = datetime.now().isoformat()
        await db.execute(
            "INSERT INTO chat_history (timestamp, user_message, bot_response, analysis_type, context) VALUES (?, ?, ?, ?, ?)",
            (now, user_message, bot_response, analysis_type, context),
        )
        await db.commit()


async def get_chat_history(limit: int = 20, analysis_type: str | None = None):
    """Витягує історію діалогів для контексту"""
    async with aiosqlite.connect(DB_NAME) as db:
        if analysis_type:
            query = "SELECT user_message, bot_response, timestamp FROM chat_history WHERE analysis_type = ? ORDER BY timestamp DESC LIMIT ?"
            params = (analysis_type, limit)
        else:
            query = "SELECT user_message, bot_response, timestamp FROM chat_history ORDER BY timestamp DESC LIMIT ?"
            params = (limit,)

        async with db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            return list(reversed(rows))  # Повернути у хронологічному порядку


async def get_last_greeting_date():
    """Перевіряє коли був останній привіт"""
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT last_greeting_date FROM user_profile WHERE id = 1") as cursor:
            result = await cursor.fetchone()
            return result[0] if result else None


async def set_last_greeting_date(date_str: str):
    """Оновлює дату останнього привіту"""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE user_profile SET last_greeting_date = ? WHERE id = 1", (date_str,))
        await db.commit()


async def get_user_mode():
    """Отримує поточний режим користувача (bulk/cut)"""
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT mode FROM user_profile WHERE id = 1") as cursor:
            result = await cursor.fetchone()
            return result[0] if result else "cut"


async def set_user_mode(mode: str):
    """Встановлює режим користувача (bulk/cut)"""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE user_profile SET mode = ? WHERE id = 1", (mode,))
        await db.commit()
        await db.commit()


async def save_chat_id(chat_id: int):
    """Зберігає chat_id користувача для розсилки"""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE user_profile SET chat_id = ? WHERE id = 1", (chat_id,))
        await db.commit()


async def get_all_chat_ids():
    """Отримує всі chat_ids для розсилки (наприклад, для щоденного привіту)"""
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT chat_id FROM user_profile WHERE chat_id IS NOT NULL") as cursor:
            rows = await cursor.fetchall()
            return [row[0] for row in rows]
