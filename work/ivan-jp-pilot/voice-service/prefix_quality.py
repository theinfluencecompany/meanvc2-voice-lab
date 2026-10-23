"""PCM-stream adaptation of the accepted prefix-grid-delay1280 experiment.

Only arrived samples enter fbank/ASR. Every four-BN ASR event advances the
prefix interpolation independently of HTTP packet size. This deliberately
trades 32 BN frames (1280 ms) of lookahead for the accepted processing policy.
It does not claim to equal whole-utterance conversion or remove all artifacts.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import torch
import torchaudio.compliance.kaldi as kaldi

from streaming_vocoder import StreamingVocoder


PREFIX_POLICY = {
    'implementation': 'causal-prefix-grid-1280-v1',
    'extra_bn_wait': 32,
    'extra_feature_context_ms': 1280,
    'noise': 'absolute-position continuous CPU RNG, frozen official CLI state',
    'steps': 3,
    'maximum_turn_seconds': 30,
    'max_turn_samples': 480000,
    'tail': 'real samples only; no ASR padding; final partial VC block retained',
    'quality_status': 'one experimental sample accepted; live conversation needs listening',
}


def load_assets(reference_digest, manifest_path=None, model_fingerprint=None):
    # Ivan keeps the exact frozen defaults. Other speakers bring their own
    # verified official embedding, while using the same frozen RNG policy.
    path = Path(manifest_path) if manifest_path else Path(__file__).with_name('prefix-assets') / 'manifest.json'
    folder = path.parent
    manifest = json.loads(path.read_text())
    if reference_digest != manifest['reference']['sha256']:
        if manifest_path:
            raise RuntimeError('Prefix reference does not match prepared profile')
        return None
    if manifest_path and manifest.get('model_fingerprint') != model_fingerprint:
        raise RuntimeError('Prefix model fingerprint does not match prepared profile')
    for name, asset in manifest['assets'].items():
        if hashlib.sha256((folder / name).read_bytes()).hexdigest() != asset['sha256']:
            raise RuntimeError(f'Prefix quality asset changed: {name}')
    embedding_name = manifest.get('speaker_embedding_asset', 'ivan-official-speaker.npy')
    if embedding_name not in manifest['assets']:
        raise RuntimeError('Prefix speaker embedding is not hash-bound')
    embedding = np.load(folder / embedding_name, allow_pickle=False)
    if embedding.shape != (256,) or embedding.dtype != np.float32 or not np.isfinite(embedding).all():
        raise RuntimeError('Invalid official speaker embedding')
    rng = torch.load(folder / 'cli-inference-rng.pt', weights_only=True, map_location='cpu')
    # Validate eagerly; each turn receives its own independent generator.
    torch.Generator(device='cpu').set_state(rng)
    return torch.from_numpy(embedding).unsqueeze(0).clone(), rng.clone(), manifest


class PrefixQualityRunner:
    """One turn's feature, transformer, noise and vocoder state.

    Float PCM uses the official CLI's int16 / 32767 normalization. Service
    performs that normalization at the protocol boundary. Models are shared,
    but inference is serialized by Service and none of these caches is shared.
    """
    processing_mode = 'prefix_quality'

    def __init__(self, asr, model, vocoder, embedding, rng_state):
        self.asr, self.vc, self.vocoder = asr, model, vocoder
        self.speaker = embedding
        self.rng = torch.Generator(device='cpu').set_state(rng_state)
        self.samples_cache = np.empty(0, dtype=np.float32)
        self.frame_cache = None
        self.asr_offset = 8
        self.asr_att_cache = torch.zeros(6, 4, 8, 128)
        self.asr_cnn_cache = torch.zeros(6, 1, 256, 8)
        self.bn = None
        self.noise = None
        self.kv_cache = None
        self.start = 0
        self.sample_count = 0
        self.finished = False
        self._streaming_vocoder = StreamingVocoder(vocoder)
        self.trace = []
        self.first_pcm_input_samples = None
        self.mel_observer = None  # Set only by numerical validation; no capture normally.
        self.bn_observer = None

    @staticmethod
    def _join(parts):
        parts = [p for p in parts if p is not None and len(p)]
        return np.concatenate(parts) if parts else np.empty(0, dtype=np.float32)

    def _ensure_noise(self, end):
        have = 0 if self.noise is None else self.noise.shape[1]
        if end > have:
            extra = torch.randn(1, end-have, 80, generator=self.rng)
            self.noise = extra if self.noise is None else torch.cat([self.noise, extra], dim=1)
        return self.noise[:, self.start:end].clone()

    @torch.inference_mode()
    def _advance(self, new_bn=None, final=False):
        if new_bn is not None:
            if new_bn.shape != (1, 4, 256):
                raise RuntimeError('ASR four-frame contract changed')
            if self.bn_observer is not None:
                self.bn_observer(new_bn)
            self.bn = new_bn if self.bn is None else torch.cat([self.bn, new_bn], dim=1)
        if self.bn is None:
            return np.empty(0, dtype=np.float32)
        n = self.bn.shape[1]
        seq_len = 4*n
        parts = []
        while self.start < seq_len:
            if not final and self.start + 16 > 4*(n-PREFIX_POLICY['extra_bn_wait']):
                break
            mid = min(self.start+12, seq_len)
            end = min(mid+4, seq_len)
            # Same prefix length, interpolation and absolute noise as the
            # accepted experiment. No final utterance length is consulted.
            prefix = torch.nn.functional.interpolate(
                self.bn.transpose(1, 2), size=seq_len,
                mode='linear', align_corners=True).transpose(1, 2)
            cond = prefix[:, self.start:end]
            x = self._ensure_noise(end)
            times = torch.linspace(1., 0., 4)
            for i in range(3):
                t, r = times[i], times[i+1]
                u, next_cache = self.vc(
                    x, torch.full((1,), t), torch.full((1,), r), cache=None,
                    cond=cond, spks=self.speaker, offset=self.start,
                    is_inference=True, kv_cache=self.kv_cache)
                x = x-(t-r)*u
            self.kv_cache = next_cache
            mel = x[:, :mid-self.start].transpose(1, 2)
            if self.mel_observer is not None:
                self.mel_observer(mel)
            pcm = self._streaming_vocoder.push(mel)
            if len(pcm):
                if self.first_pcm_input_samples is None:
                    self.first_pcm_input_samples = self.sample_count
                parts.append(pcm)
            self.trace.append({'start_frame': self.start, 'end_frame': mid,
                               'available_bn': n, 'input_samples': self.sample_count,
                               'final': final})
            self.start = mid
        if final:
            pcm = self._streaming_vocoder.finish()
            if len(pcm):
                if self.first_pcm_input_samples is None:
                    self.first_pcm_input_samples = self.sample_count
                parts.append(pcm)
        return self._join(parts)

    @torch.inference_mode()
    def process_chunk(self, samples):
        if self.finished:
            raise RuntimeError('Cannot append to a finished prefix stream')
        samples = np.asarray(samples, dtype=np.float32)
        if samples.ndim != 1 or not np.isfinite(samples).all():
            raise ValueError('Finite mono PCM required')
        if self.sample_count + len(samples) > PREFIX_POLICY['max_turn_samples']:
            raise ValueError('Prefix turn exceeds 30 seconds')
        self.sample_count += len(samples)
        padded = np.concatenate([self.samples_cache, samples])
        if len(padded) < 400:
            self.samples_cache = padded
            return np.empty(0, dtype=np.float32)
        fbanks = kaldi.fbank(torch.from_numpy(padded*(1 << 15)).unsqueeze(0),
            frame_length=25, frame_shift=10, snip_edges=True, num_mel_bins=80,
            energy_floor=0., dither=0., sample_frequency=16000)
        self.samples_cache = padded[160*fbanks.shape[0]:].copy()
        if self.frame_cache is not None:
            fbanks = torch.cat([self.frame_cache, fbanks], dim=0)
        pos, parts = 0, []
        while pos+19 <= fbanks.shape[0]:
            out, self.asr_att_cache, self.asr_cnn_cache = self.asr(
                fbanks[pos:pos+19].unsqueeze(0),
                torch.tensor(self.asr_offset, dtype=torch.int64),
                torch.tensor(8, dtype=torch.int64),
                self.asr_att_cache, self.asr_cnn_cache)
            self.asr_offset += 4
            pos += 16
            # Do not combine multiple ASR events into one larger prefix. That
            # changes already-emitted audio with incoming HTTP packet sizes.
            parts.append(self._advance(out.detach()))
        self.frame_cache = fbanks[pos:].clone()
        return self._join(parts)

    def finish(self):
        if self.finished:
            return np.empty(0, dtype=np.float32)
        self.finished = True
        result = self._advance(final=True)
        self.samples_cache = np.empty(0, dtype=np.float32)
        self.frame_cache = self.bn = self.noise = self.kv_cache = None
        return result
