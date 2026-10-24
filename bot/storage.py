# 💾 Сховище на SQLite замість набору JSON-файлів.
# Файл бази лежить у DATA_DIR — на Railway це має бути підключений Volume,
# інакше історія і джерела зникають при кожному деплої.

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse

HISTORY_MAX = 2000         # скільки останніх опублікованих новин зберігаємо
FAIL_ALERT_THRESHOLD = 5   # після скількох невдалих перевірок поспіль сповіщати адміна

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    fail_streak INTEGER NOT NULL DEFAULT 0,
    alerted INTEGER NOT NULL DEFAULT 0,
    added_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL,
    url_key TEXT NOT NULL,
    title TEXT NOT NULL,
    title_key TEXT NOT NULL,
    source TEXT NOT NULL,
    published_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_history_url_key ON history(url_key);
CREATE INDEX IF NOT EXISTS idx_history_title_key ON history(title_key);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# Відомі назви джерел — використовуються, коли назва не задана явно
_KNOWN_NAMES = {
    "expansion.com": "Expansión",
    "elpais.com": "El País",
    "elconfidencial.com": "El Confidencial",
    "elfinanciero.com.mx": "El Financiero",
    "portafolio.co": "Portafolio",
    "infobae.com": "Infobae",
    "bloomberglinea.com": "Bloomberg Línea",
    "gestion.pe": "Gestión",
    "expansion.mx": "Expansión México",
    "larepublica.co": "La República",
    "df.cl": "Diario Financiero",
}


def fallback_source_name(url):
    host = (urlparse(url).hostname or "").lower()
    for domain, name in _KNOWN_NAMES.items():
        if host == domain or host.endswith("." + domain):
            return name
    return host.removeprefix("www.") or "Fuente desconocida"


def _now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Source:
    id: int
    url: str
    name: str
    fail_streak: int
    alerted: bool


class Storage:
    def __init__(self, db_path):
        self.db_path = db_path
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def close(self):
        self.conn.close()

    # ---------- ⚙️ Налаштування ----------

    def get(self, key, default=None):
        row = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def set(self, key, value):
        with self.conn:
            self.conn.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, json.dumps(value)),
            )

    def incr(self, key, amount=1):
        self.set(key, self.get(key, 0) + amount)

    def reset_stats(self):
        self.set("posts_published", 0)
        self.set("errors", 0)
        self.set("stats_since", _now_iso())

    # ---------- 📰 Джерела ----------

    def list_sources(self):
        rows = self.conn.execute("SELECT * FROM sources ORDER BY id").fetchall()
        return [Source(r["id"], r["url"], r["name"], r["fail_streak"], bool(r["alerted"])) for r in rows]

    def get_source(self, source_id):
        r = self.conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
        return Source(r["id"], r["url"], r["name"], r["fail_streak"], bool(r["alerted"])) if r else None

    def has_source(self, url):
        return self.conn.execute("SELECT 1 FROM sources WHERE url = ?", (url,)).fetchone() is not None

    def add_source(self, url, name=None):
        try:
            with self.conn:
                self.conn.execute(
                    "INSERT INTO sources(url, name, added_at) VALUES(?, ?, ?)",
                    (url, name or fallback_source_name(url), _now_iso()),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def remove_source(self, source_id):
        source = self.get_source(source_id)
        if source:
            with self.conn:
                self.conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))
        return source

    def record_source_result(self, source_id, ok):
        """Оновлює лічильник невдач. Повертає True, якщо саме зараз треба
        сповістити адміна (джерело FAIL_ALERT_THRESHOLD разів поспіль без новин)."""
        source = self.get_source(source_id)
        if not source:
            return False
        if ok:
            streak, alerted, should_alert = 0, False, False
        else:
            streak = source.fail_streak + 1
            should_alert = streak >= FAIL_ALERT_THRESHOLD and not source.alerted
            alerted = source.alerted or should_alert
        with self.conn:
            self.conn.execute(
                "UPDATE sources SET fail_streak = ?, alerted = ? WHERE id = ?",
                (streak, int(alerted), source_id),
            )
        return should_alert

    # ---------- 📚 Історія ----------

    def is_published(self, url_key, title_key):
        row = self.conn.execute(
            "SELECT 1 FROM history WHERE url_key = ? OR (title_key != '' AND title_key = ?) LIMIT 1",
            (url_key, title_key),
        ).fetchone()
        return row is not None

    def add_history(self, url, url_key, title, title_key, source_name):
        with self.conn:
            self.conn.execute(
                "INSERT INTO history(url, url_key, title, title_key, source, published_at) "
                "VALUES(?, ?, ?, ?, ?, ?)",
                (url, url_key, title, title_key, source_name, _now_iso()),
            )
            self.conn.execute(
                "DELETE FROM history WHERE id NOT IN "
                "(SELECT id FROM history ORDER BY id DESC LIMIT ?)",
                (HISTORY_MAX,),
            )

    def latest_history(self, limit=10):
        rows = self.conn.execute(
            "SELECT title, source, published_at FROM history ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def history_count(self):
        return self.conn.execute("SELECT COUNT(*) FROM history").fetchone()[0]

    # ---------- 🔁 Перший запуск / міграція зі старих JSON-файлів v6 ----------

    def bootstrap(self, seed_file, legacy_dir, url_key_fn, title_key_fn):
        if self.get("bootstrapped"):
            return

        # Джерела: rss_sources.json з репозиторію (рядки або {"url", "name"})
        try:
            with open(seed_file, "r", encoding="utf-8") as f:
                seed = json.load(f).get("sources", [])
        except (OSError, ValueError) as e:
            logging.error(f"⚠️ Не вдалося прочитати {seed_file}: {e}")
            seed = []
        for item in seed:
            if isinstance(item, str):
                self.add_source(item)
            elif isinstance(item, dict) and item.get("url"):
                self.add_source(item["url"], item.get("name"))

        # Старі JSON-файли v6 (якщо бот запускався з цієї ж папки)
        def read_legacy(name):
            path = legacy_dir / name
            if not path.exists():
                return None
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, ValueError):
                return None

        history = read_legacy("published_history.json") or {}
        for item in history.get("items", []):
            url = item.get("url")
            if url:
                title = item.get("title", "")
                with self.conn:
                    self.conn.execute(
                        "INSERT INTO history(url, url_key, title, title_key, source, published_at) "
                        "VALUES(?, ?, ?, ?, ?, ?)",
                        (url, url_key_fn(url), title, title_key_fn(title),
                         item.get("source", ""), item.get("published_at", _now_iso())),
                    )

        interval = read_legacy("bot_interval.json")
        if interval and interval.get("interval"):
            self.set("check_interval", int(interval["interval"]))
        pause = read_legacy("bot_pause.json")
        if pause is not None:
            self.set("paused", bool(pause.get("paused", False)))
        stats = read_legacy("bot_stats.json")
        if stats:
            self.set("posts_published", int(stats.get("posts_published", 0)))
            self.set("errors", int(stats.get("errors", 0)))

        if self.get("stats_since") is None:
            self.set("stats_since", _now_iso())
        self.set("bootstrapped", True)
        logging.info(f"✅ Ініціалізовано базу: {len(self.list_sources())} джерел, "
                     f"{self.history_count()} записів історії")
