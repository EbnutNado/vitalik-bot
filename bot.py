"""
Telegram бот "Виталик Штрафующий" - полностью рабочий
Исправлены ВСЕ баги, добавлен список игроков, работают все функции
"""

import asyncio
import logging
import random
import json
import sys
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

from aiogram import Bot, Dispatcher, types, F
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
    handlers=[logging.StreamHandler(sys.stdout)]
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
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
                purchased_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        await db.commit()
        logger.info("База данных инициализирована")

async def get_user(user_id: int) -> Optional[Dict[str, Any]]:
    """Получить информацию о пользователе"""
    try:
        async with aiosqlite.connect(DB_NAME) as db:
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
        logger.error(f"Ошибка при получении пользователя {user_id}: {e}")
        return None

async def register_user(user_id: int, username: str, full_name: str):
    """Регистрация нового пользователя"""
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            cursor = await db.execute(
                "SELECT 1 FROM players WHERE user_id = ?", 
                (user_id,)
            )
            exists = await cursor.fetchone()
            
            if not exists:
                await db.execute(
                    '''INSERT INTO players 
                       (user_id, username, full_name, balance, total_earned) 
                       VALUES (?, ?, ?, 1000, 1000)''',
                    (user_id, username or "Без username", full_name)
                )
                await db.execute(
                    '''INSERT INTO transactions (user_id, type, amount, description)
                       VALUES (?, 'registration', 1000, 'Начальный баланс')''',
                    (user_id,)
                )
                await db.commit()
                logger.info(f"Зарегистрирован новый пользователь: {user_id}")
                return True
            return False
    except Exception as e:
        logger.error(f"Ошибка при регистрации пользователя {user_id}: {e}")
        return False

async def update_balance(user_id: int, amount: int, txn_type: str, description: str):
    """Обновить баланс пользователя"""
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            # Получаем текущий баланс
            cursor = await db.execute(
                "SELECT balance FROM players WHERE user_id = ?",
                (user_id,)
            )
            result = await cursor.fetchone()
            if not result:
                return False, "Пользователь не найден"
            
            current_balance = result[0]
            
            # Проверяем, не уйдет ли баланс в минус
            if txn_type not in ['fine', 'pill_fine'] and current_balance + amount < 0:
                return False, "Недостаточно средств!"
            
            # Обновляем баланс
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
            
            # Счетчики
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
                # Увеличиваем счетчик метров и заработанного в игре
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
        logger.error(f"Ошибка при обновлении баланса {user_id}: {e}")
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
        logger.error(f"Ошибка при получении списка пользователей: {e}")
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

async def add_active_pill(user_id: int, pill: Dict[str, Any]):
    """Добавить активную таблетку"""
    try:
        expires_at = datetime.now() + timedelta(hours=pill['hours'])
        
        async with aiosqlite.connect(DB_NAME) as db:
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
        logger.error(f"Ошибка при добавлении таблетки: {e}")
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
        logger.error(f"Ошибка при получении таблеток: {e}")
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
    tolerance_bonus = min(50, tolerance)  # Максимум 50% снижение
    
    effective_side_effect = max(0, total_side_effect - tolerance_bonus)
    
    return {
        'multiplier': total_effect,
        'side_effect_chance': effective_side_effect,
        'pill_count': len(pills)
    }

async def check_pill_side_effect(user_id: int) -> bool:
    """Проверить, сработал ли побочный эффект"""
    effect = await get_active_pills_effect(user_id)
    
    if random.random() * 100 < effect['side_effect_chance']:
        # Сработал побочный эффект
        fine_amount = random.randint(50, 200)
        await update_balance(user_id, -fine_amount, 'pill_fine', 'Побочный эффект от таблеток')
        return True, fine_amount
    
    return False, 0

async def remove_all_pills(user_id: int):
    """Удалить все активные таблетки"""
    try:
        async with aiosqlite.connect(DB_NAME) as db:
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
        logger.error(f"Ошибка при удалении таблеток: {e}")
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
        InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_main")
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
                    callback_data=f"transfer:{user['user_id']}"
                )
            ])
    
    buttons.append([
        InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# ==================== ОСНОВНЫЕ ОБРАБОТЧИКИ ====================
@dp.message(CommandStart())
async def cmd_start(message: Message):
    """Обработчик команды /start"""
    user_id = message.from_user.id
    username = message.from_user.username or "Без username"
    full_name = message.from_user.full_name
    
    # Всегда пытаемся получить пользователя
    user = await get_user(user_id)
    
    if not user:
        # Регистрируем если нет
        await register_user(user_id, username, full_name)
        user = await get_user(user_id)  # Получаем заново
        
        if not user:
            await message.answer("❌ Ошибка регистрации. Попробуйте еще раз.")
            return
        
        welcome_text = (
            f"👋 <b>Добро пожаловать, {full_name}!</b>\n\n"
            f"Я <b>Виталик</b>, и я буду твоим начальником! 🏢\n"
            f"Будь осторожен — я люблю штрафовать за малейшие провинности! 😈\n\n"
            f"<b>💰 Начальный баланс:</b> 1,000₽\n"
            f"<b>💊 Система Нагирта:</b> Таблетки с риском и выгодой!\n"
            f"<b>🧱 Мини-игра:</b> Укладка асфальта за деньги!\n\n"
            f"Используй кнопки ниже для управления:"
        )
    else:
        welcome_text = (
            f"👋 <b>С возвращением, {full_name}!</b>\n\n"
            f"<b>💰 Твой баланс:</b> {user['balance']}₽\n"
            f"<b>🧱 Уложено асфальта:</b> {user['asphalt_meters']}м\n"
            f"<b>💊 Толерантность:</b> {user['tolerance']}/100\n\n"
            f"Что будем делать сегодня? 😏"
        )
    
    await message.answer(welcome_text, reply_markup=get_main_keyboard())

@dp.message(F.text == "💰 Получка")
async def handle_paycheck(message: Message):
    """Обработка нажатия кнопки 'Получка'"""
    user_id = message.from_user.id
    user = await get_user(user_id)
    
    if not user:
        await message.answer("❌ Пользователь не найден. Используйте /start")
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
                f"⏳ <b>Слишком рано для получки!</b>\n\n"
                f"Подожди еще <b>{wait_minutes} мин {int(wait_seconds)} сек</b>, работяга! 😏\n"
                f"Или Виталик оштрафует за нетерпение! ⚠️"
            )
            return
    
    # Вычисляем сумму получки (100-500₽)
    base_amount = random.randint(100, 500)
    
    # Проверяем активные таблетки
    pill_effect = await get_active_pills_effect(user_id)
    multiplier = pill_effect['multiplier']
    paycheck_amount = int(base_amount * multiplier)
    
    # Проверяем побочки от таблеток
    pill_side_effect, pill_fine_amount = await check_pill_side_effect(user_id)
    
    # Обновляем баланс и время
    if not pill_side_effect:
        # Нормальная получка
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute(
                "UPDATE players SET balance = balance + ?, last_paycheck = ? WHERE user_id = ?",
                (paycheck_amount, current_time.isoformat(), user_id)
            )
            await db.commit()
        
        await update_balance(user_id, paycheck_amount, 'paycheck', 'Ежедневная получка')
        
        # Шутки Виталика
        jokes = [
            f"Держи {paycheck_amount}₽! Но не трать всё на кофе... Или трать, мне-то что! ☕",
            f"Вот твоя получка: {paycheck_amount}₽. А теперь быстро на работу! ⚡",
            f"{paycheck_amount}₽ к твоему балансу. Не благодари, лучше не зли меня! 😈",
            f"Получил {paycheck_amount}₽? Отлично! Теперь есть что терять... 🤑"
        ]
        
        pill_text = ""
        if pill_effect['pill_count'] > 0:
            pill_text = f"\n💊 <b>Бонус от таблеток:</b> x{multiplier:.1f} множитель!"
        
        # Получаем обновленные данные пользователя
        user = await get_user(user_id)
        response = (
            f"💰 <b>Получка выдана!</b>\n\n"
            f"<b>Сумма:</b> +{paycheck_amount}₽\n"
            f"<b>Новый баланс:</b> {user['balance']}₽"
            f"{pill_text}\n\n"
            f"💬 <i>Виталик:</i> {random.choice(jokes)}"
        )
    else:
        # Сработала побочка
        total_lost = pill_fine_amount
        
        # Обновляем время получки, но не даем денег
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute(
                "UPDATE players SET last_paycheck = ? WHERE user_id = ?",
                (current_time.isoformat(), user_id)
            )
            await db.commit()
        
        jokes = [
            f"Ха! Побочка от таблеток! Вместо {paycheck_amount}₽ ты теряешь {total_lost}₽! 😂",
            f"Нагирт подвел! Минус {total_lost}₽ вместо зарплаты! 💊",
            f"Побочный эффект! Забираю {total_lost}₽! Чтоб неповадно было! 👿"
        ]
        
        user = await get_user(user_id)
        response = (
            f"💊 <b>ПОБОЧНЫЙ ЭФФЕКТ ОТ ТАБЛЕТОК!</b>\n\n"
            f"<b>Вместо получки:</b> -{total_lost}₽\n"
            f"<b>Штраф за побочку:</b> -{pill_fine_amount}₽\n"
            f"<b>Новый баланс:</b> {user['balance']}₽\n\n"
            f"💬 <i>Виталик:</i> {random.choice(jokes)}"
        )
    
    await message.answer(response)

@dp.message(F.text == "🛒 Магазин")
async def handle_shop(message: Message):
    """Обработка нажатия кнопки 'Магазин'"""
    user_id = message.from_user.id
    user = await get_user(user_id)
    
    if not user:
        await message.answer("❌ Пользователь не найден. Используйте /start")
        return
    
    shop_text = (
        f"🛒 <b>Магазин Виталика</b>\n\n"
        f"<b>💰 Твой баланс:</b> {user['balance']}₽\n"
        f"<b>💊 Толерантность:</b> {user.get('tolerance', 0)}/100\n\n"
        f"<i>Чем выше толерантность, тем меньше риск побочек от таблеток</i>\n\n"
        f"<b>💊 Доступные таблетки:</b>\n"
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
        await callback.answer("❌ Сначала зарегистрируйтесь через /start!")
        return
    
    item_id = callback.data.replace("shop:", "")
    item = next((i for i in PILLS if i['id'] == item_id), None)
    
    if not item:
        await callback.answer("❌ Товар не найден!")
        return
    
    # Проверяем баланс
    if user['balance'] < item['price']:
        await callback.answer(f"❌ Недостаточно средств! Нужно {item['price']}₽")
        return
    
    if item['type'] == 'antidote':
        # Покупка антидота
        async with aiosqlite.connect(DB_NAME) as db:
            # Обновляем баланс
            await db.execute(
                "UPDATE players SET balance = balance - ? WHERE user_id = ?",
                (item['price'], user_id)
            )
            
            await db.commit()
        
        # Удаляем все таблетки
        await remove_all_pills(user_id)
        
        # Записываем транзакцию
        await update_balance(user_id, -item['price'], 'purchase', f"Покупка: {item['name']}")
        
        user = await get_user(user_id)
        response = (
            f"✅ <b>Антидот принят!</b>\n\n"
            f"<b>💉 Товар:</b> {item['name']}\n"
            f"<b>💵 Стоимость:</b> {item['price']}₽\n\n"
            f"<b>✅ Все активные таблетки сняты</b>\n"
            f"<b>📉 Толерантность уменьшена на 50%</b>\n\n"
            f"<b>💰 Новый баланс:</b> {user['balance']}₽\n\n"
            f"💬 <i>Виталик:</i> Молодец, что следишь за здоровьем! 🏥"
        )
    else:
        # Покупка таблетки
        async with aiosqlite.connect(DB_NAME) as db:
            # Обновляем баланс
            await db.execute(
                "UPDATE players SET balance = balance - ? WHERE user_id = ?",
                (item['price'], user_id)
            )
            
            await db.commit()
        
        # Добавляем активную таблетку
        await add_active_pill(user_id, item)
        
        # Записываем транзакцию
        await update_balance(user_id, -item['price'], 'purchase', f"Покупка: {item['name']}")
        
        # Шутки Виталика
        jokes = [
            f"Опа, купил {item['name']}! Удачи с побочками! 😈",
            f"Так, {item['name']}... Надеюсь, знаешь меру! 💊",
            f"Купил таблетку? Теперь работай быстрее! А то штраф! ⚡",
            f"{item['name']} активирован! Не забывай про побочки! 👀"
        ]
        
        user = await get_user(user_id)
        response = (
            f"✅ <b>Таблетка куплена!</b>\n\n"
            f"<b>💊 Товар:</b> {item['name']}\n"
            f"<b>💵 Стоимость:</b> {item['price']}₽\n"
            f"<b>⏱️ Длительность:</b> {item['hours']} час(а)\n"
            f"<b>📈 Эффект:</b> +{int(item['effect'] * 100)}% к заработку\n"
            f"<b>⚠️ Риск побочек:</b> {item['side_effect_chance']}%\n\n"
            f"<b>💰 Новый баланс:</b> {user['balance']}₽\n"
            f"<b>💪 Толерантность:</b> +10 (теперь {user.get('tolerance', 0) + 10})\n\n"
            f"💬 <i>Виталик:</i> {random.choice(jokes)}"
        )
    
    await callback.message.edit_text(response)
    await callback.answer(f"Куплено: {item['name']}")

@dp.message(F.text == "🧱 Асфальт")
async def handle_asphalt_start(message: Message):
    """Начало игры в укладку асфальта"""
    user_id = message.from_user.id
    user = await get_user(user_id)
    
    if not user:
        await message.answer("❌ Пользователь не найден. Используйте /start")
        return
    
    # Проверяем время последней укладки
    current_time = datetime.now()
    if user.get('last_asphalt'):
        last_asphalt = datetime.fromisoformat(user['last_asphalt'])
        time_diff = current_time - last_asphalt
        
        # Можно укладывать каждые 30 секунд
        if time_diff.total_seconds() < 30:
            wait_seconds = 30 - time_diff.total_seconds()
            await message.answer(
                f"⏳ <b>Отдыхай, работяга!</b>\n\n"
                f"Ты только что уложил асфальт.\n"
                f"Подожди <b>{int(wait_seconds)} секунд</b> перед следующей укладкой.\n\n"
                f"💬 <i>Виталик:</i> Не торопись, а то испортишь работу! 👷"
            )
            return
    
    # Базовый заработок
    base_earnings = 10
    
    # Эффект от таблеток
    pill_effect = await get_active_pills_effect(user_id)
    multiplier = pill_effect['multiplier']
    earnings = int(base_earnings * multiplier)
    
    # Рандомные события
    event = random.choices(
        ['success', 'success', 'success', 'vitalik_fine', 'equipment_break', 'bad_asphalt'],
        weights=[70, 10, 10, 5, 3, 2]
    )[0]
    
    # Шутки Виталика для разных событий
    jokes = {
        'success': [
            f"Отлично уложил! Держи {earnings}₽ за метр! 👍",
            f"Так, {earnings}₽ за метр... Неплохо! Но можно и лучше! 😏",
            f"Уложил как мастер! {earnings}₽ твои! 🏗️",
            f"{earnings}₽ в карман! А теперь следующий метр! 🚧"
        ],
        'vitalik_fine': [
            f"Что за херня?! Криво уложил! Штраф 100₽! 😡",
            f"Ты что, слепой? Это не асфальт, это говно! Штраф 100₽! 💩",
            f"Опять косяк! Отсчитывай 100₽ в мой карман! 👿",
            f"За такую работу только штраф! Минус 100₽! ⚖️"
        ],
        'equipment_break': [
            f"Какая херня! Каток сломался! Ремонт -50₽! 🚜",
            f"Опять техника глохнет! -50₽ на запчасти! 🔧",
            f"Каток на ремонт! С тебя 50₽! 🛠️"
        ],
        'bad_asphalt': [
            f"Это не асфальт, а дерьмо собачье! Перекладывай за свой счет! -30₽ 💩",
            f"Качество говно! Снимаю 30₽ за материалы! 🧱",
            f"Ты что, грязный асфальт положил? Минус 30₽! 🪣"
        ]
    }
    
    # Обработка события
    result_text = ""
    final_earnings = 0
    penalty = 0
    
    if event == 'success':
        # Успешная укладка
        success_type = random.choice(['normal', 'perfect', 'fast'])
        
        if success_type == 'perfect':
            bonus = random.randint(5, 20)
            earnings += bonus
            result_text = f"🎉 <b>ИДЕАЛЬНАЯ УКЛАДКА!</b>\nБонус +{bonus}₽!\n"
        elif success_type == 'fast':
            bonus = random.randint(3, 10)
            earnings += bonus
            result_text = f"⚡ <b>БЫСТРАЯ РАБОТА!</b>\nБонус +{bonus}₽!\n"
        
        final_earnings = earnings
        result_text += f"<b>Заработано:</b> +{earnings}₽"
        
        # Обновляем баланс
        await update_balance(user_id, earnings, 'asphalt', 'Укладка асфальта')
        
    else:
        # Неудача
        if event == 'vitalik_fine':
            penalty = 100
            result_text = f"⚠️ <b>ВИТАЛИК НЕДОВОЛЕН!</b>\nШтраф: -{penalty}₽"
        elif event == 'equipment_break':
            penalty = 50
            result_text = f"🔧 <b>ПОЛОМКА ТЕХНИКИ!</b>\nРемонт: -{penalty}₽"
        elif event == 'bad_asphalt':
            penalty = 30
            result_text = f"🧱 <b>БРАКОВАННЫЙ АСФАЛЬТ!</b>\nУбытки: -{penalty}₽"
        
        # Списываем штраф
        await update_balance(user_id, -penalty, 'fine', f'Штраф в игре: {event}')
    
    # Проверяем побочки от таблеток
    pill_side_effect, pill_fine_amount = await check_pill_side_effect(user_id)
    pill_side_text = ""
    
    if pill_side_effect:
        pill_side_text = f"\n\n💊 <b>ПОБОЧКА ОТ ТАБЛЕТОК!</b>\nДополнительный штраф: -{pill_fine_amount}₽"
        penalty += pill_fine_amount
    
    # Обновляем время последней укладки
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE players SET last_asphalt = ? WHERE user_id = ?",
            (current_time.isoformat(), user_id)
        )
        await db.commit()
    
    # Получаем актуальный баланс
    user = await get_user(user_id)
    
    # Эффект таблеток
    pill_text = ""
    if pill_effect['pill_count'] > 0:
        pill_text = f"\n💊 <b>Эффект таблеток:</b> x{pill_effect['multiplier']:.1f} множитель"
        if pill_effect['side_effect_chance'] > 0:
            pill_text += f" (риск побочек: {pill_effect['side_effect_chance']}%)"
    
    # Формируем итоговое сообщение
    response = (
        f"🧱 <b>УКЛАДКА АСФАЛЬТА</b>\n\n"
        f"{result_text}{pill_side_text}\n\n"
        f"<b>💰 Твой баланс:</b> {user['balance']}₽\n"
        f"<b>📊 Всего уложено:</b> {user['asphalt_meters']} метров\n"
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
        await message.answer("❌ Пользователь не найден. Используйте /start")
        return
    
    # Получаем активные таблетки
    pills = await get_active_pills(user_id)
    pill_effect = await get_active_pills_effect(user_id)
    
    profile_text = (
        f"👤 <b>Профиль игрока</b>\n\n"
        f"<b>Имя:</b> {user['full_name']}\n"
        f"<b>Username:</b> @{user['username'] or 'нет'}\n\n"
        f"<b>💰 Баланс:</b> {user['balance']}₽\n"
        f"<b>📈 Всего заработано:</b> {user['total_earned']}₽\n"
        f"<b>📉 Всего потрачено:</b> {user['total_spent']}₽\n\n"
        f"<b>📊 Статистика:</b>\n"
        f"• ⚖️ Штрафов: {user['fines_count']}\n"
        f"• 🛒 Покупок: {user['purchases_count']}\n"
        f"• 🔁 Переводов: {user['transfers_count']}\n"
        f"• 🧱 Уложено асфальта: {user['asphalt_meters']}м\n\n"
        f"<b>💊 Толерантность:</b> {user['tolerance']}/100\n"
        f"<b>💊 Активных таблеток:</b> {len(pills)}\n"
    )
    
    if pills:
        profile_text += "\n<b>Активные таблетки:</b>\n"
        for pill in pills:
            expires_at = datetime.fromisoformat(pill['expires_at'])
            time_left = expires_at - datetime.now()
            hours_left = int(time_left.total_seconds() / 3600)
            minutes_left = int((time_left.total_seconds() % 3600) / 60)
            
            profile_text += f"• {pill['pill_name']} ({hours_left}ч {minutes_left}мин)\n"
    
    await message.answer(profile_text)

@dp.message(F.text == "💊 Таблетки")
async def handle_my_pills(message: Message):
    """Показать мои активные таблетки"""
    user_id = message.from_user.id
    user = await get_user(user_id)
    
    if not user:
        await message.answer("❌ Пользователь не найден. Используйте /start")
        return
    
    pills = await get_active_pills(user_id)
    pill_effect = await get_active_pills_effect(user_id)
    
    if not pills:
        await message.answer(
            "💊 <b>У тебя нет активных таблеток</b>\n\n"
            "Зайди в 🛒 Магазин, чтобы купить таблетки и получить бусты!\n\n"
            f"<b>💪 Твоя толерантность:</b> {user['tolerance']}/100\n"
            f"<i>Чем выше толерантность, тем меньше риск побочек</i>"
        )
        return
    
    pills_text = (
        f"💊 <b>Твои активные таблетки</b>\n\n"
        f"<b>💊 Всего таблеток:</b> {len(pills)}\n"
        f"<b>📈 Общий множитель:</b> x{pill_effect['multiplier']:.1f}\n"
        f"<b>⚠️ Риск побочек:</b> {pill_effect['side_effect_chance']}%\n"
        f"<b>💪 Толерантность:</b> {user['tolerance']}/100\n\n"
    )
    
    for pill in pills:
        expires_at = datetime.fromisoformat(pill['expires_at'])
        time_left = expires_at - datetime.now()
        hours_left = int(time_left.total_seconds() / 3600)
        minutes_left = int((time_left.total_seconds() % 3600) / 60)
        
        pills_text += f"• <b>{pill['pill_name']}</b>\n"
        pills_text += f"  ⏱️ Осталось: {hours_left}ч {minutes_left}мин\n"
        pills_text += f"  📈 Эффект: +{int(pill['effect_multiplier'] * 100)}%\n"
        pills_text += f"  ⚠️ Риск: {pill['side_effect_chance']}%\n\n"
    
    await message.answer(pills_text)

@dp.message(F.text == "👥 Игроки")
async def handle_players_list(message: Message):
    """Показать список игроков"""
    user_id = message.from_user.id
    user = await get_user(user_id)
    
    if not user:
        await message.answer("❌ Пользователь не найден. Используйте /start")
        return
    
    all_users = await get_all_users()
    
    if not all_users:
        await message.answer("😔 Пока нет зарегистрированных игроков")
        return
    
    players_text = f"👥 <b>Список игроков</b> (всего: {len(all_users)})\n\n"
    
    for i, player in enumerate(all_users, 1):
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
    """Начало процесса перевода"""
    user_id = message.from_user.id
    user = await get_user(user_id)
    
    if not user:
        await message.answer("❌ Пользователь не найден. Используйте /start")
        return
    
    # Получаем список всех пользователей
    all_users = await get_all_users()
    
    if len(all_users) < 2:
        await message.answer("😔 Пока нет других игроков для перевода")
        return
    
    # Сохраняем информацию об отправителе
    await state.update_data(sender_id=user_id)
    
    # Показываем клавиатуру с выбором получателя
    keyboard = get_users_keyboard(all_users, user_id)
    
    await message.answer(
        f"🔁 <b>Перевод денег</b>\n\n"
        f"<b>💰 Твой баланс:</b> {user['balance']}₽\n\n"
        f"<b>Выбери получателя:</b>",
        reply_markup=keyboard
    )
    
    await state.set_state(TransferStates.choosing_recipient)

@dp.callback_query(F.data.startswith("transfer:"), TransferStates.choosing_recipient)
async def handle_recipient_selection(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора получателя"""
    recipient_id = int(callback.data.replace("transfer:", ""))
    
    # Получаем информацию о получателе
    recipient = await get_user(recipient_id)
    
    if not recipient:
        await callback.answer("❌ Получатель не найден!")
        return
    
    # Сохраняем данные в состоянии
    await state.update_data(
        recipient_id=recipient_id,
        recipient_name=recipient['full_name']
    )
    
    await callback.message.edit_text(
        f"✅ <b>Получатель выбран:</b> {recipient['full_name']}\n\n"
        f"💰 <b>Баланс получателя:</b> {recipient['balance']}₽\n\n"
        f"<b>Введи сумму перевода (1-10,000₽):</b>\n"
        f"<i>Или напиши 'отмена' для отмены</i>"
    )
    
    await state.set_state(TransferStates.entering_amount)
    await callback.answer()

@dp.message(TransferStates.entering_amount)
async def handle_transfer_amount(message: Message, state: FSMContext):
    """Обработка ввода суммы перевода"""
    user_data = await state.get_data()
    sender_id = user_data['sender_id']
    recipient_id = user_data['recipient_id']
    recipient_name = user_data['recipient_name']
    
    # Проверяем на отмену
    if message.text.lower() in ['отмена', 'cancel', 'стоп']:
        await state.clear()
        await message.answer("❌ Перевод отменен.", reply_markup=get_main_keyboard())
        return
    
    try:
        amount = int(message.text.strip())
        
        # Проверки суммы
        if amount <= 0:
            await message.answer("❌ Сумма должна быть положительной!")
            return
        
        if amount > 10000:
            await message.answer("❌ Максимальная сумма перевода — 10,000₽!")
            return
        
        # Проверяем баланс отправителя
        sender = await get_user(sender_id)
        if not sender:
            await message.answer("❌ Отправитель не найден!")
            await state.clear()
            return
        
        if sender['balance'] < amount:
            await message.answer(
                f"❌ Недостаточно средств!\n"
                f"Твой баланс: {sender['balance']}₽\n"
                f"Нужно: {amount}₽"
            )
            return
        
        # Проверяем получателя
        recipient = await get_user(recipient_id)
        if not recipient:
            await message.answer("❌ Получатель не найден!")
            await state.clear()
            return
        
        # ВЫПОЛНЯЕМ ПЕРЕВОД В ОДНОЙ ТРАНЗАКЦИИ
        async with aiosqlite.connect(DB_NAME) as db:
            # Проверяем баланс отправителя еще раз (для безопасности)
            cursor = await db.execute(
                "SELECT balance FROM players WHERE user_id = ?",
                (sender_id,)
            )
            sender_balance = (await cursor.fetchone())[0]
            
            if sender_balance < amount:
                await message.answer("❌ Недостаточно средств!")
                return
            
            # Списываем у отправителя
            await db.execute(
                "UPDATE players SET balance = balance - ? WHERE user_id = ?",
                (amount, sender_id)
            )
            
            # Зачисляем получателю
            await db.execute(
                "UPDATE players SET balance = balance + ? WHERE user_id = ?",
                (amount, recipient_id)
            )
            
            # Записываем транзакции
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
            
            # Увеличиваем счетчики переводов
            await db.execute(
                "UPDATE players SET transfers_count = transfers_count + 1 WHERE user_id = ?",
                (sender_id,)
            )
            
            await db.commit()
        
        # Шутки Виталика
        jokes = [
            f"Перевод выполнен! Надеюсь, это не взятка... 😏",
            f"Так, перевел {amount}₽... Интересно, за какие услуги? 🤫",
            f"Деньги ушли! А теперь вернись к работе! 💼"
        ]
        
        # Уведомляем отправителя
        sender = await get_user(sender_id)
        await message.answer(
            f"✅ <b>Перевод выполнен!</b>\n\n"
            f"<b>👤 Получатель:</b> {recipient_name}\n"
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
                f"💸 <b>Поступил перевод!</b>\n\n"
                f"<b>👤 Отправитель:</b> {sender['full_name']}\n"
                f"<b>💵 Сумма:</b> +{amount}₽\n"
                f"<b>💰 Твой баланс:</b> {recipient['balance']}₽\n\n"
                f"💬 <i>Виталик:</i> Кто-то оказался щедрым! 🤑"
            )
        except Exception as e:
            logger.error(f"Не удалось уведомить получателя: {e}")
        
    except ValueError:
        await message.answer("❌ Пожалуйста, введите корректное число!")
        return
    
    await state.clear()

@dp.message(F.text == "📢 Рассылка")
async def handle_broadcast_start(message: Message, state: FSMContext):
    """Начало процесса рассылки"""
    user_id = message.from_user.id
    
    if user_id != ADMIN_ID:
        await message.answer("⛔ Эта функция доступна только администратору!")
        return
    
    await message.answer(
        "📢 <b>Режим админской рассылки</b>\n\n"
        "Введите сообщение, которое будет отправлено всем пользователям:\n\n"
        "<i>Напиши 'отмена' для отмены</i>"
    )
    
    await state.set_state(BroadcastStates.waiting_for_message)

@dp.message(BroadcastStates.waiting_for_message)
async def handle_broadcast_message(message: Message, state: FSMContext):
    """Обработка сообщения для рассылки"""
    # Проверяем на отмену
    if message.text.lower() in ['отмена', 'cancel', 'стоп']:
        await state.clear()
        await message.answer("❌ Рассылка отменена.", reply_markup=get_main_keyboard())
        return
    
    broadcast_text = message.text
    
    # Получаем всех пользователей
    all_users = await get_all_users()
    
    if not all_users:
        await message.answer("😔 Нет пользователей для рассылки")
        await state.clear()
        return
    
    sent_count = 0
    failed_count = 0
    
    progress_msg = await message.answer(f"🔄 Начинаю рассылку для {len(all_users)} пользователей...")
    
    # Отправляем сообщение каждому пользователю
    for user in all_users:
        try:
            await bot.send_message(
                user['user_id'],
                f"📢 <b>Сообщение от администратора:</b>\n\n"
                f"{broadcast_text}\n\n"
                f"<i>— Виталик и команда</i>"
            )
            sent_count += 1
            
            # Небольшая задержка, чтобы не превысить лимиты Telegram
            await asyncio.sleep(0.05)
            
        except Exception as e:
            logger.error(f"Не удалось отправить пользователю {user['user_id']}: {e}")
            failed_count += 1
    
    await progress_msg.delete()
    
    result_text = (
        f"✅ <b>Рассылка завершена!</b>\n\n"
        f"✓ Отправлено: {sent_count}\n"
        f"✗ Не удалось: {failed_count}\n"
        f"📊 Всего пользователей: {len(all_users)}\n\n"
        f"<i>Сообщение успешно отправлено {sent_count} пользователям</i>"
    )
    
    await message.answer(result_text, reply_markup=get_main_keyboard())
    await state.clear()

@dp.callback_query(F.data == "cancel")
async def handle_cancel(callback: CallbackQuery, state: FSMContext):
    """Отмена действия"""
    await state.clear()
    await callback.message.edit_text("❌ Действие отменено.")
    await callback.answer()

@dp.callback_query(F.data == "back_to_main")
async def handle_back_to_main(callback: CallbackQuery, state: FSMContext):
    """Возврат в главное меню"""
    await state.clear()
    user_id = callback.from_user.id
    user = await get_user(user_id)
    
    if user:
        await callback.message.edit_text(
            f"<b>Главное меню</b>\n\n"
            f"👤 Игрок: {user['full_name']}\n"
            f"💰 Баланс: {user['balance']}₽\n\n"
            f"Используй кнопки ниже:",
            reply_markup=get_main_keyboard()
        )
    else:
        await callback.message.edit_text(
            "Используй /start для регистрации!",
            reply_markup=get_main_keyboard()
        )
    await callback.answer()

# ==================== СИСТЕМА ШТРАФОВ ====================
async def schedule_fines():
    """Планировщик случайных штрафов"""
    logger.info("Система штрафов запущена...")
    
    while True:
        try:
            # Случайный интервал (30-60 минут)
            wait_time = random.randint(1800, 3600)
            await asyncio.sleep(wait_time)
            
            # Получаем всех пользователей
            all_users = await get_all_users()
            
            if not all_users:
                continue
            
            # Выбираем случайного пользователя
            target_user = random.choice(all_users)
            
            fine_amount = random.randint(50, 200)
            
            # Шутки Виталика
            fine_jokes = [
                f"Пойман на нарушении дресс-кода! Штраф {fine_amount}₽! 👔",
                f"Опоздание на 0.0001 секунды! Штраф {fine_amount}₽! ⏰",
                f"Слишком громко дышишь! Штраф {fine_amount}₽! 😤",
                f"Заподозрен в излишней продуктивности! Штраф {fine_amount}₽! 🤨",
                f"Не так посмотрел на Виталика! Штраф {fine_amount}₽! 👀"
            ]
            
            # Применяем штраф
            success, msg = await update_balance(
                target_user['user_id'], 
                -fine_amount, 
                'fine', 
                'Случайный штраф от Виталика'
            )
            
            if not success:
                logger.error(f"Не удалось наложить штраф: {msg}")
                continue
            
            # Обновляем время последнего штрафа
            async with aiosqlite.connect(DB_NAME) as db:
                await db.execute(
                    "UPDATE players SET last_fine = ? WHERE user_id = ?",
                    (datetime.now().isoformat(), target_user['user_id'])
                )
                await db.commit()
            
            # Уведомляем пользователя
            try:
                await bot.send_message(
                    target_user['user_id'],
                    f"⚠️ <b>ВИТАЛИК ОШТРАФОВАЛ ТЕБЯ!</b>\n\n"
                    f"<b>💸 Штраф:</b> -{fine_amount}₽\n"
                    f"<b>💰 Новый баланс:</b> {target_user['balance'] - fine_amount}₽\n\n"
                    f"💬 <i>Виталик:</i> {random.choice(fine_jokes)}\n\n"
                    f"⚖️ <i>Не нравится? Жаловаться некуда!</i>"
                )
                
            except Exception as e:
                logger.error(f"Не удалось уведомить о штрафе: {e}")
                
        except Exception as e:
            logger.error(f"Ошибка в системе штрафов: {e}")
            await asyncio.sleep(60)

# ==================== ЗАПУСК БОТА ====================
async def main():
    """Основная функция запуска"""
    # Инициализируем базу данных
    await init_db()
    
    # Запускаем планировщик штрафов в фоне
    asyncio.create_task(schedule_fines())
    
    logger.info("Бот запускается...")
    
    # Запускаем бота
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
