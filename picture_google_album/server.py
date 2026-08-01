"""picture_google_album, public Google Photos shared-album rotation.

No OAuth and no API key: a Google Photos album shared "with link" serves a
public web page whose embedded AF_initDataCallback JSON lists every photo's
lh3.googleusercontent.com base URL, pixel dimensions and capture timestamp.
We fetch that page, regex the entries out, and build a sized display URL by
appending Google's =w{W}-h{H} suffix (scales to fit within, keeps aspect).

Accepted inputs for the "album" option:
  https://photos.app.goo.gl/XXXX          (short link; we follow the redirect)
  https://photos.google.com/share/AF1Qip...?key=...
  XXXX                                    (bare short-link code)

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
import urllib.request
from pathlib import Path
from typing import Any

HTTP_TIMEOUT_S = 20
MANIFEST_TTL_S = 6 * 3600
DISPLAY_BOX = 2048  # longest edge requested from lh3 for rendering
MIN_DIM = 128  # skip avatars / tiny thumbs that share the blob
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Rich entry: ["AF1Qip<id>",["https://lh3.../pw/<blob>",W,H,...],<epoch_ms>,...
PHOTO_RE = re.compile(
    r'\["(AF1Qip[\w-]+)",\["(https://lh3\.googleusercontent\.com/[^"]+)"'
    r",(\d+),(\d+)[^\]]*\],(\d+)"
)
# Fallback: any ["<lh3 url>",W,H triple in the page.
URL_RE = re.compile(r'\["(https://lh3\.googleusercontent\.com/[^"]+)",(\d+),(\d+)')
TITLE_RE = re.compile(r'<meta property="og:title" content="([^"]*)"')


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


def _get_page(url: str) -> tuple[str, str | None]:
    """GET the share page (follows the goo.gl redirect). Returns
    (html_text, error)."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
            return resp.read().decode("utf-8", errors="replace"), None
    except urllib.error.HTTPError as err:
        return "", f"Google returned HTTP {err.code} for the share link."
    except (urllib.error.URLError, TimeoutError, OSError) as err:
        return "", f"Could not reach Google Photos: {err}"[:200]


def _parse_photos(page: str) -> list[dict[str, Any]]:
    photos: list[dict[str, Any]] = []
    seen: set[str] = set()
    for pid, url, w, h, ts in PHOTO_RE.findall(page):
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
            }
        )
    return photos


def _parse_title(page: str) -> str:
    m = TITLE_RE.search(page)
    return html.unescape(m.group(1)).strip() if m else ""


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


def _load_manifest(link: str, data_dir: Path) -> tuple[dict[str, Any] | None, str | None]:
    cache_path = data_dir / f"manifest_{_cache_key(link)}.json"
    cached = _read_cache(cache_path, MANIFEST_TTL_S)
    if cached is not None:
        return cached, None
    page, err = _get_page(link)
    if err:
        return None, err
    photos = _parse_photos(page)
    if not photos:
        return None, (
            "No photos found at that link. Make sure the album is shared "
            "with 'Create link' in Google Photos and the link is public."
        )
    manifest = {
        "album": _parse_title(page),
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


def _filter_orientation(photos: list[dict[str, Any]], wanted: str) -> list[dict[str, Any]]:
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
