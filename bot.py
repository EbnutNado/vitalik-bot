import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.middlewares.logging import LoggingMiddleware
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton
import config

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Инициализация бота
bot = Bot(token=config.BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)
dp.middleware.setup(LoggingMiddleware())

# Подключение к базе данных
conn = sqlite3.connect('bot_database.db', check_same_thread=False)
cursor = conn.cursor()

# Создание таблиц
cursor.execute('''
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tg_id INTEGER UNIQUE,
    username TEXT,
    first_name TEXT,
    is_banned BOOLEAN DEFAULT 0,
    is_admin BOOLEAN DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    text TEXT,
    media_type TEXT,
    media_id TEXT,
    is_anonymous BOOLEAN,
    status TEXT DEFAULT 'pending',
    admin_chat_message_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    approved_at TIMESTAMP,
    rejection_reason TEXT,
    FOREIGN KEY (user_id) REFERENCES users (id)
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS admin_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_id INTEGER,
    post_id INTEGER,
    action TEXT,
    reason TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS support_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    message TEXT,
    status TEXT DEFAULT 'pending',
    admin_response TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMP
)
''')

conn.commit()

# ID канала и чата модерации
CHANNEL_ID = config.CHANNEL_ID
ADMIN_CHAT_ID = config.ADMIN_CHAT_ID

# Клавиатуры для пользователей
def get_main_keyboard():
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    keyboard.add(
        KeyboardButton("📝 Предложить пост"),
        KeyboardButton("📊 Мои посты")
    )
    keyboard.add(
        KeyboardButton("🆘 Поддержка"),
        KeyboardButton("ℹ️ Помощь")
    )
    return keyboard

def get_anonymous_keyboard():
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(KeyboardButton("👤 От своего имени"), KeyboardButton("👻 Анонимно"))
    keyboard.add(KeyboardButton("❌ Отмена"))
    return keyboard

# Клавиатура для администраторов
def get_admin_keyboard():
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    keyboard.add(
        KeyboardButton("📋 Ожидающие посты"),
        KeyboardButton("✅ Одобренные посты")
    )
    keyboard.add(
        KeyboardButton("👥 Управление админами"),
        KeyboardButton("🚫 Заблокированные")
    )
    keyboard.add(
        KeyboardButton("📊 Статистика"),
        KeyboardButton("🆘 Запросы поддержки")
    )
    keyboard.add(
        KeyboardButton("🔙 Выйти из админки")
    )
    return keyboard

# Состояния
class PostStates(StatesGroup):
    waiting_for_content = State()
    waiting_for_anonymous_choice = State()

class SupportStates(StatesGroup):
    waiting_for_message = State()

class AdminStates(StatesGroup):
    waiting_for_user_id_ban = State()
    waiting_for_user_id_unban = State()
    waiting_for_admin_add = State()
    waiting_for_admin_remove = State()
    waiting_for_support_response = State()

def get_or_create_user(tg_id, username, first_name):
    cursor.execute('SELECT * FROM users WHERE tg_id = ?', (tg_id,))
    user = cursor.fetchone()
    
    if not user:
        # Проверяем, есть ли этот ID в списке главных администраторов
        is_admin = 1 if tg_id in config.MAIN_ADMIN_IDS else 0
        cursor.execute('''
        INSERT INTO users (tg_id, username, first_name, is_admin)
        VALUES (?, ?, ?, ?)
        ''', (tg_id, username, first_name, is_admin))
        conn.commit()
        cursor.execute('SELECT * FROM users WHERE tg_id = ?', (tg_id,))
        user = cursor.fetchone()
    
    return user

def format_post_text(post_text, user, is_anonymous):
    bot_username = config.BOT_USERNAME
    
    if is_anonymous:
        author_line = "👤 Автор: Анонимно"
    else:
        author_line = f"👤 Автор: {user[3]}"  # first_name
        if user[2]:  # username
            author_line += f" (@{user[2]})"
    
    suggest_link = f"[📢 | Предложить пост](https://t.me/{bot_username})"
    
    return f"{post_text}\n\n{author_line}\n\n{suggest_link}"

def is_admin(user_id):
    cursor.execute('SELECT is_admin FROM users WHERE tg_id = ?', (user_id,))
    result = cursor.fetchone()
    return result and result[0] == 1

@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    user = get_or_create_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name
    )
    
    welcome_text = (
        f"👋 Привет, {message.from_user.first_name}!\n\n"
        f"Я бот для предложения постов в канал.\n\n"
        f"📝 Чтобы предложить пост, нажми кнопку «Предложить пост»\n"
        f"🆘 Если есть вопросы или проблемы — нажми «Поддержка»\n"
        f"ℹ️ Подробнее о работе бота — «Помощь»\n\n"
        f"Все посты проходят модерацию перед публикацией."
    )
    
    await message.answer(welcome_text, reply_markup=get_main_keyboard())
    
    # Если пользователь администратор, показываем кнопку входа в админку
    if is_admin(message.from_user.id):
        admin_button = ReplyKeyboardMarkup(resize_keyboard=True).add(
            KeyboardButton("👑 Админ-панель")
        )
        await message.answer("🔐 У вас есть доступ к админ-панели", reply_markup=admin_button)

@dp.message_handler(lambda message: message.text == "👑 Админ-панель")
async def admin_panel(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Доступ запрещен")
        return
    
    await message.answer(
        "👑 **Админ-панель**\n\n"
        "Выберите действие:",
        reply_markup=get_admin_keyboard(),
        parse_mode="Markdown"
    )

@dp.message_handler(lambda message: message.text == "🔙 Выйти из админки")
async def exit_admin(message: types.Message):
    await message.answer(
        "Вы вышли из админ-панели",
        reply_markup=get_main_keyboard()
    )

@dp.message_handler(lambda message: message.text == "📝 Предложить пост")
async def suggest_post(message: types.Message):
    user = get_or_create_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name
    )
    
    if user[4]:  # is_banned
        await message.answer(
            "⛔ Вы забанены и не можете отправлять посты.\n"
            "Для выяснения причин обратитесь в поддержку."
        )
        return
    
    await PostStates.waiting_for_content.set()
    await message.answer(
        "📝 Отправьте текст или медиа для поста.\n\n"
        "Вы можете отправить:\n"
        "• Текст\n"
        "• Фото\n"
        "• Видео\n\n"
        "Для отмены нажмите «❌ Отмена»",
        reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add(
            KeyboardButton("❌ Отмена")
        )
    )

@dp.message_handler(content_types=['text', 'photo', 'video'], state=PostStates.waiting_for_content)
async def process_content(message: types.Message, state: FSMContext):
    if message.text and message.text == "❌ Отмена":
        await state.finish()
        await message.answer("❌ Отменено", reply_markup=get_main_keyboard())
        return
    
    if message.text:
        await state.update_data(text=message.text, media_type=None, media_id=None)
    elif message.photo:
        await state.update_data(
            text=message.caption or "",
            media_type='photo',
            media_id=message.photo[-1].file_id
        )
    elif message.video:
        await state.update_data(
            text=message.caption or "",
            media_type='video',
            media_id=message.video.file_id
        )
    
    await PostStates.waiting_for_anonymous_choice.set()
    await message.answer(
        "🔒 Выберите вариант публикации:",
        reply_markup=get_anonymous_keyboard()
    )

@dp.message_handler(state=PostStates.waiting_for_anonymous_choice)
async def process_anonymous_choice(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.finish()
        await message.answer("❌ Отменено", reply_markup=get_main_keyboard())
        return
    
    is_anonymous = None
    if message.text == "👤 От своего имени":
        is_anonymous = False
    elif message.text == "👻 Анонимно":
        is_anonymous = True
    
    if is_anonymous is None:
        await message.answer("Пожалуйста, выберите вариант с помощью кнопок")
        return
    
    data = await state.get_data()
    user = get_or_create_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name
    )
    
    cursor.execute('''
    INSERT INTO posts (user_id, text, media_type, media_id, is_anonymous)
    VALUES (?, ?, ?, ?, ?)
    ''', (user[0], data['text'], data.get('media_type'), data.get('media_id'), is_anonymous))
    conn.commit()
    
    post_id = cursor.lastrowid
    
    admin_text = f"📝 Новый пост #{post_id}\n"
    
    if is_anonymous:
        admin_text += f"👤 Автор: АНОНИМНО\n"
    else:
        admin_text += f"👤 Автор: {user[3]}"
        if user[2]:
            admin_text += f" (@{user[2]})"
        admin_text += f"\n"
    
    admin_text += f"🔒 Анонимно: {'Да' if is_anonymous else 'Нет'}\n\n"
    admin_text += f"📄 Текст:\n{data['text']}\n\n"
    
    admin_keyboard = InlineKeyboardMarkup(row_width=2)
    admin_keyboard.add(
        InlineKeyboardButton("✅ Одобрить", callback_data=f"approve_{post_id}"),
        InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{post_id}")
    )
    
    try:
        if data.get('media_type') == 'photo':
            admin_msg = await bot.send_photo(
                ADMIN_CHAT_ID,
                data['media_id'],
                caption=admin_text,
                reply_markup=admin_keyboard
            )
        elif data.get('media_type') == 'video':
            admin_msg = await bot.send_video(
                ADMIN_CHAT_ID,
                data['media_id'],
                caption=admin_text,
                reply_markup=admin_keyboard
            )
        else:
            admin_msg = await bot.send_message(
                ADMIN_CHAT_ID,
                admin_text,
                reply_markup=admin_keyboard
            )
        
        cursor.execute('UPDATE posts SET admin_chat_message_id = ? WHERE id = ?',
                      (admin_msg.message_id, post_id))
        conn.commit()
        
    except Exception as e:
        logging.error(f"Ошибка отправки админам: {e}")
        await message.answer("❌ Произошла ошибка при отправке на модерацию")
        await state.finish()
        return
    
    await state.finish()
    await message.answer(
        "✅ Пост отправлен на модерацию!\n"
        "Вы получите уведомление после решения администратора.",
        reply_markup=get_main_keyboard()
    )

@dp.callback_query_handler(lambda c: c.data.startswith('approve_'))
async def approve_post(callback_query: types.CallbackQuery):
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("⛔ У вас нет прав")
        return
    
    post_id = int(callback_query.data.split('_')[1])
    
    cursor.execute('''
    SELECT p.*, u.* FROM posts p
    JOIN users u ON p.user_id = u.id
    WHERE p.id = ?
    ''', (post_id,))
    post_data = cursor.fetchone()
    
    if not post_data or post_data[6] != 'pending':
        await callback_query.answer("Пост уже обработан")
        return
    
    cursor.execute('UPDATE posts SET status = "approved", approved_at = CURRENT_TIMESTAMP WHERE id = ?', (post_id,))
    cursor.execute('INSERT INTO admin_logs (admin_id, post_id, action) VALUES (?, ?, "approve")',
                  (callback_query.from_user.id, post_id))
    conn.commit()
    
    user_data = post_data[9:]
    formatted_text = format_post_text(
        post_data[3],
        user_data,
        bool(post_data[5])
    )
    
    try:
        if post_data[4] == 'photo':
            await bot.send_photo(CHANNEL_ID, post_data[5], caption=formatted_text, parse_mode="Markdown")
        elif post_data[4] == 'video':
            await bot.send_video(CHANNEL_ID, post_data[5], caption=formatted_text, parse_mode="Markdown")
        else:
            await bot.send_message(CHANNEL_ID, formatted_text, parse_mode="Markdown")
        
        await bot.send_message(user_data[1], f"✅ Ваш пост #{post_id} опубликован в канале!")
        
        await bot.edit_message_text(
            f"✅ Пост #{post_id} одобрен @{callback_query.from_user.username}\n\n{callback_query.message.text}",
            ADMIN_CHAT_ID,
            callback_query.message.message_id
        )
        
        await callback_query.answer("Пост опубликован")
    except Exception as e:
        logging.error(f"Ошибка: {e}")
        await callback_query.answer("Ошибка публикации")

@dp.callback_query_handler(lambda c: c.data.startswith('reject_'))
async def reject_post(callback_query: types.CallbackQuery):
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("⛔ У вас нет прав")
        return
    
    post_id = int(callback_query.data.split('_')[1])
    
    cursor.execute('''
    SELECT p.*, u.* FROM posts p
    JOIN users u ON p.user_id = u.id
    WHERE p.id = ?
    ''', (post_id,))
    post_data = cursor.fetchone()
    
    if not post_data or post_data[6] != 'pending':
        await callback_query.answer("Пост уже обработан")
        return
    
    cursor.execute('UPDATE posts SET status = "rejected" WHERE id = ?', (post_id,))
    cursor.execute('INSERT INTO admin_logs (admin_id, post_id, action) VALUES (?, ?, "reject")',
                  (callback_query.from_user.id, post_id))
    conn.commit()
    
    await bot.send_message(post_data[10], f"❌ Ваш пост #{post_id} отклонён.")
    
    await bot.edit_message_text(
        f"❌ Пост #{post_id} отклонён @{callback_query.from_user.username}\n\n{callback_query.message.text}",
        ADMIN_CHAT_ID,
        callback_query.message.message_id
    )
    
    await callback_query.answer("Пост отклонён")

@dp.message_handler(lambda message: message.text == "📊 Мои посты")
async def my_posts(message: types.Message):
    user = get_or_create_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name
    )
    
    cursor.execute('''
    SELECT id, status, created_at FROM posts 
    WHERE user_id = ? 
    ORDER BY created_at DESC LIMIT 10
    ''', (user[0],))
    posts = cursor.fetchall()
    
    if not posts:
        await message.answer("📭 У вас пока нет постов")
        return
    
    status_emoji = {
        'pending': '⏳',
        'approved': '✅',
        'rejected': '❌'
    }
    
    text = "📊 **Ваши последние посты:**\n\n"
    for post in posts:
        status_text = status_emoji.get(post[1], '❓')
        date = post[2][:10]
        text += f"{status_text} Пост #{post[0]} - {date}\n"
    
    await message.answer(text, parse_mode="Markdown")

@dp.message_handler(lambda message: message.text == "🆘 Поддержка")
async def support(message: types.Message):
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(KeyboardButton("📝 Написать в поддержку"))
    keyboard.add(KeyboardButton("🔙 Назад"))
    
    await message.answer(
        "🆘 **Раздел поддержки**\n\n"
        "Если у вас есть вопросы, проблемы или жалобы:\n"
        "• Нажмите «Написать в поддержку» и отправьте сообщение\n"
        "• Администраторы свяжутся с вами в ближайшее время\n\n"
        "Также вы можете обратиться напрямую:\n"
        f"👥 **Администрация канала**\n"
        f"{config.ADMIN_CONTACTS}",
        parse_mode="Markdown",
        reply_markup=keyboard
    )

@dp.message_handler(lambda message: message.text == "📝 Написать в поддержку")
async def write_to_support(message: types.Message):
    await SupportStates.waiting_for_message.set()
    await message.answer(
        "✍️ Напишите ваше сообщение.\n\n"
        "Опишите проблему подробно, чтобы мы могли вам помочь.\n"
        "Для отмены нажмите /cancel",
        reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add(
            KeyboardButton("❌ Отмена")
        )
    )

@dp.message_handler(state=SupportStates.waiting_for_message)
async def process_support_message(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.finish()
        await message.answer("❌ Отменено", reply_markup=get_main_keyboard())
        return
    
    user = get_or_create_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name
    )
    
    cursor.execute('''
    INSERT INTO support_requests (user_id, message)
    VALUES (?, ?)
    ''', (user[0], message.text))
    conn.commit()
    
    request_id = cursor.lastrowid
    
    # Уведомляем администраторов
    for admin_id in config.MAIN_ADMIN_IDS:
        try:
            admin_keyboard = InlineKeyboardMarkup()
            admin_keyboard.add(
                InlineKeyboardButton("📝 Ответить", callback_data=f"reply_support_{request_id}")
            )
            
            await bot.send_message(
                admin_id,
                f"🆘 Новый запрос в поддержку #{request_id}\n\n"
                f"👤 От: {user[3]}\n"
                f"🆔 ID: {user[1]}\n"
                f"📝 Сообщение:\n{message.text}",
                reply_markup=admin_keyboard
            )
        except:
            pass
    
    await state.finish()
    await message.answer(
        "✅ Ваше сообщение отправлено!\n"
        "Администраторы ответят вам в ближайшее время.",
        reply_markup=get_main_keyboard()
    )

@dp.callback_query_handler(lambda c: c.data.startswith('reply_support_'))
async def reply_to_support(callback_query: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("⛔ Доступ запрещен")
        return
    
    request_id = int(callback_query.data.split('_')[2])
    
    cursor.execute('''
    SELECT sr.*, u.tg_id, u.first_name FROM support_requests sr
    JOIN users u ON sr.user_id = u.id
    WHERE sr.id = ?
    ''', (request_id,))
    request = cursor.fetchone()
    
    if not request:
        await callback_query.answer("Запрос не найден")
        return
    
    await AdminStates.waiting_for_support_response.set()
    await state.update_data(request_id=request_id, user_tg_id=request[6])
    
    await callback_query.message.answer(
        f"📝 Ответ на запрос #{request_id}\n\n"
        f"Сообщение пользователя:\n{request[2]}\n\n"
        f"Введите ваш ответ:"
    )
    
    await callback_query.answer()

@dp.message_handler(state=AdminStates.waiting_for_support_response)
async def process_support_response(message: types.Message, state: FSMContext):
    data = await state.get_data()
    
    cursor.execute('''
    UPDATE support_requests 
    SET status = 'resolved', admin_response = ?, resolved_at = CURRENT_TIMESTAMP
    WHERE id = ?
    ''', (message.text, data['request_id']))
    conn.commit()
    
    # Отправляем ответ пользователю
    await bot.send_message(
        data['user_tg_id'],
        f"🆘 **Ответ от администрации**\n\n{message.text}\n\n"
        f"Если остались вопросы, напишите снова в поддержку.",
        parse_mode="Markdown"
    )
    
    await state.finish()
    await message.answer("✅ Ответ отправлен пользователю")

@dp.message_handler(lambda message: message.text == "🔙 Назад")
async def back_to_main(message: types.Message):
    await message.answer("Главное меню", reply_markup=get_main_keyboard())

@dp.message_handler(lambda message: message.text == "ℹ️ Помощь")
async def show_help(message: types.Message):
    help_text = (
        "ℹ️ **Помощь по боту**\n\n"
        "**📝 Предложить пост**\n"
        "• Отправьте текст, фото или видео\n"
        "• Выберите анонимность\n"
        "• Дождитесь модерации\n\n"
        "**📊 Мои посты**\n"
        "• Просмотр статуса ваших постов\n\n"
        "**🆘 Поддержка**\n"
        "• Связь с администрацией\n"
        "• Решение проблем и вопросов\n\n"
        "**❓ Частые вопросы:**\n"
        "• Посты проходят модерацию до 24 часов\n"
        "• При нарушении правил пост может быть отклонен\n"
        "• По всем вопросам обращайтесь в поддержку"
    )
    
    await message.answer(help_text, parse_mode="Markdown", reply_markup=get_main_keyboard())

# Админские функции
@dp.message_handler(lambda message: message.text == "📋 Ожидающие посты")
async def admin_pending_posts(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    
    cursor.execute('SELECT COUNT(*) FROM posts WHERE status = "pending"')
    count = cursor.fetchone()[0]
    
    await message.answer(f"📋 Ожидающие посты: {count} шт.\nПроверьте чат модерации")

@dp.message_handler(lambda message: message.text == "✅ Одобренные посты")
async def admin_approved_posts(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    
    cursor.execute('''
    SELECT COUNT(*) FROM posts 
    WHERE status = "approved" AND date(approved_at) = date('now')
    ''')
    today = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) FROM posts WHERE status = "approved"')
    total = cursor.fetchone()[0]
    
    await message.answer(
        f"✅ **Статистика одобренных постов**\n\n"
        f"📅 Сегодня: {today}\n"
        f"📊 Всего: {total}",
        parse_mode="Markdown"
    )

@dp.message_handler(lambda message: message.text == "👥 Управление админами")
async def manage_admins(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    
    cursor.execute('SELECT tg_id, first_name, username FROM users WHERE is_admin = 1')
    admins = cursor.fetchall()
    
    text = "👥 **Список администраторов:**\n\n"
    for admin in admins:
        text += f"• {admin[1]}"
        if admin[2]:
            text += f" (@{admin[2]})"
        text += f" - ID: `{admin[0]}`\n"
    
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("➕ Добавить админа", callback_data="add_admin"),
        InlineKeyboardButton("➖ Удалить админа", callback_data="remove_admin")
    )
    
    await message.answer(text, parse_mode="Markdown", reply_markup=keyboard)

@dp.callback_query_handler(lambda c: c.data == "add_admin")
async def add_admin_prompt(callback_query: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("⛔ Доступ запрещен")
        return
    
    await AdminStates.waiting_for_admin_add.set()
    await callback_query.message.answer(
        "➕ Введите ID пользователя, которого хотите сделать администратором:\n\n"
        "ID можно получить через @userinfobot"
    )
    await callback_query.answer()

@dp.callback_query_handler(lambda c: c.data == "remove_admin")
async def remove_admin_prompt(callback_query: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("⛔ Доступ запрещен")
        return
    
    await AdminStates.waiting_for_admin_remove.set()
    await callback_query.message.answer(
        "➖ Введите ID пользователя, которого хотите удалить из администраторов:\n\n"
        "⚠️ Вы не можете удалить главного администратора"
    )
    await callback_query.answer()

@dp.message_handler(state=AdminStates.waiting_for_admin_add)
async def process_add_admin(message: types.Message, state: FSMContext):
    try:
        user_id = int(message.text.strip())
        
        cursor.execute('UPDATE users SET is_admin = 1 WHERE tg_id = ?', (user_id,))
        conn.commit()
        
        if cursor.rowcount > 0:
            await message.answer(f"✅ Пользователь {user_id} добавлен в администраторы")
            
            # Уведомляем нового админа
            try:
                await bot.send_message(
                    user_id,
                    "🎉 Вам выданы права администратора!\n"
                    "Теперь вы можете модерировать посты."
                )
            except:
                pass
        else:
            await message.answer("❌ Пользователь не найден в базе. Попросите его написать /start")
    except ValueError:
        await message.answer("❌ Неверный формат ID. Введите число")
    
    await state.finish()

@dp.message_handler(state=AdminStates.waiting_for_admin_remove)
async def process_remove_admin(message: types.Message, state: FSMContext):
    try:
        user_id = int(message.text.strip())
        
        if user_id in config.MAIN_ADMIN_IDS:
            await message.answer("❌ Нельзя удалить главного администратора")
            await state.finish()
            return
        
        cursor.execute('UPDATE users SET is_admin = 0 WHERE tg_id = ?', (user_id,))
        conn.commit()
        
        if cursor.rowcount > 0:
            await message.answer(f"✅ Пользователь {user_id} удален из администраторов")
            
            try:
                await bot.send_message(
                    user_id,
                    "⛔ Ваши права администратора отозваны"
                )
            except:
                pass
        else:
            await message.answer("❌ Пользователь не является администратором")
    except ValueError:
        await message.answer("❌ Неверный формат ID")
    
    await state.finish()

@dp.message_handler(lambda message: message.text == "🚫 Заблокированные")
async def banned_users(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    
    cursor.execute('SELECT tg_id, first_name, username FROM users WHERE is_banned = 1')
    banned = cursor.fetchall()
    
    if not banned:
        await message.answer("📭 Нет заблокированных пользователей")
        return
    
    text = "🚫 **Заблокированные пользователи:**\n\n"
    for user in banned:
        text += f"• {user[1]}"
        if user[2]:
            text += f" (@{user[2]})"
        text += f" - ID: `{user[0]}`\n"
    
    keyboard = InlineKeyboardMarkup()
    keyboard.add(
        InlineKeyboardButton("🔓 Разблокировать", callback_data="unban_menu")
    )
    
    await message.answer(text, parse_mode="Markdown", reply_markup=keyboard)

@dp.callback_query_handler(lambda c: c.data == "unban_menu")
async def unban_menu(callback_query: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("⛔ Доступ запрещен")
        return
    
    await AdminStates.waiting_for_user_id_unban.set()
    await callback_query.message.answer(
        "🔓 Введите ID пользователя, которого хотите разблокировать:"
    )
    await callback_query.answer()

@dp.message_handler(state=AdminStates.waiting_for_user_id_unban)
async def process_unban(message: types.Message, state: FSMContext):
    try:
        user_id = int(message.text.strip())
        cursor.execute('UPDATE users SET is_banned = 0 WHERE tg_id = ?', (user_id,))
        conn.commit()
        
        if cursor.rowcount > 0:
            await message.answer(f"✅ Пользователь {user_id} разблокирован")
            try:
                await bot.send_message(user_id, "✅ Вы разблокированы администратором")
            except:
                pass
        else:
            await message.answer("❌ Пользователь не найден или не был заблокирован")
    except ValueError:
        await message.answer("❌ Неверный формат ID")
    
    await state.finish()

@dp.message_handler(lambda message: message.text == "📊 Статистика")
async def admin_stats(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    
    cursor.execute('SELECT COUNT(*) FROM posts WHERE status = "approved"')
    total_approved = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) FROM posts WHERE status = "pending"')
    total_pending = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) FROM posts WHERE status = "rejected"')
    total_rejected = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) FROM users')
    total_users = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) FROM users WHERE is_banned = 1')
    total_banned = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) FROM support_requests WHERE status = "pending"')
    pending_support = cursor.fetchone()[0]
    
    stats_text = (
        f"📊 **Статистика бота**\n\n"
        f"✅ Одобрено: {total_approved}\n"
        f"⏳ Ожидает: {total_pending}\n"
        f"❌ Отклонено: {total_rejected}\n"
        f"👥 Пользователей: {total_users}\n"
        f"🚫 Заблокировано: {total_banned}\n"
        f"🆘 Запросов поддержки: {pending_support}"
    )
    
    await message.answer(stats_text, parse_mode="Markdown")

@dp.message_handler(lambda message: message.text == "🆘 Запросы поддержки")
async def support_requests(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    
    cursor.execute('''
    SELECT sr.id, sr.message, sr.created_at, u.first_name, u.username 
    FROM support_requests sr
    JOIN users u ON sr.user_id = u.id
    WHERE sr.status = "pending"
    ORDER BY sr.created_at DESC
    ''')
    requests = cursor.fetchall()
    
    if not requests:
        await message.answer("📭 Нет активных запросов в поддержку")
        return
    
    for req in requests:
        text = (
            f"🆘 **Запрос #{req[0]}**\n"
            f"👤 От: {req[3]}"
        )
        if req[4]:
            text += f" (@{req[4]})"
        text += f"\n📅 {req[2][:19]}\n\n📝 {req[1]}"
        
        keyboard = InlineKeyboardMarkup()
        keyboard.add(
            InlineKeyboardButton("📝 Ответить", callback_data=f"reply_support_{req[0]}")
        )
        
        await message.answer(text, parse_mode="Markdown", reply_markup=keyboard)

@dp.message_handler(commands=['ban'])
async def ban_user(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    
    args = message.get_args()
    if not args:
        await message.answer("Использование: /ban [user_id]")
        return
    
    try:
        user_id = int(args)
        cursor.execute('UPDATE users SET is_banned = 1 WHERE tg_id = ?', (user_id,))
        conn.commit()
        
        if cursor.rowcount > 0:
            await message.answer(f"✅ Пользователь {user_id} заблокирован")
            try:
                await bot.send_message(user_id, "⛔ Вы заблокированы администратором")
            except:
                pass
        else:
            await message.answer("❌ Пользователь не найден")
    except ValueError:
        await message.answer("❌ Неверный формат ID")

@dp.message_handler(commands=['unban'])
async def unban_user(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    
    args = message.get_args()
    if not args:
        await message.answer("Использование: /unban [user_id]")
        return
    
    try:
        user_id = int(args)
        cursor.execute('UPDATE users SET is_banned = 0 WHERE tg_id = ?', (user_id,))
        conn.commit()
        
        if cursor.rowcount > 0:
            await message.answer(f"✅ Пользователь {user_id} разблокирован")
            try:
                await bot.send_message(user_id, "✅ Вы разблокированы администратором")
            except:
                pass
        else:
            await message.answer("❌ Пользователь не найден")
    except ValueError:
        await message.answer("❌ Неверный формат ID")

@dp.message_handler(commands=['adminlist'])
async def admin_list(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    
    cursor.execute('SELECT tg_id, first_name, username FROM users WHERE is_admin = 1')
    admins = cursor.fetchall()
    
    text = "👥 **Список администраторов:**\n\n"
    for admin in admins:
        text += f"• {admin[1]}"
        if admin[2]:
            text += f" (@{admin[2]})"
        text += f" - `{admin[0]}`\n"
    
    await message.answer(text, parse_mode="Markdown")

@dp.message_handler(commands=['cancel'], state='*')
async def cancel_command(message: types.Message, state: FSMContext):
    await state.finish()
    await message.answer("❌ Действие отменено", reply_markup=get_main_keyboard())

if __name__ == '__main__':
    from aiogram import executor
    executor.start_polling(dp, skip_updates=True)
