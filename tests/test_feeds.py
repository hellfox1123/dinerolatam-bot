import time
from datetime import datetime, timedelta, timezone

from bot.feeds import Candidate, build_candidates, normalize_url, select_round_robin, title_key
from bot.storage import Source, Storage


def _candidate(source_id, n, hours_ago, tkey=None):
    published = datetime(2026, 1, 1, tzinfo=timezone.utc) - timedelta(hours=hours_ago)
    return Candidate(source_id, f"S{source_id}", f"t{n}", f"https://x/{n}", "", None, published,
                     f"k{n}", tkey if tkey is not None else f"t{n}")


def test_normalize_url_strips_tracking_and_fragment():
    a = normalize_url("https://www.Example.com/news/1/?utm_source=tg&id=5#top")
    b = normalize_url("http://example.com/news/1?id=5")
    assert a == b


def test_title_key_ignores_case_accents_and_punctuation():
    assert title_key("El dólar sube: récord histórico!") == title_key("el dolar sube record historico")
    assert title_key("Corto") == ""


def test_round_robin_gives_every_source_a_chance():
    first = [_candidate(1, i, i) for i in range(10)]
    second = [_candidate(2, 100 + i, i) for i in range(2)]
    picked = select_round_robin([first, second], limit=4)
    assert {c.source_id for c in picked} == {1, 2}
    assert sum(c.source_id == 2 for c in picked) == 2
    # публікуємо від старішої до новішої
    assert [c.published for c in picked] == sorted(c.published for c in picked)


def test_round_robin_prefers_freshest_sources_when_many():
    # 6 джерел, ліміт 3: мають виграти джерела зі свіжішими новинами, а не перші в списку
    per_source = [[_candidate(src, src, hours_ago=10 - src)] for src in range(1, 7)]
    picked = select_round_robin(per_source, limit=3)
    assert {c.source_id for c in picked} == {4, 5, 6}


def test_round_robin_skips_same_story_from_two_sources():
    picked = select_round_robin([[_candidate(1, 1, 0, "same")], [_candidate(2, 2, 0, "same")]])
    assert len(picked) == 1


def test_build_candidates_filters_old_published_and_non_financial():
    now = datetime(2026, 1, 2, tzinfo=timezone.utc)

    def entry(n, title, hours_ago):
        ts = (now - timedelta(hours=hours_ago)).timetuple()
        return {"link": f"https://x/{n}", "title": title, "description": "",
                "published_parsed": time.struct_time(ts)}

    entries = [
        entry(1, "El dólar sube con fuerza hoy", 1),
        entry(2, "La inflación baja en México este mes", 48),     # занадто стара
        entry(3, "Gana el equipo local el partido", 1),           # не фінанси
        entry(4, "El banco central sube la tasa de interés", 2),  # вже опублікована
    ]
    published = {normalize_url("https://x/4")}
    result = build_candidates(Source(1, "u", "S", 0, False), entries,
                              lambda u, t: u in published, max_age_hours=24, now=now)
    assert [c.link for c in result] == ["https://x/1"]


def test_storage_dedup_and_fail_alert(tmp_path):
    db = Storage(tmp_path / "t.db")
    db.add_source("https://a/rss", "A")
    src = db.list_sources()[0]
    alerts = [db.record_source_result(src.id, False) for _ in range(6)]
    assert alerts == [False, False, False, False, True, False]
    assert db.record_source_result(src.id, True) is False

    db.add_history("https://a/1", "k1", "Titulo", "t1", "A")
    assert db.is_published("k1", "zzz")
    assert db.is_published("other", "t1")
    assert not db.is_published("other", "")
    db.close()
