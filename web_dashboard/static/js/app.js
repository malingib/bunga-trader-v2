// Bunga Trader Dashboard
const API_BASE = window.location.origin;
let signals = [], trades = [], strategyStatus = null, strategySignals = [];
let autoRefresh = null;
let logsAutoRefresh = true, logsInterval = null;
let tradesPage = 0, tradesLimit = 20;
let tvWidgets = {};

document.addEventListener('DOMContentLoaded', () => {
  loadAll(); startAutoRefresh();
  setTimeout(initCharts, 1500);
});

function startAutoRefresh() { autoRefresh = setInterval(loadAll, 5000); }
function stopAutoRefresh() { if (autoRefresh) clearInterval(autoRefresh); }

async function loadAll() {
  await Promise.all([
    loadStatus(), loadPendingSignals(), loadTrades(),
    loadBrokerStatus(), loadStrategyStatus(), loadStrategySignals(),
    loadHistory(), loadPerSymbolPerformance(), loadLogs(),
  ]);
}

// ── Status ──
async function loadStatus() {
  try {
    const res = await fetch(`${API_BASE}/status`);
    const data = await res.json();
    document.getElementById('stat-pending').textContent = data.signals.pending_approval;
    document.getElementById('stat-approved').textContent = data.signals.approved;
    document.getElementById('stat-executed').textContent = data.signals.executed;
    document.getElementById('stat-daily').textContent = data.trading.daily_trades;
    const dp = Number(data.trading.daily_pnl || 0);
    const dpEl = document.getElementById('stat-daily-pnl');
    if (dpEl) dpEl.textContent = `${dp >= 0 ? '+' : ''}$${dp.toFixed(2)}`;
    const b = document.getElementById('bridge-status');
    if (strategyStatus && strategyStatus.enabled) { b.className = 'status-badge online'; b.innerHTML = '<span class="dot"></span> Strategy Running'; }
    else if (strategyStatus && !strategyStatus.enabled) { b.className = 'status-badge offline'; b.innerHTML = '<span class="dot"></span> Strategy Paused'; }
    else { b.className = 'status-badge offline'; b.innerHTML = '<span class="dot"></span> Strategy Offline'; }
  } catch (e) { console.error('Status:', e); }
}

// ── Pending Signals ──
async function loadPendingSignals() {
  try {
    const res = await fetch(`${API_BASE}/signals/pending`);
    signals = (await res.json()).signals; renderSignals();
  } catch (e) { console.error('Signals:', e); }
}

function renderSignals() {
  const c = document.getElementById('signal-list');
  if (!signals.length) {
    c.innerHTML = `<div class="empty-state"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg><p>No pending signals</p><p style="font-size:12px;margin-top:8px">New signals from Telegram will appear here</p></div>`;
    return;
  }
  c.innerHTML = signals.map(s => `
    <div class="signal-card" data-id="${s.id}">
      <div class="badge ${s.action.toLowerCase().replace('_', '-')}">${s.action}</div>
      <div class="signal-info">
        <div class="symbol">${s.symbol}</div>
        <div class="details">
          <span>Entry: ${s.entry || 'MARKET'}</span>
          <span>SL: ${s.sl || '-'}</span>
          <span>TP: ${s.tp || '-'}</span>
          ${s.age_minutes != null ? `<span class="${s.expires_in_minutes <= 5 ? 'urgent' : ''}">Age: ${s.age_minutes}m</span>` : ''}
          ${s.expires_in_minutes != null ? `<span class="${s.expires_in_minutes <= 5 ? 'urgent' : ''}">Expires: ${Math.max(0, s.expires_in_minutes)}m</span>` : ''}
          ${s.tp2 ? `<span>TP2: ${s.tp2}</span>` : ''}${s.tp3 ? `<span>TP3: ${s.tp3}</span>` : ''}
        </div>
        <div class="raw-text">${s.raw_text}</div>
      </div>
      <div class="signal-actions">
        <button class="btn btn-primary" onclick="approveSignal(${s.id})" id="btn-approve-${s.id}"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="M20 6L9 17l-5-5"/></svg>Approve</button>
        <button class="btn btn-danger" onclick="rejectSignal(${s.id})" id="btn-reject-${s.id}"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="M18 6L6 18M6 6l12 12"/></svg>Reject</button>
      </div>
    </div>`).join('');
}

// ── Trades (paginated) ──
async function loadTrades() {
  try {
    const offset = tradesPage * tradesLimit;
    const res = await fetch(`${API_BASE}/trades?limit=${tradesLimit}&offset=${offset}`);
    const data = await res.json();
    trades = data.trades; renderTrades(data);
  } catch (e) { console.error('Trades:', e); }
}

function renderTrades(data) {
  const tbody = document.getElementById('trade-list');
  if (!trades.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="empty-state" style="padding:40px">No trades yet</td></tr>';
    document.getElementById('trades-range').textContent = '0-0 of 0';
    return;
  }
  tbody.innerHTML = trades.map(t => `
    <tr>
      <td>${t.symbol}</td>
      <td>${t.action}</td>
      <td>${(t.lot || 0).toFixed(2)}</td>
      <td class="result-${t.result}">${(t.result || '?').toUpperCase()}</td>
      <td>${t.pnl != null ? '$' + t.pnl.toFixed(2) : '-'}</td>
      <td>${t.executed_at ? new Date(t.executed_at).toLocaleString() : '-'}</td>
    </tr>`).join('');
  const total = data.total || 0;
  const start = tradesPage * tradesLimit + 1;
  const end = Math.min(start + trades.length - 1, total);
  document.getElementById('trades-range').textContent = `${start}-${end} of ${total}`;
  document.getElementById('trades-prev').disabled = tradesPage === 0;
  document.getElementById('trades-next').disabled = end >= total;
}

function nextTradesPage() { tradesPage++; loadTrades(); }
function prevTradesPage() { if (tradesPage > 0) { tradesPage--; loadTrades(); } }

// ── Strategy ──
async function loadBrokerStatus() {
  try {
    const res = await fetch(`${API_BASE}/broker/status`);
    renderBrokerStatus(await res.json());
  } catch (e) { console.error('Broker:', e); }
}

function renderBrokerStatus(data) {
  const badge = document.getElementById('broker-status-badge');
  const select = document.getElementById('broker-select');
  const infoRow = document.getElementById('broker-info-row');
  const connectBtn = document.getElementById('broker-connect-btn');
  const disconnectBtn = document.getElementById('broker-disconnect-btn');
  const balanceEl = document.getElementById('broker-balance');
  const dot = document.getElementById('broker-connected-dot');
  const label = document.getElementById('broker-connected-label');
  if (!badge || !select) return;
  if (data.connected && data.active) { badge.textContent = `${data.active} connected`; badge.className = 'mini-badge active'; }
  else if (data.active) { badge.textContent = `${data.active} disconnected`; badge.className = 'mini-badge inactive'; }
  else { badge.textContent = 'No broker'; badge.className = 'mini-badge inactive'; }
  select.value = data.active || '';
  if (data.active) {
    infoRow.style.display = 'flex'; connectBtn.style.display = 'none'; disconnectBtn.style.display = 'inline-flex';
    if (data.connected) { dot.className = 'broker-connected-dot connected'; label.textContent = 'Connected'; label.style.color = 'var(--accent)'; balanceEl.textContent = data.balance != null ? '$' + Number(data.balance).toFixed(2) : '—'; }
    else { dot.className = 'broker-connected-dot disconnected'; label.textContent = 'Disconnected'; label.style.color = 'var(--danger)'; balanceEl.textContent = '—'; }
  } else { infoRow.style.display = 'none'; connectBtn.style.display = 'inline-flex'; disconnectBtn.style.display = 'none'; }
}

function onBrokerSelectChange() {
  const val = document.getElementById('broker-select').value;
  document.getElementById('broker-connect-btn').style.display = val ? 'inline-flex' : 'none';
  document.getElementById('broker-disconnect-btn').style.display = val ? 'none' : 'inline-flex';
}

async function connectBroker() {
  const select = document.getElementById('broker-select'), name = select.value;
  if (!name) return showToast('Select a broker first', 'error');
  const btn = document.getElementById('broker-connect-btn');
  btn.textContent = 'Connecting...'; btn.disabled = true;
  try {
    const res = await fetch(`${API_BASE}/broker/switch?name=${encodeURIComponent(name)}`, { method: 'POST' });
    if (!res.ok) throw new Error((await res.json()).detail || 'Switch failed');
    showToast(`Connected to ${name}`, 'success'); await loadBrokerStatus();
  } catch (e) { showToast('Connect failed: ' + (e.message || e), 'error'); }
  finally { btn.textContent = 'Connect'; btn.disabled = false; }
}

async function disconnectBroker() {
  const btn = document.getElementById('broker-disconnect-btn');
  btn.textContent = 'Disconnecting...'; btn.disabled = true;
  try {
    const res = await fetch(`${API_BASE}/broker/switch?name=`, { method: 'POST' });
    if (!res.ok) throw new Error((await res.json()).detail || 'Disconnect failed');
    showToast('Disconnected', 'info'); await loadBrokerStatus();
  } catch (e) { showToast('Disconnect failed: ' + (e.message || e), 'error'); }
  finally { btn.textContent = 'Disconnect'; btn.disabled = false; }
}

async function loadStrategyStatus() {
  try {
    const res = await fetch(`${API_BASE}/strategy/status`);
    if (!res.ok) { strategyStatus = null; return; }
    strategyStatus = await res.json(); renderStrategyStatus();
  } catch (e) { /* offline */ }
}

function renderStrategyStatus() {
  const grid = document.getElementById('strategy-grid');
  const badge = document.getElementById('strategy-status');
  if (!grid) return;
  if (!strategyStatus) {
    grid.innerHTML = '<div class="strategy-card full-width"><div class="s-label">Status</div><div class="s-value inactive">Engine not loaded</div></div>';
    if (badge) { badge.textContent = 'Offline'; badge.className = 'mini-badge inactive'; }
    return;
  }
  if (badge) { badge.textContent = strategyStatus.enabled ? 'Online' : 'Paused'; badge.className = `mini-badge ${strategyStatus.enabled ? 'active' : 'inactive'}`; }
  grid.innerHTML = `
    <div class="strategy-card"><div class="s-label">Symbols</div><div class="s-value">${strategyStatus.symbols.join(', ')}</div></div>
    <div class="strategy-card"><div class="s-label">Signal Mode</div><div class="s-value">${strategyStatus.signal_mode}</div></div>
    <div class="strategy-card"><div class="s-label">Quality Threshold</div><div class="s-value ${strategyStatus.quality_threshold >= 70 ? 'active' : 'warning'}">${strategyStatus.quality_threshold}/100</div></div>
    <div class="strategy-card"><div class="s-label">SL / TP</div><div class="s-value">${strategyStatus.sl_method} / ${strategyStatus.tp_method}</div></div>
    <div class="strategy-card"><div class="s-label">Features</div><div class="s-value" style="font-size:13px">${strategyStatus.mlma_enabled ? 'MLMA ✓' : 'MLMA ✗'} ${strategyStatus.supertrend_enabled ? 'ST ✓' : 'ST ✗'} ${strategyStatus.stoch_rsi_enabled ? 'RSI ✓' : 'RSI ✗'} ${strategyStatus.squeeze_enabled ? 'SQZ ✓' : 'SQZ ✗'} ${strategyStatus.order_blocks_enabled ? 'OB ✓' : 'OB ✗'}</div></div>
    <div class="strategy-card"><div class="s-label">Poll Interval</div><div class="s-value">${strategyStatus.poll_interval_seconds}s</div></div>`;
  const pb = document.getElementById('pause-resume-btn');
  if (pb) { pb.textContent = strategyStatus.enabled ? '⏸ Pause Engine' : '▶ Resume Engine'; pb.dataset.paused = !strategyStatus.enabled; pb.className = `btn ${strategyStatus.enabled ? 'btn-danger' : 'btn-secondary'}`; }
  const qs = document.getElementById('quality-slider');
  if (qs && strategyStatus.quality_threshold != null) { qs.value = strategyStatus.quality_threshold; const ve = document.getElementById('quality-val'); if (ve) ve.textContent = strategyStatus.quality_threshold; }
  const mt = document.getElementById('momentum-toggle');
  if (mt && strategyStatus.momentum_enabled != null) mt.checked = strategyStatus.momentum_enabled;
  const tg = document.getElementById('trend-gate-toggle');
  if (tg && strategyStatus.trend_gate_enabled != null) tg.checked = strategyStatus.trend_gate_enabled;
  const bb = document.getElementById('bridge-status');
  if (bb) { bb.className = `status-badge ${strategyStatus.enabled ? 'online' : 'offline'}`; bb.innerHTML = `<span class="dot"></span> ${strategyStatus.enabled ? 'Strategy Running' : 'Strategy Paused'}`; }
}

async function loadStrategySignals() {
  try {
    const res = await fetch(`${API_BASE}/strategy/last-signals?limit=10`);
    if (!res.ok) return;
    strategySignals = (await res.json()).signals || []; renderStrategySignals();
  } catch (e) { /* ignore */ }
}

function renderStrategySignals() {
  const c = document.getElementById('strategy-signal-list');
  if (!c) return;
  if (!strategySignals || !strategySignals.length) {
    c.innerHTML = `<div class="empty-state" style="padding:30px"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg><p>No strategy signals yet</p><p style="font-size:12px">The engine evaluates ${strategyStatus ? strategyStatus.symbols.join(', ') : 'configured symbols'} every ${strategyStatus ? strategyStatus.poll_interval_seconds : 'N/A'}s</p></div>`;
    return;
  }
  c.innerHTML = strategySignals.slice(0, 10).map(s => {
    const sc = s.quality_score >= 80 ? 'high' : s.quality_score >= 60 ? 'medium' : 'low';
    return `<div class="strategy-signal-card"><div class="badge ${s.action === 'BUY' ? 'buy' : 'sell'}">${s.action}</div><div class="signal-info" style="flex:1"><div class="symbol">${s.symbol}</div><div class="details"><span>Entry: ${parseFloat(s.entry_price).toFixed(s.symbol === 'XAUUSD' ? 2 : 5)}</span>${s.sl ? `<span>SL: ${parseFloat(s.sl).toFixed(5)}</span>` : ''}${s.tp ? `<span>TP1: ${parseFloat(s.tp).toFixed(5)}</span>` : ''}<span>Conf: ${s.confidence}</span></div></div><div class="score ${sc}">${s.quality_score}</div></div>`;
  }).join('');
}

async function forceStrategyPoll() {
  const btn = document.getElementById('strategy-poll-btn');
  if (btn) { btn.textContent = 'Polling...'; btn.disabled = true; }
  try {
    const res = await fetch(`${API_BASE}/strategy/poll`, { method: 'POST' });
    const data = await res.json();
    showToast(`Poll complete: ${data.count} signal(s)`, data.count > 0 ? 'success' : 'info');
    await loadStrategySignals();
  } catch (e) { showToast('Poll failed: ' + (e.message || e), 'error'); }
  finally { if (btn) { btn.textContent = 'Poll Now'; btn.disabled = false; } }
}

async function readResponseJson(res) { try { return await res.json(); } catch (e) { return {}; } }

// ── Approve / Reject ──
async function approveSignal(id) {
  setButtonLoading(id, true);
  try {
    const res = await fetch(`${API_BASE}/signals/${id}/approve`, { method: 'POST' });
    const data = await readResponseJson(res);
    if (!res.ok) throw new Error(data.detail || data.reason || `Approval failed (${res.status})`);
    showToast(data.status === 'approved' ? `Approved: ${data.lot_size} lots` : `Rejected: ${data.reason}`, data.status === 'approved' ? 'success' : 'error');
    setTimeout(loadAll, 500);
  } catch (e) { showToast(e.message || 'Failed', 'error'); }
  finally { setButtonLoading(id, false); }
}

async function rejectSignal(id) {
  setButtonLoading(id, true);
  try {
    const res = await fetch(`${API_BASE}/signals/${id}/reject`, { method: 'POST' });
    const data = await readResponseJson(res);
    if (!res.ok) throw new Error(data.detail || `Reject failed (${res.status})`);
    showToast('Signal rejected', 'info'); setTimeout(loadAll, 500);
  } catch (e) { showToast(e.message || 'Failed', 'error'); }
  finally { setButtonLoading(id, false); }
}

async function approveAll() {
  if (!confirm('Approve ALL pending signals?')) return;
  if (prompt('Type APPROVE ALL to confirm:') !== 'APPROVE ALL') return showToast('Cancelled', 'info');
  try {
    const res = await fetch(`${API_BASE}/signals/approve-all`, { method: 'POST' });
    const data = await readResponseJson(res);
    if (!res.ok) throw new Error(data.detail || `Batch approve failed (${res.status})`);
    showToast(`Approved ${data.approved} signals`, 'success'); setTimeout(loadAll, 1000);
  } catch (e) { showToast(e.message || 'Batch approve failed', 'error'); }
}
function setButtonLoading(id, l) { ['approve','reject'].forEach(a => { const b = document.getElementById(`btn-${a}-${id}`); if (b) b.disabled = l; }); }

function showToast(m, t) {
  const existing = document.querySelector('.toast'); if (existing) existing.remove();
  const toast = document.createElement('div'); toast.className = `toast ${t}`; toast.textContent = m;
  document.body.appendChild(toast); setTimeout(() => toast.remove(), 4000);
}

// ── Performance ──
async function loadHistory() {
  try {
    const res = await fetch(`${API_BASE}/strategy/history`);
    if (!res.ok) return;
    renderHistory(await res.json());
  } catch (e) { /* ignore */ }
}

function renderHistory(data) {
  const wr = document.getElementById('perf-win-rate');
  const tt = document.getElementById('perf-total-trades');
  const tp = document.getElementById('perf-total-pnl');
  const bd = document.getElementById('perf-best-day');
  const wd = document.getElementById('perf-worst-day');
  if (wr) { const p = data.win_rate != null ? (data.win_rate * 100).toFixed(1) + '%' : (data.total_trades ? ((data.winning_trades / data.total_trades) * 100).toFixed(1) + '%' : '—'); wr.textContent = p; }
  if (tt) tt.textContent = data.total_trades ?? '—';
  if (tp) { const p = Number(data.total_pnl || 0); tp.textContent = (p >= 0 ? '+' : '') + '$' + p.toFixed(2); tp.className = `value ${p >= 0 ? 'accent' : 'danger'}`; }
  if (bd) bd.textContent = data.best_day != null ? '$' + Number(data.best_day).toFixed(2) : '—';
  if (wd) wd.textContent = data.worst_day != null ? '$' + Number(data.worst_day).toFixed(2) : '—';
  if (data.equity_curve && data.equity_curve.length > 1) {
    renderEquityCurve(data.equity_curve);
    renderDailyPnl(data.equity_curve);
  }
}

function renderEquityCurve(curve) {
  const c = document.getElementById('equity-chart-canvas');
  if (!c || !curve.length) return;
  const ctx = c.getContext('2d'), w = 600, h = 200, pad = 20;
  c.width = w; c.height = h;
  ctx.clearRect(0, 0, w, h);
  const vals = curve.map(d => d.cumulative);
  const min = Math.min(...vals), max = Math.max(...vals), range = max - min || 1;
  const xs = vals.map((_, i) => pad + (i / (vals.length - 1 || 1)) * (w - 2 * pad));
  const ys = vals.map(v => h - pad - ((v - min) / range) * (h - 2 * pad));
  // Grid lines
  ctx.strokeStyle = '#1e293b'; ctx.lineWidth = 1;
  for (let i = 0; i < 4; i++) { const y = pad + (i / 3) * (h - 2 * pad); ctx.beginPath(); ctx.moveTo(pad, y); ctx.lineTo(w - pad, y); ctx.stroke(); }
  // Gradient fill
  const grad = ctx.createLinearGradient(0, ys[0], 0, h - pad);
  grad.addColorStop(0, 'rgba(16,185,129,0.3)'); grad.addColorStop(1, 'rgba(16,185,129,0.01)');
  ctx.beginPath(); ctx.moveTo(xs[0], h - pad);
  xs.forEach((x, i) => ctx.lineTo(x, ys[i])); ctx.lineTo(xs[xs.length - 1], h - pad); ctx.closePath();
  ctx.fillStyle = grad; ctx.fill();
  // Line
  ctx.beginPath(); xs.forEach((x, i) => i === 0 ? ctx.moveTo(x, ys[i]) : ctx.lineTo(x, ys[i]));
  ctx.strokeStyle = '#10b981'; ctx.lineWidth = 2; ctx.stroke();
  // Dots
  ctx.fillStyle = '#10b981';
  xs.forEach((x, i) => { if (i % Math.max(1, Math.floor(xs.length / 20)) === 0 || i === xs.length - 1) { ctx.beginPath(); ctx.arc(x, ys[i], 3, 0, Math.PI * 2); ctx.fill(); } });
}

function renderDailyPnl(curve) {
  const c = document.getElementById('daily-pnl-canvas');
  if (!c || !curve.length) return;
  const ctx = c.getContext('2d'), w = 600, h = 100, pad = 10;
  c.width = w; c.height = h;
  ctx.clearRect(0, 0, w, h);
  const last10 = curve.slice(-10);
  const pnls = last10.map(d => d.pnl || 0);
  const maxPnl = Math.max(...pnls.map(Math.abs), 1);
  const step = (w - 2 * pad) / last10.length;
  const bw = Math.max(step - 4, 3);
  // Zero line
  const zeroY = h - pad;
  ctx.strokeStyle = '#334155'; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(pad, zeroY); ctx.lineTo(w - pad, zeroY); ctx.stroke();
  last10.forEach((d, i) => {
    const pnl = d.pnl || 0, barH = (Math.abs(pnl) / maxPnl) * (h - 2 * pad);
    const x = pad + i * step + 2, y = pnl >= 0 ? h - pad - barH : h - pad;
    ctx.fillStyle = pnl >= 0 ? '#10b981' : '#ef4444';
    ctx.beginPath(); ctx.roundRect ? ctx.roundRect(x, y, bw, Math.max(barH, 1), [2, 2, 0, 0]) : ctx.rect(x, y, bw, Math.max(barH, 1)); ctx.fill();
  });
}

// ── Per-Symbol Performance ──
async function loadPerSymbolPerformance() {
  try {
    // First try dedicated endpoint, fallback to history endpoint data
    let res = await fetch(`${API_BASE}/performance/per-symbol`);
    if (!res.ok) {
      console.error('Per-symbol fetch error:', await res.text(), '\nRetry:', e);
      throw new Error('per-symbol endpoint failed');
    }
    const data = await res.json();
    renderPerSymbol(data);
  } catch (e) {
    // Fallback: extract from history
    try {
      const res = await fetch(`${API_BASE}/strategy/history`);
      if (!res.ok) return;
      const h = await res.json();
      if (h.per_symbol) renderPerSymbol(h.per_symbol);
    } catch (e2) { /* ignore */ }
  }
}

function renderPerSymbol(data) {
  const tbody = document.getElementById('perf-symbol-body');
  if (!tbody) return;
  const symbols = Object.keys(data);
  if (!symbols.length) {
    tbody.innerHTML = '<tr><td colspan="7" class="empty-state" style="padding:30px">No trade data yet</td></tr>';
    return;
  }
  tbody.innerHTML = symbols.map(sym => {
    const d = data[sym];
    const pnl = d.pnl || 0;
    return `<tr>
      <td><strong>${sym}</strong></td>
      <td>${d.trades || 0}</td>
      <td class="result-success">${d.wins || 0}</td>
      <td class="result-failed">${d.losses || 0}</td>
      <td>${((d.win_rate || 0) * 100).toFixed(1)}%</td>
      <td class="${pnl >= 0 ? 'result-success' : 'result-failed'}">${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)}</td>
      <td>$${(d.avg_pnl || 0).toFixed(2)}</td>
    </tr>`;
  }).join('');
}

// ── Strategy Controls ──
async function toggleStrategyPause() {
  const btn = document.getElementById('pause-resume-btn');
  const isPaused = btn ? btn.dataset.paused === 'true' : false;
  try {
    const res = await fetch(`${API_BASE}/strategy/toggle`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ paused: !isPaused }) });
    const data = await res.json();
    if (btn) { btn.textContent = data.paused ? '▶ Resume Engine' : '⏸ Pause Engine'; btn.dataset.paused = data.paused; btn.className = `btn ${data.paused ? 'btn-secondary' : 'btn-danger'}`; }
    showToast(data.paused ? 'Engine paused' : 'Engine resumed', 'success'); await loadStrategyStatus();
  } catch (e) { showToast('Toggle failed: ' + (e.message || e), 'error'); }
}

async function updateStrategyConfig(key, value) {
  try {
    const res = await fetch(`${API_BASE}/strategy/config`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ [key]: value }) });
    if (!res.ok) throw new Error('Config update failed');
    showToast(`Updated ${key}: ${value}`, 'success'); await loadStrategyStatus();
  } catch (e) { showToast('Config update failed: ' + (e.message || e), 'error'); }
}

// ── 4 TradingView Charts ──
function initCharts() {
  if (typeof TradingView === 'undefined') { setTimeout(initCharts, 500); return; }
  const symbols = [
    { id: 'tv-chart-xauusd', ticker: 'OANDA:XAUUSD' },
    { id: 'tv-chart-sp500', ticker: 'OANDA:SPX500USD' },
    { id: 'tv-chart-nas100', ticker: 'OANDA:US100USD' },
    { id: 'tv-chart-eurusd', ticker: 'OANDA:EURUSD' },
  ];
  symbols.forEach(s => {
    const container = document.getElementById(s.id);
    if (!container) return;
    tvWidgets[s.ticker] = new TradingView.widget({
      symbol: s.ticker,
      interval: '15',
      timezone: 'Etc/UTC',
      theme: 'dark',
      style: '1',
      locale: 'en',
      toolbar_bg: '#1a1a2e',
      enable_publishing: false,
      hide_top_toolbar: true,
      hide_legend: true,
      save_image: false,
      height: 200,
      width: '100%',
      container_id: s.id,
      studies: [],
      autosize: true,
    });
  });
}

// ── Live Prices Strip ──
async function loadPrices() {
  try {
    const res = await fetch(`${API_BASE}/market/live`);
    if (!res.ok) return;
    const data = await res.json();
    for (const [sym, price] of Object.entries(data)) {
      const el = document.getElementById(`price-${sym}`);
      if (el) {
        const dir = price.change >= 0 ? '\u25B2' : '\u25BC';
        el.innerHTML = `${sym} ${price.price.toFixed(price.decimals || 2)} <span style="color:${price.change >= 0 ? '#10b981' : '#ef4444'}">${dir} ${Math.abs(price.change).toFixed(2)}</span>`;
        el.className = `price-tile ${price.change >= 0 ? 'up' : 'down'}`;
      }
    }
  } catch (e) { /* ignore */ }
}

// Wrap loadAll to also fetch prices and logs
const _origLoadAll = loadAll;
loadAll = async function() {
  await _origLoadAll();
  await loadPrices();
  if (logAutoRefreshOn) await loadLogs();
};

// ── Live Logs Viewer ──
async function loadLogs() {
  try {
    const res = await fetch(`${API_BASE}/logs/latest?lines=100`);
    if (!res.ok) return;
    const data = await res.json();
    renderLogs(data);
  } catch (e) { /* ignore */ }
}

function renderLogs(data) {
  const viewer = document.getElementById('log-viewer');
  const fileName = document.getElementById('log-file-name');
  if (!viewer) return;
  if (fileName) fileName.textContent = data.file || '—';
  if (!data.lines || !data.lines.length) {
    viewer.innerHTML = '<div class="log-line log-empty">No log data yet</div>';
    return;
  }
  viewer.innerHTML = data.lines.map(l => {
    const cls = l.includes('ERROR') ? 'log-error' : l.includes('WARNING') ? 'log-warn' : l.includes('INFO') ? 'log-info' : '';
    const time = l.match(/\d{2}:\d{2}:\d{2}/);
    const label = time ? `<span class="log-time">${time[0]}</span>` : '';
    return `<div class="log-line ${cls}">${label}${escapeHtml(l)}</div>`;
  }).join('');
  // Auto-scroll to bottom
  viewer.scrollTop = viewer.scrollHeight;
}

let logAutoRefreshOn = true;
function toggleLogAutoRefresh() {
  logAutoRefreshOn = !logAutoRefreshOn;
  const btn = document.getElementById('log-toggle-btn');
  if (btn) btn.textContent = logAutoRefreshOn ? '⏸ Pause' : '▶ Resume';
  showToast(logAutoRefreshOn ? 'Log auto-refresh on' : 'Log auto-refresh off', 'info');
}

// ── Utilities ──
function escapeHtml(text) {
  const d = document.createElement('div'); d.textContent = text; return d.innerHTML;
}

// ── Init ──
document.addEventListener('DOMContentLoaded', () => {
  loadAll();
  startAutoRefresh();
  setTimeout(initCharts, 1500);
});
