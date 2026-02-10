"""
Telegram бот "Виталик Штрафующий" - ИСПРАВЛЕННАЯ ВЕРСИЯ
Всё работает: админка, переводы, укладка асфальта
"""

import asyncio
import logging
import random
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
BOT_TOKEN = "8451168327:AAGQffadqqBg3pZNQnjctVxH-dUgXsovTr4"  # !!! ЗАМЕНИТЕ НА ВАШ ТОКЕН !!!
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
DB_NAME = "vitalik_bot_v3.db"

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

# ==================== СИСТЕМА НАГИРТА ====================
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
        "fine_protection": 0.0,
        "side_effects": [],
        "has_active": len(rows) > 0
    }
    
    for row in rows:
        pill_type, strength, side_effects = row
        if pill_type in ["nagirt_pro", "nagirt_extreme"]:
            effects["salary_boost"] += strength
            effects["asphalt_boost"] += strength
        elif pill_type == "nagirt_light":
            effects["asphalt_boost"] += strength
        
        if pill_type == "nagirt_extreme":
            effects["fine_protection"] += 0.5
        
        if side_effects:
            effects["side_effects"].append(side_effects)
    
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
        [KeyboardButton(text="📊 Статистика"), KeyboardButton(text="💊 Эффекты")]
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
        buttons = [[InlineKeyboardButton(text="🛣️ Уложить асфальт (1 метр)", callback_data="lay_asphalt")]]
    else:
        buttons = [[InlineKeyboardButton(text="⏳ Асфальт сохнет...", callback_data="asphalt_wait")]]
    buttons.append([InlineKeyboardButton(text="🔙 Назад в игры", callback_data="back_to_games")])
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
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="❌ Закрыть", callback_data="admin_close")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# ==================== ОБРАБОТЧИКИ КОМАНД ====================
@dp.message(CommandStart())
async def cmd_start(message: Message):
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
    
    base_salary = random.randint(
        ECONOMY_SETTINGS["salary_min"], 
        ECONOMY_SETTINGS["salary_max"]
    )
    
    pill_fine = 0
    if nagirt_effects["has_active"] and random.random() <= ECONOMY_SETTINGS["fine_chance"]:
        pill_fine = random.randint(
            int(base_salary * 0.1),
            int(base_salary * 0.3)
        )
        fine_reasons = [
            "Обнаружены следы Нагирта в крови!",
            "Работа в состоянии измененного сознания!",
            "Нарушение техники безопасности из-за таблеток!"
        ]
        await update_balance(user_id, -pill_fine, "penalty", f"💊 {random.choice(fine_reasons)}")
    
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
        pill_comments = ["Таблетки не заменят профессионализм!", "Осторожнее с Нагиртом!", "Лекарства должны помогать, а не мешать работе!"]
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
        
        side_effects = ""
        if random.randint(1, 100) <= item.get("side_effect_chance", 0):
            side_effects = random.choice(["Головокружение", "Тошнота", "Слабость", "Дрожь в руках", "Нарушение координации"])
        
        await add_nagirt_pill(user_id, item["id"], real_effect, item["hours"], side_effects)
        await update_nagirt_tolerance(user_id)
        
        bonus_text = f"💊 Таблетка принята! Эффект: +{int(real_effect*100)}% на {item['hours']}ч"
        if side_effects:
            bonus_text += f"\n⚠️ Побочный эффект: {side_effects}"
        
        if tolerance > 1.2:
            bonus_text += f"\n📉 Толерантность: +{int((tolerance-1)*100)}% (эффект ослаблен)"
    
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

# ==================== УКЛАДКА АСФАЛЬТА (ИСПРАВЛЕНА) ====================
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
        f"• Шанс успеха: 70%\n"
        f"• Время работы: 30 секунд\n\n"
        f"💰 Ваш баланс: {format_money(user['balance'])}"
    )
    
    await message.answer(games_text, parse_mode="Markdown", reply_markup=get_minigames_keyboard())

@dp.callback_query(F.data == "game_asphalt")
async def handle_game_asphalt(callback: CallbackQuery):
    user_id = callback.from_user.id
    user = await get_user(user_id)
    
    if not user:
        await callback.answer("❌ Ошибка: пользователь не найден", show_alert=True)
        return
    
    can_work = True
    wait_seconds = 0
    
    last_asphalt = user.get('last_asphalt')
    if last_asphalt:
        last_asphalt_time = datetime.fromisoformat(last_asphalt)
        time_since_last = datetime.now() - last_asphalt_time
        
        if time_since_last.total_seconds() < 30:
            can_work = False
            wait_seconds = 30 - int(time_since_last.total_seconds())
    
    asphalt_text = (
        f"🛣️ *Укладка асфальта*\n\n"
        f"💰 Баланс: {format_money(user['balance'])}\n"
        f"📏 Уложено метров: {user.get('asphalt_meters', 0):,}\n"
        f"💵 Заработано: {format_money(user.get('asphalt_earned', 0))}\n\n"
    )
    
    if can_work:
        nagirt_effects = await get_active_nagirt_effects(user_id)
        if nagirt_effects["asphalt_boost"] > 0:
            asphalt_text += f"💊 *Буст от Нагирта:* +{int(nagirt_effects['asphalt_boost']*100)}%\n\n"
        
        asphalt_text += (
            f"*Расценки:*\n"
            f"• Успешная укладка: {format_money(ECONOMY_SETTINGS['asphalt_earnings'])}\n"
            f"• Штраф за брак: {format_money(ECONOMY_SETTINGS['asphalt_fine_min'])}-{format_money(ECONOMY_SETTINGS['asphalt_fine_max'])}\n"
            f"• Шанс успеха: 70%\n"
            f"• Время работы: 30 секунд\n\n"
            f"Нажми кнопку для работы 👇"
        )
    else:
        wait_time = format_time(wait_seconds)
        asphalt_text += f"⏳ *Перерыв для отдыха*\n\nПодожди еще *{wait_time}* (мм:сс)\n\nРаботать без отдыха опасно!"
    
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
    user_id = callback.from_user.id
    user = await get_user(user_id)
    
    if not user:
        await callback.answer("❌ Ошибка: пользователь не найден", show_alert=True)
        return
    
    current_time = datetime.now()
    
    if user.get('last_asphalt'):
        last_asphalt_time = datetime.fromisoformat(user['last_asphalt'])
        time_since_last = (current_time - last_asphalt_time).total_seconds()
        
        if time_since_last < 30:
            wait_seconds = 30 - int(time_since_last)
            wait_time = format_time(wait_seconds)
            await callback.answer(f"⏳ Отдыхай еще {wait_time}!", show_alert=True)
            return
    
    nagirt_effects = await get_active_nagirt_effects(user_id)
    asphalt_boost = nagirt_effects["asphalt_boost"]
    
    if random.random() <= 0.7:
        base_earnings = ECONOMY_SETTINGS["asphalt_earnings"]
        earnings = int(base_earnings * (1 + asphalt_boost))
        
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute(
                '''UPDATE players 
                   SET balance = balance + ?, 
                       asphalt_meters = asphalt_meters + 1,
                       asphalt_earned = asphalt_earned + ?,
                       last_asphalt = ?
                   WHERE user_id = ?''',
                (earnings, earnings, current_time.isoformat(), user_id)
            )
            await db.execute(
                '''INSERT INTO transactions (user_id, type, amount, description)
                   VALUES (?, 'asphalt', ?, 'Укладка асфальта')''',
                (user_id, earnings, "Укладка асфальта")
            )
            await db.commit()
        
        user = await get_user(user_id)
        
        result_text = (
            f"✅ *Работа выполнена!*\n\n"
            f"🛣️ Уложен 1 метр асфальта\n"
        )
        
        if asphalt_boost > 0:
            result_text += f"💊 Буст от Нагирта: +{int(asphalt_boost*100)}%\n"
        
        result_text += (
            f"💰 Заработано: {format_money(earnings)}\n"
            f"📏 Всего уложено: {user.get('asphalt_meters', 0):,} метров\n"
            f"💵 Заработано на асфальте: {format_money(user.get('asphalt_earned', 0))}\n"
            f"💳 Новый баланс: {format_money(user['balance'])}\n\n"
            f"Отличная работа! 🏗️"
        )
    else:
        penalty = random.randint(
            ECONOMY_SETTINGS["asphalt_fine_min"],
            ECONOMY_SETTINGS["asphalt_fine_max"]
        )
        
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute(
                '''UPDATE players 
                   SET balance = balance - ?,
                       last_asphalt = ?,
                       last_penalty = ?,
                       total_fines = total_fines + ?
                   WHERE user_id = ?''',
                (penalty, current_time.isoformat(), current_time.isoformat(), penalty, user_id)
            )
            await db.execute(
                '''INSERT INTO transactions (user_id, type, amount, description)
                   VALUES (?, 'penalty', -?, 'Штраф за бракованный асфальт')''',
                (user_id, penalty, "Штраф за бракованный асфальт")
            )
            await db.commit()
        
        user = await get_user(user_id)
        
        penalty_reasons = [
            "Бракованный асфальт! Придется переделывать.",
            "Нарушена технология укладки!",
            "Работа выполнена некачественно!",
            "Обнаружены пустоты в покрытии!"
        ]
        
        result_text = (
            f"⚠️ *НАРУШЕНИЕ ТЕХНОЛОГИИ!*\n\n"
            f"🛣️ {random.choice(penalty_reasons)}\n"
            f"💸 Штраф: {format_money(penalty)}\n"
            f"💳 Новый баланс: {format_money(user['balance'])}\n\n"
            f"Будь внимательнее к качеству работы! ⚠️"
        )
    
    await callback.message.answer(result_text, parse_mode="Markdown")
    
    wait_text = "⏳ *Отдых после работы*\n\nПодожди 30 секунд перед следующей укладкой."
    
    try:
        await callback.message.edit_text(
            wait_text,
            parse_mode="Markdown",
            reply_markup=get_asphalt_keyboard(False)
        )
    except:
        await callback.message.answer(
            wait_text,
            parse_mode="Markdown",
            reply_markup=get_asphalt_keyboard(False)
        )
    
    await callback.answer()

@dp.callback_query(F.data == "asphalt_wait")
async def handle_asphalt_wait(callback: CallbackQuery):
    user_id = callback.from_user.id
    user = await get_user(user_id)
    
    if not user:
        await callback.answer("❌ Ошибка: пользователь не найден", show_alert=True)
        return
    
    if user.get('last_asphalt'):
        last_time = datetime.fromisoformat(user['last_asphalt'])
        time_passed = (datetime.now() - last_time).total_seconds()
        
        if time_passed < 30:
            wait_seconds = 30 - int(time_passed)
            wait_time = format_time(wait_seconds)
            await callback.answer(f"⏳ Подожди еще {wait_time}!", show_alert=True)
        else:
            await callback.answer("✅ Асфальт высох, можно укладывать!", show_alert=True)
    else:
        await callback.answer("✅ Можно начинать укладку!", show_alert=True)

# ==================== ПЕРЕВОДЫ (ИСПРАВЛЕНЫ) ====================
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

# ==================== АДМИН-ПАНЕЛЬ (ИСПРАВЛЕНА) ====================
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

# ==================== РУЛЕТКА ====================
@dp.callback_query(F.data == "game_roulette")
async def handle_game_roulette_start(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    user = await get_user(user_id)
    
    if not user:
        await callback.answer("❌ Ошибка: пользователь не найден", show_alert=True)
        return
    
    await callback.message.edit_text(
        f"🎰 *КОРПОРАТИВНАЯ РУЛЕТКА*\n\n"
        f"💰 Ваш баланс: {format_money(user['balance'])}\n"
        f"🎯 Шанс выигрыша: {int(ECONOMY_SETTINGS['roulette_win_chance']*100)}%\n"
        f"💰 Выигрыш: x2 от ставки\n\n"
        f"💸 *Введите сумму ставки:*\n"
        f"Минимум: {format_money(ECONOMY_SETTINGS['roulette_min_bet'])}\n"
        f"Максимум: {format_money(min(ECONOMY_SETTINGS['roulette_max_bet'], user['balance']))}",
        parse_mode="Markdown"
    )
    
    await state.set_state(RouletteStates.waiting_for_bet)
    await callback.answer()

@dp.message(RouletteStates.waiting_for_bet)
async def handle_roulette_bet(message: Message, state: FSMContext):
    user_id = message.from_user.id
    user = await get_user(user_id)
    
    if not user:
        await message.answer("Сначала зарегистрируйтесь через /start")
        await state.clear()
        return
    
    try:
        bet = int(message.text)
        
        if bet < ECONOMY_SETTINGS["roulette_min_bet"]:
            await message.answer(f"❌ Минимальная ставка - {format_money(ECONOMY_SETTINGS['roulette_min_bet'])}")
            return
        if bet > user['balance']:
            await message.answer(f"❌ Недостаточно средств! Доступно: {format_money(user['balance'])}")
            return
        if bet > ECONOMY_SETTINGS["roulette_max_bet"]:
            await message.answer(f"❌ Максимальная ставка - {format_money(ECONOMY_SETTINGS['roulette_max_bet'])}")
            return
        
        win = random.random() <= ECONOMY_SETTINGS["roulette_win_chance"]
        colors = ["красное", "черное"]
        chosen_color = random.choice(colors)
        
        async with aiosqlite.connect(DB_NAME) as db:
            if win:
                win_amount = bet * 2
                await db.execute(
                    "UPDATE players SET balance = balance + ? WHERE user_id = ?",
                    (bet, user_id)  # +bet потому что ставка уже включена
                )
                
                result_text = (
                    f"🎰 *РУЛЕТКА*\n\n"
                    f"🎉 *ПОБЕДА!*\n\n"
                    f"🎲 Выпало: *{chosen_color}*\n"
                    f"💰 Ставка: {format_money(bet)}\n"
                    f"🏆 Выигрыш: {format_money(win_amount)}\n"
                    f"💎 Чистая прибыль: {format_money(bet)}\n"
                    f"💰 Новый баланс: {format_money(user['balance'] + bet)}\n\n"
                    f"Везет же некоторым! 🎰"
                )
            else:
                await db.execute(
                    "UPDATE players SET balance = balance - ? WHERE user_id = ?",
                    (bet, user_id)
                )
                
                result_text = (
                    f"🎰 *РУЛЕТКА*\n\n"
                    f"💥 *ПРОИГРЫШ!*\n\n"
                    f"🎲 Выпало: *{chosen_color}*\n"
                    f"💰 Ставка: {format_money(bet)}\n"
                    f"📉 Потеряно: {format_money(bet)}\n"
                    f"💰 Новый баланс: {format_money(user['balance'] - bet)}\n\n"
                    f"Не повезло... 🍀"
                )
            
            await db.execute(
                '''INSERT INTO transactions (user_id, type, amount, description)
                   VALUES (?, 'roulette', ?, ?)''',
                (user_id, bet if win else -bet, 
                 f"Рулетка: {'выигрыш' if win else 'проигрыш'}")
            )
            
            await db.commit()
        
        await message.answer(result_text, parse_mode="Markdown", reply_markup=get_minigames_keyboard())
        
    except ValueError:
        await message.answer("❌ Пожалуйста, введите число!")
    finally:
        await state.clear()

# ==================== ОСТАЛЬНЫЕ ОБРАБОТЧИКИ ====================
@dp.message(F.text == "💊 Эффекты")
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
            effects_text += f"  ⚠️ Риск штрафа: {ECONOMY_SETTINGS['fine_chance']*100}%\n"
        
        if effects["asphalt_boost"] > 0:
            effects_text += f"• Мини-игры: +{int(effects['asphalt_boost']*100)}%\n"
        
        if effects["side_effects"]:
            effects_text += "\n⚠️ *Побочные эффекты:*\n"
            for effect in effects["side_effects"]:
                effects_text += f"• {effect}\n"
        
        effects_text += "\n"
    else:
        effects_text += "💊 *Таблетки Нагирт:* нет\n\n"
    
    effects_text += f"📊 *Толерантность к Нагирту:* +{int((tolerance-1)*100)}%\n"
    
    if tolerance > 1.5:
        effects_text += "\n🚨 *ВНИМАНИЕ!* Высокая толерантность!\n"
        effects_text += "Эффект таблеток слабеет. Рекомендуется использовать антидот.\n"
    elif tolerance > 1.2:
        effects_text += "\n⚠️ *Предупреждение:* Толерантность повышена.\n"
    
    await message.answer(effects_text, parse_mode="Markdown")

@dp.message(F.text == "📊 Статистика")
async def handle_statistics(message: Message):
    user_id = message.from_user.id
    user = await get_user(user_id)
    
    if not user:
        await message.answer("Сначала зарегистрируйтесь через /start")
        return
    
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT full_name, balance, total_earned, asphalt_meters FROM players ORDER BY balance DESC LIMIT 10"
        )
        top_players = await cursor.fetchall()
        
        cursor = await db.execute("SELECT COUNT(*) as total, SUM(balance) as total_balance FROM players")
        total_stats = await cursor.fetchone()
    
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
        stats_text += (
            f"🏢 *Общая статистика:*\n"
            f"• Всего сотрудников: {total_stats['total']}\n"
            f"• Общий капитал: {format_money(total_stats['total_balance'] or 0)}\n\n"
        )
    
    if top_players:
        stats_text += "🏆 *ТОП-10 СОТРУДНИКОВ:*\n"
        for i, player in enumerate(top_players, 1):
            medal = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"][i-1]
            name = player['full_name'][:15] + "..." if len(player['full_name']) > 15 else player['full_name']
            stats_text += f"{medal} {name}: {format_money(player['balance'])}\n"
    
    await message.answer(stats_text, parse_mode="Markdown")

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
    asyncio.create_task(penalty_scheduler())
    logger.info("✅ Бот запущен! Все функции работают.")

async def on_shutdown():
    logger.info("🛑 Бот останавливается...")

async def main():
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    await dp.start_polling(bot, skip_updates=True)

if __name__ == "__main__":
    asyncio.run(main())
