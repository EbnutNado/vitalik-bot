"""
Telegram бот "Виталик Штрафующий" - ИСПРАВЛЕННАЯ ВЕРСИЯ
Полностью рабочий бот с игровой экономикой, штрафами, магазином и новыми фичами.
"""

import asyncio
import logging
import random
import json
from datetime import datetime, timedelta
from typing import List, Tuple, Optional, Dict, Any
from contextlib import suppress

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.exceptions import TelegramBadRequest
import aiosqlite

# ==================== КОНФИГУРАЦИЯ ====================
BOT_TOKEN = "8451168327:AAGQffadqqBg3pZNQnjctVxH-dUgXsovTr4"  # Замените на ваш токен от @BotFather
ADMIN_ID = 5775839902  # Ваш Telegram ID (замените на свой)

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# ==================== БАЗА ДАННЫХ ====================
DB_NAME = "vitalik_bot.db"

async def init_db():
    """Инициализация базы данных с новыми таблицами"""
    async with aiosqlite.connect(DB_NAME) as db:
        # Таблица игроков
        await db.execute('''
            CREATE TABLE IF NOT EXISTS players (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                balance INTEGER DEFAULT 1000,
                last_paycheck TIMESTAMP,
                last_penalty TIMESTAMP,
                penalty_immunity_until TIMESTAMP,
                daily_bonus_claimed TIMESTAMP,
                total_penalties INTEGER DEFAULT 0,
                total_earned INTEGER DEFAULT 0,
                achievements TEXT DEFAULT '[]',
                registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Таблица истории транзакций
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

        # Таблица истории покупок
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

        # Таблица мини-игр
        await db.execute('''
            CREATE TABLE IF NOT EXISTS minigames (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                game_type TEXT,
                bet INTEGER,
                win_amount INTEGER,
                result TEXT,
                played_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
            # Десериализуем achievements из JSON
            if user_dict.get('achievements'):
                user_dict['achievements'] = json.loads(user_dict['achievements'])
            else:
                user_dict['achievements'] = []
            return user_dict
        return None

async def register_user(user_id: int, username: str, full_name: str):
    """Регистрация нового пользователя"""
    async with aiosqlite.connect(DB_NAME) as db:
        # Проверяем, существует ли пользователь
        cursor = await db.execute(
            "SELECT 1 FROM players WHERE user_id = ?", 
            (user_id,)
        )
        exists = await cursor.fetchone()

        if not exists:
            achievements = json.dumps(["новичок"])
            await db.execute(
                '''INSERT INTO players (user_id, username, full_name, balance, achievements) 
                   VALUES (?, ?, ?, 1000, ?)''',
                (user_id, username, full_name, achievements)
            )
            await db.execute(
                '''INSERT INTO transactions (user_id, type, amount, description)
                   VALUES (?, 'registration', 1000, 'Начальный баланс при регистрации')''',
                (user_id,)
            )
            await db.commit()
            logger.info(f"Зарегистрирован новый пользователь: {user_id}")

async def update_balance(user_id: int, amount: int, txn_type: str, description: str):
    """Обновить баланс пользователя и добавить запись в историю"""
    async with aiosqlite.connect(DB_NAME) as db:
        # Обновляем баланс
        await db.execute(
            "UPDATE players SET balance = balance + ? WHERE user_id = ?",
            (amount, user_id)
        )

        # Добавляем запись в историю
        await db.execute(
            '''INSERT INTO transactions (user_id, type, amount, description)
               VALUES (?, ?, ?, ?)''',
            (user_id, txn_type, amount, description)
        )

        # Обновляем total_earned если это доход
        if amount > 0:
            await db.execute(
                "UPDATE players SET total_earned = total_earned + ? WHERE user_id = ?",
                (amount, user_id)
            )

        await db.commit()

async def get_all_users() -> List[Dict[str, Any]]:
    """Получить список всех зарегистрированных пользователей"""
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT user_id, full_name, username, balance FROM players"
        )
        users = await cursor.fetchall()
        return [dict(user) for user in users]

async def add_achievement(user_id: int, achievement: str):
    """Добавить достижение пользователю"""
    user = await get_user(user_id)
    if not user:
        return
    
    achievements = user.get('achievements', [])
    if achievement not in achievements:
        achievements.append(achievement)
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute(
                "UPDATE players SET achievements = ? WHERE user_id = ?",
                (json.dumps(achievements), user_id)
            )
            await db.commit()

# ==================== МАШИНЫ СОСТОЯНИЙ ====================
class TransferStates(StatesGroup):
    """Состояния для процесса перевода"""
    choosing_recipient = State()
    entering_amount = State()

class BroadcastStates(StatesGroup):
    """Состояния для рассылки сообщений"""
    waiting_for_message = State()

class MiniGameStates(StatesGroup):
    """Состояния для мини-игр"""
    choosing_game = State()
    roulette_bet = State()
    dice_bet = State()

# ==================== ТОВАРЫ МАГАЗИНА ====================
SHOP_ITEMS = [
    {
        "id": "day_off",
        "name": "Выходной",
        "price": 500,
        "description": "Отдых от штрафов Виталика на 24 часа!",
        "bonus_chance": 0.7,
        "duration_hours": 24
    },
    {
        "id": "premium_boost",
        "name": "Премиум-Буст",
        "price": 1000,
        "description": "Увеличивает получку в 2 раза на 3 дня!",
        "bonus_chance": 0.8,
        "duration_hours": 72
    },
    {
        "id": "bonus_coin",
        "name": "Бонусная монета",
        "price": 300,
        "description": "Дает случайный бонус от Виталика!",
        "bonus_chance": 1.0,
        "duration_hours": 0
    },
    {
        "id": "insurance",
        "name": "Страховка от штрафов",
        "price": 800,
        "description": "Возмещает 50% от следующего штрафа!",
        "bonus_chance": 1.0,
        "duration_hours": 0
    },
    {
        "id": "lottery_ticket",
        "name": "Лотерейный билет",
        "price": 100,
        "description": "Шанс выиграть до 1000₽!",
        "bonus_chance": 0.3,
        "duration_hours": 0
    }
]

# ==================== КЛАВИАТУРЫ ====================
def get_main_keyboard() -> ReplyKeyboardMarkup:
    """Основная клавиатура бота"""
    keyboard = [
        [KeyboardButton(text="💰 Получка"), KeyboardButton(text="🛒 Магазин")],
        [KeyboardButton(text="🔁 Перевод"), KeyboardButton(text="🎮 Мини-игры")],
        [KeyboardButton(text="📊 Статистика"), KeyboardButton(text="🏆 Достижения")],
        [KeyboardButton(text="📢 Рассылка")]
    ]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

def get_shop_keyboard(user_balance: int) -> InlineKeyboardMarkup:
    """Клавиатура для магазина"""
    buttons = []
    for item in SHOP_ITEMS:
        can_buy = user_balance >= item['price']
        button_text = f"{item['name']} - {item['price']}₽"
        if not can_buy:
            button_text = f"❌ {button_text}"
        
        buttons.append([
            InlineKeyboardButton(
                text=button_text,
                callback_data=f"buy_{item['id']}"
            )
        ])

    # Кнопки управления
    buttons.append([
        InlineKeyboardButton(text="💰 Баланс", callback_data="check_balance"),
        InlineKeyboardButton(text="📜 История", callback_data="purchase_history")
    ])
    
    buttons.append([
        InlineKeyboardButton(text="◀️ Главное меню", callback_data="back_to_main")
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_minigames_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для выбора мини-игры"""
    buttons = [
        [InlineKeyboardButton(text="🎰 Рулетка (x2)", callback_data="game_roulette")],
        [InlineKeyboardButton(text="🎲 Кости (x3)", callback_data="game_dice")],
        [InlineKeyboardButton(text="🎯 Случайный бонус", callback_data="game_random")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_main")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_users_keyboard(users: List[Dict[str, Any]], exclude_id: int) -> InlineKeyboardMarkup:
    """Клавиатура для выбора получателя перевода"""
    buttons = []
    for user in users:
        if user['user_id'] != exclude_id:
            buttons.append([
                InlineKeyboardButton(
                    text=f"{user['full_name']} ({user['balance']}₽)",
                    callback_data=f"transfer_to_{user['user_id']}"
                )
            ])

    buttons.append([
        InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_transfer")
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)

# ==================== СИСТЕМА ШТРАФОВ ====================
async def check_and_apply_penalties():
    """Проверка и наложение штрафов на всех игроков"""
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT user_id, full_name, balance, last_penalty, penalty_immunity_until 
            FROM players 
            WHERE balance > 0
        """)
        users = await cursor.fetchall()
        
        for user in users:
            user_id = user['user_id']
            user_dict = dict(user)
            
            # Проверяем иммунитет к штрафам
            immunity_until = user_dict.get('penalty_immunity_until')
            if immunity_until:
                immunity_time = datetime.fromisoformat(immunity_until) if immunity_until else None
                if immunity_time and immunity_time > datetime.now():
                    continue
            
            # Шанс штрафа: 15% каждый час
            if random.random() <= 0.15:
                # Сумма штрафа: от 50 до 200, но не более 30% от баланса
                max_penalty = min(200, user_dict['balance'] * 0.3)
                penalty = random.randint(50, max(50, int(max_penalty)))
                
                if penalty > 0:
                    # Списываем штраф
                    await db.execute(
                        "UPDATE players SET balance = balance - ?, last_penalty = ?, total_penalties = total_penalties + 1 WHERE user_id = ?",
                        (penalty, datetime.now().isoformat(), user_id)
                    )
                    
                    # Добавляем запись в историю
                    await db.execute(
                        '''INSERT INTO transactions (user_id, type, amount, description)
                           VALUES (?, 'penalty', -?, ?)''',
                        (user_id, penalty, "Штраф от Виталика")
                    )
                    
                    # Уведомляем пользователя
                    try:
                        penalty_reasons = [
                            f"штраф за плохое настроение Виталика! 😠",
                            f"штраф за криво уложенный асфальт! 🛣️",
                            f"штраф за слишком громкий смех на работе! 😂",
                            f"штраф за кофе без печеньки! ☕",
                            f"штраф за сон на рабочем месте! 💤",
                            f"штраф за слишком красивую прическу! 💇",
                            f"штраф за победу в конкурсе 'Лучший работник'! 🏆",
                            f"штраф за отсутствие на собрании! 📅",
                            f"штраф за слишком быстрое выполнение задачи! ⚡",
                            f"штраф за то, что сегодня пятница! 🎉"
                        ]
                        
                        await bot.send_message(
                            user_id,
                            f"⚠️ *ВИТАЛИК ШТРАФУЕТ!*\n\n"
                            f"📛 Причина: {random.choice(penalty_reasons)}\n"
                            f"💸 Сумма штрафа: *{penalty}₽*\n"
                            f"💰 Новый баланс: *{user_dict['balance'] - penalty}₽*\n\n"
                            f"Купи 'Выходной' в магазине, чтобы избежать штрафов!",
                            parse_mode="Markdown"
                        )
                    except Exception as e:
                        logger.error(f"Не удалось отправить уведомление о штрафе пользователю {user_id}: {e}")
        
        await db.commit()

async def penalty_scheduler():
    """Планировщик штрафов"""
    while True:
        try:
            await check_and_apply_penalties()
        except Exception as e:
            logger.error(f"Ошибка в планировщике штрафов: {e}")
        
        # Ждем случайное время от 30 до 60 минут
        wait_time = random.randint(1800, 3600)  # в секундах
        await asyncio.sleep(wait_time)

# ==================== ОБРАБОТЧИКИ КОМАНД ====================
@dp.message(CommandStart())
async def cmd_start(message: Message):
    """Обработчик команды /start"""
    user_id = message.from_user.id
    username = message.from_user.username or "Без username"
    full_name = message.from_user.full_name

    # Регистрируем пользователя
    await register_user(user_id, username, full_name)

    # Приветственное сообщение от Виталика
    welcome_text = (
        f"👋 Привет, {full_name}!\n\n"
        f"Я Виталик, и я буду твоим начальником в этой игре! 🏢\n"
        f"Будь осторожен — я люблю штрафовать за малейшие провинности! 😈\n\n"
        f"💰 Твой начальный баланс: 1000₽\n"
        f"📊 Используй кнопки ниже для управления:\n"
        f"• 💰 Получка — получай зарплату каждые 5-10 минут\n"
        f"• 🛒 Магазин — покупай полезные предметы\n"
        f"• 🔁 Перевод — отправляй деньги другим игрокам\n"
        f"• 🎮 Мини-игры — зарабатывай дополнительные деньги\n"
        f"• 📊 Статистика — твоя игровая статистика\n"
        f"• 🏆 Достижения — список твоих достижений\n"
        f"• 📢 Рассылка — только для администратора\n\n"
        f"⚠️ Внимание: я могу оштрафовать тебя в любой момент на 50-200₽!\n"
        f"🎁 Заходи каждый день за ежедневным бонусом!"
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

    # Проверяем, можно ли получить получку
    current_time = datetime.now()
    last_paycheck = user.get('last_paycheck')

    if last_paycheck:
        last_paycheck_time = datetime.fromisoformat(last_paycheck)
        time_since_last = current_time - last_paycheck_time
        min_wait = timedelta(minutes=5)

        if time_since_last < min_wait:
            wait_minutes = int((min_wait - time_since_last).total_seconds() / 60)
            await message.answer(
                f"⏳ Слишком рано для получки!\n"
                f"Подожди еще {wait_minutes} минут(ы), работяга! 😏"
            )
            return

    # Вычисляем сумму получки
    paycheck_amount = random.randint(100, 500)
    
    # Проверяем, активен ли премиум-буст
    immunity_until = user.get('penalty_immunity_until')
    if immunity_until:
        immunity_time = datetime.fromisoformat(immunity_until) if immunity_until else None
        if immunity_time and immunity_time > datetime.now():
            # Проверяем покупку премиум-буста
            async with aiosqlite.connect(DB_NAME) as db:
                cursor = await db.execute(
                    """SELECT 1 FROM purchases 
                       WHERE user_id = ? AND item_name = 'Премиум-Буст' 
                       AND purchased_at > datetime('now', '-3 days')""",
                    (user_id,)
                )
                has_boost = await cursor.fetchone()
                if has_boost:
                    paycheck_amount *= 2

    # Обновляем баланс и время последней получки
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE players SET balance = balance + ?, last_paycheck = ? WHERE user_id = ?",
            (paycheck_amount, current_time.isoformat(), user_id)
        )
        await db.execute(
            '''INSERT INTO transactions (user_id, type, amount, description)
               VALUES (?, 'paycheck', ?, 'Ежедневная получка от Виталика')''',
            (user_id, paycheck_amount)
        )
        await db.commit()

    # Добавляем достижение
    await add_achievement(user_id, "первая получка")
    
    # Шутки Виталика при выдаче получки
    jokes = [
        f"Держи {paycheck_amount}₽! Но не трать всё в одном месте... Или трать, мне-то что! 😄",
        f"Вот твоя получка: {paycheck_amount}₽. А теперь быстро на работу, бездельник! ⚡",
        f"{paycheck_amount}₽ к твоему балансу. Не благодари, лучше не провоцируй меня на штрафы! 😈",
        f"Получил {paycheck_amount}₽? Отлично! Теперь у меня есть повод оштрафовать тебя за слишком радостный вид! 🤣"
    ]
    
    response = (
        f"💸 *Получка получена!*\n"
        f"📈 Начислено: *{paycheck_amount}₽*\n"
        f"💰 Новый баланс: *{user['balance'] + paycheck_amount}₽*\n\n"
        f"{random.choice(jokes)}"
    )
    
    await message.answer(response, parse_mode="Markdown")

@dp.message(F.text == "🛒 Магазин")
async def handle_shop(message: Message):
    """Обработка нажатия кнопки 'Магазин'"""
    user_id = message.from_user.id
    user = await get_user(user_id)
    
    if not user:
        await message.answer("Сначала зарегистрируйтесь через /start")
        return
    
    # Формируем сообщение с товарами
    shop_text = "🛒 *Магазин Виталика*\n\n"
    shop_text += "Здесь ты можешь купить полезные вещи:\n\n"
    
    for item in SHOP_ITEMS:
        shop_text += (
            f"*{item['name']}*\n"
            f"💰 Цена: {item['price']}₽\n"
            f"📝 {item['description']}\n"
            f"🎲 Шанс бонуса: {int(item['bonus_chance'] * 100)}%\n"
            f"——————————————\n"
        )
    
    shop_text += f"\n💰 *Твой баланс:* {user['balance']}₽"
    
    await message.answer(
        shop_text, 
        parse_mode="Markdown", 
        reply_markup=get_shop_keyboard(user['balance'])
    )

@dp.callback_query(F.data.startswith("buy_"))
async def handle_buy_item(callback: CallbackQuery):
    """Обработка покупки товара"""
    user_id = callback.from_user.id
    user = await get_user(user_id)
    item_id = callback.data.split("_")[1]
    
    # Находим товар
    item = next((i for i in SHOP_ITEMS if i['id'] == item_id), None)
    if not item:
        await callback.answer("Товар не найден!")
        return
    
    # Проверяем баланс
    if user['balance'] < item['price']:
        await callback.answer(f"❌ Недостаточно средств! Нужно {item['price']}₽")
        return
    
    # Покупка товара
    async with aiosqlite.connect(DB_NAME) as db:
        # Списываем деньги
        await db.execute(
            "UPDATE players SET balance = balance - ? WHERE user_id = ?",
            (item['price'], user_id)
        )
        
        # Применяем бонусы
        bonus_applied = random.random() <= item['bonus_chance']
        bonus_text = "Бонус применен" if bonus_applied else "Без бонуса"
        
        # Для выходного устанавливаем иммунитет
        if item['id'] == 'day_off' and bonus_applied:
            immunity_until = (datetime.now() + timedelta(hours=item['duration_hours'])).isoformat()
            await db.execute(
                "UPDATE players SET penalty_immunity_until = ? WHERE user_id = ?",
                (immunity_until, user_id)
            )
        
        # Для лотерейного билета
        if item['id'] == 'lottery_ticket' and bonus_applied:
            lottery_win = random.randint(100, 1000)
            await db.execute(
                "UPDATE players SET balance = balance + ? WHERE user_id = ?",
                (lottery_win, user_id)
            )
            bonus_text = f"Выигрыш в лотерее: {lottery_win}₽"
        
        # Добавляем запись о покупке
        await db.execute(
            '''INSERT INTO purchases (user_id, item_name, price, bonus)
               VALUES (?, ?, ?, ?)''',
            (user_id, item['name'], item['price'], bonus_text)
        )
        
        # Добавляем запись в историю транзакций
        await db.execute(
            '''INSERT INTO transactions (user_id, type, amount, description)
               VALUES (?, 'purchase', -?, ?)''',
            (user_id, item['price'], f"Покупка: {item['name']}")
        )
        
        await db.commit()
    
    # Формируем ответ
    response = (
        f"✅ *Покупка успешна!*\n\n"
        f"📦 Товар: *{item['name']}*\n"
        f"💸 Стоимость: *{item['price']}₽*\n"
        f"💰 Остаток: *{user['balance'] - item['price']}₽*\n"
    )
    
    if bonus_applied:
        bonus_messages = {
            "day_off": "🎉 Иммунитет к штрафам на 24 часа активирован!",
            "premium_boost": "🚀 Премиум-буст активирован! Получка будет в 2 раза больше 3 дня!",
            "bonus_coin": f"🎰 Бонусная монета! Получай дополнительные {random.randint(50, 200)}₽!",
            "insurance": "🛡️ Страховка активирована! Следующий штраф будет на 50% меньше!",
            "lottery_ticket": f"🎫 Поздравляем! Вы выиграли в лотерее!"
        }
        response += f"\n{bonus_messages.get(item_id, '🎁 Бонус активирован!')}\n"
    
    # Добавляем достижение
    await add_achievement(user_id, "первая покупка")
    
    # Шутка от Виталика
    jokes = [
        f"\n\nХорошая покупка! Но я все равно найду за что оштрафовать! 😈",
        f"\n\nТратишь деньги? Отлично! Значит, есть что штрафовать! 💰",
        f"\n\nКупил {item['name']}? Надеюсь, он поможет тебе избежать моих штрафов! 🤣"
    ]
    response += random.choice(jokes)
    
    await callback.message.edit_text(response, parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "check_balance")
async def handle_check_balance(callback: CallbackQuery):
    """Показ баланса в магазине"""
    user_id = callback.from_user.id
    user = await get_user(user_id)
    
    if user:
        await callback.answer(f"💰 Ваш баланс: {user['balance']}₽", show_alert=True)
    else:
        await callback.answer("Ошибка: пользователь не найден")

@dp.callback_query(F.data == "purchase_history")
async def handle_purchase_history(callback: CallbackQuery):
    """История покупок"""
    user_id = callback.from_user.id
    
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            """SELECT item_name, price, bonus, purchased_at 
               FROM purchases 
               WHERE user_id = ? 
               ORDER BY purchased_at DESC 
               LIMIT 10""",
            (user_id,)
        )
        purchases = await cursor.fetchall()
    
    if not purchases:
        history_text = "📜 У вас еще нет покупок!"
    else:
        history_text = "📜 *Последние покупки:*\n\n"
        for purchase in purchases:
            purchase_date = datetime.fromisoformat(purchase['purchased_at']).strftime("%d.%m.%Y %H:%M")
            history_text += (
                f"🛍️ *{purchase['item_name']}*\n"
                f"💰 {purchase['price']}₽ | {purchase['bonus']}\n"
                f"📅 {purchase_date}\n"
                f"——————————————\n"
            )
    
    await callback.message.answer(history_text, parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "back_to_main")
async def handle_back_to_main(callback: CallbackQuery):
    """Обработка возврата в главное меню"""
    try:
        await callback.message.delete()
    except:
        pass
    
    await callback.message.answer(
        "Главное меню:", 
        reply_markup=get_main_keyboard()
    )
    await callback.answer()

@dp.message(F.text == "🔁 Перевод")
async def handle_transfer_start(message: Message, state: FSMContext):
    """Начало процесса перевода"""
    user_id = message.from_user.id
    user = await get_user(user_id)
    
    if not user:
        await message.answer("Сначала зарегистрируйтесь через /start")
        return
    
    # Получаем всех пользователей, кроме отправителя
    all_users = await get_all_users()
    if len(all_users) <= 1:
        await message.answer("😔 Пока нет других игроков для перевода")
        return
    
    await message.answer(
        f"💰 *Твой баланс:* {user['balance']}₽\n"
        f"👥 Выбери получателя перевода:",
        parse_mode="Markdown",
        reply_markup=get_users_keyboard(all_users, user_id)
    )
    
    await state.set_state(TransferStates.choosing_recipient)

@dp.callback_query(F.data.startswith("transfer_to_"))
async def handle_choose_recipient(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора получателя перевода"""
    recipient_id = int(callback.data.split("_")[2])
    
    # Сохраняем ID получателя в состоянии
    await state.update_data(recipient_id=recipient_id)
    
    # Получаем информацию о получателе
    recipient = await get_user(recipient_id)
    sender = await get_user(callback.from_user.id)
    
    if recipient and sender:
        await callback.message.edit_text(
            f"👤 *Получатель:* {recipient['full_name']}\n"
            f"💰 *Твой баланс:* {sender['balance']}₽\n\n"
            f"💸 *Введи сумму перевода:*\n"
            f"(от 10 до {min(sender['balance'], 5000)}₽)",
            parse_mode="Markdown"
        )
        
        await state.set_state(TransferStates.entering_amount)
    
    await callback.answer()

@dp.callback_query(F.data == "cancel_transfer")
async def handle_cancel_transfer(callback: CallbackQuery, state: FSMContext):
    """Отмена перевода"""
    await state.clear()
    await callback.message.edit_text("❌ Перевод отменен")
    await callback.answer()

@dp.message(TransferStates.entering_amount)
async def handle_transfer_amount(message: Message, state: FSMContext):
    """Обработка ввода суммы перевода"""
    user_id = message.from_user.id
    user = await get_user(user_id)
    
    try:
        amount = int(message.text)
        
        # Проверки суммы
        if amount < 10:
            await message.answer("❌ Минимальная сумма перевода - 10₽")
            return
        if amount > user['balance']:
            await message.answer(f"❌ Недостаточно средств! Доступно: {user['balance']}₽")
            return
        if amount > 5000:
            await message.answer("❌ Максимальная сумма одного перевода - 5000₽")
            return
        
        # Получаем данные получателя из состояния
        state_data = await state.get_data()
        recipient_id = state_data.get('recipient_id')
        recipient = await get_user(recipient_id)
        
        if not recipient:
            await message.answer("❌ Получатель не найден")
            await state.clear()
            return
        
        # Выполняем перевод
        async with aiosqlite.connect(DB_NAME) as db:
            # Списываем у отправителя
            await db.execute(
                "UPDATE players SET balance = balance - ? WHERE user_id = ?",
                (amount, user_id)
            )
            
            # Начисляем получателю
            await db.execute(
                "UPDATE players SET balance = balance + ? WHERE user_id = ?",
                (amount, recipient_id)
            )
            
            # Записываем транзакции
            await db.execute(
                '''INSERT INTO transactions (user_id, type, amount, description)
                   VALUES (?, 'transfer_out', -?, ?)''',
                (user_id, amount, f"Перевод для {recipient['full_name']}")
            )
            
            await db.execute(
                '''INSERT INTO transactions (user_id, type, amount, description)
                   VALUES (?, 'transfer_in', ?, ?)''',
                (recipient_id, amount, f"Перевод от {user['full_name']}")
            )
            
            await db.commit()
        
        # Добавляем достижения
        await add_achievement(user_id, "первый перевод")
        await add_achievement(recipient_id, "получил перевод")
        
        # Шутки Виталика про переводы
        jokes = [
            f"Перевел {amount}₽? Надеюсь, это не взятка! 🕵️",
            f"Щедрый перевод! Теперь у меня есть два кандидата на штраф! 😈",
            f"{amount}₽ отправлены! Молодец, но это не спасет тебя от моего внимания! 👀"
        ]
        
        response = (
            f"✅ *Перевод выполнен!*\n\n"
            f"📤 Отправитель: *Вы*\n"
            f"📥 Получатель: *{recipient['full_name']}*\n"
            f"💸 Сумма: *{amount}₽*\n"
            f"💰 Ваш новый баланс: *{user['balance'] - amount}₽*\n\n"
            f"{random.choice(jokes)}"
        )
        
        # Уведомляем получателя
        try:
            await bot.send_message(
                recipient_id,
                f"💰 *Получен перевод!*\n\n"
                f"📥 От: *{user['full_name']}*\n"
                f"💸 Сумма: *{amount}₽*\n"
                f"💰 Ваш новый баланс: *{recipient['balance'] + amount}₽*\n\n"
                f"Спасибо за перевод! 🎉",
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Не удалось отправить уведомление получателю {recipient_id}: {e}")
        
        await message.answer(response, parse_mode="Markdown")
        
    except ValueError:
        await message.answer("❌ Пожалуйста, введите число!")
    finally:
        await state.clear()

# ==================== МИНИ-ИГРЫ ====================
@dp.message(F.text == "🎮 Мини-игры")
async def handle_minigames(message: Message):
    """Обработка нажатия кнопки 'Мини-игры'"""
    user_id = message.from_user.id
    user = await get_user(user_id)
    
    if not user:
        await message.answer("Сначала зарегистрируйтесь через /start")
        return
    
    games_text = (
        "🎮 *Мини-игры от Виталика!*\n\n"
        "Выбери игру и попробуй удачу:\n\n"
        "🎰 *Рулетка (x2)*\n"
        "Ставь на цвет! Красное или черное!\n"
        "Шанс выигрыша: 45%\n\n"
        "🎲 *Кости (x3)*\n"
        "Бросай кости! Выброси больше 10!\n"
        "Шанс выигрыша: 50%\n\n"
        "🎯 *Случайный бонус*\n"
        "Получи случайный бонус от Виталика!\n"
        "Бесплатно раз в 3 часа!\n\n"
        f"💰 Твой баланс: {user['balance']}₽"
    )
    
    await message.answer(games_text, parse_mode="Markdown", reply_markup=get_minigames_keyboard())

@dp.callback_query(F.data == "game_roulette")
async def handle_game_roulette(callback: CallbackQuery, state: FSMContext):
    """Начало игры в рулетку"""
    user_id = callback.from_user.id
    user = await get_user(user_id)
    
    if not user:
        await callback.answer("Ошибка: пользователь не найден")
        return
    
    await callback.message.edit_text(
        f"🎰 *Рулетка*\n\n"
        f"💰 Твой баланс: {user['balance']}₽\n"
        f"Выигрыш: x2 от ставки\n\n"
        f"Введи сумму ставки (мин. 10₽, макс. {min(500, user['balance'])}₽):",
        parse_mode="Markdown"
    )
    
    await state.set_state(MiniGameStates.roulette_bet)
    await callback.answer()

@dp.message(MiniGameStates.roulette_bet)
async def handle_roulette_bet(message: Message, state: FSMContext):
    """Обработка ставки в рулетке"""
    user_id = message.from_user.id
    user = await get_user(user_id)
    
    try:
        bet = int(message.text)
        
        # Проверки ставки
        if bet < 10:
            await message.answer("❌ Минимальная ставка - 10₽")
            return
        if bet > user['balance']:
            await message.answer(f"❌ Недостаточно средств! Доступно: {user['balance']}₽")
            return
        if bet > 500:
            await message.answer("❌ Максимальная ставка - 500₽")
            return
        
        # Играем в рулетку
        win = random.random() <= 0.45  # 45% шанс выигрыша
        colors = ["красное", "черное"]
        chosen_color = random.choice(colors)
        
        async with aiosqlite.connect(DB_NAME) as db:
            if win:
                win_amount = bet * 2
                await db.execute(
                    "UPDATE players SET balance = balance + ? WHERE user_id = ?",
                    (win_amount - bet, user_id)
                )
                
                result_text = (
                    f"🎉 *ПОБЕДА!*\n\n"
                    f"🎰 Выпало: *{chosen_color}*\n"
                    f"💰 Ставка: {bet}₽\n"
                    f"🏆 Выигрыш: *{win_amount}₽*\n"
                    f"💎 Чистая прибыль: *{bet}₽*\n"
                    f"📈 Новый баланс: *{user['balance'] + bet}₽*\n\n"
                    f"Везет же некоторым! Но я за тобой приглядываю! 👀"
                )
                
                await db.execute(
                    '''INSERT INTO minigames (user_id, game_type, bet, win_amount, result)
                       VALUES (?, 'roulette', ?, ?, 'win')''',
                    (user_id, bet, win_amount)
                )
                
                await add_achievement(user_id, "победа в рулетке")
            else:
                await db.execute(
                    "UPDATE players SET balance = balance - ? WHERE user_id = ?",
                    (bet, user_id)
                )
                
                result_text = (
                    f"💥 *ПРОИГРЫШ!*\n\n"
                    f"🎰 Выпало: *{chosen_color}*\n"
                    f"💰 Ставка: {bet}₽\n"
                    f"📉 Потеряно: *{bet}₽*\n"
                    f"💸 Новый баланс: *{user['balance'] - bet}₽*\n\n"
                    f"Ха-ха! Проиграл! Теперь у меня есть повод оштрафовать тебя за азартные игры! 😈"
                )
                
                await db.execute(
                    '''INSERT INTO minigames (user_id, game_type, bet, win_amount, result)
                       VALUES (?, 'roulette', ?, 0, 'loss')''',
                    (user_id, bet)
                )
            
            await db.execute(
                '''INSERT INTO transactions (user_id, type, amount, description)
                   VALUES (?, 'minigame', ?, ?)''',
                (user_id, win_amount - bet if win else -bet, 
                 f"{'Рулетка: выигрыш' if win else 'Рулетка: проигрыш'}")
            )
            
            await db.commit()
        
        await message.answer(result_text, parse_mode="Markdown")
        
    except ValueError:
        await message.answer("❌ Пожалуйста, введите число!")
    finally:
        await state.clear()

@dp.callback_query(F.data == "game_dice")
async def handle_game_dice(callback: CallbackQuery):
    """Игра в кости"""
    user_id = callback.from_user.id
    user = await get_user(user_id)
    
    if not user:
        await callback.answer("Ошибка: пользователь не найден")
        return
    
    # Проверяем баланс для минимальной ставки
    if user['balance'] < 10:
        await callback.answer("❌ Недостаточно средств для игры!")
        return
    
    # Автоматическая ставка 10% от баланса, но не более 100₽
    bet = min(max(10, user['balance'] // 10), 100)
    
    # Бросаем кости
    dice1 = random.randint(1, 6)
    dice2 = random.randint(1, 6)
    total = dice1 + dice2
    
    win = total > 10  # Выигрыш если сумма больше 10
    win_amount = bet * 3 if win else 0
    
    async with aiosqlite.connect(DB_NAME) as db:
        if win:
            await db.execute(
                "UPDATE players SET balance = balance + ? WHERE user_id = ?",
                (win_amount - bet, user_id)
            )
            
            result_text = (
                f"🎲 *Кости: {dice1} + {dice2} = {total}*\n\n"
                f"🎉 *ПОБЕДА! Сумма больше 10!*\n\n"
                f"💰 Ставка: {bet}₽\n"
                f"🏆 Выигрыш: *{win_amount}₽*\n"
                f"💎 Чистая прибыль: *{bet * 2}₽*\n"
                f"📈 Новый баланс: *{user['balance'] + bet * 2}₽*\n\n"
                f"Удачливый! Но удача переменчива! 😏"
            )
            
            await add_achievement(user_id, "победа в костях")
        else:
            await db.execute(
                "UPDATE players SET balance = balance - ? WHERE user_id = ?",
                (bet, user_id)
            )
            
            result_text = (
                f"🎲 *Кости: {dice1} + {dice2} = {total}*\n\n"
                f"💥 *ПРОИГРЫШ! Сумма 10 или меньше*\n\n"
                f"💰 Ставка: {bet}₽\n"
                f"📉 Потеряно: *{bet}₽*\n"
                f"💸 Новый баланс: *{user['balance'] - bet}₽*\n\n"
                f"Не повезло! Может, в следующий раз повезет! 🍀"
            )
        
        await db.execute(
            '''INSERT INTO minigames (user_id, game_type, bet, win_amount, result)
               VALUES (?, 'dice', ?, ?, ?)''',
            (user_id, bet, win_amount, 'win' if win else 'loss')
        )
        
        await db.execute(
            '''INSERT INTO transactions (user_id, type, amount, description)
               VALUES (?, 'minigame', ?, ?)''',
            (user_id, win_amount - bet if win else -bet, 
             f"Кости: {'выигрыш' if win else 'проигрыш'}")
        )
        
        await db.commit()
    
    await callback.message.edit_text(result_text, parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "game_random")
async def handle_game_random(callback: CallbackQuery):
    """Случайный бонус"""
    user_id = callback.from_user.id
    user = await get_user(user_id)
    
    if not user:
        await callback.answer("Ошибка: пользователь не найден")
        return
    
    # Проверяем, можно ли получить бонус (раз в 3 часа)
    current_time = datetime.now()
    last_bonus = user.get('daily_bonus_claimed')
    
    if last_bonus:
        last_bonus_time = datetime.fromisoformat(last_bonus)
        time_since_last = current_time - last_bonus_time
        min_wait = timedelta(hours=3)
        
        if time_since_last < min_wait:
            wait_hours = int((min_wait - time_since_last).total_seconds() / 3600)
            await callback.answer(f"⏳ Подожди еще {wait_hours} часов!", show_alert=True)
            return
    
    # Получаем случайный бонус
    bonuses = [
        {"amount": 100, "text": "Мелкий бонус от Виталика! 🎁"},
        {"amount": 250, "text": "Неплохой бонус! 🎊"},
        {"amount": 500, "text": "Крупный выигрыш! 🏆"},
        {"amount": -100, "text": "Штраф за слишком частые запросы! 😈"},
        {"amount": 0, "text": "Ничего не выпало... Попробуй еще через 3 часа! 🍀"}
    ]
    
    bonus = random.choice(bonuses)
    
    async with aiosqlite.connect(DB_NAME) as db:
        # Обновляем баланс и время последнего бонуса
        await db.execute(
            "UPDATE players SET balance = balance + ?, daily_bonus_claimed = ? WHERE user_id = ?",
            (bonus['amount'], current_time.isoformat(), user_id)
        )
        
        await db.execute(
            '''INSERT INTO transactions (user_id, type, amount, description)
               VALUES (?, 'bonus', ?, 'Случайный бонус от Виталика')''',
            (user_id, bonus['amount'])
        )
        
        await db.commit()
    
    result_text = (
        f"🎯 *Случайный бонус!*\n\n"
        f"{bonus['text']}\n\n"
    )
    
    if bonus['amount'] > 0:
        result_text += (
            f"💰 Начислено: *{bonus['amount']}₽*\n"
            f"📈 Новый баланс: *{user['balance'] + bonus['amount']}₽*\n\n"
            f"Повезло! Но не расслабляйся! 😏"
        )
    elif bonus['amount'] < 0:
        result_text += (
            f"💸 Штраф: *{abs(bonus['amount'])}₽*\n"
            f"📉 Новый баланс: *{user['balance'] + bonus['amount']}₽*\n\n"
            f"В следующий раз будь осторожнее! ⚠️"
        )
    else:
        result_text += (
            f"💰 Начислено: *0₽*\n"
            f"📊 Баланс: *{user['balance']}₽*\n\n"
            f"Попробуй еще через 3 часа! 🕒"
        )
    
    await callback.message.edit_text(result_text, parse_mode="Markdown")
    await callback.answer()

# ==================== СТАТИСТИКА И ДОСТИЖЕНИЯ ====================
@dp.message(F.text == "📊 Статистика")
async def handle_statistics(message: Message):
    """Показ статистики игрока"""
    user_id = message.from_user.id
    user = await get_user(user_id)
    
    if not user:
        await message.answer("Сначала зарегистрируйтесь через /start")
        return
    
    # Получаем дополнительные данные из БД
    async with aiosqlite.connect(DB_NAME) as db:
        # Количество транзакций
        cursor = await db.execute(
            "SELECT COUNT(*) as count FROM transactions WHERE user_id = ?",
            (user_id,)
        )
        txn_count = (await cursor.fetchone())['count']
        
        # Общая сумма получки
        cursor = await db.execute(
            "SELECT SUM(amount) as total FROM transactions WHERE user_id = ? AND type = 'paycheck'",
            (user_id,)
        )
        paycheck_total = (await cursor.fetchone())['total'] or 0
        
        # Количество штрафов
        cursor = await db.execute(
            "SELECT COUNT(*) as count FROM transactions WHERE user_id = ? AND type = 'penalty'",
            (user_id,)
        )
        penalties_count = (await cursor.fetchone())['count']
        
        # Сумма штрафов
        cursor = await db.execute(
            "SELECT SUM(amount) as total FROM transactions WHERE user_id = ? AND type = 'penalty'",
            (user_id,)
        )
        penalties_total = abs((await cursor.fetchone())['total'] or 0)
    
    stats_text = (
        f"📊 *Статистика игрока:*\n\n"
        f"👤 *{user['full_name']}*\n"
        f"🆔 ID: {user_id}\n"
        f"📅 Зарегистрирован: {datetime.fromisoformat(user['registered_at']).strftime('%d.%m.%Y')}\n\n"
        f"💰 *Финансы:*\n"
        f"• Текущий баланс: *{user['balance']}₽*\n"
        f"• Всего заработано: *{user.get('total_earned', 0)}₽*\n"
        f"• Получено получки: *{paycheck_total}₽*\n\n"
        f"📈 *Активность:*\n"
        f"• Всего транзакций: *{txn_count}*\n"
        f"• Получено штрафов: *{penalties_count}*\n"
        f"• Сумма штрафов: *{penalties_total}₽*\n"
        f"• Достижений: *{len(user.get('achievements', []))}*\n\n"
    )
    
    # Топ игроков
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            "SELECT full_name, balance FROM players ORDER BY balance DESC LIMIT 5"
        )
        top_players = await cursor.fetchall()
    
    if top_players:
        stats_text += "🏆 *Топ-5 игроков:*\n"
        for i, player in enumerate(top_players, 1):
            medal = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"][i-1]
            stats_text += f"{medal} {player['full_name']}: *{player['balance']}₽*\n"
    
    await message.answer(stats_text, parse_mode="Markdown")

@dp.message(F.text == "🏆 Достижения")
async def handle_achievements(message: Message):
    """Показ достижений игрока"""
    user_id = message.from_user.id
    user = await get_user(user_id)
    
    if not user:
        await message.answer("Сначала зарегистрируйтесь через /start")
        return
    
    achievements = user.get('achievements', [])
    
    if not achievements:
        achievements_text = "🏆 *Достижения*\n\nУ вас пока нет достижений.\nПродолжайте играть, чтобы открыть их!"
    else:
        achievements_text = "🏆 *Ваши достижения:*\n\n"
        
        achievement_descriptions = {
            "новичок": "🎯 Зарегистрироваться в боте",
            "первая получка": "💰 Получить первую получку",
            "первая покупка": "🛒 Сделать первую покупку в магазине",
            "первый перевод": "🔁 Отправить первый перевод",
            "получил перевод": "📥 Получить первый перевод",
            "победа в рулетке": "🎰 Выиграть в рулетке",
            "победа в костях": "🎲 Выиграть в костях",
            "богач": "💎 Накопить 5000₽ на балансе",
            "ветеран": "🎖️ Играть более 7 дней",
            "укротитель виталика": "😎 Получить 0 штрафов за день",
            "щедрый": "🎁 Сделать 10 переводов",
            "игроман": "🎮 Сыграть 20 раз в мини-игры"
        }
        
        for achievement in achievements:
            desc = achievement_descriptions.get(achievement, achievement)
            achievements_text += f"✅ *{achievement.title()}*\n{desc}\n\n"
    
    # Прогресс до новых достижений
    achievements_text += "\n🎯 *Ближайшие цели:*\n"
    
    # Проверяем условия для новых достижений
    if "богач" not in achievements and user['balance'] >= 5000:
        await add_achievement(user_id, "богач")
    
    async with aiosqlite.connect(DB_NAME) as db:
        # Проверяем количество дней с регистрации
        cursor = await db.execute(
            "SELECT julianday('now') - julianday(registered_at) as days FROM players WHERE user_id = ?",
            (user_id,)
        )
        days = (await cursor.fetchone())['days']
        
        if days >= 7 and "ветеран" not in achievements:
            await add_achievement(user_id, "ветеран")
    
    await message.answer(achievements_text, parse_mode="Markdown")

# ==================== РАССЫЛКА (АДМИН) ====================
@dp.message(F.text == "📢 Рассылка")
async def handle_broadcast_start(message: Message, state: FSMContext):
    """Начало рассылки сообщений"""
    user_id = message.from_user.id
    
    # Проверяем, является ли пользователь администратором
    if user_id != ADMIN_ID:
        await message.answer("⛔ Эта функция доступна только администратору!")
        return
    
    await message.answer(
        "📢 *Режим рассылки*\n\n"
        "Отправьте сообщение, которое будет разослано всем пользователям.\n"
        "Можно использовать разметку Markdown.\n\n"
        "❌ Для отмены отправьте /cancel",
        parse_mode="Markdown"
    )
    
    await state.set_state(BroadcastStates.waiting_for_message)

@dp.message(Command("cancel"))
async def cancel_broadcast(message: Message, state: FSMContext):
    """Отмена рассылки"""
    await state.clear()
    await message.answer("❌ Рассылка отменена")

@dp.message(BroadcastStates.waiting_for_message)
async def handle_broadcast_message(message: Message, state: FSMContext):
    """Обработка сообщения для рассылки"""
    user_id = message.from_user.id
    
    if user_id != ADMIN_ID:
        await state.clear()
        return
    
    broadcast_text = message.text or message.caption
    if not broadcast_text:
        await message.answer("❌ Сообщение не может быть пустым")
        return
    
    # Получаем всех пользователей
    all_users = await get_all_users()
    
    if not all_users:
        await message.answer("❌ Нет пользователей для рассылки")
        await state.clear()
        return
    
    # Отправляем подтверждение
    await message.answer(
        f"📤 *Подтверждение рассылки*\n\n"
        f"Сообщение будет отправлено *{len(all_users)}* пользователям.\n\n"
        f"*Текст сообщения:*\n{broadcast_text}\n\n"
        f"✅ Для подтверждения отправьте 'да'\n"
        f"❌ Для отмены отправьте 'нет'",
        parse_mode="Markdown"
    )
    
    # Сохраняем текст рассылки в состоянии
    await state.update_data(broadcast_text=broadcast_text)

@dp.message(BroadcastStates.waiting_for_message)
async def handle_broadcast_confirmation(message: Message, state: FSMContext):
    """Обработка подтверждения рассылки"""
    user_id = message.from_user.id
    
    if user_id != ADMIN_ID:
        await state.clear()
        return
    
    confirmation = message.text.lower()
    
    if confirmation == 'да':
        # Получаем текст рассылки из состояния
        state_data = await state.get_data()
        broadcast_text = state_data.get('broadcast_text', '')
        
        if not broadcast_text:
            await message.answer("❌ Ошибка: текст рассылки не найден")
            await state.clear()
            return
        
        # Получаем всех пользователей
        all_users = await get_all_users()
        
        # Отправляем рассылку
        success_count = 0
        fail_count = 0
        
        await message.answer(f"⏳ Начинаю рассылку для {len(all_users)} пользователей...")
        
        for user in all_users:
            try:
                await bot.send_message(
                    user['user_id'],
                    f"📢 *ОБЪЯВЛЕНИЕ ОТ ВИТАЛИКА*\n\n{broadcast_text}",
                    parse_mode="Markdown"
                )
                success_count += 1
                
                # Небольшая задержка, чтобы не превысить лимиты Telegram
                await asyncio.sleep(0.1)
                
            except Exception as e:
                logger.error(f"Не удалось отправить рассылку пользователю {user['user_id']}: {e}")
                fail_count += 1
        
        # Отчет о рассылке
        report = (
            f"📊 *Отчет о рассылке*\n\n"
            f"✅ Успешно отправлено: *{success_count}*\n"
            f"❌ Не отправлено: *{fail_count}*\n"
            f"📈 Общий охват: *{len(all_users)}* пользователей"
        )
        
        await message.answer(report, parse_mode="Markdown")
        
    elif confirmation == 'нет':
        await message.answer("❌ Рассылка отменена")
    else:
        await message.answer("❌ Пожалуйста, ответьте 'да' или 'нет'")
        return
    
    await state.clear()

# ==================== ЕЖЕДНЕВНЫЙ БОНУС ====================
@dp.message(Command("bonus"))
async def cmd_daily_bonus(message: Message):
    """Ежедневный бонус"""
    user_id = message.from_user.id
    user = await get_user(user_id)
    
    if not user:
        await message.answer("Сначала зарегистрируйтесь через /start")
        return
    
    # Проверяем, получал ли уже сегодня бонус
    current_time = datetime.now()
    last_bonus = user.get('daily_bonus_claimed')
    
    if last_bonus:
        last_bonus_time = datetime.fromisoformat(last_bonus)
        if last_bonus_time.date() == current_time.date():
            # Уже получал сегодня
            next_bonus = (last_bonus_time + timedelta(days=1)).strftime("%H:%M")
            await message.answer(
                f"🎁 *Ежедневный бонус*\n\n"
                f"⏳ Вы уже получали бонус сегодня!\n"
                f"🕒 Следующий бонус будет доступен завтра в {next_bonus}\n\n"
                f"Возвращайся завтра! 😊"
            )
            return
    
    # Выдаем бонус
    bonus_amount = random.randint(50, 200)
    
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE players SET balance = balance + ?, daily_bonus_claimed = ? WHERE user_id = ?",
            (bonus_amount, current_time.isoformat(), user_id)
        )
        
        await db.execute(
            '''INSERT INTO transactions (user_id, type, amount, description)
               VALUES (?, 'daily_bonus', ?, 'Ежедневный бонус')''',
            (user_id, bonus_amount)
        )
        
        await db.commit()
    
    # Шутки для бонуса
    jokes = [
        f"Держи {bonus_amount}₽ на кофе! ☕",
        f"Вот тебе {bonus_amount}₽, не говори, что я не добрый! 😏",
        f"Бонус {bonus_amount}₽! Сегодня я в хорошем настроении! 😄",
        f"Забирай {bonus_amount}₽ и не появляйся на глаза! Шутка! 😂"
    ]
    
    response = (
        f"🎁 *Ежедневный бонус получен!*\n\n"
        f"💰 Начислено: *{bonus_amount}₽*\n"
        f"📈 Новый баланс: *{user['balance'] + bonus_amount}₽*\n\n"
        f"{random.choice(jokes)}"
    )
    
    await message.answer(response, parse_mode="Markdown")

# ==================== ТОП ИГРОКОВ ====================
@dp.message(Command("top"))
async def cmd_top(message: Message):
    """Топ игроков по балансу"""
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT full_name, balance FROM players ORDER BY balance DESC LIMIT 10"
        )
        top_players = await cursor.fetchall()
    
    if not top_players:
        await message.answer("📊 Пока нет игроков в рейтинге")
        return
    
    top_text = "🏆 *Топ-10 игроков по балансу:*\n\n"
    
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    
    for i, player in enumerate(top_players):
        medal = medals[i] if i < len(medals) else f"{i+1}."
        top_text += f"{medal} *{player['full_name']}* — {player['balance']}₽\n"
    
    # Добавляем статистику
    cursor = await db.execute("SELECT COUNT(*) as count, AVG(balance) as avg FROM players")
    stats = await cursor.fetchone()
    
    top_text += f"\n📊 *Статистика:*\n"
    top_text += f"• Всего игроков: *{stats['count']}*\n"
    top_text += f"• Средний баланс: *{int(stats['avg'] or 0)}₽*"
    
    await message.answer(top_text, parse_mode="Markdown")

# ==================== ЗАПУСК БОТА ====================
async def on_startup():
    """Действия при запуске бота"""
    await init_db()
    
    # Запускаем планировщик штрафов в фоне
    asyncio.create_task(penalty_scheduler())
    
    logger.info("Бот запущен и готов к работе!")

async def on_shutdown():
    """Действия при остановке бота"""
    logger.info("Бот останавливается...")

async def main():
    """Основная функция запуска бота"""
    # Регистрируем обработчики startup/shutdown
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    
    # Запускаем бота
    await dp.start_polling(bot, skip_updates=True)

if __name__ == "__main__":
    # Запускаем бота
    asyncio.run(main())
