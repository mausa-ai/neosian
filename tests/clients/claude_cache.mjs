// Execute the shipped mod with a deterministic public-host double, without a
// Claude installation, credentials, real timers, or access to a conversation.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';

const moduleUrl = new URL('../../assets/clients/claude-cache/hooks/register.js', import.meta.url);
const prompt = await readFile(new URL('../../assets/clients/claude-cache/assets/refresh.md', import.meta.url), 'utf8');
const MIN = 60000;
const API_USAGE = { input_tokens: 15, output_tokens: 5, cache_read_input_tokens: 2000,
  cache_creation_input_tokens: 17000, model: 'claude-test' };
const WARM = { isAnswered: true, text: 'WARM', usage: { input_tokens: 17,
  output_tokens: 5, cache_read_input_tokens: 19000, cache_creation_input_tokens: 500 } };
let serial = 0;

async function harness(options = {}, extra = {}) {
  const handlers = [];
  const files = new Map([['/plugin/bridge.json', '{"observations":"/observations"}'],
    ['/plugin/assets/refresh.md', extra.crlf ? prompt.replaceAll('\n', '\r\n') : prompt]]);
  let now = 100000000;
  let mainRequests = 0;
  let timers = [];
  let model = 'claude-test';
  const calls = [];
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
    session: { id: async () => extra.id ?? 'session-1', model: async () => model,
      version: async () => ({ version: extra.version ?? '2.1.314' }) },
    fs: { read: async path => {
      if (!files.has(path)) throw new Error('missing file');
      return files.get(path);
    }, stat: async path => ({ size: (files.get(path) ?? '').length, kind: 'file', isLink: false }),
    write: async (path, body) => { writes.push({ path, body }); files.set(path, body); } },
    command: { register: async () => {} },
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
      files.set('/observations/session-1.json', malformed ? 'not JSON' : JSON.stringify({
        format: 1, session_id: 'session-1', version: '2.1.314', observed_at_ms: now,
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
  await emit('session.start', { isInteractive: extra.interactive !== false, surface: 'terminal' });
  return { $, calls, files, statuses, panes, writes, emit, advance, finish, turn, command, draw, badge, details,
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
  'missing snapshot': { isAnswered: false, reason: 'nothing-to-fork' },
  'API failure': { isAnswered: false, reason: 'api-error', status: 503 },
})) {
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
