# database.py
import aiosqlite
from datetime import datetime

DB_NAME = "fitness_bot.db"

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        # Таблиця користувача (для тебе)
        await db.execute('''
            CREATE TABLE IF NOT EXISTS user_profile (
                id INTEGER PRIMARY KEY,
                weight REAL,
                height REAL,
                last_photo_date TEXT,
                streak INTEGER DEFAULT 0,
                last_workout_date TEXT
            )
        ''')
        
        # Таблиця логів (їжа та тренування)
        await db.execute('''
            CREATE TABLE IF NOT EXISTS activity_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT, -- 'food', 'body', 'leg_day'
                timestamp TEXT,
                description TEXT
            )
        ''')

        # Додаємо поле для збереження тексту аналізу (щоб можна було переглядати історію)
        async with db.execute("PRAGMA table_info(activity_logs)") as cursor:
            columns = [row[1] for row in await cursor.fetchall()]
        if "result" not in columns:
            await db.execute("ALTER TABLE activity_logs ADD COLUMN result TEXT")
        
        # Ініціалізація початкових даних, якщо порожньо
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
