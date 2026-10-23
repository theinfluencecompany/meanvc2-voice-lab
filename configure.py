"""Save a Gemini API key on this computer, without echoing it or sending it anywhere."""
from pathlib import Path
import getpass
import os

ROOT = Path(__file__).resolve().parent
SETTINGS_PATH = ROOT / 'work/ivan-jp-pilot/local-call/.env.local'


def save_settings(updates):
    """Update only this provider's fields, preserving the other provider's key."""
    if any(not value or any(c.isspace() for c in value) for value in updates.values()):
        raise ValueError('Settings must be nonempty and contain no whitespace.')
    lines = SETTINGS_PATH.read_text().splitlines() if SETTINGS_PATH.exists() else []
    kept = [line for line in lines if line.split('=', 1)[0].strip() not in updates]
    kept.extend(f'{key}={value}' for key, value in updates.items())
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with os.fdopen(os.open(SETTINGS_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as f:
        f.write('\n'.join(kept) + '\n')
    os.chmod(SETTINGS_PATH, 0o600)


def main():
    key = getpass.getpass('Gemini API key (hidden): ').strip()
    if not key or any(c.isspace() for c in key):
        raise SystemExit('No key saved: provide a nonempty key without whitespace.')
    model = input('Gemini Live model [gemini-3.8-live]: ').strip() or 'gemini-3.8-live'
    if any(c.isspace() for c in model):
        raise SystemExit('No key saved: model name must not contain whitespace.')
    save_settings({'GEMINI_API_KEY':key, 'GEMINI_LIVE_MODEL':model})
    print('Saved locally. Run start.py. Provider access is checked when you connect.')


if __name__ == '__main__':
    main()
