# Google Photos shared-album widget for Tesserae

Photo carousel from a [Google Photos album shared by link](https://support.google.com/photos/answer/6131416), rendered full-bleed onto an e-ink panel. For [Tesserae](https://github.com/dmellok/tesserae), the e-ink dashboard companion.

## How it works

1. Open the album in Google Photos (web or app).
2. Tap **Share**, then **Create link**.
3. Paste the link (`https://photos.app.goo.gl/…` or `https://photos.google.com/share/…`) into the widget's cell options.

No Google account connection and no API key needed. The widget reads the public share page directly — the page embeds every photo's image URL, dimensions and capture date. Per cell you can filter by orientation, rotate randomly or sequentially, and pick fit / fill / stretch / blurred-letterbox scaling with an optional date + album-name overlay and a sequence indicator.

The "Split view" orientation draws from the whole album: a portrait photo in a landscape cell (or a landscape photo in a tall cell) is shown with a second photo of the same shape, side by side or stacked; any other draw is shown alone. Each photo is equally likely to be drawn, so the odds follow the album's portrait/landscape mix. "Gap between pair" (pixels, 0 to 100) and "Divider colour" style the seam. Pairs apply to random selection only; sequential shows one photo. Split view shows no caption or sequence overlay.

Note: this relies on the share page's markup, which Google could change; the parser has a fallback pattern but may need updating if that happens.

## Install

Settings → Widgets → Browse community widgets → Install.

## Folders shipped

- `picture_google_album`

## License

AGPL-3.0-or-later. See [LICENSE](LICENSE).
