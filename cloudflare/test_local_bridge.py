import asyncio
import base64
import json
import unittest

from aiohttp import ClientSession, WSMsgType, web
from aiohttp.test_utils import TestServer

from local_bridge import LocalBridge


class FakeHost:
    def __init__(self):
        self.sent = asyncio.Queue()
    async def send_json(self, value):
        await self.sent.put(value)
    async def get(self):
        return await asyncio.wait_for(self.sent.get(), 2)


class BridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.received = []
        self.origins = []
        self.closed = asyncio.Event()

        async def ws(request):
            self.origins.append(request.headers.get('Origin'))
            reply = web.WebSocketResponse()
            await reply.prepare(request)
            await reply.send_str('{"type":"capabilities","modes":[]}')
            try:
                async for message in reply:
                    if message.type == WSMsgType.TEXT:
                        self.received.append(message.data)
                        await reply.send_str(message.data)
                    elif message.type == WSMsgType.BINARY:
                        self.received.append(message.data)
                        await reply.send_bytes(message.data)
            finally:
                self.closed.set()
            return reply

        app = web.Application()
        app.router.add_get('/ws', ws)
        self.server = TestServer(app)
        await self.server.start_server()
        self.client = ClientSession()
        self.host = FakeHost()
        self.bridge = LocalBridge(self.host, self.client, upstream=str(self.server.make_url('/ws')))

    async def asyncTearDown(self):
        await self.bridge.close(notify=False)
        await self.client.close()
        await self.server.close()

    async def connect(self):
        await self.bridge.consume({'type': 'connect', 'sessionId': 'one'})
        caps = await self.host.get()
        self.assertEqual(caps['sessionId'], 'one')
        self.assertEqual(json.loads(caps['data'])['type'], 'capabilities')

    async def test_text_binary_and_origin_preserved(self):
        await self.connect()
        self.assertEqual(self.origins, ['http://127.0.0.1:33330'])
        text = '{"type":"start", "mode":"gemini_vc_quality"}'
        await self.bridge.consume({'type': 'message', 'sessionId': 'one', 'data': text})
        self.assertEqual((await self.host.get())['data'], text)
        pcm = b'\x00\x01' * 640
        encoded = base64.b64encode(pcm).decode()
        await self.bridge.consume({'type': 'message', 'sessionId': 'one', 'binary': encoded})
        self.assertEqual((await self.host.get())['binary'], encoded)
        self.assertEqual(self.received, [text, pcm])

    async def test_single_session_and_close_release(self):
        await self.connect()
        await self.bridge.consume({'type': 'connect', 'sessionId': 'two'})
        busy = await self.host.get()
        self.assertEqual((busy['type'], busy['sessionId'], busy['code']), ('close', 'two', 1013))
        self.assertEqual(len(self.origins), 1)
        await self.bridge.consume({'type': 'close', 'sessionId': 'one'})
        await asyncio.wait_for(self.closed.wait(), 2)
        self.assertIsNone(self.bridge.current)

    async def test_wrong_session_ignored_and_old_mode_rejected(self):
        await self.connect()
        await self.bridge.consume({'type': 'message', 'sessionId': 'wrong', 'data': '{}'})
        await self.bridge.consume({'type': 'message', 'sessionId': 'one',
                                   'data': '{"type":"start","mode":"gemini_vc"}'})
        close = await self.host.get()
        self.assertEqual(close['code'], 1008)
        self.assertEqual(self.received, [])

    async def test_timeout_closes_upstream(self):
        self.bridge.max_seconds = .05
        await self.connect()
        close = await self.host.get()
        self.assertEqual(close['type'], 'close')
        self.assertEqual(close['reason'], 'Preview session limit reached')
        await asyncio.wait_for(self.closed.wait(), 2)
        self.assertIsNone(self.bridge.current)

    async def test_malformed_pcm_closes_upstream(self):
        await self.connect()
        await self.bridge.consume({'type': 'message', 'sessionId': 'one', 'binary': 'invalid!!!'})
        self.assertEqual((await self.host.get())['code'], 1008)
        await asyncio.wait_for(self.closed.wait(), 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
