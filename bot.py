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
        
        # Разные таблетки дают разные эффекты
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
        
        # Парсим побочные эффекты
        if side_effects_json:
            try:
                side_effects = json.loads(side_effects_json)
                if isinstance(side_effects, list):
                    effects["side_effects"].extend(side_effects)
                    # Каждая побочка увеличивает риск
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
    return f"{amount:,}₽".replace(",", " ")

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
    """Клавиатура с товарами для чеков"""
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
    """Генерация уникального ID чека"""
    chars = string.ascii_uppercase + string.digits
    return 'CHK_' + ''.join(random.choices(chars, k=12))

async def create_gift_check(creator_id: int, check_type: str, amount: int = 0, 
                           item_id: str = None, max_uses: int = 1, hours: int = 24,
                           message: str = "") -> str:
    """Создание подарочного чека"""
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
    """Активация подарочного чека по ссылке"""
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
    """Получение списка активных чеков"""
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
    """Статистика по чеку"""
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
    """Деактивация чека"""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''
            UPDATE gift_checks SET is_active = 0 WHERE check_id = ?
        ''', (check_id,))
        await db.commit()

# ==================== ОСНОВНЫЕ ОБРАБОТЧИКИ ====================
@dp.message(CommandStart())
async def cmd_start(message: Message):
    """Обработка команды /start с параметром чека"""
    args = message.text.split()
    
    if len(args) > 1:
        # Возможно, это активация чека
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
            # Это чек, активируем его
            await handle_check_activation(message, check_id)
            return
    
    # Обычный старт (регистрация)
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
    """Активация чека по ссылке"""
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

# ==================== ПОЛУЧКА С ИСПРАВЛЕННЫМ НАГИРТОМ ====================
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
    
    # РАСШИРЕННАЯ СИСТЕМА ШТРАФОВ ЗА НАГИРТ
    pill_fine = 0
    base_fine_chance = ECONOMY_SETTINGS["fine_chance"]  # 25%
    
    if nagirt_effects["has_active"]:
        # Увеличиваем шанс штрафа в зависимости от силы нагирта и побочек
        nagirt_fine_chance = base_fine_chance * nagirt_effects["risk_multiplier"]
        
        # Дополнительный риск за высокую толерантность
        if tolerance > 1.5:
            nagirt_fine_chance *= 1.5
        elif tolerance > 1.2:
            nagirt_fine_chance *= 1.2
        
        # Каждая побочка увеличивает шанс
        if nagirt_effects["side_effects"]:
            nagirt_fine_chance += len(nagirt_effects["side_effects"]) * 0.15
        
        total_fine_chance = min(0.9, nagirt_fine_chance)  # Макс 90%
        
        if random.random() <= total_fine_chance:
            # Размер штрафа зависит от силы нагирта и побочек
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

# ==================== ПОКУПКА ТОВАРОВ С ИСПРАВЛЕННЫМ НАГИРТОМ ====================
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
        
        # ГЕНЕРАЦИЯ НЕСКОЛЬКИХ ПОБОЧНЫХ ЭФФЕКТОВ
        side_effects_list = []
        side_effect_chance = item.get("side_effect_chance", 0)
        
        if random.randint(1, 100) <= side_effect_chance:
            side_effects_pool = [
                "Тошнота", "Головокружение", "Дрожь в руках", "Нарушение координации",
                "Слабость", "Спутанность сознания", "Повышенное давление", "Тахикардия",
                "Нарушение зрения", "Сухость во рту", "Бессонница", "Тревожность"
            ]
            # От 1 до 3 побочек в зависимости от силы таблетки
            num_effects = 1
            if item["id"] == "nagirt_pro":
                num_effects = random.randint(1, 2)
            elif item["id"] == "nagirt_extreme":
                num_effects = random.randint(2, 3)
            
            side_effects_list = random.sample(side_effects_pool, min(num_effects, len(side_effects_pool)))
        
        side_effects_json = json.dumps(side_effects_list, ensure_ascii=False)
        
        await add_nagirt_pill(user_id, item["id"], real_effect, item["hours"], side_effects_json)
        
        # УВЕЛИЧИВАЕМ ТОЛЕРАНТНОСТЬ В ЗАВИСИМОСТИ ОТ СИЛЫ ТАБЛЕТКИ
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
        
        # Уведомление о повышенном риске
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

# ==================== МИНИ-ИГРЫ ====================
@dp.message(F.text == "🎮 Мини-игры")
async def handle_minigames(message: Message):
    user_id = message.from_user.id
    user = await get_user(user_id)
    
    if not user:
        await message.answer("Сначала зарегистрируйтесь через /start")
        return
    
    games_text = (
        "🎮 *КОРПОРАТИВНЫЕ МИНИ-ИГРЫ*\n\n"
        "🎰 *Рулетка*\n"
        f"• Минимальная ставка: {format_money(ECONOMY_SETTINGS['roulette_min_bet'])}\n"
        f"• Шанс выигрыша: {int(ECONOMY_SETTINGS['roulette_win_chance']*100)}%\n"
        f"• Выигрыш: x2 от ставки\n\n"
        "🛣️ *Укладка асфальта*\n"
        f"• Заработок за метр: {format_money(ECONOMY_SETTINGS['asphalt_earnings'])}\n"
        f"• Штраф за брак: {format_money(ECONOMY_SETTINGS['asphalt_fine_min'])}-{format_money(ECONOMY_SETTINGS['asphalt_fine_max'])}\n"
        f"• Шанс успеха: 70% (с Нагиртом до 95%)\n"
        f"• Время работы: 30 секунд\n\n"
        f"💰 Ваш баланс: {format_money(user['balance'])}"
    )
    
    await message.answer(games_text, parse_mode="Markdown", reply_markup=get_minigames_keyboard())

# ==================== РУЛЕТКА ====================
@dp.callback_query(F.data == "game_roulette")
async def handle_game_roulette_start(callback: CallbackQuery, state: FSMContext):
    """Начало игры в рулетку"""
    user_id = callback.from_user.id
    user = await get_user(user_id)
    
    if not user:
        await callback.answer("❌ Ошибка: пользователь не найден", show_alert=True)
        return
    
    roulette_text = (
        f"🎰 *РУЛЕТКА*\n\n"
        f"💰 Ваш баланс: {format_money(user['balance'])}\n"
        f"🎯 Шанс выигрыша: {int(ECONOMY_SETTINGS['roulette_win_chance']*100)}%\n"
        f"💰 Выигрыш: x2 от ставки\n\n"
        f"💸 *Введите сумму ставки:*\n"
        f"Минимум: {format_money(ECONOMY_SETTINGS['roulette_min_bet'])}\n"
        f"Максимум: {format_money(min(ECONOMY_SETTINGS['roulette_max_bet'], user['balance']))}"
    )
    
    await callback.message.edit_text(roulette_text, parse_mode="Markdown")
    
    # Сохраняем данные для состояния
    await state.update_data(user_id=user_id, user_balance=user['balance'])
    await state.set_state(RouletteStates.waiting_for_bet)
    await callback.answer()

@dp.message(RouletteStates.waiting_for_bet)
async def handle_roulette_bet(message: Message, state: FSMContext):
    """Обработка ставки в рулетке"""
    user_id = message.from_user.id
    data = await state.get_data()
    
    # Проверяем, тот ли пользователь
    if data.get('user_id') != user_id:
        await message.answer("❌ Ошибка сессии")
        await state.clear()
        return
    
    try:
        bet = int(message.text.strip())
        user = await get_user(user_id)
        
        if not user:
            await message.answer("❌ Пользователь не найден")
            await state.clear()
            return
        
        # Проверка минимальной ставки
        if bet < ECONOMY_SETTINGS["roulette_min_bet"]:
            await message.answer(f"❌ Минимальная ставка - {format_money(ECONOMY_SETTINGS['roulette_min_bet'])}")
            return
        
        # Проверка максимальной ставки
        if bet > ECONOMY_SETTINGS["roulette_max_bet"]:
            await message.answer(f"❌ Максимальная ставка - {format_money(ECONOMY_SETTINGS['roulette_max_bet'])}")
            return
        
        # Проверка баланса
        if bet > user['balance']:
            await message.answer(f"❌ Недостаточно средств! Доступно: {format_money(user['balance'])}")
            return
        
        # Играем в рулетку
        win = random.random() <= ECONOMY_SETTINGS["roulette_win_chance"]
        
        if win:
            # ВЫИГРЫШ
            win_amount = bet * 2  # x2 от ставки
            net_profit = bet  # Чистая прибыль (ставка уже включена в win_amount)
            
            async with aiosqlite.connect(DB_NAME) as db:
                # Добавляем чистый выигрыш (ставка уже есть у пользователя)
                await db.execute(
                    "UPDATE players SET balance = balance + ? WHERE user_id = ?",
                    (bet, user_id)  # Добавляем только выигрыш, ставка остается
                )
                
                # Записываем транзакцию
                await db.execute(
                    '''INSERT INTO transactions (user_id, type, amount, description)
                       VALUES (?, ?, ?, ?)''',
                    (user_id, 'roulette_win', bet, f"Выигрыш в рулетке: ставка {bet}₽")
                )
                
                await db.commit()
            
            user = await get_user(user_id)
            
            result_text = (
                f"🎰 *РУЛЕТКА*\n\n"
                f"🎉 *ВЫ ВЫИГРАЛИ!*\n\n"
                f"💰 Ставка: {format_money(bet)}\n"
                f"🏆 Выигрыш: {format_money(win_amount)}\n"
                f"💎 Чистая прибыль: {format_money(net_profit)}\n"
                f"💳 Новый баланс: {format_money(user['balance'])}\n\n"
                f"Поздравляем! 🎊"
            )
        else:
            # ПРОИГРЫШ
            async with aiosqlite.connect(DB_NAME) as db:
                # Списываем ставку
                await db.execute(
                    "UPDATE players SET balance = balance - ? WHERE user_id = ?",
                    (bet, user_id)
                )
                
                # Записываем транзакцию
                await db.execute(
                    '''INSERT INTO transactions (user_id, type, amount, description)
                       VALUES (?, ?, ?, ?)''',
                    (user_id, 'roulette_lose', -bet, f"Проигрыш в рулетке: ставка {bet}₽")
                )
                
                await db.commit()
            
            user = await get_user(user_id)
            
            result_text = (
                f"🎰 *РУЛЕТКА*\n\n"
                f"💥 *ВЫ ПРОИГРАЛИ*\n\n"
                f"💰 Ставка: {format_money(bet)}\n"
                f"📉 Потеряно: {format_money(bet)}\n"
                f"💳 Новый баланс: {format_money(user['balance'])}\n\n"
                f"Не повезло... 😔"
            )
        
        await message.answer(result_text, parse_mode="Markdown", reply_markup=get_minigames_keyboard())
        
    except ValueError:
        await message.answer("❌ Пожалуйста, введите число!")
        return
    except Exception as e:
        logger.error(f"Ошибка в рулетке: {e}")
        await message.answer("❌ Произошла ошибка, попробуйте еще раз")
    
    await state.clear()

# ==================== УКЛАДКА АСФАЛЬТА С ИСПРАВЛЕННЫМ НАГИРТОМ ====================
@dp.callback_query(F.data == "game_asphalt")
async def handle_game_asphalt(callback: CallbackQuery):
    """Меню укладки асфальта с отображением эффектов Нагирта"""
    user_id = callback.from_user.id
    user = await get_user(user_id)
    
    if not user:
        await callback.answer("❌ Ошибка: пользователь не найден", show_alert=True)
        return
    
    # Получаем эффекты нагирта
    nagirt_effects = await get_active_nagirt_effects(user_id)
    
    # Простая проверка времени
    can_work = True
    last_asphalt = user.get('last_asphalt')
    
    if last_asphalt:
        try:
            last_time = datetime.fromisoformat(last_asphalt)
            time_passed = (datetime.now() - last_time).total_seconds()
            if time_passed < 30:
                can_work = False
        except:
            can_work = True  # Если ошибка, разрешаем работать
    
    asphalt_text = (
        f"🛣️ *Укладка асфальта*\n\n"
        f"💰 Баланс: {format_money(user['balance'])}\n"
        f"📏 Уложено метров: {user.get('asphalt_meters', 0):,}\n"
        f"💵 Заработано: {format_money(user.get('asphalt_earned', 0))}\n\n"
    )
    
    # Показываем эффекты Нагирта
    if nagirt_effects["has_active"]:
        asphalt_text += f"💊 *Активный Нагирт:* +{int(nagirt_effects['asphalt_boost']*100)}% к заработку\n"
        if nagirt_effects["side_effects"]:
            asphalt_text += f"⚠️ *Побочки:* {', '.join(nagirt_effects['side_effects'][:2])}\n"
        asphalt_text += "\n"
    
    if can_work:
        asphalt_text += "Нажми кнопку ниже, чтобы уложить 1 метр асфальта!"
    else:
        asphalt_text += "⏳ *Асфальт еще сохнет!*\nПодожди 30 секунд между укладками."
    
    try:
        await callback.message.edit_text(
            asphalt_text,
            parse_mode="Markdown",
            reply_markup=get_asphalt_keyboard(can_work)
        )
    except:
        await callback.message.answer(
            asphalt_text,
            parse_mode="Markdown",
            reply_markup=get_asphalt_keyboard(can_work)
        )
    
    await callback.answer()

@dp.callback_query(F.data == "lay_asphalt")
async def handle_lay_asphalt(callback: CallbackQuery):
    """Укладка асфальта с учетом таблеток Нагирт"""
    user_id = callback.from_user.id
    
    # Получаем данные пользователя
    user = await get_user(user_id)
    if not user:
        await callback.answer("❌ Ошибка: пользователь не найден", show_alert=True)
        return
    
    # Получаем активные эффекты нагирта
    nagirt_effects = await get_active_nagirt_effects(user_id)
    
    # Проверяем время последней укладки
    current_time = datetime.now()
    last_asphalt = user.get('last_asphalt')
    
    if last_asphalt:
        try:
            last_time = datetime.fromisoformat(last_asphalt)
            time_passed = (current_time - last_time).total_seconds()
            
            if time_passed < 30:
                wait_time = 30 - int(time_passed)
                await callback.answer(f"⏳ Отдыхай еще {wait_time} секунд!", show_alert=True)
                return
        except:
            pass  # Если ошибка, продолжаем
    
    # Определяем успех или штраф
    base_success_chance = 0.7
    success_chance = base_success_chance
    
    # НАГИРТ УВЕЛИЧИВАЕТ ШАНС УСПЕХА
    if nagirt_effects["has_active"]:
        # Базовое увеличение шанса
        success_chance = min(0.95, base_success_chance + (nagirt_effects["asphalt_boost"] * 0.25))
        
        # Побочки СНИЖАЮТ шанс значительно
        if nagirt_effects["side_effects"]:
            for effect in nagirt_effects["side_effects"]:
                if effect in ["Нарушение координации", "Дрожь в руках", "Спутанность сознания", "Нарушение зрения"]:
                    success_chance -= 0.2  # Серьезные побочки
                else:
                    success_chance -= 0.08  # Легкие побочки
        
        success_chance = max(0.1, success_chance)  # Минимум 10% шанса
    
    success = random.random() <= success_chance
    
    if success:
        # УСПЕХ
        base_earnings = ECONOMY_SETTINGS["asphalt_earnings"]
        
        # Увеличиваем заработок за счет нагирта
        if nagirt_effects["has_active"]:
            earnings_multiplier = 1.0 + nagirt_effects["asphalt_boost"]
            earnings = int(base_earnings * earnings_multiplier)
            
            # Дополнительный бонус, если нет побочных эффектов
            if not nagirt_effects["side_effects"] and nagirt_effects["asphalt_boost"] > 0:
                earnings = int(earnings * 1.15)  # +15% если нет побочек
        else:
            earnings = base_earnings
        
        # Редкий джекпот (1% шанс)
        if random.random() <= 0.01:
            jackpot_bonus = earnings * 5
            earnings += jackpot_bonus
            jackpot_message = f"\n🎰 ДЖЕКПОТ! +{format_money(jackpot_bonus)}"
        else:
            jackpot_message = ""
        
        async with aiosqlite.connect(DB_NAME) as db:
            # Обновляем баланс и статистику
            await db.execute('''
                UPDATE players 
                SET balance = balance + ?,
                    asphalt_meters = asphalt_meters + 1,
                    asphalt_earned = asphalt_earned + ?,
                    last_asphalt = ?
                WHERE user_id = ?
            ''', (earnings, earnings, current_time.isoformat(), user_id))
            
            # Записываем транзакцию
            await db.execute('''
                INSERT INTO transactions (user_id, type, amount, description)
                VALUES (?, ?, ?, ?)
            ''', (user_id, 'asphalt', earnings, 'Укладка асфальта' + (' + Нагирт' if nagirt_effects["has_active"] else '')))
            
            await db.commit()
        
        # Получаем обновленные данные
        user = await get_user(user_id)
        
        result_text = (
            f"✅ *Асфальт уложен!*\n\n"
            f"🛣️ Уложен 1 метр асфальта\n"
        )
        
        if nagirt_effects["has_active"]:
            result_text += f"💊 *Эффект Нагирта:* +{int(nagirt_effects['asphalt_boost']*100)}%\n"
        
        result_text += (
            f"💰 Заработано: {format_money(earnings)}\n"
            f"📏 Всего метров: {user.get('asphalt_meters', 0):,}\n"
            f"💵 Заработано всего: {format_money(user.get('asphalt_earned', 0))}\n"
            f"💳 Баланс: {format_money(user['balance'])}"
        )
        
        if jackpot_message:
            result_text += jackpot_message
        
        result_text += "\n\nОтличная работа! 🏗️"
        
    else:
        # ШТРАФ
        base_penalty = random.randint(
            ECONOMY_SETTINGS["asphalt_fine_min"],
            ECONOMY_SETTINGS["asphalt_fine_max"]
        )
        
        # НАГИРТ УВЕЛИЧИВАЕТ ШТРАФ
        if nagirt_effects["has_active"]:
            penalty_multiplier = 1.0 + (nagirt_effects["risk_multiplier"] - 1) * 0.7
            
            # Побочные эффекты значительно увеличивают штраф
            if nagirt_effects["side_effects"]:
                for effect in nagirt_effects["side_effects"]:
                    if effect in ["Нарушение координации", "Дрожь в руках", "Спутанность сознания"]:
                        penalty_multiplier += 0.3
                    else:
                        penalty_multiplier += 0.1
            
            penalty = int(base_penalty * penalty_multiplier)
            penalty_reason = f"Штраф за плохую укладку" + (" + Нагирт" if nagirt_effects["has_active"] else "")
        else:
            penalty = base_penalty
            penalty_reason = "Штраф за плохую укладку"
        
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute('''
                UPDATE players 
                SET balance = balance - ?,
                    last_asphalt = ?,
                    total_fines = total_fines + ?
                WHERE user_id = ?
            ''', (penalty, current_time.isoformat(), penalty, user_id))
            
            # Записываем транзакцию
            await db.execute('''
                INSERT INTO transactions (user_id, type, amount, description)
                VALUES (?, ?, ?, ?)
            ''', (user_id, 'penalty', -penalty, penalty_reason))
            
            await db.commit()
        
        user = await get_user(user_id)
        
        result_text = (
            f"⚠️ *ВИТАЛИК ШТРАФУЕТ!*\n\n"
            f"🛣️ Асфальт уложен криво!\n"
        )
        
        if nagirt_effects["has_active"]:
            result_text += f"💊 *Влияние Нагирта:* Шанс успеха {int(success_chance*100)}% (обычно 70%)\n"
        
        result_text += (
            f"💸 Штраф: {format_money(penalty)}\n"
            f"💳 Баланс: {format_money(user['balance'])}\n\n"
            f"Будь внимательнее! ⚠️"
        )
        
        # Если есть побочные эффекты, добавляем сообщение
        if nagirt_effects["side_effects"]:
            result_text += f"\n\n💊 *Побочки:* {', '.join(nagirt_effects['side_effects'])}"
    
    # Отправляем результат
    await callback.message.answer(result_text, parse_mode="Markdown")
    
    # Обновляем меню укладки асфальта
    menu_text = (
        f"🛣️ *Укладка асфальта*\n\n"
        f"💰 Баланс: {format_money(user['balance'])}\n"
        f"📏 Уложено метров: {user.get('asphalt_meters', 0):,}\n"
        f"💵 Заработано: {format_money(user.get('asphalt_earned', 0))}\n"
    )
    
    # Показываем эффекты нагирта
    if nagirt_effects["has_active"]:
        menu_text += f"\n💊 *Нагирт активен:* +{int(nagirt_effects['asphalt_boost']*100)}% к заработку"
        if nagirt_effects["side_effects"]:
            menu_text += f"\n⚠️ Побочки: {', '.join(nagirt_effects['side_effects'][:2])}"
    
    menu_text += f"\n\n⏳ Асфальт сохнет...\nЖди 30 секунд перед следующей укладкой."
    
    try:
        await callback.message.edit_text(
            menu_text,
            parse_mode="Markdown",
            reply_markup=get_asphalt_keyboard(False)
        )
    except:
        await callback.message.answer(
            menu_text,
            parse_mode="Markdown",
            reply_markup=get_asphalt_keyboard(False)
        )
    
    await callback.answer()

@dp.callback_query(F.data == "asphalt_wait")
async def handle_asphalt_wait(callback: CallbackQuery):
    """Проверка времени ожидания"""
    user_id = callback.from_user.id
    user = await get_user(user_id)
    
    if not user:
        await callback.answer("❌ Ошибка", show_alert=True)
        return
    
    last_asphalt = user.get('last_asphalt')
    if last_asphalt:
        try:
            last_time = datetime.fromisoformat(last_asphalt)
            time_passed = (datetime.now() - last_time).total_seconds()
            
            if time_passed < 30:
                wait_time = 30 - int(time_passed)
                await callback.answer(f"⏳ Жди еще {wait_time} секунд!", show_alert=True)
            else:
                await callback.answer("✅ Можно укладывать асфальт!", show_alert=True)
        except:
            await callback.answer("✅ Можно укладывать асфальт!", show_alert=True)
    else:
        await callback.answer("✅ Можно укладывать асфальт!", show_alert=True)

# ==================== ТОЛЕРАНТНОСТЬ К НАГИРТУ ====================
@dp.message(F.text == "💊 Толерантность")
async def handle_tolerance(message: Message):
    user_id = message.from_user.id
    tolerance = await get_nagirt_tolerance(user_id)
    nagirt_effects = await get_active_nagirt_effects(user_id)
    
    tolerance_text = f"📊 *ТОЛЕРАНТНОСТЬ К НАГИРТУ*\n\n"
    
    if tolerance <= 1.1:
        tolerance_text += f"Уровень: 🟢 Низкий ({tolerance:.2f}x)\n"
        tolerance_text += "✅ Эффект таблеток максимальный\n"
        tolerance_text += "⚡ Риск штрафов: нормальный\n"
    elif tolerance <= 1.5:
        tolerance_text += f"Уровень: 🟡 Средний ({tolerance:.2f}x)\n"
        tolerance_text += f"⚠️ Эффект ослаблен на {int((tolerance-1)*100)}%\n"
        tolerance_text += "⚡ Риск штрафов: повышенный\n"
    elif tolerance <= 2.0:
        tolerance_text += f"Уровень: 🟠 Высокий ({tolerance:.2f}x)\n"
        tolerance_text += f"❌ Эффект ослаблен на {int((tolerance-1)*100)}%\n"
        tolerance_text += "🔥 Риск штрафов: очень высокий\n"
    else:
        tolerance_text += f"Уровень: 🔴 Критический ({tolerance:.2f}x)\n"
        tolerance_text += f"💀 Эффект ослаблен на {int((tolerance-1)*100)}%\n"
        tolerance_text += "🚨 Риск увольнения за злоупотребление!\n"
    
    if nagirt_effects["has_active"]:
        tolerance_text += f"\n💊 *Активные таблетки:* +{int(nagirt_effects['salary_boost']*100)}%\n"
        if nagirt_effects["side_effects"]:
            tolerance_text += f"⚠️ Побочные эффекты: {', '.join(nagirt_effects['side_effects'][:3])}\n"
    
    tolerance_text += "\n📈 *Как снизить толерантность:*\n"
    tolerance_text += "• Используйте 💉 Антидот (2500₽)\n"
    tolerance_text += "• Не принимайте таблетки 24 часа\n"
    tolerance_text += "• Чередуйте разные типы Нагирта\n"
    tolerance_text += "• Используйте более слабые таблетки\n\n"
    
    tolerance_text += "⚠️ *Предупреждение:* Высокая толерантность увеличивает риск штрафов и уменьшает эффект таблеток!"
    
    await message.answer(tolerance_text, parse_mode="Markdown")

# ==================== ЭФФЕКТЫ ====================
@dp.message(F.text == "⚡ Эффекты")
async def handle_effects(message: Message):
    user_id = message.from_user.id
    
    effects = await get_active_nagirt_effects(user_id)
    tolerance = await get_nagirt_tolerance(user_id)
    boosts = await get_active_boosts(user_id)
    
    effects_text = "⚡ *АКТИВНЫЕ ЭФФЕКТЫ*\n\n"
    
    if boosts > 0:
        effects_text += f"📈 *Бусты к зарплате:* +{int(boosts*100)}%\n\n"
    else:
        effects_text += "📈 *Бусты к зарплате:* нет\n\n"
    
    if effects["has_active"]:
        effects_text += "💊 *Таблетки Нагирт:*\n"
        
        if effects["salary_boost"] > 0:
            effects_text += f"• Зарплата: +{int(effects['salary_boost']*100)}%\n"
            effects_text += f"  ⚠️ Риск штрафа: +{int((effects['risk_multiplier']-1)*100)}%\n"
        
        if effects["asphalt_boost"] > 0:
            effects_text += f"• Мини-игры: +{int(effects['asphalt_boost']*100)}%\n"
        
        if effects["side_effects"]:
            effects_text += "\n⚠️ *Побочные эффекты:*\n"
            for effect in effects["side_effects"]:
                effects_text += f"• {effect}\n"
        
        effects_text += "\n"
    else:
        effects_text += "💊 *Таблетки Нагирт:* нет\n\n"
    
    effects_text += f"📊 *Толерантность к Нагирту:* x{tolerance:.2f}\n"
    
    if tolerance > 1.5:
        effects_text += "\n🚨 *ВНИМАНИЕ!* Высокая толерантность!\n"
        effects_text += "Эффект таблеток слабеет. Рекомендуется использовать антидот.\n"
    elif tolerance > 1.2:
        effects_text += "\n⚠️ *Предупреждение:* Толерантность повышена.\n"
    
    await message.answer(effects_text, parse_mode="Markdown")

# ==================== ПЕРЕВОДЫ ====================
@dp.message(F.text == "🔁 Перевод")
async def handle_transfer_start(message: Message, state: FSMContext):
    user_id = message.from_user.id
    user = await get_user(user_id)
    
    if not user:
        await message.answer("Сначала зарегистрируйтесь через /start")
        return
    
    all_users = await get_all_users()
    
    if len(all_users) <= 1:
        await message.answer("❌ Нет других сотрудников для перевода")
        return
    
    await message.answer(
        "👥 *Выберите получателя:*\n\n"
        f"Минимальный перевод: {format_money(ECONOMY_SETTINGS['min_transfer'])}\n"
        "Нажмите на сотрудника для перевода:",
        parse_mode="Markdown",
        reply_markup=get_users_keyboard(all_users, user_id, "transfer_to_")
    )
    
    await state.set_state(TransferStates.choosing_recipient)

@dp.callback_query(F.data.startswith("transfer_to_"), TransferStates.choosing_recipient)
async def handle_transfer_recipient(callback: CallbackQuery, state: FSMContext):
    recipient_id = int(callback.data.split("_")[2])
    sender_id = callback.from_user.id
    
    await state.update_data(recipient_id=recipient_id)
    
    recipient = await get_user(recipient_id)
    sender = await get_user(sender_id)
    
    if recipient and sender:
        await callback.message.edit_text(
            f"📤 *Перевод пользователю:*\n\n"
            f"👤 *{recipient['full_name']}*\n"
            f"💰 Баланс: {format_money(recipient['balance'])}\n"
            f"💼 Ваш баланс: {format_money(sender['balance'])}\n\n"
            f"💸 *Введите сумму перевода:*\n"
            f"Минимум: {format_money(ECONOMY_SETTINGS['min_transfer'])}\n"
            f"Максимум: {format_money(sender['balance'])}",
            parse_mode="Markdown"
        )
    
    await state.set_state(TransferStates.entering_amount)
    await callback.answer()

@dp.callback_query(F.data == "cancel_transfer")
async def handle_cancel_transfer(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Перевод отменен")
    await callback.answer()

@dp.message(TransferStates.entering_amount)
async def handle_transfer_amount(message: Message, state: FSMContext):
    user_id = message.from_user.id
    sender = await get_user(user_id)
    
    if not sender:
        await message.answer("❌ Ошибка: отправитель не найден")
        await state.clear()
        return
    
    try:
        amount = int(message.text)
        
        if amount < ECONOMY_SETTINGS["min_transfer"]:
            await message.answer(f"❌ Минимальная сумма перевода - {format_money(ECONOMY_SETTINGS['min_transfer'])}")
            return
        if amount > sender['balance']:
            await message.answer(f"❌ Недостаточно средств! Ваш баланс: {format_money(sender['balance'])}")
            return
        
        data = await state.get_data()
        recipient_id = data.get('recipient_id')
        
        if not recipient_id:
            await message.answer("❌ Ошибка: получатель не выбран")
            await state.clear()
            return
        
        recipient = await get_user(recipient_id)
        if not recipient:
            await message.answer("❌ Ошибка: получатель не найден")
            await state.clear()
            return
        
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute(
                "UPDATE players SET balance = balance - ? WHERE user_id = ?",
                (amount, user_id)
            )
            
            await db.execute(
                "UPDATE players SET balance = balance + ? WHERE user_id = ?",
                (amount, recipient_id)
            )
            
            await db.execute(
                '''INSERT INTO transactions (user_id, type, amount, description)
                   VALUES (?, 'transfer_out', -?, ?)''',
                (user_id, amount, f"Перевод пользователю {recipient['full_name']}")
            )
            
            await db.execute(
                '''INSERT INTO transactions (user_id, type, amount, description)
                   VALUES (?, 'transfer_in', ?, ?)''',
                (recipient_id, amount, f"Перевод от пользователя {sender['full_name']}")
            )
            
            await db.commit()
        
        sender_updated = await get_user(user_id)
        recipient_updated = await get_user(recipient_id)
        
        await message.answer(
            f"✅ *Перевод выполнен успешно!*\n\n"
            f"📤 Отправлено: {format_money(amount)}\n"
            f"👤 Получатель: {recipient['full_name']}\n"
            f"💰 Ваш баланс: {format_money(sender_updated['balance'])}\n\n"
            f"Спасибо за перевод! 💸",
            parse_mode="Markdown",
            reply_markup=get_main_keyboard(user_id)
        )
        
        try:
            await bot.send_message(
                recipient_id,
                f"💰 *Вы получили перевод!*\n\n"
                f"📥 Получено: {format_money(amount)}\n"
                f"👤 Отправитель: {sender['full_name']}\n"
                f"💰 Ваш баланс: {format_money(recipient_updated['balance'])}\n\n"
                f"Поздравляем! 🎉",
                parse_mode="Markdown"
            )
        except:
            pass
        
    except ValueError:
        await message.answer("❌ Пожалуйста, введите число!")
        return
    
    await state.clear()

# ==================== СТАТИСТИКА ====================
@dp.message(F.text == "📊 Статистика")
async def handle_statistics(message: Message):
    user_id = message.from_user.id
    
    try:
        user = await get_user(user_id)
        
        if not user:
            await message.answer("Сначала зарегистрируйтесь через /start")
            return
        
        # Получаем топ игроков
        async with aiosqlite.connect(DB_NAME) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT full_name, balance, total_earned, asphalt_meters FROM players ORDER BY balance DESC LIMIT 10"
            )
            top_players = await cursor.fetchall()
            
            # Получаем общую статистику
            cursor = await db.execute("SELECT COUNT(*) as total, SUM(balance) as total_balance FROM players")
            total_stats = await cursor.fetchone()
            
            # Получаем статистику побочных эффектов
            cursor = await db.execute("SELECT COUNT(*) as pill_users FROM nagirt_pills WHERE expires_at > ?", 
                                     (datetime.now().isoformat(),))
            pill_stats = await cursor.fetchone()
        
        # Формируем текст
        stats_text = (
            f"📊 *КОРПОРАТИВНАЯ СТАТИСТИКА*\n\n"
            f"👤 *Ваш профиль:*\n"
            f"• Имя: {user['full_name']}\n"
            f"• Баланс: {format_money(user['balance'])}\n"
            f"• Заработано всего: {format_money(user.get('total_earned', 0))}\n"
            f"• Штрафов получено: {format_money(user.get('total_fines', 0))}\n"
            f"• Получок: {user.get('salary_count', 0)}\n"
            f"• Уложено асфальта: {user.get('asphalt_meters', 0):,} метров\n"
            f"• Заработано на асфальте: {format_money(user.get('asphalt_earned', 0))}\n\n"
        )
        
        if total_stats:
            total_balance = total_stats['total_balance'] if total_stats['total_balance'] else 0
            stats_text += (
                f"🏢 *Общая статистика:*\n"
                f"• Всего сотрудников: {total_stats['total']}\n"
                f"• Общий капитал: {format_money(total_balance)}\n"
            )
            
            if pill_stats and pill_stats['pill_users'] > 0:
                stats_text += f"• Под Нагиртом: {pill_stats['pill_users']} чел.\n"
            
            stats_text += "\n"
        
        if top_players:
            stats_text += "🏆 *ТОП-10 СОТРУДНИКОВ:*\n"
            for i, player in enumerate(top_players, 1):
                medal = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"][i-1]
                name = player['full_name'][:15] + "..." if len(player['full_name']) > 15 else player['full_name']
                balance = player['balance'] if player['balance'] else 0
                stats_text += f"{medal} {name}: {format_money(balance)}\n"
        else:
            stats_text += "🏆 *Топ игроков пока пуст*\n"
        
        # Добавляем информацию о последних транзакциях
        async with aiosqlite.connect(DB_NAME) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT type, amount, description, created_at FROM transactions WHERE user_id = ? ORDER BY id DESC LIMIT 5",
                (user_id,)
            )
            recent_tx = await cursor.fetchall()
        
        if recent_tx:
            stats_text += "\n📈 *Последние операции:*\n"
            for tx in recent_tx:
                tx_type = tx['type']
                amount = tx['amount']
                desc = tx['description'][:20] + "..." if len(tx['description']) > 20 else tx['description']
                time_str = datetime.fromisoformat(tx['created_at']).strftime("%H:%M")
                
                if tx_type in ['salary', 'transfer_in', 'bonus', 'check']:
                    stats_text += f"🟢 +{format_money(amount)} ({desc}) {time_str}\n"
                else:
                    stats_text += f"🔴 {format_money(amount)} ({desc}) {time_str}\n"
        
        await message.answer(stats_text, parse_mode="Markdown")
        
    except Exception as e:
        logger.error(f"Ошибка в статистике: {e}")
        await message.answer(f"❌ Ошибка при получении статистики. Попробуйте позже.\n\nОшибка: {str(e)[:50]}")

### 2. **Проверьте наличие всех столбцов в базе данных**
Убедитесь, что в таблице `players` есть все необходимые столбцы. Вы можете добавить проверку:

```python
async def check_db_columns():
    """Проверка наличия всех необходимых столбцов"""
    required_columns = [
        'asphalt_meters', 'asphalt_earned', 'total_earned', 
        'total_fines', 'salary_count'
    ]
    
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("PRAGMA table_info(players)")
        columns = await cursor.fetchall()
        existing_columns = [col[1] for col in columns]
        
        for col in required_columns:
            if col not in existing_columns:
                logger.warning(f"Отсутствует столбец {col} в таблице players")
                # Добавляем недостающий столбец
                try:
                    await db.execute(f"ALTER TABLE players ADD COLUMN {col} INTEGER DEFAULT 0")
                    await db.commit()
                    logger.info(f"Добавлен столбец {col}")
                except Exception as e:
                    logger.error(f"Не удалось добавить столбец {col}: {e}")

# ==================== АДМИН-ПАНЕЛЬ ====================
@dp.message(F.text == "👑 Админ-панель")
async def handle_admin_panel(message: Message):
    user_id = message.from_user.id
    
    if user_id != ADMIN_ID:
        await message.answer("⛔ Доступ запрещен!")
        return
    
    admin_text = (
        "👑 *Админ-панель*\n\n"
        "📊 *Статистика:*\n"
        "• /stats - статистика всех игроков\n"
        "• /broadcast - рассылка сообщения\n"
        "• /bonus [ID] [сумма] - выдать бонус игроку\n"
        "• /fine [ID] [сумма] - оштрафовать игрока\n\n"
        "Или используйте кнопки ниже:"
    )
    
    await message.answer(admin_text, parse_mode="Markdown", reply_markup=get_admin_keyboard())

@dp.callback_query(F.data == "admin_broadcast")
async def handle_admin_broadcast(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("⛔ Доступ запрещен!", show_alert=True)
        return
    
    await callback.message.answer(
        "📢 *Режим рассылки*\n\n"
        "Отправьте сообщение для рассылки всем пользователям.\n"
        "❌ Для отмены отправьте /cancel",
        parse_mode="Markdown"
    )
    
    await state.set_state(BroadcastStates.waiting_for_message)
    await callback.answer()

@dp.message(BroadcastStates.waiting_for_message)
async def handle_broadcast_message(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        await state.clear()
        return
    
    if message.text == "/cancel":
        await state.clear()
        await message.answer("❌ Рассылка отменена")
        return
    
    broadcast_text = message.text
    all_users = await get_all_users()
    
    if not all_users:
        await message.answer("❌ Нет пользователей для рассылки")
        await state.clear()
        return
    
    await message.answer(f"⏳ Начинаю рассылку для {len(all_users)} пользователей...")
    
    success_count = 0
    fail_count = 0
    
    for user in all_users:
        try:
            await bot.send_message(
                user['user_id'],
                f"📢 *ОБЪЯВЛЕНИЕ ОТ ВИТАЛИКА*\n\n{broadcast_text}",
                parse_mode="Markdown"
            )
            success_count += 1
            await asyncio.sleep(0.1)
        except:
            fail_count += 1
    
    report = (
        f"📊 *Отчет о рассылке*\n\n"
        f"✅ Успешно отправлено: {success_count}\n"
        f"❌ Не отправлено: {fail_count}\n"
        f"📈 Общий охват: {len(all_users)} пользователей"
    )
    
    await message.answer(report, parse_mode="Markdown")
    await state.clear()

@dp.callback_query(F.data == "admin_fine")
async def handle_admin_fine_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("⛔ Доступ запрещен!", show_alert=True)
        return
    
    all_users = await get_all_users()
    
    await callback.message.answer(
        "⚡ *Штраф пользователя*\n\n"
        "Выберите пользователя для штрафа:",
        reply_markup=get_users_keyboard(all_users, ADMIN_ID, "admin_fine_")
    )
    
    await state.set_state(AdminFineStates.waiting_for_user_id)
    await callback.answer()

@dp.callback_query(F.data.startswith("admin_fine_"), AdminFineStates.waiting_for_user_id)
async def handle_admin_fine_user(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("⛔ Доступ запрещен!", show_alert=True)
        return
    
    user_id = int(callback.data.split("_")[2])
    
    await state.update_data(fine_user_id=user_id)
    
    user = await get_user(user_id)
    if user:
        await callback.message.answer(
            f"⚡ *Штраф пользователя:* {user['full_name']}\n\n"
            f"💰 Текущий баланс: {format_money(user['balance'])}\n\n"
            f"💸 *Введите сумму штрафа:*\n"
            f"Минимум: 1₽\n"
            f"Максимум: {format_money(user['balance'])}",
            parse_mode="Markdown"
        )
    
    await state.set_state(AdminFineStates.waiting_for_amount)
    await callback.answer()

@dp.message(AdminFineStates.waiting_for_amount)
async def handle_admin_fine_amount(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        await state.clear()
        return
    
    try:
        amount = int(message.text)
        
        if amount <= 0:
            await message.answer("❌ Сумма штрафа должна быть положительной!")
            return
        
        data = await state.get_data()
        user_id = data.get('fine_user_id')
        
        if not user_id:
            await message.answer("❌ Ошибка: пользователь не выбран")
            await state.clear()
            return
        
        user = await get_user(user_id)
        if not user:
            await message.answer("❌ Ошибка: пользователь не найден")
            await state.clear()
            return
        
        if amount > user['balance']:
            amount = user['balance']
        
        await update_balance(user_id, -amount, "penalty", f"⚡ Штраф от администратора")
        
        user_updated = await get_user(user_id)
        
        await message.answer(
            f"✅ *Штраф выписан!*\n\n"
            f"👤 Пользователь: {user['full_name']}\n"
            f"💸 Сумма штрафа: {format_money(amount)}\n"
            f"💰 Новый баланс: {format_money(user_updated['balance'])}",
            parse_mode="Markdown"
        )
        
        try:
            await bot.send_message(
                user_id,
                f"⚡ *ВЫ ПОЛУЧИЛИ ШТРАФ ОТ АДМИНИСТРАЦИИ!*\n\n"
                f"💸 Сумма штрафа: {format_money(amount)}\n"
                f"💰 Новый баланс: {format_money(user_updated['balance'])}\n\n"
                f"Соблюдайте правила!",
                parse_mode="Markdown"
            )
        except:
            pass
        
    except ValueError:
        await message.answer("❌ Пожалуйста, введите число!")
        return
    
    await state.clear()

@dp.callback_query(F.data == "admin_bonus")
async def handle_admin_bonus_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("⛔ Доступ запрещен!", show_alert=True)
        return
    
    all_users = await get_all_users()
    
    await callback.message.answer(
        "🎁 *Бонус пользователю*\n\n"
        "Выберите пользователя для бонуса:",
        reply_markup=get_users_keyboard(all_users, ADMIN_ID, "admin_bonus_")
    )
    
    await state.set_state(AdminBonusStates.waiting_for_user_id)
    await callback.answer()

@dp.callback_query(F.data.startswith("admin_bonus_"), AdminBonusStates.waiting_for_user_id)
async def handle_admin_bonus_user(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("⛔ Доступ запрещен!", show_alert=True)
        return
    
    user_id = int(callback.data.split("_")[2])
    
    await state.update_data(bonus_user_id=user_id)
    
    user = await get_user(user_id)
    if user:
        await callback.message.answer(
            f"🎁 *Бонус пользователю:* {user['full_name']}\n\n"
            f"💰 Текущий баланс: {format_money(user['balance'])}\n\n"
            f"💸 *Введите сумму бонуса:*\n"
            f"Минимум: 1₽\n"
            f"Максимум: 1.000.000₽",
            parse_mode="Markdown"
        )
    
    await state.set_state(AdminBonusStates.waiting_for_amount)
    await callback.answer()

@dp.message(AdminBonusStates.waiting_for_amount)
async def handle_admin_bonus_amount(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        await state.clear()
        return
    
    try:
        amount = int(message.text)
        
        if amount <= 0:
            await message.answer("❌ Сумма бонуса должна быть положительной!")
            return
        if amount > 1000000:
            await message.answer("❌ Максимальная сумма бонуса - 1.000.000₽")
            return
        
        data = await state.get_data()
        user_id = data.get('bonus_user_id')
        
        if not user_id:
            await message.answer("❌ Ошибка: пользователь не выбран")
            await state.clear()
            return
        
        user = await get_user(user_id)
        if not user:
            await message.answer("❌ Ошибка: пользователь не найден")
            await state.clear()
            return
        
        await update_balance(user_id, amount, "bonus", f"🎁 Бонус от администратора")
        
        user_updated = await get_user(user_id)
        
        await message.answer(
            f"✅ *Бонус выдан!*\n\n"
            f"👤 Пользователь: {user['full_name']}\n"
            f"💰 Сумма бонуса: {format_money(amount)}\n"
            f"💳 Новый баланс: {format_money(user_updated['balance'])}",
            parse_mode="Markdown"
        )
        
        try:
            await bot.send_message(
                user_id,
                f"🎁 *ВЫ ПОЛУЧИЛИ БОНУС ОТ АДМИНИСТРАЦИИ!*\n\n"
                f"💰 Сумма бонуса: {format_money(amount)}\n"
                f"💳 Новый баланс: {format_money(user_updated['balance'])}\n\n"
                f"Поздравляем! 🎉",
                parse_mode="Markdown"
            )
        except:
            pass
        
    except ValueError:
        await message.answer("❌ Пожалуйста, введите число!")
        return
    
    await state.clear()

@dp.callback_query(F.data == "admin_stats")
async def handle_admin_stats(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("⛔ Доступ запрещен!", show_alert=True)
        return
    
    all_users = await get_all_users()
    
    total_balance = sum(u['balance'] for u in all_users)
    total_players = len(all_users)
    avg_balance = total_balance // total_players if total_players > 0 else 0
    
    stats_text = (
        f"📊 *Статистика системы*\n\n"
        f"👥 Всего игроков: {total_players}\n"
        f"💰 Общий баланс: {format_money(total_balance)}\n"
        f"📈 Средний баланс: {format_money(avg_balance)}\n\n"
        f"🏆 *Топ-10 по балансу:*\n"
    )
    
    sorted_users = sorted(all_users, key=lambda x: x['balance'], reverse=True)[:10]
    
    for i, user in enumerate(sorted_users, 1):
        medal = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"][i-1]
        name = user['full_name'][:15] + "..." if len(user['full_name']) > 15 else user['full_name']
        stats_text += f"{medal} {name}: {format_money(user['balance'])}\n"
    
    await callback.message.answer(stats_text, parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "admin_close")
async def handle_admin_close(callback: CallbackQuery):
    try:
        await callback.message.delete()
    except:
        pass
    await callback.answer()

# ==================== СИСТЕМА АДМИН-ЧЕКОВ ====================
@dp.callback_query(F.data == "admin_checks")
async def handle_admin_checks(callback: CallbackQuery):
    """Меню админ-чеков"""
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("⛔ Доступ запрещен!", show_alert=True)
        return
    
    checks_text = (
        "🧾 *АДМИН: СИСТЕМА ЧЕКОВ*\n\n"
        "Создавайте подарочные чеки-ссылки:\n"
        "• 🎁 **Денежные чеки** - фиксированная сумма\n"
        "• 🎁 **Товарные чеки** - бусты, таблетки, предметы\n\n"
        "Игроки активируют чеки простым переходом по ссылке!\n"
        "Один человек = одна активация ⚠️"
    )
    
    await callback.message.edit_text(checks_text, parse_mode="Markdown", 
                                   reply_markup=get_admin_checks_keyboard())
    await callback.answer()

@dp.callback_query(F.data == "admin_checks_back")
async def handle_admin_checks_back(callback: CallbackQuery):
    """Назад к меню админ-чеков"""
    if callback.from_user.id != ADMIN_ID:
        return
    
    await callback.message.edit_text(
        "🧾 *АДМИН: СИСТЕМА ЧЕКОВ*",
        parse_mode="Markdown",
        reply_markup=get_admin_checks_keyboard()
    )
    await callback.answer()

@dp.callback_query(F.data == "admin_check_money")
async def handle_admin_check_money(callback: CallbackQuery, state: FSMContext):
    """Создание денежного чека"""
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("⛔ Доступ запрещен!", show_alert=True)
        return
    
    await callback.message.edit_text(
        "💰 *Создание денежного чека*\n\n"
        "💸 Введите сумму чека (от 100 до 100000₽):",
        parse_mode="Markdown"
    )
    
    await state.update_data(check_type="money")
    await state.set_state(CheckStates.waiting_for_check_amount)
    await callback.answer()

@dp.callback_query(F.data == "admin_check_item")
async def handle_admin_check_item(callback: CallbackQuery, state: FSMContext):
    """Выбор товара для чека"""
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("⛔ Доступ запрещен!", show_alert=True)
        return
    
    await callback.message.edit_text(
        "🎁 *Создание товарного чека*\n\n"
        "Выберите товар для чека:",
        parse_mode="Markdown",
        reply_markup=get_items_for_checks()
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("check_item_"))
async def handle_check_item_select(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора товара"""
    if callback.from_user.id != ADMIN_ID:
        return
    
    item_id = callback.data[11:]  # check_item_
    
    item = None
    for shop_item in SHOP_ITEMS:
        if shop_item["id"] == item_id:
            item = shop_item
            break
    
    if not item:
        await callback.answer("❌ Товар не найден", show_alert=True)
        return
    
    await state.update_data(check_type="item", item_id=item_id)
    
    await callback.message.edit_text(
        f"🎁 *Создание чека на товар*\n\n"
        f"📦 Товар: {item['name']}\n"
        f"💵 Стоимость в магазине: {format_money(item['price'])}\n\n"
        f"🔢 Введите количество использований чека (1-100):",
        parse_mode="Markdown"
    )
    
    await state.set_state(CheckStates.waiting_for_check_uses)
    await callback.answer()

@dp.message(CheckStates.waiting_for_check_amount)
async def handle_check_amount(message: Message, state: FSMContext):
    """Обработка суммы чека"""
    if message.from_user.id != ADMIN_ID:
        await state.clear()
        return
    
    try:
        amount = int(message.text)
        
        if amount < 100:
            await message.answer("❌ Минимальная сумма - 100₽")
            return
        if amount > 100000:
            await message.answer("❌ Максимальная сумма - 100000₽")
            return
        
        await state.update_data(amount=amount)
        
        await message.answer(
            f"💰 Сумма: {format_money(amount)}\n\n"
            f"🔢 Введите количество использований чека (1-1000):\n"
            f"(Сколько людей смогут активировать этот чек)"
        )
        
        await state.set_state(CheckStates.waiting_for_check_uses)
        
    except ValueError:
        await message.answer("❌ Пожалуйста, введите число!")

@dp.message(CheckStates.waiting_for_check_uses)
async def handle_check_uses(message: Message, state: FSMContext):
    """Обработка количества использований"""
    if message.from_user.id != ADMIN_ID:
        await state.clear()
        return
    
    try:
        max_uses = int(message.text)
        
        if max_uses < 1:
            await message.answer("❌ Минимум 1 использование")
            return
        if max_uses > 1000:
            await message.answer("❌ Максимум 1000 использований")
            return
        
        await state.update_data(max_uses=max_uses)
        
        await message.answer(
            f"🔢 Использований: {max_uses}\n\n"
            f"⏳ Введите срок действия в часах (1-720):\n"
            f"(Через сколько часов чек станет недействительным)"
        )
        
        await state.set_state(CheckStates.waiting_for_check_hours)
        
    except ValueError:
        await message.answer("❌ Пожалуйста, введите число!")

@dp.message(CheckStates.waiting_for_check_hours)
async def handle_check_hours(message: Message, state: FSMContext):
    """Обработка срока действия"""
    if message.from_user.id != ADMIN_ID:
        await state.clear()
        return
    
    try:
        hours = int(message.text)
        
        if hours < 1:
            await message.answer("❌ Минимум 1 час")
            return
        if hours > 720:
            await message.answer("❌ Максимум 720 часов (30 дней)")
            return
        
        await state.update_data(hours=hours)
        
        await message.answer(
            f"⏳ Срок действия: {hours} часов\n\n"
            f"💌 Введите сообщение для получателей (необязательно):\n"
            f"Или отправьте '-' чтобы пропустить"
        )
        
        await state.set_state(CheckStates.waiting_for_check_message)
        
    except ValueError:
        await message.answer("❌ Пожалуйста, введите число!")

@dp.message(CheckStates.waiting_for_check_message)
async def handle_check_message(message: Message, state: FSMContext):
    """Обработка сообщения и создание чека"""
    if message.from_user.id != ADMIN_ID:
        await state.clear()
        return
    
    data = await state.get_data()
    check_type = data.get('check_type', 'money')
    amount = data.get('amount', 0)
    item_id = data.get('item_id')
    max_uses = data.get('max_uses', 1)
    hours = data.get('hours', 24)
    custom_message = message.text if message.text != '-' else ""
    
    # Получаем username бота
    bot_info = await bot.get_me()
    bot_username = bot_info.username
    
    if not bot_username:
        await message.answer(
            "❌ *ОШИБКА!*\n\n"
            "У бота нет username! Без username нельзя создать ссылку.\n"
            "Установите username в @BotFather и перезапустите бота.",
            parse_mode="Markdown"
        )
        await state.clear()
        return
    
    check_id = await create_gift_check(
        creator_id=ADMIN_ID,
        check_type=check_type,
        amount=amount,
        item_id=item_id,
        max_uses=max_uses,
        hours=hours,
        message=custom_message
    )
    
    # Создаем ссылку
    check_link = f"https://t.me/{bot_username}?start={check_id}"
    
    if check_type == 'money':
        check_info = f"💰 *Денежный чек на {format_money(amount)}*"
        reward_text = f"{format_money(amount)}"
    else:
        item_name = "Неизвестный товар"
        for shop_item in SHOP_ITEMS:
            if shop_item["id"] == item_id:
                item_name = shop_item['name']
                break
        check_info = f"🎁 *Товарный чек на {item_name}*"
        reward_text = item_name
    
    expires_at = datetime.now() + timedelta(hours=hours)
    
    check_text = (
        f"✅ *ЧЕК УСПЕШНО СОЗДАН!*\n\n"
        f"{check_info}\n"
        f"🔢 Использований: {max_uses}\n"
        f"⏳ Действует до: {expires_at.strftime('%d.%m.%Y %H:%M')}\n\n"
    )
    
    if custom_message:
        check_text += f"💌 Сообщение: {custom_message}\n\n"
    
    check_text += (
        f"🔗 *ССЫЛКА ДЛЯ АКТИВАЦИИ:*\n"
        f"`{check_link}`\n\n"
        f"📋 *ИНСТРУКЦИЯ:*\n"
        f"1. Отправьте эту ссылку в чат\n"
        f"2. Игроки переходят по ссылке\n"
        f"3. Первые {max_uses} человек получат {reward_text}\n"
        f"4. Остальные увидят, что чек уже использован\n\n"
        f"🆔 Код чека: `{check_id}`"
    )
    
    buttons = [
        [InlineKeyboardButton(text="📋 Отправить ссылку в чат", callback_data=f"send_link_{check_id}")],
        [InlineKeyboardButton(text="🧾 К списку чеков", callback_data="admin_checks_list")]
    ]
    
    await message.answer(check_text, parse_mode="Markdown", 
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    
    await state.clear()

@dp.callback_query(F.data.startswith("send_link_"))
async def handle_send_link(callback: CallbackQuery):
    """Отправка ссылки на чек в чат"""
    if callback.from_user.id != ADMIN_ID:
        return
    
    check_id = callback.data[10:]  # send_link_
    
    # Получаем информацию о чеке
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT check_type, amount, item_id, max_uses, used_count FROM gift_checks WHERE check_id = ?",
            (check_id,)
        )
        check = await cursor.fetchone()
    
    if not check:
        await callback.answer("❌ Чек не найден", show_alert=True)
        return
    
    check = dict(check)
    
    # Получаем username бота
    bot_info = await bot.get_me()
    bot_username = bot_info.username
    
    if not bot_username:
        await callback.answer("❌ У бота нет username!", show_alert=True)
        return
    
    # Создаем ссылку
    check_link = f"https://t.me/{bot_username}?start={check_id}"
    
    remaining_uses = check['max_uses'] - check['used_count']
    
    if check['check_type'] == 'money':
        reward_text = f"{format_money(check['amount'])}"
        message_text = (
            f"🎁 *ПОДАРОЧНЫЙ ЧЕК ОТ АДМИНИСТРАЦИИ!*\n\n"
            f"💰 Сумма: {reward_text}\n"
            f"👥 Доступно использований: {remaining_uses}/{check['max_uses']}\n\n"
            f"🔗 *Активировать:* {check_link}\n\n"
            f"📱 *Как использовать:*\n"
            f"1. Нажмите на ссылку выше\n"
            f"2. Нажмите START в боте\n"
            f"3. Получите деньги на баланс!"
        )
    else:
        item_name = "Неизвестный товар"
        for shop_item in SHOP_ITEMS:
            if shop_item["id"] == check['item_id']:
                item_name = shop_item['name']
                break
        message_text = (
            f"🎁 *ПОДАРОЧНЫЙ ЧЕК ОТ АДМИНИСТРАЦИИ!*\n\n"
            f"📦 Награда: {item_name}\n"
            f"👥 Доступно использований: {remaining_uses}/{check['max_uses']}\n\n"
            f"🔗 *Активировать:* {check_link}\n\n"
            f"📱 *Как использовать:*\n"
            f"1. Нажмите на ссылку выше\n"
            f"2. Нажмите START в боте\n"
            f"3. Получите предмет в инвентарь!"
        )
    
    # Отправляем сообщение в чат
    await callback.message.answer(message_text, parse_mode="Markdown")
    await callback.answer("✅ Ссылка отправлена в чат!")

@dp.callback_query(F.data == "admin_checks_list")
async def handle_admin_checks_list(callback: CallbackQuery):
    """Список активных чеков"""
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("⛔ Доступ запрещен!", show_alert=True)
        return
    
    active_checks = await get_active_checks()
    
    if not active_checks:
        await callback.message.edit_text(
            "📭 *Активных чеков нет*\n\n"
            "Создайте первый чек через меню!",
            parse_mode="Markdown",
            reply_markup=get_admin_checks_keyboard()
        )
        await callback.answer()
        return
    
    checks_text = "🧾 *АКТИВНЫЕ ЧЕКИ:*\n\n"
    total_amount = 0
    
    for i, check in enumerate(active_checks[:10], 1):
        expires_at = datetime.fromisoformat(check['expires_at'])
        time_left = expires_at - datetime.now()
        hours_left = int(time_left.total_seconds() // 3600)
        
        if check['check_type'] == 'money':
            check_info = f"💰 {format_money(check['amount'])}"
            total_amount += check['amount'] * (check['max_uses'] - check['used_count'])
        else:
            check_info = f"🎁 {check['item_id']}"
        
        checks_text += (
            f"{i}. `{check['check_id'][:12]}...`\n"
            f"   {check_info} | 👥 {check['used_count']}/{check['max_uses']}\n"
            f"   ⏳ {hours_left}ч | 📅 {expires_at.strftime('%d.%m %H:%M')}\n"
        )
    
    checks_text += f"\n📊 *Итого в обороте:* {format_money(total_amount)}"
    
    buttons = []
    for i, check in enumerate(active_checks[:5], 1):
        buttons.append([InlineKeyboardButton(
            text=f"📊 Статистика {check['check_id'][:8]}...",
            callback_data=f"check_stats_{check['check_id']}"
        )])
    
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_checks_back")])
    
    await callback.message.edit_text(
        checks_text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("check_stats_"))
async def handle_check_stats(callback: CallbackQuery):
    """Статистика по конкретному чеку"""
    if callback.from_user.id != ADMIN_ID:
        return
    
    check_id = callback.data[12:]  # check_stats_
    stats = await get_check_stats(check_id)
    
    if not stats:
        await callback.answer("❌ Чек не найден", show_alert=True)
        return
    
    expires_at = datetime.fromisoformat(stats['expires_at'])
    created_at = datetime.fromisoformat(stats['created_at'])
    
    if stats['check_type'] == 'money':
        check_info = f"💰 *Денежный чек на {format_money(stats['amount'])}*"
    else:
        check_info = f"🎁 *Товарный чек ({stats['item_id']})*"
    
    # Получаем username бота
    bot_info = await bot.get_me()
    bot_username = bot_info.username
    
    if bot_username:
        check_link = f"https://t.me/{bot_username}?start={check_id}"
        link_text = f"🔗 *Ссылка:* `{check_link}`"
    else:
        link_text = "❌ *У бота нет username!*"
    
    stats_text = (
        f"📊 *СТАТИСТИКА ЧЕКА*\n\n"
        f"{check_info}\n"
        f"👤 Создатель: {stats.get('creator_name', 'Админ')}\n"
        f"📅 Создан: {created_at.strftime('%d.%m.%Y %H:%M')}\n"
        f"⏳ Действует до: {expires_at.strftime('%d.%m.%Y %H:%M')}\n"
        f"👥 Использовано: {stats['used_count']}/{stats['max_uses']}\n"
        f"{link_text}\n"
    )
    
    if stats.get('custom_message'):
        stats_text += f"💌 Сообщение: {stats['custom_message']}\n"
    
    if stats['activations']:
        stats_text += f"\n🎯 *Активировали ({len(stats['activations'])}):*\n"
        for i, act in enumerate(stats['activations'][:5], 1):
            act_time = datetime.fromisoformat(act['activated_at'])
            user_name = act.get('user_name', f'ID:{act["user_id"]}')
            stats_text += f"{i}. {user_name} - {act_time.strftime('%H:%M')}\n"
        
        if len(stats['activations']) > 5:
            stats_text += f"... и ещё {len(stats['activations']) - 5} человек\n"
    else:
        stats_text += "\n🎯 Пока никто не активировал этот чек"
    
    buttons = [
        [InlineKeyboardButton(text="📤 Отправить ссылку", callback_data=f"send_link_{check_id}")],
        [InlineKeyboardButton(text="🔙 К списку чеков", callback_data="admin_checks_list")],
        [InlineKeyboardButton(text="❌ Деактивировать чек", callback_data=f"check_deactivate_{check_id}")]
    ]
    
    await callback.message.edit_text(
        stats_text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("check_deactivate_"))
async def handle_check_deactivate(callback: CallbackQuery):
    """Деактивация чека"""
    if callback.from_user.id != ADMIN_ID:
        return
    
    check_id = callback.data[16:]  # check_deactivate_
    await deactivate_check(check_id)
    
    await callback.answer(f"✅ Чек {check_id} деактивирован!", show_alert=True)
    
    await handle_admin_checks_list(callback)

@dp.callback_query(F.data == "admin_back")
async def handle_admin_back(callback: CallbackQuery):
    """Назад к админ-панели"""
    if callback.from_user.id != ADMIN_ID:
        return
    
    admin_text = (
        "👑 *Админ-панель*\n\n"
        "📊 *Статистика:*\n"
        "• /stats - статистика всех игроков\n"
        "• /broadcast - рассылка сообщения\n"
        "• /bonus [ID] [сумма] - выдать бонус игроку\n"
        "• /fine [ID] [сумма] - оштрафовать игроку\n\n"
        "Или используйте кнопки ниже:"
    )
    
    await callback.message.edit_text(
        admin_text,
        parse_mode="Markdown",
        reply_markup=get_admin_keyboard()
    )
    await callback.answer()

# ==================== ОСТАЛЬНЫЕ ОБРАБОТЧИКИ ====================
@dp.callback_query(F.data == "back_to_main")
async def handle_back_to_main(callback: CallbackQuery):
    try:
        await callback.message.delete()
    except:
        pass
    await callback.message.answer("Главное меню:", reply_markup=get_main_keyboard(callback.from_user.id))
    await callback.answer()

@dp.callback_query(F.data == "back_to_games")
async def handle_back_to_games(callback: CallbackQuery):
    try:
        await callback.message.delete()
    except:
        pass
    await callback.message.answer("🎮 Мини-игры:", reply_markup=get_minigames_keyboard())
    await callback.answer()

@dp.callback_query(F.data == "shop_close")
async def handle_shop_close(callback: CallbackQuery):
    try:
        await callback.message.delete()
    except:
        pass
    await callback.answer()

# ==================== СЛУЧАЙНЫЕ ШТРАФЫ ====================
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

# ==================== КОМАНДЫ АДМИНИСТРАТОРА ====================
@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    
    all_users = await get_all_users()
    
    total_balance = sum(u['balance'] for u in all_users)
    total_players = len(all_users)
    avg_balance = total_balance // total_players if total_players > 0 else 0
    
    stats_text = (
        f"📊 *Статистика системы (команда)*\n\n"
        f"👥 Всего игроков: {total_players}\n"
        f"💰 Общий баланс: {format_money(total_balance)}\n"
        f"📈 Средний баланс: {format_money(avg_balance)}\n\n"
    )
    
    if all_users:
        richest = max(all_users, key=lambda x: x['balance'])
        poorest = min(all_users, key=lambda x: x['balance'])
        
        stats_text += (
            f"🏆 Самый богатый: {richest['full_name']} ({format_money(richest['balance'])})\n"
            f"😢 Самый бедный: {poorest['full_name']} ({format_money(poorest['balance'])})\n"
        )
    
    await message.answer(stats_text, parse_mode="Markdown")

# ==================== ЗАПУСК БОТА ====================
async def on_startup():
    await init_db()
    await check_db_columns()
    
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

async def main():
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    await dp.start_polling(bot, skip_updates=True)

if __name__ == "__main__":
    asyncio.run(main())
