"""Bounded Vocos decoding with the context required by the shipped checkpoint.

Its input convolution and eight ConvNeXt convolutions each use kernel 7.
They need 27 mel frames on each side; centered ISTFT needs another 2 frames.
Keeping 32 frames on each side makes interior output equal to whole-mel decode.
This adds lookahead, not a wait for the complete utterance.
"""
from __future__ import annotations

import numpy as np
import torch


VOCODER_POLICY = {
    'implementation': 'bounded-context-v1',
    'history_frames': 32,
    'future_frames': 32,
    'mel_frame_ms': 10,
    'emit_frames': 12,
    'vocoder_future_context_ms': 320,
    'first_emit_after_mel_frames': 48,
    'startup_note': '32 future frames; 12-frame model packets round first emission to 48 received frames. This is not total call latency.',
}


class StreamingVocoder:
    """Consume unnormalized MeanVC2 mel, emit continuous 16 kHz float PCM."""

    def __init__(self, vocoder):
        if (vocoder.head.istft.padding != 'center'
                or vocoder.head.istft.hop_length != 160
                or vocoder.head.istft.n_fft != 640):
            raise RuntimeError('Vocoder format changed; revalidate bounded decoding')
        self.vocoder = vocoder
        self.history = VOCODER_POLICY['history_frames']
        self.future = VOCODER_POLICY['future_frames']
        self.emit_frames = VOCODER_POLICY['emit_frames']
        self.buffer = None
        self.buffer_start = 0
        self.received = 0
        self.emitted_frame = 0
        self.finished = False
        self.max_buffer_frames = 0

    @staticmethod
    def _join(parts):
        return np.concatenate(parts) if parts else np.empty(0, dtype=np.float32)

    def _drop_history(self):
        keep_from = max(0, self.emitted_frame - self.history)
        drop = keep_from - self.buffer_start
        if drop > 0:
            # clone releases storage for the old frames; a sliced view alone
            # would retain the old tensor allocation.
            self.buffer = self.buffer[:, :, drop:].clone()
            self.buffer_start = keep_from

    def _emit(self, end, final=False):
        start = self.emitted_frame
        window_start = max(0, start - self.history)
        window_end = min(self.received, end + self.future)
        mel = self.buffer[:, :, window_start-self.buffer_start:window_end-self.buffer_start]
        with torch.no_grad():
            wav = self.vocoder.decode((mel + 1) / 2).squeeze().detach().cpu().numpy()
        sample_start = start * 160
        sample_end = end * 160
        if final:
            # Centered ISTFT produces (T-1)*hop samples, including final flush.
            sample_end = min(sample_end, max(0, self.received-1)*160)
        relative_start = sample_start - window_start*160
        relative_end = sample_end - window_start*160
        if relative_start < 0 or relative_end > len(wav):
            raise RuntimeError('Vocoder output alignment changed')
        result = wav[relative_start:relative_end].copy()
        self.emitted_frame = end
        self._drop_history()
        return result

    def push(self, mel):
        if self.finished:
            raise RuntimeError('Cannot append to a finished vocoder stream')
        if mel.ndim != 3 or mel.shape[0] != 1 or mel.shape[1] != 80:
            raise ValueError('Expected one stream of [1,80,frames] mel')
        parts = []
        # Also bound memory if a caller passes a large tensor in one call.
        for pos in range(0, mel.shape[-1], self.emit_frames):
            chunk = mel[:, :, pos:pos+self.emit_frames].detach().clone()
            self.buffer = chunk if self.buffer is None else torch.cat([self.buffer, chunk], dim=-1)
            self.received += chunk.shape[-1]
            self.max_buffer_frames = max(self.max_buffer_frames, self.buffer.shape[-1])
            while self.received >= self.emitted_frame + self.emit_frames + self.future:
                parts.append(self._emit(self.emitted_frame+self.emit_frames))
        return self._join(parts)

    def finish(self):
        if self.finished:
            return np.empty(0, dtype=np.float32)
        self.finished = True
        parts = []
        if self.received > 1:
            while self.emitted_frame < self.received:
                parts.append(self._emit(min(self.emitted_frame+self.emit_frames, self.received), final=True))
        self.buffer = None
        return self._join(parts)
