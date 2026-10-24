# 🎯 Емодзі, хештеги, фільтр фінансових новин і форматування поста

import html
import re
import unicodedata
from functools import lru_cache

CAPTION_HARD_LIMIT = 1024


# ============================================================
# 🔤 ПОШУК КЛЮЧОВИХ СЛІВ
#     Шукаємо цілі слова (з можливим закінченням множини -s/-es), а не підрядки:
#     раніше "gas" спрацьовував на «Vargas», "euro" — на «neurología».
#     Наголоси прибираємо з обох боків, щоб «inversión» знаходило «inversiones».
# ============================================================

def strip_accents(text):
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize_for_match(text):
    return strip_accents(text).lower()


@lru_cache(maxsize=None)
def _words_pattern(words):
    alternatives = "|".join(re.escape(normalize_for_match(w)) for w in words)
    return re.compile(rf"\b(?:{alternatives})(?:s|es)?\b")


def contains_any(normalized_text, words):
    return bool(_words_pattern(tuple(words)).search(normalized_text))


# ============================================================
# 🎯 ЕМОДЗІ ТА ХЕШТЕГИ
# ============================================================

_EMOJI_RULES = [
    (("dólar", "euro", "peso", "moneda", "divisa", "tipo de cambio"), "💵"),
    (("bolsa", "acción", "inversión", "mercado"), "📈"),
    (("inflación", "ipc", "precio", "economía"), "📊"),
    (("banco", "crédito", "préstamo", "hipoteca"), "🏦"),
    (("petróleo", "energía", "gas", "gasolina", "combustible"), "⛽"),
    (("bitcoin", "cripto", "criptomoneda", "ethereum", "crypto"), "₿"),
    (("vivienda", "inmobiliaria", "alquiler"), "🏠"),
    (("impuesto", "hacienda", "salario"), "💰"),
    (("fintech", "tecnología", "digital"), "📱"),
    (("empresa", "compañía", "negocio"), "🏢"),
]

_HASHTAG_RULES = [
    (("dólar", "euro", "peso", "moneda", "divisa"), ["#Dólar", "#Divisas", "#LatAm"]),
    (("inflación", "ipc", "precio"), ["#Inflación", "#Precios"]),
    (("bolsa", "acción", "inversión"), ["#Bolsa", "#Inversiones"]),
    (("banco", "crédito", "préstamo"), ["#Banca", "#Créditos"]),
    (("petróleo", "energía", "gas"), ["#Energía", "#Petróleo"]),
    (("bitcoin", "cripto", "criptomoneda", "ethereum"), ["#Cripto", "#Bitcoin"]),
]

_COUNTRY_TAGS = [
    (("méxico", "mexicano", "mexicana"), "#México"),
    (("colombia", "colombiano", "colombiana"), "#Colombia"),
    (("argentina", "argentino"), "#Argentina"),
    (("chile", "chileno", "chilena"), "#Chile"),
    (("perú", "peruano", "peruana"), "#Perú"),
    (("brasil", "brasileño", "brasileña"), "#Brasil"),
    (("españa", "español", "española"), "#España"),
]


def get_emoji_for_news(title, description):
    text = normalize_for_match(f"{title} {description}")
    for words, emoji in _EMOJI_RULES:
        if contains_any(text, words):
            return emoji
    return "📰"


def get_hashtags_for_news(title, description):
    text = normalize_for_match(f"{title} {description}")
    hashtags = ["#Economía"]
    for words, tags in _HASHTAG_RULES:
        if contains_any(text, words):
            hashtags.extend(tags)
    country = next((tag for words, tag in _COUNTRY_TAGS if contains_any(text, words)), None)

    # Прибираємо дублікати, зберігаючи порядок; тег країни не відрізається лімітом
    unique = list(dict.fromkeys(hashtags))
    if country:
        return " ".join(unique[:5] + [country])
    return " ".join(unique[:6])


# ============================================================
# 💼 ФІЛЬТР ФІНАНСОВИХ НОВИН
# ============================================================

_FINANCIAL_KEYWORDS = (
    "economía", "económico", "económica", "finanzas", "financiero", "financiera",
    "inversión", "inversor", "bolsa", "mercado",
    "dólar", "peso", "euro", "divisa", "tipo de cambio",
    "inflación", "ipc", "precio", "canasta básica",
    "tasa", "tasa de interés", "tipo de interés", "banco central", "reserva federal", "fmi",
    "cripto", "criptomoneda", "bitcoin", "ethereum", "acción", "ahorro", "crédito", "deuda",
    "pib", "recesión", "desempleo", "empleo", "salario", "sueldo",
    "impuesto", "hacienda", "presupuesto", "arancel", "comercio exterior",
    "exportación", "importación", "vivienda", "hipoteca", "alquiler",
    "petróleo", "commodity", "materia prima",
)


def is_financial_news(title, description):
    return contains_any(normalize_for_match(f"{title} {description}"), _FINANCIAL_KEYWORDS)


# ============================================================
# ✂️ ОЧИЩЕННЯ, ОБРІЗАННЯ ТА ФОРМАТУВАННЯ
# ============================================================

def escape_html(text):
    """Екранування тексту для HTML parse_mode Telegram."""
    return html.escape(text or "", quote=False)


def escape_attr(text):
    """Екранування для значення атрибута (href): лапки теж треба екранувати,
    інакше `"` в URL ламає розмітку."""
    return html.escape(text or "", quote=True)


def clean_text(raw):
    """Прибирає HTML-теги й декодує сутності (&amp; → &). Без html.unescape
    сутності з RSS екранувались повторно і в пості з'являлось «&amp;»."""
    no_tags = re.sub(r'<[^>]+>', ' ', raw or '')
    return re.sub(r'\s+', ' ', html.unescape(no_tags)).strip()


def smart_truncate(text, limit):
    """Обрізає текст по кінцю речення (крапка/знак оклику/знак питання),
    а не посеред слова чи фрази. Якщо в межах ліміту немає межі речення —
    обрізає по останньому пробілу."""
    text = re.sub(r'\s+', ' ', (text or '').strip())
    if len(text) <= limit:
        return text

    cut = text[:limit]

    best_end = max(cut.rfind(marker) for marker in ('. ', '! ', '? '))

    # Використовуємо межу речення, тільки якщо вона не занадто рано обрізає текст
    if best_end > limit * 0.4:
        return cut[:best_end + 1].strip()

    last_space = cut.rfind(' ')
    if last_space > 0:
        cut = cut[:last_space]
    return cut.rstrip(" ,;:-") + "…"


def channel_line(channel_link, channel_name):
    return f'📢 Fuente: <a href="{escape_attr(channel_link)}">{escape_html(channel_name)}</a>'


def format_post(title, link, description, source_name, channel_link, channel_name, max_desc_chars=600):
    """Формує HTML-підпис поста:
      <емодзі> <жирний заголовок>

      <опис, обрізаний по реченню>

      🔗 Detalles: <посилання на статтю>

      📢 Fuente: <посилання на власний канал>

      <хештеги>
    """
    clean_title = clean_text(title)
    clean_desc = clean_text(description)

    emoji = get_emoji_for_news(clean_title, clean_desc)
    hashtags = get_hashtags_for_news(clean_title, clean_desc)

    title_html = f"{emoji} <b>{escape_html(clean_title)}</b>"
    details_html = f'🔗 Detalles: <a href="{escape_attr(link)}">{escape_html(source_name)}</a>'
    channel_html = channel_line(channel_link, channel_name)

    # Telegram рахує ліміт caption (1024) по видимому тексту, без HTML-тегів.
    # Тому бюджет опису рахуємо від видимої довжини решти частин поста.
    visible_fixed = (
        len(f"{emoji} {clean_title}")
        + len(f"🔗 Detalles: {source_name}")
        + len(f"📢 Fuente: {channel_name}")
        + len(hashtags)
        + len("\n\n") * 4
    )
    budget = min(max_desc_chars, max(150, CAPTION_HARD_LIMIT - visible_fixed - 20))

    parts = [title_html]
    desc_final = smart_truncate(clean_desc, budget)
    if desc_final:
        parts.append(escape_html(desc_final))
    parts.extend([details_html, channel_html, hashtags])
    return "\n\n".join(parts)
