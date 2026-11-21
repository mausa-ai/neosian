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
// One window's reset, as usage.js `take` tells windows apart. Recorded reset
// times fall on ten-minute marks, so in practice this means the same mark.
const SAME = 120000;

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

// A window this session holds with a known reset still ahead.
const current = (w, now) => Boolean(w) && w.resetsAt !== null && w.resetsAt > now;
// A shared window on the same reset as this session's current one.
const same = (w, theirs, now) => current(w, now) && theirs.resetsAt !== null &&
  Math.abs(theirs.resetsAt - w.resetsAt) <= SAME;

// Whether a reading is this account's, without an identity: both 5-hour
// windows are known, current and on the same reset, and any other window both
// hold with a reset still ahead agrees too. The weekly window alone never
// admits one; a session whose own 5-hour window is over takes nothing.
export function sameAccount(state, reading, now) {
  const five = reading.list.find(w => w.kind === 'five_hour');
  return Boolean(five) && five.resetsAt > now && same(state.five_hour, five, now) &&
    reading.list.every(w => w.resetsAt === null || !current(state[w.kind], now) || same(state[w.kind], w, now));
}

// Take a reading of this account into the meter: only a window this session
// already holds, current and on the same reset, from a newer reading that is
// not below the kept figure. So a shared reading never starts a window, never
// lowers a figure, and a window without a reset in it is ignored.
// The later of the two reset times is kept, so taking one never ends a window
// earlier than this session's own reading says.
function adopt(state, reading, now) {
  for (const theirs of reading.list) {
    const w = state[theirs.kind];
    if (same(w, theirs, now) && reading.at > (w.at ?? -Infinity) && theirs.p >= w.p) {
      take(state, theirs.kind, theirs.p, Math.max(theirs.resetsAt, w.resetsAt), reading.at, true);
    }
  }
}

function serialize({ at, list }) {
  return JSON.stringify({ format: 1, read_at_ms: at,
    usage: Object.fromEntries(list.map(w => [w.kind, { percent: w.p, resets_at_ms: w.resetsAt }])) });
}

// Whether a failed stat means the file is not there: ENOENT, or no errno at all
// and `exists` says so. Any other failure: the file may be there.
async function absent($, path, error) {
  if (error?.code === 'ENOENT' || /\bENOENT\b/.test(String(error?.message ?? error))) return true;
  if (typeof error?.code === 'string') return false;
  try { return !(await $.fs.exists(path)); } catch { return false; }
}

// At each tick: take the file's reading into the meter when it is this
// account's, dated after any backward clock step (`n.untrusted`; a reading
// dated after now can only predate one, so its time is never trusted), and
// `idle()` says no refresh is in flight, asked at the moment of taking (a
// refresh's measured cost never includes itself); then write this session's
// own newest reading, once, unless the file holds one as new. A path that is
// not plainly a file, or a stat that fails other than for absence, is neither
// read nor written, and `n.sharing` turns false for the pane to say so.
// `$.fs.write` is not documented as atomic: a torn file fails the checks.
export async function shareUsage($, n, directory, idle) {
  const path = directory + '/' + NAME;
  let stat = null;
  try { stat = await $.fs.stat(path); } catch (error) {
    if (!(await absent($, path, error))) { n.sharing = false; return; }
  }
  n.sharing = true;
  const plain = !stat || (stat.kind === 'file' && !stat.isLink);
  let found = null;
  if (stat && plain && stat.size <= BOUND) {
    try {
      const raw = await $.fs.read(path);
      const any = raw.length <= BOUND ? parseShared(raw, Infinity) : null;
      const now = await $.clock.now();
      if (any && any.at > now) n.untrusted = Math.max(n.untrusted, any.at);
      else found = any;
    } catch { /* Unreadable: nothing to take. */ }
  }
  try {
    const now = await $.clock.now();
    if (found && found.at > n.untrusted && idle() && sameAccount(n.meter, found, now)) adopt(n.meter, found, now);
    const own = n.reading;
    if (plain && own && own !== n.published && !(found && found.at >= own.at)) {
      n.published = own;
      await $.fs.write(path, serialize(own));
    }
  } catch { /* The session keeps its own readings. */ }
}
