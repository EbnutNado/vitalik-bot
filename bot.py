"""
🤖 Telegram бот "Виталик Штрафующий" v4.0
Полная версия с таблетками Нагирт, мини-играми и списком игроков
Оптимизировано для BotHost
"""

import asyncio
import logging
import random
import os
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message, CallbackQuery, ReplyKeyboardMarkup, 
    KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
import aiosqlite

# ==================== КОНФИГУРАЦИЯ ====================
BOT_TOKEN = os.getenv("8451168327:AAGQffadqqBg3pZNQnjctVxH-dUgXsovTr4", "8451168327:AAGQffadqqBg3pZNQnjctVxH-dUgXsovTr4")  # Берем из переменных окружения
ADMIN_ID = int(os.getenv("5775839902", 5775839902))  # Ваш Telegram ID

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# ==================== БАЗА ДАННЫХ ====================
DB_PATH = "data/vitalik_bot.db"  # BotHost сохраняет в папку data

async def init_db():
    """Инициализация базы данных"""
    # Создаем папку data если нет
    os.makedirs("data", exist_ok=True)
    
    async with aiosqlite.connect(DB_PATH) as db:
        # Игроки
        await db.execute('''
            CREATE TABLE IF NOT EXISTS players (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                balance INTEGER DEFAULT 1000,
                total_earned INTEGER DEFAULT 0,
                total_fines INTEGER DEFAULT 0,
                last_salary TIMESTAMP,
                registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Транзакции
        await db.execute('''
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                type TEXT,
                amount INTEGER,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES players (user_id)
            )
        ''')
        
        # Активные бусты
        await db.execute('''
            CREATE TABLE IF NOT EXISTS boosts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                boost_type TEXT,
                value REAL,
                expires_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES players (user_id)
            )
        ''')
        
        # Покупки
        await db.execute('''
            CREATE TABLE IF NOT EXISTS purchases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                item_name TEXT,
                price INTEGER,
                boost_value REAL,
                expires_at TIMESTAMP,
                purchased_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES players (user_id)
            )
        ''')
        
        # Штрафы
        await db.execute('''
            CREATE TABLE IF NOT EXISTS fines_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount INTEGER,
                reason TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES players (user_id)
            )
        ''')
        
        # Мини-игра "Асфальт"
        await db.execute('''
            CREATE TABLE IF NOT EXISTS asphalt_game (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                total_meters INTEGER DEFAULT 0,
                total_games INTEGER DEFAULT 0,
                best_score INTEGER DEFAULT 0,
                total_earned INTEGER DEFAULT 0,
                last_played TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES players (user_id)
            )
        ''')
        
        await db.execute('''
            CREATE TABLE IF NOT EXISTS asphalt_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                current_meters INTEGER DEFAULT 0,
                quality INTEGER DEFAULT 100,
                risk_level INTEGER DEFAULT 1,
                started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_active INTEGER DEFAULT 1,
                FOREIGN KEY (user_id) REFERENCES players (user_id)
            )
        ''')
        
        # Таблетки Нагирт
        await db.execute('''
            CREATE TABLE IF NOT EXISTS nagirt_pills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                pill_type TEXT,
                effect_strength REAL DEFAULT 1.0,
                expires_at TIMESTAMP,
                side_effects TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES players (user_id)
            )
        ''')
        
        await db.execute('''
            CREATE TABLE IF NOT EXISTS nagirt_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                pill_type TEXT,
                effect TEXT,
                side_effect TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES players (user_id)
            )
        ''')
        
        await db.execute('''
            CREATE TABLE IF NOT EXISTS nagirt_tolerance (
                user_id INTEGER PRIMARY KEY,
                tolerance_level REAL DEFAULT 1.0,
                last_used TIMESTAMP,
                total_used INTEGER DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES players (user_id)
            )
        ''')
        
        # Индексы
        await db.execute("CREATE INDEX IF NOT EXISTS idx_boosts_user_expires ON boosts(user_id, expires_at)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_transactions_user ON transactions(user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_nagirt_expires ON nagirt_pills(user_id, expires_at)")
        
        await db.commit()
    
    logger.info("✅ База данных инициализирована")

# ==================== МАШИНЫ СОСТОЯНИЙ ====================
class Form(StatesGroup):
    transfer_amount = State()
    broadcast_message = State()
    admin_fine = State()
    asphalt_risk = State()
    asphalt_action = State()

# ==================== ТОВАРЫ МАГАЗИНА ====================
SHOP_ITEMS = [
    {
        "id": "bonus_coin",
        "name": "🪙 Бонусная монета",
        "price": 300,
        "description": "+15-25% к получке на 12 часов",
        "boost_min": 0.15,
        "boost_max": 0.25,
        "duration_hours": 12,
        "emoji": "🪙",
        "category": "boost"
    },
    {
        "id": "day_off",
        "name": "🎉 Выходной",
        "price": 500,
        "description": "Защита от штрафов +20-30% к получке на 24 часа",
        "boost_min": 0.20,
        "boost_max": 0.30,
        "duration_hours": 24,
        "emoji": "🎉",
        "category": "boost"
    },
    {
        "id": "premium_boost",
        "name": "🚀 Премиум Буст",
        "price": 1000,
        "description": "+40-60% к получке на 3 дня! Максимальный доход!",
        "boost_min": 0.40,
        "boost_max": 0.60,
        "duration_hours": 72,
        "emoji": "🚀",
        "category": "boost"
    },
    {
        "id": "nagirt_light",
        "name": "💊 Нагирт Лайт",
        "price": 200,
        "description": "+50% к укладке асфальта на 1 час. Мало побочек.",
        "effect": "asphalt_boost",
        "effect_value": 0.5,
        "duration_hours": 1,
        "side_effect_chance": 15,
        "risk_level": 1,
        "emoji": "💊",
        "category": "pill"
    },
    {
        "id": "nagirt_pro",
        "name": "💊💊 Нагирт Про",
        "price": 500,
        "description": "+100% ко всему на 2 часа. Средние побочки.",
        "effect": "all_boost",
        "effect_value": 1.0,
        "duration_hours": 2,
        "side_effect_chance": 35,
        "risk_level": 2,
        "emoji": "💊💊",
        "category": "pill"
    },
    {
        "id": "nagirt_extreme",
        "name": "💊💊💊 Нагирт Экстрим",
        "price": 1000,
        "description": "+200% на 3 часа! Высокий риск побочек и штрафов!",
        "effect": "mega_boost",
        "effect_value": 2.0,
        "duration_hours": 3,
        "side_effect_chance": 60,
        "risk_level": 3,
        "emoji": "💊💊💊",
        "category": "pill"
    },
    {
        "id": "antidote",
        "name": "💉 Антидот",
        "price": 300,
        "description": "Снимает побочки от Нагирта. Понижает толерантность.",
        "effect": "antidote",
        "duration_hours": 0,
        "emoji": "💉",
        "category": "pill"
    },
    {
        "id": "detox",
        "name": "🏥 Детокс",
        "price": 800,
        "description": "Полная очистка организма. Сбрасывает толерантность к нулю.",
        "effect": "detox",
        "duration_hours": 0,
        "emoji": "🏥",
        "category": "pill"
    }
]

# ==================== ФУНКЦИИ БАЗЫ ДАННЫХ ====================
async def register_user(user_id: int, username: str, full_name: str):
    """Регистрация нового пользователя"""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """INSERT OR IGNORE INTO players 
                   (user_id, username, full_name, balance) 
                   VALUES (?, ?, ?, ?)""",
                (user_id, username, full_name, 1000)
            )
            
            await db.execute(
                """INSERT INTO transactions 
                   (user_id, type, amount, description)
                   VALUES (?, ?, ?, ?)""",
                (user_id, "start_bonus", 1000, "🎁 Стартовый бонус")
            )
            
            await db.commit()
            logger.info(f"📝 Зарегистрирован: {full_name} ({user_id})")
            return True
    except Exception as e:
        logger.error(f"❌ Ошибка регистрации: {e}")
        return False

async def get_user_data(user_id: int) -> Optional[Dict[str, Any]]:
    """Получение данных пользователя"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """SELECT balance, total_earned, total_fines, last_salary 
               FROM players WHERE user_id = ?""",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            
    if row:
        return {
            "balance": row[0],
            "total_earned": row[1] or 0,
            "total_fines": row[2] or 0,
            "last_salary": row[3]
        }
    return None

async def update_balance(user_id: int, amount: int, trans_type: str, description: str = ""):
    """Обновление баланса с записью транзакции"""
    async with aiosqlite.connect(DB_PATH) as db:
        # Обновляем баланс
        await db.execute(
            "UPDATE players SET balance = balance + ? WHERE user_id = ?",
            (amount, user_id)
        )
        
        # Обновляем статистику
        if trans_type == "salary":
            await db.execute(
                "UPDATE players SET total_earned = total_earned + ? WHERE user_id = ?",
                (amount, user_id)
            )
        elif trans_type == "fine":
            await db.execute(
                "UPDATE players SET total_fines = total_fines + ? WHERE user_id = ?",
                (-amount, user_id)
            )
        
        # Записываем транзакцию
        await db.execute(
            """INSERT INTO transactions 
               (user_id, type, amount, description)
               VALUES (?, ?, ?, ?)""",
            (user_id, trans_type, amount, description)
        )
        
        await db.commit()

async def get_active_multiplier(user_id: int) -> float:
    """Получение текущего множителя получки"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """SELECT SUM(value) FROM boosts 
               WHERE user_id = ? AND expires_at > ?""",
            (user_id, datetime.now().isoformat())
        ) as cursor:
            result = await cursor.fetchone()
            
    total_boost = result[0] if result and result[0] else 0.0
    return 1.0 + total_boost

async def add_boost(user_id: int, boost_type: str, value: float, hours: int):
    """Добавление временного буста"""
    expires_at = datetime.now() + timedelta(hours=hours)
    
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO boosts (user_id, boost_type, value, expires_at)
               VALUES (?, ?, ?, ?)""",
            (user_id, boost_type, value, expires_at.isoformat())
        )
        await db.commit()

async def has_fine_protection(user_id: int) -> bool:
    """Проверка защиты от штрафов"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """SELECT 1 FROM boosts 
               WHERE user_id = ? AND expires_at > ?
               AND boost_type = 'day_off'""",
            (user_id, datetime.now().isoformat())
        ) as cursor:
            result = await cursor.fetchone()
            
    return result is not None

async def get_all_users() -> List[Dict[str, Any]]:
    """Получение списка всех пользователей"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """SELECT user_id, full_name, balance, total_earned, total_fines 
               FROM players ORDER BY full_name"""
        ) as cursor:
            rows = await cursor.fetchall()
            
    return [
        {
            "id": row[0], 
            "name": row[1], 
            "balance": row[2],
            "total_earned": row[3],
            "total_fines": row[4]
        }
        for row in rows
    ]

async def record_fine(user_id: int, amount: int, reason: str = ""):
    """Запись штрафа в историю"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO fines_history (user_id, amount, reason)
               VALUES (?, ?, ?)""",
            (user_id, amount, reason)
        )
        await db.commit()

async def cleanup_expired_boosts():
    """Очистка истекших бустов и таблеток"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM boosts WHERE expires_at <= ?",
            (datetime.now().isoformat(),)
        )
        await db.execute(
            "DELETE FROM nagirt_pills WHERE expires_at <= ?",
            (datetime.now().isoformat(),)
        )
        await db.commit()

# ==================== ФУНКЦИИ ТАБЛЕТОК НАГИРТ ====================
async def get_active_pill_effects(user_id: int) -> Dict[str, Any]:
    """Получение активных эффектов от таблеток"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """SELECT pill_type, effect_strength, side_effects 
               FROM nagirt_pills 
               WHERE user_id = ? AND expires_at > ?""",
            (user_id, datetime.now().isoformat())
        ) as cursor:
            active_pills = await cursor.fetchall()
    
    effects = {
        "asphalt_boost": 0.0,
        "salary_boost": 0.0,
        "fine_protection": 0.0,
        "side_effects": [],
        "total_boost": 0.0,
        "has_active_pills": len(active_pills) > 0
    }
    
    for pill_type, strength, side_effects in active_pills:
        pill = next((p for p in SHOP_ITEMS if p["id"] == pill_type), None)
        if not pill:
            continue
        
        if pill["effect"] == "asphalt_boost":
            effects["asphalt_boost"] += strength
        elif pill["effect"] == "all_boost":
            effects["asphalt_boost"] += strength
            effects["salary_boost"] += strength
        elif pill["effect"] == "mega_boost":
            effects["asphalt_boost"] += strength
            effects["salary_boost"] += strength
            effects["fine_protection"] += 0.5
        
        if side_effects:
            effects["side_effects"].append(side_effects)
    
    effects["total_boost"] = effects["asphalt_boost"] + effects["salary_boost"]
    return effects

async def use_nagirt_pill(user_id: int, pill_type: str) -> Dict[str, Any]:
    """Использование таблетки Нагирт"""
    pill = next((p for p in SHOP_ITEMS if p["id"] == pill_type), None)
    if not pill:
        return {"success": False, "error": "Таблетка не найдена"}
    
    user_data = await get_user_data(user_id)
    if not user_data or user_data["balance"] < pill["price"]:
        return {"success": False, "error": f"Не хватает {pill['price'] - user_data['balance']}₽"}
    
    # Толерантность
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT tolerance_level FROM nagirt_tolerance WHERE user_id = ?",
            (user_id,)
        ) as cursor:
            tolerance_result = await cursor.fetchone()
    
    tolerance = tolerance_result[0] if tolerance_result else 1.0
    real_effect = pill["effect_value"] / tolerance
    real_side_effect_chance = pill.get("side_effect_chance", 0) * tolerance
    
    # Побочки
    side_effects = []
    fine_amount = 0
    
    if random.randint(1, 100) <= real_side_effect_chance:
        side_effect_type = random.choice(["fine", "health", "balance", "vitalik_rage"])
        
        if side_effect_type == "fine":
            fine_amount = random.randint(100, 500) * pill.get("risk_level", 1)
            side_effects.append(f"⚡ Штраф: -{fine_amount}₽")
        elif side_effect_type == "balance":
            balance_loss = random.randint(50, 200)
            side_effects.append(f"🌀 Головокружение: -{balance_loss}₽")
            fine_amount = balance_loss
        elif side_effect_type == "vitalik_rage":
            side_effects.append("😠 Виталик в ярости! Следующий штраф x2")
    
    # Оплата
    await update_balance(user_id, -pill["price"], "pill_purchase", f"💊 {pill['name']}")
    
    if fine_amount > 0:
        await update_balance(user_id, -fine_amount, "pill_side_effect", "💊 Побочка")
    
    # Добавляем эффект
    expires_at = datetime.now() + timedelta(hours=pill["duration_hours"])
    
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO nagirt_pills 
               (user_id, pill_type, effect_strength, expires_at, side_effects)
               VALUES (?, ?, ?, ?, ?)""",
            (user_id, pill_type, real_effect, expires_at.isoformat(), 
             "; ".join(side_effects) if side_effects else "")
        )
        
        # Толерантность
        tolerance_increase = 0.1 * pill.get("risk_level", 1)
        new_tolerance = min(3.0, tolerance + tolerance_increase)
        
        await db.execute(
            """INSERT OR REPLACE INTO nagirt_tolerance 
               (user_id, tolerance_level, last_used, total_used)
               VALUES (?, ?, ?, COALESCE((SELECT total_used FROM nagirt_tolerance WHERE user_id = ?), 0) + 1)""",
            (user_id, new_tolerance, datetime.now().isoformat(), user_id)
        )
        
        await db.commit()
    
    new_balance = (await get_user_data(user_id))["balance"]
    
    # Шутки Виталика
    pill_jokes = [
        "О, таблетки! Теперь работать будешь как трактор!",
        "Нагирт? Серьезно? Ладно, работай!",
        "С такими таблетками и я бы поработал!",
        "Только не переборщи!",
        "Работай! Таблетки сами себя не выложат!"
    ]
    
    return {
        "success": True,
        "pill_name": pill["name"],
        "effect": real_effect,
        "duration": pill["duration_hours"],
        "side_effects": side_effects,
        "fine": fine
