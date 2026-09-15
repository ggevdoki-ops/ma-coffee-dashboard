"""Vercel serverless function: статус сети кофеен по FusionPOS API v2.

Отдаёт дашборду агрегаты по 4 точкам за сегодня.
Плюс детальное окно точки за 7 дней: GET /api/point?name=<имя точки>.
Пароли точек берёт из env POINTS_JSON (json-строка), PIN — из env DASHBOARD_PIN.
Пароли никогда не попадают в ответ клиенту.

Формат Vercel Python: класс handler(BaseHTTPRequestHandler) — стабильно
поддерживается runtime. GET /api/status?pin=XXXX.
"""

import json
import os
import concurrent.futures
from copy import copy
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs

import requests

MSK_OFFSET = timedelta(hours=3)  # Vercel function работает в UTC; Москва = UTC+3

WEEKDAYS_RU = ['ПН', 'ВТ', 'СР', 'ЧТ', 'ПТ', 'СБ', 'ВС']


# ----------------------------- конфиг -----------------------------

def load_points():
    raw = os.environ.get('POINTS_JSON')
    if not raw:
        raise RuntimeError('POINTS_JSON env не задан')
    points = json.loads(raw)
    for p in points:
        p.setdefault('point_id', 1)
        p.setdefault('timezone_offset', 0)
        p.setdefault('schedule', {})
    return points


def get_pin():
    return os.environ.get('DASHBOARD_PIN', '')


# ----------------------------- FusionPOS API -----------------------------

def decode(resp):
    raw = resp.content
    try:
        return json.loads(raw.decode('utf-8'))
    except UnicodeDecodeError:
        return json.loads(raw.decode('cp1251'))


def login(base_url, username, password):
    r = requests.post(
        f'{base_url}/api/v2/auth',
        json={'username': username, 'password': password},
        timeout=20,
    )
    r.raise_for_status()
    token = decode(r).get('token')
    if not token:
        raise RuntimeError(f'auth без token: {r.text[:200]}')
    return token


def get_changes(token, base_url, point_id, start_date, end_date):
    r = requests.get(
        f'{base_url}/api/v2/change',
        params={
            'pointId': point_id,
            'openDateStart': start_date,
            'openDateEnd': end_date,
            'size': 200,
        },
        headers={'Authorization': f'Bearer {token}'},
        timeout=45,
    )
    r.raise_for_status()
    return decode(r).get('items', [])


def get_orders(token, base_url, change_ids, size=1000):
    """Заказы за список смен."""
    if not change_ids:
        return []
    r = requests.get(
        f'{base_url}/api/v2/orders',
        params={
            'changeIds': ','.join(map(str, change_ids)),
            'size': size,
        },
        headers={'Authorization': f'Bearer {token}'},
        timeout=45,
    )
    r.raise_for_status()
    return decode(r).get('items', [])


# ----------------------------- время / таймзона -----------------------------

def parse_dt(value):
    if not value:
        return None
    text = str(value).strip()
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M'):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def msk_now():
    return datetime.utcnow() + MSK_OFFSET


def to_msk(point, change, field):
    """Перевод времени смены в московское.

    Проверенное поведение FusionPOS (зонды 2026-07-09): все точки отдают
    open_date и close_date уже в MSK, offset применять не нужно.
    Поле timezone_offset оставлено в points.json для обратной совместимости,
    но игнорируется — для всех точек фактически 0.
    """
    dt = parse_dt(change.get(field))
    if dt is None:
        return None
    return dt


def parse_hhmm(text):
    return datetime.strptime(text, '%H:%M').replace(year=1900, month=1, day=1)


def is_weekend_or_holiday(date_obj, holidays):
    if date_obj.weekday() >= 5:
        return True
    return date_obj.strftime('%Y-%m-%d') in (holidays or [])


def expected_times(date_obj, schedule):
    holidays = schedule.get('holidays', [])
    if is_weekend_or_holiday(date_obj, holidays):
        o = schedule.get('weekend_open', schedule.get('open'))
        c = schedule.get('weekend_close', schedule.get('close'))
    else:
        o = schedule.get('open')
        c = schedule.get('close')
    return parse_hhmm(o), parse_hhmm(c)


def time_delta_min(actual_dt, expected_dt):
    """Разница в минутах между actual (реальный datetime) и expected (1900-01-01).

    Приводим actual к базовой дате expected, чтобы считать только время.
    """
    a = actual_dt.replace(year=1900, month=1, day=1)
    return int((a - expected_dt).total_seconds() // 60)


# ----------------------------- агрегация точки -----------------------------

def _select_today_shift(changes, today_msk, now_msk):
    """Выбираем смену для отображения «сегодня».

    Правило видимости (пояс из памяти):
    - Смена видна с момента открытия и до 01:00 следующего дня.
    - До 01:00 показываем сегодняшнюю (по MSK-дате открытия) смену, даже если она
      ещё не закрыта (open) или уже закрыта (closed) — статус отдаём по факту.
    - После 01:00 смена «прошедшего дня» больше не показывается, ждём новую.
    """
    cutoff = datetime(today_msk.year, today_msk.month, today_msk.day, 1, 0)  # 01:00 сегодняшнего дня
    # Если сейчас раньше 01:00, окно видимости «смены вчерашнего дня» ещё открыто.
    in_grace = now_msk < cutoff

    # Сначала ищем сегодняшнюю (по дате открытия).
    today_shifts = []
    for ch in changes:
        open_msk = to_msk(None, ch, 'open_date')
        if open_msk is None:
            continue
        if open_msk.date() == today_msk:
            today_shifts.append(ch)
    if today_shifts:
        return max(today_shifts, key=lambda c: c.get('revenue', 0) or 0)

    # Нет сегодняшней — берём вчерашнюю только если мы в «окне видимости» до 01:00.
    if in_grace:
        yesterday = today_msk - timedelta(days=1)
        yest_shifts = [c for c in changes
                       if (to_msk(None, c, 'open_date') is not None
                           and to_msk(None, c, 'open_date').date() == yesterday)]
        if yest_shifts:
            return max(yest_shifts, key=lambda c: c.get('revenue', 0) or 0)

    return None


def analyze_orders(orders, now_msk):
    """Агрегация заказов для текущей смены."""
    paid = [o for o in orders if o.get('status') == 'paid']
    nonzero = [o for o in paid if float(o.get('total_money') or 0) > 0]

    orders_count = len(paid)
    avg_check = (
        round(sum(float(o.get('total_money') or 0) for o in nonzero) / len(nonzero), 2)
        if nonzero else 0
    )

    last_close = None
    last_minutes_ago = None
    for o in sorted(paid, key=lambda x: x.get('close_date') or '', reverse=True):
        dt = parse_dt(o.get('close_date'))
        if dt:
            last_close = dt
            last_minutes_ago = max(0, int((now_msk - dt).total_seconds() // 60))
            break

    return {
        'orders_count': orders_count,
        'avg_check': avg_check,
        'last_order_time': last_close.strftime('%H:%M') if last_close else None,
        'last_order_minutes_ago': last_minutes_ago,
    }


def build_point_payload(point, changes, orders, today_msk, now_msk):
    """Формирует блок одной точки для ответа.

    Видимость смены: с момента открытия и до 01:00 следующего дня.
    """
    schedule = point.get('schedule') or {}

    today_shift = _select_today_shift(changes, today_msk, now_msk)

    today_block = {
        'status': 'no_shift', 'barista': None, 'open_time': None,
        'close_time': None, 'revenue': 0, 'card': 0, 'cash': 0,
        'orders_count': 0, 'avg_check': 0,
        'last_order_time': None, 'last_order_minutes_ago': None,
    }
    if today_shift is not None:
        open_msk = to_msk(point, today_shift, 'open_date')
        close_msk = to_msk(point, today_shift, 'close_date')
        closed = today_shift.get('close_date') is not None
        shift_id = today_shift.get('id')
        shift_orders = [o for o in orders if o.get('change_id') == shift_id]
        order_stats = analyze_orders(shift_orders, now_msk)
        today_block = {
            'status': 'closed' if closed else 'open',
            'barista': ((today_shift.get('user') or {}).get('firstname') or '').strip() or None,
            'open_time': open_msk.strftime('%H:%M') if open_msk else None,
            'close_time': close_msk.strftime('%H:%M') if (closed and close_msk) else None,
            'revenue': round(float(today_shift.get('revenue', 0) or 0), 2),
            'card': round(float(today_shift.get('totalCardSum', 0) or 0), 2),
            'cash': round(float(today_shift.get('totalCashSum', 0) or 0), 2),
            **order_stats,
        }

    return {
        'name': point['name'],
        'schedule': {
            'open': schedule.get('open'),
            'close': schedule.get('close'),
            'weekend_open': schedule.get('weekend_open', schedule.get('open')),
            'weekend_close': schedule.get('weekend_close', schedule.get('close')),
        },
        'today': today_block,
    }


def build_alerts(points, all_changes, today_msk):
    """Отклонения от графика только за текущий день.

    - Для закрытых сегодняшних смен проверяем открытие и закрытие.
    - Для открытой сегодняшней смены проверяем только открытие.
    - Вчерашние и более ранние смены не учитываются.
    """
    alerts = []
    for point, changes in zip(points, all_changes):
        schedule = point.get('schedule') or {}
        if not schedule.get('open'):
            continue
        for ch in changes:
            open_msk = to_msk(point, ch, 'open_date')
            close_msk = to_msk(point, ch, 'close_date')
            if open_msk is None:
                continue
            if open_msk.date() != today_msk:
                continue  # только сегодня
            is_open = ch.get('close_date') is None
            exp_open, exp_close = expected_times(open_msk, schedule)

            # Открытие позже графика > 15 мин
            if open_msk.time() > (exp_open + timedelta(minutes=15)).time():
                alerts.append({
                    'point': point['name'],
                    'date': open_msk.strftime('%Y-%m-%d'),
                    'weekday': WEEKDAYS_RU[open_msk.weekday()],
                    'type': 'open_late',
                    'actual': open_msk.strftime('%H:%M'),
                    'expected': exp_open.strftime('%H:%M'),
                    'delta_min': abs(time_delta_min(open_msk, exp_open)),
                })

            if is_open:
                continue  # для открытой сегодняшней смены закрытие не проверяем

            if close_msk is None:
                continue

            # Закрытие раньше графика > 10 мин
            if close_msk.time() < (exp_close - timedelta(minutes=10)).time():
                alerts.append({
                    'point': point['name'],
                    'date': open_msk.strftime('%Y-%m-%d'),
                    'weekday': WEEKDAYS_RU[open_msk.weekday()],
                    'type': 'close_early',
                    'actual': close_msk.strftime('%H:%M'),
                    'expected': exp_close.strftime('%H:%M'),
                    'delta_min': abs(time_delta_min(close_msk, exp_close)),
                })
    # Сортировка по величине отклонения; последние 8
    alerts.sort(key=lambda a: a['delta_min'], reverse=True)
    return alerts[:8]


# ----------------------------- сбор данных -----------------------------

def fetch_point_changes(point, start_date, end_date):
    token = login(point['base_url'], point['username'], point['password'])
    changes = get_changes(token, point['base_url'], int(point['point_id']), start_date, end_date)
    return token, changes


def collect():
    points = load_points()
    now = msk_now()
    today = now.date()
    # Видимость смены — с момента открытия и до 01:00 следующего дня, поэтому
    # нужны и сегодняшние, и вчерашние смены.
    start = today - timedelta(days=1)
    start_str = start.strftime('%Y-%m-%d')
    end_str = today.strftime('%Y-%m-%d')

    errors = {}
    changes_by_name = {}
    tokens_by_name = {}

    # 1. Смены.
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(points))) as ex:
        futs = {ex.submit(fetch_point_changes, p, start_str, end_str): p for p in points}
        for fut in concurrent.futures.as_completed(futs):
            p = futs[fut]
            try:
                token, changes = fut.result()
                changes_by_name[p['name']] = changes
                tokens_by_name[p['name']] = token
            except Exception as e:
                errors[p['name']] = repr(e)
                changes_by_name[p['name']] = []
                tokens_by_name[p['name']] = None

    # Выстраиваем изменения в порядке points (маппинг по имени, не по индексу).
    ordered_changes = [changes_by_name.get(p['name'], []) for p in points]

    # 2. Заказы по сменам — параллельно, переиспользуем токены.
    orders_by_name = {}

    def fetch_orders_for_point(point, changes):
        token = tokens_by_name.get(point['name'])
        if not token or not changes:
            return point['name'], []
        ids = [c['id'] for c in changes if c.get('id')]
        try:
            orders = get_orders(token, point['base_url'], ids, size=2000)
            return point['name'], orders
        except Exception as e:
            errors[point['name']] = errors.get(point['name'], '') + ' | orders: ' + repr(e)
            return point['name'], []

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(points))) as ex:
        futs = {ex.submit(fetch_orders_for_point, p, changes_by_name.get(p['name'], [])): p for p in points}
        for fut in concurrent.futures.as_completed(futs):
            name, orders = fut.result()
            orders_by_name[name] = orders

    ordered_orders = [orders_by_name.get(p['name'], []) for p in points]

    point_payloads = [
        build_point_payload(p, ch, ord, today, now)
        for p, ch, ord in zip(points, ordered_changes, ordered_orders)
    ]
    alerts = build_alerts(points, ordered_changes, today)

    today_total = round(sum(p['today']['revenue'] for p in point_payloads), 2)
    open_count = sum(1 for p in point_payloads if p['today']['status'] == 'open')
    total_orders = sum(p['today']['orders_count'] for p in point_payloads)
    network_avg = (
        round(today_total / total_orders, 2) if total_orders else 0
    )

    return {
        'updated_at': now.strftime('%Y-%m-%d %H:%M:%S'),
        'today_date': today.strftime('%Y-%m-%d'),
        'today_weekday': WEEKDAYS_RU[today.weekday()],
        'points': point_payloads,
        'network': {
            'today_total': today_total,
            'open_count': open_count,
            'total_count': len(point_payloads),
            'orders_count': total_orders,
            'avg_check': network_avg,
        },
        'alerts': alerts,
        'errors': errors,
    }


# ----------------------------- окно точки (7 дней) -----------------------------

DETAIL_DAYS = 7
BARISTA_SUFFIX = 'Бариста'


def clean_barista(name):
    """«Ксения Бариста» -> «Ксения»; «Кассир Кассир» -> «Кассир»."""
    if not name:
        return None
    text = str(name).strip()
    if text.endswith(BARISTA_SUFFIX):
        text = text[: -len(BARISTA_SUFFIX)].strip()
    parts = text.split()
    if len(parts) > 1 and len(set(parts)) == 1:
        text = parts[0]  # FusionPOS иногда дублирует имя в waiterName
    return text or None


def shift_duration_hours(open_msk, close_msk, now_msk):
    """Длительность смены в часах; для открытой — от открытия до сейчас (мин. 0.5)."""
    if open_msk is None:
        return None
    end = close_msk or now_msk
    hours = (end - open_msk).total_seconds() / 3600
    return max(0.5, round(hours, 2))


def paid_orders(orders):
    """Оплаченные заказы с ненулевой суммой — база для всех расчётов."""
    return [
        o for o in orders
        if o.get('status') == 'paid' and float(o.get('total_money') or 0) > 0
    ]


def build_shift_row(point, change, shift_orders, now_msk):
    """Одна строка истории смен: факт vs график, выручка, чеки."""
    schedule = point.get('schedule') or {}
    open_msk = to_msk(point, change, 'open_date')
    close_msk = to_msk(point, change, 'close_date')
    closed = change.get('close_date') is not None

    paid = paid_orders(shift_orders)
    orders_count = len(paid)
    revenue_orders = round(sum(float(o.get('total_money') or 0) for o in paid), 2)
    avg_check = round(revenue_orders / orders_count, 2) if orders_count else 0
    discount_sum = round(sum(
        float(o.get('total_menu_money') or 0) - float(o.get('total_money') or 0)
        for o in paid
    ), 2)

    hours = shift_duration_hours(open_msk, close_msk, now_msk)
    revenue = round(float(change.get('revenue', 0) or 0), 2)

    open_delta = None
    close_delta = None
    if open_msk is not None and schedule.get('open'):
        exp_open, exp_close = expected_times(open_msk, schedule)
        open_delta = time_delta_min(open_msk, exp_open)
        if closed and close_msk is not None:
            close_delta = time_delta_min(close_msk, exp_close)

    cost_total = None
    if any(float(o.get('cost_price') or 0) > 0 for o in paid):
        cost_total = round(sum(float(o.get('cost_price') or 0) for o in paid), 2)

    return {
        'shift_id': change.get('id'),
        'date': open_msk.strftime('%Y-%m-%d') if open_msk else None,
        'weekday': WEEKDAYS_RU[open_msk.weekday()] if open_msk else None,
        'barista': clean_barista(((change.get('user') or {}).get('firstname') or '')),
        'open_time': open_msk.strftime('%H:%M') if open_msk else None,
        'close_time': close_msk.strftime('%H:%M') if (closed and close_msk) else None,
        'is_open': not closed,
        'open_delta_min': open_delta,
        'close_delta_min': close_delta,
        'revenue': revenue,
        'revenue_orders': revenue_orders,
        'orders_count': orders_count,
        'avg_check': avg_check,
        'rev_per_hour': round(revenue / hours, 2) if hours else None,
        'discount_sum': discount_sum,
        'cost_total': cost_total,
        'duration_hours': hours,
    }


def build_hourly(orders):
    """Гистограмма по часам за 7 дней: выручка и чеки по часу закрытия чека."""
    buckets = {}
    for o in paid_orders(orders):
        dt = parse_dt(o.get('close_date')) or parse_dt(o.get('open_date'))
        if dt is None:
            continue
        h = buckets.setdefault(dt.hour, {'revenue': 0.0, 'checks': 0})
        h['revenue'] += float(o.get('total_money') or 0)
        h['checks'] += 1
    return [
        {
            'hour': hour,
            'revenue': round(b['revenue'], 2),
            'checks': h['checks'],
        }
        for hour, b in sorted(buckets.items())
    ]


def build_barista(changes, orders, now_msk):
    """Эффективность бариста за 7 дней: смены, чеки, средний чек, выручка/час."""
    # смены по бариста — из объектов смен
    shifts_by_name = {}
    for ch in changes:
        name = clean_barista(((ch.get('user') or {}).get('firstname') or ''))
        if not name:
            continue
        open_msk = to_msk(None, ch, 'open_date')
        close_msk = to_msk(None, ch, 'close_date')
        entry = shifts_by_name.setdefault(name, {'shifts': 0, 'hours': 0.0})
        entry['shifts'] += 1
        entry['hours'] += shift_duration_hours(open_msk, close_msk, now_msk) or 0

    # чеки — из заказов (waiterName)
    stats_by_name = {}
    for o in paid_orders(orders):
        name = clean_barista(o.get('waiterName'))
        if not name:
            continue
        st = stats_by_name.setdefault(name, {'checks': 0, 'revenue': 0.0, 'sum': 0.0})
        st['checks'] += 1
        st['revenue'] += float(o.get('total_money') or 0)
        st['sum'] += float(o.get('total_money') or 0)

    rows = []
    for name in sorted(set(shifts_by_name) | set(stats_by_name)):
        s = shifts_by_name.get(name, {'shifts': 0, 'hours': 0.0})
        st = stats_by_name.get(name, {'checks': 0, 'revenue': 0.0, 'sum': 0.0})
        avg_check = round(st['revenue'] / st['checks'], 2) if st['checks'] else 0
        rev_per_hour = round(st['revenue'] / s['hours'], 2) if s['hours'] else None
        rows.append({
            'name': name,
            'shifts': s['shifts'],
            'hours': round(s['hours'], 1),
            'checks': st['checks'],
            'checks_per_shift': round(st['checks'] / s['shifts'], 1) if s['shifts'] else None,
            'revenue': round(st['revenue'], 2),
            'avg_check': avg_check,
            'rev_per_hour': rev_per_hour,
        })
    rows.sort(key=lambda r: -r['revenue'])
    return rows


def build_discounts(orders):
    """Использование акций: сколько чеков прошло со скидкой и сколько она съела."""
    by_name = {}
    for o in paid_orders(orders):
        name = (o.get('discount_name') or '').strip()
        menu = float(o.get('total_menu_money') or 0)
        total = float(o.get('total_money') or 0)
        diff = round(menu - total, 2)
        if not name or diff <= 0:
            continue
        d = by_name.setdefault(name, {'checks': 0, 'sum': 0.0, 'revenue': 0.0})
        d['checks'] += 1
        d['sum'] += diff
        d['revenue'] += total
    rows = [
        {
            'name': name,
            'checks': d['checks'],
            'sum': round(d['sum'], 2),
            'revenue': round(d['revenue'], 2),
        }
        for name, d in by_name.items()
    ]
    rows.sort(key=lambda r: -r['sum'])
    return rows


def build_other_status(orders):
    """Возвраты и отмены за 7 дней (незакрытые заказы не считаем)."""
    counts = {}
    revenue = 0.0
    for o in orders:
        status = o.get('status')
        if status not in ('deleted', 'returned'):
            continue
        counts[status] = counts.get(status, 0) + 1
        revenue += float(o.get('total_money') or 0)
    return {'by_status': counts, 'total_count': sum(counts.values()), 'total_money': round(revenue, 2)}


def build_margin(orders):
    """Маржа по чекам (только точки, где FusionPOS отдаёт cost_price)."""
    paid = paid_orders(orders)
    cost = sum(float(o.get('cost_price') or 0) for o in paid)
    revenue = sum(float(o.get('total_money') or 0) for o in paid)
    if revenue <= 0 or cost <= 0:
        return None
    return {
        'revenue': round(revenue, 2),
        'cost': round(cost, 2),
        'margin': round(revenue - cost, 2),
        'margin_pct': round((revenue - cost) / revenue * 100, 1),
    }


def get_point_detail(point, now_msk):
    """Сбор данных окна точки: смены + заказы за 7 дней, агрегаты."""
    today = now_msk.date()
    start = today - timedelta(days=DETAIL_DAYS - 1)
    token = None
    last_err = None
    for _ in range(2):  # FusionPOS иногда троттлит — один ретрай
        try:
            token = login(point['base_url'], point['username'], point['password'])
            break
        except Exception as e:
            last_err = e
    if token is None:
        raise last_err
    changes = get_changes(token, point['base_url'], int(point['point_id']),
                          start.strftime('%Y-%m-%d'), today.strftime('%Y-%m-%d'))
    ids = [c['id'] for c in changes if c.get('id')]
    orders = get_orders(token, point['base_url'], ids, size=5000)

    shifts = [build_shift_row(point, ch, [o for o in orders if o.get('change_id') == ch.get('id')], now_msk)
              for ch in changes]
    shifts = [s for s in shifts if s['date']]
    shifts.sort(key=lambda s: (s['date'], s['open_time'] or ''), reverse=True)

    paid = paid_orders(orders)
    revenue_orders = round(sum(float(o.get('total_money') or 0) for o in paid), 2)
    checks = len(paid)

    return {
        'name': point['name'],
        'generated_at': now_msk.strftime('%Y-%m-%d %H:%M:%S'),
        'days': DETAIL_DAYS,
        'totals': {
            'revenue': revenue_orders,
            'checks': checks,
            'avg_check': round(revenue_orders / checks, 2) if checks else 0,
            'discount_sum': round(sum(s['discount_sum'] for s in shifts), 2),
        },
        'shifts': shifts,
        'hourly': build_hourly(orders),
        'barista': build_barista(changes, orders, now_msk),
        'discounts': build_discounts(orders),
        'other_status': build_other_status(orders),
        'margin': build_margin(orders),
        'errors': {},
    }


# ----------------------------- static files -----------------------------

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_MAP = {
    '/': 'index.html',
    '/index.html': 'index.html',
    '/css/style.css': 'css/style.css',
    '/js/app.js': 'js/app.js',
}
STATIC_MIME = {
    '.html': 'text/html; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.js': 'application/javascript; charset=utf-8',
}


def serve_static(handler, rel_path):
    full = BASE_DIR / rel_path
    if not full.is_file():
        handler.send_response(404)
        handler.end_headers()
        return
    ext = full.suffix.lower()
    content = full.read_bytes()
    handler.send_response(200)
    handler.send_header('Content-Type', STATIC_MIME.get(ext, 'application/octet-stream'))
    handler.send_header('Cache-Control', 'no-cache')
    handler.send_header('Content-Length', str(len(content)))
    handler.end_headers()
    handler.wfile.write(content)


# ----------------------------- HTTP handler -----------------------------

class handler(BaseHTTPRequestHandler):
    def _json(self, code, data):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _query(self):
        return parse_qs(self.path.split('?', 1)[1]) if '?' in self.path else {}

    def _pin_ok(self, query):
        """PIN-проверка. Пока DASHBOARD_PIN не задан — пропускаем всех."""
        expected = get_pin()
        if not expected:
            return True
        pin = (query.get('pin', [''])[0] or self.headers.get('x-pin', ''))
        if pin == expected:
            return True
        self._json(401, {'error': 'invalid pin'})
        return False

    def do_GET(self):
        path = self.path.split('?', 1)[0]
        query = self._query()

        # Статика — без PIN.
        if path in STATIC_MAP:
            serve_static(self, STATIC_MAP[path])
            return

        # API — требуется PIN.
        if not self._pin_ok(query):
            return

        try:
            if path == '/api/status':
                self._json(200, collect())
                return

            if path == '/api/point':
                name = query.get('name', [''])[0].strip()
                point = next((p for p in load_points() if p['name'] == name), None)
                if not point:
                    self._json(404, {'error': f'точка не найдена: {name!r}'})
                    return
                self._json(200, get_point_detail(point, msk_now()))
                return

            self._json(404, {'error': 'not found'})
        except Exception as e:
            self._json(500, {'error': repr(e)})

    def log_message(self, *args):
        pass  # тишина в логах Vercel