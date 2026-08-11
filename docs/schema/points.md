# Схема конфигурации точек (points.json)

`points.json` — массив точек, которые бэкенд опрашивает параллельно. Файл в репозиторий **не входит**: реальный конфиг с паролями от FusionPOS хранится локально и в Vercel как Sensitive env `POINTS_JSON`. Шаблон — [`points.example.json`](./points.example.json).

## Поля

| Поле | Тип | Описание |
|------|-----|----------|
| `name` | string | Имя точки, отображается в карточке дашборда и в логах. |
| `base_url` | string | Хост FusionPOS API v2, **только `*.fusionpos.ru`** (на `*.fusion24.ru` живёт старая веб-версия без JSON API). |
| `username` | string | Логин кассира/администратора в FusionPOS. |
| `password` | string | Пароль. Передаётся в `POST /api/v2/auth`, в ответ приходит Bearer-токен. |
| `point_id` | int | ID точки внутри FusionPOS. Обычно `1`, но бывают исключения. |
| `timezone_offset` | int | Исторически — сдвиг часовой зоны для `open_date`/`close_date`. **По факту 0 для всех точек** (зонды 2026-07-09): API v2 отдаёт время уже в MSK. Поле оставлено в схеме для обратной совместимости. |
| `schedule.open` | `HH:MM` | График открытия в будни. |
| `schedule.close` | `HH:MM` | График закрытия в будни. |
| `schedule.weekend_open` | `HH:MM` | График открытия в выходные и праздники. |
| `schedule.weekend_close` | `HH:MM` | График закрытия в выходные и праздники. |
| `schedule.holidays` | `YYYY-MM-DD[]` | Список праздничных дат, на которых действуют `weekend_*`-значения. |

## Пример

```json
{
  "name": "Coffee 42",
  "base_url": "https://coffee42.fusionpos.ru",
  "username": "manager@example.com",
  "password": "secret",
  "point_id": 1,
  "timezone_offset": 0,
  "schedule": {
    "open": "08:00",
    "close": "21:00",
    "weekend_open": "10:00",
    "weekend_close": "22:00",
    "holidays": []
  }
}
```

## Особенности хоста

API v2 живёт на `*.fusionpos.ru`. Старые хосты `*.fusion24.ru` (`alternative.fusion24.ru`, `alter2.fusion24.ru`) — это PHP/Yii-фронт, без JSON API; для автоматики не подходят. Если точка мигрировала — замените `base_url` и проверьте работу через локальный скрипт-зонд (см. `docs/local-dev.md`).

## Локальная работа с конфигом

1. Скопировать `points.example.json` в `points.json`.
2. Заполнить реальные `username`/`password`.
3. Передать в бэкенд через env `POINTS_JSON` (строка-JSON).

## Передача в Vercel

```bash
vercel env rm POINTS_JSON production --yes --scope=<your-scope>
cat points.json | vercel env add POINTS_JSON production --scope=<your-scope> --sensitive
```

Флаг `--sensitive` обязателен — иначе Vercel не скроет значение в дашборде.