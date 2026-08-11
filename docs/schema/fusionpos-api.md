# FusionPOS API v2 — что используем

Все точки работают через FusionPOS API v2 на хостах `*.fusionpos.ru`. Старые `*.fusion24.ru` — это веб-фронт без JSON API.

## Авторизация

```
POST {base_url}/api/v2/auth
Content-Type: application/json

{"username": "...", "password": "..."}
```

Ответ:
```json
{"token": "eyJ..."}
```

Токен используется во всех последующих запросах в заголовке `Authorization: Bearer <token>`.

## Смены

```
GET {base_url}/api/v2/change
  ?pointId={int}
  &openDateStart=YYYY-MM-DD
  &openDateEnd=YYYY-MM-DD
  &size=200
```

Возвращает `{items: [...]}`. Поля, которые используются:

| Поле | Тип | Описание |
|------|-----|----------|
| `id` | int | ID смены. |
| `open_date` | string | `YYYY-MM-DD HH:MM:SS` — уже в MSK. |
| `close_date` | string\|null | То же; `null`, если смена ещё открыта. |
| `revenue` | number | Выручка за смену. |
| `totalCardSum` | number | Оплата картой. |
| `totalCashSum` | number | Оплата наличными. |
| `user.firstname` | string | Имя бариста, открывшего смену. |

**Важно:** API v2 отдаёт `open_date`/`close_date` в MSK независимо от `timezone_offset` в конфиге. Поле в `points.json` оставлено для обратной совместимости, по факту всегда `0`.

## Заказы

```
GET {base_url}/api/v2/orders
  ?changeIds={int,int,...}
  &size=2000
```

Возвращает `{items: [...]}`. Используемые поля:

| Поле | Тип | Описание |
|------|-----|----------|
| `id` | int | ID заказа. |
| `change_id` | int | ID смены, к которой относится заказ. |
| `status` | string | `paid` / `closed` / `void` и т.п. Берём только `paid`. |
| `total_money` | number | Сумма заказа. |
| `close_date` | string | Время закрытия чека — по нему считаем «последний чек». |

## Кодировка

API отдаёт UTF-8, но иногда отвечает в cp1251 (часто у ALT Coffee 2). В `api/status.py` функция `decode()` пробует UTF-8 первой, откатывается на cp1251.

Битые имена вида `РђСЂС‚РµРј` = UTF-8 как cp1251; лечится `s.encode('cp1251').decode('utf-8')`. На текущем дашборде это не критично (имена идут через API как есть).

## Таймауты

- `POST /auth` — 20 секунд.
- `GET /change` — 45 секунд.
- `GET /orders` — 45 секунд.

На Vercel Python serverless функция имеет `maxDuration: 30` (см. `vercel.json`). Параллельный сбор по точкам через `ThreadPoolExecutor` сокращает общее время.

## Частые ошибки

| Симптом | Причина |
|---------|---------|
| `401` от API | Логин/пароль неверные или пользователь заблокирован. |
| `auth без token` | API вернул 200, но без поля `token` (редко). Проверьте схему ответа. |
| Пустой `items` в `/change` | Возможно, неправильный `point_id` или неверный диапазон дат. |
| Заказы не приходят | У заказа нет `change_id` или он пустой. |