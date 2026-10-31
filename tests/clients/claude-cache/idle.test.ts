// Run against Claude's official test host in a disposable plugin copy. The
// portable Node suite exercises more races; these tests check the real API seam.
import { expect, test, mock } from 'claude-code/testing';

const MIN = 60000;
const usage = { input_tokens: 2, output_tokens: 4, cache_read_input_tokens: 18000,
  cache_creation_input_tokens: 1000, model: 'claude-test' };
const warm = { isAnswered: true, text: 'WARM', usage: { input_tokens: 17,
  output_tokens: 5, cache_read_input_tokens: 19000, cache_creation_input_tokens: 500 } };

function harness(on, { ttl = '1h', reply = warm, subscription = true } = {}) {
  const clock = mock.clock(on, { now: 100000000 });
  const calls = [];
  const statuses = [];
  const writes = [];
  let observation;
  let requests = 0;
  on('session.id', () => ({ value: 'synthetic' }));
  on('session.version', () => ({ value: { version: '2.1.314' } }));
  on('session.model', () => ({ value: 'claude-test' }));
  on('fs.stat', () => ({ value: { kind: 'file', size: 1000, isLink: false, mtimeMs: clock.now() } }));
  on('fs.read', ($, e) => ({ value: e.path.endsWith('/bridge.json')
    ? '{"observations":"/synthetic-observations"}' : e.path.endsWith('/refresh.md')
      ? '---\nname: test\n---\nReply with the single word WARM.' : JSON.stringify(observation) }));
  on('fs.write', ($, e) => { writes.push(e); return { value: undefined }; });
  on('command.register', () => ({ value: undefined }));
  on('ui.status', ($, e) => { statuses.push(e); return { value: undefined }; });
  on('ui.invalidate', () => ({ value: undefined }));
  on('ui.open', () => ({ value: { isPlaced: true } }));
  on('session.start', ($, e) => ({ cwd: e.cwd }));
  on('session.end', ($, e) => ({ sessionId: e.sessionId }));
  on('turn.start', ($, e) => ({ turnId: e.turnId }));
  on('turn.complete', ($, e) => ({ text: e.answer }));
  on('turn.step', async function* ($, e) {
    requests += 1;
    observation = { format: 1, session_id: 'synthetic', version: '2.1.314',
      observed_at_ms: clock.now(), model: 'claude-test', main_requests: requests,
      state: 'warm', ttl, subscription,
      expires_at: Math.floor((clock.now() + (ttl === '1h' ? 60 : 5) * MIN) / 1000),
      last_main_cache_read_tokens: 18000, last_main_cache_write_tokens: 1000 };
    return { turnId: e.turnId, index: e.index, answer: 'READY', toolUses: [], stopReason: 'end_turn', usage };
  });
  on('model.fork', ($, e) => { calls.push({ at: clock.now(), prompt: e.prompt }); return { value: reply }; });
  return { clock, calls, statuses, writes };
}

async function start($) {
  await $.session.start({ surface: 'terminal', isInteractive: true, cwd: '/synthetic' });
}

async function finish($) {
  const stream = $.turn.step({ turnId: 't', index: 0, model: 'claude-test', messageCount: 2 });
  let part = await stream.next();
  while (!part.done) part = await stream.next();
  await $.turn.complete({ turnId: 't', answer: 'READY', durationMs: 1000,
    isAborted: false, reason: 'answer', usage });
}

async function turn($) {
  await $.turn.start({ turnId: 't', text: 'synthetic' });
  await finish($);
}

test('public lifecycle, observation and model APIs maintain an idle cache', async ($, on) => {
  const h = harness(on); await start($);
  await h.clock.advance(120 * MIN); expect(h.calls.length).toBe(0);
  await turn($); await h.clock.advance(55 * MIN - 1); expect(h.calls.length).toBe(0);
  await h.clock.advance(1); expect(h.calls.length).toBe(1);
  await h.clock.advance(110 * MIN); expect(h.calls.length).toBe(3);
});

test('main activity postpones refresh and closing stops the timer', async ($, on) => {
  const h = harness(on); await start($); await turn($);
  await h.clock.advance(54 * MIN);
  await $.turn.start({ turnId: 't', text: 'synthetic' });
  await h.clock.advance(65 * MIN); expect(h.calls.length).toBe(0);
  await finish($); await h.clock.advance(55 * MIN); expect(h.calls.length).toBe(1);
  await $.session.end({ reason: 'prompt_input_exit', sessionId: 'synthetic', resume: { id: 'synthetic' } });
  await h.clock.advance(120 * MIN); expect(h.calls.length).toBe(1);
});

test('session commands never rebuild an expired cache', async ($, on) => {
  const h = harness(on); await start($); await turn($);
  await $.command.run({ command: 'neosian-cache', args: 'off' });
  await h.clock.advance(65 * MIN);
  await $.command.run({ command: 'neosian-cache', args: 'on' });
  await h.clock.advance(65 * MIN); expect(h.calls.length).toBe(0);
  await turn($); await h.clock.advance(55 * MIN); expect(h.calls.length).toBe(1);
});

for (const args of [{ ttl: '5m' }, { subscription: false }]) {
  test('does not maintain ' + JSON.stringify(args), async ($, on) => {
    const h = harness(on, args); await start($); await turn($);
    await h.clock.advance(120 * MIN); expect(h.calls.length).toBe(0);
  });
}

test('a cold receipt stops subsequent automatic attempts', async ($, on) => {
  const h = harness(on, { reply: { ...warm, usage: { ...warm.usage,
    cache_read_input_tokens: 1000, cache_creation_input_tokens: 18000 } } });
  await start($); await turn($); await h.clock.advance(180 * MIN);
  expect(h.calls.length).toBe(1);
});

test('the host renders compact status and a details pane without warning notices', async ($, on) => {
  const h = harness(on);
  on('ui.render', ($, e) => $.ui.resolve(e).Text({ children: ['other plugin'] }));
  await start($); await turn($); await h.clock.advance(55 * MIN);
  const band = await $.ui.mount({ plugin: 'neosian-cache', component: 'AbovePrompt',
    surface: 'terminal', requestId: 'band', props: { hasSurvey: false, isWorking: false, bodyColumns: 80 } });
  expect((await band.find({ type: 'Text', text: 'neosian' }))?.text)
    .toBe('neosian · cache ~60m · keep-warm on');
  expect((await band.find({ type: 'Text', text: 'other plugin' }))?.text).toBe('other plugin');
  await band.redraw({ hasSurvey: false, isWorking: false, bodyColumns: 24 });
  expect((await band.find({ type: 'Text', text: 'neosian' }))?.text).toBe('neosian · cache ~60m');
  const pane = await $.ui.mount({ plugin: 'neosian-cache', component: 'Pane',
    surface: 'terminal', requestId: 'neosian-cache-details', props: {} });
  expect((await pane.find({ type: 'Text', text: 'Cache read:' }))?.text).toBe('Cache read: 19000 tokens');
  expect(h.statuses.length).toBe(0);
  await band.unmount(); await pane.unmount();
});
