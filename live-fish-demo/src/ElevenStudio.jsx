import { useEffect, useRef, useState } from 'react';
import { LOCALES } from '../shared/catalog.mjs';
import { ELEVEN_VOICES, ELEVEN_MODELS, DELIVERIES } from '../shared/eleven.mjs';

const BEDTIME_LINES = {
  en: "You've done enough for today. Make yourself comfortable. We can leave the rest until tomorrow. Good night.",
  ja: '今日も一日、お疲れさま。今は、ゆっくり休もう。残りは明日にしていいよ。おやすみ。',
  ko: '오늘 하루도 수고했어. 이제 편하게 쉬어. 나머지는 내일 해도 괜찮아. 잘 자.',
  'zh-TW': '今天也辛苦了。現在就好好休息吧，剩下的事情留到明天也沒關係。晚安。',
  'zh-CN': '今天也辛苦了。现在就好好休息吧，剩下的事情留到明天也没关系。晚安。',
};
const MAX_TEXT = 600;
const stopAudio = () => document.querySelectorAll('audio').forEach(player => player.pause());
const playSolo = event => document.querySelectorAll('audio').forEach(player => { if (player !== event.currentTarget) player.pause(); });

function Audition({ result }) {
  const voice = ELEVEN_VOICES.find(item => item.id === result.voiceId);
  const delivery = DELIVERIES.find(item => item.id === result.delivery);
  return <article className="result-card">
    <div className="result-meta"><strong>{delivery?.label}</strong><span>{voice?.name}</span><span className="result-tag">本次生成</span></div>
    <p className="spoken-text" lang={result.locale}>{result.text}</p>
    <audio controls preload="metadata" src={result.audioDataUrl} onPlay={playSolo} aria-label={`${voice?.name} ${delivery?.label} 本次生成音频`} />
    <a className="audio-download" href={result.audioDataUrl} download={`eleven-${result.voiceId}-${result.locale}-${result.delivery}.mp3`}>保存这段语音 ↓</a>
    <details className="technical"><summary>生成信息</summary><dl><div><dt>请求模型</dt><dd>{result.requestedModel}</dd></div><div><dt>完整生成耗时</dt><dd>{Number.isFinite(result.metrics?.totalMs) ? `${(result.metrics.totalMs / 1000).toFixed(2)} 秒` : '未返回'}</dd></div></dl><p>这是完整音频生成耗时，不是通话首音延迟。重播和保存不再次调用生成接口。</p></details>
  </article>;
}

export default function ElevenStudio({ config, token, openAccess }) {
  const [voiceId, setVoiceId] = useState('roger');
  const [locale, setLocale] = useState('ja');
  const [model, setModel] = useState('eleven_v4_turbo');
  const [delivery, setDelivery] = useState('normal');
  const [text, setText] = useState(LOCALES.find(item => item.id === 'ja').sample);
  const [pending, setPending] = useState(null);
  const [results, setResults] = useState([]);
  const [error, setError] = useState('');
  const controllerRef = useRef(null);
  const requestRef = useRef(0);
  const resultRef = useRef(null);
  const available = config?.eleven?.available === true;
  const valid = text.trim().length > 0 && text.length <= MAX_TEXT;
  const language = LOCALES.find(item => item.id === locale);

  useEffect(() => {
    requestRef.current += 1;
    controllerRef.current?.abort();
    controllerRef.current = null;
    setPending(null);
    setResults([]);
    setError('');
    return () => { requestRef.current += 1; controllerRef.current?.abort(); };
  }, [token]);

  function cancel() {
    requestRef.current += 1;
    controllerRef.current?.abort();
    controllerRef.current = null;
    setPending(null);
    stopAudio();
  }

  function change(setter, value) {
    cancel();
    setResults([]);
    setError('');
    setter(value);
  }

  async function generate(compare = false) {
    if (!valid || pending || !available) return;
    if (!token) { openAccess(); return; }
    stopAudio();
    const requestId = ++requestRef.current;
    const controller = new AbortController();
    controllerRef.current = controller;
    const content = text.trim();
    const choices = compare ? ['normal', 'whisper'] : [delivery];
    setResults([]);
    setError('');
    try {
      for (let index = 0; index < choices.length; index++) {
        if (requestId !== requestRef.current) return;
        const chosen = choices[index];
        setPending({ label: DELIVERIES.find(item => item.id === chosen).label, index: index + 1, count: choices.length });
        const response = await fetch('/api/eleven/synthesize', {
          method: 'POST', signal: controller.signal,
          headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
          body: JSON.stringify({ voiceId, locale, model, delivery: chosen, text: content }),
        });
        let data;
        try { data = await response.json(); } catch { throw new Error('服务没有返回有效结果，请稍后重试。'); }
        if (!response.ok) {
          if (response.status === 401) openAccess();
          throw new Error(data.error?.message || '生成未完成，请稍后重试。');
        }
        if (data.provider !== 'elevenlabs' || data.voiceId !== voiceId || data.locale !== locale
          || data.delivery !== chosen || data.requestedModel !== model || data.text !== content
          || typeof data.audioDataUrl !== 'string' || !data.audioDataUrl.startsWith('data:audio/mpeg;base64,')) {
          throw new Error('返回内容与本次选择不一致，未播放这段音频。');
        }
        if (requestId !== requestRef.current) return;
        setResults(items => [...items, { ...data, id: crypto.randomUUID() }]);
      }
      resultRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    } catch (reason) {
      if (requestId === requestRef.current && reason.name !== 'AbortError') setError(reason.message);
    } finally {
      if (requestId === requestRef.current) { setPending(null); controllerRef.current = null; }
    }
  }

  return <section className="eleven-studio" aria-label="Eleven 任意台词与耳语试听">
    <div className="studio-grid eleven-grid">
      <aside className="eleven-intro">
        <span className="eyebrow">ELEVENLABS AUDITION</span>
        <h2>同一声线，<br />听听正常声与耳语。</h2>
        <p>输入你自己的台词，现场合成。保持声音和模型不变，只切换发声方式。</p>
        <div className="experiment-note"><strong>使用 Eleven 官方参考音色</strong><p>Roger 和 Sarah 是独立试听声音，并非 Ivan、灯或 Dante 的克隆。可比较语言与表现力，不能据此判断谁更像原角色。</p></div>
        <div className="experiment-note"><strong>试试「晚安台词」</strong><p>先听一段轻声晚安是否舒服，再决定要不要做成角色聊天里的语音消息。当前台词可以编辑，尚未接入角色回复。</p></div>
      </aside>
      <section className="studio-panel" aria-label="Eleven 生成设置">
        <div className="section-title"><h2>选择声音与语言</h2><span className={`service-status ${available ? 'online' : ''}`} role="status"><i />{available ? '可生成新台词' : '生成服务尚未配置'}</span></div>
        <div className="voice-options" role="group" aria-label="Eleven 参考音色">{ELEVEN_VOICES.map(voice => <button key={voice.id} className={voiceId === voice.id ? 'selected' : ''} aria-pressed={voiceId === voice.id} onClick={() => change(setVoiceId, voice.id)}><strong>{voice.name}</strong><span>{voice.description}</span></button>)}</div>
        <div className="language-tabs" role="group" aria-label="Eleven 发声语言">{LOCALES.map(item => <button key={item.id} className={locale === item.id ? 'selected' : ''} aria-pressed={locale === item.id} onClick={() => { change(setLocale, item.id); setText(item.sample); }}><strong>{item.label}</strong><span>{item.nativeLabel}</span></button>)}</div>
        <div className="experiment-axis"><strong>模型</strong><div className="axis-options" role="group" aria-label="Eleven 模型">{ELEVEN_MODELS.map(item => <button key={item.id} className={model === item.id ? 'selected' : ''} aria-pressed={model === item.id} onClick={() => change(setModel, item.id)}>{item.label}</button>)}</div></div>
        <div className="experiment-axis"><strong>发声方式</strong><div className="axis-options" role="group" aria-label="Eleven 发声方式">{DELIVERIES.map(item => <button key={item.id} className={delivery === item.id ? 'selected' : ''} aria-pressed={delivery === item.id} onClick={() => change(setDelivery, item.id)}>{item.label}</button>)}</div></div>
        <p className="mode-note">耳语使用发声指令，不包含双耳空间音效。跨语言发音和耳语舒适度，需要你实际试听判断。</p>
        <div className="composer">
          <div className="composer-head"><strong>输入原文</strong><div className="preset-actions"><button className="text-button" onClick={() => change(setText, language.sample)}>标准句</button><button className="text-button" onClick={() => change(setText, BEDTIME_LINES[locale])}>晚安台词</button></div></div>
          <label className="sr-only" htmlFor="eleven-text">Eleven 要朗读的原文</label>
          <textarea id="eleven-text" value={text} maxLength={MAX_TEXT} onChange={event => setText(event.target.value)} spellCheck={false} />
          <div className="composer-foot"><span>按输入原文朗读，不自动翻译</span><span>{text.length} / {MAX_TEXT}</span></div>
        </div>
        <div className="action-row"><button className="primary generate-button" disabled={!available || !valid || !!pending} onClick={() => generate()}>{pending ? <><span className="spinner" />{pending.index}/{pending.count} · 正在生成{pending.label}</> : '生成这句'}</button>{pending ? <button className="secondary" onClick={cancel}>取消</button> : <button className="secondary" disabled={!available || !valid} onClick={() => generate(true)}>正常 / 耳语对比</button>}</div>
        <div className="below-actions"><p>对比会生成两段；每次新生成消耗额度，重播免费。</p>{!token && available && <button className="text-button" onClick={openAccess}>填写体验码</button>}</div>
        {error && <div className="error-message" role="alert">{error}</div>}
      </section>
    </div>
    <section ref={resultRef} className="results-section" aria-live="polite" aria-busy={!!pending}>
      <div className="results-heading"><div><span className="eyebrow muted">SAME VOICE · SAME MODEL</span><h2>本次试听</h2></div><span className="results-hint">{language.label} · {ELEVEN_MODELS.find(item => item.id === model)?.label}</span></div>
      {results.length ? <div className="eleven-results">{results.map(result => <Audition key={result.id} result={result} />)}</div> : <div className="empty-result"><div><h3>{pending ? '正在合成你输入的新台词…' : '先听正常声，再听同一句耳语'}</h3><p>生成结果保留到切换设置或离开本页，可直接下载音频。</p></div></div>}
    </section>
  </section>;
}
