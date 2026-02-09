"""
Telegram бот "Виталик Штрафующий" - полностью рабочий
Исправлены ВСЕ проблемы: инлайн-кнопки, база данных, сообщения
"""

import asyncio
import logging
import random
import json
import sys
import os
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

from aiogram import Bot, Dispatcher, types, F, Router
from aiogram.client.default import DefaultBotProperties
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
BOT_TOKEN = "8451168327:AAGQffadqqBg3pZNQnjctVxH-dUgXsovTr4"  # Замените на ваш токен!
ADMIN_ID = 5775839902  # Ваш Telegram ID

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot.log", encoding="utf-8")
    ]
)
logger = logging.getLogger(__name__)

# Инициализация бота
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode="HTML")
)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# ==================== БАЗА ДАННЫХ ====================
DB_NAME = "vitalik_bot.db"

async def init_db():
    """Инициализация базы данных"""
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            # Таблица игроков
            await db.execute('''
                CREATE TABLE IF NOT EXISTS players (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    full_name TEXT,
                    balance INTEGER DEFAULT 1000,
                    total_earned INTEGER DEFAULT 1000,
                    total_spent INTEGER DEFAULT 0,
                    fines_count INTEGER DEFAULT 0,
                    transfers_count INTEGER DEFAULT 0,
                    purchases_count INTEGER DEFAULT 0,
                    asphalt_meters INTEGER DEFAULT 0,
                    asphalt_total_earned INTEGER DEFAULT 0,
                    last_paycheck TIMESTAMP,
                    last_asphalt TIMESTAMP,
                    last_fine TIMESTAMP,
                    tolerance INTEGER DEFAULT 0,
                    registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Таблица транзакций
            await db.execute('''
                CREATE TABLE IF NOT EXISTS transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    type TEXT,
                    amount INTEGER,
                    description TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(user_id) REFERENCES players(user_id)
                )
            ''')
            
            # Таблица покупок
            await db.execute('''
                CREATE TABLE IF NOT EXISTS purchases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    item_name TEXT,
                    price INTEGER,
                    bonus TEXT,
                    purchased_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(user_id) REFERENCES players(user_id)
                )
            ''')
            
            # Таблица активных таблеток
            await db.execute('''
                CREATE TABLE IF NOT EXISTS active_pills (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    pill_id TEXT,
                    pill_name TEXT,
                    effect_multiplier REAL DEFAULT 1.0,
                    side_effect_chance INTEGER DEFAULT 0,
                    expires_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(user_id) REFERENCES players(user_id)
                )
            ''')
            
            # Создаем индексы для быстрого поиска
            await db.execute('CREATE INDEX IF NOT EXISTS idx_user_id ON players(user_id)')
            await db.execute('CREATE INDEX IF NOT EXISTS idx_transactions_user_id ON transactions(user_id)')
            await db.execute('CREATE INDEX IF NOT EXISTS idx_active_pills_user_id ON active_pills(user_id)')
            await db.execute('CREATE INDEX IF NOT EXISTS idx_active_pills_expires ON active_pills(expires_at)')
            
            await db.commit()
            logger.info("✅ База данных инициализирована")
            
            # Проверяем, есть ли данные в таблице
            cursor = await db.execute("SELECT COUNT(*) FROM players")
            count = (await cursor.fetchone())[0]
            logger.info(f"📊 В базе данных {count} игроков")
            
    except Exception as e:
        logger.error(f"❌ Ошибка при инициализации БД: {e}")
        raise

async def get_user(user_id: int) -> Optional[Dict[str, Any]]:
    """Получить информацию о пользователе"""
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            # Включаем поддержку внешних ключей
            await db.execute("PRAGMA foreign_keys = ON")
            
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM players WHERE user_id = ?", 
                (user_id,)
            )
            user = await cursor.fetchone()
            if user:
                return dict(user)
            return None
    except Exception as e:
        logger.error(f"❌ Ошибка при получении пользователя {user_id}: {e}")
        return None

async def register_user(user_id: int, username: str, full_name: str) -> bool:
    """Регистрация нового пользователя"""
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("PRAGMA foreign_keys = ON")
            
            # Проверяем, существует ли пользователь
            cursor = await db.execute(
                "SELECT user_id FROM players WHERE user_id = ?", 
                (user_id,)
            )
            exists = await cursor.fetchone()
            
            if not exists:
                # Регистрируем нового пользователя
                await db.execute(
                    '''INSERT INTO players 
                       (user_id, username, full_name, balance, total_earned) 
                       VALUES (?, ?, ?, 1000, 1000)''',
                    (user_id, username or "Без username", full_name)
                )
                
                # Записываем начальную транзакцию
                await db.execute(
                    '''INSERT INTO transactions (user_id, type, amount, description)
                       VALUES (?, 'registration', 1000, 'Начальный баланс')''',
                    (user_id,)
                )
                
                await db.commit()
                logger.info(f"✅ Зарегистрирован новый пользователь: {user_id} ({full_name})")
                return True
            
            logger.info(f"⚠️ Пользователь {user_id} уже зарегистрирован")
            return False
            
    except Exception as e:
        logger.error(f"❌ Ошибка при регистрации пользователя {user_id}: {e}")
        return False

async def ensure_user_exists(user_id: int, username: str, full_name: str) -> bool:
    """Гарантировать, что пользователь существует"""
    user = await get_user(user_id)
    if not user:
        return await register_user(user_id, username, full_name)
    return True

async def update_balance(user_id: int, amount: int, txn_type: str, description: str) -> tuple[bool, str]:
    """Обновить баланс пользователя"""
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("PRAGMA foreign_keys = ON")
            
            # Получаем текущий баланс
            cursor = await db.execute(
                "SELECT balance FROM players WHERE user_id = ?",
                (user_id,)
            )
            result = await cursor.fetchone()
            if not result:
                return False, "Пользователь не найден"
            
            current_balance = result[0]
            
            # Проверяем, не уйдет ли баланс в минус (кроме штрафов)
            if txn_type not in ['fine', 'pill_fine'] and current_balance + amount < 0:
                return False, "Недостаточно средств!"
            
            # Обновляем баланс в одной транзакции
            await db.execute(
                "UPDATE players SET balance = balance + ? WHERE user_id = ?",
                (amount, user_id)
            )
            
            # Обновляем статистику
            if amount > 0 and txn_type in ['paycheck', 'bonus', 'transfer_in', 'asphalt', 'pill_bonus']:
                await db.execute(
                    "UPDATE players SET total_earned = total_earned + ? WHERE user_id = ?",
                    (amount, user_id)
                )
            elif amount < 0 and txn_type in ['purchase', 'fine', 'transfer_out', 'pill_fine']:
                await db.execute(
                    "UPDATE players SET total_spent = total_spent + ? WHERE user_id = ?",
                    (abs(amount), user_id)
                )
            
            # Обновляем счетчики
            if txn_type == 'fine':
                await db.execute(
                    "UPDATE players SET fines_count = fines_count + 1 WHERE user_id = ?",
                    (user_id,)
                )
            elif txn_type == 'transfer_out':
                await db.execute(
                    "UPDATE players SET transfers_count = transfers_count + 1 WHERE user_id = ?",
                    (user_id,)
                )
            elif txn_type == 'purchase':
                await db.execute(
                    "UPDATE players SET purchases_count = purchases_count + 1 WHERE user_id = ?",
                    (user_id,)
                )
            elif txn_type == 'asphalt':
                await db.execute(
                    "UPDATE players SET asphalt_meters = asphalt_meters + 1 WHERE user_id = ?",
                    (user_id,)
                )
                if amount > 0:
                    await db.execute(
                        "UPDATE players SET asphalt_total_earned = asphalt_total_earned + ? WHERE user_id = ?",
                        (amount, user_id)
                    )
            
            # Записываем транзакцию
            await db.execute(
                '''INSERT INTO transactions (user_id, type, amount, description)
                   VALUES (?, ?, ?, ?)''',
                (user_id, txn_type, amount, description)
            )
            
            await db.commit()
            return True, "Успешно"
            
    except Exception as e:
        logger.error(f"❌ Ошибка при обновлении баланса {user_id}: {e}")
        return False, str(e)

async def get_all_users() -> List[Dict[str, Any]]:
    """Получить список всех пользователей"""
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT user_id, full_name, username, balance FROM players ORDER BY balance DESC"
            )
            users = await cursor.fetchall()
            return [dict(user) for user in users]
    except Exception as e:
        logger.error(f"❌ Ошибка при получении списка пользователей: {e}")
        return []

# ==================== СИСТЕМА ТАБЛЕТОК ====================
PILLS = [
    {
        "id": "nagirt_light",
        "name": "💊 Нагирт Лайт",
        "price": 200,
        "description": "+50% к заработку в играх на 1 час. Мало побочек.",
        "effect": 0.5,
        "hours": 1,
        "side_effect_chance": 15,
        "emoji": "💊",
        "type": "pill"
    },
    {
        "id": "nagirt_pro",
        "name": "💊💊 Нагирт Про",
        "price": 500,
        "description": "+100% ко всему на 2 часа. Риск штрафов в получке!",
        "effect": 1.0,
        "hours": 2,
        "side_effect_chance": 35,
        "emoji": "💊💊",
        "type": "pill"
    },
    {
        "id": "nagirt_extreme",
        "name": "💊💊💊 Нагирт Экстрим",
        "price": 1000,
        "description": "+200% на 3 часа! Высокий риск побочек и штрафов!",
        "effect": 2.0,
        "hours": 3,
        "side_effect_chance": 60,
        "emoji": "💊💊💊",
        "type": "pill"
    },
    {
        "id": "antidote",
        "name": "💉 Антидот",
        "price": 300,
        "description": "Снимает побочки от Нагирта. Понижает толерантность.",
        "emoji": "💉",
        "type": "antidote"
    }
]

async def add_active_pill(user_id: int, pill: Dict[str, Any]) -> bool:
    """Добавить активную таблетку"""
    try:
        expires_at = datetime.now() + timedelta(hours=pill['hours'])
        
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("PRAGMA foreign_keys = ON")
            
            # Добавляем таблетку
            await db.execute(
                '''INSERT INTO active_pills 
                   (user_id, pill_id, pill_name, effect_multiplier, side_effect_chance, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?)''',
                (user_id, pill['id'], pill['name'], pill['effect'], pill['side_effect_chance'], expires_at.isoformat())
            )
            
            # Увеличиваем толерантность
            await db.execute(
                "UPDATE players SET tolerance = tolerance + 10 WHERE user_id = ?",
                (user_id,)
            )
            
            await db.commit()
            return True
    except Exception as e:
        logger.error(f"❌ Ошибка при добавлении таблетки: {e}")
        return False

async def get_active_pills(user_id: int) -> List[Dict[str, Any]]:
    """Получить активные таблетки пользователя"""
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            # Удаляем просроченные таблетки
            await db.execute(
                "DELETE FROM active_pills WHERE user_id = ? AND expires_at < ?",
                (user_id, datetime.now().isoformat())
            )
            await db.commit()
            
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM active_pills WHERE user_id = ?",
                (user_id,)
            )
            pills = await cursor.fetchall()
            return [dict(pill) for pill in pills]
    except Exception as e:
        logger.error(f"❌ Ошибка при получении таблеток: {e}")
        return []

async def get_active_pills_effect(user_id: int) -> Dict[str, Any]:
    """Получить суммарный эффект от активных таблеток"""
    pills = await get_active_pills(user_id)
    
    if not pills:
        return {'multiplier': 1.0, 'side_effect_chance': 0, 'pill_count': 0}
    
    total_effect = 1.0
    total_side_effect = 0
    
    for pill in pills:
        total_effect += pill['effect_multiplier']
        total_side_effect += pill['side_effect_chance']
    
    # Учитываем толерантность
    user = await get_user(user_id)
    tolerance = user.get('tolerance', 0) if user else 0
    tolerance_bonus = min(50, tolerance)
    
    effective_side_effect = max(0, total_side_effect - tolerance_bonus)
    
    return {
        'multiplier': total_effect,
        'side_effect_chance': effective_side_effect,
        'pill_count': len(pills)
    }

async def check_pill_side_effect(user_id: int) -> tuple[bool, int]:
    """Проверить, сработал ли побочный эффект"""
    effect = await get_active_pills_effect(user_id)
    
    if random.random() * 100 < effect['side_effect_chance']:
        # Сработал побочный эффект
        fine_amount = random.randint(50, 200)
        await update_balance(user_id, -fine_amount, 'pill_fine', 'Побочный эффект от таблеток')
        return True, fine_amount
    
    return False, 0

async def remove_all_pills(user_id: int) -> bool:
    """Удалить все активные таблетки"""
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("PRAGMA foreign_keys = ON")
            
            await db.execute(
                "DELETE FROM active_pills WHERE user_id = ?",
                (user_id,)
            )
            
            # Уменьшаем толерантность
            user = await get_user(user_id)
            if user:
                tolerance = user.get('tolerance', 0)
                new_tolerance = max(0, tolerance - 50)
                await db.execute(
                    "UPDATE players SET tolerance = ? WHERE user_id = ?",
                    (new_tolerance, user_id)
                )
            
            await db.commit()
            return True
    except Exception as e:
        logger.error(f"❌ Ошибка при удалении таблеток: {e}")
        return False

# ==================== МАШИНЫ СОСТОЯНИЙ ====================
class TransferStates(StatesGroup):
    choosing_recipient = State()
    entering_amount = State()

class BroadcastStates(StatesGroup):
    waiting_for_message = State()

# ==================== КЛАВИАТУРЫ ====================
def get_main_keyboard() -> ReplyKeyboardMarkup:
    """Основная клавиатура бота"""
    keyboard = [
        [KeyboardButton(text="💰 Получка"), KeyboardButton(text="🛒 Магазин")],
        [KeyboardButton(text="🔁 Перевод"), KeyboardButton(text="📊 Профиль")],
        [KeyboardButton(text="🧱 Асфальт"), KeyboardButton(text="💊 Таблетки")],
        [KeyboardButton(text="👥 Игроки"), KeyboardButton(text="📢 Рассылка")]
    ]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

def get_shop_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для магазина"""
    buttons = []
    for item in PILLS:
        buttons.append([
            InlineKeyboardButton(
                text=f"{item['name']} - {item['price']}₽",
                callback_data=f"shop:{item['id']}"
            )
        ])
    
    buttons.append([
        InlineKeyboardButton(text="💰 Баланс", callback_data="check_balance"),
        InlineKeyboardButton(text="◀️ Назад", callback_data="main_menu")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_users_keyboard(users: List[Dict[str, Any]], exclude_id: int) -> InlineKeyboardMarkup:
    """Клавиатура для выбора получателя"""
    buttons = []
    for user in users:
        if user['user_id'] != exclude_id:
            display_name = user['full_name'][:20] if len(user['full_name']) > 20 else user['full_name']
            buttons.append([
                InlineKeyboardButton(
                    text=f"{display_name} ({user['balance']}₽)",
                    callback_data=f"transfer_to:{user['user_id']}"
                )
            ])
    
    buttons.append([
        InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_profile_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для профиля"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Обновить", callback_data="refresh_profile")],
        [InlineKeyboardButton(text="📈 Топ игроков", callback_data="show_top")],
        [InlineKeyboardButton(text="◀️ В меню", callback_data="main_menu")]
    ])

def get_pills_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для таблеток"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛒 Купить таблетки", callback_data="go_to_shop")],
        [InlineKeyboardButton(text="🔄 Обновить", callback_data="refresh_pills")],
        [InlineKeyboardButton(text="◀️ В меню", callback_data="main_menu")]
    ])

# ==================== ОСНОВНЫЕ ОБРАБОТЧИКИ ====================
@dp.message(CommandStart())
async def cmd_start(message: Message):
    """Обработчик команды /start"""
    user_id = message.from_user.id
    username = message.from_user.username or "Без username"
    full_name = message.from_user.full_name
    
    logger.info(f"🔄 /start от {user_id} ({full_name})")
    
    # Гарантируем, что пользователь существует
    await ensure_user_exists(user_id, username, full_name)
    user = await get_user(user_id)
    
    if not user:
        await message.answer("❌ Ошибка регистрации. Попробуйте еще раз.")
        return
    
    # Получаем активные таблетки
    pills = await get_active_pills(user_id)
    
    welcome_text = (
        f"👋 <b>Привет, {full_name}!</b>\n\n"
        f"Я <b>Виталик</b>, твой начальник! 🏢\n\n"
        f"<b>💰 Баланс:</b> {user['balance']}₽\n"
        f"<b>💊 Таблеток:</b> {len(pills)} активных\n"
        f"<b>🧱 Асфальта:</b> {user['asphalt_meters']}м\n\n"
        f"<i>Используй кнопки ниже:</i>"
    )
    
    await message.answer(welcome_text, reply_markup=get_main_keyboard())

@dp.message(F.text == "💰 Получка")
async def handle_paycheck(message: Message):
    """Обработка нажатия кнопки 'Получка'"""
    user_id = message.from_user.id
    user = await get_user(user_id)
    
    if not user:
        await message.answer("❌ Используйте /start для регистрации")
        return
    
    # Проверяем время последней получки
    current_time = datetime.now()
    if user.get('last_paycheck'):
        last_paycheck = datetime.fromisoformat(user['last_paycheck'])
        time_diff = current_time - last_paycheck
        
        if time_diff.total_seconds() < 300:  # 5 минут
            wait_seconds = 300 - time_diff.total_seconds()
            wait_minutes = int(wait_seconds / 60)
            wait_seconds %= 60
            
            await message.answer(
                f"⏳ <b>Слишком рано!</b>\n\n"
                f"Жди еще <b>{wait_minutes} мин {int(wait_seconds)} сек</b> 😏\n\n"
                f"💬 <i>Виталик:</i> Терпение, работяга!"
            )
            return
    
    # Вычисляем сумму
    base_amount = random.randint(100, 500)
    
    # Эффект таблеток
    pill_effect = await get_active_pills_effect(user_id)
    multiplier = pill_effect['multiplier']
    paycheck_amount = int(base_amount * multiplier)
    
    # Проверяем побочки
    pill_side_effect, pill_fine_amount = await check_pill_side_effect(user_id)
    
    if not pill_side_effect:
        # Нормальная получка
        await update_balance(user_id, paycheck_amount, 'paycheck', 'Получка')
        
        # Обновляем время
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute(
                "UPDATE players SET last_paycheck = ? WHERE user_id = ?",
                (current_time.isoformat(), user_id)
            )
            await db.commit()
        
        jokes = [
            f"Держи {paycheck_amount}₽! Не трать все сразу! ☕",
            f"Вот {paycheck_amount}₽. Быстро на работу! ⚡",
            f"{paycheck_amount}₽ твои! Не зли меня! 😈",
            f"Получил {paycheck_amount}₽? Отлично! 🤑"
        ]
        
        pill_text = f"\n💊 <b>Бонус:</b> x{multiplier:.1f}" if pill_effect['pill_count'] > 0 else ""
        
        user = await get_user(user_id)
        response = (
            f"💰 <b>Получка!</b>\n\n"
            f"<b>Сумма:</b> +{paycheck_amount}₽\n"
            f"<b>Баланс:</b> {user['balance']}₽"
            f"{pill_text}\n\n"
            f"💬 <i>Виталик:</i> {random.choice(jokes)}"
        )
    else:
        # Побочка
        user = await get_user(user_id)
        jokes = [
            f"Ха! Побочка! -{pill_fine_amount}₽! 😂",
            f"Нагирт подвел! -{pill_fine_amount}₽! 💊",
            f"Побочка! Забираю {pill_fine_amount}₽! 👿"
        ]
        
        response = (
            f"💊 <b>ПОБОЧКА!</b>\n\n"
            f"<b>Штраф:</b> -{pill_fine_amount}₽\n"
            f"<b>Баланс:</b> {user['balance']}₽\n\n"
            f"💬 <i>Виталик:</i> {random.choice(jokes)}"
        )
    
    await message.answer(response)

@dp.message(F.text == "🛒 Магазин")
async def handle_shop(message: Message):
    """Обработка нажатия кнопки 'Магазин'"""
    user_id = message.from_user.id
    user = await get_user(user_id)
    
    if not user:
        await message.answer("❌ Используйте /start для регистрации")
        return
    
    shop_text = (
        f"🛒 <b>Магазин Виталика</b>\n\n"
        f"<b>💰 Баланс:</b> {user['balance']}₽\n"
        f"<b>💪 Толерантность:</b> {user.get('tolerance', 0)}/100\n\n"
        f"<b>💊 Таблетки:</b>\n"
    )
    
    for item in PILLS:
        shop_text += f"\n<b>{item['name']}</b> - {item['price']}₽\n"
        shop_text += f"<i>{item['description']}</i>\n"
    
    shop_text += "\n<b>Выбери товар:</b>"
    
    await message.answer(shop_text, reply_markup=get_shop_keyboard())

@dp.callback_query(F.data.startswith("shop:"))
async def handle_buy_item(callback: CallbackQuery):
    """Обработка покупки товара"""
    user_id = callback.from_user.id
    user = await get_user(user_id)
    
    if not user:
        await callback.answer("❌ Используйте /start")
        return
    
    item_id = callback.data.replace("shop:", "")
    item = next((i for i in PILLS if i['id'] == item_id), None)
    
    if not item:
        await callback.answer("❌ Товар не найден")
        return
    
    # Проверяем баланс
    if user['balance'] < item['price']:
        await callback.answer(f"❌ Нужно {item['price']}₽")
        return
    
    if item['type'] == 'antidote':
        # Антидот
        await update_balance(user_id, -item['price'], 'purchase', f"Покупка: {item['name']}")
        await remove_all_pills(user_id)
        
        user = await get_user(user_id)
        response = (
            f"✅ <b>Антидот принят!</b>\n\n"
            f"<b>💉 Товар:</b> {item['name']}\n"
            f"<b>💵 Цена:</b> {item['price']}₽\n\n"
            f"<b>✅ Таблетки сняты</b>\n"
            f"<b>📉 Толерантность -50%</b>\n\n"
            f"<b>💰 Баланс:</b> {user['balance']}₽\n\n"
            f"💬 <i>Виталик:</i> Молодец! 🏥"
        )
    else:
        # Таблетка
        await update_balance(user_id, -item['price'], 'purchase', f"Покупка: {item['name']}")
        await add_active_pill(user_id, item)
        
        jokes = [
            f"Купил {item['name']}! Удачи! 😈",
            f"Так, {item['name']}... Знай меру! 💊",
            f"Таблетка куплена! Работай быстрее! ⚡",
            f"{item['name']} активирована! 👀"
        ]
        
        user = await get_user(user_id)
        response = (
            f"✅ <b>Таблетка куплена!</b>\n\n"
            f"<b>💊 Товар:</b> {item['name']}\n"
            f"<b>💵 Цена:</b> {item['price']}₽\n"
            f"<b>⏱️ Время:</b> {item['hours']} час\n"
            f"<b>📈 Эффект:</b> +{int(item['effect'] * 100)}%\n"
            f"<b>⚠️ Риск:</b> {item['side_effect_chance']}%\n\n"
            f"<b>💰 Баланс:</b> {user['balance']}₽\n"
            f"<b>💪 Толерантность:</b> +10\n\n"
            f"💬 <i>Виталик:</i> {random.choice(jokes)}"
        )
    
    await callback.message.edit_text(response)
    await callback.answer(f"Куплено: {item['name']}")

@dp.message(F.text == "🧱 Асфальт")
async def handle_asphalt(message: Message):
    """Мини-игра укладка асфальта"""
    user_id = message.from_user.id
    user = await get_user(user_id)
    
    if not user:
        await message.answer("❌ Используйте /start для регистрации")
        return
    
    # Проверяем время
    current_time = datetime.now()
    if user.get('last_asphalt'):
        last_asphalt = datetime.fromisoformat(user['last_asphalt'])
        time_diff = current_time - last_asphalt
        
        if time_diff.total_seconds() < 30:
            wait_seconds = 30 - time_diff.total_seconds()
            await message.answer(
                f"⏳ <b>Отдыхай!</b>\n\n"
                f"Жди <b>{int(wait_seconds)} сек</b> 👷\n\n"
                f"💬 <i>Виталик:</i> Не торопись!"
            )
            return
    
    # Заработок
    base_earnings = 10
    pill_effect = await get_active_pills_effect(user_id)
    multiplier = pill_effect['multiplier']
    earnings = int(base_earnings * multiplier)
    
    # События
    event = random.choices(
        ['success', 'success', 'success', 'vitalik_fine', 'equipment_break', 'bad_asphalt'],
        weights=[70, 10, 10, 5, 3, 2]
    )[0]
    
    jokes = {
        'success': [
            f"Отлично! {earnings}₽! 👍",
            f"Так, {earnings}₽... Неплохо! 😏",
            f"Мастер! {earnings}₽! 🏗️",
            f"{earnings}₽ в карман! 🚧"
        ],
        'vitalik_fine': [
            f"Криво! Штраф 100₽! 😡",
            f"Это говно! Штраф 100₽! 💩",
            f"Косяк! 100₽ мне! 👿",
            f"Штраф! -100₽! ⚖️"
        ],
        'equipment_break': [
            f"Каток сломался! -50₽! 🚜",
            f"Техника глохнет! -50₽! 🔧",
            f"Ремонт! -50₽! 🛠️"
        ],
        'bad_asphalt': [
            f"Дерьмо собачье! -30₽ 💩",
            f"Качество говно! -30₽ 🧱",
            f"Грязный асфальт! -30₽! 🪣"
        ]
    }
    
    # Обработка
    result_text = ""
    final_earnings = 0
    penalty = 0
    
    if event == 'success':
        success_type = random.choice(['normal', 'perfect', 'fast'])
        
        if success_type == 'perfect':
            bonus = random.randint(5, 20)
            earnings += bonus
            result_text = f"🎉 <b>ИДЕАЛЬНО!</b>\nБонус +{bonus}₽!\n"
        elif success_type == 'fast':
            bonus = random.randint(3, 10)
            earnings += bonus
            result_text = f"⚡ <b>БЫСТРО!</b>\nБонус +{bonus}₽!\n"
        
        final_earnings = earnings
        result_text += f"<b>Заработано:</b> +{earnings}₽"
        await update_balance(user_id, earnings, 'asphalt', 'Укладка асфальта')
    else:
        if event == 'vitalik_fine':
            penalty = 100
            result_text = f"⚠️ <b>ВИТАЛИК ЗЛИТСЯ!</b>\nШтраф: -{penalty}₽"
        elif event == 'equipment_break':
            penalty = 50
            result_text = f"🔧 <b>ПОЛОМКА!</b>\nРемонт: -{penalty}₽"
        elif event == 'bad_asphalt':
            penalty = 30
            result_text = f"🧱 <b>БРАК!</b>\nУбытки: -{penalty}₽"
        
        await update_balance(user_id, -penalty, 'fine', f'Штраф: {event}')
    
    # Побочки от таблеток
    pill_side_effect, pill_fine_amount = await check_pill_side_effect(user_id)
    pill_side_text = ""
    
    if pill_side_effect:
        pill_side_text = f"\n\n💊 <b>ПОБОЧКА!</b>\nДоп. штраф: -{pill_fine_amount}₽"
        penalty += pill_fine_amount
    
    # Обновляем время
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE players SET last_asphalt = ? WHERE user_id = ?",
            (current_time.isoformat(), user_id)
        )
        await db.commit()
    
    user = await get_user(user_id)
    
    pill_text = ""
    if pill_effect['pill_count'] > 0:
        pill_text = f"\n💊 <b>Эффект:</b> x{pill_effect['multiplier']:.1f}"
    
    response = (
        f"🧱 <b>УКЛАДКА АСФАЛЬТА</b>\n\n"
        f"{result_text}{pill_side_text}\n\n"
        f"<b>💰 Баланс:</b> {user['balance']}₽\n"
        f"<b>📊 Всего:</b> {user['asphalt_meters']}м\n"
        f"{pill_text}\n\n"
        f"💬 <i>Виталик:</i> {random.choice(jokes[event])}"
    )
    
    await message.answer(response)

@dp.message(F.text == "📊 Профиль")
async def handle_profile(message: Message):
    """Показать профиль пользователя"""
    user_id = message.from_user.id
    user = await get_user(user_id)
    
    if not user:
        await message.answer("❌ Используйте /start для регистрации")
        return
    
    pills = await get_active_pills(user_id)
    pill_effect = await get_active_pills_effect(user_id)
    
    profile_text = (
        f"👤 <b>Профиль</b>\n\n"
        f"<b>Имя:</b> {user['full_name']}\n"
        f"<b>Username:</b> @{user['username'] or 'нет'}\n\n"
        f"<b>💰 Баланс:</b> {user['balance']}₽\n"
        f"<b>📈 Заработано:</b> {user['total_earned']}₽\n"
        f"<b>📉 Потрачено:</b> {user['total_spent']}₽\n\n"
        f"<b>📊 Статистика:</b>\n"
        f"• ⚖️ Штрафов: {user['fines_count']}\n"
        f"• 🛒 Покупок: {user['purchases_count']}\n"
        f"• 🔁 Переводов: {user['transfers_count']}\n"
        f"• 🧱 Асфальта: {user['asphalt_meters']}м\n\n"
        f"<b>💪 Толерантность:</b> {user['tolerance']}/100\n"
        f"<b>💊 Таблеток:</b> {len(pills)} активных\n"
    )
    
    if pills:
        profile_text += "\n<b>Активные таблетки:</b>\n"
        for pill in pills:
            expires_at = datetime.fromisoformat(pill['expires_at'])
            time_left = expires_at - datetime.now()
            hours_left = int(time_left.total_seconds() / 3600)
            minutes_left = int((time_left.total_seconds() % 3600) / 60)
            
            profile_text += f"• {pill['pill_name']} ({hours_left}ч {minutes_left}мин)\n"
    
    await message.answer(profile_text, reply_markup=get_profile_keyboard())

@dp.message(F.text == "💊 Таблетки")
async def handle_my_pills(message: Message):
    """Показать мои таблетки"""
    user_id = message.from_user.id
    user = await get_user(user_id)
    
    if not user:
        await message.answer("❌ Используйте /start для регистрации")
        return
    
    pills = await get_active_pills(user_id)
    pill_effect = await get_active_pills_effect(user_id)
    
    if not pills:
        await message.answer(
            "💊 <b>Нет активных таблеток</b>\n\n"
            "Купи таблетки в 🛒 Магазине!\n\n"
            f"<b>💪 Толерантность:</b> {user['tolerance']}/100\n"
            f"<i>Снижает риск побочек</i>",
            reply_markup=get_pills_keyboard()
        )
        return
    
    pills_text = (
        f"💊 <b>Твои таблетки</b>\n\n"
        f"<b>💊 Всего:</b> {len(pills)}\n"
        f"<b>📈 Множитель:</b> x{pill_effect['multiplier']:.1f}\n"
        f"<b>⚠️ Риск:</b> {pill_effect['side_effect_chance']}%\n"
        f"<b>💪 Толерантность:</b> {user['tolerance']}/100\n\n"
    )
    
    for pill in pills:
        expires_at = datetime.fromisoformat(pill['expires_at'])
        time_left = expires_at - datetime.now()
        hours_left = int(time_left.total_seconds() / 3600)
        minutes_left = int((time_left.total_seconds() % 3600) / 60)
        
        pills_text += f"• <b>{pill['pill_name']}</b>\n"
        pills_text += f"  ⏱️ {hours_left}ч {minutes_left}мин\n"
        pills_text += f"  📈 +{int(pill['effect_multiplier'] * 100)}%\n"
        pills_text += f"  ⚠️ {pill['side_effect_chance']}%\n\n"
    
    await message.answer(pills_text, reply_markup=get_pills_keyboard())

@dp.message(F.text == "👥 Игроки")
async def handle_players_list(message: Message):
    """Показать список игроков"""
    user_id = message.from_user.id
    user = await get_user(user_id)
    
    if not user:
        await message.answer("❌ Используйте /start для регистрации")
        return
    
    all_users = await get_all_users()
    
    if not all_users:
        await message.answer("😔 Нет игроков")
        return
    
    players_text = f"👥 <b>Игроки</b> (всего: {len(all_users)})\n\n"
    
    for i, player in enumerate(all_users[:15], 1):
        medal = ""
        if i == 1: medal = "🥇"
        elif i == 2: medal = "🥈"
        elif i == 3: medal = "🥉"
        else: medal = f"{i}."
        
        name = player['full_name']
        if len(name) > 15:
            name = name[:12] + "..."
        
        username = f"@{player['username']}" if player['username'] else "без username"
        
        players_text += (
            f"{medal} <b>{name}</b>\n"
            f"   👤 {username}\n"
            f"   💰 {player['balance']}₽\n\n"
        )
    
    await message.answer(players_text)

@dp.message(F.text == "🔁 Перевод")
async def handle_transfer_start(message: Message, state: FSMContext):
    """Начало перевода"""
    user_id = message.from_user.id
    user = await get_user(user_id)
    
    if not user:
        await message.answer("❌ Используйте /start для регистрации")
        return
    
    all_users = await get_all_users()
    
    if len(all_users) < 2:
        await message.answer("😔 Нет других игроков")
        return
    
    await state.update_data(sender_id=user_id)
    keyboard = get_users_keyboard(all_users, user_id)
    
    await message.answer(
        f"🔁 <b>Перевод</b>\n\n"
        f"<b>💰 Твой баланс:</b> {user['balance']}₽\n\n"
        f"<b>Выбери получателя:</b>",
        reply_markup=keyboard
    )
    
    await state.set_state(TransferStates.choosing_recipient)

@dp.callback_query(F.data.startswith("transfer_to:"), TransferStates.choosing_recipient)
async def handle_recipient_selection(callback: CallbackQuery, state: FSMContext):
    """Выбор получателя"""
    recipient_id = int(callback.data.replace("transfer_to:", ""))
    
    recipient = await get_user(recipient_id)
    
    if not recipient:
        await callback.answer("❌ Игрок не найден")
        return
    
    await state.update_data(
        recipient_id=recipient_id,
        recipient_name=recipient['full_name']
    )
    
    await callback.message.edit_text(
        f"✅ <b>Выбрано:</b> {recipient['full_name']}\n\n"
        f"💰 <b>Баланс получателя:</b> {recipient['balance']}₽\n\n"
        f"<b>Введи сумму (1-10000₽):</b>\n"
        f"<i>или 'отмена'</i>"
    )
    
    await state.set_state(TransferStates.entering_amount)
    await callback.answer()

@dp.message(TransferStates.entering_amount)
async def handle_transfer_amount(message: Message, state: FSMContext):
    """Ввод суммы перевода"""
    user_data = await state.get_data()
    sender_id = user_data['sender_id']
    recipient_id = user_data['recipient_id']
    recipient_name = user_data['recipient_name']
    
    if message.text.lower() in ['отмена', 'cancel', 'стоп']:
        await state.clear()
        await message.answer("❌ Перевод отменен", reply_markup=get_main_keyboard())
        return
    
    try:
        amount = int(message.text.strip())
        
        if amount <= 0:
            await message.answer("❌ Сумма должна быть > 0")
            return
        
        if amount > 10000:
            await message.answer("❌ Максимум 10000₽")
            return
        
        sender = await get_user(sender_id)
        if not sender:
            await message.answer("❌ Отправитель не найден")
            await state.clear()
            return
        
        if sender['balance'] < amount:
            await message.answer(
                f"❌ Не хватает!\n"
                f"Твой баланс: {sender['balance']}₽\n"
                f"Нужно: {amount}₽"
            )
            return
        
        recipient = await get_user(recipient_id)
        if not recipient:
            await message.answer("❌ Получатель не найден")
            await state.clear()
            return
        
        # Перевод
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("PRAGMA foreign_keys = ON")
            
            # Проверяем баланс
            cursor = await db.execute(
                "SELECT balance FROM players WHERE user_id = ?",
                (sender_id,)
            )
            sender_balance = (await cursor.fetchone())[0]
            
            if sender_balance < amount:
                await message.answer("❌ Не хватает средств")
                return
            
            # Списываем
            await db.execute(
                "UPDATE players SET balance = balance - ? WHERE user_id = ?",
                (amount, sender_id)
            )
            
            # Зачисляем
            await db.execute(
                "UPDATE players SET balance = balance + ? WHERE user_id = ?",
                (amount, recipient_id)
            )
            
            # Транзакции
            await db.execute(
                '''INSERT INTO transactions (user_id, type, amount, description)
                   VALUES (?, 'transfer_out', ?, ?)''',
                (sender_id, -amount, f"Перевод {recipient_name}")
            )
            
            await db.execute(
                '''INSERT INTO transactions (user_id, type, amount, description)
                   VALUES (?, 'transfer_in', ?, ?)''',
                (recipient_id, amount, f"Перевод от {sender['full_name']}")
            )
            
            # Счетчики
            await db.execute(
                "UPDATE players SET transfers_count = transfers_count + 1 WHERE user_id = ?",
                (sender_id,)
            )
            
            await db.commit()
        
        # Шутки
        jokes = [
            f"Перевод {amount}₽! Не взятка? 😏",
            f"Так, {amount}₽... За какие услуги? 🤫",
            f"Деньги ушли! Работай! 💼"
        ]
        
        # Уведомляем отправителя
        sender = await get_user(sender_id)
        await message.answer(
            f"✅ <b>Перевод выполнен!</b>\n\n"
            f"<b>👤 Кому:</b> {recipient_name}\n"
            f"<b>💵 Сумма:</b> {amount}₽\n"
            f"<b>💰 Твой баланс:</b> {sender['balance']}₽\n\n"
            f"💬 <i>Виталик:</i> {random.choice(jokes)}",
            reply_markup=get_main_keyboard()
        )
        
        # Уведомляем получателя
        try:
            recipient = await get_user(recipient_id)
            await bot.send_message(
                recipient_id,
                f"💸 <b>Перевод!</b>\n\n"
                f"<b>👤 От кого:</b> {sender['full_name']}\n"
                f"<b>💵 Сумма:</b> +{amount}₽\n"
                f"<b>💰 Твой баланс:</b> {recipient['balance']}₽\n\n"
                f"💬 <i>Виталик:</i> Кто-то щедрый! 🤑"
            )
        except Exception as e:
            logger.error(f"Не уведомить получателя: {e}")
        
    except ValueError:
        await message.answer("❌ Введите число")
        return
    
    await state.clear()

@dp.message(F.text == "📢 Рассылка")
async def handle_broadcast_start(message: Message, state: FSMContext):
    """Начало рассылки"""
    user_id = message.from_user.id
    
    if user_id != ADMIN_ID:
        await message.answer("⛔ Только для админа!")
        return
    
    await message.answer(
        "📢 <b>Рассылка</b>\n\n"
        "Введи сообщение:\n\n"
        "<i>или 'отмена'</i>"
    )
    
    await state.set_state(BroadcastStates.waiting_for_message)

@dp.message(BroadcastStates.waiting_for_message)
async def handle_broadcast_message(message: Message, state: FSMContext):
    """Обработка рассылки"""
    if message.text.lower() in ['отмена', 'cancel', 'стоп']:
        await state.clear()
        await message.answer("❌ Рассылка отменена", reply_markup=get_main_keyboard())
        return
    
    broadcast_text = message.text
    all_users = await get_all_users()
    
    if not all_users:
        await message.answer("😔 Нет пользователей")
        await state.clear()
        return
    
    sent_count = 0
    failed_count = 0
    
    progress_msg = await message.answer(f"🔄 Рассылаю {len(all_users)} пользователям...")
    
    for user in all_users:
        try:
            await bot.send_message(
                user['user_id'],
                f"📢 <b>Сообщение от админа:</b>\n\n"
                f"{broadcast_text}\n\n"
                f"<i>— Виталик</i>"
            )
            sent_count += 1
            await asyncio.sleep(0.05)
        except Exception as e:
            logger.error(f"Не отправить {user['user_id']}: {e}")
            failed_count += 1
    
    await progress_msg.delete()
    
    result_text = (
        f"✅ <b>Готово!</b>\n\n"
        f"✓ Отправлено: {sent_count}\n"
        f"✗ Не удалось: {failed_count}\n"
        f"📊 Всего: {len(all_users)}\n\n"
        f"<i>Отправлено {sent_count} пользователям</i>"
    )
    
    await message.answer(result_text, reply_markup=get_main_keyboard())
    await state.clear()

# ==================== ОБРАБОТЧИКИ INLINE КНОПОК ====================
@dp.callback_query(F.data == "check_balance")
async def handle_check_balance(callback: CallbackQuery):
    """Проверить баланс"""
    user_id = callback.from_user.id
    user = await get_user(user_id)
    
    if user:
        await callback.answer(f"💰 Баланс: {user['balance']}₽", show_alert=True)
    else:
        await callback.answer("❌ Используйте /start", show_alert=True)

@dp.callback_query(F.data == "main_menu")
async def handle_main_menu(callback: CallbackQuery, state: FSMContext):
    """Вернуться в меню"""
    await state.clear()
    user_id = callback.from_user.id
    user = await get_user(user_id)
    
    if user:
        await callback.message.edit_text(
            f"<b>Главное меню</b>\n\n"
            f"👤 {user['full_name']}\n"
            f"💰 {user['balance']}₽\n\n"
            f"<i>Выбери действие:</i>",
            reply_markup=get_main_keyboard()
        )
    else:
        await callback.message.edit_text(
            "Используй /start!",
            reply_markup=get_main_keyboard()
        )
    await callback.answer()

@dp.callback_query(F.data == "cancel_action")
async def handle_cancel_action(callback: CallbackQuery, state: FSMContext):
    """Отмена действия"""
    await state.clear()
    await callback.message.edit_text("❌ Отменено")
    await callback.answer()

@dp.callback_query(F.data == "refresh_profile")
async def handle_refresh_profile(callback: CallbackQuery):
    """Обновить профиль"""
    user_id = callback.from_user.id
    user = await get_user(user_id)
    
    if not user:
        await callback.answer("❌ Используйте /start", show_alert=True)
        return
    
    pills = await get_active_pills(user_id)
    pill_effect = await get_active_pills_effect(user_id)
    
    profile_text = (
        f"👤 <b>Профиль (обновлено)</b>\n\n"
        f"<b>Имя:</b> {user['full_name']}\n"
        f"<b>Username:</b> @{user['username'] or 'нет'}\n\n"
        f"<b>💰 Баланс:</b> {user['balance']}₽\n"
        f"<b>📈 Заработано:</b> {user['total_earned']}₽\n"
        f"<b>📉 Потрачено:</b> {user['total_spent']}₽\n\n"
        f"<b>📊 Статистика:</b>\n"
        f"• ⚖️ Штрафов: {user['fines_count']}\n"
        f"• 🛒 Покупок: {user['purchases_count']}\n"
        f"• 🔁 Переводов: {user['transfers_count']}\n"
        f"• 🧱 Асфальта: {user['asphalt_meters']}м\n\n"
        f"<b>💪 Толерантность:</b> {user['tolerance']}/100\n"
        f"<b>💊 Таблеток:</b> {len(pills)} активных\n"
    )
    
    if pills:
        profile_text += "\n<b>Активные таблетки:</b>\n"
        for pill in pills:
            expires_at = datetime.fromisoformat(pill['expires_at'])
            time_left = expires_at - datetime.now()
            hours_left = int(time_left.total_seconds() / 3600)
            minutes_left = int((time_left.total_seconds() % 3600) / 60)
            
            profile_text += f"• {pill['pill_name']} ({hours_left}ч {minutes_left}мин)\n"
    
    await callback.message.edit_text(profile_text, reply_markup=get_profile_keyboard())
    await callback.answer("✅ Профиль обновлен")

@dp.callback_query(F.data == "show_top")
async def handle_show_top(callback: CallbackQuery):
    """Показать топ игроков"""
    all_users = await get_all_users()
    
    if not all_users:
        await callback.answer("😔 Нет игроков", show_alert=True)
        return
    
    top_text = "🏆 <b>Топ игроков</b>\n\n"
    
    for i, user in enumerate(all_users[:10], 1):
        medal = ""
        if i == 1: medal = "🥇"
        elif i == 2: medal = "🥈"
        elif i == 3: medal = "🥉"
        else: medal = f"{i}."
        
        name = user['full_name']
        if len(name) > 12:
            name = name[:9] + "..."
        
        top_text += f"{medal} <b>{name}</b> — {user['balance']}₽\n"
    
    await callback.message.answer(top_text)
    await callback.answer()

@dp.callback_query(F.data == "go_to_shop")
async def handle_go_to_shop(callback: CallbackQuery):
    """Перейти в магазин"""
    user_id = callback.from_user.id
    user = await get_user(user_id)
    
    if not user:
        await callback.answer("❌ Используйте /start", show_alert=True)
        return
    
    shop_text = (
        f"🛒 <b>Магазин Виталика</b>\n\n"
        f"<b>💰 Баланс:</b> {user['balance']}₽\n"
        f"<b>💪 Толерантность:</b> {user.get('tolerance', 0)}/100\n\n"
        f"<b>💊 Таблетки:</b>\n"
    )
    
    for item in PILLS:
        shop_text += f"\n<b>{item['name']}</b> - {item['price']}₽\n"
        shop_text += f"<i>{item['description']}</i>\n"
    
    shop_text += "\n<b>Выбери товар:</b>"
    
    await callback.message.edit_text(shop_text, reply_markup=get_shop_keyboard())
    await callback.answer()

@dp.callback_query(F.data == "refresh_pills")
async def handle_refresh_pills(callback: CallbackQuery):
    """Обновить список таблеток"""
    user_id = callback.from_user.id
    user = await get_user(user_id)
    
    if not user:
        await callback.answer("❌ Используйте /start", show_alert=True)
        return
    
    pills = await get_active_pills(user_id)
    pill_effect = await get_active_pills_effect(user_id)
    
    if not pills:
        await callback.message.edit_text(
            "💊 <b>Нет активных таблеток</b>\n\n"
            "Купи таблетки в 🛒 Магазине!\n\n"
            f"<b>💪 Толерантность:</b> {user['tolerance']}/100\n"
            f"<i>Снижает риск побочек</i>",
            reply_markup=get_pills_keyboard()
        )
        await callback.answer("✅ Обновлено")
        return
    
    pills_text = (
        f"💊 <b>Твои таблетки (обновлено)</b>\n\n"
        f"<b>💊 Всего:</b> {len(pills)}\n"
        f"<b>📈 Множитель:</b> x{pill_effect['multiplier']:.1f}\n"
        f"<b>⚠️ Риск:</b> {pill_effect['side_effect_chance']}%\n"
        f"<b>💪 Толерантность:</b> {user['tolerance']}/100\n\n"
    )
    
    for pill in pills:
        expires_at = datetime.fromisoformat(pill['expires_at'])
        time_left = expires_at - datetime.now()
        hours_left = int(time_left.total_seconds() / 3600)
        minutes_left = int((time_left.total_seconds() % 3600) / 60)
        
        pills_text += f"• <b>{pill['pill_name']}</b>\n"
        pills_text += f"  ⏱️ {hours_left}ч {minutes_left}мин\n"
        pills_text += f"  📈 +{int(pill['effect_multiplier'] * 100)}%\n"
        pills_text += f"  ⚠️ {pill['side_effect_chance']}%\n\n"
    
    await callback.message.edit_text(pills_text, reply_markup=get_pills_keyboard())
    await callback.answer("✅ Таблетки обновлены")

# ==================== СИСТЕМА ШТРАФОВ ====================
async def schedule_fines():
    """Планировщик штрафов"""
    logger.info("⚖️ Система штрафов запущена")
    
    while True:
        try:
            # Ждем 30-60 минут
            wait_time = random.randint(1800, 3600)
            await asyncio.sleep(wait_time)
            
            all_users = await get_all_users()
            
            if not all_users:
                continue
            
            # Выбираем случайного пользователя
            target_user = random.choice(all_users)
            
            fine_amount = random.randint(50, 200)
            
            jokes = [
                f"Нарушение дресс-кода! Штраф {fine_amount}₽! 👔",
                f"Опоздание! Штраф {fine_amount}₽! ⏰",
                f"Громко дышишь! Штраф {fine_amount}₽! 😤",
                f"Слишком продуктивный! Штраф {fine_amount}₽! 🤨",
                f"Не так посмотрел! Штраф {fine_amount}₽! 👀"
            ]
            
            # Накладываем штраф
            await update_balance(
                target_user['user_id'], 
                -fine_amount, 
                'fine', 
                'Штраф от Виталика'
            )
            
            # Обновляем время
            async with aiosqlite.connect(DB_NAME) as db:
                await db.execute(
                    "UPDATE players SET last_fine = ? WHERE user_id = ?",
                    (datetime.now().isoformat(), target_user['user_id'])
                )
                await db.commit()
            
            # Уведомляем
            try:
                await bot.send_message(
                    target_user['user_id'],
                    f"⚠️ <b>ВИТАЛИК ШТРАФУЕТ!</b>\n\n"
                    f"<b>💸 Штраф:</b> -{fine_amount}₽\n"
                    f"<b>💰 Баланс:</b> {target_user['balance'] - fine_amount}₽\n\n"
                    f"💬 <i>Виталик:</i> {random.choice(jokes)}\n\n"
                    f"⚖️ <i>Жаловаться некуда!</i>"
                )
            except Exception as e:
                logger.error(f"Не уведомить о штрафе: {e}")
                
        except Exception as e:
            logger.error(f"Ошибка в системе штрафов: {e}")
            await asyncio.sleep(60)

# ==================== ЗАПУСК БОТА ====================
async def main():
    """Запуск бота"""
    logger.info("🚀 Запускаю бота...")
    
    # Инициализируем БД
    await init_db()
    
    # Запускаем штрафы в фоне
    asyncio.create_task(schedule_fines())
    
    # Запускаем бота
    logger.info("✅ Бот запущен и готов!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
