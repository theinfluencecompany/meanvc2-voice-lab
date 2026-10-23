#!/usr/bin/env python3
"""Check bundled paths, model identity and recorded-source conversion, without an API."""
import argparse
import json
from pathlib import Path
import time
import numpy as np
import soundfile as sf
import soxr
import service


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo', type=Path, help='Developer validation only: existing pinned model repo')
    args = parser.parse_args()
    if args.repo:
        service.REPO = args.repo.resolve()
    out = service.ROOT / 'runtime/portable-check'
    out.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    engine = service.Engine(service.default_profiles(), receipt_dir=out/'startup')
    assert set(engine.profiles) == {'ivan-en-v1', 'saku-ja-v1', 'koharu-ja-v1', 'love-ja-v1', 'reiji-ja-v1'}
    assert set(engine.prefix_assets) == set(engine.profiles)
    runner = engine.new_runner('ivan-en-v1', 'prefix_quality')
    source, rate = sf.read(service.ROOT / 'outputs/local-realtime-20261022/gemini_native-live.wav', dtype='int16')
    assert rate == 24000 and source.ndim == 1
    source = soxr.resample(source.astype(np.float32)/32767, 24000, 16000, quality='HQ')
    parts = [engine.convert(runner, source[pos:pos+2560]) for pos in range(0, len(source), 2560)]
    parts.append(engine.flush(runner))
    wave = np.concatenate(parts)
    pcm = np.clip(np.rint(wave*32768), -32768, 32767).astype('<i2')
    expected, rate = sf.read(service.ROOT/'outputs/streaming-recheck-20261022/causal/05-prefix-grid-delay1280.wav', dtype='int16')
    assert rate == 16000 and pcm.shape == expected.shape
    error = int(np.max(np.abs(pcm.astype(np.int32)-expected.astype(np.int32))))
    assert error <= 8, f'Accepted-source comparison differs by {error} PCM units; inspect runtime/weights'
    sf.write(out/'portable-replay.wav', pcm, 16000, subtype='PCM_16')
    for profile in engine.profiles:
        if profile == 'ivan-en-v1':
            continue
        candidate = engine.new_runner(profile, 'prefix_quality')
        pieces = [engine.convert(candidate, source[pos:pos+2560]) for pos in range(0, len(source), 2560)]
        pieces.append(engine.flush(candidate))
        converted = np.concatenate(pieces)
        assert len(converted) == len(wave) and np.isfinite(converted).all(), profile
    result = {'passed':True,'model_revision':engine.model_revision,'processing_mode':'prefix_quality',
              'converted_profiles':list(engine.profiles),
              'max_pcm_difference_from_accepted_sample':error,'elapsed_seconds':round(time.perf_counter()-started,3),
              'scope':'Recorded source, local CPU; no Gemini request, browser playback or live quality claim'}
    (out/'receipt.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
