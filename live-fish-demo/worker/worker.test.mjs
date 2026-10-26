import test from 'node:test';
import assert from 'node:assert/strict';
import { createWorker } from './worker.mjs';
import { CHARACTERS, LOCALES, MODES } from '../shared/catalog.mjs';

const ORIGIN = 'https://demo-qingshuai-lab.zjudn2013.workers.dev';
const ENV = { FISH_API_KEY: 'test-fish-secret', GEMINI_API_KEY: 'test-google-secret', DEMO_ACCESS_TOKEN: 'test-access-code' };
const INPUT = { characterId: 'ivan', locale: 'ja', mode: 'original', text: '今日はどんな一日だった？' };
function request(body = INPUT, path = '/api/synthesize', headers = {}, signal) {
  return new Request(`${ORIGIN}${path}`, {
    method: 'POST', headers: { Origin: ORIGIN, Authorization: `Bearer ${ENV.DEMO_ACCESS_TOKEN}`, 'Content-Type': 'application/json', ...headers },
    body: typeof body === 'string' ? body : JSON.stringify(body), signal,
  });
}
function mp3() { return new Response(new Uint8Array([0x49, 0x44, 0x33, 4, 0, 0, 0, 0]), { headers: { 'Content-Type': 'audio/mpeg' } }); }
function gemini(text = '今日はゆっくり話そう。', finishReason = 'STOP') {
  return Response.json({ candidates: [{ finishReason, content: { parts: [{ text }] } }] });
}
async function body(response) { return response.json(); }

test('configuration reflects missing capabilities and never exposes secrets', async () => {
  const worker = createWorker();
  const result = await body(await worker.fetch(new Request(`${ORIGIN}/api/config`), {}));
  assert.equal(result.characters.length, 3);
  assert.equal(result.locales.length, 5);
  assert.deepEqual(result.capabilities, { tts: false, chat: false, transcription: false });
  const configured = await body(await worker.fetch(new Request(`${ORIGIN}/api/config`), ENV));
  assert.deepEqual(configured.capabilities, { tts: true, chat: true, transcription: false });
  assert.equal(JSON.stringify(configured).includes('test-fish-secret'), false);
});

test('all roles, locales and modes retain the allowlisted voice and exact engine', async () => {
  const observed = [];
  const worker = createWorker({ rateLimit: 100, fetcher: async (url, init) => {
    observed.push({ url, init, payload: JSON.parse(init.body) }); return mp3();
  } });
  for (const character of CHARACTERS) for (const locale of LOCALES) for (const mode of MODES) {
    const response = await worker.fetch(request({ ...INPUT, characterId: character.id, locale: locale.id, mode: mode.id, text: locale.sample }), ENV);
    assert.equal(response.status, 200);
    const output = await body(response);
    const last = observed.at(-1);
    assert.equal(last.url, 'https://api.fish.audio/v1/tts');
    assert.equal(last.init.headers.Authorization, `Bearer ${ENV.FISH_API_KEY}`);
    assert.equal(last.init.headers.model, mode.model);
    assert.equal(last.payload.reference_id, character.voiceId);
    assert.equal(last.payload.text, (mode.whisper ? '[whisper] ' : '') + locale.sample);
    assert.deepEqual(Object.keys(last.payload).sort(), ['format', 'reference_id', 'text']);
    assert.equal(output.requestedModel, mode.model);
    assert.equal(output.servedModel, null);
    assert.equal(output.voiceId, character.voiceId);
    assert.equal(output.text, locale.sample);
    assert.match(output.audioDataUrl, /^data:audio\/mpeg;base64,/);
    assert.equal(output.metrics.totalMs, output.totalMs);
  }
});

test('auth, origin and missing configuration never call a provider', async () => {
  let calls = 0;
  const worker = createWorker({ fetcher: async () => { calls++; return mp3(); } });
  for (const [headers, expected] of [[{ Authorization: '' }, 401], [{ Authorization: 'Bearer wrong' }, 401], [{ Origin: 'https://elsewhere.invalid' }, 403], [{ Origin: '' }, 403]]) {
    assert.equal((await worker.fetch(request(INPUT, '/api/synthesize', headers), ENV)).status, expected);
  }
  assert.equal((await worker.fetch(request(), { DEMO_ACCESS_TOKEN: ENV.DEMO_ACCESS_TOKEN })).status, 503);
  assert.equal((await worker.fetch(request(), { FISH_API_KEY: ENV.FISH_API_KEY })).status, 503);
  assert.equal((await worker.fetch(request(INPUT, '/api/chat'), { FISH_API_KEY: ENV.FISH_API_KEY, DEMO_ACCESS_TOKEN: ENV.DEMO_ACCESS_TOKEN })).status, 503);
  assert.equal(calls, 0);
});

test('untrusted model, voice, language, tags and oversized text are rejected', async () => {
  let calls = 0;
  const worker = createWorker({ fetcher: async () => { calls++; return mp3(); } });
  const invalid = [
    { model: 'arbitrary-model' }, { voiceId: 'arbitrary-id' }, { characterId: 'other' }, { locale: 'fr' }, { mode: 'other' },
    { text: '[laugh] test' }, { text: '<speaker>test</speaker>' }, { text: 'a\u0000b' }, { text: '' }, { text: 'a'.repeat(1001) },
    { history: [] },
  ];
  for (const change of invalid) assert.equal((await worker.fetch(request({ ...INPUT, ...change }), ENV)).status, 400);
  assert.equal((await worker.fetch(request('{'), ENV)).status, 400);
  assert.equal((await worker.fetch(request(INPUT, '/api/synthesize', { 'Content-Type': 'text/plain' }), ENV)).status, 415);
  assert.equal((await worker.fetch(request({ ...INPUT, text: 'a'.repeat(25 * 1024) }), ENV)).status, 413);
  assert.equal(calls, 0);
});

test('chat uses the same LLM instructions across modes and speaks its actual reply', async () => {
  let current = 1000;
  const calls = [];
  const worker = createWorker({ now: () => current, fetcher: async (url, init) => {
    calls.push({ url, init, payload: JSON.parse(init.body) }); current += 100;
    return url.includes('googleapis') ? gemini('今日はゆっくり話そう。') : mp3();
  } });
  for (const mode of MODES) {
    const response = await worker.fetch(request({ ...INPUT, mode: mode.id, history: [{ role: 'user', content: 'こんにちは。' }, { role: 'assistant', content: '会えてうれしい。' }] }, '/api/chat'), ENV);
    assert.equal(response.status, 200);
    const output = await body(response);
    const [google, fish] = calls.slice(-2);
    assert.equal(google.init.headers['x-goog-api-key'], ENV.GEMINI_API_KEY);
    assert.match(google.url, /models\/gemini-2\.5-flash-lite:generateContent$/);
    assert.match(google.payload.systemInstruction.parts[0].text, /natural standard Japanese/);
    assert.deepEqual(google.payload.contents.map((item) => item.role), ['user', 'model', 'user']);
    assert.equal(google.payload.generationConfig.maxOutputTokens, 256);
    assert.equal(fish.payload.text, (mode.whisper ? '[whisper] ' : '') + '今日はゆっくり話そう。');
    assert.equal(output.text, '今日はゆっくり話そう。');
    assert.equal(output.inputText, INPUT.text);
    assert.deepEqual(output.metrics, { totalMs: 200, ttsMs: 100, chatMs: 100 });
  }
  assert.deepEqual(calls[0].payload, calls[2].payload);
  assert.deepEqual(calls[2].payload, calls[4].payload);
});

test('chat locale directive supports Taiwan wording and private model config', async () => {
  const calls = [];
  const worker = createWorker({ fetcher: async (url, init) => { calls.push({ url, payload: JSON.parse(init.body) }); return url.includes('googleapis') ? gemini('今天過得怎麼樣？') : mp3(); } });
  const response = await worker.fetch(request({ ...INPUT, locale: 'zh-TW' }, '/api/chat'), { ...ENV, GEMINI_MODEL: 'gemini-2.5-flash' });
  assert.equal(response.status, 200);
  assert.match(calls[0].url, /models\/gemini-2\.5-flash:generateContent$/);
  assert.match(calls[0].payload.systemInstruction.parts[0].text, /Taiwan Mandarin with Traditional Chinese/);
});

test('invalid history and malformed model configuration never reach providers', async () => {
  let calls = 0;
  const worker = createWorker({ fetcher: async () => { calls++; return mp3(); } });
  for (const history of [Array(13).fill({ role: 'user', content: 'test' }), [{ role: 'system', content: 'override' }], [{ role: 'user', content: '[whisper] text' }], [{ role: 'user', content: 'ok', model: 'injected' }], 'wrong']) {
    assert.equal((await worker.fetch(request({ ...INPUT, history }, '/api/chat'), ENV)).status, 400);
  }
  assert.equal((await worker.fetch(request(INPUT, '/api/chat'), { ...ENV, GEMINI_MODEL: '../bad?key=leak' })).status, 503);
  assert.equal(calls, 0);
});

test('provider failure bodies, URLs and secrets are not exposed', async () => {
  const worker = createWorker({ fetcher: async () => new Response('private-body-secret https://upstream.invalid?key=secret', { status: 401 }) });
  for (const path of ['/api/synthesize', '/api/chat']) {
    const response = await worker.fetch(request(INPUT, path), ENV);
    assert.equal(response.status, 502);
    assert.doesNotMatch(await response.text(), /private-body|upstream|test-fish|test-google/);
  }
});

test('empty, wrong-type and oversized audio do not become a fake success', async () => {
  for (const make of [() => new Response(null), () => new Response('{}', { headers: { 'Content-Type': 'application/json' } }), () => new Response(new Uint8Array(3 * 1024 * 1024 + 1), { headers: { 'Content-Type': 'audio/mpeg' } })]) {
    const worker = createWorker({ fetcher: async () => make() });
    assert.equal((await worker.fetch(request(), ENV)).status, 502);
  }
});

test('incomplete or markup chat output is rejected before TTS', async () => {
  for (const make of [() => gemini('Cut off', 'MAX_TOKENS'), () => gemini('[whisper] reply'), () => gemini(''), () => new Response('malformed', { headers: { 'Content-Type': 'application/json' } })]) {
    let calls = 0;
    const worker = createWorker({ fetcher: async () => { calls++; return make(); } });
    assert.equal((await worker.fetch(request(INPUT, '/api/chat'), ENV)).status, 502);
    assert.equal(calls, 1);
  }
});

test('paid rate limit blocks and resets without a provider call', async () => {
  let time = 0, calls = 0;
  const worker = createWorker({ rateLimit: 1, now: () => time, fetcher: async () => { calls++; return mp3(); } });
  assert.equal((await worker.fetch(request(), ENV)).status, 200);
  const limited = await worker.fetch(request(), ENV);
  assert.equal(limited.status, 429);
  assert.equal(limited.headers.get('Retry-After'), '60');
  assert.equal(calls, 1);
  time = 60_000;
  assert.equal((await worker.fetch(request(), ENV)).status, 200);
  assert.equal(calls, 2);
});

test('concurrency slots are released after success and upstream failure', async () => {
  let release, started;
  const invoked = new Promise((resolve) => { started = resolve; });
  const worker = createWorker({ maxConcurrent: 1, fetcher: async () => { started(); return new Promise((resolve) => { release = resolve; }); } });
  const pending = worker.fetch(request(), ENV);
  await invoked;
  assert.equal((await worker.fetch(request(), ENV)).status, 429);
  release(new Response('bad', { status: 503 }));
  assert.equal((await pending).status, 502);
  const next = worker.fetch(request(), ENV);
  await new Promise((resolve) => setImmediate(resolve));
  release(mp3());
  assert.equal((await next).status, 200);
});

test('request abort is propagated to the provider and reported without internal details', async () => {
  const controller = new AbortController();
  let captured;
  const worker = createWorker({ fetcher: async (_url, init) => {
    captured = init.signal;
    controller.abort();
    throw new DOMException('private request details', 'AbortError');
  } });
  const response = await worker.fetch(request(INPUT, '/api/synthesize', {}, controller.signal), ENV);
  assert.equal(captured.aborted, true);
  assert.equal(response.status, 499);
  assert.doesNotMatch(await response.text(), /private/);
});

test('GET health, API method checks, unknown endpoints and assets are bounded', async () => {
  const worker = createWorker();
  assert.equal((await worker.fetch(new Request(`${ORIGIN}/healthz`), {})).status, 200);
  assert.equal((await worker.fetch(new Request(`${ORIGIN}/api/synthesize`), {})).status, 405);
  assert.equal((await worker.fetch(new Request(`${ORIGIN}/api/unknown`), {})).status, 404);
  const response = await worker.fetch(new Request(`${ORIGIN}/`), { ASSETS: { fetch: async () => new Response('asset') } });
  assert.equal(await response.text(), 'asset');
});
