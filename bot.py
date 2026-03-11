import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import requests
import logging
import re
import base64
import time
import sqlite3
from datetime import datetime, timedelta
import threading

# ========== НАСТРОЙКИ ==========
TELEGRAM_TOKEN = "8451168327:AAGQffadqqBg3pZNQnjctVxH-dUgXsovTr4"
FOLDER_ID = "b1g0s9bjamjqrvas5pqr"
API_KEY = "AQVNxnq1d97ei8asrSCgEdGN92cXym_faQZ8I3dp"  # AQVN...
CHANNEL_ID = "@kamensk_avtodor_prorab"  # например @my_channel
ADMIN_IDS = [5775839902]  # твои Telegram ID
# ===============================

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
bot = telebot.TeleBot(TELEGRAM_TOKEN)

# URL API Яндекса
YANDEX_GPT_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
YANDEX_VISION_URL = "https://vision.api.cloud.yandex.net/vision/v1/batchAnalyze"

# ========== БАЗА ДАННЫХ (переработано для многопоточности) ==========
# Вместо одного глобального соединения будем создавать соединение в каждом потоке
# и передавать курсор. Но для простоты используем локальное хранилище для потоков.

thread_local = threading.local()

def get_db_connection():
    """Возвращает соединение с БД для текущего потока"""
    if not hasattr(thread_local, 'conn'):
        thread_local.conn = sqlite3.connect('bot.db', check_same_thread=False)
        thread_local.conn.row_factory = sqlite3.Row
    return thread_local.conn

def init_db():
    """Инициализация таблиц (вызывается один раз при старте)"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        joined_date TEXT,
        requests_today INTEGER DEFAULT 0,
        last_request_date TEXT,
        bonus_balance INTEGER DEFAULT 0,
        referral_code TEXT UNIQUE,
        referrer_id INTEGER,
        subscribed INTEGER DEFAULT 0,
        last_check TEXT,
        is_blocked INTEGER DEFAULT 0,
        total_requests INTEGER DEFAULT 0
    )
    ''')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS referrals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        inviter_id INTEGER,
        invitee_id INTEGER,
        date TEXT,
        rewarded INTEGER DEFAULT 0
    )
    ''')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS admin_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        admin_id INTEGER,
        action TEXT,
        target TEXT,
        date TEXT
    )
    ''')
    conn.commit()

init_db()

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========

def is_admin(user_id):
    return user_id in ADMIN_IDS

def get_user(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    return row

def create_user(user_id, username, first_name, referrer_id=None):
    conn = get_db_connection()
    cursor = conn.cursor()
    now = datetime.now().isoformat()
    # Генерация уникального реферального кода
    referral_code = base64.urlsafe_b64encode(str(user_id).encode()).decode().replace('=', '')[:10]
    cursor.execute('''
        INSERT OR IGNORE INTO users 
        (user_id, username, first_name, joined_date, last_request_date, referral_code, referrer_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (user_id, username, first_name, now, now[:10], referral_code, referrer_id))
    conn.commit()
    return referral_code

def update_user_subscription(user_id, subscribed):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET subscribed=?, last_check=? WHERE user_id=?", 
                   (1 if subscribed else 0, datetime.now().isoformat(), user_id))
    conn.commit()

def is_subscribed(user_id):
    """Проверяет подписку на канал с кешированием на 1 час"""
    user = get_user(user_id)
    if not user:
        return False
    # индексы: 9 - subscribed, 10 - last_check
    if user[10] and datetime.now() - datetime.fromisoformat(user[10]) < timedelta(hours=1):
        return user[9] == 1
    try:
        chat_member = bot.get_chat_member(CHANNEL_ID, user_id)
        status = chat_member.status
        subscribed = status in ['creator', 'administrator', 'member', 'restricted']
    except Exception as e:
        logging.error(f"Subscription check error for {user_id}: {e}")
        return False
    update_user_subscription(user_id, subscribed)
    return subscribed

def check_and_reset_daily(user_id):
    """Сбрасывает requests_today, если прошёл день"""
    conn = get_db_connection()
    cursor = conn.cursor()
    user = get_user(user_id)
    if not user:
        return
    last = user[5]  # last_request_date
    today = datetime.now().strftime("%Y-%m-%d")
    if last < today:
        cursor.execute("UPDATE users SET requests_today=0 WHERE user_id=?", (user_id,))
        conn.commit()

def can_make_request(user_id):
    """Проверяет лимит и возвращает (разрешено, остаток_на_сегодня, бонусов)"""
    if is_admin(user_id):
        return True, 999, 0
    user = get_user(user_id)
    if not user:
        return False, 0, 0
    check_and_reset_daily(user_id)
    user = get_user(user_id)
    daily_used = user[4]
    bonus = user[6]
    limit = 10 + bonus
    remaining = limit - daily_used
    return remaining > 0, max(0, remaining), bonus

def increment_request(user_id):
    """Увеличивает счётчик использованных запросов. Сначала тратятся бесплатные, потом бонусные."""
    conn = get_db_connection()
    cursor = conn.cursor()
    user = get_user(user_id)
    if not user:
        return
    if user[4] < 10:
        cursor.execute("UPDATE users SET requests_today = requests_today + 1 WHERE user_id=?", (user_id,))
    else:
        cursor.execute("UPDATE users SET bonus_balance = bonus_balance - 1 WHERE user_id=? AND bonus_balance > 0", (user_id,))
    cursor.execute("UPDATE users SET total_requests = total_requests + 1 WHERE user_id=?", (user_id,))
    conn.commit()

def add_bonus(user_id, amount):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET bonus_balance = bonus_balance + ? WHERE user_id=?", (amount, user_id))
    conn.commit()

def log_admin(admin_id, action, target):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO admin_logs (admin_id, action, target, date) VALUES (?, ?, ?, ?)",
                   (admin_id, action, target, datetime.now().isoformat()))
    conn.commit()

# ========== ПРОМПТЫ ДЛЯ ПРЕДМЕТОВ ==========
PROMPTS = {
    "math": """Ты — профессор математики. Решай уравнения и задачи подробно.
    Формулы пиши без LaTeX. Используй √ для корней, ²/³ для степеней, / для дробей.
    Дискриминант: D = b² - 4ac. Корни: x₁ = (-b + √D)/(2a), x₂ = (-b - √D)/(2a).
    Пример: 3x²+8x-7=0 → D=8²-4·3·(-7)=64+84=148, x₁=(-8+√148)/(6)=(-4+√37)/3, x₂=(-4-√37)/3.
    В конце обязательно "Ответ: ..."
    """,
    "physics": "Ты — физик. Используй единицы измерения, √, ², /. Заканчивай ответом.",
    "biology": "Ты — биолог. Объясняй простым языком. Без LaTeX.",
    "chemistry": "Ты — химик. Формулы: H₂O, CO₂. Реакции со стрелкой →.",
    "general": "Ты — помощник. Отвечай кратко и понятно. Без LaTeX."
}

SUBJECT_NAMES = {
    "math": "📐 Математика",
    "physics": "🔮 Физика",
    "biology": "🧬 Биология",
    "chemistry": "⚗️ Химия",
    "general": "📚 Любой"
}

# Хранилище выбранного предмета (в памяти)
user_subjects = {}

# ========== КЛАВИАТУРЫ ==========
def main_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=2)
    buttons = [
        InlineKeyboardButton("📐 Математика", callback_data="subj_math"),
        InlineKeyboardButton("🔮 Физика", callback_data="subj_physics"),
        InlineKeyboardButton("🧬 Биология", callback_data="subj_biology"),
        InlineKeyboardButton("⚗️ Химия", callback_data="subj_chemistry"),
        InlineKeyboardButton("📚 Любой", callback_data="subj_general"),
        InlineKeyboardButton("🔒 VPN 20+", callback_data="vpn"),
        InlineKeyboardButton("📖 Помощь", callback_data="help"),
        InlineKeyboardButton("📸 Фото задачи", callback_data="photo_help")
    ]
    keyboard.add(*buttons)
    return keyboard

def back_keyboard():
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("🔙 Назад", callback_data="back"))
    return keyboard

def subscribe_keyboard():
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("📢 Подписаться", url=f"https://t.me/{CHANNEL_ID.lstrip('@')}"))
    keyboard.add(InlineKeyboardButton("✅ Я подписался", callback_data="check_sub"))
    return keyboard

def admin_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=2)
    buttons = [
        InlineKeyboardButton("📊 Статистика", callback_data="admin_stats"),
        InlineKeyboardButton("📨 Рассылка", callback_data="admin_broadcast"),
        InlineKeyboardButton("🎁 Выдать запросы", callback_data="admin_grant"),
        InlineKeyboardButton("🚫 Блокировка", callback_data="admin_block"),
        InlineKeyboardButton("📋 Список пользователей", callback_data="admin_userlist"),
        InlineKeyboardButton("🔙 Выход", callback_data="back")
    ]
    keyboard.add(*buttons)
    return keyboard

# ========== ФОРМАТИРОВАНИЕ ОТВЕТА ==========
def format_answer(text, subject):
    if not text:
        return "❌ Пустой ответ от нейросети."
    # Убираем LaTeX
    text = re.sub(r'\\[\[\]\(\)]', '', text)
    text = re.sub(r'\$\$.*?\$\$', '', text, flags=re.DOTALL)
    text = re.sub(r'\$.*?\$', '', text, flags=re.DOTALL)
    text = text.replace('\\', '').replace('{', '').replace('}', '')
    # Корни
    text = re.sub(r'sqrt\((\d+)\)', r'√\1', text)
    text = re.sub(r'sqrt\(([^)]+)\)', r'√(\1)', text)
    # Степени
    text = re.sub(r'\^2', '²', text)
    text = re.sub(r'\^3', '³', text)
    # Умножение
    text = text.replace('*', '·')
    # Лишние пробелы
    text = re.sub(r'\s+', ' ', text).strip()
    return text

# ========== ЗАПРОСЫ К YANDEX ==========
def ask_yandex_gpt(question, subject):
    system_prompt = PROMPTS.get(subject, PROMPTS["general"])
    data = {
        "modelUri": f"gpt://{FOLDER_ID}/yandexgpt-lite",
        "completionOptions": {"stream": False, "temperature": 0.3, "maxTokens": "1500"},
        "messages": [{"role": "system", "text": system_prompt}, {"role": "user", "text": question}]
    }
    headers = {"Content-Type": "application/json", "Authorization": f"Api-Key {API_KEY}"}
    try:
        response = requests.post(YANDEX_GPT_URL, headers=headers, json=data, timeout=30)
        if response.status_code == 200:
            result = response.json()
            return result['result']['alternatives'][0]['message']['text']
        else:
            return f"❌ Ошибка YandexGPT: {response.status_code}"
    except Exception as e:
        logging.error(f"GPT error: {e}")
        return f"❌ Ошибка соединения: {str(e)}"

def ask_yandex_vision(photo_bytes):
    encoded = base64.b64encode(photo_bytes).decode('utf-8')
    data = {
        "folderId": FOLDER_ID,
        "analyzeSpecs": [{
            "content": encoded,
            "features": [{"type": "TEXT_DETECTION", "textDetectionConfig": {"languageCodes": ["ru", "en"]}}]
        }]
    }
    headers = {"Content-Type": "application/json", "Authorization": f"Api-Key {API_KEY}"}
    try:
        response = requests.post(YANDEX_VISION_URL, headers=headers, json=data, timeout=30)
        if response.status_code == 200:
            result = response.json()
            try:
                blocks = result['results'][0]['results'][0]['textDetection']['pages'][0]['blocks']
                words = [word['text'] for block in blocks for line in block['lines'] for word in line['words']]
                return ' '.join(words) if words else None
            except:
                return None
        else:
            return None
    except:
        return None

# ========== ДЕКОРАТОР ПРОВЕРКИ ПОДПИСКИ И ЛИМИТОВ ==========
def check_sub_and_limit(handler_func):
    def wrapper(message):
        user_id = message.from_user.id
        # Проверка подписки
        if not is_subscribed(user_id) and not is_admin(user_id):
            bot.send_message(user_id, "🚫 Чтобы пользоваться ботом, подпишись на канал:",
                             reply_markup=subscribe_keyboard())
            return
        # Проверка лимита
        allowed, remaining, bonus = can_make_request(user_id)
        if not allowed and not is_admin(user_id):
            bot.send_message(user_id,
                f"❌ Ты исчерпал дневной лимит (10 + {bonus} бонусных).\n"
                "Возвращайся завтра или пригласи друга (у тебя есть реферальная ссылка в /referral).")
            return
        # Если всё ок, вызываем основной обработчик
        return handler_func(message)
    return wrapper

# ========== ОБРАБОТЧИКИ КОМАНД ==========

@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.from_user.id
    username = message.from_user.username or ""
    first_name = message.from_user.first_name or ""
    # Проверяем реферальный параметр
    args = message.text.split()
    referrer_id = None
    if len(args) > 1 and args[1].startswith('ref_'):
        ref_code = args[1][4:]
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM users WHERE referral_code=?", (ref_code,))
        row = cursor.fetchone()
        if row:
            referrer_id = row[0]
    # Создаём пользователя, если его нет
    user = get_user(user_id)
    if not user:
        create_user(user_id, username, first_name, referrer_id)

    text = """
🚀 <b>РЕШАТОР ЗАДАЧ</b>

📐 Математика
🔮 Физика  
🧬 Биология
⚗️ Химия
📸 Фото задачи

🔒 <b>VPN 20+ серверов</b> — @ProrabVPN_bot
💰 200₽/мес

Выбери предмет или отправь задачу!
    """
    # Проверяем подписку, но не блокируем, просто напоминаем
    if not is_subscribed(user_id) and not is_admin(user_id):
        bot.send_message(user_id, "⚠️ Для использования бота необходимо подписаться на канал.", reply_markup=subscribe_keyboard())
    bot.send_message(user_id, text, parse_mode="HTML", reply_markup=main_keyboard())

@bot.message_handler(commands=['referral'])
def referral(message):
    user_id = message.from_user.id
    user = get_user(user_id)
    if not user:
        bot.send_message(user_id, "Сначала запусти бота через /start")
        return
    ref_code = user[7]  # referral_code
    link = f"https://t.me/{bot.get_me().username}?start=ref_{ref_code}"
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM referrals WHERE inviter_id=? AND rewarded=1", (user_id,))
    invited = cursor.fetchone()[0]
    bot.send_message(user_id,
        f"🔗 Твоя реферальная ссылка:\n{link}\n\n"
        f"Приглашено друзей: {invited}\n"
        "Бонус начисляется, если друг подписался на канал и нажал «Я подписался».\n"
        "За каждого друга ты получаешь +3 бонусных запроса, а друг +1.")

@bot.message_handler(commands=['balance'])
def balance(message):
    user_id = message.from_user.id
    user = get_user(user_id)
    if not user:
        bot.send_message(user_id, "Сначала запусти бота через /start")
        return
    daily_used = user[4]
    bonus = user[6]
    daily_left = max(0, 10 - daily_used)
    bot.send_message(
        user_id,
        f"📊 Твой баланс на сегодня:\n"
        f"• Бесплатных осталось: {daily_left}/10\n"
        f"• Бонусных (рефералы): {bonus}\n"
        f"• Всего доступно: {daily_left + bonus}"
    )

@bot.message_handler(commands=['admin'])
def admin_panel(message):
    if not is_admin(message.from_user.id):
        return
    bot.send_message(message.chat.id, "🛠 Админ-панель", reply_markup=admin_keyboard())

# ========== ОБРАБОТЧИК ТЕКСТА ==========
@bot.message_handler(func=lambda m: True, content_types=['text'])
@check_sub_and_limit
def handle_text(message):
    user_id = message.from_user.id
    subject = user_subjects.get(user_id, "general")
    msg = bot.send_message(user_id, "🤔 Думаю...")
    answer = ask_yandex_gpt(message.text, subject)
    answer = format_answer(answer, subject)
    increment_request(user_id)
    bot.edit_message_text(
        f"✅ <b>Решение:</b>\n\n{answer}\n\n━━━━━━━━━━━━━━━━━━━━━\n🔒 @ProrabVPN_bot - 20+ серверов, 200₽/мес",
        user_id,
        msg.message_id,
        parse_mode="HTML",
        reply_markup=back_keyboard()
    )

# ========== ОБРАБОТЧИК ФОТО ==========
@bot.message_handler(content_types=['photo'])
@check_sub_and_limit
def handle_photo(message):
    user_id = message.from_user.id
    subject = user_subjects.get(user_id, "general")
    status_msg = bot.send_message(user_id, "🔍 Распознаю текст...")
    file_id = message.photo[-1].file_id
    file_info = bot.get_file(file_id)
    downloaded = bot.download_file(file_info.file_path)
    recognized = ask_yandex_vision(downloaded)
    if not recognized:
        bot.edit_message_text("❌ Не удалось распознать текст. Попробуй отправить задачу текстом.", user_id, status_msg.message_id)
        return
    bot.edit_message_text(f"✅ Распознано:\n{recognized}\n\n🤔 Решаю...", user_id, status_msg.message_id)
    answer = ask_yandex_gpt(recognized, subject)
    answer = format_answer(answer, subject)
    increment_request(user_id)
    bot.send_message(user_id,
        f"✅ <b>Решение:</b>\n\n{answer}\n\n━━━━━━━━━━━━━━━━━━━━━\n🔒 @ProrabVPN_bot - 20+ серверов, 200₽/мес",
        parse_mode="HTML", reply_markup=back_keyboard())

# ========== ОБРАБОТЧИК КНОПОК ==========
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    user_id = call.from_user.id
    data = call.data

    # Возврат в главное меню
    if data == "back":
        bot.edit_message_text("🚀 <b>Главное меню</b>", user_id, call.message.message_id,
                              parse_mode="HTML", reply_markup=main_keyboard())
    # Помощь
    elif data == "help":
        text = """
📖 <b>Помощь</b>

📝 Отправь задачу текстом
📸 Или фото (распознаю)
📚 Выбери предмет для точности

📌 Примеры:
• 3x² + 8x - 7 = 0
• Сила тока при 220В и 50 Ом
• Что такое фотосинтез?

🔒 @ProrabVPN_bot - 20+ серверов, 200₽/мес
        """
        bot.edit_message_text(text, user_id, call.message.message_id, parse_mode="HTML", reply_markup=back_keyboard())
    # VPN
    elif data == "vpn":
        text = """
🔒 <b>PRORABVPN</b>

✅ 20+ серверов
✅ Безлимитный трафик
✅ Высокая скорость
✅ Все сайты

💰 200₽/мес
🚀 @ProrabVPN_bot
        """
        bot.edit_message_text(text, user_id, call.message.message_id, parse_mode="HTML", reply_markup=back_keyboard())
    # Подсказка по фото
    elif data == "photo_help":
        text = """
📸 <b>Отправь фото задачи</b>

1. Чёткий снимок
2. Хорошее освещение
3. Без бликов

Я распознаю и решу!
        """
        bot.edit_message_text(text, user_id, call.message.message_id, parse_mode="HTML", reply_markup=back_keyboard())
    # Выбор предмета
    elif data.startswith("subj_"):
        subject = data.replace("subj_", "")
        user_subjects[user_id] = subject
        name = SUBJECT_NAMES.get(subject, "Любой")
        bot.edit_message_text(f"✅ <b>Выбран предмет:</b> {name}\n\n📝 Теперь отправь задачу!",
                              user_id, call.message.message_id, parse_mode="HTML", reply_markup=back_keyboard())
    # Проверка подписки после нажатия "Я подписался"
    elif data == "check_sub":
        if is_subscribed(user_id):
            user = get_user(user_id)
            # Начисляем бонусы, если есть реферер и ещё не начисляли
            if user and user[8]:  # есть referrer_id
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute("SELECT rewarded FROM referrals WHERE inviter_id=? AND invitee_id=?", (user[8], user_id))
                row = cursor.fetchone()
                if not row:
                    # Начисляем бонусы
                    add_bonus(user[8], 3)  # +3 пригласившему
                    add_bonus(user_id, 1)  # +1 новичку
                    cursor.execute("INSERT INTO referrals (inviter_id, invitee_id, date, rewarded) VALUES (?,?,?,?)",
                                   (user[8], user_id, datetime.now().isoformat(), 1))
                    conn.commit()
                    bot.edit_message_text("✅ Спасибо за подписку! Тебе начислен бонусный запрос, а твоему другу — 3.",
                                          user_id, call.message.message_id)
                else:
                    bot.edit_message_text("✅ Спасибо за подписку! Бонусы уже были начислены ранее.",
                                          user_id, call.message.message_id, reply_markup=main_keyboard())
            else:
                bot.edit_message_text("✅ Спасибо за подписку! Теперь ты можешь пользоваться ботом.",
                                      user_id, call.message.message_id, reply_markup=main_keyboard())
        else:
            bot.answer_callback_query(call.id, "❌ Подписка не найдена. Попробуй ещё раз.", show_alert=True)

    # ===== АДМИН-КНОПКИ =====
    if not is_admin(user_id):
        return

    if data == "admin_stats":
        conn = get_db_connection()
        cursor = conn.cursor()
        total_users = cursor.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        active_today = cursor.execute("SELECT COUNT(*) FROM users WHERE last_request_date=date('now')").fetchone()[0]
        total_requests = cursor.execute("SELECT SUM(total_requests) FROM users").fetchone()[0] or 0
        text = (f"📊 <b>Общая статистика</b>\n\n"
                f"👥 Всего пользователей: {total_users}\n"
                f"🔥 Активных сегодня: {active_today}\n"
                f"📝 Всего запросов: {total_requests}")
        bot.edit_message_text(text, user_id, call.message.message_id, parse_mode="HTML", reply_markup=back_keyboard())

    elif data == "admin_userlist":
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, first_name, username, total_requests, requests_today, bonus_balance FROM users ORDER BY user_id DESC LIMIT 10")
        rows = cursor.fetchall()
        if not rows:
            text = "Список пуст."
        else:
            text = "📋 <b>Последние 10 пользователей:</b>\n\n"
            for r in rows:
                text += f"🆔 {r[0]} | {r[1] or '?'} @{r[2] or '—'}\n   Всего: {r[3]}, сегодня: {r[4]}, бонус: {r[5]}\n"
        bot.edit_message_text(text, user_id, call.message.message_id, parse_mode="HTML", reply_markup=back_keyboard())

    elif data == "admin_broadcast":
        msg = bot.send_message(user_id, "Введи текст для рассылки (можно с HTML).")
        bot.register_next_step_handler(msg, process_broadcast)

    elif data == "admin_grant":
        msg = bot.send_message(user_id, "Введи ID пользователя и количество запросов через пробел, например:\n123456789 5")
        bot.register_next_step_handler(msg, process_grant)

    elif data == "admin_block":
        msg = bot.send_message(user_id, "Введи ID пользователя для блокировки:")
        bot.register_next_step_handler(msg, process_block)

def process_broadcast(message):
    text = message.text
    bot.send_message(message.chat.id, f"Рассылка начата. Текст:\n{text}\n\nПодтверди /yes или отмени /no")
    bot.register_next_step_handler(message, lambda m: confirm_broadcast(m, text))

def confirm_broadcast(message, text):
    if message.text == '/yes':
        conn = get_db_connection()
        cursor = conn.cursor()
        users = cursor.execute("SELECT user_id FROM users WHERE subscribed=1 AND is_blocked=0").fetchall()
        success = 0
        failed = 0
        for (uid,) in users:
            try:
                bot.send_message(uid, text, parse_mode="HTML")
                success += 1
                time.sleep(0.05)
            except Exception as e:
                logging.warning(f"Broadcast error to {uid}: {e}")
                failed += 1
        bot.send_message(message.chat.id, f"✅ Рассылка завершена.\nУспешно: {success}\nОшибок: {failed}")
        log_admin(message.from_user.id, "broadcast", f"success:{success}, fail:{failed}")
    else:
        bot.send_message(message.chat.id, "Рассылка отменена")

def process_grant(message):
    try:
        parts = message.text.split()
        if len(parts) != 2:
            raise ValueError
        uid = int(parts[0])
        amount = int(parts[1])
        add_bonus(uid, amount)
        bot.send_message(message.chat.id, f"✅ Пользователю {uid} добавлено {amount} бонусных запросов.")
        log_admin(message.from_user.id, "grant", f"{uid} +{amount}")
    except:
        bot.send_message(message.chat.id, "❌ Неверный формат. Пример: 123456789 5")

def process_block(message):
    try:
        uid = int(message.text.strip())
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET is_blocked=1 WHERE user_id=?", (uid,))
        conn.commit()
        bot.send_message(message.chat.id, f"✅ Пользователь {uid} заблокирован.")
        log_admin(message.from_user.id, "block", str(uid))
    except:
        bot.send_message(message.chat.id, "❌ Неверный ID")

# ========== ЗАПУСК ==========
if __name__ == "__main__":
    print("╔════════════════════════════════════╗")
    print("║     🚀 БОТ ЗАПУЩЕН                 ║")
    print("╠════════════════════════════════════╣")
    print("║ ✅ Подписка на канал               ║")
    print("║ ✅ Лимиты + бонусы                 ║")
    print("║ ✅ Реферальная система             ║")
    print("║ ✅ Админ-панель                    ║")
    print("║ ✅ Распознавание фото               ║")
    print("║ 🔒 VPN: @ProrabVPN_bot              ║")
    print("╚════════════════════════════════════╝")
    while True:
        try:
            bot.polling(none_stop=True)
        except Exception as e:
            logging.error(f"Polling error: {e}")
            time.sleep(3)
