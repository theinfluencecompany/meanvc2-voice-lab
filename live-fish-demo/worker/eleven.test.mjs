import test from 'node:test';
import assert from 'node:assert/strict';
import { createWorker } from './worker.mjs';

const ORIGIN = 'https://demo-voice-lab.zjudn2013.workers.dev';
const ENV = { ELEVENLABS_API_KEY: 'test-eleven-secret', DEMO_ACCESS_TOKEN: 'test-demo-code', FISH_API_KEY: 'test-fish-secret' };
const INPUT = { voiceId: 'roger', locale: 'ja', model: 'eleven_v4_turbo', delivery: 'normal', text: '今日はゆっくり休んでね。' };
function request(body = INPUT, headers = {}, signal, path = '/api/eleven/synthesize') {
  return new Request(`${ORIGIN}${path}`, {
    method: 'POST', headers: { Origin: ORIGIN, Authorization: `Bearer ${ENV.DEMO_ACCESS_TOKEN}`, 'Content-Type': 'application/json', ...headers },
    body: typeof body === 'string' ? body : JSON.stringify(body), signal,
  });
}
function mp3(headers = {}) {
  return new Response(new Uint8Array([0x49, 0x44, 0x33, 4, 0, 0, 0, 0]), { headers: { 'Content-Type': 'audio/mpeg', ...headers } });
}

test('Eleven capability and audition identities are independent of Fish and do not expose credentials', async () => {
  const worker = createWorker();
  const empty = await (await worker.fetch(new Request(`${ORIGIN}/api/config`), {})).json();
  assert.equal(empty.eleven.available, false);
  const config = await (await worker.fetch(new Request(`${ORIGIN}/api/config`), { ELEVENLABS_API_KEY: ENV.ELEVENLABS_API_KEY })).json();
  assert.equal(config.eleven.available, true);
  assert.equal(config.capabilities.tts, false);
  assert.deepEqual(config.eleven.voices.map((voice) => voice.id), ['roger', 'sarah']);
  assert.deepEqual(config.eleven.models.map((model) => model.id), ['eleven_v4_turbo', 'eleven_v4']);
  assert.deepEqual(config.eleven.deliveries.map((delivery) => delivery.id), ['normal', 'whisper']);
  assert.doesNotMatch(JSON.stringify(config), /test-eleven-secret|test-demo-code|test-fish-secret/);
});

test('new text is synthesized with the chosen Eleven voice and engine, with honest full-generation timing', async () => {
  let time = 0;
  const requests = [];
  const worker = createWorker({ now: () => time, fetcher: async (url, init) => {
    requests.push({ url, init, payload: JSON.parse(init.body) }); time += 250;
    return mp3({ 'request-id': 'provider_123' });
  } });
  for (const input of [INPUT, { ...INPUT, voiceId: 'sarah', model: 'eleven_v4', delivery: 'whisper' }]) {
    const response = await worker.fetch(request(input), ENV);
    assert.equal(response.status, 200);
    const result = await response.json();
    const call = requests.at(-1);
    const expectedVoice = input.voiceId === 'roger' ? 'CwhRBWXzGAHq8TQ4Fs17' : 'EXAVITQu4vr4xnSDxMaL';
    assert.equal(call.url, `https://api.elevenlabs.io/v1/text-to-speech/${expectedVoice}?output_format=mp3_44100_128`);
    assert.equal(call.init.headers['xi-api-key'], ENV.ELEVENLABS_API_KEY);
    assert.deepEqual(call.payload, { text: (input.delivery === 'whisper' ? '[whispers] ' : '') + input.text, model_id: input.model, language_code: 'ja' });
    assert.equal(result.voiceId, input.voiceId);
    assert.equal(result.providerVoiceId, expectedVoice);
    assert.equal(result.text, input.text);
    assert.equal(result.locale, input.locale);
    assert.equal(result.delivery, input.delivery);
    assert.equal(result.requestedModel, input.model);
    assert.equal(result.servedModel, null);
    assert.equal(result.providerRequestId, 'provider_123');
    assert.equal(result.source, 'live');
    assert.equal(result.provider, 'elevenlabs');
    assert.deepEqual(result.metrics, { totalMs: 250 });
    assert.match(result.audioDataUrl, /^data:audio\/mpeg;base64,/);
  }
});

test('locale mapping preserves the original Taiwan text and maps the provider language correctly', async () => {
  const calls = [];
  const worker = createWorker({ fetcher: async (_url, init) => { calls.push(JSON.parse(init.body)); return mp3(); } });
  for (const [locale, expected] of [['zh-TW', 'zh'], ['zh-CN', 'zh'], ['en', 'en'], ['ko', 'ko']]) {
    const text = locale === 'zh-TW' ? '今天辛苦了，慢慢來就好。' : 'Take your time.';
    const response = await worker.fetch(request({ ...INPUT, locale, text }), ENV);
    assert.equal(response.status, 200);
    assert.equal(calls.at(-1).language_code, expected);
    assert.equal(calls.at(-1).text, text);
  }
});

test('auth, same-origin and configuration gates run before any paid call', async () => {
  let calls = 0;
  const worker = createWorker({ fetcher: async () => { calls++; return mp3(); } });
  for (const [headers, expected] of [[{ Authorization: '' }, 401], [{ Authorization: 'Bearer wrong' }, 401], [{ Origin: '' }, 403], [{ Origin: 'https://other.invalid' }, 403]]) {
    assert.equal((await worker.fetch(request(INPUT, headers), ENV)).status, expected);
  }
  assert.equal((await worker.fetch(request(), { ELEVENLABS_API_KEY: ENV.ELEVENLABS_API_KEY })).status, 503);
  const unconfigured = await worker.fetch(request(), { DEMO_ACCESS_TOKEN: ENV.DEMO_ACCESS_TOKEN });
  assert.equal(unconfigured.status, 503);
  assert.equal((await unconfigured.json()).error.code, 'eleven_not_configured');
  assert.equal(calls, 0);
});

test('arbitrary IDs, unauthorized tags and oversized payloads cannot reach the provider', async () => {
  let calls = 0;
  const worker = createWorker({ rateLimit: 100, fetcher: async () => { calls++; return mp3(); } });
  for (const patch of [
    { voiceId: 'ivan' }, { voiceId: 'CwhRBWXzGAHq8TQ4Fs17' }, { model: 'arbitrary-model' }, { locale: 'fr' },
    { delivery: 'shout' }, { providerVoiceId: 'other' }, { history: [] },
    { text: '[whispers] injected' }, { text: '<break time="10s" />' }, { text: 'bad\u0000text' },
    { text: '' }, { text: 'a'.repeat(601) },
  ]) assert.equal((await worker.fetch(request({ ...INPUT, ...patch }), ENV)).status, 400);
  assert.equal((await worker.fetch(request('{'), ENV)).status, 400);
  assert.equal((await worker.fetch(request('[]'), ENV)).status, 400);
  assert.equal((await worker.fetch(request(INPUT, { 'Content-Type': 'text/plain' }), ENV)).status, 415);
  assert.equal((await worker.fetch(request({ ...INPUT, text: 'a'.repeat(25 * 1024) }), ENV)).status, 413);
  assert.equal(calls, 0);
  assert.equal((await worker.fetch(request({ ...INPUT, text: 'a'.repeat(600) }), ENV)).status, 200);
  assert.equal(calls, 1);
});

test('Eleven failures never leak provider error bodies or silently call another model', async () => {
  for (const status of [401, 403, 429, 500]) {
    let calls = 0;
    const worker = createWorker({ fetcher: async () => { calls++; return new Response('private-upstream-data test-eleven-secret', { status }); } });
    const response = await worker.fetch(request(), ENV);
    assert.equal(response.status, 502);
    assert.doesNotMatch(await response.text(), /private-upstream|test-eleven-secret/);
    assert.equal(calls, 1);
  }
});

test('invalid, empty or oversized Eleven responses cannot become playable success', async () => {
  for (const make of [
    () => new Response(null),
    () => new Response('{"error":"wrong-format"}', { headers: { 'Content-Type': 'application/json' } }),
    () => new Response(new Uint8Array(3 * 1024 * 1024 + 1), { headers: { 'Content-Type': 'audio/mpeg' } }),
  ]) {
    const worker = createWorker({ fetcher: async () => make() });
    assert.equal((await worker.fetch(request(), ENV)).status, 502);
  }
});

test('Eleven and Fish share the paid request budget', async () => {
  let calls = 0;
  const worker = createWorker({ rateLimit: 1, fetcher: async () => { calls++; return mp3(); } });
  assert.equal((await worker.fetch(request(), ENV)).status, 200);
  const fish = { characterId: 'ivan', locale: 'ja', mode: 'original', text: 'こんにちは。' };
  const limited = await worker.fetch(request(fish, {}, undefined, '/api/synthesize'), ENV);
  assert.equal(limited.status, 429);
  assert.equal(calls, 1);
});

test('Eleven uses the shared concurrency guard and releases it after provider failure', async () => {
  let started, release;
  const invoked = new Promise((resolve) => { started = resolve; });
  const worker = createWorker({ maxConcurrent: 1, fetcher: async () => { started(); return new Promise((resolve) => { release = resolve; }); } });
  const first = worker.fetch(request(), ENV);
  await invoked;
  const fish = { characterId: 'ivan', locale: 'ja', mode: 'original', text: 'こんにちは。' };
  assert.equal((await worker.fetch(request(fish, {}, undefined, '/api/synthesize'), ENV)).status, 429);
  release(new Response('upstream failure', { status: 500 }));
  assert.equal((await first).status, 502);
  const next = worker.fetch(request(), ENV);
  await new Promise((resolve) => setImmediate(resolve));
  release(mp3());
  assert.equal((await next).status, 200);
});

test('aborts cancel Eleven requests and a pre-aborted request never calls the provider', async () => {
  const controller = new AbortController();
  let signal, calls = 0;
  const worker = createWorker({ fetcher: async (_url, init) => {
    calls++; signal = init.signal; controller.abort();
    throw new DOMException('private cancellation details', 'AbortError');
  } });
  const response = await worker.fetch(request(INPUT, {}, controller.signal), ENV);
  assert.equal(signal.aborted, true);
  assert.equal(response.status, 499);
  assert.doesNotMatch(await response.text(), /private/);
  assert.equal((await worker.fetch(request(INPUT, {}, controller.signal), ENV)).status, 499);
  assert.equal(calls, 1);
});

test('provider timeout is explicit and the new endpoint rejects non-POST requests', async () => {
  const worker = createWorker({ fetcher: async () => { throw new DOMException('private timeout details', 'TimeoutError'); } });
  const response = await worker.fetch(request(), ENV);
  assert.equal(response.status, 504);
  assert.equal((await response.json()).error.code, 'provider_timeout');
  const method = await worker.fetch(new Request(`${ORIGIN}/api/eleven/synthesize`), ENV);
  assert.equal(method.status, 405);
  assert.equal(method.headers.get('Allow'), 'POST');
});
