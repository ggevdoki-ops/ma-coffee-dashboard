"""Vercel serverless function: статус сети кофеен по FusionPOS API v2.

Отдаёт дашборду агрегаты по 4 точкам за сегодня + тренд за 7 дней.
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
    def do_GET(self):
        path = self.path.split('?', 1)[0]

        # Статика — без PIN.
        if path in STATIC_MAP:
            serve_static(self, STATIC_MAP[path])
            return

        # API — требуется PIN.
        pin = ''
        if '?' in self.path:
            qs = self.path.split('?', 1)[1]
            for kv in qs.split('&'):
                if kv.startswith('pin='):
                    pin = kv[4:]
        if not pin:
            pin = self.headers.get('x-pin', '')

        expected = get_pin()
        if expected and pin != expected:
            self.send_response(401)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(json.dumps({'error': 'invalid pin'}).encode('utf-8'))
            return

        if path != '/api/status':
            self.send_response(404)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(json.dumps({'error': 'not found'}).encode('utf-8'))
            return

        try:
            data = collect()
            body = json.dumps(data, ensure_ascii=False).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(json.dumps({'error': repr(e)}, ensure_ascii=False).encode('utf-8'))

    def log_message(self, *args):
        pass  # тишина в логах Vercel