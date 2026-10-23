// Deterministic Web Audio clock/lifecycle checks, not listening-quality tests.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('./src/playback.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
}).outputText;
const { LocalVoicePlayback } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);

class Context extends EventTarget {
  state = 'running';
  currentTime = 0;
  destination = {};
  nodes = [];
  createBuffer(channels, length, rate) { return { duration: length / rate, copyToChannel() {} }; }
  createBufferSource() {
    const node = {
      buffer: null, onended: null, stopped: false, disconnected: false,
      connect() {}, start(when) { this.when = when; },
      stop() { this.stopped = true; }, disconnect() { this.disconnected = true; },
    };
    this.nodes.push(node);
    return node;
  }
  setState(state) { this.state = state; this.dispatchEvent(new Event('statechange')); }
}
const pcm = (samples) => Buffer.alloc(samples * 2).toString('base64');

test('keeps existing startup scheduling and cancels already scheduled sources', (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const context = new Context();
  const gaps = [];
  const playback = new LocalVoicePlayback(context, () => {}, (gap) => gaps.push(gap));
  t.after(() => playback.dispose());
  playback.append('turn', pcm(640), 16000, 0);
  playback.append('turn', pcm(640), 16000, 1);
  assert.equal(context.nodes[0].when, .2);
  assert.ok(Math.abs(context.nodes[1].when - .24) < 1e-9);
  assert.deepEqual(gaps, []);
  playback.cancel('turn');
  playback.append('turn', pcm(640), 16000, 2);
  assert.equal(context.nodes.length, 2);
  assert.ok(context.nodes.every((node) => node.stopped && node.disconnected));
  assert.equal(playback.playing, false);
});

test('wall time alone does not report playback; suspension clears stale sound', (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const context = new Context();
  let started = 0;
  const errors = [];
  const playback = new LocalVoicePlayback(context, () => started++, () => {}, (error) => errors.push(error));
  t.after(() => playback.dispose());
  playback.append('session', pcm(640), 16000, 0);
  t.mock.timers.tick(200);
  assert.equal(started, 0); // Audio clock has not advanced.
  context.setState('suspended');
  t.mock.timers.tick(10000);
  assert.equal(started, 0);
  assert.equal(playback.playing, false);
  assert.equal(errors.length, 1);
  assert.ok(context.nodes[0].stopped && context.nodes[0].disconnected);
  assert.throws(() => playback.append('session', pcm(640), 16000, 1), /暂停/);
  context.setState('running');
  playback.append('new-session', pcm(640), 16000, 0);
  context.currentTime = .2;
  t.mock.timers.tick(200);
  assert.equal(started, 1);
});

test('bounds both queued duration and tiny-packet node overhead', (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const context = new Context();
  const playback = new LocalVoicePlayback(context, () => {});
  t.after(() => playback.dispose());
  for (let i = 0; i < 3; i++) playback.append('large', pcm(48000), 16000, i);
  assert.throws(() => playback.append('large', pcm(48000), 16000, 3), /12 秒/);
  playback.cancel('large');
  for (let i = 0; i < 4096; i++) playback.append('tiny', pcm(1), 16000, i);
  assert.throws(() => playback.append('tiny', pcm(1), 16000, 4096), /音频包积压/);
  assert.equal(context.nodes.length, 4099);
  playback.dispose();
  assert.ok(context.nodes.every((node) => node.stopped && node.disconnected));
});
