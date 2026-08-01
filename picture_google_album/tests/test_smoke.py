"""Offline smoke tests for picture_google_album (no network)."""

from __future__ import annotations

import server

SAMPLE_PAGE = (
    '<meta property="og:title" content="Family &amp; Friends">'
    'AF_initDataCallback({data:[["AF1QipAbc-123",'
    '["https://lh3.googleusercontent.com/pw/AAAA",1600,1200,null],'
    "1618858980000,null],"
    '["AF1QipDef_456",'
    '["https://lh3.googleusercontent.com/pw/BBBB",900,1600,null],'
    "1618900000000,null]]});"
)


def test_empty_options_returns_friendly_error():
    out = server.fetch({}, {}, ctx={})
    assert out["url"] is None
    assert "photos.app.goo.gl" in out["error"]


def test_share_url_normalisation():
    assert server._share_url("https://photos.app.goo.gl/Xy9") == "https://photos.app.goo.gl/Xy9"
    assert server._share_url("photos.google.com/share/AF1?key=k") == (
        "https://photos.google.com/share/AF1?key=k"
    )
    assert server._share_url("Xy9") == "https://photos.app.goo.gl/Xy9"
    assert server._share_url("") == ""


def test_parse_photos_rich_entries():
    photos = server._parse_photos(SAMPLE_PAGE)
    assert len(photos) == 2
    assert photos[0]["id"] == "AF1QipAbc-123"
    assert photos[0]["width"] == 1600
    assert photos[0]["taken_ms"] == 1618858980000
    assert server._parse_title(SAMPLE_PAGE) == "Family & Friends"


def test_parse_photos_fallback_urls_only():
    page = '["https://lh3.googleusercontent.com/pw/CCCC",800,600,'
    photos = server._parse_photos(page)
    assert len(photos) == 1
    assert photos[0]["id"] is None
    assert photos[0]["base"].endswith("CCCC")


def test_orientation_and_display_url():
    photos = server._parse_photos(SAMPLE_PAGE)
    landscape = server._filter_orientation(photos, "landscape")
    portrait = server._filter_orientation(photos, "portrait")
    assert [p["base"] for p in landscape] == ["https://lh3.googleusercontent.com/pw/AAAA"]
    assert [p["base"] for p in portrait] == ["https://lh3.googleusercontent.com/pw/BBBB"]
    assert server._display_url(photos[0]) == (
        "https://lh3.googleusercontent.com/pw/AAAA=w1600-h1200"
    )


def test_fetch_uses_cached_manifest(tmp_path, monkeypatch):
    def boom(url):
        return "", "should not fetch, manifest is cached"

    monkeypatch.setattr(server, "_get_page", boom)
    link = "https://photos.app.goo.gl/Xy9"
    manifest = {
        "album": "Cached",
        "photos": server._parse_photos(SAMPLE_PAGE),
        "fetched_at": 0,
    }
    cache = tmp_path / f"manifest_{server._cache_key(link)}.json"
    server._write_cache(cache, manifest)

    out = server.fetch({"album": link, "mode": "sequential"}, {}, ctx={"data_dir": str(tmp_path)})
    assert out["stream"] == "Cached"
    assert out["count"] == 2
    assert out["index"] == 1
    assert out["url"].startswith("https://lh3.googleusercontent.com/pw/")
    assert out["date"].startswith("2021-04-19")

    out2 = server.fetch({"album": link, "mode": "sequential"}, {}, ctx={"data_dir": str(tmp_path)})
    assert out2["index"] == 2
    out3 = server.fetch({"album": link, "mode": "sequential"}, {}, ctx={"data_dir": str(tmp_path)})
    assert out3["index"] == 1  # wraps
