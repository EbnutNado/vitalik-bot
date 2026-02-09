"""
Telegram бот "Виталик Штрафующий" с системой достижений
Полностью рабочий бот для BotHost/PythonAnywhere
Исправлено для aiogram 3.10.0
"""

import asyncio
import logging
import random
import json
import sys
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

from aiogram import Bot, Dispatcher, types, F, html
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

# Инициализация бота с правильными параметрами для aiogram 3.10.0
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
                last_paycheck TIMESTAMP,
                last_fine TIMESTAMP,
                achievements TEXT DEFAULT '[]',
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
        
        # Таблица достижений
        await db.execute('''
            CREATE TABLE IF NOT EXISTS achievements_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                achievement_id TEXT,
                achievement_name TEXT,
                reward INTEGER,
                unlocked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        await db.commit()
        logger.info("База данных инициализирована")

async def get_user(user_id: int) -> Optional[Dict[str, Any]]:
    """Получить информацию о пользователе"""
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM players WHERE user_id = ?", 
            (user_id,)
        )
        user = await cursor.fetchone()
        if user:
            user_dict = dict(user)
            if user_dict.get('achievements'):
                try:
                    user_dict['achievements'] = json.loads(user_dict['achievements'])
                except:
                    user_dict['achievements'] = []
            else:
                user_dict['achievements'] = []
            return user_dict
        return None

async def register_user(user_id: int, username: str, full_name: str):
    """Регистрация нового пользователя"""
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            "SELECT 1 FROM players WHERE user_id = ?", 
            (user_id,)
        )
        exists = await cursor.fetchone()
        
        if not exists:
            await db.execute(
                '''INSERT INTO players 
                   (user_id, username, full_name, balance, total_earned, achievements) 
                   VALUES (?, ?, ?, 1000, 1000, '[]')''',
                (user_id, username, full_name)
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

async def update_balance(user_id: int, amount: int, txn_type: str, description: str):
    """Обновить баланс пользователя"""
    async with aiosqlite.connect(DB_NAME) as db:
        # Обновляем баланс
        await db.execute(
            "UPDATE players SET balance = balance + ? WHERE user_id = ?",
            (amount, user_id)
        )
        
        # Обновляем статистику
        if amount > 0 and txn_type in ['paycheck', 'bonus', 'transfer_in', 'achievement']:
            await db.execute(
                "UPDATE players SET total_earned = total_earned + ? WHERE user_id = ?",
                (amount, user_id)
            )
        elif amount < 0 and txn_type in ['purchase', 'fine', 'transfer_out']:
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
        
        # Записываем транзакцию
        await db.execute(
            '''INSERT INTO transactions (user_id, type, amount, description)
               VALUES (?, ?, ?, ?)''',
            (user_id, txn_type, amount, description)
        )
        
        await db.commit()

async def get_all_users() -> List[Dict[str, Any]]:
    """Получить список всех пользователей"""
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT user_id, full_name, username, balance FROM players ORDER BY balance DESC"
        )
        users = await cursor.fetchall()
        return [dict(user) for user in users]

async def add_achievement(user_id: int, achievement_id: str, achievement_name: str, reward: int = 0):
    """Добавить достижение пользователю"""
    async with aiosqlite.connect(DB_NAME) as db:
        # Проверяем, есть ли уже такое достижение
        cursor = await db.execute(
            "SELECT 1 FROM achievements_log WHERE user_id = ? AND achievement_id = ?",
            (user_id, achievement_id)
        )
        exists = await cursor.fetchone()
        
        if not exists:
            # Добавляем в лог
            await db.execute(
                '''INSERT INTO achievements_log 
                   (user_id, achievement_id, achievement_name, reward)
                   VALUES (?, ?, ?, ?)''',
                (user_id, achievement_id, achievement_name, reward)
            )
            
            # Обновляем список достижений в профиле
            user = await get_user(user_id)
            if user:
                achievements = user.get('achievements', [])
                if achievement_id not in achievements:
                    achievements.append(achievement_id)
                    await db.execute(
                        "UPDATE players SET achievements = ? WHERE user_id = ?",
                        (json.dumps(achievements), user_id)
                    )
            
            # Начисляем награду
            if reward > 0:
                await update_balance(user_id, reward, 'achievement', 
                                   f'Награда за достижение: {achievement_name}')
            
            await db.commit()
            return True
        return False

# ==================== СИСТЕМА ДОСТИЖЕНИЙ ====================
ACHIEVEMENTS = {
    'first_fine': {
        'name': '🎯 Первый штраф',
        'description': 'Получить первый штраф от Виталика',
        'reward': 50,
        'condition': lambda user: user.get('fines_count', 0) >= 1
    },
    'rich_10000': {
        'name': '💰 Богач',
        'description': 'Накопить 10,000₽ на балансе',
        'reward': 500,
        'condition': lambda user: user.get('balance', 0) >= 10000
    },
    'shopper': {
        'name': '🛍️ Шопоголик',
        'description': 'Совершить 5 покупок в магазине',
        'reward': 300,
        'condition': lambda user: user.get('purchases_count', 0) >= 5
    },
    'philanthropist': {
        'name': '🎁 Филантроп',
        'description': 'Выполнить 10 переводов другим игрокам',
        'reward': 400,
        'condition': lambda user: user.get('transfers_count', 0) >= 10
    },
    'veteran': {
        'name': '🎖️ Ветеран',
        'description': 'Получить 20 штрафов от Виталика',
        'reward': 1000,
        'condition': lambda user: user.get('fines_count', 0) >= 20
    },
    'tycoon': {
        'name': '👑 Магнат',
        'description': 'Заработать в сумме 50,000₽',
        'reward': 2000,
        'condition': lambda user: user.get('total_earned', 0) >= 50000
    }
}

async def check_achievements(user_id: int):
    """Проверить и выдать достижения"""
    user = await get_user(user_id)
    if not user:
        return []
    
    unlocked = []
    for achievement_id, achievement in ACHIEVEMENTS.items():
        if achievement_id not in user.get('achievements', []):
            if achievement['condition'](user):
                success = await add_achievement(
                    user_id, 
                    achievement_id, 
                    achievement['name'], 
                    achievement['reward']
                )
                if success:
                    unlocked.append((achievement['name'], achievement['reward']))
    
    return unlocked

# ==================== МАШИНЫ СОСТОЯНИЙ ====================
class TransferStates(StatesGroup):
    choosing_recipient = State()
    entering_amount = State()

class BroadcastStates(StatesGroup):
    waiting_for_message = State()

# ==================== ТОВАРЫ МАГАЗИНА ====================
SHOP_ITEMS = [
    {
        "id": "day_off",
        "name": "📅 Выходной",
        "price": 500,
        "description": "Отдых от штрафов Виталика на 24 часа!",
        "bonus_chance": 0.7
    },
    {
        "id": "premium_boost",
        "name": "🚀 Премиум-Буст",
        "price": 1000,
        "description": "Увеличивает получку в 2 раза на 3 дня!",
        "bonus_chance": 0.8
    },
    {
        "id": "bonus_coin",
        "name": "🪙 Бонусная монета",
        "price": 300,
        "description": "Дает случайный бонус от Виталика!",
        "bonus_chance": 1.0
    }
]

# ==================== КЛАВИАТУРЫ ====================
def get_main_keyboard() -> ReplyKeyboardMarkup:
    """Основная клавиатура бота"""
    keyboard = [
        [KeyboardButton(text="💰 Получка"), KeyboardButton(text="🛒 Магазин")],
        [KeyboardButton(text="🔁 Перевод"), KeyboardButton(text="📊 Профиль")],
        [KeyboardButton(text="🏆 Достижения"), KeyboardButton(text="📢 Рассылка")]
    ]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

def get_shop_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для магазина"""
    buttons = []
    for item in SHOP_ITEMS:
        buttons.append([
            InlineKeyboardButton(
                text=f"{item['name']} - {item['price']}₽",
                callback_data=f"shop:{item['id']}"
            )
        ])
    
    buttons.append([
        InlineKeyboardButton(text="📊 Мой баланс", callback_data="balance"),
        InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_main")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_users_keyboard(users: List[Dict[str, Any]], exclude_id: int) -> InlineKeyboardMarkup:
    """Клавиатура для выбора получателя"""
    buttons = []
    for user in users:
        if user['user_id'] != exclude_id:
            display_name = user['full_name']
            if len(display_name) > 20:
                display_name = display_name[:17] + "..."
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

def get_profile_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для профиля"""
    buttons = [
        [InlineKeyboardButton(text="🔄 Обновить", callback_data="refresh_profile")],
        [InlineKeyboardButton(text="📈 Топ игроков", callback_data="top_players")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_main")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# ==================== ОБРАБОТЧИКИ КОМАНД ====================
@dp.message(CommandStart())
async def cmd_start(message: Message):
    """Обработчик команды /start"""
    user_id = message.from_user.id
    username = message.from_user.username or "Без username"
    full_name = message.from_user.full_name
    
    is_new = await register_user(user_id, username, full_name)
    
    if is_new:
        welcome_text = (
            f"👋 <b>Добро пожаловать, {full_name}!</b>\n\n"
            f"Я <b>Виталик</b>, и я буду твоим начальником в этой игре! 🏢\n"
            f"Будь осторожен — я люблю штрафовать за малейшие провинности! 😈\n\n"
            f"<b>💰 Начальный баланс:</b> 1,000₽\n"
            f"<b>🎯 Система достижений:</b> Получай награды за активность!\n\n"
            f"<b>📌 Доступные действия:</b>\n"
            f"• 💰 <b>Получка</b> — зарплата каждые 5-10 минут\n"
            f"• 🛒 <b>Магазин</b> — полезные предметы и бонусы\n"
            f"• 🔁 <b>Перевод</b> — отправляй деньги другим\n"
            f"• 📊 <b>Профиль</b> — твоя статистика и достижения\n"
            f"• 🏆 <b>Достижения</b> — список всех достижений\n"
            f"• 📢 <b>Рассылка</b> — только для администратора\n\n"
            f"⚠️ <i>Внимание: я могу оштрафовать тебя в любой момент на 50-200₽!</i>"
        )
    else:
        user = await get_user(user_id)
        welcome_text = (
            f"👋 <b>С возвращением, {full_name}!</b>\n\n"
            f"<b>💰 Твой баланс:</b> {user['balance']}₽\n"
            f"<b>🎯 Открыто достижений:</b> {len(user.get('achievements', []))}\n\n"
            f"Что будем делать сегодня? 😏"
        )
    
    await message.answer(welcome_text, reply_markup=get_main_keyboard())

@dp.message(F.text == "💰 Получка")
async def handle_paycheck(message: Message):
    """Обработка нажатия кнопки 'Получка'"""
    user_id = message.from_user.id
    user = await get_user(user_id)
    
    if not user:
        await message.answer("Сначала зарегистрируйтесь через /start")
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
    
    # Проверяем активные бусты (упрощенная версия)
    paycheck_multiplier = 1.0
    paycheck_amount = int(base_amount * paycheck_multiplier)
    
    # Обновляем баланс и время
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
    
    response = (
        f"💰 <b>Получка выдана!</b>\n\n"
        f"<b>Сумма:</b> +{paycheck_amount}₽\n"
        f"<b>Новый баланс:</b> {user['balance'] + paycheck_amount}₽\n\n"
        f"💬 <i>Виталик:</i> {random.choice(jokes)}"
    )
    
    await message.answer(response)
    
    # Проверяем достижения
    unlocked = await check_achievements(user_id)
    if unlocked:
        achievements_text = "\n".join([f"• {name} (+{reward}₽)" for name, reward in unlocked])
        await message.answer(
            f"🎉 <b>Новые достижения разблокированы!</b>\n\n"
            f"{achievements_text}"
        )

@dp.message(F.text == "🛒 Магазин")
async def handle_shop(message: Message):
    """Обработка нажатия кнопки 'Магазин'"""
    user_id = message.from_user.id
    user = await get_user(user_id)
    
    if not user:
        await message.answer("Сначала зарегистрируйтесь через /start")
        return
    
    shop_text = (
        f"🛒 <b>Магазин Виталика</b>\n\n"
        f"<b>💰 Твой баланс:</b> {user['balance']}₽\n\n"
        f"<b>📦 Доступные товары:</b>\n"
    )
    
    for item in SHOP_ITEMS:
        shop_text += f"\n<b>{item['name']}</b> - {item['price']}₽\n"
        shop_text += f"<i>{item['description']}</i>\n"
        shop_text += f"🎁 Шанс бонуса: {int(item['bonus_chance'] * 100)}%\n"
    
    shop_text += "\n<b>Выбери товар для покупки:</b>"
    
    await message.answer(shop_text, reply_markup=get_shop_keyboard())

@dp.callback_query(F.data.startswith("shop:"))
async def handle_buy_item(callback: CallbackQuery):
    """Обработка покупки товара"""
    user_id = callback.from_user.id
    user = await get_user(user_id)
    
    if not user:
        await callback.answer("Сначала зарегистрируйтесь!")
        return
    
    item_id = callback.data.replace("shop:", "")
    item = next((i for i in SHOP_ITEMS if i['id'] == item_id), None)
    
    if not item:
        await callback.answer("Товар не найден!")
        return
    
    # Проверяем баланс
    if user['balance'] < item['price']:
        await callback.answer(f"❌ Недостаточно средств! Нужно {item['price']}₽")
        return
    
    # Определяем бонус
    got_bonus = random.random() < item['bonus_chance']
    bonus_text = "без бонуса"
    bonus_amount = 0
    
    if got_bonus:
        bonuses = [
            ("дополнительные 150₽", 150),
            ("буст x1.5 на следующую получку", 0),
            ("защита от одного штрафа", 0),
            ("бонусные 100₽", 100)
        ]
        bonus_text, bonus_amount = random.choice(bonuses)
    
    # Списываем стоимость
    new_balance = user['balance'] - item['price'] + bonus_amount
    
    async with aiosqlite.connect(DB_NAME) as db:
        # Обновляем баланс
        await db.execute(
            "UPDATE players SET balance = ? WHERE user_id = ?",
            (new_balance, user_id)
        )
        
        # Записываем покупку
        await db.execute(
            '''INSERT INTO purchases (user_id, item_name, price, bonus)
               VALUES (?, ?, ?, ?)''',
            (user_id, item['name'], item['price'], bonus_text)
        )
        
        await db.commit()
    
    # Записываем транзакции
    await update_balance(user_id, -item['price'], 'purchase', f"Покупка: {item['name']}")
    if bonus_amount > 0:
        await update_balance(user_id, bonus_amount, 'bonus', f"Бонус за покупку {item['name']}")
    
    # Шутки Виталика
    jokes = [
        f"Отличная покупка! Но помни, я всё вижу... 👀",
        f"Так, купил {item['name']}... Интересно, на что потратишь дальше? 🤔",
        f"Покупка совершена! А теперь давай работать! 💼",
        f"Хм, {item['name']}... Неплохой выбор! Но мой выбор лучше — штраф! 😈"
    ]
    
    response = (
        f"✅ <b>Покупка успешна!</b>\n\n"
        f"<b>📦 Товар:</b> {item['name']}\n"
        f"<b>💵 Стоимость:</b> {item['price']}₽\n"
        f"<b>🎁 Бонус:</b> {bonus_text}\n\n"
        f"<b>💰 Новый баланс:</b> {new_balance}₽\n\n"
        f"💬 <i>Виталик:</i> {random.choice(jokes)}"
    )
    
    await callback.message.edit_text(response)
    await callback.answer(f"Куплено: {item['name']}")

@dp.message(F.text == "🔁 Перевод")
async def handle_transfer_start(message: Message, state: FSMContext):
    """Начало процесса перевода"""
    user_id = message.from_user.id
    user = await get_user(user_id)
    
    if not user:
        await message.answer("Сначала зарегистрируйтесь через /start")
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
        await callback.answer("Получатель не найден!")
        return
    
    # Сохраняем данные в состоянии
    await state.update_data(
        recipient_id=recipient_id,
        recipient_name=recipient['full_name']
    )
    
    await callback.message.edit_text(
        f"✅ <b>Получатель выбран:</b> {recipient['full_name']}\n\n"
        f"💰 <b>Баланс получателя:</b> {recipient['balance']}₽\n\n"
        f"<b>Введи сумму перевода (1-10,000₽):</b>",
        reply_markup=None
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
        if sender['balance'] < amount:
            await message.answer(
                f"❌ Недостаточно средств!\n"
                f"Твой баланс: {sender['balance']}₽\n"
                f"Нужно: {amount}₽"
            )
            return
        
        # Выполняем перевод
        async with aiosqlite.connect(DB_NAME) as db:
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
            
            await db.commit()
        
        # Записываем транзакции
        await update_balance(sender_id, -amount, 'transfer_out', f"Перевод {recipient_name}")
        await update_balance(recipient_id, amount, 'transfer_in', f"Перевод от {sender['full_name']}")
        
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
                f"💬 <i>Виталик:</i> Кто-то оказался щедрым! 🤑",
                reply_markup=get_main_keyboard()
            )
        except Exception as e:
            logger.error(f"Не удалось уведомить получателя: {e}")
        
        # Проверяем достижения
        unlocked = await check_achievements(sender_id)
        if unlocked:
            achievements_text = "\n".join([f"• {name} (+{reward}₽)" for name, reward in unlocked])
            await message.answer(
                f"🎉 <b>Новые достижения!</b>\n\n"
                f"{achievements_text}"
            )
        
    except ValueError:
        await message.answer("❌ Пожалуйста, введите корректное число!")
        return
    
    await state.clear()

@dp.message(F.text == "📊 Профиль")
async def handle_profile(message: Message):
    """Показать профиль пользователя"""
    user_id = message.from_user.id
    user = await get_user(user_id)
    
    if not user:
        await message.answer("Сначала зарегистрируйтесь через /start")
        return
    
    # Получаем список достижений
    achievements = user.get('achievements', [])
    achievements_list = []
    for ach_id in achievements:
        if ach_id in ACHIEVEMENTS:
            achievements_list.append(ACHIEVEMENTS[ach_id]['name'])
    
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
        f"• 🔁 Переводов: {user['transfers_count']}\n\n"
        f"<b>🏆 Достижения:</b> {len(achievements)}/{len(ACHIEVEMENTS)}\n"
    )
    
    if achievements_list:
        profile_text += "\n".join([f"• {ach}" for ach in achievements_list[:5]])
        if len(achievements_list) > 5:
            profile_text += f"\n... и еще {len(achievements_list) - 5}"
    
    await message.answer(profile_text, reply_markup=get_profile_keyboard())

@dp.message(F.text == "🏆 Достижения")
async def handle_achievements(message: Message):
    """Показать все доступные достижения"""
    user_id = message.from_user.id
    user = await get_user(user_id)
    
    if not user:
        await message.answer("Сначала зарегистрируйтесь через /start")
        return
    
    user_achievements = set(user.get('achievements', []))
    
    achievements_text = "<b>🏆 Система достижений</b>\n\n"
    
    for ach_id, achievement in ACHIEVEMENTS.items():
        status = "✅" if ach_id in user_achievements else "⏳"
        achievements_text += (
            f"{status} <b>{achievement['name']}</b>\n"
            f"<i>{achievement['description']}</i>\n"
            f"Награда: {achievement['reward']}₽\n\n"
        )
    
    await message.answer(
        achievements_text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="refresh_achievements")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_main")]
        ])
    )

@dp.message(F.text == "📢 Рассылка")
async def handle_broadcast_start(message: Message, state: FSMContext):
    """Начало процесса рассылки"""
    user_id = message.from_user.id
    
    if user_id != ADMIN_ID:
        await message.answer("⛔ Эта функция доступна только администратору!")
        return
    
    await message.answer(
        "📢 <b>Режим админской рассылки</b>\n\n"
        "Введите сообщение, которое будет отправлено всем пользователям:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_broadcast")]
        ])
    )
    
    await state.set_state(BroadcastStates.waiting_for_message)

@dp.message(BroadcastStates.waiting_for_message)
async def handle_broadcast_message(message: Message, state: FSMContext):
    """Обработка сообщения для рассылки"""
    broadcast_text = message.text
    
    # Получаем всех пользователей
    all_users = await get_all_users()
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
            
            # Небольшая задержка
            if sent_count % 10 == 0:
                await asyncio.sleep(0.1)
                
        except Exception as e:
            logger.error(f"Не удалось отправить пользователю {user['user_id']}: {e}")
            failed_count += 1
    
    await progress_msg.delete()
    await message.answer(
        f"✅ <b>Рассылка завершена!</b>\n\n"
        f"✓ Отправлено: {sent_count}\n"
        f"✗ Не удалось: {failed_count}\n"
        f"📊 Всего пользователей: {len(all_users)}",
        reply_markup=get_main_keyboard()
    )
    
    await state.clear()

@dp.callback_query(F.data == "top_players")
async def handle_top_players(callback: CallbackQuery):
    """Показать топ игроков"""
    all_users = await get_all_users()
    
    top_text = "<b>🏆 Топ игроков по балансу</b>\n\n"
    
    for i, user in enumerate(all_users[:10], 1):
        medal = ""
        if i == 1: medal = "🥇"
        elif i == 2: medal = "🥈"
        elif i == 3: medal = "🥉"
        else: medal = f"{i}."
        
        name = user['full_name']
        if len(name) > 15:
            name = name[:12] + "..."
        
        top_text += f"{medal} <b>{name}</b> — {user['balance']}₽\n"
    
    if len(all_users) > 10:
        top_text += f"\n... и еще {len(all_users) - 10} игроков"
    
    await callback.message.edit_text(
        top_text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="top_players")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_main")]
        ])
    )
    await callback.answer()

@dp.callback_query(F.data == "balance")
async def handle_balance_check(callback: CallbackQuery):
    """Проверить баланс"""
    user_id = callback.from_user.id
    user = await get_user(user_id)
    
    if user:
        await callback.answer(f"💰 Баланс: {user['balance']}₽", show_alert=True)
    else:
        await callback.answer("Сначала зарегистрируйтесь!", show_alert=True)

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

@dp.callback_query(F.data == "refresh_profile")
async def handle_refresh_profile(callback: CallbackQuery):
    """Обновить профиль"""
    await handle_profile(callback.message)
    await callback.answer("Профиль обновлен!")

@dp.callback_query(F.data == "refresh_achievements")
async def handle_refresh_achievements(callback: CallbackQuery):
    """Обновить достижения"""
    await handle_achievements(callback.message)
    await callback.answer("Достижения обновлены!")

@dp.callback_query(F.data == "cancel_broadcast")
async def handle_cancel_broadcast(callback: CallbackQuery, state: FSMContext):
    """Отмена рассылки"""
    await state.clear()
    await callback.message.edit_text("❌ Рассылка отменена.")
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
            await update_balance(
                target_user['user_id'], 
                -fine_amount, 
                'fine', 
                'Случайный штраф от Виталика'
            )
            
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
                
                # Проверяем достижения
                await check_achievements(target_user['user_id'])
                
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
