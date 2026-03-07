# 📦 rss_bot.py - Бот для DineroLatam v4.0 (Зі зміною інтервалу)

import feedparser
import requests
from datetime import datetime, timedelta
import time
import re
import json
import os
import logging

# Налаштування логування
logging.basicConfig(
    filename='bot.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# 🔑 КОНФІГУРАЦІЯ (змінні середовища з Railway)
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHANNEL = os.environ.get('TELEGRAM_CHANNEL', '@dinerolatam')
ADMIN_USER_ID = int(os.environ.get('ADMIN_USER_ID', 0))

# Перевірка на старті
if not TELEGRAM_BOT_TOKEN:
    logging.error("❌ ПОМИЛКА: TELEGRAM_BOT_TOKEN не знайдено!")
    exit(1)

# 💾 Файли для збереження даних
SOURCES_FILE = "rss_sources.json"
STATS_FILE = "bot_stats.json"
INTERVAL_FILE = "bot_interval.json"

# 📊 СТАТИСТИКА
stats = {
    "posts_published": 0,
    "last_check": None,
    "start_time": datetime.now(),
    "errors": 0
}

# ⏱ ІНТЕРВАЛ (за замовчуванням 3 години = 10800 секунд)
check_interval = 10800  # 3 години в секундах

#  Відстеження опублікованих новин
published_urls = set()

# 💬 Стан діалогу з користувачем (для додавання джерел)
user_state = {}


# 📰 ЗАВАНТАЖЕННЯ СПИСКУ ДЖЕРЕЛ
def load_sources():
    """Завантаження RSS джерел з файлу"""
    default_sources = [
        "https://www.expansion.com/rss/economia.xml",
        "https://feeds.elpais.com/mrss-s/pages/ep/site/elpais.com/section/economia/portada",
        "https://www.elconfidencial.com/rss/economia.xml",
        "https://www.elfinanciero.com.mx/rss/",
        "https://www.portafolio.co/rss",
    ]
    
    try:
        if os.path.exists(SOURCES_FILE):
            with open(SOURCES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                sources = data.get("sources", default_sources)
                logging.info(f"✅ Завантажено {len(sources)} джерел з файлу")
                return sources
    except Exception as e:
        logging.error(f"⚠️ Помилка завантаження джерел: {e}")
    
    return default_sources


def save_sources(sources):
    """Збереження RSS джерел у файл"""
    try:
        with open(SOURCES_FILE, "w", encoding="utf-8") as f:
            json.dump({"sources": sources}, f, indent=2, ensure_ascii=False)
        logging.info(f"💾 Збережено {len(sources)} джерел")
        return True
    except Exception as e:
        logging.error(f"❌ Помилка збереження джерел: {e}")
        return False


# ⏱ ФУНКЦІЇ ДЛЯ ІНТЕРВАЛУ
def load_interval():
    """Завантаження інтервалу з файлу"""
    global check_interval
    try:
        if os.path.exists(INTERVAL_FILE):
            with open(INTERVAL_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                check_interval = data.get("interval", 10800)
                logging.info(f"⏱ Завантажено інтервал: {check_interval // 3600} годин")
                return check_interval
    except Exception as e:
        logging.error(f"⚠️ Помилка завантаження інтервалу: {e}")
    
    check_interval = 10800  # За замовчуванням 3 години
    return check_interval


def save_interval(interval):
    """Збереження інтервалу у файл"""
    try:
        with open(INTERVAL_FILE, "w", encoding="utf-8") as f:
            json.dump({"interval": interval}, f, indent=2)
        logging.info(f"💾 Збережено інтервал: {interval // 3600} годин")
        return True
    except Exception as e:
        logging.error(f"❌ Помилка збереження інтервалу: {e}")
        return False


def validate_rss_url(url):
    """Перевірка чи URL є валідним RSS feed"""
    try:
        logging.info(f"🔍 Перевірка URL: {url}")
        feed = feedparser.parse(url)
        
        # Перевірка чи є entries (новини)
        if len(feed.entries) == 0:
            return False, "❌ RSS feed не містить новин. Перевірте посилання."
        
        # Перевірка чи є заголовки
        if not feed.feed.get('title'):
            return False, "❌ RSS feed не має заголовка. Можливо це не валідний RSS."
        
        # Перевірка першої новини
        first_entry = feed.entries[0]
        if not first_entry.get('title') and not first_entry.get('link'):
            return False, "❌ Новини в RSS не мають заголовків або посилань."
        
        # Все добре!
        feed_title = feed.feed.get('title', 'Без назви')
        entries_count = len(feed.entries)
        
        return True, f"✅ Валідний RSS!\n📰 Назва: {feed_title}\n📊 Новин у feed: {entries_count}"
    
    except Exception as e:
        return False, f"❌ Помилка перевірки: {str(e)}"


def get_source_name_from_url(url):
    """Отримання назви джерела з URL або RSS feed"""
    try:
        feed = feedparser.parse(url)
        if feed.feed.get('title'):
            return feed.feed.get('title')
    except:
        pass
    
    # Резервний варіант - з URL
    if 'expansion' in url:
        return "Expansión"
    elif 'elpais' in url:
        return "El País"
    elif 'elconfidencial' in url:
        return "El Confidencial"
    elif 'elfinanciero' in url:
        return "El Financiero"
    elif 'portafolio' in url:
        return "Portafolio"
    elif 'infobae' in url:
        return "Infobae"
    elif 'ambito' in url:
        return "Ámbito"
    elif 'bloomberg' in url:
        return "Bloomberg"
    else:
        return "Fuente desconocida"


# 🎯 Функції для емодзі та хештегів
def get_emoji_for_news(title, description):
    text = f"{title} {description}".lower()
    
    if any(word in text for word in ["dólar", "euro", "peso", "moneda", "divisa"]):
        return "💵"
    elif any(word in text for word in ["bolsa", "acciones", "inversión", "mercado"]):
        return "📈"
    elif any(word in text for word in ["inflación", "ipc", "precios", "economía"]):
        return "📊"
    elif any(word in text for word in ["banco", "crédito", "préstamo", "hipoteca"]):
        return "🏦"
    elif any(word in text for word in ["petróleo", "energía", "gas", "combustible"]):
        return "⛽"
    elif any(word in text for word in ["bitcoin", "cripto", "ethereum", "crypto"]):
        return "₿"
    elif any(word in text for word in ["vivienda", "inmobiliaria", "alquiler"]):
        return "🏠"
    elif any(word in text for word in ["impuestos", "hacienda", "salario"]):
        return "💰"
    elif any(word in text for word in ["fintech", "tecnología", "digital"]):
        return "📱"
    elif any(word in text for word in ["empresa", "compañía", "negocio"]):
        return "🏢"
    else:
        return "📰"


def get_hashtags_for_news(title, description):
    text = f"{title} {description}".lower()
    hashtags = ["#Economía"]
    
    if any(word in text for word in ["dólar", "euro", "peso", "moneda"]):
        hashtags.extend(["#Dólar", "#Divisas", "#LatAm"])
    if any(word in text for word in ["inflación", "ipc", "precios"]):
        hashtags.extend(["#Inflación", "#Precios"])
    if any(word in text for word in ["bolsa", "acciones", "inversión"]):
        hashtags.extend(["#Bolsa", "#Inversiones"])
    if any(word in text for word in ["banco", "crédito", "préstamo"]):
        hashtags.extend(["#Banca", "#Créditos"])
    if any(word in text for word in ["petróleo", "energía", "gas"]):
        hashtags.extend(["#Energía", "#Petróleo"])
    if any(word in text for word in ["bitcoin", "cripto", "ethereum"]):
        hashtags.extend(["#Cripto", "#Bitcoin"])
    if "méxico" in text:
        hashtags.append("#México")
    elif "colombia" in text:
        hashtags.append("#Colombia")
    elif "argentina" in text:
        hashtags.append("#Argentina")
    elif "chile" in text:
        hashtags.append("#Chile")
    elif "españa" in text:
        hashtags.append("#España")
    
    return " ".join(hashtags[:6])


def is_financial_news(title, description):
    text = f"{title} {description}".lower()
    return any(keyword in text for keyword in [
        "economía", "finanzas", "inversión", "bolsa", "mercado",
        "dólar", "peso", "inflación", "tasas", "banco central",
        "cripto", "bitcoin", "acciones", "ahorro", "crédito"
    ])


def extract_image_url(entry):
    if hasattr(entry, 'media_content'):
        for media in entry.media_content:
            if media.get('medium') == 'image' or media.get('type', '').startswith('image'):
                return media.get('url')
    
    if hasattr(entry, 'enclosures'):
        for enclosure in entry.enclosures:
            if hasattr(enclosure, 'type') and enclosure.type.startswith('image'):
                return enclosure.href
    
    if hasattr(entry, 'description'):
        match = re.search(r'<img[^>]+src="([^">]+)"', entry.description)
        if match:
            return match.group(1)
    
    if hasattr(entry, 'image'):
        return entry.image.get('href')
    
    return None


def format_post(title, link, description, source_name):
    emoji = get_emoji_for_news(title, description)
    hashtags = get_hashtags_for_news(title, description)
    
    clean_desc = re.sub(r'<[^>]+>', '', description)
    if len(clean_desc) > 350:
        clean_desc = clean_desc[:350] + "..."
    
    post = f"{emoji} *{title}*\n\n"
    post += f"{clean_desc}\n\n"
    post += f"🔗 Fuente: [{source_name}]({link}) (hacer clic)\n\n"
    post += f"{hashtags}"
    
    return post


def send_to_telegram(message, image_url=None, chat_id=None, parse_mode='Markdown'):
    """Надсилання в Telegram"""
    
    if chat_id is None:
        chat_id = TELEGRAM_CHANNEL
    
    if image_url and chat_id == TELEGRAM_CHANNEL:
        photo_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
        photo_data = {
            'chat_id': chat_id,
            'photo': image_url,
            'caption': message,
            'parse_mode': parse_mode
        }
        try:
            response = requests.post(photo_url, json=photo_data, timeout=10)
            return response.json()
        except Exception as e:
            logging.error(f"Error sending photo: {e}")
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {
        'chat_id': chat_id,
        'text': message,
        'parse_mode': parse_mode
    }
    
    try:
        response = requests.post(url, json=data, timeout=10)
        return response.json()
    except Exception as e:
        logging.error(f"Error sending to Telegram: {e}")
        return None


def check_for_updates():
    """Перевірка новин та публікація"""
    global stats
    
    sources = load_sources()
    posts_count = 0
    
    for feed_url in sources:
        entries = fetch_rss_feed(feed_url)
        source_name = get_source_name_from_url(feed_url)
        
        for entry in entries[:5]:
            title = entry.get('title', '')
            link = entry.get('link', '')
            description = entry.get('description', '')
            
            if link in published_urls:
                continue
            
            if not is_financial_news(title, description):
                continue
            
            message = format_post(title, link, description, source_name)
            image_url = extract_image_url(entry)
            
            result = send_to_telegram(message, image_url)
            
            if result and result.get('ok'):
                published_urls.add(link)
                posts_count += 1
                stats["posts_published"] += 1
            else:
                stats["errors"] += 1
            
            time.sleep(2)
    
    stats["last_check"] = datetime.now()
    save_stats()
    
    return posts_count


def fetch_rss_feed(feed_url):
    """Отримання новин з RSS з повторними спробами"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/rss+xml, application/xml, text/xml, */*',
        'Connection': 'keep-alive',
    }
    
    # 3 спроби з паузою
    for attempt in range(3):
        try:
            logging.info(f"🔄 Спроба {attempt + 1}/3: {feed_url[:50]}...")
            
            response = requests.get(feed_url, headers=headers, timeout=15)
            
            if response.status_code == 200:
                feed = feedparser.parse(response.text)
                if len(feed.entries) > 0:
                    logging.info(f"✅ Успішно: {len(feed.entries)} новин")
                    return feed.entries
                else:
                    logging.warning(f"⚠️ RSS пустий")
                    return []
            else:
                logging.warning(f"⚠️ Статус {response.status_code}")
                
        except Exception as e:
            logging.error(f"❌ Помилка спроби {attempt + 1}: {str(e)[:80]}")
            if attempt < 2:  # Не спати після останньої спроби
                time.sleep(5)  # Пауза 5 секунд перед наступною спробою
    
    logging.error(f"❌ Не вдалося після 3 спроб: {feed_url[:50]}...")
    return []


def save_stats():
    try:
        with open(STATS_FILE, "w") as f:
            json.dump({
                "posts_published": stats["posts_published"],
                "last_check": str(stats["last_check"]),
                "start_time": str(stats["start_time"]),
                "errors": stats["errors"],
                "published_urls_count": len(published_urls)
            }, f)
    except Exception as e:
        logging.error(f"Error saving stats: {e}")


def load_stats():
    global stats, published_urls
    try:
        if os.path.exists(STATS_FILE):
            with open(STATS_FILE, "r") as f:
                data = json.load(f)
                stats["posts_published"] = data.get("posts_published", 0)
                stats["errors"] = data.get("errors", 0)
    except Exception as e:
        logging.error(f"Error loading stats: {e}")


# 🎮 ОБРОБКА КОМАНД TELEGRAM
def handle_commands():
    """Перевірка нових повідомлень з командами"""
    global check_interval
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    params = {
        'timeout': 1,
        'offset': handle_commands.last_update_id + 1 if hasattr(handle_commands, 'last_update_id') else 0
    }
    
    try:
        response = requests.get(url, params=params, timeout=2)
        updates = response.json().get('result', [])
        
        for update in updates:
            handle_commands.last_update_id = update['update_id']
            
            message = update.get('message', {})
            chat_id = message.get('chat', {}).get('id')
            text = message.get('text', '')
            user_id = message.get('from', {}).get('id')
            
            # Перевірка, що це адміністратор
            if user_id != ADMIN_USER_ID:
                continue
            
            # 📝 Обробка станів діалогу (очікування URL для додавання)
            if chat_id in user_state:
                state = user_state[chat_id]
                
                if state.get('action') == 'add_source':
                    # Користувач надіслав URL для додавання
                    url_to_add = text.strip()
                    
                    # Перевірка валідності
                    send_to_telegram("🔍 Перевіряю RSS feed... Зачекайте...", chat_id=chat_id)
                    
                    is_valid, message = validate_rss_url(url_to_add)
                    
                    if is_valid:
                        # Додавання джерела
                        sources = load_sources()
                        
                        if url_to_add in sources:
                            send_to_telegram("⚠️ Це джерело вже додано!", chat_id=chat_id)
                        else:
                            sources.append(url_to_add)
                            if save_sources(sources):
                                source_name = get_source_name_from_url(url_to_add)
                                send_to_telegram(
                                    f"✅ *Джерело успішно додано!*\n\n"
                                    f"📰 {message}\n"
                                    f"🔗 URL: `{url_to_add}`\n"
                                    f"📊 Всього джерел: {len(sources)}\n\n"
                                    f"Бот почне перевіряти це джерело при наступному циклі.",
                                    chat_id=chat_id
                                )
                            else:
                                send_to_telegram("❌ Помилка збереження джерела!", chat_id=chat_id)
                    else:
                        # URL не валідний
                        send_to_telegram(
                            f"{message}\n\n"
                            f"🔧 *Що робити:*\n"
                            f"1. Перевірте чи посилання відкривається в браузері\n"
                            f"2. Переконайтеся, що це RSS feed (має закінчуватись на .xml або /feed/)\n"
                            f"3. Надішліть правильне посилання ще раз\n\n"
                            f"Або введіть /cancel щоб скасувати",
                            chat_id=chat_id,
                            parse_mode='Markdown'
                        )
                        # Залишаємо стан, щоб користувач міг надіслати інший URL
                    
                    # Очищення стану тільки якщо успішно або скасовано
                    if is_valid or text.lower() == '/cancel':
                        if chat_id in user_state:
                            del user_state[chat_id]
                    
                    continue
            
            # 🎯 Обробка команд
            if text == '/start':
                sources = load_sources()
                msg = f"""
👋 *Привіт! Це DineroLatam Bot v4.0*

🤖 Я автоматично публікую фінансові новини у канал {TELEGRAM_CHANNEL}

📊 *Статус:* Працюю
📰 *Джерел:* {len(sources)}
✅ *Опубліковано:* {stats['posts_published']}
⏱ *Інтервал:* {check_interval // 3600} годин

*Основні команди:*
/stats - Детальна статистика
/sources - Управління джерелами
/check - Примусова перевірка
/interval - Налаштування інтервалу
/help - Довідка
"""
                send_to_telegram(msg, chat_id=chat_id)
            
            elif text == '/stats':
                uptime = datetime.now() - stats['start_time']
                sources = load_sources()
                msg = f"""
📊 *Статистика бота*

📰 Опубліковано постів: {stats['posts_published']}
❌ Помилок: {stats['errors']}
⏱ Остання перевірка: {stats['last_check'].strftime('%Y-%m-%d %H:%M:%S') if stats['last_check'] else 'Ніколи'}
🕐 Час роботи: {str(uptime).split('.')[0]}
💾 Збережено новин: {len(published_urls)}
📡 Активних джерел: {len(sources)}
⏱ Інтервал перевірки: {check_interval // 3600} годин

Ефективність: {stats['posts_published'] / (stats['posts_published'] + stats['errors']) * 100:.1f}% успішних публікацій
"""
                send_to_telegram(msg, chat_id=chat_id)
            
            elif text == '/check':
                msg = "🔄 *Перевірка новин...*"
                send_to_telegram(msg, chat_id=chat_id)
                
                posts = check_for_updates()
                
                msg = f"✅ *Перевірка завершена!*\n\n📰 Знайдено та опубліковано: {posts} нових постів"
                send_to_telegram(msg, chat_id=chat_id)
            
            elif text == '/sources':
                sources = load_sources()
                sources_list = ""
                for i, url in enumerate(sources, 1):
                    name = get_source_name_from_url(url)
                    sources_list += f"{i}. {name}\n   `{url}`\n\n"
                
                msg = f"""
📰 *RSS джерела ({len(sources)}):*

{sources_list}

*Управління джерелами:*
/addsource - Додати нове джерело
/removesource - Видалити джерело
"""
                send_to_telegram(msg, chat_id=chat_id, parse_mode='Markdown')
            
            elif text == '/addsource':
                # Початок процесу додавання
                user_state[chat_id] = {'action': 'add_source'}
                
                msg = """
➕ *Додавання нового RSS джерела*

📝 Надішліть URL RSS feed:

*Приклади:*
• `https://www.expansion.com/rss/economia.xml`
• `https://example.com/feed/`
• `https://example.com/rss.xml`

⚠️ *Важливо:*
• URL має бути публічним
• Має бути валідним RSS feed
• Я перевірю посилання перед додаванням

/cancel - Скасувати
"""
                send_to_telegram(msg, chat_id=chat_id, parse_mode='Markdown')
            
            elif text == '/removesource':
                sources = load_sources()
                if not sources:
                    send_to_telegram("❌ Немає джерел для видалення!", chat_id=chat_id)
                else:
                    # Показати список з номерами
                    sources_list = ""
                    for i, url in enumerate(sources, 1):
                        name = get_source_name_from_url(url)
                        sources_list += f"{i}. {name}\n"
                    
                    msg = f"""
🗑️ *Видалення джерела*

Відправте *номер* джерела для видалення:

{sources_list}

Або /cancel щоб скасувати
"""
                    user_state[chat_id] = {'action': 'remove_source', 'sources': sources}
                    send_to_telegram(msg, chat_id=chat_id, parse_mode='Markdown')
            
            elif text == '/getinterval':
                hours = check_interval // 3600
                minutes = (check_interval % 3600) // 60
                msg = f"""
⏱ *Поточний інтервал перевірки*

🕐 Інтервал: {hours} годин {minutes} хвилин
📊 Це означає, що бот перевіряє новини кожні {hours} годин

*Щоб змінити:*
Використовуйте команду /setinterval [години]

*Приклади:*
/setinterval 1 - кожну годину
/setinterval 2 - кожні 2 години
/setinterval 6 - кожні 6 годин

⚠️ *Обмеження:*
• Мінімум: 0.5 години (30 хвилин)
• Максимум: 24 години
"""
                send_to_telegram(msg, chat_id=chat_id)
            
            elif text.startswith('/setinterval'):
                try:
                    # Отримуємо значення з команди
                    parts = text.split()
                    if len(parts) < 2:
                        send_to_telegram(
                            "❌ *Помилка: вкажіть інтервал!*\n\n"
                            "Використовуйте: /setinterval [години]\n\n"
                            "Приклади:\n"
                            "/setinterval 1 - кожну годину\n"
                            "/setinterval 3 - кожні 3 години\n"
                            "/setinterval 0.5 - кожні 30 хвилин",
                            chat_id=chat_id
                        )
                    else:
                        new_interval_hours = float(parts[1])
                        
                        # Перевірка на допустимі значення
                        if new_interval_hours < 0.5:
                            send_to_telegram(
                                "❌ *Занадто малий інтервал!*\n\n"
                                "⚠️ Мінімум: 0.5 години (30 хвилин)\n\n"
                                "Використовуйте: /setinterval [години]",
                                chat_id=chat_id
                            )
                        elif new_interval_hours > 24:
                            send_to_telegram(
                                "❌ *Занадто великий інтервал!*\n\n"
                                "⚠️ Максимум: 24 години\n\n"
                                "Використовуйте: /setinterval [години]",
                                chat_id=chat_id
                            )
                        else:
                            # Конвертація в секунди
                            new_interval_seconds = int(new_interval_hours * 3600)
                            check_interval = new_interval_seconds
                            save_interval(check_interval)
                            
                            hours = check_interval // 3600
                            minutes = (check_interval % 3600) // 60
                            
                            send_to_telegram(
                                f"✅ *Інтервал змінено!*\n\n"
                                f"🕐 Новий інтервал: {hours} годин {minutes} хвилин\n"
                                f"📊 Бот перевірятиме новини кожні {hours} годин\n\n"
                                f"⚠️ *Зміни набудуть чинності після наступного циклу*",
                                chat_id=chat_id
                            )
                except ValueError:
                    send_to_telegram(
                        "❌ *Помилка: введіть число!*\n\n"
                        "Використовуйте: /setinterval [години]\n\n"
                        "Приклади:\n"
                        "/setinterval 1\n"
                        "/setinterval 2.5\n"
                        "/setinterval 6",
                        chat_id=chat_id
                    )
            
            elif text == '/help':
                msg = """
❓ *Допомога - Всі команди*

*Основні:*
/start - Привітання
/stats - Статистика бота
/check - Примусова перевірка новин
/sources - Показати всі джерела

*Управління джерелами:*
/addsource - Додати нове RSS джерело
/removesource - Видалити джерело

*Налаштування інтервалу:*
/getinterval - Поточний інтервал
/setinterval [години] - Змінити інтервал

*Системні:*
/help - Ця довідка
/restart - Перезавантажити бота
/cancel - Скасувати поточну дію
/clearstats - Очистити статистику

⚙️ *Налаштування:*
• Інтервал: {0} годин
• Макс. новин: 5 за джерело
• Фільтр: тільки фінансові
""".format(check_interval // 3600)
                send_to_telegram(msg, chat_id=chat_id)
            
            elif text == '/clearstats':
                # Скидання статистики
                stats["posts_published"] = 0
                stats["errors"] = 0
                stats["start_time"] = datetime.now()
                published_urls.clear()
                save_stats()
                
                send_to_telegram(
                    "✅ *Статистику очищено!*\n\n"
                    "📊 Всі лічильники скинуто:\n"
                    "• Опубліковано постів: 0\n"
                    "• Помилок: 0\n"
                    "• Збережено новин: 0\n"
                    "• Час роботи: 0:00:00",
                    chat_id=chat_id
                )
            
            elif text == '/cancel':
                if chat_id in user_state:
                    del user_state[chat_id]
                    send_to_telegram("✅ Дія скасована", chat_id=chat_id)
                else:
                    send_to_telegram("ℹ️ Немає активних дій для скасування", chat_id=chat_id)
            
            elif text == '/restart':
                msg = "🔄 *Перезавантаження бота...*"
                send_to_telegram(msg, chat_id=chat_id)
                save_stats()
                save_sources(load_sources())
                import sys
                sys.exit(0)
            
            # 🗑️ Обробка видалення джерела (якщо надіслано номер)
            elif chat_id in user_state and user_state[chat_id].get('action') == 'remove_source':
                try:
                    number = int(text.strip())
                    sources = user_state[chat_id]['sources']
                    
                    if 1 <= number <= len(sources):
                        removed_url = sources.pop(number - 1)
                        if save_sources(sources):
                            send_to_telegram(
                                f"✅ *Джерело видалено!*\n\n"
                                f"🗑️ Видалено: {removed_url}\n"
                                f"📊 Залишилось джерел: {len(sources)}",
                                chat_id=chat_id
                            )
                        else:
                            send_to_telegram("❌ Помилка збереження!", chat_id=chat_id)
                    else:
                        send_to_telegram(f"❌ Невірний номер (1-{len(sources)})", chat_id=chat_id)
                    
                    del user_state[chat_id]
                
                except ValueError:
                    send_to_telegram("❌ Надішліть номер джерела (наприклад: 1, 2, 3)", chat_id=chat_id)
    
    except Exception as e:
        logging.error(f"Error checking commands: {e}")


# 🔄 ГОЛОВНИЙ ЦИКЛ
if __name__ == "__main__":
    logging.info("=" * 60)
    logging.info("💰 DINEROLATAM RSS BOT v4.0 (Зі зміною інтервалу)")
    logging.info("📈 Noticias financieras para Latinoamérica")
    logging.info("=" * 60)
    
    load_stats()
    load_interval()  # Завантажуємо інтервал
    sources = load_sources()
    handle_commands.last_update_id = 0
    
    logging.info(f"✅ Бот запущено!")
    logging.info(f"📢 Канал: {TELEGRAM_CHANNEL}")
    logging.info(f"👤 Admin ID: {ADMIN_USER_ID}")
    logging.info(f"📰 Джерел: {len(sources)}")
    logging.info(f"⏱ Інтервал: {check_interval // 3600} годин")
    logging.info("=" * 60)
    
    print("=" * 60)
    print("💰 DINEROLATAM RSS BOT v4.0 (Зі зміною інтервалу)")
    print("📈 Noticias financieras para Latinoamérica")
    print("=" * 60)
    print(f"✅ Бот запущено!")
    print(f"📢 Канал: {TELEGRAM_CHANNEL}")
    print(f"👤 Admin ID: {ADMIN_USER_ID}")
    print(f"📰 Джерел: {len(sources)}")
    print(f"⏱ Інтервал: {check_interval // 3600} годин")
    print("=" * 60)
    
    while True:
        try:
            handle_commands()
            
            logging.info(f"[{datetime.now()}] 🔍 Checking for news...")
            posts = check_for_updates()
            logging.info(f"[{datetime.now()}] ✅ Published {posts} posts")
            
            # Динамічний інтервал (перевіряємо команди кожні 10 секунд)
            iterations = check_interval // 10
            for _ in range(iterations):
                time.sleep(10)
                handle_commands()
        
        except KeyboardInterrupt:
            logging.info("\n[!] Бот зупинено користувачем")
            save_stats()
            save_sources(load_sources())
            break
        except Exception as e:
            logging.error(f"[ERROR] {e}")
            time.sleep(60)