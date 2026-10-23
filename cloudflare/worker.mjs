const MODES = new Set(['gemini_vc_quality', 'gemini_native']);
const CHARACTERS = new Set(['ivan', 'saku', 'koharu', 'love', 'reiji']);
const LANGUAGES = new Set(['ja', 'en']);
const SOURCE_VOICES = new Set(['Schedar', 'Iapetus', 'Kore']);
const MAX_CLIENT_TEXT = 16384;
const MAX_HOST_TEXT = 1024 * 1024;
const IDLE_MS = 90000;
const SESSION_MS = 600000;

function json(data, status = 200) {
  return Response.json(data, { status, headers: { 'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer' } });
}

export function tokenEqual(candidate, expected) {
  if (typeof expected !== 'string' || expected.length < 43 || expected.length > 128) return false;
  if (typeof candidate !== 'string' || candidate.length !== expected.length) return false;
  let diff = 0;
  for (let i = 0; i < expected.length; i++) diff |= candidate.charCodeAt(i) ^ expected.charCodeAt(i);
  return diff === 0;
}

export function authenticate(request, env, host = false) {
  if (host) return tokenEqual(request.headers.get('Authorization')?.replace(/^Bearer /, ''), env.HOST_TOKEN)
    && request.headers.get('Authorization')?.startsWith('Bearer ');
  return typeof env.PUBLIC_ORIGIN === 'string' && env.PUBLIC_ORIGIN.startsWith('https://')
    && request.headers.get('Origin') === env.PUBLIC_ORIGIN
    && tokenEqual(new URL(request.url).searchParams.get('token'), env.INVITE_TOKEN);
}

export function filterCapabilities(data) {
  if (data?.type !== 'capabilities') return data;
  return { ...data, modes: Array.isArray(data.modes) ? data.modes.filter((mode) => MODES.has(mode.id)) : [] };
}

export function validateClientText(raw) {
  if (typeof raw !== 'string' || raw.length > MAX_CLIENT_TEXT) throw new Error('invalid message');
  const value = JSON.parse(raw);
  if (!value || Array.isArray(value) || typeof value !== 'object') throw new Error('invalid message');
  switch (value.type) {
    case 'start':
      if (!MODES.has(value.mode)) throw new Error('invalid mode');
      if (value.character_id !== undefined && !CHARACTERS.has(value.character_id)) throw new Error('invalid character');
      if (value.language !== undefined && !LANGUAGES.has(value.language)) throw new Error('invalid language');
      if (value.source_voice !== undefined && !SOURCE_VOICES.has(value.source_voice)) throw new Error('invalid source voice');
      return JSON.stringify({ type: 'start', mode: value.mode,
        ...(value.character_id === undefined ? {} : { character_id: value.character_id }),
        ...(value.language === undefined ? {} : { language: value.language }),
        ...(value.source_voice === undefined ? {} : { source_voice: value.source_voice }),
      });
    case 'text':
      if (typeof value.text !== 'string' || !value.text.trim() || value.text.length > 4000) throw new Error('invalid text');
      return JSON.stringify({ type: 'text', text: value.text });
    case 'interrupt':
    case 'stop':
      return JSON.stringify({ type: value.type });
    default: throw new Error('invalid message');
  }
}

function encodeBinary(buffer) {
  const bytes = new Uint8Array(buffer);
  let result = '';
  for (const byte of bytes) result += String.fromCharCode(byte);
  return btoa(result);
}

function decodeBinary(base64) {
  const raw = atob(base64);
  return Uint8Array.from(raw, (char) => char.charCodeAt(0)).buffer;
}

function closeSocket(socket, code = 1000, reason = 'Preview closed') {
  try { socket.close(code, reason); } catch { /* Already closed. */ }
}

export class VoiceRelay {
  constructor(state, env) {
    this.env = env;
    this.host = null;
    this.session = null;
    this.timer = null;
  }

  async fetch(request) {
    const path = new URL(request.url).pathname;
    const isHost = path === '/relay/host';
    if (!isHost && path !== '/ws') return json({ error: 'not_found' }, 404);
    if (!authenticate(request, this.env, isHost)) return json({ error: 'invitation_required' }, 403);
    if (request.headers.get('Upgrade')?.toLowerCase() !== 'websocket') return json({ error: 'websocket_required' }, 426);
    if (isHost && this.host) return json({ error: 'host_already_connected' }, 409);
    if (!isHost && !this.host) return json({ error: 'preview_unavailable' }, 503);
    if (!isHost && this.session) return json({ error: 'busy' }, 409);

    const pair = new WebSocketPair();
    const [client, server] = Object.values(pair);
    // Recent Workers runtimes default to Blob; PCM forwarding requires bytes.
    server.binaryType = 'arraybuffer';
    server.accept();
    if (isHost) this.attachHost(server);
    else this.attachClient(server);
    return new Response(null, { status: 101, webSocket: client });
  }

  attachHost(socket) {
    socket.binaryType = 'arraybuffer';
    this.host = socket;
    socket.addEventListener('message', (event) => this.hostMessage(socket, event.data));
    const disconnected = () => {
      if (this.host !== socket) return;
      this.host = null;
      this.finishSession('体验服务已断开，请稍后重连。', 1011, false);
    };
    socket.addEventListener('close', disconnected);
    socket.addEventListener('error', disconnected);
  }

  attachClient(socket) {
    socket.binaryType = 'arraybuffer';
    const now = Date.now();
    const session = { id: crypto.randomUUID(), socket, startedAt: now, lastActivity: now };
    this.session = session;
    socket.addEventListener('message', (event) => this.clientMessage(session, event.data));
    const disconnected = () => {
      if (this.session === session) this.finishSession('', 1000);
    };
    socket.addEventListener('close', disconnected);
    socket.addEventListener('error', disconnected);
    this.scheduleDeadline();
    if (!this.sendHost({ type: 'connect', sessionId: session.id })) this.finishSession('体验服务暂时不可用。', 1011);
  }

  sendHost(message) {
    if (!this.host) return false;
    try { this.host.send(JSON.stringify(message)); return true; } catch { return false; }
  }

  clientMessage(session, raw) {
    if (this.session !== session) return;
    try {
      let message;
      if (typeof raw === 'string') {
        message = { type: 'message', sessionId: session.id, data: validateClientText(raw) };
      } else {
        if (!(raw instanceof ArrayBuffer) || !raw.byteLength || raw.byteLength > 3200 || raw.byteLength % 2) throw new Error('invalid audio');
        message = { type: 'message', sessionId: session.id, binary: encodeBinary(raw) };
      }
      session.lastActivity = Date.now();
      if (!this.sendHost(message)) this.finishSession('体验服务已断开，请稍后重连。', 1011);
    } catch (error) {
      try {
        session.socket.send(JSON.stringify({ type: 'error', message: '请使用当前体验页面和语音模式。', diagnostic: {
          kind: typeof raw, tag: Object.prototype.toString.call(raw), bytes: raw?.byteLength ?? raw?.size ?? null,
          isArrayBuffer: raw instanceof ArrayBuffer, exception: error?.name,
        } }));
      } catch { /* Already disconnected. */ }
      this.finishSession('', 1008);
    }
  }

  hostMessage(socket, raw) {
    if (this.host !== socket) return;
    try {
      if (typeof raw !== 'string' || raw.length > MAX_HOST_TEXT) throw new Error('invalid relay message');
      const envelope = JSON.parse(raw);
      if (envelope.type === 'ping') { socket.send(JSON.stringify({ type: 'pong' })); return; }
      const session = this.session;
      // Late packets from a released session must not reach the next visitor.
      if (!session || envelope.sessionId !== session.id) return;
      if (envelope.type === 'close') { this.finishSession('', 1000, false); return; }
      if (envelope.type !== 'message') throw new Error('invalid relay message');
      if (typeof envelope.data === 'string') {
        const message = filterCapabilities(JSON.parse(envelope.data));
        session.socket.send(JSON.stringify(message));
      } else if (typeof envelope.binary === 'string') {
        session.socket.send(decodeBinary(envelope.binary));
      } else throw new Error('invalid relay message');
      session.lastActivity = Date.now();
    } catch {
      // Do not log messages, query tokens, transcripts or upstream errors.
      this.finishSession('体验连接中断，请重新连接。', 1011);
    }
  }

  scheduleDeadline() {
    clearTimeout(this.timer);
    const session = this.session;
    if (!session) return;
    const delay = Math.max(1, Math.min(session.startedAt + SESSION_MS, session.lastActivity + IDLE_MS) - Date.now());
    this.timer = setTimeout(() => this.checkDeadline(), delay);
    this.timer?.unref?.();
  }

  checkDeadline(now = Date.now()) {
    const session = this.session;
    if (!session) return;
    if (now >= session.startedAt + SESSION_MS) this.finishSession('本次体验已到 10 分钟，请重新连接。', 1000);
    else if (now >= session.lastActivity + IDLE_MS) this.finishSession('连接闲置 90 秒，已释放服务；点击连接即可再试。', 1000);
    else this.scheduleDeadline();
  }

  finishSession(message = '', code = 1000, notifyHost = true) {
    const session = this.session;
    if (!session) return;
    this.session = null;
    clearTimeout(this.timer);
    this.timer = null;
    if (message) {
      try { session.socket.send(JSON.stringify({ type: 'error', message })); } catch { /* Already disconnected. */ }
    }
    if (notifyHost) this.sendHost({ type: 'close', sessionId: session.id, code, reason: 'Preview session ended' });
    closeSocket(session.socket, code, 'Preview session ended');
  }
}

export default {
  async fetch(request, env) {
    const path = new URL(request.url).pathname;
    if (path === '/ws' || path === '/relay/host') {
      const relay = env.VOICE_RELAY.get(env.VOICE_RELAY.idFromName('prelulu-source-voice-preview-v3'));
      return relay.fetch(request);
    }
    if (path === '/ping') return json({ service: 'invited-voice-preview' });
    return env.ASSETS.fetch(request);
  },
};
