"""
Telegram бот "Виталик Штрафующий" - ПОЛНАЯ ВЕРСИЯ С СИСТЕМОЙ ЧЕКОВ И ИСПРАВЛЕННЫМ НАГИРТОМ
"""

import asyncio
import logging
import random
import string
import json
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
import aiosqlite

# ==================== КОНФИГУРАЦИЯ ====================
BOT_TOKEN = "8451168327:AAGQffadqqBg3pZNQnjctVxH-dUgXsovTr4"
ADMIN_ID = 5775839902  # Твой ID

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# ==================== НАСТРОЙКИ ЭКОНОМИКИ ====================
ECONOMY_SETTINGS = {
    "start_balance": 5000,
    "salary_min": 800,
    "salary_max": 2500,
    "salary_interval": 300,
    "fine_chance": 0.25,
    "random_fine_min": 200,
    "random_fine_max": 1000,
    "asphalt_earnings": 50,
    "asphalt_fine_min": 100,
    "asphalt_fine_max": 400,
    "roulette_min_bet": 100,
    "roulette_max_bet": 5000,
    "roulette_win_chance": 0.42,
    "min_transfer": 100,
    "random_fine_interval_min": 1200,
    "random_fine_interval_max": 1800,
}

# ==================== БАЗА ДАННЫХ ====================
DB_NAME = "vitalik_bot_final.db"

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS players (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                balance INTEGER DEFAULT 5000,
                total_earned INTEGER DEFAULT 0,
                total_fines INTEGER DEFAULT 0,
                salary_count INTEGER DEFAULT 0,
                last_salary TIMESTAMP,
                last_penalty TIMESTAMP,
                last_asphalt TIMESTAMP,
                penalty_immunity_until TIMESTAMP,
                asphalt_meters INTEGER DEFAULT 0,
                asphalt_earned INTEGER DEFAULT 0,
                registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        await db.execute('''
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                type TEXT,
                amount INTEGER,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        await db.execute('''
            CREATE TABLE IF NOT EXISTS purchases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                item_name TEXT,
                price INTEGER,
                purchased_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        await db.execute('''
            CREATE TABLE IF NOT EXISTS boosts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                boost_type TEXT,
                boost_value REAL,
                expires_at TIMESTAMP
            )
        ''')
        
        await db.execute('''
            CREATE TABLE IF NOT EXISTS nagirt_pills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                pill_type TEXT,
                effect_strength REAL,
                expires_at TIMESTAMP,
                side_effects TEXT
            )
        ''')
        
        await db.execute('''
            CREATE TABLE IF NOT EXISTS nagirt_tolerance (
                user_id INTEGER PRIMARY KEY,
                tolerance REAL DEFAULT 1.0,
                last_used TIMESTAMP
            )
        ''')
        
        await db.execute('''
            CREATE TABLE IF NOT EXISTS gift_checks (
                check_id TEXT PRIMARY KEY,
                creator_id INTEGER,
                check_type TEXT,
                amount INTEGER,
                item_id TEXT,
                max_uses INTEGER DEFAULT 1,
                used_count INTEGER DEFAULT 0,
                created_at TIMESTAMP,
                expires_at TIMESTAMP,
                is_active BOOLEAN DEFAULT TRUE,
                custom_message TEXT,
                last_used TIMESTAMP,
                activations_list TEXT DEFAULT '[]'
            )
        ''')
        
        await db.execute('''
            CREATE TABLE IF NOT EXISTS check_activations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                check_id TEXT,
                user_id INTEGER,
                activated_at TIMESTAMP,
                received_amount INTEGER,
                received_item TEXT
            )
        ''')
        
        await db.commit()
        logger.info("✅ База данных инициализирована")

async def get_user(user_id: int) -> Optional[Dict[str, Any]]:
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM players WHERE user_id = ?", (user_id,))
        user = await cursor.fetchone()
        return dict(user) if user else None

async def register_user(user_id: int, username: str, full_name: str):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT 1 FROM players WHERE user_id = ?", (user_id,))
        exists = await cursor.fetchone()

        if not exists:
            await db.execute(
                "INSERT INTO players (user_id, username, full_name, balance) VALUES (?, ?, ?, ?)",
                (user_id, username, full_name, ECONOMY_SETTINGS["start_balance"])
            )
            await db.execute(
                "INSERT INTO transactions (user_id, type, amount, description) VALUES (?, 'registration', ?, 'Стартовый капитал')",
                (user_id, ECONOMY_SETTINGS["start_balance"])
            )
            await db.commit()

async def update_balance(user_id: int, amount: int, txn_type: str, description: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE players SET balance = balance + ? WHERE user_id = ?",
            (amount, user_id)
        )
        
        if txn_type == "salary":
            await db.execute(
                "UPDATE players SET total_earned = total_earned + ?, salary_count = salary_count + 1 WHERE user_id = ?",
                (amount, user_id)
            )
        elif txn_type == "penalty":
            await db.execute(
                "UPDATE players SET total_fines = total_fines + ? WHERE user_id = ?",
                (-amount, user_id)
            )
        
        await db.execute(
            "INSERT INTO transactions (user_id, type, amount, description) VALUES (?, ?, ?, ?)",
            (user_id, txn_type, amount, description)
        )
        await db.commit()

async def get_all_users() -> List[Dict[str, Any]]:
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT user_id, full_name, username, balance FROM players")
        users = await cursor.fetchall()
        return [dict(user) for user in users]

# ==================== СИСТЕМА НАГИРТА (ИСПРАВЛЕННАЯ) ====================
async def add_nagirt_pill(user_id: int, pill_type: str, effect: float, hours: int, side_effects: str = ""):
    expires_at = datetime.now() + timedelta(hours=hours)
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            '''INSERT INTO nagirt_pills (user_id, pill_type, effect_strength, expires_at, side_effects)
               VALUES (?, ?, ?, ?, ?)''',
            (user_id, pill_type, effect, expires_at.isoformat(), side_effects)
        )
        await db.commit()

async def get_active_nagirt_effects(user_id: int) -> Dict[str, Any]:
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            "SELECT pill_type, effect_strength, side_effects FROM nagirt_pills WHERE user_id = ? AND expires_at > ?",
            (user_id, datetime.now().isoformat())
        )
        rows = await cursor.fetchall()
    
    effects = {
        "salary_boost": 0.0,
        "asphalt_boost": 0.0,
        "side_effects": [],
        "has_active": len(rows) > 0,
        "risk_multiplier": 1.0
    }
    
    for row in rows:
        pill_type, strength, side_effects_json = row
        
        if pill_type == "nagirt_extreme":
            effects["salary_boost"] += strength * 1.5
            effects["asphalt_boost"] += strength * 1.2
            effects["risk_multiplier"] += 0.8
        elif pill_type == "nagirt_pro":
            effects["salary_boost"] += strength
            effects["asphalt_boost"] += strength * 0.8
            effects["risk_multiplier"] += 0.5
        elif pill_type == "nagirt_light":
            effects["asphalt_boost"] += strength
            effects["salary_boost"] += strength * 0.3
            effects["risk_multiplier"] += 0.2
        
        if side_effects_json:
            try:
                side_effects = json.loads(side_effects_json)
                if isinstance(side_effects, list):
                    effects["side_effects"].extend(side_effects)
                    effects["risk_multiplier"] += len(side_effects) * 0.1
                else:
                    effects["side_effects"].append(side_effects)
                    effects["risk_multiplier"] += 0.1
            except:
                effects["side_effects"].append(side_effects_json)
                effects["risk_multiplier"] += 0.1
    
    return effects

async def get_nagirt_tolerance(user_id: int) -> float:
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT tolerance FROM nagirt_tolerance WHERE user_id = ?", (user_id,))
        result = await cursor.fetchone()
        return result[0] if result else 1.0

async def update_nagirt_tolerance(user_id: int, increase: float = 0.1):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''
            INSERT OR REPLACE INTO nagirt_tolerance (user_id, tolerance, last_used)
            VALUES (?, COALESCE((SELECT tolerance FROM nagirt_tolerance WHERE user_id = ?), 1.0) + ?, ?)
        ''', (user_id, user_id, increase, datetime.now().isoformat()))
        await db.commit()

async def reset_nagirt_tolerance(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT OR REPLACE INTO nagirt_tolerance (user_id, tolerance, last_used) VALUES (?, 1.0, ?)",
            (user_id, datetime.now().isoformat())
        )
        await db.commit()

async def cleanup_expired():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM boosts WHERE expires_at <= ?", (datetime.now().isoformat(),))
        await db.execute("DELETE FROM nagirt_pills WHERE expires_at <= ?", (datetime.now().isoformat(),))
        await db.commit()

async def add_boost(user_id: int, boost_type: str, value: float, hours: int):
    expires_at = datetime.now() + timedelta(hours=hours)
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT INTO boosts (user_id, boost_type, boost_value, expires_at) VALUES (?, ?, ?, ?)",
            (user_id, boost_type, value, expires_at.isoformat())
        )
        await db.commit()

async def get_active_boosts(user_id: int) -> float:
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            "SELECT SUM(boost_value) FROM boosts WHERE user_id = ? AND expires_at > ?",
            (user_id, datetime.now().isoformat())
        )
        result = await cursor.fetchone()
        return result[0] if result and result[0] else 0.0

async def has_fine_protection(user_id: int) -> bool:
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            "SELECT 1 FROM players WHERE user_id = ? AND penalty_immunity_until > ?",
            (user_id, datetime.now().isoformat())
        )
        result = await cursor.fetchone()
        return result is not None

# ==================== ТОВАРЫ МАГАЗИНА ====================
SHOP_ITEMS = [
    {"id": "bonus_coin", "name": "🪙 Бонусная монета", "price": 1500, "description": "+15% к получке на 8 часов", "type": "boost", "value": 0.15, "hours": 8},
    {"id": "premium_boost", "name": "🚀 Премиум-Буст", "price": 5000, "description": "+30% к получке на 24 часа", "type": "boost", "value": 0.3, "hours": 24},
    {"id": "mega_boost", "name": "💎 Мега-Буст", "price": 15000, "description": "+50% к получке на 3 дня", "type": "boost", "value": 0.5, "hours": 72},
    {"id": "day_off", "name": "🎉 Выходной", "price": 3000, "description": "Полный иммунитет к штрафам на 12 часов", "type": "protection", "hours": 12},
    {"id": "insurance", "name": "🛡️ Страховка", "price": 4000, "description": "Страховка от одного штрафа (возмещает 80%)", "type": "insurance"},
    {"id": "nagirt_light", "name": "💊 Нагирт Лайт", "price": 2000, "description": "+40% к играм на 2 часа. Мало побочек.", "type": "pill", "effect": 0.4, "hours": 2, "side_effect_chance": 15},
    {"id": "nagirt_pro", "name": "💊💊 Нагирт Про", "price": 5000, "description": "+80% ко всему на 4 часа. Риск штрафов!", "type": "pill", "effect": 0.8, "hours": 4, "side_effect_chance": 35},
    {"id": "nagirt_extreme", "name": "💊💊💊 Нагирт Экстрим", "price": 12000, "description": "+150% на 6 часов! Высокий риск!", "type": "pill", "effect": 1.5, "hours": 6, "side_effect_chance": 60},
    {"id": "antidote", "name": "💉 Антидот", "price": 2500, "description": "Снимает побочки и сбрасывает толерантность", "type": "antidote"},
    {"id": "lottery_ticket", "name": "🎫 Лотерейный билет", "price": 1000, "description": "Шанс выиграть до 10000₽!", "type": "lottery"},
    {"id": "instant_salary", "name": "⏱️ Мгновенная получка", "price": 8000, "description": "Сразу получаешь зарплату без ожидания", "type": "instant"},
]

# ==================== МАШИНЫ СОСТОЯНИЙ ====================
class TransferStates(StatesGroup):
    choosing_recipient = State()
    entering_amount = State()

class BroadcastStates(StatesGroup):
    waiting_for_message = State()

class RouletteStates(StatesGroup):
    waiting_for_bet = State()

class AdminFineStates(StatesGroup):
    waiting_for_user_id = State()
    waiting_for_amount = State()

class AdminBonusStates(StatesGroup):
    waiting_for_user_id = State()
    waiting_for_amount = State()

class CheckStates(StatesGroup):
    waiting_for_check_amount = State()
    waiting_for_check_uses = State()
    waiting_for_check_hours = State()
    waiting_for_check_message = State()

# ==================== ФУНКЦИИ ДЛЯ ФОРМАТИРОВАНИЯ ====================
def format_money(amount: int) -> str:
    try:
        if amount is None:
            return "0₽"
        amount = int(amount)
        if amount >= 1000:
            return f"{amount:,}₽".replace(",", " ")
        return f"{amount}₽"
    except:
        return "0₽"

def format_time(seconds: int) -> str:
    minutes = seconds // 60
    secs = seconds % 60
    return f"{minutes}:{secs:02d}"

# ==================== КЛАВИАТУРЫ ====================
def get_main_keyboard(user_id: int = None) -> ReplyKeyboardMarkup:
    keyboard = [
        [KeyboardButton(text="💰 Получка"), KeyboardButton(text="🛒 Магазин")],
        [KeyboardButton(text="🔁 Перевод"), KeyboardButton(text="🎮 Мини-игры")],
        [KeyboardButton(text="📊 Статистика"), KeyboardButton(text="💊 Толерантность")],
        [KeyboardButton(text="⚡ Эффекты")]
    ]
    if user_id == ADMIN_ID:
        keyboard.append([KeyboardButton(text="👑 Админ-панель")])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

def get_shop_keyboard() -> InlineKeyboardMarkup:
    buttons = []
    
    boosts = [item for item in SHOP_ITEMS if item.get("type") == "boost"]
    pills = [item for item in SHOP_ITEMS if item.get("type") == "pill"]
    protection = [item for item in SHOP_ITEMS if item.get("type") in ["protection", "insurance"]]
    other = [item for item in SHOP_ITEMS if item.get("type") in ["antidote", "lottery", "instant"]]
    
    buttons.append([InlineKeyboardButton(text="📈 БУСТЫ К ЗАРПЛАТЕ", callback_data="none")])
    for item in boosts:
        buttons.append([InlineKeyboardButton(
            text=f"{item['name']} - {format_money(item['price'])}",
            callback_data=f"buy_{item['id']}"
        )])
    
    buttons.append([InlineKeyboardButton(text="💊 ТАБЛЕТКИ НАГИРТ", callback_data="none")])
    for item in pills:
        buttons.append([InlineKeyboardButton(
            text=f"{item['name']} - {format_money(item['price'])}",
            callback_data=f"buy_{item['id']}"
        )])
    
    buttons.append([InlineKeyboardButton(text="🛡️ ЗАЩИТА", callback_data="none")])
    for item in protection:
        buttons.append([InlineKeyboardButton(
            text=f"{item['name']} - {format_money(item['price'])}",
            callback_data=f"buy_{item['id']}"
        )])
    
    for item in other:
        buttons.append([InlineKeyboardButton(
            text=f"{item['name']} - {format_money(item['price'])}",
            callback_data=f"buy_{item['id']}"
        )])
    
    buttons.append([
        InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main"),
        InlineKeyboardButton(text="❌ Закрыть", callback_data="shop_close")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_minigames_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="🎰 Рулетка", callback_data="game_roulette")],
        [InlineKeyboardButton(text="🛣️ Укладка асфальта", callback_data="game_asphalt")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_asphalt_keyboard(can_work: bool = True) -> InlineKeyboardMarkup:
    if can_work:
        buttons = [[InlineKeyboardButton(text="🛣️ Уложить асфальт", callback_data="lay_asphalt")]]
    else:
        buttons = [[InlineKeyboardButton(text="⏳ Жди 30 сек", callback_data="asphalt_wait")]]
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_games")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_users_keyboard(users: List[Dict[str, Any]], exclude_id: int, callback_prefix: str = "transfer_to_") -> InlineKeyboardMarkup:
    buttons = []
    for user in users:
        if user['user_id'] != exclude_id:
            name = user['full_name']
            if len(name) > 20:
                name = name[:17] + "..."
            buttons.append([InlineKeyboardButton(
                text=f"👤 {name} ({format_money(user['balance'])})",
                callback_data=f"{callback_prefix}{user['user_id']}"
            )])
    
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_transfer")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_admin_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="⚡ Штраф", callback_data="admin_fine")],
        [InlineKeyboardButton(text="🎁 Бонус", callback_data="admin_bonus")],
        [InlineKeyboardButton(text="🧾 Чеки", callback_data="admin_checks")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="❌ Закрыть", callback_data="admin_close")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_admin_checks_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="💰 Создать денежный чек", callback_data="admin_check_money")],
        [InlineKeyboardButton(text="🎁 Создать товарный чек", callback_data="admin_check_item")],
        [InlineKeyboardButton(text="📊 Список активных чеков", callback_data="admin_checks_list")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_items_for_checks() -> InlineKeyboardMarkup:
    buttons = []
    
    boosts = [item for item in SHOP_ITEMS if item.get("type") == "boost"]
    pills = [item for item in SHOP_ITEMS if item.get("type") == "pill"]
    other = [item for item in SHOP_ITEMS if item.get("type") in ["antidote", "insurance", "lottery", "instant"]]
    
    if boosts:
        buttons.append([InlineKeyboardButton(text="📈 БУСТЫ", callback_data="none")])
        for item in boosts[:3]:
            buttons.append([InlineKeyboardButton(
                text=f"{item['name']}",
                callback_data=f"check_item_{item['id']}"
            )])
    
    if pills:
        buttons.append([InlineKeyboardButton(text="💊 ТАБЛЕТКИ", callback_data="none")])
        for item in pills:
            buttons.append([InlineKeyboardButton(
                text=f"{item['name']}",
                callback_data=f"check_item_{item['id']}"
            )])
    
    if other:
        for item in other:
            buttons.append([InlineKeyboardButton(
                text=f"{item['name']}",
                callback_data=f"check_item_{item['id']}"
            )])
    
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_check_item")])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# ==================== СИСТЕМА ЧЕКОВ ====================
def generate_check_id() -> str:
    chars = string.ascii_uppercase + string.digits
    return 'CHK_' + ''.join(random.choices(chars, k=12))

async def create_gift_check(creator_id: int, check_type: str, amount: int = 0, 
                           item_id: str = None, max_uses: int = 1, hours: int = 24,
                           message: str = "") -> str:
    check_id = generate_check_id()
    created_at = datetime.now()
    expires_at = created_at + timedelta(hours=hours)
    
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''
            INSERT INTO gift_checks 
            (check_id, creator_id, check_type, amount, item_id, max_uses, 
             created_at, expires_at, custom_message)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (check_id, creator_id, check_type, amount, item_id, max_uses,
              created_at.isoformat(), expires_at.isoformat(), message))
        await db.commit()
    
    return check_id

async def activate_gift_check_by_link(user_id: int, check_id: str) -> Dict[str, Any]:
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute('''
            SELECT * FROM gift_checks 
            WHERE check_id = ? AND is_active = 1 
            AND (expires_at IS NULL OR expires_at > ?)
        ''', (check_id, datetime.now().isoformat()))
        check = await cursor.fetchone()
        
        if not check:
            return {"success": False, "error": "Чек не найден или недействителен"}
        
        check = dict(check)
        
        if check['used_count'] >= check['max_uses']:
            return {"success": False, "error": "Лимит использований исчерпан"}
        
        cursor = await db.execute('''
            SELECT 1 FROM check_activations 
            WHERE check_id = ? AND user_id = ?
        ''', (check_id, user_id))
        already_used = await cursor.fetchone()
        
        if already_used:
            return {"success": False, "error": "Вы уже активировали этот чек"}
        
        await db.execute('''
            UPDATE gift_checks 
            SET used_count = used_count + 1, last_used = ?
            WHERE check_id = ?
        ''', (datetime.now().isoformat(), check_id))
        
        await db.execute('''
            INSERT INTO check_activations (check_id, user_id, activated_at)
            VALUES (?, ?, ?)
        ''', (check_id, user_id, datetime.now().isoformat()))
        
        if check['check_type'] == 'money':
            amount = check['amount']
            await db.execute(
                "UPDATE players SET balance = balance + ? WHERE user_id = ?",
                (amount, user_id)
            )
            await db.execute('''
                INSERT INTO transactions (user_id, type, amount, description)
                VALUES (?, ?, ?, ?)
            ''', (user_id, 'check', amount, f"Активация чека {check_id}"))
            
            await db.execute('''
                UPDATE check_activations 
                SET received_amount = ?
                WHERE check_id = ? AND user_id = ?
            ''', (amount, check_id, user_id))
            
            reward_text = f"{format_money(amount)}"
            
        elif check['check_type'] == 'item':
            item_id = check['item_id']
            item = None
            for shop_item in SHOP_ITEMS:
                if shop_item["id"] == item_id:
                    item = shop_item
                    break
            
            if item:
                if item.get("type") == "boost":
                    await add_boost(user_id, item["id"], item["value"], item["hours"])
                elif item.get("type") == "pill":
                    await add_nagirt_pill(user_id, item["id"], item["effect"], item["hours"])
                
                await db.execute('''
                    UPDATE check_activations 
                    SET received_item = ?
                    WHERE check_id = ? AND user_id = ?
                ''', (item['name'], check_id, user_id))
                
                reward_text = f"{item['name']}"
            else:
                reward_text = "неизвестный предмет"
        
        await db.commit()
        
        cursor = await db.execute('''
            SELECT full_name FROM players WHERE user_id = ?
        ''', (check['creator_id'],))
        creator = await cursor.fetchone()
        creator_name = creator[0] if creator else "Администрация"
        
        return {
            "success": True, 
            "amount": check.get('amount'),
            "item": check.get('item_id'),
            "reward_text": reward_text,
            "message": check.get('custom_message', ''),
            "creator_name": creator_name,
            "used_count": check['used_count'] + 1,
            "max_uses": check['max_uses']
        }

async def get_active_checks() -> List[Dict[str, Any]]:
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute('''
            SELECT * FROM gift_checks 
            WHERE is_active = 1 AND (expires_at IS NULL OR expires_at > ?)
            ORDER BY created_at DESC
        ''', (datetime.now().isoformat(),))
        checks = await cursor.fetchall()
        return [dict(check) for check in checks]

async def get_check_stats(check_id: str) -> Dict[str, Any]:
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute('''
            SELECT g.*, u.full_name as creator_name 
            FROM gift_checks g
            LEFT JOIN players u ON g.creator_id = u.user_id
            WHERE g.check_id = ?
        ''', (check_id,))
        check = await cursor.fetchone()
        
        if not check:
            return None
        
        check = dict(check)
        
        cursor = await db.execute('''
            SELECT ca.*, p.full_name as user_name 
            FROM check_activations ca
            LEFT JOIN players p ON ca.user_id = p.user_id
            WHERE ca.check_id = ?
            ORDER BY ca.activated_at DESC
        ''', (check_id,))
        activations = await cursor.fetchall()
        
        check['activations'] = [dict(act) for act in activations]
        return check

async def deactivate_check(check_id: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''
            UPDATE gift_checks SET is_active = 0 WHERE check_id = ?
        ''', (check_id,))
        await db.commit()

# ==================== ОСНОВНЫЕ ОБРАБОТЧИКИ ====================
@dp.message(CommandStart())
async def cmd_start(message: Message):
    args = message.text.split()
    
    if len(args) > 1:
        check_id = args[1].upper()
        
        async with aiosqlite.connect(DB_NAME) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute('''
                SELECT 1 FROM gift_checks 
                WHERE check_id = ? AND is_active = 1
                AND (expires_at IS NULL OR expires_at > ?)
            ''', (check_id, datetime.now().isoformat()))
            check_exists = await cursor.fetchone()
        
        if check_exists:
            await handle_check_activation(message, check_id)
            return
    
    user_id = message.from_user.id
    username = message.from_user.username or "Без username"
    full_name = message.from_user.full_name

    await register_user(user_id, username, full_name)
    
    user = await get_user(user_id)
    nagirt_effects = await get_active_nagirt_effects(user_id)
    tolerance = await get_nagirt_tolerance(user_id)

    welcome_text = (
        f"👋 Добро пожаловать на работу, {full_name}!\n\n"
        f"Я *Виталик* — ваш генеральный директор! 👔\n\n"
        f"💰 *Начальный капитал:* {format_money(user['balance'] if user else ECONOMY_SETTINGS['start_balance'])}\n"
        f"💼 *Зарплата:* каждые 5 минут\n"
        f"⚡ *Случайные проверки:* каждые 20-30 минут\n\n"
    )
    
    if nagirt_effects["has_active"]:
        welcome_text += f"💊 *Активные таблетки:* +{int(nagirt_effects['salary_boost']*100)}%\n"
        welcome_text += f"⚠️ Риск штрафа: {ECONOMY_SETTINGS['fine_chance']*100}%\n\n"
    
    welcome_text += (
        f"📊 *Доступные функции:*\n"
        f"• 💰 Получка ({format_money(ECONOMY_SETTINGS['salary_min'])}-{format_money(ECONOMY_SETTINGS['salary_max'])})\n"
        f"• 🛒 Магазин (реалистичные цены)\n"
        f"• 🔁 Переводы между сотрудниками\n"
        f"• 🎮 Мини-игры для дополнительного заработка\n"
        f"• 💊 Таблетки Нагирт (риск/награда)\n"
        f"• 📊 Статистика и рейтинг\n\n"
    )
    
    if tolerance > 1.0:
        welcome_text += f"📈 Толерантность к Нагирту: +{int((tolerance-1)*100)}%\n\n"
    
    welcome_text += "*Внимание! Злоупотребление таблетками может привести к увольнению!* 💊"
    
    await message.answer(welcome_text, parse_mode="Markdown", reply_markup=get_main_keyboard(user_id))

async def handle_check_activation(message: Message, check_id: str):
    user_id = message.from_user.id
    username = message.from_user.username or "Без username"
    full_name = message.from_user.full_name
    
    await register_user(user_id, username, full_name)
    
    result = await activate_gift_check_by_link(user_id, check_id)
    
    if not result['success']:
        extra_text = f"\n\n❌ *Не удалось активировать чек:* {result['error']}"
        user = await get_user(user_id)
        nagirt_effects = await get_active_nagirt_effects(user_id)
        tolerance = await get_nagirt_tolerance(user_id)

        welcome_text = (
            f"👋 Добро пожаловать на работу, {full_name}!\n\n"
            f"Я *Виталик* — ваш генеральный директор! 👔\n\n"
            f"💰 *Начальный капитал:* {format_money(user['balance'] if user else ECONOMY_SETTINGS['start_balance'])}\n"
            f"💼 *Зарплата:* каждые 5 минут\n"
            f"⚡ *Случайные проверки:* каждые 20-30 минут\n\n"
        )
        
        if nagirt_effects["has_active"]:
            welcome_text += f"💊 *Активные таблетки:* +{int(nagirt_effects['salary_boost']*100)}%\n"
            welcome_text += f"⚠️ Риск штрафа: {ECONOMY_SETTINGS['fine_chance']*100}%\n\n"
        
        welcome_text += (
            f"📊 *Доступные функции:*\n"
            f"• 💰 Получка ({format_money(ECONOMY_SETTINGS['salary_min'])}-{format_money(ECONOMY_SETTINGS['salary_max'])})\n"
            f"• 🛒 Магазин (реалистичные цены)\n"
            f"• 🔁 Переводы между сотрудниками\n"
            f"• 🎮 Мини-игры для дополнительного заработка\n"
            f"• 💊 Таблетки Нагирт (риск/награда)\n"
            f"• 📊 Статистика и рейтинг\n\n"
        )
        
        if tolerance > 1.0:
            welcome_text += f"📈 Толерантность к Нагирту: +{int((tolerance-1)*100)}%\n\n"
        
        welcome_text += "*Внимание! Злоупотребление таблетками может привести к увольнению!* 💊"
        
        welcome_text += extra_text
        
        await message.answer(welcome_text, parse_mode="Markdown", reply_markup=get_main_keyboard(user_id))
        return
    
    if result['amount']:
        reward_text = f"💰 *{format_money(result['amount'])}*"
    else:
        reward_text = f"🎁 *{result['reward_text']}*"
    
    response = (
        f"🎉 *ЧЕК АКТИВИРОВАН!*\n\n"
        f"✅ Вы получили: {reward_text}\n"
        f"👤 От: {result['creator_name']}\n"
        f"🔢 {result['used_count']}/{result['max_uses']} использований\n"
    )
    
    if result['message']:
        response += f"💌 Сообщение: {result['message']}\n"
    
    response += f"\n🏦 *Баланс обновлён!*\n"
    
    user = await get_user(user_id)
    response += f"💰 Ваш баланс: {format_money(user['balance'])}\n\n"
    
    response += (
        f"🎮 *Доступные функции:*\n"
        f"• 💰 Получка каждые 5 минут\n"
        f"• 🛒 Магазин с бустами и таблетками\n"
        f"• 🎮 Мини-игры (рулетка, асфальт)\n"
        f"• 🔁 Переводы другим игрокам\n\n"
        f"*Добро пожаловать в компанию Виталика!* 👔"
    )
    
    await message.answer(response, parse_mode="Markdown", reply_markup=get_main_keyboard(user_id))

# ==================== ИСПРАВЛЕННАЯ СТАТИСТИКА ====================
@dp.message(F.text == "📊 Статистика")
async def handle_statistics(message: Message):
    user_id = message.from_user.id
    
    try:
        # Получаем пользователя напрямую из базы
        async with aiosqlite.connect(DB_NAME) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM players WHERE user_id = ?", (user_id,))
            user_data = await cursor.fetchone()
        
        if not user_data:
            await message.answer("Сначала зарегистрируйтесь через /start")
            return
        
        user = dict(user_data)
        
        # Формируем текст статистики БЕЗ markdown
        stats_text = "📊 ВАША СТАТИСТИКА\n\n"
        stats_text += f"👤 Имя: {user.get('full_name', 'Неизвестно')}\n"
        stats_text += f"💰 Баланс: {user.get('balance', 0)}₽\n"
        
        # Проверяем наличие полей и добавляем их если есть
        if 'total_earned' in user:
            stats_text += f"📈 Всего заработано: {user.get('total_earned', 0)}₽\n"
        if 'total_fines' in user:
            stats_text += f"⚡ Штрафов получено: {user.get('total_fines', 0)}₽\n"
        if 'salary_count' in user:
            stats_text += f"💼 Получок: {user.get('salary_count', 0)}\n"
        if 'asphalt_meters' in user:
            stats_text += f"🛣️ Уложено асфальта: {user.get('asphalt_meters', 0)} метров\n"
        if 'asphalt_earned' in user:
            stats_text += f"💵 На асфальте заработано: {user.get('asphalt_earned', 0)}₽\n"
        
        stats_text += "\n"
        
        # Пробуем получить топ игроков
        try:
            async with aiosqlite.connect(DB_NAME) as db:
                cursor = await db.execute(
                    "SELECT full_name, balance FROM players WHERE balance > 0 ORDER BY balance DESC LIMIT 5"
                )
                top_players = await cursor.fetchall()
                
                if top_players:
                    stats_text += "🏆 ТОП-5 ИГРОКОВ:\n"
                    for i, player in enumerate(top_players, 1):
                        name = str(player[0])[:10]
                        if len(str(player[0])) > 10:
                            name += "..."
                        balance = player[1] if player[1] else 0
                        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
                        medal = medals[i-1] if i <= len(medals) else f"{i}."
                        stats_text += f"{medal} {name}: {balance}₽\n"
        except Exception as e:
            logger.error(f"Ошибка при получении топа: {e}")
            stats_text += "🏆 Топ игроков временно недоступен\n"
        
        # Отправляем простым текстом (без parse_mode)
        await message.answer(stats_text)
        
    except Exception as e:
        logger.error(f"Ошибка в статистике: {e}")
        # Очень простой ответ на случай ошибки
        await message.answer(f"Ваш баланс: 0₽\n(Ошибка: {str(e)[:30]})")

# ==================== ПОЛУЧКА ====================
@dp.message(F.text == "💰 Получка")
async def handle_paycheck(message: Message):
    user_id = message.from_user.id
    user = await get_user(user_id)

    if not user:
        await message.answer("Сначала зарегистрируйтесь через /start")
        return

    current_time = datetime.now()
    last_salary = user.get('last_salary')

    if last_salary:
        last_salary_time = datetime.fromisoformat(last_salary)
        time_since_last = current_time - last_salary_time
        min_wait = timedelta(seconds=ECONOMY_SETTINGS["salary_interval"])

        if time_since_last < min_wait:
            wait_seconds = int((min_wait - time_since_last).total_seconds())
            wait_time = format_time(wait_seconds)
            await message.answer(f"⏳ *Слишком рано!*\n\nЖди еще *{wait_time}* (мм:сс)")
            return

    await cleanup_expired()
    
    boost_multiplier = await get_active_boosts(user_id)
    nagirt_effects = await get_active_nagirt_effects(user_id)
    tolerance = await get_nagirt_tolerance(user_id)
    
    base_salary = random.randint(
        ECONOMY_SETTINGS["salary_min"], 
        ECONOMY_SETTINGS["salary_max"]
    )
    
    pill_fine = 0
    base_fine_chance = ECONOMY_SETTINGS["fine_chance"]
    
    if nagirt_effects["has_active"]:
        nagirt_fine_chance = base_fine_chance * nagirt_effects["risk_multiplier"]
        
        if tolerance > 1.5:
            nagirt_fine_chance *= 1.5
        elif tolerance > 1.2:
            nagirt_fine_chance *= 1.2
        
        if nagirt_effects["side_effects"]:
            nagirt_fine_chance += len(nagirt_effects["side_effects"]) * 0.15
        
        total_fine_chance = min(0.9, nagirt_fine_chance)
        
        if random.random() <= total_fine_chance:
            penalty_multiplier = 1.0 + (nagirt_effects["risk_multiplier"] - 1) * 0.5
            pill_fine = random.randint(
                int(base_salary * 0.15 * penalty_multiplier),
                int(base_salary * 0.4 * penalty_multiplier)
            )
            
            fine_reasons = [
                "Обнаружены следы Нагирта в крови при медосмотре!",
                "Работа в состоянии измененного сознания!",
                "Нарушение техники безопасности из-за таблеток!",
                "Неадекватное поведение на рабочем месте!",
                "Потеря концентрации из-за побочных эффектов!"
            ]
            
            if nagirt_effects["side_effects"]:
                fine_reasons.append(f"Медосмотр выявил: {', '.join(nagirt_effects['side_effects'][:2])}!")
            
            if tolerance > 1.5:
                fine_reasons.append(f"Злоупотребление Нагиртом! Толерантность: +{int((tolerance-1)*100)}%")
            
            await update_balance(user_id, -pill_fine, "penalty", 
                                f"💊 {random.choice(fine_reasons)}")
    
    total_multiplier = 1.0 + boost_multiplier + nagirt_effects["salary_boost"]
    final_salary = int(base_salary * total_multiplier)
    
    await update_balance(user_id, final_salary, "salary", f"💸 Зарплата (x{total_multiplier:.2f})")
    
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE players SET last_salary = ? WHERE user_id = ?",
            (current_time.isoformat(), user_id)
        )
        await db.commit()
    
    user = await get_user(user_id)
    
    response = f"💸 *ЗАРПЛАТА НАЧИСЛЕНА!*\n\n"
    response += f"📊 *Детализация:*\n"
    response += f"• Базовая ставка: {format_money(base_salary)}\n"
    
    details = []
    if boost_multiplier > 0:
        details.append(f"Бусты: +{int(boost_multiplier*100)}%")
    if nagirt_effects["salary_boost"] > 0:
        details.append(f"Нагирт: +{int(nagirt_effects['salary_boost']*100)}%")
    
    if details:
        response += f"• Доплаты: {', '.join(details)}\n"
    
    response += f"• Итоговый коэффициент: x{total_multiplier:.2f}\n\n"
    
    if pill_fine > 0:
        response += f"⚠️ *ШТРАФ ЗА НАГИРТ:* -{format_money(pill_fine)}\n\n"
    
    response += f"✅ *Итого начислено:* {format_money(final_salary)}\n"
    response += f"💳 *Новый баланс:* {format_money(user['balance'])}\n\n"
    
    if final_salary < ECONOMY_SETTINGS["salary_min"] * 1.5:
        comments = ["Могло бы быть и больше...", "На такую сумму даже пиццу не купишь!", "Работай лучше!"]
    elif final_salary > ECONOMY_SETTINGS["salary_max"] * 0.8:
        comments = ["Отличная работа!", "Так держать!", "Вы заслужили эту премию!"]
    else:
        comments = ["Нормально работаешь.", "Продолжай в том же духе.", "Стабильно, но можно лучше."]
    
    if nagirt_effects["has_active"]:
        if pill_fine > 0:
            pill_comments = [
                "Таблетки — не замена профессионализму!",
                "Осторожнее с Нагиртом!",
                "Лекарства должны помогать, а не мешать работе!"
            ]
        else:
            pill_comments = [
                "Нагирт работает, но будь осторожен!",
                "Таблетки усилили твою продуктивность!",
                "Не злоупотребляй Нагиртом!"
            ]
        response += f"💬 *Виталик:* '{random.choice(pill_comments)}'"
    else:
        response += f"💬 *Виталик:* '{random.choice(comments)}'"
    
    await message.answer(response, parse_mode="Markdown")

# ==================== МАГАЗИН ====================
@dp.message(F.text == "🛒 Магазин")
async def handle_shop(message: Message):
    user_id = message.from_user.id
    user = await get_user(user_id)
    
    if not user:
        await message.answer("Сначала зарегистрируйтесь через /start")
        return
    
    active_boosts = await get_active_boosts(user_id)
    nagirt_effects = await get_active_nagirt_effects(user_id)
    
    shop_text = (
        "🏪 *Корпоративный магазин Виталика*\n\n"
        f"💰 *Ваш баланс:* {format_money(user['balance'])}\n\n"
    )
    
    if active_boosts > 0:
        shop_text += f"📈 *Активные бусты:* +{int(active_boosts*100)}%\n"
    
    if nagirt_effects["has_active"]:
        shop_text += f"💊 *Активные таблетки:* +{int(nagirt_effects['salary_boost']*100)}%\n"
    
    shop_text += (
        "\n*Категории товаров:*\n"
        "• 📈 **Бусты** - увеличивают зарплату\n"
        "• 💊 **Нагирт** - мощные усилители с риском\n"
        "• 🛡️ **Защита** - от штрафов и проверок\n"
        "• 🎁 **Разное** - лотереи и экстренные опции\n\n"
        "⚠️ *Таблетки Нагирт имеют побочные эффекты и вызывают привыкание!*"
    )
    
    await message.answer(shop_text, parse_mode="Markdown", reply_markup=get_shop_keyboard())

# ==================== ПОКУПКА ТОВАРОВ ====================
@dp.callback_query(F.data.startswith("buy_"))
async def handle_buy_item(callback: CallbackQuery):
    user_id = callback.from_user.id
    user = await get_user(user_id)
    
    if not user:
        await callback.answer("❌ Пользователь не найден!", show_alert=True)
        return
    
    item_id = callback.data[4:]
    
    item = None
    for shop_item in SHOP_ITEMS:
        if shop_item["id"] == item_id:
            item = shop_item
            break
    
    if not item:
        await callback.answer("❌ Товар не найден!", show_alert=True)
        return
    
    if user['balance'] < item['price']:
        await callback.answer(f"❌ Недостаточно средств! Нужно {format_money(item['price'])}", show_alert=True)
        return
    
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE players SET balance = balance - ? WHERE user_id = ?",
            (item['price'], user_id)
        )
        
        await db.execute(
            '''INSERT INTO purchases (user_id, item_name, price) VALUES (?, ?, ?)''',
            (user_id, item['name'], item['price'])
        )
        
        await db.execute(
            '''INSERT INTO transactions (user_id, type, amount, description)
               VALUES (?, 'purchase', -?, ?)''',
            (user_id, item['price'], f"Покупка: {item['name']}")
        )
        
        await db.commit()
    
    bonus_text = ""
    
    if item.get("type") == "boost":
        await add_boost(user_id, item["id"], item["value"], item["hours"])
        bonus_text = f"✅ Буст активирован! +{int(item['value']*100)}% к зарплате на {item['hours']}ч"
    
    elif item.get("type") == "protection":
        if item["id"] == "day_off":
            immunity_until = (datetime.now() + timedelta(hours=item["hours"])).isoformat()
            async with aiosqlite.connect(DB_NAME) as db:
                await db.execute(
                    "UPDATE players SET penalty_immunity_until = ? WHERE user_id = ?",
                    (immunity_until, user_id)
                )
                await db.commit()
            bonus_text = f"✅ Иммунитет к штрафам активирован на {item['hours']}ч!"
        elif item["id"] == "insurance":
            async with aiosqlite.connect(DB_NAME) as db:
                await db.execute(
                    "INSERT INTO boosts (user_id, boost_type, boost_value, expires_at) VALUES (?, ?, ?, ?)",
                    (user_id, "insurance", 0.8, (datetime.now() + timedelta(hours=24)).isoformat())
                )
                await db.commit()
            bonus_text = "✅ Страховка активирована! Следующий штраф будет возмещен на 80%"
    
    elif item.get("type") == "pill":
        tolerance = await get_nagirt_tolerance(user_id)
        real_effect = item["effect"] / tolerance
        
        side_effects_list = []
        side_effect_chance = item.get("side_effect_chance", 0)
        
        if random.randint(1, 100) <= side_effect_chance:
            side_effects_pool = [
                "Тошнота", "Головокружение", "Дрожь в руках", "Нарушение координации",
                "Слабость", "Спутанность сознания", "Повышенное давление", "Тахикардия",
                "Нарушение зрения", "Сухость во рту", "Бессонница", "Тревожность"
            ]
            num_effects = 1
            if item["id"] == "nagirt_pro":
                num_effects = random.randint(1, 2)
            elif item["id"] == "nagirt_extreme":
                num_effects = random.randint(2, 3)
            
            side_effects_list = random.sample(side_effects_pool, min(num_effects, len(side_effects_pool)))
        
        side_effects_json = json.dumps(side_effects_list, ensure_ascii=False)
        
        await add_nagirt_pill(user_id, item["id"], real_effect, item["hours"], side_effects_json)
        
        tolerance_increase = 0.1
        if item["id"] == "nagirt_pro":
            tolerance_increase = 0.15
        elif item["id"] == "nagirt_extreme":
            tolerance_increase = 0.2
        
        await update_nagirt_tolerance(user_id, tolerance_increase)
        
        bonus_text = f"💊 Таблетка принята! Эффект: +{int(real_effect*100)}% на {item['hours']}ч"
        
        if side_effects_list:
            bonus_text += f"\n⚠️ Побочные эффекты: {', '.join(side_effects_list)}"
        
        if tolerance > 1.0:
            bonus_text += f"\n📉 Толерантность: +{int((tolerance-1)*100)}% (эффект ослаблен)"
        
        risk_increase = int((item.get("side_effect_chance", 0) / 2))
        bonus_text += f"\n⚡ Риск штрафа увеличен на {risk_increase}%"
    
    elif item.get("type") == "antidote":
        await reset_nagirt_tolerance(user_id)
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("DELETE FROM nagirt_pills WHERE user_id = ?", (user_id,))
            await db.commit()
        bonus_text = "💉 Антидот применен! Все эффекты таблеток сняты, толерантность сброшена."
    
    elif item.get("type") == "lottery":
        if random.random() <= 0.25:
            win_amount = random.randint(2000, 10000)
            async with aiosqlite.connect(DB_NAME) as db:
                await db.execute(
                    "UPDATE players SET balance = balance + ? WHERE user_id = ?",
                    (win_amount, user_id)
                )
                await db.commit()
            bonus_text = f"🎉 ДЖЕКПОТ! Вы выиграли {format_money(win_amount)}!"
        else:
            bonus_text = "😔 Не повезло... Попробуй еще раз!"
    
    elif item.get("type") == "instant":
        salary = random.randint(
            ECONOMY_SETTINGS["salary_min"], 
            ECONOMY_SETTINGS["salary_max"]
        )
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute(
                "UPDATE players SET balance = balance + ?, last_salary = ? WHERE user_id = ?",
                (salary, datetime.now().isoformat(), user_id)
            )
            await db.commit()
        bonus_text = f"⏱️ Мгновенная зарплата: {format_money(salary)}"
    
    user = await get_user(user_id)
    
    response = (
        f"✅ *Покупка завершена*\n\n"
        f"📦 Товар: {item['name']}\n"
        f"💰 Стоимость: {format_money(item['price'])}\n"
        f"🎁 {bonus_text}\n\n"
        f"💳 Остаток: {format_money(user['balance'])}"
    )
    
    try:
        await callback.message.edit_text(response, parse_mode="Markdown")
    except:
        await callback.message.answer(response, parse_mode="Markdown")
    
    await callback.answer()

# ==================== ОСТАЛЬНЫЕ ОБРАБОТЧИКИ (сокращены для краткости) ====================
# Вставьте сюда остальные обработчики из вашего оригинального кода:
# - handle_minigames
# - handle_game_roulette_start
# - handle_roulette_bet
# - handle_game_asphalt
# - handle_lay_asphalt
# - handle_asphalt_wait
# - handle_tolerance
# - handle_effects
# - handle_transfer_start
# - handle_transfer_recipient
# - handle_cancel_transfer
# - handle_transfer_amount
# - handle_admin_panel
# - handle_admin_broadcast
# - handle_broadcast_message
# - handle_admin_fine_start
# - handle_admin_fine_user
# - handle_admin_fine_amount
# - handle_admin_bonus_start
# - handle_admin_bonus_user
# - handle_admin_bonus_amount
# - handle_admin_stats
# - handle_admin_close
# - handle_admin_checks
# - handle_admin_checks_back
# - handle_admin_check_money
# - handle_admin_check_item
# - handle_check_item_select
# - handle_check_amount
# - handle_check_uses
# - handle_check_hours
# - handle_check_message
# - handle_send_link
# - handle_admin_checks_list
# - handle_check_stats
# - handle_check_deactivate
# - handle_admin_back
# - handle_back_to_main
# - handle_back_to_games
# - handle_shop_close

# ==================== ЗАПУСК БОТА ====================
async def on_startup():
    await init_db()
    
    # Проверяем username бота
    bot_info = await bot.get_me()
    if not bot_info.username:
        logger.error("❌ У бота нет username! Чеки не будут работать.")
        logger.error("Установите username в @BotFather и перезапустите бота.")
    else:
        logger.info(f"✅ Username бота: @{bot_info.username}")
    
    asyncio.create_task(penalty_scheduler())
    logger.info("✅ Бот запущен! Всё должно работать.")

async def on_shutdown():
    logger.info("🛑 Бот останавливается...")

async def penalty_scheduler():
    """Планировщик случайных штрафов"""
    while True:
        try:
            wait_time = random.randint(
                ECONOMY_SETTINGS["random_fine_interval_min"],
                ECONOMY_SETTINGS["random_fine_interval_max"]
            )
            await asyncio.sleep(wait_time)
            
            all_users = await get_all_users()
            logger.info(f"🔍 Проверка на штрафы: {len(all_users)} пользователей")
            
            for user in all_users:
                user_data = await get_user(user['user_id'])
                if not user_data:
                    continue
                    
                if await has_fine_protection(user_data['user_id']):
                    continue
                
                if random.random() <= 0.25 and user_data['balance'] > ECONOMY_SETTINGS["random_fine_min"]:
                    penalty = random.randint(
                        ECONOMY_SETTINGS["random_fine_min"],
                        min(ECONOMY_SETTINGS["random_fine_max"], int(user_data['balance'] * 0.3))
                    )
                    
                    penalty_reasons = [
                        "Внеплановая проверка! Обнаружены нарушения.",
                        "Неправильно заполнена отчетность.",
                        "Опоздание на работу.",
                        "Использование рабочего времени в личных целях.",
                        "Нарушение дресс-кода.",
                        "Невыполнение плана продаж.",
                        "Поломка корпоративного оборудования.",
                        "Конфликт с коллегами.",
                        "Утечка конфиденциальной информации.",
                        "Несанкционированный доступ к данным."
                    ]
                    
                    reason = random.choice(penalty_reasons)
                    
                    await update_balance(
                        user_data['user_id'], 
                        -penalty, 
                        "penalty",
                        f"⚡ Случайная проверка: {reason}"
                    )
                    
                    try:
                        await bot.send_message(
                            user_data['user_id'],
                            f"⚠️ *СЛУЧАЙНАЯ ПРОВЕРКА ОТ ВИТАЛИКА!*\n\n"
                            f"📛 Причина: {reason}\n"
                            f"💸 Штраф: {format_money(penalty)}\n"
                            f"💰 Новый баланс: {format_money(user_data['balance'] - penalty)}\n\n"
                            f"Купите 'Выходной' в магазине для защиты!",
                            parse_mode="Markdown"
                        )
                        logger.info(f"Штраф {penalty}₽ пользователю {user_data['user_id']}")
                    except Exception as e:
                        logger.error(f"Не удалось отправить уведомление: {e}")
        
        except Exception as e:
            logger.error(f"Ошибка в планировщике штрафов: {e}")
            await asyncio.sleep(300)

async def main():
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    await dp.start_polling(bot, skip_updates=True)

if __name__ == "__main__":
    asyncio.run(main())
