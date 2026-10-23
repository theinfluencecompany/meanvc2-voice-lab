import test from 'node:test';
import assert from 'node:assert/strict';
import { VoiceRelay, authenticate, filterCapabilities, validateClientText } from './worker.mjs';

const ORIGIN = 'https://voice-lab.example';
const env = { PUBLIC_ORIGIN: ORIGIN, INVITE_TOKEN: 'i'.repeat(43), HOST_TOKEN: 'h'.repeat(43) };
const request = (path = '/ws', headers = {}) => new Request(ORIGIN + path, { headers });
class Socket {
  // Cloudflare compatibility dates >= 2026-03-17 follow browser defaults.
  binaryType = 'blob';
  sent = [];
  handlers = {};
  closed = null;
  send(data) { if (this.closed) throw new Error('closed'); this.sent.push(data); }
  close(code, reason) { this.closed = { code, reason }; this.emit('close', {}); }
  addEventListener(type, handler) { (this.handlers[type] ??= []).push(handler); }
  emit(type, data) { for (const handler of this.handlers[type] ?? []) handler(data); }
  receive(data) {
    if (typeof data !== 'string') {
      const bytes = data instanceof ArrayBuffer ? data : data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength);
      data = this.binaryType === 'arraybuffer' ? bytes : new Blob([bytes]);
    }
    this.emit('message', { data });
  }
  json() { return this.sent.map((item) => JSON.parse(item)); }
}
function connected() {
  const relay = new VoiceRelay({}, env);
  const host = new Socket();
  const client = new Socket();
  relay.attachHost(host);
  relay.attachClient(client);
  return { relay, host, client };
}

test('invite requires exact origin and strong configured secret', () => {
  assert.equal(authenticate(request('/ws?token=' + env.INVITE_TOKEN, { Origin: ORIGIN }), env), true);
  assert.equal(authenticate(request('/ws?token=' + env.INVITE_TOKEN, { Origin: ORIGIN + '.evil.test' }), env), false);
  assert.equal(authenticate(request('/ws?token=' + env.INVITE_TOKEN), env), false);
  assert.equal(authenticate(request('/ws?token=' + env.INVITE_TOKEN, { Origin: ORIGIN }), {}), false);
  assert.equal(authenticate(request('/relay/host', { Authorization: 'Bearer ' + env.HOST_TOKEN }), env, true), true);
  assert.equal(authenticate(request('/relay/host', { Authorization: env.HOST_TOKEN }), env, true), false);
});

test('no host and occupied session are refused without upgrading', async () => {
  const relay = new VoiceRelay({}, env);
  const req = request('/ws?token=' + env.INVITE_TOKEN, { Origin: ORIGIN, Upgrade: 'websocket' });
  assert.equal((await relay.fetch(req)).status, 503);
  relay.attachHost(new Socket());
  relay.attachClient(new Socket());
  assert.equal((await relay.fetch(req)).status, 409);
  relay.finishSession();
});

test('only supported modes and bounded dialogue controls are accepted', () => {
  for (const mode of ['gemini_native', 'gemini_vc_quality', 'gpt_live_native']) {
    assert.deepEqual(JSON.parse(validateClientText(JSON.stringify({ type: 'start', mode, apiKey: 'discard' }))), { type: 'start', mode });
  }
  assert.throws(() => validateClientText(JSON.stringify({ type: 'start', mode: 'legacy_stream' })));
  assert.throws(() => validateClientText(JSON.stringify({ type: 'text', text: 'x'.repeat(4001) })));
  assert.throws(() => validateClientText('[]'));
  assert.throws(() => validateClientText(JSON.stringify({ type: 'configure', key: 'not-supported' })));
});

test('text and PCM reach the authenticated host without content changes', () => {
  const { relay, host, client } = connected();
  const sessionId = relay.session.id;
  client.receive(JSON.stringify({ type: 'text', text: 'こんにちは。' }));
  client.receive(Uint8Array.from([1, 2, 3, 4]));
  assert.deepEqual(host.json(), [
    { type: 'connect', sessionId },
    { type: 'message', sessionId, data: JSON.stringify({ type: 'text', text: 'こんにちは。' }) },
    { type: 'message', sessionId, binary: 'AQIDBA==' },
  ]);
  relay.finishSession();
});

test('approved character selections reach the host and custom profile fields are discarded', () => {
  for (const character_id of ['ivan', 'saku', 'koharu', 'love', 'reiji']) {
    for (const mode of ['gemini_vc_quality', 'gemini_native']) {
      const { relay, host, client } = connected();
      const sessionId = relay.session.id;
      client.receive(JSON.stringify({ type: 'start', mode, character_id,
        profile_path: '/tmp/unapproved-profile', reference_audio: 'https://example.com/custom.wav',
      }));
      assert.equal(client.closed, null);
      assert.deepEqual(host.json().at(-1), { type: 'message', sessionId,
        data: JSON.stringify({ type: 'start', mode, character_id }),
      });
      relay.finishSession();
    }
  }
});

test('Japanese and English language choices reach the host with Love selected', () => {
  for (const language of ['ja', 'en']) {
    const { relay, host, client } = connected();
    const sessionId = relay.session.id;
    const start = { type: 'start', mode: 'gemini_vc_quality', character_id: 'love', language };
    client.receive(JSON.stringify(start));
    assert.equal(client.closed, null);
    assert.deepEqual(host.json().at(-1), { type: 'message', sessionId, data: JSON.stringify(start) });
    relay.finishSession();
  }
  for (const language of ['zh', 'en-US', '', null, { prompt: 'override' }]) {
    assert.throws(() => validateClientText(JSON.stringify({ type: 'start', mode: 'gemini_native', language })), /invalid language/);
  }
});

test('selected source voice survives relay and unsupported values are refused', () => {
  for (const source_voice of ['Schedar', 'Iapetus', 'Kore']) {
    const start = { type: 'start', mode: 'gemini_vc_quality', character_id: 'reiji', language: 'ja', source_voice };
    const { relay, host, client } = connected();
    client.receive(JSON.stringify(start));
    assert.deepEqual(JSON.parse(host.json().at(-1).data), start);
    relay.finishSession();
  }
  for (const source_voice of ['echo', '', null, {}, ['Kore']]) {
    assert.throws(() => validateClientText(JSON.stringify({ type: 'start', mode: 'gemini_native', source_voice })), /invalid source voice/);
  }
});

test('Echo is accepted only for GPT Live and preserved across relay', () => {
  const start = { type: 'start', mode: 'gpt_live_native', character_id: 'reiji', language: 'ja', source_voice: 'echo' };
  const { relay, host, client } = connected();
  client.receive(JSON.stringify(start));
  assert.deepEqual(JSON.parse(host.json().at(-1).data), start);
  relay.finishSession();
  assert.throws(() => validateClientText(JSON.stringify({ ...start, source_voice: 'Schedar' })), /invalid source voice/);
  assert.throws(() => validateClientText(JSON.stringify({ ...start, mode: 'gemini_native' })), /invalid source voice/);
});

test('legacy start stays compatible and unknown or path-based character selections are rejected', () => {
  assert.deepEqual(JSON.parse(validateClientText(JSON.stringify({ type: 'start', mode: 'gemini_vc_quality' }))),
    { type: 'start', mode: 'gemini_vc_quality' });
  for (const character_id of ['unknown', '../ivan', '/tmp/profile', '', 'Ivan', null, 1, ['saku']]) {
    assert.throws(() => validateClientText(JSON.stringify({ type: 'start', mode: 'gemini_vc_quality', character_id })), /invalid character/);
  }
  const { relay, host, client } = connected();
  client.receive(JSON.stringify({ type: 'start', mode: 'gemini_vc_quality', character_id: '../profile' }));
  assert.equal(client.closed.code, 1008);
  assert.equal(relay.session, null);
  assert.equal(host.json().some((event) => event.type === 'message'), false);
});

test('continuous 20ms microphone frames retain order between dialogue controls', () => {
  const { relay, host, client } = connected();
  const sessionId = relay.session.id;
  const expected = [{ type: 'connect', sessionId }];
  const control = (value) => {
    const data = JSON.stringify(value);
    client.receive(data);
    expected.push({ type: 'message', sessionId, data });
  };
  control({ type: 'start', mode: 'gemini_vc_quality' });
  for (let sequence = 0; sequence < 50; sequence++) {
    // The browser worklet sends 640-byte PCM16 frames at 16kHz.
    const pcm = new Uint8Array(640).fill(sequence);
    client.receive(pcm);
    expected.push({ type: 'message', sessionId, binary: Buffer.from(pcm).toString('base64') });
    if (sequence === 24) {
      control({ type: 'interrupt' });
      control({ type: 'text', text: '短く答えてください。' });
    }
  }
  assert.equal(client.closed, null);
  assert.equal(relay.session.id, sessionId);
  assert.deepEqual(host.json(), expected);
  relay.finishSession();
});

test('malformed PCM closes and releases capacity', () => {
  const { relay, client, host } = connected();
  client.receive(new ArrayBuffer(3202));
  assert.equal(relay.session, null);
  assert.equal(client.closed.code, 1008);
  assert.equal(host.json().at(-1).type, 'close');
});

test('capabilities omit obsolete modes and upstream audio is preserved', () => {
  const { relay, host, client } = connected();
  const sessionId = relay.session.id;
  const capabilities = { type: 'capabilities', character: 'Ivan', modes: [{ id: 'gemini_native' }, { id: 'legacy_stream' }, { id: 'gemini_vc_quality' }] };
  host.emit('message', { data: JSON.stringify({ type: 'message', sessionId, data: JSON.stringify(capabilities) }) });
  assert.deepEqual(client.json()[0], filterCapabilities(capabilities));
  const audio = { type: 'audio', pcm: 'AQIDBA==', sample_rate: 16000, sequence: 0, turn_id: 'test' };
  host.emit('message', { data: JSON.stringify({ type: 'message', sessionId, data: JSON.stringify(audio) }) });
  assert.deepEqual(client.json().at(-1), audio);
  relay.finishSession();
});

test('browser close releases capacity; old audio never enters new session', () => {
  const { relay, host, client } = connected();
  const oldId = relay.session.id;
  client.close(1000, 'done');
  assert.equal(relay.session, null);
  const next = new Socket();
  relay.attachClient(next);
  host.emit('message', { data: JSON.stringify({ type: 'message', sessionId: oldId, data: JSON.stringify({ type: 'audio' }) }) });
  assert.equal(next.sent.length, 0);
  relay.finishSession();
});

test('host loss closes visitor immediately and releases capacity', () => {
  const { relay, host, client } = connected();
  host.close(1000, 'gone');
  assert.equal(relay.host, null);
  assert.equal(relay.session, null);
  assert.equal(client.closed.code, 1011);
});

test('90-second inactivity and ten-minute maximum both release the session', () => {
  const first = connected();
  first.relay.checkDeadline(first.relay.session.lastActivity + 90000);
  assert.equal(first.relay.session, null);
  const second = connected();
  second.relay.session.lastActivity += 599000;
  second.relay.checkDeadline(second.relay.session.startedAt + 600000);
  assert.equal(second.relay.session, null);
});
