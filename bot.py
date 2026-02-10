"""
Telegram бот "Виталик Штрафующий" - УЛУЧШЕННАЯ ЭКОНОМИКА
С реалистичными зарплатами, ценами и секундами в таймерах
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
ADMIN_ID = 5775839902  # Ваш Telegram ID

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# ==================== НАСТРОЙКИ ЭКОНОМИКИ ====================
ECONOMY_SETTINGS = {
    "start_balance": 2500,  # Стартовый баланс
    "salary_min": 550,  # Минимальная зарплата
    "salary_max": 2500,  # Максимальная зарплата
    "salary_interval": 300,  # 5 минут между получками (300 секунд)
    "fine_chance": 0.35,  # 25% шанс штрафа в получке с таблетками
    "random_fine_min": 220,  # Минимальный случайный штраф
    "random_fine_max": 1000,  # Максимальный случайный штраф
    "asphalt_earnings": 60,  # Заработок за 1 метр асфальта
    "asphalt_fine_min": 100,  # Минимальный штраф за асфальт
    "asphalt_fine_max": 400,  # Максимальный штраф за асфальт
    "roulette_min_bet": 100,  # Минимальная ставка в рулетке
    "roulette_max_bet": 5000,  # Максимальная ставка в рулетке
    "roulette_win_chance": 0.42,  # 42% шанс выигрыша
    "min_transfer": 100,  # Минимальный перевод
    "random_fine_interval_min": 1200,  # 20 минут минимальный интервал штрафов
    "random_fine_interval_max": 1800,  # 30 минут максимальный интервал штрафов
}

# ==================== БАЗА ДАННЫХ ====================
DB_NAME = "vitalik_bot_v2.db"

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        # Таблица игроков
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
        
        # Транзакции
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
        
        # Покупки
        await db.execute('''
            CREATE TABLE IF NOT EXISTS purchases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                item_name TEXT,
                price INTEGER,
                purchased_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Бусты (от обычных товаров)
        await db.execute('''
            CREATE TABLE IF NOT EXISTS boosts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                boost_type TEXT,
                boost_value REAL,
                expires_at TIMESTAMP
            )
        ''')
        
        # Таблетки Нагирт
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
        
        # Толерантность к Нагирту
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
    """Добавление таблетки Нагирт"""
    expires_at = datetime.now() + timedelta(hours=hours)
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            '''INSERT INTO nagirt_pills (user_id, pill_type, effect_strength, expires_at, side_effects)
               VALUES (?, ?, ?, ?, ?)''',
            (user_id, pill_type, effect, expires_at.isoformat(), side_effects)
        )
        await db.commit()

async def get_active_nagirt_effects(user_id: int) -> Dict[str, Any]:
    """Получение активных эффектов таблеток"""
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
    """Получение толерантности к Нагирту"""
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT tolerance FROM nagirt_tolerance WHERE user_id = ?", (user_id,))
        result = await cursor.fetchone()
        return result[0] if result else 1.0

async def update_nagirt_tolerance(user_id: int, increase: float = 0.1):
    """Увеличение толерантности"""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''
            INSERT OR REPLACE INTO nagirt_tolerance (user_id, tolerance, last_used)
            VALUES (?, COALESCE((SELECT tolerance FROM nagirt_tolerance WHERE user_id = ?), 1.0) + ?, ?)
        ''', (user_id, user_id, increase, datetime.now().isoformat()))
        await db.commit()

async def reset_nagirt_tolerance(user_id: int):
    """Сброс толерантности"""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT OR REPLACE INTO nagirt_tolerance (user_id, tolerance, last_used) VALUES (?, 1.0, ?)",
            (user_id, datetime.now().isoformat())
        )
        await db.commit()

async def cleanup_expired():
    """Очистка истекших бустов и таблеток"""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM boosts WHERE expires_at <= ?", (datetime.now().isoformat(),))
        await db.execute("DELETE FROM nagirt_pills WHERE expires_at <= ?", (datetime.now().isoformat(),))
        await db.commit()

async def add_boost(user_id: int, boost_type: str, value: float, hours: int):
    """Добавление буста"""
    expires_at = datetime.now() + timedelta(hours=hours)
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT INTO boosts (user_id, boost_type, boost_value, expires_at) VALUES (?, ?, ?, ?)",
            (user_id, boost_type, value, expires_at.isoformat())
        )
        await db.commit()

async def get_active_boosts(user_id: int) -> float:
    """Получение активных бустов"""
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            "SELECT SUM(boost_value) FROM boosts WHERE user_id = ? AND expires_at > ?",
            (user_id, datetime.now().isoformat())
        )
        result = await cursor.fetchone()
        return result[0] if result and result[0] else 0.0

# ==================== ТОВАРЫ МАГАЗИНА (РЕАЛИСТИЧНЫЕ ЦЕНЫ) ====================
SHOP_ITEMS = [
    # Бусты к получке
    {"id": "bonus_coin", "name": "🪙 Бонусная монета", "price": 1500, "description": "+15% к получке на 8 часов", "type": "boost", "value": 0.15, "hours": 8},
    {"id": "premium_boost", "name": "🚀 Премиум-Буст", "price": 5000, "description": "+30% к получке на 24 часа", "type": "boost", "value": 0.3, "hours": 24},
    {"id": "mega_boost", "name": "💎 Мега-Буст", "price": 15000, "description": "+50% к получке на 3 дня", "type": "boost", "value": 0.5, "hours": 72},
    
    # Защита
    {"id": "day_off", "name": "🎉 Выходной", "price": 3000, "description": "Полный иммунитет к штрафам на 12 часов", "type": "protection", "hours": 12},
    {"id": "insurance", "name": "🛡️ Страховка", "price": 4000, "description": "Страховка от одного штрафа (возмещает 80%)", "type": "insurance"},
    
    # Таблетки Нагирт
    {"id": "nagirt_light", "name": "💊 Нагирт Лайт", "price": 2000, "description": "+40% к играм на 2 часа. Мало побочек.", "type": "pill", "effect": 0.4, "hours": 2, "side_effect_chance": 15},
    {"id": "nagirt_pro", "name": "💊💊 Нагирт Про", "price": 5000, "description": "+80% ко всему на 4 часа. Риск штрафов!", "type": "pill", "effect": 0.8, "hours": 4, "side_effect_chance": 35},
    {"id": "nagirt_extreme", "name": "💊💊💊 Нагирт Экстрим", "price": 12000, "description": "+150% на 6 часов! Высокий риск!", "type": "pill", "effect": 1.5, "hours": 6, "side_effect_chance": 60},
    {"id": "antidote", "name": "💉 Антидот", "price": 2500, "description": "Снимает побочки и сбрасывает толерантность", "type": "antidote"},
    
    # Разное
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
    
    # Разделение товаров по категориям
    boosts = [item for item in SHOP_ITEMS if item.get("type") == "boost"]
    pills = [item for item in SHOP_ITEMS if item.get("type") == "pill"]
    protection = [item for item in SHOP_ITEMS if item.get("type") in ["protection", "insurance"]]
    other = [item for item in SHOP_ITEMS if item.get("type") in ["antidote", "lottery", "instant"]]
    
    buttons.append([InlineKeyboardButton(text="📈 БУСТЫ К ЗАРПЛАТЕ", callback_data="none")])
    for item in boosts:
        buttons.append([InlineKeyboardButton(
            text=f"{item['name']} - {item['price']:,}₽".replace(",", " "),
            callback_data=f"buy_{item['id']}"
        )])
    
    buttons.append([InlineKeyboardButton(text="💊 ТАБЛЕТКИ НАГИРТ", callback_data="none")])
    for item in pills:
        buttons.append([InlineKeyboardButton(
            text=f"{item['name']} - {item['price']:,}₽".replace(",", " "),
            callback_data=f"buy_{item['id']}"
        )])
    
    buttons.append([InlineKeyboardButton(text="🛡️ ЗАЩИТА", callback_data="none")])
    for item in protection:
        buttons.append([InlineKeyboardButton(
            text=f"{item['name']} - {item['price']:,}₽".replace(",", " "),
            callback_data=f"buy_{item['id']}"
        )])
    
    for item in other:
        buttons.append([InlineKeyboardButton(
            text=f"{item['name']} - {item['price']:,}₽".replace(",", " "),
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

# ==================== ФУНКЦИИ ДЛЯ ФОРМАТИРОВАНИЯ ====================
def format_money(amount: int) -> str:
    """Форматирование суммы с пробелами"""
    return f"{amount:,}₽".replace(",", " ")

def format_time(seconds: int) -> str:
    """Форматирование времени в минуты:секунды"""
    minutes = seconds // 60
    secs = seconds % 60
    return f"{minutes}:{secs:02d}"

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
        f"• 💰 Получка ({ECONOMY_SETTINGS['salary_min']:,}-{ECONOMY_SETTINGS['salary_max']:,}₽)\n"
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

    # Очищаем истекшие бусты
    await cleanup_expired()
    
    # Получаем бусты и эффекты Нагирта
    boost_multiplier = await get_active_boosts(user_id)
    nagirt_effects = await get_active_nagirt_effects(user_id)
    
    # Базовая зарплата (реалистичный диапазон)
    base_salary = random.randint(
        ECONOMY_SETTINGS["salary_min"], 
        ECONOMY_SETTINGS["salary_max"]
    )
    
    # Штраф от таблеток
    pill_fine = 0
    if nagirt_effects["has_active"] and random.random() <= ECONOMY_SETTINGS["fine_chance"]:
        pill_fine = random.randint(
            int(base_salary * 0.1),  # 10% от зарплаты
            int(base_salary * 0.3)   # 30% от зарплаты
        )
        fine_reasons = [
            "Обнаружены следы Нагирта в крови!",
            "Работа в состоянии измененного сознания!",
            "Нарушение техники безопасности из-за таблеток!"
        ]
        await update_balance(user_id, -pill_fine, "penalty", f"💊 {random.choice(fine_reasons)}")
    
    # Общий множитель
    total_multiplier = 1.0 + boost_multiplier + nagirt_effects["salary_boost"]
    final_salary = int(base_salary * total_multiplier)
    
    # Начисляем зарплату
    await update_balance(user_id, final_salary, "salary", f"💸 Зарплата (x{total_multiplier:.2f})")
    
    # Обновляем время получки
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE players SET last_salary = ? WHERE user_id = ?",
            (current_time.isoformat(), user_id)
        )
        await db.commit()
    
    user = await get_user(user_id)
    
    # Формируем детализированный ответ
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
    
    # Комментарий Виталика
    if final_salary < ECONOMY_SETTINGS["salary_min"] * 1.5:
        comments = [
            "Могло бы быть и больше...",
            "На такую сумму даже пиццу не купишь!",
            "Работай лучше!"
        ]
    elif final_salary > ECONOMY_SETTINGS["salary_max"] * 0.8:
        comments = [
            "Отличная работа!",
            "Так держать!",
            "Вы заслужили эту премию!"
        ]
    else:
        comments = [
            "Нормально работаешь.",
            "Продолжай в том же духе.",
            "Стабильно, но можно лучше."
        ]
    
    if nagirt_effects["has_active"]:
        pill_comments = [
            "Таблетки не заменят профессионализм!",
            "Осторожнее с Нагиртом!",
            "Лекарства должны помогать, а не мешать работе!"
        ]
        response += f"💬 *Виталик:* '{random.choice(pill_comments)}'"
    else:
        response += f"💬 *Виталик:* '{random.choice(comments)}'"
    
    await message.answer(response, parse_mode="Markdown")

@dp.message(F.text == "🛒 Магазин")
async def handle_shop(message: Message):
    user_id = message.from_user.id
    user = await get_user(user_id)
    
    if not user:
        await message.answer("Сначала зарегистрируйтесь через /start")
        return
    
    # Получаем информацию о бустах
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
    
    item_id = callback.data[4:]  # Убираем "buy_"
    
    # Ищем товар
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
    
    # Оплачиваем
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
    
    # Применяем эффекты в зависимости от типа товара
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
            # Сохраняем информацию о страховке в отдельной таблице
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
        
        # Побочки
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
        if random.random() <= 0.25:  # 25% шанс выигрыша
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
        # Мгновенная зарплата
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
    
    # Получаем обновленные данные
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

# ==================== УКЛАДКА АСФАЛЬТА ====================
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
    
    # Проверяем время
    if user.get('last_asphalt'):
        last_asphalt_time = datetime.fromisoformat(user['last_asphalt'])
        time_since_last = (current_time - last_asphalt_time).total_seconds()
        
        if time_since_last < 30:
            wait_seconds = 30 - int(time_since_last)
            wait_time = format_time(wait_seconds)
            await callback.answer(f"⏳ Отдыхай еще {wait_time}!", show_alert=True)
            return
    
    # Получаем эффекты Нагирта для укладки асфальта
    nagirt_effects = await get_active_nagirt_effects(user_id)
    asphalt_boost = nagirt_effects["asphalt_boost"]
    
    # 70% шанс успеха, 30% шанс штрафа
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
    
    # Отправляем результат
    await callback.message.answer(result_text, parse_mode="Markdown")
    
    # Обновляем меню
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

# ==================== СТАТИСТИКА ====================
@dp.message(F.text == "📊 Статистика")
async def handle_statistics(message: Message):
    user_id = message.from_user.id
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
        
        # Общая статистика
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

# ==================== СЛУЧАЙНЫЕ ШТРАФЫ (20-30 МИНУТ) ====================
async def penalty_scheduler():
    """Планировщик случайных штрафов"""
    while True:
        try:
            # Ждем случайное время от 20 до 30 минут
            wait_time = random.randint(
                ECONOMY_SETTINGS["random_fine_interval_min"],
                ECONOMY_SETTINGS["random_fine_interval_max"]
            )
            await asyncio.sleep(wait_time)
            
            all_users = await get_all_users()
            logger.info(f"🔍 Проверка на штрафы: {len(all_users)} пользователей")
            
            for user in all_users:
                # Проверяем иммунитет
                user_data = await get_user(user['user_id'])
                if not user_data:
                    continue
                    
                if user_data.get('penalty_immunity_until'):
                    immunity_time = datetime.fromisoformat(user_data['penalty_immunity_until'])
                    if immunity_time > datetime.now():
                        continue
                
                # 25% шанс штрафа для каждого пользователя
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

@dp.message(F.text == "💊 Эффекты")
async def handle_effects(message: Message):
    user_id = message.from_user.id
    
    effects = await get_active_nagirt_effects(user_id)
    tolerance = await get_nagirt_tolerance(user_id)
    boosts = await get_active_boosts(user_id)
    
    effects_text = "⚡ *АКТИВНЫЕ ЭФФЕКТЫ*\n\n"
    
    # Бусты
    if boosts > 0:
        effects_text += f"📈 *Бусты к зарплате:* +{int(boosts*100)}%\n\n"
    else:
        effects_text += "📈 *Бусты к зарплате:* нет\n\n"
    
    # Таблетки Нагирт
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
    
    # Толерантность
    effects_text += f"📊 *Толерантность к Нагирту:* +{int((tolerance-1)*100)}%\n"
    
    if tolerance > 1.5:
        effects_text += "\n🚨 *ВНИМАНИЕ!* Высокая толерантность!\n"
        effects_text += "Эффект таблеток слабеет. Рекомендуется использовать антидот.\n"
    elif tolerance > 1.2:
        effects_text += "\n⚠️ *Предупреждение:* Толерантность повышена.\n"
    
    await message.answer(effects_text, parse_mode="Markdown")

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
        parse_mode="Markdown"
    )
    
    await state.set_state(TransferStates.choosing_recipient)

# ==================== ЗАПУСК БОТА ====================
async def on_startup():
    await init_db()
    asyncio.create_task(penalty_scheduler())
    logger.info("✅ Бот запущен с улучшенной экономикой!")

async def on_shutdown():
    logger.info("🛑 Бот останавливается...")

async def main():
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    await dp.start_polling(bot, skip_updates=True)

if __name__ == "__main__":
    asyncio.run(main())
