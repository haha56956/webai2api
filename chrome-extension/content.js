(function () {
  if (window.__webai2apiAssistantLoaded) {
    return;
  }
  window.__webai2apiAssistantLoaded = true;

  const SITES = {
    "chat.deepseek.com": { provider: "deepseek", label: "DeepSeek" },
    "chat.qwen.ai": { provider: "qwen", label: "Qwen" },
    "duck.ai": { provider: "duckduckgo", label: "DuckDuckGo" },
    "duckduckgo.com": { provider: "duckduckgo", label: "DuckDuckGo" },
  };

  function siteForHost(host) {
    for (const [name, site] of Object.entries(SITES)) {
      if (host === name || host.endsWith("." + name)) {
        return site;
      }
    }
    return null;
  }

  function isDuckChatPage(host) {
    if (host !== "duckduckgo.com") {
      return host === "duck.ai";
    }
    const params = new URLSearchParams(location.search);
    return (
      location.pathname === "/" &&
      (params.get("ia") === "chat" || params.get("duckai") === "1")
    );
  }

  const host = location.hostname;
  const site = siteForHost(host);
  if (!site) {
    return;
  }
  if (site.provider === "duckduckgo" && !isDuckChatPage(host)) {
    return;
  }

  const HIDE_KEY = "webai2api-auto-hidden";
  let autoHidden = false;
  try {
    autoHidden = sessionStorage.getItem(HIDE_KEY) === "1";
  } catch (error) {
    autoHidden = false;
  }

  const state = {
    busy: false,
    data: null,
    error: "",
  };
  let ui = null;
  let detectTimer = null;
  let pollTimer = null;

  function mask(value) {
    if (!value) {
      return "";
    }
    if (value.length <= 20) {
      return value;
    }
    return value.slice(0, 8) + "…" + value.slice(-4);
  }

  function formatTime(timestamp) {
    if (!timestamp) {
      return "刚刚";
    }
    try {
      return new Date(timestamp).toLocaleTimeString();
    } catch (error) {
      return "刚刚";
    }
  }

  function createUI() {
    if (ui) {
      return ui;
    }
    const root = document.createElement("div");
    root.id = "webai2api-floating-root";
    root.style.cssText =
      "position:fixed;right:18px;bottom:96px;z-index:2147483647;";
    const shadow = root.attachShadow({ mode: "closed" });
    shadow.innerHTML = `
      <style>
        :host { all: initial; }
        .wrap {
          font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
            "Microsoft YaHei", sans-serif;
          font-size: 13px;
          line-height: 1.5;
          color: #1f2937;
          pointer-events: none;
        }
        .panel, .launcher {
          pointer-events: auto;
        }
        [hidden] {
          display: none !important;
        }
        .panel {
          width: 328px;
          max-width: calc(100vw - 36px);
          max-height: min(70vh, 520px);
          overflow: auto;
          padding: 12px;
          border: 1px solid #e5e7eb;
          border-radius: 12px;
          background: #ffffff;
          box-shadow: 0 12px 32px rgba(15, 23, 42, 0.18);
        }
        .head {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 8px;
          margin-bottom: 8px;
        }
        .title {
          display: flex;
          align-items: center;
          gap: 8px;
        }
        .logo {
          display: inline-flex;
          align-items: center;
          justify-content: center;
          width: 26px;
          height: 26px;
          border-radius: 8px;
          background: #2563eb;
          color: #ffffff;
          font-size: 11px;
          font-weight: 700;
        }
        .title strong {
          display: block;
          font-size: 13px;
        }
        .site {
          display: block;
          color: #6b7280;
          font-size: 11px;
        }
        button {
          font: inherit;
          cursor: pointer;
        }
        .min {
          width: 26px;
          height: 26px;
          padding: 0;
          border: 1px solid #e5e7eb;
          border-radius: 7px;
          background: #ffffff;
          color: #6b7280;
          font-size: 16px;
          line-height: 1;
        }
        .status {
          margin-bottom: 8px;
          padding: 7px 9px;
          border-radius: 7px;
          background: #f3f4f6;
          color: #6b7280;
          word-break: break-all;
        }
        .status.ok { color: #15803d; }
        .status.warn { color: #b45309; }
        .status.error { color: #b91c1c; }
        .fields {
          display: grid;
          grid-template-columns: 76px 1fr;
          gap: 3px 8px;
          margin: 0 0 9px;
        }
        .fields dt {
          color: #6b7280;
        }
        .fields dd {
          margin: 0;
          overflow: hidden;
          font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
          font-size: 11px;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .actions {
          display: flex;
          gap: 6px;
        }
        .actions button {
          flex: 1;
          padding: 7px 6px;
          border: 1px solid #e5e7eb;
          border-radius: 7px;
          background: #ffffff;
          color: #374151;
        }
        .actions .copy {
          border-color: #2563eb;
          background: #2563eb;
          color: #ffffff;
          font-weight: 600;
        }
        .actions .copy:disabled {
          border-color: #e5e7eb;
          background: #e5e7eb;
          color: #9ca3af;
          cursor: not-allowed;
        }
        .actions .copy.done {
          border-color: #15803d;
          background: #15803d;
        }
        .hint {
          margin: 8px 0 0;
          color: #6b7280;
          font-size: 11px;
        }
        .launcher {
          display: inline-flex;
          align-items: center;
          gap: 7px;
          padding: 8px 12px;
          border: 0;
          border-radius: 999px;
          background: #2563eb;
          color: #ffffff;
          box-shadow: 0 6px 20px rgba(37, 99, 235, 0.35);
          font-weight: 600;
        }
        .launcher .dot {
          width: 7px;
          height: 7px;
          border-radius: 50%;
          background: #fbbf24;
        }
        .launcher.ready .dot {
          background: #4ade80;
        }
        @media (prefers-color-scheme: dark) {
          .panel {
            border-color: #374151;
            background: #1f2937;
            color: #f3f4f6;
          }
          .min, .actions button {
            border-color: #374151;
            background: #1f2937;
            color: #d1d5db;
          }
          .status { background: #111827; }
          .actions .copy { border-color: #60a5fa; background: #60a5fa; color: #0b1220; }
          .actions .copy.done { border-color: #4ade80; background: #4ade80; }
          .hint, .site, .fields dt { color: #9ca3af; }
        }
      </style>
      <div class="wrap">
        <button class="launcher" type="button" hidden>
          <span class="dot"></span>
          <span class="launcher-text">webai2api</span>
        </button>
        <section class="panel" hidden>
          <header class="head">
            <div class="title">
              <span class="logo">AI</span>
              <div>
                <strong>webai2api 凭证</strong>
                <span class="site"></span>
              </div>
            </div>
            <button class="min" type="button" title="收起">×</button>
          </header>
          <div class="status" data-role="status">正在检测…</div>
          <dl class="fields" data-role="fields"></dl>
          <div class="actions">
            <button class="refresh" type="button">重新获取</button>
            <button class="copy" type="button" disabled>一键复制配置</button>
          </div>
          <p class="hint" data-role="hint"></p>
        </section>
      </div>
    `;
    document.documentElement.appendChild(root);
    // 优先使用构造样式表，避免被站点 CSP 拦截 <style>
    try {
      const styleEl = shadow.querySelector("style");
      const sheet = new CSSStyleSheet();
      sheet.replaceSync(styleEl.textContent);
      shadow.adoptedStyleSheets = [sheet];
      styleEl.remove();
    } catch (error) {
      // 不支持时保留 <style> 回退
    }

    ui = {
      root,
      launcher: shadow.querySelector(".launcher"),
      launcherText: shadow.querySelector(".launcher-text"),
      panel: shadow.querySelector(".panel"),
      siteLabel: shadow.querySelector(".site"),
      status: shadow.querySelector('[data-role="status"]'),
      fields: shadow.querySelector('[data-role="fields"]'),
      hint: shadow.querySelector('[data-role="hint"]'),
      refresh: shadow.querySelector(".refresh"),
      copy: shadow.querySelector(".copy"),
      min: shadow.querySelector(".min"),
    };
    ui.siteLabel.textContent = site.label;

    ui.launcher.addEventListener("click", () => {
      openPanel();
      detect(true);
    });
    ui.min.addEventListener("click", () => {
      closePanel(true);
    });
    ui.refresh.addEventListener("click", () => {
      detect(true);
    });
    ui.copy.addEventListener("click", copyConfig);
    return ui;
  }

  function openPanel() {
    const current = createUI();
    current.launcher.hidden = true;
    current.panel.hidden = false;
    if (autoHidden) {
      autoHidden = false;
      try {
        sessionStorage.removeItem(HIDE_KEY);
      } catch (error) {
        // 忽略存储失败
      }
    }
  }

  function showLauncher(text) {
    const current = createUI();
    current.panel.hidden = true;
    current.launcher.hidden = false;
    if (text) {
      current.launcherText.textContent = text;
    }
  }

  function closePanel(remember) {
    if (!ui) {
      return;
    }
    ui.panel.hidden = true;
    ui.launcher.hidden = false;
    if (remember) {
      autoHidden = true;
      try {
        sessionStorage.setItem(HIDE_KEY, "1");
      } catch (error) {
        // 忽略存储失败
      }
    }
  }

  function setStatus(text, kind) {
    const current = createUI();
    current.status.textContent = text;
    current.status.className = "status" + (kind ? " " + kind : "");
  }

  function renderFields(data) {
    const current = createUI();
    current.fields.textContent = "";
    const rows = [];
    if (data.provider === "duckduckgo") {
      rows.push(["vqd_hash", mask(data.vqdHash) || "未捕获"]);
      rows.push(["fe_version", mask(data.feVersion) || "未捕获"]);
      rows.push(["fe_signals", mask(data.feSignals) || "未捕获"]);
      rows.push(["journey_id", mask(data.journeyId) || "未捕获"]);
    } else {
      rows.push(["token", mask(data.token) || "未检测到"]);
    }
    rows.push(["cookies", mask(data.cookies) || "未检测到"]);
    rows.push(["获取时间", formatTime(data.capturedAt || Date.now())]);
    for (const [name, value] of rows) {
      const dt = document.createElement("dt");
      dt.textContent = name;
      const dd = document.createElement("dd");
      dd.textContent = value;
      dd.title = value;
      current.fields.appendChild(dt);
      current.fields.appendChild(dd);
    }
  }

  function render(data) {
    const current = createUI();
    ui.copy.classList.remove("done");
    if (!data || data.error) {
      setStatus(
        (data && data.error) || "获取失败，请刷新页面后重试",
        "error"
      );
      current.copy.disabled = true;
      current.hint.textContent =
        "请确认已登录，并保持当前页面对应站点打开。";
      return;
    }

    const entry =
      (data.config &&
        data.config.providers &&
        data.config.providers[data.provider]) ||
      {};
    const ready =
      data.provider === "duckduckgo"
        ? Boolean(entry.vqd_hash)
        : Boolean(entry.token);
    current.copy.disabled = !ready;
    current.copy.textContent = "一键复制配置";

    if (ready) {
      setStatus(`已提取 ${site.label} 凭证，可一键复制配置`, "ok");
      current.launcher.classList.add("ready");
      current.launcherText.textContent = "查看已获取凭证";
    } else if (!data.loggedIn) {
      setStatus(`未检测到 ${site.label} 登录信息`, "warn");
      current.launcher.classList.remove("ready");
      current.launcherText.textContent = "登录后获取凭证";
    } else {
      const missing =
        data.provider === "duckduckgo" ? "x-vqd-hash-1 / x-fe-version / x-fe-signals" : "token";
      setStatus(`尚未捕获 ${missing}，请按下方提示操作`, "warn");
      current.launcher.classList.remove("ready");
      current.launcherText.textContent = "登录后获取凭证";
    }
    renderFields(data);
    current.hint.textContent = ready
      ? "配置片段包含敏感凭证，请勿分享；推荐粘贴到 WebUI 的 Duck.ai 面板，或 config.json 的 providers 节点。"
      : data.provider === "duckduckgo"
        ? "请先在 duck.ai 发送一条消息，以便捕获 x-vqd-hash-1、x-fe-version 与 x-fe-signals。"
        : `请先登录 ${site.label}；Qwen 可发送一条消息后再点击重新获取。`;
  }

  function launchError(error) {
    state.error = String((error && error.message) || error || "未知错误");
    render({ error: state.error });
  }

  async function detect(force) {
    if (state.busy) {
      return;
    }
    if (!force && autoHidden) {
      showLauncher("webai2api");
      return;
    }
    state.busy = true;
    if (force || !autoHidden) {
      openPanel();
    }
    setStatus("正在提取凭证…");
    if (ui) {
      ui.copy.disabled = true;
    }
    try {
      const data = await chrome.runtime.sendMessage({
        type: "capture",
        provider: site.provider,
      });
      if (!data || data.error) {
        launchError((data && data.error) || "扩展后台没有响应");
        return;
      }
      state.data = data;
      render(data);
      if (data.loggedIn) {
        if (force || !autoHidden) {
          openPanel();
        } else {
          showLauncher("webai2api");
        }
      } else {
        if (!force && autoHidden) {
          showLauncher("webai2api");
        }
      }
    } catch (error) {
      launchError(error);
    } finally {
      state.busy = false;
    }
  }

  function scheduleDetect(delay) {
    if (detectTimer) {
      clearTimeout(detectTimer);
    }
    detectTimer = setTimeout(() => {
      detectTimer = null;
      if (!autoHidden) {
        detect(false);
      }
    }, delay);
  }

  async function copyConfig() {
    if (!state.data || !ui || ui.copy.disabled) {
      return;
    }
    const text = JSON.stringify(state.data.config, null, 2);
    try {
      await navigator.clipboard.writeText(text);
    } catch (error) {
      const area = document.createElement("textarea");
      area.value = text;
      area.style.cssText = "position:fixed;left:-9999px;top:0;";
      document.body.appendChild(area);
      area.select();
      try {
        document.execCommand("copy");
      } finally {
        area.remove();
      }
    }
    ui.copy.textContent = "已复制";
    ui.copy.classList.add("done");
    setTimeout(() => {
      if (ui) {
        ui.copy.textContent = "一键复制配置";
        ui.copy.classList.remove("done");
      }
    }, 1600);
  }

  function localLoginState() {
    if (site.provider === "duckduckgo") {
      return true;
    }
    try {
      if (site.provider === "deepseek") {
        const raw = localStorage.getItem("userToken");
        if (!raw) {
          return false;
        }
        try {
          const parsed = JSON.parse(raw);
          if (parsed && typeof parsed === "object") {
            return Boolean(parsed.value || parsed.token);
          }
        } catch (error) {
          // 原始字符串也是有效 token
        }
        return Boolean(raw);
      }
      if (site.provider === "qwen") {
        return Object.keys(localStorage).some((key) => {
          const lower = key.toLowerCase();
          return (
            !lower.includes("refresh") &&
            (lower === "token" ||
              lower === "access_token" ||
              lower === "qwen_token")
          );
        });
      }
    } catch (error) {
      return false;
    }
    return false;
  }

  function reportLogin() {
    const loggedIn = localLoginState();
    chrome.runtime
      .sendMessage({
        type: "page-login",
        provider: site.provider,
        loggedIn,
        url: location.href,
      })
      .catch(() => {});
    if (loggedIn && (!state.data || !state.data.loggedIn)) {
      scheduleDetect(300);
    }
  }

  chrome.runtime.onMessage.addListener((message) => {
    if (!message || message.provider !== site.provider) {
      return false;
    }
    if (message.type === "credentials-updated") {
      if (ui && !ui.panel.hidden) {
        scheduleDetect(500);
      } else if (!autoHidden) {
        scheduleDetect(900);
      }
    }
    return false;
  });

  createUI();
  showLauncher(autoHidden ? "webai2api" : "AI 凭证");
  reportLogin();
  if (!autoHidden) {
    setTimeout(() => {
      if (!state.data) {
        detect(false);
      }
    }, 900);
  }
  pollTimer = setInterval(reportLogin, 5000);
  window.addEventListener("pagehide", () => {
    if (pollTimer) {
      clearInterval(pollTimer);
    }
  });
})();
