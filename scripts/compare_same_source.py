#!/usr/bin/env python3
"""Make controlled whole/prefix-stream listening pairs from bundled recordings.

No API request, microphone capture, live-service restart, parameter search, or
listening-quality score is performed. Both paths use exactly the same 16 kHz
PCM16 file, verified official speaker cache, weights, and initial RNG state.
The official and runtime DiT classes are disclosed separately. The input is
replayed in 160 ms packets without sleeping; processing
times are offline CPU measurements, not conversational latency.

Run after setup.py, or reuse a pinned existing MeanVC2 checkout:
  .venv/bin/python scripts/compare_same_source.py --repo /path/to/MeanVC2
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import importlib
import json
from pathlib import Path
import platform
import sys
import time
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
SERVICE_DIR = ROOT / "work/ivan-jp-pilot/voice-service"
DEFAULT_PROFILES = ("ivan-en-v1", "reiji-ja-v1")
LANGUAGES = ("ja", "en")
RATE = 16000
PACKET_SAMPLES = 2560


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def array_sha256(array) -> str:
    return hashlib.sha256(array.tobytes()).hexdigest()


def repo_path(path: Path) -> str:
    """Manifests contain portable paths, never private home-directory paths."""
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.name


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, help="Existing pinned MeanVC2 checkout with models; no downloads")
    parser.add_argument("--output", type=Path, default=ROOT / "runtime/same-source")
    parser.add_argument("--profiles", nargs="+", default=list(DEFAULT_PROFILES), choices=DEFAULT_PROFILES)
    args = parser.parse_args()

    import numpy as np
    import soundfile as sf
    import soxr
    import torch

    sys.path.insert(0, str(SERVICE_DIR))
    import service
    from prefix_quality import PREFIX_POLICY
    from streaming_vocoder import VOCODER_POLICY

    if args.repo:
        service.REPO = args.repo.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    profiles = {name: config for name, config in service.default_profiles().items() if name in args.profiles}
    engine = service.Engine(profiles, receipt_dir=output / "startup")

    # Engine imports the upstream runtime's src package. Extend that package's
    # search path for upstream src.model without replacing its loaded modules.
    # infer_e2e and both of the functions called below remain unmodified.
    upstream_src = importlib.import_module("src")
    if str(service.REPO / "src") not in upstream_src.__path__:
        upstream_src.__path__.append(str(service.REPO / "src"))
    sys.path.insert(0, str(service.REPO / "src/infer"))
    official = importlib.import_module("infer_e2e")
    preset = official.MODEL_PRESETS["120ms"]
    model_config = json.loads(Path(preset["vc_config"]).read_text())
    official_model = official.DiT(**model_config["model"]).to("cpu")
    official_model = official.load_checkpoint(
        official_model, preset["vc_ckpt"], device="cpu", use_ema=True,
    ).float().eval()

    sources = {}
    for language in LANGUAGES:
        source_path = ROOT / f"web/public/source-voices/Schedar-{language}.wav"
        source_pcm, source_rate = sf.read(source_path, dtype="int16", always_2d=True)
        # One preprocessing pass per language. Both converters subsequently read
        # this same PCM16 representation with the official /32767 convention.
        mono = source_pcm.astype(np.float64).mean(axis=1) / 32767.0
        if source_rate != RATE:
            mono = soxr.resample(mono, source_rate, RATE, quality="HQ")
        pcm = np.clip(np.rint(mono * 32767.0), -32768, 32767).astype("<i2")
        sources[language] = (source_path, source_pcm, source_rate, pcm)

    cases = []
    for profile in args.profiles:
        embedding, initial_rng, asset_manifest = engine.prefix_assets[profile]
        cache_manifest_path = Path(engine.profiles[profile].get(
            "prefix_assets_manifest", SERVICE_DIR / "prefix-assets/manifest.json"))
        embedding_name = asset_manifest.get("speaker_embedding_asset", "ivan-official-speaker.npy")
        official_cache_path = cache_manifest_path.parent / embedding_name
        reference_path = Path(engine.profiles[profile]["reference_path"])
        for language in LANGUAGES:
            case_id = f"{profile}-{language}"
            case_dir = output / case_id
            case_dir.mkdir(parents=True, exist_ok=True)
            source_path, source_pcm, source_rate, pcm = sources[language]
            input_path = case_dir / "input16k.wav"
            sf.write(input_path, pcm, RATE, subtype="PCM_16")
            shared_pcm, read_rate = sf.read(input_path, dtype="int16")
            if read_rate != RATE or not np.array_equal(shared_pcm, pcm):
                raise RuntimeError("Prepared PCM16 roundtrip changed the shared source")
            shared_float = shared_pcm.astype(np.float32) / np.iinfo(np.int16).max

            # Whole path: official full-file BN extraction, official VC schedule,
            # and one whole-mel Vocos decode. The official DiT class is retained,
            # while streaming uses its actual runtime DiT class. This is a real
            # path comparison, not proof of any single cause of quality changes.
            t0 = time.perf_counter()
            with torch.inference_mode():
                bn = official.extract_bn(
                    str(input_path), engine.template.asr,
                    window=preset["bn_window"], stride=preset["bn_stride"],
                    offset_init=preset["offset_init"], offset_step=preset["offset_step"],
                    req_cache=preset["req_cache"],
                )
                t_bn = time.perf_counter()
                with torch.random.fork_rng(devices=[]):
                    torch.set_rng_state(initial_rng.clone())
                    _, whole_tensor = official.vc_inference(
                        official_model, engine.template.vocoder, bn,
                        embedding.squeeze(0).numpy().copy(), 12, 4, 3, "cpu",
                    )
                whole = whole_tensor.detach().cpu().reshape(-1).numpy()
            t_whole = time.perf_counter()

            runner = engine.new_runner(profile, processing_mode="prefix_quality")
            if not torch.equal(runner.rng.get_state(), initial_rng):
                raise RuntimeError("Stream RNG differs from whole-path initial state")
            if not torch.equal(runner.speaker, embedding):
                raise RuntimeError("Stream speaker differs from official cache")
            parts, packets = [], []
            first_output_input_samples = None
            t_stream = time.perf_counter()
            for pos in range(0, len(shared_float), PACKET_SAMPLES):
                chunk = shared_float[pos:pos + PACKET_SAMPLES]
                t_chunk = time.perf_counter()
                converted = engine.convert(runner, chunk)
                chunk_seconds = time.perf_counter() - t_chunk
                parts.append(converted)
                arrived = pos + len(chunk)
                if len(converted) and first_output_input_samples is None:
                    first_output_input_samples = arrived
                packets.append({"input_samples_total": arrived,
                                "output_samples": len(converted),
                                "compute_seconds": chunk_seconds})
            t_flush = time.perf_counter()
            tail = engine.flush(runner)
            flush_seconds = time.perf_counter() - t_flush
            parts.append(tail)
            if len(tail) and first_output_input_samples is None:
                first_output_input_samples = len(shared_float)
            stream_seconds = time.perf_counter() - t_stream
            stream = np.concatenate(parts)
            for label, wave in (("whole", whole), ("stream", stream)):
                if not len(wave) or not np.isfinite(wave).all() or not np.any(wave):
                    raise RuntimeError(f"{case_id} {label}: invalid output")
                sf.write(case_dir / f"{label}.wav", wave, RATE, subtype="PCM_16")

            manifest = {
                "schema": "meanvc2.same-source.v1",
                "case_id": case_id,
                "character_id": profile.split("-")[0],
                "character_label": engine.profiles[profile]["label"],
                "profile": profile,
                "language": language,
                "source_voice": "Schedar",
                "created_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
                "scope": "Offline replay of a previously generated Gemini recording, not a live API call or latency benchmark. Listening quality is not scored.",
                "source": {
                    "path": repo_path(source_path), "sha256": sha256(source_path),
                    "sample_rate": source_rate, "channels": source_pcm.shape[1],
                    "samples": len(source_pcm), "duration_seconds": len(source_pcm) / source_rate,
                    "preprocessing": "One shared pass: PCM16 /32767, channel mean if needed, soxr HQ to 16000 Hz if needed, round*32767 and clip to PCM16. Both paths then use identical input16k.wav bytes and official PCM16 /32767 normalization.",
                },
                "input": {"file": "input16k.wav", "sha256": sha256(input_path),
                          "pcm_sha256": array_sha256(shared_pcm.astype("<i2")),
                          "sample_rate": RATE, "channels": 1,
                          "samples": len(shared_pcm), "duration_seconds": len(shared_pcm) / RATE},
                "reference": {"path": repo_path(reference_path), "sha256": sha256(reference_path)},
                "speaker_cache": {"path": repo_path(official_cache_path), "sha256": sha256(official_cache_path),
                                  "extraction": "Verified official infer_e2e speaker cache; not legacy runtime embedding"},
                "rng": {"initial_state_sha256": array_sha256(initial_rng.numpy()),
                        "asset_sha256": sha256(cache_manifest_path.parent / "cli-inference-rng.pt"),
                        "policy": "Identical frozen initial CPU state; official full sequence versus prefix absolute-position draws"},
                "model": {"upstream_commit": engine.model_revision, "fingerprint": engine.model_fingerprint,
                          "preset": "120ms", "future_context_ms": 40, "ode_steps": 3,
                          "device": "cpu", "torch_threads": 1,
                          "shared_objects": "Both paths use Engine.template.asr and vocoder; DiT implementations differ but load the same pinned weights",
                          "whole_vc_class": f"{type(official_model).__module__}.{type(official_model).__name__}",
                          "stream_vc_class": f"{type(engine.template.vc).__module__}.{type(engine.template.vc).__name__}",
                          "whole_vc_code_sha256": sha256(service.REPO / "src/infer/dit_kvcache.py"),
                          "stream_vc_code_sha256": sha256(service.REPO / "runtime/src/dit.py"),
                          "official_functions_sha256": sha256(Path(official.__file__)),
                          "adapter_revision": engine.adapter_revision},
                "whole": {"file": "whole.wav", "sha256": sha256(case_dir / "whole.wav"),
                          "method": "Unmodified infer_e2e.extract_bn + infer_e2e.vc_inference; full-utterance feature interpolation and whole-mel Vocos decode",
                          "duration_seconds": len(whole) / RATE, "samples": len(whole),
                          "feature_compute_seconds": t_bn - t0,
                          "conversion_compute_seconds": t_whole - t_bn,
                          "compute_seconds": t_whole - t0},
                "stream": {"file": "stream.wav", "sha256": sha256(case_dir / "stream.wav"),
                           "method": "Engine.new_runner(prefix_quality); only arrived PCM supplied in 160 ms packets, then finish; no sleeps or API transport",
                           "duration_seconds": len(stream) / RATE, "samples": len(stream),
                           "packet_samples": PACKET_SAMPLES, "packet_ms": PACKET_SAMPLES / RATE * 1000,
                           "first_output_after_input_samples": first_output_input_samples,
                           "first_output_after_input_ms": first_output_input_samples / RATE * 1000,
                           "first_output_before_eof": first_output_input_samples < len(shared_pcm),
                           "compute_seconds": stream_seconds, "flush_compute_seconds": flush_seconds,
                           "prefix_policy": PREFIX_POLICY, "vocoder_policy": VOCODER_POLICY,
                           "trace_file": "stream-trace.json"},
                "measurement_notes": [
                    "160 ms is a replay packet size, not response delay. Input required before first output measures buffering only.",
                    "Compute excludes model loading, source preparation, API generation, network and audio playback. Whole runs first; no benchmark warmup or repetition.",
                    "Both are fixed prerecorded Schedar inputs; changing the page's live source voice does not regenerate these files.",
                    "This compares the actual official whole path with the actual prefix stream path under shared source, reference, weights and initial RNG. Their DiT implementations also differ; no single-cause attribution or subjective quality, production throughput or full-duplex claim is made.",
                    "Official full-file fbank/ASR omits incomplete end frames; prefix finish uses real frames only and does not add ASR padding.",
                ],
                "environment": {"python": platform.python_version(), "torch": torch.__version__,
                                "numpy": np.__version__, "soxr": soxr.__version__,
                                "system": platform.system(), "machine": platform.machine()},
                "generator_sha256": sha256(Path(__file__)),
            }
            write_json(case_dir / "stream-trace.json", {"packets": packets, "vc": runner.trace,
                                                       "flush_output_samples": len(tail)})
            write_json(case_dir / "manifest.json", manifest)
            cases.append(manifest)
            print(json.dumps({"case_id": case_id, "ready": True,
                              "whole_seconds": round(t_whole - t0, 3),
                              "stream_seconds": round(stream_seconds, 3),
                              "first_output_after_input_ms": manifest["stream"]["first_output_after_input_ms"]}), flush=True)

    write_json(output / "manifest.json", {"schema": "meanvc2.same-source.collection.v1", "cases": cases})
    print(f"Saved {len(cases)} listening pairs to {output}", flush=True)


if __name__ == "__main__":
    main()
