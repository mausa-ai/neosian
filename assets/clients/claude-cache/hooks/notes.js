// Usage notes: read the host's own rate-limit figures and hand the agent a note
// in the classic hook results. Guidance only: never a block, stop or prompt.
// The arithmetic is in usage.js and the prose in assets/usage.md. The host
// loads `$` only into named functions, so state travels as a plain object.
import { assess, commit, forget, horizon, note, observe, parse, peek, windows } from './usage.js';

export function usageNotes() {
  return { meter: {}, metered: false, sampled: false, fresh: false, templates: null,
    enabled: true, preferred: true, hours: 6, last: null };
}

export function configureNotes(n, options) {
  n.preferred = options.usageNotes !== false;
  n.hours = horizon(options.waitHorizonHours);
}

export function setNotes(n, key, value) {
  if (key === 'neosian-cache.usageNotes') n.preferred = n.enabled = value === true;
  else if (key === 'neosian-cache.waitHorizonHours') n.hours = horizon(value);
}

// Notes need a supported session, but neither the bridge nor a terminal.
export async function startNotes($, n, supported) {
  Object.assign(n, { meter: {}, metered: supported, sampled: false, fresh: false,
    templates: null, enabled: n.preferred, last: null });
  if (!supported) return;
  try { n.templates = parse(await $.fs.read($.plugin.root + '/assets/usage.md')); } catch { n.templates = null; }
  await sampleUsage($, n);
}

// /clear and resume end the conversation but not the process, and no
// session.start follows: the next conversation may hear every level again.
export function endNotes(n) {
  forget(n.meter);
  n.last = null;
}

export async function sampleUsage($, n) {
  if (!n.metered) return null;
  const recent = n.fresh;
  n.fresh = false;
  try {
    const now = await $.clock.now();
    const { rateLimits } = await $.session.usage();
    // An idle session repeats its last figure. Only a reading after one of
    // its own responses is evidence, so idle time adds no flat samples.
    if (recent) observe(n.meter, windows(rateLimits, now), now);
    n.sampled = true;
    return now;
  } catch {
    n.fresh = n.fresh || recent;
    n.sampled = false; // A host without usage figures keeps keep-warm as before.
    return null;
  }
}

// Returns `result` itself unless a note is due; any failure returns it too.
// A prompt reaches the main thread only; a tool call may be a subagent's.
// Levels count as delivered only once the result carrying the note is built,
// and a result another hook blocked or stopped carries none.
export async function deliverNote($, n, result, e, tool) {
  try {
    const agent = e?.agent_id;
    if (!n.enabled || !n.templates || (agent && !tool) || result?.block || result?.preventContinuation) {
      return result;
    }
    const now = await sampleUsage($, n);
    const due = now === null ? [] : peek(n.meter, now, { reserve: 0, agent });
    if (!due.length) return result;
    const context = Array.isArray(result?.additionalContext) ? result.additionalContext : [];
    const out = { ...result, additionalContext: [...context, note(n.templates, due[0], now, { hours: n.hours, agent })] };
    commit(n.meter, due, agent);
    if (!agent) n.last = { level: due[0].level, window: due[0].kind, at_ms: now };
    return out;
  } catch { return result; }
}

export function noteSummary(n, now) {
  return { windows: assess(n.meter, now), last: n.last,
    notes: !n.enabled ? 'off' : n.metered && n.templates && n.sampled ? 'on' : 'unavailable' };
}
