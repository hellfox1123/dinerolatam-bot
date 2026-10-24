# 💵 Курси валют, аргентинські долари та інфляція — живі дані з відкритих API

import logging
from datetime import datetime
from email.utils import parsedate_to_datetime

import httpx

from .formatting import channel_line, escape_html

# ============================================================
# 💵 КУРСИ ВАЛЮТ — open.er-api.com (без ключа, оновлення ~раз на добу)
# ============================================================

CURRENCY_PAIRS = [
    ("MXN", "🇲🇽", "México"),
    ("COP", "🇨🇴", "Colombia"),
    ("ARS", "🇦🇷", "Argentina"),
    ("CLP", "🇨🇱", "Chile"),
    ("PEN", "🇵🇪", "Perú"),
    ("BRL", "🇧🇷", "Brasil"),
    ("UYU", "🇺🇾", "Uruguay"),
]

# Аргентинські курси — dolarapi.com. Для аргентинської аудиторії офіційний курс
# мало що означає: реально живуть за «blue», MEP і CCL.
AR_DOLLARS = [
    ("oficial", "Oficial"),
    ("blue", "Blue"),
    ("bolsa", "MEP"),
    ("contadoconliqui", "CCL"),
    ("tarjeta", "Tarjeta"),
]


async def fetch_usd_rates(client):
    """Повертає (rates_dict, дата оновлення) або (None, None)."""
    try:
        response = await client.get("https://open.er-api.com/v6/latest/USD", timeout=10)
        data = response.json()
        if data.get("result") == "success":
            updated = None
            if data.get("time_last_update_utc"):
                updated = parsedate_to_datetime(data["time_last_update_utc"])
            return data.get("rates", {}), updated
    except (httpx.HTTPError, ValueError) as e:
        logging.error(f"❌ Помилка отримання курсів валют: {e}")
    return None, None


async def fetch_ar_dollars(client):
    """Повертає {casa: {compra, venta, fechaActualizacion}} або None."""
    try:
        response = await client.get("https://dolarapi.com/v1/dolares", timeout=10)
        data = response.json()
        if isinstance(data, list):
            return {item.get("casa"): item for item in data if item.get("casa")}
    except (httpx.HTTPError, ValueError) as e:
        logging.error(f"❌ Помилка отримання курсів dolarapi: {e}")
    return None


def _fmt_ars(value):
    # 1565.5 → 1.565,50 (аргентинський формат)
    return f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _rates_lines(rates, updated):
    lines = []
    if updated:
        lines.append(f"Datos al {updated.strftime('%d/%m/%Y')} · fuente: open.er-api.com")
        lines.append("")
    for code, flag, country in CURRENCY_PAIRS:
        val = rates.get(code)
        if val is not None:
            lines.append(f"🇺🇸 USD → {flag} {code} ({country}): {val:,.2f}")
    eur_rate = rates.get("EUR")
    if eur_rate:
        lines.append(f"🇪🇺 EUR → 🇺🇸 USD: {(1 / eur_rate):,.4f}")
    return lines


def _ar_lines(ar):
    lines = ["🇦🇷 <b>Dólares en Argentina</b> (compra / venta, ARS)"]
    for casa, label in AR_DOLLARS:
        item = ar.get(casa)
        if not item or item.get("venta") is None:
            continue
        compra = _fmt_ars(item["compra"]) if item.get("compra") is not None else "—"
        lines.append(f"• {label}: {compra} / {_fmt_ars(item['venta'])}")

    oficial, blue = ar.get("oficial"), ar.get("blue")
    if oficial and blue and oficial.get("venta") and blue.get("venta"):
        gap = (blue["venta"] / oficial["venta"] - 1) * 100
        lines.append(f"📐 Brecha blue/oficial: {gap:+.1f}%")

    dates = [item.get("fechaActualizacion") for item in ar.values() if item.get("fechaActualizacion")]
    if dates:
        latest = max(datetime.fromisoformat(d.replace("Z", "+00:00")) for d in dates)
        lines.append(f"Actualizado: {latest.strftime('%d/%m/%Y %H:%M')} UTC · fuente: dolarapi.com")
    return lines


async def build_rates_post(client, channel_link, channel_name):
    """Готовий пост із курсами. Повертає None, якщо обидва API не відповіли —
    у такому разі бот НЕ публікує застарілі/вигадані цифри."""
    rates, updated = await fetch_usd_rates(client)
    ar = await fetch_ar_dollars(client)
    if not rates and not ar:
        return None

    lines = ["💵 <b>Cotización del dólar en Latinoamérica</b>", ""]
    if rates:
        lines.extend(_rates_lines(rates, updated))
        lines.append("")
    if ar:
        lines.extend(_ar_lines(ar))
        lines.append("")
    lines.append(channel_line(channel_link, channel_name))
    lines.append("")
    lines.append("#Dólar #Divisas #LatAm #Finanzas" + (" #DólarBlue #Argentina" if ar else ""))
    return "\n".join(lines)


# ============================================================
# 📈 ІНФЛЯЦІЯ — World Bank Open Data API (без ключа)
#     Індикатор FP.CPI.TOTL.ZG, mrnev=1 = остання наявна річна цифра по країні.
#     Це офіційна річна статистика, тому пост завжди чесно вказує рік даних.
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


async def fetch_inflation_data(client):
    codes = ";".join(WB_COUNTRIES.keys())
    url = f"https://api.worldbank.org/v2/country/{codes}/indicator/FP.CPI.TOTL.ZG"
    params = {"format": "json", "mrnev": 1, "per_page": 100}
    try:
        response = await client.get(url, params=params, timeout=20)
        data = response.json()
        if not isinstance(data, list) or len(data) < 2 or not data[1]:
            return None
        results = {}
        for item in data[1]:
            iso3 = item.get("countryiso3code")
            val = item.get("value")
            if iso3 and val is not None:
                results[iso3] = (val, item.get("date"))
        return results or None
    except (httpx.HTTPError, ValueError) as e:
        logging.error(f"❌ Помилка отримання даних інфляції: {e}")
        return None


async def build_inflation_post(client, channel_link, channel_name):
    """Готовий пост з інфляцією. Повертає None, якщо World Bank API не відповіло."""
    data = await fetch_inflation_data(client)
    if not data:
        return None

    rows = []
    for iso3, (flag, name) in WB_COUNTRIES.items():
        if iso3 in data:
            val, year = data[iso3]
            rows.append((val, flag, name, year))
    rows.sort(reverse=True)

    lines = ["📈 <b>Inflación en Latinoamérica</b>", ""]
    lines.append("Última cifra anual oficial disponible por país (Banco Mundial):")
    lines.append("")
    for val, flag, name, year in rows:
        lines.append(f"{flag} {escape_html(name)}: {val:.1f}% ({year})")
    lines.append("")
    lines.append("ℹ️ Cada país reporta su año más reciente disponible; los organismos "
                 "internacionales publican estas cifras con cierto rezago respecto a "
                 "los datos mensuales de cada banco central.")
    lines.append("")
    lines.append(channel_line(channel_link, channel_name))
    lines.append("")
    lines.append("#Inflación #Economía #LatAm")
    return "\n".join(lines)
