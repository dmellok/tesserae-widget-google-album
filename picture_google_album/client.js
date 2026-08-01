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

const OBJECT_FIT = { fit: "contain", fill: "cover", stretch: "fill", blurred: "contain" };

export default function render(shadow, ctx) {
  const data = ctx?.data ?? {};
  const opts = ctx?.cell?.options || {};
  const showCaption = opts.show_caption !== false;
  const showSequence = opts.show_sequence !== false;
  const scale = OBJECT_FIT[opts.scale] ? opts.scale : "fit";
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

  const url = escapeHtml(data.url);
  const backdrop = scale === "blurred"
    ? `<img class="backdrop" src="${url}" alt="" aria-hidden="true">`
    : "";

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
      .w.is-bleed > img.photo { object-fit: ${OBJECT_FIT[scale]}; position: relative; }
      .w.is-bleed > img.backdrop {
        position: absolute; inset: 0; width: 100%; height: 100%;
        object-fit: cover; filter: blur(14px) brightness(0.82);
        transform: scale(1.08);
      }
      .img-overlay { justify-content: space-between; gap: var(--space-2); }
      .img-overlay .seq { flex: none; font-variant-numeric: tabular-nums; }
    </style>
    <div class="w is-bleed" data-widget="picture_google_album">
      ${backdrop}
      <img class="photo" src="${url}" alt="">
      ${overlay}
    </div>`;
}
