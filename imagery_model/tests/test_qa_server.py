from urban_tree_ml.qa_server import _inject_street_view_embed_key


def test_street_view_embed_key_is_only_injected_when_configured() -> None:
    html = "<script>const streetViewEmbedApiKey = null;</script>"

    assert _inject_street_view_embed_key(html, None) == html
    assert _inject_street_view_embed_key(html, "browser-key") == (
        '<script>const streetViewEmbedApiKey = "browser-key";</script>'
    )
