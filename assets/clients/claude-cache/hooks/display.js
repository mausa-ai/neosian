// Presentation only. Cache evidence and maintenance decisions stay in register.js.
import { HOUR } from './evidence.js';
import { LABELS, percent, span } from './usage.js';
export const PANE = 'neosian-cache-details';
// Why keep-warm held back a refresh near a usage limit (usage.js `gate`), or
// paused after the host refused one for a rate limit, which may be a short one.
export const HELD = { limit: 'Usage limit reached · keep-warm paused',
  exhausted: 'Usage nearly exhausted · keep-warm paused to preserve it',
  refused: 'Request refused for a rate limit · keep-warm paused' };
// After a compaction the next message writes the compacted conversation, at
// least the host's own count of it; nothing is cold or failed.
export const COMPACTED = 'Context compacted';

// A positive token count rounded down to thousands ("16k"), exact below 1,000.
export function thousands(n) {
  if (!Number.isSafeInteger(n) || n <= 0) return null;
  return n < 1000 ? String(n) : `${Math.floor(n / 1000)}k`;
}

export function compacted(figure) {
  return figure ? `${COMPACTED} · your next message writes at least ${figure} tokens`
    : `${COMPACTED} · waiting for your next message`;
}

// The host's reason for an unanswered refresh (evidence.js `unanswered`).
function why({ reason, error, status }) {
  return [reason, error, status === null ? null : `HTTP ${status}`].filter(Boolean).join(' · ') ||
    'no reason given';
}

// How old a figure is, rounded up as reset times are: never newer than it is.
function ago(ms) {
  return ms < 60000 ? 'just now' : `${span(ms)} ago`;
}

// Usage figures are the host's estimates; which note is due is decided in usage.js.
// The pane always says how old each is; the line once it is older than ten minutes.
function limits({ windows, notes, last }, now) {
  const lines = windows.map(({ kind, p, resetsAt, at, shared }) => {
    const label = LABELS[kind][0].toUpperCase() + LABELS[kind].slice(1);
    return `${label} usage: ${percent(p)}%${resetsAt === null ? '' : ` · resets in ${span(resetsAt - now)}`}` +
      ` · as of ${ago(now - at)}${shared ? ' (another session)' : ''}`;
  });
  if (last) lines.push(`Last usage note: ${last.level} (${LABELS[last.window]}) at ${new Date(last.at_ms).toISOString()}`);
  const wraps = w => w.level === 'wrap' || w.level === 'pace'; // A pace wrap is a wrap too.
  const top = windows.filter(w => w.level).sort((a, b) => wraps(b) - wraps(a) || b.p - a.p)[0];
  const old = top && now - top.at > 10 * 60000 ? ` (${ago(now - top.at)})` : '';
  return { lines: ['', ...lines, `Usage notes: ${notes}`], badge: top ? `usage ${percent(top.p)}%${old}` : '',
    wrap: Boolean(top && wraps(top)) };
}

export function presentation({ enabled, busy, flight, certificate, receipt, until, reason, allowApi, usage, written },
  now) {
  let label = 'cache paused';
  let tone = 'warning';
  let note = 'next message may be cold';
  let detail = reason;
  let stated = false; // The reason already says what the note would.
  if (!enabled) {
    label = until !== null ? 'keep-warm duration ended' : 'keep-warm off';
    detail = label;
    tone = 'subtle'; note = '';
  } else if (busy) {
    label = 'conversation active'; tone = 'subtle'; note = '';
  } else if (flight) {
    label = 'refreshing cache'; tone = 'subtle'; note = '';
  } else if (certificate && now < certificate.deadline) {
    const minutes = Math.ceil((certificate.deadline - now) / 60000);
    label = `cache ~${minutes}m`;
    detail = `Cache expected warm for about ${minutes} minutes (estimate)`;
    if (certificate.ttl !== HOUR) note = 'keep-warm needs 1h cache';
    else if (!certificate.subscription && !allowApi) note = 'keep-warm paused';
    else if (certificate.retry) note = 'refresh retry pending'; // Amber: the last refresh was not answered.
    else { note = 'keep-warm on'; tone = 'success'; }
  } else if (reason === 'Waiting for your first message') {
    label = 'keep-warm ready'; tone = 'subtle'; note = '';
  } else if (reason === 'Checking cache observation') {
    label = 'checking cache'; tone = 'subtle'; note = '';
  } else if (reason.startsWith(COMPACTED)) {
    label = 'context compacted'; tone = 'subtle'; stated = true;
    note = written ? `next message writes ${written}+ tokens` : '';
  } else if (reason.includes('expired')) label = 'cache expired';
  else if (reason.includes('unknown')) label = 'cache unknown';
  else if (reason.includes('unavailable')) { label = 'keep-warm unavailable'; note = '/neosian-cache'; }
  const details = [detail, `Keep-warm: ${enabled ? 'on' : 'off'}${until !== null
    ? ` · until ${new Date(until).toISOString()}` : ' · until session closes'}`];
  // After a wrap-up note, an idle session kept warm waits for a near reset,
  // unless a /neosian-cache duration ends first (the gate is checked in notes.js).
  const wait = note === 'keep-warm on' && !(until !== null && until < usage.waiting?.resetsAt)
    ? usage.waiting : null;
  if (wait) {
    note = 'waiting for reset';
    details.push(`Waiting for the ${LABELS[wait.kind]} reset in ${span(wait.resetsAt - now)}; keep-warm continues`);
  } else if (note && note !== 'keep-warm on' && !stated) details.push(note);
  const meter = limits(usage, now);
  details.push(...meter.lines);
  if (meter.wrap) tone = 'warning';
  if (receipt) {
    details.push('', 'Last refresh', `Cache read: ${receipt.read} tokens`,
      `Cache written: ${receipt.write} tokens`, `Output: ${receipt.output} tokens`, `Result: ${receipt.outcome}`);
    if (receipt.unanswered) details.push(`Not answered: ${why(receipt.unanswered)}`);
    if (receipt.attempt === 2) {
      details.push(`Retry of an unanswered refresh${receipt.first_attempt ? `: ${why(receipt.first_attempt)}` : ''}`);
    }
    details.push(`At: ${new Date(receipt.at_ms).toISOString()}`);
    // Other sessions' usage since the previous reading is included: an upper bound.
    for (const [kind, points] of Object.entries(receipt.usage_cost ?? {})) {
      const figure = points === null ? null : percent(points);
      const unit = figure === '1' ? 'point' : 'points';
      if (figure !== null) details.push(`Refresh moved ${LABELS[kind]} usage by at most ${figure} ${unit}`);
    }
  }
  return { label, note, tone, details, usage: meter.badge };
}

export function render($, e, view, prior) {
  const { Box, Text } = $.ui.resolve(e);
  if (e.component === 'Pane') {
    return Box({ flexDirection: 'column', children: [
      Text({ weight: 'semibold', children: ['neosian cache'] }),
      ...view.details.map(line => Text({ wrap: 'wrap', children: [line] })),
      Text({ dimColor: true, children: ['Esc to close'] }),
    ] });
  }
  const width = e.props.bodyColumns ?? e.viewport?.columns ?? 80;
  const tail = [view.note, view.usage].filter(Boolean);
  const text = [`neosian · ${view.label}`, ...tail].join(' · ');
  const rest = tail.join(' · ');
  const lines = text.length <= width || !tail.length
    ? [text] : [`neosian · ${view.label}`, ...(rest.length <= width ? [rest] : tail)];
  return Box({ flexDirection: 'column', children: [
    ...(prior ? [prior] : []),
    ...lines.map(line => Text({ color: view.tone, wrap: 'wrap', children: [line] })),
  ] });
}
