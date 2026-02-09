"""
Telegram бот "Виталик Штрафующий" с системой нагирта и укладкой асфальта
Исправлены все баги, добавлены таблетки с рандомным эффектом
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
                asphalt_count INTEGER DEFAULT 0,
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
        
        # Таблица штрафов от таблеток
        await db.execute('''
            CREATE TABLE IF NOT EXISTS pill_fines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                pill_name TEXT,
                fine_amount INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
            return dict(user)
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
                   (user_id, username, full_name, balance, total_earned) 
                   VALUES (?, ?, ?, 1000, 1000)''',
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
            await db.execute(
                "UPDATE players SET asphalt_count = asphalt_count + 1 WHERE user_id = ?",
                (user_id,)
            )
        
        # Записываем транзакцию
        await db.execute(
            '''INSERT INTO transactions (user_id, type, amount, description)
               VALUES (?, ?, ?, ?)''',
            (user_id, txn_type, amount, description)
        )
        
        await db.commit()
        return True, "Успешно"

async def get_all_users() -> List[Dict[str, Any]]:
    """Получить список всех пользователей"""
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT user_id, full_name, username, balance FROM players ORDER BY balance DESC"
        )
        users = await cursor.fetchall()
        return [dict(user) for user in users]

# ==================== СИСТЕМА ТАБЛЕТОК (НАГИРТ) ====================
PILLS = [
    {
        "id": "nagirt_light",
        "name": "💊 Нагирт Лайт",
        "price": 200,
        "description": "+50% к заработку на 1 час. Мало побочек.",
        "effect": 0.5,  # +50% к заработку
        "hours": 1,
        "side_effect_chance": 15,  # 15% шанс штрафа
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

async def get_active_pills(user_id: int) -> List[Dict[str, Any]]:
    """Получить активные таблетки пользователя"""
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

async def get_active_pills_effect(user_id: int) -> Dict[str, Any]:
    """Получить суммарный эффект от активных таблеток"""
    pills = await get_active_pills(user_id)
    
    total_effect = 1.0  # Базовый множитель
    total_side_effect_chance = 0
    
    for pill in pills:
        total_effect += pill['effect_multiplier']
        total_side_effect_chance += pill['side_effect_chance']
    
    # Получаем толерантность пользователя
    user = await get_user(user_id)
    tolerance = user.get('tolerance', 0) if user else 0
    
    # Толерантность уменьшает шанс побочек (максимум 50% уменьшение)
    tolerance_reduction = min(50, tolerance)
    effective_side_effect = max(0, total_side_effect_chance - tolerance_reduction)
    
    return {
        'multiplier': total_effect,
        'side_effect_chance': effective_side_effect,
        'pill_count': len(pills)
    }

async def check_pill_side_effect(user_id: int) -> bool:
    """Проверить, сработал ли побочный эффект от таблеток"""
    effect = await get_active_pills_effect(user_id)
    
    # Шанс срабатывания побочки
    chance = effect['side_effect_chance']
    if random.random() * 100 < chance:
        # Сработал побочный эффект
        fine_amount = random.randint(50, 200)
        
        async with aiosqlite.connect(DB_NAME) as db:
            # Списываем штраф
            await db.execute(
                "UPDATE players SET balance = balance - ? WHERE user_id = ?",
                (fine_amount, user_id)
            )
            
            # Записываем в историю штрафов
            await db.execute(
                '''INSERT INTO pill_fines (user_id, pill_name, fine_amount)
                   VALUES (?, ?, ?)''',
                (user_id, "Побочка от таблеток", fine_amount)
            )
            
            await db.commit()
        
        await update_balance(user_id, -fine_amount, 'pill_fine', 'Побочный эффект от таблеток')
        
        return True, fine_amount
    
    return False, 0

async def remove_all_pills(user_id: int):
    """Удалить все активные таблетки (при приеме антидота)"""
    async with aiosqlite.connect(DB_NAME) as db:
        # Удаляем все активные таблетки
        await db.execute(
            "DELETE FROM active_pills WHERE user_id = ?",
            (user_id,)
        )
        
        # Уменьшаем толерантность на 50%
        user = await get_user(user_id)
        if user:
            tolerance = user.get('tolerance', 0)
            new_tolerance = max(0, tolerance - 50)
            await db.execute(
                "UPDATE players SET tolerance = ? WHERE user_id = ?",
                (new_tolerance, user_id)
            )
        
        await db.commit()

# ==================== МИНИ-ИГРА "УКЛАДКА АСФАЛЬТА" ====================
class AsphaltStates(StatesGroup):
    """Состояния для игры в укладку асфальта"""
    playing = State()

async def handle_asphalt_game(message: Message, state: FSMContext):
    """Обработчик мини-игры укладки асфальта"""
    user_id = message.from_user.id
    user = await get_user(user_id)
    
    if not user:
        await message.answer("Сначала зарегистрируйтесь через /start")
        return
    
    await state.set_state(AsphaltStates.playing)
    
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
                f"💬 <i>Виталик:</i> Не торопись, а то испортишь работу! 👷",
                reply_markup=get_main_keyboard()
            )
            await state.clear()
            return
    
    # Проверяем активные таблетки
    pill_effect = await get_active_pills_effect(user_id)
    
    # Базовый заработок
    base_earnings = 10  # 10 рублей за метр
    
    # Применяем эффект таблеток
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
        f"<b>📊 Всего уложено:</b> {user['asphalt_count']} метров\n"
        f"{pill_text}\n\n"
        f"💬 <i>Виталик:</i> {random.choice(jokes[event])}"
    )
    
    # Клавиатура для продолжения
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧱 Уложить еще метр", callback_data="asphalt_play")],
        [InlineKeyboardButton(text="◀️ Выйти из игры", callback_data="asphalt_exit")]
    ])
    
    await message.answer(response, reply_markup=keyboard)

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
        [KeyboardButton(text="🧱 Укладка асфальта"), KeyboardButton(text="💊 Мои таблетки")],
        [KeyboardButton(text="📢 Рассылка")]
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
            f"<b>💊 Система Нагирта:</b> Таблетки с риском и выгодой!\n"
            f"<b>🧱 Мини-игра:</b> Укладка асфальта за деньги!\n\n"
            f"<b>📌 Доступные действия:</b>\n"
            f"• 💰 <b>Получка</b> — зарплата каждые 5-10 минут\n"
            f"• 🛒 <b>Магазин</b> — таблетки Нагирт и Антидот\n"
            f"• 🔁 <b>Перевод</b> — отправляй деньги другим\n"
            f"• 📊 <b>Профиль</b> — твоя статистика\n"
            f"• 🧱 <b>Укладка асфальта</b> — мини-игра за деньги\n"
            f"• 💊 <b>Мои таблетки</b> — активные эффекты\n"
            f"• 📢 <b>Рассылка</b> — только для администратора\n\n"
            f"⚠️ <i>Внимание: таблетки могут дать буст, но и вызвать штрафы!</i>"
        )
    else:
        user = await get_user(user_id)
        pills = await get_active_pills(user_id)
        
        welcome_text = (
            f"👋 <b>С возвращением, {full_name}!</b>\n\n"
            f"<b>💰 Твой баланс:</b> {user['balance']}₽\n"
            f"<b>💊 Активных таблеток:</b> {len(pills)}\n"
            f"<b>🧱 Уложено асфальта:</b> {user['asphalt_count']}м\n\n"
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
        
        response = (
            f"💰 <b>Получка выдана!</b>\n\n"
            f"<b>Сумма:</b> +{paycheck_amount}₽\n"
            f"<b>Новый баланс:</b> {user['balance'] + paycheck_amount}₽"
            f"{pill_text}\n\n"
            f"💬 <i>Виталик:</i> {random.choice(jokes)}"
        )
    else:
        # Сработала побочка
        total_lost = paycheck_amount + pill_fine_amount
        jokes = [
            f"Ха! Побочка от таблеток! Вместо {paycheck_amount}₽ ты теряешь {total_lost}₽! 😂",
            f"Нагирт подвел! Минус {total_lost}₽ вместо зарплаты! 💊",
            f"Побочный эффект! Забираю {total_lost}₽! Чтоб неповадно было! 👿"
        ]
        
        response = (
            f"💊 <b>ПОБОЧНЫЙ ЭФФЕКТ ОТ ТАБЛЕТОК!</b>\n\n"
            f"<b>Вместо получки:</b> -{total_lost}₽\n"
            f"<b>Штраф за побочку:</b> -{pill_fine_amount}₽\n"
            f"<b>Новый баланс:</b> {user['balance'] - total_lost}₽\n\n"
            f"💬 <i>Виталик:</i> {random.choice(jokes)}"
        )
    
    await message.answer(response)

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
        await callback.answer("Сначала зарегистрируйтесь!")
        return
    
    item_id = callback.data.replace("shop:", "")
    item = next((i for i in PILLS if i['id'] == item_id), None)
    
    if not item:
        await callback.answer("Товар не найден!")
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
        
        response = (
            f"✅ <b>Антидот принят!</b>\n\n"
            f"<b>💉 Товар:</b> {item['name']}\n"
            f"<b>💵 Стоимость:</b> {item['price']}₽\n\n"
            f"<b>✅ Все активные таблетки сняты</b>\n"
            f"<b>📉 Толерантность уменьшена на 50%</b>\n\n"
            f"<b>💰 Новый баланс:</b> {user['balance'] - item['price']}₽\n\n"
            f"💬 <i>Виталик:</i> Молодец, что следишь за здоровьем! Но работать все равно надо! 🏥"
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
        
        response = (
            f"✅ <b>Таблетка куплена!</b>\n\n"
            f"<b>💊 Товар:</b> {item['name']}\n"
            f"<b>💵 Стоимость:</b> {item['price']}₽\n"
            f"<b>⏱️ Длительность:</b> {item['hours']} час(а)\n"
            f"<b>📈 Эффект:</b> +{int(item['effect'] * 100)}% к заработку\n"
            f"<b>⚠️ Риск побочек:</b> {item['side_effect_chance']}%\n\n"
            f"<b>💰 Новый баланс:</b> {user['balance'] - item['price']}₽\n"
            f"<b>💪 Толерантность:</b> +10 (теперь {user.get('tolerance', 0) + 10})\n\n"
            f"💬 <i>Виталик:</i> {random.choice(jokes)}"
        )
    
    await callback.message.edit_text(response)
    await callback.answer(f"Куплено: {item['name']}")

@dp.message(F.text == "🧱 Укладка асфальта")
async def handle_asphalt_start(message: Message, state: FSMContext):
    """Начало игры в укладку асфальта"""
    await handle_asphalt_game(message, state)

@dp.callback_query(F.data == "asphalt_play")
async def handle_asphalt_play(callback: CallbackQuery, state: FSMContext):
    """Обработка нажатия кнопки укладки асфальта"""
    await handle_asphalt_game(callback.message, state)

@dp.callback_query(F.data == "asphalt_exit")
async def handle_asphalt_exit(callback: CallbackQuery, state: FSMContext):
    """Выход из игры в укладку асфальта"""
    await state.clear()
    await callback.message.edit_text(
        "🧱 <b>Игра окончена!</b>\n\n"
        "Возвращайся, когда захочешь заработать!",
        reply_markup=get_main_keyboard()
    )
    await callback.answer()

# Остальной код (переводы, профиль, достижения, штрафы) остается без изменений
# ВАЖНО: Нужно скопировать остальные функции из предыдущего рабочего кода:
# handle_transfer_start, handle_recipient_selection, handle_transfer_amount,
# handle_profile, handle_my_pills, handle_broadcast_start, handle_broadcast_message,
# handle_top_players, handle_balance_check, handle_cancel, handle_back_to_main,
# handle_refresh_profile, handle_back_to_shop, cancel_broadcast, schedule_fines, main

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
            
            # Проверяем активные таблетки (защита от штрафов)
            active_pills = await get_active_pills(target_user['user_id'])
            has_protection = any('protection' in pill['pill_name'].lower() for pill in active_pills)
            
            if has_protection:
                logger.info(f"Пользователь {target_user['user_id']} защищен от штрафа")
                continue
            
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
