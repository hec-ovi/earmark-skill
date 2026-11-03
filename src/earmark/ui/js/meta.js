// One vocabulary for status, priority, type, comment kind, role and presence:
// every screen renders these through the same icon + color + label.

export const STATUS = {
  backlog: { label: 'Backlog', icon: 'circle-dashed', hint: 'Not planned yet' },
  todo: { label: 'Todo', icon: 'circle', hint: 'Ready to be claimed' },
  in_progress: { label: 'In progress', icon: 'st-progress', hint: 'An agent is working on it' },
  in_review: { label: 'In review', icon: 'eye', hint: 'Waiting for a reviewer' },
  blocked: { label: 'Blocked', icon: 'ban', hint: 'Cannot move until something else lands' },
  done: { label: 'Done', icon: 'circle-check', hint: 'Finished and approved' },
  canceled: { label: 'Canceled', icon: 'circle-x', hint: 'Will not be done' },
};
export const STATUS_KEYS = ['backlog', 'todo', 'in_progress', 'in_review', 'blocked', 'done', 'canceled'];
export const GROUP_ORDER = ['in_progress', 'in_review', 'todo', 'blocked', 'backlog', 'done', 'canceled'];
export const BOARD_ORDER = ['backlog', 'todo', 'in_progress', 'in_review', 'blocked', 'done'];

/** Transition table from ARCHITECTURE.md (the owner may make any move; used for hints). */
export const FLOW = {
  backlog: ['todo', 'canceled'],
  todo: ['in_progress', 'backlog', 'blocked', 'canceled'],
  in_progress: ['in_review', 'todo', 'blocked', 'done', 'canceled'],
  in_review: ['done', 'todo', 'in_progress'],
  blocked: ['todo', 'in_progress', 'canceled'],
  done: ['todo'],
  canceled: ['todo'],
};

export const PRIORITY = {
  urgent: { label: 'Urgent', icon: 'pri-urgent' },
  high: { label: 'High', icon: 'pri-high' },
  medium: { label: 'Medium', icon: 'pri-medium' },
  low: { label: 'Low', icon: 'pri-low' },
  none: { label: 'No priority', icon: 'pri-none' },
};
export const PRIORITY_KEYS = ['urgent', 'high', 'medium', 'low', 'none'];

export const TYPE = {
  bug: { label: 'Bug', icon: 'bug' },
  feature: { label: 'Feature', icon: 'sparkles' },
  task: { label: 'Task', icon: 'square-check' },
  chore: { label: 'Chore', icon: 'wrench' },
};
export const TYPE_KEYS = ['bug', 'feature', 'task', 'chore'];

export const KIND = {
  note: { label: 'Note', icon: 'message-square' },
  progress: { label: 'Progress', icon: 'activity' },
  question: { label: 'Question', icon: 'circle-help' },
  handoff: { label: 'Handoff', icon: 'send' },
  approval: { label: 'Approved', icon: 'check' },
  changes: { label: 'Changes requested', icon: 'triangle-alert' },
};
export const COMPOSER_KINDS = ['note', 'progress', 'question'];

export const ROLE = {
  worker: { label: 'Worker', icon: 'hammer' },
  reviewer: { label: 'Reviewer', icon: 'scan-eye' },
  pm: { label: 'PM', icon: 'clipboard-list' },
};
export const ROLE_KEYS = ['worker', 'reviewer', 'pm'];

export const PRESENCE = {
  active: { label: 'Active', hint: 'Online, seen in the last 15 minutes' },
  idle: { label: 'Idle', hint: 'Online, quiet for more than 15 minutes' },
  offline: { label: 'Offline', hint: 'Left the board' },
};

export const LINKS = [
  { key: 'blocks', label: 'Blocks', icon: 'ban', kind: 'blocks', reverse: false },
  { key: 'blocked_by', label: 'Blocked by', icon: 'ban', kind: 'blocks', reverse: true },
  { key: 'duplicates', label: 'Duplicates', icon: 'copy', kind: 'duplicates', reverse: false },
  { key: 'duplicated_by', label: 'Duplicated by', icon: 'copy', kind: 'duplicates', reverse: true },
  { key: 'relates', label: 'Related', icon: 'link', kind: 'relates', reverse: false },
];

/** Built-in views in the sidebar. */
export const VIEWS = {
  all: { label: 'All open', icon: 'layers' },
  my: { label: 'My issues', icon: 'user' },
  review: { label: 'In review', icon: 'eye' },
  blocked: { label: 'Blocked', icon: 'ban' },
  done: { label: 'Recently done', icon: 'circle-check' },
};

/** Lucide icons offered for topics and profiles (all present in icons.svg). */
export const ICON_CHOICES = [
  'folder', 'hash', 'layout-dashboard', 'app-window', 'smile', 'gamepad-2', 'palette', 'brush',
  'image', 'film', 'camera', 'music', 'audio-lines', 'volume-2', 'headphones', 'mic', 'code',
  'terminal', 'server', 'database', 'network', 'plug', 'cpu', 'box', 'package', 'component',
  'shapes', 'puzzle', 'globe', 'map', 'smartphone', 'shield', 'lock', 'zap', 'rocket', 'flame',
  'star', 'heart', 'swords', 'wand-sparkles', 'sparkles', 'file-text', 'scroll-text', 'book-open',
  'type', 'languages', 'settings-2', 'bug', 'hammer', 'scan-eye', 'clipboard-list', 'bot',
];

export const CLI_NAMES = {
  'claude-code': 'Claude Code', codex: 'Codex', grok: 'Grok', agy: 'agy', opencode: 'OpenCode', human: 'Human',
};

const EVENT_TEXT = {
  created: 'created the issue',
  status: (e) => `moved it from ${lbl(STATUS, e.old)} to ${lbl(STATUS, e.new)}`,
  title: 'renamed it',
  body: 'edited the description',
  priority: (e) => `set priority to ${lbl(PRIORITY, e.new)}`,
  type: (e) => `changed the type to ${lbl(TYPE, e.new)}`,
  topic: (e) => (e.new ? `moved it to ${e.new}` : 'cleared the topic'),
  assignee: (e) => (e.new ? `assigned ${e.new}` : 'removed the assignee'),
  reviewer: (e) => (e.new ? `set reviewer ${e.new}` : 'cleared the reviewer'),
  due: (e) => (e.new ? `set due ${e.new}` : 'cleared the due date'),
  labels: 'changed labels',
  parent: (e) => (e.new ? `set parent ${e.new}` : 'removed the parent'),
  link: (e) => `linked ${e.new || ''}`.trim(),
  unlink: (e) => `removed link ${e.old || ''}`.trim(),
  rank: 'reordered it',
  claim: 'claimed it',
  release: 'released it',
  takeover: (e) => `took it over${e.old ? ` from ${e.old}` : ''}`,
  review: 'handed it off for review',
  approve: 'approved it',
  reject: 'requested changes',
  reopen: 'reopened it',
};

function lbl(map, key) {
  return (map[key] && map[key].label) || key || 'none';
}

export function eventText(e) {
  const t = EVENT_TEXT[e.action];
  if (!t) return e.action.replace(/_/g, ' ');
  return typeof t === 'function' ? t(e) : t;
}

export const EVENT_ICON = {
  created: 'plus', status: 'refresh-cw', title: 'pencil', body: 'pencil', priority: 'pri-high',
  type: 'tag', topic: 'folder', assignee: 'user-plus', reviewer: 'scan-eye', due: 'calendar',
  labels: 'tag', parent: 'list-tree', link: 'link', unlink: 'link-2-off', rank: 'arrow-up-down',
  claim: 'hammer', release: 'corner-up-left', takeover: 'refresh-cw', review: 'send',
  approve: 'check', reject: 'triangle-alert', reopen: 'refresh-cw',
};
