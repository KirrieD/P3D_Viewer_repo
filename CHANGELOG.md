# Changelog

P3D Viewer — release notes. Все версии семантические (см. git теги).

## [v0.1.15] — 2026-07-06

### UI реструктуризация (завершено)

Главный milestone сессии 2026-07-06 — переход от legacy v3.x layout к модульной sidebar.

- **v0.1.15** — sash drag правильно обновляет scrollregion (bind на `B1-Motion`/`ButtonRelease-1` для PanedWindow → `_refresh_canvas`)
- **v0.1.14** — canvas window height = `max(canvas_height, content_height)` (финальное исправление scroll — было сломано в v0.1.13)
- **v0.1.13** — попытка убрать `height=ch` constraint (сломал scroll, откатил в v0.1.14)
- **v0.1.12** — sash resize fix + `update_idletasks()` перед destroy в `_do_update_models_panel` (меньше моргания при "Загрузить")
- **v0.1.11** — **PanedWindow sash между модами и выключено** (draggable мышкой). Каждая секция имеет свой scroll
- **v0.1.10** — **compact mod list** (убрал card border/padding, мелкий шрифт — больше модов на экране)
- **v0.1.9** — **auto folder picker**: если `_unpacked` не найдена при старте → диалог выбора папки + автосохранение в `app_settings`
- **v0.1.8** — **sticky header** (версия v0.1.x) сверху + **sticky footer** (Load+Blender) снизу, не скроллятся
- **v0.1.5** — restore `rp_toggle_btn` + убрал "Моды" из текста toggle
- **v0.1.4** — `icon_strip` перенесён в `_main_frame` напрямую (toggle sidebar больше не оставляет пустого места)
- **v0.1.3** — toggle `◀`/`▶` скрывает только панель модов, иконки + нижний toolbar остаются
- **v0.1.2** — независимые toggle для icon_strip + right_panel, scroll fix
- **v0.1.1** — fix порядка инициализации right_panel (`_build_mods_panel` отложен до создания `_loaded_outer`)
- **v0.1.0** — **3-column layout**: `sidebar | viewport | right_panel` (Tactical Gear Edition style)

### Визуальный стиль (Tactical Gear Edition)

- **v0.0.8** — Teal Dark theme → electric blue (`#3b82f6`) + VERSION sync (`v3.2.0` → `v0.x.x`)
- **v0.0.7** — Default theme accent → electric blue (`#3b82f6`), `accent_hov` → darker blue (`#2563eb`), `accent_dim` → `#1e3a8a`

### Функциональность

- **v0.0.6** — search filter в секции "Выключено"
- **v0.0.9** — mod card layout (позже упрощён в v0.1.10)

## [v0.0.5] — ранее

- Single-file subprocess + semaphore `max_concurrent=3` (parallel loading, без DLL deadlock)
- Vectorized batch `is_good` для материалов
- `.gitignore` (исключает `_internal/`, cache, build artifacts)
- EXE + bundled parsers (cpp_parser / p3d_proxy_parser / proxy_index) для v3.2.0 baseline

## Архитектура

### Layout (v0.1.15 финал)

```
panel_outer (sidebar content)
├─ _header_frame (TOP) — version + author "d.x.b" (sticky, не скроллится)
├─ _side_paned (PanedWindow VERTICAL, draggable sash)
│   ├─ TOP: _side_canvas (scroll) — моды
│   └─ BOTTOM: _disabled_frame — "Выключено" с своим scroll
└─ _footer_frame (BOTTOM) — Load+Blender (sticky, не скроллится)
```

### Main window

```
_main_frame
├─ toolbar (◀ toggle Моды ... ▶ toggle Загружено)
├─ icon_strip (44px, всегда видна)
├─ side_outer (моды, скрывается по toggle ◀)
├─ vp_frame (3D viewport)
└─ right_panel (Загружено, скрывается по toggle ▶)
```

### Известные gotcha (learned the hard way)

- **PanedWindow + Canvas scroll**: `<Configure>` на canvas child НЕ триггерится автоматически при sash drag → нужен явный bind на `paned.bind('<B1-Motion>', ...)` + `paned.bind('<ButtonRelease-1>', ...)` через `self.root.after(20, ...)`
- **Canvas window height**: `height=ch` обрезает scroll (бесполезно); без height scroll items не видны; правильно `height = max(canvas_height, content_height_from_bbox)`
- **Mousewheel scroll**: нужен Enter/Leave tracking иначе scroll теряется когда курсор уходит с widget

## Не сделано / запланировано

- **Virtual list** (ttk.Treeview) — для 100+ модов
- **Visual cleanup** — убрать наследие v3.x Checkbutton'ов (Кирилл: "колхоз")
- **Settings панель** — следующий слой по приоритету
- **Модульный рефакторинг** (`render.py`, `parser.py`, `materials.py`, `ui.py`) — после явной команды
- **Proxy meshes** — фундаментальный блокер, не чинить без команды (см. `p3d-viewer-state.md`)