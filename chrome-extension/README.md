# webai2api 凭证助手（Chrome 扩展）

在浏览器里检测 DeepSeek、Qwen、DuckDuckGo 的登录状态，一键提取 `webai2api` 需要的
`token`、`cookies`、`vqd_hash`、`x-fe-version`、`x-fe-signals`，并生成可粘贴的配置片段。

扩展只读取本机浏览器中的登录凭证，不会发起任何网络请求，也不会上传数据。

## 安装

1. 打开 Chrome，地址栏输入 `chrome://extensions/`。
2. 右上角开启「开发者模式」。
3. 点击「加载已解压的扩展程序」，选择本目录 `chrome-extension/`。
4. 安装后刷新已经打开的 DeepSeek / Qwen / DuckDuckGo 页面，确保扩展能注入脚本并监听请求头。

## 使用

1. 在浏览器中登录对应站点：
   - DeepSeek：<https://chat.deepseek.com/>
   - Qwen：<https://chat.qwen.ai/>
   - DuckDuckGo：<https://duck.ai/>
2. 打开对应站点后，页面右下角会自动出现悬浮窗并开始检测。
   - 检测到登录后，悬浮窗展示掩码后的凭证信息；
   - 信息提取成功后，点击「一键复制配置」即可复制配置片段（未成功前按钮不可用）；
   - 点击右上角「×」可收起为悬浮按钮，当前标签页内不再自动展开；点击悬浮按钮可再次打开。
3. 也可以点击工具栏中的扩展图标，在弹窗中提取和复制。复制后粘贴到 `config.json` 的 `providers` 下；例如：

   ```json
   {
     "providers": {
       "deepseek": {
         "token": "……",
         "cookies": "……"
       }
     }
   }
   ```

4. 也可以点击「下载 config.json」生成一份完整配置（仅适合新配置，已有配置请使用复制片段合并）。

### DuckDuckGo 的 `vqd_hash` 与 `x-fe` 请求头

DuckDuckGo 目前需要 `x-vqd-hash-1` 浏览器挑战值，以及 `x-fe-version`、`x-fe-signals`。安装扩展后：

1. 打开 <https://duck.ai/>；
2. 发送一条消息（触发 `/duckchat/v1/chat` 请求）；
3. 再打开扩展，点击「重新获取」，即可捕获这四个值（`x-ddg-journey-id` 每次请求随机生成，不需要配置）；
4. 推荐粘贴到 `http://127.0.0.1:8787/ui` 的 Duck.ai 面板；也可以复制配置片段到 `config.json`。

`vqd_hash` 和 `fe_signals` 有时效性，失效后重新发送一条消息再获取即可。
扩展会同时捕获与 hash 匹配的 `User-Agent` 和 Cookie，避免浏览器与实际请求不一致。

### 悬浮窗

- 仅在 `chat.deepseek.com`、`chat.qwen.ai`、`duck.ai`（以及 duck.ai 跳转后的
  `duckduckgo.com/?ia=chat` 页面）显示。
- 打开站点后自动展开并检测；未登录时给出对应提示（DeepSeek/Qwen 需登录，DuckDuckGo 需发送一条消息）。
- 只有信息提取成功（DeepSeek / Qwen 有 token、DuckDuckGo 有 `vqd_hash`）时，
  「一键复制配置」才可用。
- 手动点击悬浮按钮也会重新提取；聊天请求产生新的 `Authorization` / `x-vqd-hash-1` 时，
  悬浮窗会自动刷新。
- 复制内容为 `{"providers": {...}}` 片段，直接合并进现有配置即可。

### Qwen 的 token

优先读取页面 Local Storage / Cookie 中的 `token`。如果未检测到，先在 Qwen 页面发送一条消息，
扩展会在后台捕获请求头中的 `Authorization`。若仍然没有，请确认已登录且刷新页面后重试。

## 凭证映射

| 站点 | 配置字段 | 来源 |
| --- | --- | --- |
| DeepSeek | `providers.deepseek.token` | Local Storage `userToken`（JSON 的 `value`）或请求头 |
| DeepSeek / Qwen | `providers.*.cookies` | `chrome.cookies` 读取的站点 Cookie |
| Qwen | `providers.qwen.token` | Local Storage、Cookie `token` 或 `Authorization` |
| DuckDuckGo | `providers.duckduckgo.vqd_hash` | 聊天请求头 `x-vqd-hash-1` |
| DuckDuckGo | `providers.duckduckgo.fe_version` | 请求头 `x-fe-version` |
| DuckDuckGo | `providers.duckduckgo.fe_signals` | 请求头 `x-fe-signals` |
| DuckDuckGo | `providers.duckduckgo.user_agent` | 请求头 `User-Agent`（与 hash 匹配） |

提示：

- `cookies` 包含 HttpOnly Cookie，复制后请妥善保管，不要提交到公开仓库。
- DeepSeek 的 token 如果从 `userToken` 复制，值是 JSON 时扩展会自动取其中的 `value`。
- 配置支持 `${ENV_VAR}` 环境变量，也可以把敏感值放到环境变量里。

## 权限说明

| 权限 | 用途 |
| --- | --- |
| `cookies` | 读取三个站点的 Cookie |
| `webRequest` | 观察聊天请求头，捕获 `Authorization` 与 `x-vqd-hash-1` |
| `storage` | 在本次浏览器会话中暂存捕获结果 |
| `scripting` + `activeTab` | 在点击扩展时读取当前页面的 Local Storage |
| `host_permissions` | 仅限三个目标站点 |

## 故障排查

- **提示未登录**：确认已登录并刷新页面；打开 DevTools 查看 Local Storage 中是否存在
  `userToken`（DeepSeek）或 `token`（Qwen）。
- **Qwen 捕获不到 Authorization**：刷新页面后发送一条消息，再重新打开扩展。
- **DuckDuckGo 没有 `vqd_hash`**：必须在安装扩展后实际发送一次消息；仅打开首页不会产生该请求头。
- **DuckDuckGo 仍然 `418 ERR_CHALLENGE`**：hash 可能已过期，或当前 IP/浏览器被风控。重新发送消息捕获最新值；避免高频重试，必要时等待或更换网络。
- **复制失败**：点击弹窗中的「配置预览」文本框后按 `Ctrl+C`（macOS 为 `Cmd+C`）手动复制。

## 文件结构

```
chrome-extension/
├── manifest.json     # Manifest V3 配置
├── background.js     # Cookie / 请求头捕获与配置生成
├── content.js        # 登录检测、页面悬浮窗、一键复制
├── popup.html        # 弹窗界面
├── popup.css
├── popup.js          # 一键提取、复制、下载（Duck.ai 新模型列表）
├── icon16/32/48/128.png  # 扩展图标
└── README.md
```
