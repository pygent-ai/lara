# 远程访问（手机浏览器）

Lara 的 Agent 始终运行在电脑本机。远程访问让手机浏览器打开同一个界面，消息仍在电脑上执行，文件、工具、模型配置全部留在本机。传输层使用 Tailscale（个人使用免费），不向公网暴露任何端口。

## 一次性准备

1. 电脑与手机安装 Tailscale 并登录同一账号。
2. 电脑上构建 renderer 并启动带静态托管的服务：

   ```powershell
   npm --prefix apps/desktop run build
   uv run lara-api --workspace-root E:\Projects\lora --web-dist apps\desktop\dist
   ```

3. 电脑上将本地服务暴露给 tailnet：

   ```powershell
   tailscale serve --bg 8765
   ```

   得到形如 `https://<机器名>.<tailnet名>.ts.net` 的地址，自动签发 HTTPS 证书。

4. 手机浏览器打开该地址即可使用；可"添加到主屏幕"获得接近 App 的体验。首次输入地址后不再需要扫码或配对。

## 工作原理

- `--web-dist` 让 `lara-api` 把构建产物挂载在 `/`（注册在所有 API 路由之后，API 路径优先），前端页面与聊天 API 同源，浏览器无需 CORS。
- 前端 baseUrl 选择顺序见 `apps/desktop/renderer/src/shared/api/client.js` 的 `defaultBaseUrl()`：Electron 注入值 > `VITE_LARA_API_BASE_URL` > http(s) 页面用同源相对路径 > 本地默认 `127.0.0.1:8765`。因此桌面 Electron 行为不变，浏览器托管场景自动指向服务本身。
- `tailscale serve` 只做反代：手机 → Tailscale 加密隧道 → 电脑 `tailscale serve`（HTTPS）→ `localhost:8765`。聊天走 `POST /chat/stream` SSE，断线用 `execution_id` + `after_sequence` 重连，手机弱网下可用。

## 安全与限制

- 访问边界是 tailnet 成员身份：tailnet 内的设备可以使用完整 API，包括终端和工作区工具。不要邀请不信任的设备；更细粒度的隔离需要再引入令牌鉴权。
- 不要改用 ngrok/cloudflared 等公网隧道或路由器端口转发，那会把完整 Agent API 暴露到公网。
- 手机能连上的前提是电脑开机且 `lara-api` 在运行。
- 撤销暴露：`tailscale serve reset`。

## 故障排查

- 手机打开地址无响应：确认电脑 `tailscale status` 正常、`lara-api` 在跑、`tailscale serve status` 显示 8765 的转发。
- 页面能开但报模型未配置：远程页面使用的是电脑本机 `~/.lara/config.yaml`，与桌面版共用，无需重复配置。
