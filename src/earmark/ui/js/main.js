// earmark web UI entry: shell, routing, keyboard shortcuts.

import { html, render, useEffect } from './lib.js';
import { state, set, useStore, boot, closeIssue, navigate, issueHref, hrefFor, setIssueLayout } from './state.js';
import { loadSprite, PopoverHost, Toasts, ConfirmDialog, Dialog, Kbd, Icon, Empty, closePopover } from './components.js';
import { Sidebar, TopBar, applyTheme } from './shell.js';
import { IssueList, visibleKeys } from './list.js';
import { Board } from './board.js';
import { IssuePanel, IssuePage } from './detail.js';
import { TeamPage } from './team.js';
import { NewIssueDialog, openNewIssue } from './newissue.js';
import { TopicDialog } from './topics.js';
import { Lightbox } from './attachments.js';
import { cx, isTypingTarget } from './util.js';

const SHORTCUTS = [
  ['/', 'Search issues'],
  ['c', 'New issue'],
  ['j / k', 'Next / previous issue'],
  ['Enter', 'Open the selected issue'],
  ['f', 'Issue as full page / side panel'],
  ['Esc', 'Close panel, dialog or menu'],
  ['?', 'Show this help'],
  ['Ctrl / ⌘ + Enter', 'Send a comment, save a description'],
];

function HelpDialog() {
  const close = () => set({ dialog: null });
  return html`<${Dialog} title="Keyboard shortcuts" icon="keyboard" onClose=${close} cls="is-narrow">
    <dl class="shortcuts">
      ${SHORTCUTS.map(([k, d]) => html`<div class="shortcut" key=${k}><dt>${k.split(' ').map((p) => (p === '/' && k !== '/' ? ' / ' : p === '+' ? ' + ' : html`<${Kbd}>${p}</${Kbd}>`))}</dt><dd>${d}</dd></div>`)}
    </dl>
    <p class="muted small help-note">Agents see the same board through the CLI: <code>earmark ls</code>, <code>earmark show KEY</code>, <code>earmark comments KEY --since ID</code>.</p>
  </${Dialog}>`;
}

function DialogHost() {
  const d = state.dialog;
  if (!d) return null;
  if (d.type === 'new-issue') return html`<${NewIssueDialog} d=${d} key=${'new'} />`;
  if (d.type === 'topic') return html`<${TopicDialog} d=${d} key=${'topic' + d.mode} />`;
  if (d.type === 'confirm') return html`<${ConfirmDialog} d=${d} />`;
  if (d.type === 'help') return html`<${HelpDialog} />`;
  return null;
}

function BootError({ e }) {
  const noBoard = e.code === 'no_board';
  return html`<div class="boot-error">
    <${Empty} icon="circle-alert" title=${noBoard ? 'No board here yet' : 'Cannot load the board'}
      action=${html`<button type="button" class="btn btn-primary" onClick=${() => location.reload()}><${Icon} name="refresh-cw" size=${14} />Retry</button>`}>
      <p>${e.message}</p>
      ${e.hint ? html`<pre class="cmd">${e.hint}</pre>` : noBoard ? html`<pre class="cmd">earmark init --prefix GAME</pre>` : null}
    </${Empty}>
  </div>`;
}

function App() {
  useStore();
  useEffect(() => {
    const onKey = (e) => handleKey(e);
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, []);
  if (state.bootError) return html`<${BootError} e=${state.bootError} />`;
  if (!state.ready) return html`<div class="boot-msg">Loading board…</div>`;
  const r = state.route;
  const fullIssue = r.page === 'issue' && r.full;
  const panel = r.page === 'issue' && !r.full;
  const kind = fullIssue ? 'is-issue' : r.page === 'team' ? 'is-team' : state.base.layout === 'board' ? 'is-board' : 'is-list';
  const main = fullIssue ? html`<${IssuePage} />` : r.page === 'team' ? html`<${TeamPage} />` : state.base.layout === 'board' ? html`<${Board} />` : html`<${IssueList} />`;
  return html`<div class=${cx('shell', panel && 'has-panel', state.sidebarOpen && 'side-open')}>
    <${Sidebar} />
    <div class="main">
      <${TopBar} />
      <main class=${cx('content', kind)} id="content">${main}</main>
    </div>
    ${panel ? html`<${IssuePanel} />` : null}
    <${DialogHost} />
    <${Lightbox} />
    <${PopoverHost} />
    <${Toasts} />
  </div>`;
}

function handleKey(e) {
  if (e.defaultPrevented || state.lightbox) return;
  if (e.key === 'Escape') {
    if (state.popover) { closePopover(); return; }
    if (state.dialog) { set({ dialog: null }); return; }
    if (state.sidebarOpen) { set({ sidebarOpen: false }); return; }
    if (state.route.page === 'issue' && !isTypingTarget(e.target)) { closeIssue(); return; }
    return;
  }
  if (isTypingTarget(e.target) || e.metaKey || e.ctrlKey || e.altKey) return;
  if (state.dialog || state.popover) return;
  if (e.key === '/') {
    e.preventDefault();
    if (state.route.page === 'team') navigate(hrefFor(state.base));
    if (state.route.page === 'issue' && (state.route.full || window.innerWidth < 700)) closeIssue();
    setTimeout(() => window.dispatchEvent(new Event('earmark:search')), 50);
    return;
  }
  if (e.key === 'c') { e.preventDefault(); openNewIssue(); return; }
  if (e.key === '?') { e.preventDefault(); set({ dialog: { type: 'help' } }); return; }
  if (e.key === 'f' && state.route.page === 'issue') { e.preventDefault(); setIssueLayout(!state.route.full); return; }
  if (state.route.page === 'team') return;
  if (e.key === 'j' || e.key === 'k' || e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    if ((e.key === 'ArrowDown' || e.key === 'ArrowUp') && e.target.closest && e.target.closest('.panel')) return;
    const keys = visibleKeys();
    if (!keys.length) return;
    e.preventDefault();
    const cur = state.selected || (state.route.page === 'issue' ? state.route.key : null);
    const i = keys.indexOf(cur);
    const down = e.key === 'j' || e.key === 'ArrowDown';
    const next = i < 0 ? keys[0] : keys[Math.max(0, Math.min(keys.length - 1, i + (down ? 1 : -1)))];
    set({ selected: next });
    if (state.route.page === 'issue') navigate(issueHref(next));
    return;
  }
  if (e.key === 'Enter' && state.selected && !(e.target.closest && e.target.closest('button, a'))) {
    e.preventDefault();
    navigate(issueHref(state.selected));
  }
}

async function start() {
  applyTheme(state.theme);
  await loadSprite();
  const root = document.getElementById('app');
  root.classList.remove('boot');
  root.textContent = '';
  render(html`<${App} />`, root);
  boot();
}

start();
