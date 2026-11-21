// One usage reading shared by the sessions of an observations directory, so an
// idle session's figure stays current without a request (DESIGN §40.52). The
// file holds the reading's time and, per window, its percent and reset time: no
// session id, account identity or content. Nothing here sends, schedules or
// brings forward a request.
import { take } from './usage.js';

// No session id contains a dot, so no session's file (`<id>.json` or
// `<id>.refresh.json`) can have this name.
const NAME = 'usage.shared.json';
const BOUND = 1024;
const KINDS = ['five_hour', 'seven_day'];
const SAME = 120000; // One window's reset, as usage.js `take` tells windows apart.

const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const exact = (value, keys) => object(value) && Object.keys(value).length === keys.length &&
  keys.every(key => Object.hasOwn(value, key));

// The file's reading, or null for anything but its exact format: text that is
// not JSON (a half-written file), a member missing or added, a value out of
// range, no window, or a reading dated after `now`.
export function parseShared(raw, now) {
  let data;
  try { data = JSON.parse(raw); } catch { return null; }
  if (!exact(data, ['format', 'read_at_ms', 'usage']) || data.format !== 1 || !object(data.usage)) return null;
  const at = data.read_at_ms;
  if (!Number.isSafeInteger(at) || at < 0 || at > now) return null;
  const list = [];
  for (const [kind, w] of Object.entries(data.usage)) {
    if (!KINDS.includes(kind) || !exact(w, ['percent', 'resets_at_ms'])) return null;
    const { percent: p, resets_at_ms: resetsAt } = w;
    if (typeof p !== 'number' || !Number.isFinite(p) || p < 0 || p > 100 ||
        !(resetsAt === null || (Number.isSafeInteger(resetsAt) && resetsAt > at))) return null;
    list.push({ kind, p, resetsAt });
  }
  return list.length ? { at, list } : null;
}

// Whether a reading is this account's, without an identity: at least one window
// is comparable (the reading gives it a reset time and this session holds it
// with a known reset still ahead) and every comparable window resets within two
// minutes of this session's. A window of this session that is over is not compared.
export function sameAccount(state, reading, now) {
  let compared = 0;
  for (const { kind, resetsAt } of reading.list) {
    const own = state[kind];
    if (!own || own.resetsAt === null || own.resetsAt <= now || resetsAt === null) continue;
    if (Math.abs(resetsAt - own.resetsAt) > SAME) return false;
    compared += 1;
  }
  return compared > 0;
}

function serialize({ at, list }) {
  return JSON.stringify({ format: 1, read_at_ms: at,
    usage: Object.fromEntries(list.map(w => [w.kind, { percent: w.p, resets_at_ms: w.resetsAt }])) });
}

// At each tick: take the file's reading into the meter for each window this
// session has not read since, when it is this account's and no refresh is in
// flight (`ingest`: a refresh's measured cost never includes itself); then
// write this session's own newest reading, once, unless the file holds one as
// new. A path that is not plainly a file is neither read nor written. `$.fs.write`
// is not documented as atomic: a torn file fails the checks and is ignored.
export async function shareUsage($, n, directory, ingest) {
  const path = directory + '/' + NAME;
  let found = null;
  let plain = true;
  try {
    const stat = await $.fs.stat(path);
    plain = stat.kind === 'file' && !stat.isLink;
    if (plain && stat.size <= BOUND) {
      const raw = await $.fs.read(path);
      if (raw.length <= BOUND) found = parseShared(raw, await $.clock.now());
    }
  } catch { /* Absent or unreadable: nothing to take. */ }
  try {
    if (found && ingest && sameAccount(n.meter, found, await $.clock.now())) {
      for (const { kind, p, resetsAt } of found.list) {
        if (!n.meter[kind] || found.at > n.meter[kind].at) take(n.meter, kind, p, resetsAt, found.at, true);
      }
    }
    const own = n.reading;
    if (plain && own && own !== n.published && !(found && found.at >= own.at)) {
      n.published = own;
      await $.fs.write(path, serialize(own));
    }
  } catch { /* The session keeps its own readings. */ }
}
