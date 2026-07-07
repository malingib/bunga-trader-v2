// Bunga Trader Dashboard
const API_BASE = window.location.origin;

let signals = [];
let trades = [];
let llmStatus = [];
let strategyStatus = null;
let strategySignals = [];
let autoRefresh = null;

document.addEventListener('DOMContentLoaded', () => {
  syncApiKeyUi();
  loadAll();
  startAutoRefresh();
});

function startAutoRefresh() {
  autoRefresh = setInterval(loadAll, 5000);
}

function stopAutoRefresh() {
  if (autoRefresh) clearInterval(autoRefresh);
}

async function loadAll() {
  await Promise.all([
    loadStatus(),
    loadPendingSignals(),
    loadTrades(),
    loadLLMStatus(),
    loadStrategyStatus(),
    loadStrategySignals(),
  ]);
}

async function loadStatus() {
  try {
    const res = await fetch(`${API_BASE}/status`);
    const data = await res.json();
    document.getElementById('stat-pending').textContent = data.signals.pending_approval;
    document.getElementById('stat-approved').textContent = data.signals.approved;
    document.getElementById('stat-executed').textContent = data.signals.executed;
    document.getElementById('stat-daily').textContent = data.trading.daily_trades;
    const dailyPnl = Number(data.trading.daily_pnl || 0);
    const dailyPnlEl = document.getElementById('stat-daily-pnl');
    if (dailyPnlEl) dailyPnlEl.textContent = `${dailyPnl >= 0 ? '+' : ''}$${dailyPnl.toFixed(2)}`;

    const bridgeBadge = document.getElementById('bridge-status');
    if (data.bridge.connected_bridges > 0) {
      bridgeBadge.className = 'status-badge online';
      bridgeBadge.innerHTML = '<span class="dot"></span> Bridge Connected';
    } else {
      bridgeBadge.className = 'status-badge offline';
      bridgeBadge.innerHTML = '<span class="dot"></span> Bridge Offline';
    }
  } catch (e) {
    console.error('Status load failed:', e);
  }
}

async function loadPendingSignals() {
  try {
    const res = await fetch(`${API_BASE}/signals/pending`);
    const data = await res.json();
    signals = data.signals;
    renderSignals();
  } catch (e) {
    console.error('Signals load failed:', e);
  }
}

function renderSignals() {
  const container = document.getElementById('signal-list');
  if (signals.length === 0) {
    container.innerHTML = `
      <div class="empty-state">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg>
        <p>No pending signals</p>
        <p style="font-size: 12px; margin-top: 8px;">New signals from Telegram will appear here</p>
      </div>`;
    return;
  }

  container.innerHTML = signals.map(s => `
    <div class="signal-card" data-id="${s.id}">
      <div class="badge ${s.action.toLowerCase().replace('_', '-')}">${s.action}</div>
      <div class="signal-info">
        <div class="symbol">${s.symbol}</div>
        <div class="details">
          <span>Entry: ${s.entry || 'MARKET'}</span>
          <span>SL: ${s.sl || '-'}</span>
          <span>TP: ${s.tp || '-'}</span>
          ${s.age_minutes !== undefined && s.age_minutes !== null ? `<span class="${s.expires_in_minutes !== null && s.expires_in_minutes <= 5 ? 'urgent' : ''}">Age: ${s.age_minutes}m</span>` : ''}
          ${s.expires_in_minutes !== undefined && s.expires_in_minutes !== null ? `<span class="${s.expires_in_minutes <= 5 ? 'urgent' : ''}">Expires in: ${Math.max(0, s.expires_in_minutes)}m</span>` : ''}
          ${s.tp2 ? `<span>TP2: ${s.tp2}</span>` : ''}
          ${s.tp3 ? `<span>TP3: ${s.tp3}</span>` : ''}
        </div>
        <div class="raw-text">${s.raw_text}</div>
      </div>
      <div class="signal-actions">
        <button class="btn btn-primary" onclick="approveSignal(${s.id})" id="btn-approve-${s.id}">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="M20 6L9 17l-5-5"/></svg>
          Approve
        </button>
        <button class="btn btn-danger" onclick="rejectSignal(${s.id})" id="btn-reject-${s.id}">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="M18 6L6 18M6 6l12 12"/></svg>
          Reject
        </button>
      </div>
    </div>
  `).join('');
}

async function loadTrades() {
  try {
    const res = await fetch(`${API_BASE}/trades?limit=20`);
    const data = await res.json();
    trades = data.trades;
    renderTrades();
  } catch (e) {
    console.error('Trades load failed:', e);
  }
}

function renderTrades() {
  const tbody = document.getElementById('trade-list');
  if (trades.length === 0) {
    tbody.innerHTML = '<tr><td colspan="6" class="empty-state" style="padding: 40px;">No trades yet</td></tr>';
    return;
  }

  tbody.innerHTML = trades.map(t => `
    <tr>
      <td>${t.symbol}</td>
      <td>${t.action}</td>
      <td>${t.lot.toFixed(2)}</td>
      <td class="result-${t.result}">${t.result.toUpperCase()}</td>
      <td>${t.pnl !== null ? '$' + t.pnl.toFixed(2) : '-'}</td>
      <td>${new Date(t.executed_at).toLocaleString()}</td>
    </tr>
  `).join('');
}

async function loadLLMStatus() {
  try {
    const res = await fetch(`${API_BASE}/llm/status`);
    const data = await res.json();
    llmStatus = data.providers;
    renderLLMStatus();
  } catch (e) {
    console.error('LLM status load failed:', e);
  }
}

function renderLLMStatus() {
  const container = document.getElementById('llm-status');
  if (!container) return;
  container.innerHTML = llmStatus.map(p => `
    <div class="llm-provider ${p.available && p.remaining > 0 ? 'active' : 'exhausted'}">
      <span class="dot" style="width:6px;height:6px;"></span>
      ${p.name}: ${p.remaining}/${p.daily_limit}
    </div>
  `).join('');
}

// ── Strategy Engine ──

async function loadStrategyStatus() {
  try {
    const res = await fetch(`${API_BASE}/strategy/status`);
    if (!res.ok) {
      strategyStatus = null;
      return;
    }
    strategyStatus = await res.json();
    renderStrategyStatus();
  } catch (e) {
    // Strategy endpoint not available — engine may not be loaded
  }
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
  if (badge) {
    badge.textContent = strategyStatus.enabled ? 'Online' : 'Paused';
    badge.className = `mini-badge ${strategyStatus.enabled ? 'active' : 'inactive'}`;
  }
  grid.innerHTML = `
    <div class="strategy-card">
      <div class="s-label">Symbols</div>
      <div class="s-value">${strategyStatus.symbols.join(', ')}</div>
    </div>
    <div class="strategy-card">
      <div class="s-label">Signal Mode</div>
      <div class="s-value">${strategyStatus.signal_mode}</div>
    </div>
    <div class="strategy-card">
      <div class="s-label">Quality Threshold</div>
      <div class="s-value ${strategyStatus.quality_threshold >= 70 ? 'active' : 'warning'}">${strategyStatus.quality_threshold}/100</div>
    </div>
    <div class="strategy-card">
      <div class="s-label">SL / TP</div>
      <div class="s-value">${strategyStatus.sl_method} / ${strategyStatus.tp_method}</div>
    </div>
    <div class="strategy-card">
      <div class="s-label">Enabled Features</div>
      <div class="s-value" style="font-size: 13px;">
        ${strategyStatus.mlma_enabled ? 'MLMA ✓' : 'MLMA ✗'}
        ${strategyStatus.supertrend_enabled ? 'ST ✓' : 'ST ✗'}
        ${strategyStatus.stoch_rsi_enabled ? 'RSI ✓' : 'RSI ✗'}
        ${strategyStatus.squeeze_enabled ? 'SQZ ✓' : 'SQZ ✗'}
        ${strategyStatus.order_blocks_enabled ? 'OB ✓' : 'OB ✗'}
      </div>
    </div>
    <div class="strategy-card">
      <div class="s-label">Poll Interval</div>
      <div class="s-value">${strategyStatus.poll_interval_seconds}s</div>
    </div>
    <div class="strategy-card">
      <div class="s-label">ML Data</div>
      <div class="s-value" style="font-size: 12px;">${strategyStatus.ml_data_dir || '—'}</div>
    </div>
  `;
}

async function loadStrategySignals() {
  try {
    const res = await fetch(`${API_BASE}/strategy/last-signals?limit=10`);
    if (!res.ok) return;
    const data = await res.json();
    strategySignals = data.signals || [];
    renderStrategySignals();
  } catch (e) {
    // ignore
  }
}

function renderStrategySignals() {
  const container = document.getElementById('strategy-signal-list');
  if (!container) return;
  if (!strategySignals || strategySignals.length === 0) {
    container.innerHTML = `
      <div class="empty-state" style="padding: 30px;">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg>
        <p>No strategy signals yet</p>
        <p style="font-size: 12px;">The Quadapt engine will evaluate ${strategyStatus ? strategyStatus.symbols.join(', ') : 'configured symbols'} every ${strategyStatus ? strategyStatus.poll_interval_seconds : 'N/A'}s</p>
      </div>`;
    return;
  }
  container.innerHTML = strategySignals.slice(0, 10).map(s => {
    const scoreClass = s.quality_score >= 80 ? 'high' : s.quality_score >= 60 ? 'medium' : 'low';
    return `
      <div class="strategy-signal-card">
        <div class="badge ${s.action === 'BUY' ? 'buy' : 'sell'}">${s.action}</div>
        <div class="signal-info" style="flex:1;">
          <div class="symbol">${s.symbol}</div>
          <div class="details">
            <span>Entry: ${parseFloat(s.entry_price).toFixed(s.symbol === 'XAUUSD' ? 2 : 5)}</span>
            ${s.sl ? `<span>SL: ${parseFloat(s.sl).toFixed(5)}</span>` : ''}
            ${s.tp ? `<span>TP1: ${parseFloat(s.tp).toFixed(5)}</span>` : ''}
            <span>Conf: ${s.confidence}</span>
          </div>
        </div>
        <div class="score ${scoreClass}">${s.quality_score}</div>
      </div>
    `;
  }).join('');
}

async function forceStrategyPoll() {
  const btn = document.querySelector('#strategy-grid + .section-header button');
  if (btn) { btn.textContent = 'Polling...'; btn.disabled = true; }
  try {
    const res = await fetch(`${API_BASE}/strategy/poll`, { method: 'POST' });
    const data = await res.json();
    showToast(`Strategy poll complete: ${data.count} signal(s)`, data.count > 0 ? 'success' : 'info');
    await loadStrategySignals();
  } catch (e) {
    showToast('Strategy poll failed: ' + (e.message || e), 'error');
  } finally {
    if (btn) { btn.textContent = 'Poll Now'; btn.disabled = false; }
  }
}

function getStoredApiKey() {
  return sessionStorage.getItem('bunga_api_key') || '';
}

function syncApiKeyUi() {
  const input = document.getElementById('api-key-input');
  const state = document.getElementById('api-key-state');
  if (input) {
    input.value = getStoredApiKey();
  }
  if (state) {
    state.textContent = getStoredApiKey() ? 'API key saved' : 'API key not set';
    state.className = `mini-badge ${getStoredApiKey() ? 'active' : 'inactive'}`;
  }
}

function saveApiKey() {
  const input = document.getElementById('api-key-input');
  const key = input ? input.value.trim() : '';
  if (!key) {
    showToast('Enter an API key before saving', 'error');
    return;
  }
  sessionStorage.setItem('bunga_api_key', key);
  syncApiKeyUi();
  showToast('API key saved for this browser session', 'success');
}

function clearApiKey() {
  sessionStorage.removeItem('bunga_api_key');
  syncApiKeyUi();
  showToast('API key cleared', 'info');
}

function tradeAuthHeaders() {
  const key = sessionStorage.getItem('bunga_api_key');
  return key ? { 'X-API-Key': key } : {};
}

async function readResponseJson(res) {
  try {
    return await res.json();
  } catch (e) {
    return {};
  }
}

async function approveSignal(id) {
  setButtonLoading(id, true);
  try {
    const res = await fetch(`${API_BASE}/signals/${id}/approve`, {
      method: 'POST',
      headers: tradeAuthHeaders(),
    });
    const data = await readResponseJson(res);
    if (!res.ok) {
      throw new Error(data.detail || data.reason || `Approval failed (${res.status})`);
    }
    if (data.status === 'approved') {
      showToast(`Approved: ${data.lot_size} lots dispatched`, 'success');
    } else {
      showToast(`Rejected: ${data.reason || 'Unknown reason'}`, 'error');
    }
    setTimeout(loadAll, 500);
  } catch (e) {
    showToast(e.message || 'Failed to approve signal', 'error');
  } finally {
    setButtonLoading(id, false);
  }
}

async function rejectSignal(id) {
  setButtonLoading(id, true);
  try {
    const res = await fetch(`${API_BASE}/signals/${id}/reject`, {
      method: 'POST',
      headers: tradeAuthHeaders(),
    });
    const data = await readResponseJson(res);
    if (!res.ok) {
      throw new Error(data.detail || data.reason || `Reject failed (${res.status})`);
    }
    showToast(data.status === 'rejected' ? 'Signal rejected' : 'Signal updated', 'info');
    setTimeout(loadAll, 500);
  } catch (e) {
    showToast(e.message || 'Failed to reject signal', 'error');
  } finally {
    setButtonLoading(id, false);
  }
}

async function approveAll() {
  if (!confirm('Approve ALL pending signals? This dispatches every pending trade to MT5.')) return;
  const typed = prompt('Type APPROVE ALL to confirm batch approval:');
  if (typed !== 'APPROVE ALL') {
    showToast('Batch approval cancelled', 'info');
    return;
  }
  try {
    const res = await fetch(`${API_BASE}/signals/approve-all`, {
      method: 'POST',
      headers: tradeAuthHeaders(),
    });
    const data = await readResponseJson(res);
    if (!res.ok) {
      throw new Error(data.detail || data.reason || `Batch approve failed (${res.status})`);
    }
    showToast(`Approved ${data.approved} signals`, 'success');
    setTimeout(loadAll, 1000);
  } catch (e) {
    showToast(e.message || 'Batch approve failed', 'error');
  }
}

function setButtonLoading(id, loading) {
  const approveBtn = document.getElementById(`btn-approve-${id}`);
  const rejectBtn = document.getElementById(`btn-reject-${id}`);
  if (approveBtn) approveBtn.disabled = loading;
  if (rejectBtn) rejectBtn.disabled = loading;
}

function showToast(message, type) {
  const existing = document.querySelector('.toast');
  if (existing) existing.remove();
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.textContent = message;
  document.body.appendChild(toast);
  setTimeout(() => toast.remove(), 4000);
}
