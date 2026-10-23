"""Loopback-only microphone gateway for Gemini and GPT Live.

Secrets live in .env.local next to this file, never in browser messages.
MeanVC2 conversion modes require the service on 127.0.0.1:8771.
Native GPT Live audio does not pass through MeanVC2.
"""
from __future__ import annotations
import argparse
import asyncio
import base64
import contextlib
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import uuid
from aiohttp import web, WSMsgType, ClientSession, ClientTimeout

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent / 'live-source'))
from gemini_bridge import HttpVCSink, NativeAudioBridge

VC_URL = 'http://127.0.0.1:8771'
PROFILE = 'ivan-en-v1'
SOURCE_VOICES = ('Schedar', 'Iapetus', 'Kore')
GPT_LIVE_MODEL = 'gpt-live-1'
GPT_LIVE_VOICE = 'echo'
GPT_LIVE_URL = 'wss://api.openai.com/v1/live/sessions'
CONFIG_KEYS = {'GEMINI_API_KEY', 'GOOGLE_API_KEY', 'GEMINI_LIVE_MODEL',
               'OPENAI_LIVE_API_KEY', 'OPENAI_API_KEY', 'OPENAI_LIVE_URL'}
CHARACTERS = {
    'ivan': {'name': 'Ivan Volkov', 'profile': PROFILE, 'reference_language': '英语', 'gemini_voice': 'Schedar'},
    'saku': {'name': '朝霧 朔', 'profile': 'saku-ja-v1', 'reference_language': '日语', 'gemini_voice': 'Schedar'},
    'koharu': {'name': '柚木 こはる', 'profile': 'koharu-ja-v1', 'reference_language': '日语', 'gemini_voice': 'Kore'},
    'love': {'name': '月見 灯', 'profile': 'love-ja-v1', 'reference_language': '日语', 'gemini_voice': 'Kore'},
    'reiji': {'name': '久世 怜司', 'profile': 'reiji-ja-v1', 'reference_language': '日语', 'gemini_voice': 'Schedar'},
}
ACTIVE_KEY = web.AppKey('active', set)
ORIGINS_KEY = web.AppKey('origins', set)


def config():
    values = {}
    path = HERE / '.env.local'
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                if key.strip() in CONFIG_KEYS:
                    values[key.strip()] = value.strip().strip('"').strip("'")
    for key in CONFIG_KEYS:
        if os.environ.get(key): values[key] = os.environ[key]
    return values


def google_ready(values):
    try: sdk = importlib.util.find_spec('google.genai') is not None
    except ModuleNotFoundError: sdk = False
    if not (values.get('GEMINI_API_KEY') or values.get('GOOGLE_API_KEY')):
        return False, '需要 Gemini API Key；填写本地 .env.local'
    key = values.get('GEMINI_API_KEY') or values['GOOGLE_API_KEY']
    receipt_path = HERE / '.key-status.json'
    if receipt_path.exists():
        receipt = json.loads(receipt_path.read_text())
        if (receipt.get('key_sha256') == hashlib.sha256(key.encode()).hexdigest()
                and receipt.get('available') is False):
            return False, receipt['reason']
    if not values.get('GEMINI_LIVE_MODEL'):
        return False, '需要填写当前账号可用的 GEMINI_LIVE_MODEL'
    if not sdk: return False, '本地 Google SDK 尚未安装'
    return True, '已配置，连接时验证模型权限'


def openai_ready(values):
    if not (values.get('OPENAI_LIVE_API_KEY') or values.get('OPENAI_API_KEY')):
        return False, '需要 GPT Live 测试 API Key；运行 configure_openai.py 配置，Gemini Key 不可用于此模式'
    if importlib.util.find_spec('aiohttp') is None:
        return False, '本地 aiohttp 尚未安装；请先运行 setup.py'
    return True, '已配置，连接时验证 GPT-Live-1 权限及 Echo 音色'


async def voice_health(profile=PROFILE):
    async with ClientSession(timeout=ClientTimeout(total=3)) as client:
        async with client.get(VC_URL + '/health') as response:
            response.raise_for_status()
            health = await response.json()
    if not health.get('ready') or profile not in health.get('profiles', {}):
        raise RuntimeError('Selected character voice service unavailable')
    return health


async def capabilities():
    try:
        health = await voice_health()
        vc = True
        quality = 'prefix_quality' in health.get('supported_processing_modes', [])
    except Exception: health, vc, quality = {}, False, False
    settings = config()
    available, reason = google_ready(settings)
    gpt_available, gpt_reason = openai_ready(settings)
    return {'type':'capabilities', 'character':'Ivan Volkov', 'video_available':False, 'languages':['ja','en'],
            'source_voices':list(SOURCE_VOICES),
            'characters': [{'id': key, **{field: value[field] for field in
                ('name', 'reference_language', 'gemini_voice')},
                'available': available and quality and value['profile'] in health.get('profiles', {})}
                for key, value in CHARACTERS.items()],
            'modes':[
                {'id':'gemini_vc_quality','label':'Gemini Live → Ivan · 新实验','available':available and quality,
                 'reason':reason if quality else '需要启动支持新实验的 MeanVC2 本地服务'},
                {'id':'mic_vc','label':'麦克风 → Ivan 实时变声','available':vc,
                 'reason':'仅转换你说的话，不是 AI 回答' if vc else 'MeanVC2 本地服务未启动'},
                {'id':'gemini_vc','label':'Gemini Live 男声日语 → Ivan 变声','available':available and vc,
                 'reason':reason if vc else 'MeanVC2 本地服务未启动'},
                {'id':'gemini_native','label':'Gemini Live 原声（不变声）','available':available,'reason':reason},
                {'id':'gpt_live_native','label':'GPT-Live-1 · Echo 原声（不变声）',
                 'available':gpt_available,'reason':gpt_reason},
            ]}


async def make_sink(processing_mode=None, profile=PROFILE):
    health = await voice_health(profile)
    if processing_mode and processing_mode not in health.get('supported_processing_modes', []):
        raise RuntimeError('Selected conversion mode unavailable; restart the updated voice service')
    binding = {name:health[name] for name in ['model_revision','model_fingerprint']}
    binding['reference_digest'] = health['profiles'][profile]['reference_digest']
    return HttpVCSink(VC_URL, 'local-' + uuid.uuid4().hex, profile, binding, processing_mode=processing_mode)


def measured_vc_factory(emit):
    class MeasuredVCBridge(NativeAudioBridge):
        def _new_turn(self):
            turn = super()._new_turn()
            self.first_source = (turn.token, time.monotonic())
            return turn

        async def _deliver(self, turn, result):
            before = turn.expected_output_sequence
            await super()._deliver(turn, result)
            source = getattr(self, 'first_source', None)
            if before == 0 and turn.expected_output_sequence == 1 and source and source[0] == turn.token:
                emit({'type':'metrics','first_output_ms':round((time.monotonic()-source[1])*1000,1),
                      'label':'首个 Gemini 音频包 → 首个变声音频包（服务端，不含播放缓冲）'})
    return MeasuredVCBridge


class MicConversion:
    """Receives real 16k PCM; one inference worker, cancellation outside its queue."""
    def __init__(self, sink, emit):
        self.sink, self.emit = sink, emit
        self.queue = asyncio.Queue(maxsize=75)
        self.epoch = 0
        self.turn = None
        self.controls = set()
        self.control_error = None

    def feed(self, data):
        self.queue.put_nowait((self.epoch, data, time.monotonic()))

    def interrupt(self):
        self.epoch += 1
        turn = self.turn
        if turn:
            self.emit({'type':'cancel','turn_id':turn['token']})
            if turn.get('identity'):
                task = asyncio.create_task(self.sink.cancel(turn['identity']))
                self.controls.add(task)
                def done(result):
                    self.controls.discard(result)
                    if not result.cancelled() and result.exception(): self.control_error = result.exception()
                task.add_done_callback(done)

    async def run(self):
        pending = b''
        while True:
            epoch, data, received = await self.queue.get()
            if epoch != self.epoch: continue
            if self.control_error: raise RuntimeError('Voice cancellation failed')
            if self.turn is None or self.turn['epoch'] != epoch:
                pending = b''
                turn = {'epoch':epoch,'token':uuid.uuid4().hex,'seq':0,'out_seq':0,
                        'received':received,'first':True}
                self.turn = turn
                turn['identity'] = await self.sink.start(turn['token'])
                if epoch != self.epoch:
                    await self.sink.cancel(turn['identity']); continue
            turn = self.turn
            pending += data
            while len(pending) >= 5120 and epoch == self.epoch:
                block, pending = pending[:5120], pending[5120:]
                result = await self.sink.chunk(turn['identity'], turn['seq'], block)
                turn['seq'] += 1
                if epoch != self.epoch or result.get('discarded'): continue
                if any(result.get(k) != v for k,v in turn['identity'].items()):
                    raise RuntimeError('Mismatched voice response')
                if (result.get('sample_rate') != 16000 or result.get('channels') != 1
                        or result.get('output_sequence') != turn['out_seq']):
                    raise RuntimeError('Invalid voice output format or order')
                raw = base64.b64decode(result.get('pcm_s16le_base64',''), validate=True)
                if len(raw) != result.get('sample_count', -1) * 2:
                    raise RuntimeError('Invalid voice output size')
                if raw:
                    self.emit({'type':'audio','turn_id':turn['token'],'pcm':base64.b64encode(raw).decode(),
                               'sample_rate':16000,'sequence':turn['out_seq']})
                    turn['out_seq'] += 1
                    if turn['first']:
                        turn['first'] = False
                        self.emit({'type':'metrics','first_output_ms':round((time.monotonic()-turn['received'])*1000,1),
                                   'label':'服务端收到首个麦克风包 → 首个变声包（不含播放缓冲）'})

    async def close(self):
        self.interrupt()
        if self.controls: await asyncio.gather(*list(self.controls), return_exceptions=True)
        await self.sink.close()


def persona(character_id='ivan', language='ja'):
    if character_id not in CHARACTERS:
        raise ValueError('Unknown character')
    spoken_language = 'English' if language == 'en' else 'Japanese'
    if character_id == 'reiji':
        character = json.loads((ROOT / 'outputs/meanvc2-share-20261023/characters/reiji/character.json').read_text())
        return (f'You are {CHARACTERS[character_id]["name"]} in a fictional character voice call. '
                f'Keep replies short and conversational. Respond naturally in {spoken_language}.\n' +
                character['background'])
    if character_id == 'love':
        character = json.loads((ROOT / 'outputs/meanvc2-share-20261023/characters/love/public-character.json').read_text())['character']
        return (f'You are {CHARACTERS[character_id]["name"]} in a fictional character voice call. '
                f'Keep replies short and conversational. Respond naturally in {spoken_language}.\n' +
                (character.get('persona') or character.get('background') or ''))
    if character_id != 'ivan':
        brief = json.loads((ROOT / f'outputs/characters-20261020/{character_id}-brief.json').read_text())
        return (f'You are {CHARACTERS[character_id]["name"]} in a fictional character voice call. '
                f'Keep replies short and conversational. Respond naturally in {spoken_language}.\n' + brief['background'])
    data = json.loads((ROOT / 'outputs/ivan-jp-pilot-20261020/ivan-public-character.json').read_text())
    character = data.get('character', data)
    return ('You are Ivan Volkov in a fictional character voice call. Keep replies short and conversational. '
            f'Respond naturally in {spoken_language}.\n' + character.get('persona', 'A reserved, calm man with short, deliberate speech.'))


async def socket(request):
    if request.headers.get('Origin') not in request.app[ORIGINS_KEY]:
        raise web.HTTPForbidden(text='Local test origin required')
    ws = web.WebSocketResponse(max_msg_size=16384, heartbeat=20)
    await ws.prepare(request)
    outgoing = asyncio.Queue(maxsize=75)
    incoming = asyncio.Queue(maxsize=75)
    overflow = asyncio.Event()
    runner = None
    microphone = None
    sink = None
    started = False
    mode = None
    character_id = 'ivan'
    language = 'ja'

    def emit(event):
        try: outgoing.put_nowait(event)
        except asyncio.QueueFull: overflow.set()

    async def sender():
        while True: await ws.send_json(await outgoing.get())

    async def fail_on_overflow():
        await overflow.wait()
        raise RuntimeError('Playback queue exceeded capacity')

    async def user_events():
        while True: yield await incoming.get()

    def on_audio(token, pcm, meta):
        emit({'type':'audio','turn_id':token,'pcm':base64.b64encode(pcm).decode(),
              'sample_rate':meta['sample_rate'],'sequence':meta.get('output_sequence', meta.get('sequence',0))})

    async def receive():
        nonlocal runner, microphone, sink, started, mode, character_id, language
        async for message in ws:
            if message.type == WSMsgType.BINARY:
                if not started: raise ValueError('Start a mode first')
                data = message.data
                # Browser worklet sends 40ms. Bound each message to <=100ms.
                if not data or len(data) % 2 or len(data) > 3200:
                    raise ValueError('Microphone must send 16kHz mono int16, <=100ms per packet')
                if microphone: microphone.feed(data)
                else: incoming.put_nowait({'kind':'audio','pcm_s16le':data,'sample_rate':16000,'channels':1})
            elif message.type == WSMsgType.TEXT:
                event = json.loads(message.data)
                kind = event.get('type')
                if kind == 'start':
                    if started: raise ValueError('Already started')
                    mode = event.get('mode')
                    character_id = event.get('character_id', 'ivan')
                    if not isinstance(character_id, str) or character_id not in CHARACTERS:
                        emit({'type':'error','message':'请选择页面上的角色'})
                        continue
                    character = CHARACTERS[character_id]
                    language = event.get('language', 'ja')
                    if language not in ('ja','en'):
                        emit({'type':'error','message':'请选择日语或英语'})
                        continue
                    gpt_live = mode == 'gpt_live_native'
                    source_voice = event.get('source_voice', GPT_LIVE_VOICE if gpt_live else character['gemini_voice'])
                    if (not isinstance(source_voice, str)
                            or source_voice not in ((GPT_LIVE_VOICE,) if gpt_live else SOURCE_VOICES)):
                        emit({'type':'error','message':'GPT Live 原声模式仅支持 Echo' if gpt_live
                              else '请选择页面上的 Gemini 基础声音'})
                        continue
                    caps = await capabilities()
                    choice = next((item for item in caps['modes'] if item['id'] == mode),None)
                    if not choice or not choice['available']:
                        emit({'type':'error','message':choice['reason'] if choice else '未知模式'})
                        continue
                    if mode == 'gemini_vc_quality' and not next(item['available'] for item in caps['characters'] if item['id'] == character_id):
                        emit({'type':'error','message':'该角色音色暂未就绪，请稍后重试'})
                        continue
                    if request.app[ACTIVE_KEY]:
                        emit({'type':'error','message':'另一个本地通话正在使用模型，请先结束它'})
                        continue
                    request.app[ACTIVE_KEY].add(ws)
                    started = True
                    if mode == 'mic_vc':
                        sink = await make_sink(profile=character['profile'])
                        microphone = MicConversion(sink, emit)
                        runner = asyncio.create_task(microphone.run())
                        emit({'type':'ready','mode':mode,'character_id':character_id,'language':language,'model':'MeanVC2 · 本机 CPU','sample_rate':16000})
                    elif gpt_live:
                        from openai_live import run_openai_live
                        settings = config()
                        runner = asyncio.create_task(run_openai_live(
                            api_key=settings.get('OPENAI_LIVE_API_KEY') or settings['OPENAI_API_KEY'],
                            ws_url=settings.get('OPENAI_LIVE_URL') or GPT_LIVE_URL,
                            model=GPT_LIVE_MODEL, voice_name=GPT_LIVE_VOICE,
                            persona_instructions=persona(character_id, language), language=language,
                            user_events=user_events(), on_audio=on_audio,
                            on_cancel=lambda token:emit({'type':'cancel','turn_id':token}),
                            on_transcript=lambda role,text:emit({'type':'transcript','role':role,'text':text}),
                            on_ready=lambda:emit({'type':'ready','mode':mode,'character_id':character_id,
                                                 'language':language,'source_voice':GPT_LIVE_VOICE,
                                                 'model':GPT_LIVE_MODEL,'sample_rate':16000})))
                    else:
                        from google_session import run_google_live
                        from native_playback import NativePlaybackBridge
                        settings = config()
                        sink = (await make_sink('prefix_quality', character['profile']) if mode == 'gemini_vc_quality'
                                else await make_sink(profile=character['profile']) if mode == 'gemini_vc' else None)
                        runner = asyncio.create_task(run_google_live(
                            api_key=settings.get('GEMINI_API_KEY') or settings['GOOGLE_API_KEY'],
                            model=settings['GEMINI_LIVE_MODEL'],persona_instructions=persona(character_id, language),
                            language=language,
                            voice_name=source_voice,
                            voice_delivery=None if source_voice != 'Kore' else (
                                '発声は標準的で明瞭な成人女性の普通の話し声にしてください。'
                                '自然な中程度の声量と速さで、息漏れ、ささやき、ASMR、過度なかすれ声は避けてください。'
                                'キャラクターの性格にかかわらず、この発声方法を優先してください。'),
                            user_events=user_events(),vc_sink=sink,on_audio=on_audio,
                            on_cancel=lambda token:emit({'type':'cancel','turn_id':token}),
                            on_end=lambda token:emit({'type':'end','turn_id':token}),
                            on_transcript=lambda role,text:emit({'type':'transcript','role':role,'text':text}),
                            on_ready=lambda:emit({'type':'ready','mode':mode,'character_id':character_id,'language':language,'source_voice':source_voice,'model':settings['GEMINI_LIVE_MODEL'],
                                                 'sample_rate':16000 if sink else 24000}),
                            bridge_factory=measured_vc_factory(emit) if sink else NativePlaybackBridge))
                elif kind == 'interrupt':
                    if mode == 'gpt_live_native':
                        emit({'type':'error','message':'GPT Live 原声模式使用连续麦克风，不支持手动打断控制'})
                        continue
                    if microphone: microphone.interrupt()
                    elif started: incoming.put_nowait({'kind':'barge_in'})
                elif kind == 'text':
                    if mode == 'gpt_live_native':
                        emit({'type':'error','message':'GPT Live 原声模式仅支持麦克风输入'})
                        continue
                    if mode not in {'gemini_vc','gemini_vc_quality','gemini_native'} or not started:
                        emit({'type':'error','message':'麦克风变声模式不会生成 AI 回答'}); continue
                    value = event.get('text')
                    if not isinstance(value,str) or not 1 <= len(value) <= 2000: raise ValueError('Invalid text')
                    incoming.put_nowait({'kind':'text','text':value})
                elif kind == 'stop': return
                else: raise ValueError('Unknown control message')
            elif message.type in {WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR}: return

    async def monitor_runner():
        while runner is None: await asyncio.sleep(0.05)
        await runner

    send_task = asyncio.create_task(sender())
    receive_task = asyncio.create_task(receive())
    monitor = asyncio.create_task(monitor_runner())
    pressure = asyncio.create_task(fail_on_overflow())
    emit(await capabilities())
    try:
        done, _ = await asyncio.wait([send_task,receive_task,monitor,pressure],timeout=600,
                                     return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            if task.exception(): raise task.exception()
        if not done: await ws.send_json({'type':'error','message':'本次测试已到 10 分钟，请重新连接'})
    except Exception as error:
        # Never include provider errors/URLs: they can contain credentials.
        print(json.dumps({'event':'local_call_error','class':type(error).__name__}), flush=True)
        with contextlib.suppress(Exception):
            await ws.send_json({'type':'error','message':'连接或音频处理失败。请检查本地服务、模型 ID 与 API 权限。'})
    finally:
        for task in [receive_task,monitor,pressure,runner,send_task]:
            if task: task.cancel()
        await asyncio.gather(*[t for t in [receive_task,monitor,pressure,runner,send_task] if t],return_exceptions=True)
        with contextlib.suppress(Exception):
            if microphone: await microphone.close()
            elif sink: await sink.close()
        request.app[ACTIVE_KEY].discard(ws)
        await ws.close()
    return ws


async def health(request):
    return web.json_response(await capabilities())


def make_app(origins):
    app = web.Application(client_max_size=16384)
    app[ACTIVE_KEY] = set()
    app[ORIGINS_KEY] = set(origins)
    app.router.add_get('/health', health)
    app.router.add_get('/ws', socket)
    return app


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port',type=int,default=8773)
    parser.add_argument('--origin',action='append',default=['http://127.0.0.1:33330','http://localhost:33330'])
    args = parser.parse_args()
    web.run_app(make_app(args.origin),host='127.0.0.1',port=args.port,access_log=None)
