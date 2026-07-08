# Changelog

P3D Viewer — release notes. Все версии семантические (см. git теги).

## [v0.1.44] — 2026-07-09

### UI fixes

- **v0.1.44** — persist collapsed state папок модов в `app_settings` (между сессиями)
- **v0.1.43** — collapsible папки модов в "Загружено" (клик на `▶`/`▼` сворачивает)
- **v0.1.41–0.1.42** — алфавитная сортировка модов и файлов в обоих списках (включая insert на правильную позицию при enable)
- **v0.1.40** — фикс дублей кнопок "Загрузить"/"Blender" при toggle enable/disable (чистые `_enable_mod_clean` / `_enable_item_clean` методы)
- **v0.1.39** — фикс дублей секции "Загружено" + почистка orphan keys в `disabled_items` + авто-скрытие пустых модов
- **v0.1.38** — значок `⛔` вместо `⊘` (шире 36px); disabled моды/файлы убираются из "Доступные моды" и появляются в "Выключено"; collapsible "Выключено" через Treeview
- **v0.1.37** — добавлена колонка `⛔` для toggle disable модов и отдельных файлов
- **v0.1.34–0.1.36** — убрана обводка/фокус-ring вокруг Treeview (`takefocus=0` + layout override + style)
- **v0.1.32** — fix `KeyError: 'children'` в `_mods_collect_selected` (post-Treeview структура хранения)

### Tools

- **v0.1.33** — новая debug-категория `load` в DevTools → Категории логов (toggle для логов загрузки)
- **v0.1.31b** — `_dbg()` → `dbg()` fix (функция называется `dbg`, не `_dbg` — была тихая потеря логов); try/except + traceback в `_load_selected`

## [v0.1.15] — 2026-07-06

### UI реструктуризация

- **v0.1.15** — sash draggable + scroll sync для PanedWindow
- **v0.1.11** — **PanedWindow sash между модами и "Выключено"** (draggable мышкой)
- **v0.1.10** — compact mod list (убраны card border/padding)
- **v0.1.9** — auto folder picker при старте если `_unpacked` не найден
- **v0.1.8** — sticky header (version) + sticky footer (Load+Blender)
- **v0.1.5** — restore `rp_toggle_btn` + убран "Моды" из текста toggle
- **v0.1.4** — `icon_strip` перенесён в `_main_frame` (toggle не оставляет пустого места)
- **v0.1.0** — **3-column layout**: `sidebar | viewport | right_panel`

### Визуальный стиль

- **v0.0.8** — Teal Dark theme → electric blue (`#3b82f6`)
- **v0.0.7** — Default theme accent → electric blue

### Функциональность

- **v0.0.6** — search filter в секции "Выключено"
- **v0.0.5** — single-file subprocess + semaphore `max_concurrent=3` (parallel loading)

## Архитектура

### Layout (v0.1.44 финал)

```
panel_outer (sidebar)
├─ _header_frame (TOP, sticky) — version + author
├─ _side_paned (PanedWindow VERTICAL, draggable sash)
│   ├─ TOP: Treeview модов (collapsible parent rows)
│   └─ BOTTOM: Treeview "Выключено" (collapsible)
└─ _footer_frame (BOTTOM, sticky) — Load + Blender

_right_panel
├─ models search
└─ models list (Treeview-style: parent mod → collapsible children)
```

### Не сделано / запланировано

- **Модульный рефакторинг** — после явной команды
- **Settings виртуальный список** для кастомных тем
- **Proxy meshes** — фундаментальный блокер (см. `p3d-viewer-state.md`)