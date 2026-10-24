# ⚙️ Конфігурація з змінних середовища (Railway) або локального файлу .env

import os
import sys
import logging
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PROJECT_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Config:
    token: str
    channel: str
    admin_id: int
    channel_link: str
    channel_name: str
    data_dir: Path
    on_railway_without_volume: bool
    max_desc_chars: int
    timezone: str
    daily_rates_time: str
    daily_rates_enabled: bool
    max_news_age_hours: int


def _env_bool(name, default):
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _data_dir():
    # DATA_DIR має пріоритет; інакше — шлях до Railway Volume, якщо його підключено.
    # Без Volume файлова система Railway очищується при кожному деплої.
    explicit = os.environ.get("DATA_DIR") or os.environ.get("RAILWAY_VOLUME_MOUNT_PATH")
    if explicit:
        return Path(explicit), False
    on_railway = bool(os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("RAILWAY_PROJECT_ID"))
    return PROJECT_DIR / "data", on_railway


def load_config(require_telegram=True):
    """Читає конфіг. require_telegram=False — для --dry-run, коли токен не потрібен."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    channel = os.environ.get("TELEGRAM_CHANNEL", "").strip()
    admin_raw = os.environ.get("ADMIN_USER_ID", "").strip()

    if require_telegram:
        missing = [name for name, val in (("TELEGRAM_BOT_TOKEN", token),
                                          ("TELEGRAM_CHANNEL", channel),
                                          ("ADMIN_USER_ID", admin_raw)) if not val]
        if missing:
            logging.error(f"❌ ПОМИЛКА: не задано змінні середовища: {', '.join(missing)}")
            sys.exit(1)

    try:
        admin_id = int(admin_raw) if admin_raw else 0
    except ValueError:
        logging.error("❌ ПОМИЛКА: ADMIN_USER_ID має бути числом!")
        sys.exit(1)

    data_dir, no_volume = _data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)

    return Config(
        token=token,
        channel=channel,
        admin_id=admin_id,
        channel_link=os.environ.get("CHANNEL_LINK", "https://t.me/dinerolatam"),
        channel_name=os.environ.get("CHANNEL_NAME", "Dinero Latam"),
        data_dir=data_dir,
        on_railway_without_volume=no_volume,
        max_desc_chars=int(os.environ.get("MAX_DESC_CHARS", "600")),
        timezone=os.environ.get("BOT_TIMEZONE", "America/Mexico_City"),
        daily_rates_time=os.environ.get("DAILY_RATES_TIME", "09:00"),
        daily_rates_enabled=_env_bool("DAILY_RATES_ENABLED", True),
        max_news_age_hours=int(os.environ.get("MAX_NEWS_AGE_HOURS", "24")),
    )
