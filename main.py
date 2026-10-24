# 📦 main.py - Бот для DineroLatam v7.0
# Точка входу. Логіка розкладена по модулях у папці bot/:
#   config.py     — змінні середовища
#   log.py        — логування (stdout + файл, токен вирізається)
#   storage.py    — SQLite: джерела, історія, налаштування, статистика
#   feeds.py      — завантаження RSS, дедуплікація, вибір новин
#   formatting.py — фільтр, емодзі, хештеги, формат поста
#   market.py     — курси валют, долари Аргентини, інфляція
#   core.py       — перевірка новин, публікація, планувальник
#   handlers.py   — меню на кнопках і команди
#
# Запуск:            python main.py
# Перевірка без Telegram (нічого не публікує):  python main.py --dry-run

import argparse
import asyncio
import logging
import os
import sys
import tempfile
from pathlib import Path

from telegram import Update
from telegram.ext import AIORateLimiter, Application

from bot import __version__
from bot.config import PROJECT_DIR, load_config
from bot.core import Services, schedule_daily_rates, schedule_news_job
from bot.feeds import build_candidates, fetch_many, new_http_client, normalize_url, select_round_robin, title_key
from bot.formatting import format_post
from bot.handlers import register_handlers
from bot.log import setup_logging
from bot.market import build_inflation_post, build_rates_post
from bot.storage import Storage

SEED_FILE = PROJECT_DIR / "rss_sources.json"


def open_storage(cfg):
    db = Storage(cfg.data_dir / "bot.db")
    # Старі JSON-файли v6 лежали в робочій папці — переносимо їх у базу при першому запуску
    db.bootstrap(SEED_FILE, Path.cwd(), normalize_url, title_key)
    return db


def run_bot():
    cfg = load_config()
    setup_logging(secrets=[cfg.token], log_file=cfg.data_dir / "bot.log")

    logging.info("=" * 60)
    logging.info(f"💰 DINEROLATAM RSS BOT v{__version__}")
    logging.info("=" * 60)
    if cfg.on_railway_without_volume:
        logging.warning("⚠️ Railway Volume не підключено: база в контейнері зітреться при наступному "
                        "деплої, і бот може повторно опублікувати старі новини. Підключи Volume "
                        "(Settings → Volumes) — шлях підхопиться автоматично.")

    db = open_storage(cfg)

    async def post_init(app):
        app.bot_data["svc"] = Services(cfg=cfg, db=db, http=new_http_client())
        s = app.bot_data["svc"]
        schedule_news_job(app)
        schedule_daily_rates(app)
        logging.info(f"✅ Бот запущено! Канал: {cfg.channel} · джерел: {len(db.list_sources())} · "
                     f"історія: {db.history_count()} · інтервал: {s.interval // 60} хв · "
                     f"пауза: {s.paused} · щоденні курси: "
                     f"{s.daily_rates_time if s.daily_rates_enabled else 'вимк.'} ({cfg.timezone})")

    async def post_shutdown(app):
        s = app.bot_data.get("svc")
        if s:
            await s.http.aclose()
        db.close()

    app = (
        Application.builder()
        .token(cfg.token)
        .rate_limiter(AIORateLimiter(max_retries=3))   # автоматично чекає retry_after при 429
        .concurrent_updates(True)                      # меню відповідає навіть під час перевірки новин
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    register_handlers(app, cfg.admin_id)
    app.run_polling(allowed_updates=[Update.MESSAGE, Update.CALLBACK_QUERY])

    if app.bot_data.get("restart_requested"):
        logging.info("🔄 Перезапуск процесу...")
        os.execv(sys.executable, [sys.executable] + sys.argv)


async def dry_run():
    """Завантажує всі джерела й друкує пости, які були б опубліковані.
    Нічого не надсилає і не змінює історію — працює з тимчасовою копією бази."""
    cfg = load_config(require_telegram=False)
    setup_logging()
    with tempfile.TemporaryDirectory() as tmp:
        db = Storage(Path(tmp) / "dry.db")
        db.bootstrap(SEED_FILE, Path(tmp), normalize_url, title_key)
        real_db_path = cfg.data_dir / "bot.db"
        real_db = Storage(real_db_path) if real_db_path.exists() else None
        is_published = real_db.is_published if real_db else db.is_published
        sources = (real_db or db).list_sources() or db.list_sources()

        async with new_http_client() as http:
            results = await fetch_many(http, [src.url for src in sources])
            per_source = []
            print("\n===== ДЖЕРЕЛА =====")
            for source, result in zip(sources, results):
                candidates = build_candidates(source, result.entries, is_published,
                                              cfg.max_news_age_hours) if result.ok else []
                per_source.append(candidates)
                status = f"✅ {len(result.entries)} новин, кандидатів: {len(candidates)}" if result.ok \
                    else f"❌ {result.error}"
                print(f"{source.name}: {status}")

            for candidate in select_round_robin(per_source):
                post = format_post(candidate.title, candidate.link, candidate.description,
                                   candidate.source_name, cfg.channel_link, cfg.channel_name,
                                   cfg.max_desc_chars)
                print(f"\n===== ПОСТ ({len(post)} симв. HTML, фото: {candidate.image_url or '—'}) =====")
                print(post)

            print("\n===== КУРСИ =====")
            print(await build_rates_post(http, cfg.channel_link, cfg.channel_name))
            print("\n===== ІНФЛЯЦІЯ =====")
            print(await build_inflation_post(http, cfg.channel_link, cfg.channel_name))

        if real_db:
            real_db.close()
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DineroLatam Telegram bot")
    parser.add_argument("--dry-run", action="store_true",
                        help="показати, що бот опублікував би зараз, нічого не надсилаючи")
    args = parser.parse_args()

    if args.dry_run:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        asyncio.run(dry_run())
    else:
        run_bot()
