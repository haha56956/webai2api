const PROVIDERS = {
  deepseek: { label: "DeepSeek", hosts: ["chat.deepseek.com"] },
  qwen: { label: "Qwen", hosts: ["chat.qwen.ai"] },
  duckduckgo: { label: "DuckDuckGo", hosts: ["duck.ai", "duckduckgo.com"] },
};

const DEFAULT_PROVIDER = "deepseek";

const DEEPSEEK_MODELS = {
  "deepseek-chat": { model_type: "default", thinking: false, search: false },
  "deepseek-reasoner": { model_type: "expert", thinking: true, search: false },
};

const QWEN_MODELS = {
  "qwen3.8-max": "qwen3.8-max",
  "qwen3.7-plus": "qwen3.7-plus",
  "qwen3.5-plus": "qwen3.5-plus",
  "qwen3.5-flash": "qwen3.5-flash",
  "qwen3-coder-plus": "qwen3-coder-plus",
};

const DUCK_MODELS = {
  "gpt-5.4-mini": "gpt-5.4-mini",
  "gpt-6-luna": "gpt-6-luna",
  "claude-haiku-4-5": "claude-haiku-4-5",
  "mistral-small-2603": "mistral-small-2603",
  "tinfoil/gpt-oss-120b": "tinfoil/gpt-oss-120b",
  "tinfoil/gemma4-31b": "tinfoil/gemma4-31b",
  "gpt-5.6-terra": "gpt-5.6-terra",
  "gpt-5.6-sol": "gpt-5.6-sol",
  "claude-sonnet-4-6": "claude-sonnet-4-6",
  "claude-opus-4-8": "claude-opus-4-8",
};

let activeProvider = DEFAULT_PROVIDER;
let result = null;
let autoSelected = false;

function randomApiKey() {
  const bytes = new Uint8Array(24);
  crypto.getRandomValues(bytes);
  return (
    "sk-webai2api-" +
    Array.from(bytes)
      .map((value) => value.toString(16).padStart(2, "0"))
      .join("")
  );
}

function providerForUrl(url) {
  try {
    const host = new URL(url).hostname;
    for (const [name, meta] of Object.entries(PROVIDERS)) {
      if (meta.hosts.some((item) => host === item || host.endsWith("." + item))) {
        return name;
      }
    }
  } catch (error) {
    return null;
  }
  return null;
}

function mask(value) {
  if (!value) {
    return "";
  }
  if (value.length <= 24) {
    return value;
  }
  return value.slice(0, 12) + "…" + value.slice(-6);
}

function formatTime(timestamp) {
  if (!timestamp) {
    return "未捕获";
  }
  try {
    return new Date(timestamp).toLocaleString();
  } catch (error) {
    return "未知";
  }
}

function buildTabs() {
  const container = document.getElementById("tabs");
  container.textContent = "";
  for (const [name, meta] of Object.entries(PROVIDERS)) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = meta.label;
    button.dataset.provider = name;
    button.addEventListener("click", () => {
      activeProvider = name;
      renderTabs();
      refresh();
    });
    container.appendChild(button);
  }
}

function renderTabs() {
  for (const button of document.querySelectorAll("#tabs button")) {
    button.classList.toggle("active", button.dataset.provider === activeProvider);
  }
}

function renderStatus(state) {
  const status = document.getElementById("status");
  status.className = "status";
  if (!state) {
    status.textContent = "正在检测…";
    return;
  }
  if (state.error) {
    status.textContent = state.error;
    status.classList.add("error");
    return;
  }
  const label = PROVIDERS[state.provider].label;
  if (state.provider === "duckduckgo") {
    if (!state.onSite) {
      status.textContent = `当前标签页不是 ${label}，请先打开 duck.ai`;
      status.classList.add("warn");
    } else {
      status.textContent = "DuckDuckGo 已就绪（无需登录）";
      status.classList.add("ok");
    }
    return;
  }
  if (!state.onSite) {
    status.textContent = `当前标签页不是 ${label}，已尝试从浏览器 Cookie 中读取`;
    status.classList.add("warn");
    return;
  }
  if (state.loggedIn) {
    status.textContent = `${label} 已登录，凭证提取成功`;
    status.classList.add("ok");
  } else {
    status.textContent = `未检测到 ${label} 登录信息`;
    status.classList.add("warn");
  }
}

function renderFields(state) {
  const fields = document.getElementById("fields");
  fields.textContent = "";
  if (!state || state.error) {
    return;
  }
  const rows = [];
  if (state.provider === "duckduckgo") {
    rows.push(["vqd_hash", mask(state.vqdHash) || "未捕获"]);
    rows.push(["fe_version", mask(state.feVersion) || "未捕获"]);
    rows.push(["fe_signals", mask(state.feSignals) || "未捕获"]);
    rows.push(["journey_id", mask(state.journeyId) || "未捕获"]);
  } else {
    rows.push(["token", mask(state.token) || "未检测到"]);
  }
  rows.push(["cookies", mask(state.cookies) || "未检测到"]);
  rows.push(["捕获时间", formatTime(state.capturedAt)]);
  rows.push(["页面", state.pageUrl || "未知"]);
  for (const [name, value] of rows) {
    const dt = document.createElement("dt");
    dt.textContent = name;
    const dd = document.createElement("dd");
    dd.textContent = value;
    dd.title = value;
    fields.appendChild(dt);
    fields.appendChild(dd);
  }
}

function renderOutput(state) {
  const output = document.getElementById("output");
  if (!state || state.error) {
    output.value = "";
    return;
  }
  output.value = JSON.stringify(state.config, null, 2);
}

function renderHint(state) {
  const hint = document.getElementById("hint");
  if (!state || state.error) {
    hint.textContent = "";
    return;
  }
  if (state.provider === "duckduckgo") {
    hint.textContent = state.onSite
      ? "提示：先在 duck.ai 发送一条消息，扩展才能捕获 x-vqd-hash-1、x-fe-version 与 x-fe-signals；可在 WebUI 的 Duck.ai 面板粘贴，或复制配置片段到 config.json。"
      : "提示：打开 duck.ai 并发送一条消息后，再点击“重新获取”。";
    return;
  }
  hint.textContent = state.loggedIn
    ? "提示：复制配置片段，粘贴到 config.json 的 providers 对应节点；配置文件包含敏感凭证，请勿分享。"
    : `提示：请先在 ${PROVIDERS[state.provider].label} 完成登录；Qwen 可先发送一条消息以捕获 Authorization 请求头。`;
}

function render() {
  renderTabs();
  renderStatus(result);
  renderFields(result);
  renderOutput(result);
  renderHint(result);
}

function fullConfig(state) {
  const config = {
    listen: { host: "127.0.0.1", port: 8787 },
    api_keys: [randomApiKey()],
    rpm: 30,
    rpm_window: 60,
    rpm_per_provider: {},
    queue: { enabled: true, max_size: 200, max_wait: 120 },
    fallback: {
      enabled: true,
      order: ["qwen", "deepseek"],
      max_attempts: 3,
      models: {},
    },
    timeout: 180,
    proxy: "",
    log_level: "INFO",
    default_provider: state ? state.provider : "qwen",
    cors_origins: ["*"],
    max_body_bytes: 16777216,
    providers: {
      deepseek: {
        enabled: true,
        base_url: "https://chat.deepseek.com",
        token: "",
        cookies: "",
        headers: {},
        models: DEEPSEEK_MODELS,
        thinking: false,
        search: false,
        pow_backend: "auto",
        timeout: 180,
        proxy: "",
      },
      qwen: {
        enabled: true,
        base_url: "https://chat.qwen.ai",
        token: "",
        cookies: "",
        headers: {},
        models: QWEN_MODELS,
        chat_mode: "",
        client_version: "0.2.84",
        user_agent: "",
        auto_cookies: true,
        auto_bxua: true,
        bx_umidtoken: "",
        auto_delete_chat: false,
        timeout: 300,
        proxy: "",
      },
      duckduckgo: {
        enabled: true,
        base_url: "https://duck.ai",
        cookies: "",
        headers: {},
        models: DUCK_MODELS,
        vqd_hash: "",
        fe_version: "",
        fe_signals: "",
        user_agent: "",
        timeout: 120,
        proxy: "",
      },
    },
  };
  if (state && state.config && state.config.providers) {
    for (const [name, entry] of Object.entries(state.config.providers)) {
      if (config.providers[name]) {
        Object.assign(config.providers[name], entry);
      }
    }
  }
  return config;
}

function downloadJson() {
  const content = JSON.stringify(fullConfig(result), null, 2) + "\n";
  const blob = new Blob([content], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = "webai2api-config.json";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function copySnippet() {
  const output = document.getElementById("output");
  const button = document.getElementById("copy");
  if (!output.value) {
    return;
  }
  try {
    await navigator.clipboard.writeText(output.value);
    const previous = button.textContent;
    button.textContent = "已复制";
    setTimeout(() => {
      button.textContent = previous;
    }, 1500);
  } catch (error) {
    output.select();
    document.execCommand("copy");
  }
}

async function refresh() {
  const status = document.getElementById("status");
  status.className = "status";
  status.textContent = "正在检测…";
  document.getElementById("output").value = "";
  try {
    result = await chrome.runtime.sendMessage({
      type: "capture",
      provider: activeProvider,
    });
  } catch (error) {
    result = { error: String((error && error.message) || error) };
  }
  render();
}

async function init() {
  buildTabs();
  renderTabs();
  try {
    const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
    const detected = tabs && tabs[0] ? providerForUrl(tabs[0].url || "") : null;
    if (detected && !autoSelected) {
      activeProvider = detected;
      autoSelected = true;
      renderTabs();
    }
  } catch (error) {
    // 无法读取当前标签页时使用默认 provider
  }
  document.getElementById("refresh").addEventListener("click", refresh);
  document.getElementById("copy").addEventListener("click", copySnippet);
  document.getElementById("download").addEventListener("click", downloadJson);
  await refresh();
}

document.addEventListener("DOMContentLoaded", init);
