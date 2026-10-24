from bot.formatting import (CAPTION_HARD_LIMIT, clean_text, format_post, get_emoji_for_news,
                            get_hashtags_for_news, is_financial_news, smart_truncate)


def test_smart_truncate_keeps_short_text():
    assert smart_truncate("Hola mundo.", 100) == "Hola mundo."


def test_smart_truncate_cuts_on_sentence_end():
    text = "Primera frase bastante larga aquí. Segunda frase que no cabe en el límite."
    assert smart_truncate(text, 50) == "Primera frase bastante larga aquí."


def test_smart_truncate_falls_back_to_word_boundary():
    result = smart_truncate("palabra " * 30, 50)
    assert result.endswith("…")
    assert len(result) <= 51
    assert "palabr…" not in result


def test_keywords_match_whole_words_only():
    # Раніше "gas" спрацьовував на «Vargas», "euro" — на «neurología»
    assert not is_financial_news("Vargas visita el hospital de neurología", "")
    assert get_emoji_for_news("Vargas visita el hospital", "") == "📰"


def test_keywords_ignore_accents_and_plurals():
    assert is_financial_news("Suben las inversiones extranjeras", "")
    assert is_financial_news("Los PRECIOS se disparan", "")
    assert get_emoji_for_news("Caen las acciones", "") == "📈"


def test_hashtags_country_and_no_duplicates():
    tags = get_hashtags_for_news("El peso mexicano y el dólar", "Banco de México")
    assert "#México" in tags
    assert len(tags.split()) == len(set(tags.split()))


def test_clean_text_decodes_entities_once():
    assert clean_text("<p>Bonos &amp; acciones</p>") == "Bonos & acciones"


def test_format_post_escapes_and_fits_caption():
    post = format_post(
        title="Inflación &lt;alta&gt; &amp; precios",
        link='https://example.com/a?x="1"&y=2',
        description="<p>" + "La inflación sube en la región. " * 80 + "</p>",
        source_name="Fuente",
        channel_link="https://t.me/dinerolatam",
        channel_name="Dinero Latam",
    )
    assert "&lt;alta&gt; &amp; precios" in post
    assert 'href="https://example.com/a?x=&quot;1&quot;&amp;y=2"' in post
    visible = clean_text(post.replace("\n", " "))
    assert len(visible) <= CAPTION_HARD_LIMIT
