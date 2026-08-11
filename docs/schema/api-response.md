# Схема ответа API

Бэкенд отдаёт JSON по `GET /api/status`. Если задан env `DASHBOARD_PIN`, требуется параметр `?pin=XXXX` или заголовок `x-pin: XXXX`; иначе — открыто.

## Верхний уровень

```json
{
  "updated_at": "2026-08-11 14:32:11",
  "today_date": "2026-08-11",
  "today_weekday": "ПН",
  "points": [ ... ],
  "network": { ... },
  "alerts": [ ... ],
  "errors": { "Point Name": "error text" }
}
```

| Поле | Описание |
|------|----------|
| `updated_at` | Время ответа в MSK (UTC+3), формат `YYYY-MM-DD HH:MM:SS`. |
| `today_date` | Дата «сегодня» в MSK. |
| `today_weekday` | День недели на русском, 2 буквы (`ПН`, `ВТ`, ...). |
| `points` | Массив карточек точек (см. ниже). |
| `network` | Сводка по сети. |
| `alerts` | Отклонения от графика, **только за сегодня**, отсортированы по `delta_min` убыв., лимит 8. |
| `errors` | Ошибки сбора по конкретным точкам (например, сетевой сбой). Если всё ок — пустой объект. |

## Точка

```json
{
  "name": "Coffee 42",
  "schedule": {
    "open": "08:00",
    "close": "21:00",
    "weekend_open": "10:00",
    "weekend_close": "22:00"
  },
  "today": {
    "status": "open",
    "barista": "Ксения",
    "open_time": "08:49",
    "close_time": null,
    "revenue": 12450.0,
    "card": 9450.0,
    "cash": 3000.0,
    "orders_count": 87,
    "avg_check": 143.1,
    "last_order_time": "14:31",
    "last_order_minutes_ago": 1
  }
}
```

### `status`

- `no_shift` — до открытия или после 01:00 следующего дня (вчерашняя закрытая смена уже не показывается).
- `open` — с момента открытия и до момента закрытия. `close_time` всегда `null`.
- `closed` — смена закрыта, но ещё в окне видимости (до 01:00 следующего дня). `close_time` заполнен.

### `barista`

Имя из `user.firstname` в API v2. Если пусто/None — `null`.

### `open_time` / `close_time`

Строки `HH:MM` в MSK. Если время неизвестно (нет открытия или смена ещё открыта) — `null`.

### `revenue` / `card` / `cash`

В рублях, округлено до 2 знаков. На момент запроса — это **выручка текущей смены**, не за день.

### `orders_count` / `avg_check`

Считаются только по оплаченным заказам (`status = "paid"`) с ненулевой суммой. Если заказов нет — `orders_count = 0`, `avg_check = 0`.

### `last_order_time` / `last_order_minutes_ago`

Время последнего оплаченного заказа и сколько минут назад он был. Используется для подсветки «давно не было чеков» (>30 мин на открытой смене).

## Сеть

```json
{
  "today_total": 47820.0,
  "open_count": 3,
  "total_count": 4,
  "orders_count": 312,
  "avg_check": 153.27
}
```

| Поле | Описание |
|------|----------|
| `today_total` | Сумма `revenue` по всем точкам за сегодня. |
| `open_count` | Точек в статусе `open`. |
| `total_count` | Всего точек в конфиге. |
| `orders_count` | Сумма `orders_count` по точкам. |
| `avg_check` | `today_total / orders_count`, 0 если чеков нет. |

## Алерт

```json
{
  "point": "Coffee 42",
  "date": "2026-08-11",
  "weekday": "ПН",
  "type": "open_late",
  "actual": "09:13",
  "expected": "08:00",
  "delta_min": 73
}
```

### `type`

- `open_late` — открытие позже графика на >15 мин.
- `close_early` — закрытие раньше графика на >10 мин (только для закрытых сегодняшних смен).

Раннее открытие и позднее закрытие **не** фиксируются.

## Коды ошибок

- `200` — JSON-ответ.
- `401` — неверный PIN (если `DASHBOARD_PIN` задан).
- `404` — путь не `/api/status` и не статика.
- `500` — внутренняя ошибка (например, не задан `POINTS_JSON`). В теле: `{"error": "<repr>"}`.

Статика (`/`, `/index.html`, `/css/style.css`, `/js/app.js`) отдаётся без PIN и без авторизации, с `Cache-Control: no-cache`.