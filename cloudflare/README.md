# 可选公网中转

先确保本机实时体验可用。Worker 不运行 MeanVC2，只在浏览器和你电脑上的网关之间转发 WebSocket。公开仓库不附带任何可用的体验码或 host token。

1. 在 `wrangler.jsonc` 设置自己的 Worker 名称和 `vars.PUBLIC_ORIGIN`，例如 `https://your-voice-lab.your-account.workers.dev`。
2. 将 `web/public/share-config.json` 的 `gatewayUrl` 改成对应的 `wss://your-voice-lab.your-account.workers.dev/ws`。
3. 使用相同 origin 构建前端：

   ```bash
   VITE_PUBLIC_ORIGIN=https://your-voice-lab.your-account.workers.dev npm --prefix web run build
   ```

4. 在仓库根目录生成两个不同的随机 token，保存在 Git 忽略的 `private/` 中：

   ```bash
   python3 - <<'PY'
   from pathlib import Path
   import os, secrets
   folder = Path('private')
   folder.mkdir(exist_ok=True)
   for name in ('invite-token.txt', 'host-token.txt'):
       path = folder / name
       if path.exists():
           raise SystemExit(f'{path} already exists; not overwritten')
       with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as f:
           f.write(secrets.token_urlsafe(32))
   PY
   ```

5. 在 `cloudflare/` 执行 `wrangler secret put INVITE_TOKEN` 和 `wrangler secret put HOST_TOKEN`，分别粘贴上述两个文件的值，再执行 `wrangler deploy`。API Key 不放入 Worker。
6. 运行本机 `python3.11 start.py`，另开终端启动出站桥接：

   ```bash
   .venv/bin/python cloudflare/local_bridge.py \
     --worker-url wss://your-voice-lab.your-account.workers.dev/relay/host \
     --token-file private/host-token.txt
   ```

7. 给体验者发送页面链接和 invite token，或自行组成 `https://your-voice-lab.your-account.workers.dev/#invite=你的体验码`。host token 只供本机桥接使用。

离开电脑、休眠或停止桥接会中断实时体验。改回本地运行时，将 `share-config.json` 恢复为 `{"gatewayUrl":"ws://127.0.0.1:8773/ws"}`，重新构建。公网一次仅允许一个访客通话，闲置或超过会话时限会断开。
