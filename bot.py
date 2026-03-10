import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import requests
import json
import time
import logging
import re
import base64
from datetime import datetime

# ======== ТВОИ ДАННЫЕ ========
TELEGRAM_TOKEN = "8451168327:AAGQffadqqBg3pZNQnjctVxH-dUgXsovTr4"
FOLDER_ID = "b1g0s9bjamjqrvas5pqr"  # из шага 1
API_KEY = "AQVNxnq1d97ei8asrSCgEdGN92cXym_faQZ8I3dp"      # из шага 3
# =============================

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

bot = telebot.TeleBot(TELEGRAM_TOKEN)
bot.set_my_commands([
    telebot.types.BotCommand("/start", "🚀 Запустить бота"),
    telebot.types.BotCommand("/help", "📖 Помощь"),
    telebot.types.BotCommand("/subjects", "📚 Выбрать предмет"),
    telebot.types.BotCommand("/vpn", "🔒 ProrabVPN"),
])

# URL для API Яндекса
YANDEX_GPT_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
YANDEX_VISION_URL = "https://vision.api.cloud.yandex.net/vision/v1/batchAnalyze"

# Промпты для разных предметов
PROMPTS = {
    "physics": "Ты — профессор физики. Объясняй законы физики, используй формулы. Решай задачи подробно и понятно. Пиши без LaTeX разметки, используй обычный текст.",
    "biology": "Ты — учитель биологии. Объясняй процессы простым языком, используй аналогии из жизни. Пиши понятно и интересно.",
    "math": "Ты — репетитор по математике. Показывай пошаговое решение, объясняй каждый шаг. Используй обычные символы, без LaTeX.",
    "chemistry": "Ты — учитель химии. Объясняй реакции, используй уравнения. Пиши формулы в виде H2O, CO2.",
    "general": "Ты — умный помощник. Решаешь задачи по любым предметам. Будь дружелюбным и понятным. Отвечай без LaTeX разметки."
}

# Названия предметов для кнопок
SUBJECT_NAMES = {
    "physics": "🔮 Физика",
    "biology": "🧬 Биология", 
    "math": "📐 Математика",
    "chemistry": "⚗️ Химия",
    "general": "📚 Любой предмет"
}

# Хранилище выбранных предметов для пользователей
user_subjects = {}

# ========== КЛАВИАТУРЫ ==========

def create_main_keyboard():
    """Главная клавиатура"""
    keyboard = InlineKeyboardMarkup(row_width=2)
    buttons = [
        InlineKeyboardButton("🔮 Физика", callback_data="subj_physics"),
        InlineKeyboardButton("🧬 Биология", callback_data="subj_biology"),
        InlineKeyboardButton("📐 Математика", callback_data="subj_math"),
        InlineKeyboardButton("⚗️ Химия", callback_data="subj_chemistry"),
        InlineKeyboardButton("📚 Любой предмет", callback_data="subj_general"),
        InlineKeyboardButton("📸 Фото задачи", callback_data="photo_help"),
        InlineKeyboardButton("🔒 ProrabVPN", callback_data="vpn"),
        InlineKeyboardButton("📖 Помощь", callback_data="help"),
        InlineKeyboardButton("📢 Поделиться", callback_data="share")
    ]
    keyboard.add(*buttons)
    return keyboard

def create_subject_keyboard():
    """Клавиатура выбора предмета"""
    keyboard = InlineKeyboardMarkup(row_width=2)
    buttons = [
        InlineKeyboardButton("🔮 Физика", callback_data="subj_physics"),
        InlineKeyboardButton("🧬 Биология", callback_data="subj_biology"),
        InlineKeyboardButton("📐 Математика", callback_data="subj_math"),
        InlineKeyboardButton("⚗️ Химия", callback_data="subj_chemistry"),
        InlineKeyboardButton("📚 Любой предмет", callback_data="subj_general"),
        InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")
    ]
    keyboard.add(*buttons)
    return keyboard

def create_after_answer_keyboard():
    """Клавиатура после ответа"""
    keyboard = InlineKeyboardMarkup(row_width=2)
    buttons = [
        InlineKeyboardButton("🔄 Новая задача", callback_data="new_task"),
        InlineKeyboardButton("📚 Выбрать предмет", callback_data="show_subjects"),
        InlineKeyboardButton("🔒 ProrabVPN", callback_data="vpn"),
        InlineKeyboardButton("📖 Помощь", callback_data="help")
    ]
    keyboard.add(*buttons)
    return keyboard

# ========== ФУНКЦИИ ДЛЯ РАБОТЫ С API ==========

def ask_yandex_gpt(question, subject="general"):
    """Отправляет запрос в YandexGPT"""
    
    system_prompt = PROMPTS.get(subject, PROMPTS["general"])
    system_prompt += " НЕ ИСПОЛЬЗУЙ LaTeX разметку, доллары, обратные слэши. Пиши обычным текстом."
    
    data = {
        "modelUri": f"gpt://{FOLDER_ID}/yandexgpt-lite",
        "completionOptions": {
            "stream": False,
            "temperature": 0.3,
            "maxTokens": "2000"
        },
        "messages": [
            {"role": "system", "text": system_prompt},
            {"role": "user", "text": question}
        ]
    }
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Api-Key {API_KEY}"
    }
    
    try:
        response = requests.post(YANDEX_GPT_URL, headers=headers, json=data, timeout=30)
        
        if response.status_code == 200:
            result = response.json()
            return result['result']['alternatives'][0]['message']['text']
        else:
            return f"❌ Ошибка YandexGPT: {response.status_code}"
            
    except Exception as e:
        return f"❌ Ошибка: {str(e)}"

def ask_yandex_vision(photo_bytes):
    """Отправляет фото в Yandex Vision для распознавания текста"""
    
    # Конвертируем фото в base64
    encoded_image = base64.b64encode(photo_bytes).decode('utf-8')
    
    data = {
        "folderId": FOLDER_ID,
        "analyzeSpecs": [{
            "content": encoded_image,
            "features": [{
                "type": "TEXT_DETECTION",
                "textDetectionConfig": {"languageCodes": ["ru", "en"]}
            }]
        }]
    }
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Api-Key {API_KEY}"
    }
    
    try:
        response = requests.post(YANDEX_VISION_URL, headers=headers, json=data, timeout=30)
        
        if response.status_code == 200:
            result = response.json()
            
            try:
                blocks = result['results'][0]['results'][0]['textDetection']['pages'][0]['blocks']
                text_parts = []
                
                for block in blocks:
                    for line in block['lines']:
                        for word in line['words']:
                            text_parts.append(word['text'])
                
                recognized_text = ' '.join(text_parts)
                return recognized_text if recognized_text else "❌ Текст на фото не найден"
                
            except (KeyError, IndexError):
                return "❌ Не удалось распознать текст на фото"
        else:
            return f"❌ Ошибка Vision API: {response.status_code}"
            
    except Exception as e:
        return f"❌ Ошибка при распознавании: {str(e)}"

def format_answer(text):
    """Делает ответ красивым"""
    
    # Убираем LaTeX
    text = text.replace('$$', '').replace('$', '')
    text = text.replace('\\', '').replace('{', '').replace('}', '')
    text = text.replace('frac', '').replace('cdot', '·')
    text = text.replace('text', '').replace('displaystyle', '')
    
    # Делаем степени красивыми
    text = re.sub(r'\^2', '²', text)
    text = re.sub(r'\^3', '³', text)
    text = re.sub(r'\^(\d+)', r'^\1', text)
    
    # Убираем лишние пробелы
    text = re.sub(r'\s+', ' ', text)
    
    # Разбиваем на строки
    lines = text.split('. ')
    formatted_lines = []
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
            
        if '=' in line and any(c.isdigit() for c in line):
            line = line.replace('=', ' = ')
            formatted_lines.append(f"🔹 {line}.")
        elif 'ответ' in line.lower():
            formatted_lines.append(f"\n✅ {line}.")
        elif 'дано' in line.lower() or 'найти' in line.lower():
            formatted_lines.append(f"\n📌 {line}.")
        elif any(unit in line for unit in ['км/ч', 'м/с', 'кг', 'Дж', 'Н', 'В', 'А', 'Ом']):
            formatted_lines.append(f"⚡ {line}.")
        else:
            if line and line[0].isalpha():
                line = line[0].upper() + line[1:]
            formatted_lines.append(f"   {line}.")
    
    result = '\n'.join(formatted_lines)
    
    # Реклама VPN
    result += "\n\n━━━━━━━━━━━━━━━━━━━━━\n"
    result += "🚀 Решено с помощью @ProrabVPN_bot\n"
    result += "🔒 Всего 200₽/мес"
    
    return result

# ========== ОБРАБОТЧИКИ КОМАНД ==========

@bot.message_handler(commands=['start'])
def start(message):
    """Старт"""
    welcome_text = """
🚀 <b>Добро пожаловать в SolverBot!</b>

┏━━━━━━━━━━━━━━━━━━━━━┓
┃ 🔮 Физика           ┃
┃ 🧬 Биология         ┃
┃ 📐 Математика       ┃
┃ ⚗️ Химия            ┃
┃ 📸 Фото задачи      ┃
┗━━━━━━━━━━━━━━━━━━━━━┛

📌 Отправь задачу или выбери предмет!

🔒 Наш партнер: @ProrabVPN_bot - 200₽/мес
    """
    
    bot.send_message(
        message.chat.id,
        welcome_text,
        parse_mode="HTML",
        reply_markup=create_main_keyboard()
    )

@bot.message_handler(commands=['help'])
def help(message):
    """Помощь"""
    help_text = """
📖 <b>Помощь</b>

📝 <b>Текстом:</b>
• Просто отправь условие
• Я определю предмет сам

📸 <b>Фото:</b>
• Сфоткай задачу
• Отправь фото
• Я распознаю и решу

🎯 <b>Примеры:</b>
• Сила тока при 220В и 50 Ом
• Что такое фотосинтез?
• Реши 2x + 5 = 15

🔒 <b>VPN:</b> @ProrabVPN_bot - 200₽/мес
    """
    
    bot.send_message(message.chat.id, help_text, parse_mode="HTML")

@bot.message_handler(commands=['vpn'])
def vpn(message):
    """VPN"""
    vpn_text = """
🔒 <b>ProrabVPN</b>

┏━━━━━━━━━━━━━━━━━━━━━┓
┃ ✅ Быстрый          ┃
┃ ✅ Безлимитный      ┃
┃ ✅ 20+ серверов    ┃
┃ ✅ Все сайты        ┃
┗━━━━━━━━━━━━━━━━━━━━━┛

💰 <b>200₽/мес</b>

🚀 <b>Переходи:</b> @ProrabVPN_bot
    """
    
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("🚀 Перейти", url="https://t.me/ProrabVPN_bot"))
    
    bot.send_message(message.chat.id, vpn_text, parse_mode="HTML", reply_markup=keyboard)

@bot.message_handler(commands=['subjects'])
def subjects(message):
    """Выбор предмета"""
    bot.send_message(
        message.chat.id,
        "📚 <b>Выбери предмет:</b>",
        parse_mode="HTML",
        reply_markup=create_subject_keyboard()
    )

@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    """Обработка фото"""
    user_id = message.from_user.id
    
    # Получаем фото
    file_id = message.photo[-1].file_id
    file_info = bot.get_file(file_id)
    downloaded_file = bot.download_file(file_info.file_path)
    
    # Статус
    status_msg = bot.send_message(
        message.chat.id,
        "🔍 <b>Распознаю текст...</b>",
        parse_mode="HTML"
    )
    
    # Распознаем
    recognized_text = ask_yandex_vision(downloaded_file)
    
    if recognized_text.startswith("❌"):
        bot.edit_message_text(
            f"{recognized_text}\n\n📝 Попробуй отправить текстом.",
            message.chat.id,
            status_msg.message_id
        )
        return
    
    # Показываем распознанный текст
    bot.edit_message_text(
        f"✅ <b>Текст:</b>\n\n{recognized_text}\n\n🤔 Решаю...",
        message.chat.id,
        status_msg.message_id,
        parse_mode="HTML"
    )
    
    # Получаем предмет
    subject = user_subjects.get(user_id, "general")
    subject_name = SUBJECT_NAMES.get(subject, "Любой предмет")
    
    # Решаем
    solving_msg = bot.send_message(
        message.chat.id,
        f"🤔 <b>Решаю</b> по {subject_name}...",
        parse_mode="HTML"
    )
    
    answer = ask_yandex_gpt(recognized_text, subject)
    formatted_answer = format_answer(answer)
    
    # Отправляем ответ
    try:
        bot.edit_message_text(
            f"✅ <b>Решение:</b>\n\n{formatted_answer}",
            message.chat.id,
            solving_msg.message_id,
            parse_mode="HTML",
            reply_markup=create_after_answer_keyboard()
        )
    except:
        bot.edit_message_text(
            f"✅ Решение:\n\n{answer}\n\n━━━━━━━━━━━━━━━━━━━━━\n🚀 @ProrabVPN_bot - 200₽/мес",
            message.chat.id,
            solving_msg.message_id,
            reply_markup=create_after_answer_keyboard()
        )

@bot.message_handler(func=lambda message: True)
def handle_text(message):
    """Обработка текста"""
    user_text = message.text
    user_id = message.from_user.id
    
    bot.send_chat_action(message.chat.id, 'typing')
    
    subject = user_subjects.get(user_id, "general")
    subject_name = SUBJECT_NAMES.get(subject, "Любой предмет")
    
    status_msg = bot.send_message(
        message.chat.id,
        f"🤔 <b>Решаю</b> по {subject_name}...",
        parse_mode="HTML"
    )
    
    answer = ask_yandex_gpt(user_text, subject)
    formatted_answer = format_answer(answer)
    
    try:
        bot.edit_message_text(
            f"✅ <b>Решение:</b>\n\n{formatted_answer}",
            message.chat.id,
            status_msg.message_id,
            parse_mode="HTML",
            reply_markup=create_after_answer_keyboard()
        )
    except:
        bot.edit_message_text(
            f"✅ Решение:\n\n{answer}\n\n━━━━━━━━━━━━━━━━━━━━━\n🚀 @ProrabVPN_bot - 200₽/мес",
            message.chat.id,
            status_msg.message_id,
            reply_markup=create_after_answer_keyboard()
        )

# ========== ОБРАБОТЧИК КНОПОК ==========

@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    """Обработка нажатий на кнопки"""
    
    if call.data == "vpn":
        text = """
🔒 <b>ProrabVPN</b>

┏━━━━━━━━━━━━━━━━━━━━━┓
┃ ✅ Быстрый          ┃
┃ ✅ Безлимитный      ┃
┃ ✅ 20+ серверов    ┃
┃ ✅ Все сайты        ┃
┗━━━━━━━━━━━━━━━━━━━━━┛

💰 <b>200₽/мес</b>

🚀 <b>Переходи:</b> @ProrabVPN_bot
        """
        
        keyboard = InlineKeyboardMarkup()
        keyboard.add(
            InlineKeyboardButton("🚀 Перейти", url="https://t.me/ProrabVPN_bot"),
            InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")
        )
        
        bot.edit_message_text(
            text,
            call.message.chat.id,
            call.message.message_id,
            parse_mode="HTML",
            reply_markup=keyboard
        )
    
    elif call.data == "help":
        text = """
📖 <b>Помощь</b>

📝 <b>Текстом:</b> просто отправь задачу
📸 <b>Фото:</b> сфоткай и отправь
📚 <b>Предметы:</b> физика, биология, математика, химия

🔒 <b>VPN:</b> @ProrabVPN_bot - 200₽/мес
        """
        
        keyboard = InlineKeyboardMarkup()
        keyboard.add(InlineKeyboardButton("🔙 Назад", callback_data="back_to_main"))
        
        bot.edit_message_text(
            text,
            call.message.chat.id,
            call.message.message_id,
            parse_mode="HTML",
            reply_markup=keyboard
        )
    
    elif call.data == "share":
        share_text = "🔮 Отличный бот для решения задач!\n\nРешает по физике, биологии, математике и химии.\n\n👉 @YourBotUsername"
        
        keyboard = InlineKeyboardMarkup()
        keyboard.add(
            InlineKeyboardButton("📤 Поделиться", switch_inline_query=share_text),
            InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")
        )
        
        bot.edit_message_text(
            "📢 <b>Поделись с друзьями!</b>",
            call.message.chat.id,
            call.message.message_id,
            parse_mode="HTML",
            reply_markup=keyboard
        )
    
    elif call.data == "photo_help":
        text = """
📸 <b>Отправь фото задачи!</b>

📌 <b>Советы:</b>
• Четкий текст
• Хорошее освещение
• Без бликов

✅ Я распознаю и решу!
        """
        
        bot.edit_message_text(
            text,
            call.message.chat.id,
            call.message.message_id,
            parse_mode="HTML",
            reply_markup=create_subject_keyboard()
        )
    
    elif call.data == "back_to_main":
        bot.edit_message_text(
            "🚀 <b>Главное меню</b>",
            call.message.chat.id,
            call.message.message_id,
            parse_mode="HTML",
            reply_markup=create_main_keyboard()
        )
    
    elif call.data == "show_subjects":
        bot.edit_message_text(
            "📚 <b>Выбери предмет:</b>",
            call.message.chat.id,
            call.message.message_id,
            parse_mode="HTML",
            reply_markup=create_subject_keyboard()
        )
    
    elif call.data == "new_task":
        bot.edit_message_text(
            "📝 Отправь новую задачу!",
            call.message.chat.id,
            call.message.message_id
        )
    
    elif call.data.startswith("subj_"):
        subject = call.data.replace("subj_", "")
        user_id = call.from_user.id
        user_subjects[user_id] = subject
        subject_name = SUBJECT_NAMES.get(subject, "Любой предмет")
        
        bot.edit_message_text(
            f"✅ <b>Выбран предмет:</b> {subject_name}\n\n📝 Теперь отправь задачу!",
            call.message.chat.id,
            call.message.message_id,
            parse_mode="HTML"
        )

# ========== ЗАПУСК ==========

if __name__ == "__main__":
    print("╔════════════════════════════════════╗")
    print("║     🚀 SOLVERBOT ЗАПУЩЕН!          ║")
    print("╠════════════════════════════════════╣")
    print(f"║ 📁 Folder ID: {FOLDER_ID[:15]}...   ║")
    print(f"║ 🔑 API Key: {API_KEY[:10]}...        ║")
    print("║ 📸 Фото: ✓                          ║")
    print("║ 🔒 VPN: @ProrabVPN_bot              ║")
    print("║ 💰 Цена: 200₽/мес                   ║")
    print("╚════════════════════════════════════╝")
    
    # Устанавливаем библиотеку pillow если нет
    try:
        from PIL import Image
        print("📸 Pillow установлен")
    except ImportError:
        print("📸 Устанавливаю Pillow...")
        import subprocess
        subprocess.check_call(['pip', 'install', 'pillow'])
        print("✅ Pillow установлен")
    
    while True:
        try:
            bot.polling(none_stop=True, interval=0)
        except Exception as e:
            print(f"❌ Ошибка: {e}")
            time.sleep(3)
