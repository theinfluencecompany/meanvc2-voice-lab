"""Copy explicitly generated comparison recordings into the static preview."""
from pathlib import Path
import argparse
import hashlib
import json
import shutil

ROOT = Path(__file__).resolve().parents[1]


def publish(input_root, public_root):
    rows = []
    for profile in ('reiji-ja-v1', 'ivan-en-v1'):
        for language in ('ja', 'en'):
            case_id = f'{profile}-{language}'
            source = input_root / case_id
            data = json.loads((source / 'manifest.json').read_text())
            target = public_root / 'same-source' / case_id
            target.mkdir(parents=True, exist_ok=True)
            for section in ('input', 'whole', 'stream'):
                name = data[section]['file']
                if Path(name).name != name:
                    raise ValueError('Expected a direct audio filename')
                asset = source / name
                if hashlib.sha256(asset.read_bytes()).hexdigest() != data[section]['sha256']:
                    raise ValueError(f'Changed audio: {case_id}/{name}')
                shutil.copyfile(asset, target / name)
            reference = (ROOT / data['reference']['path']).resolve()
            reference.relative_to(ROOT)
            if hashlib.sha256(reference.read_bytes()).hexdigest() != data['reference']['sha256']:
                raise ValueError('Changed reference recording')
            shutil.copyfile(reference, target / 'reference.wav')
            shutil.copyfile(source / 'manifest.json', target / 'manifest.json')
            shutil.copyfile(source / 'stream-trace.json', target / 'stream-trace.json')
            url = f'./same-source/{case_id}'
            rows.append({
                'id': case_id, 'character': data['character_id'], 'name': data['character_label'],
                'language': language, 'sourceVoice': data['source_voice'],
                'input': f'{url}/input16k.wav', 'reference': f'{url}/reference.wav',
                'whole': f'{url}/whole.wav', 'stream': f'{url}/stream.wav',
                'sourceSeconds': data['input']['duration_seconds'],
                'wholeComputeSeconds': data['whole']['compute_seconds'],
                'streamComputeSeconds': data['stream']['compute_seconds'],
                'firstStreamInputSeconds': data['stream']['first_output_after_input_ms'] / 1000,
                'sourceSha256': data['input']['sha256'], 'referenceSha256': data['reference']['sha256'],
            })
    (public_root / 'same-source.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2) + '\n')
    print(f'Published {len(rows)} same-source comparisons to {public_root}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-root', type=Path, default=ROOT / 'runtime/same-source')
    parser.add_argument('--public-root', type=Path, default=ROOT / 'web/public')
    args = parser.parse_args()
    publish(args.input_root, args.public_root)
