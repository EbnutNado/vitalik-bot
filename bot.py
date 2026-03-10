import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import requests
import json
import time
import logging
import re
from datetime import datetime

# ======== НАСТРОЙКИ ========
TELEGRAM_TOKEN = "8451168327:AAGQffadqqBg3pZNQnjctVxH-dUgXsovTr4"
FOLDER_ID = "b1g0s9bjamjqrvas5pqr"  # из шага 1
API_KEY = "AQVNxnq1d97ei8asrSCgEdGN92cXym_faQZ8I3dp"      # из шага 3
# ============================

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

bot = telebot.TeleBot(TELEGRAM_TOKEN)
bot.set_my_commands([
    telebot.types.BotCommand("/start", "🚀 Запустить бота"),
    telebot.types.BotCommand("/help", "📖 Помощь"),
    telebot.types.BotCommand("/subjects", "📚 Выбрать предмет"),
])

# URL для YandexGPT
YANDEX_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"

# Промпты для разных предметов
PROMPTS = {
    "physics": "Ты — профессор физики. Объясняй законы физики, используй формулы. Решай задачи подробно и понятно. Формулы пиши в строчку, используй ^ для степеней.",
    "biology": "Ты — учитель биологии. Объясняй процессы простым языком, используй аналогии из жизни. Пиши понятно и интересно.",
    "math": "Ты — репетитор по математике. Показывай пошаговое решение, объясняй каждый шаг. Используй ^ для степеней.",
    "chemistry": "Ты — учитель химии. Объясняй реакции, используй уравнения. Пиши формулы правильно: H2O, CO2 и т.д.",
    "general": "Ты — умный помощник. Решаешь задачи по любым предметам. Будь дружелюбным и понятным. Отвечай подробно, но не слишком длинно."
}

# Названия предметов для кнопок
SUBJECT_NAMES = {
    "physics": "🔮 Физика",
    "biology": "🧬 Биология", 
    "math": "📐 Математика",
    "chemistry": "⚗️ Химия",
    "general": "📚 Общее"
}

# Хранилище выбранных предметов для пользователей
user_subjects = {}

def create_subject_keyboard():
    """Создает клавиатуру с предметами"""
    keyboard = InlineKeyboardMarkup(row_width=2)
    buttons = [
        InlineKeyboardButton(SUBJECT_NAMES["physics"], callback_data="subj_physics"),
        InlineKeyboardButton(SUBJECT_NAMES["biology"], callback_data="subj_biology"),
        InlineKeyboardButton(SUBJECT_NAMES["math"], callback_data="subj_math"),
        InlineKeyboardButton(SUBJECT_NAMES["chemistry"], callback_data="subj_chemistry"),
        InlineKeyboardButton(SUBJECT_NAMES["general"], callback_data="subj_general"),
    ]
    keyboard.add(*buttons)
    return keyboard

def format_answer(text):
    """Форматирует ответ для красивого вывода"""
    
    # Заменяем LaTeX
    text = re.sub(r'\$\$(.*?)\$\$', r'\1', text)
    text = re.sub(r'\\\((.*?)\\\)', r'\1', text)
    text = text.replace('\\', '')
    
    # Делаем степени красивыми
    text = re.sub(r'\^2', '²', text)
    text = re.sub(r'\^3', '³', text)
    
    # Разбиваем на строки и добавляем отступы
    lines = text.split('\n')
    formatted_lines = []
    
    for line in lines:
        line = line.strip()
        if not line:
            formatted_lines.append('')
            continue
            
        if '=' in line and any(c.isdigit() for c in line):
            # Формулы
            formatted_lines.append(f"🔹 {line}")
        elif line.lower().startswith(('ответ', 'answer')):
            # Ответ
            formatted_lines.append(f"\n✅ {line}")
        elif line.lower().startswith(('где', 'дано', 'given')):
            # Дано
            formatted_lines.append(f"\n📌 {line}")
        elif line[0].isdigit() and '.' in line[:3]:
            # Нумерованные списки
            formatted_lines.append(f"   {line}")
        elif line.startswith(('-', '•')):
            # Маркированные списки
            formatted_lines.append(f"   {line}")
        else:
            # Обычный текст
            if len(line) > 0 and line[0].isupper():
                # Заголовки
                formatted_lines.append(f"\n📝 {line}")
            else:
                formatted_lines.append(f"   {line}")
    
    return '\n'.join(formatted_lines)

def ask_yandex(question, subject="general"):
    """Отправляет запрос в YandexGPT"""
    
    system_prompt = PROMPTS.get(subject, PROMPTS["general"])
    
    # Формируем запрос
    data = {
        "modelUri": f"gpt://{FOLDER_ID}/yandexgpt-lite",
        "completionOptions": {
            "stream": False,
            "temperature": 0.3,
            "maxTokens": "2000"
        },
        "messages": [
            {
                "role": "system",
                "text": system_prompt
            },
            {
                "role": "user",
                "text": question
            }
        ]
    }
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Api-Key {API_KEY}"
    }
    
    try:
        logging.info(f"Запрос к YandexGPT ({subject})")
        response = requests.post(YANDEX_URL, headers=headers, json=data, timeout=30)
        
        if response.status_code == 200:
            result = response.json()
            answer = result['result']['alternatives'][0]['message']['text']
            return answer
        else:
            error_text = response.text
            logging.error(f"Ошибка {response.status_code}")
            return f"❌ Ошибка YandexGPT: {response.status_code}"
            
    except Exception as e:
        logging.error(f"Исключение: {e}")
        return f"❌ Ошибка: {str(e)}"

@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    """Обрабатывает нажатия на кнопки"""
    
    if call.data.startswith("subj_"):
        subject = call.data.replace("subj_", "")
        user_id = call.from_user.id
        
        # Сохраняем выбранный предмет
        user_subjects[user_id] = subject
        
        subject_name = SUBJECT_NAMES.get(subject, "Общее")
        
        # Редактируем сообщение
        bot.edit_message_text(
            f"✅ Выбран предмет: **{subject_name}**\n\nТеперь отправь мне задачу!",
            call.message.chat.id,
            call.message.message_id,
            parse_mode="Markdown"
        )
        
        # Отправляем дополнительное сообщение с подсказкой
        bot.send_message(
            call.message.chat.id,
            "📝 Отправь условие задачи, и я решу её с учётом выбранного предмета.\n"
            "Или используй /subjects чтобы сменить предмет."
        )

@bot.message_handler(commands=['start'])
def start(message):
    """Обработчик команды /start"""
    
    # Красивое приветствие
    welcome_text = """
🚀 **Добро пожаловать!**

Я — **умный бот-помощник** на базе ProrabVPN. Решаю задачи по:

🔮 **Физике**     🧬 **Биологии**
📐 **Математике** ⚗️ **Химии**

📌 **Просто отправь мне условие задачи** — и я покажу подробное решение!
@prorabVPN_bot

👇 **Выбери предмет** или отправляй задачу сразу (я определю сам)
    """
    
    # Отправляем приветствие с кнопками
    bot.send_message(
        message.chat.id,
        welcome_text,
        parse_mode="Markdown",
        reply_markup=create_subject_keyboard()
    )

@bot.message_handler(commands=['help'])
def help(message):
    """Обработчик команды /help"""
    
    help_text = """
📖 **Как пользоваться ботом:**

1️⃣ **Отправь задачу** — я определю предмет автоматически
2️⃣ Или выбери предмет через /subjects
3️⃣ Получи подробное решение с объяснениями

📌 **Примеры запросов:**
• `Найди силу тока при 220В и 50 Ом`
• `Что такое фотосинтез?`
• `Реши уравнение 2x + 5 = 15`

🔄 **Команды:**
/start — перезапустить бота
/subjects — выбрать предмет
/help — эта справка

⚡ **Бот работает на YandexGPT** — быстро и точно!
    """
    
    bot.send_message(message.chat.id, help_text, parse_mode="Markdown")

@bot.message_handler(commands=['subjects'])
def subjects(message):
    """Обработчик команды /subjects"""
    
    text = "📚 **Выбери предмет:**\n\nЗадачи будут решаться с учётом выбранной специализации."
    
    bot.send_message(
        message.chat.id,
        text,
        parse_mode="Markdown",
        reply_markup=create_subject_keyboard()
    )

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    """Обработчик всех текстовых сообщений"""
    
    user_text = message.text
    user_id = message.from_user.id
    
    # Показываем, что бот печатает
    bot.send_chat_action(message.chat.id, 'typing')
    
    # Получаем выбранный предмет пользователя (или общий)
    subject = user_subjects.get(user_id, "general")
    subject_name = SUBJECT_NAMES.get(subject, "Общее")
    
    # Отправляем статус
    status_msg = bot.send_message(
        message.chat.id,
        f"🤔 **Решаю задачу** по {subject_name}...\nЭто займёт несколько секунд.",
        parse_mode="Markdown"
    )
    
    # Получаем ответ от YandexGPT
    answer = ask_yandex(user_text, subject)
    
    # Форматируем ответ
    formatted_answer = format_answer(answer)
    
    # Создаем клавиатуру для быстрых действий
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("🔄 Новый запрос", callback_data="subj_general"),
        InlineKeyboardButton("📚 Выбрать предмет", callback_data="subjects")
    )
    
    # Отправляем ответ
    try:
        # Пробуем отправить с HTML форматированием
        bot.edit_message_text(
            f"✅ **Решение:**\n\n{formatted_answer}",
            message.chat.id,
            status_msg.message_id,
            parse_mode="HTML",
            reply_markup=keyboard
        )
    except:
        try:
            # Если HTML не работает, пробуем Markdown
            bot.edit_message_text(
                f"✅ **Решение:**\n\n{answer}",
                message.chat.id,
                status_msg.message_id,
                parse_mode="Markdown",
                reply_markup=keyboard
            )
        except:
            # Если всё сломалось, отправляем просто текст
            bot.edit_message_text(
                f"✅ РЕШЕНИЕ:\n\n{answer}",
                message.chat.id,
                status_msg.message_id,
                reply_markup=keyboard
            )

# Запуск бота
if __name__ == "__main__":
    print("╔════════════════════════════════════╗")
    print("║     🚀 БОТ ЗАПУЩЕН!                ║")
    print("╠════════════════════════════════════╣")
    print(f"║ 📁 Folder ID: {FOLDER_ID[:15]}...")
    print(f"║ 🔑 API Key: {API_KEY[:10]}...")
    print("║ 🤖 Жду сообщения...                ║")
    print("╚════════════════════════════════════╝")
    
    while True:
        try:
            bot.polling(none_stop=True, interval=0)
        except Exception as e:
            print(f"❌ Ошибка: {e}")
            time.sleep(3)
