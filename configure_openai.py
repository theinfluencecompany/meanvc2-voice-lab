"""Save a separately supplied GPT Live API key; never reads Codex credentials."""
import getpass
from urllib.parse import urlsplit

from configure import save_settings

DEFAULT_URL = 'wss://api.openai.com/v1/live/sessions'


def validate_url(value):
    try:
        parsed = urlsplit(value)
        valid_port = parsed.port is None or 1 <= parsed.port <= 65535
    except ValueError:
        raise ValueError('Invalid GPT Live WebSocket URL.') from None
    local = parsed.hostname in {'localhost', '127.0.0.1', '::1'}
    if (not value or any(c.isspace() for c in value) or not parsed.hostname or not valid_port
            or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment
            or parsed.scheme not in ({'ws', 'wss'} if local else {'wss'})):
        raise ValueError('Use wss:// without credentials, query or fragment; ws:// is allowed only on loopback.')
    return value


def main():
    key = getpass.getpass('GPT Live test API key (hidden; separate from Gemini): ').strip()
    if not key or any(c.isspace() for c in key):
        raise SystemExit('No key saved: provide a nonempty key without whitespace.')
    url = input(f'GPT Live WebSocket URL [{DEFAULT_URL}]: ').strip() or DEFAULT_URL
    try:
        validate_url(url)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    save_settings({'OPENAI_LIVE_API_KEY':key, 'OPENAI_LIVE_URL':url})
    print('Saved locally. GPT-Live-1 defaults to Echo; Shida and Harema can be selected in the page. Access is checked on connection.')


if __name__ == '__main__':
    main()
