# Схема ответа API

Бэкенд отдаёт JSON по `GET /api/status` (дашборд) и `GET /api/point?name=<имя>` (окно точки за 7 дней).
Если задан env `DASHBOARD_PIN`, требуется параметр `?pin=XXXX` или заголовок `x-pin: XXXX`; иначе — открыто.

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
- `404` — путь не `/api/status` / `/api/point` и не статика; для `/api/point` — также если точка не найдена по имени.
- `500` — внутренняя ошибка (например, не задан `POINTS_JSON`). В теле: `{"error": "<repr>"}`.

Статика (`/`, `/index.html`, `/css/style.css`, `/js/app.js`) отдаётся без PIN и без авторизации, с `Cache-Control: no-cache`.

---

# Окно точки: `GET /api/point?name=<имя>`

Данные одной точки за 7 дней (`today-6 .. today`). Источник: смены `/api/v2/change` + заказы `/api/v2/orders` по `changeIds` (size=5000). Логин — с одним ретраем (FusionPOS иногда троттлит).

## Верхний уровень

```json
{
  "name": "Вместе Лучше",
  "generated_at": "2026-09-15 13:19:00",
  "days": 7,
  "totals": { "revenue": 460294.5, "checks": 433, "avg_check": 1063.04, "discount_sum": 5955.5 },
  "shifts": [ ... ],
  "hourly": [ ... ],
  "barista": [ ... ],
  "discounts": [ ... ],
  "other_status": { ... },
  "margin": { ... }
}
```

Все агрегаты по заказам считаются только по `status = "paid"` с `total_money > 0`.

### `totals`

- `revenue` — сумма чеков (paid, nonzero) за 7 дней. Не совпадает с суммой `change.revenue` — та может включать внечековые начисления.
- `checks` — число чеков.
- `avg_check` — `revenue / checks`.
- `discount_sum` — сумма скидок: `total_menu_money − total_money` по чекам со скидкой.

### `shifts[]` (отсортированы по дате убыв.)

```json
{
  "shift_id": 612,
  "date": "2026-09-09",
  "weekday": "СР",
  "barista": "Кассир",
  "open_time": "07:49",
  "close_time": "20:58",
  "is_open": false,
  "open_delta_min": -11,
  "close_delta_min": -2,
  "revenue": 88787.0,
  "revenue_orders": 90337.0,
  "orders_count": 60,
  "avg_check": 1505.62,
  "rev_per_hour": 6751.86,
  "discount_sum": 913.0,
  "cost_total": 19233.95,
  "duration_hours": 13.15
}
```

- `open_delta_min` / `close_delta_min` — отклонение факта от графика в минутах (со знаком: `+` позже графика). `null`, если график не задан.
- `revenue` — из `change.revenue`; `revenue_orders` — сумма чеков смены.
- `rev_per_hour` — `revenue / длительность`; для открытой смены длительность считается до «сейчас» (минимум 0.5 ч).
- `cost_total` — сумма `cost_price` по чекам смены; `null`, если POS не отдаёт себестоимость (все точки, кроме «Вместе Лучше»).

### `hourly`

Выручка и чеки по часу закрытия чека за 7 дней: `[{ "hour": 9, "revenue": 51464.0, "checks": 33 }, ...]`. Отсортировано по часу; часы без чеков отсутствуют.

### `barista`

Слияние двух источников: смены (`user.firstname`) и заказы (`waiterName`). Очистка имени: суффикс «Бариста» отрезается, задвоенные имена («Кассир Кассир») схлопываются.

```json
{
  "name": "Кассир",
  "shifts": 7,
  "hours": 83.8,
  "checks": 433,
  "checks_per_shift": 61.9,
  "revenue": 460294.5,
  "avg_check": 1063.04,
  "rev_per_hour": 5494.09
}
```

### `discounts`

Только чеки, где скидка реально съела деньги (`total_menu_money − total_money > 0`).

```json
{ "name": "Яндекс", "checks": 7, "sum": 3286.5, "revenue": 6103.5 }
```

### `other_status`

Возвраты и отмены за 7 дней. Незакрытые заказы (`active` и т.п.) **не** считаются.

```json
{ "by_status": { "deleted": 3 }, "total_count": 3, "total_money": 0.0 }
```

### `margin`

`null`, если в заказах нет ненулевого `cost_price`. Иначе:

```json
{
  "revenue": 460294.5,
  "cost": 104213.06,
  "margin": 356081.44,
  "margin_pct": 77.4
}
```

Себестоимость из POS, без зарплат, аренды и прочих накладных — не полная маржа бизнеса.

Статика (`/`, `/index.html`, `/css/style.css`, `/js/app.js`) отдаётся без PIN и без авторизации, с `Cache-Control: no-cache`.