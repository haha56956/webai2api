const PROVIDERS = {
  deepseek: {
    label: "DeepSeek",
    hosts: ["chat.deepseek.com"],
    cookieUrl: "https://chat.deepseek.com/",
  },
  qwen: {
    label: "Qwen",
    hosts: ["chat.qwen.ai"],
    cookieUrl: "https://chat.qwen.ai/",
  },
  duckduckgo: {
    label: "DuckDuckGo",
    hosts: ["duck.ai", "duckduckgo.com"],
    cookieUrls: ["https://duckduckgo.com/", "https://duck.ai/"],
  },
};

const CAPTURE_HEADERS = new Set([
  "authorization",
  "cookie",
  "user-agent",
  "x-vqd-hash-1",
  "x-vqd-4",
  "x-fe-version",
  "x-fe-signals",
  "x-ddg-journey-id",
]);

let captured = {};
let loginState = {};

const ready = chrome.storage.session
  .get(["captured", "loginState"])
  .then((stored) => {
    captured = stored.captured || {};
    loginState = stored.loginState || {};
    updateBadge();
  })
  .catch(() => {});

function providerForHost(host) {
  for (const [name, meta] of Object.entries(PROVIDERS)) {
    if (meta.hosts.some((item) => host === item || host.endsWith("." + item))) {
      return name;
    }
  }
  return null;
}

function providerForUrl(url) {
  try {
    return providerForHost(new URL(url).hostname);
  } catch (error) {
    return null;
  }
}

function updateBadge() {
  const active = Object.values(loginState).filter(Boolean).length;
  chrome.action.setBadgeBackgroundColor({ color: "#16a34a" });
  chrome.action.setBadgeText({ text: active > 0 ? String(active) : "" });
}

chrome.webRequest.onSendHeaders.addListener(
  (details) => {
    const provider = providerForUrl(details.url);
    if (!provider) {
      return;
    }
    const headers = {};
    for (const header of details.requestHeaders || []) {
      const name = (header.name || "").toLowerCase();
      if (CAPTURE_HEADERS.has(name) && header.value) {
        headers[name] = header.value;
      }
    }
    if (!Object.keys(headers).length) {
      return;
    }
    const entry = captured[provider] || { headers: {} };
    entry.headers = { ...(entry.headers || {}), ...headers };
    entry.url = details.url;
    entry.time = Date.now();
    captured[provider] = entry;
    chrome.storage.session.set({ captured }).catch(() => {});
    loginState[provider] = true;
    chrome.storage.session.set({ loginState }).catch(() => {});
    updateBadge();
    if (details.tabId != null && details.tabId >= 0) {
      chrome.tabs
        .sendMessage(details.tabId, {
          type: "credentials-updated",
          provider,
        })
        .catch(() => {});
    }
  },
  {
    urls: [
      "https://chat.deepseek.com/*",
      "https://chat.qwen.ai/*",
      "https://duckduckgo.com/*",
      "https://duck.ai/*",
    ],
    types: ["xmlhttprequest", "main_frame", "sub_frame"],
  },
  ["requestHeaders", "extraHeaders"]
);

function parseMaybeJson(value) {
  if (typeof value !== "string" || !value) {
    return "";
  }
  const text = value.trim();
  if (!text.startsWith("{") && !text.startsWith('"')) {
    return value;
  }
  try {
    const parsed = JSON.parse(text);
    if (typeof parsed === "string") {
      return parsed;
    }
    if (parsed && typeof parsed === "object") {
      for (const key of ["value", "token", "access_token"]) {
        if (typeof parsed[key] === "string" && parsed[key]) {
          return parsed[key];
        }
      }
    }
  } catch (error) {
    return value;
  }
  return value;
}

function stripBearer(value) {
  return String(value || "").replace(/^bearer\s+/i, "").trim();
}

function findToken(localStorage, names) {
  for (const name of names) {
    for (const [key, value] of Object.entries(localStorage || {})) {
      if (key.toLowerCase() === name.toLowerCase() && value) {
        const token = parseMaybeJson(value);
        if (token) {
          return stripBearer(token);
        }
      }
    }
  }
  const wanted = new Set(
    names.map((name) => name.toLowerCase().replace(/_/g, ""))
  );
  for (const [key, value] of Object.entries(localStorage || {})) {
    const lower = key.toLowerCase().replace(/_/g, "");
    if (wanted.has(lower)) {
      const token = parseMaybeJson(value);
      if (token && token.length > 10) {
        return stripBearer(token);
      }
    }
  }
  return "";
}

function parseCookies(header) {
  const result = {};
  for (const part of String(header || "").split(";")) {
    const item = part.trim();
    if (!item || !item.includes("=")) {
      continue;
    }
    const index = item.indexOf("=");
    result[item.slice(0, index).trim()] = item.slice(index + 1).trim();
  }
  return result;
}

function cookieValue(header, names) {
  const cookies = parseCookies(header);
  for (const name of names) {
    if (cookies[name]) {
      return stripBearer(parseMaybeJson(cookies[name]));
    }
  }
  return "";
}

async function readPage(tabId) {
  try {
    const results = await chrome.scripting.executeScript({
      target: { tabId },
      func: () => {
        const data = {
          url: location.href,
          localStorage: {},
          cookie: document.cookie,
        };
        try {
          for (let index = 0; index < localStorage.length; index += 1) {
            const key = localStorage.key(index);
            data.localStorage[key] = localStorage.getItem(key);
          }
        } catch (error) {
          data.error = String(error);
        }
        return data;
      },
    });
    return results && results[0] ? results[0].result : null;
  } catch (error) {
    return null;
  }
}

async function cookieHeader(urls) {
  const seen = new Map();
  for (const url of urls) {
    try {
      const cookies = await chrome.cookies.getAll({ url });
      for (const cookie of cookies) {
        if (!seen.has(cookie.name)) {
          seen.set(cookie.name, cookie.value);
        }
      }
    } catch (error) {
      // 忽略不可访问的 URL
    }
  }
  return Array.from(seen.entries())
    .map(([name, value]) => `${name}=${value}`)
    .join("; ");
}

async function activeTab(tabId) {
  if (tabId != null) {
    try {
      return await chrome.tabs.get(tabId);
    } catch (error) {
      return null;
    }
  }
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  return tabs && tabs[0] ? tabs[0] : null;
}

function buildEntry(provider, token, cookies, vqdHash, userAgent, extra) {
  const entry = {};
  if (provider === "duckduckgo") {
    if (vqdHash) {
      entry.vqd_hash = vqdHash;
    }
    if (extra && extra.feVersion) {
      entry.fe_version = extra.feVersion;
    }
    if (extra && extra.feSignals) {
      entry.fe_signals = extra.feSignals;
    }
    if (cookies) {
      entry.cookies = cookies;
    }
    if (userAgent) {
      entry.user_agent = userAgent;
    }
    return entry;
  }
  if (token) {
    entry.token = token;
  }
  if (cookies) {
    entry.cookies = cookies;
  }
  return entry;
}

async function capture(providerName, tabId) {
  await ready;
  const meta = PROVIDERS[providerName];
  if (!meta) {
    throw new Error(`未知 provider: ${providerName}`);
  }
  const tab = await activeTab(tabId);
  let host = "";
  try {
    host = tab && tab.url ? new URL(tab.url).hostname : "";
  } catch (error) {
    host = "";
  }
  const onSite = meta.hosts.some(
    (item) => host === item || host.endsWith("." + item)
  );
  const page = onSite && tab ? await readPage(tab.id) : null;
  const local = (page && page.localStorage) || {};
  const capturedEntry = captured[providerName] || {};
  const headers = capturedEntry.headers || {};
  const cookieUrls = [...(meta.cookieUrls || [meta.cookieUrl])];
  if (onSite && tab && tab.url) {
    cookieUrls.push(tab.url);
  }
  const cookies = await cookieHeader(cookieUrls);
  const cookieToken = cookieValue(cookies, [
    "token",
    "access_token",
    "qwen_token",
    "userToken",
    "user_token",
  ]);

  let token = "";
  let vqdHash = "";
  let vqd = "";
  if (providerName === "deepseek") {
    token =
      findToken(local, ["userToken", "user_token", "ds_token"]) ||
      stripBearer(headers.authorization) ||
      cookieToken;
  } else if (providerName === "qwen") {
    token =
      findToken(local, ["token", "access_token", "qwen_token"]) ||
      stripBearer(headers.authorization) ||
      cookieToken;
  } else {
    vqdHash = headers["x-vqd-hash-1"] || "";
    vqd = headers["x-vqd-4"] || "";
  }

  const userAgent = headers["user-agent"] || "";
  const feVersion = headers["x-fe-version"] || "";
  const feSignals = headers["x-fe-signals"] || "";
  const journeyId = headers["x-ddg-journey-id"] || "";
  const entry = buildEntry(providerName, token, cookies, vqdHash, userAgent, {
    feVersion,
    feSignals,
  });
  const loggedIn =
    providerName === "duckduckgo"
      ? Boolean(vqdHash || cookies)
      : Boolean(token);
  if (loggedIn) {
    loginState[providerName] = true;
    chrome.storage.session.set({ loginState }).catch(() => {});
    updateBadge();
  }

  return {
    provider: providerName,
    label: meta.label,
    onSite,
    loggedIn,
    pageUrl: tab ? tab.url || "" : "",
    token,
    cookies,
    vqdHash,
    vqd,
    feVersion,
    feSignals,
    journeyId,
    userAgent,
    capturedAt: capturedEntry.time || null,
    config: { providers: { [providerName]: entry } },
  };
}

async function status(providerName, tabId) {
  await ready;
  const meta = PROVIDERS[providerName];
  if (!meta) {
    return { error: `未知 provider: ${providerName}` };
  }
  const tab = await activeTab(tabId);
  let host = "";
  try {
    host = tab && tab.url ? new URL(tab.url).hostname : "";
  } catch (error) {
    host = "";
  }
  const onSite = meta.hosts.some(
    (item) => host === item || host.endsWith("." + item)
  );
  return {
    provider: providerName,
    label: meta.label,
    onSite,
    loggedIn: Boolean(loginState[providerName]),
  };
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message || typeof message.type !== "string") {
    return false;
  }
  if (message.type === "capture") {
    const tabId =
      message.tabId != null
        ? message.tabId
        : sender && sender.tab
          ? sender.tab.id
          : undefined;
    capture(message.provider, tabId)
      .then(sendResponse)
      .catch((error) =>
        sendResponse({ error: String((error && error.message) || error) })
      );
    return true;
  }
  if (message.type === "status") {
    status(message.provider, message.tabId)
      .then(sendResponse)
      .catch((error) =>
        sendResponse({ error: String((error && error.message) || error) })
      );
    return true;
  }
  if (message.type === "page-login") {
    if (message.provider) {
      loginState[message.provider] =
        Boolean(message.loggedIn) || Boolean(captured[message.provider]);
      chrome.storage.session.set({ loginState }).catch(() => {});
      updateBadge();
    }
    sendResponse({ ok: true });
    return false;
  }
  return false;
});
