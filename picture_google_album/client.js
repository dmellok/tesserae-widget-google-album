// picture_google_album, Spectra full-bleed image. Renders one photo from a
// public Google Photos shared album filling the cell, with optional
// caption + sequence overlay and fit/fill/stretch/blurred scaling.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function fmtDate(iso) {
  if (typeof iso !== "string" || !iso) return "";
  return iso.slice(0, 10);
}

const PAIR_DIVIDER = { background: "var(--surface-sunken)", white: "#fff", black: "#000" };
const OBJECT_FIT = { fit: "contain", fill: "cover", stretch: "fill", blurred: "contain" };

function shapeOf(w, h) {
  if (!(w > 0 && h > 0)) return null;
  const r = w / h;
  return Math.abs(r - 1) <= 0.05 ? "square" : r > 1 ? "landscape" : "portrait";
}

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const opts = ctx?.cell?.options || {};
  // Split view hides both options, so it never shows either overlay.
  const split = opts.orientation === "splitview";
  const showCaption = !split && opts.show_caption !== false;
  const showSequence = !split && opts.show_sequence !== false;
  const scale = OBJECT_FIT[opts.scale] ? opts.scale : "fit";
  const gap = Math.min(Math.max(Number(opts.pair_gap ?? 2) || 0, 0), 100);
  const divider = PAIR_DIVIDER[opts.pair_divider] ?? PAIR_DIVIDER.background;
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css">`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="picture_google_album">
        <div class="w-title"><i class="ph-bold ph-warning-circle"></i><h3>Album</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  if (!data.url) {
    shadow.innerHTML = `
      ${css}
      <div class="w is-bleed" data-widget="picture_google_album">
        <div class="bleed-empty">No photos.</div>
      </div>`;
    return;
  }

  // Split view: show the mate only when the drawn photo is the opposite
  // shape of the cell (portrait in a wide cell, landscape in a tall one).
  const cell = ctx?.cell;
  const cellShape = shapeOf(cell?.w, cell?.h)
    ?? (typeof ctx?.panel?.portrait === "boolean"
      ? (ctx.panel.portrait ? "portrait" : "landscape") : null);
  const photoShape = shapeOf(data.width, data.height);
  const pairLayout = split && data.url2
    && cellShape && photoShape && cellShape !== photoShape
    && cellShape !== "square" && photoShape !== "square"
    ? (cellShape === "landscape" ? "row" : "column") : null;
  const urls = (pairLayout ? [data.url, data.url2] : [data.url]).map(escapeHtml);
  const paired = pairLayout !== null;
  const tile = (u) => `<div class="tile">${scale === "blurred"
    ? `<img class="backdrop" src="${u}" alt="" aria-hidden="true">` : ""}
    <img class="photo" src="${u}" alt=""></div>`;

  const captionBits = [];
  if (showCaption) {
    if (data.date) captionBits.push(fmtDate(data.date));
    if (data.stream) captionBits.push(data.stream);
  }
  const seq = showSequence && data.index && data.count
    ? `${data.index} / ${data.count}` : "";

  const overlay = captionBits.length || seq
    ? `<div class="img-overlay">
         <span class="sub">${escapeHtml(captionBits.join(" · "))}</span>
         ${seq ? `<span class="sub seq">${escapeHtml(seq)}</span>` : ""}
       </div>`
    : "";

  shadow.innerHTML = `
    ${css}
    <style>
      .w.is-bleed { overflow: hidden; background: var(--surface-sunken); }
      .w.is-bleed > .tiles { position: absolute; inset: 0; display: flex;
        flex-direction: ${pairLayout === "column" ? "column" : "row"}; gap: ${gap}px; }
      .w.is-bleed > .tiles.paired { background: ${divider}; }
      .tile { position: relative; flex: 1; min-width: 0; min-height: 0; overflow: hidden; }
      .tile > img.photo { position: relative; width: 100%; height: 100%;
        object-fit: ${OBJECT_FIT[scale]}; display: block; }
      .tile > img.backdrop {
        position: absolute; inset: 0; width: 100%; height: 100%;
        object-fit: cover; filter: blur(14px) brightness(0.82);
        transform: scale(1.08);
      }
      .img-overlay { justify-content: space-between; gap: var(--space-2); }
      .img-overlay .seq { flex: none; font-variant-numeric: tabular-nums; }
    </style>
    <div class="w is-bleed" data-widget="picture_google_album">
      <div class="tiles${paired ? " paired" : ""}">${urls.map(tile).join("")}</div>
      ${overlay}
    </div>`;
}
