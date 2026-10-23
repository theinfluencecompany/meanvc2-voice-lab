"""Local fake aiohttp provider: protocol checks, not Live access/audio-quality QA."""
import asyncio
import base64
import unittest
from unittest.mock import patch

from aiohttp import web, WSMsgType

import openai_live as live


def audio_event(pcm=b'\0\0' * 640, **changes):
    return {'kind': 'audio', 'pcm_s16le': pcm, 'sample_rate': 16000, 'channels': 1, **changes}


def output(pcm=b'\x01\x00' * 640):
    return {'type': 'session.output_audio.delta', 'delta': base64.b64encode(pcm).decode()}


def resolved():
    return {'type': 'session.started', 'session': {'id': 'mock_live_1',
        'model': 'gpt-live-1', 'status': 'active',
        'audio': {'format': {'type': 'audio/pcm', 'rate': 16000},
                  'output': {'voice': 'echo'}}}}


class Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.ack = resolved()
        self.initial_events = []
        self.on_input = None
        self.close_ack = True
        self.server_events = []
        self.audio = []
        self.cancels = []
        self.transcripts = []
        self.ready = asyncio.Event()
        self.got_audio = asyncio.Event()
        self.provider_closed = asyncio.Event()
        self.input_queue = asyncio.Queue()
        self.callback_block = None
        self.tasks = []
        self.patches = [patch.object(live, 'CLOSE_TIMEOUT', .12),
                        patch.object(live, 'CALLBACK_TIMEOUT', .3)]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        app = web.Application()
        app.router.add_get('/v1/live/sessions', self.provider)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, '127.0.0.1', 0)
        await site.start()
        self.url = f'ws://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/v1/live/sessions'

    async def asyncTearDown(self):
        for task in self.tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.runner.cleanup()

    async def provider(self, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        start = await ws.receive_json()
        self.server_events.append(start)
        await ws.send_json(self.ack)
        for event in self.initial_events:
            await ws.send_json(event)
        async for message in ws:
            if message.type != WSMsgType.TEXT:
                continue
            import json
            event = json.loads(message.data)
            self.server_events.append(event)
            if event['type'] == 'session.input_audio.append' and self.on_input:
                await self.on_input(ws, event)
            elif event['type'] == 'session.close' and self.close_ack:
                await ws.send_json({'type': 'session.closed', 'reason': 'close_requested',
                                    'session': self.ack.get('session', {}), 'usage': {}})
        self.provider_closed.set()
        return ws

    async def events(self):
        while True:
            yield await self.input_queue.get()

    async def on_audio(self, token, pcm, meta):
        self.audio.append((token, pcm, meta))
        self.got_audio.set()
        if self.callback_block:
            await self.callback_block.wait()

    def launch(self, **kwargs):
        args = dict(api_key='fake-local-test-key', persona_instructions='A polite character.',
                    user_events=self.events(), on_audio=self.on_audio,
                    on_cancel=self.cancels.append,
                    on_transcript=lambda role, text: self.transcripts.append((role, text)),
                    on_ready=self.ready.set, ws_url=self.url)
        args.update(kwargs)
        task = asyncio.create_task(live.run_openai_live(**args))
        self.tasks.append(task)
        return task

    async def finish(self, task):
        await self.input_queue.put({'kind': 'stop'})
        await asyncio.wait_for(task, 2)
        await asyncio.wait_for(self.provider_closed.wait(), 1)

    async def test_echo_resolved_native_output_sequence_and_silence(self):
        async def reply(ws, event):
            pcm = base64.b64decode(event['audio'])
            await ws.send_json(output(pcm))
        self.on_input = reply
        task = self.launch(language='en')
        await asyncio.wait_for(self.ready.wait(), 1)
        self.assertEqual(self.server_events[0]['session']['model'], 'gpt-live-1')
        self.assertEqual(self.server_events[0]['session']['audio']['output']['voice'], 'echo')
        self.assertEqual(self.server_events[0]['session']['audio']['format']['rate'], 16000)
        for pcm in [b'\0\0' * 640, b'\x20\x00' * 640]:
            self.got_audio.clear()
            await self.input_queue.put(audio_event(pcm))
            await asyncio.wait_for(self.got_audio.wait(), 1)
        await self.finish(task)
        self.assertEqual([x[2]['output_sequence'] for x in self.audio], [0, 1])
        self.assertTrue(all(x[2]['sample_rate'] == 16000 for x in self.audio))
        self.assertEqual(len({x[0] for x in self.audio}), 1)
        self.assertEqual(self.audio[0][1], b'\0\0' * 640)
        self.assertEqual(self.cancels, [self.audio[0][0]])
        self.assertEqual(self.server_events[-1]['type'], 'session.close')

    async def test_selected_live_voice_must_be_confirmed_before_ready(self):
        for voice in ['shida', 'harema']:
            with self.subTest(voice=voice):
                self.ack = resolved()
                self.ack['session']['audio']['output']['voice'] = voice
                self.ready.clear()
                task = self.launch(voice_name=voice)
                await asyncio.wait_for(self.ready.wait(), 1)
                self.assertEqual(self.server_events[-1]['session']['audio']['output']['voice'], voice)
                await self.finish(task)
        self.ack = resolved()  # Provider silently falls back to Echo.
        self.ready.clear()
        with self.assertRaisesRegex(live.OpenAILiveError, 'requested voice'):
            await asyncio.wait_for(self.launch(voice_name='shida'), 1)
        self.assertFalse(self.ready.is_set())

    async def test_input_continues_while_output_callback_is_pending(self):
        self.initial_events = [output()]
        self.callback_block = asyncio.Event()
        received = asyncio.Event()
        async def reply(ws, event):
            received.set()
        self.on_input = reply
        task = self.launch()
        await asyncio.wait_for(self.got_audio.wait(), 1)
        await self.input_queue.put(audio_event())
        await asyncio.wait_for(received.wait(), 1)
        self.assertFalse(self.callback_block.is_set())
        self.callback_block.set()
        await self.finish(task)

    async def test_audio_source_pause_does_not_close_or_emit_a_turn_boundary(self):
        async def reply(ws, event):
            await ws.send_json(output())
        self.on_input = reply
        task = self.launch()
        await asyncio.wait_for(self.ready.wait(), 1)
        await self.input_queue.put({'kind': 'audio_stream_end'})
        await self.input_queue.put(audio_event())
        await asyncio.wait_for(self.got_audio.wait(), 1)
        self.assertFalse(task.done())
        self.assertEqual([e['type'] for e in self.server_events],
                         ['session.start', 'session.input_audio.append'])
        await self.finish(task)

    async def test_source_iterator_eof_keeps_session_receiving_until_app_cancel(self):
        async def finite_source():
            yield audio_event()
        async def reply(ws, event):
            await ws.send_json(output())
        self.on_input = reply
        task = self.launch(user_events=finite_source())
        await asyncio.wait_for(self.got_audio.wait(), 1)
        self.assertFalse(task.done())
        self.assertFalse(any(e['type'] == 'session.close' for e in self.server_events))
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
        self.assertEqual(self.server_events[-1]['type'], 'session.close')

    async def test_invalid_resolved_model_voice_and_rate_never_ready(self):
        for change in ['model', 'voice', 'rate', 'missing_audio']:
            with self.subTest(change=change):
                self.ack = resolved()
                if change == 'model':
                    self.ack['session']['model'] = 'gpt-realtime-2.1'
                elif change == 'voice':
                    self.ack['session']['audio']['output']['voice'] = 'marin'
                elif change == 'rate':
                    self.ack['session']['audio']['format']['rate'] = 24000
                else:
                    del self.ack['session']['audio']
                with self.assertRaises(live.OpenAILiveError):
                    await asyncio.wait_for(self.launch(), 1)
                self.assertFalse(self.ready.is_set())
                self.assertFalse(self.audio)
                self.assertFalse(self.cancels)

    async def test_malformed_client_pcm_is_not_sent(self):
        for event in [audio_event(b'x'), audio_event(sample_rate=24000),
                      audio_event(channels=2), audio_event(b'\0' * 3202),
                      audio_event('not-bytes'), audio_event(b'')]:
            with self.subTest(event=event):
                task = self.launch()
                await self.input_queue.put(event)
                with self.assertRaisesRegex(live.OpenAILiveError, '16 kHz'):
                    await asyncio.wait_for(task, 1)
        self.assertFalse(any(e['type'] == 'session.input_audio.append' for e in self.server_events))

    async def test_text_video_and_barge_in_are_rejected(self):
        for kind in ['text', 'video', 'barge_in']:
            with self.subTest(kind=kind):
                task = self.launch()
                await self.input_queue.put({'kind': kind, 'text': 'not microphone audio'})
                with self.assertRaisesRegex(live.OpenAILiveError, 'microphone audio only'):
                    await asyncio.wait_for(task, 1)
        self.assertFalse(any(e['type'] == 'response.create' for e in self.server_events))

    async def test_invalid_provider_pcm_is_rejected(self):
        for delta in ['not base64!', base64.b64encode(b'x').decode(),
                      base64.b64encode(b'\0' * 64002).decode()]:
            with self.subTest(delta=delta[:20]):
                self.initial_events = [{'type': 'session.output_audio.delta', 'delta': delta}]
                with self.assertRaises(live.OpenAILiveError):
                    await asyncio.wait_for(self.launch(), 1)
        self.assertFalse(self.audio)

    async def test_bounded_output_queue_fails_closed_and_cleans_up(self):
        self.callback_block = asyncio.Event()
        self.initial_events = [output() for _ in range(40)]
        with self.assertRaisesRegex(live.OpenAILiveError, 'bounded capacity'):
            await asyncio.wait_for(self.launch(), 1)
        self.assertEqual(len(self.cancels), 1)
        self.assertTrue(any(e['type'] == 'session.close' for e in self.server_events))
        self.assertLessEqual(len(self.audio), 1)

    async def test_delegation_stops_without_fabricating_a_tool_result(self):
        self.initial_events = [{'type': 'session.delegation.created',
                                'delegation': {'id': 'del_mock', 'target': 'client'}, 'offset_ms': 100}]
        with self.assertRaisesRegex(live.OpenAILiveError, 'no task backend'):
            await asyncio.wait_for(self.launch(), 1)
        self.assertEqual([x['type'] for x in self.server_events], ['session.start', 'session.close'])

    async def test_provider_error_does_not_expose_secret_or_url(self):
        self.initial_events = [{'type': 'error', 'error': {
            'message': 'secret-secret at wss://provider.invalid?key=secret-secret'}}]
        with self.assertRaises(live.OpenAILiveError) as raised:
            await asyncio.wait_for(self.launch(), 1)
        self.assertNotIn('secret-secret', str(raised.exception))
        self.assertNotIn('provider.invalid', str(raised.exception))

    async def test_close_is_bounded_and_missing_finalization_is_reported(self):
        self.close_ack = False
        task = self.launch()
        await asyncio.wait_for(self.ready.wait(), 1)
        await self.input_queue.put({'kind': 'stop'})
        with self.assertRaisesRegex(live.OpenAILiveError, 'without confirming'):
            await asyncio.wait_for(task, 1)
        self.assertEqual(len(self.cancels), 1)

    async def test_cancellation_closes_transport_and_clears_one_session_token(self):
        task = self.launch()
        await asyncio.wait_for(self.ready.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
        await asyncio.wait_for(self.provider_closed.wait(), 1)
        self.assertEqual(len(self.cancels), 1)
        self.assertEqual(self.server_events[-1]['type'], 'session.close')

    async def test_transcripts_remain_separate_without_turn_end(self):
        self.initial_events = [
            {'type': 'session.input_transcript.delta', 'delta': 'Hello', 'start_ms': 1, 'end_ms': 10},
            {'type': 'session.output_transcript.delta', 'delta': 'Hi', 'start_ms': 2, 'end_ms': 9}, output()]
        task = self.launch()
        await asyncio.wait_for(self.got_audio.wait(), 1)
        self.assertEqual(self.transcripts, [('user', 'Hello'), ('model', 'Hi')])
        self.assertFalse(self.cancels)
        await self.finish(task)

    async def test_slow_transcript_callback_does_not_block_receiving_audio(self):
        transcript_started = asyncio.Event()
        transcript_release = asyncio.Event()
        async def transcript(role, text):
            transcript_started.set()
            await transcript_release.wait()
        self.initial_events = [
            {'type': 'session.input_transcript.delta', 'delta': 'Hello'}, output()]
        task = self.launch(on_transcript=transcript)
        await asyncio.wait_for(transcript_started.wait(), 1)
        await asyncio.wait_for(self.got_audio.wait(), .15)
        self.assertFalse(transcript_release.is_set())
        transcript_release.set()
        await self.finish(task)

    async def test_transcript_backlog_is_bounded_even_if_callback_stalls(self):
        block = asyncio.Event()
        async def transcript(role, text):
            await block.wait()
        self.initial_events = [
            {'type': 'session.output_transcript.delta', 'delta': 'x' * 4096}
            for _ in range(12)]
        with self.assertRaisesRegex(live.OpenAILiveError, 'transcript buffer'):
            await asyncio.wait_for(self.launch(on_transcript=transcript), 1)
        self.assertEqual(len(self.cancels), 1)
        self.assertEqual(self.server_events[-1]['type'], 'session.close')

    async def test_probe_keeps_packet_spacing_after_slow_send(self):
        import importlib.util
        from pathlib import Path
        probe_path = Path(__file__).resolve().parents[3] / 'probe_openai_live.py'
        spec = importlib.util.spec_from_file_location('live_probe_pacing_test', probe_path)
        probe = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(probe)
        now = 0.0
        async def sleep(delay):
            nonlocal now
            now += delay
        packets = []
        timestamps = []
        async for packet in probe.paced_packets(b'\x01\0' * 320, 1, clock=lambda: now, sleep=sleep):
            timestamps.append(now)
            packets.append(packet)
            if len(packets) == 3:
                now += .5  # Transport stall; never replay the backlog in a burst.
        self.assertEqual(len(packets), 50)
        self.assertEqual(packets[0], b'\x01\0' * 320)
        self.assertEqual(packets[-1], b'\0' * 640)
        self.assertTrue(all(b - a >= .019999 for a, b in zip(timestamps, timestamps[1:])))

    async def test_credentials_are_rejected_in_urls_and_exact_model_is_required(self):
        for args in [{'ws_url': 'wss://user:pass@example.com/v1/live/sessions'},
                     {'ws_url': 'wss://example.com/v1/live/sessions?model=gpt-live-1'},
                     {'ws_url': 'ws://example.com/v1/live/sessions'},
                     {'model': 'gpt-realtime'}, {'voice_name': 'Schedar'}]:
            with self.subTest(args=args):
                with self.assertRaises(live.OpenAILiveError):
                    await self.launch(**args)
        self.assertFalse(self.server_events)


if __name__ == '__main__':
    unittest.main()
