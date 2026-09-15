/* Coffee Network · фронт дашборда */

const PIN_KEY = 'coffee_pin';
const REFRESH_MS = 60_000;
let pollTimer = null;
let clockTimer = null;
let lastData = null;

const $ = (s) => document.querySelector(s);
const el = (tag, cls, txt) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (txt != null) e.textContent = txt;
  return e;
};

const fmtRub = (n) => new Intl.NumberFormat('ru-RU').format(Math.round(n || 0));
const fmtRubK = (n) => {
  const v = Math.round(n || 0);
  if (v >= 1000) return new Intl.NumberFormat('ru-RU').format(v);
  return String(v);
};

/* ---------- PIN-гейт ---------- */
function getPin() { return sessionStorage.getItem(PIN_KEY) || ''; }
function setPin(p) { sessionStorage.setItem(PIN_KEY, p); }
function clearPin() { sessionStorage.removeItem(PIN_KEY); }

function showGate(err) {
  $('#gate').classList.remove('hidden');
  $('#board').classList.add('hidden');
  const input = $('#pin-input');
  input.value = '';
  $('#gate-error').textContent = err || '';
  setTimeout(() => input.focus(), 50);
  stopPolling();
  stopClock();
}

function showBoard() {
  $('#gate').classList.add('hidden');
  $('#board').classList.remove('hidden');
  startClock();
  startPolling();
}

$('#gate-form').addEventListener('submit', (e) => {
  e.preventDefault();
  const pin = $('#pin-input').value.trim();
  if (!pin) return;
  $('#gate-error').textContent = '';
  // проверяем PIN реальным запросом
  fetchStatus(pin).then(d => {
    setPin(pin);
    lastData = d;
    showBoard();
    render(d, true);
  }).catch(err => {
    if (err.status === 401) {
      $('#gate-error').textContent = 'Неверный код';
    } else {
      // если сеть упала, но PIN мог быть верным — не пускаем вслепую
      $('#gate-error').textContent = 'Нет связи с сервером';
    }
  });
});

/* ---------- запрос ---------- */
async function fetchStatus(pin) {
  const effective = (pin != null) ? pin : getPin();
  const url = '/api/status' + (effective ? '?pin=' + encodeURIComponent(effective) : '');
  const r = await fetch(url, { cache: 'no-store' });
  if (r.status === 401) {
    const e = new Error('invalid pin'); e.status = 401; throw e;
  }
  if (!r.ok) {
    const e = new Error('server ' + r.status); e.status = r.status; throw e;
  }
  return r.json();
}

/* ---------- часы ---------- */
function startClock() {
  tickClock();
  if (!clockTimer) clockTimer = setInterval(tickClock, 1000);
}
function stopClock() { if (clockTimer) { clearInterval(clockTimer); clockTimer = null; } }
function tickClock() {
  const d = new Date();
  $('#clock').textContent =
    String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
}

/* ---------- polling ---------- */
function startPolling() {
  pollOnce();
  if (!pollTimer) pollTimer = setInterval(pollOnce, REFRESH_MS);
}
function stopPolling() { if (pollTimer) { clearInterval(pollTimer); pollTimer = null; } }

async function pollOnce() {
  try {
    const d = await fetchStatus();
    lastData = d;
    render(d, false);
    setLive(true);
  } catch (err) {
    if (err.status === 401) {
      // Бэк снова требует PIN (DASHBOARD_PIN вернули в env) — показываем гейт.
      clearPin();
      showGate('Код больше не действует');
      return;
    }
    setLive(false);
    const u = $('#updated');
    u.textContent = 'нет связи — показаны последние данные';
    u.classList.add('err');
  }
}

function setLive(ok) {
  const dot = $('#live-dot');
  const wrap = $('.topbar-live');
  if (ok) {
    dot.classList.remove('stale');
    wrap.classList.remove('stale');
  } else {
    dot.classList.add('stale');
    wrap.classList.add('stale');
  }
}

/* ---------- рендер ---------- */
function render(d, full) {
  setLive(true);
  // дата сегодня
  $('#today-label').textContent = formatDate(d.today_date, d.today_weekday);

  renderPoints(d.points);
  renderNetwork(d.network);
  renderAlerts(d.alerts);

  const u = $('#updated');
  u.classList.remove('err');
  u.textContent = 'обновлено: ' + d.updated_at + ' МСК';
}

/* ---------- окно точки ---------- */
const MONTHS_RU = ['янв','фев','мар','апр','мая','июн','июл','авг','сен','окт','ноя','дек'];
let modalPointName = null;

function openPointModal(name) {
  modalPointName = name;
  $('#modal-point-name').textContent = name;
  $('#modal-period').textContent = 'за 7 дней · загружаем…';
  $('#modal-body').innerHTML = '<div class="modal-loading">загружаем данные…</div>';
  $('#point-modal').classList.remove('hidden');
  document.body.classList.add('modal-open');
  loadPointDetail(name);
}

function closePointModal() {
  modalPointName = null;
  $('#point-modal').classList.add('hidden');
  document.body.classList.remove('modal-open');
}

async function loadPointDetail(name) {
  const body = $('#modal-body');
  const pin = getPin();
  const url = '/api/point?name=' + encodeURIComponent(name)
    + (pin ? '&pin=' + encodeURIComponent(pin) : '');
  body.innerHTML = '<div class="modal-loading">загружаем данные…</div>';
  try {
    const r = await fetch(url, { cache: 'no-store' });
    if (r.status === 401) {
      closePointModal();
      clearPin();
      showGate('Код больше не действует');
      return;
    }
    if (!r.ok) throw new Error('server ' + r.status);
    const d = await r.json();
    if (modalPointName !== name) return; // окно уже закрыли или переключили
    renderPointDetail(d);
  } catch (err) {
    if (modalPointName !== name) return;
    body.innerHTML = '<div class="modal-loading modal-err">нет данных — ' +
      (err.message || 'ошибка сети') + '. Попробуйте ещё раз (↻).</div>';
  }
}

function section(tag, title) {
  const s = el('section', 'ps');
  s.appendChild(el('div', 'ps-tag', tag));
  if (title) s.appendChild(el('div', 'ps-title', title));
  return s;
}

function renderPointDetail(d) {
  const body = $('#modal-body');
  body.innerHTML = '';

  // сводка за 7 дней
  const totals = section('СВОДКА · 7 ДНЕЙ');
  const totalsBar = el('div', 'pd-totals');
  const addTotal = (k, v) => {
    const t = el('div', 'pd-total');
    t.appendChild(el('span', 'pt-k', k));
    t.appendChild(el('span', 'pt-v', v));
    totalsBar.appendChild(t);
  };
  addTotal('выручка', fmtRub(d.totals.revenue) + ' ₽');
  addTotal('чеков', fmtRub(d.totals.checks));
  addTotal('средний чек', d.totals.avg_check ? fmtRub(d.totals.avg_check) + ' ₽' : '—');
  if (d.totals.discount_sum > 0) addTotal('скидки', '−' + fmtRub(d.totals.discount_sum) + ' ₽');
  totals.appendChild(totalsBar);
  body.appendChild(totals);

  // история смен
  const sh = section('ИСТОРИЯ СМЕН · ' + d.days + ' ДНЕЙ');
  sh.appendChild(buildShiftTable(d));
  body.appendChild(sh);

  // загрузка по часам
  const hr = section('ЗАГРУЗКА ПО ЧАСАМ · 7 ДНЕЙ', 'выручка и чеки по часу закрытия');
  hr.appendChild(buildHourly(d.hourly));
  body.appendChild(hr);

  // бариста
  const ba = section('БАРИСТА · 7 ДНЕЙ');
  ba.appendChild(buildBaristaTable(d.barista));
  body.appendChild(ba);

  // акции
  if (d.discounts && d.discounts.length) {
    const di = section('АКЦИИ И СКИДКИ');
    di.appendChild(buildDiscounts(d.discounts, d.totals.revenue));
    body.appendChild(di);
  }

  // возвраты/отмены
  if (d.other_status && d.other_status.total_count > 0) {
    const os = d.other_status;
    const parts = Object.entries(os.by_status)
      .map(([st, n]) => `${statusRu(st)} — ${n}`);
    const ro = section('ВОЗВРАТЫ И ОТМЕНЫ');
    const row = el('div', 'pd-note', `${parts.join(' · ')} · на сумму ${fmtRub(os.total_money)} ₽`);
    ro.appendChild(row);
    body.appendChild(ro);
  }

  // шапка окна: период + время генерации
  $('#modal-period').textContent =
    `${d.days} дней · данные на ${d.generated_at.slice(11, 16)} МСК`;
}

function statusRu(st) {
  if (st === 'returned') return 'возвраты';
  if (st === 'deleted') return 'отмены';
  return st;
}

function fmtDelta(min) {
  if (min == null) return '';
  const sign = min > 0 ? '+' : '−';
  return sign + Math.abs(min) + 'м';
}

function deltaClass(kind, min) {
  // kind: 'open' — позднее открытие плохо; 'close' — раннее закрытие плохо
  if (min == null) return '';
  if (kind === 'open' && min > 15) return 'bad';
  if (kind === 'close' && min < -10) return 'warn';
  return '';
}

function buildShiftTable(d) {
  if (!d.shifts || !d.shifts.length) return el('div', 'pd-note', 'смен за период нет');
  const wrap = el('div', 'tbl-wrap');
  const table = el('table', 'tbl');
  const thead = el('thead');
  const trh = el('tr');
  const shiftCols = [
    ['Дата', ''], ['Бариста', ''], ['Открытие', ''], ['Закрытие', ''],
    ['Выручка', 'num'], ['Чеки', 'num'], ['Ср.чек', 'num'], ['₽/час', 'num'],
  ];
  for (const [h, cls] of shiftCols) {
    trh.appendChild(el('th', cls || null, h));
  }
  thead.appendChild(trh);
  table.appendChild(thead);
  const tbody = el('tbody');
  for (const s of d.shifts) {
    const tr = el('tr');
    const [y, m, day] = s.date.split('-').map(Number);
    const dateCell = el('td', 'td-date',
      `${String(day).padStart(2, '0')}.${String(m).padStart(2, '0')} ${s.weekday}`);
    if (s.is_open) dateCell.appendChild(el('span', 'live-dot td-live'));
    tr.appendChild(dateCell);
    tr.appendChild(el('td', null, s.barista || '—'));

    const openTd = el('td', 'td-time');
    openTd.appendChild(el('span', null, s.open_time || '—'));
    const openDelta = el('span', 'delta ' + deltaClass('open', s.open_delta_min), fmtDelta(s.open_delta_min));
    openDelta.title = 'отклонение от графика';
    openTd.appendChild(openDelta);
    tr.appendChild(openTd);

    const closeTd = el('td', 'td-time');
    if (s.close_time) {
      closeTd.appendChild(el('span', null, s.close_time));
      const cd = el('span', 'delta ' + deltaClass('close', s.close_delta_min), fmtDelta(s.close_delta_min));
      cd.title = 'отклонение от графика';
      closeTd.appendChild(cd);
    } else {
      closeTd.appendChild(el('span', 'open-tag', s.is_open ? 'открыта' : '—'));
    }
    tr.appendChild(closeTd);

    tr.appendChild(el('td', 'td-num', fmtRub(s.revenue)));
    tr.appendChild(el('td', 'td-num', s.orders_count || '—'));
    tr.appendChild(el('td', 'td-num', s.avg_check ? fmtRub(s.avg_check) : '—'));
    tr.appendChild(el('td', 'td-num', s.rev_per_hour ? fmtRub(s.rev_per_hour) : '—'));
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
  wrap.appendChild(table);
  return wrap;
}

function buildHourly(hourly) {
  if (!hourly || !hourly.length) return el('div', 'pd-note', 'нет данных за период');
  const maxRev = Math.max(...hourly.map(h => h.revenue), 1);
  const wrap = el('div', 'hist');
  for (const h of hourly) {
    const col = el('div', 'hist-col');
    const pct = Math.max(2, Math.round(h.revenue / maxRev * 100));
    const bar = el('div', 'hist-bar');
    bar.style.height = pct + '%';
    bar.title = `${String(h.hour).padStart(2, '0')}:00 — ${fmtRub(h.revenue)} ₽ · ${h.checks} чеков`;
    const num = el('div', 'hist-num', h.revenue >= 1000 ? fmtRubK(h.revenue) : String(Math.round(h.revenue)));
    col.appendChild(num);
    const barZone = el('div', 'hist-barzone');
    barZone.appendChild(bar);
    col.appendChild(barZone);
    col.appendChild(el('div', 'hist-hour', String(h.hour).padStart(2, '0')));
    wrap.appendChild(col);
  }
  return wrap;
}

function buildBaristaTable(barista) {
  if (!barista || !barista.length) return el('div', 'pd-note', 'нет данных за период');
  const wrap = el('div', 'tbl-wrap');
  const table = el('table', 'tbl');
  const thead = el('thead');
  const trh = el('tr');
  const baristaCols = [
    ['Бариста', ''], ['Смены', 'num'], ['Часы', 'num'], ['Чеков/смену', 'num'],
    ['Ср.чек', 'num'], ['Выручка', 'num'], ['₽/час', 'num'],
  ];
  for (const [h, cls] of baristaCols) {
    trh.appendChild(el('th', cls || null, h));
  }
  thead.appendChild(trh);
  table.appendChild(thead);
  const tbody = el('tbody');
  for (const b of barista) {
    const tr = el('tr');
    tr.appendChild(el('td', null, b.name));
    tr.appendChild(el('td', 'td-num', b.shifts));
    tr.appendChild(el('td', 'td-num', b.hours != null ? b.hours : '—'));
    tr.appendChild(el('td', 'td-num', b.checks_per_shift != null ? b.checks_per_shift : '—'));
    tr.appendChild(el('td', 'td-num', b.avg_check ? fmtRub(b.avg_check) : '—'));
    tr.appendChild(el('td', 'td-num', fmtRub(b.revenue)));
    tr.appendChild(el('td', 'td-num', b.rev_per_hour ? fmtRub(b.rev_per_hour) : '—'));
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
  wrap.appendChild(table);
  return wrap;
}

function buildDiscounts(discounts, revenue7) {
  const wrap = el('div', 'disc-list');
  let totalChecks = 0;
  let totalSum = 0;
  for (const dd of discounts) {
    const row = el('div', 'disc-row');
    row.appendChild(el('span', 'disc-name', dd.name));
    const mid = el('span', 'disc-mid');
    mid.appendChild(el('span', null, `${dd.checks} чеков`));
    if (revenue7 > 0) {
      mid.appendChild(el('span', 'disc-share',
        ` ${(dd.revenue / revenue7 * 100).toFixed(1)}% выручки`));
    }
    row.appendChild(mid);
    row.appendChild(el('span', 'disc-sum', '−' + fmtRub(dd.sum) + ' ₽'));
    wrap.appendChild(row);
    totalChecks += dd.checks;
    totalSum += dd.sum;
  }
  const total = el('div', 'disc-total');
  total.appendChild(el('span', null, `всего по акциям: ${totalChecks} чеков`));
  total.appendChild(el('span', 'disc-sum', '−' + fmtRub(totalSum) + ' ₽'));
  wrap.appendChild(total);
  return wrap;
}

function formatDate(iso, wd) {
  const months = ['янв','фев','мар','апр','мая','июн','июл','авг','сен','окт','ноя','дек'];
  const [y, m, day] = iso.split('-').map(Number);
  return `${day} ${months[m - 1]} ${y} · ${wd}`;
}

function statusInfo(status) {
  if (status === 'open') return { cls: 'open', label: 'ОТКРЫТО' };
  if (status === 'closed') return { cls: 'closed', label: 'ЗАКРЫТО' };
  return { cls: 'no_shift', label: 'НЕТ СМЕНЫ' };
}

function renderPoints(points) {
  const root = $('#points');
  root.innerHTML = '';
  // жёсткий порядок карточек: Вместе Лучше, Coffee 42, ALT1, ALT2
  const order = ['Вместе Лучше', 'Coffee 42', 'ALT Coffee 1', 'ALT Coffee 2'];
  const byName = Object.fromEntries(points.map(p => [p.name, p]));
  for (const name of order) {
    const p = byName[name];
    if (!p) continue;
    const t = p.today;
    const si = statusInfo(t.status);

    const card = el('div', 'card');

    const head = el('div', 'card-head');
    const titles = el('div');
    titles.appendChild(el('div', 'card-name', p.name));
    const sch = p.schedule;
    titles.appendChild(el('div', 'card-schedule',
      `график: ${sch.open}–${sch.close}`));
    head.appendChild(titles);
    const st = el('div', 'status ' + si.cls);
    st.appendChild(el('span', 's-dot'));
    st.appendChild(el('span', null, si.label));
    head.appendChild(st);
    card.appendChild(head);

    const shift = el('div', 'card-shift');
    const bar = el('div', 'shift-barista');
    bar.appendChild(el('span', 'b-label', 'Бариста'));
    bar.appendChild(el('span', null, t.barista || '—'));
    const opn = el('div', 'shift-times');
    if (t.open_time) {
      const row = el('div', 'shift-time-row');
      row.appendChild(el('span', 'o-label', 'Открытие'));
      row.appendChild(el('span', 'o-val', t.open_time));
      opn.appendChild(row);
    }
    if (t.close_time) {
      const row = el('div', 'shift-time-row');
      row.appendChild(el('span', 'o-label', 'Закрытие'));
      row.appendChild(el('span', 'o-val', t.close_time));
      opn.appendChild(row);
    }
    shift.appendChild(bar);
    shift.appendChild(opn);
    card.appendChild(shift);

    const stamp = el('div', 'revenue-stamp');
    const numWrap = el('div', 'rev-amount');
    const num = el('span', 'rev-num' + (t.revenue > 0 ? '' : ' zero'), fmtRub(t.revenue));
    const cur = el('span', 'rev-cur', '₽');
    numWrap.appendChild(num);
    numWrap.appendChild(cur);
    stamp.appendChild(numWrap);
    card.appendChild(stamp);

    const ordersBar = buildOrdersBar(t);
    card.appendChild(ordersBar);

    card.classList.add('clickable');
    card.title = 'открыть окно точки';
    card.addEventListener('click', () => openPointModal(p.name));

    root.appendChild(card);
  }
}

function buildOrdersBar(t) {
  const wrap = el('div', 'orders-bar');

  const count = el('div', 'orders-metric');
  count.appendChild(el('span', 'om-k', 'чеков'));
  count.appendChild(el('span', 'om-v', t.orders_count || '—'));

  const avg = el('div', 'orders-metric');
  avg.appendChild(el('span', 'om-k', 'средний чек'));
  avg.appendChild(el('span', 'om-v', t.avg_check ? fmtRubK(t.avg_check) + ' ₽' : '—'));

  const last = el('div', 'orders-metric' + (shouldWarnLastOrder(t) ? ' bad' : ''));
  last.appendChild(el('span', 'om-k', 'последний чек'));
  const lastText = formatLastOrder(t);
  last.appendChild(el('span', 'om-v', lastText));

  wrap.appendChild(count);
  wrap.appendChild(avg);
  wrap.appendChild(last);
  return wrap;
}

function shouldWarnLastOrder(t) {
  return t.status === 'open' && t.last_order_minutes_ago != null && t.last_order_minutes_ago > 30;
}

function formatLastOrder(t) {
  if (t.status !== 'open') return '—';
  if (t.last_order_time) {
    const ago = t.last_order_minutes_ago;
    if (ago == null) return t.last_order_time;
    return `${t.last_order_time} · ${ago}м назад`;
  }
  return 'нет чеков';
}

function renderNetwork(net) {
  $('#net-today').textContent = fmtRub(net.today_total);
  $('#net-open').textContent = `${net.open_count} / ${net.total_count}`;

  const sub = $('#net-sub');
  sub.innerHTML = '';

  const openItem = el('div', 'net-sub-item');
  openItem.appendChild(el('span', 'nsi-k', 'открыто точек'));
  openItem.appendChild(el('span', 'nsi-v', `${net.open_count} / ${net.total_count}`));
  sub.appendChild(openItem);

  const checksItem = el('div', 'net-sub-item');
  checksItem.appendChild(el('span', 'nsi-k', 'чеков сеть'));
  checksItem.appendChild(el('span', 'nsi-v', net.orders_count != null ? fmtRub(net.orders_count) : '—'));
  sub.appendChild(checksItem);

  const avgItem = el('div', 'net-sub-item');
  avgItem.appendChild(el('span', 'nsi-k', 'средний чек сеть'));
  avgItem.appendChild(el('span', 'nsi-v', net.avg_check ? fmtRubK(net.avg_check) + ' ₽' : '—'));
  sub.appendChild(avgItem);
}

const ALERT_TXT = {
  open_late:   'открытие позже графика',
  close_early: 'закрытие раньше графика',
  close_late:  'закрытие позже графика',
};

function renderAlerts(alerts) {
  const root = $('#alerts');
  root.innerHTML = '';
  if (!alerts || !alerts.length) {
    root.appendChild(el('div', 'alerts-empty', 'за неделю отклонений нет'));
    return;
  }
  for (const a of alerts) {
    const row = el('div', 'alert ' + a.type);
    const date = el('div', 'a-date');
    date.appendChild(el('span', 'a-wd', a.weekday + ' '));
    const [y, m, d] = a.date.split('-').map(Number);
    date.appendChild(document.createTextNode(`${String(d).padStart(2,'0')}.${String(m).padStart(2,'0')}`));
    const txt = el('div', 'a-txt');
    txt.appendChild(el('span', 'a-point', a.point));
    txt.appendChild(el('span', null,
      ` — ${ALERT_TXT[a.type] || a.type}: факт ${a.actual}, граф ${a.expected}`));
    const delta = el('div', 'a-delta', a.delta_min + 'м');
    row.appendChild(date);
    row.appendChild(txt);
    row.appendChild(delta);
    root.appendChild(row);
  }
}

/* ---------- окно точки: события ---------- */
$('#modal-close').addEventListener('click', closePointModal);
$('#modal-refresh').addEventListener('click', () => {
  if (modalPointName) loadPointDetail(modalPointName);
});
$('#point-modal').addEventListener('click', (e) => {
  if (e.target.dataset && e.target.dataset.close) closePointModal();
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') closePointModal();
});

/* ---------- старт ---------- */
(function init() {
  // По дефолту PIN-гейт отключён: пытаемся загрузить дашборд без кода.
  // Гейт показывается только в двух случаях:
  //   1. Бэк ответил 401 — на Vercel снова включён DASHBOARD_PIN.
  //   2. Первая загрузка упала по сети (init-фаза) — пользователю нужна
  //      возможность увидеть причину и/или ввести PIN руками.
  // При штатной работе ни 401, ни сеть не падают — гейт не появляется.
  fetchStatus().then(d => {
    lastData = d;
    showBoard();
    render(d, true);
  }).catch(err => {
    if (err.status === 401) {
      showGate();
    } else {
      showGate('Нет связи с сервером');
    }
  });
})();