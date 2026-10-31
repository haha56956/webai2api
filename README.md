# webai2api

把 **DeepSeek（chat.deepseek.com）**、**Qwen（chat.qwen.ai）**、**DuckDuckGo AI Chat（duck.ai）** 的免费网页聊天封装成 **OpenAI 兼容 API** 的本地服务。

只用 Python 标准库，无需 pip 安装依赖；DeepSeek PoW 会自动用 `cc/gcc/clang` 编译 C 加速模块，没有编译器时回退到纯 Python。

## 功能

- OpenAI 兼容接口：
  - `GET /v1/models`
  - `POST /v1/chat/completions`（流式与非流式，支持 `tools` / `tool_calls`）
  - `GET /health`
  - `GET /ui`（内置 WebUI，可设置 RPM、排队与 Duck.ai 凭证）
  - `GET /admin/config`、`POST /admin/rpm`、`POST /admin/duck`、`POST /admin/reload`
- API Key 鉴权：`Authorization: Bearer`、`X-API-Key`，或 Cookie `webai2api_key`
- 全局 RPM + 按 provider 的独立 RPM 限流；超限时进入 FIFO 等待队列（缓存池），队列满或超时才返回 `429`
- DuckDuckGo 额度耗尽时自动清除 Cookie 刷新重试，仍失败则按配置自动路由到其他 provider
- 配置文件修改后自动热重载；也可调用 `/admin/reload` 强制重载
- 附带 Chrome 扩展：检测登录状态并一键提取 token / Cookie / `vqd_hash` / `x-fe-version` / `x-fe-signals`
- Function calling：网关级工具提示词注入 + 输出解析，带 tools 的流式请求会先缓冲再输出 `tool_calls`
- systemd 用户服务安装/卸载，支持开机自启（`loginctl enable-linger`）
- 上游支持：
  - DeepSeek：Bearer token、Cookie、PoW（`DeepSeekHashV1`）、思考/联网搜索开关
  - Qwen：token 或访客模式、自动生成 `ssxmod` Cookie 与 `bx-ua`、思考模式
  - DuckDuckGo：`duck.ai` 新接口，需要浏览器计算的 `x-vqd-hash-1` 与 `x-fe-version` / `x-fe-signals`；额度用尽自动重试/回退
- HTTP/HTTPS 代理、`${ENV_VAR}` 环境变量展开
- 流式响应支持 `reasoning_content`（思考内容）和 `stream_options.include_usage`

## 环境要求

- Linux（systemd 自启功能需要 systemd）
- Python **3.10+**（使用 `int | str` 等语法）
- 可选：`cc` / `gcc` / `clang`（用于编译 DeepSeek PoW 加速模块）
- 能访问上游站点的网络环境；DuckDuckGo 在部分网络下不可达，需要代理

## 快速开始

```bash
cd /path/to/webapi

# 1. 生成配置（自动生成随机 API Key）
python3 webai2api.py init

# 2. 编辑 config.json，填入上游 token / cookies
#    可以使用 chrome-extension/ 中的扩展一键获取，见下方“Chrome 扩展”

# 3. 自检（可选，--pow 会验证 DeepSeek PoW）
python3 webai2api.py check --pow

# 4. 启动
python3 webai2api.py run
```

默认监听 `127.0.0.1:8787`，API Base URL 为 `http://127.0.0.1:8787/v1`。

也可以使用模块方式启动：

```bash
python3 -m webai2api run --config ./config.json
```

## 配置

复制 `config.example.json` 为 `config.json` 后修改。配置支持：

- 文件修改后，下一次请求会自动检测 mtime 并重载
- 字符串值中的 `${ENV_NAME}` 会替换为环境变量
- `providers.deepseek`、`providers.qwen`、`providers.duckduckgo` 均可单独 `enabled: false`
- `rpm: 0` 表示不限制；`rpm_per_provider` 可为某个 provider 设置更严格的限制

主要字段：

| 字段 | 说明 |
| --- | --- |
| `listen.host/port` | 监听地址和端口 |
| `api_keys` | 客户端访问本服务的 API Key 列表；为空则不鉴权（不推荐） |
| `rpm` | 全局每分钟请求数，0 为不限 |
| `rpm_window` | RPM 统计窗口秒数，默认 60 |
| `rpm_per_provider` | 例：`{"qwen": 10}`，对单个 provider 额外限流 |
| `queue.enabled` | 超限时是否进入等待队列（默认 `true`） |
| `queue.max_size` | 等待队列最大长度，默认 200 |
| `queue.max_wait` | 单个请求最长排队秒数，默认 120 |
| `fallback.enabled` | 上游限流/额度耗尽时是否自动路由，默认 `true` |
| `fallback.order` | 回退 provider 顺序，如 `["qwen", "deepseek"]`；留空使用配置顺序 |
| `fallback.max_attempts` | 单个请求最多尝试的 provider 数，含主 provider |
| `fallback.models` | 回退时指定目标模型，如 `{"qwen": "qwen3.5-plus"}` |
| `timeout` | 默认上游超时（秒） |
| `proxy` | 全局 HTTP/HTTPS 代理，例：`http://127.0.0.1:7890` |
| `default_provider` | 无法匹配模型时使用的 provider：`deepseek` / `qwen` / `duckduckgo` |
| `cors_origins` | 允许的浏览器来源，默认 `["*"]` |
| `max_body_bytes` | 请求体大小上限 |
| `providers.*.token` | 上游 Bearer token（可带 `Bearer ` 前缀） |
| `providers.*.cookies` | 上游 Cookie 请求头字符串 |
| `providers.*.headers` | 额外上游请求头，会覆盖默认值 |
| `providers.*.models` | 对外模型名到上游模型/配置的映射 |
| `providers.*.proxy` | 单个 provider 的代理，优先于全局 |
| `providers.deepseek.pow_backend` | `auto` / `c` / `python` |
| `providers.qwen.auto_cookies` | 自动生成 `ssxmod_itna`/`ssxmod_itna2` |
| `providers.qwen.auto_bxua` | 自动生成 `bx-ua` 请求头 |
| `providers.qwen.bx_umidtoken` | 手动固定 `bx-umidtoken`；留空则自动获取并缓存 |
| `providers.qwen.auto_delete_chat` | 请求结束后删除上游会话（默认关闭） |
| `providers.duckduckgo.vqd_hash` | 浏览器计算的 `X-Vqd-Hash-1`（可通过扩展或 WebUI 写入） |
| `providers.duckduckgo.fe_version` | 前端版本 `x-fe-version`，留空自动从 `https://duck.ai/` HTML 解析 |
| `providers.duckduckgo.fe_signals` | 前端信号 `x-fe-signals`（base64 JSON），扩展会自动捕获 |

> 模型名带 provider 前缀也可以路由，例如 `qwen/qwen3.7-plus`、`deepseek/deepseek-chat`、`duckduckgo/gpt-4o-mini`。
> 未匹配到任何模型时，会回退到 `default_provider` 的第一个模型，并原样回显请求中的模型名。

## Chrome 扩展（一键获取凭证）

`chrome-extension/` 目录是一个 Manifest V3 扩展，可检测 DeepSeek / Qwen / DuckDuckGo 的登录状态，
读取 Local Storage、Cookie 和聊天请求头，一键生成配置片段。

1. 打开 `chrome://extensions/`，开启「开发者模式」；
2. 点击「加载已解压的扩展程序」，选择 `chrome-extension/` 目录；
3. 刷新已打开的 DeepSeek / Qwen / duck.ai 页面，页面右下角会自动出现悬浮窗并检测凭证；
4. 提取成功后点击悬浮窗中的「一键复制配置」（也可点击工具栏图标，在弹窗中操作）；
5. 粘贴到 `config.json` 的 `providers` 对应节点。

DuckDuckGo 需要先在 duck.ai 发送一条消息，扩展才能捕获 `x-vqd-hash-1`、`x-fe-version`、`x-fe-signals` 与 Cookie。
捕获后可直接粘贴到 WebUI 的 Duck.ai 面板（推荐），或复制配置片段到 `config.json`。
扩展只在本地处理数据，不会上传；详细说明见 `chrome-extension/README.md`。

## 获取上游凭证

> 以下内容涉及第三方网站的非公开接口，接口和风控会随时变化。请只使用自己的账号，并遵守对应网站的服务条款。不要分享、提交或上传自己的 Cookie/token。

### DeepSeek

1. 浏览器登录 `https://chat.deepseek.com`。
2. 打开开发者工具 → Application/存储 → Local Storage → `https://chat.deepseek.com`。
3. 找到 `userToken`，它的值通常是一个 JSON，如 `{"value":"xxxx"}`，把 `xxxx` 填入：

```json
"providers": {
  "deepseek": {
    "token": "xxxx",
    "cookies": "可选：从请求头 Cookie 整段复制"
  }
}
```

也可以直接把请求头里的 `Authorization: Bearer xxxx` 整段填入 `token`，程序会自动去掉 `Bearer `。

DeepSeek 每次对话需要先创建会话、获取 PoW challenge，再提交 `x-ds-pow-response`。本服务会自动完成。首次使用会尝试编译 C 加速模块到 `~/.cache/webai2api/`；如果编译失败，则用纯 Python 求解（可能慢几十秒）。

### Qwen

推荐使用已登录账号的 token + 浏览器 Cookie：

1. 浏览器登录 `https://chat.qwen.ai`，随便发一条消息。
2. 开发者工具 → Network → 找到 `POST /api/v2/chat/completions?chat_id=...`。
3. 复制请求头：
   - `Authorization: Bearer ...` → 填入 `providers.qwen.token`
   - `Cookie: ...` 整段 → 填入 `providers.qwen.cookies`
4. 如果仍被 WAF 拦截，把 `auto_cookies` 设为 `false`，直接使用浏览器原始 Cookie；必要时把 `auto_bxua` 设为 `false`，并在 `headers` 中固定浏览器里的 `bx-ua`。

不填 token/cookies 时会尝试访客模式（`chat_mode: guest`）。实测访客可以创建会话，但 `completions` 接口经常被阿里 WAF 拦截：

```
FAIL_SYS_USER_VALIDATE / RGV587_ERROR
```

这是上游风控，不是本服务的 Bug。解决方式通常是：使用已登录账号的 cookie/token、住宅 IP、减少请求频率，或换网络/代理。

### DuckDuckGo

`duck.ai` 的新流程由浏览器 JS 计算 `x-vqd-hash-1`，本服务不内置 JS 引擎。推荐用 Chrome 扩展：

1. 在 Chrome 打开 `https://duck.ai/` 并发送一条消息；
2. 扩展会自动捕获 `x-vqd-hash-1`、`x-fe-version`、`x-fe-signals`、Cookie 与 User-Agent；
3. 打开 `http://127.0.0.1:8787/ui` → Duck.ai，粘贴并保存（或复制扩展生成的配置片段）。

也可以手动抓包：开发者工具 → Network → `POST /duckchat/v1/chat`，复制 `x-vqd-hash-1`、`x-fe-version`、`x-fe-signals`。
`x-fe-version` 留空时，服务会从 `https://duck.ai/` 首页的 `data-version-tag` / `data-version-sha` 自动解析。
当前部分网络无法直连 DuckDuckGo，请配置代理。该 provider 的流程变化较快，属于实验性支持。

Duck.ai 模型（`/duckchat/v1/models`）：

| 模型 | 访问级别 | reasoningEffort |
| --- | --- | --- |
| `gpt-5.4-mini` | free | none / low / medium |
| `gpt-6-luna` | 受限 | none / low |
| `claude-haiku-4-5` | free | none / low |
| `mistral-small-2603` | 受限 | - |
| `tinfoil/gpt-oss-120b` | free | low |
| `tinfoil/gemma4-31b` | 受限 | none / low |
| `gpt-5.6-terra` | plus / pro | none / low / medium |
| `gpt-5.6-sol` | pro | none / low / medium |
| `claude-sonnet-4-6` | plus / pro | none / low |
| `claude-opus-4-8` | pro | none / low / medium |

受限模型会返回 `404 ERR_MODEL_RESTRICTED`；公开模型的实际可用性仍受账号与风控影响。

## 调用示例

### curl（非流式）

```bash
curl http://127.0.0.1:8787/v1/chat/completions \
  -H "Authorization: Bearer sk-webai2api-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3.7-plus",
    "messages": [
      {"role": "system", "content": "你是一个简洁的助手"},
      {"role": "user", "content": "用一句话介绍你自己"}
    ]
  }'
```

### curl（流式）

```bash
curl -N http://127.0.0.1:8787/v1/chat/completions \
  -H "Authorization: Bearer sk-webai2api-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "deepseek-reasoner",
    "stream": true,
    "stream_options": {"include_usage": true},
    "messages": [{"role": "user", "content": "9.11 和 9.9 哪个大？"}]
  }'
```

思考内容会以 `reasoning_content` 字段出现在 delta 中。

### OpenAI Python SDK

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8787/v1",
    api_key="sk-webai2api-change-me",
)

resp = client.chat.completions.create(
    model="qwen3.7-plus",
    messages=[{"role": "user", "content": "你好"}],
)
print(resp.choices[0].message.content)
```

### Function calling / tools

请求体支持 OpenAI `tools` 与 `tool_choice`。由于网页聊天接口没有原生 tools，
网关会把工具定义注入提示词，并在模型输出中解析 `[[tool_call]]...[[/tool_call]]` 块：

```bash
curl http://127.0.0.1:8787/v1/chat/completions \
  -H "Authorization: Bearer sk-webai2api-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "deepseek-chat",
    "messages": [{"role": "user", "content": "北京天气怎么样？"}],
    "tools": [{
      "type": "function",
      "function": {
        "name": "get_weather",
        "description": "查询城市天气",
        "parameters": {
          "type": "object",
          "properties": {"city": {"type": "string"}},
          "required": ["city"]
        }
      }
    }]
  }'
```

模型返回工具调用时，`finish_reason` 为 `tool_calls`，`message.tool_calls` 为 OpenAI 格式；
把工具结果以 `{"role":"tool","tool_call_id":"...","name":"...","content":"..."}` 追加后再次请求即可。
流式请求会先缓冲上游文本，解析完再发送 `tool_calls`，避免半截 JSON。

### WebUI

启动后打开 `http://127.0.0.1:8787/ui`，输入 `api_keys` 中的任意一个 Key：

- 概览：服务版本、Provider 状态、模型列表、实时用量
- 限流 / RPM：全局 RPM、窗口、按 provider RPM、排队开关/上限/等待时间
- Duck.ai：粘贴扩展捕获的 `vqd_hash`、`fe_version`、`fe_signals`、Cookie、UA
- 保存后自动热重载，无需重启服务；管理接口只返回脱敏后的凭证状态

### Cookie 方式鉴权

```bash
curl http://127.0.0.1:8787/v1/models \
  -b "webai2api_key=sk-webai2api-change-me"
```

### 模型列表

```bash
curl http://127.0.0.1:8787/v1/models \
  -H "Authorization: Bearer sk-webai2api-change-me"
```

## 开机自启（systemd 用户服务）

```bash
# 安装、启用并立即启动
python3 webai2api.py install-service --config /绝对路径/config.json

# 查看状态
python3 webai2api.py status

# 查看日志
journalctl --user -u webai2api -f

# 停止并卸载（保留配置文件）
python3 webai2api.py uninstall-service
```

`install-service` 默认执行 `loginctl enable-linger $USER`，这样注销登录后服务仍会运行，并在开机时自动启动。若不需要，加 `--no-linger`。服务名可用 `--name` 修改。

如果没有 systemd（容器、WSL 等），请使用 `nohup`、tmux、supervisord 等方式自行守护。

## 限流、排队与自动路由

### 缓存池排队

全局 `rpm` 和 `rpm_per_provider` 使用滑动窗口。达到上限时不会立刻拒绝，而是：

1. 请求进入 FIFO 等待队列（`queue.max_size`）；
2. 窗口释放配额后自动继续执行；
3. 等待超过 `queue.max_wait` 秒或队列已满时，才返回 `429`。

排队成功的响应会带 `X-Queue-Wait-Ms`，便于观察等待时间。默认值：

```json
"rpm": 30,
"rpm_window": 60,
"queue": {"enabled": true, "max_size": 200, "max_wait": 120}
```

如果希望恢复“超限立即 429”，设置 `"queue": {"enabled": false}`。

### DuckDuckGo 额度与自动路由

- 当 DuckDuckGo 返回 429、`quota`、`rate limit` 或“免费额度已用尽”等提示时：
  1. 自动丢弃配置的 Cookie，重新获取 `vqd` 后重试一次；
  2. 仍然失败则抛出可回退错误。
- `fallback.enabled` 为 `true` 时，服务会按 `fallback.order` 自动尝试其他 provider；
  一旦进入回退流程，后续 provider 失败也会继续尝试下一个，直到 `fallback.max_attempts` 用尽。
  响应头带 `X-WebAI-Fallback: true` 和最终的 `X-WebAI-Provider`。
- 可通过 `fallback.models` 指定回退后使用的模型；未指定时使用目标 provider 的默认模型。

```json
"fallback": {
  "enabled": true,
  "order": ["qwen", "deepseek"],
  "max_attempts": 3,
  "models": {"qwen": "qwen3.5-plus"}
}
```

超时或队列满时的错误示例：

```json
{
  "error": {
    "message": "请求过于频繁（qwen），排队等待超时。",
    "type": "rate_limit_error",
    "code": "rate_limit_exceeded"
  }
}
```

并带有 `Retry-After`、`X-RateLimit-Limit`、`X-RateLimit-Remaining` 响应头。

### 热重载

- 修改 `config.json` 后无需重启，下一次请求会自动加载。
- 强制重载：

```bash
curl -X POST http://127.0.0.1:8787/admin/reload \
  -H "Authorization: Bearer sk-webai2api-change-me"
```

## 测试

```bash
python3 -m unittest discover -s tests -v
```

测试使用本地 mock 上游，覆盖：

- DeepSeek PoW 4 组已知向量、C/Python 求解、响应头构造
- AES-128-CBC 与 OpenSSL 对照
- 配置、鉴权、Cookie、RPM 限流、排队等待、队列满、排队超时
- DeepSeek/Qwen/DuckDuckGo 的非流式与流式端到端
- DuckDuckGo 额度耗尽清 Cookie 重试、跨 provider 自动路由
- `tools` / `tool_choice` 提示词注入、非流式与流式 `tool_calls` 解析
- `/v1/models`、`/health`、`/ui`、`/admin/config`、`/admin/rpm`、`/admin/duck`、`/admin/reload`
- 429、Cookie 鉴权、管理接口鉴权与凭证脱敏

## 已知限制

- 网页接口不是官方 API，随时可能变更或触发风控；账号也可能被限制。请自行评估风险。
- 所有 `messages` 会被拼成一段文本发送，不保留原始多轮消息结构；上游每条请求会新建会话。
- Function calling 是“提示词注入 + 文本解析”的模拟实现，不是上游原生 tools；复杂 schema、并行调用或模型不遵循格式时可能失败。
- 不支持图片、文件上传、多模态。
- DeepSeek 的 `INCOMPLETE` 流不会自动 continue/resume。
- Qwen 访客模式经常被 WAF 拦截；DuckDuckGo 的 hash 挑战需要浏览器/扩展提供，且 headless、Marionette、频繁失败尝试可能触发 `418 ERR_CHALLENGE`。
- 上游模型的实际能力、上下文长度、配额由网页端决定，与本服务声明的模型名不一定完全一致。
- 代理只支持 HTTP/HTTPS（urllib 限制），不支持 SOCKS。
- 服务是单进程多线程；适合个人/小规模使用，不建议直接暴露到公网。

## 安全建议

- 默认只监听 `127.0.0.1`，不要在没有 API Key 的情况下绑定 `0.0.0.0`。
- `config.json` 包含上游账号凭证，建议 `chmod 600 config.json`，不要提交到 Git。
- 不要把本服务暴露给不受信任的用户，否则他们可以消耗你的上游账号额度。
- 定期更换 `api_keys` 和上游 token。

## 故障排查

| 现象 | 处理 |
| --- | --- |
| `401 invalid_api_key` | 检查 `Authorization: Bearer`、`X-API-Key` 或 Cookie 是否与 `api_keys` 一致 |
| `429 rate_limit_exceeded` | 队列满或排队超时；调大 `rpm`、`queue.max_size`、`queue.max_wait`，或等待 `Retry-After` |
| DuckDuckGo 提示额度用尽后仍失败 | 检查 `fallback.enabled` 和 `fallback.order`；或等待额度恢复/更换网络 |
| DeepSeek `Missing Token` / `40002` | `providers.deepseek.token` 未配置或已过期，重新复制 `userToken` |
| DeepSeek PoW 很慢 | 安装 `gcc`/`clang`，运行 `python3 webai2api.py check --pow` 检查 |
| Qwen `FAIL_SYS_USER_VALIDATE` / `RGV587` | WAF 风控；配置登录 Cookie/token、住宅 IP，尝试 `auto_cookies: false` |
| Qwen `Bad_Request` | 检查 `client_version`、Cookie/token 是否来自同一账号和浏览器 |
| DuckDuckGo `418` / `ERR_CHALLENGE` | 用扩展在真实浏览器中重新捕获 `x-vqd-hash-1` / `x-fe-version` / `x-fe-signals`；避免 headless/Marionette 与高频重试，必要时等待风控解除或更换网络 |
| `502` / 连接超时 | 检查网络、`proxy`，确认能直接访问对应站点 |
| 自启失败 | `journalctl --user -u webai2api -e`，确认 `ExecStart` 中配置路径为绝对路径 |
