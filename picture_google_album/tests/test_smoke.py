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
    assert (
        server._share_url("https://photos.app.goo.gl/Xy9")
        == "https://photos.app.goo.gl/Xy9"
    )
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


def test_parse_photos_rich_entries_2026_nested_arrays():
    # Mid-2026 page shape: nested arrays inside the media descriptor,
    # verbatim structure from a real share page.
    page = (
        '[["AF1QipPY5sWHzRcBbIjsO4oAJveQm2AFvxA8o_sbf2Jo",'
        '["https://lh3.googleusercontent.com/pw/AP1GczN3REph",4032,2268,'
        "null,null,null,null,null,[null,null,1],[9073751]],"
        '1735427730483,"T1TGVJ9_fE_p-Bv0MvMfSVrnW-Q",39600000]]'
    )
    photos = server._parse_photos(page)
    assert len(photos) == 1
    assert photos[0]["id"] == "AF1QipPY5sWHzRcBbIjsO4oAJveQm2AFvxA8o_sbf2Jo"
    assert photos[0]["width"] == 4032
    assert photos[0]["height"] == 2268
    assert photos[0]["taken_ms"] == 1735427730483


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
    assert [p["base"] for p in landscape] == [
        "https://lh3.googleusercontent.com/pw/AAAA"
    ]
    assert [p["base"] for p in portrait] == [
        "https://lh3.googleusercontent.com/pw/BBBB"
    ]
    assert server._display_url(photos[0]) == (
        "https://lh3.googleusercontent.com/pw/AAAA=w1600-h1200"
    )


def test_fetch_uses_cached_manifest(tmp_path, monkeypatch):
    def boom(url):
        return "", "", "should not fetch, manifest is cached"

    monkeypatch.setattr(server, "_get_page", boom)
    link = "https://photos.app.goo.gl/Xy9"
    manifest = {
        "schema": server.MANIFEST_SCHEMA,
        "album": "Cached",
        "photos": server._parse_photos(SAMPLE_PAGE),
        "fetched_at": 0,
    }
    cache = tmp_path / f"manifest_{server._cache_key(link)}.json"
    server._write_cache(cache, manifest)

    out = server.fetch(
        {"album": link, "mode": "sequential"}, {}, ctx={"data_dir": str(tmp_path)}
    )
    assert out["stream"] == "Cached"
    assert out["count"] == 2
    assert out["index"] == 1
    assert out["url"].startswith("https://lh3.googleusercontent.com/pw/")
    assert out["date"].startswith("2021-04-19")

    out2 = server.fetch(
        {"album": link, "mode": "sequential"}, {}, ctx={"data_dir": str(tmp_path)}
    )
    assert out2["index"] == 2
    out3 = server.fetch(
        {"album": link, "mode": "sequential"}, {}, ctx={"data_dir": str(tmp_path)}
    )
    assert out3["index"] == 1  # wraps


# Verbatim shape from a real share page (photo entry then video entry).
# The video's trailing metadata object carries protobuf field 76647426
# (duration + stream info); photo entries never have that key.
VIDEO_PAGE = (
    '[["AF1QipPhoto111",'
    '["https://lh3.googleusercontent.com/pw/PHOTO",4000,3000,'
    "null,null,null,null,null,[null,null,1],[10066836],2],"
    '1535348376000,"5pSCFkAMa0JJcZrcq0BekVBLHS0",32400000,1565370026893,'
    '["AF1QipAlbum"],[[2],[19],[22]],2,'
    '{"15":15110,"101428965":[0,"vddo73h1"]}],'
    '["AF1QipVideo222",'
    '["https://lh3.googleusercontent.com/pw/VIDEO",1080,1920,'
    "null,null,null,null,null,[null,null,1]],"
    '1584355524000,"sip8Y0vN3BHtqXQt60FbuMIfFio",-25200000,'
    '["AF1QipAlbum"],[[2],[19],[22]],2,'
    '{"15":419943,"76647426":[null,null,null,null,null,4]}]]'
)


def test_parse_photos_flags_videos():
    photos = server._parse_photos(VIDEO_PAGE)
    assert len(photos) == 2
    by_id = {p["id"]: p for p in photos}
    assert by_id["AF1QipPhoto111"]["is_video"] is False
    assert by_id["AF1QipVideo222"]["is_video"] is True


def test_fetch_skips_videos_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr(
        server, "_get_page", lambda url: ("", "", "no network in tests")
    )
    link = "https://photos.app.goo.gl/Vid1"
    manifest = {
        "schema": server.MANIFEST_SCHEMA,
        "album": "Mixed",
        "photos": server._parse_photos(VIDEO_PAGE),
        "fetched_at": 0,
    }
    cache = tmp_path / f"manifest_{server._cache_key(link)}.json"
    server._write_cache(cache, manifest)

    out = server.fetch({"album": link}, {}, ctx={"data_dir": str(tmp_path)})
    assert out["count"] == 1
    assert "PHOTO" in out["url"]

    out = server.fetch(
        {"album": link, "include_videos": True}, {}, ctx={"data_dir": str(tmp_path)}
    )
    assert out["count"] == 2


def test_fetch_errors_when_album_is_all_videos(tmp_path, monkeypatch):
    monkeypatch.setattr(
        server, "_get_page", lambda url: ("", "", "no network in tests")
    )
    link = "https://photos.app.goo.gl/Vid2"
    photos = [p for p in server._parse_photos(VIDEO_PAGE) if p["is_video"]]
    manifest = {
        "schema": server.MANIFEST_SCHEMA,
        "album": "Videos only",
        "photos": photos,
        "fetched_at": 0,
    }
    cache = tmp_path / f"manifest_{server._cache_key(link)}.json"
    server._write_cache(cache, manifest)

    out = server.fetch({"album": link}, {}, ctx={"data_dir": str(tmp_path)})
    assert out["url"] is None
    assert "videos" in out["error"]


# Trimmed real page shape: the album payload is one of several
# AF_initDataCallback blocks; [_, entries, next_page_token, album_meta].
JSON_PAGE = (
    "<html>AF_initDataCallback({key: 'ds:0', hash: '1', data:[1,2,3,4]"
    ", sideChannel: {}});"
    "AF_initDataCallback({key: 'ds:1', hash: '2', data:[null,"
    '[["AF1QipJson1",'
    '["https://lh3.googleusercontent.com/pw/J1",4032,2268,null,'
    '[null,null,1],[9073751]],1735427730483,"tok1",39600000,'
    '{"15":15110}],'
    '["AF1QipJson2",'
    '["https://lh3.googleusercontent.com/pw/J2",1080,1920],'
    '1735427730000,"tok2",39600000,'
    '{"15":419943,"76647426":[null,null,null,null,null,4]}]],'
    '"",["AF1QipAlbumMeta","JSON Album"]], sideChannel: {}});'
)


def test_photos_from_page_json_payload():
    photos, title = server._photos_from_page(JSON_PAGE, "")
    assert title == "JSON Album"
    assert len(photos) == 2
    by_id = {p["id"]: p for p in photos}
    assert by_id["AF1QipJson1"]["width"] == 4032
    assert by_id["AF1QipJson1"]["taken_ms"] == 1735427730483
    assert by_id["AF1QipJson1"]["is_video"] is False
    assert by_id["AF1QipJson2"]["is_video"] is True


def test_share_context():
    tok, key = server._share_context(
        "https://photos.google.com/share/AF1QipShare?key=SECRET"
    )
    assert tok == "AF1QipShare"
    assert key == "SECRET"
    assert server._share_context("") == (None, None)
    assert server._share_context("https://photos.google.com/") == (None, None)


def test_photos_from_page_follows_continuation_token(monkeypatch):
    page = JSON_PAGE.replace('"",["AF1QipAlbumMeta"', '"TOKEN1",["AF1QipAlbumMeta"')
    calls = []

    def fake_fetch(share_token, auth_key, page_token):
        calls.append((share_token, auth_key, page_token))
        entry = [
            "AF1QipJson3",
            ["https://lh3.googleusercontent.com/pw/J3", 800, 600],
            1735427731000,
        ]
        return [entry], None, None

    monkeypatch.setattr(server, "_fetch_album_page", fake_fetch)
    photos, _ = server._photos_from_page(
        page, "https://photos.google.com/share/AF1QipShare?key=SECRET"
    )
    assert calls == [("AF1QipShare", "SECRET", "TOKEN1")]
    assert [p["id"] for p in photos] == ["AF1QipJson1", "AF1QipJson2", "AF1QipJson3"]


def test_photos_from_page_pagination_error_keeps_partial(monkeypatch):
    page = JSON_PAGE.replace('"",["AF1QipAlbumMeta"', '"TOKEN1",["AF1QipAlbumMeta"')
    monkeypatch.setattr(
        server, "_fetch_album_page", lambda *a: ([], None, "network down")
    )
    photos, _ = server._photos_from_page(
        page, "https://photos.google.com/share/AF1QipShare?key=SECRET"
    )
    assert len(photos) == 2  # embedded photos survive the failed page fetch


def test_fetch_end_to_end_json_page(tmp_path, monkeypatch):
    monkeypatch.setattr(
        server,
        "_get_page",
        lambda url: (JSON_PAGE, "https://photos.google.com/share/AF1QipShare", None),
    )
    out = server.fetch(
        {"album": "https://photos.app.goo.gl/Json1"},
        {},
        ctx={"data_dir": str(tmp_path)},
    )
    assert out["stream"] == "JSON Album"
    assert out["count"] == 1  # the video is excluded by default
    assert "J1" in out["url"]


def test_stale_schema_manifest_is_refetched(tmp_path, monkeypatch):
    # Pre-``is_video`` caches must not satisfy the load; the widget
    # refetches so videos can't slip through for the rest of the TTL.
    fetched = {"n": 0}

    def fake_get(url):
        fetched["n"] += 1
        return SAMPLE_PAGE, "https://photos.google.com/share/AF1Old?key=k", None

    monkeypatch.setattr(server, "_get_page", fake_get)
    link = "https://photos.app.goo.gl/Old1"
    manifest = {
        "album": "Old",
        "photos": server._parse_photos(SAMPLE_PAGE),
        "fetched_at": 0,
    }
    cache = tmp_path / f"manifest_{server._cache_key(link)}.json"
    server._write_cache(cache, manifest)

    out = server.fetch({"album": link}, {}, ctx={"data_dir": str(tmp_path)})
    assert fetched["n"] == 1
    assert out["count"] == 2
