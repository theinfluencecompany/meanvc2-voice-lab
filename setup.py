"""Install the pinned MeanVC2 CPU runtime. Does not read API credentials."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import urllib.request
import venv

ROOT = Path(__file__).resolve().parent
REPO = ROOT / 'work/meanvc2-lab/repo'
REVISION = '13acf84c1bf135ea5edad9c245b345289b06b33e'
UPSTREAM = 'https://github.com/ASLP-lab/MeanVC2.git'


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def prepare_repo():
    if not shutil.which('git'):
        raise RuntimeError('Install Git first, then run setup.py again.')
    if not REPO.exists():
        REPO.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(['git', 'clone', UPSTREAM, str(REPO)], check=True)
        subprocess.run(['git', 'checkout', '--detach', REVISION], cwd=REPO, check=True)
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=REPO, text=True).strip()
    if head != REVISION:
        raise RuntimeError('Existing MeanVC2 revision differs; refusing to overwrite it.')
    patch = str(ROOT / 'vendor/meanvc2-local.patch')
    if subprocess.run(['git', 'apply', '--reverse', '--check', patch], cwd=REPO,
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
        subprocess.run(['git', 'apply', '--check', patch], cwd=REPO, check=True)
        subprocess.run(['git', 'apply', patch], cwd=REPO, check=True)
    cfg = ROOT / 'vendor/wavlm_large_cfg.pt'
    if sha(cfg) != 'aade57d9ff18f7266a712329ebf9e15cb71d761fe7031fb1b90290d5cd421abe':
        raise RuntimeError('Bundled WavLM config checksum mismatch.')
    target = REPO / 'preprocess/ckpts/wavlm_large_cfg.pt'
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(cfg, target)


def fetch_models():
    for item in json.loads((ROOT / 'vendor/models.json').read_text()):
        target = REPO / item['path']
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if sha(target) == item['sha256']:
                print('Verified:', target.name, flush=True)
                continue
            raise RuntimeError(f'Checksum mismatch: {target.name}; move this file aside and retry.')
        temporary = target.with_suffix(target.suffix + '.download')
        print(f"Downloading {target.name} ({item['bytes'] / 1e6:.0f} MB)...", flush=True)
        request = urllib.request.Request(item['url'], headers={'User-Agent': 'MeanVC2-voice-lab/1.0'})
        with urllib.request.urlopen(request, timeout=120) as response, temporary.open('wb') as output:
            shutil.copyfileobj(response, output, 1024 * 1024)
        if sha(temporary) != item['sha256']:
            temporary.unlink(missing_ok=True)
            raise RuntimeError('Downloaded model checksum mismatch; retry setup.')
        temporary.replace(target)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--assets-only', action='store_true', help='Prepare source/models; skip Python installation')
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 11) and not args.assets_only:
        raise RuntimeError('Use Python 3.11: python3.11 setup.py (Windows: py -3.11 setup.py).')
    if not args.assets_only and sys.platform == "darwin" and platform.machine() == "x86_64":
        raise RuntimeError("This pinned PyTorch runtime requires Apple Silicon on macOS; Intel Mac is unsupported.")
    prepare_repo()
    if not args.assets_only:
        env = ROOT / '.venv'
        if not env.exists():
            venv.EnvBuilder(with_pip=True).create(env)
        python = env / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        subprocess.run([str(python), '-m', 'pip', 'install', '--upgrade', 'pip'], check=True)
        torch_cmd = [str(python), '-m', 'pip', 'install', 'torch==2.5.1', 'torchaudio==2.5.1']
        if sys.platform != 'darwin':
            torch_cmd += ['--index-url', 'https://download.pytorch.org/whl/cpu']
        subprocess.run(torch_cmd, check=True)
        subprocess.run([str(python), '-m', 'pip', 'install', '-r', str(ROOT / 'requirements.txt')], check=True)
    fetch_models()
    print('Ready. Run configure.py to set your Gemini key, then start.py.')


if __name__ == '__main__':
    try:
        main()
    except (Exception, KeyboardInterrupt) as error:
        print(f'Setup stopped: {error}', file=sys.stderr)
        sys.exit(1)
