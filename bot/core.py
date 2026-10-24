# 🔄 Основна логіка: перевірка новин, публікація, планувальник задач

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timezone
from zoneinfo import ZoneInfo

import httpx
from telegram import LinkPreviewOptions
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError

from .config import Config
from .feeds import build_candidates, fetch_many, select_round_robin
from .formatting import escape_html, format_post
from .market import build_rates_post
from .storage import FAIL_ALERT_THRESHOLD, Storage

DEFAULT_INTERVAL = 10800   # 3 години
NO_PREVIEW = LinkPreviewOptions(is_disabled=True)


@dataclass
class Services:
    cfg: Config
    db: Storage
    http: httpx.AsyncClient
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    check_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    # ---------- налаштування з бази ----------

    @property
    def interval(self):
        return self.db.get("check_interval", DEFAULT_INTERVAL)

    @property
    def paused(self):
        return self.db.get("paused", False)

    @property
    def daily_rates_enabled(self):
        return self.db.get("daily_rates_enabled", self.cfg.daily_rates_enabled)

    @property
    def daily_rates_time(self):
        return self.db.get("daily_rates_time", self.cfg.daily_rates_time)


def svc(context_or_app):
    app = getattr(context_or_app, "application", context_or_app)
    return app.bot_data["svc"]


def parse_hhmm(value):
    """'09:30' → (9, 30) або None."""
    try:
        hours, minutes = (int(p) for p in value.strip().split(":"))
    except (ValueError, AttributeError):
        return None
    if 0 <= hours < 24 and 0 <= minutes < 60:
        return hours, minutes
    return None


# ============================================================
# 📤 НАДСИЛАННЯ
# ============================================================

async def notify_admin(bot, s, text):
    try:
        await bot.send_message(s.cfg.admin_id, text, parse_mode=ParseMode.HTML,
                               link_preview_options=NO_PREVIEW)
    except TelegramError as e:
        logging.error(f"❌ Не вдалося написати адміну: {e}")


async def publish_text(bot, s, text):
    try:
        await bot.send_message(s.cfg.channel, text, parse_mode=ParseMode.HTML,
                               link_preview_options=NO_PREVIEW)
        return True
    except TelegramError as e:
        logging.error(f"❌ Не вдалося опублікувати в канал: {e}")
        return False


async def publish_candidate(bot, s, candidate):
    message = format_post(candidate.title, candidate.link, candidate.description,
                          candidate.source_name, s.cfg.channel_link, s.cfg.channel_name,
                          s.cfg.max_desc_chars)
    sent = False
    if candidate.image_url:
        try:
            await bot.send_photo(s.cfg.channel, photo=candidate.image_url, caption=message,
                                 parse_mode=ParseMode.HTML)
            sent = True
        except BadRequest as e:
            logging.warning(f"⚠️ sendPhoto не вдався ({e}), пробуємо як текстове повідомлення")
        except TelegramError as e:
            logging.error(f"❌ Помилка надсилання фото: {e}")

    if not sent:
        sent = await publish_text(bot, s, message)

    if sent:
        s.db.add_history(candidate.link, candidate.url_key, candidate.title,
                         candidate.title_key, candidate.source_name)
        s.db.incr("posts_published")
    else:
        s.db.incr("errors")
    return sent


# ============================================================
# 🔍 ПЕРЕВІРКА НОВИН
# ============================================================

async def run_news_check(app):
    """Повертає кількість опублікованих постів або None, якщо перевірка вже йде."""
    s = svc(app)
    if s.check_lock.locked():
        return None

    async with s.check_lock:
        sources = s.db.list_sources()
        logging.info(f"🔍 Перевіряю {len(sources)} джерел...")
        results = await fetch_many(s.http, [src.url for src in sources])

        per_source = []
        for source, result in zip(sources, results):
            # 🩺 Сповіщаємо адміна, якщо джерело кілька перевірок поспіль не дає новин
            if s.db.record_source_result(source.id, result.ok):
                await notify_admin(
                    app.bot, s,
                    f"⚠️ Джерело <b>{escape_html(source.name)}</b> не віддає новин уже "
                    f"{FAIL_ALERT_THRESHOLD} перевірок поспіль ({escape_html(result.error)}).\n"
                    f"URL: <code>{escape_html(source.url)}</code>\n\n"
                    f"Перевір його кнопкою «🕵️ Перевірити джерела» в меню — можливо, "
                    f"посилання застаріло і його треба замінити."
                )
            if result.ok:
                per_source.append(build_candidates(source, result.entries, s.db.is_published,
                                                   s.cfg.max_news_age_hours))

        # 🧊 Перший запуск з порожньою історією: лише запам'ятовуємо поточні новини,
        # щоб не продублювати в каналі те, що вже публікувала попередня версія бота
        if not s.db.get("warmed_up"):
            s.db.set("warmed_up", True)
            if s.db.history_count() == 0:
                for candidate in (c for items in per_source for c in items):
                    s.db.add_history(candidate.link, candidate.url_key, candidate.title,
                                     candidate.title_key, candidate.source_name)
                logging.info("🧊 Перший запуск: поточні новини позначено як опубліковані")
                await notify_admin(app.bot, s, "🧊 Перший запуск з новою базою: поточні новини "
                                               "запам'ятовано без публікації, щоб уникнути дублів. "
                                               "Нові новини публікуватимуться з наступної перевірки.")
                s.db.set("last_check", datetime.now(timezone.utc).isoformat(timespec="seconds"))
                return 0

        posts = 0
        for candidate in select_round_robin(per_source):
            if await publish_candidate(app.bot, s, candidate):
                posts += 1
            await asyncio.sleep(2)

        s.db.set("last_check", datetime.now(timezone.utc).isoformat(timespec="seconds"))
        logging.info(f"✅ Опубліковано {posts} постів")
        return posts


async def verify_sources_report(s):
    """Діагностика: перевіряє кожне джерело наживо."""
    sources = s.db.list_sources()
    if not sources:
        return "❌ Немає налаштованих джерел."
    results = await fetch_many(s.http, [src.url for src in sources])
    lines = ["🕵️ <b>Перевірка RSS-джерел</b>", ""]
    for i, (source, result) in enumerate(zip(sources, results), 1):
        if result.ok:
            lines.append(f"{i}. ✅ {escape_html(source.name)} — {len(result.entries)} новин")
        else:
            lines.append(f"{i}. ❌ {escape_html(source.name)} — {escape_html(result.error or 'немає відповіді')}")
        lines.append(f"    <code>{escape_html(source.url)}</code>")
    return "\n".join(lines)


# ============================================================
# ⏰ ПЛАНУВАЛЬНИК
# ============================================================

async def news_job(context):
    if svc(context).paused:
        logging.info("⏸ Бот на паузі, пропускаємо перевірку")
        return
    await run_news_check(context.application)


async def daily_rates_job(context):
    s = svc(context)
    if s.paused:
        logging.info("⏸ Бот на паузі, щоденний пост з курсами пропущено")
        return
    post = await build_rates_post(s.http, s.cfg.channel_link, s.cfg.channel_name)
    if not post or not await publish_text(context.bot, s, post):
        await notify_admin(context.bot, s, "⚠️ Не вдалося опублікувати щоденний пост з курсами валют.")


def schedule_news_job(app, first=10):
    s = svc(app)
    for job in app.job_queue.get_jobs_by_name("news"):
        job.schedule_removal()
    app.job_queue.run_repeating(news_job, interval=s.interval, first=first, name="news")


def schedule_daily_rates(app):
    s = svc(app)
    for job in app.job_queue.get_jobs_by_name("daily_rates"):
        job.schedule_removal()
    hhmm = parse_hhmm(s.daily_rates_time)
    if not s.daily_rates_enabled or not hhmm:
        return
    at = dtime(hhmm[0], hhmm[1], tzinfo=ZoneInfo(s.cfg.timezone))
    app.job_queue.run_daily(daily_rates_job, time=at, name="daily_rates")
