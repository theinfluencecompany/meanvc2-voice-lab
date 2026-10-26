export const CHARACTERS = [
  { id: 'ivan', name: 'Ivan Volkov', shortName: 'Ivan', subtitle: '冷静克制 · 英语参考声线', referenceLanguage: 'en', voiceId: '6fb84253b20442fb92298c892e0ff239', image: '/characters/ivan.webp', referenceAudio: '/characters/ivan-reference.mp3', accent: '#7f9db3' },
  { id: 'love', name: '月見 灯', shortName: '灯', subtitle: '温柔亲近 · 日语参考声线', referenceLanguage: 'ja', voiceId: '0089dce5fefb4c6ba9b9f2f0debe1ddc', image: '/characters/love.webp', referenceAudio: '/characters/love-reference.mp3', accent: '#e5a795' },
  { id: 'saku', name: '朝霧 朔', shortName: '朔', subtitle: '轻松随和 · 日语参考声线', referenceLanguage: 'ja', voiceId: 'b34382d843ff4352b5c3f44c0874d085', image: '/characters/saku.webp', referenceAudio: '/characters/saku-reference.mp3', accent: '#aca1cc' },
];

export const LOCALES = [
  { id: 'en', label: 'English', nativeLabel: '英语', language: 'English', sample: "How was your day? It's okay if you don't feel like talking. Take your time. I'm here to listen.", greeting: "What would you like to talk about today?" },
  { id: 'ja', label: '日本語', nativeLabel: '日语', language: 'natural standard Japanese', sample: '今日は、どんな一日だった？無理に話さなくても大丈夫。ゆっくりでいいから、聞かせて。', greeting: '今日はどんな一日だった？' },
  { id: 'ko', label: '한국어', nativeLabel: '韩语', language: 'natural Korean', sample: '오늘 하루는 어땠어? 억지로 말하지 않아도 괜찮아. 천천히 해도 돼. 네 이야기를 들어줄게.', greeting: '오늘 하루는 어땠어?' },
  { id: 'zh-TW', label: '繁體中文', nativeLabel: '台湾', language: 'Taiwan Mandarin with Traditional Chinese characters and natural Taiwan wording', sample: '今天過得怎麼樣？不想說也沒關係，慢慢來，我會在這裡聽你說。', greeting: '今天過得怎麼樣？' },
  { id: 'zh-CN', label: '简体中文', nativeLabel: '普通话', language: 'Mandarin Chinese with Simplified Chinese characters', sample: '今天过得怎么样？不想说也没关系，慢慢来，我会在这里听你说。', greeting: '今天过得怎么样？' },
];

export const MODES = [
  { id: 'original', label: '原版', model: 's2-pro', badge: '配置基线', description: '角色原有音色 · S2 Pro', whisper: false, priceMultiplier: 1 },
  { id: 'optimized', label: '语言优化', model: 's2.1-pro', badge: '同价候选', description: '同一音色 · S2.1 Pro', whisper: false, priceMultiplier: 1 },
  { id: 'asmr', label: 'ASMR · 耳语', model: 'drama-3', badge: '常价约 3.3×', description: '同一音色 · Drama 3 + 耳语指令', whisper: true, priceMultiplier: 50 / 15 },
];

export const FISH_PRICING = {
  unit: 'USD / 百万 UTF-8 输入字节', checkedAt: '2026-10-26',
  s2List: 15, dramaList: 50, dramaPromotion: 10,
  dramaPromotionUntil: '2026-11-12T17:00:00Z',
  url: 'https://docs.fish.audio/developer-guide/models-pricing/pricing-and-rate-limits',
};

export const COMPARISON_URL = 'https://meanvc2-ivan-report.asashiki-qingshuai.workers.dev/?view=fish';
