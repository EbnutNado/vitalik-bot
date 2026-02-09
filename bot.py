"""
Telegram бот "Виталик Штрафующий" с таблетками Нагирта
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
        
        await db.execute('''
            CREATE TABLE IF NOT EXISTS nagiret_effects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                effect_type TEXT,
                effect_value REAL,
                expires_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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

class MiniGameStates(StatesGroup):
    roulette_bet = State()
    dice_bet = State()

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
        [KeyboardButton(text="📊 Статистика"), KeyboardButton(text="🏆 Достижения")],
        [KeyboardButton(text="📢 Рассылка")]
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
        InlineKeyboardButton(text="📜 История", callback_data="purchase_history")
    ])
    buttons.append([InlineKeyboardButton(text="◀️ Главное меню", callback_data="back_to_main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_minigames_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="🎰 Рулетка (x2)", callback_data="game_roulette")],
        [InlineKeyboardButton(text="🎲 Кости (x3)", callback_data="game_dice")],
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
    """Проверка и наложение штрафов от Виталика"""
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT user_id, full_name, balance, penalty_immunity_until, nagiret_penalty_multiplier FROM players WHERE balance > 0")
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
            
            # Базовый шанс штрафа: 15%
            penalty_chance = 0.15
            
            # Учитываем множитель от Нагирта
            penalty_multiplier = user_dict.get('nagiret_penalty_multiplier', 1.0)
            if penalty_multiplier > 1.0:
                # Проверяем, не истек ли эффект Нагирта
                cursor2 = await db.execute(
                    "SELECT expires_at FROM nagiret_effects WHERE user_id = ? AND effect_type = 'penalty_risk' ORDER BY created_at DESC LIMIT 1",
                    (user_id,)
                )
                nagiret_effect = await cursor2.fetchone()
                if nagiret_effect and datetime.fromisoformat(nagiret_effect['expires_at']) > datetime.now():
                    penalty_chance *= penalty_multiplier
                    logger.info(f"Повышенный шанс штрафа для {user_id}: {penalty_chance*100}%")
                else:
                    # Эффект истек, сбрасываем множитель
                    await db.execute(
                        "UPDATE players SET nagiret_penalty_multiplier = 1.0 WHERE user_id = ?",
                        (user_id,)
                    )
            
            # Проверяем шанс штрафа
            if random.random() <= penalty_chance:
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
                        "штраф за сон на рабочем месте! 💤",
                        "штраф за разговор по телефону! 📱",
                        "штраф за опоздание на 1 минуту! ⏰",
                        "штраф за грязные ботинки! 👢",
                        "штраф за то, что сегодня понедельник! 📅",
                        "штраф за слишком красивую прическу! 💇",
                        "штраф за победу в конкурсе 'Лучший работник'! 🏆",
                        "штраф за отсутствие на собрании! 📊"
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
        wait_time = random.randint(1800, 3600)
        logger.info(f"Следующая проверка штрафов через {wait_time//60} минут")
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
        f"💊 Попробуй Нагирт в магазине — таблетки с рандомным эффектом!\n"
        f"🛣️ Укладывай асфальт каждые 30 секунд и зарабатывай!"
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
            await message.answer(
                f"⏳ Слишком рано для получки!\n"
                f"Подожди еще {wait_minutes} минут(ы), работяга! 😏"
            )
            return

    # Базовая сумма получки
    paycheck_amount = random.randint(100, 500)
    
    # Проверяем активные бусты
    async with aiosqlite.connect(DB_NAME) as db:
        # Проверяем премиум-буст из магазина
        cursor = await db.execute(
            """SELECT 1 FROM purchases 
               WHERE user_id = ? AND item_name = 'Премиум-Буст' 
               AND purchased_at > datetime('now', '-3 days')""",
            (user_id,)
        )
        has_premium_boost = await cursor.fetchone()
        
        # Проверяем Нагирт-буст
        cursor = await db.execute(
            """SELECT effect_value, expires_at FROM nagiret_effects 
               WHERE user_id = ? AND effect_type = 'paycheck_boost' 
               AND expires_at > datetime('now') 
               ORDER BY created_at DESC LIMIT 1""",
            (user_id,)
        )
        nagiret_boost = await cursor.fetchone()
    
    # Применяем бусты
    boost_multiplier = 1.0
    boost_text = ""
    
    if has_premium_boost:
        boost_multiplier *= 2
        boost_text += "Премиум-Буст: x2\n"
    
    if nagiret_boost:
        boost_value = nagiret_boost['effect_value']
        boost_multiplier *= boost_value
        boost_text += f"Нагирт-буст: x{boost_value}\n"
    
    if boost_multiplier > 1.0:
        paycheck_amount = int(paycheck_amount * boost_multiplier)
        boost_text = f"🎁 *Бусты активны:*\n{boost_text}\n"
    else:
        boost_text = ""

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

    jokes = [
        f"Держи {paycheck_amount}₽! Но не трать всё в одном месте... Или трать, мне-то что! 😄",
        f"Вот твоя получка: {paycheck_amount}₽. А теперь быстро на работу, бездельник! ⚡",
        f"{paycheck_amount}₽ к твоему балансу. Не благодари, лучше не провоцируй меня на штрафы! 😈",
        f"Получил {paycheck_amount}₽? Отлично! Теперь у меня есть повод оштрафовать тебя за слишком радостный вид! 🤣"
    ]
    
    response = (
        f"💸 *Получка получена!*\n\n"
        f"{boost_text}"
        f"📈 Начислено: *{paycheck_amount}₽*\n"
        f"💰 Новый баланс: *{user['balance'] + paycheck_amount}₽*\n\n"
        f"{random.choice(jokes)}"
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
    user_id = callback.from_user.id
    user = await get_user(user_id)
    item_id = callback.data.split("_")[1]
    
    # Находим товар
    item = None
    for shop_item in SHOP_ITEMS:
        if shop_item['id'] == item_id:
            item = shop_item
            break
    
    if not item:
        await callback.answer("❌ Товар не найден!")
        return
    
    # Проверяем баланс
    if user['balance'] < item['price']:
        await callback.answer(f"❌ Недостаточно средств! Нужно {item['price']}₽")
        return
    
    # Покупка товара
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE players SET balance = balance - ? WHERE user_id = ?",
            (item['price'], user_id)
        )
        
        bonus_applied = random.random() <= item['bonus_chance']
        
        # Обработка разных товаров
        if item['id'] == 'day_off' and bonus_applied:
            immunity_until = (datetime.now() + timedelta(hours=24)).isoformat()
            await db.execute(
                "UPDATE players SET penalty_immunity_until = ? WHERE user_id = ?",
                (immunity_until, user_id)
            )
            bonus_text = "Иммунитет к штрафам на 24 часа!"
        
        elif item['id'] == 'premium_boost' and bonus_applied:
            bonus_text = "Премиум-буст активирован на 3 дня!"
        
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
        
        elif item['id'] == 'nagiret':
            # Рандомный эффект Нагирта
            effect_type = random.choice(['paycheck_boost', 'penalty_risk'])
            
            if effect_type == 'paycheck_boost':
                # Повышение получки на 30-80%
                boost_value = round(random.uniform(1.3, 1.8), 1)
                expires_at = datetime.now() + timedelta(hours=2)
                
                await db.execute(
                    '''INSERT INTO nagiret_effects (user_id, effect_type, effect_value, expires_at)
                       VALUES (?, ?, ?, ?)''',
                    (user_id, effect_type, boost_value, expires_at.isoformat())
                )
                bonus_text = f"Нагирт: повышение получки на {int((boost_value-1)*100)}% на 2 часа!"
            
            else:  # penalty_risk
                # Увеличение шанса штрафа в 1.5-3 раза
                risk_multiplier = round(random.uniform(1.5, 3.0), 1)
                expires_at = datetime.now() + timedelta(hours=1)
                
                await db.execute(
                    "UPDATE players SET nagiret_penalty_multiplier = ? WHERE user_id = ?",
                    (risk_multiplier, user_id)
                )
                
                await db.execute(
                    '''INSERT INTO nagiret_effects (user_id, effect_type, effect_value, expires_at)
                       VALUES (?, ?, ?, ?)''',
                    (user_id, effect_type, risk_multiplier, expires_at.isoformat())
                )
                bonus_text = f"Нагирт: риск штрафа увеличен в {risk_multiplier} раз на 1 час!"
        
        else:
            bonus_text = "Без дополнительного бонуса"
        
        # Добавляем запись о покупке
        await db.execute(
            '''INSERT INTO purchases (user_id, item_name, price, bonus)
               VALUES (?, ?, ?, ?)''',
            (user_id, item['name'], item['price'], bonus_text)
        )
        
        await db.execute(
            '''INSERT INTO transactions (user_id, type, amount, description)
               VALUES (?, 'purchase', -?, ?)''',
            (user_id, item['price'], f"Покупка: {item['name']}")
        )
        
        await db.commit()
    
    response = (
        f"✅ *Покупка успешна!*\n\n"
        f"📦 Товар: *{item['name']}*\n"
        f"💸 Стоимость: *{item['price']}₽*\n"
        f"💰 Остаток: *{user['balance'] - item['price']}₽*\n"
    )
    
    if bonus_applied:
        response += f"\n🎁 *Бонус:* {bonus_text}\n"
    
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
    user_id = callback.from_user.id
    user = await get_user(user_id)
    
    if user:
        await callback.answer(f"💰 Ваш баланс: {user['balance']}₽", show_alert=True)
    else:
        await callback.answer("Ошибка: пользователь не найден")

@dp.callback_query(F.data == "purchase_history")
async def handle_purchase_history(callback: CallbackQuery):
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
    try:
        await callback.message.delete()
    except:
        pass
    
    await callback.message.answer(
        "Главное меню:", 
        reply_markup=get_main_keyboard()
    )
    await callback.answer()

@dp.callback_query(F.data == "back_to_games")
async def handle_back_to_games(callback: CallbackQuery):
    try:
        await callback.message.delete()
    except:
        pass
    
    await callback.message.answer(
        "🎮 Мини-игры:", 
        reply_markup=get_minigames_keyboard()
    )
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
        "Выбери игру и попробуй удачу:\n\n"
        "🎰 *Рулетка (x2)*\n"
        "Ставь на цвет! Красное или черное!\n"
        "Шанс выигрыша: 45%\n\n"
        "🎲 *Кости (x3)*\n"
        "Бросай кости! Выброси больше 10!\n"
        "Шанс выигрыша: 50%\n\n"
        "🛣️ *Укладка асфальта*\n"
        "Уложи 1 метр асфальта и получи 10₽!\n"
        "Но Виталик может оштрафовать за плохую работу!\n"
        "Перерыв между укладкой: 30 секунд\n\n"
        f"💰 Твой баланс: {user['balance']}₽"
    )
    
    await message.answer(games_text, parse_mode="Markdown", reply_markup=get_minigames_keyboard())

# ==================== УКЛАДКА АСФАЛЬТА ====================
@dp.callback_query(F.data == "game_asphalt")
async def handle_game_asphalt(callback: CallbackQuery):
    user_id = callback.from_user.id
    user = await get_user(user_id)
    
    if not user:
        await callback.answer("Ошибка: пользователь не найден")
        return
    
    # Проверяем, можно ли укладывать асфальт
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
            f"Подожди еще {wait_time} секунд перед следующей укладкой.\n\n"
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
    
    # Проверяем задержку
    last_asphalt = user.get('last_asphalt')
    if last_asphalt:
        last_asphalt_time = datetime.fromisoformat(last_asphalt)
        time_since_last = datetime.now() - last_asphalt_time
        min_wait = timedelta(seconds=30)
        
        if time_since_last < min_wait:
            wait_time = int((min_wait - time_since_last).total_seconds())
            await callback.answer(f"⏳ Подожди еще {wait_time} секунд!", show_alert=True)
            return
    
    # Определяем результат укладки
    # 70% шанс успешной укладки, 30% шанс штрафа
    if random.random() <= 0.7:
        # Успешная укладка
        earnings = 10
        result_text = (
            f"✅ *Асфальт уложен успешно!*\n\n"
            f"🛣️ Уложен 1 метр асфальта\n"
            f"💰 Заработано: *{earnings}₽*\n"
            f"📏 Всего уложено: *{user.get('asphalt_meters', 0) + 1} метров*\n"
            f"💵 Заработано на асфальте: *{user.get('asphalt_earned', 0) + earnings}₽*\n\n"
            f"Хорошая работа! Но не расслабляйся! 🏗️"
        )
        
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
    
    else:
        # Штраф за плохую укладку
        penalty = random.randint(5, 20)
        penalty_reasons = [
            "асфальт лег неровно! 📏",
            "использовал некачественный материал! 🧱",
            "работал слишком медленно! 🐌",
            "оставил мусор на дороге! 🗑️",
            "не по технологии уложил! 📋",
            "испортил соседнюю полосу! 🚧"
        ]
        
        result_text = (
            f"⚠️ *ВИТАЛИК ШТРАФУЕТ!*\n\n"
            f"🛣️ При укладке асфальта: {random.choice(penalty_reasons)}\n"
            f"💸 Штраф: *{penalty}₽*\n"
            f"💰 Новый баланс: *{user['balance'] - penalty}₽*\n\n"
            f"Будь внимательнее в следующий раз! ⚠️"
        )
        
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
    
    await callback.message.edit_text(
        result_text,
        parse_mode="Markdown",
        reply_markup=get_asphalt_keyboard(False)  # После укладки показываем кнопку ожидания
    )
    await callback.answer()

# ==================== ЗАПУСК БОТА ====================
async def on_startup():
    await init_db()
    # Запускаем планировщик штрафов в фоне
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
