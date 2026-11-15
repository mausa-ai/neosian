// Usage notes: read the host's own rate-limit figures and hand the agent a note
// in the classic hook results. Guidance only: never a block, stop or prompt.
// The arithmetic is in usage.js and the prose in assets/usage.md. The host
// loads `$` only into named functions, so state travels as a plain object.
import { assess, charge, commit, forget, gate, horizon, note, observe, parse, peek, reserves, snapshot,
  waiting, windows } from './usage.js';

// `costs` holds the last observed refresh costs per window (usage.js `charge`).
// `warm` is set by register.js: keep-warm is on and available in this session
// and its last observed cache was one hour and eligible for refresh, so the
// reserve applies. Before a first observation it is false: plain thresholds.
export function usageNotes() {
  return { meter: {}, costs: {}, warm: false, metered: false, sampled: false, fresh: false, templates: null,
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
  Object.assign(n, { meter: {}, costs: {}, metered: supported, sampled: false, fresh: false,
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
    const due = now === null ? [] : peek(n.meter, now,
      { reserve: reserve(n, now), agent });
    if (!due.length) return result;
    const context = Array.isArray(result?.additionalContext) ? result.additionalContext : [];
    const out = { ...result, additionalContext: [...context, note(n.templates, due[0], now, { hours: n.hours, agent })] };
    commit(n.meter, due, agent);
    if (!agent) n.last = { level: due[0].level, window: due[0].kind, at_ms: now };
    return out;
  } catch { return result; }
}

// A verified refresh's cost per window, from a reading taken right after it
// (usage.js `charge`); null where none was possible. Never throws.
export async function measureRefresh($, n) {
  try {
    const before = snapshot(n.meter);
    n.fresh = true;
    if (await sampleUsage($, n) !== null) return charge(n.costs, before, n.meter);
  } catch { /* An unmeasured refresh keeps the earlier estimate. */ }
  return { five_hour: null, seven_day: null };
}

// The reserve lowers the wrap floor only while keep-warm can spend it (`warm`).
function reserve(n, now) {
  return n.warm ? reserves(n.meter, n.costs, now, n.hours) : 0;
}

export function noteSummary(n, now) {
  return { windows: assess(n.meter, now, reserve(n, now)), last: n.last,
    // Waiting is shown only if the next refresh would be sent, not held back.
    waiting: gate(n.meter, n.costs, now, n.hours) ? null : waiting(n.meter, n.last, now, n.hours),
    notes: !n.enabled ? 'off' : n.metered && n.templates && n.sampled ? 'on' : 'unavailable' };
}
