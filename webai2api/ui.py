UI_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>webai2api 控制台</title>
<style>
:root{color-scheme:dark;--bg:#0d1117;--panel:#161b22;--panel2:#1c2330;--line:#30363d;--text:#e6edf3;--muted:#8b949e;--accent:#58a6ff;--ok:#3fb950;--warn:#d29922;--bad:#f85149}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}
header{display:flex;flex-wrap:wrap;gap:12px;align-items:center;justify-content:space-between;padding:16px 24px;background:var(--panel);border-bottom:1px solid var(--line);position:sticky;top:0;z-index:10}
h1{font-size:18px;margin:0;font-weight:600}
h1 span{color:var(--accent)}
.badge{font-size:12px;padding:2px 8px;border-radius:999px;background:var(--panel2);border:1px solid var(--line);color:var(--muted)}
main{max-width:1100px;margin:0 auto;padding:24px}
.tabs{display:flex;gap:8px;margin-bottom:18px;flex-wrap:wrap}
.tab{padding:8px 14px;border-radius:8px;border:1px solid var(--line);background:var(--panel);color:var(--muted);cursor:pointer}
.tab.active{color:var(--text);border-color:var(--accent);background:var(--panel2)}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:18px;margin-bottom:18px}
.panel h2{font-size:15px;margin:0 0 14px;font-weight:600}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:14px}
label{display:block;font-size:12px;color:var(--muted);margin-bottom:6px}
input,textarea,select{width:100%;padding:9px 10px;border-radius:8px;border:1px solid var(--line);background:#0b1017;color:var(--text);font:inherit;outline:none}
input:focus,textarea:focus{border-color:var(--accent)}
textarea{min-height:84px;resize:vertical;font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:12px}
button{padding:9px 14px;border-radius:8px;border:1px solid var(--line);background:var(--panel2);color:var(--text);cursor:pointer;font:inherit}
button.primary{background:#1f6feb;border-color:#1f6feb}
button:hover{filter:brightness(1.12)}
button:disabled{opacity:.5;cursor:not-allowed}
.row{display:flex;gap:10px;align-items:end;flex-wrap:wrap}
.row .field{flex:1;min-width:140px}
.actions{display:flex;gap:10px;margin-top:14px;align-items:center;flex-wrap:wrap}
.hint{font-size:12px;color:var(--muted);margin-top:10px;line-height:1.6}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
.chip{font-size:11px;padding:3px 8px;border-radius:999px;background:var(--panel2);border:1px solid var(--line);color:var(--muted)}
.provider{border:1px solid var(--line);border-radius:10px;padding:14px;background:var(--panel2)}
.provider .name{display:flex;justify-content:space-between;align-items:center;font-weight:600;margin-bottom:6px}
.dot{width:8px;height:8px;border-radius:50%;display:inline-block;margin-right:6px}
.dot.ok{background:var(--ok)}.dot.bad{background:var(--bad)}.dot.warn{background:var(--warn)}
.usage{height:6px;border-radius:4px;background:#0b1017;overflow:hidden;margin-top:8px}
.usage > i{display:block;height:100%;background:var(--accent);width:0}
.kv{display:flex;justify-content:space-between;gap:10px;font-size:12px;color:var(--muted);margin-top:6px}
.kv b{color:var(--text);font-weight:500}
.toast{position:fixed;right:20px;bottom:20px;max-width:420px;padding:12px 14px;border-radius:10px;background:var(--panel2);border:1px solid var(--line);box-shadow:0 8px 30px rgba(0,0,0,.4);opacity:0;transform:translateY(8px);transition:.2s;pointer-events:none}
.toast.show{opacity:1;transform:none}
.toast.err{border-color:var(--bad)}
.hidden{display:none!important}
code{font-family:ui-monospace,Consolas,monospace;background:#0b1017;padding:1px 5px;border-radius:4px}
</style>
</head>
<body>
<header>
  <h1>webai<span>2api</span> 控制台</h1>
  <div class="row" style="gap:8px">
    <input id="apiKey" type="password" placeholder="API Key (Bearer)" style="width:260px">
    <button id="saveKey">保存 Key</button>
    <span id="conn" class="badge">未连接</span>
  </div>
</header>
<main>
  <div class="tabs">
    <button class="tab active" data-tab="overview">概览</button>
    <button class="tab" data-tab="limit">限流 / RPM</button>
    <button class="tab" data-tab="duck">Duck.ai</button>
  </div>

  <section id="tab-overview">
    <div class="panel">
      <h2>运行状态</h2>
      <div class="grid" id="statusGrid"></div>
      <div class="hint" id="warningBox"></div>
    </div>
    <div class="panel">
      <h2>Provider</h2>
      <div class="grid" id="providerGrid"></div>
    </div>
  </section>

  <section id="tab-limit" class="hidden">
    <div class="panel">
      <h2>全局 RPM</h2>
      <div class="row">
        <div class="field"><label>每分钟请求数（0 = 不限制）</label><input id="rpm" type="number" min="0" step="1"></div>
        <div class="field"><label>统计窗口（秒）</label><input id="rpmWindow" type="number" min="0.1" step="0.1"></div>
      </div>
      <div class="grid" style="margin-top:14px" id="providerRpmGrid"></div>
    </div>
    <div class="panel">
      <h2>超限排队</h2>
      <div class="row">
        <div class="field"><label>启用排队</label><select id="queueEnabled"><option value="true">开启</option><option value="false">关闭</option></select></div>
        <div class="field"><label>队列上限</label><input id="queueSize" type="number" min="0" step="1"></div>
        <div class="field"><label>最长等待（秒）</label><input id="queueWait" type="number" min="0" step="0.1"></div>
      </div>
      <div class="actions"><button class="primary" id="saveRpm">保存限流设置</button><span class="hint" id="rpmHint"></span></div>
    </div>
    <div class="panel">
      <h2>实时用量</h2>
      <div id="usageBox"></div>
    </div>
  </section>

  <section id="tab-duck" class="hidden">
    <div class="panel">
      <h2>Duck.ai 凭证</h2>
      <div class="grid">
        <div><label>vqd_hash（x-vqd-hash-1，浏览器计算值）</label><textarea id="duckVqd" placeholder="base64 挑战值"></textarea></div>
        <div><label>fe_version（x-fe-version）</label><input id="duckFeVersion" placeholder="serp_20261021_104105_ET-..."></div>
        <div><label>fe_signals（x-fe-signals）</label><textarea id="duckFeSignals" placeholder="base64 JSON"></textarea></div>
        <div><label>Cookie</label><textarea id="duckCookies" placeholder="name=value; name2=value2"></textarea></div>
        <div><label>User-Agent</label><input id="duckUa" placeholder="Mozilla/5.0 ..."></div>
        <div><label>Base URL</label><input id="duckBase" placeholder="https://duck.ai"></div>
      </div>
      <div class="actions"><button class="primary" id="saveDuck">保存 Duck.ai 配置</button><span class="hint">修改后自动热重载，无需重启服务。</span></div>
      <div class="hint">推荐安装 chrome-extension：打开 <code>https://duck.ai/</code> 发送一条消息，扩展会自动捕获 vqd_hash、fe_version、fe_signals 与 Cookie。vqd_hash / fe_signals 有时效性，失效后重新发送消息再捕获。</div>
    </div>
    <div class="panel">
      <h2>Duck.ai 状态</h2>
      <div id="duckStatus" class="hint"></div>
    </div>
  </section>
</main>
<div class="toast" id="toast"></div>
<script>
const $ = (id) => document.getElementById(id);
let state = { config: null, health: null };

function toast(msg, isErr) {
  const el = $('toast');
  el.textContent = msg;
  el.className = 'toast show' + (isErr ? ' err' : '');
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { el.className = 'toast'; }, 4000);
}
function apiHeaders() {
  const key = $('apiKey').value.trim();
  return Object.assign({'Content-Type':'application/json'}, key ? {'Authorization':'Bearer ' + key} : {});
}
async function api(path, opts) {
  opts = opts || {};
  const res = await fetch(path, Object.assign({}, opts, {headers: Object.assign(apiHeaders(), opts.headers || {})}));
  const text = await res.text();
  let data = null;
  try { data = JSON.parse(text); } catch (e) { data = text; }
  if (!res.ok) {
    const msg = (data && data.error && data.error.message) || text || ('HTTP ' + res.status);
    throw new Error(msg);
  }
  return data;
}
function setTab(name) {
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === name));
  ['overview','limit','duck'].forEach(n => $('tab-' + n).classList.toggle('hidden', n !== name));
  if (name === 'duck') renderDuck();
  if (name === 'limit') renderUsage();
}
document.querySelectorAll('.tab').forEach(t => t.addEventListener('click', () => setTab(t.dataset.tab)));

function providerCard(name, p, health) {
  const ok = p.enabled && p.configured;
  const dot = ok ? 'ok' : (p.enabled ? 'bad' : 'warn');
  const status = health && health.error ? '错误' : (health && health.status) || (p.enabled ? '已启用' : '已禁用');
  const chips = (p.models || []).map(m => '<span class="chip">' + m + '</span>').join('');
  const extra = [];
  if ('vqd_hash_set' in p) extra.push('vqd_hash: ' + (p.vqd_hash_set ? '已配置' : '未配置'));
  if (p.token_set !== undefined) extra.push('token: ' + (p.token_set ? '已配置' : '未配置'));
  if (p.cookies_set !== undefined) extra.push('cookies: ' + (p.cookies_set ? '已配置' : '未配置'));
  return '<div class="provider"><div class="name"><span><span class="dot ' + dot + '"></span>' + name + '</span><span class="badge">' + status + '</span></div>' +
    '<div class="kv"><span>Base URL</span><b>' + (p.base_url || '-') + '</b></div>' +
    (extra.length ? '<div class="kv"><span>凭证</span><b>' + extra.join(' / ') + '</b></div>' : '') +
    '<div class="chips">' + chips + '</div></div>';
}

function renderOverview() {
  const cfg = state.config || {};
  const health = state.health || {};
  const rl = health.rate_limit || {};
  $('statusGrid').innerHTML =
    '<div class="provider"><div class="name">服务</div><div class="kv"><span>版本</span><b>' + (health.version || '-') + '</b></div><div class="kv"><span>配置版本</span><b>' + (cfg.config_version || health.config_version || '-') + '</b></div><div class="kv"><span>运行时长</span><b>' + (health.uptime || 0) + ' 秒</b></div></div>' +
    '<div class="provider"><div class="name">限流</div><div class="kv"><span>全局 RPM</span><b>' + (rl.default_rpm != null ? rl.default_rpm : (cfg.rpm || 0)) + '</b></div><div class="kv"><span>窗口</span><b>' + (rl.window_seconds || cfg.rpm_window || 60) + ' 秒</b></div><div class="kv"><span>排队</span><b>' + ((cfg.queue && cfg.queue.enabled) ? '开启' : '关闭') + '</b></div></div>' +
    '<div class="provider"><div class="name">默认 Provider</div><div class="kv"><span>名称</span><b>' + (cfg.default_provider || '-') + '</b></div><div class="kv"><span>Provider 数</span><b>' + Object.keys(cfg.providers || {}).length + '</b></div><div class="kv"><span>API Key</span><b>' + (cfg.api_key_required ? '必需' : '可选') + '</b></div></div>';
  const providers = cfg.providers || {};
  const ph = health.providers || {};
  $('providerGrid').innerHTML = Object.keys(providers).map(n => providerCard(n, providers[n], ph[n])).join('') || '<div class="hint">暂无 Provider</div>';
  const warnings = health.warnings || [];
  $('warningBox').innerHTML = warnings.length ? ('<span class="dot warn"></span> ' + warnings.map(w => '<div>' + w + '</div>').join('')) : '';
}

function renderLimit() {
  const cfg = state.config || {};
  $('rpm').value = cfg.rpm != null ? cfg.rpm : 30;
  $('rpmWindow').value = cfg.rpm_window != null ? cfg.rpm_window : 60;
  const q = cfg.queue || {};
  $('queueEnabled').value = q.enabled === false ? 'false' : 'true';
  $('queueSize').value = q.max_size != null ? q.max_size : 200;
  $('queueWait').value = q.max_wait != null ? q.max_wait : 120;
  const per = cfg.rpm_per_provider || {};
  const names = Object.keys(cfg.providers || {});
  $('providerRpmGrid').innerHTML = names.map(n =>
    '<div><label>' + n + ' RPM（留空 = 跟随全局）</label><input data-provider-rpm="' + n + '" type="number" min="0" step="1" value="' + (per[n] != null ? per[n] : '') + '"></div>'
  ).join('') || '<div class="hint">暂无 Provider</div>';
  renderUsage();
}

function renderUsage() {
  const rl = (state.health && state.health.rate_limit) || {};
  const providers = rl.providers || {};
  const rows = Object.keys(providers).map(n => {
    const p = providers[n] || {};
    const rpm = p.rpm || 0;
    const used = p.used || 0;
    const pct = rpm > 0 ? Math.min(100, Math.round(used / rpm * 100)) : 0;
    return '<div class="provider" style="margin-bottom:10px"><div class="name"><span>' + n + '</span><span class="badge">' + used + ' / ' + (rpm > 0 ? rpm : '∞') + '</span></div><div class="usage"><i style="width:' + pct + '%"></i></div></div>';
  }).join('');
  $('usageBox').innerHTML = rows || '<div class="hint">尚无使用记录</div>';
}

function renderDuck() {
  const cfg = state.config || {};
  const duck = (cfg.providers || {}).duckduckgo || {};
  $('duckVqd').value = '';
  $('duckVqd').placeholder = duck.vqd_hash_set ? ('已配置（' + (duck.vqd_hash_length || 0) + ' 字符），留空保持不变') : 'base64 挑战值';
  $('duckFeVersion').value = duck.fe_version || '';
  $('duckFeSignals').value = '';
  $('duckFeSignals').placeholder = duck.fe_signals_set ? '已配置，留空保持不变' : 'base64 JSON';
  $('duckCookies').value = '';
  $('duckCookies').placeholder = duck.cookies_set ? '已配置，留空保持不变' : 'name=value; name2=value2';
  $('duckUa').value = duck.user_agent || '';
  $('duckBase').value = duck.base_url || 'https://duck.ai';
  $('duckStatus').innerHTML =
    '<div class="kv"><span>启用</span><b>' + (duck.enabled ? '是' : '否') + '</b></div>' +
    '<div class="kv"><span>vqd_hash</span><b>' + (duck.vqd_hash_set ? ('已配置 (' + (duck.vqd_hash_length || 0) + ' 字符)') : '未配置') + '</b></div>' +
    '<div class="kv"><span>fe_version</span><b>' + (duck.fe_version || '未配置') + '</b></div>' +
    '<div class="kv"><span>fe_signals</span><b>' + (duck.fe_signals_set ? '已配置' : '未配置') + '</b></div>' +
    '<div class="kv"><span>Cookie</span><b>' + (duck.cookies_set ? '已配置' : '未配置') + '</b></div>';
}

async function loadConfig() {
  const data = await api('/admin/config');
  state.config = data.config || {};
  $('conn').textContent = '已连接 · v' + (data.config_version != null ? data.config_version : '');
  $('conn').style.color = 'var(--ok)';
  renderOverview(); renderLimit();
  if (!$('tab-duck').classList.contains('hidden')) renderDuck();
}
async function loadHealth() {
  try {
    const res = await fetch('/health');
    state.health = await res.json();
    renderOverview();
    if (!$('tab-limit').classList.contains('hidden')) renderUsage();
  } catch (e) {}
}

$('saveKey').addEventListener('click', async () => {
  localStorage.setItem('webai2api_key', $('apiKey').value.trim());
  try { await loadConfig(); toast('已连接'); } catch (e) { toast(e.message, true); }
});

$('saveRpm').addEventListener('click', async () => {
  const per = {};
  document.querySelectorAll('[data-provider-rpm]').forEach(el => {
    if (el.value.trim() !== '') per[el.dataset.providerRpm] = Number(el.value);
  });
  const body = {
    rpm: Number($('rpm').value),
    rpm_window: Number($('rpmWindow').value),
    rpm_per_provider: per,
    queue: {
      enabled: $('queueEnabled').value === 'true',
      max_size: Number($('queueSize').value),
      max_wait: Number($('queueWait').value)
    }
  };
  try {
    const data = await api('/admin/rpm', {method:'POST', body: JSON.stringify(body)});
    state.config = data.config || state.config;
    renderLimit(); toast('限流设置已保存');
  } catch (e) { toast(e.message, true); }
});

$('saveDuck').addEventListener('click', async () => {
  const body = {
    fe_version: $('duckFeVersion').value.trim(),
    user_agent: $('duckUa').value.trim(),
    base_url: $('duckBase').value.trim()
  };
  ['vqd_hash','fe_signals','cookies'].forEach(key => {
    const el = {vqd_hash:$('duckVqd'), fe_signals:$('duckFeSignals'), cookies:$('duckCookies')}[key];
    if (el.value.trim()) body[key] = el.value.trim();
  });
  try {
    const data = await api('/admin/duck', {method:'POST', body: JSON.stringify(body)});
    state.config = data.config || state.config;
    renderDuck(); toast('Duck.ai 配置已保存');
  } catch (e) { toast(e.message, true); }
});

$('apiKey').value = localStorage.getItem('webai2api_key') || '';
setTab('overview');
loadConfig().catch(e => {
  $('conn').textContent = '未连接';
  $('conn').style.color = 'var(--bad)';
  toast('请填写 API Key：' + e.message, true);
});
loadHealth();
setInterval(loadHealth, 5000);
</script>
</body>
</html>
"""
