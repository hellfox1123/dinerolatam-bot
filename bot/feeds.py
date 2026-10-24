# 📰 Завантаження RSS, нормалізація посилань і вибір новин для публікації

import asyncio
import hashlib
import logging
import re
from calendar import timegm
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import feedparser
import httpx

from .formatting import clean_text, is_financial_news, normalize_for_match

FEED_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/rss+xml, application/xml, text/xml, */*',
}

FETCH_ATTEMPTS = 2
ENTRIES_PER_SOURCE = 15    # скільки найсвіжіших новин розглядаємо з кожного джерела
MAX_POSTS_PER_CYCLE = 5    # запобіжник від "заспамлення" каналу
MAX_PARALLEL_FETCHES = 5


def new_http_client():
    """Один спільний клієнт на весь бот: keep-alive з'єднання і таймаути за замовчуванням."""
    return httpx.AsyncClient(timeout=httpx.Timeout(15.0), follow_redirects=True, headers=FEED_HEADERS)


# ============================================================
# 🔗 КЛЮЧІ ДЛЯ ДЕДУПЛІКАЦІЇ
# ============================================================

_TRACKING_PARAMS = {"fbclid", "gclid", "mc_cid", "mc_eid", "ocid", "ref", "cmpid"}


def normalize_url(url):
    """Та сама стаття з різними utm-мітками, #якорем чи слешем у кінці
    має давати однаковий ключ."""
    parts = urlsplit((url or "").strip())
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in _TRACKING_PARAMS]
    host = parts.netloc.lower().removeprefix("www.")
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(("https", host, path, urlencode(sorted(query)), ""))


def title_key(title):
    """Ключ заголовка: та сама новина, перепублікована під іншим URL, не дублюється."""
    words = re.findall(r"\w+", normalize_for_match(clean_text(title)))
    if len(words) < 4:  # занадто короткі заголовки не порівнюємо — забагато збігів
        return ""
    return hashlib.sha1(" ".join(words).encode("utf-8")).hexdigest()


# ============================================================
# 📥 ЗАВАНТАЖЕННЯ
# ============================================================

@dataclass
class FeedResult:
    ok: bool
    entries: list
    title: str = ""
    error: str = ""


async def fetch_feed(client, url, attempts=FETCH_ATTEMPTS):
    error = ""
    for attempt in range(1, attempts + 1):
        try:
            response = await client.get(url)
            if response.status_code != 200:
                error = f"HTTP {response.status_code}"
            else:
                feed = await asyncio.to_thread(feedparser.parse, response.content)
                if feed.entries:
                    return FeedResult(True, feed.entries, feed.feed.get("title", ""))
                return FeedResult(False, [], feed.feed.get("title", ""), "порожній feed")
        except httpx.HTTPError as e:
            error = type(e).__name__
        logging.warning(f"⚠️ {url[:60]}: спроба {attempt}/{attempts} — {error}")
        if attempt < attempts:
            await asyncio.sleep(3)
    return FeedResult(False, [], error=error)


async def fetch_many(client, urls):
    semaphore = asyncio.Semaphore(MAX_PARALLEL_FETCHES)

    async def one(url):
        async with semaphore:
            return await fetch_feed(client, url)

    return await asyncio.gather(*(one(u) for u in urls))


async def validate_rss_url(client, url):
    """Повертає (ok, повідомлення, назва feed'у)."""
    if not re.match(r"^https?://", url or ""):
        return False, "❌ Це не схоже на посилання. Воно має починатися з http:// або https://", ""
    result = await fetch_feed(client, url, attempts=1)
    if not result.ok:
        return False, (f"❌ Не вдалося отримати новини ({result.error or 'немає записів'}).\n"
                       "Можливо, це не прямий шлях до feed'у (наприклад, потрібен "
                       "конкретний розділ на кшталт /rss/economia замість просто /rss/)."), ""
    if not result.title:
        return False, "❌ RSS feed не має заголовка. Можливо це не валідний RSS.", ""
    first = result.entries[0]
    if not first.get("title") and not first.get("link"):
        return False, "❌ Новини в RSS не мають заголовків або посилань.", ""
    return True, f"📰 Назва: {result.title}\n📊 Новин у feed: {len(result.entries)}", result.title


# ============================================================
# 🧩 КАНДИДАТИ НА ПУБЛІКАЦІЮ
# ============================================================

@dataclass
class Candidate:
    source_id: int
    source_name: str
    title: str
    link: str
    description: str
    image_url: str | None
    published: datetime | None
    url_key: str
    title_key: str


def entry_datetime(entry):
    for field in ("published_parsed", "updated_parsed"):
        parsed = entry.get(field)
        if parsed:
            return datetime.fromtimestamp(timegm(parsed), tz=timezone.utc)
    return None


def extract_image_url(entry):
    for media in entry.get("media_content", []) or []:
        if media.get('medium') == 'image' or (media.get('type') or '').startswith('image'):
            if media.get('url'):
                return media['url']

    thumbs = entry.get("media_thumbnail") or []
    if thumbs and thumbs[0].get('url'):
        return thumbs[0]['url']

    for enclosure in entry.get("enclosures", []) or []:
        if (enclosure.get('type') or '').startswith('image') and enclosure.get('href'):
            return enclosure['href']

    for html_chunk in (entry.get('description', ''), (entry.get('content') or [{}])[0].get('value', '')):
        match = re.search(r'<img[^>]+src="([^">]+)"', html_chunk or '')
        if match:
            return match.group(1)

    image = entry.get('image')
    if isinstance(image, dict) and image.get('href'):
        return image['href']
    return None


def build_candidates(source, entries, is_published, max_age_hours, now=None):
    """Нові фінансові новини одного джерела, найсвіжіші першими."""
    now = now or datetime.now(timezone.utc)
    oldest_allowed = now - timedelta(hours=max_age_hours)
    result = []
    seen = set()
    for entry in entries[:ENTRIES_PER_SOURCE]:
        link = entry.get('link', '')
        title = clean_text(entry.get('title', ''))
        description = entry.get('description', entry.get('summary', ''))
        if not link or not title:
            continue
        published = entry_datetime(entry)
        if published and published < oldest_allowed:
            continue
        u_key, t_key = normalize_url(link), title_key(title)
        if u_key in seen or is_published(u_key, t_key):
            continue
        if not is_financial_news(title, clean_text(description)):
            continue
        seen.add(u_key)
        result.append(Candidate(source.id, source.name, title, link, description,
                                extract_image_url(entry), published, u_key, t_key))
    result.sort(key=lambda c: c.published or now, reverse=True)
    return result


def select_round_robin(per_source, limit=MAX_POSTS_PER_CYCLE):
    """По одній найсвіжішій новині з кожного джерела по колу, поки не набереться limit.
    Раніше джерела обходились по порядку, і перші в списку забирали весь ліміт.
    Результат — від старішої до новішої, щоб у каналі був природний порядок."""
    far_past = datetime.min.replace(tzinfo=timezone.utc)
    queues = [list(items) for items in per_source if items]
    picked = []
    seen_titles = set()
    while queues and len(picked) < limit:
        # Кожне коло починаємо з джерела, у якого найсвіжіша новина: інакше при
        # 11 джерелах і ліміті 5 завжди вигравали б перші 5 джерел у списку
        queues.sort(key=lambda q: q[0].published or far_past, reverse=True)
        for queue in list(queues):
            if len(picked) >= limit:
                break
            while queue:
                candidate = queue.pop(0)
                # Одна й та сама новина у двох джерелах — беремо лише раз
                if candidate.title_key and candidate.title_key in seen_titles:
                    continue
                if candidate.title_key:
                    seen_titles.add(candidate.title_key)
                picked.append(candidate)
                break
            if not queue:
                queues.remove(queue)
    picked.sort(key=lambda c: c.published or far_past)
    return picked
