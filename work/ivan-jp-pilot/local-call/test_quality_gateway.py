"""Real local WS/HTTP contract, fake provider/model; never reads API keys.

This checks routing and lifecycle, not synthesis quality or production readiness.
Run using the MeanVC2 venv Python with unittest discovery in this directory.
"""
import asyncio
import base64
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import wave

import numpy as np
import soxr
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'live-source'))
spec = importlib.util.spec_from_file_location('quality_gateway_under_test', HERE / 'server.py')
gateway = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gateway)
import google_session
from gemini_bridge import NativeAudioBridge


def provider_audio(complete=True):
    pcm = (np.sin(np.arange(12000) * 2 * np.pi * 220 / 24000) * 10000).astype('<i2').tobytes()
    return {'serverContent': {'modelTurn': {'parts': [{'inlineData': {
        'mimeType': 'audio/pcm;rate=24000', 'data': base64.b64encode(pcm).decode()}}]},
        'turnComplete': complete}}


class FakeVoice:
    def __init__(self):
        self.supported = ['legacy_stream', 'prefix_quality']
        self.start_ack = 'echo'
        self.format_ack = 'echo'
        self.starts, self.chunks, self.cancels, self.closes = [], [], [], []
        self.sequences = {}
        self.binding = {'model_revision': 'test-revision', 'model_fingerprint': 'test-fingerprint',
                        'reference_digest': 'test-reference'}

    async def health(self, request):
        data = {'ready': True, 'profiles': {character['profile']: {
            'reference_digest': self.binding['reference_digest'] if role == 'ivan' else 'reference-' + role}
            for role, character in gateway.CHARACTERS.items()},
            'model_revision': self.binding['model_revision'],
            'model_fingerprint': self.binding['model_fingerprint']}
        if self.supported is not None:
            data['supported_processing_modes'] = self.supported
        return web.json_response(data)

    async def call(self, request):
        body = await request.json()
        kind = request.match_info['kind']
        if kind == 'start':
            self.starts.append(body)
            self.sequences[body['turn_id']] = 0
            result = {**body, 'generation_id': str(len(self.starts)), 'sample_rate': 16000, 'channels': 1}
            if self.start_ack == 'missing': result.pop('processing_mode', None)
            elif self.start_ack != 'echo': result['processing_mode'] = self.start_ack
            if self.format_ack == 'missing': result.pop('input_sample_format', None)
            elif self.format_ack != 'echo': result['input_sample_format'] = self.format_ack
            return web.json_response(result)
        if kind in {'chunk', 'flush'}:
            if kind == 'chunk': self.chunks.append(body)
            if body.get('sample_format') == 'float32le':
                floats = np.frombuffer(base64.b64decode(body['pcm_f32le_base64']), dtype='<f4')
                raw = np.clip(np.rint(floats * 32768), -32768, 32767).astype('<i2').tobytes()
            else:
                raw = base64.b64decode(body.get('pcm_s16le_base64', ''))
            sequence = self.sequences[body['turn_id']]
            if raw: self.sequences[body['turn_id']] += 1
            return web.json_response({**{k: body[k] for k in ['session_id', 'turn_id', 'generation_id']},
                'protocol_version': 'meanvc2.local.v1', 'sample_rate': 16000, 'channels': 1,
                'output_sequence': sequence, 'sample_count': len(raw) // 2, 'discarded': False,
                'pcm_s16le_base64': base64.b64encode(raw).decode()})
        if kind == 'cancel': self.cancels.append(body)
        if kind == 'close': self.closes.append(body)
        return web.json_response({'discarded': True})


class GatewayContracts(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.voice = FakeVoice()
        voice_app = web.Application()
        voice_app.router.add_get('/health', self.voice.health)
        voice_app.router.add_post('/v1/{kind}', self.voice.call)
        self.voice_server = TestServer(voice_app)
        await self.voice_server.start_server()
        self.provider_calls = []

        async def fake_provider(**kwargs):
            self.provider_calls.append(kwargs)
            bridge = kwargs['bridge_factory'](kwargs['vc_sink'], kwargs['on_audio'], kwargs['on_cancel'],
                                               on_end=kwargs['on_end'])
            bridge.start_worker()
            kwargs['on_ready']()
            try:
                async for event in kwargs['user_events']:
                    if event['kind'] == 'text':
                        bridge.submit(provider_audio(complete=event['text'] != 'cancel-me'))
                        await bridge.drain()
                    elif event['kind'] == 'barge_in':
                        bridge.interrupt(local=True)
                        # Model acknowledgement is essential to resume; no invented boundary.
                        bridge.submit({'serverContent': {'interrupted': True}})
                        await bridge.drain()
            finally:
                await bridge.close()

        self.patches = [
            patch.object(gateway, 'VC_URL', str(self.voice_server.make_url('')).rstrip('/')),
            patch.object(gateway, 'config', lambda: {'GEMINI_API_KEY': 'unit-test-only', 'GEMINI_LIVE_MODEL': 'fake'}),
            patch.object(gateway, 'google_ready', lambda settings: (True, 'test only')),
            patch.object(gateway, 'persona', lambda character_id='ivan', language='ja': 'test persona ' + character_id),
            patch.object(google_session, 'run_google_live', fake_provider),
        ]
        for item in self.patches: item.start()
        self.client = TestClient(TestServer(gateway.make_app(['http://127.0.0.1:33330'])))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        for item in reversed(self.patches): item.stop()
        await self.voice_server.close()

    async def connect(self):
        ws = await self.client.ws_connect('/ws', headers={'Origin': 'http://127.0.0.1:33330'})
        caps = await ws.receive_json(timeout=2)
        self.assertEqual(caps['type'], 'capabilities')
        self.assertEqual(caps['source_voices'], ['Schedar', 'Iapetus', 'Kore'])
        return ws, {item['id']: item for item in caps['modes']}

    async def until(self, ws, kind):
        seen = []
        async with asyncio.timeout(3):
            while True:
                event = await ws.receive_json()
                seen.append(event)
                if event['type'] == kind: return event, seen
                if event['type'] == 'error': self.fail(event['message'])

    async def start(self, ws, mode):
        await ws.send_json({'type': 'start', 'mode': mode})
        ready, _ = await self.until(ws, 'ready')
        self.assertEqual(ready['mode'], mode)
        if mode != 'mic_vc':
            self.assertEqual(ready['source_voice'], gateway.CHARACTERS['ivan']['gemini_voice'])

    async def complete_turn(self, ws, text):
        await ws.send_json({'type': 'text', 'text': text})
        end, seen = await self.until(ws, 'end')
        audio = [event for event in seen if event['type'] == 'audio']
        self.assertTrue(audio)
        self.assertTrue(all(e['turn_id'] == end['turn_id'] for e in audio))
        self.assertEqual([e['sequence'] for e in audio], list(range(len(audio))))
        self.assertTrue(all(e['sample_rate'] == 16000 for e in audio))
        return end['turn_id'], audio

    async def test_unsupported_quality_cannot_silently_use_legacy(self):
        for supported in [None, ['legacy_stream']]:
            self.voice.supported = supported
            ws, caps = await self.connect()
            self.assertFalse(caps['gemini_vc_quality']['available'])
            self.assertTrue(caps['gemini_vc']['available'])
            await ws.send_json({'type': 'start', 'mode': 'gemini_vc_quality'})
            error, _ = await self.until(ws, 'error')
            self.assertTrue(error['message'])
            self.assertFalse(self.provider_calls)
            self.assertFalse(self.voice.starts)
            await ws.close()

    async def test_quality_two_turns_retain_mode_and_reset_output_sequence(self):
        ws, caps = await self.connect()
        self.assertTrue(caps['gemini_vc_quality']['available'])
        await self.start(ws, 'gemini_vc_quality')
        first, _ = await self.complete_turn(ws, 'first')
        second, _ = await self.complete_turn(ws, 'second')
        self.assertNotEqual(first, second)
        self.assertEqual(len(self.voice.starts), 2)
        self.assertTrue(all(s['processing_mode'] == 'prefix_quality' for s in self.voice.starts))
        self.assertTrue(all(s['input_sample_format'] == 'float32le' for s in self.voice.starts))
        self.assertTrue(all(s['sample_format'] == 'float32le' for s in self.voice.chunks))
        await ws.close()

    async def test_selected_character_binds_reference_persona_language_and_source_voice(self):
        for role, voice in [('saku', 'Schedar'), ('koharu', 'Kore'), ('love', 'Kore'), ('reiji', 'Schedar')]:
            ws, _ = await self.connect()
            await ws.send_json({'type':'start', 'mode':'gemini_vc_quality', 'character_id':role, 'language':'en'})
            ready, _ = await self.until(ws, 'ready')
            self.assertEqual(ready['character_id'], role)
            self.assertEqual(ready['language'], 'en')
            self.assertEqual(ready['source_voice'], voice)
            await self.complete_turn(ws, 'hello')
            binding = self.voice.starts[-1]
            self.assertEqual(binding['conversion_profile_id'], gateway.CHARACTERS[role]['profile'])
            self.assertEqual(binding['reference_digest'], 'reference-' + role)
            provider = self.provider_calls[-1]
            self.assertEqual(provider['voice_name'], voice)
            self.assertEqual(provider['language'], 'en')
            self.assertEqual(provider['persona_instructions'], 'test persona ' + role)
            await ws.close()
            await asyncio.sleep(.02)

    async def test_source_voice_selection_applies_to_every_character_and_keeps_reference(self):
        for role, character in gateway.CHARACTERS.items():
            for voice in ['Schedar', 'Iapetus', 'Kore']:
                with self.subTest(role=role, voice=voice):
                    ws, _ = await self.connect()
                    await ws.send_json({'type':'start', 'mode':'gemini_vc_quality',
                        'character_id':role, 'language':'ja', 'source_voice':voice})
                    ready, _ = await self.until(ws, 'ready')
                    self.assertEqual(ready['source_voice'], voice)
                    self.assertEqual(ready['character_id'], role)
                    provider = self.provider_calls[-1]
                    self.assertEqual(provider['voice_name'], voice)
                    if voice == 'Kore':
                        self.assertIn('成人女性', provider['voice_delivery'])
                    else:
                        self.assertIsNone(provider['voice_delivery'])
                    await self.complete_turn(ws, 'sample')
                    self.assertEqual(self.voice.starts[-1]['conversion_profile_id'], character['profile'])
                    await ws.close()
                    await asyncio.sleep(.02)

    async def test_native_mode_receives_selected_source_voice(self):
        ws, _ = await self.connect()
        await ws.send_json({'type':'start', 'mode':'gemini_native', 'source_voice':'Iapetus'})
        ready, _ = await self.until(ws, 'ready')
        self.assertEqual(ready['source_voice'], 'Iapetus')
        self.assertEqual(self.provider_calls[-1]['voice_name'], 'Iapetus')
        self.assertIsNone(self.provider_calls[-1]['vc_sink'])
        await ws.close()

    async def test_unknown_source_voice_does_not_start_provider_or_claim_session(self):
        ws, _ = await self.connect()
        for voice in ['echo', '', None, ['Schedar'], {'voice_name':'Schedar'}]:
            await ws.send_json({'type':'start', 'mode':'gemini_vc_quality', 'source_voice':voice})
            error, _ = await self.until(ws, 'error')
            self.assertEqual(error['message'], '请选择页面上的 Gemini 基础声音')
            self.assertFalse(self.provider_calls)
            self.assertFalse(self.voice.starts)
            self.assertFalse(self.client.server.app[gateway.ACTIVE_KEY])
        # A rejected selection leaves this connection available for a valid start.
        await self.start(ws, 'gemini_vc_quality')
        self.assertEqual(len(self.provider_calls), 1)
        await ws.close()

    async def test_unknown_character_and_language_do_not_start_provider(self):
        ws, _ = await self.connect()
        for selection in [{'character_id':'../other'}, {'character_id':'saku','language':'xx'}]:
            await ws.send_json({'type':'start','mode':'gemini_vc_quality', **selection})
            error, _ = await self.until(ws, 'error')
            self.assertTrue(error['message'])
            self.assertFalse(self.provider_calls)
        await ws.close()

    async def test_quality_interrupt_cancels_old_turn_then_new_turn_works(self):
        ws, _ = await self.connect()
        await self.start(ws, 'gemini_vc_quality')
        await ws.send_json({'type': 'text', 'text': 'cancel-me'})
        first, _ = await self.until(ws, 'audio')
        await ws.send_json({'type': 'interrupt'})
        cancel, _ = await self.until(ws, 'cancel')
        self.assertEqual(cancel['turn_id'], first['turn_id'])
        next_token, audio = await self.complete_turn(ws, 'after interruption')
        self.assertNotEqual(next_token, first['turn_id'])
        self.assertTrue(all(e['turn_id'] != first['turn_id'] for e in audio))
        self.assertEqual([s['turn_id'] for s in self.voice.cancels], [first['turn_id']])
        self.assertTrue(all(s['processing_mode'] == 'prefix_quality' for s in self.voice.starts))
        await ws.close()

    async def test_quality_rejects_missing_or_wrong_start_ack_without_audio(self):
        for ack in ['missing', 'legacy_stream']:
            self.voice.start_ack = ack
            ws, _ = await self.connect()
            await self.start(ws, 'gemini_vc_quality')
            await ws.send_json({'type': 'text', 'text': 'must not synthesize'})
            _, seen = await self.until(ws, 'error')
            self.assertFalse(any(e['type'] == 'audio' for e in seen))
            self.assertFalse(self.voice.chunks)
            self.assertTrue(self.voice.closes)
            await ws.close()

    async def test_existing_vc_mode_does_not_select_new_algorithm(self):
        self.voice.supported = None
        ws, _ = await self.connect()
        await self.start(ws, 'gemini_vc')
        await self.complete_turn(ws, 'legacy')
        self.assertEqual(len(self.voice.starts), 1)
        self.assertNotIn('processing_mode', self.voice.starts[0])
        self.assertNotIn('input_sample_format', self.voice.starts[0])
        self.assertTrue(all('pcm_s16le_base64' in s and 'pcm_f32le_base64' not in s for s in self.voice.chunks))
        await ws.close()

    async def test_quality_rejects_missing_or_wrong_input_format_ack_without_audio(self):
        for ack in ['missing', 'pcm_s16le']:
            self.voice.format_ack = ack
            ws, _ = await self.connect()
            await self.start(ws, 'gemini_vc_quality')
            await ws.send_json({'type': 'text', 'text': 'must not synthesize'})
            _, seen = await self.until(ws, 'error')
            self.assertFalse(any(e['type'] == 'audio' for e in seen))
            self.assertFalse(self.voice.chunks)
            self.assertTrue(self.voice.closes)
            await ws.close()

    async def test_recorded_accepted_source_preserves_float_resampling_across_packet_sizes(self):
        source = HERE.parents[2] / 'outputs/local-realtime-20261022/gemini_native-live.wav'
        with wave.open(str(source), 'rb') as recorded:
            self.assertEqual((recorded.getframerate(), recorded.getnchannels(), recorded.getsampwidth()), (24000, 1, 2))
            source_pcm = recorded.readframes(recorded.getnframes())
        source_float = np.frombuffer(source_pcm, dtype='<i2').astype(np.float32) / 32767
        expected = soxr.resample(source_float, 24000, 16000, quality='HQ')
        received = []
        for packet_samples in [307, 3600, len(source_pcm) // 2]:
            before = len(self.voice.chunks)
            sink = await gateway.make_sink(processing_mode='prefix_quality')
            bridge = NativeAudioBridge(sink, lambda *args: None, lambda token: None)
            bridge.start_worker()
            try:
                for offset in range(0, len(source_pcm), packet_samples * 2):
                    pcm = source_pcm[offset:offset + packet_samples * 2]
                    bridge.submit({'serverContent': {'modelTurn': {'parts': [{'inlineData': {
                        'mimeType': 'audio/pcm;rate=24000', 'data': base64.b64encode(pcm).decode()}}]}}})
                    await bridge.drain()
                bridge.submit({'serverContent': {'turnComplete': True}})
                await bridge.drain()
            finally:
                await bridge.close()
            chunks = self.voice.chunks[before:]
            self.assertTrue(chunks)
            self.assertEqual([chunk['sequence'] for chunk in chunks], list(range(len(chunks))))
            self.assertTrue(all(chunk.get('sample_format') == 'float32le' for chunk in chunks))
            self.assertTrue(all('pcm_s16le_base64' not in chunk for chunk in chunks))
            blocks = [base64.b64decode(chunk['pcm_f32le_base64']) for chunk in chunks]
            self.assertTrue(all(len(block) == 2560 * 4 for block in blocks[:-1]))
            actual = np.frombuffer(b''.join(blocks), dtype='<f4')
            np.testing.assert_array_equal(actual, expected)
            # Ensures this route did not round back onto the signed-16-bit grid.
            self.assertGreater(np.count_nonzero(abs(actual * 32768 - np.rint(actual * 32768)) > 0.01), len(actual) // 2)
            received.append(actual)
        for actual in received[1:]:
            np.testing.assert_array_equal(actual, received[0])

    async def test_native_mode_uses_no_converter(self):
        ws, _ = await self.connect()
        await self.start(ws, 'gemini_native')
        await ws.send_json({'type': 'text', 'text': 'native'})
        _, seen = await self.until(ws, 'end')
        audio = [e for e in seen if e['type'] == 'audio']
        self.assertTrue(audio)
        self.assertTrue(all(e['sample_rate'] == 24000 for e in audio))
        self.assertFalse(self.voice.starts)
        self.assertIsNone(self.provider_calls[0]['vc_sink'])
        await ws.close()


if __name__ == '__main__':
    unittest.main()
