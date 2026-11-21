// Execute the shipped mod with a deterministic public-host double, without a
// Claude installation, credentials, real timers, or access to a conversation.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';

const moduleUrl = new URL('../../assets/clients/claude-cache/hooks/register.js', import.meta.url);
const prompt = await readFile(new URL('../../assets/clients/claude-cache/assets/refresh.md', import.meta.url), 'utf8');
const notesAsset = await readFile(new URL('../../assets/clients/claude-cache/assets/usage.md', import.meta.url), 'utf8');
const MIN = 60000;
const HOUR = 60 * MIN;
const START = 100000000;
const API_USAGE = { input_tokens: 15, output_tokens: 5, cache_read_input_tokens: 2000,
  cache_creation_input_tokens: 17000, model: 'claude-test' };
const WARM = { isAnswered: true, text: 'WARM', usage: { input_tokens: 17,
  output_tokens: 5, cache_read_input_tokens: 19000, cache_creation_input_tokens: 500 } };
let serial = 0;

async function harness(options = {}, extra = {}) {
  const handlers = [];
  const files = new Map([['/plugin/bridge.json', extra.bridge ?? '{"observations":"/observations"}'],
    ['/plugin/assets/refresh.md', extra.crlf ? prompt.replaceAll('\n', '\r\n') : prompt]]);
  if (extra.notes !== null) files.set('/plugin/assets/usage.md', extra.notes ?? notesAsset);
  let now = START;
  let limits = extra.limits;
  let mainRequests = 0;
  let timers = [];
  let model = 'claude-test';
  let id = extra.id ?? 'session-1';
  const calls = [];
  const registered = [];
  const statuses = [];
  const panes = [];
  const writes = [];
  const on = (event, matcher, handler) => {
    handlers.push({ event, matcher: handler ? matcher : {}, handler: handler ?? matcher });
  };
  const $ = {
    plugin: { root: '/plugin' },
    clock: { now: async () => now, every: (period, fn) => {
      const entry = { next: now + period, period, fn, active: true };
      timers.push(entry);
      return { cancel: () => { entry.active = false; } };
    } },
    session: { id: async () => id, model: async () => model,
      version: async () => ({ version: extra.version ?? '2.1.314' }),
      // Absent unless a case supplies figures: older hosts have no usage().
      ...(limits !== undefined || extra.usageError ? { usage: async () => {
        if (extra.usageError) throw new Error('usage unavailable');
        const rateLimits = typeof limits === 'function' ? limits() : structuredClone(limits);
        return { startedAt: START, context: { window: 200000 }, rateLimits };
      } } : {}) },
    fs: { read: async path => {
      if (!files.has(path)) throw new Error('missing file');
      return files.get(path);
    }, stat: async path => ({ size: (files.get(path) ?? '').length, kind: 'file', isLink: false }),
    write: async (path, body) => { writes.push({ path, body }); files.set(path, body); } },
    command: { register: async spec => { registered.push(spec); return { command: spec.name }; } },
    ui: { status: text => { statuses.push(text); }, toast: () => {}, invalidate: () => {},
      open: async pane => { panes.push(pane); },
      resolve: () => Object.fromEntries(['Box', 'Text'].map(type => [type,
        ({ children, ...props }) => ({ type, props, children })])) },
    model: { fork: async request => { calls.push({ at: now, ...request });
      return extra.fork ? extra.fork(calls.length) : structuredClone(WARM); } },
  };
  const { register } = await import(moduleUrl.href + '?case=' + serial++);
  register(on, options);
  const matches = (matcher, e) => Object.entries(matcher).every(([key, value]) =>
    Array.isArray(value) ? value.includes(e[key]) : value === e[key]);
  async function emit(event, e = {}, result = {}) {
    for (const h of handlers.filter(h => h.event === event && matches(h.matcher, e))) {
      if (event === 'turn.step') {
        const stream = h.handler($, e, async function* () {
          now += extra.stepMs ?? 0;
          return result;
        });
        let chunk = await stream.next();
        while (!chunk.done) chunk = await stream.next();
        result = chunk.value;
      } else result = await h.handler($, e, async () => result);
    }
    return result;
  }
  async function advance(ms, jump = false) {
    const end = now + ms;
    if (jump) now = end;
    while (true) {
      const due = timers.filter(t => t.active && t.next <= end).sort((a, b) => a.next - b.next)[0];
      if (!due) break;
      if (!jump) now = due.next;
      due.next = now + due.period;
      await due.fn();
    }
    now = end;
  }
  async function finish({ agentId, ttl = '1h', subscription = true, observation = true,
    malformed = false, reason = 'answer', data = {}, u = API_USAGE } = {}) {
    const stamp = now;
    const result = { turnId: 'turn', index: 0, answer: 'SENSITIVE_ANSWER',
      toolUses: [], stopReason: 'end_turn', usage: u };
    assert.equal(await emit('turn.step', { turnId: 'turn', index: 0, model, agentId }, result), result);
    if (!agentId && observation) {
      mainRequests += 1;
      files.set('/observations/' + id + '.json', malformed ? 'not JSON' : JSON.stringify({
        format: 1, session_id: id, version: '2.1.314', observed_at_ms: now,
        model: u?.model, main_requests: mainRequests, state: 'warm', ttl, subscription,
        expires_at: Math.floor((stamp + (ttl === '1h' ? 60 : 5) * MIN) / 1000),
        last_main_cache_read_tokens: u?.cache_read_input_tokens,
        last_main_cache_write_tokens: u?.cache_creation_input_tokens, ...data,
      }));
    }
    await emit('turn.complete', { turnId: 'turn', reason, agentId });
  }
  async function turn(args = {}) {
    await emit('turn.start', { turnId: 'turn', text: 'SENSITIVE_PROMPT', agentId: args.agentId });
    await finish(args);
  }
  const command = args => emit('command.run', { command: 'neosian-cache', args });
  const draw = (component = 'AbovePrompt', props = {}, prior = null, requestId = 'neosian-cache-details') =>
    emit('ui.render', { component, props, requestId, surface: 'terminal' }, prior);
  const badge = async () => textOf(await draw());
  const details = async () => textOf(await draw('Pane'));
  const tool = (agent, prior = {}) => emit('classic.PostToolUse', { hook_event_name: 'PostToolUse',
    tool_name: 'Bash', tool_input: {}, tool_response: 'SENSITIVE_OUTPUT', tool_use_id: 'use',
    ...(agent ? { agent_id: agent } : {}) }, prior);
  // A response of this session (a subagent's by default): fresh usage evidence.
  const respond = async (list, agentId = 'helper') => {
    if (list) limits = list;
    await emit('turn.step', { turnId: 'side', index: 0, model, agentId }, { turnId: 'side', index: 0,
      answer: 'SENSITIVE_ANSWER', toolUses: [], stopReason: 'end_turn', usage: API_USAGE });
  };
  const submit = (prior = {}) => emit('classic.UserPromptSubmit',
    { hook_event_name: 'UserPromptSubmit', prompt: 'SENSITIVE_PROMPT' }, prior);
  const begin = (source, sessionId = id, prior = {}) => emit('classic.SessionStart',
    { hook_event_name: 'SessionStart', session_id: sessionId, transcript_path: '', cwd: '/work', source }, prior);
  // /clear or a resume, as Claude Code 2.1.325 raises it: session.end for the
  // old id, then the classic SessionStart naming the next conversation. On a
  // resume the host switches its own id only after the SessionStart hooks.
  async function restart({ reason = 'clear', source = reason, next = 'session-2', prior } = {}) {
    await emit('session.end', { reason, sessionId: id, resume: { id } });
    if (reason === 'clear') id = next;
    const result = await begin(source, next, prior);
    id = next;
    return result;
  }
  await emit('session.start', { isInteractive: extra.interactive !== false, surface: 'terminal' });
  return { $, calls, files, statuses, panes, writes, registered, emit, advance, finish, turn, command, draw, badge,
    details, tool, submit, respond, begin, restart, limits: list => { limits = list; }, use: next => { id = next; },
    running: () => timers.filter(t => t.active).length,
    now: () => now, elapse: ms => { now += ms; }, model: name => { model = name; } };
}

function textOf(tree) {
  if (!tree) return '';
  if (typeof tree === 'string') return tree;
  return (tree.children ?? []).map(textOf).join(tree.type === 'Box' ? '\n' : '');
}

test('healthy cache is compact and unadorned; full receipts open outside the conversation', async () => {
  const h = await harness(); await h.turn(); await h.advance(55 * MIN);
  assert.equal(await h.badge(), 'neosian · cache ~60m · keep-warm on');
  assert.deepEqual(h.statuses, []); // The host styles ui.status as a warning.
  for (const width of [80, 40, 24]) {
    const tree = await h.draw('AbovePrompt', { bodyColumns: width });
    const lines = textOf(tree).split('\n');
    assert.ok(lines.every(line => line.length <= width));
    assert.ok(tree.children.every(line => line.props.color === 'success' && line.props.wrap === 'wrap'));
  }
  const count = h.calls.length;
  assert.deepEqual(await h.command('status'), {});
  assert.equal(h.calls.length, count);
  assert.equal(h.panes.at(-1).id, 'neosian-cache-details');
  assert.equal(h.panes.at(-1).closeOnEscape, true);
  assert.match(await h.details(), /Cache read: 19000 tokens/);
  assert.match(await h.details(), /Cache written: 500 tokens/);
  assert.match(await h.details(), /Output: 5 tokens/);
});

test('cache display preserves other mods and yields to surveys and agent views', async () => {
  const h = await harness();
  const prior = { type: 'Text', props: {}, children: ['another plugin'] };
  assert.equal((await h.draw('AbovePrompt', {}, prior)).children[0], prior);
  for (const props of [{ hasSurvey: true }, { view: { agentId: 'child' } }]) {
    assert.equal(await h.draw('AbovePrompt', props, prior), prior);
  }
  assert.equal(await h.draw('Pane', {}, prior, 'another-plugin'), prior);
  await h.emit('session.end');
  assert.equal(await h.draw('AbovePrompt', {}, prior), prior);
});

test('expiry earns warning styling while ready and disabled remain neutral', async () => {
  const h = await harness();
  assert.equal((await h.draw()).children[0].props.color, 'subtle');
  await h.turn(); await h.advance(65 * MIN, true);
  assert.match(await h.badge(), /cache expired.*next message may be cold/);
  assert.equal((await h.draw()).children[0].props.color, 'warning');
  assert.equal(h.calls.length, 0);
  await h.command('off');
  assert.equal((await h.draw()).children[0].props.color, 'subtle');
});

test('idle maintenance repeats for hours without conversation or tool calls', async () => {
  const h = await harness();
  await h.advance(2 * 60 * MIN); assert.equal(h.calls.length, 0);
  await h.turn();
  await h.advance(55 * MIN - 1); assert.equal(h.calls.length, 0);
  await h.advance(1); assert.equal(h.calls.length, 1);
  await h.advance(110 * MIN); assert.equal(h.calls.length, 3);
  assert.deepEqual(h.calls.map(c => c.at - h.calls[0].at), [0, 55 * MIN, 110 * MIN]);
  assert.match(await h.details(), /Cache read: 19000 tokens/);
  assert.ok(h.writes.every(w => !w.body.includes('SENSITIVE')));
  assert.equal(h.calls[0].prompt, prompt.split('\n---\n')[1].trim());
});

test('real activity resets the idle deadline and never receives a concurrent refresh', async () => {
  const h = await harness(); await h.turn(); await h.advance(54 * MIN);
  await h.emit('turn.start', { turnId: 'busy' }); await h.advance(2 * 60 * MIN);
  assert.equal(h.calls.length, 0);
  await h.finish(); await h.advance(54 * MIN); assert.equal(h.calls.length, 0);
  await h.advance(MIN); assert.equal(h.calls.length, 1);
});

test('even a long main response finishes without awaiting a maintenance call', async () => {
  const h = await harness({}, { stepMs: 55 * MIN });
  await h.turn(); assert.equal(h.calls.length, 0);
  await h.advance(0, true); assert.equal(h.calls.length, 1);
});

test('subagent work does not seed or move the main conversation deadline', async () => {
  const h = await harness(); await h.turn({ agentId: 'child' });
  await h.advance(60 * MIN); assert.equal(h.calls.length, 0);
  await h.turn(); await h.advance(54 * MIN); await h.turn({ agentId: 'child' });
  await h.advance(MIN); assert.equal(h.calls.length, 1);
});

test('sleep past expiry produces no model call; ordinary conversation rearms automatically', async () => {
  const h = await harness(); await h.turn(); await h.advance(65 * MIN, true);
  assert.equal(h.calls.length, 0); assert.match(await h.badge(), /next message may be cold/);
  await h.advance(3 * 60 * MIN); assert.equal(h.calls.length, 0);
  await h.turn(); await h.advance(55 * MIN); assert.equal(h.calls.length, 1);
});

test('off and duration control only maintenance, and on never rebuilds a cold cache', async () => {
  const h = await harness(); await h.turn(); await h.command('off');
  await h.advance(65 * MIN); await h.command('on'); await h.advance(65 * MIN);
  assert.equal(h.calls.length, 0);
  await h.turn(); await h.command('90m'); await h.advance(180 * MIN);
  assert.equal(h.calls.length, 1); assert.match(await h.badge(), /duration ended/);
});

test('status is read-only even when a refresh is due', async () => {
  const h = await harness(); await h.turn(); h.elapse(55 * MIN);
  await h.command('status'); await h.command(''); assert.equal(h.calls.length, 0);
  await h.advance(0, true); assert.equal(h.calls.length, 1);
});

test('missing evidence becomes an unknown warning, not a permanent checking state', async () => {
  const h = await harness(); await h.turn({ observation: false });
  await h.advance(2 * MIN); assert.match(await h.badge(), /unknown.*next message/);
  assert.equal(h.calls.length, 0);
});

test('Windows asset line endings preserve the maintenance prompt', async () => {
  const h = await harness({}, { crlf: true }); await h.turn(); await h.advance(55 * MIN);
  assert.equal(h.calls.length, 1);
  assert.equal(h.calls[0].prompt, prompt.split('\n---\n')[1].trim());
});

test('session controls reset on a new session and never import an old certificate', async () => {
  const h = await harness(); await h.turn(); await h.command('off');
  await h.emit('session.end'); await h.emit('session.start', { isInteractive: true });
  await h.advance(55 * MIN); assert.equal(h.calls.length, 0);
  await h.turn(); await h.advance(55 * MIN); assert.equal(h.calls.length, 1);
});

test('persistent preference uses the host-accepted value, including on next session', async () => {
  const h = await harness(); await h.turn();
  await h.emit('config.set', { key: 'neosian-cache.enabled', value: true }, { value: false });
  await h.advance(55 * MIN); assert.equal(h.calls.length, 0);
  await h.emit('session.end'); await h.emit('session.start', { isInteractive: true });
  await h.turn(); await h.advance(55 * MIN); assert.equal(h.calls.length, 0);
});

test('closing during a fork prevents its completion from restoring UI or maintenance', async () => {
  let release;
  const waiting = new Promise(resolve => { release = resolve; });
  const h = await harness({}, { fork: () => waiting }); await h.turn();
  const running = h.advance(55 * MIN);
  while (!h.calls.length) await new Promise(resolve => setImmediate(resolve));
  await h.emit('session.end'); release(structuredClone(WARM)); await running;
  await h.advance(180 * MIN); assert.equal(h.calls.length, 1);
  assert.equal(await h.badge(), '');
});

for (const [name, args] of Object.entries({
  'missing observation': { observation: false }, 'malformed observation': { malformed: true },
  'five-minute cache': { ttl: '5m' }, 'usage-billed default': { subscription: false },
  'aborted turn': { reason: 'aborted' }, 'no usage': { u: null },
  'wrong session': { data: { session_id: 'someone-else' } },
  'wrong model': { data: { model: 'another-model' } },
  'future observation': { data: { observed_at_ms: Number.MAX_SAFE_INTEGER } },
  'unsafe count': { data: { main_requests: 1.5 } },
  'wrong receipt': { data: { last_main_cache_read_tokens: 500 } },
  'unknown lifetime': { data: { ttl: 'tomorrow' } },
})) {
  test('no automatic model call with ' + name, async () => {
    const h = await harness(); await h.turn(args); await h.advance(180 * MIN);
    assert.equal(h.calls.length, 0);
  });
}

test('explicit API opt-in still requires a warm one-hour observation', async () => {
  const h = await harness({ allowApi: true }); await h.turn({ subscription: false });
  await h.advance(55 * MIN); assert.equal(h.calls.length, 1);
});

for (const [name, extra] of Object.entries({
  'old host': { version: '2.1.286' }, 'unqualified major version': { version: '3.0.0' },
  'noninteractive process': { interactive: false }, 'unsafe session id': { id: '../elsewhere' },
})) {
  test(name + ' cannot start maintenance', async () => {
    const h = await harness({}, extra); await h.turn(); await h.advance(180 * MIN);
    assert.equal(h.calls.length, 0);
  });
}

for (const [name, response] of Object.entries({
  cold: { ...WARM, usage: { ...WARM.usage, cache_read_input_tokens: 2000, cache_creation_input_tokens: 17500 } },
  'oversized output': { ...WARM, usage: { ...WARM.usage, output_tokens: 1000 } },
  'too many new input tokens': { ...WARM, usage: { ...WARM.usage, input_tokens: 5000 } },
  'unexpected answer': { ...WARM, text: 'I will continue working.' },
  'malformed answer': { ...WARM, text: null },
})) {
  // An unanswered refresh is retried once first (U3, below).
  test('one ' + name + ' response stops further attempts', async () => {
    const h = await harness({}, { fork: async () => response });
    await h.turn(); await h.advance(180 * MIN); assert.equal(h.calls.length, 1);
    assert.match(await h.details(), /automatic refresh paused/);
  });
}

test('small high-effort receipt permits a bounded helper tail', async () => {
  const h = await harness({}, { fork: async () => ({ ...WARM, usage: {
    input_tokens: 17, output_tokens: 151, cache_read_input_tokens: 8859,
    cache_creation_input_tokens: 656,
  } }) });
  await h.turn({ u: { ...API_USAGE, cache_read_input_tokens: 645, cache_creation_input_tokens: 8867 } });
  await h.advance(55 * MIN);
  const data = JSON.parse(h.files.get('/observations/session-1.refresh.json'));
  assert.equal(data.last_refresh.outcome, 'reused');
});

test('large-context receipt still rejects more than 2048 unreused tokens', async () => {
  const h = await harness({}, { fork: async () => ({ ...WARM, usage: {
    ...WARM.usage, cache_read_input_tokens: 497900, cache_creation_input_tokens: 2000,
  } }) });
  await h.turn({ u: { ...API_USAGE, cache_read_input_tokens: 400000, cache_creation_input_tokens: 100000 } });
  await h.advance(180 * MIN); assert.equal(h.calls.length, 1);
  const data = JSON.parse(h.files.get('/observations/session-1.refresh.json'));
  assert.equal(data.last_refresh.outcome, 'stopped');
});

for (const [event, args] of [
  ['session.end', { reason: 'prompt_input_exit' }],
  ['session.compact', { trigger: 'manual' }],
  ['classic.PostModelSwitch', {}],
  ['command.run', { command: 'rewind', args: '' }],
  ['config.set', { key: 'effortLevel', value: 'high' }],
]) {
  test(event + ' invalidates pending maintenance', async () => {
    const h = await harness(); await h.turn(); await h.advance(54 * MIN);
    await h.emit(event, args); await h.advance(180 * MIN); assert.equal(h.calls.length, 0);
  });
}

test('a model switch without a notification is caught before sending', async () => {
  const h = await harness(); await h.turn(); h.model('another-model');
  await h.advance(180 * MIN); assert.equal(h.calls.length, 0);
});

test('a host estimate extended by an unrelated helper cannot extend our verified lifetime', async () => {
  const h = await harness(); await h.turn({ data: { expires_at: 999999999 } });
  await h.advance(65 * MIN, true); assert.equal(h.calls.length, 0);
});

test('an old receipt cannot arm a resumed session', async () => {
  const h = await harness(); await h.turn(); await h.emit('session.end', {});
  await h.emit('session.start', { isInteractive: true });
  await h.advance(55 * MIN); assert.equal(h.calls.length, 0);
  await h.turn(); await h.advance(55 * MIN); assert.equal(h.calls.length, 1);
});

test('a user turn overtaking a fork keeps the new deadline and records the superseded call', async () => {
  let release;
  const waiting = new Promise(resolve => { release = resolve; });
  const h = await harness({}, { fork: () => waiting });
  await h.turn(); const running = h.advance(55 * MIN);
  while (!h.calls.length) await new Promise(resolve => setImmediate(resolve));
  await h.turn(); release(structuredClone(WARM)); await running;
  const receipt = JSON.parse(h.files.get('/observations/session-1.refresh.json'));
  assert.equal(receipt.last_refresh.outcome, 'superseded');
  await h.advance(54 * MIN); assert.equal(h.calls.length, 1);
});

// /clear and a resume end the conversation, not the process: no session.start
// follows, and the next conversation's classic SessionStart re-arms keep-warm.
for (const [name, args] of Object.entries({
  '/clear': {}, 'a resume': { reason: 'resume' }, 'a resume as a fork': { reason: 'resume', source: 'fork' },
})) {
  test(name + ' re-arms keep-warm for the next conversation under its new id', async () => {
    const h = await harness(); await h.turn(); await h.advance(10 * MIN);
    const prior = { additionalContext: ['from a settings hook'] };
    assert.equal(await h.restart({ ...args, prior }), prior); // next(e)'s result, unchanged.
    assert.equal(h.running(), 1);
    assert.equal(h.registered.length, 2);
    assert.equal(await h.badge(), 'neosian · keep-warm ready');
    const ready = JSON.parse(h.files.get('/observations/session-2.refresh.json'));
    assert.deepEqual([ready.session_id, ready.state, ready.observed_at_ms], ['session-2', 'Waiting for your first message', h.now()]);
    const old = h.files.get('/observations/session-1.refresh.json');
    await h.advance(120 * MIN); assert.equal(h.calls.length, 0); // Nothing to keep warm before a message.
    await h.turn(); await h.advance(55 * MIN - 1); assert.equal(h.calls.length, 0);
    await h.advance(1); assert.equal(h.calls.length, 1);
    const data = JSON.parse(h.files.get('/observations/session-2.refresh.json'));
    assert.deepEqual([data.session_id, data.state, data.last_refresh.outcome, data.observed_at_ms],
      ['session-2', 'Cache refresh verified', 'reused', h.now()]);
    assert.equal(h.files.get('/observations/session-1.refresh.json'), old);
  });
}

test('session controls end with their conversation; the next starts at the configured defaults', async () => {
  const h = await harness(); await h.turn(); await h.command('off');
  await h.restart();
  assert.equal(await h.badge(), 'neosian · keep-warm ready');
  await h.turn(); await h.advance(55 * MIN); assert.equal(h.calls.length, 1);
  const timed = await harness(); await timed.turn(); await timed.command('90m');
  await timed.restart({ reason: 'resume' });
  assert.match(await timed.details(), /Keep-warm: on · until session closes/);
  const off = await harness({ enabled: false }); await off.turn(); await off.command('on');
  await off.restart();
  await off.turn(); await off.advance(180 * MIN); assert.equal(off.calls.length, 0);
});

test('a real exit stays closed, whatever follows it', async () => {
  for (const reason of ['prompt_input_exit', 'other', 'logout']) {
    const h = await harness(); await h.turn();
    await h.emit('session.end', { reason, sessionId: 'session-1', resume: { id: 'session-1' } });
    const writes = h.writes.length;
    await h.begin('clear', 'session-2'); await h.command('status');
    await h.turn(); await h.advance(180 * MIN);
    assert.deepEqual([h.calls.length, h.running(), h.writes.length, h.registered.length], [0, 0, writes, 1]);
    assert.equal(await h.badge(), '');
  }
});

test('a SessionStart at process start starts nothing twice and adds no context', async () => {
  for (const source of ['startup', 'resume', 'fork', 'compact']) {
    const h = await harness();
    const writes = h.writes.length;
    const prior = { additionalContext: ['from a settings hook'] };
    assert.equal(await h.begin(source, 'session-1', prior), prior);
    assert.deepEqual([h.registered.length, h.running(), h.writes.length], [1, 1, writes]);
    await h.turn(); await h.advance(55 * MIN); assert.equal(h.calls.length, 1);
  }
});

test('a SessionStart before the end, or none, leaves the next main turn or command to re-arm', async () => {
  const h = await harness(); await h.turn();
  await h.begin('clear', 'session-2'); // Still open: ignored.
  await h.emit('session.end', { reason: 'clear', sessionId: 'session-1', resume: { id: 'session-1' } });
  h.use('session-2');
  await h.begin('compact');
  await h.turn({ agentId: 'child' });
  assert.deepEqual([h.running(), h.registered.length, await h.badge()], [0, 1, '']);
  await h.turn(); assert.equal(h.running(), 1);
  await h.advance(55 * MIN); assert.equal(h.calls.length, 1);
  assert.equal(JSON.parse(h.files.get('/observations/session-2.refresh.json')).last_refresh.outcome, 'reused');
  const c = await harness(); await c.turn();
  await c.emit('session.end', { reason: 'resume', sessionId: 'session-1', resume: { id: 'session-1' } });
  c.use('session-2');
  await c.command('off'); assert.equal(c.running(), 1); // The command applies to the new conversation.
  await c.turn(); await c.advance(180 * MIN); assert.equal(c.calls.length, 0);
});

// Usage-limit notes (U1). Figures stand for `$.session.usage()`'s rate limits.
const lib = await import(new URL('../../assets/clients/claude-cache/hooks/usage.js', import.meta.url).href);
const limit = (kind, p, resetsAt) => ({ kind, percentUsed: p,
  ...(resetsAt === undefined ? {} : { resetsAt: new Date(resetsAt).toISOString() }) });
const five = (p, resetsAt = START + 2 * HOUR) => limit('five_hour', p, resetsAt);
const week = (p, resetsAt = START + 3 * 24 * HOUR) => limit('seven_day', p, resetsAt);
const notesOf = result => result?.additionalContext ?? [];

test('usage windows keep finite 5-hour and weekly figures and a future reset', () => {
  const now = Date.parse('2026-11-15T10:00:00Z');
  assert.deepEqual(lib.windows([
    { kind: 'five_hour', percentUsed: 93.5, resetsAt: '2026-11-15T12:14:00Z' },
    { kind: 'seven_day', percentUsed: 23, resetsAt: 'not a date' },
    { kind: 'spend_limit', percentUsed: 120 }, { kind: 'five_hour', percentUsed: 10 },
  ], now), [{ kind: 'five_hour', p: 93.5, resetsAt: Date.parse('2026-11-15T12:14:00Z') },
    { kind: 'seven_day', p: 23, resetsAt: null }]);
  for (const bad of [NaN, Infinity, -1, 100.1, '90', null]) {
    assert.deepEqual(lib.windows([{ kind: 'five_hour', percentUsed: bad }], now), []);
  }
  assert.equal(lib.windows([five(50, now - 1)], now)[0].resetsAt, null);
  assert.deepEqual(lib.windows(undefined, now), []);
});

test('the rate is the steepest rise over samples aged 90 seconds to 15 minutes', () => {
  const samples = [{ t: -20 * MIN, p: 0 }, { t: -10 * MIN, p: 40 }, { t: -4 * MIN, p: 42 },
    { t: -MIN, p: 20 }, { t: 0, p: 50 }];
  assert.equal(lib.rate(samples, 0), 2);
  assert.equal(lib.rate([{ t: -5 * MIN, p: 50 }, { t: 0, p: 50 }], 0), null);
  assert.equal(lib.rate([{ t: -30000, p: 10 }, { t: 0, p: 50 }], 0), null);
  assert.equal(lib.minutesLeft(50, 2), 25);
  assert.equal(lib.minutesLeft(99, 2, 5), 0);
  assert.equal(lib.minutesLeft(50, null), null);
});

const levelOf = (state, now) => lib.assess(state, now).map(w => [w.kind, w.p, w.level]);
const deliver = (state, now, agent) => {
  const due = lib.peek(state, now, { agent });
  if (due.length) lib.commit(state, due, agent);
  return due[0]?.level ?? null;
};

test('a moved reset or a large fall with no reset known starts a window and rearms its levels', () => {
  const state = {};
  const reset = 5 * HOUR;
  lib.observe(state, [{ kind: 'five_hour', p: 96, resetsAt: reset }], 0);
  assert.equal(deliver(state, 0), 'wrap');
  lib.observe(state, [{ kind: 'five_hour', p: 96, resetsAt: reset + 120000 }], MIN);
  assert.equal(deliver(state, MIN), null);
  lib.observe(state, [{ kind: 'five_hour', p: 3, resetsAt: reset + 5 * HOUR }], 2 * MIN);
  lib.observe(state, [{ kind: 'five_hour', p: 86, resetsAt: reset + 5 * HOUR }], 3 * MIN);
  assert.equal(deliver(state, 3 * MIN), 'notice');
  const open = {};
  lib.observe(open, [{ kind: 'seven_day', p: 30, resetsAt: null }], 0);
  lib.observe(open, [{ kind: 'seven_day', p: 11, resetsAt: null }], 1000);
  assert.deepEqual(levelOf(open, 1000), [['seven_day', 30, null]]);
  lib.observe(open, [{ kind: 'seven_day', p: 10, resetsAt: null }], 2000);
  assert.deepEqual(levelOf(open, 2000), [['seven_day', 10, null]]);
  // A reset time known on one side only: the large fall still starts a window.
  for (const [before, after] of [[null, 9 * HOUR], [4 * HOUR, null]]) {
    const mixed = {};
    lib.observe(mixed, [{ kind: 'five_hour', p: 96, resetsAt: before }], 0);
    assert.equal(deliver(mixed, 0), 'wrap');
    lib.observe(mixed, [{ kind: 'five_hour', p: 3, resetsAt: after }], MIN);
    assert.deepEqual(levelOf(mixed, MIN), [['five_hour', 3, null]]);
    lib.observe(mixed, [{ kind: 'five_hour', p: 96, resetsAt: after }], 2 * MIN);
    assert.equal(deliver(mixed, 2 * MIN), 'wrap');
  }
});

test('a one-point step back inside a window is jitter: same window, same levels, same pace', () => {
  const state = {};
  const reset = 5 * HOUR;
  lib.observe(state, [{ kind: 'five_hour', p: 20, resetsAt: reset }], 0);
  lib.observe(state, [{ kind: 'five_hour', p: 30, resetsAt: reset }], 5 * MIN);
  const before = lib.assess(state, 5 * MIN + 3000);
  lib.observe(state, [{ kind: 'five_hour', p: 29, resetsAt: reset }], 5 * MIN + 3000);
  assert.deepEqual(lib.assess(state, 5 * MIN + 3000), before);
  lib.observe(state, [{ kind: 'five_hour', p: 86, resetsAt: reset }], 30 * MIN);
  assert.equal(deliver(state, 30 * MIN), 'notice');
  lib.observe(state, [{ kind: 'five_hour', p: 85, resetsAt: reset }], 30 * MIN + 3000);
  assert.equal(deliver(state, 30 * MIN + 3000), null);
});

test('figures every 2 s at 3 points a minute still raise the pace wrap well before 95%', () => {
  const state = {};
  for (let t = 0; t < 20 * MIN; t += 2000) {
    const p = Math.floor((50 + 3 * t / MIN) * 10) / 10;
    lib.observe(state, [{ kind: 'five_hour', p, resetsAt: 4 * HOUR }], t);
    const [w] = lib.assess(state, t);
    if (w.level) {
      assert.equal(w.level, 'pace');
      assert.ok(w.p < 75, `first wrap at ${w.p}%`);
      return;
    }
  }
  assert.fail('no pace wrap');
});

test('a clock that goes back throws nothing and invents no pace', () => {
  const state = {};
  const reset = 5 * HOUR;
  for (const [t, p] of [[0, 60], [5 * MIN, 62], [-30 * MIN, 63], [-29 * MIN, 63.5], [15 * MIN, 64]]) {
    lib.observe(state, [{ kind: 'five_hour', p, resetsAt: reset }], t);
    assert.deepEqual(lib.assess(state, t).map(w => w.level), [null]);
  }
});

test('note copy comes from the asset with formatted placeholders and no stray markup', () => {
  const templates = lib.parse(notesAsset);
  assert.ok(templates);
  assert.deepEqual(lib.parse(notesAsset.replaceAll('\n', '\r\n')), templates);
  assert.deepEqual([lib.percent(93), lib.percent(93.5), lib.percent(7)], ['93', '93.5', '7']);
  assert.deepEqual([2 * HOUR + 14 * MIN, 42 * MIN, 76 * HOUR, 30000].map(lib.span), ['2h 14m', '42m', '3d 4h', '1m']);
  const notice = lib.compose(templates, 'notice', { kind: 'seven_day', p: 85.25, resetsAt: null,
    left: null, now: 0, advice: 'handoff' });
  assert.match(notice, /^neosian usage note: this account has used 85\.3% of its weekly usage limit\. Its reset time is not known\. Nothing needs to stop yet\./);
  const wrap = lib.compose(templates, 'wrap', { kind: 'five_hour', p: 96, now: 0,
    resetsAt: Date.parse('2026-11-15T12:14:00Z') - Date.parse('2026-11-15T10:00:00Z'), left: 4.7, advice: 'wait' });
  assert.match(wrap, /It resets in 2h 14m \(1970-01-01 02:14 UTC\)\. At the recent pace it reaches the limit in about 4m\./);
  assert.match(wrap, /schedule its continuation for just after the reset/);
  assert.equal(lib.compose(templates, 'pace', { kind: 'five_hour', p: 96, now: 0,
    resetsAt: Date.parse('2026-11-15T12:14:00Z') - Date.parse('2026-11-15T10:00:00Z'), left: 4.7, advice: 'wait' }), wrap);
  const agent = lib.compose(templates, 'wrap', { kind: 'five_hour', p: 96, now: 0, resetsAt: null,
    left: null, advice: 'wait' }, 'agent');
  assert.match(agent, /Finish the step in progress and return now/);
  assert.doesNotMatch(agent, /schedule/);
  for (const text of [notice, wrap, agent]) {
    assert.doesNotMatch(text, /[{}—]|\s{2}|\n/);
  }
});

test('advice waits only for a known reset inside the horizon, clamped to 1 to 10 hours', () => {
  assert.equal(lib.advice(6 * HOUR, 0, 6), 'wait');
  assert.equal(lib.advice(6 * HOUR + 1, 0, 6), 'handoff');
  assert.equal(lib.advice(null, 0, 10), 'handoff');
  assert.deepEqual([50, 0, 2.5, '8', undefined, NaN].map(lib.horizon), [10, 1, 2.5, 6, 6, 6]);
});

test('a notice and a wrap each reach the main thread once per window, never as a block', async () => {
  const h = await harness({}, { limits: [five(86)] });
  await h.respond();
  const first = await h.tool();
  assert.deepEqual(Object.keys(first), ['additionalContext']);
  assert.equal(notesOf(first).length, 1);
  assert.match(notesOf(first)[0], /used 86% of its 5-hour usage limit\. It resets in 2h \(1970-01-02 05:46 UTC\)\. Nothing needs to stop yet/);
  assert.deepEqual(await h.tool(), {});
  assert.deepEqual(await h.submit(), {});
  await h.respond([five(95.5)]);
  const wrap = await h.submit();
  assert.equal(notesOf(wrap).length, 1);
  assert.match(notesOf(wrap)[0], /95\.5%.*clean stopping point.*schedule its continuation/);
  for (const result of [await h.submit(), await h.tool()]) assert.deepEqual(result, {});
  for (const result of [first, wrap]) {
    assert.ok(!['block', 'preventContinuation', 'stopReason'].some(key => key in result));
    assert.doesNotMatch(notesOf(result)[0], /SENSITIVE/);
  }
});

test('a figure the session has not refreshed with a response of its own records nothing', async () => {
  const h = await harness({}, { limits: [five(96)] });
  assert.deepEqual(await h.tool(), {}); // The start-up reading is not fresh evidence.
  await h.advance(5 * MIN);
  assert.deepEqual(await h.submit(), {});
  await h.respond();
  assert.equal(notesOf(await h.tool()).length, 1);
});

test('a wrap supersedes an undelivered notice, and no notice follows it', async () => {
  const h = await harness({}, { limits: [five(50)] });
  await h.respond();
  assert.deepEqual(await h.tool(), {});
  await h.respond([five(97)]);
  assert.match(notesOf(await h.tool())[0], /97%.*clean stopping point/);
  await h.respond([five(97)]); assert.deepEqual(await h.tool(), {});
  await h.respond([five(88)]); assert.deepEqual(await h.tool(), {});
});

test('an out-of-order step back inside a window sends no second note', async () => {
  const h = await harness({}, { limits: [five(95)] });
  await h.respond();
  assert.equal(notesOf(await h.tool()).length, 1);
  for (const p of [94, 95, 94.9]) {
    await h.respond([five(p)]);
    assert.deepEqual(await h.tool(), {});
  }
  assert.match(await h.details(), /5-hour usage: 95% · resets in 2h/);
});

test('a real burst while active calls for a wrap before 95%', async () => {
  const h = await harness({}, { limits: [five(40, START + 3 * HOUR)] });
  await h.turn();
  await h.advance(MIN); await h.respond([five(50, START + 3 * HOUR)], 'child');
  await h.advance(MIN); await h.respond([five(60, START + 3 * HOUR)], 'child');
  const [text] = notesOf(await h.tool());
  assert.match(text, /used 60%.*about 4m\..*clean stopping point/);
  const slow = await harness({}, { limits: [five(58)] });
  await slow.respond(); await slow.advance(10 * MIN);
  await slow.respond([five(60)]);
  assert.deepEqual(await slow.tool(), {});
});

test('an idle session adds no flat samples: 60 then 66 after idle raises no wrap; 95 still does', async () => {
  const h = await harness({}, { limits: [five(60, START + 3 * HOUR)] });
  await h.turn();
  await h.advance(10 * MIN); // Ticks read the same figure 20 times and record none of it.
  h.limits([five(66, START + 3 * HOUR)]);
  await h.advance(MIN);
  assert.deepEqual(await h.tool(), {});
  await h.turn();
  assert.deepEqual(await h.tool(), {}); // 6 points over 11 minutes, about 9 hours left.
  await h.respond([five(95, START + 3 * HOUR)]);
  assert.match(notesOf(await h.tool())[0], /95%.*clean stopping point/);
});

test('a pace wrap needs the limit to come before the reset; the 95% wrap does not', async () => {
  const pace = async resetsAt => {
    const h = await harness({}, { limits: [limit('five_hour', 40, resetsAt)] });
    await h.respond(); await h.advance(2 * MIN);
    await h.respond([limit('five_hour', 60, resetsAt)]);
    return notesOf(await h.tool());
  };
  assert.equal((await pace(START + 3 * HOUR)).length, 1); // Limit in about 4m, reset in about 3h.
  assert.deepEqual(await pace(START + 5 * MIN), []); // The reset, about 3m away, comes first.
  assert.match((await pace(undefined))[0], /Its reset time is not known.*about 4m/);
  const late = await harness({}, { limits: [five(96, START + 2 * MIN)] });
  await late.respond();
  assert.equal(notesOf(await late.tool()).length, 1);
});

test('a new window rearms both levels; a figure from before its reset sends nothing', async () => {
  const h = await harness({}, { limits: [five(96)] });
  await h.respond();
  assert.equal(notesOf(await h.tool()).length, 1);
  await h.respond([five(3, START + 7 * HOUR)]); assert.deepEqual(await h.tool(), {});
  await h.respond([five(86, START + 7 * HOUR)]); assert.match(notesOf(await h.tool())[0], /86%.*Nothing needs to stop/);
  const stale = await harness({}, { limits: [five(50, START + 10 * MIN)] });
  await stale.respond();
  stale.elapse(11 * MIN);
  await stale.respond([five(97, START + 10 * MIN)]);
  assert.deepEqual(await stale.tool(), {});
  assert.doesNotMatch(await stale.badge(), /usage/);
  await stale.respond([five(97, START + 5 * HOUR)]);
  assert.equal(notesOf(await stale.tool()).length, 1);
});

test('with several windows at wrap the note names the later reset, and its advice follows it', async () => {
  const far = await harness({}, { limits: [five(97), week(96)] });
  await far.respond();
  const [weekly] = notesOf(await far.tool());
  assert.match(weekly, /96% of its weekly usage limit\. It resets in 3d \(1970-01-05 03:46 UTC\)\..*Do not wait for the reset/);
  assert.deepEqual(await far.tool(), {}); // The 5-hour wrap was covered by that note.
  const unknown = await harness({}, { limits: [limit('five_hour', 96), week(97)] });
  await unknown.respond();
  assert.match(notesOf(await unknown.tool())[0], /96% of its 5-hour usage limit\. Its reset time is not known/);
  const levels = await harness({}, { limits: [five(96), week(90)] });
  await levels.respond();
  assert.match(notesOf(await levels.tool())[0], /96% of its 5-hour.*schedule its continuation/);
  assert.deepEqual(await levels.tool(), {});
  const notices = await harness({}, { limits: [five(86), week(88)] });
  await notices.respond();
  assert.match(notesOf(await notices.tool())[0], /88% of its weekly.*Nothing needs to stop/);
});

test('the wait horizon option chooses between waiting and a handoff', async () => {
  const far = [five(96, START + 8 * HOUR)];
  const standard = await harness({}, { limits: far });
  await standard.respond();
  assert.match(notesOf(await standard.tool())[0], /Do not wait for the reset\. Leave a handoff note/);
  const longer = await harness({ waitHorizonHours: 10 }, { limits: far });
  await longer.respond();
  assert.match(notesOf(await longer.tool())[0], /schedule its continuation/);
  const changed = await harness({}, { limits: far });
  await changed.emit('config.set', { key: 'neosian-cache.waitHorizonHours', value: 9 }, { value: 9 });
  await changed.respond();
  assert.match(notesOf(await changed.tool())[0], /schedule its continuation/);
});

test('subagents get the wrap note once per agent and window, never the notice', async () => {
  const h = await harness({}, { limits: [five(88)] });
  await h.respond([five(88)], 'agent-a');
  assert.deepEqual(await h.tool('agent-a'), {});
  assert.match(notesOf(await h.tool())[0], /Nothing needs to stop yet/);
  await h.respond([five(96)], 'agent-a');
  const [text] = notesOf(await h.tool('agent-a'));
  assert.match(text, /96% of its 5-hour.*Finish the step in progress and return now/);
  assert.doesNotMatch(text, /clean stopping point/);
  assert.deepEqual(await h.tool('agent-a'), {});
  assert.equal(notesOf(await h.tool('agent-b')).length, 1);
  assert.match(notesOf(await h.tool())[0], /clean stopping point/);
  assert.match(await h.details(), /Last usage note: wrap \(5-hour\)/);
  for (let i = 0; i < 64; i++) assert.equal(notesOf(await h.tool('bulk-' + i)).length, 1);
  assert.equal(notesOf(await h.tool('agent-a')).length, 1); // The id set is bounded at 64.
});

test("other hooks' context and fields are kept ahead of the note", async () => {
  const h = await harness({}, { limits: [five(86)] });
  await h.respond();
  const result = await h.tool(undefined, { additionalContext: ['from another hook'], updatedToolOutput: 'kept' });
  assert.equal(result.updatedToolOutput, 'kept');
  assert.equal(result.additionalContext[0], 'from another hook');
  assert.match(result.additionalContext[1], /86%/);
  await h.respond([five(96)]);
  const prompted = await h.submit({ additionalContext: ['a'], sessionTitle: 'kept' });
  assert.equal(prompted.sessionTitle, 'kept');
  assert.deepEqual(prompted.additionalContext.slice(0, 1), ['a']);
  assert.equal(prompted.additionalContext.length, 2);
});

test('a failure anywhere in the usage path returns the host result unchanged', async () => {
  const exploding = () => [{ kind: 'five_hour', get percentUsed() { throw new Error('boom'); } }];
  for (const extra of [{ usageError: true }, { limits: exploding }, { limits: [five(96)] }]) {
    const h = await harness({}, extra);
    if (!extra.usageError && extra.limits !== exploding) {
      h.$.session.usage = async () => { throw new Error('later failure'); };
    }
    await h.respond();
    for (const hook of [prior => h.tool(undefined, prior), prior => h.tool('child', prior), h.submit]) {
      const prior = { additionalContext: ['other'], updatedToolOutput: 'kept' };
      assert.equal(await hook(prior), prior);
    }
  }
  const hostile = await harness({}, { limits: [five(96)] });
  await hostile.respond();
  const odd = new Proxy({}, { get: (target, key) => {
    if (key === 'additionalContext') throw new Error('odd result');
    return Reflect.get(target, key);
  } });
  assert.equal(await hostile.tool(undefined, odd), odd); // Thrown while the note was being attached,
  assert.equal(notesOf(await hostile.tool()).length, 1); // so it arrives at the next event instead.
  const display = await harness({}, { limits: [five(96)] });
  display.$.ui.invalidate = () => { throw new Error('display failure'); };
  await display.respond();
  const prior = { additionalContext: ['other'] };
  assert.deepEqual(notesOf(await display.tool(undefined, prior)).length, 2); // A broken display keeps the note.
});

test('usageNotes off silences notes only; keep-warm off leaves notes on', async () => {
  const h = await harness({ usageNotes: false }, { limits: [five(96)] });
  await h.respond();
  assert.deepEqual(await h.tool(), {});
  assert.match(await h.details(), /Usage notes: off/);
  await h.turn(); await h.advance(55 * MIN); assert.equal(h.calls.length, 1);
  await h.emit('config.set', { key: 'neosian-cache.usageNotes', value: true }, { value: true });
  assert.equal(notesOf(await h.tool()).length, 1);
  const off = await harness({}, { limits: [five(96)] });
  await off.command('off');
  await off.respond();
  assert.equal(notesOf(await off.tool()).length, 1);
  assert.match(await off.details(), /Keep-warm: off[\s\S]*Usage notes: on/);
});

for (const [name, extra] of Object.entries({
  'a host without usage()': {}, 'a usage() that throws': { usageError: true },
  'a missing note asset': { limits: [five(96)], notes: null },
  'a note asset without frontmatter': { limits: [five(96)], notes: notesAsset.slice(notesAsset.indexOf('## notice')) },
  'a note asset missing a block': { limits: [five(96)], notes: notesAsset.replace('## handoff', '## farewell') },
  'a note asset with an unknown placeholder': { limits: [five(96)], notes: notesAsset.replace('used {percent}%', 'used {percentage}%') },
  'a note asset with a block placeholder out of place': { limits: [five(96)], notes: notesAsset.replace('Its reset time is not known.', '{advice}') },
})) {
  test(name + ' leaves notes unavailable and keep-warm exactly as before', async () => {
    const h = await harness({}, extra);
    await h.respond();
    assert.deepEqual(await h.tool(undefined, { additionalContext: ['other'] }), { additionalContext: ['other'] });
    assert.match(await h.details(), /Usage notes: unavailable/);
    await h.turn(); await h.advance(55 * MIN); assert.equal(h.calls.length, 1);
    // The badge still shows figures the host reported; only notes are unavailable.
    assert.equal(await h.badge(), 'neosian · cache ~60m · keep-warm on' + (extra.limits ? ' · usage 96%' : ''));
  });
}

test('a session without subscription windows gets no note and no badge', async () => {
  for (const limits of [[], [{ kind: 'spend_limit', percentUsed: 99 }]]) {
    const h = await harness({}, { limits });
    await h.respond();
    assert.deepEqual(await h.tool(), {});
    assert.deepEqual(await h.submit(), {});
    assert.equal(await h.badge(), 'neosian · keep-warm ready');
    assert.match(await h.details(), /Usage notes: on/);
    assert.doesNotMatch(await h.details(), /usage:/);
  }
});

test('two concurrent tool results carry one note between them', async () => {
  const h = await harness({}, { limits: [five(96)] });
  await h.respond();
  const results = await Promise.all([h.tool(), h.tool(), h.tool('child'), h.tool('child')]);
  assert.deepEqual(results.map(r => notesOf(r).length), [1, 0, 1, 0]);
});

test('a result another hook blocked or stopped carries no note, and the note waits for the next event', async () => {
  for (const beneath of [{ block: 'policy says no' }, { preventContinuation: true, stopReason: 'stop' }]) {
    const h = await harness({}, { limits: [five(96)] });
    await h.respond();
    assert.equal(await h.submit(beneath), beneath);
    assert.equal(await h.tool(undefined, beneath), beneath);
    assert.equal(notesOf(await h.tool()).length, 1);
  }
});

test('after /clear the next conversation hears the wrap again, without a session.start', async () => {
  const h = await harness({}, { limits: [five(96)] });
  await h.respond();
  assert.equal(notesOf(await h.tool()).length, 1);
  await h.emit('session.end', { reason: 'clear', sessionId: 'session-1' });
  await h.respond([five(97)]);
  assert.match(notesOf(await h.tool())[0], /97%.*clean stopping point/);
  assert.deepEqual(await h.tool(), {});
});

test('keep-warm coming back after /clear keeps the samples and lets the wrap be heard again', async () => {
  const h = await harness({}, { limits: [five(96)] });
  await h.respond();
  assert.equal(notesOf(await h.tool()).length, 1);
  await h.restart();
  assert.equal(await h.badge(), 'neosian · keep-warm ready · usage 96%'); // No new reading yet.
  assert.doesNotMatch(await h.details(), /Last usage note/);
  await h.respond([five(97)]);
  assert.match(notesOf(await h.tool())[0], /97%.*clean stopping point/);
  assert.deepEqual(await h.tool(), {});
});

test('a pace wrap early in a window leaves the notice at 85% and the wrap at 95% to come', async () => {
  const h = await harness({}, { limits: [five(40, START + 4 * HOUR)] });
  await h.respond();
  for (const p of [50, 60]) { await h.advance(MIN); await h.respond([five(p, START + 4 * HOUR)]); }
  assert.match(notesOf(await h.tool())[0], /used 60%.*clean stopping point/);
  const heard = [];
  for (let p = 61; p <= 96; p += 1) {
    await h.advance(3 * MIN); await h.respond([five(p, START + 4 * HOUR)]);
    for (const text of notesOf(await h.tool())) heard.push([p, /Nothing needs to stop/.test(text) ? 'notice' : 'wrap']);
  }
  assert.deepEqual(heard, [[85, 'notice'], [95, 'wrap']]);
});

for (const [name, interrupt] of Object.entries({
  'a failed refresh': async h => { await h.advance(55 * MIN + 30000); },
  'an interrupted step': async h => {
    await h.advance(12 * MIN);
    await h.emit('turn.step', { turnId: 't2', index: 0, model: 'claude-test' },
      { turnId: 't2', index: 0, answer: '', toolUses: [], stopReason: null, usage: null });
    await h.advance(30000);
  },
})) {
  test(name + ' is no evidence: idle at 60, then 70 from a real response, raises no wrap', async () => {
    const h = await harness({}, { limits: [five(60, START + 4 * HOUR)],
      fork: () => ({ isAnswered: false, reason: 'api-error', status: 529 }) });
    await h.turn();
    await interrupt(h);
    await h.advance(2 * MIN);
    await h.respond([five(70, START + 4 * HOUR)]);
    assert.deepEqual(await h.tool(), {});
  });
}

test('no reassuring notice while another window is at wrap; it comes after that window resets', async () => {
  const h = await harness({}, { limits: [five(96), week(80)] });
  await h.respond();
  assert.match(notesOf(await h.tool())[0], /96% of its 5-hour/);
  await h.respond([five(96.5), week(86)]);
  assert.deepEqual(await h.tool(), {});
  await h.respond([five(3, START + 7 * HOUR), week(86)]);
  assert.match(notesOf(await h.tool())[0], /86% of its weekly.*Nothing needs to stop/);
});

test('a window past its reset leaves the badge, pane and receipt; a vanished window is dropped', async () => {
  const h = await harness({}, { limits: [five(96, START + 10 * MIN), week(88)] });
  await h.turn();
  assert.match(await h.details(), /5-hour usage: 96%/);
  await h.advance(15 * MIN);
  const pane = await h.details();
  assert.doesNotMatch(pane, /5-hour usage/);
  assert.match(pane, /Weekly usage: 88%/);
  const receipt = JSON.parse(h.files.get('/observations/session-1.refresh.json'));
  assert.deepEqual(Object.keys(receipt.usage), ['seven_day']);
  await h.respond([five(20, START + 5 * HOUR)]);
  await h.advance(30000);
  assert.doesNotMatch(await h.details(), /Weekly usage/);
  assert.doesNotMatch(await h.badge(), /usage/);
  await h.respond([]);
  await h.advance(30000);
  assert.doesNotMatch(await h.details(), /usage: /);
});

for (const [name, run] of Object.entries({
  'idle maintenance for hours': async h => { await h.turn(); await h.advance(3 * 60 * MIN); },
  'activity postpones the refresh': async h => {
    await h.turn(); await h.advance(54 * MIN);
    await h.emit('turn.start', { turnId: 'busy' }); await h.advance(2 * 60 * MIN);
    await h.finish(); await h.advance(60 * MIN);
  },
  'sleep past expiry, then an ordinary turn': async h => {
    await h.turn(); await h.advance(65 * MIN, true); await h.advance(60 * MIN);
    await h.turn(); await h.advance(60 * MIN);
  },
})) {
  test('keep-warm refreshes exactly as before with usage figures present: ' + name, async () => {
    const plain = await harness();
    await run(plain);
    assert.ok(plain.calls.length > 0);
    // No gate applies to any of these: same calls at the same times.
    const moving = () => { let points = 70; return { limits: () => [five(points, START + 5 * HOUR)],
      fork: () => { points += 0.4; return structuredClone(WARM); } }; };
    for (const extra of [{ limits: [five(96, START + 5 * HOUR), week(50)] },
      { limits: [five(99.4, START + 5 * HOUR), week(94.9)] },
      { limits: [limit('five_hour', 94.9), limit('seven_day', 80)] },
      { limits: [five(100, START + 10 * MIN), week(60)] }, moving()]) {
      const measured = await harness({}, extra);
      await run(measured);
      assert.deepEqual(measured.calls.map(c => c.at), plain.calls.map(c => c.at));
    }
  });
}

test('a refresh response counts as fresh evidence for the next reading', async () => {
  const h = await harness({}, { limits: [five(50)] });
  await h.turn();
  h.limits([five(86)]);
  await h.advance(54 * MIN); assert.deepEqual(await h.tool(), {});
  await h.advance(MIN); assert.equal(h.calls.length, 1);
  assert.equal(notesOf(await h.tool()).length, 1);
});

test('notes work without the bridge in a non-interactive session and write no diagnostics', async () => {
  const h = await harness({}, { interactive: false, limits: [five(96)] });
  await h.respond();
  assert.equal(notesOf(await h.tool()).length, 1);
  assert.equal(h.writes.length, 0);
});

test('the badge appends usage at notice in the same tone, turns amber at wrap, and wraps narrowly', async () => {
  const h = await harness({}, { limits: [five(86, START + 3 * HOUR)] });
  await h.turn(); await h.advance(55 * MIN);
  assert.equal(await h.badge(), 'neosian · cache ~60m · keep-warm on · usage 86%');
  assert.equal((await h.draw()).children[0].props.color, 'success');
  await h.respond([five(96, START + 3 * HOUR)]); await h.advance(30000);
  assert.equal(await h.badge(), 'neosian · cache ~60m · keep-warm on · usage 96%');
  for (const width of [80, 40, 24, 20]) {
    const tree = await h.draw('AbovePrompt', { bodyColumns: width });
    assert.ok(textOf(tree).split('\n').every(line => line.length <= width));
    assert.ok(tree.children.every(line => line.props.color === 'warning'));
  }
});

test('the receipt carries usage figures and the last note, bounded and content-free', async () => {
  const h = await harness({}, { limits: [five(86), week(23.5)] });
  await h.respond();
  await h.tool();
  const raw = h.files.get('/observations/session-1.refresh.json');
  const data = JSON.parse(raw);
  assert.deepEqual(data.usage, { five_hour: { percent: 86, resets_at_ms: START + 2 * HOUR },
    seven_day: { percent: 23.5, resets_at_ms: START + 3 * 24 * HOUR } });
  assert.deepEqual(data.usage_note, { level: 'notice', window: 'five_hour', at_ms: START });
  assert.ok(raw.length < 8192);
  assert.doesNotMatch(raw, /SENSITIVE|usage note:/);
  const pane = await h.details();
  assert.match(pane, /5-hour usage: 86% · resets in 2h\nWeekly usage: 23\.5% · resets in 3d/);
  assert.match(pane, /Last usage note: notice \(5-hour\) at 1970-01-02T03:46:40\.000Z\nUsage notes: on/);
});

// U2: the warm wait. Refresh cost, the reserve, the refresh gate and the waiting badge.
const evidence = await import(new URL('../../assets/clients/claude-cache/hooks/evidence.js', import.meta.url).href);
const RATE_LIMITED = { isAnswered: false, reason: 'api-error', status: 429, error: 'rate_limit',
  usage: { input_tokens: 0, output_tokens: 0, cache_read_input_tokens: 0, cache_creation_input_tokens: 0 } };
const at = (kind, p, resetsAt) => ({ kind, p, resetsAt });

test('the cost estimate is the smallest of the last three observed, clamped to 0.1..2, or 0.5 before any', () => {
  assert.deepEqual([undefined, [], [0.3, 0.7, 1.2], [1.2, 0.7], [0], [3, 2.5]].map(lib.estimate),
    [0.5, 0.5, 0.3, 0.7, 0.1, 2]);
  const state = {};
  const costs = {};
  lib.observe(state, [at('five_hour', 60, 5 * HOUR)], 0);
  for (const [t, p, moved] of [[1, 60.3, 0.3], [2, 61, 0.7], [3, 60.9, 0], [4, 62.2, 1.2]]) {
    const before = lib.snapshot(state);
    lib.observe(state, [at('five_hour', p, 5 * HOUR)], t * HOUR);
    assert.deepEqual(lib.charge(costs, before, state), { five_hour: moved, seven_day: null });
  }
  assert.deepEqual(costs, { five_hour: [0.7, 0, 1.2] }); // The last three; a step back counts as 0.
  assert.equal(lib.estimate(costs.five_hour), 0.1);
  const before = lib.snapshot(state);
  lib.observe(state, [at('five_hour', 3, 10 * HOUR)], 5 * HOUR); // A new window has no previous reading.
  assert.deepEqual(lib.charge(costs, before, state), { five_hour: null, seven_day: null });
  assert.deepEqual(costs.five_hour, [0.7, 0, 1.2]);
});

test('the reserve covers the refreshes until a near reset, at most 5 points; far or unknown keeps none', () => {
  const state = {};
  lib.observe(state, [at('five_hour', 90, 120 * MIN), at('seven_day', 90, 3 * 24 * HOUR)], 0);
  assert.deepEqual(lib.reserves(state, {}, 0), { five_hour: 1.5, seven_day: 0 }); // ceil(120 / 55) refreshes at 0.5.
  assert.deepEqual(lib.reserves(state, { five_hour: [0.3, 0.4] }, 0), { five_hour: 0.9, seven_day: 0 });
  assert.equal(lib.reserves(state, { five_hour: [2] }, 0).five_hour, 5);
  assert.equal(lib.reserves(state, {}, 0, 1).five_hour, 0); // Beyond a one-hour horizon.
  assert.equal(lib.reserves(state, {}, 121 * MIN).five_hour, 0); // Past its reset.
  const open = {};
  lib.observe(open, [at('five_hour', 90, null)], 0);
  assert.deepEqual(lib.reserves(open, {}, 0), { five_hour: 0 });
  // The wrap floor is 95 less the window's reserve; the notice stays at 85.
  const level = (p, reserve) => {
    const one = {};
    lib.observe(one, [at('five_hour', p, 120 * MIN)], 0);
    return lib.assess(one, 0, { five_hour: reserve })[0].level;
  };
  assert.deepEqual([level(93.5, 1.5), level(93.4, 1.5), level(90, 5), level(89.9, 5), level(85, 5), level(84.9, 5)],
    ['wrap', 'notice', 'wrap', 'notice', 'notice', null]);
  assert.equal(lib.minutesLeft(90, 2, 5), 2.5); // Minutes left run to 100 less the reserve.
  const wrapped = {};
  lib.observe(wrapped, [at('five_hour', 94, 120 * MIN)], 0);
  lib.commit(wrapped, [{ kind: 'five_hour', level: 'wrap' }]);
  assert.equal(lib.assess(wrapped, 0)[0].level, 'wrap'); // A delivered wrap holds without the reserve.
});

test('the reserve decides when a note is due; the note still counts the minutes to the real limit', () => {
  const state = {};
  for (const [t, p] of [[0, 59], [5 * MIN, 69], [10 * MIN, 79]]) lib.observe(state, [at('five_hour', p, 3 * HOUR)], t);
  assert.equal(lib.assess(state, 10 * MIN)[0].level, null); // 21 points at 2 a minute: 10.5 minutes.
  const [top] = lib.assess(state, 10 * MIN, { five_hour: 2 }); // 19 points to the reserve: 9.5 minutes.
  assert.deepEqual([top.level, top.left], ['pace', 10.5]);
  assert.match(lib.note(lib.parse(notesAsset), top, 10 * MIN), /reaches the limit in about 10m\./);
});

test('the gate holds a refresh at the limit, or at the wrap floor before a far or unknown reset', () => {
  const gate = (list, now = 0) => {
    const state = {};
    lib.observe(state, list, 0);
    return lib.gate(state, {}, now);
  };
  assert.deepEqual([
    gate([at('five_hour', 99.5, 30 * MIN)]), gate([at('five_hour', 99.4, 30 * MIN)]),
    gate([at('seven_day', 99.5, 3 * 24 * HOUR)]), gate([at('seven_day', 95, 3 * 24 * HOUR)]),
    gate([at('seven_day', 94.9, 3 * 24 * HOUR)]), gate([at('five_hour', 95, null)]),
    gate([at('five_hour', 100, 10 * MIN)], 11 * MIN), gate([at('five_hour', 97, 2 * HOUR), at('seven_day', 96, 3 * 24 * HOUR)]),
    gate([]),
  ], ['limit', null, 'limit', 'exhausted', null, 'exhausted', null, 'exhausted', null]);
});

test('a session waits for the reset of its last main-thread wrap while that reset is near and ahead', () => {
  const state = {};
  lib.observe(state, [at('five_hour', 96, 2 * HOUR), at('seven_day', 96, 3 * 24 * HOUR)], 0);
  lib.commit(state, lib.peek(state, 0)); // One note covers both windows at wrap.
  const last = (window, level = 'wrap') => ({ level, window, at_ms: 0 });
  assert.deepEqual(lib.waiting(state, last('five_hour'), 0), { kind: 'five_hour', resetsAt: 2 * HOUR });
  assert.deepEqual([lib.waiting(state, last('seven_day'), 0), lib.waiting(state, last('five_hour'), 2 * HOUR),
    lib.waiting(state, last('five_hour', 'notice'), 0), lib.waiting(state, null, 0)], [null, null, null, null]);
  lib.observe(state, [at('five_hour', 3, 7 * HOUR), at('seven_day', 96, 3 * 24 * HOUR)], MIN);
  assert.equal(lib.waiting(state, last('five_hour'), MIN), null); // A new window heard no note yet.
});

test('a refusal for a rate limit is told apart from other failures', () => {
  assert.deepEqual([RATE_LIMITED, { ...RATE_LIMITED, status: null }, { ...RATE_LIMITED, error: 'unknown' },
    { ...RATE_LIMITED, status: 529, error: 'overloaded' }, { ...RATE_LIMITED, status: 500, error: 'server_error' },
    WARM, null].map(evidence.limited), [true, true, true, false, false, false, false]);
});

test('a verified refresh records what it moved each window by, as an upper bound', async () => {
  let points = 60;
  const h = await harness({}, { limits: () => [five(points, START + 4 * HOUR), week(20)],
    fork: () => { points += 0.3; return structuredClone(WARM); } });
  await h.turn(); await h.advance(55 * MIN); assert.equal(h.calls.length, 1);
  const beat = () => JSON.parse(h.files.get('/observations/session-1.refresh.json')).last_refresh;
  assert.deepEqual(beat().usage_cost, { five_hour: 0.3, seven_day: 0 });
  assert.match(await h.details(), /Refresh moved 5-hour usage by at most 0\.3 points\nRefresh moved weekly usage by at most 0 points/);
  points += 1.1; // Other sessions' usage while idle lands in the next refresh's figure.
  await h.advance(55 * MIN); assert.equal(h.calls.length, 2);
  assert.deepEqual(beat().usage_cost, { five_hour: 1.4, seven_day: 0 });
  points += 0.7;
  await h.advance(55 * MIN); assert.equal(h.calls.length, 3);
  assert.deepEqual(beat().usage_cost, { five_hour: 1, seven_day: 0 });
  assert.match(await h.details(), /by at most 1 point\nRefresh moved weekly usage by at most 0 points/);
  for (const extra of [{ limits: [five(60)], fork: async () => ({ ...WARM, text: 'no' }) }, {}]) {
    const other = await harness({}, extra); // Unverified, or a host without usage figures.
    await other.turn(); await other.advance(55 * MIN);
    assert.deepEqual(JSON.parse(other.files.get('/observations/session-1.refresh.json')).last_refresh.usage_cost,
      { five_hour: null, seven_day: null });
    assert.doesNotMatch(await other.details(), /Refresh moved/);
  }
});

test('a near reset lowers the wrap floor by the reserve; a far reset and the notice stay put', async () => {
  const near = await harness({}, { limits: [five(94, START + 2 * HOUR)] });
  await near.turn(); // A one-hour subscription cache observed: keep-warm can refresh.
  assert.match(notesOf(await near.tool())[0], /used 94%.*clean stopping point/); // Floor 95 - 3 * 0.5.
  const far = await harness({}, { limits: [five(94, START + 8 * HOUR)] });
  await far.turn();
  assert.match(notesOf(await far.tool())[0], /used 94%.*Nothing needs to stop yet/);
});

test('the reserve applies only while keep-warm can refresh in this session', async () => {
  const limits = [five(94, START + 2 * HOUR)];
  const heard = async (h, turn = {}) => {
    if (turn) await h.turn(turn); else await h.respond(); // A subagent's reading: no cache observation.
    const [text] = notesOf(await h.tool());
    return /clean stopping point/.test(text) ? 'wrap' : /Nothing needs to stop yet/.test(text) ? 'notice' : text;
  };
  assert.equal(await heard(await harness({}, { limits })), 'wrap'); // A one-hour subscription cache observed.
  assert.equal(await heard(await harness({}, { limits }), null), 'notice'); // Before the first observation.
  assert.equal(await heard(await harness({}, { limits }), { ttl: '5m' }), 'notice');
  assert.equal(await heard(await harness({}, { limits }), { subscription: false }), 'notice');
  assert.equal(await heard(await harness({ allowApi: true }, { limits }), { subscription: false }), 'wrap');
  assert.equal(await heard(await harness({}, { limits, interactive: false })), 'notice'); // A -p run.
  assert.equal(await heard(await harness({}, { limits, bridge: '{"observations":""}' })), 'notice'); // No bridge.
  assert.equal(await heard(await harness({ enabled: false }, { limits })), 'notice'); // Off by preference.
  const toggled = await harness({}, { limits });
  await toggled.turn(); await toggled.command('off');
  assert.equal(await heard(toggled, null), 'notice');
  await toggled.command('on'); // Back on: the reserve returns and the wrap, still due, follows.
  assert.equal(await heard(toggled, null), 'wrap');
  const busy = await harness({}, { limits }); // A turn clears the certificate; the eligibility stays.
  await busy.turn(); await busy.emit('turn.start', { turnId: 'next' });
  assert.equal(await heard(busy, null), 'wrap');
  const cleared = await harness({}, { limits }); // A new conversation waits for its own observation.
  await cleared.turn(); await cleared.restart();
  assert.equal(await heard(cleared, null), 'notice');
  assert.equal(await heard(cleared), 'wrap');
  const badge = await harness({}, { limits });
  await badge.turn();
  assert.equal((await badge.draw()).children[0].props.color, 'warning'); // Wrap level: amber.
  await badge.command('off');
  assert.equal((await badge.draw()).children[0].props.color, 'subtle'); // A notice again.
});

test('a delivered wrap holds for its window while the floor moves: no notice after it, amber, still waiting', async () => {
  for (const control of ['refresh', 'off']) {
    const h = await harness({}, { limits: [five(94, START + 2 * HOUR), week(86)] });
    await h.turn();
    assert.match(notesOf(await h.tool())[0], /used 94% of its 5-hour.*clean stopping point/);
    if (control === 'refresh') { await h.advance(56 * MIN); assert.equal(h.calls.length, 1); } // Cost 0: floor 94.8.
    else await h.command('off'); // No reserve: the floor is 95 again.
    assert.deepEqual(await h.submit(), {}); // The weekly notice stays held.
    assert.deepEqual(await h.tool(), {});
    assert.equal((await h.draw()).children[0].props.color, 'warning', control);
    assert.match(await h.badge(), control === 'refresh' ? /^neosian · cache ~59m · waiting for reset · usage 94%$/
      : /^neosian · keep-warm off · usage 94%$/);
  }
});

test('the waiting line appears only when the next refresh would be sent', async () => {
  const shown = async (limits, control) => {
    const h = await harness({}, { limits });
    await h.turn(); await h.tool();
    if (control) await h.command(control);
    return [await h.badge(), /Waiting for the/.test(await h.details())];
  };
  const reset = START + 3 * HOUR;
  assert.deepEqual(await shown([five(96, reset)]), ['neosian · cache ~60m · waiting for reset · usage 96%', true]);
  assert.deepEqual(await shown([five(99.6, reset)]), ['neosian · cache ~60m · keep-warm on · usage 99.6%', false]);
  assert.deepEqual(await shown([five(96, reset)], '2h'), ['neosian · cache ~60m · keep-warm on · usage 96%', false]);
  assert.deepEqual(await shown([five(96, reset)], '4h'), ['neosian · cache ~60m · waiting for reset · usage 96%', true]);
  let weekly = 94; // The weekly window, far from its reset, reaches 95.5 with the refresh's own reading.
  const h = await harness({}, { limits: () => [five(96, START + 4 * HOUR), week(weekly)],
    fork: () => { weekly = 95.5; return structuredClone(WARM); } });
  await h.turn(); await h.tool();
  assert.match(await h.badge(), /waiting for reset/);
  await h.advance(55 * MIN); assert.equal(h.calls.length, 1);
  assert.equal(await h.badge(), 'neosian · cache ~60m · keep-warm on · usage 96%');
  assert.doesNotMatch(await h.details(), /Waiting for the/);
});

test('at the usage limit keep-warm sends nothing, and the next ordinary message rearms it', async () => {
  const h = await harness({}, { limits: [five(99.6, START + 3 * HOUR)] });
  await h.turn(); await h.advance(55 * MIN); assert.equal(h.calls.length, 0);
  assert.match(await h.details(), /^Usage limit reached · keep-warm paused$/m);
  assert.equal(await h.badge(), 'neosian · cache paused · next message may be cold · usage 99.6%');
  await h.advance(2 * HOUR); assert.equal(h.calls.length, 0);
  h.limits([five(2, START + 8 * HOUR)]); // A new window after the reset.
  await h.turn(); await h.advance(55 * MIN); assert.equal(h.calls.length, 1);
});

test('near the end of a window that resets far away or at an unknown time, keep-warm pauses to preserve it', async () => {
  for (const [limits, calls] of [[[five(40), week(95.5)], 0], [[five(40), week(94.9)], 1],
    [[limit('five_hour', 96)], 0], [[five(97, START + 3 * HOUR)], 1], [[five(100, START + 10 * MIN)], 1]]) {
    const h = await harness({}, { limits });
    await h.turn(); await h.advance(55 * MIN);
    assert.equal(h.calls.length, calls, JSON.stringify(limits));
    if (!calls) assert.match(await h.details(), /^Usage nearly exhausted · keep-warm paused to preserve it$/m);
  }
});

test('a refresh the host refuses for a rate limit says so; other failures keep the general reason', async () => {
  const h = await harness({}, { fork: async () => structuredClone(RATE_LIMITED) });
  await h.turn(); await h.advance(180 * MIN); assert.equal(h.calls.length, 1);
  assert.match(await h.details(), /^Request refused for a rate limit · keep-warm paused$/m); // Perhaps a short one.
  assert.equal(await h.badge(), 'neosian · cache paused · next message may be cold');
  assert.match(await h.details(), /Result: stopped/);
  const busy = await harness({}, { fork: async () => ({ ...RATE_LIMITED, status: 529, error: 'overloaded' }) });
  await busy.turn(); await busy.advance(180 * MIN); assert.equal(busy.calls.length, 2); // Retried once (U3).
  assert.match(await busy.details(), /^Refresh unverified · automatic refresh paused until your next message$/m);
});

test('after a wrap-up note an idle session kept warm shows that it waits for the reset', async () => {
  const h = await harness({}, { limits: [five(96, START + 2 * HOUR)] });
  await h.turn();
  assert.equal(notesOf(await h.tool()).length, 1);
  assert.equal(await h.badge(), 'neosian · cache ~60m · waiting for reset · usage 96%');
  assert.match(await h.details(), /^Waiting for the 5-hour reset in 2h; keep-warm continues$/m);
  assert.doesNotMatch(await h.details(), /^keep-warm on$/m);
  await h.advance(55 * MIN); assert.equal(h.calls.length, 1); // Keep-warm continues as before.
  assert.match(await h.details(), /^Waiting for the 5-hour reset in 1h 5m; keep-warm continues$/m);
  await h.emit('turn.start', { turnId: 'busy' }); await h.advance(30000); // The next tick redraws.
  assert.equal(await h.badge(), 'neosian · conversation active · usage 96%');
  await h.finish(); await h.advance(70 * MIN); // Past the reset: the badge is as before.
  assert.equal(await h.badge(), 'neosian · cache ~45m · keep-warm on');
  for (const [limits, control] of [[[week(96)], ''], [[five(86, START + 2 * HOUR)], ''], [[five(96, START + 2 * HOUR)], 'off']]) {
    const other = await harness({}, { limits }); // A far reset, a notice only, keep-warm off.
    await other.turn(); await other.tool();
    if (control) await other.command(control);
    assert.doesNotMatch(await other.badge(), /waiting/);
    assert.doesNotMatch(await other.details(), /Waiting for/);
  }
});

// U3: why a refresh was not answered, one retry, and the line after a compaction.
const ZERO = { input_tokens: 0, output_tokens: 0, cache_read_input_tokens: 0, cache_creation_input_tokens: 0 };
const OVERLOADED = { isAnswered: false, reason: 'api-error', status: 529, error: 'overloaded', usage: ZERO };
const WHY = { reason: 'api-error', error: 'overloaded', status: 529 };
const UNVERIFIED = 'Refresh unverified · automatic refresh paused until your next message';
const receiptOf = (h, id = 'session-1') => JSON.parse(h.files.get(`/observations/${id}.refresh.json`));
// The host's replies in order; the last one repeats.
const replies = (...list) => n => structuredClone(list[Math.min(n, list.length) - 1]);

for (const [name, reply, why, line] of [
  ['an overloaded API', OVERLOADED, WHY, 'api-error · overloaded · HTTP 529'],
  ['a dropped connection', { ...OVERLOADED, status: null, error: 'unknown' },
    { reason: 'api-error', error: 'unknown', status: null }, 'api-error · unknown'],
  ['an abort before any reply', { isAnswered: false, reason: 'aborted', usage: ZERO },
    { reason: 'aborted', error: null, status: null }, 'aborted'],
  ['nothing to fork', { isAnswered: false, reason: 'nothing-to-fork' },
    { reason: 'nothing-to-fork', error: null, status: null }, 'nothing-to-fork'],
  ['text where an identifier belongs', { ...OVERLOADED, status: '529', error: 'SENSITIVE error text' },
    { reason: 'api-error', error: null, status: null }, 'api-error'],
  ['a status out of range', { ...OVERLOADED, status: 52900 },
    { reason: 'api-error', error: 'overloaded', status: null }, 'api-error · overloaded'],
  ['no result at all', null, { reason: null, error: null, status: null }, 'no reason given'],
]) {
  test('an unanswered refresh records the host reason, never its text: ' + name, async () => {
    const h = await harness({}, { fork: () => structuredClone(reply) });
    await h.turn(); await h.advance(55 * MIN); assert.equal(h.calls.length, 1);
    const data = receiptOf(h);
    const r = data.last_refresh;
    assert.deepEqual([r.outcome, r.attempt, r.unanswered, r.first_attempt], ['unanswered', 1, why, null]);
    assert.equal(data.state, 'Refresh not answered · retrying once in about a minute');
    assert.match(await h.details(), new RegExp(`^Not answered: ${line}$`, 'm'));
    assert.doesNotMatch(h.files.get('/observations/session-1.refresh.json'), /SENSITIVE/);
  });
}

test('an unanswered refresh is retried once about a minute later and continues as verified', async () => {
  const h = await harness({}, { fork: replies(OVERLOADED, WARM) });
  await h.turn(); await h.advance(55 * MIN); assert.equal(h.calls.length, 1);
  assert.equal(await h.badge(), 'neosian · cache ~5m · refresh retry pending');
  assert.equal((await h.draw()).children[0].props.color, 'warning');
  assert.match(await h.details(), /^refresh retry pending$/m);
  await h.advance(30000); assert.equal(h.calls.length, 1); // Not yet: 45 seconds at least.
  await h.advance(30000); assert.equal(h.calls.length, 2); // The first tick after them.
  assert.deepEqual(h.calls.map(c => c.at - START), [55 * MIN, 56 * MIN]);
  assert.equal(h.calls[1].prompt, h.calls[0].prompt);
  const { state, last_refresh: r } = receiptOf(h);
  assert.equal(state, 'Cache refresh verified');
  assert.deepEqual([r.outcome, r.attempt, r.unanswered, r.first_attempt, r.at_ms, r.read],
    ['reused', 2, null, WHY, START + 56 * MIN, 19000]);
  assert.equal(await h.badge(), 'neosian · cache ~60m · keep-warm on');
  assert.match(await h.details(), /^Result: reused\nRetry of an unanswered refresh: api-error · overloaded · HTTP 529$/m);
  await h.advance(55 * MIN - 1); assert.equal(h.calls.length, 2); // Due 55 minutes after the retry.
  await h.advance(1); assert.equal(h.calls.length, 3);
  assert.deepEqual([receiptOf(h).last_refresh.attempt, receiptOf(h).last_refresh.first_attempt], [1, null]);
});

for (const [name, second] of Object.entries({
  'not answered again': OVERLOADED,
  'answered over a cold cache': { ...WARM, usage: { ...WARM.usage, cache_read_input_tokens: 2000,
    cache_creation_input_tokens: 17500 } },
  'answered with something else': { ...WARM, text: 'I will continue working.' },
  'with nothing to fork': { isAnswered: false, reason: 'nothing-to-fork' },
})) {
  test('a retry ' + name + ' pauses as before, and the next message rearms', async () => {
    const h = await harness({}, { fork: replies(OVERLOADED, second) });
    await h.turn(); await h.advance(180 * MIN); assert.equal(h.calls.length, 2);
    const { state, last_refresh: r } = receiptOf(h);
    assert.equal(state, UNVERIFIED);
    assert.deepEqual([r.outcome, r.attempt, r.first_attempt], ['stopped', 2, WHY]);
    assert.equal(await h.badge(), 'neosian · cache paused · next message may be cold');
    await h.turn(); await h.advance(55 * MIN); assert.equal(h.calls.length, 3);
  });
}

test('a limit refusal is never retried; a retry refused for a limit says so', async () => {
  const h = await harness({}, { fork: async () => structuredClone(RATE_LIMITED) });
  await h.turn(); await h.advance(180 * MIN); assert.equal(h.calls.length, 1);
  const { state, last_refresh: r } = receiptOf(h);
  assert.equal(state, 'Request refused for a rate limit · keep-warm paused');
  assert.deepEqual([r.outcome, r.attempt, r.unanswered],
    ['stopped', 1, { reason: 'api-error', error: 'rate_limit', status: 429 }]);
  assert.match(await h.details(), /^Not answered: api-error · rate_limit · HTTP 429$/m);
  const later = await harness({}, { fork: replies(OVERLOADED, RATE_LIMITED) });
  await later.turn(); await later.advance(180 * MIN); assert.equal(later.calls.length, 2);
  assert.equal(receiptOf(later).state, 'Request refused for a rate limit · keep-warm paused');
  assert.equal(receiptOf(later).last_refresh.attempt, 2);
});

for (const [name, response] of Object.entries({
  'cold': { ...WARM, usage: { ...WARM.usage, cache_read_input_tokens: 2000, cache_creation_input_tokens: 17500 } },
  'an unexpected answer': { ...WARM, text: 'I will continue working.' },
  'oversized output': { ...WARM, usage: { ...WARM.usage, output_tokens: 1000 } },
  'a reply with no text that counted tokens': { isAnswered: false, reason: 'empty-reply', usage: { ...WARM.usage } },
  'an error after a served request': { ...OVERLOADED, usage: { ...WARM.usage, output_tokens: 40 } },
})) {
  test('a served refresh that did not verify is never retried: ' + name, async () => {
    const h = await harness({}, { fork: async () => structuredClone(response) });
    await h.turn(); await h.advance(180 * MIN); assert.equal(h.calls.length, 1);
    const { state, last_refresh: r } = receiptOf(h);
    assert.equal(state, UNVERIFIED);
    assert.deepEqual([r.outcome, r.attempt, r.first_attempt], ['stopped', 1, null]);
    assert.equal(r.unanswered?.reason ?? null, response.isAnswered ? null : response.reason);
  });
}

for (const [name, interrupt] of Object.entries({
  'a user turn': async h => {
    await h.emit('turn.start', { turnId: 'next' }); await h.advance(2 * MIN); await h.finish();
  },
  'a compaction': async h => { await h.emit('session.compact', { trigger: 'manual' }, { messages: [] }); },
  'a model switch': async h => { await h.emit('classic.PostModelSwitch', {}); },
})) {
  test('no retry once ' + name + ' comes in between', async () => {
    const h = await harness({}, { fork: replies(OVERLOADED, WARM) });
    await h.turn(); await h.advance(55 * MIN);
    assert.equal(receiptOf(h).last_refresh.outcome, 'unanswered');
    await h.advance(30000); await interrupt(h); await h.advance(5 * MIN);
    assert.equal(h.calls.length, 1);
    assert.equal(receiptOf(h).last_refresh.attempt, 1);
  });
}

test('a user turn in between keeps its own certificate and refreshes 55 minutes after it', async () => {
  const h = await harness({}, { fork: replies(OVERLOADED, WARM) });
  await h.turn(); await h.advance(55 * MIN + 30000);
  await h.emit('turn.start', { turnId: 'next' }); await h.advance(2 * MIN);
  await h.finish(); // 57m30s
  await h.advance(54 * MIN); assert.equal(h.calls.length, 1);
  await h.advance(MIN); assert.deepEqual(h.calls.map(c => c.at - START), [55 * MIN, 112 * MIN + 30000]);
  assert.deepEqual([receiptOf(h).last_refresh.outcome, receiptOf(h).last_refresh.attempt], ['reused', 1]);
});

test('no retry when the model changed without a notification', async () => {
  const h = await harness({}, { fork: replies(OVERLOADED, WARM) });
  await h.turn(); await h.advance(55 * MIN);
  assert.equal(receiptOf(h).last_refresh.outcome, 'unanswered');
  h.model('another-model'); await h.advance(5 * MIN);
  assert.equal(h.calls.length, 1);
  assert.equal(receiptOf(h).state, 'Model changed · waiting for your next message');
});

test('no retry once keep-warm was turned off, even when it is on again before the retry', async () => {
  const preference = value => h => h.emit('config.set', { key: 'neosian-cache.enabled', value }, { value });
  for (const [name, controls] of Object.entries({
    off: [h => h.command('off')],
    'off then on': [h => h.command('off'), h => h.command('on')],
    'the preference off then on': [preference(false), preference(true)],
  })) {
    const h = await harness({}, { fork: replies(OVERLOADED, WARM) });
    await h.turn(); await h.advance(55 * MIN);
    assert.equal(receiptOf(h).last_refresh.outcome, 'unanswered');
    for (const control of controls) await control(h);
    await h.advance(5 * MIN); assert.equal(h.calls.length, 1, name);
    if (name === 'off') assert.equal(await h.badge(), 'neosian · keep-warm off');
    else assert.equal(receiptOf(h).state, UNVERIFIED, name);
  }
});

test('no retry once the deadline margin is reached first', async () => {
  // Sent at 58m50s: 45 seconds later is past the deadline less its 30-second margin.
  const late = await harness({}, { fork: replies(OVERLOADED, WARM) });
  await late.turn(); await late.command('off'); await late.advance(58 * MIN + 50000);
  await late.command('on'); assert.equal(late.calls.length, 1);
  const r = receiptOf(late).last_refresh;
  assert.deepEqual([r.outcome, r.attempt, r.unanswered], ['stopped', 1, WHY]);
  assert.equal(receiptOf(late).state, UNVERIFIED);
  // Sent at 58m44s the retry is due at 59m29s, but the next tick at 59m30s finds the margin reached.
  const edge = await harness({}, { fork: replies(OVERLOADED, WARM) });
  await edge.turn(); await edge.command('off'); await edge.advance(58 * MIN + 44000);
  await edge.command('on'); assert.equal(receiptOf(edge).last_refresh.outcome, 'unanswered');
  await edge.advance(5 * MIN); assert.equal(edge.calls.length, 1);
  assert.equal(receiptOf(edge).state, 'Cache expired or near expiry · your next message may rebuild context');
  // A machine asleep past the margin sends no retry when it wakes.
  const asleep = await harness({}, { fork: replies(OVERLOADED, WARM) });
  await asleep.turn(); await asleep.advance(55 * MIN); await asleep.advance(10 * MIN, true);
  assert.equal(asleep.calls.length, 1);
  assert.equal(receiptOf(asleep).state, 'Cache expired or near expiry · your next message may rebuild context');
});

test('at most one retry per certificate; a verified retry renews it with one of its own', async () => {
  const h = await harness({}, { fork: replies(OVERLOADED, WARM, OVERLOADED, OVERLOADED) });
  await h.turn(); await h.advance(4 * HOUR);
  assert.deepEqual(h.calls.map(c => c.at - START), [55 * MIN, 56 * MIN, 111 * MIN, 112 * MIN]);
  const { state, last_refresh: r } = receiptOf(h);
  assert.deepEqual([state, r.outcome, r.attempt, r.first_attempt], [UNVERIFIED, 'stopped', 2, WHY]);
});

test('the usage gate is checked again before the retry', async () => {
  const h = await harness({}, { limits: [five(60, START + 3 * HOUR)], fork: replies(OVERLOADED, WARM) });
  await h.turn(); await h.advance(55 * MIN);
  assert.equal(receiptOf(h).last_refresh.outcome, 'unanswered');
  await h.respond([five(99.6, START + 3 * HOUR)]); // A subagent's response brings a fresh figure.
  await h.advance(MIN); assert.equal(h.calls.length, 1);
  assert.equal(receiptOf(h).state, 'Usage limit reached · keep-warm paused');
});

test('after a compaction the line says the context was compacted and what the next message writes', async () => {
  const h = await harness(); await h.turn(); await h.advance(10 * MIN);
  const result = { messages: [], tokensBefore: 69015, tokensAfter: 16384 };
  assert.equal(await h.emit('session.compact', { trigger: 'manual' }, result), result); // Passed on unchanged.
  assert.equal(await h.badge(), 'neosian · context compacted · next message writes 16k+ tokens'); // Drawn at once.
  assert.equal((await h.draw()).children[0].props.color, 'subtle');
  const pane = await h.details();
  assert.match(pane, /^Context compacted · your next message writes at least 16k tokens\nKeep-warm: on/m);
  assert.doesNotMatch(pane, /16k\+/); // The panel states it once.
  assert.doesNotMatch(pane + await h.badge(), /cold|expired/);
  assert.equal(receiptOf(h).state, 'Context compacted · your next message writes at least 16k tokens');
  await h.advance(3 * HOUR); assert.equal(h.calls.length, 0); // Nothing is sent after a compaction.
  assert.equal(await h.badge(), 'neosian · context compacted · next message writes 16k+ tokens');
  await h.turn(); assert.equal(await h.badge(), 'neosian · cache ~60m · keep-warm on'); // The next message certifies.
  await h.advance(55 * MIN); assert.equal(h.calls.length, 1);
});

test('a compaction without a token count says only that the context was compacted', async () => {
  for (const [after, line, reason] of [
    [undefined, 'neosian · context compacted', 'Context compacted · waiting for your next message'],
    [0, 'neosian · context compacted', 'Context compacted · waiting for your next message'],
    ['16384', 'neosian · context compacted', 'Context compacted · waiting for your next message'],
    [1.5, 'neosian · context compacted', 'Context compacted · waiting for your next message'],
    [800, 'neosian · context compacted · next message writes 800+ tokens',
      'Context compacted · your next message writes at least 800 tokens'],
    [1234567, 'neosian · context compacted · next message writes 1234k+ tokens',
      'Context compacted · your next message writes at least 1234k tokens'],
  ]) {
    const h = await harness(); await h.turn();
    await h.emit('session.compact', { trigger: 'auto' }, { messages: [], ...(after === undefined ? {} : { tokensAfter: after }) });
    assert.equal(await h.badge(), line, String(after));
    assert.match(await h.details(), new RegExp(`^${reason}$`, 'm'));
    assert.doesNotMatch(await h.details(), /cold/);
  }
});

test('a skipped, precomputed or subagent compaction keeps the wording it had', async () => {
  const skipped = await harness(); await skipped.turn();
  await skipped.emit('session.compact', { trigger: 'manual' }, { skip: 'declined by another hook' });
  await skipped.advance(30000);
  assert.match(await skipped.details(), /^Context changed · waiting for your next message$/m);
  for (const e of [{ trigger: 'precompute' }, { trigger: 'auto', agentId: 'child' }]) {
    const h = await harness(); await h.turn();
    await h.emit('session.compact', e, { messages: [], tokensAfter: 16384 });
    assert.equal(await h.badge(), 'neosian · cache ~60m · keep-warm on');
    await h.advance(55 * MIN); assert.equal(h.calls.length, 1);
  }
});

test('a compaction inside a turn certifies again when the turn completes, as before', async () => {
  const h = await harness(); await h.turn(); await h.advance(10 * MIN);
  await h.emit('turn.start', { turnId: 'next' });
  await h.emit('session.compact', { trigger: 'auto' }, { messages: [], tokensAfter: 10497 });
  assert.equal(await h.badge(), 'neosian · conversation active');
  await h.finish();
  assert.equal(await h.badge(), 'neosian · cache ~60m · keep-warm on');
  await h.advance(55 * MIN); assert.equal(h.calls.length, 1);
});

test('the largest receipt stays bounded and content-free, byte for byte as the bridge test reads it', async () => {
  const id = 'w'.repeat(128);
  const odd = { isAnswered: false, reason: 'r'.repeat(40), error: 'e'.repeat(40), status: 599, usage: ZERO };
  const h = await harness({}, { id, version: '2.1.99999', fork: () => structuredClone(odd),
    limits: [five(94.95, START + 4 * HOUR), week(94.95, START + 6 * 24 * HOUR)] });
  await h.turn({ data: { version: '2.1.99999' } });
  assert.equal(notesOf(await h.tool()).length, 1); // A usage note: `usage_note`.
  await h.command('168h'); // `until_ms`.
  await h.advance(56 * MIN); assert.equal(h.calls.length, 2); // The refresh and its retry, both unanswered.
  await h.emit('session.compact', { trigger: 'auto' }, { messages: [], tokensAfter: Number.MAX_SAFE_INTEGER });
  const raw = h.files.get(`/observations/${id}.refresh.json`);
  const fixture = await readFile(new URL('./claude-cache/receipt.json', import.meta.url), 'utf8');
  assert.equal(raw, fixture.trimEnd());
  assert.ok(Buffer.byteLength(raw) < 8192); // The bridge's bound (cache_bridge.rs).
  assert.doesNotMatch(raw, /SENSITIVE|WARM/);
  const r = JSON.parse(raw).last_refresh;
  assert.deepEqual(Object.keys(r), ['at_ms', 'read', 'write', 'output', 'outcome', 'attempt', 'unanswered',
    'first_attempt', 'usage_cost']);
});
