import { HOUR, INTERVAL, MARGIN, duration, observation, reused, supported, usage } from './evidence.js';
import { PANE, presentation, render } from './display.js';

// All model calls, lifecycle events, timers and UI belong to Claude Code.
// No conversation content, credentials or Neosian memory is read or persisted.
let enabled = true;
let allowApi = false;
let defaultEnabled = true;
let defaultAllowApi = false;
let available = false;
let busy = false;
let closed = false;
let flight = false;
let epoch = 0;
let sessionId = '';
let version = '';
let directory = '';
let prompt = '';
let pending;
let certificate;
let receipt;
let until = null;
let requests = 0;
let lastClock = 0;
let timer;
let reason = 'Waiting for your first message';
let lastDisplay = '';
let lastPersisted = '';
let view;

function invalidate(why) {
  epoch += 1;
  pending = undefined;
  certificate = undefined;
  reason = why;
}

async function show($, now) {
  view = presentation({ enabled, busy, flight, certificate, receipt, until, reason, allowApi }, now);
  const text = JSON.stringify(view);
  if (text !== lastDisplay) {
    $.ui.invalidate('ui.render');
    lastDisplay = text;
  }
  // One bounded diagnostics record, containing counts and UTC epoch times only.
  const record = JSON.stringify({ format: 1, session_id: sessionId, version, observed_at_ms: now,
    enabled, state: reason, expires_at_ms: certificate?.deadline ?? null,
    until_ms: until, last_refresh: receipt ?? null });
  if (directory && record !== lastPersisted) {
    try {
      await $.fs.write(directory + '/' + sessionId + '.refresh.json', record);
      lastPersisted = record;
    } catch { /* The in-session badge remains available without disk diagnostics. */ }
  }
}

async function inspect($, now) {
  if (!pending || !directory) return;
  const mine = epoch;
  const step = pending;
  try {
    const path = directory + '/' + sessionId + '.json';
    const stat = await $.fs.stat(path);
    if (!stat || stat.kind !== 'file' || stat.isLink || stat.size > 8192) return;
    const raw = await $.fs.read(path);
    if (mine !== epoch || pending !== step || raw.length > 8192) return;
    const data = observation(raw, sessionId, version, step, now);
    if (!data) return;
    requests = data.main_requests;
    pending = undefined;
    if (data.state !== 'warm' || step.read + step.write === 0) {
      invalidate('Cache status unknown · automatic refresh paused');
      return;
    }
    certificate = { ...data, started: step.started, expected: step.read + step.write,
      model: step.requestModel, due: step.started + INTERVAL };
    reason = 'Cache observed';
  } catch { /* An absent or partial observation never authorizes a model call. */ }
}

async function tick($, allowRefresh = true) {
  if (closed) return;
  const owner = sessionId;
  const now = await $.clock.now();
  if (closed) return;
  if (!available) { await show($, now); return; }
  if (now < lastClock) invalidate('Clock changed · waiting for your next message');
  lastClock = now;
  if (until !== null && now >= until) enabled = false;
  if (!busy && pending) await inspect($, now);
  if (!busy && pending && now - pending.started >= 90000) {
    invalidate('Cache status unknown · your next message may rebuild context');
  }
  if (certificate && now >= certificate.deadline - MARGIN) {
    invalidate('Cache expired or near expiry · your next message may rebuild context');
  }
  if (allowRefresh && enabled && !busy && !flight && certificate?.ttl === HOUR &&
      (certificate.subscription || allowApi) && now >= certificate.due) {
    await refresh($);
  }
  if (!closed && sessionId === owner) await show($, await $.clock.now());
}

async function refresh($) {
  const mine = epoch;
  const owner = sessionId;
  const saved = certificate;
  flight = true;
  try {
    const model = await $.session.model();
    const started = await $.clock.now();
    if (mine !== epoch || !enabled || busy || closed || certificate !== saved) return;
    if (model !== saved.model) {
      invalidate('Model changed · waiting for your next message');
      return;
    }
    if (started >= saved.deadline - MARGIN || started < saved.started ||
        (until !== null && started >= until)) return;
    reason = 'Refreshing warm cache';
    await show($, started);
    // Recheck after the asynchronous diagnostics write. A user turn wins.
    const sending = await $.clock.now();
    if (mine !== epoch || busy || closed || !enabled || sending >= saved.deadline - MARGIN) return;
    const result = await $.model.fork({ prompt });
    const finished = await $.clock.now();
    const valid = reused(result, saved.expected);
    const u = usage(result?.usage) ? result.usage : null;
    if (owner !== sessionId) return;
    receipt = { at_ms: sending, read: u?.cache_read_input_tokens ?? null,
      write: u?.cache_creation_input_tokens ?? null, output: u?.output_tokens ?? null,
      outcome: mine !== epoch ? 'superseded' : valid ? 'reused' : 'stopped' };
    if (mine !== epoch || closed) return;
    if (!valid || finished >= sending + HOUR - MARGIN) {
      invalidate('Refresh unverified · automatic refresh paused until your next message');
      return;
    }
    // Renew only the existing one-hour prefix that was actually read. The
    // helper's new tail can have a five-minute TTL and is never our anchor.
    certificate = { ...saved, started: sending, deadline: sending + HOUR,
      due: sending + INTERVAL, expected: u.cache_read_input_tokens };
    reason = 'Cache refresh verified';
  } catch {
    if (mine === epoch) invalidate('Refresh failed · automatic refresh paused until your next message');
  } finally {
    flight = false;
  }
}

export function register(on, options) {
  defaultEnabled = options.enabled !== false;
  defaultAllowApi = options.allowApi === true;
  on('ui.render', { component: ['AbovePrompt', 'Pane'] }, async ($, e, next) => {
    if (!view || closed || (e.component === 'Pane' && e.requestId !== PANE) ||
        (e.component === 'AbovePrompt' && (e.props.hasSurvey || e.props.view?.agentId))) return next(e);
    const prior = e.component === 'AbovePrompt' ? await next(e) : null;
    return render($, e, view, prior);
  });
  on('session.start', async ($, e, next) => {
    try {
      closed = false;
      busy = false;
      available = false;
      enabled = defaultEnabled;
      allowApi = defaultAllowApi;
      until = null;
      requests = 0;
      receipt = undefined;
      directory = '';
      lastDisplay = '';
      lastPersisted = '';
      view = undefined;
      invalidate('Waiting for your first message');
      sessionId = await $.session.id();
      version = (await $.session.version()).version;
      lastClock = await $.clock.now();
      if (!supported(version) || !/^[A-Za-z0-9_-]{1,128}$/.test(sessionId)) {
        reason = 'Keep-warm unavailable on this Claude Code version';
      } else if (!e.isInteractive) {
        reason = 'Keep-warm runs in interactive sessions';
      } else {
        const config = JSON.parse(await $.fs.read($.plugin.root + '/bridge.json'));
        directory = typeof config.observations === 'string' ? config.observations : '';
        const raw = (await $.fs.read($.plugin.root + '/assets/refresh.md')).replace(/\r\n/g, '\n');
        const body = raw.indexOf('\n---\n');
        if (!raw.startsWith('---\n') || body < 0) throw new Error('prompt metadata');
        prompt = raw.slice(body + 5).trim();
        if (directory && prompt) {
          available = true;
          if (timer) timer.cancel();
          timer = $.clock.every(30000, async () => {
            try { await tick($); }
            catch { invalidate('Cache status unknown · automatic refresh paused'); }
          });
        } else reason = 'Cache observation unavailable · run neosian setup';
      }
      await $.command.register({ name: 'neosian-cache', immediate: true,
        description: 'Cache status; on, off, or a duration such as 2h', argumentHint: '[on|off|30m|2h]' });
      await show($, lastClock);
    } catch {
      invalidate('Keep-warm unavailable · run neosian setup --check');
      await show($, lastClock);
    }
    return next(e);
  });

  on('command.run', { command: 'neosian-cache' }, async ($, e) => {
    const arg = e.args.trim().toLowerCase();
    const span = duration(arg);
    if (arg === 'off') { enabled = false; until = null; epoch += 1; }
    else if (arg === 'on' || span !== null) {
      enabled = true;
      allowApi = true; // Explicit session control also opts usage-billed users in.
      until = span === null ? null : await $.clock.now() + span;
    } else if (arg && arg !== 'status') {
      $.ui.toast('Use /neosian-cache on, off, or a duration up to 7d (for example 2h).');
    }
    await tick($, arg === 'on' || span !== null);
    await $.ui.open({ id: PANE, title: 'neosian cache', focus: true, closeOnEscape: true, rows: 12 });
    return {};
  });

  on('turn.start', async ($, e, next) => {
    if (available && !e.agentId) {
      busy = true;
      invalidate('Conversation active');
    }
    return next(e);
  });

  on('turn.step', async function* ($, e, next) {
    const mine = epoch;
    const started = !available || e.agentId ? null : await $.clock.now();
    const result = yield* next(e);
    if (available && !e.agentId && mine === epoch && usage(result.usage)) {
      pending = { started, read: result.usage.cache_read_input_tokens,
        write: result.usage.cache_creation_input_tokens, model: result.usage.model,
        requestModel: e.model, previousRequests: requests };
    }
    return result;
  });

  on('turn.complete', async ($, e, next) => {
    if (available && !e.agentId) {
      busy = false;
      if (e.reason !== 'answer') invalidate('Turn unfinished · waiting for your next message');
      else if (pending) reason = 'Checking cache observation';
      else invalidate('Cache status unknown · your next message may rebuild context');
      // Completing a user response may observe/rearm, but never waits for a
      // maintenance model call. Only the idle timer or explicit on may send.
      try { await tick($, false); } catch { invalidate('Cache status unknown · automatic refresh paused'); }
    }
    return next(e);
  });

  on('session.end', async ($, e, next) => {
    closed = true;
    if (timer) timer.cancel();
    timer = undefined;
    invalidate('Session closed');
    view = undefined;
    $.ui.invalidate('ui.render');
    return next(e);
  });

  on('session.compact', async ($, e, next) => {
    if (available && !e.agentId && e.trigger !== 'precompute') invalidate('Context changed · waiting for your next message');
    return next(e);
  });

  on('classic.PostModelSwitch', async ($, e, next) => {
    if (available) invalidate('Model changed · waiting for your next message');
    return next(e);
  });

  on('command.run', { command: ['rewind', 'resume', 'mcp', 'plugin', 'plugins', 'fast', 'effort'] }, async ($, e, next) => {
    if (available) invalidate('Session configuration changed · waiting for your next message');
    return next(e);
  });

  on('config.set', async ($, e, next) => {
    const result = await next(e);
    if (!result.deny) {
      if (e.key === 'neosian-cache.enabled') {
        defaultEnabled = enabled = result.value === true;
        until = null;
        epoch += 1;
      } else if (e.key === 'neosian-cache.allowApi') defaultAllowApi = allowApi = result.value === true;
      else if (available && ['model', 'effortLevel', 'fastMode', 'promptCacheTtl', 'subagentPromptCacheTtl'].includes(e.key)) {
        invalidate('Session configuration changed · waiting for your next message');
      }
    }
    return result;
  });
}
