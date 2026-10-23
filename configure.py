"""Save a Gemini API key on this computer, without echoing it or sending it anywhere."""
from pathlib import Path
import getpass
import os

ROOT = Path(__file__).resolve().parent
path = ROOT / 'work/ivan-jp-pilot/local-call/.env.local'
key = getpass.getpass('Gemini API key (hidden): ').strip()
if not key or any(c.isspace() for c in key):
    raise SystemExit('No key saved: provide a nonempty key without whitespace.')
model = input('Gemini Live model [gemini-3.8-live]: ').strip() or 'gemini-3.8-live'
if any(c.isspace() for c in model):
    raise SystemExit('No key saved: model name must not contain whitespace.')
path.parent.mkdir(parents=True, exist_ok=True)
with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as f:
    f.write(f'GEMINI_API_KEY={key}\nGEMINI_LIVE_MODEL={model}\n')
os.chmod(path, 0o600)
print('Saved locally. Run start.py. Provider access is checked when you connect.')
