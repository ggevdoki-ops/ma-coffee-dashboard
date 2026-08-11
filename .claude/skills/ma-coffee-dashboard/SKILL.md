---
name: ma-coffee-dashboard
description: Работа с исходниками онлайн-дашборда сети кофеен — backend на Vercel (FusionPOS API v2), фронт без фреймворков, конфиг точек.
metadata:
  type: project
---

Скилл для проекта **ma-coffee-dashboard**. Используй его, когда задача касается:
- исходников дашборда `myai/projects/ma-coffee/dashboard/` или репозитория `ggevdoki-ops/ma-coffee-dashboard`;
- сбора/отображения данных по точкам Coffee 42, ALT Coffee 1, ALT Coffee 2, Вместе Лучше;
- конфигурации точек в `points.json` / `POINTS_JSON`;
- деплоя на Vercel, фронта (HTML/CSS/JS), бэка (`api/status.py`).

## Описание

Дашборд для управляющего сети кофеен — статус смен, текущая выручка, чеки, отклонения от графика. Данные подтягиваются из FusionPOS API v2 параллельно по всем точкам, агрегируются на бэке (Vercel Python serverless), отдаются фронту как JSON, обновляются раз в 60 секунд без перезагрузки.

- **Production URL:** `https://ma-coffee-dashboard.vercel.app`
- **Репозиторий (приватный):** `https://github.com/ggevdoki-ops/ma-coffee-dashboard`
- **Локальные исходники:** `myai/projects/ma-coffee/dashboard/`
- **Локальная сборка репозитория:** `myai/projects/ma-coffee/repo/`
- **PIN на дашборде:** отключён (инфраструктура сохранена в коде, можно вернуть одной env).

## Стек

- **Backend:** Vercel Python serverless function (`src/api/status.py`). Параллельный сбор через `ThreadPoolExecutor`.
- **Frontend:** чистый HTML + CSS + JS, без фреймворков. Industrial/кофейный стиль (Bebas Neue, Oswald, IBM Plex Mono).
- **Источник:** FusionPOS API v2 на `*.fusionpos.ru` (на `*.fusion24.ru` его нет — старая веб-версия).
- **Хранилище:** нет. Конфиг точек — env `POINTS_JSON` на Vercel (Sensitive).

## Ключевые файлы в репозитории

```
ggevdoki-ops/ma-coffee-dashboard
├── src/                     # корень деплоя на Vercel
│   ├── api/status.py        # serverless handler + сбор данных
│   ├── index.html           # UI (PIN-экран + дашборд)
│   ├── css/style.css        # стили
│   ├── js/app.js            # polling + рендер
│   ├── pyproject.toml       # зависимости + vercel entrypoint
│   ├── requirements.txt     # fallback
│   └── vercel.json          # routes, maxDuration, github disabled
├── docs/
│   ├── data-overview.md     # описание данных и метрик
│   ├── local-dev.md         # как поднять локально
│   ├── points.example.json  # шаблон конфига (реальный points.json — в gitignore)
│   └── schema/
│       ├── points.md        # описание полей points.json
│       ├── api-response.md  # формат JSON-ответа /api/status
│       └── fusionpos-api.md # что и как дёргаем у FusionPOS
├── README.md                # описание проекта
└── .gitignore
```

## Типичные задачи

### Обновить выгрузку данных

Файл: `src/api/status.py`. Логика сбора — в `collect()`. После правок:

```bash
cd myai/projects/ma-coffee/dashboard
vercel --yes --prod --scope=<your-scope>
```

### Поменять фронт (раскладка/цвета/тексты)

Файлы: `src/index.html`, `src/css/style.css`, `src/js/app.js`. Деплой — аналогично.

### Добавить новую точку

1. В локальном `points.json` добавить запись по образцу из `docs/points.example.json`.
2. Обновить бэк, если меняется имя хоста/параметры (см. `docs/schema/fusionpos-api.md`).
3. Передать обновлённый конфиг в Vercel:
   ```bash
   vercel env rm POINTS_JSON production --yes --scope=<your-scope>
   cat points.json | vercel env add POINTS_JSON production --scope=<your-scope> --sensitive
   ```

### Включить PIN обратно

```bash
vercel env add DASHBOARD_PIN production --scope=<your-scope>   # значение, например 1711
```

Гейт автоматически появится — фронт показывает его при 401 от бэка.

### Обновить данные конфигурации (alias userinfo)

`points.json` хранится локально и в Vercel через Sensitive env `POINTS_JSON`. Локально он gitignored. После изменения всегда нужно обновлять обе копии.

## Команды

```bash
# Локальный запуск (без Vercel CLI)
cd 'C:/Users/Геннадий/myai/projects/ma-coffee/repo'
export POINTS_JSON="$(cat ../points.json)"
python -c "from http.server import HTTPServer; from api.status import handler; HTTPServer(('127.0.0.1', 8000), handler).serve_forever()"

# Локальный запуск (с Vercel CLI)
cd 'C:/Users/Геннадий/myai/projects/ma-coffee/repo/src'
vercel dev

# Деплой
cd 'C:/Users/Геннадий/myai/projects/ma-coffee/repo/src'
vercel --yes --prod --scope=<your-scope>
```

## Что важно помнить

1. **Пароли в коде не хранятся.** Реальный `points.json` — в gitignore. На Vercel — Sensitive env `POINTS_JSON`.
2. **FusionPOS API v2 — это `*.fusionpos.ru`**, не `*.fusion24.ru`. Старые хосты — это PHP-фронт без JSON API.
3. **API v2 уже отдаёт время в MSK.** `timezone_offset` в `points.json` по факту всегда `0`. Функция `to_msk` это не меняет — она применяет offset только если он не ноль (текущая реализация в проде — нулевая, для обратной совместимости).
4. **Видимость смены:** три состояния (`no_shift` / `open` / `closed`) с окном до 01:00 следующего дня. Реализовано через `_select_today_shift` и `in_grace`-флаг.
5. **Алерты только за сегодня.** Позднее открытие (>15 мин) и раннее закрытие (>10 мин). Раннее открытие и позднее закрытие не отслеживаются.
6. **Заказы** берём только `status = "paid"` с `total_money > 0`. Без заказов `orders_count = 0`, `avg_check = 0`.
7. **Кодировка API** нестабильна: UTF-8 → откат на cp1251 в функции `decode()`.
8. **В production PIN отключён.** Если что-то сломалось с входом — проверить, не вернулся ли `DASHBOARD_PIN` в env.

## Типичные ошибки и отладка

| Симптом | Где искать |
|---------|-----------|
| Карточка показывает «нет смены» | Диапазон `openDateStart`/`openDateEnd` в `get_changes` (окно 2 дня). Проверить `point_id`. |
| Время смены «сдвинуто» на 3 часа | API вдруг начал отдавать UTC. Восстановить `to_msk` со смещением, но по факту 2026-07-09 всё работает. |
| `/api/status` отдаёт 500 | `POINTS_JSON` пуст или битый. Проверить вывод `vercel env ls`. |
| Алерт-панель пустая | Отклонений за сегодня нет — это штатно, не баг. |
| Фронт показывает PIN-гейт | Бэк ответил 401 — `DASHBOARD_PIN` снова задан. Убрать env или ввести правильный код. |
| Заказы не приходят | У заказа пустой `change_id` или `status != "paid"`. |
| `RuntimeError: POINTS_JSON env не задан` | Env не проброшен в Vercel. Загрузить заново через `vercel env add … --sensitive`. |

## Связанные проекты и память

- `[[project_ma-coffee-dashboard]]` — статус прод-деплоя, URL, PIN-режим.
- `[[project_salary-calculation]]` — расчёт ЗП бариста, использует тот же `points.json` и FusionPOS API.
- Локальные исходники дашборда (рабочая копия для Vercel CLI): `myai/projects/ma-coffee/dashboard/`.
- Зеркало репозитория на диске: `myai/projects/ma-coffee/repo/`.

## Изменения

При доработках, которые затрагивают структуру, env vars, API, логику алертов, дизайн или PIN-инфраструктуру — обновить:

1. Исходники в `dashboard/` и/или `repo/src/`.
2. Соответствующий документ в `docs/` или `docs/schema/`.
3. Этот skill (если менялись команды, env vars или «что важно помнить»).
4. Память `project_ma-coffee-dashboard` (если менялись URL, PIN-статус или production-нюансы).