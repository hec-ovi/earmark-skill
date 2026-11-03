// Conversation: comments oldest → newest, optional activity events, and the composer.

import { html, useState, useRef, useEffect } from './lib.js';
import { state, set, mutate, loadDetail, toast } from './state.js';
import { enc } from './api.js';
import { Icon, Avatar, KindBadge, Age, agentOf, Kbd, RoleBadge, PriorityIcon, StatusIcon } from './components.js';
import { renderMarkdown, excerpt } from './markdown.js';
import { KIND, COMPOSER_KINDS, eventText, EVENT_ICON, PRIORITY, STATUS } from './meta.js';
import { cx, copyText, absTime, store, age } from './util.js';
import { mdClick } from './components.js';

const WORKFLOW = new Set(['claim', 'release', 'takeover', 'review', 'approve', 'reject', 'reopen']);

export function Conversation({ issue, d }) {
  const [replyTo, setReplyTo] = useState(null);
  const comments = d.comments || [];
  const byId = new Map(comments.map((c) => [c.id, c]));
  const showActivity = state.showActivity;
  const entries = comments.map((c) => ({ at: c.created_at, c }));
  if (showActivity) {
    // a workflow event (claim, review, approve…) already says what the paired status move did
    const workflow = new Set((d.events || []).filter((e) => WORKFLOW.has(e.action)).map((e) => e.actor + '|' + e.at));
    (d.events || []).filter((e) => !(e.action === 'status' && workflow.has(e.actor + '|' + e.at)))
      .forEach((e) => entries.push({ at: e.at, e }));
    entries.sort((a, b) => (a.at < b.at ? -1 : a.at > b.at ? 1 : a.c ? 1 : -1));
  }
  const composerRef = useRef(null);
  const reply = (c) => {
    setReplyTo(c);
    const ta = composerRef.current && composerRef.current.querySelector('textarea');
    if (ta) { ta.focus(); ta.scrollIntoView({ block: 'nearest' }); }
  };
  return html`<section class="section convo">
    <div class="section-head">
      <h3 class="section-title">Conversation ${comments.length ? html`<span class="section-count">${comments.length}</span>` : null}</h3>
      <label class="switch" title="Interleave status changes, claims and edits">
        <input type="checkbox" checked=${showActivity} onChange=${(e) => { set({ showActivity: e.target.checked }); store.set('detail.activity', e.target.checked); }} />
        <span class="switch-track"><span class="switch-thumb"></span></span>
        <span>Activity</span>
      </label>
    </div>
    <ol class="thread" onClick=${mdClick}>
      ${entries.map((x) => (x.c
        ? html`<${Comment} key=${'c' + x.c.id} c=${x.c} issue=${issue} parent=${x.c.reply_to ? byId.get(x.c.reply_to) : null} onReply=${reply} fresh=${d.fresh && d.fresh.has(x.c.id)} />`
        : html`<${EventRow} key=${'e' + x.e.id} e=${x.e} />`))}
      ${!entries.length ? html`<li class="thread-empty">No comments yet. Agents post progress, questions and handoffs here.</li>` : null}
    </ol>
    <div ref=${composerRef}><${Composer} issue=${issue} replyTo=${replyTo} clearReply=${() => setReplyTo(null)} /></div>
  </section>`;
}

function Comment({ c, issue, parent, onReply, fresh }) {
  const a = agentOf(c.author);
  const owner = state.board && state.board.owner === c.author;
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(c.body);
  const prefix = state.board ? state.board.prefix : null;
  const copyCursor = async () => {
    const cmd = `earmark comments ${issue.key} --since ${c.id}`;
    if (await copyText(cmd)) toast({ kind: 'success', title: 'Copied', body: cmd });
  };
  const jump = (id) => {
    const el = document.getElementById('c' + id);
    if (!el) return;
    el.scrollIntoView({ block: 'center', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
    el.classList.remove('is-target');
    void el.offsetWidth;
    el.classList.add('is-target');
  };
  const save = async () => {
    const res = await mutate('PATCH', `/comments/${c.id}`, { body: draft });
    if (res) { setEditing(false); loadDetail(issue.key); }
  };
  return html`<li class=${cx('comment', 'kind-row-' + c.kind, fresh && 'is-fresh')} id=${'c' + c.id}>
    <${Avatar} handle=${c.author} size=${28} />
    <div class="comment-main">
      <div class="comment-head">
        <span class="comment-author">${c.author}</span>
        ${a && a.role ? html`<${RoleBadge} role=${a.role} />` : owner ? html`<span class="role role-owner">Owner</span>` : null}
        ${c.kind !== 'note' ? html`<${KindBadge} kind=${c.kind} />` : null}
        <span class="comment-spacer"></span>
        <span class="comment-actions">
          <button type="button" class="act" onClick=${() => onReply(c)} title="Reply to this comment"><${Icon} name="reply" size=${13} />Reply</button>
          ${owner && !editing ? html`<button type="button" class="act" onClick=${() => { setDraft(c.body); setEditing(true); }} title="Edit your comment"><${Icon} name="pencil" size=${13} />Edit</button>` : null}
        </span>
        <time class="comment-time" datetime=${c.created_at} title=${absTime(c.created_at)}>${age(c.created_at)}${c.edited_at ? html`<span class="muted" title=${'Edited ' + absTime(c.edited_at)}> · edited</span>` : null}</time>
        <button type="button" class="cid mono" title=${`Copy: earmark comments ${issue.key} --since ${c.id}`} onClick=${copyCursor}>#${c.id}</button>
      </div>
      ${c.reply_to ? html`<button type="button" class="reply-quote" onClick=${() => jump(c.reply_to)} title="Show the comment this replies to">
        <${Icon} name="corner-up-left" size=${12} />
        <span class="mono">#${c.reply_to}</span>
        ${parent ? html`<strong>${parent.author}</strong><span class="reply-ex">${excerpt(parent.body, 80)}</span>` : html`<span class="muted">earlier comment</span>`}
      </button>` : null}
      ${editing
        ? html`<div class="comment-edit">
            <textarea class="textarea" rows="4" value=${draft} aria-label="Edit comment" onInput=${(e) => setDraft(e.target.value)}
              onKeyDown=${(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); save(); } if (e.key === 'Escape') { e.stopPropagation(); setEditing(false); } }}></textarea>
            <div class="edit-actions"><button type="button" class="btn btn-sm" onClick=${() => setEditing(false)}>Cancel</button>
              <button type="button" class="btn btn-sm btn-primary" onClick=${save}>Save</button></div>
          </div>`
        : html`<div class="md comment-body" dangerouslySetInnerHTML=${{ __html: renderMarkdown(c.body, { prefix }) }}></div>`}
    </div>
  </li>`;
}

function EventRow({ e }) {
  return html`<li class="event">
    <span class="event-ico">${e.action === 'priority' && PRIORITY[e.new]
      ? html`<${PriorityIcon} priority=${e.new} size=${12} />`
      : e.action === 'status' && STATUS[e.new] ? html`<${StatusIcon} status=${e.new} size=${12} />`
        : html`<${Icon} name=${EVENT_ICON[e.action] || 'activity'} size=${12} />`}</span>
    <span class="event-txt"><strong>${e.actor}</strong> ${eventText(e)}</span>
    <time class="event-time" datetime=${e.at} title=${absTime(e.at)}>${age(e.at)}</time>
  </li>`;
}

function Composer({ issue, replyTo, clearReply }) {
  const [body, setBody] = useState('');
  const [kind, setKind] = useState('note');
  const [busy, setBusy] = useState(false);
  const ta = useRef(null);
  useEffect(() => {
    const el = ta.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = Math.min(320, Math.max(72, el.scrollHeight)) + 'px';
  }, [body]);
  const send = async () => {
    const text = body.trim();
    if (!text || busy) return;
    setBusy(true);
    const res = await mutate('POST', `/issues/${enc(issue.key)}/comments`, { body: text, kind, reply_to: replyTo ? replyTo.id : undefined });
    setBusy(false);
    if (res) {
      setBody('');
      clearReply();
      setKind('note');
      loadDetail(issue.key, { incremental: true });
    }
  };
  const mac = /Mac|iPhone|iPad/.test(navigator.platform || '');
  return html`<div class="composer" onKeyDown=${(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); send(); } }}>
    ${replyTo ? html`<div class="composer-reply">
      <${Icon} name="corner-up-left" size=${13} />Replying to <span class="mono">#${replyTo.id}</span> <strong>${replyTo.author}</strong>
      <span class="reply-ex">${excerpt(replyTo.body, 60)}</span>
      <button type="button" class="icon-btn is-xs" aria-label="Cancel reply" onClick=${clearReply}><${Icon} name="x" size=${12} /></button>
    </div>` : null}
    <textarea ref=${ta} class="textarea composer-input" value=${body} rows="3"
      placeholder=${replyTo ? `Reply to ${replyTo.author}…` : 'Write a comment… @mention an agent to reach its inbox'}
      aria-label="Comment" onInput=${(e) => setBody(e.target.value)}
      onKeyDown=${(e) => { if (e.key === 'Escape') { e.stopPropagation(); e.target.blur(); } }}></textarea>
    <div class="composer-bar">
      <div class="seg seg-sm" role="radiogroup" aria-label="Comment kind">
        ${COMPOSER_KINDS.map((k) => html`<button type="button" role="radio" aria-checked=${String(kind === k)} key=${k}
          class=${cx('seg-btn', 'kind-' + k, kind === k && 'is-on')} onClick=${() => setKind(k)} title=${KIND[k].label}>
          <${Icon} name=${KIND[k].icon} size=${13} /><span>${KIND[k].label}</span></button>`)}
      </div>
      <span class="composer-hint"><${Kbd}>${mac ? '⌘' : 'Ctrl'}</${Kbd}><${Kbd}>Enter</${Kbd}></span>
      <button type="button" class="btn btn-primary btn-sm" disabled=${!body.trim() || busy} onClick=${send}>
        <${Icon} name="send" size=${13} />Comment</button>
    </div>
  </div>`;
}
