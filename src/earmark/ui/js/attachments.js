// Attachments: issue section (thumbnails, players, file rows), comment strips, lightbox, uploads.

import { html, useEffect, useRef, useState } from './lib.js';
import { state, set, toast, loadDetail, mutate } from './state.js';
import { uploadFile } from './api.js';
import { Icon, IconButton, confirmDialog } from './components.js';
import { cx, formatBytes, absTime, copyText, plural } from './util.js';

const KIND = {
  image: { label: 'Image', icon: 'file-image' },
  audio: { label: 'Audio', icon: 'file-audio' },
  video: { label: 'Video', icon: 'file-video' },
  pdf: { label: 'PDF', icon: 'file-text' },
  text: { label: 'Text', icon: 'file-code' },
  file: { label: 'File', icon: 'file' },
};
const kindOf = (a) => KIND[a.kind] || KIND.file;

export const limitMB = () => (state.board && state.board.settings && state.board.settings.max_attachment_mb) || null;

// ---------------------------------------------------------------- uploads

let seq = 0;
const patchUpload = (id, patch) => set({ uploads: state.uploads.map((u) => (u.id === id ? Object.assign({}, u, patch) : u)) });
const dropUpload = (id) => set({ uploads: state.uploads.filter((u) => u.id !== id) });

/** Upload files to an issue (optionally attached to a comment). Shows progress, toasts errors. */
export async function uploadFiles(key, files, { comment } = {}) {
  const list = [...(files || [])].filter(Boolean);
  if (!list.length) return [];
  const mb = limitMB();
  const results = await Promise.all(list.map(async (file) => {
    const id = ++seq;
    const name = file.name || 'pasted-image.png';
    set({ uploads: [...state.uploads, { id, key, name, size: file.size, loaded: 0, status: 'uploading', comment: comment || null }] });
    const fail = (message, hint) => {
      patchUpload(id, { status: 'error', error: message });
      toast({ kind: 'error', title: `Could not attach ${name}`, body: message, hint });
      setTimeout(() => dropUpload(id), 12000);
      return null;
    };
    if (mb && file.size > mb * 1048576) return fail(`The file is over the ${mb} MB limit (${formatBytes(file.size)}).`);
    try {
      const att = await uploadFile(key, file, { comment, onProgress: (loaded) => patchUpload(id, { loaded }) });
      dropUpload(id);
      return att;
    } catch (e) {
      if (e.status === 413) return fail(`The file is over the ${mb || 'server'} MB limit (${formatBytes(file.size)}).`, e.hint);
      return fail(e.message, e.hint);
    }
  }));
  const ok = results.filter(Boolean);
  if (ok.length) {
    toast({ kind: 'success', title: ok.length === 1 ? `Attached ${ok[0].name}` : `Attached ${ok.length} files` });
    if (state.detail && state.detail.key === key) loadDetail(key);
  }
  return ok;
}

export function deleteAttachment(a) {
  confirmDialog({
    title: `Delete ${a.name}?`,
    body: `The file (${formatBytes(a.size)}) is removed from ${a.issue} for good.`,
    confirm: 'Delete', danger: true,
    onConfirm: async () => {
      if (await mutate('DELETE', `/attachments/${a.id}`)) {
        toast({ kind: 'success', title: `Deleted ${a.name}` });
        if (state.detail) loadDetail(state.detail.key);
      }
    },
  });
}

/** Hidden file input behind a button. */
export function pickFiles(onFiles) {
  const input = document.createElement('input');
  input.type = 'file';
  input.multiple = true;
  input.className = 'sr-only';
  input.addEventListener('change', () => { onFiles([...input.files]); input.remove(); });
  document.body.appendChild(input);
  input.click();
}

/** Drag-and-drop files onto an element. Returns [dragging, handlers]. */
export function useFileDrop(onFiles) {
  const [over, setOver] = useState(false);
  const depth = useRef(0);
  const hasFiles = (e) => e.dataTransfer && [...e.dataTransfer.types].includes('Files');
  return [over, {
    onDragEnter: (e) => { if (!hasFiles(e)) return; e.preventDefault(); depth.current++; setOver(true); },
    onDragOver: (e) => { if (!hasFiles(e)) return; e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; },
    onDragLeave: (e) => { if (!hasFiles(e)) return; depth.current = Math.max(0, depth.current - 1); if (!depth.current) setOver(false); },
    onDrop: (e) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      depth.current = 0;
      setOver(false);
      onFiles([...e.dataTransfer.files]);
    },
  }];
}

export function DropOverlay({ issueKey }) {
  return html`<div class="drop-overlay" aria-hidden="true">
    <div class="drop-card"><${Icon} name="upload" size=${22} /><strong>Drop to attach to ${issueKey}</strong>
      <span>Images, audio, video, PDFs or any file${limitMB() ? ` up to ${limitMB()} MB` : ''}</span></div>
  </div>`;
}

// ---------------------------------------------------------------- display

export function AttachmentsSection({ issue }) {
  const items = issue.attachments || [];
  const uploads = state.uploads.filter((u) => u.key === issue.key && !u.comment);
  const fromComments = items.filter((a) => a.comment_id).length;
  return html`<section class="section">
    <div class="section-head">
      <h3 class="section-title">Attachments ${items.length ? html`<span class="section-count">${items.length}</span>` : null}</h3>
      <button type="button" class="btn btn-ghost btn-sm" onClick=${() => pickFiles((f) => uploadFiles(issue.key, f))}>
        <${Icon} name="paperclip" size=${13} />Attach</button>
    </div>
    ${items.length || uploads.length
      ? html`<${AttachmentGallery} items=${items} uploads=${uploads} />
        ${fromComments ? html`<div class="section-note">${plural(fromComments, 'file')} came with comments; they also show under each comment.</div>` : null}`
      : html`<div class="section-empty">Drag files onto this issue, or use Attach. Screenshots, logs, recordings and specs all work.</div>`}
  </section>`;
}

/** Images as a thumbnail grid, audio/video as players, everything else as file rows. */
export function AttachmentGallery({ items, uploads = [], compact }) {
  const images = items.filter((a) => a.kind === 'image');
  const media = items.filter((a) => a.kind === 'audio' || a.kind === 'video');
  const files = items.filter((a) => a.kind !== 'image' && a.kind !== 'audio' && a.kind !== 'video');
  return html`<div class=${cx('att', compact && 'is-compact')}>
    ${images.length ? html`<div class="att-grid">${images.map((a, i) => html`<${Thumb} key=${a.id} a=${a} onOpen=${() => set({ lightbox: { items: images, index: i } })} />`)}</div>` : null}
    ${media.map((a) => html`<${Player} key=${a.id} a=${a} />`)}
    ${files.length ? html`<div class="att-files">${files.map((a) => html`<${FileRow} key=${a.id} a=${a} />`)}</div>` : null}
    ${uploads.map((u) => html`<${UploadRow} key=${'u' + u.id} u=${u} />`)}
  </div>`;
}

function meta(a) {
  return `${kindOf(a).label} · ${formatBytes(a.size)} · ${a.author || 'unknown'} · ${absTime(a.created_at)}${a.comment_id ? ` · comment #${a.comment_id}` : ''}`;
}

function Thumb({ a, onOpen }) {
  const [broken, setBroken] = useState(false);
  return html`<figure class="thumb" title=${a.name + '\n' + meta(a)}>
    <button type="button" class="thumb-open" onClick=${onOpen} aria-label=${'Open ' + a.name}>
      ${broken ? html`<span class="thumb-fallback"><${Icon} name="file-image" size=${22} /></span>`
        : html`<img src=${a.url} alt=${a.name} loading="lazy" decoding="async" onError=${() => setBroken(true)} />`}
    </button>
    <figcaption class="thumb-cap"><span class="thumb-name">${a.name}</span><span class="thumb-size">${formatBytes(a.size)}</span></figcaption>
    <span class="thumb-actions">
      <a class="icon-btn is-xs" href=${a.url} download=${a.name} aria-label=${'Download ' + a.name} title="Download"><${Icon} name="download" size=${13} /></a>
      <button type="button" class="icon-btn is-xs" aria-label=${'Delete ' + a.name} title="Delete" onClick=${() => deleteAttachment(a)}><${Icon} name="trash-2" size=${13} /></button>
    </span>
  </figure>`;
}

function Player({ a }) {
  return html`<div class=${cx('player', 'is-' + a.kind)}>
    <div class="player-head">
      <${Icon} name=${kindOf(a).icon} size=${15} cls="att-ico" />
      <span class="att-name" title=${a.name}>${a.name}</span>
      <span class="att-meta">${formatBytes(a.size)}</span>
      <${AttActions} a=${a} />
    </div>
    ${a.kind === 'video'
      ? html`<video class="player-video" controls preload="metadata" src=${a.url} aria-label=${a.name}></video>`
      : html`<audio class="player-audio" controls preload="metadata" src=${a.url} aria-label=${a.name}></audio>`}
  </div>`;
}

function FileRow({ a }) {
  const inline = a.kind === 'pdf' || a.kind === 'text';
  return html`<div class="file-row" title=${meta(a)}>
    <span class=${cx('file-ico', 'file-' + a.kind)}><${Icon} name=${kindOf(a).icon} size=${16} /></span>
    <span class="file-txt">
      ${inline ? html`<a class="att-name" href=${a.url} target="_blank" rel="noopener">${a.name}</a>` : html`<span class="att-name">${a.name}</span>`}
      <span class="att-meta">${kindOf(a).label} · ${formatBytes(a.size)}${a.author ? ' · ' + a.author : ''}</span>
    </span>
    <${AttActions} a=${a} />
  </div>`;
}

function AttActions({ a }) {
  return html`<span class="att-actions">
    ${a.path ? html`<${IconButton} icon="copy" size=${13} cls="is-xs" label="Copy the file path for an agent"
      onClick=${async () => { if (await copyText(a.path)) toast({ kind: 'success', title: 'Copied path', body: a.path }); }} />` : null}
    <a class="icon-btn is-xs" href=${a.url} download=${a.name} aria-label=${'Download ' + a.name} title="Download"><${Icon} name="download" size=${13} /></a>
    <${IconButton} icon="trash-2" size=${13} cls="is-xs" label=${'Delete ' + a.name} onClick=${() => deleteAttachment(a)} />
  </span>`;
}

function UploadRow({ u }) {
  const pct = u.size ? Math.round((100 * u.loaded) / u.size) : 0;
  return html`<div class=${cx('file-row', 'upload-row', u.status === 'error' && 'is-error')} role="status">
    <span class="file-ico"><${Icon} name=${u.status === 'error' ? 'circle-alert' : 'upload'} size=${16} /></span>
    <span class="file-txt">
      <span class="att-name">${u.name}</span>
      ${u.status === 'error'
        ? html`<span class="att-meta tone-red">${u.error}</span>`
        : html`<span class="upload-bar" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow=${pct} aria-label=${'Uploading ' + u.name}>
            <span style=${{ width: pct + '%' }}></span></span>`}
    </span>
    <span class="att-meta">${u.status === 'error' ? '' : `${pct}% of ${formatBytes(u.size)}`}</span>
    ${u.status === 'error' ? html`<${IconButton} icon="x" size=${13} cls="is-xs" label="Dismiss" onClick=${() => dropUpload(u.id)} />` : null}
  </div>`;
}

// ---------------------------------------------------------------- lightbox

export function Lightbox() {
  const lb = state.lightbox;
  const ref = useRef(null);
  useEffect(() => {
    if (!lb) return undefined;
    const prev = document.activeElement;
    if (ref.current) ref.current.focus();
    const onKey = (e) => {
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); set({ lightbox: null }); }
      if (e.key === 'ArrowLeft') { e.preventDefault(); e.stopPropagation(); step(-1); }
      if (e.key === 'ArrowRight') { e.preventDefault(); e.stopPropagation(); step(1); }
    };
    document.addEventListener('keydown', onKey, true);
    return () => {
      document.removeEventListener('keydown', onKey, true);
      if (prev && prev.isConnected) prev.focus({ preventScroll: true });
    };
  }, [!!lb]);
  if (!lb) return null;
  const a = lb.items[lb.index];
  const many = lb.items.length > 1;
  return html`<div class="lightbox" role="dialog" aria-modal="true" aria-label=${'Image ' + a.name} tabindex="-1" ref=${ref}
    onClick=${(e) => { if (e.target === e.currentTarget || e.target.classList.contains('lb-stage')) set({ lightbox: null }); }}>
    <div class="lb-bar">
      <span class="lb-name" title=${a.name}>${a.name}</span>
      <span class="lb-meta">${formatBytes(a.size)}${many ? ` · ${lb.index + 1} of ${lb.items.length}` : ''}</span>
      <span class="top-spacer"></span>
      <a class="lb-btn" href=${a.url} download=${a.name} title="Download" aria-label=${'Download ' + a.name}><${Icon} name="download" size=${18} /></a>
      <a class="lb-btn" href=${a.url} target="_blank" rel="noopener" title="Open the original in a new tab" aria-label="Open original"><${Icon} name="external-link" size=${18} /></a>
      <button type="button" class="lb-btn" title="Close (Esc)" aria-label="Close" onClick=${() => set({ lightbox: null })}><${Icon} name="x" size=${20} /></button>
    </div>
    <div class="lb-stage">
      ${many ? html`<button type="button" class="lb-nav is-prev" aria-label="Previous image" title="Previous (←)" onClick=${() => step(-1)}><${Icon} name="chevron-left" size=${26} /></button>` : null}
      <img class="lb-img" src=${a.url} alt=${a.name} key=${a.id} />
      ${many ? html`<button type="button" class="lb-nav is-next" aria-label="Next image" title="Next (→)" onClick=${() => step(1)}><${Icon} name="chevron-right" size=${26} /></button>` : null}
    </div>
  </div>`;
}

function step(d) {
  const lb = state.lightbox;
  if (!lb) return;
  const n = lb.items.length;
  set({ lightbox: Object.assign({}, lb, { index: (lb.index + d + n) % n }) });
}
