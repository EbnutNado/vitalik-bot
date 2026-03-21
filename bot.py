import logging
import os
import sqlite3
import sys
import tempfile
import traceback
import json
import re
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.middlewares.logging import LoggingMiddleware
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, InputFile
from aiogram.utils import executor
from html import escape

# ========== КОНФИГУРАЦИЯ ==========
BOT_TOKEN = "8460789866:AAHPtTNzZo_lmlECcBeWq_CEUsxQwejfSWc"
BOT_USERNAME = "podsl49_bot"
CHANNEL_ID = -1003572107512
ADMIN_CHAT_ID = -1003636427960
MAIN_ADMIN_IDS = [6042290296, 7412555136, 5775839902]
ADMIN_CONTACTS = "💬 Telegram: @podslyshano499"
DB_PATH = "bot_database.db"

# ========== НАСТРОЙКА ЛОГИРОВАНИЯ ==========
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# ========== АНТИСПАМ И ЦЕНЗУРА ==========
MAX_MEDIA_COUNT = 10
SPAM_COOLDOWN_SECONDS = 60
last_submit_times = {}

# Базовый список запрещенных слов (можно расширять)
BANNED_WORD_PATTERNS = [
    r"\bхуй\w*\b",
    r"\bпизд\w*\b",
    r"\bеб\w*\b",
    r"\bебал\w*\b",
    r"\bебан\w*\b",
    r"\bбля\w*\b",
    r"\bсука\w*\b",
    r"\bматьеб\w*\b",
    r"\bfuck\w*\b",
    r"\bshit\w*\b",
]

LOOKALIKE_MAP = str.maketrans({
    "a": "а", "b": "б", "c": "с", "e": "е", "k": "к", "m": "м", "h": "н", "o": "о",
    "p": "р", "t": "т", "x": "х", "y": "у", "r": "г", "i": "и", "l": "л", "n": "п",
    "3": "з", "4": "ч", "6": "б", "0": "о", "1": "л",
    "@": "а", "$": "с",
})

def normalize_for_filter(text: str) -> str:
    normalized = (text or "").lower().translate(LOOKALIKE_MAP).replace("ё", "е")
    # Убираем все, кроме букв/цифр/пробелов, чтобы "бл*я-ть" тоже ловилось
    normalized = re.sub(r"[^a-zа-я0-9\s]", "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized

def find_banned_fragment(text: str) -> str:
    if not text:
        return ""
    normalized_text = normalize_for_filter(text)
    compact_text = normalized_text.replace(" ", "")
    for pattern in BANNED_WORD_PATTERNS:
        match = re.search(pattern, normalized_text, flags=re.IGNORECASE)
        if match:
            return match.group(0)
        # Повторная проверка без пробелов против обходов типа "ма т ь е б а л"
        compact_pattern = pattern.replace(r"\b", "").replace(r"\w*", r"[а-яa-z0-9]*")
        match_compact = re.search(compact_pattern, compact_text, flags=re.IGNORECASE)
        if match_compact:
            return match_compact.group(0)
    return ""

def is_spam_blocked(user_tg_id: int, user_db_id: int):
    now = datetime.utcnow()
    last_time = last_submit_times.get(user_tg_id)
    if last_time and (now - last_time).total_seconds() < SPAM_COOLDOWN_SECONDS:
        seconds_left = SPAM_COOLDOWN_SECONDS - int((now - last_time).total_seconds())
        return True, f"⏱ Подождите {seconds_left} сек. перед следующей отправкой."

    return False, ""

def mark_submit_time(user_tg_id: int):
    last_submit_times[user_tg_id] = datetime.utcnow()

# ========== ИНИЦИАЛИЗАЦИЯ БОТА ==========
bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)
dp.middleware.setup(LoggingMiddleware())

# ========== БАЗА ДАННЫХ ==========
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
cursor = conn.cursor()

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
logger.info("База данных инициализирована")

def create_database_backup_file():
    """Согласованная копия SQLite (backup API), пока основное соединение открыто."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    backup_conn = sqlite3.connect(path)
    try:
        conn.backup(backup_conn)
    except Exception:
        backup_conn.close()
        try:
            os.unlink(path)
        except OSError:
            pass
        raise
    backup_conn.close()
    return path

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def get_or_create_user(tg_id, username, first_name):
    cursor.execute('SELECT * FROM users WHERE tg_id = ?', (tg_id,))
    user = cursor.fetchone()
    if not user:
        is_admin = 1 if tg_id in MAIN_ADMIN_IDS else 0
        cursor.execute('''
            INSERT INTO users (tg_id, username, first_name, is_admin)
            VALUES (?, ?, ?, ?)
        ''', (tg_id, username, first_name, is_admin))
        conn.commit()
        cursor.execute('SELECT * FROM users WHERE tg_id = ?', (tg_id,))
        user = cursor.fetchone()
    else:
        # Обновляем данные пользователя при каждом обращении (имя/юзернейм могли измениться)
        if user[2] != username or user[3] != first_name:
            cursor.execute('''
                UPDATE users SET username = ?, first_name = ? WHERE tg_id = ?
            ''', (username, first_name, tg_id))
            conn.commit()
            cursor.execute('SELECT * FROM users WHERE tg_id = ?', (tg_id,))
            user = cursor.fetchone()
    return user

def is_admin(user_id):
    cursor.execute('SELECT is_admin FROM users WHERE tg_id = ?', (user_id,))
    result = cursor.fetchone()
    return result and result[0] == 1

def format_post_text(post_text, user, is_anonymous):
    """Форматирует текст поста для публикации в канале"""
    safe_text = escape(post_text or "")
    if is_anonymous:
        author_line = "👤 Автор: Анонимно"
    else:
        display_name = user[3] if user[3] else "Пользователь"
        author_line = f"👤 Автор: {display_name}"
        if user[2]:
            author_line += f" (@{user[2]})"
    # HTML-ссылка на бота
    suggest_link = f'<a href="https://t.me/{BOT_USERNAME}">📢 | Предложить пост</a>'
    return f"{safe_text}\n\n{author_line}\n\n{suggest_link}"

# ========== КЛАВИАТУРЫ ==========
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
    keyboard.add(KeyboardButton("💾 Бэкап базы"))
    keyboard.add(
        KeyboardButton("🔙 Выйти из админки")
    )
    return keyboard

# ========== СОСТОЯНИЯ ==========
class PostStates(StatesGroup):
    waiting_for_content = State()
    waiting_for_anonymous_choice = State()

class SupportStates(StatesGroup):
    waiting_for_message = State()

class AdminStates(StatesGroup):
    waiting_for_admin_add = State()
    waiting_for_admin_remove = State()
    waiting_for_user_id_unban = State()
    waiting_for_support_response = State()

# ========== ОБРАБОТЧИКИ КОМАНД ==========
@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    try:
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
        if is_admin(message.from_user.id):
            admin_button = get_main_keyboard()
            admin_button.add(KeyboardButton("👑 Админ-панель"))
            await message.answer("🔐 У вас есть доступ к админ-панели", reply_markup=admin_button)
    except Exception as e:
        logger.error(f"Ошибка в /start: {e}\n{traceback.format_exc()}")
        await message.answer("Произошла ошибка. Попробуйте позже.")

@dp.message_handler(commands=['help'])
async def cmd_help(message: types.Message):
    help_text = (
        "ℹ️ <b>Помощь по боту</b>\n\n"
        "<b>📝 Предложить пост</b>\n"
        "• Отправьте текст, фото или видео\n"
        "• Выберите анонимность\n"
        "• Дождитесь модерации\n\n"
        "<b>📊 Мои посты</b>\n"
        "• Просмотр статуса ваших постов\n\n"
        "<b>🆘 Поддержка</b>\n"
        "• Связь с администрацией\n\n"
        "<b>❓ Частые вопросы:</b>\n"
        "• Посты проходят модерацию до 24 часов\n"
        "• При нарушении правил пост может быть отклонен"
    )
    await message.answer(help_text, parse_mode="HTML", reply_markup=get_main_keyboard())

# ========== ПРЕДЛОЖЕНИЕ ПОСТА ==========
@dp.message_handler(lambda message: message.text == "📝 Предложить пост")
async def suggest_post(message: types.Message):
    try:
        user = get_or_create_user(
            message.from_user.id,
            message.from_user.username,
            message.from_user.first_name
        )
        if user[4]:
            await message.answer("⛔ Вы забанены и не можете отправлять посты.")
            return

        await PostStates.waiting_for_content.set()
        await dp.current_state(user=message.from_user.id).update_data(text="", media_items=[])
        await message.answer(
            "📝 Отправьте контент для поста.\n\n"
            f"Можно прикрепить до {MAX_MEDIA_COUNT} фото/видео.\n"
            "Когда закончите, нажмите «✅ Готово отправить».\n"
            "Для отмены: «❌ Отмена».",
            reply_markup=ReplyKeyboardMarkup(resize_keyboard=True, row_width=2).add(
                KeyboardButton("✅ Готово отправить"),
                KeyboardButton("❌ Отмена")
            ),
        )
    except Exception as e:
        logger.error(f"Ошибка в suggest_post: {e}\n{traceback.format_exc()}")
        await message.answer("Произошла ошибка. Попробуйте позже.")

@dp.message_handler(content_types=['text', 'photo', 'video'], state=PostStates.waiting_for_content)
async def process_content(message: types.Message, state: FSMContext):
    if message.text and message.text == "❌ Отмена":
        await state.finish()
        await message.answer("❌ Отменено", reply_markup=get_main_keyboard())
        return

    data = await state.get_data()
    text_value = data.get("text", "")
    media_items = data.get("media_items", [])

    if message.text and message.text == "✅ Готово отправить":
        if not text_value and not media_items:
            await message.answer("Добавьте текст или медиа перед отправкой.")
            return
        await PostStates.waiting_for_anonymous_choice.set()
        await message.answer("🔒 Выберите вариант публикации:", reply_markup=get_anonymous_keyboard())
        return

    if message.text:
        merged_text = f"{text_value}\n{message.text}".strip() if text_value else message.text
        banned = find_banned_fragment(merged_text)
        if banned:
            await message.answer(
                f"🚫 Обнаружено запрещенное слово: <code>{escape(banned)}</code>.\n"
                "Исправьте текст и отправьте снова.",
                parse_mode="HTML"
            )
            return
        await state.update_data(text=merged_text)
        await message.answer("Текст добавлен. Нажмите «✅ Готово отправить», когда будете готовы.")
    elif message.photo:
        if len(media_items) >= MAX_MEDIA_COUNT:
            await message.answer(f"Можно прикрепить максимум {MAX_MEDIA_COUNT} медиа.")
            return
        caption = (message.caption or "").strip()
        banned = find_banned_fragment(caption)
        if banned:
            await message.answer(
                f"🚫 Подпись к фото содержит запрещенное слово: <code>{escape(banned)}</code>.\n"
                "Удалите его и отправьте фото снова.",
                parse_mode="HTML"
            )
            return
        media_items.append({"type": "photo", "id": message.photo[-1].file_id})
        if caption:
            text_value = f"{text_value}\n{caption}".strip() if text_value else caption
        await state.update_data(text=text_value, media_items=media_items)
        await message.answer(f"Фото добавлено ({len(media_items)}/{MAX_MEDIA_COUNT}).")
    elif message.video:
        if len(media_items) >= MAX_MEDIA_COUNT:
            await message.answer(f"Можно прикрепить максимум {MAX_MEDIA_COUNT} медиа.")
            return
        caption = (message.caption or "").strip()
        banned = find_banned_fragment(caption)
        if banned:
            await message.answer(
                f"🚫 Подпись к видео содержит запрещенное слово: <code>{escape(banned)}</code>.\n"
                "Удалите его и отправьте видео снова.",
                parse_mode="HTML"
            )
            return
        media_items.append({"type": "video", "id": message.video.file_id})
        if caption:
            text_value = f"{text_value}\n{caption}".strip() if text_value else caption
        await state.update_data(text=text_value, media_items=media_items)
        await message.answer(f"Видео добавлено ({len(media_items)}/{MAX_MEDIA_COUNT}).")

@dp.message_handler(state=PostStates.waiting_for_anonymous_choice)
async def process_anonymous_choice(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.finish()
        await message.answer("❌ Отменено", reply_markup=get_main_keyboard())
        return

    if message.text == "👤 От своего имени":
        is_anonymous = False
    elif message.text == "👻 Анонимно":
        is_anonymous = True
    else:
        await message.answer("Пожалуйста, выберите вариант с помощью кнопок")
        return

    data = await state.get_data()
    user = get_or_create_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name
    )

    blocked, reason = is_spam_blocked(message.from_user.id, user[0])
    if blocked:
        await message.answer(reason)
        return

    post_text = (data.get("text") or "").strip()
    banned = find_banned_fragment(post_text)
    if banned:
        await message.answer(
            f"🚫 В тексте поста есть запрещенное слово: <code>{escape(banned)}</code>.\n"
            "Исправьте текст перед отправкой.",
            parse_mode="HTML"
        )
        await PostStates.waiting_for_content.set()
        return
    media_items = data.get("media_items", [])
    if len(media_items) > MAX_MEDIA_COUNT:
        media_items = media_items[:MAX_MEDIA_COUNT]

    media_type = None
    media_id = None
    if len(media_items) == 1:
        media_type = media_items[0]["type"]
        media_id = media_items[0]["id"]
    elif len(media_items) > 1:
        media_type = "album"
        media_id = json.dumps(media_items, ensure_ascii=False)

    # Сохраняем пост
    cursor.execute('''
        INSERT INTO posts (user_id, text, media_type, media_id, is_anonymous)
        VALUES (?, ?, ?, ?, ?)
    ''', (user[0], post_text, media_type, media_id, is_anonymous))
    conn.commit()
    post_id = cursor.lastrowid
    mark_submit_time(message.from_user.id)

    # Формируем сообщение для админ-чата с HTML-форматированием
    admin_text = f"<b>📝 Новый пост #{post_id}</b>\n\n"
    admin_text += f"<b>👤 Реальный автор:</b> {escape(user[3] or 'Пользователь')}"
    if user[2]:
        admin_text += f" (@{escape(user[2])})"
    admin_text += f"\n<b>🆔 TG ID:</b> <code>{user[1]}</code>\n"
    admin_text += f"<b>👁 Для подписчиков:</b> {'Анонимно' if is_anonymous else 'От своего имени'}\n"
    admin_text += f"<b>🔒 Анонимно:</b> {'Да' if is_anonymous else 'Нет'}\n\n"
    admin_text += f"<b>📎 Медиа:</b> {len(media_items)} шт.\n\n"
    admin_text += f"<b>📄 Текст:</b>\n{escape(post_text) if post_text else '—'}\n"

    admin_keyboard = InlineKeyboardMarkup(row_width=2)
    admin_keyboard.add(
        InlineKeyboardButton("✅ Одобрить", callback_data=f"approve_{post_id}"),
        InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{post_id}")
    )

    try:
        if media_type == 'photo':
            admin_msg = await bot.send_photo(
                ADMIN_CHAT_ID,
                media_id,
                caption=admin_text,
                reply_markup=admin_keyboard,
                parse_mode="HTML"
            )
        elif media_type == 'video':
            admin_msg = await bot.send_video(
                ADMIN_CHAT_ID,
                media_id,
                caption=admin_text,
                reply_markup=admin_keyboard,
                parse_mode="HTML"
            )
        elif media_type == 'album':
            media_list = json.loads(media_id)
            preview = media_list[0]
            if preview["type"] == "video":
                await bot.send_video(ADMIN_CHAT_ID, preview["id"], caption="📎 Превью альбома")
            else:
                await bot.send_photo(ADMIN_CHAT_ID, preview["id"], caption="📎 Превью альбома")
            admin_msg = await bot.send_message(
                ADMIN_CHAT_ID,
                admin_text,
                reply_markup=admin_keyboard,
                parse_mode="HTML"
            )
        else:
            admin_msg = await bot.send_message(
                ADMIN_CHAT_ID,
                admin_text,
                reply_markup=admin_keyboard,
                parse_mode="HTML"
            )
        cursor.execute('UPDATE posts SET admin_chat_message_id = ? WHERE id = ?',
                       (admin_msg.message_id, post_id))
        conn.commit()
        await state.finish()
        await message.answer("✅ Пост отправлен на модерацию!", reply_markup=get_main_keyboard())

    except Exception as e:
        logger.error(f"Ошибка отправки в админ-чат: {e}\n{traceback.format_exc()}")
        await message.answer("❌ Произошла ошибка при отправке на модерацию. Попробуйте позже.")
        await state.finish()

# ========== ОДОБРЕНИЕ/ОТКЛОНЕНИЕ ==========
@dp.callback_query_handler(lambda c: c.data.startswith('approve_'))
async def approve_post(callback_query: types.CallbackQuery):
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("⛔ У вас нет прав", show_alert=True)
        return

    post_id = int(callback_query.data.split('_')[1])
    cursor.execute('''
        SELECT
            p.id, p.text, p.media_type, p.media_id, p.is_anonymous, p.status,
            u.tg_id, u.username, u.first_name
        FROM posts p
        JOIN users u ON p.user_id = u.id
        WHERE p.id = ?
    ''', (post_id,))
    post_data = cursor.fetchone()
    if not post_data or post_data[5] != 'pending':
        await callback_query.answer("Пост уже обработан", show_alert=True)
        return

    cursor.execute('UPDATE posts SET status = "approved", approved_at = CURRENT_TIMESTAMP WHERE id = ?', (post_id,))
    cursor.execute('INSERT INTO admin_logs (admin_id, post_id, action) VALUES (?, ?, "approve")',
                   (callback_query.from_user.id, post_id))
    conn.commit()

    post_text = post_data[1]
    media_type = (post_data[2] or "").strip().lower()
    media_id = post_data[3]
    is_anonymous = bool(post_data[4])
    user_data = (None, post_data[6], post_data[7], post_data[8])
    formatted_text = format_post_text(post_text, user_data, is_anonymous)

    try:
        if media_type == 'photo' and media_id:
            await bot.send_photo(CHANNEL_ID, media_id, caption=formatted_text, parse_mode="HTML")
        elif media_type == 'video' and media_id:
            await bot.send_video(CHANNEL_ID, media_id, caption=formatted_text, parse_mode="HTML")
        elif media_type == 'album' and media_id:
            media_items = json.loads(media_id)
            media_group = []
            for idx, item in enumerate(media_items[:MAX_MEDIA_COUNT]):
                if item["type"] == "video":
                    media = types.InputMediaVideo(media=item["id"])
                else:
                    media = types.InputMediaPhoto(media=item["id"])
                if idx == 0:
                    media.caption = formatted_text
                    media.parse_mode = "HTML"
                media_group.append(media)
            if media_group:
                await bot.send_media_group(CHANNEL_ID, media_group)
            else:
                await bot.send_message(CHANNEL_ID, formatted_text, parse_mode="HTML")
        else:
            await bot.send_message(CHANNEL_ID, formatted_text, parse_mode="HTML")

        await bot.send_message(post_data[6], f"✅ Ваш пост #{post_id} опубликован в канале!")
        admin_source_text = callback_query.message.text or callback_query.message.caption or ""
        await bot.edit_message_text(
            f"✅ <b>Пост #{post_id} одобрен</b> @{callback_query.from_user.username}\n\n{admin_source_text}",
            ADMIN_CHAT_ID,
            callback_query.message.message_id,
            parse_mode="HTML"
        )
        await callback_query.answer("Пост опубликован")
    except Exception as e:
        logger.error(f"Ошибка при публикации поста: {e}\n{traceback.format_exc()}")
        await callback_query.answer("Ошибка публикации", show_alert=True)

    await callback_query.message.edit_reply_markup()

@dp.callback_query_handler(lambda c: c.data.startswith('reject_'))
async def reject_post(callback_query: types.CallbackQuery):
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("⛔ У вас нет прав", show_alert=True)
        return

    post_id = int(callback_query.data.split('_')[1])
    cursor.execute('''
        SELECT p.id, p.status, u.tg_id
        FROM posts p
        JOIN users u ON p.user_id = u.id
        WHERE p.id = ?
    ''', (post_id,))
    post_data = cursor.fetchone()
    if not post_data or post_data[1] != 'pending':
        await callback_query.answer("Пост уже обработан", show_alert=True)
        return

    cursor.execute('UPDATE posts SET status = "rejected" WHERE id = ?', (post_id,))
    cursor.execute('INSERT INTO admin_logs (admin_id, post_id, action) VALUES (?, ?, "reject")',
                   (callback_query.from_user.id, post_id))
    conn.commit()

    user_tg_id = post_data[2]
    await bot.send_message(user_tg_id, f"❌ Ваш пост #{post_id} отклонён.")
    admin_source_text = callback_query.message.text or callback_query.message.caption or ""
    await bot.edit_message_text(
        f"❌ <b>Пост #{post_id} отклонён</b> @{callback_query.from_user.username}\n\n{admin_source_text}",
        ADMIN_CHAT_ID,
        callback_query.message.message_id,
        parse_mode="HTML"
    )
    await callback_query.answer("Пост отклонён")
    await callback_query.message.edit_reply_markup()

# ========== МОИ ПОСТЫ ==========
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

    status_emoji = {'pending': '⏳', 'approved': '✅', 'rejected': '❌'}
    text = "📊 <b>Ваши последние посты:</b>\n\n"
    for post in posts:
        text += f"{status_emoji.get(post[1], '❓')} Пост #{post[0]} - {post[2][:10]}\n"
    await message.answer(text, parse_mode="HTML")

# ========== ПОДДЕРЖКА ==========
@dp.message_handler(lambda message: message.text == "🆘 Поддержка")
async def support(message: types.Message):
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(KeyboardButton("📝 Написать в поддержку"))
    keyboard.add(KeyboardButton("🔙 Назад"))
    await message.answer(
        "🆘 <b>Раздел поддержки</b>\n\n"
        "Нажмите «Написать в поддержку» и отправьте сообщение.\n"
        "Администраторы ответят вам в ближайшее время.\n\n"
        f"Контакты: {ADMIN_CONTACTS}",
        parse_mode="HTML",
        reply_markup=keyboard
    )

@dp.message_handler(lambda message: message.text == "📝 Написать в поддержку")
async def write_to_support(message: types.Message):
    await SupportStates.waiting_for_message.set()
    await message.answer(
        "✍️ Напишите ваше сообщение. Для отмены нажмите /cancel или кнопку «❌ Отмена»",
        reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add(KeyboardButton("❌ Отмена"))
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
    cursor.execute('INSERT INTO support_requests (user_id, message) VALUES (?, ?)', (user[0], message.text))
    conn.commit()
    request_id = cursor.lastrowid

    admin_text = (
        f"<b>🆘 Новый запрос в поддержку #{request_id}</b>\n\n"
        f"<b>👤 От:</b> {user[3]}"
    )
    if user[2]:
        admin_text += f" (@{user[2]})"
    admin_text += f"\n<b>📝 Сообщение:</b>\n{message.text}"

    admin_keyboard = InlineKeyboardMarkup().add(
        InlineKeyboardButton("📝 Ответить", callback_data=f"reply_support_{request_id}")
    )
    try:
        await bot.send_message(ADMIN_CHAT_ID, admin_text, reply_markup=admin_keyboard, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Не удалось отправить запрос поддержки в админ-чат: {e}")

    await state.finish()
    await message.answer("✅ Сообщение отправлено! Администраторы ответят.", reply_markup=get_main_keyboard())

@dp.callback_query_handler(lambda c: c.data.startswith('reply_support_'))
async def reply_to_support(callback_query: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("⛔ Доступ запрещен", show_alert=True)
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
        f"📝 Ответ на запрос #{request_id}\n\nСообщение пользователя:\n{request[2]}\n\nВведите ваш ответ:"
    )
    await callback_query.answer()

@dp.message_handler(state=AdminStates.waiting_for_support_response)
async def process_support_response(message: types.Message, state: FSMContext):
    data = await state.get_data()
    request_id = data['request_id']
    user_tg_id = data.get('user_tg_id')

    if not user_tg_id:
        cursor.execute('''
            SELECT u.tg_id FROM support_requests sr
            JOIN users u ON sr.user_id = u.id
            WHERE sr.id = ?
        ''', (request_id,))
        row = cursor.fetchone()
        if row:
            user_tg_id = row[0]
        else:
            await message.answer("❌ Не удалось найти пользователя для ответа")
            await state.finish()
            return

    cursor.execute('''
        UPDATE support_requests
        SET status = 'resolved', admin_response = ?, resolved_at = CURRENT_TIMESTAMP
        WHERE id = ?
    ''', (message.text, request_id))
    conn.commit()

    try:
        await bot.send_message(
            user_tg_id,
            f"🆘 <b>Ответ от администрации</b>\n\n{message.text}\n\nЕсли остались вопросы, напишите снова.",
            parse_mode="HTML"
        )
        await message.answer("✅ Ответ отправлен пользователю")
    except Exception as e:
        logger.error(f"Не удалось отправить ответ пользователю {user_tg_id}: {e}")
        await message.answer(f"❌ Не удалось отправить ответ (пользователь заблокировал бота). Ответ сохранён в базе.")

    await state.finish()

# ========== КНОПКА "НАЗАД" ==========
@dp.message_handler(lambda message: message.text == "🔙 Назад")
async def back_to_main(message: types.Message):
    await message.answer("Главное меню", reply_markup=get_main_keyboard())

@dp.message_handler(lambda message: message.text == "ℹ️ Помощь")
async def show_help(message: types.Message):
    await cmd_help(message)

# ========== АДМИН-ПАНЕЛЬ ==========
@dp.message_handler(lambda message: message.text == "👑 Админ-панель")
async def admin_panel(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Доступ запрещен")
        return
    await message.answer("👑 <b>Админ-панель</b>\nВыберите действие:", parse_mode="HTML", reply_markup=get_admin_keyboard())

@dp.message_handler(lambda message: message.text == "🔙 Выйти из админки")
async def exit_admin(message: types.Message):
    await message.answer("Вы вышли из админ-панели", reply_markup=get_main_keyboard())

# ========== АДМИН-ФУНКЦИИ ==========
@dp.message_handler(lambda message: message.text == "📋 Ожидающие посты")
async def pending_posts(message: types.Message):
    if not is_admin(message.from_user.id): return
    cursor.execute('SELECT COUNT(*) FROM posts WHERE status = "pending"')
    count = cursor.fetchone()[0]
    await message.answer(f"📋 Ожидающие посты: {count} шт.\nПроверьте чат модерации")

@dp.message_handler(lambda message: message.text == "✅ Одобренные посты")
async def approved_posts(message: types.Message):
    if not is_admin(message.from_user.id): return
    cursor.execute('SELECT COUNT(*) FROM posts WHERE status = "approved" AND date(approved_at) = date("now")')
    today = cursor.fetchone()[0]
    cursor.execute('SELECT COUNT(*) FROM posts WHERE status = "approved"')
    total = cursor.fetchone()[0]
    await message.answer(f"✅ Сегодня: {today}\nВсего: {total}", parse_mode="HTML")

@dp.message_handler(lambda message: message.text == "💾 Бэкап базы")
async def admin_database_backup(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    backup_path = None
    try:
        backup_path = create_database_backup_file()
        stamp = datetime.utcnow().strftime("%Y-%m-%d_%H%M%S")
        filename = f"bot_database_backup_{stamp}.db"
        admin_name = escape(message.from_user.full_name or "Админ")
        un = message.from_user.username
        who_line = admin_name + (f" (@{escape(un)})" if un else "")
        caption = (
            f"💾 <b>Резервная копия БД</b>\n"
            f"🕐 UTC: <code>{escape(stamp)}</code>\n"
            f"👤 Запросил: {who_line}"
        )
        await bot.send_document(
            ADMIN_CHAT_ID,
            InputFile(backup_path, filename=filename),
            caption=caption,
            parse_mode="HTML",
        )
        await message.answer("✅ Бэкап отправлен в админский чат.")
    except Exception as e:
        logger.error(f"Ошибка бэкапа БД: {e}\n{traceback.format_exc()}")
        await message.answer("❌ Не удалось создать или отправить бэкап. Проверьте логи.")
    finally:
        if backup_path and os.path.isfile(backup_path):
            try:
                os.unlink(backup_path)
            except OSError:
                pass

@dp.message_handler(lambda message: message.text == "📊 Статистика")
async def admin_stats(message: types.Message):
    if not is_admin(message.from_user.id): return
    cursor.execute('SELECT COUNT(*) FROM posts WHERE status = "approved"')
    approved = cursor.fetchone()[0]
    cursor.execute('SELECT COUNT(*) FROM posts WHERE status = "pending"')
    pending = cursor.fetchone()[0]
    cursor.execute('SELECT COUNT(*) FROM users')
    users = cursor.fetchone()[0]
    cursor.execute('SELECT COUNT(*) FROM support_requests WHERE status = "pending"')
    support = cursor.fetchone()[0]
    await message.answer(
        f"✅ Одобрено: {approved}\n⏳ Ожидает: {pending}\n👥 Пользователей: {users}\n🆘 Запросов поддержки: {support}",
        parse_mode="HTML"
    )

@dp.message_handler(lambda message: message.text == "👥 Управление админами")
async def manage_admins(message: types.Message):
    if not is_admin(message.from_user.id): return
    cursor.execute('SELECT tg_id, first_name, username FROM users WHERE is_admin = 1')
    admins = cursor.fetchall()
    text = "👥 Список администраторов:\n\n"
    for a in admins:
        text += f"• {a[1]}" + (f" (@{a[2]})" if a[2] else "") + f" - ID: <code>{a[0]}</code>\n"
    keyboard = InlineKeyboardMarkup(row_width=2).add(
        InlineKeyboardButton("➕ Добавить админа", callback_data="add_admin"),
        InlineKeyboardButton("➖ Удалить админа", callback_data="remove_admin")
    )
    await message.answer(text, parse_mode="HTML", reply_markup=keyboard)

@dp.callback_query_handler(lambda c: c.data == "add_admin")
async def add_admin_prompt(callback_query: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("⛔ Доступ запрещен", show_alert=True)
        return
    await AdminStates.waiting_for_admin_add.set()
    await callback_query.message.answer("Введите ID пользователя:")
    await callback_query.answer()

@dp.callback_query_handler(lambda c: c.data == "remove_admin")
async def remove_admin_prompt(callback_query: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("⛔ Доступ запрещен", show_alert=True)
        return
    await AdminStates.waiting_for_admin_remove.set()
    await callback_query.message.answer("Введите ID пользователя:")
    await callback_query.answer()

@dp.message_handler(state=AdminStates.waiting_for_admin_add)
async def process_add_admin(message: types.Message, state: FSMContext):
    try:
        user_id = int(message.text.strip())
        cursor.execute('UPDATE users SET is_admin = 1 WHERE tg_id = ?', (user_id,))
        conn.commit()
        if cursor.rowcount > 0:
            await message.answer(f"✅ Пользователь {user_id} добавлен в администраторы")
            try:
                await bot.send_message(user_id, "🎉 Вам выданы права администратора!")
            except:
                pass
        else:
            await message.answer("❌ Пользователь не найден в базе. Попросите его написать /start")
    except ValueError:
        await message.answer("❌ Неверный ID")
    await state.finish()

@dp.message_handler(state=AdminStates.waiting_for_admin_remove)
async def process_remove_admin(message: types.Message, state: FSMContext):
    try:
        user_id = int(message.text.strip())
        if user_id in MAIN_ADMIN_IDS:
            await message.answer("❌ Нельзя удалить главного администратора")
            await state.finish()
            return
        cursor.execute('UPDATE users SET is_admin = 0 WHERE tg_id = ?', (user_id,))
        conn.commit()
        if cursor.rowcount > 0:
            await message.answer(f"✅ Пользователь {user_id} удален из администраторов")
            try:
                await bot.send_message(user_id, "⛔ Ваши права администратора отозваны")
            except:
                pass
        else:
            await message.answer("❌ Пользователь не является администратором")
    except ValueError:
        await message.answer("❌ Неверный ID")
    await state.finish()

@dp.message_handler(lambda message: message.text == "🚫 Заблокированные")
async def banned_users(message: types.Message):
    if not is_admin(message.from_user.id): return
    cursor.execute('SELECT tg_id, first_name, username FROM users WHERE is_banned = 1')
    banned = cursor.fetchall()
    if not banned:
        await message.answer("📭 Нет заблокированных пользователей")
        return
    text = "🚫 Заблокированные:\n\n"
    for u in banned:
        text += f"• {u[1]}" + (f" (@{u[2]})" if u[2] else "") + f" - ID: <code>{u[0]}</code>\n"
    keyboard = InlineKeyboardMarkup().add(InlineKeyboardButton("🔓 Разблокировать", callback_data="unban_menu"))
    await message.answer(text, parse_mode="HTML", reply_markup=keyboard)

@dp.callback_query_handler(lambda c: c.data == "unban_menu")
async def unban_menu(callback_query: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback_query.from_user.id):
        await callback_query.answer("⛔ Доступ запрещен", show_alert=True)
        return
    await AdminStates.waiting_for_user_id_unban.set()
    await callback_query.message.answer("Введите ID пользователя для разблокировки:")
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
                await bot.send_message(user_id, "✅ Вы разблокированы")
            except:
                pass
        else:
            await message.answer("❌ Пользователь не найден или не был заблокирован")
    except ValueError:
        await message.answer("❌ Неверный ID")
    await state.finish()

@dp.message_handler(lambda message: message.text == "🆘 Запросы поддержки")
async def support_requests_admin(message: types.Message):
    if not is_admin(message.from_user.id): return
    cursor.execute('''
        SELECT sr.id, sr.message, sr.created_at, u.first_name, u.username
        FROM support_requests sr
        JOIN users u ON sr.user_id = u.id
        WHERE sr.status = "pending"
        ORDER BY sr.created_at DESC
    ''')
    requests = cursor.fetchall()
    if not requests:
        await message.answer("📭 Нет активных запросов")
        return
    for req in requests:
        text = f"<b>🆘 Запрос #{req[0]}</b>\n<b>👤 От:</b> {req[3]}" + (f" (@{req[4]})" if req[4] else "") + f"\n<b>📅</b> {req[2][:19]}\n\n{req[1]}"
        keyboard = InlineKeyboardMarkup().add(InlineKeyboardButton("📝 Ответить", callback_data=f"reply_support_{req[0]}"))
        await message.answer(text, parse_mode="HTML", reply_markup=keyboard)

@dp.message_handler(commands=['cancel'], state='*')
async def cancel_command(message: types.Message, state: FSMContext):
    await state.finish()
    await message.answer("❌ Действие отменено", reply_markup=get_main_keyboard())

# ========== ЗАПУСК ==========
if __name__ == '__main__':
    # Принудительно обновляем права администраторов
    for admin_id in MAIN_ADMIN_IDS:
        cursor.execute('UPDATE users SET is_admin = 1 WHERE tg_id = ?', (admin_id,))
    conn.commit()
    logger.info("Бот запущен")
    executor.start_polling(dp, skip_updates=True)
