"""Stream genuine Gemini 24 kHz PCM unchanged for a native-voice comparison.

No inference, recording, or synthesized fallback. The sink argument only keeps
the factory signature compatible with NativeAudioBridge; it is never used.
"""
from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, field
import uuid

from gemini_bridge import DEFAULT_QUEUE_BLOCKS, MAX_PROVIDER_PCM_BYTES


@dataclass
class PlaybackTurn:
    token: str = field(default_factory=lambda: uuid.uuid4().hex)
    sequence: int = 0
    cancelled: bool = False
    provider_complete: bool = False
    complete: bool = False


class NativePlaybackBridge:
    def __init__(self, sink, on_audio, on_cancel, queue_blocks=DEFAULT_QUEUE_BLOCKS, on_end=None):
        self.on_audio, self.on_cancel = on_audio, on_cancel
        self.on_end = on_end or (lambda token: None)
        self.queue = asyncio.Queue(maxsize=queue_blocks)
        self.worker = None
        self.producer = None
        self.current = None
        self.suppress_until_boundary = False
        self.closed = False
        self.stats = {'provider_pcm_parts': 0, 'input_24k_samples': 0,
                      'native_packets': 0, 'discarded_results': 0,
                      'interruptions': 0, 'max_queue_blocks': 0}

    def start_worker(self):
        if self.worker is not None or self.closed:
            raise RuntimeError('Worker already started or bridge closed')
        self.worker = asyncio.create_task(self._work())

    async def wait_control_failure(self):
        # Native playback has no asynchronous inference cancellation transport.
        # The caller monitors worker failures separately and cancels this wait.
        await asyncio.Future()

    def interrupt(self, local=False):
        pending_boundary = self.suppress_until_boundary or bool(
            self.producer and not self.producer.provider_complete)
        turn = self.current
        if turn and not turn.cancelled:
            turn.cancelled = True
            self.stats['interruptions'] += 1
            self.on_cancel(turn.token)
        self.producer = None
        self.suppress_until_boundary = local and pending_boundary

    def _enqueue(self, turn, kind, data=None):
        try:
            self.queue.put_nowait((turn, kind, data))
        except asyncio.QueueFull:
            self.interrupt(local=True)
            raise RuntimeError('Native playback backpressure: bounded audio queue exceeded') from None
        self.stats['max_queue_blocks'] = max(self.stats['max_queue_blocks'], self.queue.qsize())

    def _new_turn(self):
        if self.current and not self.current.cancelled:
            self.interrupt()
        self.current = self.producer = PlaybackTurn()
        return self.producer

    def submit(self, message):
        if self.closed:
            raise RuntimeError('Native playback bridge is closed')
        if not isinstance(message, dict):
            raise ValueError('Expected provider object')
        content = message.get('serverContent')
        if content is None:
            return
        if not isinstance(content, dict):
            raise ValueError('Invalid serverContent')
        if content.get('interrupted'):
            self.interrupt(local=False)
            return
        if self.suppress_until_boundary:
            if content.get('turnComplete'):
                self.suppress_until_boundary = False
            return
        parts = content.get('modelTurn', {}).get('parts', [])
        if not isinstance(parts, list):
            raise ValueError('Invalid model parts')
        for part in parts:
            inline = part.get('inlineData')
            if inline is None:
                continue
            if inline.get('mimeType') != 'audio/pcm;rate=24000':
                raise ValueError('Expected Gemini native 24kHz mono PCM')
            try:
                data = base64.b64decode(inline.get('data', ''), validate=True)
            except (TypeError, ValueError):
                raise ValueError('Invalid provider PCM encoding') from None
            if len(data) % 2 or len(data) > MAX_PROVIDER_PCM_BYTES:
                raise ValueError('Invalid/oversized provider PCM part')
            if not data:
                continue
            turn = self.producer or self._new_turn()
            self.stats['provider_pcm_parts'] += 1
            self.stats['input_24k_samples'] += len(data) // 2
            self._enqueue(turn, 'audio', data)
        if content.get('turnComplete') and self.producer:
            turn = self.producer
            turn.provider_complete = True
            self._enqueue(turn, 'end')
            self.producer = None

    async def _work(self):
        while True:
            turn, kind, data = await self.queue.get()
            try:
                if kind == 'stop':
                    return
                if turn.cancelled or self.current is not turn:
                    self.stats['discarded_results'] += 1
                    continue
                if kind == 'audio':
                    self.on_audio(turn.token, data, {'sample_rate': 24000, 'channels': 1,
                        'generation_id': turn.token, 'output_sequence': turn.sequence})
                    turn.sequence += 1
                    self.stats['native_packets'] += 1
                elif kind == 'end':
                    self.on_end(turn.token)
                    turn.complete = True
            except Exception:
                self.interrupt(local=True)
                raise
            finally:
                self.queue.task_done()

    async def drain(self):
        if self.worker is None:
            raise RuntimeError('Worker not started')
        joined = asyncio.create_task(self.queue.join())
        done, _ = await asyncio.wait([joined, self.worker], return_when=asyncio.FIRST_COMPLETED)
        if self.worker in done:
            joined.cancel()
            await asyncio.gather(joined, return_exceptions=True)
            await self.worker
        else:
            await joined

    async def close(self):
        if self.closed:
            return
        self.closed = True
        self.interrupt(local=True)
        if self.worker and not self.worker.done():
            await self.queue.put((None, 'stop', None))
            await self.worker
