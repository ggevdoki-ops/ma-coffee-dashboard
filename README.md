# ma-coffee-dashboard

Веб-дашборд для управляющего сети кофеен. Показывает статус смен, текущую выручку по точке, сводку по сети и отклонения от графика. Данные подтягиваются из FusionPOS API v2 параллельно по всем точкам.

## Что показывает

- **Статус смены** по каждой точке: открыта / закрыта / нет смены.
- **Бариста**, **время открытия/закрытия**.
- **Выручка сейчас** — картой, наличными, итого.
- **Чеки**: количество, средний чек, время последнего чека (с подсветкой, если >30 мин назад на открытой смене).
- **Сеть · сегодня**: общая выручка, число открытых точек, средний чек по сети.
- **Отклонения от графика**: позднее открытие (>15 мин) и раннее закрытие (>10 мин) только за текущий день.

UI обновляется раз в 60 секунд без перезагрузки.

## Стек

- **Backend**: Vercel Python serverless function (`api/status.py`). Параллельный сбор по точкам через `ThreadPoolExecutor`.
- **Frontend**: чистый HTML + CSS + JS, без фреймворков. Стиль — industrial/кофейный (Bebas Neue, Oswald, IBM Plex Mono).
- **Хранилище**: нет. Конфиг точек — через env `POINTS_JSON`.
- **Источник данных**: FusionPOS API v2 на `*.fusionpos.ru`.

## Структура репозитория

```
ma-coffee-dashboard/
├── src/                  # корень деплоя (Vercel)
│   ├── api/status.py     # serverless handler + сбор данных
│   ├── index.html        # UI
│   ├── css/style.css     # стили
│   ├── js/app.js         # логика фронта (polling, рендер)
│   ├── pyproject.toml    # зависимости + vercel entrypoint
│   ├── requirements.txt  # fallback
│   └── vercel.json       # routes, maxDuration
├── docs/
│   ├── data-overview.md  # описание данных и метрик
│   ├── local-dev.md      # как поднять локально
│   └── schema/
│       ├── points.md            # схема points.json
│       ├── api-response.md      # формат JSON-ответа
│       └── fusionpos-api.md     # что и как дёргаем из API
└── README.md             # этот файл
```

## Конфигурация точек

Реальный `points.json` с паролями в репозиторий **не входит**. Шаблон — `docs/points.example.json`. Подробная схема полей — [`docs/schema/points.md`](./docs/schema/points.md).

На Vercel конфиг передаётся как Sensitive env `POINTS_JSON` (строка-JSON). Без него бэк упадёт с `RuntimeError: POINTS_JSON env не задан`.

## API

Бэкенд отдаёт один endpoint: `GET /api/status`. Полная схема ответа — [`docs/schema/api-response.md`](./docs/schema/api-response.md). Что запрашиваем у FusionPOS — [`docs/schema/fusionpos-api.md`](./docs/schema/fusionpos-api.md).

Если задан env `DASHBOARD_PIN`, требуется `?pin=XXXX` или заголовок `x-pin: XXXX`. По умолчанию PIN не задан — бэк отдаёт данные без авторизации. Подробнее — в [`docs/local-dev.md`](./docs/local-dev.md).

## Локальная разработка

См. [`docs/local-dev.md`](./docs/local-dev.md). Короткий вариант:

```bash
cp docs/points.example.json points.json  # заполнить
export POINTS_JSON="$(cat points.json)"
python -c "from http.server import HTTPServer; from api.status import handler; HTTPServer(('127.0.0.1', 8000), handler).serve_forever()"
```

## Деплой

```bash
vercel --yes --prod --scope=<your-scope>

# если менялся points.json:
vercel env rm POINTS_JSON production --yes --scope=<your-scope>
cat points.json | vercel env add POINTS_JSON production --scope=<your-scope> --sensitive
```

## Дизайн

- Industrial/кофейный: крафтовый фон с зернистостью, чёрные рамки с жёсткими тенями, `Bebas Neue` для цифр и заголовков.
- Карточки точек: 2×2 на десктопе, 1 колонка на мобильном.
- Нижняя панель: «Сеть · сегодня» + «Отклонения от графика» бок о бок на десктопе, стопкой на мобильном.
- Цифры выручки приклеены к `₽` (`gap: 2px`), цифра 38px, знак 22px.

## Безопасность

- Пароли точек только в `POINTS_JSON` (Sensitive env), никогда не попадают в ответ клиенту.
- `points.json` (если создаётся локально) добавляется в `.gitignore` — не коммитить.
- Все ошибки сбора FusionPOS логируются в поле `errors` ответа, но **без** секретов.

## Что не входит в репозиторий

- `points.json` (реальные креды).
- `.vercel/` (состояние Vercel CLI).
- `__pycache__/`, артефакты сборки.

См. `.gitignore`.

## Лицензия

Приватный репозиторий. Использование кода вне проекта — только с разрешения владельца.