// Pure usage-limit arithmetic for the wrap-up note and the warm wait: no `$`,
// clock or I/O. Figures are the host's own estimates from its last response;
// nothing here meters a model or knows a price. Note prose lives in assets/usage.md.
import { HOUR, INTERVAL } from './evidence.js';

const MINUTE = 60000;
const RING = 32;
const AGENTS = 64;
const KINDS = ['five_hour', 'seven_day'];
const BLOCKS = ['notice', 'wrap', 'agent', 'pace', 'reset', 'noreset', 'wait', 'handoff'];
const SCALARS = ['window', 'percent', 'reset_in', 'reset_at', 'minutes_left'];
const NOTES = ['notice', 'wrap', 'agent'];
const RANK = { notice: 1, pace: 2, wrap: 2 };
const WRAPS = ['pace', 'wrap'];
export const LABELS = { five_hour: '5-hour', seven_day: 'weekly' };

export function windows(rateLimits, now) {
  const found = new Map();
  for (const limit of Array.isArray(rateLimits) ? rateLimits : []) {
    const p = limit?.percentUsed;
    if (!KINDS.includes(limit?.kind) || found.has(limit.kind) || typeof p !== 'number' ||
        !Number.isFinite(p) || p < 0 || p > 100) continue;
    const at = typeof limit.resetsAt === 'string' ? Date.parse(limit.resetsAt) : NaN;
    found.set(limit.kind, { kind: limit.kind, p, resetsAt: Number.isFinite(at) && at > now ? at : null });
  }
  return [...found.values()];
}

// Record one fresh reading. A window keeps its current figure `p`, the highest
// seen in it, apart from the ring of samples the rate is measured against.
export function observe(state, list, now) {
  for (const kind of KINDS) {
    if (state[kind] && !list.some(w => w.kind === kind)) delete state[kind]; // A window that vanished.
  }
  for (const { kind, p, resetsAt } of list) {
    let w = state[kind];
    const moved = w && w.resetsAt !== null && resetsAt !== null && Math.abs(resetsAt - w.resetsAt) > 120000;
    // Without a reset time on both sides, only a large fall tells a new window.
    const fell = w && (w.resetsAt === null || resetsAt === null) && w.p - p >= 20;
    if (!w || moved || fell) {
      w = state[kind] = { p, resetsAt: null, samples: [], delivered: [], agents: { pace: [], wrap: [] } };
    }
    if (resetsAt !== null) w.resetsAt = resetsAt;
    // Responses can arrive out of order and step back a point inside one
    // window: keep the highest seen, so levels and the rate never move back.
    w.p = Math.max(w.p, p);
    const tail = w.samples.at(-1);
    if (tail && now < tail.t) w.samples = []; // The clock went back: start the ring over.
    const age = w.samples.length ? now - w.samples.at(-1).t : Infinity;
    // Spaced at least 30 s apart, 32 samples always span the 15-minute lookback.
    if (age >= MINUTE || (age >= 30000 && w.samples.at(-1).p !== w.p)) {
      w.samples.push({ t: now, p: w.p });
      if (w.samples.length > RING) w.samples.shift();
    }
  }
  return state;
}

export function rate(samples, now, current = samples.at(-1)?.p) {
  let best = null;
  for (const s of samples) {
    const age = now - s.t;
    if (age < 90000 || age > 15 * MINUTE) continue;
    const slope = (current - s.p) / (age / MINUTE);
    if (best === null || slope > best) best = slope;
  }
  return best !== null && best > 0 ? best : null;
}

export function minutesLeft(p, perMinute, reserve = 0) {
  return perMinute === null ? null : Math.max(0, (100 - reserve - p) / perMinute);
}

// The level each current window has reached, delivered or not: `wrap` at 95%
// less its reserve (or by pace from 85%), `pace` for a pace wrap below 85%,
// then `notice`. `reserve` is points, or points per window (`reserves`): it
// decides when a level is due, while `left` stays the minutes to the real
// limit, for the note's text. A wrap delivered in this window stays a wrap,
// whatever the floor is now. A window past its known reset is left out.
export function assess(state, now, reserve = 0) {
  return KINDS.filter(kind => state[kind] && !(state[kind].resetsAt !== null && now >= state[kind].resetsAt))
    .map(kind => {
      const { p, resetsAt, samples } = state[kind];
      const r = typeof reserve === 'number' ? reserve : reserve?.[kind] ?? 0;
      const perMinute = rate(samples, now, p);
      const due = minutesLeft(p, perMinute, r);
      // The pace matters only if the limit would come before the window resets.
      const pace = due !== null && due <= 10 && p >= 50 && (resetsAt === null || resetsAt > now + due * MINUTE);
      const wrapped = state[kind].delivered?.includes('wrap');
      const level = wrapped || p >= 95 - r || (pace && p >= 85) ? 'wrap' : pace ? 'pace' : p >= 85 ? 'notice' : null;
      return { kind, p, resetsAt, left: minutesLeft(p, perMinute), level };
    });
}

// A known reset that is still ahead and inside the wait horizon.
function near(w, now, hours) {
  return w.resetsAt !== null && w.resetsAt > now && w.resetsAt - now <= hours * HOUR;
}

// One refresh's cost in points: the smallest of the last three observed,
// clamped to 0.1..2, or 0.5 before any. Each observation is an upper bound.
export function estimate(list) {
  return list?.length ? Math.min(2, Math.max(0.1, Math.min(...list))) : 0.5;
}

// Points kept back per window for the refreshes until a near reset, so the
// wrap floor is 95 less it. A far or unknown reset keeps none.
export function reserves(state, costs, now, hours = 6) {
  return Object.fromEntries(KINDS.filter(kind => state[kind]).map(kind => [kind, near(state[kind], now, hours)
    ? Math.min(5, Math.round(Math.ceil((state[kind].resetsAt - now) / INTERVAL) * estimate(costs?.[kind]) * 10) / 10)
    : 0]));
}

// Whether keep-warm holds back the refresh it is about to send, from the last
// recorded figures (an idle session learns nothing newer): 'limit' at 99.5,
// 'exhausted' at the wrap floor with a far or unknown reset, else null. A
// window past its known reset never holds one back.
export function gate(state, costs, now, hours = 6) {
  const reserve = reserves(state, costs, now, hours);
  const current = assess(state, now, reserve);
  if (current.some(w => w.p >= 99.5)) return 'limit';
  return current.some(w => w.p >= 95 - reserve[w.kind] && !near(w, now, hours)) ? 'exhausted' : null;
}

// Each window and its figure, taken just before the reading after a refresh.
export function snapshot(state) {
  return Object.fromEntries(KINDS.filter(kind => state[kind]).map(kind => [kind, [state[kind], state[kind].p]]));
}

// What a verified refresh moved each window by since its previous recorded
// reading: never negative, and an upper bound, since the figure includes every
// other session's usage in between. Keeps the last three per window; a window
// that is new since `before` has no previous reading and records nothing.
export function charge(costs, before, state) {
  return Object.fromEntries(KINDS.map(kind => {
    const [w, p] = before[kind] ?? [];
    if (!w || state[kind] !== w) return [kind, null];
    const moved = Math.max(0, Math.round((w.p - p) * 10) / 10);
    costs[kind] = [...(costs[kind] ?? []), moved].slice(-3);
    return [kind, moved];
  }));
}

// The reset a session that wrapped up waits for: the window of the last
// main-thread wrap or pace wrap note, while that window's reset is near.
export function waiting(state, last, now, hours = 6) {
  const w = last && WRAPS.includes(last.level) ? state[last.window] : undefined;
  return w && w.delivered.includes(last.level) && near(w, now, hours)
    ? { kind: last.window, resetsAt: w.resetsAt } : null;
}

// What is due now, the window to describe first; nothing is marked here.
export function peek(state, now, { reserve = 0, agent } = {}) {
  const current = assess(state, now, reserve);
  const due = current.filter(({ kind, level }) => {
    const { delivered, agents } = state[kind];
    if (agent) return WRAPS.includes(level) && !agents.wrap.includes(agent) && !agents[level].includes(agent);
    if (level === 'wrap') return !delivered.includes('wrap');
    if (level === 'pace') return !delivered.includes('pace') && !delivered.includes('wrap');
    // No reassuring notice while another window is at wrap; it stays due.
    return level === 'notice' && !delivered.includes('notice') && !delivered.includes('wrap') &&
      !current.some(w => w.kind !== kind && WRAPS.includes(w.level));
  });
  // With several at wrap, describe the later reset: waiting for the earlier
  // one does not help. An unknown reset counts as latest.
  const later = w => w.resetsAt ?? Infinity;
  return due.sort((a, b) => RANK[b.level] - RANK[a.level] ||
    (RANK[a.level] === 2 ? later(b) - later(a) : 0) || b.p - a.p);
}

// Mark a delivered note: its window, and every other window due at the same
// rank. A wrap covers all three levels; a pace wrap leaves notice and wrap due.
export function commit(state, due, agent) {
  for (const { kind, level } of due.filter(w => RANK[w.level] === RANK[due[0].level])) {
    const w = state[kind];
    const marks = level === 'wrap' ? ['pace', 'notice', 'wrap'] : [level];
    if (!agent) w.delivered.push(...marks);
    else for (const mark of marks.filter(m => m !== 'notice')) {
      if (w.agents[mark].push(agent) > AGENTS) w.agents[mark].shift();
    }
  }
}

// After /clear or a resume the conversation is new: the samples remain account
// evidence, but every level is deliverable again.
export function forget(state) {
  for (const w of Object.values(state)) Object.assign(w, { delivered: [], agents: { pace: [], wrap: [] } });
}

export function horizon(value) {
  return typeof value === 'number' && Number.isFinite(value) ? Math.min(10, Math.max(1, value)) : 6;
}

export function advice(resetsAt, now, hours = 6) {
  return resetsAt !== null && resetsAt - now <= hours * 3600000 ? 'wait' : 'handoff';
}

export function percent(p) {
  return String(Math.round(p * 10) / 10);
}

export function span(ms) {
  const minutes = Math.max(0, Math.ceil(ms / MINUTE));
  const [d, h, m] = [Math.floor(minutes / 1440), Math.floor(minutes % 1440 / 60), minutes % 60];
  if (d) return h ? `${d}d ${h}h` : `${d}d`;
  if (h) return m ? `${h}h ${m}m` : `${h}h`;
  return `${m}m`;
}

export function parse(raw) {
  const text = typeof raw === 'string' ? raw.replace(/\r\n/g, '\n') : '';
  const end = text.indexOf('\n---\n');
  const meta = text.slice(4, end);
  if (!text.startsWith('---\n') || end < 0 || !/^name: neosian-cache-usage$/m.test(meta) ||
      !/^summary: \S/m.test(meta)) return null;
  const blocks = {};
  const parts = text.slice(end + 5).split(/^## +([a-z]+)[ \t]*$/m);
  for (let i = 1; i < parts.length; i += 2) {
    if (!BLOCKS.includes(parts[i]) || blocks[parts[i]] !== undefined) return null;
    blocks[parts[i]] = parts[i + 1].replace(/\s+/g, ' ').trim();
  }
  const valid = BLOCKS.every(name => blocks[name] && [...blocks[name].matchAll(/\{([^}]*)\}/g)]
    .every(([, key]) => SCALARS.includes(key) || (NOTES.includes(name) && ['reset', 'pace', 'advice'].includes(key))));
  return valid ? blocks : null;
}

// Fill one note: `facts` is a window from `peek` plus `now` and its `advice`.
// A pace wrap reads as the wrap block.
export function compose(templates, level, facts, audience = 'main') {
  const known = facts.resetsAt !== null;
  const values = { window: LABELS[facts.kind], percent: percent(facts.p),
    reset_in: known ? span(facts.resetsAt - facts.now) : '',
    reset_at: known ? new Date(facts.resetsAt).toISOString().slice(0, 16).replace('T', ' ') : '',
    minutes_left: facts.left === null ? '' : span(Math.max(1, Math.floor(facts.left)) * MINUTE) };
  const fill = block => block.replace(/\{([a-z_]+)\}/g, (all, key) => values[key] ?? all);
  values.reset = fill(known ? templates.reset : templates.noreset);
  values.pace = facts.left === null ? '' : fill(templates.pace);
  values.advice = level === 'notice' ? '' : fill(templates[facts.advice]);
  const block = audience === 'agent' ? 'agent' : level === 'notice' ? 'notice' : 'wrap';
  return fill(templates[block]).replace(/\s+/g, ' ').trim();
}

// The text of the note `peek` put first.
export function note(templates, top, now, { hours = 6, agent } = {}) {
  return compose(templates, top.level, { ...top, now, advice: advice(top.resetsAt, now, hours) },
    agent ? 'agent' : 'main');
}
