// Pure validation shared by the host adapter and keyless tests.
export const HOUR = 3600000;
export const INTERVAL = 55 * 60000;
export const MARGIN = 30000;

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

export function duration(text) {
  const match = /^([1-9][0-9]{0,4})(m|h)$/.exec(text);
  if (!match) return null;
  const ms = Number(match[1]) * (match[2] === 'h' ? HOUR : 60000);
  return ms <= 7 * 24 * HOUR ? ms : null;
}
