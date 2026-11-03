// Small, safe Markdown renderer for issue bodies and comments.
// Safety model: the whole source is HTML-escaped first; afterwards only tags generated here
// are added, and link targets must start with http:, https: or mailto:.

const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
export const escapeHtml = (s) => String(s).replace(/[&<>"']/g, (c) => ESC[c]);

const SAFE_URL = /^(https?:\/\/|mailto:)[^\s]+$/i;

/**
 * @param {string} src markdown source
 * @param {{prefix?: string}} opts prefix: board key prefix for GAME-42 style links
 */
export function renderMarkdown(src, opts = {}) {
  const lines = escapeHtml(src || '').replace(/\r\n?/g, '\n').split('\n');
  return blocks(lines, opts);
}

function blocks(lines, opts) {
  const out = [];
  let i = 0;
  let para = [];
  const flush = () => {
    if (para.length) {
      out.push(`<p>${joinLines(para, opts)}</p>`);
      para = [];
    }
  };
  while (i < lines.length) {
    const line = lines[i];
    const fence = line.match(/^\s*(`{3,}|~{3,})\s*([\w+-]*)\s*$/);
    if (fence) {
      flush();
      const close = fence[1];
      const body = [];
      i++;
      while (i < lines.length && !lines[i].trim().startsWith(close)) body.push(lines[i++]);
      i++;
      const lang = fence[2] ? ` data-lang="${fence[2]}"` : '';
      out.push(`<pre${lang}><code>${body.join('\n')}</code></pre>`);
      continue;
    }
    if (!line.trim()) {
      flush();
      i++;
      continue;
    }
    const heading = line.match(/^(#{1,6})\s+(.*?)\s*#*\s*$/);
    if (heading) {
      flush();
      const level = Math.min(6, heading[1].length + 2);
      out.push(`<h${level}>${inline(heading[2], opts)}</h${level}>`);
      i++;
      continue;
    }
    if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) {
      flush();
      out.push('<hr>');
      i++;
      continue;
    }
    if (/^\s*&gt;/.test(line)) {
      flush();
      const quoted = [];
      while (i < lines.length && /^\s*&gt;/.test(lines[i])) {
        quoted.push(lines[i].replace(/^\s*&gt;\s?/, ''));
        i++;
      }
      out.push(`<blockquote>${blocks(quoted, opts)}</blockquote>`);
      continue;
    }
    if (LIST_ITEM.test(line)) {
      flush();
      const items = [];
      while (i < lines.length && (LIST_ITEM.test(lines[i]) || (/^\s{2,}\S/.test(lines[i]) && items.length))) {
        items.push(lines[i]);
        i++;
      }
      out.push(list(items, opts));
      continue;
    }
    para.push(line);
    i++;
  }
  flush();
  return out.join('');
}

const LIST_ITEM = /^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$/;

// CommonMark line breaks: a newline inside a paragraph is a soft break (a space); two trailing
// spaces or a trailing backslash make a hard break. Agents hard-wrap at ~100 columns.
const HARD_BREAK = /( {2,}|\\)$/;
const stripBreak = (line) => line.replace(/( {2,}|\\)$/, '').replace(/\s+$/, '');

function joinLines(lines, opts) {
  let html = '';
  lines.forEach((line, i) => {
    const last = i === lines.length - 1;
    html += inline(last ? line.trim() : stripBreak(line.trim()), opts);
    if (!last) html += HARD_BREAK.test(line) ? '<br>' : ' ';
  });
  return html;
}

function list(lines, opts) {
  // Items with their indentation; continuation lines join the previous item.
  const items = [];
  for (const raw of lines) {
    const m = raw.match(LIST_ITEM);
    if (!m) {
      if (items.length) {
        const item = items[items.length - 1];
        item.html += (item.hard ? '<br>' : ' ') + inline(stripBreak(raw.trim()), opts);
        item.hard = HARD_BREAK.test(raw);
      }
      continue;
    }
    let text = m[3];
    let task = null;
    const t = text.match(/^\[([ xX])\]\s+(.*)$/);
    if (t) {
      task = t[1] !== ' ';
      text = t[2];
    }
    items.push({
      indent: m[1].replace(/\t/g, '    ').length,
      ordered: /\d/.test(m[2]),
      start: parseInt(m[2], 10) || 1,
      task,
      html: inline(stripBreak(text), opts),
      hard: HARD_BREAK.test(raw),
    });
  }
  return renderItems(items);
}

function renderItems(items) {
  let out = '';
  let i = 0;
  while (i < items.length) {
    const { indent, ordered, start } = items[i];
    const lis = [];
    let tasks = false;
    while (i < items.length && items[i].indent === indent && items[i].ordered === ordered) {
      const item = items[i++];
      const kids = [];
      while (i < items.length && items[i].indent > indent) kids.push(items[i++]);
      const box = item.task === null ? ''
        : `<span class="md-task${item.task ? ' is-done' : ''}" role="img" aria-label="${item.task ? 'done' : 'not done'}"></span>`;
      if (item.task !== null) tasks = true;
      lis.push(`<li${item.task === null ? '' : ' class="md-task-item"'}>${box}${item.html}${kids.length ? renderItems(kids) : ''}</li>`);
    }
    const tag = ordered ? 'ol' : 'ul';
    const startAttr = ordered && start !== 1 ? ` start="${start}"` : '';
    out += `<${tag}${startAttr}${tasks ? ' class="md-tasks"' : ''}>${lis.join('')}</${tag}>`;
  }
  return out;
}

function inline(text, opts) {
  const slots = [];
  const keep = (htmlText) => `\u0000${slots.push(htmlText) - 1}\u0000`;

  // 1. code spans are literal
  let s = text.replace(/(`+)([^`]|[^`].*?[^`])\1(?!`)/g, (_, __, code) => keep(`<code>${code}</code>`));

  // 2. [text](url) links with a safe scheme
  s = s.replace(/\[([^\]\n]+)\]\(([^)\s]+)\)/g, (all, label, url) => {
    const href = url.replace(/&amp;/g, '&');
    if (!SAFE_URL.test(href)) return all;
    return keep(`<a href="${url}" target="_blank" rel="noopener noreferrer">${emphasis(label)}</a>`);
  });

  // 3. bare URLs
  s = s.replace(/\b(https?:\/\/[^\s<\u0000]+)/gi, (all, url) => {
    let tail = '';
    while (/[.,;:!?)\]]$/.test(url) && !(url.endsWith(')') && url.includes('('))) {
      tail = url.slice(-1) + tail;
      url = url.slice(0, -1);
    }
    if (url.endsWith('&amp')) return all;
    return keep(`<a href="${url}" target="_blank" rel="noopener noreferrer">${url}</a>`) + tail;
  });

  // 4. issue keys, comment ids and @mentions
  if (opts.prefix) {
    const re = new RegExp(`(^|[^\\w/-])(${opts.prefix}-(\\d+))\\b`, 'g');
    s = s.replace(re, (_, pre, key) => pre + keep(`<a class="md-key" href="#/issue/${key}">${key}</a>`));
  }
  s = s.replace(/(^|[\s(])#(\d{1,9})\b/g, (_, pre, id) => pre + keep(`<button type="button" class="md-cref" data-cid="${id}">#${id}</button>`));
  s = s.replace(/(^|[^\w@./-])@([a-z0-9][a-z0-9-]{0,31})\b/g, (_, pre, h) => pre + keep(`<a class="md-mention" href="#/team" data-handle="${h}">@${h}</a>`));

  // 5. emphasis
  s = emphasis(s);
  return s.replace(/\u0000(\d+)\u0000/g, (_, n) => slots[Number(n)]);
}

function emphasis(s) {
  return s
    .replace(/\*\*(?=\S)([^*]*?\S)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^\w])__(?=\S)([^_]*?\S)__(?!\w)/g, '$1<strong>$2</strong>')
    .replace(/~~(?=\S)([^~]*?\S)~~/g, '<del>$1</del>')
    .replace(/(^|[^*\w])\*(?=[^\s*])([^*]*?[^\s*])\*(?![*\w])/g, '$1<em>$2</em>')
    .replace(/(^|[^\w])_(?=[^\s_])([^_]*?[^\s_])_(?!\w)/g, '$1<em>$2</em>');
}

/** First line of a body as plain text (for reply quotes and previews). */
export function excerpt(src, max = 90) {
  const text = String(src || '').replace(/^\s*(```|~~~)[\s\S]*?(^\s*\1\s*$|$(?![\s\S]))/gm, ' ');
  const line = text.split('\n').map((l) => l.trim()).find((l) => l && !/^(```|~~~)/.test(l)) || '';
  const plain = line.replace(/^#+\s*|^>\s*|^[-*+]\s+(\[[ xX]\]\s+)?/g, '').replace(/[*_`~]/g, '');
  return plain.length > max ? plain.slice(0, max - 1) + '…' : plain;
}
