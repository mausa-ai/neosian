// Presentation only. Cache evidence and maintenance decisions stay in register.js.
import { HOUR } from './evidence.js';
import { LABELS, percent, span } from './usage.js';
export const PANE = 'neosian-cache-details';

// Usage figures are the host's estimates; which note is due is decided in usage.js.
function limits({ windows, notes, last }, now) {
  const lines = windows.map(({ kind, p, resetsAt }) => {
    const label = LABELS[kind][0].toUpperCase() + LABELS[kind].slice(1);
    return `${label} usage: ${percent(p)}%${resetsAt === null ? '' : ` · resets in ${span(resetsAt - now)}`}`;
  });
  if (last) lines.push(`Last usage note: ${last.level} (${LABELS[last.window]}) at ${new Date(last.at_ms).toISOString()}`);
  const wraps = w => w.level === 'wrap' || w.level === 'pace'; // A pace wrap is a wrap too.
  const top = windows.filter(w => w.level).sort((a, b) => wraps(b) - wraps(a) || b.p - a.p)[0];
  return { lines: ['', ...lines, `Usage notes: ${notes}`], badge: top ? `usage ${percent(top.p)}%` : '',
    wrap: Boolean(top && wraps(top)) };
}

export function presentation({ enabled, busy, flight, certificate, receipt, until, reason, allowApi, usage }, now) {
  let label = 'cache paused';
  let tone = 'warning';
  let note = 'next message may be cold';
  let detail = reason;
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
    else { note = 'keep-warm on'; tone = 'success'; }
  } else if (reason === 'Waiting for your first message') {
    label = 'keep-warm ready'; tone = 'subtle'; note = '';
  } else if (reason === 'Checking cache observation') {
    label = 'checking cache'; tone = 'subtle'; note = '';
  } else if (reason.includes('expired')) label = 'cache expired';
  else if (reason.includes('unknown')) label = 'cache unknown';
  else if (reason.includes('unavailable')) { label = 'keep-warm unavailable'; note = '/neosian-cache'; }
  const details = [detail, `Keep-warm: ${enabled ? 'on' : 'off'}${until !== null
    ? ` · until ${new Date(until).toISOString()}` : ' · until session closes'}`];
  if (note && note !== 'keep-warm on') details.push(note);
  const meter = limits(usage, now);
  details.push(...meter.lines);
  if (meter.wrap) tone = 'warning';
  if (receipt) {
    details.push('', 'Last refresh', `Cache read: ${receipt.read} tokens`,
      `Cache written: ${receipt.write} tokens`, `Output: ${receipt.output} tokens`,
      `Result: ${receipt.outcome}`, `At: ${new Date(receipt.at_ms).toISOString()}`);
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
