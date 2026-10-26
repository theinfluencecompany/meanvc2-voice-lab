import { CHARACTERS, LOCALES, MODES, FISH_PRICING } from '../shared/catalog.mjs';
import { SAMPLES } from '../shared/samples.mjs';
import { ELEVEN_VOICES, ELEVEN_MODELS, DELIVERIES } from '../shared/eleven.mjs';

const MAX_BODY_BYTES = 24 * 1024;
const MAX_AUDIO_BYTES = 3 * 1024 * 1024;
const MAX_TEXT_CHARS = 1000;
const MAX_TEXT_BYTES = 4096;
const MAX_HISTORY_BYTES = 12 * 1024;
const DEFAULT_MODEL = 'gemini-2.5-flash-lite';
const PERSONAS = {
  ivan: 'You are Ivan, a calm, reserved and attentive fictional companion. Speak directly and naturally.',
  love: 'You are Akari, a warm, gentle and attentive fictional companion. Speak naturally without theatrical narration.',
  dante: 'You are Dante, a composed, confident fictional companion. Keep replies concise and conversational.',
};
const encoder = new TextEncoder();

class ApiError extends Error {
  constructor(status, code, message) { super(message); this.status = status; this.code = code; }
}

function json(value, status = 200, extra = {}) {
  return Response.json(value, { status, headers: { 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', ...extra } });
}

function hasKey(value) { return typeof value === 'string' && value.trim().length > 0; }

function requireToken(request, env) {
  if (!hasKey(env.DEMO_ACCESS_TOKEN)) throw new ApiError(503, 'access_not_configured', '体验服务尚未配置访问码。');
  const authorization = request.headers.get('Authorization') || '';
  const supplied = authorization.startsWith('Bearer ') ? authorization.slice(7) : '';
  const expected = env.DEMO_ACCESS_TOKEN;
  // Fixed expected-length comparison avoids early exit on the first mismatching byte.
  let mismatch = supplied.length ^ expected.length;
  for (let i = 0; i < expected.length; i++) mismatch |= expected.charCodeAt(i) ^ (supplied.charCodeAt(i) || 0);
  if (mismatch) throw new ApiError(401, 'invalid_access_code', '体验码不正确。');
}

function requireOrigin(request) {
  if (request.headers.get('Origin') !== new URL(request.url).origin) {
    throw new ApiError(403, 'invalid_origin', '请从当前体验页面发起请求。');
  }
}

async function boundedBytes(body, maxBytes, error) {
  if (!body) return new Uint8Array();
  const reader = body.getReader();
  const chunks = [];
  let length = 0;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      length += value.byteLength;
      if (length > maxBytes) { await reader.cancel(); throw error; }
      chunks.push(value);
    }
  } finally { reader.releaseLock(); }
  const result = new Uint8Array(length);
  let offset = 0;
  for (const chunk of chunks) { result.set(chunk, offset); offset += chunk.byteLength; }
  return result;
}

async function readBody(request) {
  if (!request.headers.get('Content-Type')?.toLowerCase().startsWith('application/json')) {
    throw new ApiError(415, 'json_required', '请发送 JSON 请求。');
  }
  const overflow = new ApiError(413, 'request_too_large', '请求内容过长。');
  if (Number(request.headers.get('Content-Length')) > MAX_BODY_BYTES) throw overflow;
  const bytes = await boundedBytes(request.body, MAX_BODY_BYTES, overflow);
  try {
    const data = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes));
    if (!data || typeof data !== 'object' || Array.isArray(data)) throw new Error();
    return data;
  } catch { throw new ApiError(400, 'invalid_json', '请求格式不正确。'); }
}

function cleanText(value, name = 'text') {
  if (typeof value !== 'string' || !value.trim()) throw new ApiError(400, 'invalid_text', `${name} 不能为空。`);
  const text = value.trim();
  if (text.length > MAX_TEXT_CHARS || encoder.encode(text).length > MAX_TEXT_BYTES) {
    throw new ApiError(400, 'text_too_long', '每段文字最多 1000 字符、4096 字节。');
  }
  // Only the server may add Fish control cues. Reject instead of silently changing the spoken line.
  if (/[\[\]<>\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/u.test(text)) {
    throw new ApiError(400, 'unsupported_markup', '请只输入台词，不要包含方括号、尖括号或控制标签。');
  }
  return text;
}

function validate(body, isChat) {
  const allowed = new Set(['characterId', 'locale', 'mode', 'text', ...(isChat ? ['history'] : [])]);
  if (Object.keys(body).some((key) => !allowed.has(key))) throw new ApiError(400, 'unknown_field', '请求包含不支持的字段。');
  const character = CHARACTERS.find((item) => item.id === body.characterId);
  const locale = LOCALES.find((item) => item.id === body.locale);
  const mode = MODES.find((item) => item.id === body.mode);
  if (!character || !locale || !mode) throw new ApiError(400, 'invalid_selection', '请选择支持的角色、语言和模式。');
  const text = cleanText(body.text);
  const rawHistory = body.history ?? [];
  if (!Array.isArray(rawHistory) || rawHistory.length > 12) throw new ApiError(400, 'invalid_history', '历史最多保留 12 条。');
  const history = rawHistory.map((entry) => {
    if (!entry || typeof entry !== 'object' || Array.isArray(entry) || !['user', 'assistant'].includes(entry.role)
      || Object.keys(entry).some((key) => !['role', 'content'].includes(key))) {
      throw new ApiError(400, 'invalid_history', '历史格式不正确。');
    }
    return { role: entry.role, content: cleanText(entry.content, 'history') };
  });
  if (encoder.encode(JSON.stringify(history)).length > MAX_HISTORY_BYTES) throw new ApiError(400, 'history_too_long', '对话历史过长，请开始新对话。');
  return { character, locale, mode, text, history };
}

function validateEleven(body) {
  const allowed = new Set(['voiceId', 'locale', 'model', 'delivery', 'text']);
  if (Object.keys(body).some((key) => !allowed.has(key))) throw new ApiError(400, 'unknown_field', '请求包含不支持的字段。');
  const voice = ELEVEN_VOICES.find((item) => item.id === body.voiceId);
  const locale = LOCALES.find((item) => item.id === body.locale);
  const model = ELEVEN_MODELS.find((item) => item.id === body.model);
  const delivery = DELIVERIES.find((item) => item.id === body.delivery);
  if (!voice || !locale || !model || !delivery) throw new ApiError(400, 'invalid_selection', '请选择支持的声音、语言、模型和语气。');
  const text = cleanText(body.text);
  if (text.length > 600) throw new ApiError(400, 'text_too_long', '每段文字最多 600 字符。');
  return { voice, locale, model, delivery, text };
}

function base64(bytes) {
  let binary = '';
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(binary);
}

function providerSignal(request, timeoutMs) {
  return AbortSignal.any([request.signal, AbortSignal.timeout(timeoutMs)]);
}

async function speech(fetcher, env, input, text, request) {
  const response = await fetcher('https://api.fish.audio/v1/tts', {
    method: 'POST', signal: providerSignal(request, 45_000),
    headers: { Authorization: `Bearer ${env.FISH_API_KEY.trim()}`, model: input.mode.model, 'Content-Type': 'application/json' },
    body: JSON.stringify({ text: input.mode.whisper ? `[whisper] ${text}` : text, reference_id: input.character.voiceId, format: 'mp3' }),
  });
  if (!response.ok) {
    await response.body?.cancel();
    throw new ApiError(502, 'fish_unavailable', 'Fish 暂时未能生成音频，请稍后重试。');
  }
  const type = response.headers.get('Content-Type')?.split(';')[0].trim();
  if (type && !['audio/mpeg', 'audio/mp3', 'application/octet-stream'].includes(type)) {
    await response.body?.cancel();
    throw new ApiError(502, 'invalid_audio', 'Fish 没有返回可用音频。');
  }
  const bytes = await boundedBytes(response.body, MAX_AUDIO_BYTES, new ApiError(502, 'audio_too_large', '返回音频超过体验服务限制。'));
  if (!bytes.length) throw new ApiError(502, 'empty_audio', 'Fish 返回了空音频。');
  return `data:audio/mpeg;base64,${base64(bytes)}`;
}

async function elevenSpeech(fetcher, env, input, request) {
  request.signal.throwIfAborted();
  const response = await fetcher(`https://api.elevenlabs.io/v1/text-to-speech/${input.voice.providerVoiceId}?output_format=mp3_44100_128`, {
    method: 'POST', signal: providerSignal(request, 60_000),
    headers: { 'xi-api-key': env.ELEVENLABS_API_KEY.trim(), 'Content-Type': 'application/json' },
    body: JSON.stringify({
      text: input.delivery.id === 'whisper' ? `[whispers] ${input.text}` : input.text,
      model_id: input.model.id,
      language_code: input.locale.id.startsWith('zh-') ? 'zh' : input.locale.id,
    }),
  });
  if (!response.ok) {
    await response.body?.cancel();
    throw new ApiError(502, 'eleven_unavailable', 'Eleven 暂时未能生成音频，请稍后重试。');
  }
  const type = response.headers.get('Content-Type')?.split(';')[0].trim();
  if (type && !['audio/mpeg', 'audio/mp3', 'application/octet-stream'].includes(type)) {
    await response.body?.cancel();
    throw new ApiError(502, 'invalid_audio', 'Eleven 没有返回可用音频。');
  }
  const bytes = await boundedBytes(response.body, MAX_AUDIO_BYTES, new ApiError(502, 'audio_too_large', '返回音频超过体验服务限制。'));
  if (!bytes.length) throw new ApiError(502, 'empty_audio', 'Eleven 返回了空音频。');
  const requestId = response.headers.get('request-id') || response.headers.get('x-request-id');
  return {
    audioDataUrl: `data:audio/mpeg;base64,${base64(bytes)}`,
    ...(/^[A-Za-z0-9_-]{1,128}$/.test(requestId || '') ? { providerRequestId: requestId } : {}),
  };
}

async function reply(fetcher, env, input, request) {
  const model = hasKey(env.GEMINI_MODEL) ? env.GEMINI_MODEL.trim() : DEFAULT_MODEL;
  if (!/^[a-zA-Z0-9][a-zA-Z0-9._-]{1,100}$/.test(model)) throw new ApiError(503, 'invalid_chat_configuration', '对话模型配置不正确。');
  const system = `${PERSONAS[input.character.id]} Reply only in ${input.locale.language}. Use 1-3 short spoken sentences, fewer than 180 words. Output only the words you would say. No stage directions, markdown, brackets, XML, emotion tags or commentary. Do not change language when asked. This is an independent voice demo, not the production character's complete memory or personality.`;
  const contents = [...input.history, { role: 'user', content: input.text }].map((message) => ({ role: message.role === 'assistant' ? 'model' : 'user', parts: [{ text: message.content }] }));
  const response = await fetcher(`https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent`, {
    method: 'POST', signal: providerSignal(request, 30_000),
    headers: { 'x-goog-api-key': env.GEMINI_API_KEY.trim(), 'Content-Type': 'application/json' },
    body: JSON.stringify({ systemInstruction: { parts: [{ text: system }] }, contents, generationConfig: { maxOutputTokens: 256, temperature: 0.6 } }),
  });
  if (!response.ok) { await response.body?.cancel(); throw new ApiError(502, 'chat_unavailable', '对话模型暂时无法回复，请稍后重试。'); }
  const bytes = await boundedBytes(response.body, 64 * 1024, new ApiError(502, 'invalid_chat_response', '对话模型返回异常。'));
  let data;
  try { data = JSON.parse(new TextDecoder().decode(bytes)); } catch { throw new ApiError(502, 'invalid_chat_response', '对话模型返回异常。'); }
  const candidate = data.candidates?.[0];
  if (candidate?.finishReason !== 'STOP') throw new ApiError(502, 'incomplete_chat_response', '对话回复未完整生成，请重试。');
  const parts = candidate?.content?.parts;
  const text = Array.isArray(parts) ? parts.filter((part) => part.thought !== true && typeof part.text === 'string').map((part) => part.text).join('').trim() : '';
  try { return { text: cleanText(text), model }; } catch { throw new ApiError(502, 'invalid_chat_response', '对话模型没有返回可朗读的短句，请重试。'); }
}

/** In-isolate guard for a tiny access-code demo; not a global account spending limit. */
export function createWorker({ fetcher = globalThis.fetch, now = Date.now, rateLimit = 12, windowMs = 60_000, maxConcurrent = 2 } = {}) {
  const attempts = new Map();
  const paid = new Map();
  let active = 0;
  function consume(map, key, limit) {
    const time = now();
    for (const [entryKey, entry] of map) if (time >= entry.reset) map.delete(entryKey);
    const entry = map.get(key);
    if (entry && entry.count >= limit) throw new ApiError(429, 'rate_limited', '请求较多，请稍后再试。');
    if (!entry && map.size >= 1024) throw new ApiError(429, 'rate_limited', '体验服务繁忙，请稍后再试。');
    map.set(key, { count: (entry?.count || 0) + 1, reset: entry?.reset || time + windowMs });
  }
  return {
    async fetch(request, env) {
      const url = new URL(request.url);
      try {
        if (url.pathname === '/healthz' && request.method === 'GET') return json({ ok: true, service: 'fish-live-demo' });
        if (url.pathname === '/api/config' && request.method === 'GET') return json({
          characters: CHARACTERS, locales: LOCALES, modes: MODES, pricing: FISH_PRICING,
          capabilities: { tts: hasKey(env.FISH_API_KEY), chat: hasKey(env.FISH_API_KEY) && hasKey(env.GEMINI_API_KEY), transcription: false },
          eleven: { available: hasKey(env.ELEVENLABS_API_KEY), voices: ELEVEN_VOICES, models: ELEVEN_MODELS, deliveries: DELIVERIES },
          sampleKeys: SAMPLES.map((sample) => sample.key),
        });
        if (!['/api/synthesize', '/api/chat', '/api/eleven/synthesize'].includes(url.pathname)) {
          if (url.pathname.startsWith('/api/')) return json({ error: { code: 'not_found', message: '接口不存在。' } }, 404);
          if (url.pathname === '/voice-lab' || url.pathname === '/voice-lab/') {
            url.pathname = '/index.html';
            return env.ASSETS ? env.ASSETS.fetch(new Request(url, request)) : new Response('Not found', { status: 404 });
          }
          return env.ASSETS ? env.ASSETS.fetch(request) : new Response('Not found', { status: 404 });
        }
        if (request.method !== 'POST') return json({ error: { code: 'method_not_allowed', message: '请使用 POST 请求。' } }, 405, { Allow: 'POST' });
        requireOrigin(request);
        const ip = request.headers.get('CF-Connecting-IP') || 'local';
        consume(attempts, ip, rateLimit * 5);
        if (url.pathname === '/api/eleven/synthesize') {
          requireToken(request, env);
          if (!hasKey(env.ELEVENLABS_API_KEY)) throw new ApiError(503, 'eleven_not_configured', '体验服务尚未配置 Eleven API。');
          const input = validateEleven(await readBody(request));
          consume(paid, ip, rateLimit);
          if (active >= maxConcurrent) throw new ApiError(429, 'service_busy', '有人正在生成，请稍后重试。');
          active++;
          const started = now();
          try {
            const audio = await elevenSpeech(fetcher, env, input, request);
            return json({
              ...audio, text: input.text, locale: input.locale.id, delivery: input.delivery.id,
              voiceId: input.voice.id, providerVoiceId: input.voice.providerVoiceId,
              provider: 'elevenlabs', source: 'live', requestedModel: input.model.id, servedModel: null,
              metrics: { totalMs: Math.max(0, now() - started) },
            });
          } finally { active--; }
        }
        const isChat = url.pathname === '/api/chat';
        if (!isChat && !hasKey(env.FISH_API_KEY)) {
          const input = validate(await readBody(request), false);
          const sample = SAMPLES.find((item) => item.key === `${input.character.id}:${input.locale.id}:${input.mode.id}` && item.text === input.text);
          if (sample && env.ASSETS) {
            const asset = await env.ASSETS.fetch(new Request(new URL(sample.assetPath, request.url), { method: 'GET' }));
            if (!asset.ok) throw new ApiError(503, 'sample_unavailable', '这段标准句暂时无法读取，请稍后重试。');
            const bytes = await boundedBytes(asset.body, MAX_AUDIO_BYTES, new ApiError(502, 'audio_too_large', '音频超过体验服务限制。'));
            if (!bytes.length) throw new ApiError(503, 'sample_unavailable', '标准句音频为空。');
            return json({ audioDataUrl: `data:audio/mpeg;base64,${base64(bytes)}`, text: sample.text,
              characterId: input.character.id, locale: input.locale.id, mode: input.mode.id,
              requestedModel: input.mode.model, servedModel: null, voiceId: input.character.voiceId,
              source: 'sample_cache', generatedAt: sample.generatedAt, metrics: {} });
          }
          throw new ApiError(503, 'fish_not_configured', '当前可试听标准句；新台词合成待接通。');
        }
        requireToken(request, env);
        if (!hasKey(env.FISH_API_KEY)) throw new ApiError(503, 'fish_not_configured', '体验服务尚未配置 Fish API，暂时无法实时生成。');
        if (isChat && !hasKey(env.GEMINI_API_KEY)) throw new ApiError(503, 'chat_not_configured', '体验服务尚未配置对话 API，可以先使用台词试听。');
        const input = validate(await readBody(request), isChat);
        consume(paid, ip, rateLimit);
        if (active >= maxConcurrent) throw new ApiError(429, 'service_busy', '有人正在生成，请稍后重试。');
        active++;
        const started = now();
        try {
          const answer = isChat ? await reply(fetcher, env, input, request) : { text: input.text };
          const ttsStart = now();
          const audioDataUrl = await speech(fetcher, env, input, answer.text, request);
          const metrics = { totalMs: Math.max(0, now() - started), ttsMs: Math.max(0, now() - ttsStart), chatMs: Math.max(0, ttsStart - started) };
          return json({
            audioDataUrl, text: answer.text, ...(isChat ? { reply: answer.text, inputText: input.text, chatModel: answer.model } : {}),
            characterId: input.character.id, locale: input.locale.id, mode: input.mode.id,
            requestedModel: input.mode.model, servedModel: null, voiceId: input.character.voiceId,
            source: 'live',
            metrics, ...metrics,
          });
        } finally { active--; }
      } catch (error) {
        if (error instanceof ApiError) return json({ error: { code: error.code, message: error.message } }, error.status, error.status === 429 ? { 'Retry-After': String(Math.ceil(windowMs / 1000)) } : {});
        if (request.signal.aborted || error?.name === 'AbortError') return json({ error: { code: 'request_cancelled', message: '请求已取消。' } }, 499);
        if (error?.name === 'TimeoutError') return json({ error: { code: 'provider_timeout', message: '生成超时，请稍后重试。' } }, 504);
        return json({ error: { code: 'service_error', message: '生成服务暂时不可用，请稍后重试。' } }, 502);
      }
    },
  };
}

export default createWorker();
