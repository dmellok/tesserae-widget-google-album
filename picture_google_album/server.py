"""picture_google_album, public Google Photos shared-album rotation.

No OAuth and no API key: a Google Photos album shared "with link" serves a
public web page whose embedded AF_initDataCallback JSON lists each photo's
lh3.googleusercontent.com base URL, pixel dimensions and capture timestamp.
We parse that payload as JSON and build a sized display URL by appending
Google's =w{W}-h{H} suffix (scales to fit within, keeps aspect).

The page embeds only the first ~300 items; longer albums carry a
continuation token. We follow it with the same unauthenticated
batchexecute RPC (rpcid snAcKc) the Google Photos web app issues while
scrolling, so albums beyond one page rotate through everything (capped
at MAX_ALBUM_PAGES pages as a runaway guard). A regex scrape of the page
remains as a fallback for page shapes the JSON walk doesn't recognise.

Accepted inputs for the "album" option:
  https://photos.app.goo.gl/XXXX          (short link; we follow the redirect)
  https://photos.google.com/share/AF1Qip...?key=...
  XXXX                                    (bare short-link code)

Videos in the album are listed on the same page with an lh3 poster-frame
URL; they are detected by the video-info key in each entry's trailing
metadata and skipped unless the include_videos option is on.

One cache in data_dir: manifest_<hash>.json (TTL 6h; album contents rarely
change and the share page can be several MB). Unlike Apple's signed URLs,
lh3 base URLs are long-lived, so no per-photo asset cache is needed.
"""

from __future__ import annotations

import contextlib
import hashlib
import html
import json
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

HTTP_TIMEOUT_S = 20
MANIFEST_TTL_S = 6 * 3600
# Bump when the parsed-photo shape changes; cached manifests from an
# older schema are refetched rather than trusted (e.g. pre-``is_video``
# caches would let video stills through the filter for up to the TTL,
# and pre-pagination caches would pin big albums at the first 300).
MANIFEST_SCHEMA = 3
# Google embeds ~300 items per page; 20 pages ≈ 6,000 photos.
MAX_ALBUM_PAGES = 20
BATCHEXECUTE_URL = "https://photos.google.com/_/PhotosUi/data/batchexecute"
DISPLAY_BOX = 2048  # longest edge requested from lh3 for rendering
MIN_DIM = 128  # skip avatars / tiny thumbs that share the blob
# NOTE: must NOT claim Macintosh — photos.app.goo.gl serves a JS-only
# Firebase Dynamic Links interstitial (no 302, no photos) to Mac desktop
# UAs, while Windows/Linux UAs get a clean server-side redirect.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/138.0.0.0 Safari/537.36"
)

# Rich entry: ["AF1Qip<id>",["https://lh3.../pw/<blob>",W,H,...nulls and
# nested arrays like [null,null,1],[9073751]...],<epoch_ms>,...
# The filler must tolerate one level of nested [...] before the closing ].
PHOTO_RE = re.compile(
    r'\["(AF1Qip[\w-]+)",\["(https://lh3\.googleusercontent\.com/[^"]+)"'
    r",(\d+),(\d+)(?:[^\[\]]|\[[^\]]*\])*\],(\d+)"
)
# Fallback: any ["<lh3 url>",W,H triple in the page.
URL_RE = re.compile(r'\["(https://lh3\.googleusercontent\.com/[^"]+)",(\d+),(\d+)')
TITLE_RE = re.compile(r'<meta property="og:title" content="([^"]*)"')
# Video entries carry a trailing metadata object keyed by this protobuf
# field number (duration-ms + stream info). Photo entries never have it:
# 0 hits across 448 photo entries in four sampled albums, present on
# every sampled video entry. The lh3 URL itself is just the poster
# frame, so this key is the only way to tell a video from a photo.
VIDEO_KEY = '"76647426"'
VIDEO_KEY_JSON = "76647426"
# The album payload lives in one of the page's AF_initDataCallback
# blocks: [_, entries, next_page_token, album_meta, ...].
DATA_CALLBACK_RE = re.compile(
    r"AF_initDataCallback\(\{key: '[^']*', hash: '[^']*', data:"
)
# An entry is ~0.5-1.5 KB; cap the balanced-bracket walk well above that
# so a malformed page can't make us scan to EOF per entry.
ENTRY_SCAN_CAP = 100_000


# ----- share-link normalisation --------------------------------------


def _share_url(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    if s.startswith(("http://", "https://")):
        return s
    if s.startswith(("photos.app.goo.gl/", "photos.google.com/")):
        return f"https://{s}"
    # Bare short-link code (the bit after photos.app.goo.gl/).
    return f"https://photos.app.goo.gl/{s}"


# ----- HTTP + parse ---------------------------------------------------


def _get_page(url: str) -> tuple[str, str, str | None]:
    """GET the share page (follows the goo.gl redirect). Returns
    (html_text, final_url, error); final_url is the resolved
    photos.google.com/share/… URL, needed for pagination."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
            text = resp.read().decode("utf-8", errors="replace")
            return text, resp.geturl(), None
    except urllib.error.HTTPError as err:
        return "", "", f"Google returned HTTP {err.code} for the share link."
    except (urllib.error.URLError, TimeoutError, OSError) as err:
        return "", "", f"Could not reach Google Photos: {err}"[:200]


def _entry_extent(page: str, start: int) -> str:
    """Return the full ``["AF1Qip…", …]`` entry starting at ``start``
    by walking brackets (string-aware). PHOTO_RE only reaches the
    capture timestamp; the video marker lives further along, in the
    entry's trailing metadata object."""
    depth = 0
    in_str = False
    esc = False
    end = min(len(page), start + ENTRY_SCAN_CAP)
    for i in range(start, end):
        c = page[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                return page[start : i + 1]
    return page[start:end]


def _parse_photos(page: str) -> list[dict[str, Any]]:
    photos: list[dict[str, Any]] = []
    seen: set[str] = set()
    for m in PHOTO_RE.finditer(page):
        pid, url, w, h, ts = m.groups()
        if url in seen:
            continue
        seen.add(url)
        photos.append(
            {
                "id": pid,
                "base": url,
                "width": int(w),
                "height": int(h),
                "taken_ms": int(ts),
                "is_video": VIDEO_KEY in _entry_extent(page, m.start()),
            }
        )
    if photos:
        return photos
    # Older/alternate page shapes: fall back to bare url triples.
    for url, w, h in URL_RE.findall(page):
        if url in seen or int(w) < MIN_DIM or int(h) < MIN_DIM:
            continue
        seen.add(url)
        photos.append(
            {
                "id": None,
                "base": url,
                "width": int(w),
                "height": int(h),
                "taken_ms": None,
                # Bare url triples carry no entry context, so videos
                # can't be told apart on this legacy page shape.
                "is_video": False,
            }
        )
    return photos


def _parse_title(page: str) -> str:
    m = TITLE_RE.search(page)
    return html.unescape(m.group(1)).strip() if m else ""


# ----- JSON payload + pagination --------------------------------------


def _entry_is_video(entry: list[Any]) -> bool:
    return any(isinstance(el, dict) and VIDEO_KEY_JSON in el for el in entry)


def _photo_from_entry(entry: Any) -> dict[str, Any] | None:
    try:
        pid, desc, ts = entry[0], entry[1], entry[2]
        url, w, h = desc[0], int(desc[1]), int(desc[2])
    except (TypeError, ValueError, IndexError, KeyError):
        return None
    if not isinstance(pid, str) or not isinstance(url, str):
        return None
    if not url.startswith("https://lh3.googleusercontent.com/"):
        return None
    return {
        "id": pid,
        "base": url,
        "width": w,
        "height": h,
        "taken_ms": int(ts) if isinstance(ts, (int, float)) else None,
        "is_video": _entry_is_video(entry),
    }


def _album_payload(page: str) -> list[Any] | None:
    """Pick the AF_initDataCallback block that holds the album payload:
    the candidate whose entry list yields the most parseable photos."""
    decoder = json.JSONDecoder()
    best: list[Any] | None = None
    best_photos = -1
    for m in DATA_CALLBACK_RE.finditer(page):
        try:
            data, _ = decoder.raw_decode(page, m.end())
        except ValueError:
            continue
        if not (
            isinstance(data, list) and len(data) >= 4 and isinstance(data[1], list)
        ):
            continue
        n = sum(1 for e in data[1] if _photo_from_entry(e) is not None)
        if n > best_photos:
            best, best_photos = data, n
    return best


def _share_context(final_url: str) -> tuple[str | None, str | None]:
    """(share_token, auth_key) from the resolved share URL."""
    m = re.search(r"/share/([^/?#]+)", final_url or "")
    if not m:
        return None, None
    query = urllib.parse.parse_qs(urllib.parse.urlparse(final_url).query)
    return m.group(1), (query.get("key") or [None])[0]


def _fetch_album_page(
    share_token: str, auth_key: str | None, page_token: str
) -> tuple[list[Any], str | None, str | None]:
    """One snAcKc batchexecute call, the RPC the Google Photos web app
    issues while scrolling a shared album. Anonymous: the share token
    and the link's key parameter are the only credentials. Returns
    (entries, next_token, error)."""
    inner = json.dumps([share_token, page_token, None, auth_key])
    freq = json.dumps([[["snAcKc", inner, None, "generic"]]])
    body = urllib.parse.urlencode({"f.req": freq}).encode()
    req = urllib.request.Request(
        BATCHEXECUTE_URL,
        data=body,
        headers={
            "User-Agent": USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
            text = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as err:
        return [], None, str(err)[:200]
    idx = text.find('[["wrb.fr","snAcKc"')
    if idx < 0:
        return [], None, "unexpected batchexecute response"
    try:
        envelope, _ = json.JSONDecoder().raw_decode(text, idx)
        payload = json.loads(envelope[0][2])
    except (ValueError, TypeError, IndexError):
        # An error envelope carries null where the payload string goes.
        return [], None, "could not parse batchexecute payload"
    entries = payload[1] if len(payload) > 1 and isinstance(payload[1], list) else []
    token = payload[2] if len(payload) > 2 else None
    token = token if isinstance(token, str) and token else None
    return entries, token, None


def _photos_from_page(page: str, final_url: str) -> tuple[list[dict[str, Any]], str]:
    """All album photos: the entries embedded in the share page plus,
    for albums longer than one embedded page, the rest via pagination.
    Returns (photos, album_title); title may be empty."""
    data = _album_payload(page)
    if data is None:
        return _parse_photos(page), ""
    photos: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(entries: list[Any]) -> None:
        for e in entries:
            p = _photo_from_entry(e)
            if p is not None and p["base"] not in seen:
                seen.add(p["base"])
                photos.append(p)

    add(data[1])
    meta = data[3]
    title = (
        meta[1]
        if isinstance(meta, list) and len(meta) > 1 and isinstance(meta[1], str)
        else ""
    )
    token = data[2] if len(data) > 2 and isinstance(data[2], str) and data[2] else None
    share_token, auth_key = _share_context(final_url)
    pages = 0
    while token and share_token and pages < MAX_ALBUM_PAGES:
        entries, token, err = _fetch_album_page(share_token, auth_key, token)
        if err:
            break  # partial album beats an error; TTL retries later
        add(entries)
        pages += 1
    if not photos:
        return _parse_photos(page), title
    return photos, title


# ----- manifest cache -------------------------------------------------


def _cache_key(link: str) -> str:
    return hashlib.sha1(link.encode("utf-8")).hexdigest()[:16]


def _read_cache(path: Path, ttl: int) -> dict[str, Any] | None:
    if not path.exists():
        return None
    if time.time() - path.stat().st_mtime >= ttl:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]
    except (json.JSONDecodeError, OSError):
        return None


def _write_cache(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        path.write_text(json.dumps(payload), encoding="utf-8")


def _load_manifest(
    link: str, data_dir: Path
) -> tuple[dict[str, Any] | None, str | None]:
    cache_path = data_dir / f"manifest_{_cache_key(link)}.json"
    cached = _read_cache(cache_path, MANIFEST_TTL_S)
    if cached is not None and cached.get("schema") == MANIFEST_SCHEMA:
        return cached, None
    page, final_url, err = _get_page(link)
    if err:
        return None, err
    photos, title = _photos_from_page(page, final_url)
    if not photos:
        return None, (
            "No photos found at that link. Make sure the album is shared "
            "with 'Create link' in Google Photos and the link is public."
        )
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "album": title or _parse_title(page),
        "photos": photos,
        "fetched_at": int(time.time()),
    }
    _write_cache(cache_path, manifest)
    return manifest, None


# ----- selection helpers ----------------------------------------------


def _orientation_of(p: dict[str, Any]) -> str:
    w = int(p.get("width") or 0)
    h = int(p.get("height") or 0)
    if w == 0 or h == 0:
        return "any"
    ratio = w / h
    if abs(ratio - 1.0) <= 0.05:
        return "square"
    return "landscape" if ratio > 1 else "portrait"


def _filter_orientation(
    photos: list[dict[str, Any]], wanted: str
) -> list[dict[str, Any]]:
    if wanted == "any":
        return photos
    return [p for p in photos if _orientation_of(p) == wanted]


def _display_url(p: dict[str, Any]) -> str:
    w = int(p.get("width") or 0) or DISPLAY_BOX
    h = int(p.get("height") or 0) or DISPLAY_BOX
    box_w = min(w, DISPLAY_BOX)
    box_h = min(h, DISPLAY_BOX)
    return f"{p['base']}=w{box_w}-h{box_h}"


def _iso_date(ms: Any) -> str | None:
    if not ms:
        return None
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ms) / 1000))
    except (ValueError, OSError, OverflowError):
        return None


# ----- plugin contract ------------------------------------------------


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    del settings
    link = _share_url(options.get("album") or "")
    if not link:
        return {
            "error": "Paste a Google Photos shared-album link "
            "(https://photos.app.goo.gl/… or "
            "https://photos.google.com/share/…). In Google Photos: open the "
            "album, Share, then 'Create link'.",
            "url": None,
        }

    data_dir = Path(ctx.get("data_dir") or ".")
    data_dir.mkdir(parents=True, exist_ok=True)

    manifest, err = _load_manifest(link, data_dir)
    if err or manifest is None:
        return {"error": err or "Could not load the album.", "url": None}

    photos = manifest.get("photos") or []
    if not options.get("include_videos"):
        stills = [p for p in photos if not p.get("is_video")]
        if not stills and photos:
            return {
                "error": "This album only contains videos. Enable "
                "'Include videos' to show their still frames.",
                "url": None,
            }
        photos = stills
    orientation = (options.get("orientation") or "any").lower()
    filtered = _filter_orientation(photos, orientation)
    if not filtered:
        return {"error": f"No {orientation} photos in this album.", "url": None}

    mode = options.get("mode", "random")
    if mode == "sequential":
        suffix = f"_{orientation}" if orientation != "any" else ""
        idx_file = data_dir / f".seq_idx_{_cache_key(link)}{suffix}"
        try:
            current = int(idx_file.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError, OSError):
            current = -1
        next_idx = (current + 1) % len(filtered)
        with contextlib.suppress(OSError):
            idx_file.write_text(str(next_idx), encoding="utf-8")
        photo = filtered[next_idx]
        index = next_idx + 1
    else:
        photo = random.choice(filtered)
        index = filtered.index(photo) + 1

    return {
        "url": _display_url(photo),
        "id": photo.get("id"),
        "date": _iso_date(photo.get("taken_ms")),
        "stream": manifest.get("album") or "",
        "width": photo.get("width"),
        "height": photo.get("height"),
        "index": index,
        "count": len(filtered),
    }
