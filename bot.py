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

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

bot = telebot.TeleBot(TELEGRAM_TOKEN)
bot.set_my_commands([
    telebot.types.BotCommand("/start", "🚀 Запустить"),
    telebot.types.BotCommand("/help", "📖 Помощь"),
    telebot.types.BotCommand("/subjects", "📚 Предметы"),
    telebot.types.BotCommand("/vpn", "🔒 ProrabVPN"),
])

# URL
YANDEX_GPT_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
YANDEX_VISION_URL = "https://vision.api.cloud.yandex.net/vision/v1/batchAnalyze"

# ========== УСИЛЕННЫЕ ПРОМПТЫ ДЛЯ ВСЕХ ПРЕДМЕТОВ ==========

PROMPTS = {
    "physics": """Ты — профессор физики. Решай задачи ЧЁТКО и ПОНЯТНО.

ПРАВИЛА ОФОРМЛЕНИЯ:
1. НИКАКОГО LaTeX! Не используй \\[, \\], \\ (, \\), $, $$
2. Дроби пиши как ½ ¼ ¾ или a─b (используй символ дроби ─, например 3─4)
3. Корни пиши как √2, √3
4. Степени: м², м³, с⁻¹, кг·м/с²
5. Умножение обозначай ·
6. Единицы измерения пиши через пробел: 10 м/с, 5 кг
7. Формулы выделяй с новой строки
8. В конце обязательно "Ответ: ..."

Пример: F = m·a = 5 кг · 10 м/с² = 50 Н
Ответ: сила равна 50 Н""",

    "biology": """Ты — учитель биологии. Объясняй ПРОСТО и ПОНЯТНО.

ПРАВИЛА:
1. НИКАКОГО LaTeX!
2. Используй простые термины
3. Добавляй примеры из жизни
4. Структурируй ответ:
   - Что это?
   - Как работает?
   - Зачем нужно?
5. В конце краткий вывод

Пример: Фотосинтез — это процесс...""",

    "math": """Ты — профессор математики. Решай УРАВНЕНИЯ и ЗАДАЧИ ЧЁТКО.

ПРАВИЛА ОФОРМЛЕНИЯ:
1. НИКАКОГО LaTeX! Забудь про \\[, \\], \\ (, \\), $, $$ НАХУЙ!
2. Дроби ТОЛЬКО через ─ (символ дроби): 3─4, (x+1)─2, a─b
3. Корни через √: √2, √(x+1), √148
4. Степени: x², x³, xⁿ, a⁻¹
5. Умножение через · или ничего: 2·3, 4a
6. Дискриминант: D = b² - 4ac
7. Корни: x₁, x₂
8. Структура:
   - Дано (если есть)
   - Решение (по шагам)
   - Ответ

ПРИМЕР:
Дискриминант: D = 8² - 4·3·(-7) = 64 + 84 = 148
Корни: x₁ = (-8 + √148)─6 = (-4 + √37)─3
       x₂ = (-8 - √148)─6 = (-4 - √37)─3
Ответ: x₁ = (-4 + √37)─3, x₂ = (-4 - √37)─3""",

    "chemistry": """Ты — учитель химии. Решай задачи ЧЁТКО.

ПРАВИЛА:
1. НИКАКОГО LaTeX!
2. Формулы: H₂O, CO₂, CH₄ (используй нижние индексы)
3. Реакции: 2H₂ + O₂ → 2H₂O
4. Дроби: ½, ¼ или a─b
5. Моли, граммы, литры
6. Структура:
   - Уравнение реакции
   - Расчёты
   - Ответ""",

    "general": """Ты — умный помощник. Решай любые задачи.

ПРАВИЛА:
1. НИКАКОГО LaTeX!
2. Пиши ПРОСТО и ПОНЯТНО
3. Используй эмодзи для наглядности
4. Дроби через ─
5. Корни через √
6. Степени через ² ³
7. В конце всегда "Ответ: ..."""
}

SUBJECT_NAMES = {
    "physics": "🔮 Физика",
    "biology": "🧬 Биология", 
    "math": "📐 Математика",
    "chemistry": "⚗️ Химия",
    "general": "📚 Любой"
}

user_subjects = {}

# ========== КЛАВИАТУРЫ ==========

def create_main_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=2)
    buttons = [
        InlineKeyboardButton("🔮 Физика", callback_data="subj_physics"),
        InlineKeyboardButton("🧬 Биология", callback_data="subj_biology"),
        InlineKeyboardButton("📐 Математика", callback_data="subj_math"),
        InlineKeyboardButton("⚗️ Химия", callback_data="subj_chemistry"),
        InlineKeyboardButton("📚 Любой", callback_data="subj_general"),
        InlineKeyboardButton("📸 Фото", callback_data="photo_help"),
        InlineKeyboardButton("🔒 VPN", callback_data="vpn"),
        InlineKeyboardButton("📖 Помощь", callback_data="help"),
        InlineKeyboardButton("📢 Поделиться", callback_data="share")
    ]
    keyboard.add(*buttons)
    return keyboard

def create_subject_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=2)
    buttons = [
        InlineKeyboardButton("🔮 Физика", callback_data="subj_physics"),
        InlineKeyboardButton("🧬 Биология", callback_data="subj_biology"),
        InlineKeyboardButton("📐 Математика", callback_data="subj_math"),
        InlineKeyboardButton("⚗️ Химия", callback_data="subj_chemistry"),
        InlineKeyboardButton("📚 Любой", callback_data="subj_general"),
        InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")
    ]
    keyboard.add(*buttons)
    return keyboard

def create_after_answer_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=2)
    buttons = [
        InlineKeyboardButton("🔄 Новая", callback_data="new_task"),
        InlineKeyboardButton("📚 Предмет", callback_data="show_subjects"),
        InlineKeyboardButton("🔒 VPN", callback_data="vpn"),
        InlineKeyboardButton("📖 Помощь", callback_data="help")
    ]
    keyboard.add(*buttons)
    return keyboard

# ========== ФОРМАТИРОВАНИЕ С НАСТОЯЩИМИ ДРОБЯМИ ==========

def format_answer(text):
    """Пиздатое форматирование с дробями ─ и корнями √"""
    
    # 1. ВЫРЕЗАЕМ ВСЮ LaTeX ХУЙНЮ
    text = re.sub(r'\\\[.*?\\\]', '', text, flags=re.DOTALL)
    text = re.sub(r'\\\(.*?\\\)', '', text, flags=re.DOTALL)
    text = re.sub(r'\$\$.*?\$\$', '', text, flags=re.DOTALL)
    text = re.sub(r'\$.*?\$', '', text, flags=re.DOTALL)
    
    # 2. УБИРАЕМ СЛЭШИ И СКОБКИ
    text = text.replace('\\', '')
    text = text.replace('{', '').replace('}', '')
    
    # 3. КОРНИ: sqrt(148) -> √148
    text = re.sub(r'sqrt\((\d+)\)', r'√\1', text)
    text = re.sub(r'sqrt\(([^)]+)\)', r'√(\1)', text)
    
    # 4. ДРОБИ: / → ─ (САМОЕ ВАЖНОЕ!)
    # Заменяем дроби вида a/b на a─b
    text = re.sub(r'(\d+)\s*/\s*(\d+)', r'\1─\2', text)
    text = re.sub(r'\(([^)]+)\)\s*/\s*(\d+)', r'(\1)─\2', text)
    text = re.sub(r'(\d+)\s*/\s*\(([^)]+)\)', r'\1─(\2)', text)
    text = re.sub(r'\(([^)]+)\)\s*/\s*\(([^)]+)\)', r'(\1)─(\2)', text)
    
    # 5. СТЕПЕНИ: ^2 -> ²
    text = re.sub(r'\^2', '²', text)
    text = re.sub(r'\^3', '³', text)
    text = re.sub(r'\^(-?\d+)', r'⁻\1', text)  # отрицательные степени
    
    # 6. УМНОЖЕНИЕ
    text = text.replace('*', '·')
    
    # 7. РАЗБИВКА НА СТРОКИ
    lines = text.split('\n')
    formatted_lines = []
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        # Дискриминант и формулы
        if re.search(r'[Dd] =|дискриминант', line):
            formatted_lines.append(f"\n🔷 {line}")
        
        # Корни x₁, x₂
        elif re.search(r'x[₁₁2₂]|x[12]', line):
            line = line.replace('x1', 'x₁').replace('x2', 'x₂')
            formatted_lines.append(f"\n📌 {line}")
        
        # Ответ
        elif 'ответ' in line.lower():
            formatted_lines.append(f"\n✅ {line}")
        
        # Обычные формулы
        elif '=' in line and any(c.isdigit() for c in line):
            formatted_lines.append(f"   {line}")
        
        # Обычный текст
        else:
            if line and line[0].isalpha():
                formatted_lines.append(f"\n📝 {line}")
            else:
                formatted_lines.append(f"   {line}")
    
    result = '\n'.join(formatted_lines)
    result = re.sub(r'\n{3,}', '\n\n', result)
    
    # Реклама
    result += "\n\n━━━━━━━━━━━━━━━━━━━━━\n"
    result += "💬 Решено с помощью @ProrabVPN_bot\n"
    result += "💰 200₽/мес"
    
    return result

# ========== API ЗАПРОСЫ ==========

def ask_yandex_gpt(question, subject="general"):
    system_prompt = PROMPTS.get(subject, PROMPTS["general"])
    
    data = {
        "modelUri": f"gpt://{FOLDER_ID}/yandexgpt-lite",
        "completionOptions": {
            "stream": False,
            "temperature": 0.2,  # меньше креатива - больше точности
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
            return f"❌ Ошибка: {response.status_code}"
    except Exception as e:
        return f"❌ Ошибка: {str(e)}"

def ask_yandex_vision(photo_bytes):
    encoded = base64.b64encode(photo_bytes).decode('utf-8')
    data = {
        "folderId": FOLDER_ID,
        "analyzeSpecs": [{
            "content": encoded,
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
                text = ' '.join([word['text'] for block in blocks for line in block['lines'] for word in line['words']])
                return text if text else "❌ Текст не найден"
            except:
                return "❌ Ошибка распознавания"
        return f"❌ Ошибка Vision: {response.status_code}"
    except Exception as e:
        return f"❌ Ошибка: {str(e)}"

# ========== ОБРАБОТЧИКИ ==========

@bot.message_handler(commands=['start'])
def start(message):
    text = """
🚀 <b>SOLVERBOT - РЕШАЕТ ВСЁ!</b>

┏━━━━━━━━━━━━━━━━━━━━━┓
┃ 🔮 Физика          ┃
┃ 🧬 Биология        ┃
┃ 📐 Математика      ┃
┃ ⚗️ Химия           ┃
┃ 📸 Фото задач      ┃
┗━━━━━━━━━━━━━━━━━━━━━┛

📌 <b>Отправь задачу или выбери предмет!</b>

🔒 Партнер: @ProrabVPN_bot - 200₽/мес
    """
    bot.send_message(message.chat.id, text, parse_mode="HTML", reply_markup=create_main_keyboard())

@bot.message_handler(commands=['help'])
def help(message):
    text = """
📖 <b>ПОМОЩЬ</b>

📝 <b>Текст:</b> просто отправь задачу
📸 <b>Фото:</b> сфоткай и отправь
🎯 <b>Предметы:</b> физика, биология, математика, химия

📌 <b>Примеры:</b>
• 3x² + 8x - 7 = 0
• Сила тока при 220В и 50 Ом
• Что такое фотосинтез?

🔒 <b>VPN:</b> @ProrabVPN_bot - 200₽/мес
    """
    bot.send_message(message.chat.id, text, parse_mode="HTML")

@bot.message_handler(commands=['vpn'])
def vpn(message):
    text = """
🔒 <b>PRORABVPN</b>

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
    bot.send_message(message.chat.id, text, parse_mode="HTML", reply_markup=keyboard)

@bot.message_handler(commands=['subjects'])
def subjects(message):
    bot.send_message(message.chat.id, "📚 <b>Выбери предмет:</b>", parse_mode="HTML", reply_markup=create_subject_keyboard())

@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    user_id = message.from_user.id
    file_id = message.photo[-1].file_id
    file_info = bot.get_file(file_id)
    downloaded = bot.download_file(file_info.file_path)
    
    status = bot.send_message(message.chat.id, "🔍 <b>Распознаю...</b>", parse_mode="HTML")
    recognized = ask_yandex_vision(downloaded)
    
    if recognized.startswith("❌"):
        bot.edit_message_text(recognized, message.chat.id, status.message_id)
        return
    
    bot.edit_message_text(f"✅ <b>Текст:</b>\n{recognized}\n\n🤔 Решаю...", message.chat.id, status.message_id, parse_mode="HTML")
    
    subject = user_subjects.get(user_id, "general")
    solving = bot.send_message(message.chat.id, f"🤔 Решаю...", parse_mode="HTML")
    answer = ask_yandex_gpt(recognized, subject)
    formatted = format_answer(answer)
    
    try:
        bot.edit_message_text(f"✅ <b>Решение:</b>\n{formatted}", message.chat.id, solving.message_id, parse_mode="HTML", reply_markup=create_after_answer_keyboard())
    except:
        bot.edit_message_text(f"✅ Решение:\n{answer}\n\n━━━━━━━━━━━━━━━━━━━━━\n💬 @ProrabVPN_bot - 200₽/мес", message.chat.id, solving.message_id, reply_markup=create_after_answer_keyboard())

@bot.message_handler(func=lambda message: True)
def handle_text(message):
    user_text = message.text
    user_id = message.from_user.id
    
    bot.send_chat_action(message.chat.id, 'typing')
    subject = user_subjects.get(user_id, "general")
    
    status = bot.send_message(message.chat.id, f"🤔 <b>Решаю...</b>", parse_mode="HTML")
    answer = ask_yandex_gpt(user_text, subject)
    formatted = format_answer(answer)
    
    try:
        bot.edit_message_text(f"✅ <b>Решение:</b>\n{formatted}", message.chat.id, status.message_id, parse_mode="HTML", reply_markup=create_after_answer_keyboard())
    except:
        bot.edit_message_text(f"✅ Решение:\n{answer}\n\n━━━━━━━━━━━━━━━━━━━━━\n💬 @ProrabVPN_bot - 200₽/мес", message.chat.id, status.message_id, reply_markup=create_after_answer_keyboard())

# ========== КНОПКИ ==========

@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    if call.data == "vpn":
        text = """
🔒 <b>PRORABVPN</b>
┏━━━━━━━━━━━━━━━━━━━━━┓
┃ ✅ Быстрый          ┃
┃ ✅ Безлимитный      ┃
┃ ✅ 20+ серверов    ┃
┃ ✅ Все сайты        ┃
┗━━━━━━━━━━━━━━━━━━━━━┛
💰 <b>200₽/мес</b>
🚀 <b>@ProrabVPN_bot</b>
        """
        keyboard = InlineKeyboardMarkup()
        keyboard.add(InlineKeyboardButton("🚀 Перейти", url="https://t.me/ProrabVPN_bot"), InlineKeyboardButton("🔙 Назад", callback_data="back_to_main"))
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, parse_mode="HTML", reply_markup=keyboard)
    
    elif call.data == "help":
        text = "📖 <b>Помощь</b>\n\n📝 Текст: просто отправь\n📸 Фото: сфоткай и отправь\n📚 Физика, биология, математика, химия\n\n🔒 @ProrabVPN_bot - 200₽/мес"
        keyboard = InlineKeyboardMarkup()
        keyboard.add(InlineKeyboardButton("🔙 Назад", callback_data="back_to_main"))
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, parse_mode="HTML", reply_markup=keyboard)
    
    elif call.data == "share":
        share = "🔮 Отличный бот! Решает задачи по физике, биологии, математике и химии. 👉 @YourBotUsername"
        keyboard = InlineKeyboardMarkup()
        keyboard.add(InlineKeyboardButton("📤 Поделиться", switch_inline_query=share), InlineKeyboardButton("🔙 Назад", callback_data="back_to_main"))
        bot.edit_message_text("📢 <b>Поделись с друзьями!</b>", call.message.chat.id, call.message.message_id, parse_mode="HTML", reply_markup=keyboard)
    
    elif call.data == "photo_help":
        text = "📸 <b>Отправь фото задачи!</b>\n\n✅ Четкий текст\n✅ Хорошее освещение\n✅ Без бликов\n\nЯ распознаю и решу!"
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, parse_mode="HTML", reply_markup=create_subject_keyboard())
    
    elif call.data == "back_to_main":
        bot.edit_message_text("🚀 <b>Главное меню</b>", call.message.chat.id, call.message.message_id, parse_mode="HTML", reply_markup=create_main_keyboard())
    
    elif call.data == "show_subjects":
        bot.edit_message_text("📚 <b>Выбери предмет:</b>", call.message.chat.id, call.message.message_id, parse_mode="HTML", reply_markup=create_subject_keyboard())
    
    elif call.data == "new_task":
        bot.edit_message_text("📝 Отправь новую задачу!", call.message.chat.id, call.message.message_id)
    
    elif call.data.startswith("subj_"):
        subject = call.data.replace("subj_", "")
        user_subjects[call.from_user.id] = subject
        name = SUBJECT_NAMES.get(subject, "Любой")
        bot.edit_message_text(f"✅ <b>Выбран:</b> {name}\n\n📝 Теперь отправь задачу!", call.message.chat.id, call.message.message_id, parse_mode="HTML")

# ========== ЗАПУСК ==========

if __name__ == "__main__":
    print("╔════════════════════════════════════╗")
    print("║     🚀 SOLVERBOT ЗАПУЩЕН!          ║")
    print("╠════════════════════════════════════╣")
    print(f"║ 📁 Folder: {FOLDER_ID[:15]}...      ║")
    print("║ 🔢 Дроби: ─ (настоящие!)           ║")
    print("║ 📸 Фото: ✓                          ║")
    print("║ 🔒 VPN: @ProrabVPN_bot              ║")
    print("║ 💰 Цена: 200₽/мес                   ║")
    print("╚════════════════════════════════════╝")
    
    try:
        from PIL import Image
        print("📸 Pillow OK")
    except:
        print("📸 Устанавливаю Pillow...")
        import subprocess
        subprocess.check_call(['pip', 'install', 'pillow'])
    
    while True:
        try:
            bot.polling(none_stop=True, interval=0)
        except Exception as e:
            print(f"❌ Ошибка: {e}")
            time.sleep(3)
