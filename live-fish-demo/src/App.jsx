import { useEffect, useRef, useState } from 'react';
import { CHARACTERS, LOCALES, MODES, FISH_PRICING, COMPARISON_URL } from '../shared/catalog.mjs';

const MAX_TEXT = 600;
const localeCodes = { en: 'en-US', ja: 'ja-JP', ko: 'ko-KR', 'zh-TW': 'zh-TW', 'zh-CN': 'zh-CN' };
const Wave = ({ small = false }) => <span className={`wave ${small ? 'wave-small' : ''}`} aria-hidden="true">{[14, 29, 21, 40, 53, 32, 45, 24, 36, 17, 27].map((h, i) => <i key={i} style={{ '--height': `${h}px`, '--delay': `${i * .08}s` }} />)}</span>;
function Icon({ name, ...props }) {
  const shapes = {
    arrow: <path d="M5 12h14m-6-6 6 6-6 6" />,
    spark: <path d="m12 3 2.7 6.3L21 12l-6.3 2.7L12 21l-2.7-6.3L3 12l6.3-2.7L12 3Z" />,
    mic: <><rect x="9" y="3" width="6" height="12" rx="3" /><path d="M5 10v2a7 7 0 0 0 14 0v-2M12 19v3m-4 0h8" /></>,
    check: <path d="m5 12 4 4L19 6" />,
    stop: <rect x="6" y="6" width="12" height="12" rx="2" />,
    compare: <><path d="M7 3v18M17 3v18" /><circle cx="7" cy="9" r="3" /><circle cx="17" cy="15" r="3" /></>,
  };
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}>{shapes[name] || shapes.arrow}</svg>;
}

function takeInvite() {
  return new URLSearchParams(window.location.hash.slice(1)).get('invite') || '';
}

function playSolo(event) {
  document.querySelectorAll('audio').forEach(player => { if (player !== event.currentTarget) player.pause(); });
}

function pauseAudio() {
  document.querySelectorAll('audio').forEach(player => player.pause());
}

function ResultCard({ result, compact = false, autoplay = false }) {
  const mode = MODES.find(item => item.id === result.mode);
  const character = CHARACTERS.find(item => item.id === result.characterId);
  const locale = LOCALES.find(item => item.id === result.locale);
  const cached = result.source === 'sample_cache';
  return <article className={`result-card ${compact ? 'compact' : ''}`}>
    <div className="result-meta"><span className={`mode-dot ${result.mode}`} /><strong>{mode?.label || result.mode}</strong><span>{character?.shortName} · {locale?.label}</span>{!compact && <span className="result-tag">{cached ? '预生成标准句' : '本次生成'}</span>}</div>
    <p className="spoken-text" lang={result.locale}>{result.text}</p>
    <audio controls autoPlay={autoplay} preload="metadata" src={result.audioDataUrl} onPlay={playSolo} aria-label={`${character?.name} ${locale?.label} ${mode?.label} 生成音频`} />
    <details className="technical"><summary>{cached ? '样音信息' : '生成信息'}</summary><dl><div><dt>请求模型</dt><dd>{result.requestedModel || mode?.model}</dd></div>{!cached && <div><dt>完整生成耗时</dt><dd>{Number.isFinite(result.metrics?.totalMs) ? `${(result.metrics.totalMs / 1000).toFixed(2)} 秒` : '未返回'}</dd></div>}</dl><p>{cached ? '这段标准句已使用该角色的 Fish 音色提前生成，本次直接加载试听。' : '耗时为服务端记录的完整生成等待，不含浏览器网络时间，也不代表流式首音延迟。服务端未提供实际执行模型的独立证明。'}</p></details>
  </article>;
}

export default function App() {
  const [config, setConfig] = useState(null);
  const [configError, setConfigError] = useState('');
  const [token, setToken] = useState(takeInvite);
  const [tokenDraft, setTokenDraft] = useState('');
  const [showAccess, setShowAccess] = useState(false);
  const [characterId, setCharacterId] = useState('ivan');
  const [locale, setLocale] = useState('ja');
  const [mode, setMode] = useState('original');
  const [text, setText] = useState(LOCALES.find(item => item.id === 'ja').sample);
  const [history, setHistory] = useState([]);
  const [currentResults, setCurrentResults] = useState([]);
  const [pending, setPending] = useState(null);
  const [error, setError] = useState('');
  const [listening, setListening] = useState(false);
  const [micNote, setMicNote] = useState('');
  const controllerRef = useRef(null);
  const requestRef = useRef(0);
  const recognitionRef = useRef(null);
  const resultsRef = useRef(null);
  const characters = config?.characters?.length ? config.characters : CHARACTERS;
  const locales = config?.locales?.length ? config.locales : LOCALES;
  const modes = config?.modes?.length ? config.modes : MODES;
  const character = characters.find(item => item.id === characterId) || CHARACTERS[0];
  const language = locales.find(item => item.id === locale) || LOCALES[1];
  const ttsAvailable = config?.capabilities?.tts === true;
  const sampleKeys = new Set(config?.sampleKeys || []);
  const isStandardText = text.trim() === language.sample.trim();
  const hasSample = selectedMode => isStandardText && sampleKeys.has(`${characterId}:${locale}:${selectedMode}`);
  const validText = text.trim().length > 0 && text.length <= MAX_TEXT;
  const canGenerate = validText && (ttsAvailable || hasSample(mode));
  const canCompare = validText && (ttsAvailable || modes.every(item => hasSample(item.id)));
  const samplesAvailable = sampleKeys.size > 0;
  const pricing = { ...FISH_PRICING, ...config?.pricing };
  const promotionActive = Date.now() < Date.parse(pricing.dramaPromotionUntil);
  const filteredHistory = history.filter(item => item.characterId === characterId && item.locale === locale);
  const supportsMic = typeof window !== 'undefined' && !!(window.SpeechRecognition || window.webkitSpeechRecognition);

  useEffect(() => {
    if (new URLSearchParams(window.location.hash.slice(1)).has('invite')) window.history.replaceState(null, '', window.location.pathname + window.location.search);
    const controller = new AbortController();
    fetch('/api/config', { signal: controller.signal }).then(async response => {
      if (!response.ok) throw new Error('暂时无法读取体验服务状态');
      return response.json();
    }).then(setConfig).catch(reason => { if (reason.name !== 'AbortError') setConfigError(reason.message); });
    return () => controller.abort();
  }, []);

  useEffect(() => () => {
    controllerRef.current?.abort();
    const recognition = recognitionRef.current;
    recognitionRef.current = null;
    recognition?.abort();
  }, []);

  function cancel() {
    pauseAudio();
    requestRef.current += 1;
    controllerRef.current?.abort();
    controllerRef.current = null;
    setPending(null);
  }

  function stopRecognition() {
    const recognition = recognitionRef.current;
    recognitionRef.current = null;
    recognition?.abort();
    setListening(false);
    setMicNote('');
  }

  function changeContext(next) {
    cancel();
    stopRecognition();
    setError('');
    setCurrentResults([]);
    if (next.characterId) setCharacterId(next.characterId);
    if (next.locale) {
      setLocale(next.locale);
      setText(locales.find(item => item.id === next.locale).sample);
    }
    if (next.mode) setMode(next.mode);
  }

  async function synthesize(endpoint, body, signal) {
    const response = await fetch(endpoint, {
      method: 'POST', signal,
      headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      body: JSON.stringify(body),
    });
    let data;
    try { data = await response.json(); } catch { throw new Error('体验服务暂时没有返回音频，请稍后重试。'); }
    if (!response.ok) {
      if (response.status === 401 || response.status === 403) setShowAccess(true);
      throw new Error(typeof data.error === 'string' ? data.error : data.error?.message || data.message || '生成失败，请稍后重试。');
    }
    if (typeof data.audioDataUrl !== 'string' || !/^data:audio\//.test(data.audioDataUrl)) throw new Error('服务没有返回可播放的音频。');
    if (data.characterId !== body.characterId || data.locale !== body.locale || data.mode !== body.mode) throw new Error('返回的角色或模式与本次请求不一致，音频未采用。');
    return { ...data, id: crypto.randomUUID(), createdAt: Date.now() };
  }

  async function generate(compare = false) {
    if (pending || (compare ? !canCompare : !canGenerate)) return;
    const content = text.trim();
    if (!content || content.length > MAX_TEXT) return;
    pauseAudio();
    stopRecognition();
    const requestId = ++requestRef.current;
    const controller = new AbortController();
    controllerRef.current = controller;
    setError('');
    setCurrentResults([]);
    const requestedModes = compare ? modes : [modes.find(item => item.id === mode)];
    try {
      for (let index = 0; index < requestedModes.length; index += 1) {
        if (requestId !== requestRef.current) return;
        const selectedMode = requestedModes[index];
        setPending({ compare, cached: hasSample(selectedMode.id), label: selectedMode.label, index: index + 1, total: requestedModes.length });
        const result = await synthesize('/api/synthesize', {
          characterId, locale, mode: selectedMode.id, text: content,
        }, controller.signal);
        if (requestId !== requestRef.current) return;
        setCurrentResults(items => [...items, result]);
        setHistory(items => [result, ...items].slice(0, 12));
      }
      resultsRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    } catch (reason) {
      if (reason.name !== 'AbortError' && requestId === requestRef.current) setError(reason.message);
    } finally {
      if (requestId === requestRef.current) { setPending(null); controllerRef.current = null; }
    }
  }

  function useMicrophone() {
    if (recognitionRef.current) { recognitionRef.current.stop(); return; }
    const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!Recognition) return;
    pauseAudio();
    const recognition = new Recognition();
    recognition.lang = localeCodes[locale];
    recognition.interimResults = false;
    recognition.continuous = false;
    recognition.onstart = () => { if (recognitionRef.current !== recognition) return; setListening(true); setMicNote('正在听……结束后可编辑文字，再生成语音。'); };
    recognition.onresult = event => { if (recognitionRef.current !== recognition) return; const value = Array.from(event.results).map(item => item[0].transcript).join(''); setText(value.slice(0, MAX_TEXT)); setMicNote('已转成文字。可先修改，再点击生成。'); };
    recognition.onerror = event => { if (recognitionRef.current !== recognition) return; setMicNote(event.error === 'not-allowed' ? '浏览器未获得麦克风权限，请直接输入文字。' : '语音输入没有完成，请重试或直接输入文字。'); };
    recognition.onend = () => { if (recognitionRef.current !== recognition) return; recognitionRef.current = null; setListening(false); };
    recognitionRef.current = recognition;
    try { recognition.start(); } catch { recognitionRef.current = null; setListening(false); setMicNote('无法启动语音输入，请直接输入文字。'); }
  }

  return <div className="page-shell">
    <header className="topbar"><a className="brand" href="/voice-lab/" aria-label="Prelulu Voice Lab 首页"><span className="brand-symbol">p<span>·</span></span><span>prelulu<span className="brand-divider" />voice lab</span></a><nav><a href={COMPARISON_URL} target="_blank" rel="noreferrer">固定试听对照 <span aria-hidden="true">↗</span></a>{ttsAvailable && <button className="access-trigger" onClick={() => setShowAccess(value => !value)}>{token ? '体验码已填写' : '连接体验码'}</button>}</nav></header>

    {showAccess && ttsAvailable && <section className="access-panel" aria-label="体验服务连接"><div><strong>{token ? '已保存当前体验码' : '输入体验码，连接生成服务'}</strong><p>只在当前页面内使用，刷新后需重新连接；也可从邀请链接打开。</p></div><form onSubmit={event => { event.preventDefault(); if (tokenDraft.trim()) { cancel(); setToken(tokenDraft.trim()); setTokenDraft(''); setShowAccess(false); setError(''); } }}><label className="sr-only" htmlFor="access-token">体验码</label><input id="access-token" type="password" autoComplete="off" value={tokenDraft} onChange={event => setTokenDraft(event.target.value)} placeholder="粘贴体验码" /><button type="submit" className="primary small" disabled={!tokenDraft.trim()}>连接</button></form></section>}

    <main>
      <section className="hero"><div><div className="eyebrow"><span /> FISH VOICE STUDIO</div><h1>同一个声音，<br className="mobile-break" />跨语言开口<span>。</span></h1><p>选一个角色，试听同一句话的不同表现。<br className="mobile-break" />比较不同语言的自然度，也试试耳边轻声。</p></div><div className="hero-wave"><Wave /><span>ONE VOICE · MANY LANGUAGES</span></div></section>

      <div className="studio-grid">
        <aside className="character-panel">
          <div className="section-title"><span className="step">01</span><h2>选择声音</h2><span className="count">3 位角色</span></div>
          <div className="character-tabs" role="group" aria-label="选择角色">{characters.map(item => <button key={item.id} className={characterId === item.id ? 'selected' : ''} onClick={() => changeContext({ characterId: item.id })} aria-pressed={characterId === item.id}><img src={item.image} alt="" /><span>{item.shortName}</span>{characterId === item.id && <Icon name="check" />}</button>)}</div>
          <div className="portrait" style={{ '--character-accent': character.accent }}><img key={character.id} src={character.image} alt={character.name} /><span className="portrait-chip">同一角色 · 固定音色</span><div className="portrait-caption"><span>YOUR VOICE</span><h3>{character.name}</h3><p>{character.subtitle}</p></div></div>
          <div className="reference"><div><span className="reference-icon">♪</span><div><h3>角色原声参考</h3><p>{character.referenceLanguage === 'en' ? '英语' : '日语'}参考录音 · 用来对照音色</p></div></div><audio key={character.id} controls preload="metadata" src={character.referenceAudio} onPlay={playSolo} aria-label={`${character.name} 原声参考`} /></div>
          <p className="character-note">切换语言和模型时，保留这个角色的同一个音色 ID。</p>
        </aside>

        <section className="studio-panel" aria-label="语音生成工作台">
          <div className="section-title"><span className="step">02</span><h2>语言与表现</h2><span className={`service-status ${ttsAvailable || samplesAvailable ? 'online' : ''}`} role="status"><i />{ttsAvailable ? '生成服务已配置' : samplesAvailable ? '标准句可试听' : configError ? '服务暂不可用' : config ? '样音准备中' : '连接服务中'}</span></div>
          <div className="language-tabs" role="group" aria-label="选择发声语言">{locales.map(item => <button key={item.id} className={locale === item.id ? 'selected' : ''} aria-pressed={locale === item.id} onClick={() => changeContext({ locale: item.id })}><strong>{item.label}</strong><span>{item.nativeLabel}</span></button>)}</div>
          <div className="mode-grid" role="group" aria-label="选择发声模式">{modes.map(item => <button key={item.id} className={`mode-card ${item.id} ${mode === item.id ? 'selected' : ''}`} aria-pressed={mode === item.id} onClick={() => changeContext({ mode: item.id })}><div className="mode-top"><span className="mode-icon">{item.id === 'asmr' ? '☾' : item.id === 'optimized' ? '✧' : '◉'}</span><span className="radio-dot" /></div><strong>{item.label}</strong><span className="model-name">{item.model === 's2-pro' ? 'S2 Pro' : item.model === 's2.1-pro' ? 'S2.1 Pro' : 'Drama 3'}</span><span className="price-badge">{item.badge}</span></button>)}</div>
          <p className="mode-note">{mode === 'asmr' ? '轻声耳语实验：使用 Drama 3 耳语指令；不是双耳录音，也不包含 ASMR 音效。' : mode === 'optimized' ? '语言优化是本次试验的候选模式，是否更自然，请用同一句话试听判断。' : '原版配置基线：使用 S2 Pro 直接生成角色语音。'}</p>

          <div className="composer">
            <div className="composer-head"><strong>读原文</strong><button className="text-button sample-button" onClick={() => { setText(language.sample); setError(''); }}><Icon name="spark" />填入标准句</button></div>
            <label className="sr-only" htmlFor="speech-text">要朗读的原文</label>
            <textarea id="speech-text" value={text} maxLength={MAX_TEXT} onChange={event => setText(event.target.value)} placeholder={`输入${language.label}台词，角色会照着读……`} spellCheck={false} />
            <div className="composer-foot"><span>按输入文字朗读，不自动翻译</span><span>{text.length} / {MAX_TEXT}</span></div>
          </div>
          {!ttsAvailable && (config || configError) && <p className="notice">{configError || (samplesAvailable ? '当前可试听标准句；新台词合成待接通。' : '标准句样音正在准备；新台词合成待接通。')} {!isStandardText && <button className="text-button" onClick={() => { setText(language.sample); setError(''); }}>恢复标准句</button>}</p>}
          <div className="action-row"><button className="primary generate-button" onClick={() => generate()} disabled={!canGenerate || !!pending}>{pending ? <><span className="spinner" />{pending.compare ? `${pending.index}/${pending.total} · ` : ''}{pending.cached ? '正在加载' : '正在生成'}{pending.label}</> : <><Icon name="spark" />朗读这句<Icon name="arrow" /></>}</button>{pending ? <button className="secondary" onClick={cancel}><Icon name="stop" />取消</button> : <button className="secondary compare-button" onClick={() => generate(true)} disabled={!canCompare} title="同一段文字，分别试听三个模型"><Icon name="compare" />同句对比</button>}</div>
          <div className="below-actions"><p>同句对比会显示三段音频，方便并排听。</p>{supportsMic && ttsAvailable && <button className={`text-button mic-button ${listening ? 'listening' : ''}`} onClick={useMicrophone} disabled={!!pending}><Icon name={listening ? 'stop' : 'mic'} />{listening ? '结束听写' : '语音输入'}</button>}</div>
          {micNote && <p className="mic-note" role="status">{micNote}</p>}
          {error && <div className="error-message" role="alert">{error}</div>}
        </section>
      </div>

      <section className="results-section" ref={resultsRef} aria-live="polite" aria-busy={!!pending}>
        <div className="results-heading"><div><span className="eyebrow muted">LISTEN & COMPARE</span><h2>{currentResults.length > 1 || pending?.compare ? '同一句话，三种表现' : '听听这一次的声音'}</h2></div><span className="results-hint">{character.shortName} · {language.label}</span></div>
        {currentResults.length ? <div className={`results-grid ${currentResults.length > 1 || pending?.compare ? 'multiple' : ''}`}>{currentResults.map(result => <ResultCard key={result.id} result={result} autoplay={currentResults.length === 1 && !pending?.compare} />)}{pending?.compare && <div className="result-waiting"><span className="spinner" /><p>{pending.cached ? '正在加载' : '正在生成'}{pending.label}…</p></div>}</div> : <div className={`empty-result ${pending ? 'working' : ''}`}><Wave small /><div><h3>{pending ? `${pending.cached ? '正在加载' : '正在生成'}${pending.label}…` : '你的下一句，会是什么声音？'}</h3><p>{pending ? '音频准备好后会显示在这里。你可以随时取消。' : '点击「朗读这句」或「同句对比」，开始试听。'}</p></div></div>}
      </section>

      {filteredHistory.length > 0 && <section className="history-section"><div className="history-heading"><h2>最近试听 <span>{filteredHistory.length}</span></h2><button className="text-button" onClick={() => { setHistory([]); setCurrentResults([]); }}>清空记录</button></div><p>仅展示当前角色与语言，最多保留本页最近 12 次试听；刷新后清空。</p><div className="history-grid">{filteredHistory.map(result => <ResultCard key={result.id} result={result} compact />)}</div></section>}

      <details className="pricing-details"><summary><span>价格与体验说明</span><span aria-hidden="true">＋</span></summary><div><p>S2 Pro / S2.1 Pro 常价均为 ${pricing.s2List}；Drama 3 常价 ${pricing.dramaList}，约 {(pricing.dramaList / pricing.s2List).toFixed(1)} 倍。计费单位：{pricing.unit}。这只比较语音合成价格，不代表整场通话成本。</p>{promotionActive && <p>官方当前列出的 Drama 3 促销价为 ${pricing.dramaPromotion}，截至 {new Date(pricing.dramaPromotionUntil).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', year: 'numeric', month: 'long', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false })}（北京时间）。实际费用以账号账单为准。</p>}<p>这里是 Fish Audio 朗读与试听实验。预生成标准句会明确标注；新台词需语音合成服务可用。不是实时电话或全双工通话，所有模式均未经过 MeanVC2 变声。</p><a href={pricing.url} target="_blank" rel="noreferrer">查看 Fish 官方价格 ↗</a></div></details>
    </main>
    <footer><span><strong>prelulu</strong> voice lab</span><p>让声音留住角色，让语言靠近你。</p><a href={COMPARISON_URL} target="_blank" rel="noreferrer">打开固定试听对照 ↗</a></footer>
  </div>;
}
