#!/usr/bin/env python3
"""Loopback-only MeanVC2 prototype. No production RTA integration is implied."""
from __future__ import annotations
import argparse
import base64
import copy
import hashlib
import inspect
import json
from pathlib import Path
import sys
import textwrap
import threading
import time
import types
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from streaming_vocoder import StreamingVocoder, VOCODER_POLICY
from prefix_quality import PrefixQualityRunner, PREFIX_POLICY, load_assets

ROOT = Path(__file__).resolve().parents[3]
PROTOCOL = 'meanvc2.local.v1'
REPO = ROOT / 'work/meanvc2-lab/repo'
EXPECTED_COMMIT = '13acf84c1bf135ea5edad9c245b345289b06b33e'
RECEIPTS = ROOT / 'runtime/voice-service'
PROCESSING_MODES = ('legacy_stream', 'prefix_quality')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def model_identity():
    files = {'asr': 'preprocess/ckpts/fastu2pp_160ms.pt',
             'vc': 'ckpts/pretrained_models/meanvc2_120ms_40ms.safetensors',
             'vc_config': 'src/config/config_120ms_40ms.json',
             'vocoder': 'ckpts/vocos/vocos.pt',
             'speaker': 'preprocess/ckpts/wavlm_large_finetune.pth',
             'speaker_config': 'preprocess/ckpts/wavlm_large_cfg.pt',
             'speaker_code': 'runtime/src/speaker.py', 'runtime': 'runtime/run_rt.py'}
    components = {name: {'relative_path': rel, 'sha256': digest(REPO / rel)} for name, rel in files.items()}
    fingerprint = hashlib.sha256(json.dumps(components, sort_keys=True).encode()).hexdigest()
    return {'fingerprint': fingerprint, 'components': components}


class Fault(Exception):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code, self.status = code, status


def required(body, name):
    value = body.get(name)
    if not isinstance(value, str) or not value or len(value) > 160:
        raise Fault('invalid_field', f'{name} must be a nonempty string <=160 characters')
    return value


class Engine:
    """One set of read-only models; all mutable caches belong to a runner/turn."""
    def __init__(self, profiles, receipt_dir=RECEIPTS):
        import numpy as np
        import torch
        import subprocess
        sys.path.insert(0, str(REPO / 'runtime'))
        sys.path.insert(0, str(ROOT / 'work/meanvc2-stream-latency'))
        import run_rt
        import measure
        self.np, self.torch, self.upstream, self.flush_upstream = np, torch, run_rt, measure.flush_like_upstream
        torch.set_num_threads(1)
        self.profiles, self.embeddings, self.gtm, self.prefix_assets = {}, {}, {}, {}
        self.model_identity = model_identity()
        self.model_fingerprint = self.model_identity['fingerprint']
        self.adapter_components = {name: digest(Path(__file__).parent / name)
                                   for name in ('service.py', 'streaming_vocoder.py', 'prefix_quality.py',
                                                'prefix-assets/manifest.json', 'prefix-assets/cli-inference-rng.pt',
                                                'prefix-assets/ivan-official-speaker.npy')}
        self.adapter_revision = hashlib.sha256(json.dumps(self.adapter_components, sort_keys=True).encode()).hexdigest()
        self.model_revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=REPO, text=True).strip()
        if self.model_revision != EXPECTED_COMMIT:
            raise RuntimeError('Upstream revision changed; review the adapter first')
        for name, config in profiles.items():
            # Published profiles resolve relative to this checkout, independent of cwd.
            config = dict(config)
            for key in ('reference_path', 'embedding_path', 'receipt_path', 'prefix_assets_manifest'):
                if key in config:
                    path = Path(config[key])
                    config[key] = str(path if path.is_absolute() else ROOT / path)
            reference, cache, receipt = (Path(config[k]) for k in ('reference_path', 'embedding_path', 'receipt_path'))
            manifest = json.loads(receipt.read_text())
            if digest(reference) != manifest['reference_sha256'] or digest(cache) != manifest['embedding_sha256']:
                raise RuntimeError(f'Reference/cache hash mismatch: {name}')
            for key, component in [('speaker_model_sha256', 'speaker'), ('speaker_config_sha256', 'speaker_config'),
                                   ('speaker_code_sha256', 'speaker_code')]:
                if manifest.get(key) != self.model_identity['components'][component]['sha256']:
                    raise RuntimeError(f'Reference extractor changed or unbound ({key}); rebuild cache: {name}')
            if config.get('model_fingerprint') != self.model_fingerprint:
                raise RuntimeError(f'Model/config fingerprint mismatch or missing; review/rebuild profile: {name}')
            embedding = np.load(cache, allow_pickle=False)
            if embedding.shape != (1, 256) or embedding.dtype != np.float32 or not np.isfinite(embedding).all():
                raise RuntimeError(f'Invalid speaker embedding: {name}')
            self.embeddings[name] = torch.from_numpy(embedding).clone()
            self.profiles[name] = {**config, 'reference_digest': manifest['reference_sha256'],
                                   'embedding_digest': manifest['embedding_sha256']}
            assets = load_assets(manifest['reference_sha256'], config.get('prefix_assets_manifest'),
                                 self.model_fingerprint)
            if assets is not None:
                self.prefix_assets[name] = assets
        first = next(iter(self.profiles))
        # The reference extraction has already been verified and cached. Restore
        # module functions immediately after this one startup-only construction.
        original_init, original_extract = run_rt.init_speaker_model, run_rt.extract_embedding
        started = time.perf_counter()
        try:
            run_rt.init_speaker_model = lambda *a, **k: None
            run_rt.extract_embedding = lambda *a, **k: self.embeddings[first].clone()
            self.template = run_rt.VCRunner(self.profiles[first]['reference_path'], device='cpu', model='120ms')
        finally:
            run_rt.init_speaker_model, run_rt.extract_embedding = original_init, original_extract
        self.template.vc.eval()
        self.template.vocoder.eval()
        with torch.no_grad():
            for name, embedding in self.embeddings.items():
                self.gtm[name] = self.template.vc.gtm(embedding)
        before = textwrap.dedent(inspect.getsource(run_rt.VCRunner._vc_step))
        if before.count('N = 2  # ODE steps') != 1 or before.count('dtype=cond.dtype)') != 1:
            raise RuntimeError('Upstream inference changed; review step count / RNG adapter')
        after = before.replace('N = 2  # ODE steps', 'N = 3  # ODE steps')
        after = after.replace('dtype=cond.dtype)', 'dtype=cond.dtype, generator=self._turn_rng)')
        namespace = {}
        exec(compile(after, '<local-three-step-turn-rng>', 'exec'), run_rt.__dict__, namespace)
        self.step = namespace['_vc_step']
        receipt_dir.mkdir(parents=True, exist_ok=True)
        (receipt_dir / 'upstream-vc-step.py').write_text(before)
        (receipt_dir / 'adapter-vc-step.py').write_text(after)
        self.load_seconds = time.perf_counter() - started
        self.loads = 1
        self.info = {'protocol_version': PROTOCOL, 'model': '120ms', 'future_context_ms': 40, 'steps': 3,
                     'device': 'cpu', 'torch_threads': 1, 'model_revision': self.model_revision,
                     'model_fingerprint': self.model_fingerprint, 'model_identity': self.model_identity,
                     'adapter_revision': self.adapter_revision, 'adapter_components': self.adapter_components,
                     'vocoder': VOCODER_POLICY,
                     'supported_processing_modes': list(PROCESSING_MODES),
                     'prefix_quality': PREFIX_POLICY,
                     'runtime_sha256': digest(REPO / 'runtime/run_rt.py'), 'model_load_count': self.loads,
                     'load_seconds': self.load_seconds, 'profiles': self.profiles,
                     'changes': ['runtime step count 2->3 in memory', 'per-turn seeded RNG for isolation',
                                 'bounded vocoder with 32 history and 32 future mel frames; whole-decode-equivalent context'],
                     'tail_policy': 'pad partial 160ms block, append 480ms ASR context; retain >=160ms guard beyond source; only trim verified quiet suffix below -80dBFS',
                     'scope': 'local prototype; no RTA, TTS, playback device, network or production capacity validation'}
        (receipt_dir / 'engine.json').write_text(json.dumps(self.info, indent=2) + '\n')
        (receipt_dir / 'adapter-source.py').write_text(Path(__file__).read_text())
        (receipt_dir / 'streaming-vocoder-source.py').write_text(Path(__file__).with_name('streaming_vocoder.py').read_text())

    def new_runner(self, profile, processing_mode='legacy_stream'):
        if processing_mode == 'prefix_quality':
            if profile not in self.prefix_assets:
                raise Fault('unsupported_profile', 'Prefix quality needs its verified official reference cache')
            embedding, rng, _ = self.prefix_assets[profile]
            return PrefixQualityRunner(self.template.asr, self.template.vc, self.template.vocoder,
                                       embedding, rng)
        if processing_mode != 'legacy_stream':
            raise Fault('unsupported_processing_mode', 'Unknown processing_mode')
        runner = copy.copy(self.template)
        runner.vc_spk_emb = self.embeddings[profile]
        runner.vc_gtm_kv = self.gtm[profile]
        runner._turn_rng = self.torch.Generator(device='cpu').manual_seed(42)
        runner._vc_step = types.MethodType(self.step, runner)
        runner._init_cache()
        runner._streaming_vocoder = StreamingVocoder(runner.vocoder)
        # Keep upstream last_wav=None: the context decoder owns all final PCM.
        runner._decode_mel = types.MethodType(lambda this, mel: this._streaming_vocoder.push(mel), runner)
        return runner

    def convert(self, runner, data):
        return runner.process_chunk(data)

    def flush(self, runner):
        if isinstance(runner, PrefixQualityRunner):
            return runner.finish()
        pending = self.flush_upstream(runner)
        final = runner._streaming_vocoder.finish()
        parts = [part for part in (pending, final) if part is not None and len(part)]
        return self.np.concatenate(parts) if parts else self.np.empty(0, dtype=self.np.float32)


class Turn:
    def __init__(self, session_id, turn_id, generation, profile, runner, processing_mode='legacy_stream',
                 input_sample_format='s16le'):
        import numpy as np
        self.session_id, self.turn_id, self.generation, self.profile = session_id, turn_id, generation, profile
        self.runner, self.pending = runner, np.empty(0, dtype=np.float32)
        self.processing_mode = processing_mode
        self.input_sample_format = input_sample_format
        self.lock = threading.Lock()
        self.status, self.next_sequence, self.output_sequence = 'active', 0, 0
        self.input_samples, self.output_samples = 0, 0
        self.tail_metadata = {}
        self.last_used = time.monotonic()


class Service:
    def __init__(self, engine, max_sessions=4):
        self.engine, self.max_sessions = engine, max_sessions
        self.guard, self.compute = threading.Lock(), threading.Lock()
        self.turns, self.sessions = {}, {}

    def health(self):
        return {'ready': True, 'protocol_version': PROTOCOL, 'model_revision': self.engine.model_revision,
                'model_fingerprint': self.engine.model_fingerprint,
                'adapter_revision': self.engine.adapter_revision,
                'vocoder': VOCODER_POLICY,
                'supported_processing_modes': list(PROCESSING_MODES),
                'default_processing_mode': 'legacy_stream',
                'prefix_quality': PREFIX_POLICY,
                'supported_input_sample_formats': {'legacy_stream': ['s16le'],
                                                    'prefix_quality': ['s16le', 'float32le']},
                'model': '120ms', 'steps': 3, 'sample_rate': 16000, 'channels': 1,
                'model_load_count': self.engine.loads, 'max_active_sessions': self.max_sessions,
                'inference_parallelism': 1,
                'production_ready': False, 'reference_quality_status': 'pending_listening',
                'tail_policy': '480ms flush context; >=160ms retained guard; quiet-only suffix trim',
                'profiles': {name: {'reference_digest': p['reference_digest'], 'label': p['label'],
                                   'supported_processing_modes': ['legacy_stream'] +
                                      (['prefix_quality'] if name in self.engine.prefix_assets else [])}
                             for name, p in self.engine.profiles.items()},
                'scope': 'local service only; not connected to realtime-avatar'}

    def _find(self, body):
        key = (required(body, 'session_id'), required(body, 'turn_id'), required(body, 'generation_id'))
        turn = self.turns.get(key)
        if turn is None:
            raise Fault('unknown_generation', 'Unknown or expired session/turn/generation', 409)
        return turn

    def _reply(self, turn, data=b'', discarded=False, **extra):
        return {'protocol_version': PROTOCOL, 'session_id': turn.session_id, 'turn_id': turn.turn_id,
                'generation_id': turn.generation, 'status': turn.status, 'discarded': discarded,
                'processing_mode': turn.processing_mode,
                'input_sample_format': turn.input_sample_format,
                'output_sequence': turn.output_sequence, 'sample_rate': 16000, 'channels': 1,
                'sample_count': len(data) // 2, 'pcm_s16le_base64': base64.b64encode(data).decode(), **extra}

    def _prune(self):
        now = time.monotonic()
        for key, turn in list(self.turns.items()):
            if now - turn.last_used > 120 and not turn.lock.locked():
                turn.status = 'expired'
                turn.runner = None
                self.turns.pop(key)
                if self.sessions.get(turn.session_id) is turn:
                    self.sessions.pop(turn.session_id)
        if len(self.turns) > 256:
            for key, turn in list(self.turns.items()):
                if turn.status != 'active' and not turn.lock.locked():
                    self.turns.pop(key)

    def start(self, body):
        if body.get('protocol_version') != PROTOCOL:
            raise Fault('protocol_mismatch', f'Expected {PROTOCOL}')
        sid, tid, profile = (required(body, key) for key in ('session_id', 'turn_id', 'conversion_profile_id'))
        processing_mode = body.get('processing_mode', 'legacy_stream')
        if processing_mode not in PROCESSING_MODES:
            raise Fault('unsupported_processing_mode', 'Unknown processing_mode')
        input_format = body.get('input_sample_format', 's16le')
        if input_format not in ('s16le', 'float32le') or (input_format == 'float32le' and processing_mode != 'prefix_quality'):
            raise Fault('audio_format', 'Float32 input is available only for prefix_quality')
        if profile not in self.engine.profiles:
            raise Fault('unknown_profile', 'Reference profile has not been prepared')
        p = self.engine.profiles[profile]
        if processing_mode == 'prefix_quality' and profile not in self.engine.prefix_assets:
            raise Fault('unsupported_profile', 'Prefix quality needs its verified official reference cache')
        for key, expected in [('reference_digest', p['reference_digest']), ('model_revision', self.engine.model_revision),
                              ('model_fingerprint', self.engine.model_fingerprint)]:
            if required(body,key) != expected:
                raise Fault('binding_mismatch', f'{key} does not match prepared profile', 409)
        with self.guard:
            self._prune()
            previous = self.sessions.get(sid)
            if previous and previous.turn_id == tid and previous.status == 'active':
                raise Fault('turn_already_active', 'Use a new turn_id or explicitly cancel', 409)
            active = sum(t.status == 'active' for t in self.sessions.values())
            if active >= self.max_sessions and (previous is None or previous.status != 'active'):
                raise Fault('capacity', 'Local active-session limit reached', 503)
            if previous:
                previous.status = 'cancelled'
                if not previous.lock.locked():
                    previous.runner = None
            generation = uuid.uuid4().hex
            turn = Turn(sid, tid, generation, profile,
                        self.engine.new_runner(profile, processing_mode), processing_mode, input_format)
            self.turns[(sid, tid, generation)] = turn
            self.sessions[sid] = turn
            return self._reply(turn, reference_digest=p['reference_digest'], model_revision=self.engine.model_revision,
                               model_fingerprint=self.engine.model_fingerprint,
                               adapter_revision=self.engine.adapter_revision,
                               conversion_profile_id=profile, reference_label=p['label'], internal_chunk_samples=2560,
                               max_input_samples=5120, steps=3, model='120ms', vocoder=VOCODER_POLICY,
                               **({'processing_policy': PREFIX_POLICY} if processing_mode == 'prefix_quality' else {}))

    def cancel(self, body):
        with self.guard:
            turn = self._find(body)
            turn.status, turn.last_used = 'cancelled', time.monotonic()
            if not turn.lock.locked():
                turn.runner = None
            return self._reply(turn, discarded=True)

    def close(self, body):
        sid = required(body, 'session_id')
        with self.guard:
            for turn in self.turns.values():
                if turn.session_id == sid:
                    turn.status = 'closed'
                    if not turn.lock.locked():
                        turn.runner = None
            self.sessions.pop(sid, None)
        return {'session_id': sid, 'status': 'closed', 'protocol_version': PROTOCOL}

    def audio(self, body, flush=False):
        import numpy as np
        began = time.perf_counter()
        if not flush:
            if body.get('sample_rate') != 16000 or body.get('channels') != 1:
                raise Fault('audio_format', 'Requires 16000 Hz mono PCM')
            sample_format = body.get('sample_format', 's16le')
            if sample_format not in ('s16le', 'float32le'):
                raise Fault('audio_format', 'Expected s16le or float32le')
            sample_bytes = 4 if sample_format == 'float32le' else 2
            payload_field = 'pcm_f32le_base64' if sample_format == 'float32le' else 'pcm_s16le_base64'
            if 'pcm_f32le_base64' in body and 'pcm_s16le_base64' in body:
                raise Fault('audio_format', 'Send exactly one PCM payload field')
            try:
                data = base64.b64decode(body.get(payload_field, ''), validate=True)
            except (ValueError, TypeError):
                raise Fault('audio_format', 'Invalid base64 PCM')
            if not data or len(data) % sample_bytes or len(data) > 5120*sample_bytes:
                raise Fault('audio_size', 'Each chunk needs 1..5120 complete samples')
            if sample_format == 'float32le':
                floats = np.frombuffer(data, dtype='<f4')
                if not np.isfinite(floats).all() or np.max(np.abs(floats)) > 2.:
                    raise Fault('audio_format', 'Float PCM must be finite normalized samples with absolute value <=2')
        with self.guard:
            turn = self._find(body)
            if turn.status != 'active':
                return self._reply(turn, discarded=True)
            if not flush and sample_format != turn.input_sample_format:
                raise Fault('audio_format', 'Chunk sample_format differs from the start negotiation')
            if not turn.lock.acquire(blocking=False):
                raise Fault('turn_busy', 'Only one audio request per turn may be in flight', 409)
        parts = []
        try:
            with self.compute:
                with self.guard:
                    if turn.status != 'active':
                        return self._reply(turn, discarded=True)
                    if not flush:
                        seq = body.get('sequence')
                        if type(seq) is not int or seq != turn.next_sequence:
                            raise Fault('sequence_mismatch', f'Expected sequence {turn.next_sequence}', 409)
                        turn.next_sequence += 1
                if not flush:
                    if (turn.processing_mode == 'prefix_quality' and
                            turn.input_samples + len(data)//sample_bytes > PREFIX_POLICY['max_turn_samples']):
                        turn.status, turn.runner = 'failed', None
                        raise Fault('turn_too_long', 'Prefix quality supports at most 30 seconds per turn', 413)
                    if sample_format == 'float32le':
                        # Quality bridge normalizes original PCM16 by 32767,
                        # then resamples to float32 without re-quantization.
                        samples = np.frombuffer(data, dtype='<f4').copy()
                    else:
                        divisor = 32767 if turn.processing_mode == 'prefix_quality' else 32768
                        samples = np.frombuffer(data, dtype='<i2').astype(np.float32) / divisor
                    turn.input_samples += len(samples)
                    turn.pending = np.concatenate([turn.pending, samples])
                while len(turn.pending) >= 2560:
                    with self.guard:
                        if turn.status != 'active':
                            return self._reply(turn, discarded=True)
                    part = self.engine.convert(turn.runner, turn.pending[:2560])
                    turn.pending = turn.pending[2560:]
                    if part is not None and len(part):
                        parts.append(part)
                if flush:
                    if turn.processing_mode == 'prefix_quality':
                        if len(turn.pending):
                            part = self.engine.convert(turn.runner, turn.pending)
                            if part is not None and len(part):
                                parts.append(part)
                        turn.pending = np.empty(0, dtype=np.float32)
                        part = self.engine.flush(turn.runner)
                        if part is not None and len(part):
                            parts.append(part)
                        turn.tail_metadata = {'added_context_samples': 0, 'minimum_guard_samples': 0,
                            'trimmed_quiet_samples': 0, 'trimmed_suffix_peak': 0.,
                            'trim_rule': 'none; real-source ASR windows only; same final policy as accepted experiment',
                            'alignment_status': 'official ASR finite-window tail; not word alignment'}
                    else:
                        # Legacy tail policy is unchanged for existing callers.
                        added = 0
                        if turn.input_samples and len(turn.pending):
                            added = 2560-len(turn.pending)
                            padded = np.pad(turn.pending, (0,added))
                            part = self.engine.convert(turn.runner, padded)
                            if part is not None and len(part):
                                parts.append(part)
                        turn.pending = np.empty(0, dtype=np.float32)
                        if turn.input_samples:
                            for _ in range(3):
                                with self.guard:
                                    if turn.status != 'active':
                                        return self._reply(turn, discarded=True)
                                part = self.engine.convert(turn.runner, np.zeros(2560,dtype=np.float32))
                                added += 2560
                                if part is not None and len(part):
                                    parts.append(part)
                            part = self.engine.flush(turn.runner)
                            if part is not None and len(part):
                                parts.append(part)
                        turn.tail_metadata = {'added_context_samples':added,'minimum_guard_samples':2560,
                            'trim_rule':'never before source length +160ms; remove only suffix peak <=0.0001, keep >=40ms quiet after last louder sample',
                            'alignment_status':'conservative guard, no semantic alignment acceptance'}
                with self.guard:
                    if turn.status != 'active':
                        return self._reply(turn, discarded=True)
                    pcm = np.concatenate(parts) if parts else np.empty(0, dtype=np.float32)
                    if not np.isfinite(pcm).all():
                        turn.status, turn.runner = 'failed', None
                        raise Fault('nonfinite_output', 'Nonfinite audio; terminate turn', 500)
                    if flush and len(pcm) and turn.processing_mode == 'legacy_stream':
                        protected = max(0,turn.input_samples + 2560 - turn.output_samples)
                        nonquiet = np.flatnonzero(np.abs(pcm) > 0.0001)
                        keep = min(len(pcm),max(protected,int(nonquiet[-1])+1+640 if len(nonquiet) else 0))
                        removed = pcm[keep:]
                        turn.tail_metadata.update({'raw_flush_samples':len(pcm),'trimmed_quiet_samples':len(removed),
                            'trimmed_suffix_peak':float(np.abs(removed).max()) if len(removed) else 0.0,
                            'retained_extra_samples':turn.output_samples+keep-turn.input_samples})
                        pcm = pcm[:keep]
                    encoded = np.clip(np.rint(pcm * 32768), -32768, 32767).astype('<i2').tobytes()
                    turn.output_samples += len(pcm)
                    if flush:
                        turn.status, turn.runner = 'finished', None
                        if turn.input_samples and turn.output_samples == 0:
                            raise Fault('input_too_short', 'Upstream produced no audio for this short utterance', 422)
                    turn.last_used = time.monotonic()
                    response = self._reply(turn, encoded, processing_ms=(time.perf_counter() - began) * 1000,
                                           input_samples_total=turn.input_samples, output_samples_total=turn.output_samples,
                                           **({'tail':turn.tail_metadata} if flush else {}))
                    if len(pcm):
                        turn.output_sequence += 1
                    return response
        except Fault:
            raise
        except Exception:
            with self.guard:
                turn.status, turn.runner = 'failed', None
            raise
        finally:
            with self.guard:
                if turn.status != 'active':
                    turn.runner = None
                turn.lock.release()

    def dispatch(self, path, body):
        routes = {'/v1/start': self.start, '/v1/chunk': self.audio,
                  '/v1/flush': lambda b: self.audio(b, flush=True), '/v1/cancel': self.cancel, '/v1/close': self.close}
        if path not in routes:
            raise Fault('not_found', 'Unknown endpoint', 404)
        return routes[path](body)


def handler_for(service):
    class Handler(BaseHTTPRequestHandler):
        def respond(self, status, payload):
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self.respond(200 if self.path == '/health' else 404,
                         service.health() if self.path == '/health' else {'error': 'not_found'})

        def do_POST(self):
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 32768:
                    raise Fault('body_size', 'JSON request body must be 1..32768 bytes', 413)
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise Fault('invalid_json', 'Expected JSON object')
                self.respond(200, service.dispatch(self.path, body))
            except Fault as exc:
                self.respond(exc.status, {'error': exc.code, 'message': str(exc)})
            except (ValueError, json.JSONDecodeError):
                self.respond(400, {'error': 'invalid_json'})
            except Exception as exc:
                print(f'Inference error: {type(exc).__name__}: {exc}', file=sys.stderr, flush=True)
                self.respond(500, {'error': 'internal_error'})

        def log_message(self, fmt, *args):
            return
    return Handler


def default_profiles():
    path = ROOT / 'outputs/ivan-jp-pilot-20261020/voice-service/ivan-reference/profiles.json'
    return json.loads(path.read_text())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8771)
    parser.add_argument('--profiles', type=Path)
    args = parser.parse_args()
    profiles = json.loads(args.profiles.read_text()) if args.profiles else default_profiles()
    engine = Engine(profiles)
    service = Service(engine)
    server = ThreadingHTTPServer(('127.0.0.1', args.port), handler_for(service))
    print(json.dumps({'ready': True, 'url': f'http://127.0.0.1:{args.port}', **service.health()}), flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
