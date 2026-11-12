"""Offline smoke tests for picture_google_album (no network)."""

from __future__ import annotations

import os

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
    assert out["url"].startswith(f"/plugins/picture_google_album/photo/{server._cache_key(link)}/")
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
    assert out["url"].endswith("/AF1QipPhoto111")

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
    assert out["url"].endswith("/AF1QipJson1")


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


def test_options_panel_is_short_and_keeps_saved_cells() -> None:
    """The essentials up front, fine-tuning under More options and last;
    no option, choice value or default a saved cell holds has changed."""
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    m = json.loads((root / "plugin.json").read_text(encoding="utf-8"))
    opts = m["cell_options"]
    by = {o["name"]: o for o in opts}
    always = [o["name"] for o in opts if not o.get("show_if") and not o.get("advanced")]
    assert always == ["album", "mode", "orientation", "scale"]
    first_adv = next(i for i, o in enumerate(opts) if o.get("advanced"))
    assert all(o.get("advanced") for o in opts[first_adv:])
    for o in opts:
        assert ":" not in o["label"] and "(" not in o["label"], o["label"]
    old = json.loads(
        (root / "tests" / "options_v0_3_0.json").read_text(encoding="utf-8")
    )
    assert set(old) <= set(by)
    assert set(by) - set(old) == {"pair_gap", "pair_divider"}
    for name, was in old.items():
        o = by[name]
        assert o["type"] == was["type"] and o.get("default") == was["default"], name
        assert [c["value"] for c in o.get("choices", [])][: len(was["values"])] == was["values"], name
    assert "—" not in json.dumps(m, ensure_ascii=False)


def _mk(n, w, h):
    return {"id": f"id{n}", "base": f"https://lh3.googleusercontent.com/pw/{n}",
            "width": w, "height": h, "taken_ms": None, "is_video": False}


def test_pick_split_mate_has_same_shape(monkeypatch):
    photos = [_mk("L1", 1600, 900), _mk("P1", 900, 1600), _mk("P2", 900, 1600)]
    monkeypatch.setattr(server.random, "randrange", lambda n: 1)  # P1
    photo, mate, idx = server._pick_split(photos)
    assert photo["id"] == "idP1" and mate["id"] == "idP2" and idx == 2


def test_pick_split_mate_is_never_the_same_image():
    dup = _mk("P1", 900, 1600)
    photos = [dup, dict(dup), _mk("P2", 900, 1600)]  # same image listed twice
    for _ in range(100):
        photo, mate, _i = server._pick_split(photos)
        assert mate is not None and mate["base"] != photo["base"]


def test_pick_split_alone_without_mate_or_for_square():
    photos = [_mk("L1", 1600, 900), _mk("P1", 900, 1600), _mk("S1", 1000, 1000), _mk("S2", 1000, 1000)]
    for _ in range(100):
        photo, mate, _i = server._pick_split(photos)
        assert mate is None, photo["id"]


def test_fetch_splitview_returns_mate(tmp_path):
    link = "https://photos.app.goo.gl/Xy9"
    photos = [_mk(f"P{i}", 900, 1600) for i in range(3)]
    manifest = {"schema": server.MANIFEST_SCHEMA, "album": "A", "photos": photos, "fetched_at": 0}
    server._write_cache(tmp_path / f"manifest_{server._cache_key(link)}.json", manifest)
    ctx = {"data_dir": str(tmp_path)}
    out = server.fetch({"album": link, "orientation": "splitview"}, {}, ctx=ctx)
    assert out["url2"] and out["url"] != out["url2"] and out["count"] == 3
    out = server.fetch({"album": link, "orientation": "portrait"}, {}, ctx=ctx)
    assert out["url2"] is None
    out = server.fetch({"album": link, "orientation": "splitview", "mode": "sequential"}, {}, ctx=ctx)
    assert out["url2"] is None and out["index"] == 1


def test_display_url_covers_the_cell_without_enlarging():
    photo = {"base": "https://lh3.googleusercontent.com/pw/AAAA", "width": 6000, "height": 4000}
    # A 4K cell gets a photo just covering it (fit inside the box = cover).
    assert server._display_url(photo, (3840, 2160)).endswith("=w3840-h2560")
    # A small e-paper cell gets a small photo.
    assert server._display_url(photo, (800, 480)).endswith("=w800-h534")
    # A photo smaller than the cell is never enlarged.
    small = {"base": "https://lh3.googleusercontent.com/pw/CCCC", "width": 1600, "height": 1200}
    assert server._display_url(small, (3840, 2160)).endswith("=w1600-h1200")


def test_fetch_sizes_photos_to_the_cell(tmp_path, monkeypatch):
    link = "https://photos.app.goo.gl/Xy9"
    manifest = {
        "schema": server.MANIFEST_SCHEMA,
        "album": "Cached",
        "photos": server._parse_photos(SAMPLE_PAGE),
        "fetched_at": 0,
    }
    server._write_cache(tmp_path / f"manifest_{server._cache_key(link)}.json", manifest)
    ctx = {"data_dir": str(tmp_path), "cell_w": 800, "cell_h": 480}
    out = server.fetch({"album": link, "mode": "sequential"}, {}, ctx=ctx)
    # The page gets the proxy route, carrying the cell; the route sizes the
    # lh3 request (see test_photo_route_sizes_the_lh3_request_to_the_cell).
    assert out["url"].endswith("?cw=800&ch=480")
    # Without a cell size the request carries none.
    out = server.fetch({"album": link, "mode": "sequential"}, {}, ctx={"data_dir": str(tmp_path)})
    assert "?" not in out["url"]


def test_manifest_lets_lineups_draw_another_photo():
    import json
    from pathlib import Path

    manifest = json.loads((Path(server.__file__).parent / "plugin.json").read_text())
    assert manifest["lineups"] == {"draw": True}


# ----- photos served through the widget's own route ---------------------

import pytest  # noqa: E402

GOOD_JPEG = b"\xff\xd8\xff\xe0" + b"photo-bytes" * 20
ALBUM_LINK = "https://photos.app.goo.gl/Xy9"


def _seed_manifest(data_dir, photos=None):
    manifest = {
        "schema": server.MANIFEST_SCHEMA,
        "album": "Cached",
        "photos": photos or server._parse_photos(SAMPLE_PAGE),
        "fetched_at": 0,
    }
    server._write_cache(data_dir / f"manifest_{server._cache_key(ALBUM_LINK)}.json", manifest)


class _Registry:
    def __init__(self, data_dir):
        self._plugin = type("P", (), {"data_dir": data_dir})()

    def get(self, plugin_id):
        return self._plugin if plugin_id == "picture_google_album" else None


@pytest.fixture
def client(tmp_path, monkeypatch):
    flask = pytest.importorskip("flask")
    app = flask.Flask(__name__)
    app.config["PLUGIN_REGISTRY"] = _Registry(tmp_path)
    app.register_blueprint(server.blueprint(), url_prefix="/plugins/picture_google_album")
    monkeypatch.setattr(server, "_sleep", lambda s: None)
    _seed_manifest(tmp_path)
    return app.test_client()


class _Upstream:
    """Stands in for the lh3 opener: replays one outcome per call."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.urls = []

    def open(self, req, timeout=None):
        self.urls.append(req.full_url)
        out = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        if isinstance(out, Exception):
            raise out
        import io

        return io.BytesIO(out)


def _http_error(code):
    import urllib.error

    return urllib.error.HTTPError("https://lh3.googleusercontent.com/x", code, "x", {}, None)


def _photo_path(key="AF1QipAbc-123", query=""):
    return f"/plugins/picture_google_album/photo/{server._cache_key(ALBUM_LINK)}/{key}{query}"


def test_fetch_returns_the_proxied_url_for_both_photos(tmp_path):
    photos = [_mk(f"P{i}", 900, 1600) for i in range(3)]
    _seed_manifest(tmp_path, photos)
    ctx = {"data_dir": str(tmp_path), "cell_w": 480, "cell_h": 800}
    out = server.fetch({"album": ALBUM_LINK, "orientation": "splitview"}, {}, ctx=ctx)
    root = f"/plugins/picture_google_album/photo/{server._cache_key(ALBUM_LINK)}/"
    for u in (out["url"], out["url2"]):
        assert u.startswith(root) and u.endswith("?cw=480&ch=800")
        assert "googleusercontent" not in u


def test_photo_route_serves_the_manifest_photo(client, monkeypatch):
    up = _Upstream(GOOD_JPEG)
    monkeypatch.setattr(server, "_OPENER", up)
    r = client.get(_photo_path())
    assert r.status_code == 200
    assert r.data == GOOD_JPEG
    assert r.mimetype == "image/jpeg"
    assert "max-age" in r.headers["Cache-Control"]
    assert up.urls == ["https://lh3.googleusercontent.com/pw/AAAA=w1600-h1200"]


def test_photo_route_sizes_the_lh3_request_to_the_cell(client, monkeypatch):
    up = _Upstream(GOOD_JPEG)
    monkeypatch.setattr(server, "_OPENER", up)
    assert client.get(_photo_path(query="?cw=800&ch=480")).status_code == 200
    assert up.urls == ["https://lh3.googleusercontent.com/pw/AAAA=w800-h600"]


def test_photo_route_retries_then_succeeds(client, monkeypatch):
    import urllib.error

    up = _Upstream(
        urllib.error.URLError("reset"), _http_error(503), GOOD_JPEG
    )
    monkeypatch.setattr(server, "_OPENER", up)
    r = client.get(_photo_path())
    assert r.status_code == 200 and r.data == GOOD_JPEG
    assert len(up.urls) == 3


def test_photo_route_retries_429_and_timeouts(client, monkeypatch):
    up = _Upstream(_http_error(429), TimeoutError("slow"), GOOD_JPEG)
    monkeypatch.setattr(server, "_OPENER", up)
    assert client.get(_photo_path()).status_code == 200
    assert len(up.urls) == 3


def test_photo_route_gives_up_with_an_error_status(client, monkeypatch):
    up = _Upstream(_http_error(503))
    monkeypatch.setattr(server, "_OPENER", up)
    r = client.get(_photo_path())
    assert r.status_code == 502
    assert len(up.urls) == server.PHOTO_TRIES
    assert r.headers["Cache-Control"] == "no-store"


def test_photo_route_does_not_retry_a_missing_photo(client, monkeypatch):
    up = _Upstream(_http_error(404))
    monkeypatch.setattr(server, "_OPENER", up)
    assert client.get(_photo_path()).status_code == 502
    assert len(up.urls) == 1


def test_photo_route_rejects_a_non_image_answer(client, monkeypatch):
    up = _Upstream(b"<html>consent</html>")
    monkeypatch.setattr(server, "_OPENER", up)
    assert client.get(_photo_path()).status_code == 502


def test_photo_route_caches_the_bytes(client, monkeypatch, tmp_path):
    up = _Upstream(GOOD_JPEG)
    monkeypatch.setattr(server, "_OPENER", up)
    assert client.get(_photo_path()).data == GOOD_JPEG
    assert client.get(_photo_path()).data == GOOD_JPEG
    assert len(up.urls) == 1
    # A different size is a different photo.
    client.get(_photo_path(query="?cw=100&ch=100"))
    assert len(up.urls) == 2
    # Past the TTL it is fetched again.
    for f in (tmp_path / "photos").iterdir():
        os.utime(f, (0, 0))
    client.get(_photo_path())
    assert len(up.urls) == 3


def test_photo_route_rejects_unknown_ids(client, monkeypatch):
    up = _Upstream(GOOD_JPEG)
    monkeypatch.setattr(server, "_OPENER", up)
    album = server._cache_key(ALBUM_LINK)
    for path in (
        _photo_path("AF1QipNotInTheAlbum"),
        f"/plugins/picture_google_album/photo/{'0' * 16}/AF1QipAbc-123",  # unknown album
        "/plugins/picture_google_album/photo/../AF1QipAbc-123",
        f"/plugins/picture_google_album/photo/{album}/https%3A%2F%2Fevil.example%2Fa.jpg",
        f"/plugins/picture_google_album/photo/{album}/..%2Fmanifest",
        f"/plugins/picture_google_album/photo/..%2F..%2Fetc/AF1QipAbc-123",
    ):
        assert client.get(path).status_code == 404, path
    assert up.urls == []  # nothing was fetched for any of them


def test_photo_route_only_fetches_google_hosts(client, monkeypatch, tmp_path):
    up = _Upstream(GOOD_JPEG)
    monkeypatch.setattr(server, "_OPENER", up)
    evil = [
        {"id": "AF1QipEvil", "base": "https://evil.example/pw/AAAA", "width": 10, "height": 10},
        {"id": "AF1QipEvil2", "base": "https://lh3.googleusercontent.com.evil.example/x", "width": 10, "height": 10},
        {"id": "AF1QipEvil3", "base": "http://lh3.googleusercontent.com/x", "width": 10, "height": 10},
    ]
    _seed_manifest(tmp_path, evil)
    for p in evil:
        assert client.get(_photo_path(p["id"])).status_code == 404
    assert up.urls == []


def test_redirects_leaving_google_are_refused():
    handler = server._GoogleRedirects()
    req = server.urllib.request.Request("https://lh3.googleusercontent.com/pw/AAAA")
    import urllib.error

    with pytest.raises(urllib.error.URLError):
        handler.redirect_request(req, None, 302, "Found", {}, "http://169.254.169.254/latest")


def test_photo_route_is_render_safe_and_has_no_index():
    assert server.RENDER_SAFE_ENDPOINTS == ("picture_google_album_photos.photo",)
    flask = pytest.importorskip("flask")
    app = flask.Flask(__name__)
    app.register_blueprint(server.blueprint(), url_prefix="/plugins/picture_google_album")
    rules = {r.rule for r in app.url_map.iter_rules()}
    assert "/plugins/picture_google_album/" not in rules
    assert "picture_google_album_photos.photo" in {r.endpoint for r in app.url_map.iter_rules()}
