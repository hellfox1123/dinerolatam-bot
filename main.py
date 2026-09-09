# 📦 main.py - Бот для DineroLatam v6.0
# Зміни у v6.0:
# - /rates і /inflation тепер тягнуть реальні дані (open.er-api.com, World Bank API)
#   замість захардкоджених чисел
# - Формат постів: HTML замість Markdown (не ламається на спецсимволах у заголовках)
# - Розумне обрізання опису по кінцю речення + динамічний бюджет під ліміт caption 1024
# - Нове меню на inline-кнопках (callback_query) замість команд
# - Історія опублікованих новин зберігається на диск (не втрачається при рестарті)
# - Моніторинг здоров'я джерел: адміну приходить сповіщення, якщо джерело
#   N разів поспіль не віддає новин
# - Новий блок перед хештегами: посилання на статтю + посилання на власний канал
# - Розширений фільтр фінансових новин

import feedparser
import requests
from datetime import datetime
import time
import re
import json
import os
import html
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
TELEGRAM_CHANNEL = os.environ.get('TELEGRAM_CHANNEL')

# Посилання на власний канал і назва, які показуємо в кожному пості перед хештегами.
# Можна перевизначити через змінні середовища CHANNEL_LINK / CHANNEL_NAME.
CHANNEL_LINK = os.environ.get('CHANNEL_LINK', 'https://t.me/dinerolatam')
CHANNEL_NAME = os.environ.get('CHANNEL_NAME', 'Dinero Latam')

# Скільки символів опису новини показуємо в пості (обрізаємо по кінцю речення).
# Це "стеля" — реальний бюджет ще звужується динамічно, щоб caption не перевищив
# ліміт Telegram у 1024 символи разом із заголовком, посиланнями і хештегами.
MAX_DESC_CHARS = int(os.environ.get('MAX_DESC_CHARS', '600'))
CAPTION_HARD_LIMIT = 1024

# Перевірка на старті
if not TELEGRAM_BOT_TOKEN:
    logging.error("❌ ПОМИЛКА: TELEGRAM_BOT_TOKEN не знайдено!")
    exit(1)

if not TELEGRAM_CHANNEL:
    logging.error("❌ ПОМИЛКА: TELEGRAM_CHANNEL не знайдено!")
    exit(1)

_admin_id_raw = os.environ.get('ADMIN_USER_ID')
if not _admin_id_raw:
    logging.error("❌ ПОМИЛКА: ADMIN_USER_ID не знайдено!")
    exit(1)
try:
    ADMIN_USER_ID = int(_admin_id_raw)
except ValueError:
    logging.error("❌ ПОМИЛКА: ADMIN_USER_ID має бути числом!")
    exit(1)

# 💾 Файли для збереження даних
SOURCES_FILE = "rss_sources.json"
STATS_FILE = "bot_stats.json"
INTERVAL_FILE = "bot_interval.json"
PAUSE_FILE = "bot_pause.json"
HISTORY_FILE = "published_history.json"
HEALTH_FILE = "source_health.json"

HISTORY_MAX = 300          # скільки останніх опублікованих новин зберігаємо
FAIL_ALERT_THRESHOLD = 5   # після скількох невдалих перевірок поспіль сповіщати адміна

# 📊 СТАТИСТИКА
stats = {
    "posts_published": 0,
    "last_check": None,
    "start_time": datetime.now(),
    "errors": 0
}

# ⏱ ІНТЕРВАЛ (за замовчуванням 3 години = 10800 секунд)
check_interval = 10800

# ⏸ СТАТУС ПАУЗИ
is_paused = False

# Відстеження опублікованих новин (завантажується з HISTORY_FILE при старті)
published_urls = set()
published_history = []  # список dict: {url, title, source, published_at}

# 💬 Стан діалогу з користувачем (для додавання/видалення джерел)
user_state = {}


# ============================================================
# 📰 ДЖЕРЕЛА RSS
# ============================================================

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


# ============================================================
# ⏱ ІНТЕРВАЛ
# ============================================================

def load_interval():
    global check_interval
    try:
        if os.path.exists(INTERVAL_FILE):
            with open(INTERVAL_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                check_interval = data.get("interval", 10800)
                logging.info(f"⏱ Завантажено інтервал: {check_interval // 60} хвилин")
                return check_interval
    except Exception as e:
        logging.error(f"⚠️ Помилка завантаження інтервалу: {e}")

    check_interval = 10800
    return check_interval


def save_interval(interval):
    try:
        with open(INTERVAL_FILE, "w", encoding="utf-8") as f:
            json.dump({"interval": interval}, f, indent=2)
        logging.info(f"💾 Збережено інтервал: {interval // 60} хвилин")
        return True
    except Exception as e:
        logging.error(f"❌ Помилка збереження інтервалу: {e}")
        return False


# ============================================================
# ⏸ ПАУЗА
# ============================================================

def load_pause_state():
    global is_paused
    try:
        if os.path.exists(PAUSE_FILE):
            with open(PAUSE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                is_paused = data.get("paused", False)
                logging.info(f"⏸ Стан паузи: {is_paused}")
                return is_paused
    except Exception as e:
        logging.error(f"⚠️ Помилка завантаження паузи: {e}")

    is_paused = False
    return is_paused


def save_pause_state(paused):
    try:
        with open(PAUSE_FILE, "w", encoding="utf-8") as f:
            json.dump({"paused": paused}, f, indent=2)
        logging.info(f"⏸ Збережено стан паузи: {paused}")
        return True
    except Exception as e:
        logging.error(f"❌ Помилка збереження паузи: {e}")
        return False


# ============================================================
# 📚 ІСТОРІЯ ОПУБЛІКОВАНИХ НОВИН (персистентна, на відміну від v5.0)
# ============================================================

def load_history():
    try:
        if os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("items", [])
    except Exception as e:
        logging.error(f"⚠️ Помилка завантаження історії: {e}")
    return []


def save_history(items):
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump({"items": items[-HISTORY_MAX:]}, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logging.error(f"❌ Помилка збереження історії: {e}")


def add_to_history(url, title, source_name):
    global published_history, published_urls
    published_history.append({
        "url": url,
        "title": title,
        "source": source_name,
        "published_at": datetime.now().isoformat()
    })
    published_history = published_history[-HISTORY_MAX:]
    published_urls.add(url)
    save_history(published_history)


# ============================================================
# 🩺 ЗДОРОВ'Я ДЖЕРЕЛ (для сповіщень про "мертві" джерела)
# ============================================================

def load_health():
    try:
        if os.path.exists(HEALTH_FILE):
            with open(HEALTH_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logging.error(f"⚠️ Помилка завантаження стану джерел: {e}")
    return {}


def save_health(health):
    try:
        with open(HEALTH_FILE, "w", encoding="utf-8") as f:
            json.dump(health, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logging.error(f"❌ Помилка збереження стану джерел: {e}")


# ============================================================
# 🔎 ВАЛІДАЦІЯ ТА НАЗВИ ДЖЕРЕЛ
# ============================================================

_FEED_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/rss+xml, application/xml, text/xml, */*',
    'Connection': 'keep-alive',
}


def validate_rss_url(url):
    """Перевірка чи URL є валідним RSS feed (через requests + власний User-Agent,
    щоб сайти, які блокують дефолтний User-Agent feedparser'а, теж проходили перевірку)."""
    try:
        logging.info(f"🔍 Перевірка URL: {url}")
        response = requests.get(url, headers=_FEED_HEADERS, timeout=15)
        feed = feedparser.parse(response.content)

        if len(feed.entries) == 0:
            return False, ("❌ RSS feed не містить новин.\n"
                            "Можливо, це не прямий шлях до feed'у (наприклад, потрібен "
                            "конкретний розділ на кшталт /rss/economia замість просто /rss/).")

        if not feed.feed.get('title'):
            return False, "❌ RSS feed не має заголовка. Можливо це не валідний RSS."

        first_entry = feed.entries[0]
        if not first_entry.get('title') and not first_entry.get('link'):
            return False, "❌ Новини в RSS не мають заголовків або посилань."

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
    except Exception:
        pass

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
    elif 'google' in url:
        return "Google News"
    elif 'investing' in url:
        return "Investing.com"
    elif 'cripto' in url or 'cointelegraph' in url:
        return "Cripto"
    else:
        return "Fuente desconocida"


# ============================================================
# 🎯 ЕМОДЗІ, ХЕШТЕГИ, ФІЛЬТР ФІНАНСОВИХ НОВИН
# ============================================================

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
    elif "perú" in text or "peru" in text:
        hashtags.append("#Perú")
    elif "brasil" in text:
        hashtags.append("#Brasil")

    return " ".join(hashtags[:6])


# Розширений список ключових слів — v5.0 мав лише 15 слів і пропускав чимало
# релевантних фінансових новин.
_FINANCIAL_KEYWORDS = [
    "economía", "finanzas", "financiero", "inversión", "inversor", "bolsa", "mercado",
    "dólar", "peso", "euro", "divisa", "tipo de cambio",
    "inflación", "ipc", "precios", "canasta básica",
    "tasas", "tasa de interés", "banco central", "reserva federal", "fmi",
    "cripto", "bitcoin", "ethereum", "acciones", "ahorro", "crédito", "deuda",
    "pib", "recesión", "desempleo", "empleo", "salario", "sueldo",
    "impuesto", "hacienda", "presupuesto", "arancel", "comercio exterior",
    "exportaciones", "importaciones", "vivienda", "hipoteca", "alquiler",
    "petróleo", "commodities", "materias primas",
]


def is_financial_news(title, description):
    text = f"{title} {description}".lower()
    return any(keyword in text for keyword in _FINANCIAL_KEYWORDS)


def extract_image_url(entry):
    if hasattr(entry, 'media_content'):
        for media in entry.media_content:
            if media.get('medium') == 'image' or media.get('type', '').startswith('image'):
                return media.get('url')

    if hasattr(entry, 'media_thumbnail') and entry.media_thumbnail:
        url = entry.media_thumbnail[0].get('url')
        if url:
            return url

    if hasattr(entry, 'enclosures'):
        for enclosure in entry.enclosures:
            if hasattr(enclosure, 'type') and enclosure.type.startswith('image'):
                return enclosure.href

    if hasattr(entry, 'description'):
        match = re.search(r'<img[^>]+src="([^">]+)"', entry.description)
        if match:
            return match.group(1)

    if hasattr(entry, 'content') and entry.content:
        match = re.search(r'<img[^>]+src="([^">]+)"', entry.content[0].get('value', ''))
        if match:
            return match.group(1)

    if hasattr(entry, 'image'):
        return entry.image.get('href')

    return None


# ============================================================
# ✂️ РОЗУМНЕ ОБРІЗАННЯ ТЕКСТУ ТА ФОРМАТУВАННЯ ПОСТА
# ============================================================

def escape_html(text):
    """Екранування для HTML parse_mode. На відміну від legacy Markdown у v5.0,
    HTML-режим Telegram не 'падає' мовчки через *, _, [, ] у заголовках з RSS —
    достатньо екранувати лише &, < і >."""
    return html.escape(text or "", quote=False)


def smart_truncate(text, limit):
    """Обрізає текст по кінцю речення (крапка/знак оклику/знак питання),
    а не посеред слова чи фрази. Якщо в межах ліміту немає межі речення —
    обрізає по останньому пробілу."""
    text = re.sub(r'\s+', ' ', (text or '').strip())
    if len(text) <= limit:
        return text

    cut = text[:limit]

    best_end = -1
    for marker in ['. ', '! ', '? ', '.\n']:
        idx = cut.rfind(marker)
        if idx > best_end:
            best_end = idx

    # Використовуємо межу речення, тільки якщо вона не занадто рано обрізає текст
    if best_end > limit * 0.4:
        return cut[:best_end + 1].strip()

    last_space = cut.rfind(' ')
    if last_space > 0:
        cut = cut[:last_space]
    return cut.strip() + "…"


def format_post(title, link, description, source_name):
    """Формує HTML-підпис поста.

    Структура (знизу вгору відповідає п.4 запиту користувача):
      <емодзі> <жирний заголовок>

      <опис, обрізаний по реченню>

      🔗 Detalles: <посилання на статтю>

      📢 Fuente: <посилання на власний канал>

      <хештеги>
    """
    emoji = get_emoji_for_news(title, description)
    hashtags = get_hashtags_for_news(title, description)

    clean_title = re.sub(r'<[^>]+>', '', title or '').strip()
    clean_desc = re.sub(r'<[^>]+>', '', description or '').strip()

    title_html = f"{emoji} <b>{escape_html(clean_title)}</b>"
    details_line = f'🔗 Detalles: <a href="{escape_html(link)}">{escape_html(source_name)}</a>'
    channel_line = f'📢 Fuente: <a href="{escape_html(CHANNEL_LINK)}">{escape_html(CHANNEL_NAME)}</a>'

    # Динамічний бюджет під опис: рахуємо, скільки місця вже займають
    # заголовок, обидва рядки з посиланнями і хештеги, і не даємо всій
    # публікації перевищити ліміт Telegram (1024 символи для caption).
    separators_len = len("\n\n") * 4
    fixed_len = len(title_html) + len(details_line) + len(channel_line) + len(hashtags) + separators_len
    budget = min(MAX_DESC_CHARS, max(150, CAPTION_HARD_LIMIT - fixed_len - 20))

    desc_final = smart_truncate(clean_desc, budget)
    desc_html = escape_html(desc_final)

    post = f"{title_html}\n\n{desc_html}\n\n{details_line}\n\n{channel_line}\n\n{hashtags}"
    return post


# ============================================================
# 💵 КУРСИ ВАЛЮТ — живі дані замість хардкоду v5.0
#     Джерело: open.er-api.com (безкоштовний, без ключа, оновлення ~раз на добу)
# ============================================================

CURRENCY_PAIRS = [
    ("MXN", "🇲🇽", "México"),
    ("COP", "🇨🇴", "Colombia"),
    ("ARS", "🇦🇷", "Argentina"),
    ("CLP", "🇨🇱", "Chile"),
    ("PEN", "🇵🇪", "Perú"),
]


def fetch_usd_rates():
    """Повертає (rates_dict, updated_str) або (None, None), якщо API недоступне."""
    try:
        response = requests.get("https://open.er-api.com/v6/latest/USD", timeout=10)
        data = response.json()
        if data.get("result") == "success":
            return data.get("rates", {}), data.get("time_last_update_utc")
    except Exception as e:
        logging.error(f"❌ Помилка отримання курсів валют: {e}")
    return None, None


def build_rates_post():
    """Готовий пост із курсами валют. Повертає None, якщо API не відповіло —
    у такому разі бот НЕ публікує застарілі/вигадані цифри, а повідомляє про помилку."""
    rates, updated = fetch_usd_rates()
    if not rates:
        return None

    lines = ["💵 <b>Cotización del dólar en Latinoamérica</b>", ""]
    lines.append(f"Actualizado: {datetime.now().strftime('%d/%m/%Y')} · fuente: open.er-api.com")
    lines.append("")

    for code, flag, country in CURRENCY_PAIRS:
        val = rates.get(code)
        if val is not None:
            lines.append(f"🇺🇸 USD → {flag} {code} ({country}): {val:,.2f}")

    eur_rate = rates.get("EUR")
    if eur_rate:
        lines.append(f"🇪🇺 EUR → 🇺🇸 USD: {(1 / eur_rate):,.4f}")

    lines.append("")
    lines.append(f'📢 Fuente: <a href="{escape_html(CHANNEL_LINK)}">{escape_html(CHANNEL_NAME)}</a>')
    lines.append("")
    lines.append("#Dólar #Divisas #LatAm #Finanzas")

    return "\n".join(lines)


# ============================================================
# 📈 ІНФЛЯЦІЯ — живі дані з World Bank Open Data API (без ключа)
#     Індикатор FP.CPI.TOTL.ZG, mrnev=1 = остання наявна річна цифра по країні.
#     Важливо: це офіційна річна статистика, вона оновлюється рідше, ніж курси
#     валют — тому пост завжди чесно вказує рік даних, а не "сьогодні".
# ============================================================

WB_COUNTRIES = {
    "ARG": ("🇦🇷", "Argentina"),
    "MEX": ("🇲🇽", "México"),
    "COL": ("🇨🇴", "Colombia"),
    "CHL": ("🇨🇱", "Chile"),
    "PER": ("🇵🇪", "Perú"),
    "BRA": ("🇧🇷", "Brasil"),
    "URY": ("🇺🇾", "Uruguay"),
    "VEN": ("🇻🇪", "Venezuela"),
}


def fetch_inflation_data():
    codes = ";".join(WB_COUNTRIES.keys())
    url = f"https://api.worldbank.org/v2/country/{codes}/indicator/FP.CPI.TOTL.ZG"
    params = {"format": "json", "mrnev": 1, "per_page": 100}
    try:
        response = requests.get(url, params=params, timeout=15)
        data = response.json()
        if not isinstance(data, list) or len(data) < 2 or not data[1]:
            return None

        results = {}
        for item in data[1]:
            iso3 = item.get("countryiso3code")
            val = item.get("value")
            year = item.get("date")
            if iso3 and val is not None:
                results[iso3] = (val, year)
        return results or None
    except Exception as e:
        logging.error(f"❌ Помилка отримання даних інфляції: {e}")
        return None


def build_inflation_post():
    """Готовий пост з інфляцією. Повертає None, якщо World Bank API не відповіло."""
    data = fetch_inflation_data()
    if not data:
        return None

    rows = []
    for iso3, (flag, name) in WB_COUNTRIES.items():
        if iso3 in data:
            val, year = data[iso3]
            rows.append((val, flag, name, year))
    rows.sort(reverse=True)

    if not rows:
        return None

    lines = ["📈 <b>Inflación en Latinoamérica</b>", ""]
    lines.append("Última cifra anual oficial disponible por país (Banco Mundial):")
    lines.append("")
    for val, flag, name, year in rows:
        lines.append(f"{flag} {name}: {val:.1f}% ({year})")

    lines.append("")
    lines.append("ℹ️ Cada país reporta su año más reciente disponible; los organismos "
                  "internacionales publican estas cifras con cierto rezago respecto a "
                  "los datos mensuales de cada banco central.")
    lines.append("")
    lines.append(f'📢 Fuente: <a href="{escape_html(CHANNEL_LINK)}">{escape_html(CHANNEL_NAME)}</a>')
    lines.append("")
    lines.append("#Inflación #Economía #LatAm")

    return "\n".join(lines)


# ============================================================
# 📤 НАДСИЛАННЯ В TELEGRAM
# ============================================================

def send_to_telegram(message, image_url=None, chat_id=None, parse_mode='HTML', reply_markup=None):
    """Надсилання в Telegram. За замовчуванням HTML замість Markdown (v5.0) —
    надійніше для сирого тексту з RSS-фідів."""

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
        if reply_markup:
            photo_data['reply_markup'] = reply_markup
        try:
            response = requests.post(photo_url, json=photo_data, timeout=15)
            result = response.json()
            if result.get('ok'):
                return result
            logging.warning(f"⚠️ sendPhoto не вдався ({result.get('description')}), "
                             f"пробуємо як текстове повідомлення")
        except Exception as e:
            logging.error(f"❌ Error sending photo: {e}")

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {
        'chat_id': chat_id,
        'text': message,
        'parse_mode': parse_mode,
        'disable_web_page_preview': True,
    }
    if reply_markup:
        data['reply_markup'] = reply_markup

    try:
        response = requests.post(url, json=data, timeout=15)
        return response.json()
    except Exception as e:
        logging.error(f"❌ Error sending to Telegram: {e}")
        return None


def edit_message(chat_id, message_id, text, reply_markup=None, parse_mode='HTML'):
    """Редагування вже надісланого повідомлення (для меню на кнопках)."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText"
    data = {
        'chat_id': chat_id,
        'message_id': message_id,
        'text': text,
        'parse_mode': parse_mode,
        'disable_web_page_preview': True,
    }
    if reply_markup:
        data['reply_markup'] = reply_markup
    try:
        response = requests.post(url, json=data, timeout=15)
        return response.json()
    except Exception as e:
        logging.error(f"❌ Error editing message: {e}")
        return None


def answer_callback(callback_id, text=None, show_alert=False):
    """Прибирає 'годинник очікування' на кнопці й опційно показує спливаюче повідомлення."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery"
    data = {'callback_query_id': callback_id}
    if text:
        data['text'] = text
        data['show_alert'] = show_alert
    try:
        requests.post(url, json=data, timeout=10)
    except Exception as e:
        logging.error(f"❌ Error answering callback: {e}")


# ============================================================
# 🔄 ОСНОВНА ЛОГІКА ПЕРЕВІРКИ НОВИН
# ============================================================

MAX_ENTRIES_PER_SOURCE = 3   # скільки останніх новин розглядаємо з кожного джерела за цикл
MAX_POSTS_PER_CYCLE = 5      # запобіжник від "заспамлення" каналу, якщо джерела ожили одразу всі


def fetch_rss_feed(feed_url):
    """Отримання новин з RSS з повторними спробами"""
    for attempt in range(3):
        try:
            logging.info(f"🔄 Спроба {attempt + 1}/3: {feed_url[:50]}...")

            response = requests.get(feed_url, headers=_FEED_HEADERS, timeout=15)

            if response.status_code == 200:
                feed = feedparser.parse(response.content)
                if len(feed.entries) > 0:
                    logging.info(f"✅ Успішно: {len(feed.entries)} новин")
                    return feed.entries
                else:
                    logging.warning("⚠️ RSS пустий")
                    return []
            else:
                logging.warning(f"⚠️ Статус {response.status_code}")

        except Exception as e:
            logging.error(f"❌ Помилка спроби {attempt + 1}: {str(e)[:80]}")
            if attempt < 2:
                time.sleep(5)

    logging.error(f"❌ Не вдалося після 3 спроб: {feed_url[:50]}...")
    return []


def verify_all_sources():
    """Діагностика: перевіряє кожне джерело наживо і показує, скільки новин
    воно реально віддає. Використовується, коли джерела 'зникають' незрозуміло чому."""
    sources = load_sources()
    if not sources:
        return "❌ Немає налаштованих джерел."

    lines = ["🕵️ <b>Перевірка RSS-джерел</b>", ""]
    for i, url in enumerate(sources, 1):
        entries = fetch_rss_feed(url)
        name = get_source_name_from_url(url)
        if entries:
            lines.append(f"{i}. ✅ {escape_html(name)} — {len(entries)} новин")
        else:
            lines.append(f"{i}. ❌ {escape_html(name)} — немає відповіді або порожній feed")
        lines.append(f"    <code>{escape_html(url)}</code>")

    return "\n".join(lines)


def check_for_updates():
    """Перевірка новин та публікація"""
    global stats, is_paused

    if is_paused:
        logging.info("⏸ Бот на паузі, пропускаємо перевірку")
        return 0

    sources = load_sources()
    health = load_health()
    posts_count = 0

    for feed_url in sources:
        if posts_count >= MAX_POSTS_PER_CYCLE:
            break

        entries = fetch_rss_feed(feed_url)
        source_name = get_source_name_from_url(feed_url)

        # 🩺 Оновлюємо стан здоров'я джерела та сповіщаємо адміна,
        # якщо воно вже кілька разів поспіль не дає новин.
        rec = health.get(feed_url, {"fail_streak": 0, "alerted": False})
        if not entries:
            rec["fail_streak"] = rec.get("fail_streak", 0) + 1
            if rec["fail_streak"] == FAIL_ALERT_THRESHOLD and not rec.get("alerted"):
                send_to_telegram(
                    f"⚠️ Джерело <b>{escape_html(source_name)}</b> не віддає новин уже "
                    f"{FAIL_ALERT_THRESHOLD} перевірок поспіль.\n"
                    f"URL: <code>{escape_html(feed_url)}</code>\n\n"
                    f"Перевір його кнопкою «🕵️ Перевірити джерела» в меню — можливо, "
                    f"посилання застаріло і його треба замінити.",
                    chat_id=ADMIN_USER_ID
                )
                rec["alerted"] = True
        else:
            rec["fail_streak"] = 0
            rec["alerted"] = False
        health[feed_url] = rec

        for entry in entries[:MAX_ENTRIES_PER_SOURCE]:
            if posts_count >= MAX_POSTS_PER_CYCLE:
                break

            title = entry.get('title', '')
            link = entry.get('link', '')
            description = entry.get('description', entry.get('summary', ''))

            if not link or link in published_urls:
                continue

            if not is_financial_news(title, description):
                continue

            message = format_post(title, link, description, source_name)
            image_url = extract_image_url(entry)

            result = send_to_telegram(message, image_url)

            if result and result.get('ok'):
                clean_title = re.sub(r'<[^>]+>', '', title or '').strip()
                add_to_history(link, clean_title, source_name)
                posts_count += 1
                stats["posts_published"] += 1
            else:
                stats["errors"] += 1
                logging.error(f"❌ Не вдалося опублікувати: {result}")

            time.sleep(2)

    save_health(health)
    stats["last_check"] = datetime.now()
    save_stats()

    return posts_count


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
    global stats
    try:
        if os.path.exists(STATS_FILE):
            with open(STATS_FILE, "r") as f:
                data = json.load(f)
                stats["posts_published"] = data.get("posts_published", 0)
                stats["errors"] = data.get("errors", 0)
    except Exception as e:
        logging.error(f"Error loading stats: {e}")


# ============================================================
# 🧱 ТЕКСТИ ЕКРАНІВ МЕНЮ
# ============================================================

def build_menu_text():
    sources = load_sources()
    hours = check_interval // 3600
    minutes = (check_interval % 3600) // 60
    return (
        f"🤖 <b>DineroLatam Bot — панель керування</b>\n\n"
        f"📊 Стан: {'⏸ На паузі' if is_paused else '✅ Працює'}\n"
        f"📰 Джерел: {len(sources)}\n"
        f"✅ Опубліковано: {stats['posts_published']}\n"
        f"⏱ Інтервал: {hours} год {minutes} хв\n\n"
        f"Обери розділ кнопкою нижче 👇"
    )


def build_stats_text():
    uptime = datetime.now() - stats['start_time']
    sources = load_sources()
    hours = check_interval // 3600
    minutes = (check_interval % 3600) // 60

    total = stats['posts_published'] + stats['errors']
    efficiency_text = f"{(stats['posts_published'] / total * 100):.1f}%" if total > 0 else "Немає даних"

    last_check = stats['last_check']
    last_check_text = last_check.strftime('%Y-%m-%d %H:%M:%S') if isinstance(last_check, datetime) else 'Ніколи'

    return (
        f"📊 <b>Статистика бота</b>\n\n"
        f"📰 Опубліковано постів: {stats['posts_published']}\n"
        f"❌ Помилок: {stats['errors']}\n"
        f"⏱ Остання перевірка: {last_check_text}\n"
        f"🕐 Час роботи: {str(uptime).split('.')[0]}\n"
        f"💾 Збережено новин: {len(published_urls)}\n"
        f"📡 Активних джерел: {len(sources)}\n"
        f"⏱ Інтервал перевірки: {hours} год {minutes} хв\n\n"
        f"Ефективність: {efficiency_text} успішних публікацій"
    )


def build_sources_text(sources):
    if not sources:
        return "📰 <b>RSS джерела</b>\n\nСписок порожній — додай перше джерело кнопкою нижче."

    lines = [f"📰 <b>RSS джерела ({len(sources)})</b>", ""]
    for i, url in enumerate(sources, 1):
        name = get_source_name_from_url(url)
        lines.append(f"{i}. {escape_html(name)}")
        lines.append(f"    <code>{escape_html(url)}</code>")
    lines.append("")
    lines.append("Натисни на джерело нижче, щоб видалити його, або додай нове.")
    return "\n".join(lines)


def build_interval_text():
    hours = check_interval // 3600
    minutes = (check_interval % 3600) // 60
    return (
        f"⏱ <b>Інтервал перевірки новин</b>\n\n"
        f"Поточний: {hours} год {minutes} хв\n\n"
        f"Обери новий інтервал кнопкою нижче.\n"
        f"⚠️ Зміна набуде чинності після завершення поточного циклу очікування."
    )


def build_latest_text():
    if not published_history:
        return "📭 Поки немає опублікованих новин."

    lines = ["📰 <b>Останні опубліковані новини</b>", ""]
    for item in reversed(published_history[-10:]):
        title = escape_html(item.get('title', ''))[:100]
        source = escape_html(item.get('source', ''))
        published_at = item.get('published_at', '')[:16].replace('T', ' ')
        lines.append(f"• <b>{title}</b>\n   {source} · {published_at}")
    lines.append("")
    lines.append(f"📊 Всього збережено в історії: {len(published_history)}")
    return "\n".join(lines)


HELP_TEXT = (
    "❓ <b>Допомога</b>\n\n"
    "Керування ботом тепер відбувається через кнопки в меню (/menu або /start).\n\n"
    "<b>Розділи меню:</b>\n"
    "📊 Статистика — лічильники постів/помилок\n"
    "📰 Джерела — перегляд, додавання, видалення RSS-джерел\n"
    "⏱ Інтервал — як часто бот перевіряє новини\n"
    "⏸️/▶️ Пауза/Резюме — тимчасово зупинити чи відновити публікації\n"
    "🔄 Перевірити зараз — примусова перевірка новин негайно\n"
    "🕵️ Перевірити джерела — діагностика: скільки новин реально віддає кожне джерело\n"
    "💵 Курси — актуальні курси валют (окремим постом)\n"
    "📈 Інфляція — актуальна інфляція по країнах (окремим постом)\n"
    "📰 Останні новини — що вже опубліковано\n\n"
    "Старі текстові команди (/stats, /sources, /rates і т.д.) також продовжують працювати."
)


ADD_SOURCE_PROMPT = (
    "➕ <b>Додавання нового RSS джерела</b>\n\n"
    "Надішли URL RSS feed повідомленням у чат.\n\n"
    "<b>Приклади:</b>\n"
    "<code>https://www.expansion.com/rss/economia.xml</code>\n"
    "<code>https://example.com/feed/</code>\n\n"
    "⚠️ Я перевірю посилання перед додаванням. Якщо не спрацює — введи /cancel."
)


# ============================================================
# ⌨️ INLINE-КЛАВІАТУРИ
# ============================================================

def kb_main():
    pause_btn = {"text": "▶️ Відновити", "callback_data": "pause_toggle"} if is_paused \
        else {"text": "⏸ Пауза", "callback_data": "pause_toggle"}
    return {
        "inline_keyboard": [
            [{"text": "📊 Статистика", "callback_data": "stats"},
             {"text": "📰 Джерела", "callback_data": "sources"}],
            [{"text": "⏱ Інтервал", "callback_data": "interval"}, pause_btn],
            [{"text": "🔄 Перевірити зараз", "callback_data": "check_now"},
             {"text": "🕵️ Перевірити джерела", "callback_data": "verify_sources"}],
            [{"text": "💵 Курси", "callback_data": "rates"},
             {"text": "📈 Інфляція", "callback_data": "inflation"}],
            [{"text": "📰 Останні новини", "callback_data": "latest"},
             {"text": "❓ Довідка", "callback_data": "help"}],
        ]
    }


def kb_back():
    return {"inline_keyboard": [[{"text": "🔙 Меню", "callback_data": "menu"}]]}


def kb_sources(sources):
    rows = []
    for i, url in enumerate(sources):
        name = get_source_name_from_url(url)
        label = name if len(name) <= 28 else name[:27] + "…"
        rows.append([{"text": f"🗑 {label}", "callback_data": f"src_del:{i}"}])
    rows.append([{"text": "➕ Додати джерело", "callback_data": "src_add"}])
    rows.append([{"text": "🔙 Меню", "callback_data": "menu"}])
    return {"inline_keyboard": rows}


def kb_confirm_delete(index, name):
    label = name if len(name) <= 28 else name[:27] + "…"
    return {
        "inline_keyboard": [
            [{"text": f"✅ Так, видалити «{label}»", "callback_data": f"src_delc:{index}"}],
            [{"text": "❌ Скасувати", "callback_data": "sources"}],
        ]
    }


def kb_interval():
    return {
        "inline_keyboard": [
            [{"text": "10 хв", "callback_data": "int_set:600"},
             {"text": "30 хв", "callback_data": "int_set:1800"}],
            [{"text": "1 год", "callback_data": "int_set:3600"},
             {"text": "3 год", "callback_data": "int_set:10800"}],
            [{"text": "6 год", "callback_data": "int_set:21600"},
             {"text": "12 год", "callback_data": "int_set:43200"}],
            [{"text": "🔙 Меню", "callback_data": "menu"}],
        ]
    }


# ============================================================
# 🎮 ОБРОБКА CALLBACK-ІВ (натискань кнопок)
# ============================================================

def handle_callback(cq):
    global check_interval, is_paused

    data = cq.get('data', '')
    user_id = cq.get('from', {}).get('id')
    msg = cq.get('message', {}) or {}
    chat_id = msg.get('chat', {}).get('id')
    message_id = msg.get('message_id')

    if user_id != ADMIN_USER_ID:
        answer_callback(cq['id'], "⛔ Немає доступу", show_alert=True)
        return

    answer_callback(cq['id'])

    if data == 'menu':
        edit_message(chat_id, message_id, build_menu_text(), reply_markup=kb_main())

    elif data == 'stats':
        edit_message(chat_id, message_id, build_stats_text(), reply_markup=kb_back())

    elif data == 'sources':
        sources = load_sources()
        edit_message(chat_id, message_id, build_sources_text(sources), reply_markup=kb_sources(sources))

    elif data == 'src_add':
        user_state[chat_id] = {'action': 'add_source'}
        edit_message(chat_id, message_id, ADD_SOURCE_PROMPT, reply_markup=kb_back())

    elif data.startswith('src_del:'):
        index = int(data.split(':')[1])
        sources = load_sources()
        if 0 <= index < len(sources):
            name = get_source_name_from_url(sources[index])
            edit_message(chat_id, message_id, f"Видалити джерело «{escape_html(name)}»?",
                         reply_markup=kb_confirm_delete(index, name))
        else:
            edit_message(chat_id, message_id, build_sources_text(sources), reply_markup=kb_sources(sources))

    elif data.startswith('src_delc:'):
        index = int(data.split(':')[1])
        sources = load_sources()
        if 0 <= index < len(sources):
            sources.pop(index)
            save_sources(sources)
        sources = load_sources()
        edit_message(chat_id, message_id, build_sources_text(sources), reply_markup=kb_sources(sources))

    elif data == 'interval':
        edit_message(chat_id, message_id, build_interval_text(), reply_markup=kb_interval())

    elif data.startswith('int_set:'):
        seconds = int(data.split(':')[1])
        check_interval = seconds
        save_interval(check_interval)
        edit_message(chat_id, message_id, "✅ Інтервал оновлено!\n\n" + build_interval_text(),
                     reply_markup=kb_interval())

    elif data == 'pause_toggle':
        is_paused = not is_paused
        save_pause_state(is_paused)
        edit_message(chat_id, message_id, build_menu_text(), reply_markup=kb_main())

    elif data == 'check_now':
        edit_message(chat_id, message_id, "🔄 Перевіряю джерела...")
        posts = check_for_updates()
        edit_message(chat_id, message_id, f"✅ Перевірку завершено.\n📰 Опубліковано: {posts}",
                     reply_markup=kb_back())

    elif data == 'verify_sources':
        edit_message(chat_id, message_id, "🕵️ Перевіряю джерела, це може зайняти кілька секунд...")
        report = verify_all_sources()
        edit_message(chat_id, message_id, report, reply_markup=kb_back())

    elif data == 'rates':
        post = build_rates_post()
        if post:
            send_to_telegram(post, chat_id=chat_id)
        else:
            send_to_telegram("⚠️ Не вдалося отримати курси валют зараз. Спробуй пізніше.", chat_id=chat_id)

    elif data == 'inflation':
        post = build_inflation_post()
        if post:
            send_to_telegram(post, chat_id=chat_id)
        else:
            send_to_telegram("⚠️ Не вдалося отримати дані по інфляції зараз. Спробуй пізніше.", chat_id=chat_id)

    elif data == 'latest':
        edit_message(chat_id, message_id, build_latest_text(), reply_markup=kb_back())

    elif data == 'help':
        edit_message(chat_id, message_id, HELP_TEXT, reply_markup=kb_back())


# ============================================================
# 🎮 ОБРОБКА ТЕКСТОВИХ КОМАНД (залишені для сумісності)
# ============================================================

def handle_commands():
    """Перевірка нових повідомлень і натискань кнопок"""
    global check_interval, is_paused

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    params = {
        'timeout': 1,
        'offset': handle_commands.last_update_id + 1 if hasattr(handle_commands, 'last_update_id') else 0
    }

    try:
        response = requests.get(url, params=params, timeout=5)
        updates = response.json().get('result', [])

        for update in updates:
            handle_commands.last_update_id = update['update_id']

            if 'callback_query' in update:
                handle_callback(update['callback_query'])
                continue

            message = update.get('message', {})
            chat_id = message.get('chat', {}).get('id')
            text = message.get('text', '') or ''
            user_id = message.get('from', {}).get('id')

            if user_id != ADMIN_USER_ID:
                continue

            # 📝 Стан діалогу (додавання/видалення джерела)
            if chat_id in user_state:
                state = user_state[chat_id]

                if text.strip().lower() == '/cancel':
                    del user_state[chat_id]
                    send_to_telegram("✅ Дію скасовано", chat_id=chat_id, reply_markup=kb_back())
                    continue

                if state.get('action') == 'add_source':
                    url_to_add = text.strip()
                    send_to_telegram("🔍 Перевіряю RSS feed... Зачекайте...", chat_id=chat_id)
                    is_valid, val_msg = validate_rss_url(url_to_add)

                    if is_valid:
                        sources = load_sources()
                        if url_to_add in sources:
                            send_to_telegram("⚠️ Це джерело вже додано!", chat_id=chat_id)
                        else:
                            sources.append(url_to_add)
                            if save_sources(sources):
                                send_to_telegram(
                                    f"✅ <b>Джерело додано!</b>\n\n{escape_html(val_msg)}\n"
                                    f"📊 Всього джерел: {len(sources)}",
                                    chat_id=chat_id, reply_markup=kb_sources(sources)
                                )
                            else:
                                send_to_telegram("❌ Помилка збереження джерела!", chat_id=chat_id)
                        del user_state[chat_id]
                    else:
                        send_to_telegram(
                            f"{escape_html(val_msg)}\n\n"
                            f"Надішли інше посилання або /cancel щоб скасувати.",
                            chat_id=chat_id
                        )
                    continue

                elif state.get('action') == 'remove_source':
                    try:
                        number = int(text.strip())
                        sources = state['sources']
                        if 1 <= number <= len(sources):
                            removed_url = sources.pop(number - 1)
                            save_sources(sources)
                            send_to_telegram(f"✅ Видалено: {removed_url}", chat_id=chat_id,
                                             reply_markup=kb_sources(sources))
                        else:
                            send_to_telegram(f"❌ Невірний номер (1-{len(sources)})", chat_id=chat_id)
                        del user_state[chat_id]
                    except ValueError:
                        send_to_telegram("❌ Надішли номер джерела (наприклад: 1)", chat_id=chat_id)
                    continue

            # 🎯 Команди
            if text in ('/start', '/menu'):
                send_to_telegram(build_menu_text(), chat_id=chat_id, reply_markup=kb_main())

            elif text == '/help':
                send_to_telegram(HELP_TEXT, chat_id=chat_id, reply_markup=kb_back())

            elif text == '/ping':
                send_to_telegram("🏓 Понг! Бот онлайн ✅", chat_id=chat_id)

            elif text == '/stats':
                send_to_telegram(build_stats_text(), chat_id=chat_id, reply_markup=kb_back())

            elif text == '/sources':
                sources = load_sources()
                send_to_telegram(build_sources_text(sources), chat_id=chat_id, reply_markup=kb_sources(sources))

            elif text == '/addsource':
                user_state[chat_id] = {'action': 'add_source'}
                send_to_telegram(ADD_SOURCE_PROMPT, chat_id=chat_id)

            elif text == '/removesource':
                sources = load_sources()
                if not sources:
                    send_to_telegram("❌ Немає джерел для видалення!", chat_id=chat_id)
                else:
                    user_state[chat_id] = {'action': 'remove_source', 'sources': sources}
                    send_to_telegram(build_sources_text(sources) + "\n\nНадішли номер джерела для видалення "
                                      "або /cancel.", chat_id=chat_id)

            elif text == '/pause':
                if is_paused:
                    send_to_telegram("⚠️ Бот вже на паузі!", chat_id=chat_id)
                else:
                    is_paused = True
                    save_pause_state(True)
                    send_to_telegram("⏸ Бот призупинив парсинг!", chat_id=chat_id, reply_markup=kb_back())

            elif text == '/resume':
                if not is_paused:
                    send_to_telegram("ℹ️ Бот вже працює!", chat_id=chat_id)
                else:
                    is_paused = False
                    save_pause_state(False)
                    send_to_telegram("▶️ Бот відновив парсинг!", chat_id=chat_id, reply_markup=kb_back())

            elif text == '/status':
                send_to_telegram(build_stats_text(), chat_id=chat_id, reply_markup=kb_back())

            elif text == '/latest':
                send_to_telegram(build_latest_text(), chat_id=chat_id, reply_markup=kb_back())

            elif text == '/rates':
                post = build_rates_post()
                if post:
                    send_to_telegram(post, chat_id=chat_id)
                else:
                    send_to_telegram("⚠️ Не вдалося отримати курси валют зараз.", chat_id=chat_id)

            elif text == '/inflation':
                post = build_inflation_post()
                if post:
                    send_to_telegram(post, chat_id=chat_id)
                else:
                    send_to_telegram("⚠️ Не вдалося отримати дані по інфляції зараз.", chat_id=chat_id)

            elif text == '/check':
                if is_paused:
                    send_to_telegram("⚠️ Бот на паузі! Спочатку /resume", chat_id=chat_id)
                else:
                    send_to_telegram("🔄 Перевірка новин...", chat_id=chat_id)
                    posts = check_for_updates()
                    send_to_telegram(f"✅ Перевірку завершено!\n📰 Опубліковано: {posts}", chat_id=chat_id)

            elif text == '/verifysources':
                send_to_telegram("🕵️ Перевіряю джерела...", chat_id=chat_id)
                send_to_telegram(verify_all_sources(), chat_id=chat_id)

            elif text == '/getinterval':
                send_to_telegram(build_interval_text(), chat_id=chat_id, reply_markup=kb_interval())

            elif text.startswith('/setinterval'):
                try:
                    parts = text.split()
                    if len(parts) < 2:
                        send_to_telegram("❌ Використовуй: /setinterval [години], напр. /setinterval 1",
                                         chat_id=chat_id)
                    else:
                        hours_val = float(parts[1])
                        if hours_val < 0.167:
                            send_to_telegram("❌ Мінімум: 0.167 год (10 хв)", chat_id=chat_id)
                        elif hours_val > 24:
                            send_to_telegram("❌ Максимум: 24 год", chat_id=chat_id)
                        else:
                            check_interval = int(hours_val * 3600)
                            save_interval(check_interval)
                            send_to_telegram("✅ Інтервал змінено!\n\n" + build_interval_text(), chat_id=chat_id)
                except ValueError:
                    send_to_telegram("❌ Введи число, напр. /setinterval 1", chat_id=chat_id)

            elif text == '/clearstats':
                stats["posts_published"] = 0
                stats["errors"] = 0
                stats["start_time"] = datetime.now()
                published_urls.clear()
                published_history.clear()
                save_stats()
                save_history([])
                send_to_telegram("✅ Статистику та історію очищено!", chat_id=chat_id, reply_markup=kb_back())

            elif text == '/cancel':
                send_to_telegram("ℹ️ Немає активних дій для скасування", chat_id=chat_id)

            elif text == '/restart':
                send_to_telegram("🔄 Перезавантаження бота...", chat_id=chat_id)
                save_stats()
                import sys
                sys.exit(0)

    except Exception as e:
        logging.error(f"Error checking commands: {e}")


# ============================================================
# 🔄 ГОЛОВНИЙ ЦИКЛ
# ============================================================

if __name__ == "__main__":
    logging.info("=" * 60)
    logging.info("💰 DINEROLATAM RSS BOT v6.0")
    logging.info("📈 Noticias financieras para Latinoamérica")
    logging.info("=" * 60)

    load_stats()
    load_interval()
    load_pause_state()
    sources = load_sources()

    published_history = load_history()
    published_urls = set(item['url'] for item in published_history)

    handle_commands.last_update_id = 0

    hours = check_interval // 3600
    minutes = (check_interval % 3600) // 60

    logging.info("✅ Бот запущено!")
    logging.info(f"📢 Канал: {TELEGRAM_CHANNEL}")
    logging.info(f"👤 Admin ID: {ADMIN_USER_ID}")
    logging.info(f"📰 Джерел: {len(sources)}")
    logging.info(f"📚 Записів в історії: {len(published_history)}")
    logging.info(f"⏱ Інтервал: {hours} год {minutes} хв")
    logging.info(f"⏸ Пауза: {is_paused}")
    logging.info("=" * 60)

    print("=" * 60)
    print("💰 DINEROLATAM RSS BOT v6.0")
    print("📈 Noticias financieras para Latinoamérica")
    print("=" * 60)
    print("✅ Бот запущено!")
    print(f"📢 Канал: {TELEGRAM_CHANNEL}")
    print(f"👤 Admin ID: {ADMIN_USER_ID}")
    print(f"📰 Джерел: {len(sources)}")
    print(f"📚 Записів в історії: {len(published_history)}")
    print(f"⏱ Інтервал: {hours} год {minutes} хв")
    print(f"⏸ Пауза: {is_paused}")
    print("=" * 60)

    while True:
        try:
            handle_commands()

            logging.info(f"[{datetime.now()}] 🔍 Checking for news...")
            posts = check_for_updates()
            logging.info(f"[{datetime.now()}] ✅ Published {posts} posts")

            iterations = check_interval // 10
            for _ in range(iterations):
                time.sleep(10)
                handle_commands()

        except KeyboardInterrupt:
            logging.info("\n[!] Бот зупинено користувачем")
            save_stats()
            save_history(published_history)
            break
        except Exception as e:
            logging.error(f"[ERROR] {e}")
            time.sleep(60)
