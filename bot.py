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
import math
import random
import json

# ========== НАСТРОЙКИ (ЗАМЕНИТЬ НА СВОИ) ==========
TELEGRAM_TOKEN = "8451168327:AAGQffadqqBg3pZNQnjctVxH-dUgXsovTr4"
FOLDER_ID = "b1g0s9bjamjqrvas5pqr"
API_KEY = "AQVNxnq1d97ei8asrSCgEdGN92cXym_faQZ8I3dp"  # AQVN...
CHANNEL_ID = "@kamensk_avtodor_prorab"  # например @my_channel
ADMIN_IDS = [5775839902]  # твои Telegram ID
RESET_HOUR = 8  # 8:00 по МСК
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
    total_requests INTEGER DEFAULT 0,
    last_reset_date TEXT,
    referral_bonus_received INTEGER DEFAULT 0
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

cursor.execute('''
CREATE TABLE IF NOT EXISTS daily_reset_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reset_date TEXT,
    users_reset INTEGER
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
    # Генерируем уникальный реферальный код
    referral_code = base64.urlsafe_b64encode(str(user_id).encode()).decode().replace('=', '')[:10]
    # Проверяем, что код уникален (на случай коллизий)
    while True:
        cursor.execute("SELECT user_id FROM users WHERE referral_code=?", (referral_code,))
        if not cursor.fetchone():
            break
        referral_code = base64.urlsafe_b64encode(str(user_id + random.randint(1, 1000)).encode()).decode().replace('=', '')[:10]
    
    cursor.execute('''
        INSERT OR IGNORE INTO users 
        (user_id, username, first_name, joined_date, last_request_date, referral_code, referrer_id, last_reset_date, referral_bonus_received)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (user_id, username, first_name, now, now[:10], referral_code, referrer_id, now[:10], 0))
    conn.commit()
    return referral_code

def update_user(user_id, **kwargs):
    """Обновляет поля пользователя"""
    for key, value in kwargs.items():
        cursor.execute(f"UPDATE users SET {key}=? WHERE user_id=?", (value, user_id))
    conn.commit()

def update_subscription_status(user_id, subscribed):
    cursor.execute("UPDATE users SET subscribed=?, last_check=? WHERE user_id=?", 
                   (1 if subscribed else 0, datetime.now().isoformat(), user_id))
    conn.commit()

def is_subscribed_now(user_id):
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
    last_reset = user[13]  # last_reset_date
    today = datetime.now().strftime("%Y-%m-%d")
    if last_reset < today:
        cursor.execute("UPDATE users SET requests_today=0, last_reset_date=? WHERE user_id=?", (today, user_id))
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
    cursor.execute("UPDATE users SET total_requests = total_requests + 1, last_request_date=? WHERE user_id=?", 
                   (datetime.now().strftime("%Y-%m-%d"), user_id))
    conn.commit()

def add_bonus(user_id, amount):
    cursor.execute("UPDATE users SET bonus_balance = bonus_balance + ? WHERE user_id=?", (amount, user_id))
    conn.commit()

def log_admin(admin_id, action, target):
    cursor.execute("INSERT INTO admin_logs (admin_id, action, target, date) VALUES (?, ?, ?, ?)",
                   (admin_id, action, target, datetime.now().isoformat()))
    conn.commit()

# ========== РЕФЕРАЛЬНАЯ СИСТЕМА ==========
def process_referral_bonus(invitee_id, inviter_id):
    """
    Начисляет бонусы за реферала.
    Возвращает True, если бонусы начислены впервые.
    """
    # Проверяем, не было ли уже начисления для этой пары
    cursor.execute("SELECT rewarded FROM referrals WHERE inviter_id=? AND invitee_id=?", (inviter_id, invitee_id))
    if cursor.fetchone():
        return False
    
    # Начисляем бонусы
    add_bonus(inviter_id, 3)  # +3 пригласившему
    add_bonus(invitee_id, 1)  # +1 новичку
    
    # Записываем в таблицу рефералов
    cursor.execute("INSERT INTO referrals (inviter_id, invitee_id, date, rewarded) VALUES (?, ?, ?, ?)",
                   (inviter_id, invitee_id, datetime.now().isoformat(), 1))
    
    # Отмечаем, что новичок уже получил бонус за реферала
    update_user(invitee_id, referral_bonus_received=1)
    
    conn.commit()
    
    # Отправляем уведомления
    try:
        inviter = get_user(inviter_id)
        inviter_name = inviter[2] or f"пользователь {inviter_id}"
        bot.send_message(inviter_id, 
                        f"🎉 По вашей реферальной ссылке зарегистрировался новый пользователь!\n"
                        f"Вам начислено +3 бонусных запроса.")
    except Exception as e:
        logging.warning(f"Failed to notify inviter {inviter_id}: {e}")
    
    try:
        bot.send_message(invitee_id, 
                        f"🎁 Вы зарегистрировались по реферальной ссылке!\n"
                        f"Вам начислен +1 бонусный запрос.")
    except Exception as e:
        logging.warning(f"Failed to notify invitee {invitee_id}: {e}")
    
    return True

def handle_referral_start(user_id, referrer_id):
    """
    Обрабатывает переход по реферальной ссылке.
    """
    # Получаем данные пользователей
    inviter = get_user(referrer_id)
    invitee = get_user(user_id)
    
    if not inviter:
        logging.warning(f"Referrer {referrer_id} not found")
        return
    
    if not invitee:
        logging.warning(f"Invitee {user_id} not found")
        return
    
    # Проверяем, не является ли реферер самим пользователем
    if referrer_id == user_id:
        logging.info(f"User {user_id} tried to refer themselves")
        return
    
    # Проверяем, не получал ли уже новичок бонус за реферала
    if invitee[14] == 1:  # referral_bonus_received
        logging.info(f"User {user_id} already received referral bonus")
        return
    
    # Если новичок уже подписан на канал, начисляем бонус сразу
    if invitee[9] == 1:  # subscribed
        process_referral_bonus(user_id, referrer_id)
    else:
        # Иначе ждём подписки
        logging.info(f"User {user_id} needs to subscribe first")

# ========== ЕЖЕДНЕВНЫЙ СБРОС ЗАПРОСОВ ==========
def reset_daily_requests():
    """Сбрасывает requests_today для пользователей, у которых закончились запросы, в 8:00 МСК"""
    while True:
        now = datetime.now()
        # Расчёт времени до следующего сброса (8:00 МСК = UTC+3)
        target = now.replace(hour=RESET_HOUR, minute=0, second=0, microsecond=0) - timedelta(hours=3)
        if now >= target:
            target += timedelta(days=1)
        sleep_seconds = (target - now).total_seconds()
        logging.info(f"Сброс запланирован через {sleep_seconds/3600:.1f} часов")
        time.sleep(max(1, sleep_seconds))
        
        try:
            # Получаем пользователей, у которых были запросы сегодня
            today = datetime.now().strftime("%Y-%m-%d")
            users_to_reset = cursor.execute(
                "SELECT user_id, first_name FROM users WHERE requests_today > 0 AND last_reset_date < ?",
                (today,)
            ).fetchall()
            
            # Сбрасываем счётчики
            cursor.execute("UPDATE users SET requests_today = 0, last_reset_date = ? WHERE last_reset_date < ?", 
                           (today, today))
            conn.commit()
            
            # Отправляем уведомления
            for user_id, name in users_to_reset:
                try:
                    bot.send_message(
                        user_id,
                        f"🌅 Доброе утро, {name or 'друг'}!\n\n"
                        f"Твои 10 бесплатных запросов на сегодня снова с тобой! 🎉\n"
                        f"Заходи решать задачи по математике, физике, биологии и другим предметам.\n\n"
                        f"Если нужно больше — пригласи друга (кнопка «🔗 Рефералка») и получи бонусные запросы!"
                    )
                except Exception as e:
                    logging.warning(f"Не удалось отправить уведомление пользователю {user_id}: {e}")
            
            # Логируем сброс
            cursor.execute("INSERT INTO daily_reset_log (reset_date, users_reset) VALUES (?, ?)",
                          (today, len(users_to_reset)))
            conn.commit()
            
            log_admin(0, "daily_reset", f"Сброшено {len(users_to_reset)} пользователей")
            logging.info(f"Ежедневный сброс выполнен. Сброшено {len(users_to_reset)} пользователей.")
            
        except Exception as e:
            logging.error(f"Ошибка при ежедневном сбросе: {e}")

# Запуск потока для ежедневного сброса
reset_thread = threading.Thread(target=reset_daily_requests, daemon=True)
reset_thread.start()

# ========== ТОЧНЫЕ РЕШАТЕЛИ ==========
class ProrabBotSolver:
    """Универсальный решатель для точных предметов"""
    
    def __init__(self):
        self.g = 9.8
        self.math = math
        
    # ===== ФИЗИКА =====
    def solve_ballistics(self, v0, angle_deg):
        """
        Решает задачу баллистики
        v0: начальная скорость (м/с)
        angle_deg: угол в градусах
        """
        # Точные тригонометрические значения
        angle_rad = math.radians(angle_deg)
        sin_a = math.sin(angle_rad)
        cos_a = math.cos(angle_rad)
        sin_2a = math.sin(2 * angle_rad)
        
        # Расчёты
        t_flight = (2 * v0 * sin_a) / self.g
        h_max = (v0**2 * sin_a**2) / (2 * self.g)
        distance = (v0**2 * sin_2a) / self.g
        
        # Пошаговое объяснение
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
            "velocity": (0, 100000, "м/с"),
            "mass": (0, 1000000, "кг"),
            "energy": (0, 1e9, "Дж"),
            "probability": (0, 1, "")
        }
        if quantity in checks:
            min_val, max_val, unit = checks[quantity]
            if value < min_val or value > max_val:
                return f"⚠️ Подозрительное значение: {value} {unit}"
        return "✅ Значение правдоподобно"
    
    # ===== ГЕНЕТИКА =====
    def genetics_x_linked(self, mother_gen, father_gen, trait):
        """
        Решает задачи по Х-сцепленному наследованию
        mother_gen: генотип матери (например "XBXb")
        father_gen: генотип отца (например "XbY")
        trait: искомый признак
        """
        # Решётка Пеннета для Х-хромосомы
        x_offspring = {
            "XBXb": 0.25,  # дочь, носитель
            "XbXb": 0.25,  # дочь, больная
            "XBY": 0.25,   # сын, здоровый
            "XbY": 0.25    # сын, больной
        }
        
        probabilities = {
            "male_affected": 0.25,
            "female_affected": 0.25,
            "male_healthy": 0.25,
            "female_healthy": 0.25,
            "carrier_female": 0.25
        }
        
        # Расчёт вероятности для конкретного признака
        if trait == "male_affected":
            prob = 0.25
            explanation = [
                "P(самец) = 1/2",
                "P(больной среди самцов) = 1/2 (XbY)",
                "Итого: 1/2 × 1/2 = 1/4 = 25%"
            ]
            return {"probability": prob, "percent": "25%", "explanation": explanation}
        
        return probabilities
    
    # ===== СТАТИСТИКА И ВЕРОЯТНОСТЬ =====
    def combination(self, n, k):
        """Число сочетаний C(n, k)"""
        return math.comb(n, k)
    
    def permutation(self, n, k=None):
        """Число размещений A(n, k)"""
        if k is None:
            return math.factorial(n)
        return math.perm(n, k)
    
    def binomial_probability(self, n, k, p):
        """Вероятность k успехов в n испытаниях Бернулли"""
        return math.comb(n, k) * (p ** k) * ((1 - p) ** (n - k))
    
    def expectation(self, values, probabilities=None):
        """Математическое ожидание"""
        if probabilities:
            return sum(v * p for v, p in zip(values, probabilities))
        return sum(values) / len(values)
    
    def variance(self, values, probabilities=None):
        """Дисперсия"""
        mu = self.expectation(values, probabilities)
        if probabilities:
            return sum(p * (v - mu) ** 2 for v, p in zip(values, probabilities))
        return sum((v - mu) ** 2 for v in values) / len(values)

solver = ProrabBotSolver()

# ========== БАЗА ЗНАНИЙ ДЛЯ ЧАСТЫХ ВОПРОСОВ ==========
knowledge_base = {
    "social": {
        "инфляция": "Инфляция — это повышение общего уровня цен на товары и услуги.\n\n"
                    "Причины: рост денежной массы, увеличение спроса, рост издержек.\n"
                    "Виды: умеренная (до 10% в год), галопирующая (до 50%), гиперинфляция (свыше 50% в месяц).\n"
                    "Последствия: обесценивание денег, снижение покупательной способности, перераспределение доходов.\n\n"
                    "Ответ: инфляция — это долгосрочное повышение общего уровня цен.",
        
        "спрос": "Спрос — это желание и возможность потребителя купить товар или услугу.\n\n"
                 "Закон спроса: чем выше цена, тем ниже спрос (при прочих равных).\n"
                 "Неценовые факторы: доходы, вкусы, цены на заменители, ожидания.\n\n"
                 "Ответ: спрос — это платёжеспособная потребность в товаре.",
        
        "предложение": "Предложение — это желание и возможность производителя продать товар.\n\n"
                       "Закон предложения: чем выше цена, тем выше предложение.\n"
                       "Неценовые факторы: технологии, налоги, цены на ресурсы.\n\n"
                       "Ответ: предложение — это готовность продавцов поставлять товары.",
        
        "государство": "Государство — это организация политической власти, управляющая обществом.\n\n"
                       "Признаки: территория, суверенитет, публичная власть, налоги, законы.\n"
                       "Функции: внутренние (экономическая, социальная, правоохранительная) и внешние (оборона, дипломатия).\n\n"
                       "Ответ: государство — это суверенная организация власти.",
        
        "мораль": "Мораль — это совокупность норм и правил поведения, основанных на представлениях о добре и зле.\n\n"
                  "Функции: регулятивная, оценочная, воспитательная.\n"
                  "Отличие от права: неформальность, опора на совесть и общественное мнение.\n\n"
                  "Ответ: мораль — это неписаные правила поведения."
    },
    
    "literature": {
        "онегин": "«Евгений Онегин» — роман в стихах А.С. Пушкина.\n\n"
                  "Главные герои: Евгений Онегин, Татьяна Ларина, Владимир Ленский, Ольга Ларина.\n"
                  "Сюжет: Онегин приезжает в деревню, знакомится с Ленским, отвергает любовь Татьяны, убивает Ленского на дуэли, уезжает. Позже встречает Татьяну в свете — она уже замужем, но любит его.\n"
                  "Темы: лишний человек, любовь, дружба, долг.\n\n"
                  "Ответ: роман о трагической судьбе «лишнего человека».",
        
        "печорин": "Григорий Печорин — главный герой романа М.Ю. Лермонтова «Герой нашего времени».\n\n"
                   "Характеристика: умный, cynical, разочарованный, ищущий острых ощущений.\n"
                   "Черты: эгоизм, противоречивость, способность к анализу.\n"
                   "Роль в романе: «портрет, составленный из пороков всего поколения».\n\n"
                   "Ответ: Печорин — тип «лишнего человека» 30-х годов XIX века.",
        
        "раскольников": "Родион Раскольников — герой романа Ф.М. Достоевского «Преступление и наказание».\n\n"
                        "Идея: разделение людей на «право имеющих» и «тварей дрожащих».\n"
                        "Преступление: убийство старухи-процентщицы.\n"
                        "Наказание: нравственные муки, каторга, перерождение через любовь к Соне.\n\n"
                        "Ответ: Раскольников — образ страдающего интеллигента, ищущего правду."
    },
    
    "russian": {
        "фонетика": "Фонетика — раздел языкознания, изучающий звуки речи.\n\n"
                    "Гласные: 6 основных (а, о, у, э, и, ы).\n"
                    "Согласные: твёрдые/мягкие, звонкие/глухие.\n"
                    "Звуки и буквы: не путать! Звуки слышим и произносим, буквы пишем.\n\n"
                    "Ответ: фонетика — учение о звуковом строе языка.",
        
        "морфология": "Морфология — раздел грамматики, изучающий части речи.\n\n"
                      "Самостоятельные части речи: имя существительное, имя прилагательное, имя числительное, глагол, наречие, местоимение.\n"
                      "Служебные: предлог, союз, частица.\n"
                      "Особые формы глагола: причастие, деепричастие.\n\n"
                      "Ответ: морфология — учение о частях речи и их формах.",
        
        "синтаксис": "Синтаксис — раздел грамматики, изучающий строение словосочетаний и предложений.\n\n"
                     "Словосочетание: два и более слов, связанных по смыслу и грамматически.\n"
                     "Предложение: имеет грамматическую основу и интонационную завершённость.\n"
                     "Виды предложений: простые/сложные, повествовательные/вопросительные/побудительные.\n\n"
                     "Ответ: синтаксис — учение о строении предложений.",
        
        "нн": "Правило написания Н и НН в прилагательных и причастиях.\n\n"
              "НН пишется:\n"
              "• в суффиксах -енн-, -онн- (соломенный, революционный);\n"
              "• в прилагательных, образованных от существительных с основой на Н (туманный ← туман);\n"
              "• в полных страдательных причастиях прошедшего времени (скошенный, прочитанный).\n\n"
              "Н пишется:\n"
              "• в суффиксах -ан-, -ян-, -ин- (кожаный, серебряный, звериный);\n"
              "• в кратких причастиях (книга прочитана).\n\n"
              "Ответ: правописание Н и НН зависит от части речи и способа образования."
    }
}

# ========== МАКСИМАЛЬНО УСИЛЕННЫЕ ПРОМПТЫ ==========
PROMPTS = {
    "math": """
ТЫ — ПРОФЕССОР МАТЕМАТИКИ. Решай любые математические задачи максимально подробно.

СТРОГИЕ ПРАВИЛА ОФОРМЛЕНИЯ:
1. ЗАПРЕЩЕНО использовать LaTeX (никаких $, $$, \\, {, }, \frac, \sqrt).
2. Корни обозначай как sqrt() (например, sqrt(2), sqrt(148)).
3. Степени обозначай как ^2, ^3 (например, x^2, a^3).
4. Дроби пиши через / : 3/4, (a+b)/c, (-b ± sqrt(D))/(2a).
5. Умножение обозначай * или просто ставь множители рядом.
6. Дискриминант: D = b^2 - 4ac.
7. Корни: x1 = (-b + sqrt(D))/(2a), x2 = (-b - sqrt(D))/(2a).
8. В конце ОБЯЗАТЕЛЬНО "Ответ: ...".
9. Если запрос не является математической задачей, попробуй угадать, что имел в виду пользователь.

НИКАКОГО LaTeX. ТОЛЬКО ОБЫЧНЫЙ ТЕКСТ.
    """,

    "physics": """
ТЫ — ПРОФЕССОР ФИЗИКИ. Твои ответы должны быть точными, с формулами и единицами измерения.

ПРАВИЛА:
1. НИКАКОГО LaTeX.
2. Единицы измерения пиши через пробел: 10 м/с, 5 кг.
3. Степени: м^2, м^3, с^-1, кг·м/с^2.
4. Корни: sqrt().
5. Дроби: /.
6. Используй g = 9.8 м/с².
7. Все вычисления делай с максимальной точностью, не округляй промежуточные результаты.
8. В конце обязательно "Ответ: ...".

ПРИМЕР:
Задача: тело брошено под углом 60° со скоростью 400 м/с. Найти высоту и время.
Решение:
v0 = 400 м/с, α = 60°.
sin(60°) = √3/2 ≈ 0.8660254
v0y = v0·sinα = 400·0.8660254 = 346.41 м/с
t = 2·v0y/g = 2·346.41/9.8 = 70.7 с
h = v0y²/(2g) = (346.41)²/(19.6) = 120000/19.6 ≈ 6122 м
Ответ: время полёта 70.7 с, максимальная высота 6122 м.
    """,

    "biology": """
ТЫ — ОПЫТНЫЙ УЧИТЕЛЬ БИОЛОГИИ. Объясняй простым языком, но полно и научно.

ТРЕБОВАНИЯ:
1. НИКАКОГО LaTeX.
2. Структурируй ответ: что это? как работает? зачем нужно?
3. Если речь о генетике, используй чёткие обозначения и расчёты вероятностей.
4. В конце "Ответ:" или "Вывод:".

ПРИМЕР ПО ГЕНЕТИКЕ:
Задача: мать XᴮXᵇ, отец XᵇY. Какова вероятность больного сына?
Решение:
X-хромосомы матери: Xᴮ (50%), Xᵇ (50%).
X-хромосома отца: Xᵇ (50% для дочерей), Y (50% для сыновей).
Вероятность сына: 1/2.
Вероятность больного сына (XᵇY): 1/2 · 1/2 = 1/4 = 25%.
Ответ: 25%.
    """,

    "chemistry": """
ТЫ — ХИМИК-ЭКСПЕРТ. Используй химические формулы и уравнения реакций.

ПРАВИЛА:
1. НИКАКОГО LaTeX.
2. Формулы: H2O, CO2, CH4, H2SO4.
3. Реакции со стрелкой ->.
4. Коэффициенты ставь перед формулами.
5. Дроби: /, корни: sqrt().
6. В конце "Ответ:".

ПРИМЕР:
Задача: сколько литров кислорода нужно для сжигания 2 моль метана?
Решение:
CH4 + 2O2 -> CO2 + 2H2O
На 1 моль CH4 нужно 2 моль O2.
На 2 моль CH4 нужно 4 моль O2.
V = n·22.4 = 4·22.4 = 89.6 л.
Ответ: 89.6 л.
    """,

    "statistics": """
ТЫ — ПРОФЕССОР СТАТИСТИКИ. Решай задачи по теории вероятностей и статистике.

ПРАВИЛА:
1. НИКАКОГО LaTeX.
2. Используй обозначения: C(n,k) для сочетаний, P(A) для вероятности.
3. Давай пошаговое решение с формулами.
4. В конце "Ответ: ...".

ПРИМЕР:
Задача: сколько способов выбрать 3 человек из 10?
Решение:
C(10,3) = 10!/(3!·7!) = (10·9·8)/(3·2·1) = 720/6 = 120.
Ответ: 120 способов.
    """,

    "social": """
ТЫ — УЧИТЕЛЬ ОБЩЕСТВОЗНАНИЯ. Отвечай на вопросы понятно и структурированно.

ПРАВИЛА:
1. НИКАКОГО LaTeX.
2. Дай определение, перечисли признаки/функции, приведи примеры.
3. В конце "Ответ: ...".

ПРИМЕР:
Вопрос: что такое инфляция?
Ответ: инфляция — это повышение общего уровня цен...
    """,

    "literature": """
ТЫ — УЧИТЕЛЬ ЛИТЕРАТУРЫ. Анализируй произведения и образы героев.

ПРАВИЛА:
1. НИКАКОГО LaTeX.
2. Для анализа героя: кто такой? черты характера? роль в произведении?
3. Для сюжета: краткое содержание, тема, идея.
4. В конце "Ответ:" с кратким итогом.
    """,

    "russian": """
ТЫ — УЧИТЕЛЬ РУССКОГО ЯЗЫКА. Объясняй правила, делай разборы.

ПРАВИЛА:
1. НИКАКОГО LaTeX.
2. Для правила: формулировка, примеры, исключения.
3. Для разбора слова/предложения: покажи структуру.
4. В конце "Ответ:".
    """,

    "general": """
ТЫ — ИНТЕЛЛЕКТУАЛЬНЫЙ ПОМОЩНИК. Отвечай на любые вопросы полезно.

ПРАВИЛА:
1. НИКАКОГО LaTeX.
2. На простые примеры отвечай с пояснением.
3. Если запрос с опечаткой, исправь и ответь.
4. В конце "Ответ: ...".
    """
}

SUBJECT_NAMES = {
    "math": "📐 Математика",
    "physics": "🔮 Физика",
    "biology": "🧬 Биология",
    "chemistry": "⚗️ Химия",
    "statistics": "📊 Статистика",
    "social": "🌍 Обществознание",
    "literature": "📚 Литература",
    "russian": "🇷🇺 Русский язык",
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
        InlineKeyboardButton("🌍 Обществознание", callback_data="subj_social"),
        InlineKeyboardButton("📚 Литература", callback_data="subj_literature"),
        InlineKeyboardButton("🇷🇺 Русский язык", callback_data="subj_russian"),
        InlineKeyboardButton("📚 Любой", callback_data="subj_general"),
        InlineKeyboardButton("🔗 Рефералка", callback_data="referral"),
        InlineKeyboardButton("📊 Мой баланс", callback_data="balance"),
        InlineKeyboardButton("🔒 VPN 20+", callback_data="vpn"),
        InlineKeyboardButton("📖 Помощь", callback_data="help"),
        InlineKeyboardButton("📸 Фото задачи", callback_data="photo_help")
    ]
    # Размещаем по 2 в ряд
    rows = [buttons[i:i+2] for i in range(0, len(buttons), 2)]
    for row in rows:
        keyboard.add(*row)
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

# ========== УЛУЧШЕННАЯ ОЧИСТКА И FALLBACK ==========
def safe_eval_math(expr):
    """Безопасно вычисляет простое арифметическое выражение."""
    expr = expr.strip()
    if not re.match(r'^[0-9+\-*/().\s]+$', expr):
        return None
    try:
        result = eval(expr, {"__builtins__": None}, {})
        return str(result)
    except:
        return None

def extract_possible_math(question):
    """Пытается извлечь математическое выражение из текста."""
    match = re.search(r'[0-9+\-*/().\s]+', question)
    if match:
        return match.group(0)
    return None

def trig_fallback(question):
    """Обрабатывает типичные тригонометрические запросы с опечатками."""
    q = question.lower().strip()
    q = re.sub(r'\s+', '', q)
    patterns = [
        (r'(cos|косинус|кос|cosinus|косинуса?)\s*(\d+)', lambda m: f"cos({m.group(2)}°)"),
        (r'(sin|синус|син)\s*(\d+)', lambda m: f"sin({m.group(2)}°)"),
        (r'(tan|tg|тангенс|тг|тангенса?)\s*(\d+)', lambda m: f"tan({m.group(2)}°)")
    ]
    for pat, fmt in patterns:
        match = re.search(pat, q)
        if match:
            angle = match.group(2)
            if angle in ['30', '45', '60', '90', '0']:
                if 'cos' in match.group(0) or 'кос' in match.group(0):
                    if angle == '30':
                        return "cos 30° = √3/2 ≈ 0.8660\n\nОтвет: 0.8660"
                    elif angle == '45':
                        return "cos 45° = √2/2 ≈ 0.7071\n\nОтвет: 0.7071"
                    elif angle == '60':
                        return "cos 60° = 1/2 = 0.5\n\nОтвет: 0.5"
                    elif angle == '90':
                        return "cos 90° = 0\n\nОтвет: 0"
                    elif angle == '0':
                        return "cos 0° = 1\n\nОтвет: 1"
                elif 'sin' in match.group(0) or 'син' in match.group(0):
                    if angle == '30':
                        return "sin 30° = 1/2 = 0.5\n\nОтвет: 0.5"
                    elif angle == '45':
                        return "sin 45° = √2/2 ≈ 0.7071\n\nОтвет: 0.7071"
                    elif angle == '60':
                        return "sin 60° = √3/2 ≈ 0.8660\n\nОтвет: 0.8660"
                    elif angle == '90':
                        return "sin 90° = 1\n\nОтвет: 1"
                    elif angle == '0':
                        return "sin 0° = 0\n\nОтвет: 0"
                elif 'tan' in match.group(0) or 'тангенс' in match.group(0):
                    if angle == '30':
                        return "tan 30° = √3/3 ≈ 0.5774\n\nОтвет: 0.5774"
                    elif angle == '45':
                        return "tan 45° = 1\n\nОтвет: 1"
                    elif angle == '60':
                        return "tan 60° = √3 ≈ 1.732\n\nОтвет: 1.732"
                    elif angle == '90':
                        return "tan 90° не определен (бесконечность)"
                    elif angle == '0':
                        return "tan 0° = 0\n\nОтвет: 0"
            else:
                func = 'cos' if 'cos' in match.group(0) or 'кос' in match.group(0) else ('sin' if 'sin' in match.group(0) or 'син' in match.group(0) else 'tan')
                return f"{func}({angle}°) – вычислите с помощью калькулятора.\n\nОтвет: требуется численное значение."
    return None

def clean_answer(text, original_question=""):
    """Очищает ответ и возвращает читаемый текст."""
    # Сначала пробуем тригонометрический fallback
    trig = trig_fallback(original_question)
    if trig:
        return trig
    
    if not text or len(text.strip()) < 2:
        if original_question:
            expr = extract_possible_math(original_question)
            if expr:
                calc = safe_eval_math(expr)
                if calc:
                    return f"Результат: {calc}\n\nОтвет: {calc}"
        return "❌ Нейросеть не дала ответ. Возможно, запрос неясен. Пожалуйста, переформулируйте."

    # Удаляем LaTeX
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
            expr = extract_possible_math(original_question)
            if expr:
                calc = safe_eval_math(expr)
                if calc:
                    return f"Результат: {calc}\n\nОтвет: {calc}"
        return "❌ Ответ слишком короткий. Попробуйте переформулировать."

    return text

def ask_yandex_gpt_with_retry(question, subject, retries=2):
    """Запрашивает GPT с повторными попытками при пустом ответе."""
    for attempt in range(retries):
        answer = ask_yandex_gpt_once(question, subject)
        if answer and len(answer.strip()) > 2:
            return answer
        logging.warning(f"Empty response from GPT, attempt {attempt+1}")
        time.sleep(1)
    return ""

def ask_yandex_gpt_once(question, subject):
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
        return ""

def ask_yandex_gpt(question, subject):
    return ask_yandex_gpt_with_retry(question, subject, retries=2)

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

def check_physical_question(question):
    """Проверяет, можно ли решить задачу через точный решатель физики."""
    q = question.lower()
    # Баллистика: скорость и угол
    v_match = re.search(r'(\d+)\s*м[/\\]с', q)
    a_match = re.search(r'(\d+)\s*°', q) or re.search(r'угол\s*(\d+)', q)
    if v_match and a_match and ('брос' in q or 'летит' in q or 'кид' in q):
        v0 = float(v_match.group(1))
        angle = float(a_match.group(1))
        result = solver.solve_ballistics(v0, angle)
        exp = '\n'.join(result['explanation'])
        return (f"✅ <b>Решение:</b>\n\n{exp}\n\n"
                f"Время полёта: {result['time']} с\n"
                f"Максимальная высота: {result['height']} м\n"
                f"Дальность: {result['range']} м\n\n"
                f"Ответ: t = {result['time']} с, H = {result['height']} м, L = {result['range']} м")
    return None

def check_stats_question(question):
    """Проверяет, можно ли решить задачу через точный решатель статистики."""
    q = question.lower()
    # Сочетания: "выбрать k из n"
    choose_match = re.search(r'выбрать\s*(\d+)\s*из\s*(\d+)', q)
    if choose_match:
        k = int(choose_match.group(1))
        n = int(choose_match.group(2))
        if k <= n:
            c = solver.combination(n, k)
            return f"✅ <b>Решение:</b>\n\nЧисло способов выбрать {k} из {n}:\nC({n},{k}) = {n}!/({k}!·({n}-{k})!) = {c}\n\nОтвет: {c}"
    
    # Вероятность биномиальная: "вероятность k успехов из n"
    binom_match = re.search(r'вероятность\s*(\d+)\s*успехов\s*из\s*(\d+)', q)
    if binom_match:
        k = int(binom_match.group(1))
        n = int(binom_match.group(2))
        # предполагаем p=0.5 если не указано
        p = 0.5
        prob = solver.binomial_probability(n, k, p)
        return f"✅ <b>Решение:</b>\n\nВероятность {k} успехов из {n} при p=0.5:\nP = C({n},{k})·(0.5)^{n} = {prob:.4f}\n\nОтвет: {prob:.2%}"
    
    return None

def check_knowledge_base(question, subject):
    """Проверяет, есть ли ответ в базе знаний."""
    if subject not in knowledge_base:
        return None
    q = question.lower().strip()
    # Ищем ключевые слова
    for key, answer in knowledge_base[subject].items():
        if key in q:
            return f"✅ <b>Ответ из базы знаний:</b>\n\n{answer}"
    return None

# ========== ДЕКОРАТОР ПРОВЕРКИ ==========
def check_sub_and_limit(handler_func):
    def wrapper(message):
        user_id = message.from_user.id
        user = get_user(user_id)
        if user and user[11] == 1:
            bot.send_message(user_id, "❌ Вы заблокированы. Обратитесь к администратору.")
            return
        if not is_subscribed_cached(user_id) and not is_admin(user_id):
            bot.send_message(user_id, "🚫 Чтобы пользоваться ботом, подпишись на канал:",
                             reply_markup=subscribe_keyboard())
            return
        allowed, remaining, bonus = can_make_request(user_id)
        if not allowed and not is_admin(user_id):
            bot.send_message(user_id,
                f"❌ У тебя закончились запросы.\n"
                f"Доступно сегодня: 10 бесплатных + {bonus} бонусных.\n"
                "Возвращайся завтра в 8:00 (бесплатные обновятся) или пригласи друга (кнопка «🔗 Рефералка»).")
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
    
    # Обработка реферального параметра
    if len(args) > 1 and args[1].startswith('ref_'):
        ref_code = args[1][4:]
        logging.info(f"Referral start with code: {ref_code}")
        cursor.execute("SELECT user_id FROM users WHERE referral_code=?", (ref_code,))
        row = cursor.fetchone()
        if row:
            referrer_id = row[0]
            logging.info(f"Found referrer: {referrer_id}")
    
    # Создаём пользователя, если его нет
    user = get_user(user_id)
    if not user:
        create_user(user_id, username, first_name, referrer_id)
        logging.info(f"Created new user {user_id} with referrer {referrer_id}")
        
        # Если есть реферер, обрабатываем реферальный бонус
        if referrer_id and referrer_id != user_id:
            # Проверяем, не получал ли уже новичок бонус
            cursor.execute("SELECT referral_bonus_received FROM users WHERE user_id=?", (user_id,))
            bonus_received = cursor.fetchone()[0]
            if not bonus_received:
                # Если новичок уже подписан, начисляем сразу
                if is_subscribed_cached(user_id):
                    process_referral_bonus(user_id, referrer_id)
                else:
                    # Иначе запоминаем реферера для начисления после подписки
                    update_user(user_id, referrer_id=referrer_id)
                    logging.info(f"User {user_id} will get referral bonus after subscription")
    else:
        # Если пользователь уже есть, но перешёл по реферальной ссылке впервые
        if referrer_id and referrer_id != user_id and user[8] is None and user[14] == 0:
            # Обновляем referrer_id
            update_user(user_id, referrer_id=referrer_id)
            logging.info(f"Updated referrer for existing user {user_id} to {referrer_id}")
            
            # Если уже подписан, начисляем бонус
            if is_subscribed_cached(user_id):
                process_referral_bonus(user_id, referrer_id)

    bot.send_message(user_id,
        "🚀 <b>ПРОРАБ ГДЗ</b>\n\n"
        "📐 Математика\n🔮 Физика\n🧬 Биология\n⚗️ Химия\n📊 Статистика\n"
        "🌍 Обществознание\n📚 Литература\n🇷🇺 Русский язык\n📸 Фото задачи\n\n"
        "🔒 <b>VPN 20+ серверов</b> — @ProrabVPN_bot\n💰 200₽/мес",
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
    
    # 1. Проверяем тригонометрический fallback
    trig = trig_fallback(question)
    if trig:
        increment_request(user_id)
        bot.send_message(
            user_id,
            f"✅ <b>Решение:</b>\n\n{trig}\n\n━━━━━━━━━━━━━━━━━━━━━\n🔒 @ProrabVPN_bot - 20+ серверов, 200₽/мес",
            parse_mode="HTML", reply_markup=back_keyboard())
        return
    
    # 2. Для физики проверяем, можно ли решить через точный решатель
    if subject == "physics":
        phys_answer = check_physical_question(question)
        if phys_answer:
            increment_request(user_id)
            bot.send_message(
                user_id,
                f"{phys_answer}\n\n━━━━━━━━━━━━━━━━━━━━━\n🔒 @ProrabVPN_bot - 20+ серверов, 200₽/мес",
                parse_mode="HTML", reply_markup=back_keyboard())
            return
    
    # 3. Для статистики проверяем точный решатель
    if subject == "statistics":
        stats_answer = check_stats_question(question)
        if stats_answer:
            increment_request(user_id)
            bot.send_message(
                user_id,
                f"{stats_answer}\n\n━━━━━━━━━━━━━━━━━━━━━\n🔒 @ProrabVPN_bot - 20+ серверов, 200₽/мес",
                parse_mode="HTML", reply_markup=back_keyboard())
            return
    
    # 4. Проверяем базу знаний для гуманитарных предметов
    kb_answer = check_knowledge_base(question, subject)
    if kb_answer:
        increment_request(user_id)
        bot.send_message(
            user_id,
            f"{kb_answer}\n\n━━━━━━━━━━━━━━━━━━━━━\n🔒 @ProrabVPN_bot - 20+ серверов, 200₽/мес",
            parse_mode="HTML", reply_markup=back_keyboard())
        return
    
    # 5. Если ничего не подошло, отправляем в GPT
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
        text = ("📖 <b>Помощь</b>\n\n"
                "📝 Отправь задачу текстом\n📸 Или фото (распознаю)\n"
                "📚 Выбери предмет для точности\n\n"
                "📌 Примеры:\n• 3x^2 + 8x - 7 = 0\n"
                "• Сила тока при 220В и 50 Ом\n"
                "• Что такое фотосинтез?\n"
                "• C(10,3)\n"
                "• Что такое инфляция?\n\n"
                "🔒 @ProrabVPN_bot - 20+ серверов, 200₽/мес")
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
            
            # Если есть реферер и ещё не получали бонус
            if user and user[8] and user[14] == 0:
                # Начисляем бонус
                process_referral_bonus(user_id, user[8])
                bot.edit_message_text("✅ Спасибо за подписку! Тебе начислен бонусный запрос, а твоему другу — 3.",
                                      user_id, call.message.message_id)
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
        bonus_sum = cursor.execute("SELECT SUM(bonus_balance) FROM users").fetchone()[0] or 0
        ref_count = cursor.execute("SELECT COUNT(*) FROM referrals").fetchone()[0]
        bot.edit_message_text(f"📊 <b>Статистика</b>\n\n"
                              f"👥 Всего пользователей: {total}\n"
                              f"🔥 Активных сегодня: {active}\n"
                              f"📝 Всего запросов: {req_sum}\n"
                              f"🎁 Всего бонусных: {bonus_sum}\n"
                              f"🔗 Всего рефералов: {ref_count}",
                              user_id, call.message.message_id, parse_mode="HTML", reply_markup=back_keyboard())
    elif data == "admin_userlist":
        cursor.execute("SELECT user_id, first_name, username, total_requests, requests_today, bonus_balance, subscribed, is_blocked, referrer_id FROM users ORDER BY user_id DESC LIMIT 15")
        rows = cursor.fetchall()
        if not rows:
            text = "Список пуст."
        else:
            text = "📋 <b>Последние 15 пользователей:</b>\n\n"
            for r in rows:
                sub = "✅" if r[6] else "❌"
                block = "🚫" if r[7] else ""
                ref = f"приглашён {r[8]}" if r[8] else "—"
                text += f"🆔 {r[0]} | {r[1] or '?'} @{r[2] or '—'} {sub}{block}\n   Всего: {r[3]}, сегодня: {r[4]}, бонус: {r[5]}, {ref}\n"
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
        except Exception as e:
            logging.warning(f"Failed to notify user {uid} about bonus: {e}")
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
    print("╔════════════════════════════════════════════╗")
    print("║        🚀 ПРОРАБ ГДЗ ЗАПУЩЕН              ║")
    print("╠════════════════════════════════════════════╣")
    print("║ ✅ Физика (точные расчёты)                ║")
    print("║ ✅ Статистика (комбинаторика)             ║")
    print("║ ✅ База знаний (обществознание, лит-ра)   ║")
    print("║ ✅ Новые предметы: статистика, социал,    ║")
    print("║    литература, русский язык               ║")
    print("║ ✅ Ежедневный сброс в 8:00 МСК            ║")
    print("║ ✅ Уведомления о сбросе                    ║")
    print("║ ✅ Реферальная система ИСПРАВЛЕНА         ║")
    print("║ ✅ Кнопки рефералки и баланса              ║")
    print("║ ✅ Распознавание фото                       ║")
    print("║ ✅ Блокировка/разблокировка                ║")
    print("║ ✅ Уведомление о бонусах                   ║")
    print("║ 🔒 VPN: @ProrabVPN_bot                     ║")
    print("╚════════════════════════════════════════════╝")
    while True:
        try:
            bot.polling(none_stop=True)
        except Exception as e:
            logging.error(f"Polling error: {e}")
            time.sleep(3)
