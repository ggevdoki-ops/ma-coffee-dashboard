# Локальная разработка

Бэкенд — Vercel Python serverless function (`api/status.py` + статика). Локально можно поднять через стандартный `http.server` с минимальной обвязкой или через Vercel CLI.

## Зависимости

- Python 3.11+
- `requests>=2.32.0`
- (опционально) `vercel` CLI — для эмуляции serverless-окружения.

## Быстрый старт

1. Скопировать конфиг:
   ```bash
   cp docs/points.example.json points.json
   # заполнить username/password
   ```

2. Поднять локально (без Vercel CLI, через простой wrapper):

   ```python
   # run_local.py
   import os
   from http.server import HTTPServer
   from api.status import handler

   if __name__ == '__main__':
       os.environ['POINTS_JSON'] = open('points.json').read()
       server = HTTPServer(('127.0.0.1', 8000), handler)
       print('http://127.0.0.1:8000/')
       server.serve_forever()
   ```

   ```bash
   python run_local.py
   ```

3. Открыть:
   - `http://127.0.0.1:8000/` — UI.
   - `http://127.0.0.1:8000/api/status` — JSON (если PIN не задан).

## Через Vercel CLI

```bash
npm i -g vercel
vercel dev
```

`vercel.json` уже настроен: `entrypoint = "api.status:handler"`, `maxDuration = 30`, GitHub-интеграция отключена.

## Переменные окружения

| Имя | Описание |
|-----|----------|
| `POINTS_JSON` | **Обязателен.** JSON-строка с конфигом точек. |
| `DASHBOARD_PIN` | Опционально. Если задан — `/api/status` требует `?pin=...` или `x-pin: ...`. Бэк отдаёт `401` иначе. |

## Тестирование логики без UI

В `tmp/` рабочего проекта (`myai/projects/ma-coffee/dashboard/tmp/`) лежат скрипты:

- `local-test.py` — простой запрос к API, печать JSON в консоль.
- `range-test.py` — тест диапазонов дат для `get_changes`.
- `local-test-output*.json` — снапшоты для регрессий.

## Типичные ошибки при локальной разработке

| Симптом | Причина |
|---------|---------|
| `RuntimeError: POINTS_JSON env не задан` | Не сделан `export POINTS_JSON="$(cat points.json)"` или файл пустой. |
| UI отдаётся, но `/api/status` падает с 500 | `points.json` пустой или с битым JSON. |
| Часы в карточках неправильные | API отдаёт время не в MSK (см. `to_msk` — нужно проверить логику offset). |
| Карточка показывает «нет смены», хотя она есть | Не подходит диапазон дат в `get_changes`. Проверьте `openDateStart`/`openDateEnd`. |

## Деплой

```bash
vercel --yes --prod --scope=<your-scope>
```

Если менялся конфиг точек:

```bash
vercel env rm POINTS_JSON production --yes --scope=<your-scope>
cat points.json | vercel env add POINTS_JSON production --scope=<your-scope> --sensitive
```

После деплоя проверить:

1. Главная (`/`) отдаёт HTML.
2. `/api/status` отдаёт JSON (если задан PIN — с правильным кодом; иначе — без авторизации).
3. Карточки точек показывают актуальные данные.
4. Алерт-панель не пустая, если были отклонения.