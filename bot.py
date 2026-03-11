import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import requests
import logging
import re
import base64
import time
import sqlite3
from datetime import datetime, timedelta

# ========== НАСТРОЙКИ (ЗАМЕНИТЬ НА СВОИ) ==========
TELEGRAM_TOKEN = "8451168327:AAGQffadqqBg3pZNQnjctVxH-dUgXsovTr4"
FOLDER_ID = "b1g0s9bjamjqrvas5pqr"
API_KEY = "AQVNxnq1d97ei8asrSCgEdGN92cXym_faQZ8I3dp"  # AQVN...
CHANNEL_ID = "@kamensk_avtodor_prorab"  # например @my_channel
ADMIN_IDS = [5775839902]  # твои Telegram ID
# ==================================================

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
bot = telebot.TeleBot(TELEGRAM_TOKEN)

# URL API Яндекса
YANDEX_GPT_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
YANDEX_VISION_URL = "https://vision.api.cloud.yandex.net/vision/v1/batchAnalyze"

# ========== БАЗА ДАННЫХ ==========
conn = sqlite3.connect('bot.db', check_same_thread=False)
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

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========

def is_admin(user_id):
    return user_id in ADMIN_IDS

def get_user(user_id):
    cursor.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    return cursor.fetchone()

def create_user(user_id, username, first_name, referrer_id=None):
    now = datetime.now().isoformat()
    referral_code = base64.urlsafe_b64encode(str(user_id).encode()).decode().replace('=', '')[:10]
    cursor.execute('''
        INSERT OR IGNORE INTO users 
        (user_id, username, first_name, joined_date, last_request_date, referral_code, referrer_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (user_id, username, first_name, now, now[:10], referral_code, referrer_id))
    conn.commit()
    return referral_code

def update_subscription_status(user_id, subscribed):
    cursor.execute("UPDATE users SET subscribed=?, last_check=? WHERE user_id=?", 
                   (1 if subscribed else 0, datetime.now().isoformat(), user_id))
    conn.commit()

def is_subscribed_now(user_id):
    """Мгновенная проверка подписки (без кеша)"""
    try:
        chat_member = bot.get_chat_member(CHANNEL_ID, user_id)
        status = chat_member.status
        subscribed = status in ['creator', 'administrator', 'member', 'restricted']
        update_subscription_status(user_id, subscribed)
        return subscribed
    except Exception as e:
        logging.error(f"Subscription check error for {user_id}: {e}")
        return False

def is_subscribed_cached(user_id):
    """Проверка с кешем 5 минут"""
    user = get_user(user_id)
    if not user:
        return False
    if user[10] and datetime.now() - datetime.fromisoformat(user[10]) < timedelta(minutes=5):
        return user[9] == 1
    return is_subscribed_now(user_id)

def check_and_reset_daily(user_id):
    user = get_user(user_id)
    if not user:
        return
    last = user[5]
    today = datetime.now().strftime("%Y-%m-%d")
    if last < today:
        cursor.execute("UPDATE users SET requests_today=0 WHERE user_id=?", (user_id,))
        conn.commit()

def can_make_request(user_id):
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
    cursor.execute("UPDATE users SET bonus_balance = bonus_balance + ? WHERE user_id=?", (amount, user_id))
    conn.commit()

def log_admin(admin_id, action, target):
    cursor.execute("INSERT INTO admin_logs (admin_id, action, target, date) VALUES (?, ?, ?, ?)",
                   (admin_id, action, target, datetime.now().isoformat()))
    conn.commit()

# ========== УСИЛЕННЫЕ ПРОМПТЫ ДЛЯ ВСЕХ ПРЕДМЕТОВ ==========
PROMPTS = {
    "math": """Ты — профессор математики. Твоя задача — давать точные, полные и понятные ответы на любые математические вопросы.
    Требования к ответу:
    - НИКОГДА не используй LaTeX-разметку (никаких $, $$, \\, {}).
    - Для корней используй символ √ (например, √2, √(x+1)).
    - Для степеней используй ², ³, ⁿ (например, x², a³).
    - Для дробей используй обычный слеш / (например, 3/4, (a+b)/c).
    - Для умножения используй · или ничего (например, 2·3, 4a).
    - Дискриминант: D = b² - 4ac. Корни: x₁ = (-b + √D)/(2a), x₂ = (-b - √D)/(2a).
    - Даже на простые вопросы (например, "2+2") отвечай развёрнуто: "2 + 2 = 4" и краткое пояснение.
    - В конце обязательно пиши "Ответ: ...".
    - Не оставляй ответ пустым или коротким — всегда давай решение.
    """,

    "physics": """Ты — профессор физики. Твои ответы должны быть точными, с формулами и единицами измерения.
    Требования:
    - НИКАКОГО LaTeX.
    - Степени: м², м³, с⁻¹, кг·м/с².
    - Корни: √.
    - Дроби: /.
    - Единицы измерения пиши через пробел: 10 м/с, 5 кг.
    - Формулы выделяй с новой строки.
    - В конце обязательно "Ответ: ...".
    """,

    "biology": """Ты — опытный учитель биологии. Объясняй биологические процессы простым, понятным языком, но полно.
    Требования:
    - НИКАКОГО LaTeX.
    - Используй аналогии из жизни.
    - Структурируй ответ: что это? как работает? зачем нужно?
    - В конце добавляй "Ответ:" или "Вывод:".
    """,

    "chemistry": """Ты — химик-эксперт. Используй химические формулы и уравнения реакций.
    Требования:
    - НИКАКОГО LaTeX.
    - Формулы веществ: H₂O, CO₂, CH₄ (используй нижние индексы).
    - Реакции со стрелкой → (например, 2H₂ + O₂ → 2H₂O).
    - Коэффициенты ставь перед формулами.
    - Дроби: /, корни: √.
    - В конце "Ответ:".
    """,

    "general": """Ты — интеллектуальный помощник, который отвечает на любые вопросы.
    Требования:
    - НИКАКОГО LaTeX.
    - Если вопрос простой арифметический (например, "2-2"), просто напиши результат с кратким пояснением.
    - Если вопрос требует объяснения, дай развёрнутый, но понятный ответ.
    - В конце обязательно "Ответ: ...".
    """
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
        InlineKeyboardButton("🔗 Рефералка", callback_data="referral"),
        InlineKeyboardButton("📊 Мой баланс", callback_data="balance"),
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

# ========== УЛУЧШЕННАЯ ОЧИСТКА И ФОРМАТИРОВАНИЕ ==========
def safe_eval_math(expr):
    """Безопасно вычисляет простое арифметическое выражение (числа и операторы + - * /)."""
    expr = expr.strip()
    if not re.match(r'^[0-9+\-*/().\s]+$', expr):
        return None
    try:
        result = eval(expr, {"__builtins__": None}, {})
        return str(result)
    except:
        return None

def clean_answer(text, original_question=""):
    """Очищает ответ от LaTeX, приводит к читаемому виду. При пустом ответе пробует fallback."""
    if not text or len(text.strip()) < 2:
        if original_question:
            calc = safe_eval_math(original_question)
            if calc:
                return f"Результат: {calc}\n\nОтвет: {calc}"
        return "❌ Нейросеть вернула пустой ответ. Пожалуйста, переформулируйте вопрос."

    text = re.sub(r'\\[\[\]\(\)]', '', text)
    text = re.sub(r'\$\$.*?\$\$', '', text, flags=re.DOTALL)
    text = re.sub(r'\$.*?\$', '', text, flags=re.DOTALL)
    text = text.replace('\\', '').replace('{', '').replace('}', '')

    text = re.sub(r'sqrt\((\d+)\)', r'√\1', text)
    text = re.sub(r'sqrt\(([^)]+)\)', r'√(\1)', text)

    text = re.sub(r'\^2', '²', text)
    text = re.sub(r'\^3', '³', text)

    text = text.replace('*', '·')

    text = re.sub(r'\s+', ' ', text).strip()

    if len(text) < 2:
        if original_question:
            calc = safe_eval_math(original_question)
            if calc:
                return f"Результат: {calc}\n\nОтвет: {calc}"
        return "❌ Не удалось распознать ответ. Попробуйте переформулировать."

    return text

# ========== ЗАПРОСЫ К YANDEX ==========
def ask_yandex_gpt(question, subject):
    system_prompt = PROMPTS.get(subject, PROMPTS["general"])
    system_prompt += "\nОТВЕЧАЙ ВСЕГДА. НЕ ИСПОЛЬЗУЙ LaTeX. НЕ ОСТАВЛЯЙ ПУСТЫХ ОТВЕТОВ."

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

# ========== ДЕКОРАТОР ПРОВЕРКИ ==========
def check_sub_and_limit(handler_func):
    def wrapper(message):
        user_id = message.from_user.id
        if not is_subscribed_cached(user_id) and not is_admin(user_id):
            bot.send_message(user_id, "🚫 Чтобы пользоваться ботом, подпишись на канал:",
                             reply_markup=subscribe_keyboard())
            return
        allowed, remaining, bonus = can_make_request(user_id)
        if not allowed and not is_admin(user_id):
            bot.send_message(user_id,
                f"❌ У тебя закончились запросы.\n"
                f"Доступно сегодня: 10 бесплатных + {bonus} бонусных.\n"
                "Возвращайся завтра (бесплатные обновятся) или пригласи друга (кнопка «🔗 Рефералка»).")
            return
        return handler_func(message)
    return wrapper

# ========== ОБРАБОТЧИКИ КОМАНД ==========

@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.from_user.id
    username = message.from_user.username or ""
    first_name = message.from_user.first_name or ""
    args = message.text.split()
    referrer_id = None
    if len(args) > 1 and args[1].startswith('ref_'):
        ref_code = args[1][4:]
        cursor.execute("SELECT user_id FROM users WHERE referral_code=?", (ref_code,))
        row = cursor.fetchone()
        if row:
            referrer_id = row[0]
    if not get_user(user_id):
        create_user(user_id, username, first_name, referrer_id)

    bot.send_message(user_id,
        "🚀 <b>РЕШАТОР ЗАДАЧ</b>\n\n📐 Математика\n🔮 Физика\n🧬 Биология\n⚗️ Химия\n📸 Фото задачи\n\n🔒 <b>VPN 20+ серверов</b> — @ProrabVPN_bot\n💰 200₽/мес",
        parse_mode="HTML", reply_markup=main_keyboard())
    if not is_subscribed_cached(user_id) and not is_admin(user_id):
        bot.send_message(user_id, "⚠️ Для использования бота необходимо подписаться на канал.", reply_markup=subscribe_keyboard())

@bot.message_handler(commands=['referral'])
def referral_command(message):
    send_referral_info(message)

@bot.message_handler(commands=['balance'])
def balance_command(message):
    send_balance_info(message)

@bot.message_handler(commands=['admin'])
def admin_panel(message):
    if not is_admin(message.from_user.id):
        return
    bot.send_message(message.chat.id, "🛠 Админ-панель", reply_markup=admin_keyboard())

# ========== ФУНКЦИИ ДЛЯ КНОПОК ==========
def send_referral_info(call_or_message):
    user_id = call_or_message.from_user.id
    user = get_user(user_id)
    if not user:
        bot.send_message(user_id, "Сначала запусти бота через /start")
        return
    ref_code = user[7]
    link = f"https://t.me/{bot.get_me().username}?start=ref_{ref_code}"
    cursor.execute("SELECT COUNT(*) FROM referrals WHERE inviter_id=? AND rewarded=1", (user_id,))
    invited = cursor.fetchone()[0]
    text = (f"🔗 <b>Твоя реферальная ссылка:</b>\n<code>{link}</code>\n\n"
            f"Приглашено друзей: {invited}\n"
            "Бонус начисляется после того, как друг подпишется на канал и нажмёт «Я подписался».\n"
            "За каждого друга ты получаешь +3 бонусных запроса, а друг +1.")
    if isinstance(call_or_message, telebot.types.CallbackQuery):
        bot.edit_message_text(text, user_id, call_or_message.message.message_id, parse_mode="HTML", reply_markup=back_keyboard())
    else:
        bot.send_message(user_id, text, parse_mode="HTML", reply_markup=back_keyboard())

def send_balance_info(call_or_message):
    user_id = call_or_message.from_user.id
    user = get_user(user_id)
    if not user:
        bot.send_message(user_id, "Сначала запусти бота через /start")
        return
    daily_used = user[4]
    bonus = user[6]
    daily_left = max(0, 10 - daily_used)
    text = (f"📊 <b>Твой баланс запросов</b>\n\n"
            f"• Бесплатных сегодня использовано: {daily_used}/10\n"
            f"• Осталось бесплатных сегодня: {daily_left}\n"
            f"• Бонусных (рефералы): {bonus}\n"
            f"• Всего доступно сейчас: {daily_left + bonus}")
    if isinstance(call_or_message, telebot.types.CallbackQuery):
        bot.edit_message_text(text, user_id, call_or_message.message.message_id, parse_mode="HTML", reply_markup=back_keyboard())
    else:
        bot.send_message(user_id, text, parse_mode="HTML", reply_markup=back_keyboard())

# ========== ОБРАБОТЧИК ТЕКСТА ==========
@bot.message_handler(func=lambda m: True, content_types=['text'])
@check_sub_and_limit
def handle_text(message):
    user_id = message.from_user.id
    subject = user_subjects.get(user_id, "general")
    question = message.text.strip()
    msg = bot.send_message(user_id, "🤔 Думаю...")
    answer = ask_yandex_gpt(question, subject)
    cleaned = clean_answer(answer, question)
    increment_request(user_id)
    bot.edit_message_text(
        f"✅ <b>Решение:</b>\n\n{cleaned}\n\n━━━━━━━━━━━━━━━━━━━━━\n🔒 @ProrabVPN_bot - 20+ серверов, 200₽/мес",
        user_id, msg.message_id, parse_mode="HTML", reply_markup=back_keyboard())

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
    cleaned = clean_answer(answer, recognized)
    increment_request(user_id)
    bot.send_message(user_id,
        f"✅ <b>Решение:</b>\n\n{cleaned}\n\n━━━━━━━━━━━━━━━━━━━━━\n🔒 @ProrabVPN_bot - 20+ серверов, 200₽/мес",
        parse_mode="HTML", reply_markup=back_keyboard())

# ========== ОБРАБОТЧИК КНОПОК ==========
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    user_id = call.from_user.id
    data = call.data

    if data == "back":
        bot.edit_message_text("🚀 <b>Главное меню</b>", user_id, call.message.message_id,
                              parse_mode="HTML", reply_markup=main_keyboard())
    elif data == "referral":
        send_referral_info(call)
    elif data == "balance":
        send_balance_info(call)
    elif data == "help":
        text = ("📖 <b>Помощь</b>\n\n📝 Отправь задачу текстом\n📸 Или фото (распознаю)\n"
                "📚 Выбери предмет для точности\n\n📌 Примеры:\n• 3x² + 8x - 7 = 0\n"
                "• Сила тока при 220В и 50 Ом\n• Что такое фотосинтез?\n\n🔒 @ProrabVPN_bot - 20+ серверов, 200₽/мес")
        bot.edit_message_text(text, user_id, call.message.message_id, parse_mode="HTML", reply_markup=back_keyboard())
    elif data == "vpn":
        text = ("🔒 <b>PRORABVPN</b>\n\n✅ 20+ серверов\n✅ Безлимитный трафик\n✅ Высокая скорость\n✅ Все сайты\n\n💰 200₽/мес\n🚀 @ProrabVPN_bot")
        bot.edit_message_text(text, user_id, call.message.message_id, parse_mode="HTML", reply_markup=back_keyboard())
    elif data == "photo_help":
        text = ("📸 <b>Отправь фото задачи</b>\n\n1. Чёткий снимок\n2. Хорошее освещение\n3. Без бликов\n\nЯ распознаю и решу!")
        bot.edit_message_text(text, user_id, call.message.message_id, parse_mode="HTML", reply_markup=back_keyboard())
    elif data.startswith("subj_"):
        subject = data.replace("subj_", "")
        user_subjects[user_id] = subject
        name = SUBJECT_NAMES.get(subject, "Любой")
        bot.edit_message_text(f"✅ <b>Выбран предмет:</b> {name}\n\n📝 Теперь отправь задачу!",
                              user_id, call.message.message_id, parse_mode="HTML", reply_markup=back_keyboard())
    elif data == "check_sub":
        if is_subscribed_now(user_id):
            user = get_user(user_id)
            if user and user[8]:
                cursor.execute("SELECT rewarded FROM referrals WHERE inviter_id=? AND invitee_id=?", (user[8], user_id))
                if not cursor.fetchone():
                    add_bonus(user[8], 3)
                    add_bonus(user_id, 1)
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
            bot.answer_callback_query(call.id, "❌ Подписка не найдена. Убедись, что ты подписался, и попробуй ещё раз.", show_alert=True)

    # Админ-кнопки
    if not is_admin(user_id):
        return

    if data == "admin_stats":
        total = cursor.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        active = cursor.execute("SELECT COUNT(*) FROM users WHERE last_request_date=date('now')").fetchone()[0]
        req_sum = cursor.execute("SELECT SUM(total_requests) FROM users").fetchone()[0] or 0
        bot.edit_message_text(f"📊 <b>Статистика</b>\n\n👥 Всего: {total}\n🔥 Активных сегодня: {active}\n📝 Всего запросов: {req_sum}",
                              user_id, call.message.message_id, parse_mode="HTML", reply_markup=back_keyboard())
    elif data == "admin_userlist":
        cursor.execute("SELECT user_id, first_name, username, total_requests, requests_today, bonus_balance, subscribed, is_blocked FROM users ORDER BY user_id DESC LIMIT 15")
        rows = cursor.fetchall()
        if not rows:
            text = "Список пуст."
        else:
            text = "📋 <b>Последние 15 пользователей:</b>\n\n"
            for r in rows:
                sub = "✅" if r[6] else "❌"
                block = "🚫" if r[7] else ""
                text += f"🆔 {r[0]} | {r[1] or '?'} @{r[2] or '—'} {sub}{block}\n   Всего: {r[3]}, сегодня: {r[4]}, бонус: {r[5]}\n"
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
        users = cursor.execute("SELECT user_id FROM users WHERE subscribed=1 AND is_blocked=0").fetchall()
        success = 0
        failed = 0
        for (uid,) in users:
            try:
                bot.send_message(uid, text, parse_mode="HTML")
                success += 1
                time.sleep(0.05)
            except:
                failed += 1
        bot.send_message(message.chat.id, f"✅ Рассылка завершена.\nУспешно: {success}\nОшибок: {failed}")
        log_admin(message.from_user.id, "broadcast", f"success:{success}, fail:{failed}")
    else:
        bot.send_message(message.chat.id, "Рассылка отменена")

def process_grant(message):
    try:
        parts = message.text.split()
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
    print("║ ✅ Промпты усилены для всех        ║")
    print("║ ✅ Fallback для арифметики         ║")
    print("║ ✅ Кнопки рефералки и баланса      ║")
    print("║ ✅ Распознавание фото               ║")
    print("║ 🔒 VPN: @ProrabVPN_bot              ║")
    print("╚════════════════════════════════════╝")
    while True:
        try:
            bot.polling(none_stop=True)
        except Exception as e:
            logging.error(f"Polling error: {e}")
            time.sleep(3)
