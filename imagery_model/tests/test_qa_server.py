from urban_tree_ml.qa_server import _inject_street_view_embed_key, _safe_curation_return


def test_street_view_embed_key_is_only_injected_when_configured() -> None:
    html = "<script>const streetViewEmbedApiKey = null;</script>"

    assert _inject_street_view_embed_key(html, None) == html
    assert _inject_street_view_embed_key(html, "browser-key") == (
        '<script>const streetViewEmbedApiKey = "browser-key";</script>'
    )


def test_curation_return_only_allows_internal_studio_views() -> None:
    assert _safe_curation_return(None) == "/registration"
    assert _safe_curation_return("/model?run=abc&chip=r000001_c000002") == (
        "/model?run=abc&chip=r000001_c000002"
    )
    assert _safe_curation_return("https://example.com/model") == "/registration"
    assert _safe_curation_return("//example.com/model") == "/registration"
    assert _safe_curation_return("/api/reviews") == "/registration"
