"""Opt-in paid Live API smoke check with paced audio; not a listening-quality test."""
from pathlib import Path
import argparse
import asyncio
import importlib.util
import json
import sys
import time
import wave

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'work/ivan-jp-pilot/live-source'))
from openai_live import run_openai_live, OpenAILiveError, SUPPORTED_VOICES, VOICE


async def paced_packets(pcm, seconds, *, clock=time.monotonic, sleep=asyncio.sleep):
    """Replay 20 ms packets without burst-catching-up after a delayed send.

    Preserves input audio if scheduling or transport pauses; the probe may take
    longer than the file duration, instead of pretending that burst input was a
    realtime microphone. Production capture is handled by the browser worklet.
    """
    next_at = clock() + .02
    for index in range(seconds * 50):
        await sleep(max(0, next_at - clock()))
        yield pcm[index * 640:(index + 1) * 640].ljust(640, b'\0')
        next_at = max(next_at + .02, clock() + .02)


async def probe(args):
    import numpy as np
    import soxr
    spec = importlib.util.spec_from_file_location('probe_gateway', ROOT / 'work/ivan-jp-pilot/local-call/server.py')
    gateway = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gateway)
    settings = gateway.config()
    key = settings.get('OPENAI_LIVE_API_KEY') or settings.get('OPENAI_API_KEY')
    if not key:
        raise ValueError('No GPT Live key configured. Run configure_openai.py first.')
    with wave.open(str(args.input_wav), 'rb') as recording:
        if recording.getnchannels() != 1 or recording.getsampwidth() != 2:
            raise ValueError('Input must be mono PCM16 WAV.')
        source = np.frombuffer(recording.readframes(recording.getnframes()), dtype='<i2').astype(np.float32) / 32768
        rate = recording.getframerate()
    if rate != 16000:
        source = soxr.resample(source, rate, 16000, quality='HQ')
    pcm = np.clip(np.rint(source * 32768), -32768, 32767).astype('<i2').tobytes()
    if not pcm or len(pcm) > args.seconds * 32000:
        raise ValueError('Input must be nonempty and shorter than the requested duration.')
    ready = asyncio.Event()
    output = bytearray()
    stats = {'model': 'gpt-live-1', 'voice': args.voice, 'sample_rate': 16000,
             'session_started_validated': False, 'input_packets': 0,
             'input_packets_after_first_output': 0, 'nonzero_input_packets_after_first_output': 0,
             'output_packets': 0, 'first_output_ms': None,
             'scope': 'Paced recording plus silence, native Live voice only; not proof of conversational full duplex or voice quality.'}
    started_at = None

    def on_ready():
        nonlocal started_at
        started_at = time.monotonic()
        stats['session_started_validated'] = True
        ready.set()

    def on_audio(token, audio, metadata):
        if metadata['sample_rate'] != 16000 or metadata['output_sequence'] != stats['output_packets']:
            raise ValueError('Unexpected output format or order.')
        if stats['first_output_ms'] is None:
            stats['first_output_ms'] = round((time.monotonic() - started_at) * 1000, 1)
        stats['output_packets'] += 1
        output.extend(audio)

    async def events():
        await ready.wait()
        async for packet in paced_packets(pcm, args.seconds):
            stats['input_packets'] += 1
            if stats['output_packets']:
                stats['input_packets_after_first_output'] += 1
                stats['nonzero_input_packets_after_first_output'] += int(any(packet))
            yield {'kind': 'audio', 'pcm_s16le': packet, 'sample_rate': 16000, 'channels': 1}
        # Explicit application stop: file EOF is not a provider turn boundary.
        yield {'kind': 'stop'}

    async with asyncio.timeout(args.seconds + 20):
        await run_openai_live(api_key=key, persona_instructions='You are a concise voice demo. Respond naturally to the caller.',
                              user_events=events(), on_audio=on_audio, on_cancel=lambda token: None,
                              on_ready=on_ready, language=args.language, voice_name=args.voice,
                              ws_url=settings.get('OPENAI_LIVE_URL') or 'wss://api.openai.com/v1/live/sessions')
    stats['session_closed_confirmed'] = True
    stats['wall_duration_seconds'] = round(time.monotonic() - started_at, 3)
    stats['output_seconds'] = len(output) / 32000
    stats['nonzero_output'] = any(output)
    folder = ROOT / 'runtime/openai-live-probe' / time.strftime('%Y%m%d-%H%M%S')
    folder.mkdir(parents=True, exist_ok=False)
    with wave.open(str(folder / f'{args.voice}-output.wav'), 'wb') as recording:
        recording.setnchannels(1)
        recording.setsampwidth(2)
        recording.setframerate(16000)
        recording.writeframes(output)
    (folder / 'receipt.json').write_text(json.dumps(stats, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'result_directory': str(folder.relative_to(ROOT)), **stats}, ensure_ascii=False))
    return 0 if stats['nonzero_output'] else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-wav', type=Path, required=True)
    parser.add_argument('--seconds', type=int, default=20, choices=range(5, 121), metavar='5..120')
    parser.add_argument('--language', choices=['ja', 'en'], default='ja')
    parser.add_argument('--voice', choices=SUPPORTED_VOICES, default=VOICE)
    args = parser.parse_args()
    try:
        return asyncio.run(probe(args))
    except OpenAILiveError as exc:
        print(str(exc), file=sys.stderr)
    except Exception as exc:
        # No raw transport exception, request, URL or credential output.
        print(f'Probe did not complete ({type(exc).__name__}); check local configuration and input WAV.', file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
