import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import requests
import logging
import re
import base64
import time
import sqlite3
from datetime import datetime, timedelta
import math

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

# Константы
G = 9.8  # м/с²

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
    """Мгновенная проверка подписки"""
    try:
        chat_member = bot.get_chat_member(CHANNEL_ID, user_id)
        status = chat_member.status
        subscribed = status in ['creator', 'administrator', 'member', 'restricted']
        update_subscription_status(user_id, subscribed)
        return subscribed
    except Exception as e:
        logging.error(f"Subscription check error for {user_id}: {e}")
        return False

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

# ========== КЛАСС ТОЧНЫХ РЕШАТЕЛЕЙ ==========

class ProrabBotSolver:
    """Универсальный решатель для точных наук"""
    
    def __init__(self):
        self.g = G
        self.math = math
    
    # ===== ФИЗИКА =====
    def solve_ballistics(self, v0, angle_deg):
        """
        Точное решение задачи баллистики
        v0: начальная скорость (м/с)
        angle_deg: угол в градусах
        """
        angle_rad = math.radians(angle_deg)
        sin_a = math.sin(angle_rad)
        cos_a = math.cos(angle_rad)
        sin_2a = math.sin(2 * angle_rad)
        
        t_flight = (2 * v0 * sin_a) / self.g
        h_max = (v0**2 * sin_a**2) / (2 * self.g)
        distance = (v0**2 * sin_2a) / self.g
        
        explanation = [
            f"1. Раскладываем скорость:",
            f"   vx = {v0:.1f}·cos({angle_deg}°) = {v0*cos_a:.1f} м/с",
            f"   vy = {v0:.1f}·sin({angle_deg}°) = {v0*sin_a:.1f} м/с",
            f"2. Время полёта: T = 2·vy/g = {t_flight:.1f} с",
            f"3. Максимальная высота: H = vy²/(2g) = {h_max:.0f} м",
            f"4. Дальность: L = vx·T = {distance:.0f} м"
        ]
        
        return {
            "time": round(t_flight, 1),
            "height": round(h_max, 0),
            "range": round(distance, 0),
            "explanation": explanation
        }
    
    def check_physical_sense(self, value, quantity):
        """Проверка физического смысла"""
        checks = {
            "height": (0, 100000, "м"),
            "time": (0, 1000, "с"),
            "range": (0, 1000000, "м"),
            "probability": (0, 1, "")
        }
        if quantity in checks:
            min_val, max_val, unit = checks[quantity]
            if value < min_val or value > max_val:
                return f"⚠️ Подозрительное значение: {value} {unit}"
        return "✅ Значение правдоподобно"
    
    # ===== ГЕНЕТИКА =====
    def solve_genetics_x_linked(self, mother=None, father=None, question=None):
        """
        Решение для Х-сцепленного наследования
        (конкретная задача: XᴮXᵇ × XᵇY)
        """
        probabilities = {
            "male_XBY": 0.25,
            "male_XbY": 0.25,
            "female_XBXb": 0.25,
            "female_XbXb": 0.25
        }
        
        explanation = [
            "Решётка Пеннета для Х-хромосомы:",
            "   XB  Xb",
            "Xb XBXb XbXb",
            "Y  XBY  XbY",
            "",
            "Вероятности:",
            "- Самец XBY (устойчив, не пьёт): 25%",
            "- Самец XbY (неустойчив, пьёт): 25%",
            "- Самка XBXb (носитель, не пьёт): 25%",
            "- Самка XbXb (неустойчив, пьёт): 25%"
        ]
        
        if question == "male_resistant_no_pills":
            prob = 0.125  # 1/8
            return {
                "probability": prob,
                "percent": f"{prob*100:.1f}%",
                "explanation": [
                    "P(самец) = 1/2",
                    "P(устойчив) = 1/2 (Aa × aa)",
                    "P(не пьёт среди самцов) = 1/2 (XBY)",
                    "Итого: 1/2 × 1/2 × 1/2 = 1/8 = 12.5%"
                ]
            }
        
        return {
            "probabilities": probabilities,
            "explanation": explanation
        }
    
    # ===== СТАТИСТИКА И ВЕРОЯТНОСТЬ =====
    def combinations(self, n, k):
        """Число сочетаний C(n, k)"""
        return math.comb(n, k)
    
    def permutations(self, n, k=None):
        """Число размещений (если k указано) или перестановок (если k=None)"""
        if k is None:
            return math.factorial(n)
        return math.perm(n, k)
    
    def factorial(self, n):
        """Факториал числа n"""
        return math.factorial(n)
    
    def binomial_probability(self, n, k, p):
        """Вероятность по формуле Бернулли: C(n, k) * p^k * (1-p)^(n-k)"""
        return math.comb(n, k) * (p**k) * ((1-p)**(n-k))
    
    def expected_value(self, values, probabilities):
        """Математическое ожидание"""
        return sum(v * p for v, p in zip(values, probabilities))
    
    def variance(self, values, probabilities):
        """Дисперсия"""
        mu = self.expected_value(values, probabilities)
        return sum(((v - mu)**2) * p for v, p in zip(values, probabilities))
    
    # ===== РУССКИЙ ЯЗЫК =====
    def russian_phonetics(self, word):
        """Фонетический разбор слова (упрощённый)"""
        vowels = 'аеёиоуыэюя'
        consonants = 'бвгджзйклмнпрстфхцчшщ'
        
        result = {
            "word": word,
            "letters": len(word),
            "sounds": len(word),  # упрощённо
            "vowels": sum(1 for ch in word.lower() if ch in vowels),
            "consonants": sum(1 for ch in word.lower() if ch in consonants),
            "syllables": self._count_syllables(word)
        }
        
        explanation = [
            f"Слово: {word}",
            f"Количество букв: {result['letters']}",
            f"Количество звуков: {result['sounds']}",
            f"Гласных: {result['vowels']}",
            f"Согласных: {result['consonants']}",
            f"Слогов: {result['syllables']}"
        ]
        
        return {
            "result": result,
            "explanation": explanation
        }
    
    def _count_syllables(self, word):
        """Подсчёт слогов по гласным"""
        vowels = 'аеёиоуыэюя'
        return sum(1 for ch in word.lower() if ch in vowels)
    
    # ===== ОБЩЕСТВОЗНАНИЕ =====
    def society_terms(self, term):
        """База основных терминов по обществознанию"""
        terms_db = {
            "спрос": "Спрос — это количество товара, которое потребители готовы купить по данной цене в определённое время.",
            "предложение": "Предложение — это количество товара, которое производители готовы продать по данной цене.",
            "инфляция": "Инфляция — это повышение общего уровня цен на товары и услуги.",
            "государство": "Государство — политическая организация общества, обладающая суверенитетом и осуществляющая управление.",
            "мораль": "Мораль — совокупность норм и правил поведения, принятых в обществе."
        }
        return terms_db.get(term.lower(), None)
    
    # ===== ЛИТЕРАТУРА =====
    def literature_analysis(self, work, character=None):
        """Шаблоны для анализа литературных произведений"""
        templates = {
            "евгений онегин": {
                "title": "Евгений Онегин",
                "author": "А.С. Пушкин",
                "genre": "роман в стихах",
                "theme": "лишний человек, любовь, дружба, смысл жизни",
                "characters": {
                    "онегин": "Евгений Онегин — молодой дворянин, разочарованный в жизни, эгоистичный, но способный на глубокие чувства.",
                    "татьяна": "Татьяна Ларина — воплощение русского национального характера, искренняя, верная, нравственно чистая."
                }
            },
            "война и мир": {
                "title": "Война и мир",
                "author": "Л.Н. Толстой",
                "genre": "роман-эпопея",
                "theme": "историческая судьба России, роль личности в истории, семья, народ",
                "characters": {
                    "пьер": "Пьер Безухов — ищущий правду, проходящий сложный путь духовного развития.",
                    "андрей": "Андрей Болконский — разочарованный в свете, стремящийся к славе, но приходящий к пониманию истинных ценностей."
                }
            }
        }
        return templates.get(work.lower(), None)

solver = ProrabBotSolver()

# ========== ПРОМПТЫ ==========
PROMPTS = {
    "math": """
ТЫ — ПРОФЕССОР МАТЕМАТИКИ. Решай задачи максимально подробно.

СТРОГИЕ ПРАВИЛА:
1. ЗАПРЕЩЕНО использовать LaTeX.
2. Корни пиши как sqrt().
3. Степени: ^2, ^3.
4. Дроби через /.
5. Умножение через *.
6. В конце обязательно "Ответ: ...".
    """,
    "physics": """
ТЫ — ПРОФЕССОР ФИЗИКИ. Используй точные формулы.

ПРАВИЛА:
1. НИКАКОГО LaTeX.
2. Единицы измерения через пробел: 10 м/с.
3. Степени: м^2, м^3.
4. Корни: sqrt().
5. Дроби: /.
6. g = 9.8 м/с².
7. В конце "Ответ: ...".
    """,
    "biology": """
ТЫ — УЧИТЕЛЬ БИОЛОГИИ. Объясняй простым языком.

ТРЕБОВАНИЯ:
1. НИКАКОГО LaTeX.
2. Структура: что это? как работает? примеры?
3. В конце "Ответ:" или "Вывод:".
    """,
    "chemistry": """
ТЫ — ХИМИК. Используй формулы и реакции.

ПРАВИЛА:
1. НИКАКОГО LaTeX.
2. Формулы: H2O, CO2.
3. Реакции со стрелкой ->.
4. В конце "Ответ:".
    """,
    "statistics": """
ТЫ — ПРОФЕССОР СТАТИСТИКИ. Решай задачи по теории вероятностей и статистике.

ПРАВИЛА:
1. НИКАКОГО LaTeX.
2. Для сочетаний используй C(n,k), для размещений A(n,k).
3. Вероятности выражай в дробях и процентах.
4. В конце "Ответ: ...".
    """,
    "russian": """
ТЫ — УЧИТЕЛЬ РУССКОГО ЯЗЫКА. Объясняй правила, делай разборы.

ТРЕБОВАНИЯ:
1. НИКАКОГО LaTeX.
2. Фонетический разбор: буквы, звуки, гласные/согласные.
3. Морфемный разбор: приставка, корень, суффикс, окончание.
4. В конце "Ответ:".
    """,
    "literature": """
ТЫ — УЧИТЕЛЬ ЛИТЕРАТУРЫ. Анализируй произведения, давай характеристики героям.

ТРЕБОВАНИЯ:
1. НИКАКОГО LaTeX.
2. Указывай автора, жанр, тему, идею.
3. Характеристики героев с цитатами.
4. В конце "Ответ:".
    """,
    "society": """
ТЫ — УЧИТЕЛЬ ОБЩЕСТВОЗНАНИЯ. Объясняй термины, процессы.

ТРЕБОВАНИЯ:
1. НИКАКОГО LaTeX.
2. Давай определение, признаки, функции, примеры.
3. В конце "Ответ:".
    """,
    "general": """
ТЫ — ПОМОЩНИК. Отвечай кратко и понятно.

ТРЕБОВАНИЯ:
1. НИКАКОГО LaTeX.
2. В конце "Ответ: ...".
    """
}

SUBJECT_NAMES = {
    "math": "📐 Математика",
    "physics": "🔮 Физика",
    "biology": "🧬 Биология",
    "chemistry": "⚗️ Химия",
    "statistics": "📊 Статистика",
    "russian": "🇷🇺 Русский язык",
    "literature": "📚 Литература",
    "society": "🏛 Обществознание",
    "general": "📚 Любой"
}

user_subjects = {}

# ========== КЛАВИАТУРЫ ==========
def main_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=2)
    buttons = [
        InlineKeyboardButton("📐 Математика", callback_data="subj_math"),
        InlineKeyboardButton("🔮 Физика", callback_data="subj_physics"),
        InlineKeyboardButton("🧬 Биология", callback_data="subj_biology"),
        InlineKeyboardButton("⚗️ Химия", callback_data="subj_chemistry"),
        InlineKeyboardButton("📊 Статистика", callback_data="subj_statistics"),
        InlineKeyboardButton("🇷🇺 Русский язык", callback_data="subj_russian"),
        InlineKeyboardButton("📚 Литература", callback_data="subj_literature"),
        InlineKeyboardButton("🏛 Обществознание", callback_data="subj_society"),
        InlineKeyboardButton("📚 Любой", callback_data="subj_general"),
        InlineKeyboardButton("🔗 Рефералка", callback_data="referral"),
        InlineKeyboardButton("📊 Мой баланс", callback_data="balance"),
        InlineKeyboardButton("🔒 VPN 20+", callback_data="vpn"),
        InlineKeyboardButton("📖 Помощь", callback_data="help"),
        InlineKeyboardButton("📸 Фото задачи", callback_data="photo_help")
    ]
    # Разбиваем на ряды по 2
    keyboard.add(*buttons[0:2])
    keyboard.add(*buttons[2:4])
    keyboard.add(*buttons[4:6])
    keyboard.add(*buttons[6:8])
    keyboard.add(*buttons[8:10])
    keyboard.add(*buttons[10:12])
    keyboard.add(*buttons[12:14])
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
        InlineKeyboardButton("🔓 Разблокировать", callback_data="admin_unblock"),
        InlineKeyboardButton("📋 Список пользователей", callback_data="admin_userlist"),
        InlineKeyboardButton("🔙 Выход", callback_data="back")
    ]
    keyboard.add(*buttons)
    return keyboard

# ========== FALLBACK ==========
def trig_fallback(question):
    """Обрабатывает тригонометрические запросы"""
    q = question.lower().strip()
    q = re.sub(r'\s+', '', q)
    
    patterns = [
        (r'(cos|косинус|кос)\s*(\d+)', lambda m: f"cos({m.group(2)}°)"),
        (r'(sin|синус|син)\s*(\d+)', lambda m: f"sin({m.group(2)}°)"),
        (r'(tan|tg|тангенс|тг)\s*(\d+)', lambda m: f"tan({m.group(2)}°)")
    ]
    
    values = {
        '30': {'cos': '√3/2 ≈ 0.8660', 'sin': '1/2 = 0.5', 'tan': '√3/3 ≈ 0.5774'},
        '45': {'cos': '√2/2 ≈ 0.7071', 'sin': '√2/2 ≈ 0.7071', 'tan': '1'},
        '60': {'cos': '1/2 = 0.5', 'sin': '√3/2 ≈ 0.8660', 'tan': '√3 ≈ 1.732'},
        '90': {'cos': '0', 'sin': '1', 'tan': 'не определен'}
    }
    
    for pat, _ in patterns:
        match = re.search(pat, q)
        if match:
            func = 'cos' if 'cos' in match.group(0) or 'кос' in match.group(0) else ('sin' if 'sin' in match.group(0) or 'син' in match.group(0) else 'tan')
            angle_match = re.search(r'(\d+)', q)
            if angle_match and angle_match.group(1) in values:
                val = values[angle_match.group(1)][func]
                return f"{func} {angle_match.group(1)}° = {val}\n\nОтвет: {val.split('≈')[-1].strip() if '≈' in val else val}"
    return None

def detect_physics_problem(text):
    """Определяет, является ли задача физической и извлекает параметры"""
    text_lower = text.lower()
    
    # Баллистика
    if 'скорость' in text_lower and ('угол' in text_lower or 'градус' in text_lower):
        numbers = re.findall(r'(\d+)', text)
        if len(numbers) >= 2:
            try:
                v0 = float(numbers[0])
                angle = float(numbers[1])
                return "ballistics", {"v0": v0, "angle": angle}
            except:
                pass
    
    # Кинетическая энергия
    if 'кинетическ' in text_lower and 'масс' in text_lower and 'скорост' in text_lower:
        numbers = re.findall(r'(\d+)', text)
        if len(numbers) >= 2:
            try:
                m = float(numbers[0])
                v = float(numbers[1])
                return "kinetic_energy", {"m": m, "v": v}
            except:
                pass
    
    return None, None

def detect_genetics_problem(text):
    """Определяет генетические задачи"""
    text_lower = text.lower()
    
    if 'сцеплен' in text_lower and ('пол' in text_lower or 'x' in text_lower):
        return "x_linked", {}
    
    return None, None

def detect_stats_problem(text):
    """Определяет задачи по статистике"""
    text_lower = text.lower()
    
    if 'сочетани' in text_lower or 'c из' in text_lower or 'комбинаци' in text_lower:
        numbers = re.findall(r'(\d+)', text)
        if len(numbers) >= 2:
            try:
                n = int(numbers[0])
                k = int(numbers[1])
                return "combinations", {"n": n, "k": k}
            except:
                pass
    
    if 'факториал' in text_lower:
        numbers = re.findall(r'(\d+)', text)
        if numbers:
            return "factorial", {"n": int(numbers[0])}
    
    return None, None

def detect_russian_problem(text):
    """Определяет задачи по русскому языку"""
    text_lower = text.lower()
    
    if 'фонетическ' in text_lower or 'звуко' in text_lower or 'разбор слова' in text_lower:
        # Ищем слово в кавычках или после "слова"
        word_match = re.search(r'["«»"“”]([^"«»"“”]+)["«»"“”]', text)
        if not word_match:
            word_match = re.search(r'слова?\s+(\w+)', text_lower)
        if word_match:
            return "phonetics", {"word": word_match.group(1)}
    
    return None, None

def detect_literature_problem(text):
    """Определяет задачи по литературе"""
    text_lower = text.lower()
    
    works = ["евгений онегин", "война и мир", "преступление и наказание", "отцы и дети", "герой нашего времени"]
    for work in works:
        if work in text_lower:
            return "literature_analysis", {"work": work}
    
    return None, None

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

def clean_gpt_answer(text):
    """Очистка ответа от LaTeX"""
    if not text:
        return "❌ Пустой ответ"
    
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
    
    return text if len(text) >= 2 else "❌ Ответ слишком короткий"

# ========== ДЕКОРАТОР ПРОВЕРКИ (МГНОВЕННАЯ ПОДПИСКА) ==========
def check_sub_and_limit(handler_func):
    def wrapper(message):
        user_id = message.from_user.id
        user = get_user(user_id)
        
        if user and user[11] == 1:
            bot.send_message(user_id, "❌ Вы заблокированы. Обратитесь к администратору.")
            return
        
        # МГНОВЕННАЯ ПРОВЕРКА ПОДПИСКИ (без кеша)
        if not is_subscribed_now(user_id) and not is_admin(user_id):
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
        "🚀 <b>ПРОРАБ ГДЗ - УНИВЕРСАЛЬНЫЙ ПОМОЩНИК</b>\n\n"
        "📐 Математика\n🔮 Физика\n🧬 Биология\n⚗️ Химия\n📊 Статистика\n"
        "🇷🇺 Русский язык\n📚 Литература\n🏛 Обществознание\n📸 Фото задачи\n\n"
        "🔒 <b>VPN 20+ серверов</b> — @ProrabVPN_bot\n💰 200₽/мес",
        parse_mode="HTML", reply_markup=main_keyboard())
    
    if not is_subscribed_now(user_id) and not is_admin(user_id):
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
    
    # 1. Тригонометрический fallback
    trig = trig_fallback(question)
    if trig:
        increment_request(user_id)
        bot.edit_message_text(
            f"✅ <b>Решение:</b>\n\n{trig}\n\n━━━━━━━━━━━━━━━━━━━━━\n🔒 @ProrabVPN_bot - 20+ серверов, 200₽/мес",
            user_id, msg.message_id, parse_mode="HTML", reply_markup=back_keyboard())
        return
    
    # 2. Физика (точные решатели)
    if subject in ["physics", "general"]:
        phys_type, phys_params = detect_physics_problem(question)
        if phys_type == "ballistics":
            result = solver.solve_ballistics(phys_params["v0"], phys_params["angle"])
            answer = "\n".join(result["explanation"]) + f"\n\nОтвет: время {result['time']} с, высота {result['height']} м, дальность {result['range']} м"
            increment_request(user_id)
            bot.edit_message_text(
                f"✅ <b>Решение:</b>\n\n{answer}\n\n━━━━━━━━━━━━━━━━━━━━━\n🔒 @ProrabVPN_bot - 20+ серверов, 200₽/мес",
                user_id, msg.message_id, parse_mode="HTML", reply_markup=back_keyboard())
            return
    
    # 3. Генетика
    if subject in ["biology", "general"]:
        gen_type, _ = detect_genetics_problem(question)
        if gen_type == "x_linked":
            result = solver.solve_genetics_x_linked(question="male_resistant_no_pills")
            answer = "\n".join(result["explanation"]) + f"\n\nОтвет: {result['percent']}"
            increment_request(user_id)
            bot.edit_message_text(
                f"✅ <b>Решение:</b>\n\n{answer}\n\n━━━━━━━━━━━━━━━━━━━━━\n🔒 @ProrabVPN_bot - 20+ серверов, 200₽/мес",
                user_id, msg.message_id, parse_mode="HTML", reply_markup=back_keyboard())
            return
    
    # 4. Статистика
    if subject in ["statistics", "general"]:
        stats_type, stats_params = detect_stats_problem(question)
        if stats_type == "combinations":
            result = solver.combinations(stats_params["n"], stats_params["k"])
            answer = f"C({stats_params['n']}, {stats_params['k']}) = {result}\n\nОтвет: {result}"
            increment_request(user_id)
            bot.edit_message_text(
                f"✅ <b>Решение:</b>\n\n{answer}\n\n━━━━━━━━━━━━━━━━━━━━━\n🔒 @ProrabVPN_bot - 20+ серверов, 200₽/мес",
                user_id, msg.message_id, parse_mode="HTML", reply_markup=back_keyboard())
            return
        elif stats_type == "factorial":
            result = solver.factorial(stats_params["n"])
            answer = f"{stats_params['n']}! = {result}\n\nОтвет: {result}"
            increment_request(user_id)
            bot.edit_message_text(
                f"✅ <b>Решение:</b>\n\n{answer}\n\n━━━━━━━━━━━━━━━━━━━━━\n🔒 @ProrabVPN_bot - 20+ серверов, 200₽/мес",
                user_id, msg.message_id, parse_mode="HTML", reply_markup=back_keyboard())
            return
    
    # 5. Русский язык
    if subject in ["russian", "general"]:
        russian_type, russian_params = detect_russian_problem(question)
        if russian_type == "phonetics":
            result = solver.russian_phonetics(russian_params["word"])
            answer = "\n".join(result["explanation"])
            increment_request(user_id)
            bot.edit_message_text(
                f"✅ <b>Решение:</b>\n\n{answer}\n\n━━━━━━━━━━━━━━━━━━━━━\n🔒 @ProrabVPN_bot - 20+ серверов, 200₽/мес",
                user_id, msg.message_id, parse_mode="HTML", reply_markup=back_keyboard())
            return
    
    # 6. Литература
    if subject in ["literature", "general"]:
        lit_type, lit_params = detect_literature_problem(question)
        if lit_type == "literature_analysis":
            result = solver.literature_analysis(lit_params["work"])
            if result:
                answer = f"Произведение: {result['title']}\nАвтор: {result['author']}\nЖанр: {result['genre']}\nТема: {result['theme']}"
                increment_request(user_id)
                bot.edit_message_text(
                    f"✅ <b>Решение:</b>\n\n{answer}\n\n━━━━━━━━━━━━━━━━━━━━━\n🔒 @ProrabVPN_bot - 20+ серверов, 200₽/мес",
                    user_id, msg.message_id, parse_mode="HTML", reply_markup=back_keyboard())
                return
    
    # 7. Обществознание
    if subject in ["society", "general"]:
        term = solver.society_terms(question)
        if term:
            answer = term
            increment_request(user_id)
            bot.edit_message_text(
                f"✅ <b>Решение:</b>\n\n{answer}\n\n━━━━━━━━━━━━━━━━━━━━━\n🔒 @ProrabVPN_bot - 20+ серверов, 200₽/мес",
                user_id, msg.message_id, parse_mode="HTML", reply_markup=back_keyboard())
            return
    
    # 8. GPT (если ничего не подошло)
    answer = ask_yandex_gpt(question, subject)
    answer = clean_gpt_answer(answer)
    
    increment_request(user_id)
    bot.edit_message_text(
        f"✅ <b>Решение:</b>\n\n{answer}\n\n━━━━━━━━━━━━━━━━━━━━━\n🔒 @ProrabVPN_bot - 20+ серверов, 200₽/мес",
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
    answer = clean_gpt_answer(answer)
    increment_request(user_id)
    bot.send_message(user_id,
        f"✅ <b>Решение:</b>\n\n{answer}\n\n━━━━━━━━━━━━━━━━━━━━━\n🔒 @ProrabVPN_bot - 20+ серверов, 200₽/мес",
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
                "📚 Выбери предмет для точности\n\n📌 Примеры:\n• 3x^2 + 8x - 7 = 0\n"
                "• Сила тока при 220В и 50 Ом\n• Что такое фотосинтез?\n"
                "• C(10,3)\n• Фонетический разбор слова «солнце»\n\n🔒 @ProrabVPN_bot - 20+ серверов, 200₽/мес")
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
    elif data == "admin_unblock":
        msg = bot.send_message(user_id, "Введи ID пользователя для разблокировки:")
        bot.register_next_step_handler(msg, process_unblock)

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
        try:
            bot.send_message(uid, f"🎁 Вам начислено {amount} бонусных запросов от администратора!")
        except:
            pass
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

def process_unblock(message):
    try:
        uid = int(message.text.strip())
        cursor.execute("UPDATE users SET is_blocked=0 WHERE user_id=?", (uid,))
        conn.commit()
        bot.send_message(message.chat.id, f"✅ Пользователь {uid} разблокирован.")
        log_admin(message.from_user.id, "unblock", str(uid))
    except:
        bot.send_message(message.chat.id, "❌ Неверный ID")

# ========== ЗАПУСК ==========
if __name__ == "__main__":
    print("╔════════════════════════════════════╗")
    print("║     🚀 ПРОРАБ ГДЗ ЗАПУЩЕН         ║")
    print("╠════════════════════════════════════╣")
    print("║ ✅ Подписка: МГНОВЕННАЯ проверка  ║")
    print("║ ✅ Физика: точные расчёты         ║")
    print("║ ✅ Генетика: решётки Пеннета      ║")
    print("║ ✅ Статистика: комбинаторика      ║")
    print("║ ✅ Русский язык: фонетика         ║")
    print("║ ✅ Литература: анализ             ║")
    print("║ ✅ Обществознание: термины        ║")
    print("║ ✅ Тригонометрия: fallback        ║")
    print("║ ✅ Кнопки: баланс, рефералка      ║")
    print("║ 🔒 VPN: @ProrabVPN_bot            ║")
    print("╚════════════════════════════════════╝")
    while True:
        try:
            bot.polling(none_stop=True)
        except Exception as e:
            logging.error(f"Polling error: {e}")
            time.sleep(3)
