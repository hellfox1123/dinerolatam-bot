# 🎮 Меню на inline-кнопках і текстові команди.
# Кожен екран описано один раз (функції screen_*) — і кнопки, і команди
# показують той самий текст і клавіатуру.

import logging
from datetime import datetime, timezone

from telegram import InlineKeyboardButton as Btn, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler,
                          ContextTypes, MessageHandler, filters)

from .core import (NO_PREVIEW, parse_hhmm, publish_text, run_news_check, schedule_daily_rates,
                   schedule_news_job, svc, verify_sources_report)
from .feeds import validate_rss_url
from .formatting import escape_html
from .market import build_inflation_post, build_rates_post

INTERVAL_CHOICES = {600: "10 хв", 1800: "30 хв", 3600: "1 год",
                    10800: "3 год", 21600: "6 год", 43200: "12 год"}


def _fmt_interval(seconds):
    return f"{seconds // 3600} год {(seconds % 3600) // 60} хв"


def _short(name, limit=28):
    return name if len(name) <= limit else name[:limit - 1] + "…"


def _kb(rows):
    return InlineKeyboardMarkup([[Btn(text, callback_data=data) for text, data in row] for row in rows])


BACK = [("🔙 Меню", "menu")]


# ============================================================
# 🧱 ЕКРАНИ
# ============================================================

def screen_menu(s):
    text = (
        f"🤖 <b>DineroLatam Bot — панель керування</b>\n\n"
        f"📊 Стан: {'⏸ На паузі' if s.paused else '✅ Працює'}\n"
        f"📰 Джерел: {len(s.db.list_sources())}\n"
        f"✅ Опубліковано: {s.db.get('posts_published', 0)}\n"
        f"⏱ Інтервал: {_fmt_interval(s.interval)}\n"
        f"🗓 Щоденні курси: {s.daily_rates_time if s.daily_rates_enabled else 'вимкнено'}\n\n"
        f"Обери розділ кнопкою нижче 👇"
    )
    pause = ("▶️ Відновити", "pause_toggle") if s.paused else ("⏸ Пауза", "pause_toggle")
    return text, _kb([
        [("📊 Статистика", "stats"), ("📰 Джерела", "sources")],
        [("⏱ Інтервал", "interval"), pause],
        [("🔄 Перевірити зараз", "check_now"), ("🕵️ Перевірити джерела", "verify_sources")],
        [("💵 Курси", "rates"), ("📈 Інфляція", "inflation")],
        [("📰 Останні новини", "latest"), ("❓ Довідка", "help")],
    ])


def screen_stats(s):
    posts, errors = s.db.get("posts_published", 0), s.db.get("errors", 0)
    total = posts + errors
    efficiency = f"{posts / total * 100:.1f}%" if total else "Немає даних"
    last_check = s.db.get("last_check")
    last_check_text = (datetime.fromisoformat(last_check).astimezone().strftime('%Y-%m-%d %H:%M:%S')
                       if last_check else "Ніколи")
    since = s.db.get("stats_since", "")[:10]
    uptime = str(datetime.now(timezone.utc) - s.started_at).split('.')[0]
    text = (
        f"📊 <b>Статистика бота</b>\n\n"
        f"📰 Опубліковано постів: {posts}\n"
        f"❌ Помилок: {errors}\n"
        f"📅 Рахуємо з: {since}\n"
        f"⏱ Остання перевірка: {last_check_text}\n"
        f"🕐 Час роботи з останнього запуску: {uptime}\n"
        f"💾 Новин в історії: {s.db.history_count()}\n"
        f"📡 Активних джерел: {len(s.db.list_sources())}\n"
        f"⏱ Інтервал перевірки: {_fmt_interval(s.interval)}\n\n"
        f"Ефективність: {efficiency} успішних публікацій"
    )
    return text, _kb([BACK])


def screen_sources(s):
    sources = s.db.list_sources()
    if not sources:
        text = "📰 <b>RSS джерела</b>\n\nСписок порожній — додай перше джерело кнопкою нижче."
    else:
        lines = [f"📰 <b>RSS джерела ({len(sources)})</b>", ""]
        for i, src in enumerate(sources, 1):
            warn = f" ⚠️ {src.fail_streak} невдач поспіль" if src.fail_streak else ""
            lines.append(f"{i}. {escape_html(src.name)}{warn}")
            lines.append(f"    <code>{escape_html(src.url)}</code>")
        lines.append("")
        lines.append("Натисни на джерело нижче, щоб видалити його, або додай нове.")
        text = "\n".join(lines)
    rows = [[(f"🗑 {_short(src.name)}", f"src_del:{src.id}")] for src in sources]
    rows.append([("➕ Додати джерело", "src_add")])
    rows.append(BACK)
    return text, _kb(rows)


def screen_interval(s, note=""):
    text = (
        f"{note}⏱ <b>Інтервал перевірки новин</b>\n\n"
        f"Поточний: {_fmt_interval(s.interval)}\n\n"
        f"Обери новий інтервал кнопкою нижче або командою /setinterval [години].\n"
        f"Наступна перевірка буде через обраний інтервал від моменту зміни."
    )
    items = [(label, f"int_set:{sec}") for sec, label in INTERVAL_CHOICES.items()]
    return text, _kb([items[0:2], items[2:4], items[4:6], BACK])


def screen_latest(s):
    items = s.db.latest_history(10)
    if not items:
        return "📭 Поки немає опублікованих новин.", _kb([BACK])
    lines = ["📰 <b>Останні опубліковані новини</b>", ""]
    for item in items:
        title = escape_html(item['title'][:100])
        published_at = item['published_at'][:16].replace('T', ' ')
        lines.append(f"• <b>{title}</b>\n   {escape_html(item['source'])} · {published_at} UTC")
    lines.append("")
    lines.append(f"📊 Всього збережено в історії: {s.db.history_count()}")
    return "\n".join(lines), _kb([BACK])


def screen_rates(s, note=""):
    status = f"✅ щодня о {s.daily_rates_time} ({s.cfg.timezone})" if s.daily_rates_enabled else "❌ вимкнено"
    text = (
        f"{note}💵 <b>Курси валют</b>\n\n"
        f"Пост: курси USD до валют LatAm + долари Аргентини (oficial, blue, MEP, CCL, tarjeta).\n\n"
        f"🗓 Автопублікація в канал: {status}\n"
        f"Змінити час: /setratestime ГГ:ХХ"
    )
    toggle = "🗓 Вимкнути щоденний пост" if s.daily_rates_enabled else "🗓 Увімкнути щоденний пост"
    return text, _kb([
        [("👁 Попередній перегляд", "rates_preview")],
        [("📢 Опублікувати в канал зараз", "rates_publish")],
        [(toggle, "daily_toggle")],
        BACK,
    ])


def screen_inflation(s, note=""):
    text = (f"{note}📈 <b>Інфляція</b>\n\n"
            "Остання офіційна річна інфляція по країнах LatAm (Banco Mundial).")
    return text, _kb([
        [("👁 Попередній перегляд", "infl_preview")],
        [("📢 Опублікувати в канал зараз", "infl_publish")],
        BACK,
    ])


HELP_TEXT = (
    "❓ <b>Допомога</b>\n\n"
    "Керування ботом — через кнопки в меню (/menu або /start).\n\n"
    "<b>Розділи меню:</b>\n"
    "📊 Статистика — лічильники постів/помилок\n"
    "📰 Джерела — перегляд, додавання, видалення RSS-джерел\n"
    "⏱ Інтервал — як часто бот перевіряє новини\n"
    "⏸️/▶️ Пауза/Відновити — зупинити чи відновити всі автопублікації\n"
    "🔄 Перевірити зараз — примусова перевірка новин негайно\n"
    "🕵️ Перевірити джерела — скільки новин реально віддає кожне джерело\n"
    "💵 Курси — перегляд/публікація курсів, щоденний автопост\n"
    "📈 Інфляція — перегляд/публікація інфляції по країнах\n"
    "📰 Останні новини — що вже опубліковано\n\n"
    "<b>Команди:</b> /stats /sources /addsource /latest /check /verifysources "
    "/rates /inflation /pause /resume /setinterval [год] /setratestime ГГ:ХХ "
    "/clearstats /ping /restart /cancel"
)


ADD_SOURCE_PROMPT = (
    "➕ <b>Додавання нового RSS джерела</b>\n\n"
    "Надішли URL RSS feed повідомленням у чат.\n\n"
    "<b>Приклади:</b>\n"
    "<code>https://www.expansion.com/rss/economia.xml</code>\n"
    "<code>https://example.com/feed/</code>\n\n"
    "⚠️ Я перевірю посилання перед додаванням. Щоб скасувати — /cancel."
)


# ============================================================
# 🛠 ДОПОМІЖНЕ
# ============================================================

async def show(update, text, keyboard=None):
    """Для кнопки — редагує поточне повідомлення, для команди — надсилає нове.
    Повертає повідомлення, щоб його можна було оновити ще раз."""
    query = update.callback_query
    if query:
        try:
            return await query.edit_message_text(text, parse_mode=ParseMode.HTML,
                                                 reply_markup=keyboard, link_preview_options=NO_PREVIEW)
        except BadRequest as e:
            if "not modified" in str(e).lower():
                return query.message
            raise
    return await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML,
                                                     reply_markup=keyboard, link_preview_options=NO_PREVIEW)


async def update_progress(message, text, keyboard=None):
    await message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard,
                            link_preview_options=NO_PREVIEW)


async def send_new(update, text, keyboard=None):
    await update.effective_chat.send_message(text, parse_mode=ParseMode.HTML, reply_markup=keyboard,
                                             link_preview_options=NO_PREVIEW)


# ============================================================
# 🎬 ДІЇ (спільні для кнопок і команд)
# ============================================================

async def act_check_now(update, context):
    s = svc(context)
    if s.paused:
        await show(update, "⚠️ Бот на паузі! Спочатку віднови роботу.", _kb([BACK]))
        return
    progress = await show(update, "🔄 Перевіряю джерела...")
    posts = await run_news_check(context.application)
    if posts is None:
        await update_progress(progress, "⏳ Перевірка вже виконується, зачекай кілька хвилин.", _kb([BACK]))
    else:
        await update_progress(progress, f"✅ Перевірку завершено.\n📰 Опубліковано: {posts}", _kb([BACK]))


async def act_verify_sources(update, context):
    progress = await show(update, "🕵️ Перевіряю джерела, це може зайняти кілька секунд...")
    await update_progress(progress, await verify_sources_report(svc(context)), _kb([BACK]))


async def act_set_paused(update, context, paused):
    s = svc(context)
    s.db.set("paused", paused)
    note = "⏸ Бот на паузі — автопублікації зупинено.\n\n" if paused else "▶️ Бот відновив роботу!\n\n"
    text, kb = screen_menu(s)
    await show(update, note + text, kb)


async def act_set_interval(update, context, seconds):
    s = svc(context)
    s.db.set("check_interval", seconds)
    schedule_news_job(context.application, first=seconds)
    await show(update, *screen_interval(s, "✅ Інтервал оновлено!\n\n"))


async def act_preview(update, context, kind):
    s = svc(context)
    builder = build_rates_post if kind == "rates" else build_inflation_post
    post = await builder(s.http, s.cfg.channel_link, s.cfg.channel_name)
    if not post:
        await send_new(update, "⚠️ Не вдалося отримати дані зараз. Спробуй пізніше.")
        return
    publish = "rates_publish" if kind == "rates" else "infl_publish"
    await send_new(update, post, _kb([[("📢 Опублікувати в канал", publish)], BACK]))


async def act_publish(update, context, kind):
    s = svc(context)
    builder = build_rates_post if kind == "rates" else build_inflation_post
    post = await builder(s.http, s.cfg.channel_link, s.cfg.channel_name)
    ok = bool(post) and await publish_text(context.bot, s, post)
    await send_new(update, "✅ Опубліковано в канал!" if ok else "⚠️ Не вдалося опублікувати. Спробуй пізніше.",
                   _kb([BACK]))


async def act_add_source(update, context, url):
    s = svc(context)
    if s.db.has_source(url):
        await send_new(update, "⚠️ Це джерело вже додано! Надішли інше посилання або /cancel.")
        return
    progress = await update.effective_message.reply_text("🔍 Перевіряю RSS feed... Зачекайте...")
    ok, message, feed_title = await validate_rss_url(s.http, url)
    if not ok:
        await progress.edit_text(f"{message}\n\nНадішли інше посилання або /cancel щоб скасувати.")
        return
    s.db.add_source(url, feed_title.strip()[:60] or None)
    context.user_data.pop("awaiting", None)
    text, kb = screen_sources(s)
    await update_progress(progress, f"✅ <b>Джерело додано!</b>\n{escape_html(message)}\n\n{text}", kb)


# ============================================================
# 🔘 КНОПКИ
# ============================================================

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    s = svc(context)
    if query.from_user.id != s.cfg.admin_id:
        await query.answer("⛔ Немає доступу", show_alert=True)
        return
    await query.answer()

    data = query.data or ""
    action, _, arg = data.partition(":")

    simple_screens = {
        "menu": screen_menu, "stats": screen_stats, "sources": screen_sources,
        "interval": screen_interval, "latest": screen_latest,
        "rates": screen_rates, "inflation": screen_inflation,
    }
    if action in simple_screens:
        context.user_data.pop("awaiting", None)
        await show(update, *simple_screens[action](s))
    elif action == "help":
        await show(update, HELP_TEXT, _kb([BACK]))
    elif action == "src_add":
        context.user_data["awaiting"] = "add_source"
        await show(update, ADD_SOURCE_PROMPT, _kb([BACK]))
    elif action == "src_del" and arg.isdigit():
        source = s.db.get_source(int(arg))
        if not source:
            await show(update, *screen_sources(s))
            return
        await show(update, f"Видалити джерело «{escape_html(source.name)}»?\n<code>{escape_html(source.url)}</code>",
                   _kb([[(f"✅ Так, видалити «{_short(source.name)}»", f"src_delc:{source.id}")],
                        [("❌ Скасувати", "sources")]]))
    elif action == "src_delc" and arg.isdigit():
        s.db.remove_source(int(arg))
        await show(update, *screen_sources(s))
    elif action == "int_set" and arg.isdigit() and int(arg) in INTERVAL_CHOICES:
        await act_set_interval(update, context, int(arg))
    elif action == "pause_toggle":
        await act_set_paused(update, context, not s.paused)
    elif action == "check_now":
        await act_check_now(update, context)
    elif action == "verify_sources":
        await act_verify_sources(update, context)
    elif action == "rates_preview":
        await act_preview(update, context, "rates")
    elif action == "rates_publish":
        await act_publish(update, context, "rates")
    elif action == "infl_preview":
        await act_preview(update, context, "inflation")
    elif action == "infl_publish":
        await act_publish(update, context, "inflation")
    elif action == "daily_toggle":
        s.db.set("daily_rates_enabled", not s.daily_rates_enabled)
        schedule_daily_rates(context.application)
        await show(update, *screen_rates(s))


# ============================================================
# ⌨️ ТЕКСТОВІ КОМАНДИ
# ============================================================

def _screen_command(screen):
    async def handler(update, context):
        context.user_data.pop("awaiting", None)
        await show(update, *screen(svc(context)))
    return handler


async def cmd_help(update, context):
    await show(update, HELP_TEXT, _kb([BACK]))


async def cmd_ping(update, context):
    await show(update, "🏓 Понг! Бот онлайн ✅")


async def cmd_addsource(update, context):
    context.user_data["awaiting"] = "add_source"
    await show(update, ADD_SOURCE_PROMPT)


async def cmd_cancel(update, context):
    if context.user_data.pop("awaiting", None):
        await show(update, "✅ Дію скасовано", _kb([BACK]))
    else:
        await show(update, "ℹ️ Немає активних дій для скасування")


async def cmd_pause(update, context):
    if svc(context).paused:
        await show(update, "⚠️ Бот вже на паузі!")
    else:
        await act_set_paused(update, context, True)


async def cmd_resume(update, context):
    if not svc(context).paused:
        await show(update, "ℹ️ Бот вже працює!")
    else:
        await act_set_paused(update, context, False)


async def cmd_check(update, context):
    await act_check_now(update, context)


async def cmd_verify(update, context):
    await act_verify_sources(update, context)


async def cmd_rates(update, context):
    await act_preview(update, context, "rates")


async def cmd_inflation(update, context):
    await act_preview(update, context, "inflation")


async def cmd_setinterval(update, context):
    try:
        hours = float(context.args[0].replace(",", "."))
    except (IndexError, ValueError):
        await show(update, "❌ Використовуй: /setinterval [години], напр. /setinterval 1 або /setinterval 0.5")
        return
    seconds = int(hours * 3600)
    if seconds < 600:
        await show(update, "❌ Мінімум: 10 хв (0.17 год)")
    elif seconds > 86400:
        await show(update, "❌ Максимум: 24 год")
    else:
        await act_set_interval(update, context, seconds)


async def cmd_setratestime(update, context):
    hhmm = parse_hhmm(context.args[0]) if context.args else None
    if not hhmm:
        await show(update, "❌ Використовуй: /setratestime ГГ:ХХ, напр. /setratestime 09:00")
        return
    s = svc(context)
    s.db.set("daily_rates_time", f"{hhmm[0]:02d}:{hhmm[1]:02d}")
    s.db.set("daily_rates_enabled", True)
    schedule_daily_rates(context.application)
    await show(update, *screen_rates(s, "✅ Час щоденного поста оновлено!\n\n"))


async def cmd_clearstats(update, context):
    # Історію опублікованих новин НЕ чистимо — інакше бот повторно опублікує старі новини
    svc(context).db.reset_stats()
    await show(update, "✅ Лічильники статистики обнулено (історія публікацій збережена).", _kb([BACK]))


async def cmd_restart(update, context):
    await show(update, "🔄 Перезавантаження бота...")
    logging.info("🔄 Запит на перезапуск від адміна")
    # stop_running() штатно зупиняє polling і підтверджує Telegram усі отримані оновлення,
    # тому /restart не прийде повторно після старту (у v6 бот міг перезапускатися по колу)
    context.application.bot_data["restart_requested"] = True
    context.application.stop_running()


async def on_text(update, context):
    if context.user_data.get("awaiting") == "add_source":
        await act_add_source(update, context, (update.message.text or "").strip())


async def on_error(update, context):
    logging.error("❌ Помилка під час обробки оновлення", exc_info=context.error)


def register_handlers(app: Application, admin_id):
    admin = filters.User(user_id=admin_id)
    commands = {
        "start": _screen_command(screen_menu),
        "menu": _screen_command(screen_menu),
        "stats": _screen_command(screen_stats),
        "status": _screen_command(screen_stats),
        "sources": _screen_command(screen_sources),
        "removesource": _screen_command(screen_sources),
        "getinterval": _screen_command(screen_interval),
        "latest": _screen_command(screen_latest),
        "help": cmd_help,
        "ping": cmd_ping,
        "addsource": cmd_addsource,
        "cancel": cmd_cancel,
        "pause": cmd_pause,
        "resume": cmd_resume,
        "check": cmd_check,
        "verifysources": cmd_verify,
        "rates": cmd_rates,
        "inflation": cmd_inflation,
        "setinterval": cmd_setinterval,
        "setratestime": cmd_setratestime,
        "clearstats": cmd_clearstats,
        "restart": cmd_restart,
    }
    for name, handler in commands.items():
        app.add_handler(CommandHandler(name, handler, filters=admin))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(admin & filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(on_error)
