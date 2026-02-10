"""
Telegram бот "Виталик Штрафующий" - РАБОЧАЯ ВЕРСИЯ
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
BOT_TOKEN = "8451168327:AAGQffadqqBg3pZNQnjctVxH-dUgXsovTr4"
ADMIN_ID = 5775839902  # Ваш Telegram ID

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# ==================== БАЗА ДАННЫХ ====================
DB_NAME = "vitalik_bot.db"

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS players (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                balance INTEGER DEFAULT 1000,
                last_paycheck TIMESTAMP,
                last_penalty TIMESTAMP,
                last_asphalt TIMESTAMP,
                penalty_immunity_until TIMESTAMP,
                daily_bonus_claimed TIMESTAMP,
                nagiret_boost_until TIMESTAMP,
                nagiret_penalty_multiplier REAL DEFAULT 1.0,
                total_penalties INTEGER DEFAULT 0,
                total_earned INTEGER DEFAULT 0,
                asphalt_meters INTEGER DEFAULT 0,
                asphalt_earned INTEGER DEFAULT 0,
                achievements TEXT DEFAULT '[]',
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
                bonus TEXT,
                purchased_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        await db.commit()
        logger.info("База данных инициализирована")

async def get_user(user_id: int) -> Optional[Dict[str, Any]]:
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
                user_dict['achievements'] = json.loads(user_dict['achievements'])
            else:
                user_dict['achievements'] = []
            return user_dict
        return None

async def register_user(user_id: int, username: str, full_name: str):
    async with aiosqlite.connect(DB_NAME) as db:
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

async def update_balance(user_id: int, amount: int, txn_type: str, description: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE players SET balance = balance + ? WHERE user_id = ?",
            (amount, user_id)
        )
        await db.execute(
            '''INSERT INTO transactions (user_id, type, amount, description)
               VALUES (?, ?, ?, ?)''',
            (user_id, txn_type, amount, description)
        )
        if amount > 0:
            await db.execute(
                "UPDATE players SET total_earned = total_earned + ? WHERE user_id = ?",
                (amount, user_id)
            )
        await db.commit()

async def get_all_users() -> List[Dict[str, Any]]:
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT user_id, full_name, username, balance FROM players"
        )
        users = await cursor.fetchall()
        return [dict(user) for user in users]

# ==================== МАШИНЫ СОСТОЯНИЙ ====================
class TransferStates(StatesGroup):
    choosing_recipient = State()
    entering_amount = State()

class BroadcastStates(StatesGroup):
    waiting_for_message = State()

class RouletteStates(StatesGroup):
    waiting_for_bet = State()

# ==================== ТОВАРЫ МАГАЗИНА ====================
SHOP_ITEMS = [
    {"id": "day_off", "name": "Выходной", "price": 500, "description": "Отдых от штрафов Виталика на 24 часа!", "bonus_chance": 0.7},
    {"id": "premium_boost", "name": "Премиум-Буст", "price": 1000, "description": "Увеличивает получку в 2 раза на 3 дня!", "bonus_chance": 0.8},
    {"id": "bonus_coin", "name": "Бонусная монета", "price": 300, "description": "Дает случайный бонус от Виталика!", "bonus_chance": 1.0},
    {"id": "insurance", "name": "Страховка от штрафов", "price": 800, "description": "Возмещает 50% от следующего штрафа!", "bonus_chance": 1.0},
    {"id": "lottery_ticket", "name": "Лотерейный билет", "price": 100, "description": "Шанс выиграть до 1000₽!", "bonus_chance": 0.3},
    {"id": "nagiret", "name": "Нагирт (таблетки)", "price": 600, "description": "Рандомный эффект: повышение получки или риск штрафа!", "bonus_chance": 1.0}
]

# ==================== КЛАВИАТУРЫ ====================
def get_main_keyboard() -> ReplyKeyboardMarkup:
    keyboard = [
        [KeyboardButton(text="💰 Получка"), KeyboardButton(text="🛒 Магазин")],
        [KeyboardButton(text="🔁 Перевод"), KeyboardButton(text="🎮 Мини-игры")],
        [KeyboardButton(text="📊 Статистика"), KeyboardButton(text="📢 Рассылка")]
    ]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

def get_shop_keyboard(user_balance: int) -> InlineKeyboardMarkup:
    buttons = []
    for item in SHOP_ITEMS:
        can_buy = user_balance >= item['price']
        button_text = f"{item['name']} - {item['price']}₽"
        if not can_buy:
            button_text = f"❌ {button_text}"
        buttons.append([InlineKeyboardButton(text=button_text, callback_data=f"buy_{item['id']}")])
    buttons.append([
        InlineKeyboardButton(text="💰 Баланс", callback_data="check_balance"),
        InlineKeyboardButton(text="◀️ Главное меню", callback_data="back_to_main")
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_minigames_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="🎰 Рулетка", callback_data="game_roulette")],
        [InlineKeyboardButton(text="🛣️ Укладка асфальта", callback_data="game_asphalt")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_main")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_asphalt_keyboard(can_work: bool = True) -> InlineKeyboardMarkup:
    if can_work:
        buttons = [[InlineKeyboardButton(text="🛣️ Уложить асфальт (1 метр)", callback_data="lay_asphalt")]]
    else:
        buttons = [[InlineKeyboardButton(text="⏳ Асфальт еще сохнет...", callback_data="asphalt_wait")]]
    buttons.append([InlineKeyboardButton(text="◀️ Назад в игры", callback_data="back_to_games")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_users_keyboard(users: List[Dict[str, Any]], exclude_id: int) -> InlineKeyboardMarkup:
    buttons = []
    for user in users:
        if user['user_id'] != exclude_id:
            buttons.append([
                InlineKeyboardButton(
                    text=f"{user['full_name']} ({user['balance']}₽)",
                    callback_data=f"transfer_to_{user['user_id']}"
                )
            ])
    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_transfer")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# ==================== СИСТЕМА ШТРАФОВ ВИТАЛИКА ====================
async def check_and_apply_penalties():
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT user_id, full_name, balance, penalty_immunity_until FROM players WHERE balance > 0")
        users = await cursor.fetchall()
        
        for user in users:
            user_id = user['user_id']
            user_dict = dict(user)
            
            immunity_until = user_dict.get('penalty_immunity_until')
            if immunity_until:
                immunity_time = datetime.fromisoformat(immunity_until) if immunity_until else None
                if immunity_time and immunity_time > datetime.now():
                    continue
            
            if random.random() <= 0.15:
                max_penalty = min(200, user_dict['balance'] * 0.3)
                penalty = random.randint(50, max(50, int(max_penalty)))
                
                if penalty > 0:
                    await db.execute(
                        "UPDATE players SET balance = balance - ?, last_penalty = ?, total_penalties = total_penalties + 1 WHERE user_id = ?",
                        (penalty, datetime.now().isoformat(), user_id)
                    )
                    
                    penalty_reasons = [
                        "штраф за плохое настроение Виталика! 😠",
                        "штраф за криво уложенный асфальт! 🛣️",
                        "штраф за слишком громкий смех на работе! 😂",
                        "штраф за кофе без печеньки! ☕",
                        "штраф за сон на рабочем месте! 💤"
                    ]
                    
                    reason = random.choice(penalty_reasons)
                    
                    await db.execute(
                        '''INSERT INTO transactions (user_id, type, amount, description)
                           VALUES (?, 'penalty', -?, ?)''',
                        (user_id, penalty, f"Штраф от Виталика: {reason}")
                    )
                    
                    try:
                        await bot.send_message(
                            user_id,
                            f"⚠️ *ВИТАЛИК ШТРАФУЕТ!*\n\n"
                            f"📛 Причина: {reason}\n"
                            f"💸 Сумма штрафа: *{penalty}₽*\n"
                            f"💰 Новый баланс: *{user_dict['balance'] - penalty}₽*\n\n"
                            f"Купи 'Выходной' в магазине!",
                            parse_mode="Markdown"
                        )
                    except Exception as e:
                        logger.error(f"Не удалось отправить уведомление: {e}")
        
        await db.commit()

async def penalty_scheduler():
    while True:
        try:
            await check_and_apply_penalties()
        except Exception as e:
            logger.error(f"Ошибка в планировщике штрафов: {e}")
        
        wait_time = random.randint(1800, 3600)
        await asyncio.sleep(wait_time)

# ==================== ОБРАБОТЧИКИ КОМАНД ====================
@dp.message(CommandStart())
async def cmd_start(message: Message):
    user_id = message.from_user.id
    username = message.from_user.username or "Без username"
    full_name = message.from_user.full_name

    await register_user(user_id, username, full_name)

    welcome_text = (
        f"👋 Привет, {full_name}!\n\n"
        f"Я Виталик, и я буду твоим начальником! 🏢\n"
        f"Будь осторожен — я люблю штрафовать! 😈\n\n"
        f"💰 Твой баланс: 1000₽\n"
        f"📊 Используй кнопки ниже:\n"
        f"• 💰 Получка — зарплата каждые 5-10 минут\n"
        f"• 🛒 Магазин — покупай полезные предметы\n"
        f"• 🔁 Перевод — отправляй деньги другим\n"
        f"• 🎮 Мини-игры — зарабатывай деньги\n"
        f"• 📊 Статистика — твоя статистика\n"
        f"• 📢 Рассылка — только для администратора\n\n"
        f"⚠️ Я могу оштрафовать тебя в любой момент!\n"
        f"💊 Попробуй Нагирт в магазине!\n"
        f"🛣️ Укладывай асфальт каждые 30 секунд!"
    )

    await message.answer(welcome_text, reply_markup=get_main_keyboard())

@dp.message(F.text == "💰 Получка")
async def handle_paycheck(message: Message):
    user_id = message.from_user.id
    user = await get_user(user_id)

    if not user:
        await message.answer("Сначала зарегистрируйтесь через /start")
        return

    current_time = datetime.now()
    last_paycheck = user.get('last_paycheck')

    if last_paycheck:
        last_paycheck_time = datetime.fromisoformat(last_paycheck)
        time_since_last = current_time - last_paycheck_time
        min_wait = timedelta(minutes=5)

        if time_since_last < min_wait:
            wait_minutes = int((min_wait - time_since_last).total_seconds() / 60)
            await message.answer(f"⏳ Слишком рано! Жди еще {wait_minutes} минут!")
            return

    paycheck_amount = random.randint(100, 500)
    
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

    user = await get_user(user_id)
    
    response = (
        f"💸 *Получка получена!*\n\n"
        f"📈 Начислено: *{paycheck_amount}₽*\n"
        f"💰 Новый баланс: *{user['balance']}₽*\n\n"
        f"Работай дальше, бездельник! 😏"
    )
    
    await message.answer(response, parse_mode="Markdown")

@dp.message(F.text == "🛒 Магазин")
async def handle_shop(message: Message):
    user_id = message.from_user.id
    user = await get_user(user_id)
    
    if not user:
        await message.answer("Сначала зарегистрируйтесь через /start")
        return
    
    shop_text = "🛒 *Магазин Виталика*\n\n"
    
    for item in SHOP_ITEMS:
        shop_text += (
            f"*{item['name']}*\n"
            f"💰 Цена: {item['price']}₽\n"
            f"📝 {item['description']}\n"
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
    user_id = callback.from_user.id
    user = await get_user(user_id)
    
    if not user:
        await callback.answer("❌ Пользователь не найден!")
        return
    
    # Получаем ID товара из callback_data
    callback_data = callback.data
    if "_" not in callback_data:
        await callback.answer("❌ Ошибка в данных товара!")
        return
    
    item_id = callback.data.split("_")[1]
    
    # Ищем товар в списке
    item = None
    for shop_item in SHOP_ITEMS:
        if shop_item["id"] == item_id:
            item = shop_item
            break
    
    if not item:
        await callback.answer("❌ Товар не найден!")
        return
    
    if user['balance'] < item['price']:
        await callback.answer(f"❌ Недостаточно средств! Нужно {item['price']}₽")
        return
    
    async with aiosqlite.connect(DB_NAME) as db:
        # Списываем деньги
        await db.execute(
            "UPDATE players SET balance = balance - ? WHERE user_id = ?",
            (item['price'], user_id)
        )
        
        # Применяем бонусы
        bonus_applied = random.random() <= item['bonus_chance']
        
        if item['id'] == 'day_off' and bonus_applied:
            immunity_until = (datetime.now() + timedelta(hours=24)).isoformat()
            await db.execute(
                "UPDATE players SET penalty_immunity_until = ? WHERE user_id = ?",
                (immunity_until, user_id)
            )
            bonus_text = "Иммунитет к штрафам на 24 часа!"
        elif item['id'] == 'bonus_coin' and bonus_applied:
            bonus_amount = random.randint(50, 200)
            await db.execute(
                "UPDATE players SET balance = balance + ? WHERE user_id = ?",
                (bonus_amount, user_id)
            )
            bonus_text = f"Бонус: {bonus_amount}₽!"
        elif item['id'] == 'lottery_ticket' and bonus_applied:
            lottery_win = random.randint(100, 1000)
            await db.execute(
                "UPDATE players SET balance = balance + ? WHERE user_id = ?",
                (lottery_win, user_id)
            )
            bonus_text = f"Выигрыш в лотерее: {lottery_win}₽!"
        else:
            bonus_text = "Без дополнительного бонуса"
        
        # Записываем покупку
        await db.execute(
            '''INSERT INTO purchases (user_id, item_name, price, bonus)
               VALUES (?, ?, ?, ?)''',
            (user_id, item['name'], item['price'], bonus_text)
        )
        
        # Записываем транзакцию
        await db.execute(
            '''INSERT INTO transactions (user_id, type, amount, description)
               VALUES (?, 'purchase', -?, ?)''',
            (user_id, item['price'], f"Покупка: {item['name']}")
        )
        
        await db.commit()
    
    # Получаем обновленные данные
    user = await get_user(user_id)
    
    response = (
        f"✅ *Покупка успешна!*\n\n"
        f"📦 Товар: *{item['name']}*\n"
        f"💸 Стоимость: *{item['price']}₽*\n"
        f"💰 Остаток: *{user['balance']}₽*\n"
    )
    
    if bonus_applied and bonus_text != "Без дополнительного бонуса":
        response += f"\n🎁 *Бонус:* {bonus_text}\n"
    
    response += f"\nУдачной покупки! 🛒"
    
    await callback.message.edit_text(response, parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "check_balance")
async def handle_check_balance(callback: CallbackQuery):
    user_id = callback.from_user.id
    user = await get_user(user_id)
    
    if user:
        await callback.answer(f"💰 Ваш баланс: {user['balance']}₽", show_alert=True)
    else:
        await callback.answer("Ошибка: пользователь не найден")

@dp.callback_query(F.data == "back_to_main")
async def handle_back_to_main(callback: CallbackQuery):
    try:
        await callback.message.delete()
    except:
        pass
    
    await callback.message.answer("Главное меню:", reply_markup=get_main_keyboard())
    await callback.answer()

@dp.callback_query(F.data == "back_to_games")
async def handle_back_to_games(callback: CallbackQuery):
    try:
        await callback.message.delete()
    except:
        pass
    
    await callback.message.answer("🎮 Мини-игры:", reply_markup=get_minigames_keyboard())
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
        "🎮 *Мини-игры от Виталика!*\n\n"
        "Выбери игру:\n\n"
        "🎰 *Рулетка (x2)*\n"
        "Введи свою ставку!\n"
        "Шанс выигрыша: 45%\n\n"
        "🛣️ *Укладка асфальта*\n"
        "Уложи 1 метр асфальта и получи 10₽!\n"
        "Перерыв: 30 секунд\n\n"
        f"💰 Твой баланс: {user['balance']}₽"
    )
    
    await message.answer(games_text, parse_mode="Markdown", reply_markup=get_minigames_keyboard())

# ==================== РУЛЕТКА (С ВВОДОМ СТАВКИ) ====================
@dp.callback_query(F.data == "game_roulette")
async def handle_game_roulette_start(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    user = await get_user(user_id)
    
    if not user:
        await callback.answer("Ошибка: пользователь не найден")
        return
    
    await callback.message.edit_text(
        f"🎰 *РУЛЕТКА*\n\n"
        f"💰 Твой баланс: {user['balance']}₽\n"
        f"🎯 Шанс выигрыша: 45%\n"
        f"💰 Выигрыш: x2 от ставки\n\n"
        f"💸 *Введи сумму ставки:*\n"
        f"Минимум: 10₽\n"
        f"Максимум: {min(500, user['balance'])}₽",
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
        win = random.random() <= 0.45
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
                    f"🎰 *РУЛЕТКА*\n\n"
                    f"🎉 *ПОБЕДА!*\n\n"
                    f"🎲 Выпало: *{chosen_color}*\n"
                    f"💰 Ставка: {bet}₽\n"
                    f"🏆 Выигрыш: *{win_amount}₽*\n"
                    f"💎 Чистая прибыль: *{bet}₽*\n"
                    f"💰 Новый баланс: *{user['balance'] + bet}₽*\n\n"
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
                    f"💰 Ставка: {bet}₽\n"
                    f"📉 Потеряно: *{bet}₽*\n"
                    f"💰 Новый баланс: *{user['balance'] - bet}₽*\n\n"
                    f"Не повезло... 🍀"
                )
            
            await db.execute(
                '''INSERT INTO transactions (user_id, type, amount, description)
                   VALUES (?, 'roulette', ?, ?)''',
                (user_id, win_amount - bet if win else -bet, 
                 f"Рулетка: {'выигрыш' if win else 'проигрыш'}")
            )
            
            await db.commit()
        
        await message.answer(result_text, parse_mode="Markdown", reply_markup=get_minigames_keyboard())
        
    except ValueError:
        await message.answer("❌ Пожалуйста, введите число!")
    finally:
        await state.clear()

# ==================== УКЛАДКА АСФАЛЬТА (РАБОТАЕТ) ====================
@dp.callback_query(F.data == "game_asphalt")
async def handle_game_asphalt(callback: CallbackQuery):
    user_id = callback.from_user.id
    user = await get_user(user_id)
    
    if not user:
        await callback.answer("Ошибка: пользователь не найден")
        return
    
    can_work = True
    wait_time = 0
    
    last_asphalt = user.get('last_asphalt')
    if last_asphalt:
        last_asphalt_time = datetime.fromisoformat(last_asphalt)
        time_since_last = datetime.now() - last_asphalt_time
        min_wait = timedelta(seconds=30)
        
        if time_since_last < min_wait:
            can_work = False
            wait_time = int((min_wait - time_since_last).total_seconds())
    
    if can_work:
        asphalt_text = (
            f"🛣️ *Укладка асфальта*\n\n"
            f"💰 Твой баланс: {user['balance']}₽\n"
            f"📏 Уложено метров: {user.get('asphalt_meters', 0)}\n"
            f"💵 Заработано на асфальте: {user.get('asphalt_earned', 0)}₽\n\n"
            f"Нажми кнопку ниже, чтобы уложить 1 метр асфальта.\n"
            f"За каждый метр получишь 10₽, но будь осторожен — Виталик может оштрафовать!\n\n"
            f"⏱️ Перерыв между укладкой: 30 секунд"
        )
    else:
        asphalt_text = (
            f"🛣️ *Укладка асфальта*\n\n"
            f"⏳ Асфальт еще сохнет!\n"
            f"Подожди еще {wait_time} секунд.\n\n"
            f"📏 Уложено метров: {user.get('asphalt_meters', 0)}\n"
            f"💵 Заработано на асфальте: {user.get('asphalt_earned', 0)}₽"
        )
    
    await callback.message.edit_text(
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
        await callback.answer("Ошибка: пользователь не найден")
        return
    
    last_asphalt = user.get('last_asphalt')
    if last_asphalt:
        last_asphalt_time = datetime.fromisoformat(last_asphalt)
        time_since_last = datetime.now() - last_asphalt_time
        min_wait = timedelta(seconds=30)
        
        if time_since_last < min_wait:
            wait_time = int((min_wait - time_since_last).total_seconds())
            await callback.answer(f"⏳ Подожди еще {wait_time} секунд!", show_alert=True)
            return
    
    # 70% шанс успеха, 30% шанс штрафа
    if random.random() <= 0.7:
        earnings = 10
        
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute(
                "UPDATE players SET balance = balance + ?, asphalt_meters = asphalt_meters + 1, asphalt_earned = asphalt_earned + ?, last_asphalt = ? WHERE user_id = ?",
                (earnings, earnings, datetime.now().isoformat(), user_id)
            )
            await db.execute(
                '''INSERT INTO transactions (user_id, type, amount, description)
                   VALUES (?, 'asphalt', ?, 'Укладка 1 метра асфальта')''',
                (user_id, earnings, "Укладка 1 метра асфальта")
            )
            await db.commit()
        
        user = await get_user(user_id)
        
        result_text = (
            f"✅ *Асфальт уложен успешно!*\n\n"
            f"🛣️ Уложен 1 метр асфальта\n"
            f"💰 Заработано: *{earnings}₽*\n"
            f"📏 Всего уложено: *{user.get('asphalt_meters', 0)} метров*\n"
            f"💵 Заработано на асфальте: *{user.get('asphalt_earned', 0)}₽*\n"
            f"💰 Новый баланс: *{user['balance']}₽*\n\n"
            f"Хорошая работа! 🏗️"
        )
    else:
        penalty = random.randint(5, 20)
        
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute(
                "UPDATE players SET balance = balance - ?, last_asphalt = ?, last_penalty = ?, total_penalties = total_penalties + 1 WHERE user_id = ?",
                (penalty, datetime.now().isoformat(), datetime.now().isoformat(), user_id)
            )
            await db.execute(
                '''INSERT INTO transactions (user_id, type, amount, description)
                   VALUES (?, 'penalty', -?, 'Штраф за плохую укладку асфальта')''',
                (user_id, penalty, "Штраф за плохую укладку асфальта")
            )
            await db.commit()
        
        user = await get_user(user_id)
        
        penalty_reasons = [
            "асфальт лег неровно! 📏",
            "использовал некачественный материал! 🧱",
            "работал слишком медленно! 🐌",
            "оставил мусор на дороге! 🗑️"
        ]
        
        result_text = (
            f"⚠️ *ВИТАЛИК ШТРАФУЕТ!*\n\n"
            f"🛣️ При укладке асфальта: {random.choice(penalty_reasons)}\n"
            f"💸 Штраф: *{penalty}₽*\n"
            f"💰 Новый баланс: *{user['balance']}₽*\n\n"
            f"Будь внимательнее! ⚠️"
        )
    
    await callback.message.edit_text(
        result_text,
        parse_mode="Markdown",
        reply_markup=get_asphalt_keyboard(False)
    )
    await callback.answer()

@dp.callback_query(F.data == "asphalt_wait")
async def handle_asphalt_wait(callback: CallbackQuery):
    user_id = callback.from_user.id
    user = await get_user(user_id)
    
    if not user:
        await callback.answer("Ошибка: пользователь не найден")
        return
    
    last_asphalt = user.get('last_asphalt')
    if last_asphalt:
        last_asphalt_time = datetime.fromisoformat(last_asphalt)
        time_since_last = datetime.now() - last_asphalt_time
        min_wait = timedelta(seconds=30)
        
        if time_since_last < min_wait:
            wait_time = int((min_wait - time_since_last).total_seconds())
            await callback.answer(f"⏳ Подожди еще {wait_time} секунд!", show_alert=True)
        else:
            await callback.answer("✅ Асфальт высох, можно укладывать!", show_alert=True)
    else:
        await callback.answer("✅ Можно начинать укладку!", show_alert=True)

# ==================== СТАТИСТИКА ====================
@dp.message(F.text == "📊 Статистика")
async def handle_statistics(message: Message):
    user_id = message.from_user.id
    user = await get_user(user_id)
    
    if not user:
        await message.answer("Сначала зарегистрируйтесь через /start")
        return
    
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM transactions WHERE user_id = ?",
            (user_id,)
        )
        txn_result = await cursor.fetchone()
        txn_count = txn_result[0] if txn_result else 0
        
        cursor = await db.execute(
            "SELECT SUM(amount) FROM transactions WHERE user_id = ? AND type = 'paycheck'",
            (user_id,)
        )
        paycheck_result = await cursor.fetchone()
        paycheck_total = paycheck_result[0] if paycheck_result and paycheck_result[0] else 0
        
        cursor = await db.execute(
            "SELECT COUNT(*) FROM transactions WHERE user_id = ? AND type = 'penalty'",
            (user_id,)
        )
        penalties_result = await cursor.fetchone()
        penalties_count = penalties_result[0] if penalties_result else 0
        
        cursor = await db.execute(
            "SELECT SUM(amount) FROM transactions WHERE user_id = ? AND type = 'penalty'",
            (user_id,)
        )
        penalties_sum_result = await cursor.fetchone()
        penalties_total = abs(penalties_sum_result[0]) if penalties_sum_result and penalties_sum_result[0] else 0
    
    reg_date = datetime.fromisoformat(user['registered_at']).strftime('%d.%m.%Y')
    
    stats_text = (
        f"📊 *Статистика игрока:*\n\n"
        f"👤 *{user['full_name']}*\n"
        f"📅 Зарегистрирован: {reg_date}\n\n"
        f"💰 *Финансы:*\n"
        f"• Текущий баланс: *{user['balance']}₽*\n"
        f"• Всего заработано: *{user.get('total_earned', 0)}₽*\n"
        f"• Получено получки: *{paycheck_total}₽*\n\n"
        f"📈 *Активность:*\n"
        f"• Всего транзакций: *{txn_count}*\n"
        f"• Получено штрафов: *{penalties_count}*\n"
        f"• Сумма штрафов: *{penalties_total}₽*\n"
        f"• Уложено асфальта: *{user.get('asphalt_meters', 0)} метров*\n"
        f"• Заработано на асфальте: *{user.get('asphalt_earned', 0)}₽*\n"
    )
    
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            "SELECT full_name, balance FROM players ORDER BY balance DESC LIMIT 5"
        )
        top_players = await cursor.fetchall()
    
    if top_players:
        stats_text += "\n🏆 *Топ-5 игроков:*\n"
        for i, player in enumerate(top_players, 1):
            medal = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"][i-1]
            stats_text += f"{medal} {player[0]}: *{player[1]}₽*\n"
    
    await message.answer(stats_text, parse_mode="Markdown")

# ==================== РАССЫЛКА ====================
@dp.message(F.text == "📢 Рассылка")
async def handle_broadcast_start(message: Message, state: FSMContext):
    user_id = message.from_user.id
    
    if user_id != ADMIN_ID:
        await message.answer("⛔ Эта функция доступна только администратору!")
        return
    
    await message.answer(
        "📢 *Режим рассылки*\n\n"
        "Отправьте сообщение для рассылки.\n"
        "❌ Для отмены отправьте /cancel",
        parse_mode="Markdown"
    )
    
    await state.set_state(BroadcastStates.waiting_for_message)

@dp.message(Command("cancel"))
async def cancel_broadcast(message: Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state == BroadcastStates.waiting_for_message:
        await state.clear()
        await message.answer("❌ Рассылка отменена")

@dp.message(BroadcastStates.waiting_for_message)
async def handle_broadcast_message(message: Message, state: FSMContext):
    user_id = message.from_user.id
    
    if user_id != ADMIN_ID:
        await state.clear()
        return
    
    broadcast_text = message.text
    if not broadcast_text:
        await message.answer("❌ Сообщение не может быть пустым")
        return
    
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
            
        except Exception as e:
            logger.error(f"Не удалось отправить рассылку: {e}")
            fail_count += 1
    
    report = (
        f"📊 *Отчет о рассылке*\n\n"
        f"✅ Успешно отправлено: *{success_count}*\n"
        f"❌ Не отправлено: *{fail_count}*\n"
        f"📈 Общий охват: *{len(all_users)}* пользователей"
    )
    
    await message.answer(report, parse_mode="Markdown")
    await state.clear()

# ==================== ЗАПУСК БОТА ====================
async def on_startup():
    await init_db()
    asyncio.create_task(penalty_scheduler())
    logger.info("Бот запущен и готов к работе!")

async def on_shutdown():
    logger.info("Бот останавливается...")

async def main():
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    await dp.start_polling(bot, skip_updates=True)

if __name__ == "__main__":
    asyncio.run(main())
