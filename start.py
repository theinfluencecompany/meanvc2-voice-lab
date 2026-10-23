"""Start the local preview; Ctrl+C stops only the processes started here."""
from pathlib import Path
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parent


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path.split('?', 1)[0] in ('/local-voice', '/local-voice/'):
            self.path = '/index.html'
        super().do_GET()

    def log_message(self, *args):
        pass


def check_free(port):
    with socket.socket() as s:
        try:
            s.bind(('127.0.0.1', port))
        except OSError:
            raise RuntimeError(f'Port {port} is already occupied. Existing services were not stopped.') from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preview-only', action='store_true', help='Only page and recordings; no models/API needed')
    parser.add_argument('--no-open', action='store_true')
    parser.add_argument('--port', type=int, default=33330, help='Alternative port allowed only with --preview-only')
    args = parser.parse_args()
    if not args.preview_only and args.port != 33330:
        raise RuntimeError('Live mode requires port 33330.')
    if not (ROOT / 'web/dist/index.html').exists():
        raise RuntimeError('Missing web/dist. Run npm ci and npm run build inside web/ first.')
    for port in ([args.port] if args.preview_only else [33330, 8771, 8773]):
        check_free(port)
    processes, logs = [], []
    server = None
    try:
        if not args.preview_only:
            python = ROOT / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
            if not python.exists():
                raise RuntimeError('Run Python 3.11 setup.py first.')
            (ROOT / 'logs').mkdir(exist_ok=True)
            base = ROOT / 'work/ivan-jp-pilot'
            specs = [
                ('voice', 8771, [base / 'voice-service/service.py', '--port', '8771', '--profiles',
                                ROOT / 'outputs/ivan-jp-pilot-20261020/voice-service/ivan-reference/profiles.json']),
                ('gateway', 8773, [base / 'local-call/server.py']),
            ]
            for name, port, arguments in specs:
                log = (ROOT / 'logs' / (name + '.log')).open('ab')
                logs.append(log)
                proc = subprocess.Popen([str(python), '-u', *map(str, arguments)], cwd=ROOT,
                                        stdout=log, stderr=log, stdin=subprocess.DEVNULL)
                processes.append(proc)
                ready = False
                for _ in range(240):
                    if proc.poll() is not None:
                        raise RuntimeError(f'{name} stopped; see logs/{name}.log')
                    try:
                        with urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=1) as r:
                            data = json.load(r)
                        ready = data.get('ready') is True if name == 'voice' else data.get('type') == 'capabilities'
                        if ready:
                            break
                    except (OSError, ValueError):
                        pass
                    time.sleep(.5)
                if not ready:
                    raise RuntimeError(f'{name} did not become ready; see logs/{name}.log')
                print(name + ' ready', flush=True)
        server = ThreadingHTTPServer(('127.0.0.1', args.port), partial(Handler, directory=str(ROOT / 'web/dist')))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        url = f'http://127.0.0.1:{args.port}/'
        print(url + (' — recordings only' if args.preview_only else ' — live preview'), flush=True)
        print('Keep this terminal open. Ctrl+C to stop.', flush=True)
        if not args.no_open:
            webbrowser.open(url)
        while True:
            time.sleep(1)
            if any(p.poll() is not None for p in processes):
                raise RuntimeError('A backend stopped; see logs/.')
    finally:
        if server:
            server.shutdown()
            server.server_close()
        for p in reversed(processes):
            if p.poll() is None:
                p.terminate()
        for p in processes:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()
        for log in logs:
            log.close()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nStopped.')
    except Exception as error:
        print(f'Start failed: {error}', file=sys.stderr)
        sys.exit(1)
