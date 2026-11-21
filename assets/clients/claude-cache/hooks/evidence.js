// Pure validation shared by the host adapter and keyless tests.
export const HOUR = 3600000;
export const INTERVAL = 55 * 60000;
export const MARGIN = 30000;
// An unanswered refresh is retried at the first 30-second tick this long after
// it came back: 45 to 75 seconds later.
export const RETRY = 45000;

export function count(value) {
  return Number.isSafeInteger(value) && value >= 0;
}

export function supported(version) {
  const match = /^2\.1\.(\d+)$/.exec(version ?? '');
  return match !== null && Number(match[1]) >= 314;
}

export function usage(value) {
  return value && ['input_tokens', 'output_tokens', 'cache_read_input_tokens',
    'cache_creation_input_tokens'].every(key => count(value[key]));
}

export function observation(raw, sessionId, version, step, now) {
  let data;
  try { data = JSON.parse(raw); } catch { return null; }
  if (data?.format !== 1 || data.session_id !== sessionId || data.version !== version ||
      !count(data.observed_at_ms) || data.observed_at_ms < step.started ||
      data.observed_at_ms > now || data.model !== step.model ||
      !count(data.main_requests) || data.main_requests <= step.previousRequests ||
      data.last_main_cache_read_tokens !== step.read ||
      data.last_main_cache_write_tokens !== step.write ||
      !count(data.expires_at) || !['1h', '5m'].includes(data.ttl) ||
      typeof data.subscription !== 'boolean') return null;
  const ttl = data.ttl === '1h' ? HOUR : 300000;
  // The provider counts TTL from request START, not response completion. A
  // side request can advance the host's estimate; never extend from that alone.
  const deadline = Math.min(data.expires_at * 1000, step.started + ttl);
  if (deadline > step.started + ttl || deadline < step.started) return null;
  return { ...data, deadline, ttl };
}

export function reused(result, expected) {
  if (!result?.isAnswered || typeof result.text !== 'string' || !usage(result.usage)) return false;
  const u = result.usage;
  // A high-effort model can account for reasoning despite a one-word reply.
  // Allow the measured small helper tail, while a large context may lose at
  // most 2,048 tokens. These are receipt checks, not request-time API caps.
  return result.text.trim() === 'WARM' && u.output_tokens <= 256 &&
    u.input_tokens <= 1024 && u.cache_creation_input_tokens <= 2048 &&
    u.cache_read_input_tokens > 0 && u.cache_read_input_tokens >= expected * 0.90 &&
    u.cache_read_input_tokens >= expected - 2048;
}

// The host refused the request for a rate limit: the fork's `api-error` arm,
// with Claude Code's `rate_limit` kind or HTTP status 429.
export function limited(result) {
  return result?.isAnswered === false && result.reason === 'api-error' &&
    (result.error === 'rate_limit' || result.status === 429);
}

// A host identifier (a fork's reason or error kind), never free text.
function atom(value) {
  return typeof value === 'string' && /^[A-Za-z0-9_.-]{1,40}$/.test(value) ? value : null;
}

// Why the host left a refresh unanswered: its reason, error kind and HTTP
// status where it gives them, each null otherwise; null when it answered.
export function unanswered(result) {
  if (result?.isAnswered === true) return null;
  const status = result?.status;
  return { reason: atom(result?.reason), error: atom(result?.error),
    status: Number.isSafeInteger(status) && status >= 100 && status <= 599 ? status : null };
}

// Failures sending again cannot fix (U3 review): kinds the host documents as
// not clearing on their own, refused credentials and invalid requests by
// status, nothing to fork, and an abort (the person interrupted it).
const FINAL = ['authentication_failed', 'invalid_request', 'billing_error', 'model_not_found'];
const REFUSED = [400, 401, 403, 413];

// Worth one retry: not answered, not a rate-limit refusal, not a failure that
// stays (above), and no tokens counted. That leaves an empty result, a timeout
// or dropped connection, an overload and a server error. A request that counted
// tokens was served, and sending it again could spend them against a cache
// that is gone.
export function retriable(result) {
  if (result?.isAnswered === true || limited(result)) return false;
  if (['nothing-to-fork', 'aborted'].includes(result?.reason)) return false;
  if (result?.reason === 'api-error' && (FINAL.includes(result.error) || REFUSED.includes(result.status))) {
    return false;
  }
  const u = result?.usage;
  return u === undefined || u === null || (usage(u) && u.input_tokens + u.output_tokens +
    u.cache_read_input_tokens + u.cache_creation_input_tokens === 0);
}

export function duration(text) {
  const match = /^([1-9][0-9]{0,4})(m|h)$/.exec(text);
  if (!match) return null;
  const ms = Number(match[1]) * (match[2] === 'h' ? HOUR : 60000);
  return ms <= 7 * 24 * HOUR ? ms : null;
}
