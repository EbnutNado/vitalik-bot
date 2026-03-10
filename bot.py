import telebot
import requests
import json
import time
from datetime import datetime
import logging

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

# ======== ТВОИ ДАННЫЕ ========
TELEGRAM_TOKEN = "8451168327:AAGQffadqqBg3pZNQnjctVxH-dUgXsovTr4"
FOLDER_ID = "b1ggcn6gnmknss2qm13j"  # из шага 1
API_KEY = "ajel4lmkbhl6b7o83ik5"      # из шага 3
# ==============================

bot = telebot.TeleBot(TELEGRAM_TOKEN)

# URL для YandexGPT
YANDEX_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"

# Системные промпты для разных предметов
PROMPTS = {
    "физика": "Ты — профессор физики. Объясняй законы физики, используй формулы. Решай задачи подробно.",
    "биология": "Ты — учитель биологии. Объясняй процессы простым языком, используй аналогии из жизни.",
    "математика": "Ты — репетитор по математике. Показывай пошаговое решение, объясняй каждый шаг.",
    "химия": "Ты — учитель химии. Объясняй реакции, используй уравнения. Пиши формулы правильно.",
    "общий": "Ты — умный помощник. Решаешь задачи по любым предметам. Будь дружелюбным и понятным."
}

def ask_yandex(question, subject="общий"):
    """Отправляет запрос в YandexGPT"""
    
    # Выбираем промпт
    system_prompt = PROMPTS.get(subject, PROMPTS["общий"])
    
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
        logging.info(f"Отправляю запрос: {question[:50]}...")
        response = requests.post(YANDEX_URL, headers=headers, json=data, timeout=30)
        
        if response.status_code == 200:
            result = response.json()
            answer = result['result']['alternatives'][0]['message']['text']
            logging.info("Получил ответ от YandexGPT")
            return answer
        else:
            error_text = response.text
            logging.error(f"Ошибка {response.status_code}: {error_text}")
            return f"❌ Ошибка YandexGPT: {response.status_code}\n{error_text[:200]}"
            
    except requests.exceptions.Timeout:
        return "⏰ Таймаут. YandexGPT долго думает, попробуй еще раз"
    except Exception as e:
        logging.error(f"Исключение: {e}")
        return f"❌ Ошибка: {str(e)}"

def detect_subject(text):
    """Определяет предмет по тексту"""
    text = text.lower()
    
    # Ключевые слова для предметов
    subjects = {
        "физика": ["сила", "ток", "напряжение", "сопротивление", "масса", "скорость", 
                   "ускорение", "ньютон", "джоуль", "физика", "электричество"],
        "биология": ["клетка", "организм", "фотосинтез", "днк", "эволюция", "биология",
                     "ген", "белок", "фермент", "сердце", "мозг"],
        "математика": ["уравнение", "функция", "производная", "интеграл", "корень",
                       "косинус", "синус", "логарифм", "математика"],
        "химия": ["реакция", "молекула", "атом", "кислота", "щелочь", "химия",
                  "вещество", "раствор", "водород", "кислород"]
    }
    
    for subject, keywords in subjects.items():
        if any(keyword in text for keyword in keywords):
            return subject
    
    return "общий"

# Команда /start
@bot.message_handler(commands=['start'])
def start(message):
    welcome = """
👋 Привет! Я бот-помощник на YandexGPT.

📚 Решаю задачи по:
• Физике ⚡
• Биологии 🧬
• Математике 📐
• Химии ⚗️

📝 Просто отправь условие задачи!
    """
    bot.reply_to(message, welcome)

# Команда /help
@bot.message_handler(commands=['help'])
def help(message):
    help_text = """
🔍 **Как пользоваться:**
1. Отправь текст задачи
2. Я определю предмет автоматически
3. Получишь подробное решение

📌 **Примеры:**
• "Найди силу тока при 220В и 50 Ом"
• "Что такое фотосинтез?"
• "Реши 2x + 5 = 15"

💡 **Можно указать предмет:**
[физика] твоя задача
[биология] твой вопрос
    """
    bot.reply_to(message, help_text, parse_mode="Markdown")

# Обработка всех сообщений
@bot.message_handler(func=lambda message: True)
def handle_message(message):
    user_text = message.text
    
    # Отправляем "печатает..."
    bot.send_chat_action(message.chat.id, 'typing')
    
    # Проверяем, указан ли предмет в скобках
    subject = "общий"
    clean_text = user_text
    
    if user_text.startswith('[') and ']' in user_text:
        end = user_text.find(']')
        subject_candidate = user_text[1:end].strip().lower()
        if subject_candidate in PROMPTS:
            subject = subject_candidate
            clean_text = user_text[end+1:].strip()
    else:
        # Определяем предмет автоматически
        subject = detect_subject(user_text)
    
    # Сообщение о начале решения
    status_msg = bot.send_message(
        message.chat.id, 
        f"🤔 Решаю задачу по {subject}..."
    )
    
    # Получаем ответ от YandexGPT
    answer = ask_yandex(clean_text, subject)
    
    # Отправляем ответ
    try:
        bot.edit_message_text(
            f"✅ **Решение:**\n\n{answer}",
            message.chat.id,
            status_msg.message_id,
            parse_mode="Markdown"
        )
    except:
        # Если Markdown ломается, отправляем без форматирования
        bot.edit_message_text(
            f"✅ Решение:\n\n{answer}",
            message.chat.id,
            status_msg.message_id
        )

# Запуск бота
if __name__ == "__main__":
    print("🚀 Бот запущен...")
    print(f"📁 Folder ID: {FOLDER_ID}")
    print(f"🔑 API Key: {API_KEY[:10]}...")
    print("🤖 Жду сообщения...")
    
    while True:
        try:
            bot.polling(none_stop=True, interval=0)
        except Exception as e:
            print(f"❌ Ошибка: {e}")
            time.sleep(3)
