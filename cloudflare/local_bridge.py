"""Outbound WSS relay to the local Gemini gateway; API keys stay in gateway.

Wire messages: connect(sessionId), message(sessionId,data|binary), close(sessionId).
All errors/logs are fixed text; never log token-bearing URLs or conversation data.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import json
import os
import stat
from pathlib import Path

from aiohttp import ClientSession, ClientTimeout, WSMsgType, WSServerHandshakeError

ALLOWED_MODES = frozenset({'gemini_vc_quality', 'gemini_native'})


def load_token(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError('Token must be a regular file')
    if os.name != 'nt' and stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError('Token file permissions must be 0600')
    token = path.read_text().strip()
    if not 43 <= len(token) <= 128 or not all(c.isalnum() or c in '-_' for c in token):
        raise ValueError('Token file must contain a strong URL-safe token')
    return token


class LocalBridge:
    def __init__(self, host, client, upstream='http://127.0.0.1:8773/ws', max_seconds=600):
        self.host = host
        self.client = client
        self.upstream = upstream
        self.max_seconds = min(max_seconds, 600)
        self.current = None

    async def emit_close(self, sid, code=1000, reason='Session ended'):
        with contextlib.suppress(Exception):
            await self.host.send_json({'type': 'close', 'sessionId': sid, 'code': code, 'reason': reason})

    async def close(self, *, notify=True, code=1000, reason='Session ended'):
        current, self.current = self.current, None
        if current is None:
            return
        for task in current['tasks']:
            if task is not asyncio.current_task():
                task.cancel()
        await asyncio.gather(*[t for t in current['tasks'] if t is not asyncio.current_task()], return_exceptions=True)
        with contextlib.suppress(Exception):
            await current['upstream'].close()
        if notify:
            await self.emit_close(current['id'], code, reason)

    async def connect(self, sid):
        if self.current is not None:
            await self.emit_close(sid, 1013, 'Preview busy')
            return
        try:
            upstream = await self.client.ws_connect(self.upstream, origin='http://127.0.0.1:33330',
                                                    heartbeat=20, max_msg_size=1024 * 1024)
        except Exception:
            await self.emit_close(sid, 1013, 'Preview unavailable')
            return
        current = {'id': sid, 'upstream': upstream, 'tasks': []}
        self.current = current

        async def relay_upstream():
            try:
                async for message in upstream:
                    if self.current is not current:
                        break
                    if message.type == WSMsgType.TEXT:
                        await self.host.send_json({'type': 'message', 'sessionId': sid, 'data': message.data})
                    elif message.type == WSMsgType.BINARY:
                        await self.host.send_json({'type': 'message', 'sessionId': sid,
                                                  'binary': base64.b64encode(message.data).decode('ascii')})
                    else:
                        break
            except Exception:
                pass
            finally:
                if self.current is current:
                    await self.close()

        async def timeout():
            await asyncio.sleep(self.max_seconds)
            if self.current is current:
                await self.close(code=1000, reason='Preview session limit reached')

        current['tasks'] = [asyncio.create_task(relay_upstream()), asyncio.create_task(timeout())]

    async def consume(self, event):
        if not isinstance(event, dict):
            return
        sid = event.get('sessionId')
        if not isinstance(sid, str) or not 1 <= len(sid) <= 128:
            return
        kind = event.get('type')
        if kind == 'connect':
            await self.connect(sid)
            return
        if self.current is None or self.current['id'] != sid:
            return
        if kind == 'close':
            await self.close(notify=False)
            return
        if kind != 'message':
            return
        try:
            if 'data' in event:
                data = event['data']
                if not isinstance(data, str) or len(data.encode()) > 16384:
                    raise ValueError('Invalid text')
                control = json.loads(data)
                if not isinstance(control, dict):
                    raise ValueError('Invalid control')
                if control.get('type') == 'start' and control.get('mode') not in ALLOWED_MODES:
                    raise ValueError('Invalid mode')
                await self.current['upstream'].send_str(data)
            elif 'binary' in event:
                encoded = event['binary']
                if not isinstance(encoded, str) or len(encoded) > 4300:
                    raise ValueError('Invalid audio')
                pcm = base64.b64decode(encoded, validate=True)
                if not pcm or len(pcm) > 3200 or len(pcm) % 2:
                    raise ValueError('Invalid audio')
                await self.current['upstream'].send_bytes(pcm)
            else:
                raise ValueError('Missing payload')
        except Exception:
            await self.close(code=1008, reason='Invalid preview message')

    async def run(self):
        try:
            async for message in self.host:
                if message.type == WSMsgType.TEXT:
                    try:
                        event = message.json()
                    except (ValueError, TypeError):
                        continue
                    await self.consume(event)
                elif message.type in {WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR}:
                    break
        finally:
            await self.close(notify=False)


async def run(worker_url, token_file):
    if not worker_url.startswith('wss://') or '?' in worker_url:
        raise ValueError('Use the HTTPS worker WSS endpoint without query parameters')
    token = load_token(token_file)
    timeout = ClientTimeout(total=None, connect=10, sock_connect=10)
    while True:
        try:
            async with ClientSession(timeout=timeout) as client:
                async with client.ws_connect(worker_url, headers={
                    'Authorization': 'Bearer ' + token,
                    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36',
                },
                                             heartbeat=20, max_msg_size=2 * 1024 * 1024) as host:
                    print(json.dumps({'event': 'relay_connected'}), flush=True)
                    await LocalBridge(host, client).run()
        except asyncio.CancelledError:
            raise
        except WSServerHandshakeError as error:
            print(json.dumps({'event': 'relay_handshake_rejected', 'status': error.status}), flush=True)
            # A deployment can briefly retain the old host socket; retry 409.
            if error.status in {401, 403, 404}:
                return
        except Exception as error:
            print(json.dumps({'event': 'relay_disconnected', 'class': type(error).__name__}), flush=True)
        await asyncio.sleep(3)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--worker-url', required=True)
    parser.add_argument('--token-file', type=Path, default=Path(__file__).resolve().parent / 'private/host-token.txt')
    args = parser.parse_args()
    try:
        asyncio.run(run(args.worker_url, args.token_file))
    except KeyboardInterrupt:
        pass
