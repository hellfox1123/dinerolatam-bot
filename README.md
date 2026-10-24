# dinerolatam-bot

Telegram-бот, що публікує фінансові новини Латинської Америки в канал
[Dinero Latam](https://t.me/dinerolatam): збирає RSS, фільтрує фінансові новини,
форматує пости, публікує курси валют (включно з dólar blue/MEP/CCL в Аргентині)
та інфляцію. Керування — через меню на inline-кнопках у приватному чаті з ботом.

## Локальний запуск

```bash
py -3.11 -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt
copy .env.example .env      # заповни TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL, ADMIN_USER_ID
.venv\Scripts\python main.py
```

Перевірити, що бот опублікував би просто зараз, без Telegram і без змін в історії:

```bash
.venv\Scripts\python main.py --dry-run
```

Тести:

```bash
.venv\Scripts\python -m pytest
```

> ⚠️ Один токен — один процес. Якщо бот уже працює на Railway, локальний запуск з тим самим
> токеном отримає `409 Conflict`. Для експериментів створи окремого тестового бота в
> @BotFather і тестовий канал.

## Деплой на Railway

1. Variables: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHANNEL`, `ADMIN_USER_ID` (решта — див. `.env.example`).
2. **Підключи Volume** (Settings → Volumes, напр. mount path `/data`). Бот сам підхоплює
   `RAILWAY_VOLUME_MOUNT_PATH` і зберігає там `bot.db` та `bot.log`. Без Volume база
   стирається при кожному деплої.
3. Start command з `Procfile`: `python main.py`.

## Структура

| Файл | Що робить |
|---|---|
| `main.py` | точка входу, `--dry-run` |
| `bot/config.py` | змінні середовища |
| `bot/log.py` | логування в stdout і файл, токен вирізається |
| `bot/storage.py` | SQLite: джерела, історія, налаштування, статистика; міграція з JSON v6 |
| `bot/feeds.py` | завантаження RSS, дедуплікація, вибір новин по колу між джерелами |
| `bot/formatting.py` | фільтр фінансових новин, емодзі, хештеги, формат поста |
| `bot/market.py` | курси (open.er-api.com, dolarapi.com), інфляція (World Bank) |
| `bot/core.py` | перевірка новин, публікація, планувальник |
| `bot/handlers.py` | меню та команди |
| `rss_sources.json` | початковий список джерел (використовується лише при першому запуску) |

## Команди адміна

`/menu` · `/stats` · `/sources` · `/addsource` · `/latest` · `/check` · `/verifysources` ·
`/rates` · `/inflation` · `/pause` · `/resume` · `/setinterval [год]` ·
`/setratestime ГГ:ХХ` · `/clearstats` · `/ping` · `/restart` · `/cancel`
