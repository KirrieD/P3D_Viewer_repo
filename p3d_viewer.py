# -*- coding: utf-8 -*-
import os, sys, struct, ctypes, re, io, subprocess, tempfile, json, queue, threading, datetime, hashlib, time
import glob as _glob
import winreg as _winreg
from concurrent.futures import ThreadPoolExecutor, as_completed
import tkinter as tk
from tkinter import filedialog, messagebox, colorchooser, ttk, scrolledtext
import numpy as np
from PIL import Image

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    USE_DND = True
except ImportError:
    USE_DND = False

try:
    from pyopengltk import OpenGLFrame
    from OpenGL.GL import *
    from OpenGL.GLU import *
    USE_GL = True
except ImportError:
    USE_GL = False

# v2.3+: внедрённые конфиги — хранятся ВНУТРИ EXE (упаковываются PyInstaller --add-data)
try:
    from embedded_configs import EMBEDDED_CONFIGS
except Exception:
    EMBEDDED_CONFIGS = {}

VERSION    = 'v0.1.33'
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MIKERO     = r'C:\Program Files (x86)\Mikero\DePboTools\bin'
DERAP_EXE  = os.path.join(MIKERO, 'DeRap.exe')

_EXE_DIR  = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else SCRIPT_DIR
LOG_DIR   = os.path.join(_EXE_DIR, 'log')       # лог анализа DLL
DBG_DIR   = os.path.join(_EXE_DIR, 'debuglog')  # полный трейс работы программы
PROXIES_DIR = os.path.join(_EXE_DIR, 'proxies')

# ── Флаги логирования (ON по умолчанию для детальной диагностики) ─────────
_log_enabled = True   # запись namedsel_*.txt в log/
_dbg_enabled = True   # запись debug_*.txt в debuglog/ + вывод в монитор

# Категории диагностики — каждая вкл/выкл отдельно через UI
# (по умолчанию ВСЕ включены — детальные логи помогают ловить баги)
_DBG_CATS = {
    'general':     True,   # общие сообщения (init, settings, итд)
    'parse':       True,   # P3D парсинг (face/vertex/normal/UV/material)
    'uv':          True,   # UV диагностика (диапазоны, unique, mirror)
    'recompute':   True,   # пересчёт split/materials (uv/edge sliders)
    'apply':       True,   # apply chain (mesh upload, race conditions)
    'subproc':     True,   # subprocess parse_p3d
    'render':      True,   # OpenGL render (shader, uniforms, draw)
    'split':       True,   # auto-split logic (uv_split_detect, kmeans, area)
    'errors':      True,   # errors popup (load failures)
}

# ── Глобальная очередь для Debug Monitor ──────────────────────────────────
_dbg_queue = queue.Queue()
_dbg_file  = None      # открытый файл текущей сессии

# ── Настройки приложения ────────────────────────────────────────────────────
_SETTINGS_PATH = os.path.join(_EXE_DIR, 'app_settings.json')
_SETTINGS_DEFAULTS = {
    'log_enabled':    True,   # лог анализа (namedsel_*.txt) — ON для детальной диагностики
    'dbg_enabled':    True,   # master-флаг debug monitor — ON для детальной диагностики
    'dbg_cats':       dict(_DBG_CATS),  # per-category toggles
    'lighting':       True,
    'alpha':          False,  # DEPRECATED: заменён на per-material alpha_mats
    'alpha_mats':     {},     # v3.1.7: {mat_path: True} — какие материалы прозрачные (per-material в UI)
    'brightness':     1.0,
    'geometry':       '1600x900',
    'sidebar_width':  450,
    'section_models':   True,
    'section_browser':  True,
    'section_mats':     True,
    'section_textures': True,
    'section_display':  True,
    'section_blender':  False,
    'section_mods':     True,
    'blender_path':    '',
    'theme_name':     'Teal Dark',
    'section_order':  ['mods', 'mats', 'textures', 'view', 'file', 'convert'],
    'disabled_mods':  [],
    'disabled_items': [],  # ["MOD/file.p3d"] — отдельные файлы
    'mods_list_height':   110,  # v3.1: фикс. высота "Доступные моды"
    'loaded_list_height': 160,  # v3.1: фикс. высота "Загружено"
    'showed_errors':      [],   # v3.1.9: list из "path|error" уже показанных ошибок — popup не повторяется
    'dev_mode':           False,# v3.1.9: режим разработчика — показывает дебаг монитор + Dev Tools
    'custom_labels':      {},   # v3.1.9: dict {key: new_text} — переопределения текстов UI
    'mirror_uv':     False,  # v3.0: V-flip UV (DirectX→OpenGL convention), default OFF — "оригинальный" mirrored стиль
    'normalize_uv':  False,  # v3.0: deprecated
    'per_face_flip': True,   # v3.0: deprecated
    'flip_x':        False,  # v3.0: mesh mirror по оси X
    'flip_y':        False,  # v3.0: mesh mirror по оси Y
    'flip_z':        False,  # v3.0: mesh mirror по оси Z
    'proxies_enabled': True,
    'gradient_enabled':  False,
    'gradient_color2':   '#080c0a',
    'font_fam':          'Segoe UI',
    'custom_colors':     {},
    'section_order_names': [],
    # Стили текста: bold/italic/underline для каждого уровня заголовков
    'text_styles': {
        'h1':   {'bold': True,  'italic': False, 'underline': False},
        'h2':   {'bold': True,  'italic': False, 'underline': False},
        'h3':   {'bold': False, 'italic': False, 'underline': False},
        'body': {'bold': False, 'italic': False, 'underline': False},
    },
}

def _load_app_settings():
    s = dict(_SETTINGS_DEFAULTS)
    try:
        if os.path.exists(_SETTINGS_PATH):
            with open(_SETTINGS_PATH, 'r', encoding='utf-8') as f:
                s.update(json.load(f))
    except Exception:
        pass
    # Миграция per-category debug: подтянуть дефолты для отсутствующих категорий
    saved_cats = s.get('dbg_cats') or {}
    if not isinstance(saved_cats, dict):
        saved_cats = {}
    merged = dict(_DBG_CATS)
    merged.update(saved_cats)
    s['dbg_cats'] = merged
    # v2.4 миграция: если старое sidebar_width < 320, обновить до 450
    if s.get('sidebar_width', 450) < 320:
        s['sidebar_width'] = 450
    # v2.8 миграция: models_list_height 350→500
    if s.get('models_list_height', 500) < 400:
        s['models_list_height'] = 500
    # v3.0 миграция: mirror_uv default = True (DirectX→OpenGL fix)
    if 'mirror_uv' not in s:
        s['mirror_uv'] = False
    # v3.0 миграция: normalize_uv (wrap UV в [0,1])
    if 'normalize_uv' not in s:
        s['normalize_uv'] = False
    # v3.0 миграция: per_face_flip (default True — большинство моделей требуют)
    if 'per_face_flip' not in s:
        s['per_face_flip'] = True
    # v3.0 миграция: flip_x/y/z (mesh mirror, default False)
    for axis in ('x', 'y', 'z'):
        if f'flip_{axis}' not in s:
            s[f'flip_{axis}'] = False
    # v3.0 миграция: dbg_cats добавлен, новые категории получают дефолт ON
    # v3.1 миграция: disabled_items (отдельные файлы, не только целые моды)
    if 'disabled_items' not in s:
        s['disabled_items'] = []
    elif not isinstance(s['disabled_items'], list):
        s['disabled_items'] = []
    # v3.1 миграция: фикс. высоты списков (доступные моды / загружено)
    if 'mods_list_height' not in s or not isinstance(s['mods_list_height'], int) or s['mods_list_height'] < 60:
        s['mods_list_height'] = 110
    if 'loaded_list_height' not in s or not isinstance(s['loaded_list_height'], int) or s['loaded_list_height'] < 80:
        s['loaded_list_height'] = 160
    # v3.1.3: если сохранёнка была большой — сбрасываем до компактных дефолтов
    if s['mods_list_height'] >= 130:
        s['mods_list_height'] = 110
    if s['loaded_list_height'] >= 180:
        s['loaded_list_height'] = 160
    return s

def _save_app_settings(s):
    try:
        with open(_SETTINGS_PATH, 'w', encoding='utf-8') as f:
            json.dump(s, f, ensure_ascii=False, indent=4)
    except Exception:
        pass


def _dbg_open_session():
    """Открывает файл лога текущей сессии (только если dbg включён)."""
    global _dbg_file
    if not _dbg_enabled:
        return
    try:
        os.makedirs(DBG_DIR, exist_ok=True)
        ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        path = os.path.join(DBG_DIR, f'debug_{ts}.txt')
        _dbg_file = open(path, 'w', encoding='utf-8', buffering=1)
        _dbg_file.write(f'=== P3D Viewer {VERSION} session started ===\n')
    except Exception:
        pass


def dbg(msg: str):
    """Отправляет сообщение в Debug Monitor / файл (только если dbg включён)."""
    if not _dbg_enabled:
        return
    ts = datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]
    line = f'[{ts}] {msg}'
    try:
        if _dbg_file:
            _dbg_file.write(line + '\n')
    except Exception:
        pass
    _dbg_queue.put(line)

# псевдоним для внутреннего использования
_dbg = dbg


def dbg_cat(category: str, msg: str):
    """v3.1.14: Per-category debug.
    Master switch (Debug Monitor, sidebar): если _dbg_enabled=False — ВСЕ категории no-op.
    Per-category toggle (DevTools): можно ВКЛ/ВЫКЛ каждую категорию отдельно.
    Категории: general, parse, uv, recompute, apply, subproc, render, split, errors.
    """
    if not _dbg_enabled:
        return
    if not _DBG_CATS.get(category, True):
        return
    dbg(f'[{category:7s}] {msg}')

def _write_log(name, lines):
    """Пишет файл в папку log/ (только если лог включён)."""
    if not _log_enabled:
        return
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        path = os.path.join(LOG_DIR, name)
        with open(path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
    except Exception:
        pass


# ── Конфигурации предметов ─────────────────────────────────────────────────

def _get_config_dir():
    """Папка item_configs/ рядом с EXE (или скриптом)."""
    if getattr(sys, 'frozen', False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    d = os.path.join(base, 'item_configs')
    os.makedirs(d, exist_ok=True)
    return d


def _cfg_path(model_name):
    """Путь к JSON-конфигу предмета."""
    safe = re.sub(r'[<>:"/\\|?*]', '_', model_name)
    return os.path.join(_get_config_dir(), safe + '.json')


_DOS_MOD_SETTINGS_PATH = r'D:\d.x.b\DoS\- Frosty\dos_mod_editor_v2\dos_mod_editor_settings.json'

def _get_pbo_dir():
    """Читает pbo_dir из настроек dos_mod_editor_v2."""
    try:
        with open(_DOS_MOD_SETTINGS_PATH, 'r', encoding='utf-8') as f:
            return json.load(f).get('pbo_dir', '')
    except Exception:
        return ''


def _find_blender_exe():
    """Ищет blender.exe через Steam registry и libraryfolders.vdf."""
    try:
        key = _winreg.OpenKey(_winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam')
        steam_path, _ = _winreg.QueryValueEx(key, 'SteamPath')
        _winreg.CloseKey(key)
    except Exception:
        steam_path = None

    search_roots = []
    if steam_path:
        search_roots.append(steam_path)
        vdf = os.path.join(steam_path, 'steamapps', 'libraryfolders.vdf')
        if os.path.exists(vdf):
            try:
                with open(vdf, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if '"path"' in line.lower():
                            parts = line.split('"')
                            if len(parts) >= 4:
                                search_roots.append(parts[3].replace('\\\\', '\\'))
            except Exception:
                pass

    for root in search_roots:
        candidate = os.path.join(root, 'steamapps', 'common', 'Blender', 'blender.exe')
        if os.path.exists(candidate):
            return candidate

    for candidate in [
        r'C:\Program Files\Blender Foundation\Blender 4.3\blender.exe',
        r'C:\Program Files\Blender Foundation\Blender 4.2\blender.exe',
        r'C:\Program Files\Blender Foundation\Blender 4.1\blender.exe',
        r'C:\Program Files\Blender Foundation\Blender\blender.exe',
    ]:
        if os.path.exists(candidate):
            return candidate
    return None


def _find_unpacked_dir():
    """Ищет папку _unpacked в нескольких возможных TEMP папках."""
    temp_candidates = []
    for var in ('TEMP', 'TMP'):
        v = os.environ.get(var, '')
        if v:
            temp_candidates.append(v)
    # Дополнительные пути
    localappdata = os.environ.get('LOCALAPPDATA', '')
    if localappdata:
        temp_candidates.append(os.path.join(localappdata, 'Temp'))
    userprofile = os.environ.get('USERPROFILE', '')
    if userprofile:
        temp_candidates.append(os.path.join(userprofile, 'AppData', 'Local', 'Temp'))
    # Убираем дубликаты
    seen = set()
    unique = []
    for t in temp_candidates:
        key = t.lower()
        if key not in seen:
            seen.add(key)
            unique.append(t)
    # Ищем
    for temp in unique:
        try:
            candidates = _glob.glob(os.path.join(temp, 'dos_mod_editor_*', '_unpacked'))
            candidates = [c for c in candidates if os.path.isdir(c)]
            if candidates:
                return sorted(candidates, key=os.path.getmtime, reverse=True)[0]
        except Exception:
            pass
    return None


def _get_mod_p3d_files(unpacked_dir, mod_name):
    """Returns all .p3d file paths under mod folder recursively, sorted by path."""
    results = []
    mod_path = os.path.join(unpacked_dir, mod_name)
    if not os.path.isdir(mod_path):
        return results
    for root, dirs, files in os.walk(mod_path):
        dirs.sort(key=str.lower)
        for fname in sorted(files, key=str.lower):
            if fname.lower().endswith('.p3d'):
                results.append(os.path.join(root, fname))
    return results


def _get_all_mods(unpacked_dir):
    """Возвращает список имён модов (подпапок) из _unpacked."""
    try:
        return sorted(
            [e.name for e in os.scandir(unpacked_dir) if e.is_dir()],
            key=str.lower
        )
    except Exception:
        return []


def _proxy_basename_normalize(raw_name):
    """Normalize proxy path: 'proxy:\\dz\\gear\\radio\\walkietalkie.001' → 'walkietalkie.p3d'"""
    import re as _re
    s = raw_name.replace('\\', '/').lower()
    if s.startswith('proxy:'):
        s = s[6:]
    s = s.lstrip('/')
    s = s.rsplit('/', 1)[-1]
    s = _re.sub(r'\.\d+$', '', s)
    _i = s.rfind('.p3d')
    if _i >= 0:
        s = s[:_i + 4]
    elif s:
        s += '.p3d'
    return s


def _extract_cpp_block(text, start):
    """Return balanced {...} block from text[start] (must be '{')."""
    if start >= len(text) or text[start] != '{':
        return ''
    depth = 0
    for i in range(start, len(text)):
        c = text[i]
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return text[start:]


def _find_config_cpp(p3d_path):
    """Walk up directory tree from p3d_path to find config.cpp."""
    folder = os.path.dirname(os.path.abspath(p3d_path))
    for _ in range(8):
        cfg = os.path.join(folder, 'config.cpp')
        if os.path.isfile(cfg):
            return cfg
        parent = os.path.dirname(folder)
        if parent == folder:
            break
        folder = parent
    return None


def _parse_proxy_config(cpp_path, p3d_path):
    """
    Parse config.cpp to find attachment slots and proxy model paths for a p3d.
    Returns {'attachments': [...], 'slot_to_model': {slot: model_path, ...}}
    """
    import re as _re
    result = {'attachments': [], 'slot_to_model': {}}
    try:
        with open(cpp_path, 'r', encoding='utf-8', errors='replace') as f:
            text = f.read()
    except Exception:
        return result
    text = _re.sub(r'//[^\n]*', '', text)
    text = _re.sub(r'/\*.*?\*/', '', text, flags=_re.DOTALL)

    p3d_bn = os.path.splitext(os.path.basename(p3d_path))[0]

    for ref_m in _re.finditer(_re.escape(p3d_bn), text, _re.IGNORECASE):
        ref_pos = ref_m.start()
        stack = []
        i = ref_pos - 1
        while i >= 0:
            c = text[i]
            if c == '}':
                stack.append(i)
            elif c == '{':
                if stack:
                    stack.pop()
                else:
                    block = _extract_cpp_block(text, i)
                    att_m = _re.search(r'attachments\s*\[\s*\]\s*=\s*\{([^}]*)\}', block, _re.DOTALL)
                    if att_m:
                        for it in _re.findall(r'"([^"]+)"', att_m.group(1)):
                            if it not in result['attachments']:
                                result['attachments'].append(it)
                        break
            i -= 1

    cfg_m = _re.search(r'class\s+CfgNonAIVehicles\s*\{', text, _re.IGNORECASE)
    if cfg_m:
        brace_pos = text.find('{', cfg_m.start())
        if brace_pos >= 0:
            cfg_block = _extract_cpp_block(text, brace_pos)
            for pm in _re.finditer(r'class\s+\w+\s*:\s*ProxyAttachment\s*\{', cfg_block, _re.IGNORECASE):
                brace = cfg_block.find('{', pm.start())
                body  = _extract_cpp_block(cfg_block, brace)
                model_m = _re.search(r'\bmodel\s*=\s*"([^"]+)"', body)
                slot_m  = _re.search(r'inventorySlot\s*\[\s*\]\s*=\s*\{([^}]+)\}', body, _re.DOTALL)
                if model_m and slot_m:
                    model_path = model_m.group(1)
                    for slot in _re.findall(r'"([^"]+)"', slot_m.group(1)):
                        if slot not in result['slot_to_model']:
                            result['slot_to_model'][slot] = model_path
    return result


def _proxy_json_path(p3d_path):
    """Returns path to proxy JSON sidecar: 'Model.p3d' → 'Model.p3d.proxy.json'"""
    return p3d_path + '.proxy.json'


def _save_proxy_json(p3d_path, proxies):
    """Save proxy positions/settings to JSON sidecar next to the P3D file."""
    if not p3d_path:
        return
    import json as _json
    out = []
    for prx in proxies:
        entry = {}
        if prx.get('slot_name'):
            entry['slot_name'] = prx['slot_name']
        if prx.get('name'):
            entry['name'] = prx['name']
        if prx.get('pos') is not None:
            entry['pos'] = list(prx['pos'])
        if prx.get('mat') is not None:
            entry['mat'] = list(prx['mat'])
        if prx.get('model_bn'):
            entry['model_bn'] = prx['model_bn']
        if prx.get('hidden'):
            entry['hidden'] = True
        if prx.get('locked'):
            entry['locked'] = True
        out.append(entry)
    try:
        jpath = _proxy_json_path(p3d_path)
        with open(jpath, 'w', encoding='utf-8') as f:
            _json.dump({'version': 1, 'proxies': out}, f, indent=2, ensure_ascii=False)
    except Exception as e:
        _dbg(f'proxy json save error: {e}')


def _load_proxy_json(p3d_path):
    """Load proxy JSON sidecar. Returns list of dicts, or None if not found."""
    import json as _json
    jpath = _proxy_json_path(p3d_path)
    if not os.path.isfile(jpath):
        return None
    try:
        with open(jpath, 'r', encoding='utf-8') as f:
            data = _json.load(f)
        return data.get('proxies', [])
    except Exception as e:
        _dbg(f'proxy json load error: {e}')
        return None


def _merge_proxy_json(proxies, saved_list):
    """Overlay saved positions/settings onto proxies list, matching by slot_name then name."""
    if not saved_list:
        return
    by_slot = {s['slot_name']: s for s in saved_list if s.get('slot_name')}
    by_name = {s['name']: s for s in saved_list if s.get('name')}
    for prx in proxies:
        saved = by_slot.get(prx.get('slot_name', '')) or by_name.get(prx.get('name', ''))
        if not saved:
            continue
        if 'pos' in saved:
            prx['pos'] = list(saved['pos'])
        if 'mat' in saved:
            prx['mat'] = list(saved['mat'])
        if 'model_bn' in saved:
            prx['model_bn'] = saved['model_bn']
        if saved.get('hidden'):
            prx['hidden'] = True
        if saved.get('locked'):
            prx['locked'] = True


def _embed_proxies_to_p3d(p3d_path, proxies):
    """
    Patch proxy transform data in-place in the P3D binary.
    Creates .bak backup on first call. Returns (ok, message).
    """
    import shutil as _sh
    try:
        backup = p3d_path + '.bak'
        if not os.path.exists(backup):
            _sh.copy2(p3d_path, backup)
        with open(p3d_path, 'rb') as f:
            data = bytearray(f.read())
        patched = 0
        skipped = []
        for i, prx in enumerate(proxies):
            if prx.get('_synthetic'):
                continue
            # Write to ALL LOD occurrences so every LOD gets updated
            _offsets = prx.get('_file_offsets_all') or (
                [prx['_file_offset']] if prx.get('_file_offset') else []
            )
            if not _offsets:
                skipped.append(prx.get('slot_name') or str(i))
                continue
            mat = prx.get('mat', [1, 0, 0, 0, 1, 0, 0, 0, 1])
            pos = prx.get('pos', [0.0, 0.0, 0.0])
            floats = list(mat) + list(pos)
            _wrote = False
            for _off in _offsets:
                if _off + 48 > len(data):
                    continue
                struct.pack_into('<12f', data, _off, *floats)
                _wrote = True
            if _wrote:
                patched += 1
            else:
                skipped.append(prx.get('slot_name') or str(i))
        with open(p3d_path, 'wb') as f:
            f.write(data)
        msg = f'Встроено {patched} proxy.'
        if skipped:
            msg += f' Без офсета (пропущено): {", ".join(skipped)}'
        return True, msg
    except Exception as e:
        return False, f'Ошибка записи P3D: {e}'


def _find_proxy_file(proxy_name, unpacked_dir):
    """
    Given proxy name like 'proxy:\\DZ\\gear\\radio\\walkietalkie.001'
    or 'proxy:/DZ/characters/proxies/walkietalkie.p3d',
    search for the file in unpacked_dir recursively.
    Returns full path or None.
    """
    if not unpacked_dir or not os.path.isdir(unpacked_dir):
        return None
    # Strip leading 'proxy:' and normalize separators
    rel = proxy_name
    if rel.lower().startswith('proxy:'):
        rel = rel[6:]
    rel = rel.lstrip('/\\').replace('/', os.sep).replace('\\', os.sep)

    # Strip numeric instance suffix: walkietalkie.001 → walkietalkie
    # (must be done BEFORE .p3d check since proxy names often have NO .p3d before the suffix)
    import re as _re_prx
    rel = _re_prx.sub(r'\.\d+$', '', rel)

    # Now handle .p3d extension
    _p3d_i = rel.lower().rfind('.p3d')
    if _p3d_i >= 0:
        rel_p3d = rel[:_p3d_i + 4]
    else:
        rel_p3d = rel + '.p3d'

    def _try_candidates(base_rel):
        # 1) Direct path: {unpacked_dir}/{base_rel}  (DayZ base game content at root)
        candidate = os.path.join(unpacked_dir, base_rel)
        if os.path.isfile(candidate):
            return candidate
        # 2) Through one top-level folder: {unpacked_dir}/{mod}/{base_rel}
        try:
            tops = os.listdir(unpacked_dir)
        except OSError:
            tops = []
        for top in tops:
            candidate = os.path.join(unpacked_dir, top, base_rel)
            if os.path.isfile(candidate):
                return candidate
        return None

    found = _try_candidates(rel_p3d)
    if found:
        return found
    # Fallback: search by p3d filename only (any subfolder)
    target_name = os.path.basename(rel_p3d).lower()
    for root, dirs, files in os.walk(unpacked_dir):
        for f in files:
            if f.lower() == target_name:
                return os.path.join(root, f)
    return None


def _copy_proxy_to_local(src_path, proxies_dir):
    """Copy proxy .p3d to local proxies/ folder. Returns dest path."""
    import shutil
    os.makedirs(proxies_dir, exist_ok=True)
    dest = os.path.join(proxies_dir, os.path.basename(src_path))
    if not os.path.exists(dest):
        try:
            shutil.copy2(src_path, dest)
        except Exception:
            pass
    return dest


def _is_blender_running():
    """Проверяет запущен ли blender.exe через tasklist."""
    try:
        out = subprocess.check_output(
            ['tasklist', '/FI', 'IMAGENAME eq blender.exe', '/NH'],
            creationflags=subprocess.CREATE_NO_WINDOW
        ).decode('utf-8', 'replace')
        return 'blender.exe' in out.lower()
    except Exception:
        return False


_BLENDER_BRIDGE_SCRIPT = r'''
import bpy, sys, os, threading, time, json

QUEUE_FILE = os.path.join(os.environ.get('TEMP', ''), 'p3d_blender_queue.txt')
_imported = set()

def _dayz_to_blender_pos(pos):
    # DayZ Y-up → Blender Z-up: (X, Y, Z) → (X, -Z, Y)
    return (pos[0], -pos[2], pos[1])

def _dayz_to_blender_rot(mat9):
    # mat9 = (Xx,Xy,Xz, Yx,Yy,Yz, Zx,Zy,Zz) column-major X,Y,Z basis
    # Convert to Blender 3x3 rotation matrix (row-major for mathutils.Matrix)
    import mathutils
    # DayZ columns → Blender rows (Y-up → Z-up conversion applied)
    # Blender X = DayZ X, Blender Y = -DayZ Z, Blender Z = DayZ Y
    # Column mapping: B_right=DayZ_X, B_fwd=-DayZ_Z, B_up=DayZ_Y
    Xx,Xy,Xz = mat9[0], mat9[1], mat9[2]  # DayZ X-col (right)
    Yx,Yy,Yz = mat9[3], mat9[4], mat9[5]  # DayZ Y-col (up)
    Zx,Zy,Zz = mat9[6], mat9[7], mat9[8]  # DayZ Z-col (forward)
    # Blender rows: [right_x, right_y, right_z], [fwd_x, fwd_y, fwd_z], [up_x, up_y, up_z]
    blender_mat = mathutils.Matrix([
        [Xx, -Xz,  Xy],
        [-Zx, Zz, -Zy],
        [Yx, -Yz,  Yy],
    ])
    return blender_mat.to_euler()

def import_proxies(proxy_json):
    if not os.path.exists(proxy_json): return
    try:
        import mathutils
        proxies = json.load(open(proxy_json, encoding='utf-8'))
        for prx in proxies:
            raw_name = prx.get('name', 'proxy')
            short_name = raw_name.split('\\')[-1].replace('.p3d', '')
            short_name = short_name.split('/')[-1]
            # Strip trailing .001, .002 etc
            base_name = short_name.rsplit('.', 1)[0] if '.' in short_name else short_name
            disp_name = f'PROXY_{base_name}_{prx.get("idx", 0):03d}'
            pos = _dayz_to_blender_pos(prx['pos'])
            mat9 = prx['mat']
            empty = bpy.data.objects.new(disp_name, None)
            bpy.context.collection.objects.link(empty)
            empty.location = pos
            try:
                empty.rotation_euler = _dayz_to_blender_rot(mat9)
            except Exception:
                pass
            empty.empty_display_type = 'ARROWS'
            empty.empty_display_size = 0.06
    except Exception as e:
        print(f'[P3D Bridge] proxy import error: {e}')

def import_obj(path):
    if not os.path.exists(path): return
    bpy.ops.wm.obj_import(filepath=path)
    # Try to import companion proxy JSON
    base = os.path.splitext(path)[0]
    proxy_json = base + '_proxies.json'
    import_proxies(proxy_json)

def _watch_queue():
    while True:
        time.sleep(1)
        try:
            if not os.path.exists(QUEUE_FILE): continue
            with open(QUEUE_FILE, 'r', encoding='utf-8') as f:
                paths = [l.strip() for l in f if l.strip()]
            new_paths = [p for p in paths if p not in _imported]
            if new_paths:
                for p in new_paths:
                    _imported.add(p)
                    import_obj(p)
        except Exception:
            pass

argv = sys.argv
if '--' in argv:
    objs = argv[argv.index('--') + 1:]
    for obj in objs:
        _imported.add(obj)
        import_obj(obj)

t = threading.Thread(target=_watch_queue, daemon=True)
t.start()
'''


def _load_item_config(model_name):
    """Загружает конфиг предмета. Возвращает dict или None.

    Приоритет:
      1. embedded_configs.EMBEDDED_CONFIGS[model_name] — внутри EXE (KirrieD внедрил)
      2. APPDATA/.p3dviewer_embedded/<model>.json — fallback для frozen mode
      3. item_configs/<model_name>.json — рядом с EXE
    """
    # 1) Embedded (внутри EXE) — главный приоритет
    emb = _load_embedded_for_model(model_name)
    if emb is not None:
        return emb
    # 2) Item configs (рядом с EXE)
    p = _cfg_path(model_name)
    if not os.path.exists(p):
        return None
    try:
        with open(p, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def _save_item_config(model_name, split1, split2, swap, uv_threshold=1.5, edge_threshold=50, note='', splits=None):
    """Сохраняет конфиг предмета в JSON с читаемым форматом.

    splits — v2.3: массив границ для N материалов. Если задан, перекрывает split1/split2.
    """
    p = _cfg_path(model_name)
    cfg = {
        'model':           model_name,
        'split1':          int(split1),
        'split2':          int(split2),
        'swap':            bool(swap),
        'uv_threshold':    float(round(uv_threshold, 3)),
        'edge_threshold':  int(edge_threshold),
        'note':            str(note),
    }
    if splits is not None:
        cfg['splits'] = [int(s) for s in splits]
    with open(p, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=4)
    return p


# ── v2.3: Внедрение конфигов в EXE ─────────────────────────────────────────

def _embedded_cfg_path():
    """Путь к embedded_configs.py рядом с запущенным скриптом/EXE."""
    if getattr(sys, 'frozen', False):
        # PyInstaller: _MEIPASS содержит bundled данные
        base = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, 'embedded_configs.py')


def _embed_config(model_name, cfg):
    """Внедряет конфиг в embedded_configs.py + обновляет runtime dict.

    Возвращает (success, message).
    Работает в обоих режимах:
      - source (.py): перезаписывает файл на диске
      - frozen (EXE): только runtime dict (для переноса в EXE нужно пересобрать)
    """
    global EMBEDDED_CONFIGS
    model_key = model_name.lower()
    EMBEDDED_CONFIGS[model_key] = dict(cfg)

    if getattr(sys, 'frozen', False):
        # В EXE файлы внутри _MEIPASS защищены от записи (WinError 5).
        # Сохраняем в APPDATA как fallback + runtime dict.
        appdata_dir = os.path.join(os.path.expanduser('~'), '.p3dviewer_embedded')
        try:
            os.makedirs(appdata_dir, exist_ok=True)
            p = os.path.join(appdata_dir, f'{model_key}.json')
            with open(p, 'w', encoding='utf-8') as f:
                json.dump(cfg, f, ensure_ascii=False, indent=4)
            return True, f'Внедрено в runtime + сохранено в {p} (для EXE пересоберите — будет вшито)'
        except Exception as e:
            return True, f'Внедрено в runtime (EXE режим, файлы read-only: {e})'

    # Source mode: перезаписываем embedded_configs.py
    cfg_path = _embedded_cfg_path()
    try:
        # Парсим текущий файл, обновляем dict, пишем обратно
        existing = {}
        if os.path.exists(cfg_path):
            try:
                with open(cfg_path, 'r', encoding='utf-8') as f:
                    code = f.read()
                # Простой парсер: ищем EMBEDDED_CONFIGS = {...}
                import ast
                tree = ast.parse(code)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Assign):
                        for target in node.targets:
                            if isinstance(target, ast.Name) and target.id == 'EMBEDDED_CONFIGS':
                                if isinstance(node.value, ast.Dict):
                                    existing = ast.literal_eval(node.value)
            except Exception:
                existing = {}

        existing[model_key] = dict(cfg)

        # Генерируем новый файл (Python literal — repr(), не JSON, чтобы был валидный Python)
        lines = [
            '# -*- coding: utf-8 -*-',
            '"""',
            'Embedded configs для P3D Viewer v2.3+',
            'Хранятся ВНУТРИ программы (упаковываются в EXE через PyInstaller --add-data).',
            '',
            'Формат: {model_name_lower: config_dict}',
            '',
            'KirrieD может:',
            '  1) В UI нажать "Внедрить конфиг" → программа допишет запись сюда',
            '  2) Вручную отредактировать файл → пересобрать EXE',
            '  3) Через меню "Внедрённые конфиги" → посмотреть/удалить записи',
            '"""',
            '',
            'EMBEDDED_CONFIGS = {',
        ]
        for k, v_dict in existing.items():
            # Сериализуем dict через json.loads(repr(...)) для unicode-безопасности
            # затем json.dumps с ensure_ascii=True → ASCII-safe string, потом заменяем JSON-кавычки на Python
            j = json.dumps(v_dict, ensure_ascii=True, indent=4, sort_keys=True)
            # json: false/true/null → Python: False/True/None
            py_literal = j.replace(': false', ': False').replace(': true', ': True').replace(': null', ': None')
            indented = '\n'.join('    ' + ln for ln in py_literal.split('\n'))
            lines.append(f'    {repr(k)}: {indented},')
        lines.append('}')
        lines.append('')

        with open(cfg_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))

        return True, f'Внедрено в {cfg_path} (пересоберите EXE для включения)'
    except Exception as e:
        return False, f'Ошибка записи: {e}'


def _unembed_config(model_name):
    """Удаляет конфиг из embedded (runtime + файл/APPDATA)."""
    global EMBEDDED_CONFIGS
    model_key = model_name.lower()
    if model_key not in EMBEDDED_CONFIGS:
        return False, 'Конфиг не внедрён'

    del EMBEDDED_CONFIGS[model_key]

    # Удаляем из APPDATA fallback (если frozen)
    if getattr(sys, 'frozen', False):
        appdata_p = os.path.join(os.path.expanduser('~'), '.p3dviewer_embedded', f'{model_key}.json')
        if os.path.exists(appdata_p):
            try: mavis-trash(appdata_p)
            except Exception: pass

    # Удаляем из source файла
    cfg_path = _embedded_cfg_path()
    if not getattr(sys, 'frozen', False) and os.path.exists(cfg_path):
        try:
            existing = {}
            with open(cfg_path, 'r', encoding='utf-8') as f:
                code = f.read()
            import ast
            tree = ast.parse(code)
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id == 'EMBEDDED_CONFIGS':
                            if isinstance(node.value, ast.Dict):
                                existing = ast.literal_eval(node.value)
            if model_key in existing:
                del existing[model_key]
                lines = [
                    '# -*- coding: utf-8 -*-',
                    '"""Embedded configs для P3D Viewer v2.3+ — управляются через UI."""',
                    '',
                    'EMBEDDED_CONFIGS = {',
                ]
                for k, v_dict in existing.items():
                    j = json.dumps(v_dict, ensure_ascii=True, indent=4, sort_keys=True)
                    py_literal = j.replace(': false', ': False').replace(': true', ': True').replace(': null', ': None')
                    indented = '\n'.join('    ' + ln for ln in py_literal.split('\n'))
                    lines.append(f'    {repr(k)}: {indented},')
                lines.append('}')
                lines.append('')
                with open(cfg_path, 'w', encoding='utf-8') as f:
                    f.write('\n'.join(lines))
        except Exception:
            pass

    return True, 'Удалено из внедрённых'


def _load_embedded_for_model(model_name):
    """Загружает embedded конфиг для модели. Приоритет: in-memory > файл/APPDATA."""
    model_key = model_name.lower()
    # 1. In-memory (после _embed_config в текущей сессии)
    if EMBEDDED_CONFIGS and model_key in EMBEDDED_CONFIGS:
        return EMBEDDED_CONFIGS[model_key]
    # 2. APPDATA fallback (только frozen mode)
    if getattr(sys, 'frozen', False):
        appdata_p = os.path.join(os.path.expanduser('~'), '.p3dviewer_embedded', f'{model_key}.json')
        if os.path.exists(appdata_p):
            try:
                with open(appdata_p, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
    return None


# ── Система тем ────────────────────────────────────────────────────────────

THEME_FILE = os.path.join(os.path.expanduser('~'), '.p3dviewer_theme.json')

DEFAULT_THEME = {
    'bg_main':    '#0f1019',   # v0.1.25: фон окна (глубже)
    'bg_side':    '#131520',   # v0.1.25: боковая панель
    'bg_widget':  '#1d1f2e',   # v0.1.25: фон виджетов / кнопки
    'bg_sep':     '#2a2d3e',   # v0.1.25: разделители (мягче)
    'fg_text':    '#e8eaf0',   # v0.1.25: основной текст
    'fg_label':   '#9ca0b0',   # v0.1.25: заголовки секций (мягче)
    'fg_muted':   '#5a5e72',   # v0.1.25: приглушённый текст
    'btn_bg':     '#1d1f2e',   # v0.1.25: кнопки: фон
    'btn_fg':     '#e8eaf0',   # v0.1.25: кнопки: текст
    'btn_hover':  '#2a2d3e',   # v0.1.25: кнопки: hover
    'accent_bg':  '#3b82f6',   # акцент: фон (electric blue)
    'accent_fg':  '#ffffff',   # акцент: текст (белый)
    'accent_hov': '#2563eb',   # акцент: hover
    'accent_dim': '#1e3a8a',   # акцент: dim/disabled
    'c_ok':       '#4ade80',   # v0.1.25: статус: успех (ярче)
    'c_warn':     '#fbbf24',   # v0.1.25: статус: предупреждение
    'c_err':      '#f87171',   # v0.1.25: статус: ошибка
    'gl_bg':      '#08090e',   # v0.1.25: фон 3D вьюпорта (глубже)
    'btn_relief': 'flat',      # стиль кнопок
    'font_fam':   'Segoe UI Variable',  # шрифт
    'font_sz':    10,          # v0.1.25: размер шрифта 9→10
    'side_w':     235,         # ширина боковой панели
}

PRESET_THEMES = {
    'Catppuccin': DEFAULT_THEME.copy(),
    'Dracula': {
        'bg_main': '#282a36', 'bg_side': '#21222c', 'bg_widget': '#414558',
        'bg_sep': '#44475a', 'fg_text': '#f8f8f2', 'fg_label': '#6272a4',
        'fg_muted': '#44475a', 'btn_bg': '#414558', 'btn_fg': '#f8f8f2',
        'btn_hover': '#6272a4', 'accent_bg': '#bd93f9', 'accent_fg': '#282a36',
        'accent_hov': '#ff79c6', 'c_ok': '#50fa7b', 'c_warn': '#f1fa8c',
        'c_err': '#ff5555', 'gl_bg': '#1a1b26',
        'btn_relief': 'flat', 'font_fam': 'Segoe UI', 'font_sz': 9, 'side_w': 235,
    },
    'VS Code': {
        'bg_main': '#1e1e1e', 'bg_side': '#252526', 'bg_widget': '#3c3c3c',
        'bg_sep': '#444444', 'fg_text': '#d4d4d4', 'fg_label': '#808080',
        'fg_muted': '#555555', 'btn_bg': '#3c3c3c', 'btn_fg': '#d4d4d4',
        'btn_hover': '#505050', 'accent_bg': '#0e639c', 'accent_fg': '#ffffff',
        'accent_hov': '#1177bb', 'c_ok': '#89d185', 'c_warn': '#cca700',
        'c_err': '#f48771', 'gl_bg': '#1e1e1e',
        'btn_relief': 'flat', 'font_fam': 'Segoe UI', 'font_sz': 9, 'side_w': 235,
    },
    'Monokai': {
        'bg_main': '#272822', 'bg_side': '#1e1f1c', 'bg_widget': '#3e3d32',
        'bg_sep': '#49483e', 'fg_text': '#f8f8f2', 'fg_label': '#75715e',
        'fg_muted': '#49483e', 'btn_bg': '#3e3d32', 'btn_fg': '#f8f8f2',
        'btn_hover': '#49483e', 'accent_bg': '#a6e22e', 'accent_fg': '#272822',
        'accent_hov': '#e6db74', 'c_ok': '#a6e22e', 'c_warn': '#e6db74',
        'c_err': '#f92672', 'gl_bg': '#1e1f1c',
        'btn_relief': 'flat', 'font_fam': 'Segoe UI', 'font_sz': 9, 'side_w': 235,
    },
    'Light': {
        'bg_main': '#f0f0f5', 'bg_side': '#e4e4ee', 'bg_widget': '#d0d0dc',
        'bg_sep': '#b0b0c0', 'fg_text': '#1e1e2e', 'fg_label': '#4a4a6a',
        'fg_muted': '#8080a0', 'btn_bg': '#d0d0dc', 'btn_fg': '#1e1e2e',
        'btn_hover': '#b8b8cc', 'accent_bg': '#1a6aba', 'accent_fg': '#ffffff',
        'accent_hov': '#145498', 'c_ok': '#207820', 'c_warn': '#906000',
        'c_err': '#c02020', 'gl_bg': '#d8d8e8',
        'btn_relief': 'raised', 'font_fam': 'Segoe UI', 'font_sz': 9, 'side_w': 235,
    },
    'Solarized': {
        'bg_main': '#002b36', 'bg_side': '#073642', 'bg_widget': '#0d4a5a',
        'bg_sep': '#0d4a5a', 'fg_text': '#839496', 'fg_label': '#586e75',
        'fg_muted': '#073642', 'btn_bg': '#0d4a5a', 'btn_fg': '#839496',
        'btn_hover': '#1a5a6c', 'accent_bg': '#268bd2', 'accent_fg': '#fdf6e3',
        'accent_hov': '#2aa198', 'c_ok': '#859900', 'c_warn': '#b58900',
        'c_err': '#dc322f', 'gl_bg': '#001e26',
        'btn_relief': 'flat', 'font_fam': 'Segoe UI', 'font_sz': 9, 'side_w': 235,
    },
    'Teal Dark': {
        'bg_main':    '#111520',
        'bg_side':    '#0d1018',
        'bg_widget':  '#1a2030',
        'bg_sep':     '#1e2838',
        'fg_text':    '#ffffff',
        'fg_label':   '#3b82f6',
        'fg_muted':   '#6b7280',
        'btn_bg':     '#1a2030',
        'btn_fg':     '#ffffff',
        'btn_hover':  '#1e2838',
        'accent_bg':  '#3b82f6',
        'accent_fg':  '#ffffff',
        'accent_hov': '#2563eb',
        'c_ok':       '#3b82f6',
        'c_warn':     '#f9e2af',
        'c_err':      '#f38ba8',
        'gl_bg':      '#080c10',
        'btn_relief': 'flat',
        'font_fam':   'Segoe UI',
        'font_sz':    9,
        'side_w':     235,
    },
    'Amber Dark': {
        'bg_main':    '#111008',
        'bg_side':    '#0c0b05',
        'bg_widget':  '#1a1a08',
        'bg_sep':     '#1e1a08',
        'fg_text':    '#EF9F27',
        'fg_label':   '#BA7517',
        'fg_muted':   '#3a2c0a',
        'btn_bg':     '#1a1a08',
        'btn_fg':     '#EF9F27',
        'btn_hover':  '#1e1a10',
        'accent_bg':  '#BA7517',
        'accent_fg':  '#faeeda',
        'accent_hov': '#854F0B',
        'c_ok':       '#97C459',
        'c_warn':     '#EF9F27',
        'c_err':      '#f38ba8',
        'gl_bg':      '#070600',
        'btn_relief': 'flat',
        'font_fam':   'Segoe UI',
        'font_sz':    9,
        'side_w':     235,
    },
    'Coral Dark': {
        'bg_main':    '#140c0c',
        'bg_side':    '#0e0808',
        'bg_widget':  '#1e1010',
        'bg_sep':     '#241010',
        'fg_text':    '#F0997B',
        'fg_label':   '#D85A30',
        'fg_muted':   '#3a1410',
        'btn_bg':     '#1e1010',
        'btn_fg':     '#F0997B',
        'btn_hover':  '#281414',
        'accent_bg':  '#993C1D',
        'accent_fg':  '#FAECE7',
        'accent_hov': '#712B13',
        'c_ok':       '#5dcaa5',
        'c_warn':     '#EF9F27',
        'c_err':      '#f38ba8',
        'gl_bg':      '#080505',
        'btn_relief': 'flat',
        'font_fam':   'Segoe UI',
        'font_sz':    9,
        'side_w':     235,
    },
    'Ice Blue': {
        'bg_main':    '#080f14',
        'bg_side':    '#050b10',
        'bg_widget':  '#0e1e2a',
        'bg_sep':     '#0e1e2a',
        'fg_text':    '#85B7EB',
        'fg_label':   '#378ADD',
        'fg_muted':   '#0c2a40',
        'btn_bg':     '#0e1e2a',
        'btn_fg':     '#85B7EB',
        'btn_hover':  '#122030',
        'accent_bg':  '#185FA5',
        'accent_fg':  '#E6F1FB',
        'accent_hov': '#0C447C',
        'c_ok':       '#5dcaa5',
        'c_warn':     '#EF9F27',
        'c_err':      '#f38ba8',
        'gl_bg':      '#030810',
        'btn_relief': 'flat',
        'font_fam':   'Segoe UI',
        'font_sz':    9,
        'side_w':     235,
    },
    'Matrix Green': {
        'bg_main':    '#080f0a',
        'bg_side':    '#050c06',
        'bg_widget':  '#0e1e10',
        'bg_sep':     '#0e1e10',
        'fg_text':    '#97C459',
        'fg_label':   '#639922',
        'fg_muted':   '#0c2a0e',
        'btn_bg':     '#0e1e10',
        'btn_fg':     '#97C459',
        'btn_hover':  '#122014',
        'accent_bg':  '#3B6D11',
        'accent_fg':  '#EAF3DE',
        'accent_hov': '#27500A',
        'c_ok':       '#97C459',
        'c_warn':     '#EF9F27',
        'c_err':      '#f38ba8',
        'gl_bg':      '#030a04',
        'btn_relief': 'flat',
        'font_fam':   'Segoe UI',
        'font_sz':    9,
        'side_w':     235,
    },
    'Synthwave': {
        'bg_main':    '#120a14',
        'bg_side':    '#0d0610',
        'bg_widget':  '#1c0a20',
        'bg_sep':     '#200c24',
        'fg_text':    '#ED93B1',
        'fg_label':   '#D4537E',
        'fg_muted':   '#3a1040',
        'btn_bg':     '#1c0a20',
        'btn_fg':     '#ED93B1',
        'btn_hover':  '#241030',
        'accent_bg':  '#993556',
        'accent_fg':  '#FBEAF0',
        'accent_hov': '#72243E',
        'c_ok':       '#5dcaa5',
        'c_warn':     '#EF9F27',
        'c_err':      '#f38ba8',
        'gl_bg':      '#080510',
        'btn_relief': 'flat',
        'font_fam':   'Segoe UI',
        'font_sz':    9,
        'side_w':     235,
    },
    'Gunmetal': {
        'bg_main':    '#0e0f11',
        'bg_side':    '#090a0c',
        'bg_widget':  '#161820',
        'bg_sep':     '#1a1c20',
        'fg_text':    '#B4B2A9',
        'fg_label':   '#888780',
        'fg_muted':   '#252628',
        'btn_bg':     '#161820',
        'btn_fg':     '#B4B2A9',
        'btn_hover':  '#1e2028',
        'accent_bg':  '#5F5E5A',
        'accent_fg':  '#F1EFE8',
        'accent_hov': '#444441',
        'c_ok':       '#97C459',
        'c_warn':     '#EF9F27',
        'c_err':      '#f38ba8',
        'gl_bg':      '#060708',
        'btn_relief': 'flat',
        'font_fam':   'Segoe UI',
        'font_sz':    9,
        'side_w':     235,
    },
}

COLOR_NAMES = {
    'bg_main':   'Фон окна',       'bg_side':   'Боковая панель',
    'bg_widget': 'Виджеты/кнопки', 'bg_sep':    'Разделители',
    'fg_text':   'Текст основной', 'fg_label':  'Заголовки',
    'fg_muted':  'Текст тусклый',  'btn_bg':    'Кнопка: фон',
    'btn_fg':    'Кнопка: текст',  'btn_hover': 'Кнопка: hover',
    'accent_bg': 'Акцент: фон',    'accent_fg': 'Акцент: текст',
    'accent_hov':'Акцент: hover',  'c_ok':      'Статус: OK',
    'c_warn':    'Статус: warning','c_err':     'Статус: error',
    'gl_bg':     'Фон 3D вьюпорта',
}

_SECTION_NAMES = ['ФАЙЛ', 'ТЕКСТУРЫ (ручной выбор)', 'МАТЕРИАЛЫ', 'КОНВЕРТАЦИЯ']  # v3.1.8: ВИД удалён


def _load_theme():
    settings = _load_app_settings()
    name = settings.get('theme_name', 'Teal Dark')
    return PRESET_THEMES.get(name, PRESET_THEMES.get('Teal Dark', DEFAULT_THEME)).copy()


def _save_theme(t):
    try:
        json.dump(t, open(THEME_FILE, 'w', encoding='utf-8'), indent=2, ensure_ascii=False)
    except Exception:
        pass


# ── GLSL шейдеры ───────────────────────────────────────────────────────────

VERT_SRC = """\
#version 120
uniform int u_flip_x;
uniform int u_flip_y;
uniform int u_flip_z;
attribute vec3 a_tangent;
varying vec3 v_ne;
varying vec3 v_te;
varying vec3 v_be;
varying vec2 v_uv;
void main() {
    // v3.0: mesh mirror по осям — flip vertex positions + normals + tangent
    vec3 p = gl_Vertex.xyz;
    vec3 n = gl_Normal;
    vec3 t = a_tangent;
    if (u_flip_x == 1) { p.x = -p.x; n.x = -n.x; t.x = -t.x; }
    if (u_flip_y == 1) { p.y = -p.y; n.y = -n.y; t.y = -t.y; }
    if (u_flip_z == 1) { p.z = -p.z; n.z = -n.z; t.z = -t.z; }
    gl_Position = gl_ModelViewProjectionMatrix * vec4(p, 1.0);
    v_ne = normalize(gl_NormalMatrix * n);
    v_te = normalize(gl_NormalMatrix * t);
    v_be = cross(v_ne, v_te);
    v_uv = gl_MultiTexCoord0.xy;
}
"""

FRAG_SRC = """\
#version 120
uniform sampler2D u_diff;
uniform sampler2D u_nrm;
uniform int u_has_tex;
uniform int u_has_nrm;
uniform int u_lit;
uniform int u_alpha;
uniform float u_brightness;
uniform int u_mirror_uv;  // v3.0: 1 = flip V UV (DirectX→OpenGL convention)
uniform int u_flip_x;     // v3.0: 1 = flip vertex positions по X (mesh mirror)
uniform int u_flip_y;     // v3.0: 1 = flip vertex positions по Y (mesh mirror)
uniform int u_flip_z;     // v3.0: 1 = flip vertex positions по Z (mesh mirror)
varying vec3 v_ne;
varying vec3 v_te;
varying vec3 v_be;
varying vec2 v_uv;
void main() {
    // v3.0: V-flip UV (DirectX→OpenGL convention). Toggle в UI.
    vec2 base_uv = u_mirror_uv == 1 ? vec2(v_uv.x, 1.0 - v_uv.y) : v_uv;
    vec2 uv = base_uv;
    vec4 col = u_has_tex == 1 ? texture2D(u_diff, uv)
                               : vec4(0.53, 0.71, 0.98, 1.0);
    float alpha = u_alpha == 1 ? col.a : 1.0;
    if (u_lit == 0) {
        gl_FragColor = vec4(col.rgb * 0.25, alpha);
        return;
    }
    vec3 N;
    if (u_has_nrm == 1) {
        vec3 nm = texture2D(u_nrm, uv).rgb * 2.0 - 1.0;
        mat3 TBN = mat3(normalize(v_te), normalize(v_be), normalize(v_ne));
        N = normalize(TBN * nm);
    } else {
        N = normalize(v_ne);
    }
    vec3 L0 = normalize(vec3( 1.0,  2.0,  3.0));
    vec3 L1 = normalize(vec3(-3.0,  1.0, -1.0));
    vec3 L2 = normalize(vec3( 0.0, -1.0, -3.0));
    float d0 = abs(dot(N, L0));
    float d1 = abs(dot(N, L1));
    float d2 = abs(dot(N, L2));
    vec3 amb  = vec3(0.30);
    vec3 diff = vec3(1.1)*d0 + vec3(0.6,0.6,0.65)*d1 + vec3(0.4)*d2;
    vec3 lit  = clamp(amb + diff, 0.0, 2.0) * col.rgb * u_brightness;
    gl_FragColor = vec4(clamp(lit, 0.0, 1.0), alpha);
}
"""


# ── ImageToPAA (DayZ Tools) ────────────────────────────────────────────────

def _find_imagetopaa():
    base    = getattr(sys, '_MEIPASS', SCRIPT_DIR)
    bundled = os.path.join(base, 'imagetopaa', 'ImageToPAA.exe')
    if os.path.isfile(bundled): return bundled
    for drive in 'CDEFGHIJKLMNOPQRSTUVWXYZ':
        for stem in (r'Steam\steamapps\common',
                     r'Program Files (x86)\Steam\steamapps\common'):
            p = os.path.join(f'{drive}:\\', stem,
                             r'DayZ Tools\Bin\ImageToPAA\ImageToPAA.exe')
            if os.path.isfile(p): return p
    return None

IMAGE_TO_PAA = _find_imagetopaa()

# Не показывать консольное окно при запуске subprocess
_NO_WIN = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


# ── PAA reader ─────────────────────────────────────────────────────────────

def _read_paa_native(path):
    if not IMAGE_TO_PAA:
        raise FileNotFoundError('ImageToPAA.exe не найден')
    with tempfile.NamedTemporaryFile(suffix='.tga', delete=False) as tf:
        tmp = tf.name
    try:
        r = subprocess.run(
            [IMAGE_TO_PAA, path, tmp],
            capture_output=True, timeout=30,
            cwd=os.path.dirname(IMAGE_TO_PAA),
            creationflags=_NO_WIN
        )
        if r.returncode != 0 or not os.path.isfile(tmp):
            raise RuntimeError(f'ImageToPAA вернул код {r.returncode}')
        return np.array(Image.open(tmp).convert('RGBA'), dtype=np.uint8)
    finally:
        try: os.unlink(tmp)
        except OSError: pass


def _make_dds(dxt_data, w, h, is_dxt5):
    fourcc     = b'DXT5' if is_dxt5 else b'DXT1'
    block_size = 16     if is_dxt5 else 8
    bw = max(1, (w + 3) // 4)
    bh = max(1, (h + 3) // 4)
    linear = bw * bh * block_size
    hdr = bytearray(128)
    struct.pack_into('<4sI', hdr, 0,   b'DDS ', 124)
    struct.pack_into('<I',   hdr, 8,   0x00021007)
    struct.pack_into('<II',  hdr, 12,  h, w)
    struct.pack_into('<I',   hdr, 20,  linear)
    struct.pack_into('<I',   hdr, 28,  1)
    struct.pack_into('<II',  hdr, 76,  32, 4)
    hdr[84:88] = fourcc
    struct.pack_into('<I',   hdr, 108, 0x1000)
    return bytes(hdr) + dxt_data[:linear]


def _dxt_to_rgba(dxt_data, w, h, is_dxt5=False):
    buf = _make_dds(dxt_data, w, h, is_dxt5)
    return np.array(Image.open(io.BytesIO(buf)).convert('RGBA'), dtype=np.uint8)


def _bi_lzss(src, max_out):
    N    = 4096
    ring = bytearray(b'\x20' * N)
    rpos = N - 18
    dst  = bytearray()
    si   = 0
    while si < len(src) and len(dst) < max_out:
        ctrl = src[si]; si += 1
        for bit in range(8):
            if si >= len(src) or len(dst) >= max_out: break
            if ctrl & (1 << bit):
                c = src[si]; si += 1
                dst.append(c); ring[rpos] = c; rpos = (rpos + 1) & (N - 1)
            else:
                if si + 1 >= len(src): break
                lo = src[si]; hi = src[si + 1]; si += 2
                length = (hi & 0x0F) + 3
                offset = lo | ((hi & 0xF0) << 4)
                for k in range(length):
                    c = ring[(offset + k) & (N - 1)]
                    dst.append(c); ring[rpos] = c; rpos = (rpos + 1) & (N - 1)
                    if len(dst) >= max_out: break
    return bytes(dst[:max_out])


def _read_paa_python(path):
    with open(path, 'rb') as f:
        data = f.read()
    magic = struct.unpack_from('<H', data, 0)[0]
    if magic in (0x4444, 0x5555):
        is_dxt5 = magic == 0x5555
        w  = struct.unpack_from('<H', data, 16)[0]
        h  = struct.unpack_from('<H', data, 18)[0]
        sz = struct.unpack_from('<I', data, 20)[0]
        raw = data[24: 24 + sz]
        exp = max(1,(w+3)//4) * max(1,(h+3)//4) * (16 if is_dxt5 else 8)
        if len(raw) < exp: raw = _bi_lzss(raw, exp)
        return _dxt_to_rgba(raw, w, h, is_dxt5)
    if magic != 0xFF01:
        raise ValueError(f'Неизвестный PAA magic: {hex(magic)}')
    off = 2; mip_offsets = []
    for _ in range(30):
        if off + 12 > len(data): break
        tag    = data[off:off + 8]
        length = struct.unpack_from('<I', data, off + 8)[0]
        tag_s  = tag.decode('latin-1', errors='replace')
        off   += 12
        if length > len(data): break
        if tag_s == 'GGATSFFO' and length >= 4:
            mip_offsets = [o for o in struct.unpack_from(f'<{length//4}I', data, off) if o > 0]
        off += length
        if tag == b'\x00' * 8: break
    if not mip_offsets:
        raise ValueError('GGATSFFO не найден')
    offsets = sorted(mip_offsets) + [len(data)]
    best_rgba = best_size = None
    for i in range(len(offsets) - 1):
        s, e = offsets[i], offsets[i + 1]
        if s + 4 > len(data): continue
        w_enc = struct.unpack_from('<H', data, s)[0]
        h     = struct.unpack_from('<H', data, s + 2)[0]
        w     = w_enc & 0x7FFF
        if not w or not h: continue
        if w_enc & 0x8000:
            comp = data[s + 4:e]
            for dxt5 in (False, True):
                exp = max(1,(w+3)//4) * max(1,(h+3)//4) * (16 if dxt5 else 8)
                try:
                    dxt = _bi_lzss(comp, exp)
                    if sum(b == 0x20 for b in dxt[:512]) / min(512, len(dxt)) > 0.6: continue
                    return _dxt_to_rgba(dxt, w, h, dxt5)
                except Exception: continue
        else:
            if s + 7 > len(data): continue
            ds  = struct.unpack_from('<H', data, s + 4)[0]
            raw = data[s + 7: s + 7 + ds]
            if len(raw) < ds: continue
            try:
                rgba = _dxt_to_rgba(raw, w, h, False)
                if best_size is None or w * h > best_size:
                    best_rgba, best_size = rgba, w * h
                return rgba
            except Exception: continue
    if best_rgba is not None:
        bh, bw = best_rgba.shape[:2]
        if max(bw, bh) <= 128:
            img = Image.fromarray(best_rgba).resize((512, 512), Image.LANCZOS)
            best_rgba = np.array(img, dtype=np.uint8)
        return best_rgba
    raise ValueError('Нет читаемых мипмапов')


def read_paa(path):
    try:   return _read_paa_native(path)
    except Exception: return _read_paa_python(path)


# ── RVMAT parser ───────────────────────────────────────────────────────────

def _derap(path):
    if not os.path.exists(DERAP_EXE): return None
    try:
        tmp = path + '.tmp_derap.txt'
        subprocess.run([DERAP_EXE, path, tmp], capture_output=True, timeout=10,
                       creationflags=_NO_WIN)
        if os.path.exists(tmp):
            txt = open(tmp, errors='replace').read()
            os.unlink(tmp)
            return txt
    except Exception: pass
    return None


def parse_rvmat(path):
    raw = open(path, 'rb').read()
    if raw[:4] == b'\x00raP':
        txt = _derap(path) or raw.decode('latin-1', errors='replace')
    else:
        txt = raw.decode('utf-8', errors='replace')
    result = {}
    def stage_tex(n):
        m = re.search(rf'class\s+Stage{n}\s*\{{[^}}]*?texture\s*=\s*"([^"]+)"',
                      txt, re.DOTALL | re.IGNORECASE)
        return m.group(1) if m else None
    t0 = stage_tex(0)
    if t0: result['diffuse'] = t0
    t1 = stage_tex(1)
    if t1: result['normal'] = t1
    if 'diffuse' not in result:
        m = re.search(r'"([^"]*_co\.paa)"', txt, re.IGNORECASE)
        if m: result['diffuse'] = m.group(1)
    if 'normal' not in result:
        m = re.search(r'"([^"]*_nohq\.paa)"', txt, re.IGNORECASE)
        if m: result['normal'] = m.group(1)
    return result


def find_texture_near(ref_path, tex_rel):
    """Найти файл по имени, поднимаясь на 6 уровней вверх от ref_path."""
    name = os.path.basename(tex_rel.replace('\\', os.sep))
    if not name: return None
    if os.path.isfile(tex_rel): return tex_rel
    search_root = os.path.dirname(os.path.abspath(ref_path))
    visited = set()
    for _ in range(6):
        if search_root in visited: break
        visited.add(search_root)
        for root, dirs, files in os.walk(search_root):
            depth = len(os.path.relpath(root, search_root).split(os.sep)) - 1
            if depth >= 3: dirs.clear(); continue
            for fn in files:
                if fn.lower() == name.lower():
                    return os.path.join(root, fn)
        parent = os.path.dirname(search_root)
        if parent == search_root: break
        search_root = parent
    return None


# ── P3D parser ─────────────────────────────────────────────────────────────

def _load_via_dll(path):
    _dbg(f'_load_via_dll: {path}')
    try:
        _dll_dir = _EXE_DIR if getattr(sys, 'frozen', False) else SCRIPT_DIR
        os.add_dll_directory(MIKERO)
        os.add_dll_directory(_dll_dir)
        ctypes.WinDLL(os.path.join(MIKERO, 'deOgg64.dll'))
        dll = ctypes.WinDLL(os.path.join(_dll_dir, 'DePbo64.dll'))
        _dbg('DLL loaded OK')
        # В subprocess режиме — также print в stderr (там ничего не блокируется)
        if '--p3d-subproc' in sys.argv:
            print(f'[subproc] DLL loaded OK; path={path}', file=sys.stderr, flush=True)
    except Exception as e:
        _dbg(f'DLL load FAILED: {e}')
        if '--p3d-subproc' in sys.argv:
            print(f'[subproc] DLL load FAILED: {e!r}', file=sys.stderr, flush=True)
        return None, None, None, 0, '', [], [], []

    V = ctypes.c_void_p
    def fn(n, r, *a):
        f = dll[n]; f.restype = r; f.argtypes = list(a); return f

    try:
        ctor = dll['??0BisDLL@@QEAA@XZ']
        gp3d = fn('?dll_GetDeP3d@BisDLL@@QEAAPEAVDeP3d@@XZ', V, V)
        op   = fn('?p3d_Open@DeP3d@@QEAAHPEBDIP6A_N0ZZ@Z',
                  ctypes.c_int, V, ctypes.c_char_p, ctypes.c_uint, V)
        gl_  = fn('?p3d_get_lod@DeP3d@@QEAAPEBXH@Z', V, V, ctypes.c_int)
        gf   = fn('?p3d_get_face_table@DeP3d@@QEAAPEAULodFace@@PEBXPEA_K@Z',    V, V, V, V)
        gpts = fn('?p3d_get_points_table@DeP3d@@QEAAPEAUVector3@@PEBXPEA_K@Z',  V, V, V, V)
        gnrm = fn('?p3d_get_normals_table@DeP3d@@QEAAPEAUVector3@@PEBXPEA_K@Z', V, V, V, V)
        guv  = fn('?p3d_get_uvset_table@DeP3d@@QEAAPEAUUVFloats@@PEBXIPEA_K@Z',
                  V, V, V, ctypes.c_uint, V)
        gnt  = fn('?p3d_get_nTriangles@DeP3d@@QEAA_KPEBX@Z', ctypes.c_uint64, V, V)
        glc  = fn('?p3d_GetLodCount@DeP3d@@QEAAIXZ', ctypes.c_uint, V)
        gtex = fn('?p3d_get_texture_name@DeP3d@@QEAAPEBDPEBXH@Z',
                  ctypes.c_char_p, V, V, ctypes.c_int)
        gsec  = fn('?p3d_get_section_table@DeP3d@@QEAAPEAULodSection@@PEBXPEA_K@Z', V, V, V, V)
        gind  = fn('?p3d_get_face_indicators@DeP3d@@QEAAPEAEHPEA_K0@Z', V, V, V, V)
        gmat  = fn('?p3d_get_material@DeP3d@@QEAAPEAULodMaterial@@PEBXPEA_K@Z', V, V, V, V)
        gitem = fn('?p3d_get_item@DeP3d@@QEAAPEBIPEBXPEA_K@Z', V, V, V, V)
        gpf   = fn('?p3d_get_pointflags_table@DeP3d@@QEAAPEBIPEBXPEA_K@Z', V, V, V, V)
        gvp   = fn('?p3d_get_vtx_property@DeP3d@@QEAAPEBUVprops@@PEBXPEA_K@Z', V, V, V, V)
        gflg  = fn('?p3d_get_flagbits@DeP3d@@QEAAHPEBX@Z', ctypes.c_int, V, V)
        # gwhat — опциональная функция, может отсутствовать в DLL
        try:
            gwhat = fn('?p3d_get_what_table@DeP3d@@QEAAPEBUWHATSTRUCT@@PEBXPEA_K@Z', V, V, V, V)
        except Exception:
            gwhat = None

        buf = ctypes.create_string_buffer(1024 * 1024)
        ctor(buf); p3d = gp3d(buf)
        op_rc = op(p3d, path.encode('mbcs'), 0, None)
        if '--p3d-subproc' in sys.argv:
            print(f'[subproc] op() rc={op_rc} glc={glc(p3d)}', file=sys.stderr, flush=True)

        best_lod, best_nt = None, 0
        for li in range(glc(p3d)):
            lod = gl_(p3d, li)
            if lod:
                nt = gnt(p3d, lod)
                if nt > best_nt: best_nt = nt; best_lod = lod
        if '--p3d-subproc' in sys.argv:
            print(f'[subproc] best_lod={best_lod} best_nt={best_nt}', file=sys.stderr, flush=True)
        if not best_lod: return None, None, None, 0, '', [], [], []

        cnt  = ctypes.c_uint64(0)
        pcnt = ctypes.cast(ctypes.addressof(cnt), V)

        def read_floats(get_fn, dim):
            cnt.value = 0
            ptr = get_fn(p3d, best_lod, pcnt)
            n   = cnt.value
            raw = list(struct.unpack_from(f'<{n*dim}f',
                       (ctypes.c_uint8 * (n*dim*4)).from_address(ptr)))
            return [tuple(raw[i*dim:(i+1)*dim]) for i in range(n)]

        _dbg('Reading face/points/normals/UV tables...')
        cnt.value = 0
        face_ptr = gf(p3d, best_lod, pcnt)
        nf = cnt.value
        _dbg(f'  nf={nf}  face_ptr={face_ptr}')
        if '--p3d-subproc' in sys.argv:
            print(f'[subproc] after gf: nf={nf} face_ptr={face_ptr}', file=sys.stderr, flush=True)
        pts  = read_floats(gpts, 3)
        _dbg(f'  pts={len(pts)}')
        if '--p3d-subproc' in sys.argv:
            print(f'[subproc] after gpts: pts={len(pts)}', file=sys.stderr, flush=True)
        nrms = read_floats(gnrm, 3)
        _dbg(f'  nrms={len(nrms)}')
        if '--p3d-subproc' in sys.argv:
            print(f'[subproc] after gnrm: nrms={len(nrms)}', file=sys.stderr, flush=True)
        cnt.value = 0
        up = guv(p3d, best_lod, 0, pcnt); nu = cnt.value
        raw_uv = list(struct.unpack_from(f'<{nu*2}f',
                      (ctypes.c_uint8 * (nu*2*4)).from_address(up)))
        uvs = [tuple(raw_uv[i*2:(i+1)*2]) for i in range(nu)]
        _dbg(f'  uvs={len(uvs)}')
        if '--p3d-subproc' in sys.argv:
            print(f'[subproc] after guv: uvs={len(uvs)}', file=sys.stderr, flush=True)
        # dll_log инициализируется ниже — пишем туда ПОСЛЕ инициализации
        if uvs:
            us = [u for u,v in uvs]; vs = [v for u,v in uvs]
            u_uniq = len(set(round(u,3) for u in us)); v_uniq = len(set(round(v,3) for v in vs))
            dbg_cat('uv', f'{os.path.basename(path)}: U=[{min(us):.4f},{max(us):.4f}] V=[{min(vs):.4f},{max(vs):.4f}] uniq_U={u_uniq} uniq_V={v_uniq}')
        # UV диагностика — какой диапазон, есть ли wrap
        if uvs:
            us = [u for u,v in uvs]; vs = [v for u,v in uvs]
            u_uniq = len(set(round(u,3) for u in us)); v_uniq = len(set(round(v,3) for v in vs))
            _dbg(f'  UV U range: [{min(us):.4f}, {max(us):.4f}] uniq={u_uniq}')
            _dbg(f'  UV V range: [{min(vs):.4f}, {max(vs):.4f}] uniq={v_uniq}')
            _dbg(f'  UV first12: {uvs[:12]}')
            _dbg(f'  UV last12:  {uvs[-12:]}')

        _dbg('Reading material list...')
        mat_list = []
        for gi in range(200):
            try:
                m = gtex(p3d, best_lod, gi)
                if not m: break
                mat_list.append(m.decode('mbcs'))
            except Exception:
                break
        _dbg(f'  mat_list ({len(mat_list)}): {mat_list}')
        if '--p3d-subproc' in sys.argv:
            print(f'[subproc] mat_list: {mat_list}', file=sys.stderr, flush=True)

        tex_hint = mat_list[0] if mat_list else ''
        face_mats = [''] * nf
        dll_log = [f'nf={nf}  n_mats={len(mat_list)}']
        # UV диагностика в dll_log (после инициализации!)
        if uvs:
            us = [u for u,v in uvs]; vs = [v for u,v in uvs]
            u_uniq = len(set(round(u,3) for u in us)); v_uniq = len(set(round(v,3) for v in vs))
            dll_log.append(f'uvs={len(uvs)}')
            dll_log.append(f'UV U range: [{min(us):.4f}, {max(us):.4f}] uniq={u_uniq}')
            dll_log.append(f'UV V range: [{min(vs):.4f}, {max(vs):.4f}] uniq={v_uniq}')
            dll_log.append(f'UV first12: {uvs[:12]}')
            dll_log.append(f'UV last12:  {uvs[-12:]}')

        def _probe_ptr(label, ptr, n, sizes, max_show=6):
            """Логирует поля структур по ptr при разных размерах struct."""
            dll_log.append(f'{label}: ptr={ptr} n={n}')
            if not ptr or not n: return
            for sz in sizes:
                try:
                    raw = (ctypes.c_uint8 * (n * sz)).from_address(ptr)
                    dll_log.append(f'  sz={sz}:')
                    for si in range(min(n, max_show)):
                        fields = [struct.unpack_from('<I', raw, si*sz + j*4)[0]
                                  for j in range(sz//4)]
                        dll_log.append(f'    [{si}]: {fields}')
                    dll_log.append('  ---')
                except Exception as e:
                    dll_log.append(f'  sz={sz} error: {e}')
                    break

        # ReadProcessMemory — не вызывает SEH краш при невалидной памяти
        _k32 = ctypes.WinDLL('kernel32', use_last_error=True)
        _rpm = _k32.ReadProcessMemory
        _rpm.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
        _rpm.restype  = ctypes.c_bool
        _hproc = _k32.GetCurrentProcess()

        def _safe_read(ptr, byte_count, label=''):
            """Безопасно читает byte_count байт через ReadProcessMemory.
            Не вызывает SEH-краш при выходе за границу буфера."""
            _dbg(f'    _safe_read {label}: ptr={ptr} bytes={byte_count}')
            if not ptr or byte_count <= 0:
                _dbg(f'    _safe_read SKIP (ptr=0 or count=0)')
                return None
            # Читаем чанками по 4 MB чтобы не упасть на очень большом блоке
            CHUNK = 4 * 1024 * 1024
            result = bytearray()
            offset = 0
            while offset < byte_count:
                n = min(CHUNK, byte_count - offset)
                buf     = (ctypes.c_uint8 * n)()
                n_read  = ctypes.c_size_t(0)
                ok = _rpm(_hproc, ptr + offset, buf, n, ctypes.byref(n_read))
                if not ok or n_read.value != n:
                    _dbg(f'    _safe_read FAILED at offset={offset} '
                         f'ok={ok} n_read={n_read.value} expected={n}')
                    return None
                result += bytes(buf)
                offset += n
            _dbg(f'    _safe_read OK ({byte_count} bytes)')
            return bytes(result)

        def _find_material_field(ptr, n, n_mats):
            """Поиск поля {0..n_mats-1} в struct-массиве.
            Пробует первые PROBE_N записей, потом верифицирует на всех."""
            PROBE_N = min(n, 500)   # пробуем на первых 500, не на всех 67k
            expected = set(range(n_mats))
            if not ptr or not n: return None, None, None
            _dbg(f'  _find_material_field: n={n} probe={PROBE_N} n_mats={n_mats}')

            for sz in [4, 8, 12, 16, 20, 24, 32, 40]:
                _dbg(f'  trying sz={sz} (probe {PROBE_N*sz} bytes)...')
                raw = _safe_read(ptr, PROBE_N * sz, f'sz={sz}')
                if raw is None:
                    _dbg(f'  sz={sz}: read failed, stopping')
                    break
                buf = np.frombuffer(raw, dtype=np.uint8).reshape(PROBE_N, sz)
                _dbg(f'  sz={sz}: buf shaped OK, scanning fields...')
                found = False
                for w, dt in [(1, np.uint8), (2, np.uint16), (4, np.uint32)]:
                    for off in range(0, sz - w + 1, w):
                        col = buf[:, off:off+w].view(dt).ravel()
                        uniq = set(int(v) for v in np.unique(col))
                        if uniq == expected:
                            sample = col[:20].tolist()
                            msg = f'  FOUND: sz={sz} off={off} w={w} sample={sample}'
                            dll_log.append(msg); _dbg(msg)
                            return (sz, off, w)
                _dbg(f'  sz={sz}: no matching field')
            _dbg('  _find_material_field: not found')
            return None, None, None

        solved = [False]

        # ── А: LodFace stride=144, mat_idx at offset 28 ───────────────────────
        # Из анализа dll_sections.txt (v1.33):
        #   face_ptr[0..23]  = заголовок (3 ptr x 8 байт)
        #   face[0] nv=3 at byte 24, mat=0 at byte 28, verts at 32-47
        #   face[1] nv=3 at byte 168 → stride = 168-24 = 144 байта
        #   mat_idx field index в uint32 массиве = 28//4 = 7
        FACE_STRIDE = 144
        MAT_FIELD   = 7   # offset в uint32 единицах внутри каждого stride

        def _lodface_stride_probe():
            """Логирует первые 20 граней при stride=144."""
            dll_log.append(f'=== LodFace stride probe ptr={face_ptr} nf={nf} ===')
            raw = _safe_read(face_ptr, min(nf, 20) * FACE_STRIDE, 'lodface_probe')
            if raw is None:
                dll_log.append('  probe read FAILED')
                return
            dll_log.append(f'  stride={FACE_STRIDE} mat_field=uint32[{MAT_FIELD}]')
            for i in range(min(nf, 20)):
                base = i * FACE_STRIDE
                u32s = struct.unpack_from('<36I', raw, base)
                nv   = u32s[6]   # offset 24 / 4 = 6
                mat  = u32s[7]   # offset 28 / 4 = 7
                v0   = u32s[8] & 0xFFFF
                v1   = u32s[9] & 0xFFFF
                v2   = u32s[10] & 0xFFFF
                dll_log.append(f'  face[{i}]: nv={nv} mat={mat} v=[{v0},{v1},{v2}]')

        def _lodface_read_mats():
            """Читает mat_idx для всех граней при stride=144.
            Пробует сначала полное чтение, при неудаче — чанками по 2000 граней.
            Rank-encoding для значений вне {0..n_mats-1}.
            Примечание: sample-проверки НЕ используем — mid-array read часто падает
            из-за нелинейного layout памяти (DLL не гарантирует смежность)."""
            _dbg('Strategy A: LodFace stride=144 chunked mat read')
            n_mats = len(mat_list)
            total_bytes = nf * FACE_STRIDE

            # ── Попытка 1: читаем весь массив за один вызов ──────────────────
            _dbg(f'  attempt full read: {total_bytes // 1024} KB from face_ptr')
            raw_all = _safe_read(face_ptr, total_bytes, 'lodface_full')

            if raw_all is not None:
                arr_all  = np.frombuffer(raw_all, dtype=np.uint32).reshape(nf, 36)
                mat_raw  = arr_all[:, MAT_FIELD].tolist()
                _dbg('  full read OK')
            else:
                # ── Попытка 2: чанками по 2000 граней ────────────────────────
                _dbg('  full read FAILED — switching to chunk mode (2000 faces/chunk)')
                CHUNK = 2000
                mat_raw = [-1] * nf
                ok_faces = 0
                for start in range(0, nf, CHUNK):
                    n_chunk = min(CHUNK, nf - start)
                    raw_c = _safe_read(face_ptr + start * FACE_STRIDE,
                                       n_chunk * FACE_STRIDE, f'chunk@{start}')
                    if raw_c is None:
                        dll_log.append(f'  chunk@{start} FAILED (skip {n_chunk} faces)')
                        continue
                    arr_c = np.frombuffer(raw_c, dtype=np.uint32).reshape(n_chunk, 36)
                    col_c = arr_c[:, MAT_FIELD].tolist()
                    for i in range(n_chunk):
                        mat_raw[start + i] = col_c[i]
                    ok_faces += n_chunk
                _dbg(f'  chunk mode: {ok_faces}/{nf} faces read OK')
                dll_log.append(f'  chunk mode: {ok_faces}/{nf} faces read')
                if ok_faces < nf // 4:
                    _dbg('  too few faces read — giving up Strategy A')
                    return False

            # ── Определяем уникальные значения (без -1 маркеров) ─────────────
            valid_vals = [v for v in mat_raw if v >= 0]
            uniq_all   = sorted(set(valid_vals))
            counts     = {v: valid_vals.count(v) for v in uniq_all[:20]}
            _dbg(f'  mat unique={uniq_all[:20]}  counts={counts}')
            dll_log.append(f'  mat unique={uniq_all[:20]}  counts_sample={counts}')

            if not uniq_all:
                return False
            if len(uniq_all) > n_mats * 8:
                _dbg(f'  too many unique values ({len(uniq_all)}) — wrong field')
                return False

            # ── Rank-encoding → назначаем материалы ──────────────────────────
            if set(uniq_all).issubset(set(range(n_mats))):
                rank_map = {v: v for v in uniq_all}
                method = 'direct'
            else:
                rank_map = {v: i for i, v in enumerate(uniq_all)}
                method = 'rank'
            _dbg(f'  rank_map={rank_map}  method={method}')
            dll_log.append(f'  rank_map={rank_map}  method={method}')

            fallback_mi = 0
            for fi in range(nf):
                raw_v = mat_raw[fi]
                mi = rank_map.get(raw_v, fallback_mi) if raw_v >= 0 else fallback_mi
                face_mats[fi] = mat_list[min(mi, n_mats - 1)]

            msg = f'  => face_mats SET via stride=144 method={method}'
            dll_log.append(msg); _dbg(msg)
            solved[0] = True
            return True

        _dbg('Strategy A: LodFace struct probe')
        dll_log.append(f'=== LodFace ptr={face_ptr} nf={nf} ===')
        _probe_ptr('LodFace', face_ptr, min(nf, 10), [8, 24], max_show=10)
        _lodface_stride_probe()

        if len(mat_list) > 1:
            _lodface_read_mats()

        # ── Б: p3d_get_what_table ─────────────────────────────────────────────
        if len(mat_list) > 1 and not solved[0] and gwhat is not None:
            _dbg('Strategy B: p3d_get_what_table')
            try:
                cnt.value = 0
                what_ptr = gwhat(p3d, best_lod, pcnt)
                n_what = cnt.value
                _dbg(f'  what_ptr={what_ptr} n={n_what}')
                dll_log.append(f'what_table: ptr={what_ptr} n={n_what}')
                if what_ptr and n_what > 0:
                    _probe_ptr('WhatStruct', what_ptr, min(n_what, 10),
                               [4, 8, 12, 16, 20, 24], max_show=10)
                    r = _find_material_field(what_ptr, min(n_what, nf), len(mat_list))
                    dll_log.append(f'  what mat_field: {r}')
                    if r[0] is not None:
                        sz, off, w = r
                        dt = {1: np.uint8, 2: np.uint16, 4: np.uint32}[w]
                        raw_w = _safe_read(what_ptr, min(n_what, nf) * sz, 'what_full')
                        if raw_w:
                            buf_w = np.frombuffer(raw_w, dtype=np.uint8).reshape(min(n_what,nf), sz)
                            idx_w = buf_w[:, off:off+w].view(dt).ravel()
                            for fi in range(min(n_what, nf)):
                                face_mats[fi] = mat_list[min(int(idx_w[fi]), len(mat_list)-1)]
                            dll_log.append(f'  => face_mats SET from what_table')
                            _dbg('  => face_mats SET from what_table')
                            solved[0] = True
            except Exception as e:
                dll_log.append(f'what_table error: {e}'); _dbg(f'  error: {e}')

        # ── В: p3d_get_item (только лог, не применяем — n << nf) ─────────────
        _dbg('Strategy C: p3d_get_item (info only)')
        try:
            cnt.value = 0
            item_ptr = gitem(p3d, best_lod, pcnt)
            n_item = cnt.value
            _dbg(f'  item_ptr={item_ptr} n={n_item}')
            dll_log.append(f'item: ptr={item_ptr} n={n_item}')
            if item_ptr and n_item > 0:
                # Читаем только реальные данные (n_item * 4 байта)
                raw_i = _safe_read(item_ptr, n_item * 4, 'item_real')
                if raw_i:
                    vals = list(struct.unpack_from(f'<{n_item}I', raw_i))
                    dll_log.append(f'  values(uint32): {vals}')
                    _dbg(f'  item values: {vals}')
        except Exception as e:
            dll_log.append(f'item error: {e}'); _dbg(f'  error: {e}')

        # ── Г: p3d_get_vtx_property ──────────────────────────────────────────
        if len(mat_list) > 1 and not solved[0]:
            _dbg('Strategy D: p3d_get_vtx_property')
            try:
                cnt.value = 0
                vp_ptr = gvp(p3d, best_lod, pcnt)
                n_vp = cnt.value
                _dbg(f'  vp_ptr={vp_ptr} n={n_vp}')
                _probe_ptr('vtx_property', vp_ptr, min(n_vp, 10), [4, 8, 12, 16, 20, 24])
                dll_log.append(f'vtx_property: ptr={vp_ptr} n={n_vp}')
            except Exception as e:
                dll_log.append(f'vtx_property error: {e}'); _dbg(f'  error: {e}')

        # ── Д: flagbits + section_table (всегда логируем) ────────────────────
        try:
            fb = gflg(p3d, best_lod)
            dll_log.append(f'flagbits=0x{fb&0xFFFFFFFF:08x}')
            _dbg(f'flagbits=0x{fb&0xFFFFFFFF:08x}')
        except Exception as e:
            dll_log.append(f'flagbits error: {e}')

        try:
            cnt.value = 0
            sec_ptr = gsec(p3d, best_lod, pcnt)
            n_sec = cnt.value
            _dbg(f'  sec_ptr={sec_ptr} n={n_sec}')
            dll_log.append(f'section_table n={n_sec}')
            if sec_ptr and n_sec > 0:
                _probe_ptr('LodSection', sec_ptr, n_sec, [8, 12, 16, 20, 24, 32])
        except Exception as e:
            dll_log.append(f'section_table error: {e}'); _dbg(f'  error: {e}')

        _dbg(f'Analysis done. solved={solved[0]}  writing dll_sections.txt')
        _write_log('dll_sections.txt', dll_log)

        # ── Read proxies from best_lod ─────────────────────────────────────────
        proxies = []
        try:
            gprx = fn('?p3d_get_proxy_table@DeP3d@@QEAAPEAULodProxy@@PEBXPEA_K@Z', V, V, V, V)
            cnt.value = 0
            prx_ptr = gprx(p3d, best_lod, pcnt)
            n_prx   = cnt.value
            if prx_ptr and n_prx > 0:
                import re as _re
                with open(path, 'rb') as _f:
                    _p3d_bin = _f.read()
                _proxy_names = [
                    m.group().decode('ascii', 'replace')
                    for m in _re.finditer(rb'proxy:[^\x00]+', _p3d_bin)
                ]
                _PRX_STRIDE = 128
                for i in range(n_prx):
                    _raw_prx = _safe_read(prx_ptr + i * _PRX_STRIDE, 84)
                    if _raw_prx and len(_raw_prx) >= 84:
                        fs = struct.unpack_from('<12f', _raw_prx, 32)
                        pos  = (fs[9], fs[10], fs[11])
                        mat  = (fs[0], fs[1], fs[2],
                                fs[3], fs[4], fs[5],
                                fs[6], fs[7], fs[8])
                        prx_idx = struct.unpack_from('<I', _raw_prx, 80)[0]
                        name = _proxy_names[i] if i < len(_proxy_names) else f'proxy_{i}'
                        _prx_entry = {'name': name, 'pos': list(pos), 'mat': list(mat), 'idx': prx_idx}
                        # Record file offset for in-place write-back.
                        # The 12-float transform is stored after the SELECTION name in binary
                        # (proxy path minus 'proxy:' prefix and numeric '.001' suffix).
                        # Try multiple name variants to handle case/path differences.
                        _sep = chr(92)  # backslash
                        _sel_base = name
                        if _sel_base.lower().startswith('proxy:'):
                            _sel_base = _sel_base[6:]
                        _sel_base = _re.sub(r'\.\d+$', '', _sel_base)
                        _basename = _sel_base.replace('/', _sep).split(_sep)[-1]
                        # Candidates: exact, lowercase, forward-slash, basename, basename-lower
                        _candidates = [_sel_base]
                        if _sel_base.lower() != _sel_base:
                            _candidates.append(_sel_base.lower())
                        _fwd = _sel_base.replace(_sep, '/')
                        if _fwd != _sel_base:
                            _candidates.append(_fwd)
                        if _basename and _basename != _sel_base:
                            _candidates.append(_basename)
                            if _basename.lower() != _basename:
                                _candidates.append(_basename.lower())

                        _all_hits = []  # list of (diff, offset, floats, cand)
                        _found_by = ''
                        for _cand in _candidates:
                            _cand_b = _cand.encode('ascii', 'replace') + b'\x00'
                            _fstart = 0
                            while True:
                                _foff_base = _p3d_bin.find(_cand_b, _fstart)
                                if _foff_base < 0:
                                    break
                                _foff = _foff_base + len(_cand_b)
                                try:
                                    _ffs = struct.unpack_from('<12f', _p3d_bin, _foff)
                                    # Validate rotation matrix rows (allow slight scale: 0.7–1.35)
                                    _rot_ok = all(
                                        0.7 < sum(_ffs[_r * 3 + _c] ** 2 for _c in range(3)) ** 0.5 < 1.35
                                        for _r in range(3)
                                    )
                                    if _rot_ok:
                                        _fdiff = sum(abs(_ffs[9 + _j] - pos[_j]) for _j in range(3))
                                        _all_hits.append((_fdiff, _foff, list(_ffs), _cand))
                                except Exception:
                                    pass
                                _fstart = _foff_base + 1
                            if _all_hits:
                                break  # found at least one via this candidate — stop trying others

                        # Fallback: scan binary for exact position float bytes + valid rotation
                        if not _all_hits:
                            try:
                                _pos_b = struct.pack('<3f', pos[0], pos[1], pos[2])
                                _fscan = 0
                                while True:
                                    _poff = _p3d_bin.find(_pos_b, _fscan)
                                    if _poff < 0:
                                        break
                                    _moff = _poff - 36  # 9 rot floats (36 bytes) before position
                                    if _moff >= 0:
                                        try:
                                            _mfs = struct.unpack_from('<12f', _p3d_bin, _moff)
                                            _rot_ok = all(
                                                0.7 < sum(_mfs[_r * 3 + _c] ** 2 for _c in range(3)) ** 0.5 < 1.35
                                                for _r in range(3)
                                            )
                                            if _rot_ok:
                                                _all_hits.append((0.0, _moff, list(_mfs), 'float_scan'))
                                        except Exception:
                                            pass
                                    _fscan = _poff + 1
                            except Exception:
                                pass

                        if _all_hits:
                            _all_hits.sort(key=lambda _h: _h[0])
                            _best = _all_hits[0]
                            _found_by = _best[3]
                            _prx_entry['_file_offset'] = _best[1]
                            _prx_entry['_file_orig_floats'] = _best[2]
                            # All occurrences within 1 m = same proxy in different LODs
                            _close_offs = [_h[1] for _h in _all_hits if _h[0] < 1.0]
                            _prx_entry['_file_offsets_all'] = _close_offs
                            _dbg(f'proxy[{i}] found via "{_found_by}" off={_best[1]} diff={_best[0]:.4f} '
                                 f'lod_hits={len(_close_offs)} '
                                 f'file_pos=({_best[2][9]:.3f},{_best[2][10]:.3f},{_best[2][11]:.3f}) '
                                 f'dll=({pos[0]:.3f},{pos[1]:.3f},{pos[2]:.3f})')
                        else:
                            _dbg(f'proxy[{i}] NOT FOUND. tried: {_candidates} + float_scan')
                        proxies.append(_prx_entry)
        except Exception as _pe:
            _dbg(f'proxy read WARN: {_pe}')

        return pts, uvs, nrms, nf, tex_hint, face_mats, mat_list, proxies
    except Exception as e:
        _dbg(f'_load_via_dll EXCEPTION: {e}')
        if '--p3d-subproc' in sys.argv:
            import traceback as _tb
            _tb.print_exc(file=sys.stderr)
            print(f'[subproc] _load_via_dll EXCEPTION: {e!r}', file=sys.stderr, flush=True)
        return None, None, None, 0, '', [], [], []


def _try_faces(data, start, n, max_v):
    """Парсит n face из бинарника.
    Возвращает (tris, count, face_map, end_off).
    end_off — смещение сразу после последнего фейса."""
    tris     = []
    face_map = []
    off = start
    for fi in range(n):
        if off >= len(data): return tris, fi, face_map, off
        nv = data[off]; off += 1
        if nv not in (3, 4): return tris, fi, face_map, off
        if off + nv * 2 > len(data): return tris, fi, face_map, off
        vs = [struct.unpack_from('<H', data, off + j*2)[0] for j in range(nv)]
        off += nv * 2
        if any(v >= max_v for v in vs): return tris, fi, face_map, off
        t0 = len(tris)
        tris.append(vs[:3])
        if nv == 4: tris.append([vs[0], vs[2], vs[3]])
        face_map.append((t0, len(tris)))
    return tris, n, face_map, off


_CACHE_DIR     = os.path.join(_EXE_DIR, 'cache')
_TEX_CACHE_DIR = os.path.join(_CACHE_DIR, 'tex')   # кэш декодированных текстур (.npy)
_IDX_CACHE_DIR = os.path.join(_CACHE_DIR, 'idx')   # кэш индекса PAA файлов (.json)


def _tex_cache_key(path):
    """Ключ кэша текстуры: md5(абс_путь + размер + mtime)."""
    try:
        st = os.stat(path)
        raw = f'{os.path.abspath(path)}_{st.st_size}_{int(st.st_mtime_ns)}'
        return hashlib.md5(raw.encode('utf-8')).hexdigest()
    except Exception:
        return None


def read_paa_cached(path):
    """Читает PAA с диск-кэшем декодированного numpy-массива."""
    key = _tex_cache_key(path)
    if key:
        npy_path = os.path.join(_TEX_CACHE_DIR, key + '.npy')
        if os.path.exists(npy_path):
            try:
                return np.load(npy_path)
            except Exception:
                pass
    rgba = read_paa(path)
    if key:
        try:
            os.makedirs(_TEX_CACHE_DIR, exist_ok=True)
            np.save(npy_path, rgba)
        except Exception:
            pass
    return rgba


def _paa_index_cache_path(scan_key):
    os.makedirs(_IDX_CACHE_DIR, exist_ok=True)
    return os.path.join(_IDX_CACHE_DIR,
                        hashlib.md5(scan_key.encode('utf-8')).hexdigest() + '.json')

def _cache_path(p3d_path):
    """Кэш хранится в cache/ рядом с EXE, имя = хэш абсолютного пути."""
    h = hashlib.md5(os.path.abspath(p3d_path).encode('utf-8')).hexdigest()[:16]
    return os.path.join(_CACHE_DIR, h + '.p3dcache')

def _load_cache(p3d_path, file_size):
    """Возвращает (best_end_off, face_start_off, skip) или None."""
    cp = _cache_path(p3d_path)
    try:
        with open(cp, 'r') as f:
            parts = f.read().split()
        if len(parts) == 4 and int(parts[0]) == file_size:
            return int(parts[1]), int(parts[2]), int(parts[3])
    except Exception:
        pass
    return None

def _save_cache(p3d_path, file_size, best_end_off, face_start_off, skip):
    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        with open(_cache_path(p3d_path), 'w') as f:
            f.write(f'{file_size} {best_end_off} {face_start_off} {skip}')
    except Exception:
        pass


def _uv_split_detect(best_tris, uvs, nf, n_mats, log_lines):
    """Strategy F: определяет границу раздела материалов через UV k-means кластеризацию.
    Предполагает, что грани хранятся последовательно по материалам.
    Возвращает split-индекс (0 = не удалось)."""
    if n_mats < 2 or nf < 20 or not uvs:
        return 0
    try:
        # Вычисляем центр UV для каждой грани
        uc = np.zeros((nf, 2), dtype=np.float32)
        n_uvs = len(uvs)
        for fi in range(min(nf, len(best_tris))):
            tri = best_tris[fi]
            verts = [vi for vi in tri if vi < n_uvs]
            if verts:
                pts_uv = np.array([uvs[vi] for vi in verts], dtype=np.float32)
                uc[fi] = pts_uv.mean(axis=0)

        # k-means 2 кластера, 25 итераций
        c0 = uc[nf // 4].copy()
        c1 = uc[nf * 3 // 4].copy()
        labels = np.zeros(nf, dtype=np.int32)
        for _ in range(25):
            d0 = np.sum((uc - c0) ** 2, axis=1)
            d1 = np.sum((uc - c1) ** 2, axis=1)
            new_labels = (d1 < d0).astype(np.int32)
            if np.array_equal(new_labels, labels):
                break
            labels = new_labels
            m0 = labels == 0
            m1 = labels == 1
            if m0.any():
                c0 = uc[m0].mean(axis=0)
            if m1.any():
                c1 = uc[m1].mean(axis=0)

        # Оба кластера должны иметь ≥5% граней
        n1 = int(labels.sum())
        n0 = nf - n1
        if n0 < nf * 0.05 or n1 < nf * 0.05:
            log_lines.append(f'Strategy F (UV): деградация кластеров n0={n0} n1={n1}')
            return 0

        # Ищем оптимальную границу: [0,s) = один кластер, [s,N) = другой
        cum1 = np.cumsum(labels)          # накопл. кол-во label=1 в [0..i]
        total1 = int(cum1[-1])

        s_range      = np.arange(1, nf)
        cnt1_before  = cum1[s_range - 1]
        cnt1_after   = total1 - cnt1_before
        cnt0_after   = (nf - s_range) - cnt1_after

        # Ориентация A: [0,s)=label-0, [s,N)=label-1
        errors_a = cnt1_before + cnt0_after
        # Ориентация B: [0,s)=label-1, [s,N)=label-0
        cnt0_before  = s_range - cnt1_before
        errors_b     = cnt0_before + cnt1_after

        best_a_idx = int(np.argmin(errors_a))
        best_b_idx = int(np.argmin(errors_b))

        if errors_a[best_a_idx] <= errors_b[best_b_idx]:
            split = best_a_idx + 1
            err   = int(errors_a[best_a_idx])
        else:
            split = best_b_idx + 1
            err   = int(errors_b[best_b_idx])

        confidence = 1.0 - err / nf
        log_lines.append(
            f'Strategy F (UV k-means): split={split}/{nf} '
            f'ошибок={err} уверенность={confidence:.3f}')

        if confidence < 0.80:
            log_lines.append('Strategy F: уверенность <80%, пропуск')
            return 0

        return split

    except Exception as e:
        log_lines.append(f'Strategy F ошибка: {e}')
        return 0


def _kmeans_splits(features, k, n_face):
    """k-means кластеризация с k кластерами и поиск оптимальных split'ов.

    Используется для автодетекта N-материального разделения face'ов.
    Возвращает (splits_list, confidence) где splits_list = [s1, s2, ..., s_{k-1}]
    — позиции границ (отсортированы). Confidence ∈ [0, 1].
    """
    if k < 2 or len(features) < k * 2:
        return [], 0.0
    try:
        n = len(features)
        # Инициализация центроидов: разнести по индексу
        centroids = np.array([features[i * n // k].copy() for i in range(k)], dtype=np.float32)
        labels = np.zeros(n, dtype=np.int32)
        n_iter = 25
        for _ in range(n_iter):
            dists = np.zeros((n, k))
            for ki in range(k):
                dists[:, ki] = np.sum((features - centroids[ki]) ** 2, axis=1)
            new_labels = np.argmin(dists, axis=1).astype(np.int32)
            if np.array_equal(new_labels, labels):
                break
            labels = new_labels
            for ki in range(k):
                mask = labels == ki
                if mask.any():
                    centroids[ki] = features[mask].mean(axis=0)

        # Каждый кластер должен иметь ≥5% face'ов
        counts = np.array([(labels == ki).sum() for ki in range(k)])
        if (counts < n * 0.05).any():
            return [], 0.0

        # Сортируем кластеры по индексу первой face'а (для стабильности)
        first_idx = np.array([np.argmax(labels == ki) if (labels == ki).any() else 0 for ki in range(k)])
        order = np.argsort(first_idx)
        remap = np.zeros(k, dtype=np.int32)
        for new_idx, old_idx in enumerate(order):
            remap[old_idx] = new_idx
        labels_sorted = remap[labels]

        # Найти оптимальные k-1 split'ов: пробуем все возможные позиции,
        # для каждой проверяем ошибку (face неправильного кластера по обе стороны)
        # Это O(n^k-1) — для k=3 это O(n^2)=166K для nf=410 — OK.
        # Для k>4 может быть медленно, ограничим k<=4.
        if k > 4:
            return [], 0.0

        def count_errors(splits_sorted):
            """Возвращает число face'ов которые попали не в свой кластер для данной последовательности splits."""
            err = 0
            prev = 0
            for ki in range(k):
                end = splits_sorted[ki] if ki < len(splits_sorted) else n
                # В face[prev:end) ожидаем все = ki
                err += int((labels_sorted[prev:end] != ki).sum())
                prev = end
            return err

        # Ищем k-1 splits оптимально — перебираем с шагом
        # Шаг: не больше 100 итераций на каждый split
        step = max(1, n // 100)
        # DP: для каждого ki ищем оптимальный splits
        if k == 2:
            best_err = n
            best_splits = [n // 2]
            for s1 in range(step, n, step):
                err = count_errors([s1])
                if err < best_err:
                    best_err = err
                    best_splits = [s1]
        elif k == 3:
            best_err = n
            best_splits = [n // 3, 2 * n // 3]
            for s1 in range(step, n - step, step):
                for s2 in range(s1 + step, n, step):
                    err = count_errors([s1, s2])
                    if err < best_err:
                        best_err = err
                        best_splits = [s1, s2]
        elif k == 4:
            best_err = n
            best_splits = [n // 4, n // 2, 3 * n // 4]
            for s1 in range(step, n - 2 * step, step):
                for s2 in range(s1 + step, n - step, step):
                    for s3 in range(s2 + step, n, step):
                        err = count_errors([s1, s2, s3])
                        if err < best_err:
                            best_err = err
                            best_splits = [s1, s2, s3]
        else:
            return [], 0.0

        confidence = 1.0 - best_err / n
        if confidence < 0.5:
            return [], 0.0
        return best_splits, confidence
    except Exception:
        return [], 0.0


def parse_p3d(path, uv_limit=1.5, edge_mult=50):
    if '--p3d-subproc' in sys.argv:
        print(f'[subproc] parse_p3d start: {path}', file=sys.stderr, flush=True)
    data = open(path, 'rb').read()
    if data[:4] != b'ODOL':
        if '--p3d-subproc' in sys.argv:
            print(f'[subproc] parse_p3d: not ODOL (header={data[:8]!r})', file=sys.stderr, flush=True)
        raise ValueError('Не ODOL формат')

    pts, uvs, nrms, nf, tex_hint, face_mats, mat_list, proxies = _load_via_dll(path)
    if '--p3d-subproc' in sys.argv:
        print(f'[subproc] _load_via_dll returned: pts={"None" if pts is None else len(pts)} uvs={"None" if uvs is None else len(uvs)} nf={nf} mats={len(mat_list)}', file=sys.stderr, flush=True)
    if pts is None:
        raise RuntimeError('DLL не смогла открыть файл')

    pts_arr = np.array(pts, dtype=np.float32)

    # ── Brute-force поиск face-таблицы (с кешем) ───────────────────────────
    best_tris, best_score, best_face_map = None, float('inf'), None
    best_end_off = 0

    cached = _load_cache(path, len(data))
    if cached is not None:
        # Быстрый путь: парсим сразу с закешированного offset
        best_end_off, face_start_off, skip = cached
        parsed, count, fmap, end_off = _try_faces(
            data, face_start_off + skip, nf, len(pts))
        if count == nf:
            best_tris, best_face_map = parsed, fmap
            best_end_off = end_off

    if best_tris is None:
        # Медленный путь: полный перебор
        target = struct.pack('<I', nf)
        off = 0
        best_face_start = 0
        best_skip = 4
        while True:
            idx = data.find(target, off)
            if idx < 0: break
            for skip in range(4, 22, 2):
                parsed, count, fmap, end_off = _try_faces(
                    data, idx + skip, nf, len(pts))
                if count == nf:
                    sample = parsed[::max(1, len(parsed)//500)]
                    score  = sum(np.linalg.norm(pts_arr[t[1]] - pts_arr[t[0]]) +
                                 np.linalg.norm(pts_arr[t[2]] - pts_arr[t[1]])
                                 for t in sample) / max(1, len(sample))
                    if score < best_score:
                        best_score, best_tris, best_face_map = score, parsed, fmap
                        best_end_off  = end_off
                        best_face_start = idx
                        best_skip = skip
            off = idx + 4
        if best_tris is not None:
            _save_cache(path, len(data), best_end_off, best_face_start, best_skip)

    if best_tris is None:
        raise RuntimeError(f'Не удалось найти фейсы (ожидалось {nf})')

    # ── Парсим named selections для назначения материалов ───────────────────
    # Что выяснено из hex-дампа boots_f.p3d:
    #   best_end_off → n_sel(uint32) → [selection...] → skeleton_sels...
    #
    # Структура одного selection (ODOL53):
    #   cstring name         (напр. "boots", "socks")
    #   uint32 n_faces
    #   [0..50 байт неизвестного контента — varies per selection]
    #   uint16[n_faces] face_indices
    #
    # Алгоритм: после нахождения name + uint32(n), пробуем skip=0..50
    # и ищем массив uint16[n] с разнообразными значениями < nf.

    face_mat_from_sel = [''] * nf
    n_mats = len(mat_list)
    log_lines = [f'face_data_end={best_end_off}  nf={nf}  n_mats={n_mats}']
    found_tex_idx = False

    def _is_valid_face_array(buf, off, n, nf_total):
        """Проверяем: n штук uint16 начиная с off — валидные face-индексы?"""
        end = off + n * 2
        if end > len(buf): return False
        arr = struct.unpack_from(f'<{n}H', buf, off)
        if any(v >= nf_total for v in arr): return False
        # Должны быть разнообразными (не одно и то же значение)
        mn, mx = min(arr), max(arr)
        return (mx - mn) >= max(n // 4, 1)

    # ── Поиск named selections после best_end_off ────────────────────────────
    try:
        n_sel_hdr = struct.unpack_from('<I', data, best_end_off)[0]
        log_lines.append(f'n_sel_hdr={n_sel_hdr} at {best_end_off}')

        # Собираем все short-строки после best_end_off в пределах 200KB
        scan_end = min(len(data), best_end_off + 200_000)
        found_sels = []  # list of (name, face_indices)

        pos = best_end_off + 4
        while pos < scan_end and len(found_sels) < 64:
            nul = data.find(b'\x00', pos, scan_end)
            if nul < 0: break
            seg = data[pos:nul]
            str_len = len(seg)
            if 1 <= str_len <= 64:
                try:
                    name = seg.decode('latin-1')
                    printable = all(32 <= ord(c) < 127 for c in name)
                except Exception:
                    printable = False

                if printable and nul + 5 <= len(data):
                    n_faces_cand = struct.unpack_from('<I', data, nul + 1)[0]
                    if 1 <= n_faces_cand <= nf:
                        # Пробуем skip=0..50 для поиска face-индексов
                        base = nul + 1 + 4  # сразу после uint32
                        found = False
                        for skip in range(0, 52):
                            arr_off = base + skip
                            if _is_valid_face_array(data, arr_off, n_faces_cand, nf):
                                arr = list(struct.unpack_from(
                                    f'<{n_faces_cand}H', data, arr_off))
                                log_lines.append(
                                    f'  sel "{name}" n={n_faces_cand} '
                                    f'skip={skip} first={arr[0]} last={arr[-1]}')
                                found_sels.append((name, arr))
                                found = True
                                break
                        if not found:
                            log_lines.append(
                                f'  str "{name}" n={n_faces_cand} '
                                f'→ no valid face array found')
            pos = nul + 1

        log_lines.append(f'found_sels count={len(found_sels)}')

        # ── Сопоставление: selection name → material ─────────────────────────
        # Стратегия 1: имя selection входит в basename материала (или наоборот)
        def _match_name_to_mat(sel_name, mat_list):
            sl = sel_name.lower()
            for mat in mat_list:
                mb = os.path.basename(mat.replace('\\', os.sep)).lower()
                # убираем суффиксы _co, _nohq и расширение
                mb_base = mb.split('_')[0]
                if sl == mb_base or sl in mb or mb_base in sl:
                    return mat
            return None

        used_mats = set()
        for sel_name, fidxs in found_sels:
            mat = _match_name_to_mat(sel_name, mat_list)
            if mat and mat not in used_mats:
                for fi in fidxs:
                    if fi < nf:
                        face_mat_from_sel[fi] = mat
                used_mats.add(mat)
                log_lines.append(f'  ASSIGNED "{sel_name}" → "{mat}" ({len(fidxs)} faces)')
                found_tex_idx = True

        # Стратегия 2: если не нашли по имени — матчим по порядку
        # (sel[0]→mat[0], sel[1]→mat[1]...) только для sel кол-во = n_mats
        if not found_tex_idx and len(found_sels) == n_mats:
            log_lines.append('Fallback: match by ORDER')
            for i, (sel_name, fidxs) in enumerate(found_sels[:n_mats]):
                mat = mat_list[i]
                for fi in fidxs:
                    if fi < nf:
                        face_mat_from_sel[fi] = mat
                log_lines.append(f'  ORDER sel[{i}] "{sel_name}" → "{mat}" ({len(fidxs)} faces)')
            found_tex_idx = True

    except Exception as e:
        log_lines.append(f'Parser exception: {e}')

    # ── Мост: если Strategy A (DLL) дала face_mats — используем их напрямую ──
    # Санити-чек: требуем ≥75% граней заполнены И каждый из n_mats материалов
    # встречается хотя бы у 5% граней (защита от garbage-read где всё = mat[0])
    if not found_tex_idx and n_mats >= 2:
        dll_filled = sum(1 for m in face_mats if m)
        if dll_filled >= nf * 3 // 4:
            mat_counts = {}
            for m in face_mats:
                if m: mat_counts[m] = mat_counts.get(m, 0) + 1
            min_share = nf * 5 // 100   # минимум 5% граней на материал
            all_mats_present = all(
                mat_counts.get(mat, 0) >= min_share
                for mat in mat_list
            )
            if all_mats_present:
                for fi in range(nf):
                    if face_mats[fi]:
                        face_mat_from_sel[fi] = face_mats[fi]
                found_tex_idx = True
                log_lines.append(
                    f'Strategy A bridge: filled={dll_filled}/{nf} '
                    f'counts={mat_counts} → Strategy E skipped')
            else:
                log_lines.append(
                    f'Strategy A bridge REJECTED: counts={mat_counts} '
                    f'(some mat <5% = likely garbage read)')

    # ── Стратегия F: UV k-means (приоритет перед E, когда A не сработала) ──────
    _raw_initial_split = 0
    if not found_tex_idx and n_mats >= 2:
        uv_split = _uv_split_detect(best_tris, uvs, nf, n_mats, log_lines)
        if uv_split > 0:
            _raw_initial_split = uv_split
            for fi in range(nf):
                mi = 1 if fi < uv_split else 0
                face_mat_from_sel[fi] = mat_list[min(mi, n_mats - 1)]
            found_tex_idx = True
            log_lines.append(f'Strategy F applied: split={uv_split}/{nf}')

    # ── Стратегия E: binary section table face-range (fallback после F) ────────
    if not found_tex_idx and n_mats >= 2:
        try:
            for probe_off in range(4, 64, 4):
                if best_end_off + probe_off + 4 > len(data):
                    break
                fc_cand = struct.unpack_from('<I', data, best_end_off + probe_off)[0]
                split_cand = fc_cand
                if 0 < split_cand < nf:
                    log_lines.append(
                        f'Strategy E: fc_at+{probe_off}={fc_cand} '
                        f'split={split_cand}/{nf}')
                    _raw_initial_split = split_cand
                    for fi in range(nf):
                        mi = 1 if fi < split_cand else 0
                        face_mat_from_sel[fi] = mat_list[min(mi, n_mats - 1)]
                    found_tex_idx = True
                    log_lines.append(
                        f'  => face_mats SET via binary section range '
                        f'({split_cand} × mat[1], {nf-split_cand} × mat[0])')
                    break
        except Exception as e:
            log_lines.append(f'Strategy E error: {e}')

    if not found_tex_idx:
        log_lines.append('FAILED — all faces get fallback mat')

    # Запись лога named selections
    model_name = os.path.splitext(os.path.basename(path))[0]
    _write_log(f'namedsel_{model_name}.txt', log_lines)

    # ── Порог отсечения вырожденных треугольников ──────────────────────────
    sample  = best_tris[::max(1, len(best_tris)//1000)]
    lengths = [np.linalg.norm(pts_arr[t[i]] - pts_arr[t[(i+1)%3]])
               for t in sample for i in range(3)]
    base_limit = float(np.median(lengths))
    # v2.8: для больших edge_mult (>=500) пользователь явно хочет видеть ВСЕ грани.
    # Используем max edge length из sample × небольшой запас, чтобы покрыть outliers.
    # Раньше я ставил min — это ОШИБКА: вырожденные короткие грани дают крошечный
    # лимит, и почти все нормальные грани отбрасываются.
    if float(edge_mult) >= 500:
        max_len = float(np.max(lengths)) if lengths else base_limit
        # robust_limit = max × 2 (запас на outliers)
        robust_limit = max_len * 2.0
    else:
        # Robust estimate: max(median, 5th_percentile) — обычное поведение
        p5 = float(np.percentile(lengths, 5)) if lengths else base_limit
        robust_limit = max(base_limit, p5 * 0.5)
    limit      = robust_limit * float(edge_mult)

    # UV-массив для фильтрации UV-шовных треугольников
    uvs_arr = np.array(uvs, dtype=np.float32) if uvs and len(uvs) == len(pts) else None

    def is_good(tri):
        # Отсечение по длине рёбер в 3D
        if not all(np.linalg.norm(pts_arr[tri[i]] - pts_arr[tri[(i+1)%3]]) < limit
                   for i in range(3)):
            return False
        # Отсечение UV-шовных треугольников через bbox всего треугольника
        # (ловит случаи когда два ребра маленькие, а суммарный размах большой)
        if uvs_arr is not None:
            us = [float(uvs_arr[tri[i]][0]) for i in range(3)]
            vs = [float(uvs_arr[tri[i]][1]) for i in range(3)]
            if max(us) - min(us) > uv_limit or max(vs) - min(vs) > uv_limit:
                return False
        return True

    # ── Группировка треугольников по материалу ─────────────────────────────
    mat_groups_dict = {}
    all_tris = []
    covered_faces = set()  # трек какие face indices попали в named selections

    for fi, (t_start, t_end) in enumerate(best_face_map):
        mat = face_mat_from_sel[fi] if fi < len(face_mat_from_sel) else ''
        # Fallback: если selections не нашли — используем gtex результаты
        if not mat:
            mat = face_mats[fi] if fi < len(face_mats) else ''
        key = mat.lower().replace('\\', '/').strip()
        if key not in mat_groups_dict:
            mat_groups_dict[key] = {'path': mat, 'tris': []}
        for ti in range(t_start, t_end):
            covered_faces.add(ti)
            if ti < len(best_tris):
                tri = best_tris[ti]
                if is_good(tri):
                    mat_groups_dict[key]['tris'].append(tri)
                    all_tris.append(tri)

    # v2.8 fix: если named selections покрывают не ВСЕ faces (covered_faces < nf),
    # добавляем остальные в default mat_group. Иначе они теряются.
    if len(covered_faces) < nf:
        uncovered_count = 0
        default_key = '__uncovered_default__'
        for ti in range(nf):
            if ti not in covered_faces:
                uncovered_count += 1
                if ti < len(best_tris):
                    tri = best_tris[ti]
                    if is_good(tri):
                        if default_key not in mat_groups_dict:
                            mat_groups_dict[default_key] = {'path': '', 'tris': []}
                        mat_groups_dict[default_key]['tris'].append(tri)
                        all_tris.append(tri)
        if uncovered_count > 0:
            print(f'[v2.8] parse_p3d: {os.path.basename(path)} — named selections покрыли {len(covered_faces)}/{nf} faces, '
                  f'добавлено {uncovered_count} uncovered в default group')

    mat_groups = [(v['path'], v['tris'])
                  for v in mat_groups_dict.values() if v['tris']]

    # Сырые данные для ручного инструмента переназначения материалов
    _raw = {
        'face_map':      best_face_map,
        'proxies':       proxies,
        'mat_list':      mat_list,
        'nf':            nf,
        'n_mats':        n_mats,
        'best_tris':     best_tris,
        'pts':           pts,
        'nrms':          nrms,
        'uvs':           uvs,
        'pts_arr':       pts_arr,
        'uvs_arr':       uvs_arr,
        'limit':         limit,
        'base_limit':    base_limit,
        'edge_mult':     float(edge_mult),
        'face_mats':     face_mats,
        'face_mat_orig': list(face_mat_from_sel),
        'initial_split': _raw_initial_split,
        'uv_limit':      uv_limit,
        # v2.8: parse diagnostics (для отладки потери граней)
        'diag': {
            'dll_pts':   len(pts),
            'dll_nf':    nf,
            'best_tris': len(best_tris),
            'covered':   len(covered_faces),
            'good_tris': len(all_tris),
            'n_groups':  len(mat_groups),
            'base_limit': base_limit,
            'robust_limit': robust_limit,
            'edge_mult': float(edge_mult),
            'uv_limit':  uv_limit,
        },
    }
    return pts, uvs, nrms, all_tris, tex_hint, mat_groups, _raw


def _parse_named_selections(data, mat_list, n_faces, log_path=None):
    """Ищет named-selection данные в raw P3D binary.
    Для каждого материала находит массив весов uint8[n_faces].
    Возвращает dict {mat_path: [face_indices]} или пустой dict."""
    result    = {}
    log_lines = [f'_parse_named_selections  n_faces={n_faces}  mats={len(mat_list)}']

    for mat in mat_list:
        variants = [mat, mat.replace('\\', '/'), mat.replace('/', '\\'),
                    os.path.basename(mat.replace('\\', os.sep))]
        found_pos = -1; found_enc = ''; found_b = b''
        for var in variants:
            for enc in ('mbcs', 'utf-8', 'latin-1'):
                try:
                    b   = var.encode(enc)
                    pos = data.find(b)
                    if pos >= 0:
                        found_pos, found_enc, found_b = pos, enc, b
                        break
                except Exception:
                    pass
            if found_pos >= 0:
                break

        log_lines.append(f'\n  mat: "{mat}"')
        log_lines.append(f'  found at: {found_pos}  enc={found_enc}')

        if found_pos < 0:
            log_lines.append('  NOT FOUND in binary')
            continue

        # Контекст вокруг найденной строки
        cs = max(0, found_pos - 16)
        ce = min(len(data), found_pos + len(found_b) + 64)
        log_lines.append('  context: ' + ' '.join(f'{x:02x}' for x in data[cs:ce]))

        # Ищем n_faces (uint32 LE) в диапазоне ±256 байт
        target_nf    = struct.pack('<I', n_faces)
        search_start = max(0, found_pos - 128)
        search_end   = min(len(data), found_pos + len(found_b) + 256)
        sel_pos = search_start
        while True:
            idx = data.find(target_nf, sel_pos, search_end)
            if idx < 0: break
            after = idx + 4
            if after + n_faces <= len(data):
                weights  = data[after:after + n_faces]
                non_zero = sum(1 for w in weights if w != 0)
                log_lines.append(f'  nf@{idx}: non_zero={non_zero}/{n_faces}')
                if 0 < non_zero < n_faces:
                    result[mat] = [fi for fi, w in enumerate(weights) if w != 0]
                    log_lines.append(f'  -> selected {len(result[mat])} faces')
                    break
            sel_pos = idx + 1

        if mat not in result:
            log_lines.append('  -> no valid selection')

    if log_path:
        try:
            open(log_path, 'w', encoding='utf-8').write('\n'.join(log_lines))
        except Exception:
            pass

    return result


def _compute_tangents(pts, uvs, tris, nrms):
    n  = len(pts)
    pa = np.array(pts,  dtype=np.float32)
    na = np.array(nrms, dtype=np.float32)
    if not uvs or len(uvs) < n:
        return np.zeros((n, 3), dtype=np.float32)
    ua  = np.array(uvs,  dtype=np.float32)
    tan = np.zeros((n, 3), dtype=np.float32)
    ta    = np.array(tris, dtype=np.int32)
    valid = np.all(ta < n, axis=1)
    ta    = ta[valid]
    if not len(ta): return tan
    i0, i1, i2 = ta[:,0], ta[:,1], ta[:,2]
    e1  = pa[i1] - pa[i0];  e2  = pa[i2] - pa[i0]
    uv1 = ua[i1] - ua[i0];  uv2 = ua[i2] - ua[i0]
    denom = uv1[:,0]*uv2[:,1] - uv2[:,0]*uv1[:,1]
    safe  = np.abs(denom) > 1e-9
    r     = np.where(safe, 1.0 / np.where(safe, denom, 1.0), 0.0)[:,np.newaxis]
    T     = r * (uv2[:,1:2] * e1 - uv1[:,1:2] * e2)
    np.add.at(tan, i0, T); np.add.at(tan, i1, T); np.add.at(tan, i2, T)
    dot  = np.sum(na * tan, axis=1, keepdims=True)
    tan  = tan - dot * na
    norm = np.linalg.norm(tan, axis=1, keepdims=True)
    tan  = np.where(norm > 1e-8, tan / np.where(norm > 1e-8, norm, 1.0), na)
    return tan.astype(np.float32)


def export_obj(path, pts, uvs, nrms, tris, tex_png=None):
    """Совместимая версия — все треугольники под одним материалом."""
    mat_groups = [('default', tris)]
    tex_pngs = {0: tex_png} if tex_png else {}
    export_obj_multi(path, pts, uvs, nrms, mat_groups, tex_pngs)


# ── Subprocess entry point (v2.3) ─────────────────────────────────────────
# Запускается как отдельный python.exe процесс для каждой загрузки P3D.
# Принимает JSON-команду через stdin, пишет результат в файл (numpy arrays → .npy).
# Это позволяет: (a) грузить несколько моделей параллельно, (b) жёстко отменять
# через process.terminate() (DLL state изолирован).

_P3D_SUBPROC_PROTOCOL_VERSION = 1


def _p3d_subprocess_main():
    """Entry point для subprocess. Читает JSON из stdin, парсит P3D, пишет .npy.

    Формат команды (stdin JSON):
      {
        "cmd": "parse",
        "p3d_path": "...",
        "uv_limit": 1.5,
        "edge_mult": 50,
        "out_prefix": "..."   # путь к .json с метаданными; .npy файлы пишутся рядом
      }

    Формат результата:
      {out_prefix}.json: {
        "ok": true,
        "pts_count": ..., "tris_count": ..., "n_mats": ..., "n_proxies": ...,
        "mat_paths": [...],
        "tex_hint": ...,
        "files": {  # относительные пути к .npy файлам
          "pts": "...npy",
          "uvs": "...npy"  // или null
          "nrms": "...npy",
          "tris": "...npy",
          "mat_tris": ["...npy", "...npy", ...]  // по одному на каждую группу
          "proxies": "...npy" // или null
        }
      }

    При ошибке: {"ok": false, "error": "..."}
    """
    import sys as _sys
    import json as _json
    try:
        raw = _sys.stdin.read()
        cmd = _json.loads(raw)
        if cmd.get('cmd') != 'parse':
            print(_json.dumps({'ok': False, 'error': 'unknown cmd'}))
            return
        p3d_path  = cmd['p3d_path']
        uv_limit  = float(cmd.get('uv_limit', 1.5))
        edge_mult = float(cmd.get('edge_mult', 50))
        out_prefix = cmd['out_prefix']

        pts, uvs, nrms, tris, tex_hint, mat_groups, raw = parse_p3d(
            p3d_path, uv_limit=uv_limit, edge_mult=edge_mult)

        # Сериализуем numpy массивы в .npy файлы
        files = {}
        def _save(arr, name):
            p = f'{out_prefix}_{name}.npy'
            try:
                np.save(p, np.asarray(arr))
                return p
            except Exception:
                return None

        files['pts']  = _save(pts, 'pts')
        files['nrms'] = _save(nrms, 'nrms')
        files['tris'] = _save(tris, 'tris')
        files['uvs']  = _save(uvs, 'uvs') if uvs else None
        if mat_groups:
            files['mat_tris'] = [_save(gtris, f'mat{i}') for i, (_, gtris) in enumerate(mat_groups)]
        else:
            files['mat_tris'] = []
        proxies = raw.get('proxies', [])
        files['proxies'] = _save(proxies, 'proxies') if proxies else None

        # proxies могут содержать list-поля (pos/mat) — numpy их не съест напрямую.
        # На практике они list/dict, поэтому отдельная обработка:
        # ^ примечание: если proxies = list of dicts, np.save сохранит как object[],
        # что безопасно. Если там вложенные list'ы — могут быть проблемы.
        # Для надёжности: пишем proxies как pickle
        if proxies:
            try:
                import pickle as _pickle
                pkl_p = f'{out_prefix}_proxies.pkl'
                with open(pkl_p, 'wb') as _pf:
                    _pickle.dump(proxies, _pf)
                files['proxies'] = pkl_p
            except Exception:
                files['proxies'] = None

        # Дополнительные данные для Viewport (face_map, mat_list, face_mats, base_limit).
        # Без них _recompute_mats и _update_mat_panel падают с KeyError.
        # Сохраняем одним pickle (быстрее и компактнее чем JSON).
        try:
            import pickle as _pickle2
            extra_pkl = f'{out_prefix}_extra.pkl'
            extra_data = {
                'face_map':       raw.get('face_map', []),
                'mat_list':       raw.get('mat_list', []),
                'face_mats':      raw.get('face_mats', []),
                'base_limit':     raw.get('base_limit', 1.0),
                'best_tris':      raw.get('best_tris', []),
                'pts_arr':        raw.get('pts_arr', []),
                'uvs_arr':        raw.get('uvs_arr', []),
                'initial_split':  raw.get('initial_split', 0),
                'diag':           raw.get('diag', {}),  # v2.8: parse diagnostics
            }
            with open(extra_pkl, 'wb') as _ef:
                _pickle2.dump(extra_data, _ef, protocol=_pickle2.HIGHEST_PROTOCOL)
            files['extra'] = extra_pkl
        except Exception as _ex_e:
            _dbg(f'extra pickle save failed: {_ex_e}')
            files['extra'] = None

        result = {
            'ok': True,
            'protocol': _P3D_SUBPROC_PROTOCOL_VERSION,
            'p3d_path': p3d_path,
            'pts_count':  len(pts) if pts is not None else 0,
            'tris_count': len(tris) if tris is not None else 0,
            'nrms_count': len(nrms) if nrms is not None else 0,
            'uvs_count':  len(uvs) if uvs is not None else 0,
            'n_mats':  len(mat_groups) if mat_groups else 0,
            'mat_paths': [m for m, _ in (mat_groups or [])],
            'tex_hint': tex_hint or '',
            'n_proxies': len(proxies),
            'raw_initial_split': raw.get('initial_split', 0),
            'files': files,
            # v2.8: parse diagnostics (для диагностики потери граней)
            'diagnostics': raw.get('diag', {}),
        }
        with open(out_prefix + '.json', 'w', encoding='utf-8') as _f:
            _json.dump(result, _f, ensure_ascii=False)
        print(_json.dumps({'ok': True, 'result_json': out_prefix + '.json'}))
    except Exception as e:
        try:
            err = {'ok': False, 'error': f'{type(e).__name__}: {e}'}
            print(_json.dumps(err))
        except Exception:
            pass


def export_obj_multi(path, pts, uvs, nrms, mat_groups, tex_pngs=None):
    """Экспорт OBJ с отдельными usemtl для каждой группы материалов.

    mat_groups: [(mat_name_or_path, list_of_tris), ...]
    tex_pngs:   {gi: png_path, ...}  — путь к PNG для каждой группы
    """
    if tex_pngs is None:
        tex_pngs = {}
    name = os.path.splitext(os.path.basename(path))[0]
    with open(path, 'w', encoding='utf-8') as f:
        f.write(f'# P3D Viewer {VERSION}\no {name}\nmtllib {name}.mtl\n')
        for v in pts:  f.write(f'v  {v[0]:.6f} {v[1]:.6f} {v[2]:.6f}\n')
        for u in uvs:  f.write(f'vt {u[0]:.6f} {1.0-u[1]:.6f}\n')
        for n in nrms: f.write(f'vn {n[0]:.6f} {n[1]:.6f} {n[2]:.6f}\n')
        for gi, (mat_name, group_tris) in enumerate(mat_groups):
            f.write(f'usemtl mat_{gi}\n')
            for t in group_tris:
                i, j, k = t[0]+1, t[1]+1, t[2]+1
                f.write(f'f {i}/{i}/{i} {j}/{j}/{j} {k}/{k}/{k}\n')
    mtl_lines = []
    for gi in range(len(mat_groups)):
        mtl_lines += [f'newmtl mat_{gi}', 'Ka 0.2 0.2 0.2',
                      'Kd 0.8 0.8 0.8', 'Ks 0.0 0.0 0.0']
        if gi in tex_pngs and tex_pngs[gi]:
            mtl_lines.append(f'map_Kd {os.path.basename(tex_pngs[gi])}')
        mtl_lines.append('')
    with open(os.path.splitext(path)[0] + '.mtl', 'w', encoding='utf-8') as f:
        f.write('\n'.join(mtl_lines))


# ── Заглушка для прокси (бокс 8×8×8 см) ────────────────────────────────────
_BOX_PTS = np.array([
    [-0.04,-0.04,-0.04], [0.04,-0.04,-0.04], [0.04,0.04,-0.04], [-0.04,0.04,-0.04],
    [-0.04,-0.04, 0.04], [0.04,-0.04, 0.04], [0.04,0.04, 0.04], [-0.04,0.04, 0.04],
], dtype=np.float32)
_BOX_TRIS = [
    [0,2,1],[0,3,2],[4,5,6],[4,6,7],
    [0,1,5],[0,5,4],[2,3,7],[2,7,6],
    [0,4,7],[0,7,3],[1,2,6],[1,6,5],
]
_PLACEHOLDER_MESH = {'pts': _BOX_PTS, 'tris': _BOX_TRIS, 'placeholder': True}


def _mat3_mul(a, b):
    """Умножение двух матриц 3×3 (column-major, 9 элементов)."""
    res = [0.0] * 9
    for col in range(3):
        for row in range(3):
            for k in range(3):
                res[col*3+row] += a[k*3+row] * b[col*3+k]
    return res


def _rot3(axis, angle_deg):
    """Матрица вращения 3×3 вокруг оси (column-major)."""
    import math
    a = math.radians(angle_deg)
    c, s = math.cos(a), math.sin(a)
    if axis == 'x':   return [1,0,0,  0,c,-s,  0,s,c]
    elif axis == 'y': return [c,0,s,  0,1,0,  -s,0,c]
    else:             return [c,-s,0,  s,c,0,   0,0,1]


# ── OpenGL Viewport ────────────────────────────────────────────────────────

class Viewport(OpenGLFrame):
    def __init__(self, master, **kw):
        super().__init__(master, **kw)
        # Общие VBO (вершины / нормали / UV / касательные)
        self.vbo_v = self.vbo_n = self.vbo_uv = self.vbo_tan = None
        # Группы материалов: список dict с ibo / tex / нормаль
        self.mesh_groups = []
        # Если load_mesh вызван до инициализации GL — сохраняем данные
        self._pending_mesh = None
        self._pending_mesh_cb = None  # вызывается после применения pending mesh
        self.tri_count   = 0
        # v3.1.8: cache — какой model idx уже загружен в VBO/IBO. Skip load_mesh если совпадает.
        self._mesh_loaded_for_idx = None
        # v3.1.8: cache — для какой модели уже забинжены текстуры. Skip _bind_textures_for_model если совпадает.
        self._tex_bound_for_idx = None
        # v3.1.7: per-material alpha state — dict {mat_path: True}
        # OLD: self.use_alpha = False (deprecated — global toggle заменён на per-material)
        self._alpha_mats = {}  # sync с app_settings['alpha_mats'] в _load_settings / при toggle
        self.lighting    = True
        self.brightness  = 1.0
        self.rot_x = 20.0; self.rot_y = 30.0; self.zoom = 1.0
        self.pan_x = 0.0; self.pan_y = 0.0
        self.proxies      = []
        self.show_proxies = True
        self.selected_proxy   = -1
        self._proxy_screen_cache = []
        self.on_proxy_select    = None      # callback(idx)
        self.on_proxy_transform = None      # callback(idx) after move/rotate
        self.proxy_meshes     = {}
        self._lx = self._ly = 0; self._drag = False
        self._center = (0.0, 0.0, 0.0); self._scale = 1.0
        self.prog = None; self.a_tan_loc = -1; self.ul = {}
        self.animate = 1
        # Proxy drag state
        self._proxy_key  = None             # None / 'x'/'y'/'z' — ограничитель оси
        self._proxy_drag = None             # None / 'move' / 'rotate'
        self._proxy_drag_lxy = (0, 0)
        self._last_axis_key = None          # защита от key-repeat
        self._local_space = False           # True = локальные оси прокси
        self.on_camera_mode_change = None   # callback(bool) — зарезервировано
        self.on_axis_change = None          # callback(axis_str_or_None)
        # LMB: если proxy выбран → двигать; иначе → вращать камеру
        self.bind('<ButtonPress-1>',   self._drag_start)
        self.bind('<B1-Motion>',       self._drag_move)
        self.bind('<ButtonRelease-1>', self._drag_end)
        # RMB — вращение прокси
        self.bind('<ButtonPress-3>',   self._rmb_start)
        self.bind('<B3-Motion>',       self._rmb_move)
        self.bind('<ButtonRelease-3>', self._rmb_end)
        self.bind('<MouseWheel>',      lambda e: self._wheel(e))
        # Средняя кнопка — pan (всегда доступен)
        self.bind('<Button-2>',  lambda e: self._pan_start(e))
        self.bind('<B2-Motion>', lambda e: self._pan_move(e))
        self.bind('<Escape>', self._deselect_proxy)
        # Клавиши оси — привязываем к toplevel
        self.bind('<Map>', self._bind_root_keys, add=True)

    def _compile_shader(self, src, kind):
        sh = glCreateShader(kind)
        glShaderSource(sh, src)
        glCompileShader(sh)
        if not glGetShaderiv(sh, GL_COMPILE_STATUS):
            raise RuntimeError(glGetShaderInfoLog(sh).decode(errors='replace'))
        return sh

    def initgl(self):
        glEnable(GL_DEPTH_TEST); glDisable(GL_CULL_FACE)
        self.bg_color = (0.11, 0.11, 0.14)
        glClearColor(*self.bg_color, 1.0)
        try:
            vs = self._compile_shader(VERT_SRC, GL_VERTEX_SHADER)
            fs = self._compile_shader(FRAG_SRC, GL_FRAGMENT_SHADER)
            p  = glCreateProgram()
            glAttachShader(p, vs); glAttachShader(p, fs)
            glLinkProgram(p)
            if not glGetProgramiv(p, GL_LINK_STATUS):
                raise RuntimeError(glGetProgramInfoLog(p).decode(errors='replace'))
            self.prog      = p
            self.a_tan_loc = glGetAttribLocation(p, 'a_tangent')
            for nm in ('u_has_tex', 'u_has_nrm', 'u_lit', 'u_alpha', 'u_diff', 'u_nrm', 'u_brightness', 'u_mirror_uv', 'u_flip_x', 'u_flip_y', 'u_flip_z'):
                self.ul[nm] = glGetUniformLocation(p, nm)
        except Exception as e:
            print(f'Shader error: {e}'); self.prog = None
        # Применить меш отложенный до инициализации GL
        if self._pending_mesh is not None:
            _m = self._pending_mesh
            _cb = self._pending_mesh_cb
            self._pending_mesh = None
            self._pending_mesh_cb = None
            self.load_mesh(*_m)
            if _cb:
                try: _cb()
                except Exception: pass

    # ── Render ──────────────────────────────────────────────────────────────

    def redraw(self):
        w, h = self.winfo_width(), self.winfo_height()
        if w < 1 or h < 1: return
        glViewport(0, 0, w, h)
        if hasattr(self, 'bg_color'): glClearColor(*self.bg_color, 1.0)
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        glMatrixMode(GL_PROJECTION); glLoadIdentity()
        gluPerspective(45.0, w/h, 0.001, 1000.0)
        glMatrixMode(GL_MODELVIEW); glLoadIdentity()
        gluLookAt(0, 0, 3.5/self.zoom, 0, 0, 0, 0, 1, 0)
        glTranslatef(self.pan_x, self.pan_y, 0.0)
        glRotatef(self.rot_x, 1, 0, 0)
        glRotatef(self.rot_y, 0, 1, 0)
        glScalef(self._scale, self._scale, self._scale)
        cx, cy, cz = self._center
        glTranslatef(-cx, -cy, -cz)
        if not self.mesh_groups: return
        glDepthMask(GL_TRUE)
        # v3.1.7: alpha blending перенесён per-group внутрь _draw_shader (per-material настройка)
        glDisable(GL_BLEND)  # safety: default OFF до per-group решения в _draw_shader
        if self.prog: self._draw_shader()
        else:          self._draw_fixed()
        glDisable(GL_BLEND); glDepthMask(GL_TRUE)
        self._draw_proxies()
        try:
            self._draw_proxy_meshes()
        except Exception:
            pass

    def _draw_shader(self):
        glUseProgram(self.prog)
        ul = self.ul
        # Глобальные юниформы
        if ul.get('u_lit',        -1) >= 0: glUniform1i(ul['u_lit'],        int(self.lighting))
        # v3.1.7: u_alpha теперь per-group (внутри цикла ниже), убрано глобальное
        # v3.0: mirror UV toggle (default ON)
        if ul.get('u_mirror_uv', -1) >= 0:
            glUniform1i(ul['u_mirror_uv'], int(getattr(self, '_mirror_uv_enabled', True)))
        # v3.0: mesh mirror по осям X/Y/Z
        if ul.get('u_flip_x', -1) >= 0:
            glUniform1i(ul['u_flip_x'], int(getattr(self, '_flip_x_enabled', False)))
        if ul.get('u_flip_y', -1) >= 0:
            glUniform1i(ul['u_flip_y'], int(getattr(self, '_flip_y_enabled', False)))
        if ul.get('u_flip_z', -1) >= 0:
            glUniform1i(ul['u_flip_z'], int(getattr(self, '_flip_z_enabled', False)))
        if ul.get('u_diff',       -1) >= 0: glUniform1i(ul['u_diff'],       0)
        if ul.get('u_nrm',        -1) >= 0: glUniform1i(ul['u_nrm'],        1)
        if ul.get('u_brightness', -1) >= 0: glUniform1f(ul['u_brightness'], self.brightness)
        # Общие вершинные атрибуты
        glEnableClientState(GL_VERTEX_ARRAY)
        glEnableClientState(GL_NORMAL_ARRAY)
        glBindBuffer(GL_ARRAY_BUFFER, self.vbo_v); glVertexPointer(3, GL_FLOAT, 0, None)
        glBindBuffer(GL_ARRAY_BUFFER, self.vbo_n); glNormalPointer(GL_FLOAT, 0, None)
        if self.vbo_uv:
            glEnableClientState(GL_TEXTURE_COORD_ARRAY)
            glBindBuffer(GL_ARRAY_BUFFER, self.vbo_uv)
            glTexCoordPointer(2, GL_FLOAT, 0, None)
        if self.a_tan_loc >= 0 and self.vbo_tan:
            glEnableVertexAttribArray(self.a_tan_loc)
            glBindBuffer(GL_ARRAY_BUFFER, self.vbo_tan)
            glVertexAttribPointer(self.a_tan_loc, 3, GL_FLOAT, GL_FALSE, 0, None)
        # Рисуем каждую группу с её текстурой
        for g in self.mesh_groups:
            if g['tri_count'] == 0: continue
            # v3.1.7: per-group alpha blending (включается если mat_path в _alpha_mats)
            mat_path = g.get('mat_path', '') or ''
            alpha_on = bool(self._alpha_mats.get(mat_path, False))
            if alpha_on:
                glEnable(GL_BLEND)
                glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
            else:
                glDisable(GL_BLEND)
            if ul.get('u_has_tex', -1) >= 0:
                glUniform1i(ul['u_has_tex'], int(g['has_tex']))
            if ul.get('u_has_nrm', -1) >= 0:
                glUniform1i(ul['u_has_nrm'], int(g['has_nrm']))
            if ul.get('u_alpha', -1) >= 0:
                glUniform1i(ul['u_alpha'], int(alpha_on))
            glActiveTexture(GL_TEXTURE0)
            glBindTexture(GL_TEXTURE_2D, g['tex_id'] if (g['has_tex'] and g['tex_id']) else 0)
            glActiveTexture(GL_TEXTURE1)
            glBindTexture(GL_TEXTURE_2D, g['nrm_id'] if (g['has_nrm'] and g['nrm_id']) else 0)
            glActiveTexture(GL_TEXTURE0)
            glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, g['ibo'])
            glDrawElements(GL_TRIANGLES, g['tri_count'] * 3, GL_UNSIGNED_INT, None)
        # Очистка
        glDisableClientState(GL_VERTEX_ARRAY)
        glDisableClientState(GL_NORMAL_ARRAY)
        glDisableClientState(GL_TEXTURE_COORD_ARRAY)
        if self.a_tan_loc >= 0: glDisableVertexAttribArray(self.a_tan_loc)
        glUseProgram(0)

    def _draw_fixed(self):
        if self.lighting:
            glEnable(GL_LIGHTING); glEnable(GL_LIGHT0)
            glLightModeli(GL_LIGHT_MODEL_TWO_SIDE, GL_TRUE)
            glLightModelfv(GL_LIGHT_MODEL_AMBIENT, [0.55, 0.55, 0.55, 1.0])
            glLightfv(GL_LIGHT0, GL_POSITION, [1.0, 2.0, 3.0, 0.0])
            glLightfv(GL_LIGHT0, GL_DIFFUSE,  [0.9, 0.9, 0.9, 1.0])
        else:
            glDisable(GL_LIGHTING)
        glEnableClientState(GL_VERTEX_ARRAY)
        glEnableClientState(GL_NORMAL_ARRAY)
        glBindBuffer(GL_ARRAY_BUFFER, self.vbo_v); glVertexPointer(3, GL_FLOAT, 0, None)
        glBindBuffer(GL_ARRAY_BUFFER, self.vbo_n); glNormalPointer(GL_FLOAT, 0, None)
        for g in self.mesh_groups:
            if g['tri_count'] == 0: continue
            if g['has_tex'] and self.vbo_uv and g['tex_id']:
                glEnable(GL_TEXTURE_2D); glBindTexture(GL_TEXTURE_2D, g['tex_id'])
                glColor3f(1, 1, 1)
                glEnableClientState(GL_TEXTURE_COORD_ARRAY)
                glBindBuffer(GL_ARRAY_BUFFER, self.vbo_uv)
                glTexCoordPointer(2, GL_FLOAT, 0, None)
            else:
                glDisable(GL_TEXTURE_2D); glColor3f(0.53, 0.71, 0.98)
            glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, g['ibo'])
            glDrawElements(GL_TRIANGLES, g['tri_count'] * 3, GL_UNSIGNED_INT, None)
            glDisableClientState(GL_TEXTURE_COORD_ARRAY)
            glDisable(GL_TEXTURE_2D)
        glDisableClientState(GL_VERTEX_ARRAY)
        glDisableClientState(GL_NORMAL_ARRAY)

    def _draw_proxies(self):
        if not self.proxies or not self.show_proxies:
            self._proxy_screen_cache = []
            return
        glDisable(GL_TEXTURE_2D)
        glDisable(GL_LIGHTING)
        glDisable(GL_DEPTH_TEST)

        # Cache screen-space positions for hit-testing
        try:
            _mv   = glGetDoublev(GL_MODELVIEW_MATRIX)
            _proj = glGetDoublev(GL_PROJECTION_MATRIX)
            _vp   = glGetIntegerv(GL_VIEWPORT)
            _can_proj = True
        except Exception:
            _can_proj = False
        self._proxy_screen_cache = []

        AXIS = 0.05
        for i, prx in enumerate(self.proxies):
            px, py, pz = prx['pos']
            m = prx['mat']
            xx, xy, xz = m[0], m[1], m[2]
            yx, yy, yz = m[3], m[4], m[5]
            zx, zy, zz = m[6], m[7], m[8]
            is_sel = (i == self.selected_proxy)
            lw = 3.5 if is_sel else 2.0
            glLineWidth(lw)
            # X axis
            glBegin(GL_LINES)
            glColor3f(1.0, 0.2, 0.2)
            glVertex3f(px, py, pz)
            glVertex3f(px + xx*AXIS, py + xy*AXIS, pz + xz*AXIS)
            glEnd()
            # Y axis
            glBegin(GL_LINES)
            glColor3f(0.2, 1.0, 0.2)
            glVertex3f(px, py, pz)
            glVertex3f(px + yx*AXIS, py + yy*AXIS, pz + yz*AXIS)
            glEnd()
            # Z axis
            glBegin(GL_LINES)
            glColor3f(0.3, 0.5, 1.0)
            glVertex3f(px, py, pz)
            glVertex3f(px + zx*AXIS, py + zy*AXIS, pz + zz*AXIS)
            glEnd()
            # Crosshair center
            D = AXIS * 0.18
            glLineWidth(lw)
            glBegin(GL_LINES)
            glColor3f(1.0 if is_sel else 1.0,
                      1.0 if is_sel else 1.0,
                      0.0 if is_sel else 0.0)
            glVertex3f(px-D, py, pz); glVertex3f(px+D, py, pz)
            glVertex3f(px, py-D, pz); glVertex3f(px, py+D, pz)
            glVertex3f(px, py, pz-D); glVertex3f(px, py, pz+D)
            glEnd()
            # Selection ring (circle of lines around center)
            if is_sel:
                import math as _math
                R = AXIS * 0.35
                N = 16
                glLineWidth(1.5)
                glColor3f(1.0, 1.0, 0.0)
                glBegin(GL_LINE_LOOP)
                for k in range(N):
                    a = 2 * _math.pi * k / N
                    glVertex3f(px + R*_math.cos(a), py + R*_math.sin(a), pz)
                glEnd()
            # Project to screen for hit-testing
            if _can_proj:
                try:
                    sx, sy, _ = gluProject(px, py, pz, _mv, _proj, _vp)
                    self._proxy_screen_cache.append((float(sx), float(_vp[3] - sy)))
                except Exception:
                    self._proxy_screen_cache.append((-999.0, -999.0))
            else:
                self._proxy_screen_cache.append((-999.0, -999.0))

        glEnable(GL_DEPTH_TEST)
        glLineWidth(1.0)
        glColor3f(1.0, 1.0, 1.0)
        if self.lighting:
            glEnable(GL_LIGHTING)

    def _draw_proxy_meshes(self):
        """Draw loaded proxy model meshes at their attachment transforms."""
        if not self.proxies or not self.show_proxies or not self.proxy_meshes:
            return
        glDisable(GL_TEXTURE_2D)
        glDisable(GL_LIGHTING)
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        for i, prx in enumerate(self.proxies):
            if prx.get('hidden', False):
                continue
            raw_name = prx.get('name', '')
            key = _proxy_basename_normalize(raw_name)
            mesh = self.proxy_meshes.get(key)
            if not mesh:
                continue
            pos = prx['pos']
            m   = prx['mat']
            # Build 4x4 column-major OpenGL matrix from 4x3 proxy transform
            # DayZ columns: X=(m[0..2]), Y=(m[3..5]), Z=(m[6..8]), T=pos
            mat4 = [
                m[0], m[1], m[2], 0.0,   # col 0 = X basis
                m[3], m[4], m[5], 0.0,   # col 1 = Y basis
                m[6], m[7], m[8], 0.0,   # col 2 = Z basis
                pos[0], pos[1], pos[2], 1.0,  # col 3 = translation
            ]
            glPushMatrix()
            glMultMatrixf(mat4)
            # Build flat 1D vertex array once per mesh (numpy indexing, cached)
            if 'verts_flat' not in mesh:
                _pts = mesh['pts']
                _tris = mesh['tris']
                pts_np = np.asarray(_pts, dtype=np.float32)
                max_idx = max(0, len(pts_np) - 1)
                try:
                    tri_np = np.clip(np.array(_tris, dtype=np.int32), 0, max_idx)
                    flat = pts_np[tri_np.reshape(-1)].flatten()
                except (ValueError, TypeError):
                    # Разноразмерные полигоны — fan-триангуляция
                    rows = []
                    for face in _tris:
                        v = [min(int(i), max_idx) for i in face]
                        for k in range(1, len(v) - 1):
                            rows += [pts_np[v[0]], pts_np[v[k]], pts_np[v[k+1]]]
                    flat = np.array(rows, dtype=np.float32).flatten() if rows else np.empty(0, dtype=np.float32)
                mesh['verts_flat'] = np.ascontiguousarray(flat, dtype=np.float32)
                mesh['verts_flat_n'] = len(flat) // 3
            vf = mesh['verts_flat']
            nv = mesh['verts_flat_n']
            # Отвязать VBO чтобы glVertexPointer читал client-side данные, а не offset в VBO
            glBindBuffer(GL_ARRAY_BUFFER, 0)
            glEnableClientState(GL_VERTEX_ARRAY)
            glVertexPointer(3, GL_FLOAT, 0, vf)
            glColor4f(0.9, 0.85, 0.6, 0.45)
            glDrawArrays(GL_TRIANGLES, 0, nv)
            glColor4f(1.0, 0.9, 0.4, 0.8)
            glPolygonMode(GL_FRONT_AND_BACK, GL_LINE)
            glDrawArrays(GL_TRIANGLES, 0, nv)
            glPolygonMode(GL_FRONT_AND_BACK, GL_FILL)
            glDisableClientState(GL_VERTEX_ARRAY)
            glPopMatrix()
        glDisable(GL_BLEND)
        if self.lighting:
            glEnable(GL_LIGHTING)

    # ── Загрузка ─────────────────────────────────────────────────────────────

    def load_mesh(self, pts, nrms, tris, uvs=None, mat_groups=None):
        """Загрузить меш. mat_groups = [(mat_path, [tris]), ...] — группы материалов."""
        if not bool(glGenBuffers):
            # GL контекст ещё не инициализирован — отложить до initgl
            self._pending_mesh = (pts, nrms, tris, uvs, mat_groups)
            return
        # v3.1.6: при пустых данных — очистить viewport и не вычислять bbox
        if len(pts) == 0:
            self._center = (0.0, 0.0, 0.0)
            self._scale = 1.0
            self.mesh_groups = []
            self.proxies = []
            self.proxy_meshes = {}
            try:
                self.tkExpose()  # request redraw to clear
            except Exception: pass
            _dbg('load_mesh: empty pts, viewport cleared')
            return
        pa = np.array(pts,  dtype=np.float32)
        na = np.array(nrms, dtype=np.float32)
        mn, mx       = pa.min(0), pa.max(0)
        self._center = tuple((mn + mx) / 2)
        self._scale  = 1.5 / max((mx - mn).max(), 1e-6)

        # Общие VBO (вершины / нормали)
        if self.vbo_v is None: self.vbo_v, self.vbo_n = glGenBuffers(2)
        glBindBuffer(GL_ARRAY_BUFFER, self.vbo_v)
        glBufferData(GL_ARRAY_BUFFER, pa.nbytes, pa, GL_STATIC_DRAW)
        glBindBuffer(GL_ARRAY_BUFFER, self.vbo_n)
        glBufferData(GL_ARRAY_BUFFER, na.nbytes, na, GL_STATIC_DRAW)

        if uvs is not None:
            ua = np.array([(u, v) for u, v in uvs], dtype=np.float32)
            if self.vbo_uv is None: self.vbo_uv = glGenBuffers(1)
            glBindBuffer(GL_ARRAY_BUFFER, self.vbo_uv)
            glBufferData(GL_ARRAY_BUFFER, ua.nbytes, ua, GL_STATIC_DRAW)
            tan = _compute_tangents(pts, uvs, tris, nrms)
            if self.vbo_tan is None: self.vbo_tan = glGenBuffers(1)
            glBindBuffer(GL_ARRAY_BUFFER, self.vbo_tan)
            glBufferData(GL_ARRAY_BUFFER, tan.nbytes, tan, GL_STATIC_DRAW)

        # Освободить старые IBO / текстуры групп
        for g in self.mesh_groups:
            try: glDeleteBuffers(1, [g['ibo']])
            except: pass
        self.mesh_groups = []

        # Создать IBO для каждой группы
        groups_data = mat_groups if mat_groups else [('', tris)]
        for mat_path, gtris in groups_data:
            if not gtris: continue
            ia  = np.array(gtris, dtype=np.uint32)
            ibo = glGenBuffers(1)
            glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, ibo)
            glBufferData(GL_ELEMENT_ARRAY_BUFFER, ia.nbytes, ia, GL_STATIC_DRAW)
            self.mesh_groups.append({
                'ibo':       ibo,
                'tri_count': len(gtris),
                'mat_path':  mat_path,
                'tex_id':    None,
                'nrm_id':    None,
                'has_tex':   False,
                'has_nrm':   False,
            })

        self.tri_count = sum(g['tri_count'] for g in self.mesh_groups)
        # v3.1.8: mesh успешно загружен — invalidate вызывающий код сам обновит _mesh_loaded_for_idx
        # (вызываем из _switch_to_model где знаем idx)

    def _upload_tex(self, tex_id, rgba):
        h, w = rgba.shape[:2]
        glBindTexture(GL_TEXTURE_2D, tex_id)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR_MIPMAP_LINEAR)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_REPEAT)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_REPEAT)
        try:
            from OpenGL.GL import GL_TEXTURE_MAX_ANISOTROPY_EXT, GL_MAX_TEXTURE_MAX_ANISOTROPY_EXT
            glTexParameterf(GL_TEXTURE_2D, GL_TEXTURE_MAX_ANISOTROPY_EXT,
                            min(16.0, glGetFloatv(GL_MAX_TEXTURE_MAX_ANISOTROPY_EXT)))
        except Exception: pass
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, w, h, 0, GL_RGBA, GL_UNSIGNED_BYTE, rgba)
        glGenerateMipmap(GL_TEXTURE_2D)

    def load_group_texture(self, gi, rgba):
        if gi >= len(self.mesh_groups): return
        g = self.mesh_groups[gi]
        if g['tex_id'] is None: g['tex_id'] = glGenTextures(1)
        self._upload_tex(g['tex_id'], rgba)
        g['has_tex'] = True

    def load_group_normal(self, gi, rgba):
        if gi >= len(self.mesh_groups): return
        g = self.mesh_groups[gi]
        if g['nrm_id'] is None: g['nrm_id'] = glGenTextures(1)
        self._upload_tex(g['nrm_id'], rgba)
        g['has_nrm'] = True

    def load_texture(self, rgba):
        """Применить одну диффузную текстуру ко ВСЕМ группам (ручной override)."""
        for gi in range(len(self.mesh_groups)):
            self.load_group_texture(gi, rgba)

    def load_normal_map(self, rgba):
        """Применить одну карту нормалей ко ВСЕМ группам."""
        for gi in range(len(self.mesh_groups)):
            self.load_group_normal(gi, rgba)

    def clear_texture(self):
        for g in self.mesh_groups: g['has_tex'] = False

    def clear_normal(self):
        for g in self.mesh_groups: g['has_nrm'] = False

    # ── Keyboard axis + camera mode ─────────────────────────────────────────
    def _bind_root_keys(self, e=None):
        top = self.winfo_toplevel()
        # X/Y/Z обрабатываются через P3DViewer.root binding с keycode (layout-independent)
        top.bind('<Escape>', self._deselect_proxy)

    def _key_press(self, e):
        """X/Y/Z — опциональный ограничитель оси при перемещении proxy."""
        key = e.keysym.lower()
        if key not in ('x', 'y', 'z'):
            return
        if key == self._last_axis_key:
            return                          # key-repeat — игнорировать
        self._last_axis_key = key
        if self._proxy_key == key:
            self._proxy_key = None          # повторное нажатие = отключить
        else:
            self._proxy_key = key           # новая ось = включить
        _dbg(f'axis key: {key} → proxy_key={self._proxy_key}')
        if self.on_axis_change:
            self.on_axis_change(self._proxy_key)

    def _key_release(self, e):
        self._last_axis_key = None

    def _deselect_proxy(self, e=None):
        self.selected_proxy = -1
        self._proxy_key  = None
        self._proxy_drag = None
        if self.on_proxy_select:
            self.on_proxy_select(-1)
        if self.on_axis_change:
            self.on_axis_change(None)

    # ── LMB: proxy выбран → двигать; иначе → вращать камеру ────────────────
    def _drag_start(self, e):
        self.focus_set()
        if self.show_proxies:
            # Найти ближайший proxy к месту клика
            try:
                _THRESH2 = 28 * 28
                best_i, best_d = -1, _THRESH2
                for i, (sx, sy) in enumerate(self._proxy_screen_cache):
                    d2 = (e.x - sx)**2 + (e.y - sy)**2
                    if d2 < best_d:
                        best_d, best_i = d2, i
                if best_i >= 0:
                    self.selected_proxy = best_i
                    try:
                        if self.on_proxy_select:
                            self.on_proxy_select(best_i)
                    except Exception:
                        pass
                    self.focus_set()
                    _dbg(f'proxy selected: {best_i}')
            except Exception:
                pass
            # Proxy выбран → начать перемещение (ось активна или свободное XZ)
            if self.selected_proxy >= 0:
                if not self.proxies[self.selected_proxy].get('locked', False):
                    self._proxy_drag = 'move'
                    self._proxy_drag_lxy = (e.x, e.y)
                    _dbg(f'proxy drag START: sel={self.selected_proxy} ax={self._proxy_key}')
                return
        # Нет выбранного proxy → вращение камеры
        self._drag = True; self._lx = e.x; self._ly = e.y

    def _drag_move(self, e):
        if self._proxy_drag == 'move':
            self._proxy_move_drag(e)
            return
        if not self._drag: return
        self.rot_y += (e.x - self._lx) * 0.5
        self.rot_x += (e.y - self._ly) * 0.5
        self._lx, self._ly = e.x, e.y

    def _drag_end(self, e):
        self._drag = False
        self._proxy_drag = None

    def _proxy_move_drag(self, e):
        if self.selected_proxy < 0 or self.selected_proxy >= len(self.proxies):
            _dbg(f'proxy move SKIP: sel={self.selected_proxy} len={len(self.proxies)}')
            return
        lx, ly = self._proxy_drag_lxy
        dx_px = e.x - lx
        dy_px = -(e.y - ly)
        self._proxy_drag_lxy = (e.x, e.y)
        sens = 0.005 / (self._scale * max(self.zoom, 0.05))
        dx = dx_px * sens
        dy = dy_px * sens
        prx = self.proxies[self.selected_proxy]
        if prx.get('locked', False):
            return
        pos = list(prx.get('pos', [0.0, 0.0, 0.0]))
        ax = self._proxy_key
        if self._local_space:
            # Локальные оси — движение вдоль собственных осей прокси
            m = prx.get('mat', [1,0,0, 0,1,0, 0,0,1])
            if ax == 'x':
                pos[0] += dx * m[0]; pos[1] += dx * m[1]; pos[2] += dx * m[2]
            elif ax == 'y':
                pos[0] += dy * m[3]; pos[1] += dy * m[4]; pos[2] += dy * m[5]
            elif ax == 'z':
                pos[0] += dx * m[6]; pos[1] += dx * m[7]; pos[2] += dx * m[8]
            else:
                # Свободно: локальная плоскость XZ прокси
                pos[0] += dx * m[0] - dy * m[6]
                pos[1] += dx * m[1] - dy * m[7]
                pos[2] += dx * m[2] - dy * m[8]
        else:
            # Глобальные оси (мировые)
            if ax == 'x':
                pos[0] += dx
            elif ax == 'y':
                pos[1] += dy
            elif ax == 'z':
                pos[2] += dx
            else:
                pos[0] += dx                # свободно: X и Z мировые
                pos[2] -= dy
        prx['pos'] = pos
        _dbg(f'proxy moved: ax={ax} dx={dx:.4f} pos={pos[0]:.3f},{pos[1]:.3f},{pos[2]:.3f}')
        if self.on_proxy_transform:
            self.on_proxy_transform(self.selected_proxy)

    # ── RMB — вращение прокси ───────────────────────────────────────────────
    def _rmb_start(self, e):
        self.focus_set()
        if self.selected_proxy >= 0 and self.show_proxies:
            if self.proxies[self.selected_proxy].get('locked', False):
                return
            self._proxy_drag = 'rotate'
            self._proxy_drag_lxy = (e.x, e.y)

    def _rmb_move(self, e):
        if self._proxy_drag != 'rotate': return
        if self.selected_proxy < 0 or self.selected_proxy >= len(self.proxies):
            return
        lx, ly = self._proxy_drag_lxy
        angle = (e.x - lx) * 0.5
        self._proxy_drag_lxy = (e.x, e.y)
        # Ось: если задана X/Y/Z → вращать по ней, иначе по Y
        ax = self._proxy_key if self._proxy_key else 'y'
        prx = self.proxies[self.selected_proxy]
        mat = list(prx.get('mat', [1,0,0, 0,1,0, 0,0,1]))
        prx['mat'] = _mat3_mul(mat, _rot3(ax, angle))
        if self.on_proxy_transform:
            self.on_proxy_transform(self.selected_proxy)

    def _rmb_end(self, e):
        if self._proxy_drag == 'rotate':
            self._proxy_drag = None

    # ── Прочие ──────────────────────────────────────────────────────────────
    def _wheel(self, e):
        self.zoom = max(0.05, min(self.zoom * (1.1 if e.delta > 0 else 0.9), 20.0))

    def _pan_start(self, e):
        self._pan_last_x = e.x
        self._pan_last_y = e.y

    def _pan_move(self, e):
        if not hasattr(self, '_pan_last_x'):
            return
        dx = (e.x - self._pan_last_x) * 0.005
        dy = (e.y - self._pan_last_y) * 0.005
        self._pan_last_x = e.x
        self._pan_last_y = e.y
        self.pan_x += dx
        self.pan_y -= dy
        self.redraw()


def _show_splash(root):
    """Показывает заставку d.x.b без рамок со скруглёнными углами, цвета из темы."""
    # Подгружаем цвета из сохранённых настроек
    _settings = _load_app_settings()
    _theme_name = _settings.get('theme_name', 'Teal Dark')
    _t = PRESET_THEMES.get(_theme_name, PRESET_THEMES.get('Teal Dark', DEFAULT_THEME))
    bg_col    = _t.get('bg_side', '#161b22')
    accent    = _t.get('accent_bg', '#1d9e75')
    sep_col   = _t.get('bg_sep', '#1d3d30')
    muted     = _t.get('fg_muted', '#45475a')
    text_col  = _t.get('fg_text', '#cdd6f4')
    # Применяем кастомные цвета если есть
    for k, v in _settings.get('custom_colors', {}).items():
        if k == 'bg_side':   bg_col  = v
        if k == 'accent_bg': accent  = v
        if k == 'bg_sep':    sep_col = v
        if k == 'fg_muted':  muted   = v
        if k == 'fg_text':   text_col = v

    W, H = 360, 210
    radius = 18
    TRANS = '#010101'   # цвет прозрачных углов

    splash = tk.Toplevel(root)
    splash.overrideredirect(True)
    splash.attributes('-topmost', True)
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    splash.geometry(f'{W}x{H}+{(sw-W)//2}+{(sh-H)//2}')
    splash.configure(bg=TRANS)
    splash.wm_attributes('-transparentcolor', TRANS)

    try:
        from PIL import Image, ImageDraw, ImageTk
        img = Image.new('RGB', (W, H), TRANS)
        d = ImageDraw.Draw(img)
        # Скруглённый прямоугольник
        d.rounded_rectangle([0, 0, W-1, H-1], radius=radius, fill=bg_col, outline=sep_col, width=1)
        # Акцентная линия вверху
        d.rectangle([radius, 0, W-radius-1, 3], fill=accent)
        photo = ImageTk.PhotoImage(img)
        canvas = tk.Canvas(splash, bg=TRANS, highlightthickness=0, width=W, height=H)
        canvas.pack(fill=tk.BOTH, expand=True)
        canvas.create_image(0, 0, image=photo, anchor='nw')
        canvas._splash_bg = photo  # держим ссылку
    except Exception:
        canvas = tk.Canvas(splash, bg=bg_col, highlightthickness=0, width=W, height=H)
        canvas.pack(fill=tk.BOTH, expand=True)
        canvas.create_rectangle(1, 1, W-2, H-2, outline=sep_col, width=1, fill='')

    # Логотип
    canvas.create_text(W//2, H//2 - 28, text='d.x.b',
                       fill=accent, font=('Segoe UI', 48, 'bold'), anchor='center')
    # Акцентная линия по центру
    canvas.create_line(W//2 - 50, H//2 + 6, W//2 + 50, H//2 + 6, fill=accent, width=1)
    # Подписи
    canvas.create_text(W//2, H//2 + 26, text='P3D Viewer',
                       fill=muted, font=('Segoe UI', 13), anchor='center')
    canvas.create_text(W//2, H//2 + 46, text=VERSION,
                       fill=muted, font=('Segoe UI', 9), anchor='center')
    return splash


def _make_app_icon(root):
    """Создаёт и устанавливает иконку приложения P3D."""
    try:
        from PIL import Image, ImageDraw, ImageFont, ImageTk
        sz = 64
        img = Image.new('RGBA', (sz, sz), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        # Фон — скруглённый прямоугольник
        d.rounded_rectangle([0, 0, sz-1, sz-1], radius=12, fill='#0d1a14')
        # Акцентная полоса сверху
        d.rectangle([0, 0, sz-1, 4], fill='#1d9e75')
        # Текст P3D
        try:
            fnt = ImageFont.truetype('segoeuib.ttf', 20)
        except Exception:
            fnt = ImageFont.load_default()
        d.text((sz//2, sz//2 - 4), 'P3D', fill='#1d9e75', font=fnt, anchor='mm')
        # Подтекст
        try:
            fnt_sm = ImageFont.truetype('segoeui.ttf', 10)
        except Exception:
            fnt_sm = ImageFont.load_default()
        d.text((sz//2, sz//2 + 16), 'd.x.b', fill='#0f6e56', font=fnt_sm, anchor='mm')
        photo = ImageTk.PhotoImage(img)
        root.iconphoto(True, photo)
        root._icon_photo = photo  # сохраняем ссылку
    except Exception:
        pass  # если PIL недоступен — без иконки


# ── GUI ────────────────────────────────────────────────────────────────────

class App:
    def __init__(self, root):
        self.root = root
        _make_app_icon(root)
        self.theme = _load_theme()
        self.root.title(f'P3D Viewer {VERSION}')
        self.root.geometry('1600x900')
        # v2.9: минимальный размер чтобы UI не схлопывался
        try:
            self.root.minsize(900, 600)
        except Exception:
            pass
        self.root.configure(bg=self.theme['bg_main'])
        self.pts = self.uvs = self.nrms = self.tris = None
        self.current_file = None
        self.mat_groups   = []
        # Ручной инструмент переназначения материалов
        self._raw_model   = None
        self._cur_split   = 0
        self._cur_split2   = 0      # второй срез (0 = отключён, однорежимный)
        self._cur_swap     = False
        self._cur_uv_limit  = 1.5   # UV bbox threshold для is_good()
        self._cur_edge_mult = 50.0  # множитель порога длины рёбер для is_good()
        # ── Хранилище моделей ────────────────────────────────────────────────
        self._models      = {}    # {idx: dict со всем состоянием модели}
        self._active_idx  = None  # индекс активной модели
        self._next_idx    = 0     # счётчик для новых индексов
        self._loading         = False
        self._spinner_running = False
        self._spinner_idx     = 0
        self._load_cancelled  = False
        self._open_buttons    = []
        self._mat_panel   = None
        self._split_var   = None
        self._split2_var  = None
        self._selected_proxy_idx = -1
        # Ссылки на виджеты для перекраски
        self._tw_frames   = []   # (widget, key)  — bg из темы
        self._tw_labels   = []   # (widget, bg_key, fg_key, font_role)
        self._tw_buttons  = []   # (widget, 'normal'|'accent')
        self._tw_scales   = []   # (widget,)
        self._tw_seps     = []   # (widget,)  — разделители
        self._tw_checkbuttons = []  # v3.1.7: Checkbutton'ы (alpha blend toggles, swap, etc.)
        # Секции боковой панели для reorder
        self._section_frames = {}   # name → tk.Frame
        self._section_order  = list(_SECTION_NAMES)
        self._section_vis    = {n: True for n in _SECTION_NAMES}
        self._dbg_win        = None  # Debug Monitor окно
        # v3.1.9: Dev Tools infrastructure
        self._dev_win        = None  # 🛠 Dev Tools Toplevel
        self._widget_registry = {}   # {key: (widget, original_text)} — собирается через _discover_widgets()
        self._errors_log     = []  # cached failed-list (для re-open через кнопку)
        self._errors_dismissed = False
        # v3.1.9: showed_errors set cache — синхронизируется с self._app_settings['showed_errors']
        # НЕ показывать popup повторно для уже показанных (path, error) — persistent через settings.json
        self._icon_btns       = {}
        self._icon_btn_outers = {}
        # ── v2.3: Subprocess-based loader state ─────────────────────────────
        # Каждая загрузка идёт в отдельном python-процессе (см. _p3d_subprocess_main).
        # Это даёт: настоящий параллелизм (несколько моделей грузятся одновременно),
        # жёсткий cancel через process.terminate(), изоляцию DLL state.
        self._load_procs    = {}    # {idx: subprocess.Popen} — живые воркеры
        self._load_poll_after = None  # after-id для опроса завершения
        self._load_results  = {}    # {idx: dict} — результаты, ожидающие применения
        self._app_settings   = _load_app_settings()
        self._build_ui()
        self._apply_saved_settings()
        self._poll_dbg_queue()
        self.root.protocol('WM_DELETE_WINDOW', self._on_close)

    # ── Helpers ─────────────────────────────────────────────────────────────

    def _T(self): return self.theme

    def _sep(self, p):
        T = self._T()
        w = tk.Frame(p, bg=T['bg_sep'], height=1)
        w.pack(fill=tk.X, padx=16, pady=8)
        self._tw_seps.append(w)
        return w

    def _lbl(self, p, t):
        T = self._T()
        w = tk.Label(p, text=t, bg=T['bg_side'], fg=T['fg_label'],
                     font=(T['font_fam'], T['font_sz'] - 1, 'bold'))
        w.pack(padx=16, anchor='w')
        self._tw_labels.append((w, 'bg_side', 'fg_label', 'section'))
        return w

    def _btn(self, parent, text, cmd, accent=False):
        T = self._T()
        relief = T.get('btn_relief', 'flat')
        w = tk.Button(parent, text=text, command=cmd,
                      bg=T['accent_bg'] if accent else T['btn_bg'],
                      fg=T['accent_fg'] if accent else T['btn_fg'],
                      relief=relief, font=(T['font_fam'], T['font_sz']),
                      padx=8, pady=7, cursor='hand2',
                      activebackground=T['accent_hov'] if accent else T['btn_hover'],
                      activeforeground=T['accent_fg']  if accent else T['btn_fg'],
                      bd=0, anchor='w')
        self._tw_buttons.append((w, 'accent' if accent else 'normal'))
        return w

    def _ind(self, widget, on):
        widget.config(fg=self._T()['c_ok'] if on else self._T()['fg_muted'])

    # ── Построение UI ────────────────────────────────────────────────────────

    def _switch_icon_panel(self, key):
        """Переключает активную иконку и показывает соответствующую панель."""
        T = self._T()
        prev = self._active_icon[0]

        # Скрываем предыдущую панель
        if prev is not None:
            if prev in self._icon_panels:
                self._icon_panels[prev].pack_forget()
            if prev in self._icon_btns:
                self._icon_btns[prev].config(fg=T['fg_muted'], bg=T['bg_main'])
            if prev in self._icon_btn_outers:
                self._icon_btn_outers[prev].config(bg=T['bg_main'])

        # v0.1.18: "Выключено" должно быть только в модуле "Моды".
        # Если предыдущая вкладка была mods, но мы переключаемся на другую — скрыть _disabled_frame из PanedWindow
        if prev == 'mods' and key != 'mods' and hasattr(self, '_side_paned'):
            try:
                self._side_paned.forget(self._disabled_frame)
            except Exception: pass

        # Второй клик на ту же иконку — сворачиваем
        if prev == key:
            self._active_icon[0] = None
            return

        # Показываем новую панель
        self._active_icon[0] = key
        if key in self._icon_btns:
            self._icon_btns[key].config(fg=T['accent_bg'], bg=T['bg_widget'])
        if key in self._icon_btn_outers:
            self._icon_btn_outers[key].config(bg=T['bg_widget'])
        if key in self._icon_panels:
            # v2.9: адаптивный layout — панель растягивается во все стороны
            self._icon_panels[key].pack(fill=tk.BOTH, expand=True)
            # v0.1.18: при возврате на mods — снова показать _disabled_frame в PanedWindow
            if key == 'mods' and hasattr(self, '_side_paned') and hasattr(self, '_disabled_frame'):
                try:
                    panes = self._side_paned.panes()
                    if self._disabled_frame not in panes:
                        self._side_paned.add(self._disabled_frame, minsize=80, stretch='always')
                except Exception: pass
        try:
            self._side_canvas.yview_moveto(0)
        except Exception:
            pass
        try:
            self.root.after(20, self._refresh_canvas)
        except Exception:
            pass

    def _build_ui(self):
        T = self._T()

        # ── Тулбар с кнопкой скрытия боковой панели ───────────────────────────
        toolbar_frame = tk.Frame(self.root, bg=T['bg_main'], height=30)
        toolbar_frame.pack(fill=tk.X, side=tk.TOP)
        toolbar_frame.pack_propagate(False)

        self._main_frame = tk.Frame(self.root, bg=T['bg_main'])
        self._main_frame.pack(fill=tk.BOTH, expand=True)
        self._tw_frames.append((self._main_frame, 'bg_main'))

        sw = self._app_settings.get('sidebar_width', 350)
        side_outer = tk.Frame(self._main_frame, bg=T['bg_side'], width=sw)
        side_outer.pack(side=tk.LEFT, fill=tk.Y)
        side_outer.pack_propagate(False)
        self._side_outer = side_outer
        self._tw_frames.append((side_outer, 'bg_side'))

        # v0.1.4: icon_strip выносим в _main_frame (теперь не внутри side_outer),
        # чтобы toggle мог скрывать только side_outer (моды), оставляя иконки видимыми
        icon_strip = tk.Frame(self._main_frame, bg=T['bg_main'], width=44)
        icon_strip.pack(side=tk.LEFT, fill=tk.Y, before=side_outer)
        icon_strip.pack_propagate(False)
        self._tw_frames.append((icon_strip, 'bg_main'))

        self._sidebar_visible = True

        def _toggle_sidebar():
            # v0.1.4: скрывает side_outer (только моды), icon_strip в _main_frame остаётся
            if self._sidebar_visible:
                side_outer.pack_forget()
                self._sidebar_visible = False
                toggle_btn.config(text='▶ Моды')
            else:
                side_outer.pack(side=tk.LEFT, fill=tk.Y, before=self._vp_frame)
                self._sidebar_visible = True
                toggle_btn.config(text='◀ Моды')

        def _toggle_sidebar():
            # v0.1.4: скрывает side_outer (только моды), icon_strip в _main_frame остаётся
            if self._sidebar_visible:
                side_outer.pack_forget()
                self._sidebar_visible = False
                toggle_btn.config(text='▶')
            else:
                side_outer.pack(side=tk.LEFT, fill=tk.Y, before=self._vp_frame)
                self._sidebar_visible = True
                toggle_btn.config(text='◀')

        toggle_btn = tk.Button(toolbar_frame, text='◀', relief='flat',
                               bg=T['bg_main'], fg=T['fg_label'],
                               font=(T['font_fam'], T['font_sz'] - 1),
                               padx=6, pady=0, bd=0,
                               command=_toggle_sidebar)
        toggle_btn.pack(side=tk.LEFT, padx=(6, 0), pady=0)
        self._tw_buttons.append((toggle_btn, 'normal'))
        self._toggle_btn = toggle_btn

        # v0.1.2: toggle для right_panel (Загружено) — справа в toolbar
        self._right_panel_visible = True
        rp_toggle_btn = tk.Button(toolbar_frame, text='▶', relief='flat',
                                  bg=T['bg_main'], fg=T['fg_label'],
                                  font=(T['font_fam'], T['font_sz'] - 1),
                                  padx=6, pady=0, bd=0,
                                  command=self._toggle_right_panel)
        rp_toggle_btn.pack(side=tk.RIGHT, padx=(0, 6), pady=0)
        self._tw_buttons.append((rp_toggle_btn, 'normal'))
        self._rp_toggle_btn = rp_toggle_btn

        # v0.1.4: icon_strip создаётся РАНЬШЕ (в _main_frame), см. выше

        # v3.1.9: 1px sidebar divider убран (KirrieD: белая полоса была видна)

        # ── Область контента (правая, скроллируемая) ──────────────────────────
        panel_outer = tk.Frame(side_outer, bg=T['bg_side'])
        panel_outer.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # v0.1.8: фиксированный header (версия) сверху
        self._header_frame = tk.Frame(panel_outer, bg=T['bg_side'])
        self._header_frame.pack(side=tk.TOP, fill=tk.X)
        self._tw_frames.append((self._header_frame, 'bg_side'))

        # v0.1.11: PanedWindow sash между модами и выключено (мышкой двигается)
        self._side_paned = tk.PanedWindow(
            panel_outer, orient=tk.VERTICAL, bg=T['bg_sep'],
            sashwidth=5, sashrelief='raised', bd=0,
            handlesize=0, opaqueresize=False)
        self._side_paned.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self._tw_frames.append((self._side_paned, 'bg_sep'))

        # scrollable середина (моды)
        self._side_canvas = tk.Canvas(
            self._side_paned, bg=T['bg_side'], bd=0, highlightthickness=0)
        self._side_paned.add(self._side_canvas, minsize=80, stretch='always')
        # v0.1.13: при перетягивании sash scrollregion должен обновиться
        def _on_side_pane_configure(_e=None):
            try:
                if getattr(self, '_refresh_canvas', None):
                    self._refresh_canvas()
            except Exception: pass
        self._side_paned.bind('<ButtonRelease-1>', lambda e: self.root.after(20, _on_side_pane_configure))
        self._side_paned.bind('<B1-Motion>', lambda e: self.root.after(20, _on_side_pane_configure))

        # bottom (выключено) — отдельная панель со своим scroll
        self._disabled_frame = tk.Frame(self._side_paned, bg=T['bg_side'])
        self._side_paned.add(self._disabled_frame, minsize=80, stretch='always')

        # v0.1.8: фиксированный footer (Загрузить+Blender) снизу
        self._footer_frame = tk.Frame(panel_outer, bg=T['bg_side'])
        self._footer_frame.pack(side=tk.BOTTOM, fill=tk.X)
        self._tw_frames.append((self._footer_frame, 'bg_side'))

        self._side = tk.Frame(self._side_canvas, bg=T['bg_side'])
        self._side_win_id = self._side_canvas.create_window(
            (0, 0), window=self._side, anchor='nw')

        # Fix: update canvas window width and scrollregion reliably
        def _refresh_canvas():
            try:
                # v0.1.12: process pending events first, чтобы bbox('all') вернул
                # реальный размер контента
                try: self._side_canvas.update_idletasks()
                except Exception: pass
                w = self._side_canvas.winfo_width()
                if w < 2:
                    # Canvas not yet laid out — use parent width minus scrollbar
                    w = max(100, panel_outer.winfo_width() - 6)
                self._side_canvas.itemconfig(self._side_win_id, width=w)
                # v0.1.21: canvas window height = ch (canvas height), НЕ max
                # с content_h. Иначе при sash вверх scrollframe остаётся большой
                # и "вылезает" за уменьшенный canvas. Scrollregion = content_h
                # для нормального скролла.
                bbox = self._side_canvas.bbox('all')
                content_h = (bbox[3] - bbox[1]) if bbox else 0
                ch = self._side_canvas.winfo_height()
                if ch > 2:
                    self._side_canvas.itemconfig(self._side_win_id, height=ch)
                # scrollregion по content_h — если контента больше canvas, скролл
                self._side_canvas.configure(
                    scrollregion=self._side_canvas.bbox('all') or (0, 0, w, max(content_h, ch)))
            except Exception:
                pass
        self._refresh_canvas = _refresh_canvas

        self._configure_job = None
        def _on_side_configure(e):
            if self._configure_job:
                try: self.root.after_cancel(self._configure_job)
                except: pass
            self._configure_job = self.root.after(30, _refresh_canvas)
        self._side.bind('<Configure>', _on_side_configure)
        # v0.1.13: bind на canvas чтобы обновить scrollregion при ресайзе (sash drag)
        self._side_canvas.bind('<Configure>',
            lambda e: self.root.after(30, _refresh_canvas))

        def _on_canvas_configure(e):
            try:
                self._side_canvas.itemconfig(self._side_win_id, width=e.width)
            except Exception:
                pass
        self._side_canvas.bind('<Configure>', _on_canvas_configure)

        def _on_side_mousewheel(e):
            self._side_canvas.yview_scroll(int(-1*(e.delta/120)), 'units')
        # v0.1.2: Enter/Leave tracking — mousewheel скроллит только когда курсор над sidebar
        def _bind_wheel(_e=None):
            self._side_canvas.bind('<MouseWheel>', _on_side_mousewheel)
            self._side.bind('<MouseWheel>', _on_side_mousewheel)
        def _unbind_wheel(_e=None):
            self._side_canvas.unbind('<MouseWheel>')
            self._side.unbind('<MouseWheel>')
        self._side_canvas.bind('<Enter>', _bind_wheel)
        self._side_canvas.bind('<Leave>', _unbind_wheel)
        self._side.bind('<Enter>', _bind_wheel)
        self._side.bind('<Leave>', _unbind_wheel)
        _bind_wheel()  # initial bind (на случай если cursor уже там)

        self._tw_frames.append((self._side, 'bg_side'))
        self._tw_frames.append((panel_outer, 'bg_side'))
        # side_outer already appended above — no duplicate

        # ── Иконки навигации ──────────────────────────────────────────────────
        self._icon_panels = {}      # panel_key → Frame (content)
        self._active_icon = [None]  # текущая активная вкладка

        # Верхняя часть иконочной полоски — логотип
        logo_lbl = tk.Label(icon_strip, text='P3D', bg=T['bg_main'],
                            fg=T['accent_bg'],
                            font=(T['font_fam'], 7, 'bold'))
        logo_lbl.pack(pady=(10, 12))
        self._tw_labels.append((logo_lbl, 'bg_main', 'accent_bg', 'normal'))

        tk.Frame(icon_strip, bg=T['bg_sep'], height=1).pack(fill=tk.X, padx=4)

        def _make_icon_btn(key, symbol, tooltip_text):
            """Создаёт иконку-кнопку в icon_strip."""
            btn_outer = tk.Frame(icon_strip, bg=T['bg_main'])
            btn_outer.pack(fill=tk.X, pady=1)

            btn = tk.Label(btn_outer, text=symbol, bg=T['bg_main'],
                           fg=T['fg_muted'],
                           font=(T['font_fam'], 14),
                           width=3, pady=7, cursor='hand2')
            btn.pack(fill=tk.X)

            self._icon_btns[key] = btn
            self._icon_btn_outers[key] = btn_outer

            def _click(e=None):
                self._switch_icon_panel(key)

            btn.bind('<Button-1>', _click)
            btn_outer.bind('<Button-1>', _click)

            # Hover — используем self._T() чтобы подхватывать текущую тему
            btn.bind('<Enter>', lambda e, b=btn, k=key: b.config(
                fg=self._T()['fg_text']) if self._active_icon[0] != k else None)
            btn.bind('<Leave>', lambda e, b=btn, k=key: b.config(
                fg=self._T()['accent_bg'] if self._active_icon[0] == k else self._T()['fg_muted']))

            # Tooltip
            def _show_tip(e):
                tip = tk.Toplevel(self.root)
                tip.overrideredirect(True)
                tip.wm_geometry(f'+{e.x_root+30}+{e.y_root-4}')
                tk.Label(tip, text=tooltip_text, bg=T['bg_widget'], fg=T['fg_text'],
                         font=(T['font_fam'], 8),
                         padx=8, pady=4,
                         highlightbackground=T['bg_sep'],
                         highlightthickness=1).pack()
                def _hide():
                    try: tip.destroy()
                    except: pass
                tip.after(1600, _hide)
                btn.bind('<Leave>', lambda e2, h=_hide, b=btn, k=key: (h(), b.config(
                    fg=self._T()['accent_bg'] if self._active_icon[0] == k else self._T()['fg_muted'])))

            btn.bind('<Enter>', lambda e, b=btn, st=_show_tip: (b.config(fg=self._T()['fg_text']), st(e)))

            return _click

        # v0.1.16: применяем icon_strip_order из settings (если есть)
        _default_icon_order = ['mods', 'mats', 'textures', 'file', 'proxy']
        _icon_strip_order = self._app_settings.get('icon_strip_order', _default_icon_order)
        # фильтруем — оставляем только валидные ключи
        _icon_strip_order = [k for k in _icon_strip_order if k in ('mods','mats','textures','file','proxy')]
        for k in _default_icon_order:
            if k not in _icon_strip_order:
                _icon_strip_order.append(k)
        _icon_icons = {
            'mods':     '⊞',
            'mats':     '◈',
            'textures': '▦',
            'file':     '⊡',
            'proxy':    '⊕',
        }
        _icon_names = {
            'mods':     'Моды',
            'mats':     'Материалы',
            'textures': 'Текстуры',
            'file':     'Файл',
            'proxy':    'Прокси',
        }
        for k in _icon_strip_order:
            _make_icon_btn(k, _icon_icons[k], _icon_names[k])
        self._icon_strip_order = _icon_strip_order

        # Разделитель + настройки внизу
        spacer = tk.Frame(icon_strip, bg=T['bg_main'])
        spacer.pack(fill=tk.Y, expand=True)

        tk.Frame(icon_strip, bg=T['bg_sep'], height=1).pack(fill=tk.X, padx=4)

        settings_btn = tk.Label(icon_strip, text='⚙', bg=T['bg_main'],
                                fg=T['fg_muted'],
                                font=(T['font_fam'], 13),
                                width=3, pady=8, cursor='hand2')
        settings_btn.pack(fill=tk.X)
        settings_btn.bind('<Button-1>', lambda e: self._open_theme_editor())
        settings_btn.bind('<Enter>', lambda e: settings_btn.config(fg=T['fg_text']))
        settings_btn.bind('<Leave>', lambda e: settings_btn.config(fg=T['fg_muted']))

        # ── Панели контента (по одной на каждую иконку) ───────────────────────

        def _make_panel(key):
            """Создаёт Frame-контейнер для панели, изначально скрытый."""
            f = tk.Frame(self._side, bg=T['bg_side'])
            self._icon_panels[key] = f
            self._tw_frames.append((f, 'bg_side'))
            return f

        # Версия + автор (v0.1.8: фиксированный хедер — НЕ скроллится)
        ver_bar = tk.Frame(self._header_frame, bg=T['bg_side'])
        ver_bar.pack(fill=tk.X, padx=8, pady=(10, 2))
        self._tw_frames.append((ver_bar, 'bg_side'))

        self._ver_lbl = tk.Label(ver_bar, text=f'{VERSION}',
                                  bg=T['bg_side'], fg=T['accent_bg'],
                                  font=(T['font_fam'], T['font_sz'], 'bold'))
        self._ver_lbl.pack(side=tk.LEFT)
        self._tw_labels.append((self._ver_lbl, 'bg_side', 'accent_bg', 'normal'))

        self._author_lbl = tk.Label(ver_bar, text='d.x.b',
                                     bg=T['bg_side'], fg=T['fg_muted'],
                                     font=(T['font_fam'], T['font_sz'] - 1))
        self._author_lbl.pack(side=tk.RIGHT)
        self._tw_labels.append((self._author_lbl, 'bg_side', 'fg_muted', 'normal'))

        tk.Frame(self._side, bg=T['bg_sep'], height=1).pack(fill=tk.X, pady=(4, 0))

        # Скрытый _title_lbl (для совместимости с _apply_theme)
        self._title_lbl = tk.Label(self._side, text='', bg=T['bg_side'], fg=T['fg_text'],
                                    font=(T['font_fam'], T['font_sz']))
        # Не пакуем — оставляем скрытым

        # ПАНЕЛЬ: Моды
        mods_panel = _make_panel('mods')
        # v0.1.0: _build_mods_panel отложен — вызывается ПОСЛЕ создания right_panel
        # (см. ниже)

        # ПАНЕЛЬ: Материалы
        mats_panel = _make_panel('mats')

        # ПАНЕЛЬ: Текстуры
        tex_panel = _make_panel('textures')

        # v3.1.8: view_panel удалён — секция ВИД переехала в Текстуры

        # ПАНЕЛЬ: Файл + Конвертация + статус
        file_panel = _make_panel('file')

        # ПАНЕЛЬ: Прокси
        proxy_panel = _make_panel('proxy')

        # Строим секции внутри соответствующих панелей
        self._build_section_ФАЙЛ(parent=file_panel)
        self._build_section_ТЕКСТУРЫ(parent=tex_panel)
        # v3.1.8: _build_section_ВИД удалён — Освещение/Яркость переехали в Текстуры
        self._build_section_МАТЕРИАЛЫ(parent=mats_panel)
        self._build_section_КОНВЕРТАЦИЯ(parent=file_panel)
        self._build_section_ПРОКСИ(parent=proxy_panel)

        # Статус / инфо в панели Файл
        self._sep(file_panel)
        self.info = tk.Label(file_panel, text='Файл не загружен',
                             bg=T['bg_side'], fg=T['fg_label'],
                             font=(T['font_fam'], T['font_sz'] - 1),
                             wraplength=170, justify=tk.LEFT)
        self.info.pack(padx=10, anchor='w')
        self._tw_labels.append((self.info, 'bg_side', 'fg_label', 'normal'))

        self.status = tk.Label(file_panel, text='', bg=T['bg_side'], fg=T['c_ok'],
                               font=(T['font_fam'], T['font_sz'] - 1),
                               wraplength=170, justify=tk.LEFT)
        self.status.pack(padx=10, pady=4, anchor='w')

        self._sep(file_panel)
        self._hints_lbl = tk.Label(
            file_panel, text='ЛКМ — вращение  |  Колёсико — зум',
            bg=T['bg_side'], fg=T['fg_muted'],
            font=(T['font_fam'], T['font_sz'] - 1), justify=tk.LEFT)
        self._hints_lbl.pack(padx=10, anchor='w', pady=(2, 6))
        self._tw_labels.append((self._hints_lbl, 'bg_side', 'fg_muted', 'normal'))

        self._sep(file_panel)
        self._dbg_btn = self._btn(file_panel, '🛠  Debug Monitor: ВЫКЛ',
                                  self._toggle_debug_monitor)
        self._dbg_btn.pack(fill=tk.X, padx=10, pady=(4, 2))
        self._tw_buttons.append((self._dbg_btn, 'normal'))
        # v3.1.13: Debug Monitor ВСЕГДА виден в sidebar (master switch для всех _dbg_cat логов)
        self._dbg_btn_visible = True
        # Обновить текст кнопки согласно текущему _dbg_enabled
        try:
            st = 'ВКЛ' if _dbg_enabled else 'ВЫКЛ'
            self._dbg_btn.config(text=f'🛠  Debug Monitor: {st}')
        except Exception:
            pass

        self._log_btn = self._btn(file_panel, '📋  Лог анализа: ВЫКЛ',
                                  self._toggle_log)
        self._log_btn.pack(fill=tk.X, padx=10, pady=(0, 8))
        self._tw_buttons.append((self._log_btn, 'normal'))

        # v3.1.13: per-category КАТЕГОРИИ ДИАГНОСТИКИ перенесены в DevTools
        # (где есть master Debug Monitor switch). Они всегда включены в коде
        # когда _dbg_enabled=True. Удаляем sidebar-блок.

        # v3.1.13: cleanup legacy _dbg_cat_btns (больше не используется)

        # Кнопка "Ошибки загрузки" — повторно открыть popup с ошибками
        err_lbl = tk.Label(file_panel, text='⚠️ ИНФОРМАЦИЯ ОБ ОШИБКАХ',
                           bg=T['bg_side'], fg=T['fg_muted'],
                           font=(T['font_fam'], T['font_sz'] - 1))
        err_lbl.pack(padx=10, pady=(8, 2), anchor='w')
        self._tw_labels.append((err_lbl, 'bg_side', 'fg_muted', 'small'))

        err_btn_frame = tk.Frame(file_panel, bg=T['bg_side'])
        err_btn_frame.pack(fill=tk.X, padx=10, pady=(0, 4))
        self._tw_frames.append((err_btn_frame, 'bg_side'))
        self._err_btn = self._btn(err_btn_frame, '⚠️  Открыть ошибки загрузки',
                                  self._show_errors_popup)
        self._err_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        self._tw_buttons.append((self._err_btn, 'small'))
        self._err_clear_btn = self._btn(err_btn_frame, '🧹 Очистить',
                                        self._clear_errors_log)
        self._err_clear_btn.pack(side=tk.LEFT, padx=(4, 0))
        self._tw_buttons.append((self._err_clear_btn, 'small'))

        # Активируем вкладку "Моды" по умолчанию — с предварительной инициализацией ширины
        def _startup():
            # Force canvas to correct width before showing panel
            try:
                sw_val = self._app_settings.get('sidebar_width', 240)
                canvas_w = sw_val - 44 - 1 - 6  # icon_strip=44, sep=1, scrollbar=6
                self._side_canvas.itemconfig(self._side_win_id, width=max(100, canvas_w))
            except Exception:
                pass
            self._switch_icon_panel('mods')
            # v0.1.9: auto-picker если _unpacked не найдена — диалог выбора папки
            try:
                if not self._mods_unpacked:
                    d = filedialog.askdirectory(
                        title='Выберите папку с модами (содержит _unpacked или мод-папки)')
                    if d and os.path.isdir(d):
                        self._mods_unpacked = d
                        self._app_settings['unpacked_dir'] = d
                        self._save_app_settings()
                        # перестроить mods panel с новой папкой
                        mods_panel_local = self._icon_panels.get('mods')
                        if mods_panel_local:
                            for w in mods_panel_local.winfo_children():
                                w.destroy()
                            self._build_mods_panel(mods_panel_local)
            except Exception as _e:
                _dbg(f'_startup auto-picker ERROR: {_e}')
            # v3.1.9: Dev Tools — discover widgets + apply custom_labels
            try:
                self._discover_widgets()
                self._apply_custom_labels()
            except Exception as _e:
                _dbg(f'_startup _discover/_apply ERROR: {_e}')

        self.root.after(100, _startup)

        # GL вьюпорт
        self._vp_frame = tk.Frame(self._main_frame, bg=T.get('gl_bg', '#1c1c24'))
        self._vp_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._tw_frames.append((self._vp_frame, 'gl_bg'))

        # v0.1.0: right panel — "Загружено" панель (Tactical Gear style)
        rp_w = self._app_settings.get('right_panel_width', 320)
        self._right_panel = tk.Frame(self._main_frame, bg=T['bg_side'], width=rp_w)
        self._right_panel.pack(side=tk.LEFT, fill=tk.Y)
        self._right_panel.pack_propagate(False)
        self._tw_frames.append((self._right_panel, 'bg_side'))
        self._loaded_outer = tk.Frame(self._right_panel, bg=T['bg_side'])
        self._loaded_outer.pack(fill=tk.BOTH, expand=True)

        # v0.1.0: теперь строим mods panel — self._loaded_outer готов
        self._build_mods_panel(mods_panel)

        if USE_GL:
            self.viewport = Viewport(self._vp_frame, width=880, height=680, takefocus=1)
            self.viewport.pack(fill=tk.BOTH, expand=True)
            # ── Статус-badges поверх viewport ────────────────────────────────
            _bf = T.get('font_fam', 'Arial')
            self._cam_badge = tk.Label(
                self._vp_frame, text='  КАМЕРА ВКЛ  ',
                bg='#e07800', fg='#ffffff', font=(_bf, 9, 'bold'), padx=4, pady=2)
            self._prx_badge = tk.Label(
                self._vp_frame, text='',
                bg='#1a6bbf', fg='#ffffff', font=(_bf, 9, 'bold'), padx=4, pady=2)
            self._axis_badge = tk.Label(
                self._vp_frame, text='',
                bg='#1e8c3a', fg='#ffffff', font=(_bf, 9, 'bold'), padx=4, pady=2)

            def _update_badges():
                # Перестроить позиции снизу вверх (камера верхняя)
                y = 6
                if self._cam_badge.winfo_ismapped():
                    self._cam_badge.place(relx=1.0, rely=0.0, anchor='ne', x=-6, y=y)
                    y += self._cam_badge.winfo_reqheight() + 3
                else:
                    self._cam_badge.place_forget()
                if self._prx_badge.winfo_ismapped():
                    self._prx_badge.place(relx=1.0, rely=0.0, anchor='ne', x=-6, y=y)
                    y += self._prx_badge.winfo_reqheight() + 3
                else:
                    self._prx_badge.place_forget()
                if self._axis_badge.winfo_ismapped():
                    self._axis_badge.place(relx=1.0, rely=0.0, anchor='ne', x=-6, y=y)
                else:
                    self._axis_badge.place_forget()

            # camera_mode badge отключён (режим камеры убран в v1.85)
            self._cam_badge.place_forget()

            def _on_axis(axis):
                is_local = getattr(self, '_proxy_local_space', False)
                space_tag = ' [Лок]' if is_local else ''
                if axis:
                    self._axis_badge.config(text=f'  Ось: {axis.upper()}{space_tag}  ')
                    y_ax = 48 if self._prx_badge.winfo_ismapped() else 26
                    self._axis_badge.place(relx=1.0, rely=0.0, anchor='ne', x=-6, y=y_ax)
                elif is_local:
                    self._axis_badge.config(text='  Лок. пространство  ')
                    y_ax = 48 if self._prx_badge.winfo_ismapped() else 26
                    self._axis_badge.place(relx=1.0, rely=0.0, anchor='ne', x=-6, y=y_ax)
                else:
                    self._axis_badge.place_forget()
                self._refresh_axis_btns()
            self.viewport.on_axis_change = _on_axis

            # Root-level axis key bindings через keycode — не зависят от раскладки.
            # Keycode — физический код клавиши (X=88, Y=89, Z=90, L=76 на Windows VK).
            _AXIS_KEYCODES = {88: 'x', 89: 'y', 90: 'z'}
            _SPACE_KEYCODE = 76  # L — переключение Глоб/Лок пространства

            def _root_axis_key(e):
                # L — toggle local/global space
                if e.keycode == _SPACE_KEYCODE:
                    try:
                        fw = self.root.focus_get()
                        if fw and fw.winfo_class() == 'Entry':
                            return
                    except Exception:
                        pass
                    self._proxy_local_space = not getattr(self, '_proxy_local_space', False)
                    self.viewport._local_space = self._proxy_local_space
                    if self.viewport.on_axis_change:
                        self.viewport.on_axis_change(self.viewport._proxy_key)
                    return
                key = _AXIS_KEYCODES.get(e.keycode)
                if not key:
                    return
                try:
                    fw = self.root.focus_get()
                    if fw and fw.winfo_class() == 'Entry':
                        return   # пользователь вводит координаты — не переключаем
                except Exception:
                    pass
                vp = self.viewport
                if key == vp._last_axis_key:
                    return  # key-repeat guard
                vp._last_axis_key = key
                new_key = None if vp._proxy_key == key else key
                vp._proxy_key = new_key
                if vp.on_axis_change:
                    vp.on_axis_change(new_key)

            def _root_axis_key_release(e):
                if e.keycode in _AXIS_KEYCODES:
                    try:
                        self.viewport._last_axis_key = None
                    except Exception:
                        pass

            self.root.bind('<KeyPress>',   _root_axis_key,         add=True)
            self.root.bind('<KeyRelease>', _root_axis_key_release,  add=True)

            # Применяем сохранённое состояние прокси
            self.viewport.show_proxies = self._app_settings.get('proxies_enabled', True)
            # Сразу задаём bg_color из темы, не ждём initgl
            try:
                c = T.get('gl_bg', '#1c1c24').lstrip('#')
                self.viewport.bg_color = (int(c[0:2],16)/255, int(c[2:4],16)/255, int(c[4:6],16)/255)
            except Exception:
                pass
            if USE_DND:
                self.viewport.drop_target_register(DND_FILES)
                self.viewport.dnd_bind('<<Drop>>', self._on_drop)
        else:
            tk.Label(self._vp_frame,
                     text='Установи PyOpenGL:\npip install pyopengltk PyOpenGL',
                     bg=T.get('gl_bg', '#1c1c24'), fg=T['c_err'],
                     font=(T['font_fam'], 13)).pack(expand=True)
        if USE_DND:
            self._vp_frame.drop_target_register(DND_FILES)
            self._vp_frame.dnd_bind('<<Drop>>', self._on_drop)

    # ── Настройки приложения ─────────────────────────────────────────────────

    def _apply_saved_settings(self):
        """Применяет сохранённые настройки после построения UI."""
        global _log_enabled, _dbg_enabled, _DBG_CATS
        s = self._app_settings

        # Геометрия окна
        try:
            self.root.geometry(s.get('geometry', '1600x900'))
        except Exception:
            pass

        # Лог анализа
        _log_enabled = bool(s.get('log_enabled', True))
        if hasattr(self, '_log_btn'):
            st = 'ВКЛ' if _log_enabled else 'ВЫКЛ'
            self._log_btn.config(text=f'📋  Лог анализа: {st}')

        # Debug Monitor — master флаг
        _dbg_enabled = bool(s.get('dbg_enabled', True))
        if hasattr(self, '_dbg_btn'):
            st = 'ВКЛ' if _dbg_enabled else 'ВЫКЛ'
            self._dbg_btn.config(text=f'🛠  Debug Monitor: {st}')
        # v3.1.14: per-category toggles в DevTools — загружаем из settings.json
        saved_cats = s.get('dbg_cats') or {}
        _DBG_CATS.update(saved_cats)
        # Обновить тексты кнопок категорий если DevTools уже открыт
        for cat, btn in getattr(self, '_dbg_cat_btns', {}).items():
            st = 'ВКЛ' if _DBG_CATS.get(cat, True) else 'ВЫКЛ'
            btn.config(text=f'{cat}: {st}')
        if _dbg_enabled:
            _dbg_open_session()

        # Яркость
        try:
            self._bright_var.set(s.get('brightness', 1.0))
            if USE_GL:
                self.viewport.brightness = self._bright_var.get()
        except Exception:
            pass

        # Освещение
        if USE_GL:
            want_light = bool(s.get('lighting', True))
            if self.viewport.lighting != want_light:
                self.viewport.lighting = want_light
                st = 'ВКЛ' if want_light else 'ВЫКЛ'
                if hasattr(self, '_light_btn'):
                    self._light_btn.config(text=f'💡  Освещение: {st}')

            # v3.1.7: alpha blending теперь per-material — загружаем dict в viewport
            self.viewport._alpha_mats = dict(s.get('alpha_mats', {}))

            # v3.0: инициализируем mesh mirror uniforms из settings при старте
            self.viewport._mirror_uv_enabled = bool(s.get('mirror_uv', True))
            self.viewport._flip_x_enabled = bool(s.get('flip_x', False))
            self.viewport._flip_y_enabled = bool(s.get('flip_y', False))
            self.viewport._flip_z_enabled = bool(s.get('flip_z', False))
            dbg_cat('render', f'init flip uniforms: mirror_uv={self.viewport._mirror_uv_enabled} x={self.viewport._flip_x_enabled} y={self.viewport._flip_y_enabled} z={self.viewport._flip_z_enabled}')

        # Применяем сохранённые кастомные цвета
        saved_custom = self._app_settings.get('custom_colors', {})
        if saved_custom:
            for k, v in saved_custom.items():
                if k in self.theme:
                    self.theme[k] = v
            self._apply_theme()

        # Применяем сохранённый шрифт
        saved_font_fam = self._app_settings.get('font_fam', '')
        saved_font_sz  = self._app_settings.get('font_sz', 0)
        if saved_font_fam and saved_font_fam != self.theme.get('font_fam', ''):
            self.theme['font_fam'] = saved_font_fam
            self._apply_theme()
        if saved_font_sz and saved_font_sz != self.theme.get('font_sz', 0):
            self.theme['font_sz'] = saved_font_sz
            self._apply_theme()

        # Стили текста — применяем если сохранены
        if self._app_settings.get('text_styles'):
            self._apply_theme()

        # Применяем сохранённый градиент
        if self._app_settings.get('gradient_enabled', False):
            color2 = self._app_settings.get('gradient_color2', self.theme.get('bg_main', '#000000'))
            self.root.after(300, lambda: self._apply_sidebar_gradient(
                self.theme.get('bg_side', '#000000'), color2))

    def _discover_widgets(self):
        """v3.1.9: walk все собранные theme widgets и зарегистрировать их в _widget_registry.

        key = "L_<sanitized_text>_<counter>" для Label/Checkbutton
        key = "B_<sanitized_text>_<counter>" для Button
        Counter защищает от дубликатов при одном тексте в нескольких местах.
        """
        import re as _re
        registry = {}
        counter = {'L': 0, 'B': 0, 'C': 0}

        def _safe_cget(w, key):
            try: return w.cget(key)
            except Exception: return None

        def _normalize(text):
            if not text: return ''
            t = str(text).strip()
            t = _re.sub(r'\s+', '_', t)
            t = _re.sub(r'[^\w_\-]', '', t)
            return t[:40] or 'item'

        # Theme-collected widgets — основная масса
        for entry in (
            getattr(self, '_tw_labels', []),
            getattr(self, '_tw_checkbuttons', []),
        ):
            for tup in entry:
                w = tup[0] if isinstance(tup, tuple) else tup
                if w is None: continue
                try:
                    if not w.winfo_exists(): continue
                except Exception:
                    continue
                text = _safe_cget(w, 'text')
                if not text or str(text).strip() == '': continue
                key = f'L_{_normalize(text)}_{counter["L"]}'
                counter['L'] += 1
                registry[key] = (w, str(text))

        for tup in getattr(self, '_tw_buttons', []):
            w = tup[0] if isinstance(tup, tuple) else tup
            if w is None: continue
            try:
                if not w.winfo_exists(): continue
            except Exception:
                continue
            text = _safe_cget(w, 'text')
            if not text or str(text).strip() == '': continue
            key = f'B_{_normalize(text)}_{counter["B"]}'
            counter['B'] += 1
            registry[key] = (w, str(text))

        self._widget_registry = registry
        _dbg(f'_discover_widgets: {len(registry)} entries')

    def _apply_custom_labels(self):
        """v3.1.9: применить custom_labels из settings к UI виджетам."""
        custom = self._app_settings.get('custom_labels', {}) if hasattr(self, '_app_settings') else {}
        if not custom:
            return
        applied = 0
        for key, new_text in custom.items():
            entry = self._widget_registry.get(key) if hasattr(self, '_widget_registry') else None
            if entry is None: continue
            widget, _orig = entry
            try:
                widget.config(text=str(new_text))
                applied += 1
            except Exception as _e:
                _dbg(f'_apply_custom_labels: key={key} ERROR {_e}')
        if applied:
            _dbg(f'_apply_custom_labels: applied {applied}')

    def _open_dev_tools(self):
        """v3.1.9: открыть Toplevel "Инструменты разработчика" — toggle dev_mode + редактор labels."""
        try:
            # Если уже открыто — поднять
            if hasattr(self, '_dev_win') and self._dev_win and self._dev_win.winfo_exists():
                self._dev_win.lift()
                return
        except Exception:
            self._dev_win = None

        T = self._T()
        win = tk.Toplevel(self.root)
        win.title('🛠  Инструменты разработчика')
        win.geometry('780x560+120+120')
        win.configure(bg=T['bg_side'])
        self._dev_win = win

        def _close():
            try: win.destroy()
            except Exception: pass
            self._dev_win = None
        win.protocol('WM_DELETE_WINDOW', _close)

        # ── Section A: Режим разработчика (toggle) ────────────────────────────
        sec_dev = tk.Frame(win, bg=T['bg_side'])
        sec_dev.pack(fill=tk.X, padx=10, pady=(10, 4))
        dev_hdr = tk.Label(sec_dev, text='🛠 РЕЖИМ РАЗРАБОТЧИКА',
                           bg=T['bg_side'], fg=T['fg_label'],
                           font=(T['font_fam'], T['font_sz'], 'bold'))
        dev_hdr.pack(anchor='w')

        dev_chk_var = tk.BooleanVar(value=bool(self._app_settings.get('dev_mode', False)))
        chk_row = tk.Frame(sec_dev, bg=T['bg_side'])
        chk_row.pack(fill=tk.X, pady=(4, 4))
        self._tw_frames.append((chk_row, 'bg_side'))

        def _toggle_dev():
            new_val = dev_chk_var.get()
            self._app_settings['dev_mode'] = new_val
            _save_app_settings(self._app_settings)
            self._status(f'Режим разработчика: {"ВКЛ" if new_val else "ВЫКЛ"}',
                         '#a6e3a1' if new_val else '#f38ba8')
            # При вкл: пересобрать registry чтобы захватить свежее
            if new_val:
                # Discover для свежего захвата виджетов, но refresh вызовем позже (когда tree создан)
                try:
                    self._discover_widgets()
                except Exception as _e:
                    _dbg(f'_discover_widgets on dev_toggle FAILED: {_e}')

        dev_chk = tk.Checkbutton(chk_row,
            text='✅ Включить режим разработчика (показывает Debug Monitor и редактор UI)',
            variable=dev_chk_var, command=_toggle_dev,
            bg=T['bg_side'], fg=T['fg_text'], selectcolor=T['bg_widget'],
            activebackground=T['bg_side'], activeforeground=T['fg_text'],
            font=(T['font_fam'], T['font_sz'] - 1))
        dev_chk.pack(anchor='w')
        self._tw_checkbuttons.append((dev_chk, 'normal'))

        # ── Section B: КАТЕГОРИИ ЛОГОВ (per-category toggles, v3.1.14) ─────────────
        sep_dbg = tk.Frame(win, bg=T['bg_sep'], height=1)
        sep_dbg.pack(fill=tk.X, padx=10, pady=(6, 4))
        self._tw_frames.append((sep_dbg, 'bg_sep'))

        cats_hdr = tk.Label(win,
            text='📊 КАТЕГОРИИ ЛОГОВ (Debug Monitor в sidebar = master switch)',
            bg=T['bg_side'], fg=T['fg_label'],
            font=(T['font_fam'], T['font_sz'] - 1, 'bold'))
        cats_hdr.pack(anchor='w', padx=10, pady=(0, 2))
        self._tw_labels.append((cats_hdr, 'bg_side', 'fg_label', 'bold'))

        cats_hint = tk.Label(win,
            text='Каждая категория ВКЛ/ВЫКЛ отдельно. Кнопки ниже — toggles.',
            bg=T['bg_side'], fg=T['fg_muted'],
            font=(T['font_fam'], T['font_sz'] - 2))
        cats_hint.pack(anchor='w', padx=10, pady=(0, 4))
        self._tw_labels.append((cats_hint, 'bg_side', 'fg_muted', 'small'))

        # v3.1.14: per-category TOGGLEABLE buttons (как было в v3.1.10 sidebar)
        self._dbg_cat_btns = {}
        cats_frame = tk.Frame(win, bg=T['bg_side'])
        cats_frame.pack(fill=tk.X, padx=10, pady=(0, 4))
        self._tw_frames.append((cats_frame, 'bg_side'))

        def _update_cat_btn(cat):
            """Обновить текст кнопки категории."""
            if cat in self._dbg_cat_btns:
                st = 'ВКЛ' if _DBG_CATS.get(cat, True) else 'ВЫКЛ'
                self._dbg_cat_btns[cat].config(text=f'{cat}: {st}')

        def _toggle_cat(cat):
            """v3.1.14: toggle per-category debug флаг."""
            global _DBG_CATS
            _DBG_CATS[cat] = not _DBG_CATS.get(cat, True)
            _update_cat_btn(cat)
            try:
                _save_app_settings(self._collect_settings())
            except Exception:
                pass
            st = 'ВКЛ' if _DBG_CATS[cat] else 'ВЫКЛ'
            dbg(f'dbg_cat[{cat}] toggled → {st}')

        cat_names = list(_DBG_CATS.keys())
        for i, cat in enumerate(cat_names):
            row, col = divmod(i, 2)
            st = 'ВКЛ' if _DBG_CATS.get(cat, True) else 'ВЫКЛ'
            # v3.1.14 FIX: фиксированная ширина кнопки (width=14 символов)
            # Без этого .grid(sticky='ew') пересчитывает layout при toggle ВКЛ/ВЫКЛ
            # и соседние кнопки смещаются влево/вправо.
            btn = tk.Button(cats_frame, text=f'{cat}: {st}',
                           command=lambda c=cat: _toggle_cat(c),
                           bg=T['btn_bg'], fg=T['btn_fg'],
                           font=(T['font_fam'], T['font_sz']),
                           relief='flat', padx=8, pady=4, bd=0,
                           width=14, anchor='w', cursor='hand2',
                           activebackground=T['btn_hover'],
                           activeforeground=T['btn_fg'])
            btn.grid(row=row, column=col, padx=2, pady=2, sticky='w')
            self._dbg_cat_btns[cat] = btn
            self._tw_buttons.append((btn, 'small'))
        # v3.1.14 FIX: НЕ растягиваем колонки — фиксированная ширина кнопок
        # cats_frame.grid_columnconfigure(0, weight=1)
        # cats_frame.grid_columnconfigure(1, weight=1)

        # ── Section C: Редактор UI текстов (active только если dev_mode=True) ──
        sep_lbl = tk.Frame(win, bg=T['bg_sep'], height=1)
        sep_lbl.pack(fill=tk.X, padx=10, pady=(6, 4))
        self._tw_frames.append((sep_lbl, 'bg_sep'))

        edit_hdr = tk.Label(win,
            text='✏️  РЕДАКТОР ТЕКСТОВ (включите dev_mode выше для редактирования)',
            bg=T['bg_side'], fg=T['fg_label'],
            font=(T['font_fam'], T['font_sz'] - 1, 'bold'))
        edit_hdr.pack(anchor='w', padx=10)

        # v3.1.10: Treeview (ВМЕСТО Listbox + Entry) — кнопочный вариант
        # Человек не может сломать UI через свободный ввод — все правки через mini-dialog + confirm
        from tkinter import ttk
        from tkinter import messagebox

        list_frame = tk.Frame(win, bg=T['bg_side'])
        list_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(4, 4))
        self._tw_frames.append((list_frame, 'bg_side'))

        tree = ttk.Treeview(list_frame,
            columns=('id', 'original', 'override'), show='headings', height=18)
        tree.heading('id', text='ID')
        tree.heading('original', text='Оригинал')
        tree.heading('override', text='Текущее значение')
        tree.column('id', width=160, anchor='w')
        tree.column('original', width=240, anchor='w')
        tree.column('override', width=240, anchor='w')
        tree.tag_configure('overridden', foreground='#a6e3a1')
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        tree_sb = ttk.Scrollbar(list_frame, orient='vertical', command=tree.yview)
        tree_sb.pack(side=tk.LEFT, fill=tk.Y)
        tree.configure(yscrollcommand=tree_sb.set)

        # Selected info label — показывает оригинал и текущее значение выбранного
        sel_lbl = tk.Label(win,
            text='Выберите элемент в таблице ниже и нажмите кнопку внизу',
            bg=T['bg_widget'], fg=T['fg_text'], anchor='w',
            font=(T['font_fam'], T['font_sz'] - 1), wraplength=700,
            justify=tk.LEFT, relief='flat')
        sel_lbl.pack(fill=tk.X, padx=10, pady=(0, 6), ipady=6, ipadx=4)

        def _tree_refresh():
            for iid in tree.get_children():
                tree.delete(iid)
            if not hasattr(self, '_widget_registry') or not self._widget_registry:
                self._discover_widgets()
            # v3.1.11 FIX: защита от custom_labels=None (corrupted settings.json)
            custom_raw = self._app_settings.get('custom_labels', {})
            custom = custom_raw if isinstance(custom_raw, dict) else {}
            for key in sorted(self._widget_registry.keys()):
                widget, orig = self._widget_registry[key]
                override = custom.get(key, '')
                tag = ('overridden',) if override else ()
                d_orig = (orig[:55] + '...') if len(str(orig)) > 55 else str(orig)
                d_ovr = ((override[:55] + '...') if len(str(override)) > 55 else str(override)) if override else '— не изменён —'
                tree.insert('', 'end', iid=key, values=(key, d_orig, d_ovr), tags=tag)

        def _selected_key():
            sel = tree.selection()
            return sel[0] if sel else None

        def _on_tree_select(event=None):
            key = _selected_key()
            if not key or key not in self._widget_registry:
                return
            widget, orig = self._widget_registry[key]
            custom = self._app_settings.get('custom_labels', {})
            override = custom.get(key, '')
            if override:
                sel_lbl.config(text=f'[{key}]' + '\\nОРИГИНАЛ: ' + repr(orig) + '\\nТЕКУЩЕЕ: ' + repr(override))
            else:
                sel_lbl.config(text=f'[{key}]' + '\\nОРИГИНАЛ: ' + repr(orig) + '\\n(не изменён)')

        tree.bind('<<TreeviewSelect>>', _on_tree_select)

        def _open_edit_dialog():
            """v3.1.10: кнопочное редактирование — открывает mini-dialog (НЕ свободный Entry на главном экране)."""
            key = _selected_key()
            if not key:
                self._status('Сначала выберите элемент в таблице', '#f9e2af')
                return
            if key not in self._widget_registry:
                return
            widget, orig = self._widget_registry[key]
            custom = self._app_settings.get('custom_labels', {})
            current_val = custom.get(key, orig)

            dlg = tk.Toplevel(win)
            dlg.title(f'Редактирование: {key}')
            dlg.geometry('480x200+160+160')
            dlg.transient(win)
            dlg.configure(bg=T['bg_side'])
            dlg.grab_set()

            tk.Label(dlg, text=f'ID: {key}',
                     bg=T['bg_side'], fg=T['fg_muted'],
                     font=(T['font_fam'], T['font_sz'] - 2)).pack(anchor='w', padx=14, pady=(14, 2))
            tk.Label(dlg, text=f'Оригинал: {orig!r}',
                     bg=T['bg_side'], fg=T['fg_text'],
                     font=(T['font_fam'], T['font_sz'] - 1),
                     wraplength=440, justify=tk.LEFT).pack(anchor='w', padx=14, pady=(2, 8))

            new_var = tk.StringVar(value=current_val)
            ef = tk.Frame(dlg, bg=T['bg_side'])
            ef.pack(fill=tk.X, padx=14)
            tk.Label(ef, text='Новый текст:',
                     bg=T['bg_side'], fg=T['fg_label'],
                     font=(T['font_fam'], T['font_sz'] - 1)).pack(anchor='w')
            entry = tk.Entry(ef, textvariable=new_var,
                            bg=T['bg_widget'], fg=T['fg_text'],
                            insertbackground=T['fg_text'], relief='flat',
                            font=(T['font_fam'], T['font_sz']))
            entry.pack(fill=tk.X, pady=2, ipady=4)
            entry.focus_set()
            entry.select_range(0, tk.END)

            dlg_res = {'applied': False, 'cancelled': False}

            def _do_apply():
                text = new_var.get().strip()
                if not text:
                    messagebox.showwarning('Пустой текст', 'Введите непустой текст', parent=dlg)
                    return
                try:
                    widget.config(text=text)
                except Exception as _e:
                    messagebox.showerror('Ошибка', f'Не удалось применить: {_e}', parent=dlg)
                    return
                custom = self._app_settings.get('custom_labels', {})
                custom[key] = text
                self._app_settings['custom_labels'] = custom
                _save_app_settings(self._app_settings)
                dlg_res['applied'] = True
                dlg.destroy()

            def _do_cancel():
                dlg_res['cancelled'] = True
                dlg.destroy()

            dlg.bind('<Return>', lambda e: _do_apply())
            dlg.bind('<Escape>', lambda e: _do_cancel())
            dlg.protocol('WM_DELETE_WINDOW', _do_cancel)

            dbr = tk.Frame(dlg, bg=T['bg_side'])
            dbr.pack(fill=tk.X, padx=14, pady=(8, 14))
            tk.Button(dbr, text='💾 Apply (Enter)', relief='flat',
                      bg=T['btn_bg'], fg=T['btn_fg'],
                      font=(T['font_fam'], T['font_sz']),
                      padx=14, pady=5, command=_do_apply).pack(side=tk.LEFT, padx=(0, 6))
            tk.Button(dbr, text='✕ Cancel (Esc)', relief='flat',
                      bg=T['bg_widget'], fg='#f38ba8',
                      font=(T['font_fam'], T['font_sz']),
                      padx=14, pady=5, command=_do_cancel).pack(side=tk.LEFT)

            win.wait_window(dlg)

            if dlg_res['applied']:
                _tree_refresh()
                tree.selection_set(key)
                tree.see(key)
                self._status(f'✅ [{key}] заменён', '#a6e3a1')

        def _do_revert_selected():
            """С confirm dialog."""
            key = _selected_key()
            if not key:
                self._status('Сначала выберите элемент', '#f9e2af')
                return
            if key not in self._app_settings.get('custom_labels', {}):
                self._status(f'[{key}] не изменён — нечего сбрасывать', '#f9e2af')
                return
            if not messagebox.askyesno(
                'Сбросить изменение?',
                f'Сбросить "{key}" к оригиналу?',
                parent=win, icon='question'):
                return
            widget, orig = self._widget_registry[key]
            if 'custom_labels' in self._app_settings and key in self._app_settings['custom_labels']:
                del self._app_settings['custom_labels'][key]
                _save_app_settings(self._app_settings)
            try: widget.config(text=orig)
            except Exception: pass
            _tree_refresh()
            tree.selection_set(key)
            self._status(f'🔄 [{key}] сброшен к оригиналу', '#89b4fa')

        def _do_revert_all():
            """С confirm dialog. Строгое двойное подтверждение."""
            custom = self._app_settings.get('custom_labels', {})
            if not custom:
                self._status('Нечего сбрасывать — нет изменений', '#f9e2af')
                return
            n = len(custom)
            if not messagebox.askyesno(
                '⚠️ Сбросить ВСЁ?',
                f'Сбросить ВСЕ {n} переопределений текстов? Это ДЕЙСТВИЕ необратимо.',
                parent=win, icon='warning'):
                return
            if not messagebox.askyesno(
                '⚠️ Точно?',
                f'Подтвердите: сбросить {n} переопределений. Продолжить?',
                parent=win, icon='warning'):
                return
            self._app_settings['custom_labels'] = {}
            _save_app_settings(self._app_settings)
            for kk, (ww, oo) in self._widget_registry.items():
                try: ww.config(text=oo)
                except Exception: pass
            _tree_refresh()
            self._status(f'🧹 Сброшено {n} переопределений', '#89b4fa')

        def _do_discover():
            self._discover_widgets()
            _tree_refresh()
            self._status(f'🔍 Re-discover: {len(self._widget_registry)} виджетов', '#a6e3a1')

        # КНОПКИ ДЕЙСТВИЙ
        btn_row = tk.Frame(win, bg=T['bg_side'])
        btn_row.pack(fill=tk.X, padx=10, pady=(0, 12))
        self._tw_frames.append((btn_row, 'bg_side'))

        tk.Button(btn_row, text='✏️  Заменить текст', relief='flat',
                  bg='#a6e3a1', fg='#1e1e2e',
                  font=(T['font_fam'], T['font_sz'], 'bold'),
                  padx=14, pady=6, command=_open_edit_dialog).pack(side=tk.LEFT, padx=(0, 4))
        tk.Button(btn_row, text='↩  Сбросить этот', relief='flat',
                  bg=T['bg_widget'], fg=T['fg_text'],
                  font=(T['font_fam'], T['font_sz'] - 1),
                  padx=10, pady=4, command=_do_revert_selected).pack(side=tk.LEFT, padx=(0, 4))
        tk.Button(btn_row, text='🧹  Сбросить ВСЁ', relief='flat',
                  bg=T['bg_widget'], fg='#f38ba8',
                  font=(T['font_fam'], T['font_sz'] - 1),
                  padx=10, pady=4, command=_do_revert_all).pack(side=tk.LEFT, padx=(0, 4))
        tk.Button(btn_row, text='🔍  Re-discover', relief='flat',
                  bg=T['bg_widget'], fg=T['fg_text'],
                  font=(T['font_fam'], T['font_sz'] - 1),
                  padx=10, pady=4, command=_do_discover).pack(side=tk.LEFT, padx=(0, 4))
        tk.Button(btn_row, text='✕  Закрыть', relief='flat',
                  bg=T['bg_widget'], fg='#f38ba8',
                  font=(T['font_fam'], T['font_sz'] - 1),
                  padx=10, pady=4, command=_close).pack(side=tk.RIGHT)

        # v3.1.10: defensive — убеждаемся что registry populated (могло не сработать в _startup если UI ещё не готов)
        try:
            if not hasattr(self, '_widget_registry') or not self._widget_registry:
                self._discover_widgets()
        except Exception as _e:
            _dbg(f'open_dev_tools _discover_widgets FAILED: {_e}')
            self._widget_registry = {}

        # v3.1.11 FIX: try/except вокруг _tree_refresh + diagnostic (раньше падал silent → tree пустой)
        try:
            _tree_refresh()
        except Exception as _e:
            _dbg(f'_tree_refresh FAILED: {_e}')
            self._status(f'❌ Tree refresh упал: {_e}', '#f38ba8')

        # Status info — KirrieD видит сколько widgets найдено
        n = len(self._widget_registry)
        if n == 0:
            self._status('⚠️ 0 виджетов в registry — попробуй 🔍 Re-discover', '#f38ba8')
        elif n < 10:
            self._status(f'Dev Tools: {n} виджетов (мало) — попробуй Re-discover', '#f9e2af')
        else:
            self._status(f'Dev Tools: {n} виджетов в registry ✓', '#a6e3a1')

    def _collect_settings(self):
        """Собирает текущие настройки для сохранения."""
        sidebar_w = self._app_settings.get('sidebar_width', 350)
        try:
            sidebar_w = self._side_outer.winfo_width()
        except Exception:
            pass
        return {
            'log_enabled':   _log_enabled,
            'dbg_enabled':   _dbg_enabled,
            'dbg_cats':      dict(_DBG_CATS),
            'lighting':      self.viewport.lighting if USE_GL else True,
            # 'alpha' deprecated — оставлен в defaults для обратной совместимости, но больше не используется
            'alpha_mats':    dict(getattr(self.viewport, '_alpha_mats', {})),
            'brightness':    self._bright_var.get() if hasattr(self, '_bright_var') else 1.0,
            'geometry':      self.root.geometry(),
            'sidebar_width': sidebar_w,
        }

    def _on_close(self):
        """Сохраняет настройки и закрывает приложение. v2.3: корректно гасит subprocess."""
        # Отменяем опрос загрузок
        if self._load_poll_after is not None:
            try: self.root.after_cancel(self._load_poll_after)
            except Exception: pass
            self._load_poll_after = None
        # Terminate все subprocess-загрузки
        for idx, info in list(self._load_procs.items()):
            try:
                proc = info['proc']
                if proc.poll() is None:
                    proc.terminate()
            except Exception:
                pass
        self._load_procs.clear()
        # Обновляем текущие значения поверх сохранённых (тема, кастомные цвета и пр. уже в _app_settings)
        collected = self._collect_settings()
        self._app_settings.update(collected)
        try:
            self._app_settings['sidebar_width'] = self._side_outer.winfo_width()
        except Exception:
            pass
        _save_app_settings(self._app_settings)
        self.root.destroy()

    def _toggle_log(self):
        global _log_enabled
        _log_enabled = not _log_enabled
        st = 'ВКЛ' if _log_enabled else 'ВЫКЛ'
        self._log_btn.config(text=f'📋  Лог анализа: {st}')
        _save_app_settings(self._collect_settings())

    def _clear_errors_log(self):
        """Очищает лог ошибок и сбрасывает dismissed-флаг."""
        try:
            err_path = os.path.join(LOG_DIR, 'load_errors.txt')
            if os.path.exists(err_path):
                os.remove(err_path)
        except Exception:
            pass
        self._errors_log = []
        self._errors_dismissed = False
        # v3.1.9: сброс persistent showed_errors (ручная очистка → ошибки снова покажутся)
        self._app_settings['showed_errors'] = []
        _save_app_settings(self._app_settings)
        self._update_err_btn_state()

    def _update_err_btn_state(self):
        """Обновляет текст/состояние кнопки ошибок (показывает сколько ошибок)."""
        if not hasattr(self, '_err_btn'):
            return
        n = len(getattr(self, '_errors_log', []))
        if n == 0:
            self._err_btn.config(text='⚠️  Ошибки загрузки (нет)')
            self._err_btn.config(state='disabled')
        else:
            self._err_btn.config(text=f'⚠️  Ошибки загрузки ({n})')
            self._err_btn.config(state='normal')

    def _refresh_per_face_flip_uniform(self):
        """v3.0: deprecated (mirror UV теперь всегда в shader)."""
        pass

    def _refresh_mirror_uv_uniform(self):
        """v3.0: обновляет состояние зеркала UV в viewport."""
        if hasattr(self, 'viewport') and self.viewport is not None:
            self.viewport._mirror_uv_enabled = self._mirror_uv_var.get()
            try:
                self.viewport.redraw()
            except Exception:
                pass

    def _refresh_flip_uniform(self, axis):
        """v3.0: mesh mirror по оси X/Y/Z."""
        if hasattr(self, 'viewport') and self.viewport is not None:
            var = getattr(self, f'_flip_{axis}_var', None)
            setattr(self.viewport, f'_flip_{axis}_enabled', var.get() if var else False)
            dbg_cat('render', f'flip_{axis} toggle → {var.get() if var else False}')
            try:
                self.viewport.redraw()
            except Exception:
                pass

    # ── Debug Monitor ────────────────────────────────────────────────────────

    def _poll_dbg_queue(self):
        """Периодически читает очередь и вставляет строки в монитор."""
        try:
            while True:
                line = _dbg_queue.get_nowait()
                if self._dbg_win and self._dbg_win.winfo_exists():
                    self._dbg_txt.configure(state='normal')
                    self._dbg_txt.insert(tk.END, line + '\n')
                    self._dbg_txt.see(tk.END)
                    self._dbg_txt.configure(state='disabled')
        except queue.Empty:
            pass
        self.root.after(100, self._poll_dbg_queue)

    def _toggle_right_panel(self):
        """v0.1.2: показать/скрыть right_panel (Загружено)."""
        if self._right_panel_visible:
            self._right_panel.pack_forget()
            self._right_panel_visible = False
            self._rp_toggle_btn.config(text='◀')
        else:
            # pack ПОСЛЕ viewport (side=LEFT), чтобы быть справа
            self._right_panel.pack(side=tk.LEFT, fill=tk.Y)
            self._right_panel_visible = True
            self._rp_toggle_btn.config(text='▶')

    def _toggle_debug_monitor(self):
        global _dbg_enabled
        if self._dbg_win and self._dbg_win.winfo_exists():
            self._dbg_win.destroy()
            self._dbg_win = None
            _dbg_enabled = False
            self._dbg_btn.config(text='🛠  Debug Monitor: ВЫКЛ')
            _save_app_settings(self._collect_settings())
            return
        _dbg_enabled = True
        self._dbg_btn.config(text='🛠  Debug Monitor: ВКЛ')
        _dbg_open_session()
        _save_app_settings(self._collect_settings())
        T = self._T()
        win = tk.Toplevel(self.root)
        win.title('Debug Monitor')
        win.geometry('820x480')
        win.configure(bg=T['bg_main'])
        self._dbg_win = win

        # Toolbar
        bar = tk.Frame(win, bg=T['bg_side'])
        bar.pack(fill=tk.X)
        tk.Label(bar, text=f'debuglog/ → {DBG_DIR}',
                 bg=T['bg_side'], fg=T['fg_label'],
                 font=(T['font_fam'], T['font_sz'] - 1)).pack(side=tk.LEFT, padx=8, pady=4)
        tk.Button(bar, text='Очистить', command=self._dbg_clear,
                  bg=T['btn_bg'], fg=T['btn_fg'], relief='flat',
                  font=(T['font_fam'], T['font_sz']), padx=6, pady=2).pack(side=tk.RIGHT, padx=4, pady=4)
        tk.Button(bar, text='Открыть папку', command=lambda: subprocess.Popen(['explorer', DBG_DIR]),
                  bg=T['btn_bg'], fg=T['btn_fg'], relief='flat',
                  font=(T['font_fam'], T['font_sz']), padx=6, pady=2).pack(side=tk.RIGHT, padx=4, pady=4)

        # Текстовое поле
        self._dbg_txt = scrolledtext.ScrolledText(
            win, bg='#0d0d14', fg='#a8e6a3', insertbackground='white',
            font=('Consolas', 9), state='disabled', wrap=tk.WORD)
        self._dbg_txt.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        # Вставить уже накопленные строки из файла
        try:
            if _dbg_file:
                _dbg_file.flush()
                log_path = _dbg_file.name
                with open(log_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                self._dbg_txt.configure(state='normal')
                self._dbg_txt.insert(tk.END, content)
                self._dbg_txt.see(tk.END)
                self._dbg_txt.configure(state='disabled')
        except Exception:
            pass

    def _dbg_clear(self):
        if self._dbg_txt:
            self._dbg_txt.configure(state='normal')
            self._dbg_txt.delete('1.0', tk.END)
            self._dbg_txt.configure(state='disabled')

    def _bind_scroll(self, widget):
        """Привязывает колёсико мыши к прокрутке сайдбара для widget и детей."""
        def _scroll(e):
            self._side_canvas.yview_scroll(int(-1*(e.delta/120)), 'units')
        widget.bind('<MouseWheel>', _scroll, add='+')
        for child in widget.winfo_children():
            self._bind_scroll(child)

    def _make_collapsible_section(self, parent, title, settings_key, default_open=True):
        """
        Создаёт секцию с кнопкой-заголовком для сворачивания/разворачивания.
        Возвращает (content_frame, toggle_fn).
        """
        T = self._T()
        is_open = self._app_settings.get(settings_key, default_open)

        outer = tk.Frame(parent, bg=T['bg_side'])
        outer.pack(fill=tk.X)

        toggle_var = [is_open]

        hdr_btn = tk.Button(outer, relief='flat', bg=T['bg_side'], fg=T['fg_label'],
                            font=(T['font_fam'], T['font_sz'] - 1),
                            anchor='w', padx=8, pady=4,
                            activebackground=T['bg_widget'])
        hdr_btn.pack(fill=tk.X)

        content = tk.Frame(outer, bg=T['bg_side'])

        def _update_label():
            arrow = '▼' if toggle_var[0] else '▶'
            hdr_btn.config(text=f'{arrow}  {title}')

        def _toggle():
            toggle_var[0] = not toggle_var[0]
            if toggle_var[0]:
                content.pack(fill=tk.X)
            else:
                content.pack_forget()
            _update_label()
            self._app_settings[settings_key] = toggle_var[0]
            _save_app_settings(self._app_settings)

        hdr_btn.config(command=_toggle)
        _update_label()

        if is_open:
            content.pack(fill=tk.X)

        sep = tk.Frame(outer, bg=T['bg_sep'], height=1)
        sep.pack(fill=tk.X)

        return content, _toggle

    def _make_section(self, name):
        """Создать контейнер для секции и зарегистрировать его."""
        # Маппинг имён секций на ключи настроек и заголовки для collapsible
        _collapsible_map = {
            'МАТЕРИАЛЫ':              ('section_mats',     'Материалы'),
            'ТЕКСТУРЫ (ручной выбор)':('section_textures', 'Текстуры'),
            # v3.1.8: 'ВИД' удалён — Освещение/Яркость теперь внутри Текстуры
        }
        if name in _collapsible_map:
            settings_key, title = _collapsible_map[name]
            content, _ = self._make_collapsible_section(self._side, title, settings_key)
            self._section_frames[name] = content
            self._tw_frames.append((content, 'bg_side'))
            content.bind('<Configure>', lambda e, ff=content: self._bind_scroll(ff))
            return content

        T = self._T()
        f = tk.Frame(self._side, bg=T['bg_side'])
        self._section_frames[name] = f
        self._tw_frames.append((f, 'bg_side'))
        f.pack(fill=tk.X)
        # Привяжем скролл после того как контент добавлен (via after)
        f.bind('<Configure>', lambda e, ff=f: self._bind_scroll(ff))
        return f

    def _build_section_ФАЙЛ(self, parent=None):
        f = parent or self._make_section('ФАЙЛ')
        _b = self._btn(f, '📂  Открыть P3D', self._open_p3d)
        _b.pack(fill=tk.X, padx=10, pady=3)
        self._open_buttons.append(_b)
        self._btn(f, '💾  Экспорт в OBJ', self._export, accent=True).pack(fill=tk.X, padx=10, pady=3)

    def _build_section_ТЕКСТУРЫ(self, parent=None):
        f = parent or self._make_section('ТЕКСТУРЫ (ручной выбор)')
        T = self._T()
        self._sep(f); self._lbl(f, 'ТЕКСТУРЫ (ручной выбор)')
        _b2 = self._btn(f, '🎨  Диффуз (_co.paa)', self._open_paa)
        _b2.pack(fill=tk.X, padx=10, pady=3)
        self._open_buttons.append(_b2)
        _b3 = self._btn(f, '🗺  Нормаль (_nohq)', self._open_nrm)
        _b3.pack(fill=tk.X, padx=10, pady=3)
        self._open_buttons.append(_b3)
        _b4 = self._btn(f, '📋  Материал (.rvmat)', self._open_rvmat)
        _b4.pack(fill=tk.X, padx=10, pady=3)
        self._open_buttons.append(_b4)
        self._btn(f, '✖  Убрать текстуры',   self._clear_all_tex).pack(fill=tk.X, padx=10, pady=3)
        self._ind_frame = tk.Frame(f, bg=T['bg_widget'])
        self._ind_frame.pack(fill=tk.X, padx=10, pady=(4, 0))
        self._tw_frames.append((self._ind_frame, 'bg_widget'))
        self._diff_ind = tk.Label(self._ind_frame, text='● Диффуз',  bg=T['bg_widget'],
                                  fg=T['fg_muted'], font=(T['font_fam'], T['font_sz'] - 1))
        self._diff_ind.pack(side=tk.LEFT, padx=6, pady=4)
        self._nrm_ind  = tk.Label(self._ind_frame, text='● Нормаль', bg=T['bg_widget'],
                                  fg=T['fg_muted'], font=(T['font_fam'], T['font_sz'] - 1))
        self._nrm_ind.pack(side=tk.LEFT, padx=6, pady=4)

        # v3.1.8: перенесено из ВИД — Освещение + Яркость в Текстуры (удалена отдельная секция ВИД)
        self._sep(f)
        self._lbl(f, 'ОТОБРАЖЕНИЕ')
        self._light_btn = self._btn(f, '💡  Освещение: ВКЛ', self._toggle_light)
        self._light_btn.pack(fill=tk.X, padx=16, pady=3)
        bright_f = tk.Frame(f, bg=T['bg_side'])
        bright_f.pack(fill=tk.X, padx=16, pady=(2, 4))
        self._tw_frames.append((bright_f, 'bg_side'))
        bright_lbl = tk.Label(bright_f, text='☀  Яркость', bg=T['bg_side'], fg=T['fg_label'],
                              font=(T['font_fam'], T['font_sz'] - 1))
        bright_lbl.pack(anchor='w')
        self._tw_labels.append((bright_lbl, 'bg_side', 'fg_label', 'normal'))
        self._bright_var = tk.DoubleVar(value=1.0)
        self._bright_scale = tk.Scale(
            bright_f, variable=self._bright_var,
            from_=0.1, to=3.0, resolution=0.05, orient=tk.HORIZONTAL,
            bg=T['bg_side'], fg=T['fg_text'], troughcolor=T['bg_widget'],
            highlightthickness=0, bd=0, sliderrelief=tk.FLAT,
            command=self._on_brightness)
        self._bright_scale.pack(fill=tk.X)
        self._tw_scales.append(self._bright_scale)

    # v3.1.8: секция ВИД удалена — Освещение + Яркость перенесены в Текстуры

    def _build_section_КОНВЕРТАЦИЯ(self, parent=None):
        f = parent or self._make_section('КОНВЕРТАЦИЯ')
        self._sep(f); self._lbl(f, 'КОНВЕРТАЦИЯ')
        self._btn(f, '🖼  PAA → PNG (модель)',   self._convert_paa_to_png).pack(fill=tk.X, padx=16, pady=3)
        self._btn(f, '🖼  PAA → PNG (файл)',     self._convert_paa_to_png_single).pack(fill=tk.X, padx=16, pady=3)
        self._btn(f, '🔄  PNG → PAA (файл)',     self._convert_png_to_paa).pack(fill=tk.X, padx=16, pady=3)

    def _build_section_МАТЕРИАЛЫ(self, parent=None):
        f = parent or self._make_section('МАТЕРИАЛЫ')
        T = self._T()
        self._sep(f); self._lbl(f, 'УПРАВЛЕНИЕ МАТЕРИАЛАМИ')
        # v3.2.0: scrollable mat_panel — Canvas + inner Frame
        # Canvas с visible border + NO scrollbar widget
        self._mat_canvas = tk.Canvas(f, bg=T['bg_side'],
                                     highlightthickness=1,
                                     highlightbackground=T['bg_sep'],
                                     bd=0)
        self._mat_canvas.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self._mat_panel = tk.Frame(self._mat_canvas, bg=T['bg_side'])
        self._mat_window = self._mat_canvas.create_window((0, 0), window=self._mat_panel, anchor='nw')
        # Auto-resize inner frame на ширину canvas (widgets fill=X растягиваются)
        def _on_canvas_configure(e):
            self._mat_canvas.itemconfig(self._mat_window, width=e.width)
        self._mat_canvas.bind('<Configure>', _on_canvas_configure)
        # Auto-update scrollregion
        self._mat_panel.bind('<Configure>', lambda e: self._mat_canvas.config(
            scrollregion=self._mat_canvas.bbox('all')))
        # Mousewheel binding
        def _mat_scroll(e):
            self._mat_canvas.yview_scroll(int(-1*(e.delta/120)), 'units')
            return 'break'
        def _bind_mat_wheel(widget):
            widget.bind('<MouseWheel>', _mat_scroll, add='+')
            for child in widget.winfo_children():
                _bind_mat_wheel(child)
        self._mat_canvas.bind('<MouseWheel>', _mat_scroll, add='+')
        _bind_mat_wheel(self._mat_panel)
        self._mat_bind_wheel = _bind_mat_wheel
        self._tw_frames.append((self._mat_panel, 'bg_side'))
        self._update_mat_panel()

    def _build_section_ПРОКСИ(self, parent=None):
        f = parent or self._make_section('ПРОКСИ')
        T = self._T()
        self._sep(f); self._lbl(f, 'ПРОКСИ (точки крепления)')

        # Toggle прокси
        prx_enabled = self._app_settings.get('proxies_enabled', True)
        self._proxies_enabled_var = tk.BooleanVar(value=prx_enabled)

        def _toggle_proxies():
            val = self._proxies_enabled_var.get()
            self._app_settings['proxies_enabled'] = val
            _save_app_settings(self._app_settings)
            if hasattr(self, 'viewport'):
                self.viewport.show_proxies = val
                self.viewport.redraw()
            _update_toggle_lbl()

        def _update_toggle_lbl():
            val = self._proxies_enabled_var.get()
            prx_toggle_btn.config(
                text='⊕  Прокси: ВКЛ' if val else '⊕  Прокси: ВЫКЛ',
                bg=T['btn_bg'] if val else T['bg_widget'])

        prx_toggle_btn = tk.Button(f, relief='flat',
                                    bg=T['btn_bg'], fg=T['btn_fg'],
                                    font=(T['font_fam'], T['font_sz'] - 1),
                                    padx=6, pady=3,
                                    command=_toggle_proxies)
        prx_toggle_btn.pack(fill=tk.X, padx=10, pady=(2, 2))
        _update_toggle_lbl()

        # Кнопки добавить/удалить прокси
        prx_btn_row = tk.Frame(f, bg=T['bg_side'])
        prx_btn_row.pack(fill=tk.X, padx=10, pady=(0, 4))

        def _add_proxy():
            proxies = (self._raw_model or {}).get('proxies', [])
            new_idx = len(proxies)
            proxies.append({
                'name': f'proxy:\\dz\\new_proxy_{new_idx}.001',
                'pos': [0.0, 0.0, 0.0],
                'mat': [1.0,0.0,0.0, 0.0,1.0,0.0, 0.0,0.0,1.0],
                'index': new_idx,
            })
            if USE_GL and hasattr(self, 'viewport'):
                self.viewport.proxies = proxies
                key = f'new_proxy_{new_idx}.p3d'
                self.viewport.proxy_meshes[key] = _PLACEHOLDER_MESH
            self._update_proxy_list()
            self._show_proxy_edit(new_idx)
            if USE_GL and hasattr(self, 'viewport'):
                self.viewport.selected_proxy = new_idx

        def _delete_proxy():
            idx = getattr(self, '_selected_proxy_idx', -1)
            proxies = (self._raw_model or {}).get('proxies', [])
            if idx < 0 or idx >= len(proxies):
                return
            proxies.pop(idx)
            if USE_GL and hasattr(self, 'viewport'):
                self.viewport.proxies = proxies
                self.viewport.selected_proxy = -1
            self._selected_proxy_idx = -1
            if hasattr(self, '_proxy_edit_frame'):
                try: self._proxy_edit_frame.pack_forget()
                except Exception: pass
            self._update_proxy_list()

        def _add_custom_proxy():
            """Добавить свой предмет — открыть любой .p3d файл."""
            path = filedialog.askopenfilename(
                title='Выбрать модель предмета (.p3d)',
                filetypes=[('P3D Model', '*.p3d'), ('All files', '*.*')],
            )
            if not path:
                return
            import shutil as _sh
            bname = os.path.basename(path).lower()
            os.makedirs(PROXIES_DIR, exist_ok=True)
            dest = os.path.join(PROXIES_DIR, bname)
            try:
                _sh.copy2(path, dest)
            except Exception as _ce:
                _dbg(f'custom proxy copy error: {_ce}')
            proxies = (self._raw_model or {}).get('proxies', [])
            new_idx = len(proxies)
            # Имя без instance suffix (он будет .001 по умолчанию)
            stem = bname if bname.endswith('.p3d') else bname + '.p3d'
            proxies.append({
                'name': f'proxy:\\custom\\{stem}.001',
                'pos': [0.0, 0.0, 0.0],
                'mat': [1.0,0.0,0.0, 0.0,1.0,0.0, 0.0,0.0,1.0],
                'idx': new_idx,
            })
            if USE_GL and hasattr(self, 'viewport'):
                self.viewport.proxies = proxies
            self._load_proxy_meshes_to_viewport(proxies)
            self._update_proxy_list()
            self._show_proxy_edit(new_idx)
            if USE_GL and hasattr(self, 'viewport'):
                self.viewport.selected_proxy = new_idx

        tk.Button(prx_btn_row, text='+ Пустой', relief='flat',
                  bg=T['btn_bg'], fg=T['btn_fg'],
                  font=(T['font_fam'], T['font_sz'] - 1), padx=4, pady=2,
                  command=_add_proxy).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 2))
        tk.Button(prx_btn_row, text='📂 Предмет', relief='flat',
                  bg=T['btn_bg'], fg=T['btn_fg'],
                  font=(T['font_fam'], T['font_sz'] - 1), padx=4, pady=2,
                  command=_add_custom_proxy).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 2))
        tk.Button(prx_btn_row, text='− Удалить', relief='flat',
                  bg=T['bg_widget'], fg='#f38ba8',
                  font=(T['font_fam'], T['font_sz'] - 1), padx=4, pady=2,
                  command=_delete_proxy).pack(side=tk.LEFT, expand=True, fill=tk.X)

        # Подсказка по управлению
        hint = ('Выбор: ЛКМ по точке\n'
                'Перемещение: тяни ЛКМ (XZ) или X/Y/Z ось\n'
                'Поворот: тяни ПКМ')
        tk.Label(f, text=hint, bg=T['bg_side'], fg=T['fg_muted'],
                 font=(T['font_fam'], T['font_sz'] - 2),
                 justify=tk.LEFT, anchor='w').pack(anchor='w', padx=10, pady=(0, 4))

        # Proxy list frame
        self._proxy_list_frame = tk.Frame(f, bg=T['bg_side'])
        self._proxy_list_frame.pack(fill=tk.X, padx=10, pady=(4, 0))
        self._tw_frames.append((self._proxy_list_frame, 'bg_side'))

        # Edit frame (hidden until proxy selected)
        self._proxy_edit_frame = tk.Frame(f, bg=T['bg_widget'],
                                          relief=tk.FLAT, bd=0)
        self._proxy_edit_frame.pack(fill=tk.X, padx=10, pady=(6, 4))
        self._tw_frames.append((self._proxy_edit_frame, 'bg_widget'))

        # Name label
        self._proxy_name_lbl = tk.Label(
            self._proxy_edit_frame, text='', bg=T['bg_widget'],
            fg=T['fg_label'], font=(T['font_fam'], T['font_sz'] - 1),
            wraplength=320, justify=tk.LEFT)
        self._proxy_name_lbl.pack(anchor='w', padx=6, pady=(4, 2))
        self._tw_labels.append((self._proxy_name_lbl, 'bg_widget', 'fg_label', 'normal'))

        # XYZ entry rows
        self._proxy_xyz_vars = {}
        for axis in ('X', 'Y', 'Z'):
            row = tk.Frame(self._proxy_edit_frame, bg=T['bg_widget'])
            row.pack(fill=tk.X, padx=6, pady=1)
            self._tw_frames.append((row, 'bg_widget'))
            lbl = tk.Label(row, text=axis + ':', width=3, bg=T['bg_widget'],
                           fg=T['fg_text'], font=(T['font_fam'], T['font_sz']))
            lbl.pack(side=tk.LEFT)
            self._tw_labels.append((lbl, 'bg_widget', 'fg_text', 'normal'))
            var = tk.StringVar(value='0.000000')
            ent = tk.Entry(row, textvariable=var, width=14,
                           bg=T['bg_main'], fg=T['fg_text'],
                           insertbackground=T['fg_text'],
                           relief=tk.FLAT, font=(T['font_fam'], T['font_sz']))
            ent.pack(side=tk.LEFT, padx=(4, 0))
            self._proxy_xyz_vars[axis] = var

        # Axis constraint buttons  — LMB/RMB drag follows selected axis
        ax_row = tk.Frame(self._proxy_edit_frame, bg=T['bg_widget'])
        ax_row.pack(fill=tk.X, padx=6, pady=(6, 2))
        self._tw_frames.append((ax_row, 'bg_widget'))
        lbl_ax = tk.Label(ax_row, text='Ось:', bg=T['bg_widget'],
                          fg=T['fg_text'], font=(T['font_fam'], T['font_sz']))
        lbl_ax.pack(side=tk.LEFT)
        self._tw_labels.append((lbl_ax, 'bg_widget', 'fg_text', 'normal'))
        self._proxy_axis_btns = {}
        _AXIS_COLORS = {'x': '#c0392b', 'y': '#27ae60', 'z': '#2574b0'}
        for ax_letter in ('X', 'Y', 'Z'):
            def _make_axis_cb(a=ax_letter.lower()):
                def _cb():
                    if not USE_GL or not hasattr(self, 'viewport'):
                        return
                    vp = self.viewport
                    new_key = None if vp._proxy_key == a else a
                    vp._proxy_key = new_key
                    if vp.on_axis_change:
                        vp.on_axis_change(new_key)
                    self._refresh_axis_btns()
                    vp.focus_set()
                return _cb
            btn = tk.Button(ax_row, text=ax_letter, width=3,
                            bg=T['bg_main'], fg=T['fg_text'],
                            relief=tk.FLAT, bd=0,
                            font=(T['font_fam'], T['font_sz'], 'bold'),
                            command=_make_axis_cb(), cursor='hand2',
                            activebackground=_AXIS_COLORS[ax_letter.lower()],
                            activeforeground='#ffffff')
            btn.pack(side=tk.LEFT, padx=3)
            self._proxy_axis_btns[ax_letter.lower()] = btn
        # Free movement button (clears axis)
        def _free_cb():
            if not USE_GL or not hasattr(self, 'viewport'):
                return
            self.viewport._proxy_key = None
            if self.viewport.on_axis_change:
                self.viewport.on_axis_change(None)
            self._refresh_axis_btns()
            self.viewport.focus_set()
        btn_free = tk.Button(ax_row, text='XZ', width=3,
                             bg=T['bg_main'], fg=T['fg_text'],
                             relief=tk.FLAT, bd=0,
                             font=(T['font_fam'], T['font_sz']),
                             command=_free_cb, cursor='hand2')
        btn_free.pack(side=tk.LEFT, padx=3)
        self._proxy_axis_btn_free = btn_free

        # Local/Global space toggle button
        self._proxy_local_space = False

        def _toggle_space():
            if not USE_GL or not hasattr(self, 'viewport'):
                return
            self._proxy_local_space = not self._proxy_local_space
            self.viewport._local_space = self._proxy_local_space
            self._refresh_axis_btns()
            if self.viewport.on_axis_change:
                self.viewport.on_axis_change(self.viewport._proxy_key)
            self.viewport.focus_set()

        self._proxy_space_btn = tk.Button(
            ax_row, text='Глоб', width=5,
            bg=T['bg_main'], fg=T['fg_muted'],
            relief=tk.FLAT, bd=0,
            font=(T['font_fam'], T['font_sz']),
            command=_toggle_space, cursor='hand2')
        self._proxy_space_btn.pack(side=tk.LEFT, padx=(6, 3))
        self._tw_frames.append((self._proxy_space_btn, 'bg_main'))

        # Hint label
        hint_lbl = tk.Label(self._proxy_edit_frame,
                            text='LMB = перемещение   RMB = вращение',
                            bg=T['bg_widget'], fg=T['fg_muted'],
                            font=(T['font_fam'], T['font_sz'] - 1))
        hint_lbl.pack(anchor='w', padx=6, pady=(2, 0))
        self._tw_labels.append((hint_lbl, 'bg_widget', 'fg_muted', 'normal'))

        # Apply button
        self._proxy_apply_btn = self._btn(
            self._proxy_edit_frame, '✔  Применить позицию',
            self._apply_proxy_pos, accent=True)
        self._proxy_apply_btn.pack(fill=tk.X, padx=6, pady=(6, 2))

        self._proxy_edit_frame.pack_forget()  # hidden initially
        self._update_proxy_list()

        # --- Action buttons: Обновить CPP + Встроить в P3D ---
        sep = tk.Frame(f, bg=T['fg_muted'], height=1)
        sep.pack(fill=tk.X, padx=10, pady=(8, 4))
        lbl_act = tk.Label(f, text='Экспорт / Запись', bg=T['bg_side'],
                           fg=T['fg_muted'],
                           font=(T['font_fam'], T['font_sz'] - 2))
        lbl_act.pack(anchor='w', padx=10)
        self._tw_labels.append((lbl_act, 'bg_side', 'fg_muted', 'normal'))
        act_row = tk.Frame(f, bg=T['bg_side'])
        act_row.pack(fill=tk.X, padx=10, pady=(2, 4))
        self._tw_frames.append((act_row, 'bg_side'))
        tk.Button(act_row, text='📄 Обновить CPP', relief='flat',
                  bg=T['btn_bg'], fg=T['btn_fg'],
                  font=(T['font_fam'], T['font_sz'] - 1), padx=4, pady=3,
                  command=self._update_cpp_from_working,
                  cursor='hand2').pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 2))
        tk.Button(act_row, text='💾 Встроить в P3D', relief='flat',
                  bg=T['btn_bg'], fg=T['btn_fg'],
                  font=(T['font_fam'], T['font_sz'] - 1), padx=4, pady=3,
                  command=self._embed_proxies_action,
                  cursor='hand2').pack(side=tk.LEFT, fill=tk.X, expand=True)

    def _update_proxy_row_btn(self, idx):
        """Update only eye/lock buttons for one row — no list rebuild, no flicker."""
        if not hasattr(self, '_proxy_row_btns') or idx >= len(self._proxy_row_btns):
            self._update_proxy_list()
            return
        proxies = (self._raw_model or {}).get('proxies', [])
        if idx >= len(proxies):
            return
        T = self._T()
        _acc = T.get('accent', T.get('btn_fg', '#89b4fa'))
        prx = proxies[idx]
        eye_btn, lock_btn = self._proxy_row_btns[idx]
        _hidden = prx.get('hidden', False)
        try:
            eye_btn.config(text='○' if _hidden else '●',
                           fg=T['fg_muted'] if _hidden else _acc)
        except Exception:
            pass
        _locked = prx.get('locked', False)
        try:
            lock_btn.config(text='[X]' if _locked else '[ ]',
                            fg='#f38ba8' if _locked else T['fg_muted'])
        except Exception:
            pass

    def _update_proxy_list(self):
        """Rebuild proxy list buttons in sidebar."""
        if not hasattr(self, '_proxy_list_frame'):
            return
        T = self._T()
        for w in self._proxy_list_frame.winfo_children():
            try: w.destroy()
            except Exception: pass
        self._proxy_row_btns = []
        proxies = (self._raw_model or {}).get('proxies', [])
        if not proxies:
            lbl = tk.Label(self._proxy_list_frame, text='Нет проксей',
                           bg=T['bg_side'], fg=T['fg_label'],
                           font=(T['font_fam'], T['font_sz'] - 1))
            lbl.pack(anchor='w', pady=2)
            if hasattr(self, '_proxy_edit_frame'):
                self._proxy_edit_frame.pack_forget()
            return
        _acc = T.get('accent', T.get('btn_fg', '#89b4fa'))
        for i, prx in enumerate(proxies):
            try:
                slot_name = prx.get('slot_name', '')
                if slot_name:
                    disp = slot_name + (' *' if prx.get('_synthetic') else '')
                else:
                    disp = _proxy_basename_normalize(prx.get('name', f'proxy_{i}')).replace('.p3d', '')
                row = tk.Frame(self._proxy_list_frame, bg=T['bg_side'])
                row.pack(fill=tk.X, pady=2)
                _hidden = prx.get('hidden', False)
                def _make_eye_cb(ii=i):
                    def _cb():
                        prxs = (self._raw_model or {}).get('proxies', [])
                        if ii < len(prxs):
                            prxs[ii]['hidden'] = not prxs[ii].get('hidden', False)
                        if USE_GL and hasattr(self, 'viewport'):
                            self.viewport.proxies = prxs
                        self._update_proxy_row_btn(ii)
                    return _cb
                eye_btn = tk.Button(row, text='○' if _hidden else '●', width=2,
                                    relief='flat', bg=T['bg_side'],
                                    fg=T['fg_muted'] if _hidden else _acc,
                                    font=(T['font_fam'], T['font_sz']),
                                    command=_make_eye_cb(), cursor='hand2')
                eye_btn.pack(side=tk.LEFT)
                _locked = prx.get('locked', False)
                def _make_lock_cb(ii=i):
                    def _cb():
                        prxs = (self._raw_model or {}).get('proxies', [])
                        if ii < len(prxs):
                            prxs[ii]['locked'] = not prxs[ii].get('locked', False)
                        if USE_GL and hasattr(self, 'viewport'):
                            self.viewport.proxies = prxs
                        self._update_proxy_row_btn(ii)
                        self._show_proxy_edit(getattr(self, '_selected_proxy_idx', -1))
                    return _cb
                lock_btn = tk.Button(row, text='[X]' if _locked else '[ ]', width=3,
                                     relief='flat', bg=T['bg_side'],
                                     fg='#f38ba8' if _locked else T['fg_muted'],
                                     font=(T['font_fam'], T['font_sz'] - 1),
                                     command=_make_lock_cb(), cursor='hand2')
                lock_btn.pack(side=tk.LEFT)
                self._proxy_row_btns.append((eye_btn, lock_btn))
                self._btn(row, disp,
                          lambda ii=i: self._select_proxy_from_sidebar(ii)
                          ).pack(side=tk.LEFT, fill=tk.X, expand=True)
            except Exception as _row_e:
                _dbg(f'proxy list row {i} error: {_row_e}')
                self._proxy_row_btns.append((None, None))

    def _select_proxy_from_sidebar(self, idx):
        """Select proxy by clicking its button in sidebar."""
        if USE_GL and hasattr(self, 'viewport'):
            self.viewport.selected_proxy = idx
        self._show_proxy_edit(idx)

    def _on_viewport_proxy_select(self, idx):
        """Called from viewport when user clicks a proxy in 3D view."""
        try:
            self._show_proxy_edit(idx)
        except Exception:
            pass
        # Обновить badge с именем proxy
        try:
            if hasattr(self, '_prx_badge'):
                proxies = (self._raw_model or {}).get('proxies', [])
                if idx >= 0 and idx < len(proxies):
                    prx_entry = proxies[idx]
                    name = prx_entry.get('slot_name') or _proxy_basename_normalize(prx_entry.get('name', '')).replace('.p3d', '')
                    self._prx_badge.config(text=f'  Proxy: {name}  ')
                    self._prx_badge.place(relx=1.0, rely=0.0, anchor='ne', x=-6, y=26)
                else:
                    self._prx_badge.place_forget()
        except Exception:
            pass
        self._refresh_axis_btns()

    def _augment_proxies_from_config(self, p3d_path, p3d_proxies):
        """
        Find config.cpp, parse attachment slots, and return augmented proxy list.
        Adds synthetic zero-position entries for slots not in the P3D binary.
        """
        cpp_path = _find_config_cpp(p3d_path)
        if not cpp_path:
            for prx in p3d_proxies:
                if 'slot_name' not in prx:
                    prx['slot_name'] = _proxy_basename_normalize(prx.get('name', '')).replace('.p3d', '')
            return list(p3d_proxies)

        cfg = _parse_proxy_config(cpp_path, p3d_path)
        attachments  = cfg['attachments']
        slot_to_model = cfg['slot_to_model']
        self._cpp_src_path = cpp_path
        _dbg(f'config.cpp found: {cpp_path}  slots: {attachments}')

        if not attachments:
            for prx in p3d_proxies:
                if 'slot_name' not in prx:
                    prx['slot_name'] = _proxy_basename_normalize(prx.get('name', '')).replace('.p3d', '')
            return list(p3d_proxies)

        # Build lookup: model_basename → P3D proxy entry
        p3d_by_bn = {}
        for prx in p3d_proxies:
            if not prx.get('_synthetic'):
                bn = _proxy_basename_normalize(prx.get('name', ''))
                if bn and bn not in p3d_by_bn:
                    p3d_by_bn[bn] = prx

        augmented  = []
        handled_bns = set()

        for slot_name in attachments:
            model_path = slot_to_model.get(slot_name, '')
            if model_path:
                model_bn = model_path.replace('\\', '/').rsplit('/', 1)[-1].lower()
                if not model_bn.endswith('.p3d'):
                    model_bn += '.p3d'
            else:
                model_bn = ''

            matched = p3d_by_bn.get(model_bn) if model_bn else None

            if matched:
                entry = dict(matched)
                entry['slot_name'] = slot_name
                entry['model_bn']  = model_bn
                augmented.append(entry)
                handled_bns.add(model_bn)
            else:
                # Synthetic proxy: no P3D point → use (0, 0, 0)
                proxy_name = ('proxy:\\' + model_path) if model_path else f'proxy:_{slot_name}'
                augmented.append({
                    'name':       proxy_name,
                    'pos':        [0.0, 0.0, 0.0],
                    'mat':        [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0],
                    'idx':        0,
                    'slot_name':  slot_name,
                    'model_bn':   model_bn,
                    '_synthetic': True,
                })

        # Add P3D proxies not matched to any config slot
        for prx in p3d_proxies:
            if prx.get('_synthetic'):
                continue
            bn = _proxy_basename_normalize(prx.get('name', ''))
            if bn not in handled_bns:
                entry = dict(prx)
                entry.setdefault('slot_name', bn.replace('.p3d', ''))
                entry.setdefault('model_bn', bn)
                augmented.append(entry)

        return augmented

    def _auto_copy_proxies(self, proxies, on_done=None):
        """Find and copy proxy .p3d files to local proxies/ folder in background."""
        def _call_done():
            if on_done:
                try: self.root.after(0, on_done)
                except Exception: pass
        if not proxies:
            _call_done(); return
        unpacked = _find_unpacked_dir()
        if not unpacked:
            _dbg('_auto_copy_proxies: unpacked dir not found — skip copy')
            _call_done(); return
        def _worker():
            for prx in proxies:
                name = prx.get('name', '')
                if not name:
                    continue
                src = _find_proxy_file(name, unpacked)
                if src:
                    dest = _copy_proxy_to_local(src, PROXIES_DIR)
                    _dbg(f'proxy copied: {os.path.basename(dest)}')
                else:
                    _dbg(f'proxy NOT FOUND: {name}')
            _call_done()
        import threading as _th2
        _th2.Thread(target=_worker, daemon=True).start()

    def _load_proxy_meshes_to_viewport(self, proxies):
        """Load proxy .p3d files from proxies/ dir into viewport for rendering."""
        if not USE_GL or not hasattr(self, 'viewport'):
            return
        if not proxies:
            return
        def _worker():
            loaded = {}
            for prx in proxies:
                raw_name = prx.get('name', '')
                basename = _proxy_basename_normalize(raw_name)
                if basename in loaded:
                    continue
                local_path = os.path.join(PROXIES_DIR, basename)
                if os.path.isfile(local_path):
                    try:
                        pts, uvs, nrms, all_tris, tex_hint, mat_groups, _raw = parse_p3d(local_path)
                        pts_arr = np.array(pts, dtype=np.float32)
                        loaded[basename] = {'pts': pts_arr, 'tris': all_tris}
                        _dbg(f'proxy mesh loaded: {basename} ({len(pts)} verts, {len(all_tris)} tris)')
                    except Exception as ex:
                        _dbg(f'proxy mesh load error {basename}: {ex}')
                else:
                    _dbg(f'proxy file not found (skipped): {local_path}')
            if hasattr(self, 'viewport'):
                self.viewport.proxy_meshes = loaded
        import threading as _th3
        _th3.Thread(target=_worker, daemon=True).start()

    def _refresh_axis_btns(self):
        """Update visual state of axis constraint buttons."""
        if not hasattr(self, '_proxy_axis_btns'):
            return
        T = self._T()
        _AXIS_COLORS = {'x': '#c0392b', 'y': '#27ae60', 'z': '#2574b0'}
        ax = None
        if USE_GL and hasattr(self, 'viewport'):
            ax = self.viewport._proxy_key
        for key, btn in self._proxy_axis_btns.items():
            if key == ax:
                btn.config(bg=_AXIS_COLORS[key], fg='#ffffff')
            else:
                btn.config(bg=T['bg_main'], fg=T['fg_text'])
        # Free (XZ) button — highlight when no axis locked
        if hasattr(self, '_proxy_axis_btn_free'):
            if ax is None:
                self._proxy_axis_btn_free.config(bg=T['accent'], fg='#ffffff')
            else:
                self._proxy_axis_btn_free.config(bg=T['bg_main'], fg=T['fg_text'])
        # Local/Global space toggle button
        if hasattr(self, '_proxy_space_btn'):
            _acc = T.get('accent', T.get('btn_fg', '#89b4fa'))
            is_local = getattr(self, '_proxy_local_space', False)
            if is_local:
                self._proxy_space_btn.config(
                    text='Лок.', fg='#ffffff',
                    bg=_acc)
            else:
                self._proxy_space_btn.config(
                    text='Глоб', fg=T['fg_muted'],
                    bg=T['bg_main'])

    def _on_proxy_transform_live(self, idx):
        """Called after every proxy move/rotate — updates edit panel + axis badge."""
        self._show_proxy_edit(idx)
        # Обновить axis badge с текущей координатой
        try:
            if hasattr(self, '_axis_badge') and USE_GL and hasattr(self, 'viewport'):
                ax = self.viewport._proxy_key
                if ax:
                    proxies = (self._raw_model or {}).get('proxies', [])
                    if idx >= 0 and idx < len(proxies):
                        pos = proxies[idx].get('pos', [0.0, 0.0, 0.0])
                        coord = pos[0] if ax == 'x' else pos[1] if ax == 'y' else pos[2]
                        self._axis_badge.config(text=f'  Ось: {ax.upper()}  {coord:.3f}  ')
        except Exception:
            pass

    def _show_proxy_edit(self, idx):
        """Show/update proxy edit panel for given index."""
        if not hasattr(self, '_proxy_edit_frame'):
            return
        try:
            proxies = (self._raw_model or {}).get('proxies', [])
            if idx < 0 or idx >= len(proxies):
                self._proxy_edit_frame.pack_forget()
                return
            prx = proxies[idx]
            raw_name = prx.get('name', f'proxy_{idx}')
            short = raw_name.split('\\')[-1].split('/')[-1].replace('.p3d', '')
            _is_locked = prx.get('locked', False)
            _lock_pfx = '[=] ' if _is_locked else ''
            self._proxy_name_lbl.config(text=f'{_lock_pfx}#{idx}  {short}')
            pos = prx.get('pos', [0.0, 0.0, 0.0])
            for j, axis in enumerate(('X', 'Y', 'Z')):
                self._proxy_xyz_vars[axis].set(f'{pos[j]:.6f}')
            self._proxy_edit_frame.pack(fill=tk.X, padx=10, pady=(6, 4))
            self._selected_proxy_idx = idx
            # Dim inputs when locked
            T = self._T()
            _is_locked = prx.get('locked', False)
            _entry_fg = T['fg_muted'] if _is_locked else T['fg_text']
            _entry_state = 'disabled' if _is_locked else 'normal'
            for _var_widget in self._proxy_edit_frame.winfo_children():
                try:
                    if isinstance(_var_widget, tk.Frame):
                        for _w in _var_widget.winfo_children():
                            if isinstance(_w, tk.Entry):
                                _w.config(state=_entry_state, fg=_entry_fg)
                except Exception:
                    pass
            if hasattr(self, '_proxy_apply_btn'):
                try:
                    self._proxy_apply_btn.config(
                        state='disabled' if _is_locked else 'normal',
                        fg=T['fg_muted'] if _is_locked else T['btn_fg']
                    )
                except Exception:
                    pass
            # Model switcher
            self._refresh_model_switcher(idx)
        except Exception:
            pass

    def _refresh_model_switcher(self, idx):
        """Rebuild the model-switcher dropdown inside the proxy edit panel."""
        if not hasattr(self, '_proxy_edit_frame'):
            return
        # Remove old switcher if present
        if hasattr(self, '_model_sw_frame') and self._model_sw_frame.winfo_exists():
            try: self._model_sw_frame.destroy()
            except Exception: pass
        proxies = (self._raw_model or {}).get('proxies', [])
        if idx < 0 or idx >= len(proxies):
            return
        T = self._T()
        # List .p3d files in proxies/
        available = []
        if os.path.isdir(PROXIES_DIR):
            available = sorted(
                f for f in os.listdir(PROXIES_DIR)
                if f.lower().endswith('.p3d')
            )
        if not available:
            return
        prx = proxies[idx]
        current_bn = prx.get('model_bn') or _proxy_basename_normalize(prx.get('name', ''))
        sw_frame = tk.Frame(self._proxy_edit_frame, bg=T['bg_widget'])
        sw_frame.pack(fill=tk.X, padx=6, pady=(4, 2))
        self._tw_frames.append((sw_frame, 'bg_widget'))
        self._model_sw_frame = sw_frame
        lbl = tk.Label(sw_frame, text='Модель:', bg=T['bg_widget'],
                       fg=T['fg_text'], font=(T['font_fam'], T['font_sz'] - 1))
        lbl.pack(side=tk.LEFT)
        self._tw_labels.append((lbl, 'bg_widget', 'fg_text', 'normal'))
        import tkinter.ttk as _ttk
        var = tk.StringVar(value=current_bn if current_bn in available else (available[0] if available else ''))
        combo = _ttk.Combobox(sw_frame, textvariable=var, values=available,
                              state='readonly', width=18,
                              font=(T['font_fam'], T['font_sz'] - 1))
        combo.pack(side=tk.LEFT, padx=(4, 0), fill=tk.X, expand=True)
        def _on_model_select(event=None):
            chosen = var.get()
            if not chosen:
                return
            prxs = (self._raw_model or {}).get('proxies', [])
            if idx >= len(prxs):
                return
            prxs[idx]['model_bn'] = chosen
            # Load mesh if not yet loaded
            if USE_GL and hasattr(self, 'viewport'):
                if chosen not in self.viewport.proxy_meshes:
                    local = os.path.join(PROXIES_DIR, chosen)
                    if os.path.isfile(local):
                        try:
                            import numpy as _np
                            pts, uvs, nrms, all_tris, tex_hint, mat_grp, _raw = parse_p3d(local)
                            pts_arr = _np.array(pts, dtype=_np.float32)
                            self.viewport.proxy_meshes[chosen] = {'pts': pts_arr, 'tris': all_tris}
                        except Exception as _me:
                            _dbg(f'model switch load error: {_me}')
                # Patch proxy name so _draw_proxy_meshes finds the mesh
                prxs[idx]['name'] = f'proxy:\\custom\\{chosen}.001'
                self.viewport.proxies = prxs
        combo.bind('<<ComboboxSelected>>', _on_model_select)

    def _update_cpp_from_working(self):
        """Add new proxies from current P3D to config.cpp with Russian display names."""
        import tkinter as tk
        import tkinter.ttk as ttk
        import tkinter.messagebox as _mb
        import re as _re
        import shutil as _sh

        T = self._theme
        p3d_path = getattr(self, '_current_p3d_path', '') or ''
        p3d_name = os.path.basename(p3d_path).lower()

        # Locate config.cpp — use known path or search near P3D
        cpp_src = getattr(self, '_cpp_src_path', None)
        if not cpp_src or not os.path.isfile(cpp_src):
            for _d in [
                os.path.dirname(p3d_path),
                os.path.dirname(os.path.dirname(p3d_path)),
                os.path.dirname(os.path.dirname(os.path.dirname(p3d_path))),
            ]:
                _c = os.path.join(_d, 'config.cpp')
                if os.path.isfile(_c):
                    cpp_src = _c
                    break
        if not cpp_src or not os.path.isfile(cpp_src):
            _mb.showwarning('Обновить CPP',
                            'config.cpp не найден рядом с моделью.\n'
                            'Откройте модель через PBO-браузер.')
            return

        proxies = (self._raw_model or {}).get('proxies', [])
        if not proxies:
            self.status.config(text='Нет прокси в текущей модели')
            return

        with open(cpp_src, 'r', encoding='utf-8', errors='replace') as f:
            cpp_text = f.read()

        # ── helpers ─────────────────────────────────────────────────────────
        def _block_end(txt, start):
            depth = 0
            for i in range(start, len(txt)):
                if txt[i] == '{': depth += 1
                elif txt[i] == '}':
                    depth -= 1
                    if depth == 0:
                        return i
            return len(txt) - 1

        def _find_named_block(txt, class_name):
            m = _re.search(r'class\s+' + _re.escape(class_name) + r'\s*\{',
                           txt, _re.IGNORECASE)
            if not m:
                return None, None
            bpos = txt.index('{', m.start())
            return bpos, _block_end(txt, bpos)

        # ── existing CfgSlots names ──────────────────────────────────────────
        existing_slots = set()
        bpos, epos = _find_named_block(cpp_text, 'CfgSlots')
        if bpos is not None:
            for nm in _re.finditer(r'name\s*=\s*"([^"]+)"', cpp_text[bpos:epos]):
                existing_slots.add(nm.group(1).lower())

        # ── existing Proxy classes in CfgNonAIVehicles ──────────────────────
        existing_proxy = set()
        bpos2, epos2 = _find_named_block(cpp_text, 'CfgNonAIVehicles')
        if bpos2 is not None:
            for pm in _re.finditer(r'class\s+Proxy(\w+)\s*:', cpp_text[bpos2:epos2]):
                existing_proxy.add(pm.group(1).lower())

        # ── find CfgVehicles class referencing our P3D ──────────────────────
        existing_attach = set()
        target_cls = None
        for cm in _re.finditer(r'class\s+(\w+)\s*(?::\s*[\w:]+)?\s*\{', cpp_text):
            cb = cpp_text.index('{', cm.start())
            ce = _block_end(cpp_text, cb)
            body = cpp_text[cb:ce]
            refs = _re.findall(r'(?:model|male|female)\s*=\s*"([^"]*)"', body, _re.IGNORECASE)
            if any(p3d_name in r.lower() for r in refs):
                target_cls = cm.group(1)
                am = _re.search(r'attachments\s*\[\s*\]\s*=\s*\{([^}]*)\}', body)
                if am:
                    for s in _re.findall(r'"([^"]+)"', am.group(1)):
                        existing_attach.add(s.lower())
                break

        # ── determine truly new proxies ──────────────────────────────────────
        new_proxies = []
        for prx in proxies:
            if prx.get('_synthetic'):
                continue
            raw = prx.get('name', '')
            sel = raw[6:] if raw.lower().startswith('proxy:') else raw
            sel = _re.sub(r'\.\d+$', '', sel).replace('/', '\\').strip('\\')
            slot = sel.split('\\')[-1]
            if not slot:
                continue
            if (slot.lower() in existing_slots or slot.lower() in existing_attach
                    or slot.lower() in existing_proxy):
                continue
            new_proxies.append({'raw': raw, 'slot': slot, 'model': sel})

        if not new_proxies:
            _mb.showinfo('Обновить CPP', 'Все proxy уже зарегистрированы в config.cpp')
            return

        # ── Dialog ───────────────────────────────────────────────────────────
        ICONS = [
            ('Подсумок / Карман',     'set:dayz_inventory image:vestpouches'),
            ('Рация',                  'set:dayz_inventory image:walkietalkie'),
            ('Снаряжение (general)',   'set:dayz_inventory image:gear'),
            ('Магазин',               'set:dayz_inventory image:magazine'),
            ('Нож / Инструмент',      'set:dayz_inventory image:knife'),
        ]
        ICON_LABELS = [lbl for lbl, _ in ICONS]
        ICON_VALUES = [val for _, val in ICONS]

        dlg = tk.Toplevel(self.root)
        dlg.title('Добавить proxy в config.cpp')
        dlg.configure(bg=T['bg_main'])
        dlg.resizable(False, False)
        dlg.transient(self.root)
        dlg.grab_set()

        tk.Label(dlg, text=f'Новых proxy: {len(new_proxies)}',
                 bg=T['bg_main'], fg=T['fg'],
                 font=(T['font_fam'], T['font_sz'] + 1, 'bold')
                 ).pack(padx=16, pady=(12, 2), anchor='w')

        if target_cls:
            tk.Label(dlg, text=f'Класс: {target_cls}  →  {os.path.basename(cpp_src)}',
                     bg=T['bg_main'], fg=T['fg_muted'],
                     font=(T['font_fam'], T['font_sz'] - 1)
                     ).pack(padx=16, pady=(0, 8), anchor='w')
        else:
            tk.Label(dlg,
                     text='⚠ Класс в CfgVehicles не найден — добавьте слот в attachments[] вручную',
                     bg=T['bg_main'], fg='#f38ba8',
                     font=(T['font_fam'], T['font_sz'] - 1)
                     ).pack(padx=16, pady=(0, 8), anchor='w')

        rows = []
        for prx in new_proxies:
            card = tk.Frame(dlg, bg=T['bg_side'], padx=10, pady=8)
            card.pack(fill=tk.X, padx=16, pady=3)

            tk.Label(card, text=prx['raw'], bg=T['bg_side'], fg=T['fg_muted'],
                     font=(T['font_fam'], T['font_sz'] - 2)
                     ).grid(row=0, column=0, columnspan=2, sticky='w')
            tk.Label(card, text=f'Слот: {prx["slot"]}', bg=T['bg_side'], fg=T['fg'],
                     font=(T['font_fam'], T['font_sz'] - 1)
                     ).grid(row=1, column=0, columnspan=2, sticky='w', pady=(2, 0))

            tk.Label(card, text='Название (RU):', bg=T['bg_side'], fg=T['fg'],
                     font=(T['font_fam'], T['font_sz'])
                     ).grid(row=2, column=0, sticky='w', pady=(8, 0))
            name_var = tk.StringVar(value=prx['slot'])
            tk.Entry(card, textvariable=name_var, width=28, bg=T['bg_main'], fg=T['fg'],
                     insertbackground=T['fg'], relief='flat',
                     font=(T['font_fam'], T['font_sz'])
                     ).grid(row=2, column=1, sticky='w', padx=(8, 0), pady=(8, 0))

            tk.Label(card, text='Иконка:', bg=T['bg_side'], fg=T['fg'],
                     font=(T['font_fam'], T['font_sz'])
                     ).grid(row=3, column=0, sticky='w', pady=(4, 0))
            icon_var = tk.StringVar(value=ICON_VALUES[0])
            cb = ttk.Combobox(card, values=ICON_LABELS, state='readonly', width=28,
                              font=(T['font_fam'], T['font_sz']))
            cb.current(0)
            cb.grid(row=3, column=1, sticky='w', padx=(8, 0), pady=(4, 0))

            def _sel(e, _cb=cb, _iv=icon_var):
                _iv.set(ICON_VALUES[_cb.current()])
            cb.bind('<<ComboboxSelected>>', _sel)

            rows.append({'prx': prx, 'name_var': name_var, 'icon_var': icon_var})

        btn_row = tk.Frame(dlg, bg=T['bg_main'])
        btn_row.pack(fill=tk.X, padx=16, pady=12)
        confirmed = [False]

        def _ok():
            confirmed[0] = True
            dlg.destroy()

        _acc = T.get('accent', T.get('btn_fg', '#89b4fa'))
        tk.Button(btn_row, text='Отмена', bg=T['btn_bg'], fg=T['btn_fg'],
                  relief='flat', padx=10, command=dlg.destroy
                  ).pack(side=tk.RIGHT, padx=(6, 0))
        tk.Button(btn_row, text='Добавить в CPP', bg=_acc, fg='#ffffff',
                  relief='flat', padx=10, command=_ok
                  ).pack(side=tk.RIGHT)

        dlg.update_idletasks()
        _w = dlg.winfo_reqwidth()
        _h = dlg.winfo_reqheight()
        _x = self.root.winfo_x() + (self.root.winfo_width() - _w) // 2
        _y = self.root.winfo_y() + (self.root.winfo_height() - _h) // 2
        dlg.geometry(f'{_w}x{_h}+{_x}+{_y}')
        self.root.wait_window(dlg)
        if not confirmed[0]:
            return

        # ── Patch CPP text ───────────────────────────────────────────────────
        new_cpp = cpp_text

        # 1. CfgSlots — insert new Slot_Xxx classes
        m_slots = _re.search(r'class\s+CfgSlots\s*\{', new_cpp, _re.IGNORECASE)
        if m_slots:
            ep = _block_end(new_cpp, new_cpp.index('{', m_slots.start()))
            ins = ''
            for row in rows:
                sn = row['prx']['slot']
                dn = row['name_var'].get().strip() or sn
                ic = row['icon_var'].get()
                ins += (f'\tclass Slot_{sn}\n\t{{\n'
                        f'\t\tname="{sn}";\n'
                        f'\t\tdisplayName="{dn}";\n'
                        f'\t\tghostIcon="{ic}";\n'
                        f'\t}};\n')
            new_cpp = new_cpp[:ep] + ins + new_cpp[ep:]
        else:
            # Create CfgSlots block before CfgVehicles
            ins = 'class CfgSlots\n{\n'
            for row in rows:
                sn = row['prx']['slot']
                dn = row['name_var'].get().strip() or sn
                ic = row['icon_var'].get()
                ins += (f'\tclass Slot_{sn}\n\t{{\n'
                        f'\t\tname="{sn}";\n'
                        f'\t\tdisplayName="{dn}";\n'
                        f'\t\tghostIcon="{ic}";\n'
                        f'\t}};\n')
            ins += '};\n'
            mv = _re.search(r'class\s+CfgVehicles', new_cpp, _re.IGNORECASE)
            ins_pos = mv.start() if mv else len(new_cpp)
            new_cpp = new_cpp[:ins_pos] + ins + new_cpp[ins_pos:]

        # 2. CfgNonAIVehicles — insert new ProxyXxx classes
        m_nonaiv = _re.search(r'class\s+CfgNonAIVehicles\s*\{', new_cpp, _re.IGNORECASE)
        if m_nonaiv:
            ep = _block_end(new_cpp, new_cpp.index('{', m_nonaiv.start()))
            ins = ''
            for row in rows:
                sn = row['prx']['slot']
                mp = row['prx']['model']
                ins += (f'\tclass Proxy{sn}: ProxyAttachment\n\t{{\n'
                        f'\t\tscope=2;\n'
                        f'\t\tinventorySlot[]=\n\t\t{{\n'
                        f'\t\t\t"{sn}"\n\t\t}};\n'
                        f'\t\tmodel="{mp}";\n'
                        f'\t}};\n')
            new_cpp = new_cpp[:ep] + ins + new_cpp[ep:]
        else:
            ins = 'class CfgNonAIVehicles\n{\n\tclass ProxyAttachment;\n'
            for row in rows:
                sn = row['prx']['slot']
                mp = row['prx']['model']
                ins += (f'\tclass Proxy{sn}: ProxyAttachment\n\t{{\n'
                        f'\t\tscope=2;\n'
                        f'\t\tinventorySlot[]=\n\t\t{{\n'
                        f'\t\t\t"{sn}"\n\t\t}};\n'
                        f'\t\tmodel="{mp}";\n'
                        f'\t}};\n')
            ins += '};\n'
            mv = _re.search(r'class\s+CfgVehicles', new_cpp, _re.IGNORECASE)
            ins_pos = mv.start() if mv else len(new_cpp)
            new_cpp = new_cpp[:ins_pos] + ins + new_cpp[ins_pos:]

        # 3. CfgVehicles target class — add to attachments[]
        if target_cls:
            m_cls = _re.search(
                r'class\s+' + _re.escape(target_cls) + r'\s*(?::\s*[\w:]+)?\s*\{', new_cpp)
            if m_cls:
                cb = new_cpp.index('{', m_cls.start())
                ce = _block_end(new_cpp, cb)
                body = new_cpp[cb:ce]
                am = _re.search(r'attachments\s*\[\s*\]\s*=\s*\{([^}]*)\}', body)
                if am:
                    existing_items = _re.findall(r'"([^"]+)"', am.group(1))
                    all_items = existing_items + [row['prx']['slot'] for row in rows]
                    new_inner = '\n' + ''.join(f'\t\t"{it}",\n' for it in all_items) + '\t'
                    new_body = body[:am.start(1)] + new_inner + body[am.end(1):]
                    new_cpp = new_cpp[:cb] + new_body + new_cpp[ce:]
                else:
                    ins = ('\n\tattachments[]=\n\t{\n'
                           + ''.join(f'\t\t"{row["prx"]["slot"]}",\n' for row in rows)
                           + '\t};\n')
                    new_cpp = new_cpp[:cb + 1] + ins + new_cpp[cb + 1:]

        # ── Save ─────────────────────────────────────────────────────────────
        backup = cpp_src + '.bak'
        if not os.path.exists(backup):
            _sh.copy2(cpp_src, backup)
        with open(cpp_src, 'w', encoding='utf-8') as f:
            f.write(new_cpp)

        n = len(rows)
        self.status.config(text=f'CPP обновлён: добавлено {n} proxy')
        _mb.showinfo('Обновить CPP',
                     f'Добавлено {n} proxy в {os.path.basename(cpp_src)}.\n'
                     f'Резервная копия: {os.path.basename(backup)}')

    def _embed_proxies_action(self):
        """Write proxy positions into the P3D binary in-place."""
        import tkinter.messagebox as _mb
        path = getattr(self, '_current_p3d_path', None)
        if not path:
            # Try to get from model state
            for st in self._models.values():
                if st.get('status') == 'ready':
                    path = st.get('path')
                    break
        if not path or not os.path.isfile(path):
            self.status.config(text='P3D файл не найден')
            return
        proxies = (self._raw_model or {}).get('proxies', [])
        if not proxies:
            self.status.config(text='Нет прокси для записи')
            return
        has_offset = any(p.get('_file_offset') for p in proxies if not p.get('_synthetic'))
        if not has_offset:
            _mb.showwarning('Встроить в P3D',
                            'Смещения в файле не найдены.\n'
                            'Попробуйте перезагрузить модель.')
            return
        if not _mb.askyesno('Встроить в P3D',
                            f'Файл: {os.path.basename(path)}\n\n'
                            'Позиции proxy будут записаны прямо в P3D.\n'
                            'Резервная копия (.bak) создастся автоматически.\n\n'
                            'Продолжить?'):
            return
        ok, msg = _embed_proxies_to_p3d(path, proxies)
        self.status.config(text=msg)
        if ok:
            _mb.showinfo('Встроить в P3D', msg)
        else:
            _mb.showerror('Встроить в P3D', msg)

    def _apply_proxy_pos(self):
        """Apply edited XYZ to the selected proxy."""
        idx = getattr(self, '_selected_proxy_idx', -1)
        if idx < 0: return
        try:
            x = float(self._proxy_xyz_vars['X'].get())
            y = float(self._proxy_xyz_vars['Y'].get())
            z = float(self._proxy_xyz_vars['Z'].get())
        except ValueError:
            return
        proxies = (self._raw_model or {}).get('proxies', [])
        if idx >= len(proxies): return
        proxies[idx]['pos'] = [x, y, z]
        # Update viewport display
        if USE_GL and hasattr(self, 'viewport'):
            self.viewport.proxies = proxies
        # Also update all loaded model states
        for st in self._models.values():
            if st.get('status') == 'ready' and st.get('raw'):
                prx_list = st['raw'].get('proxies', [])
                if idx < len(prx_list):
                    prx_list[idx]['pos'] = [x, y, z]
        self.status.config(text=f'Прокси #{idx} обновлён')

    def _auto_split_for_multi_material(self, idx):
        """v2.3: автопоиск split'ов для моделей с mat_list>1 но пустым mat_groups.

        Ситуация: parse_p3d нашёл несколько материалов через DLL (gtex),
        но не смог определить per-face принадлежность (нет named selections,
        Strategy A не нашла face_mats, Strategy F не нашла UV разделения).

        Возвращает СПИСОК split'ов длиной (n_mats - 1):
          [split1, split2, ...] — k-1 границ для разделения face'ов на k зон.
          face[0:s1) → mat_list[1]
          face[s1:s2) → mat_list[0]
          face[s2:s3) → mat_list[1]
          ...
          Пустой список = не нашли.

        Пробует 2 heuristics:
          1. UV k-means (k=n_mats) — кластеризация по UV
          2. Normal k-means (k=n_mats) — кластеризация по нормали
        Возвращает результат с лучшей confidence.
        """
        if idx not in self._models:
            return []
        state = self._models[idx]
        mat_groups = state.get('mat_groups', [])
        mat_list   = state.get('raw', {}).get('mat_list', [])
        raw        = state.get('raw', {})
        best_tris  = raw.get('best_tris', [])
        nrms       = raw.get('nrms', [])
        pts        = raw.get('pts_arr')
        uvs        = raw.get('uvs_arr')
        face_map   = raw.get('face_map', [])
        nf         = raw.get('nf', 0)
        n_mats     = raw.get('n_mats', 0)

        # Только если есть что разделять
        if n_mats < 2 or nf < 10 or not best_tris or pts is None or nrms is None:
            return []

        # Если mat_groups уже имеет 2+ раздельных групп с непустыми путями — не трогаем
        non_empty = [mg for mg in mat_groups if mg[0] and len(mg[1]) > 0]
        if len(non_empty) >= 2:
            return []

        # Если mat_list пуст (не получили имена материалов), не пытаемся
        if not mat_list or len(mat_list) < n_mats:
            return []

        # Вычислим нормали и centroid'ы face'ов
        face_normals = []
        face_centroids = []
        face_areas = np.zeros(len(face_map), dtype=np.float32)
        face_uv = np.zeros((len(face_map), 2), dtype=np.float32)
        for fi, (t_start, t_end) in enumerate(face_map):
            if t_start >= len(best_tris):
                continue
            tri = best_tris[t_start]
            try:
                p0, p1, p2 = np.array(pts[tri[0]]), np.array(pts[tri[1]]), np.array(pts[tri[2]])
                n = np.cross(p1 - p0, p2 - p0)
                ln = np.linalg.norm(n)
                if ln > 1e-9:
                    face_normals.append(n / ln)
                    face_areas[fi] = ln * 0.5
                else:
                    face_normals.append(np.array([0.0, 0.0, 0.0]))
                face_centroids.append((p0 + p1 + p2) / 3.0)
                if uvs is not None and len(uvs) == len(pts):
                    face_uv[fi] = ((uvs[tri[0]] + uvs[tri[1]] + uvs[tri[2]]) / 3.0)[:2]
            except Exception:
                face_normals.append(np.array([0.0, 0.0, 0.0]))
                face_centroids.append(np.array([0.0, 0.0, 0.0]))
        if len(face_normals) < 10:
            return []

        fn_arr = np.array(face_normals)
        fc_arr = np.array(face_centroids)
        n_face = len(fn_arr)

        candidates = []  # (splits, confidence, source)

        # ===== Heuristic A: Natural break on face area =====
        # Box sides / большие грани имеют area >> мелких деталей (болты и т.п.).
        # Ищем позицию, где средняя area справа >> средней area слева (или наоборот).
        try:
            cumsum_a = np.cumsum(face_areas)
            total_a = float(cumsum_a[-1])
            if total_a > 1e-9:
                ks = np.arange(1, n_face, dtype=np.float64)
                avg_left = cumsum_a[:-1] / ks
                avg_right = (total_a - cumsum_a[:-1]) / (n_face - ks)
                # Разница в средних (положительная = справа больше)
                gap_a = np.abs(avg_right - avg_left)
                # Только значимые позиции (не хвосты) — 1% margin для edge-case моделей (типа bolti где 96.6%)
                valid = (ks > n_face * 0.01) & (ks < n_face * 0.99)
                if valid.any():
                    gap_a_masked = np.where(valid, gap_a, -1)
                    best_k_a = int(np.argmax(gap_a_masked)) + 1
                    al = float(avg_left[best_k_a - 1])
                    ar = float(avg_right[best_k_a - 1])
                    ratio = (max(al, ar) / max(min(al, ar), 1e-9))
                    # conf: 5x разница = 0.5, 50x = 1.0
                    conf_a = min(1.0, ratio / 50.0)
                    # Граничные условия: split не слишком близко к краям (даём запас 1% для extreme cases типа bolti где mat=15/410)
                    if 0.01 * n_face < best_k_a < 0.99 * n_face and conf_a > 0.2:
                        candidates.append(([best_k_a], conf_a, f'area-break (ratio={ratio:.0f}x)'))
                        _dbg(f'auto_split A (area-break): split={best_k_a} ratio={ratio:.0f}x conf={conf_a:.2f}')
        except Exception as _e:
            _dbg(f'auto_split A error: {_e}')

        # ===== Heuristic B: Natural break on Y centroid =====
        # Коробка обычно имеет верх/низ с экстремальным y, отличным от болтов внутри.
        try:
            y_cent = fc_arr[:, 1]
            cumsum_y = np.cumsum(y_cent)
            total_y = float(cumsum_y[-1])
            if abs(total_y) > 1e-6:
                avg_left_y = cumsum_y[:-1] / ks
                avg_right_y = (total_y - cumsum_y[:-1]) / (n_face - ks)
                gap_y = np.abs(avg_right_y - avg_left_y)
                gap_y_masked = np.where(valid, gap_y, -1)
                best_k_y = int(np.argmax(gap_y_masked)) + 1
                y_std = float(np.std(y_cent)) + 1e-9
                ratio_y = gap_y[best_k_y - 1] / y_std
                conf_y = min(1.0, ratio_y / 3.0)
                if 0.01 * n_face < best_k_y < 0.99 * n_face and conf_y > 0.3:
                    if not candidates or best_k_y != candidates[-1][0][0]:
                        candidates.append(([best_k_y], conf_y, f'y-break (ratio={ratio_y:.1f}x)'))
                        _dbg(f'auto_split B (y-break): split={best_k_y} ratio={ratio_y:.1f}x conf={conf_y:.2f}')
        except Exception as _e:
            _dbg(f'auto_split B error: {_e}')

        # ===== Heuristic C: UV k-means =====
        if face_uv.any():
            try:
                splits_uv, conf_uv = _kmeans_splits(face_uv, n_mats, n_face)
                if splits_uv and all(0.02 < s/n_face < 0.98 for s in splits_uv):
                    candidates.append((splits_uv, conf_uv, f'uv k={n_mats}'))
                    _dbg(f'auto_split C (uv): splits={splits_uv} conf={conf_uv:.2f}')
            except Exception as _e:
                _dbg(f'auto_split C error: {_e}')

        # ===== Heuristic D: Normal k-means =====
        try:
            splits_n, conf_n = _kmeans_splits(fn_arr, n_mats, n_face)
            if splits_n and all(0.02 < s/n_face < 0.98 for s in splits_n):
                candidates.append((splits_n, conf_n, f'normal k={n_mats}'))
                _dbg(f'auto_split D (normal): splits={splits_n} conf={conf_n:.2f}')
        except Exception as _e:
            _dbg(f'auto_split D error: {_e}')

        if not candidates:
            # Сохраняем info о неудаче
            state['_auto_split_info'] = {'splits': [], 'confidence': 0.0, 'source': 'none',
                                          'candidates': [(c[0], round(c[1], 2), c[2]) for c in candidates]}
            return []

        # Выбираем по уверенности; если есть равные — предпочитаем более "большую" первую группу
        # (box-typical: малая площадь слева = коробка, или наоборот — для каждой модели по-разному)
        candidates.sort(key=lambda c: (-c[1], c[0][0]))
        best_splits, best_conf, source = candidates[0]
        _dbg(f'auto_split CHOSEN: {source} splits={best_splits} conf={best_conf:.2f}')

        # Сохраняем инфо для UI (confidence indicator + Auto-Tune)
        state['_auto_split_info'] = {
            'splits':     list(best_splits),
            'confidence': float(best_conf),
            'source':     source,
            'candidates': [(c[0], round(c[1], 2), c[2]) for c in candidates[:5]],
        }
        return best_splits

    def _auto_detect_thresholds(self):
        """
        Анализирует распределение длин рёбер и UV-спанов.
        Возвращает (suggested_edge_mult, suggested_uv_limit).
        Ищет наибольший относительный разрыв в отсортированных значениях.
        """
        raw = self._raw_model
        if raw is None:
            return 50, 1.5

        pts_arr   = raw.get('pts_arr')
        uvs_arr   = raw.get('uvs_arr')
        best_tris = raw.get('best_tris', [])
        base_limit = raw.get('base_limit', 1.0)

        # ── Авто edge_mult ────────────────────────────────────────────────────
        suggested_edge = 50
        if pts_arr is not None and len(best_tris) > 0:
            sample = best_tris[::max(1, len(best_tris) // 2000)]
            lengths = sorted([
                float(np.linalg.norm(pts_arr[t[i]] - pts_arr[t[(i+1)%3]]))
                for t in sample for i in range(3)
            ])
            if len(lengths) > 10 and base_limit > 0:
                cut = int(len(lengths) * 0.7)
                tail = lengths[cut:]
                best_gap = 0
                gap_pos  = len(lengths) - 1
                for k in range(len(tail) - 1):
                    gap = tail[k+1] - tail[k]
                    if gap > best_gap:
                        best_gap = gap
                        gap_pos  = cut + k
                if best_gap > 0:
                    boundary = lengths[gap_pos]
                    suggested_edge = max(1, int(boundary / base_limit) + 1)

        # ── Авто uv_limit ─────────────────────────────────────────────────────
        suggested_uv = 1.5
        if uvs_arr is not None and len(best_tris) > 0:
            sample = best_tris[::max(1, len(best_tris) // 2000)]
            spans = sorted([
                max(
                    max(float(uvs_arr[t[i]][0]) for i in range(3)) -
                    min(float(uvs_arr[t[i]][0]) for i in range(3)),
                    max(float(uvs_arr[t[i]][1]) for i in range(3)) -
                    min(float(uvs_arr[t[i]][1]) for i in range(3))
                )
                for t in sample
            ])
            if len(spans) > 10:
                cut = int(len(spans) * 0.9)
                tail = spans[cut:]
                best_gap = 0
                gap_pos  = len(spans) - 1
                for k in range(len(tail) - 1):
                    gap = tail[k+1] - tail[k]
                    if gap > best_gap:
                        best_gap = gap
                        gap_pos  = cut + k
                if best_gap > 0:
                    suggested_uv = round(spans[gap_pos], 2)
                    suggested_uv = max(0.1, min(10.0, suggested_uv))

        return suggested_edge, suggested_uv

    def _update_mat_panel(self):
        """Обновляет содержимое панели управления материалами."""
        if self._mat_panel is None:
            return
        T = self._T()
        for w in self._mat_panel.winfo_children():
            w.destroy()

        raw = self._raw_model
        if raw is None:
            lbl = tk.Label(self._mat_panel, text='Загрузите P3D файл',
                           bg=T['bg_side'], fg=T['fg_muted'],
                           font=(T['font_fam'], T['font_sz'] - 1))
            lbl.pack(padx=16, anchor='w', pady=4, fill=tk.X)
            self._tw_labels.append((lbl, 'bg_side', 'fg_muted', 'normal'))
            return

        mat_list = raw['mat_list']
        nf       = raw['nf']
        n_mats   = raw['n_mats']

        # Список материалов
        for i, mat in enumerate(mat_list):
            short = os.path.basename(mat.replace('\\', os.sep)) or mat
            lbl = tk.Label(self._mat_panel, text=f'[{i}] {short}',
                           bg=T['bg_side'], fg=T['fg_text'],
                           font=(T['font_fam'], T['font_sz'] - 1),
                           wraplength=None, anchor='w')
            lbl.pack(padx=16, anchor='w', pady=1, fill=tk.X)
            self._tw_labels.append((lbl, 'bg_side', 'fg_text', 'normal'))

        # ── v3.1.7: Per-material ALPHA BLENDING (чеклисты) ─────────────────────
        sep_alpha = tk.Frame(self._mat_panel, bg=T['bg_sep'], height=1)
        sep_alpha.pack(fill=tk.X, padx=16, pady=(8, 2))
        self._tw_seps.append(sep_alpha)

        alpha_hdr = tk.Label(self._mat_panel,
                            text='ALPHA BLENDING (прозрачность по материалу)',
                            bg=T['bg_side'], fg=T['fg_label'],
                            font=(T['font_fam'], T['font_sz'] - 1, 'bold'))
        alpha_hdr.pack(padx=16, anchor='w', fill=tk.X)
        self._tw_labels.append((alpha_hdr, 'bg_side', 'fg_label', 'bold'))

        alpha_hint = tk.Label(self._mat_panel,
                              text='Включайте для стёкол, сеток, прозрачных тканей.\nПо умолчанию всё выключено (как раньше).',
                              bg=T['bg_side'], fg=T['fg_muted'],
                              font=(T['font_fam'], T['font_sz'] - 3),
                              wraplength=None)
        alpha_hint.pack(padx=16, anchor='w', pady=(0, 4))
        self._tw_labels.append((alpha_hint, 'bg_side', 'fg_muted', 'normal'))

        # Загружаем текущее состояние per-material из viewport (sync с settings)
        alpha_state = getattr(self.viewport, '_alpha_mats', {})

        # v3.2.0: store references к checkbox vars для in-place update (НЕ destroy+recreate)
        # Чтобы кнопки "Все ВКЛ/Сбросить/Подсказка" не вызывали flash
        self._alpha_vars = {}  # {mat: tk.BooleanVar}

        for i, mat in enumerate(mat_list):
            short = os.path.basename(mat.replace('\\', os.sep)) or mat or f'mat_{i}'
            var = tk.BooleanVar(value=alpha_state.get(mat, False))

            def _on_alpha_toggle(m=mat, v=var):
                # m захватывается через default-arg (избегаем closure NameError)
                if v.get():
                    self.viewport._alpha_mats[m] = True
                else:
                    self.viewport._alpha_mats.pop(m, None)
                # persist в settings
                self._app_settings['alpha_mats'] = dict(self.viewport._alpha_mats)
                _save_app_settings(self._app_settings)
                # перерисовка
                try: self.viewport.redraw()
                except Exception: pass

            cb = tk.Checkbutton(
                self._mat_panel,
                text=f'[{i}] {short[:50]}',
                variable=var,
                bg=T['bg_side'], fg=T['fg_text'],
                selectcolor=T['bg_widget'],
                activebackground=T['bg_side'], activeforeground=T['fg_text'],
                font=(T['font_fam'], T['font_sz'] - 2),
                command=_on_alpha_toggle,
                anchor='w',
            )
            cb.pack(padx=24, anchor='w', pady=1, fill=tk.X)
            self._tw_checkbuttons.append((cb, 'normal'))
            # v3.2.0: save var ref для in-place update (НЕ destroy+recreate)
            self._alpha_vars[mat] = var

        # Кнопки "Включить все / Сбросить"
        alpha_btn_row = tk.Frame(self._mat_panel, bg=T['bg_side'])
        alpha_btn_row.pack(fill=tk.X, padx=24, pady=(4, 4))
        self._tw_frames.append((alpha_btn_row, 'bg_side'))

        def _alpha_all():
            # v3.2.0: in-place update (НЕ _update_mat_panel — иначе flash)
            for m, v in self._alpha_vars.items():
                v.set(True)
                self.viewport._alpha_mats[m] = True
            self._app_settings['alpha_mats'] = dict(self.viewport._alpha_mats)
            _save_app_settings(self._app_settings)
            try: self.viewport.redraw()
            except Exception: pass

        def _alpha_none():
            # v3.2.0: in-place update
            for m, v in self._alpha_vars.items():
                v.set(False)
            self.viewport._alpha_mats.clear()
            self._app_settings['alpha_mats'] = {}
            _save_app_settings(self._app_settings)
            try: self.viewport.redraw()
            except Exception: pass

        b_all = tk.Button(alpha_btn_row, text='Все ВКЛ', relief='flat',
                          bg=T['btn_bg'], fg=T['btn_fg'],
                          font=(T['font_fam'], T['font_sz'] - 2),
                          padx=8, pady=2, command=_alpha_all)
        b_all.pack(side=tk.LEFT, padx=(0, 4))
        self._tw_buttons.append((b_all, 'normal'))

        b_none = tk.Button(alpha_btn_row, text='Сбросить', relief='flat',
                           bg=T['bg_widget'], fg=T['fg_text'],
                           font=(T['font_fam'], T['font_sz'] - 2),
                           padx=8, pady=2, command=_alpha_none)
        b_none.pack(side=tk.LEFT)
        self._tw_buttons.append((b_none, 'normal'))

        # Авто-подсветка типичных прозрачных (стекло/окно/сетка)
        def _alpha_suggest():
            # v3.2.0: in-place update (НЕ _update_mat_panel — иначе flash)
            keywords = ('glass', 'window', 'screen', 'mesh', 'lattice', 'wire', 'grid', 'transparent')
            n_found = 0
            for m in mat_list:
                ml = m.lower() if m else ''
                if any(k in ml for k in keywords):
                    if m:
                        self.viewport._alpha_mats[m] = True
                        # in-place update checkbox var
                        if m in self._alpha_vars:
                            self._alpha_vars[m].set(True)
                        n_found += 1
            self._app_settings['alpha_mats'] = dict(self.viewport._alpha_mats)
            _save_app_settings(self._app_settings)
            try: self.viewport.redraw()
            except Exception: pass
            if n_found:
                self._status(f'Alpha: подсвечено {n_found} мат. (glass/window/...)', '#a6e3a1')
            else:
                self._status(f'Alpha: ничего не найдено (glass/window/...)', '#f9e2af')

        b_sug = tk.Button(alpha_btn_row, text='🔍 Подсказка', relief='flat',
                          bg=T['bg_widget'], fg=T['fg_text'],
                          font=(T['font_fam'], T['font_sz'] - 2),
                          padx=8, pady=2, command=_alpha_suggest)
        b_sug.pack(side=tk.LEFT, padx=(4, 0))
        self._tw_buttons.append((b_sug, 'normal'))


        # ── UV Threshold (показывается для ВСЕХ моделей) ─────────────────────
        sep_uv = tk.Frame(self._mat_panel, bg=T['bg_sep'], height=1)
        sep_uv.pack(fill=tk.X, padx=16, pady=(6, 2))
        self._tw_seps.append(sep_uv)

        uv_hdr = tk.Label(self._mat_panel,
                          text='UV порог отсечения (0.1 – 10.0)',
                          bg=T['bg_side'], fg=T['fg_label'],
                          font=(T['font_fam'], T['font_sz'] - 1))
        uv_hdr.pack(padx=16, anchor='w', fill=tk.X)
        self._tw_labels.append((uv_hdr, 'bg_side', 'fg_label', 'normal'))

        uv_hint = tk.Label(self._mat_panel,
                           text='Меньше = строже (убирает артефакты по швам)',
                           bg=T['bg_side'], fg=T['fg_muted'],
                           font=(T['font_fam'], T['font_sz'] - 3),
                           wraplength=None)
        uv_hint.pack(padx=16, anchor='w', pady=(0, 2))
        self._tw_labels.append((uv_hint, 'bg_side', 'fg_muted', 'normal'))

        uv_row = tk.Frame(self._mat_panel, bg=T['bg_side'])
        uv_row.pack(fill=tk.X, padx=16, pady=2)
        self._tw_frames.append((uv_row, 'bg_side'))

        cur_uv = getattr(self, '_cur_uv_limit', 1.5)
        uv_var     = tk.DoubleVar(value=cur_uv)
        uv_entry_v = tk.StringVar(value=str(round(cur_uv, 2)))

        uv_slider = tk.Scale(
            uv_row, variable=uv_var,
            from_=0.1, to=10.0, resolution=0.1, orient=tk.HORIZONTAL,
            bg=T['bg_side'], fg=T['fg_text'], troughcolor=T['bg_widget'],
            highlightthickness=0, bd=0, sliderrelief=tk.FLAT, showvalue=0,
            command=lambda v: uv_entry_v.set(str(round(float(v), 2))))
        uv_slider.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._tw_scales.append(uv_slider)

        uv_entry = tk.Entry(uv_row, textvariable=uv_entry_v,
                            bg=T['bg_widget'], fg=T['fg_text'],
                            insertbackground=T['fg_text'],
                            relief='flat', width=5,
                            font=(T['font_fam'], T['font_sz']))
        uv_entry.pack(side=tk.LEFT, padx=(4, 0))

        def _on_uv_entry(*_):
            try:
                v = max(0.05, min(10.0, float(uv_entry_v.get())))
                uv_var.set(v)
                uv_entry_v.set(str(round(v, 2)))
            except ValueError:
                pass
        uv_entry.bind('<Return>', _on_uv_entry)
        uv_entry.bind('<FocusOut>', _on_uv_entry)

        # Быстрые пресеты UV
        uv_pf = tk.Frame(self._mat_panel, bg=T['bg_side'])
        uv_pf.pack(fill=tk.X, padx=16, pady=(0, 2))
        self._tw_frames.append((uv_pf, 'bg_side'))
        for _uv_label, _uv_val in [('0.3', 0.3), ('0.5', 0.5), ('0.8', 0.8),
                                    ('1.0', 1.0), ('1.5', 1.5),
                                    ('3.0', 3.0), ('5.0', 5.0), ('10.0', 10.0)]:
            b = tk.Button(uv_pf, text=_uv_label, relief='flat',
                          bg=T['btn_bg'], fg=T['btn_fg'],
                          font=(T['font_fam'], T['font_sz'] - 1), padx=4, pady=1,
                          command=lambda v=_uv_val: [uv_var.set(v),
                                                     uv_entry_v.set(str(v))])
            b.pack(side=tk.LEFT, padx=1)
            self._tw_buttons.append((b, 'normal'))

        def _auto_uv():
            _, suggested = self._auto_detect_thresholds()
            uv_var.set(suggested)
            uv_entry_v.set(str(suggested))
            self._status(f'Авто UV порог: {suggested}', '#89b4fa')
        tk.Button(self._mat_panel, text='🔍 Авто UV', relief='flat',
                  bg=T['btn_bg'], fg=T['btn_fg'],
                  font=(T['font_fam'], T['font_sz'] - 1), padx=4, pady=1,
                  command=_auto_uv).pack(padx=16, anchor='w', pady=(0, 4))

        # ── Порог длины рёбер ─────────────────────────────────────────────────
        sep_edge = tk.Frame(self._mat_panel, bg=T['bg_sep'], height=1)
        sep_edge.pack(fill=tk.X, padx=16, pady=(6, 2))
        self._tw_seps.append(sep_edge)

        edge_hdr = tk.Label(self._mat_panel,
                            text='Порог длины рёбер (×median, 1 – 5000)',
                            bg=T['bg_side'], fg=T['fg_label'],
                            font=(T['font_fam'], T['font_sz'] - 1))
        edge_hdr.pack(padx=16, anchor='w', fill=tk.X)
        self._tw_labels.append((edge_hdr, 'bg_side', 'fg_label', 'normal'))

        edge_hint = tk.Label(self._mat_panel,
                             text='Больше = меньше треугольников скрывается',
                             bg=T['bg_side'], fg=T['fg_muted'],
                             font=(T['font_fam'], T['font_sz'] - 3),
                             wraplength=None)
        edge_hint.pack(padx=16, anchor='w', pady=(0, 2))
        self._tw_labels.append((edge_hint, 'bg_side', 'fg_muted', 'normal'))

        edge_row = tk.Frame(self._mat_panel, bg=T['bg_side'])
        edge_row.pack(fill=tk.X, padx=16, pady=2)
        self._tw_frames.append((edge_row, 'bg_side'))

        cur_edge = getattr(self, '_cur_edge_mult', 50.0)
        edge_var     = tk.IntVar(value=int(cur_edge))
        edge_entry_v = tk.StringVar(value=str(int(cur_edge)))

        edge_slider = tk.Scale(
            edge_row, variable=edge_var,
            from_=1, to=5000, resolution=1, orient=tk.HORIZONTAL,
            bg=T['bg_side'], fg=T['fg_text'], troughcolor=T['bg_widget'],
            highlightthickness=0, bd=0, sliderrelief=tk.FLAT, showvalue=0,
            command=lambda v: edge_entry_v.set(str(int(float(v)))))
        edge_slider.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._tw_scales.append(edge_slider)

        edge_entry = tk.Entry(edge_row, textvariable=edge_entry_v,
                              bg=T['bg_widget'], fg=T['fg_text'],
                              insertbackground=T['fg_text'],
                              relief='flat', width=5,
                              font=(T['font_fam'], T['font_sz']))
        edge_entry.pack(side=tk.LEFT, padx=(4, 0))

        def _on_edge_entry(*_):
            try:
                v = max(1, min(5000, int(edge_entry_v.get())))
                edge_var.set(v)
                edge_entry_v.set(str(v))
            except ValueError:
                pass
        edge_entry.bind('<Return>', _on_edge_entry)
        edge_entry.bind('<FocusOut>', _on_edge_entry)

        # Быстрые пресеты edge
        edge_pf = tk.Frame(self._mat_panel, bg=T['bg_side'])
        edge_pf.pack(fill=tk.X, padx=16, pady=(0, 2))
        self._tw_frames.append((edge_pf, 'bg_side'))
        for _el, _ev in [('10', 10), ('25', 25), ('50', 50), ('100', 100),
                          ('500', 500), ('1000', 1000), ('2000', 2000), ('5000', 5000)]:
            b = tk.Button(edge_pf, text=_el, relief='flat',
                          bg=T['btn_bg'], fg=T['btn_fg'],
                          font=(T['font_fam'], T['font_sz'] - 1), padx=4, pady=1,
                          command=lambda v=_ev: [edge_var.set(v), edge_entry_v.set(str(v))])
            b.pack(side=tk.LEFT, padx=1)
            self._tw_buttons.append((b, 'normal'))

        def _auto_edge():
            suggested, _ = self._auto_detect_thresholds()
            edge_var.set(suggested)
            edge_entry_v.set(str(suggested))
            self._status(f'Авто порог рёбер: {suggested}', '#89b4fa')
        tk.Button(self._mat_panel, text='🔍 Авто рёбра', relief='flat',
                  bg=T['btn_bg'], fg=T['btn_fg'],
                  font=(T['font_fam'], T['font_sz'] - 1), padx=4, pady=1,
                  command=_auto_edge).pack(padx=16, anchor='w', pady=(0, 4))

        # v3.0: Mirror UV — toggle скрыт из UI (default OFF). Код сохранён для будущего использования.
        self._mirror_uv_var = tk.BooleanVar(value=self._app_settings.get('mirror_uv', False))
        def _on_mirror_change():
            self._app_settings['mirror_uv'] = self._mirror_uv_var.get()
            self._refresh_mirror_uv_uniform()
        # Toggle НЕ показывается в UI (hidden, default OFF).

        # v3.0: Mesh mirror по осям X/Y/Z — flip vertex positions + normals + tangents
        flip_row = tk.Frame(self._mat_panel, bg=T['bg_side'])
        flip_row.pack(fill=tk.X, padx=16, pady=(0, 2))
        self._tw_frames.append((flip_row, 'bg_side'))
        flip_lbl = tk.Label(flip_row, text='🪞 Отражение модели:',
                            bg=T['bg_side'], fg=T['fg_muted'],
                            font=(T['font_fam'], T['font_sz'] - 1))
        flip_lbl.pack(side=tk.LEFT, padx=(0, 6))
        self._tw_labels.append((flip_lbl, 'bg_side', 'fg_muted', 'small'))

        def make_flip_cb(axis):
            def cb():
                self._app_settings[f'flip_{axis}'] = getattr(self, f'_flip_{axis}_var').get()
                self._refresh_flip_uniform(axis)
            return cb
        for axis in ('x', 'y', 'z'):
            var = tk.BooleanVar(value=self._app_settings.get(f'flip_{axis}', False))
            setattr(self, f'_flip_{axis}_var', var)
            tk.Checkbutton(flip_row, text=axis.upper(),
                           variable=var, command=make_flip_cb(axis),
                           bg=T['bg_side'], fg=T['fg_text'],
                           selectcolor=T['bg_widget'],
                           activebackground=T['bg_side'],
                           activeforeground=T['fg_text'],
                           font=(T['font_fam'], T['font_sz'] - 1)).pack(side=tk.LEFT, padx=2)

        # ── Контроли разбивки (только для моделей с 2+ материалами) ──────────
        swap_var = tk.BooleanVar(value=self._cur_swap)

        if n_mats >= 2:
            # Вспомогательная функция: виджет «слайдер + текстовое поле»
            def make_split_row(parent, label_text, init_val, row_tag):
                sep = tk.Frame(parent, bg=T['bg_sep'], height=1)
                sep.pack(fill=tk.X, padx=16, pady=(6, 2))
                self._tw_seps.append(sep)

                hdr = tk.Label(parent, text=label_text,
                               bg=T['bg_side'], fg=T['fg_label'],
                               font=(T['font_fam'], T['font_sz'] - 1))
                hdr.pack(padx=16, anchor='w', fill=tk.X)
                self._tw_labels.append((hdr, 'bg_side', 'fg_label', 'normal'))

                row = tk.Frame(parent, bg=T['bg_side'])
                row.pack(fill=tk.X, padx=16, pady=2)
                self._tw_frames.append((row, 'bg_side'))

                var = tk.IntVar(value=init_val)
                entry_var = tk.StringVar(value=str(init_val))

                slider = tk.Scale(
                    row, variable=var,
                    from_=0, to=nf, orient=tk.HORIZONTAL,
                    bg=T['bg_side'], fg=T['fg_text'], troughcolor=T['bg_widget'],
                    highlightthickness=0, bd=0, sliderrelief=tk.FLAT,
                    showvalue=0,
                    command=lambda v: entry_var.set(str(int(float(v)))))
                slider.pack(side=tk.LEFT, fill=tk.X, expand=True)
                self._tw_scales.append(slider)

                entry = tk.Entry(row, textvariable=entry_var,
                                 bg=T['bg_widget'], fg=T['fg_text'],
                                 insertbackground=T['fg_text'],
                                 relief='flat', width=7,
                                 font=(T['font_fam'], T['font_sz']))
                entry.pack(side=tk.LEFT, padx=(4, 0))
                self._tw_frames.append((entry, 'bg_widget'))

                def _on_entry(*_):
                    try:
                        v = int(entry_var.get())
                        v = max(0, min(nf, v))
                        var.set(v)
                        entry_var.set(str(v))
                    except ValueError:
                        pass
                entry.bind('<Return>', _on_entry)
                entry.bind('<FocusOut>', _on_entry)

                preset_f = tk.Frame(parent, bg=T['bg_side'])
                preset_f.pack(fill=tk.X, padx=16, pady=(0, 2))
                self._tw_frames.append((preset_f, 'bg_side'))
                for name, val in [('0', 0), ('1/4', nf//4), ('1/2', nf//2),
                                   ('3/4', nf*3//4), ('MAX', nf)]:
                    b = tk.Button(preset_f, text=name, relief='flat',
                                  bg=T['btn_bg'], fg=T['btn_fg'],
                                  font=(T['font_fam'], T['font_sz'] - 1), padx=4, pady=1,
                                  command=lambda v=val: [var.set(v), entry_var.set(str(v))])
                    b.pack(side=tk.LEFT, padx=1)
                    self._tw_buttons.append((b, 'normal'))

                return var

            # v2.3: динамически создаём N-1 слайдеров для N материалов
            self._split_vars = []   # list of (var, entry_var, row_tag) для каждой границы

            # Получаем текущие splits (из state если есть)
            cur_splits = []
            if hasattr(self, '_raw_model') and self._raw_model is not None:
                state = self._models.get(self._active_idx) if self._active_idx is not None else None
                if state:
                    cur_splits = list(state.get('cur_splits', []))
                if not cur_splits:
                    # Fallback к legacy split/split2
                    if self._cur_split and self._cur_split > 0:
                        cur_splits.append(self._cur_split)
                    if self._cur_split2 and self._cur_split2 > 0:
                        cur_splits.append(self._cur_split2)
            cur_splits.sort()

            # Создаём n_mats-1 слайдеров (по одному на каждую границу)
            for i in range(n_mats - 1):
                init_val = cur_splits[i] if i < len(cur_splits) else 0
                # Описание: для i=0 "[0 … Г1) → мат[1]", для i=1 "[Г1 … Г2) → мат[0]", ...
                # Чередуемся: 1, 0, 1, 0, ...
                target_mat = (i + 1) if i % 2 == 0 else i
                if target_mat >= n_mats:
                    target_mat = n_mats - 1
                # Упрощённое описание
                if i == 0:
                    desc = f'Граница 1  [0 … Г1) → мат[{target_mat}]  (0–{nf})'
                else:
                    desc = f'Граница {i+1}  (i = {i+1}, диапазон 0–{nf})'
                self._split_vars.append(
                    make_split_row(self._mat_panel, desc, init_val, f's{i+1}')
                )

            # Backward compat: первый/второй split_vars как _split_var/_split2_var
            self._split_var  = self._split_vars[0] if len(self._split_vars) >= 1 else None
            self._split2_var = self._split_vars[1] if len(self._split_vars) >= 2 else None

            # Подсказка
            hint_text = f'Разделение на {n_mats} мат: используйте {n_mats-1} слайдер(ов)'
            hint = tk.Label(self._mat_panel,
                            text=hint_text,
                            bg=T['bg_side'], fg=T['fg_muted'],
                            font=(T['font_fam'], T['font_sz'] - 2),
                            wraplength=None)
            hint.pack(padx=16, anchor='w', pady=(2, 0))
            self._tw_labels.append((hint, 'bg_side', 'fg_muted', 'normal'))

            # v2.3: блок Auto-Tune — кнопка + confidence indicator
            def _auto_tune():
                """Повторно запускает auto-split эвристики и применяет результат."""
                # v3.1.7: try/finally гарантирует сброс кнопки даже при exception
                try:
                    state = self._models.get(self._active_idx) if self._active_idx is not None else None
                    if not state:
                        self._status('Auto-Tune: сначала выберите модель в списке "Загружено"', '#f9e2af')
                        return
                    # v3.1.6: показать "Анализ..." в кнопке
                    try:
                        b_tune.config(text='⏳ Анализ...', state='disabled')
                        self.root.update_idletasks()
                    except Exception: pass
                    splits = self._auto_split_for_multi_material(self._active_idx)
                    info = state.get('_auto_split_info', {})
                    if not splits:
                        self._status('Auto-Tune: эвристики не нашли split, настройте вручную', '#f9e2af')
                        self._show_auto_tune_popup('Эвристики не нашли split.\n\nВозможные причины:\n• Модель single-material (нечего делить)\n• Все триангуляции в одной зоне UV\n\nПопробуйте настроить splits вручную.')
                        return
                    # Применяем найденные splits к слайдерам
                    for i, var_tuple in enumerate(self._split_vars or []):
                        if i < len(splits):
                            try:
                                var_tuple[0].set(int(splits[i]))
                            except Exception:
                                pass
                    # Запускаем _recompute_mats
                    all_splits = sorted([int(s) for s in splits])
                    uv   = round(uv_var.get(), 3)
                    edge = int(edge_var.get())
                    self._recompute_mats(splits=all_splits, swap=swap_var.get(),
                                         uv_limit=uv, edge_mult=edge)
                    conf = info.get('confidence', 0.0)
                    src  = info.get('source', 'unknown')
                    self._status(f'Auto-Tune: {src} conf={conf:.2f} splits={all_splits}', '#89b4fa')
                    conf_pct = int(conf * 100)
                    if conf >= 0.7:
                        label = '🟢 НАДЁЖНО'
                    elif conf >= 0.4:
                        label = '🟡 ПРОВЕРЬТЕ'
                    else:
                        label = '🔴 ВРУЧНУЮ'
                    _show_auto_tune_popup(
                        f'Результат: {label}\n\n'
                        f'Алгоритм: {src}\n'
                        f'Уверенность: {conf_pct}% ({conf:.2f})\n'
                        f'Splits: {all_splits}\n\n'
                        f'Применено к слайдерам и пересчитано.'
                    )
                    _update_confidence_label()
                except Exception as _e:
                    _dbg(f'_auto_tune FATAL: {_e}')
                    try:
                        import traceback as _tb
                        _dbg(_tb.format_exc())
                    except Exception: pass
                finally:
                    # v3.1.7: сброс кнопки в любом случае (через after — главный поток)
                    def _reset_btn():
                        try:
                            if hasattr(self, '_tune_btn') and self._tune_btn is not None:
                                self._tune_btn.config(text='✨  Auto-Tune', state='normal')
                        except Exception: pass
                    try: self.root.after(50, _reset_btn)
                    except Exception:
                        _reset_btn()

            def _update_confidence_label():
                state = self._models.get(self._active_idx) if self._active_idx is not None else None
                if not state:
                    return
                info = state.get('_auto_split_info', {})
                conf = info.get('confidence', 0.0)
                src  = info.get('source', '—')
                if conf >= 0.7:
                    color = '#a6e3a1'  # зелёный — надёжно
                    icon  = '🟢'
                elif conf >= 0.4:
                    color = '#f9e2af'  # жёлтый — стоит проверить
                    icon  = '🟡'
                elif conf > 0:
                    color = '#f38ba8'  # красный — вручную
                    icon  = '🔴'
                else:
                    color = T['fg_muted']
                    icon  = '⚪'
                try:
                    conf_lbl.config(text=f'{icon}  Auto: {src}  conf={conf:.2f}', fg=color)
                except Exception:
                    pass

            tune_row = tk.Frame(self._mat_panel, bg=T['bg_side'])
            tune_row.pack(fill=tk.X, padx=16, pady=(4, 2))
            self._tw_frames.append((tune_row, 'bg_side'))

            b_tune = tk.Button(tune_row, text='✨  Auto-Tune', relief='flat',
                               bg=T['btn_bg'], fg='#cba6f7',
                               font=(T['font_fam'], T['font_sz'] - 1), padx=6, pady=3,
                               command=_auto_tune)
            b_tune.pack(side=tk.LEFT, padx=(0, 6))
            self._tw_buttons.append((b_tune, 'normal'))
            # v3.1.6: сохранить reference для дисейбла когда модель не активна
            self._tune_btn = b_tune
            # Начальное состояние: отключена пока нет активной модели
            if self._active_idx is None:
                b_tune.config(state='disabled', fg=T['fg_muted'])

            conf_lbl = tk.Label(tune_row, text='⚪  Auto: —  conf=0.00',
                                bg=T['bg_side'], fg=T['fg_muted'],
                                font=(T['font_fam'], T['font_sz'] - 2))
            conf_lbl.pack(side=tk.LEFT)
            self._tw_labels.append((conf_lbl, 'bg_side', 'fg_muted', 'normal'))

            # Показываем индикатор из state если есть
            self.root.after(50, _update_confidence_label)

        # ── Кнопка «Применить вручную» (ВСЕГДА видна, не зависит от n_mats) ──
        def _apply():
            # ПРЯМОЙ print в файл (stderr в windowed EXE теряется)
            try:
                _apply_log = os.path.join(_CACHE_DIR, 'apply_log.txt')
                with open(_apply_log, 'a', encoding='utf-8') as _alf:
                    _alf.write(f'>>> _apply() ENTER n_mats={n_mats}\n')
            except Exception:
                pass
            # Визуальный feedback
            if hasattr(self, '_apply_btn'):
                try:
                    self._apply_btn.config(text='⏳ Применяю...', state='disabled')
                    self.root.update_idletasks()
                except Exception:
                    pass
            # mirror UV теперь снова toggle (default ON)
            if hasattr(self, '_mirror_uv_var'):
                try:
                    self._refresh_mirror_uv_uniform()
                except Exception as _me:
                    try:
                        with open(_apply_log, 'a', encoding='utf-8') as _alf:
                            _alf.write(f'_refresh_mirror_uv_uniform FAILED: {_me!r}\n')
                    except Exception:
                        pass
            if hasattr(self, '_per_face_flip_var'):
                try:
                    self._refresh_per_face_flip_uniform()
                except Exception as _pe:
                    try:
                        with open(_apply_log, 'a', encoding='utf-8') as _alf:
                            _alf.write(f'_refresh_per_face_flip_uniform FAILED: {_pe!r}\n')
                    except Exception:
                        pass
            for axis in ('x', 'y', 'z'):
                if hasattr(self, f'_flip_{axis}_var'):
                    try:
                        self._refresh_flip_uniform(axis)
                    except Exception as _fe:
                        try:
                            with open(_apply_log, 'a', encoding='utf-8') as _alf:
                                _alf.write(f'_refresh_flip_{axis}_uniform FAILED: {_fe!r}\n')
                        except Exception:
                            pass
            uv   = round(uv_var.get(), 3)
            edge = int(edge_var.get())
            if n_mats >= 2:
                self._cur_swap = swap_var.get()
                all_splits = sorted([int(v.get()) for v in (self._split_vars or []) if v.get() > 0])
                try:
                    self._recompute_mats(splits=all_splits,
                                         swap=self._cur_swap,
                                         uv_limit=uv,
                                         edge_mult=edge)
                except Exception as _re:
                    try:
                        with open(_apply_log, 'a', encoding='utf-8') as _alf:
                            _alf.write(f'_recompute_mats FAILED: {_re!r}\n')
                    except Exception:
                        pass
            else:
                try:
                    self._recompute_mats(uv_limit=uv, edge_mult=edge)
                except Exception as _re:
                    try:
                        with open(_apply_log, 'a', encoding='utf-8') as _alf:
                            _alf.write(f'_recompute_mats FAILED: {_re!r}\n')
                    except Exception:
                        pass
            try:
                with open(_apply_log, 'a', encoding='utf-8') as _alf:
                    _alf.write('<<< _apply() EXIT\n')
            except Exception:
                pass
            # Визуальный feedback
            if hasattr(self, '_apply_btn'):
                try:
                    self._apply_btn.config(text='✔ Применено!', state='normal')
                    self.root.after(800, lambda: self._apply_btn.config(text='✔  Применить (ручн.)') if hasattr(self, '_apply_btn') else None)
                except Exception:
                    pass

        # Apply button — используем bind (более надёжно чем command через closure)
        apply_btn = self._btn(self._mat_panel, '✔  Применить (ручн.)', None, accent=True)
        apply_btn.configure(command=_apply)
        apply_btn.pack(fill=tk.X, padx=16, pady=(6, 4))
        self._apply_btn = apply_btn
        self._apply_callback = _apply  # сохраняем для возможного перепривязывания

        # Чекбокс «поменять местами» (только для n_mats >= 2) — обёрнут в свой if
        if n_mats >= 2:
            sep2 = tk.Frame(self._mat_panel, bg=T['bg_sep'], height=1)
            sep2.pack(fill=tk.X, padx=16, pady=(6, 2))
            self._tw_seps.append(sep2)
            swap_cb = tk.Checkbutton(self._mat_panel, text='Поменять текстуры местами',
                variable=swap_var, bg=T['bg_side'], fg=T['fg_text'],
                selectcolor=T['bg_widget'], activebackground=T['bg_side'],
                font=(T['font_fam'], T['font_sz'] - 1))
            swap_cb.pack(padx=16, anchor='w', pady=(2, 2))

        # Блок сохранения конфига (ВСЕГДА виден, не зависит от n_mats)
        sep3 = tk.Frame(self._mat_panel, bg=T['bg_sep'], height=1)
        sep3.pack(fill=tk.X, padx=16, pady=(6, 2))
        self._tw_seps.append(sep3)

        cfg_hdr = tk.Label(self._mat_panel, text='КОНФИГ ПРЕДМЕТА', bg=T['bg_side'],
            fg=T['fg_label'], font=(T['font_fam'], T['font_sz'] - 1))
        cfg_hdr.pack(padx=16, anchor='w', fill=tk.X)
        self._tw_labels.append((cfg_hdr, 'bg_side', 'fg_label', 'normal'))

        # Статус: загружен ли конфиг
        cfg_name = getattr(self, '_config_loaded_name', None)
        model_name_cur = (os.path.splitext(os.path.basename(self.current_file))[0]
                          if self.current_file else '')
        if cfg_name:
            cfg_status_txt = f'✔ Конфиг загружен: {cfg_name}.json'
            cfg_status_clr = '#a6e3a1'
        else:
            cfg_status_txt = 'Конфиг не сохранён'
            cfg_status_clr = T['fg_muted']

        cfg_status = tk.Label(self._mat_panel, text=cfg_status_txt,
                              bg=T['bg_side'], fg=cfg_status_clr,
                              font=(T['font_fam'], T['font_sz'] - 2),
                              wraplength=None)
        cfg_status.pack(padx=16, anchor='w', pady=(2, 4))
        self._tw_labels.append((cfg_status, 'bg_side', 'fg_muted', 'normal'))

        # Поле заметки
        note_lbl = tk.Label(self._mat_panel, text='Заметка (необязательно):',
                             bg=T['bg_side'], fg=T['fg_label'],
                             font=(T['font_fam'], T['font_sz'] - 2))
        note_lbl.pack(padx=16, anchor='w', fill=tk.X)
        self._tw_labels.append((note_lbl, 'bg_side', 'fg_label', 'normal'))

        existing_note = ''
        if model_name_cur:
            ec = _load_item_config(model_name_cur)
            if ec:
                existing_note = ec.get('note', '')

        note_var = tk.StringVar(value=existing_note)
        note_entry = tk.Entry(self._mat_panel, textvariable=note_var,
                              bg=T['bg_widget'], fg=T['fg_text'],
                              insertbackground=T['fg_text'],
                              relief='flat', font=(T['font_fam'], T['font_sz'] - 1))
        note_entry.pack(fill=tk.X, padx=16, pady=(2, 4))

        # Кнопки конфига
        btn_row = tk.Frame(self._mat_panel, bg=T['bg_side'])
        btn_row.pack(fill=tk.X, padx=16, pady=(0, 4))
        self._tw_frames.append((btn_row, 'bg_side'))

        def _save_cfg():
            if not model_name_cur:
                return
            # v2.3: собираем все splits из динамических слайдеров
            all_splits = sorted([int(v.get()) for v in (self._split_vars or []) if v.get() > 0])
            s1   = all_splits[0] if len(all_splits) >= 1 else 0
            s2   = all_splits[1] if len(all_splits) >= 2 else 0
            sw   = swap_var.get()
            uv   = round(uv_var.get(), 3)
            edge = int(edge_var.get())
            saved_path = _save_item_config(model_name_cur, s1, s2, sw, uv, edge, note_var.get(),
                                          splits=all_splits if all_splits else None)
            self._config_loaded_name = model_name_cur
            cfg_status.config(text=f'✔ Сохранено: {model_name_cur}.json',
                              fg='#a6e3a1')
            self._status(f'Конфиг сохранён: {os.path.basename(saved_path)}', '#a6e3a1')

        def _delete_cfg():
            if not model_name_cur:
                return
            p = _cfg_path(model_name_cur)
            if os.path.exists(p):
                os.remove(p)
            self._config_loaded_name = None
            cfg_status.config(text='Конфиг удалён', fg=T['fg_muted'])
            self._status('Конфиг удалён', '#6c7086')

        def _open_cfg_folder():
            d = _get_config_dir()
            import subprocess as _sp
            _sp.Popen(['explorer', d])

        def _embed_cfg():
            """Внедряет текущий конфиг в EXE/embedded_configs.py."""
            if not model_name_cur:
                return
            all_splits = sorted([int(v.get()) for v in (self._split_vars or []) if v.get() > 0])
            s1   = all_splits[0] if len(all_splits) >= 1 else 0
            s2   = all_splits[1] if len(all_splits) >= 2 else 0
            sw   = swap_var.get()
            uv   = round(uv_var.get(), 3)
            edge = int(edge_var.get())
            cfg = {
                'model':           model_name_cur,
                'split1':          int(s1),
                'split2':          int(s2),
                'swap':            bool(sw),
                'uv_threshold':    float(round(uv, 3)),
                'edge_threshold':  int(edge),
                'note':            str(note_var.get()),
            }
            if all_splits:
                cfg['splits'] = [int(s) for s in all_splits]
            ok, msg = _embed_config(model_name_cur, cfg)
            if ok:
                cfg_status.config(text=f'📌 Внедрено: {model_name_cur}', fg='#89b4fa')
                self._status(msg, '#89b4fa')
            else:
                cfg_status.config(text='✗ Ошибка внедрения', fg='#f38ba8')
                self._status(msg, '#f38ba8')

        def _unembed_cfg():
            """Удаляет внедрённый конфиг."""
            if not model_name_cur:
                return
            ok, msg = _unembed_config(model_name_cur)
            if ok:
                cfg_status.config(text='Внедрение снято', fg=T['fg_muted'])
                self._status(msg, '#6c7086')
            else:
                cfg_status.config(text='Не внедрён', fg=T['fg_muted'])
                self._status(msg, '#6c7086')

        b_save = tk.Button(btn_row, text='💾  Сохранить', relief='flat',
                           bg=T['btn_bg'], fg=T['btn_fg'],
                           font=(T['font_fam'], T['font_sz'] - 1), padx=6, pady=3,
                           command=_save_cfg)
        b_save.pack(side=tk.LEFT, padx=(0, 4))
        self._tw_buttons.append((b_save, 'normal'))

        b_embed = tk.Button(btn_row, text='📌  Внедрить', relief='flat',
                            bg=T['btn_bg'], fg='#89b4fa',
                            font=(T['font_fam'], T['font_sz'] - 1), padx=6, pady=3,
                            command=_embed_cfg)
        b_embed.pack(side=tk.LEFT, padx=(0, 4))
        self._tw_buttons.append((b_embed, 'normal'))

        if model_name_cur.lower() in EMBEDDED_CONFIGS:
            b_unembed = tk.Button(btn_row, text='📌✕', relief='flat',
                                  bg=T['btn_bg'], fg='#f38ba8',
                                  font=(T['font_fam'], T['font_sz'] - 1), padx=4, pady=3,
                                  command=_unembed_cfg)
            b_unembed.pack(side=tk.LEFT, padx=(0, 4))
            self._tw_buttons.append((b_unembed, 'normal'))

        b_del = tk.Button(btn_row, text='🗑', relief='flat',
                          bg=T['btn_bg'], fg='#f38ba8',
                          font=(T['font_fam'], T['font_sz'] - 1), padx=6, pady=3,
                          command=_delete_cfg)
        b_del.pack(side=tk.LEFT, padx=(0, 4))
        self._tw_buttons.append((b_del, 'normal'))

        b_folder = tk.Button(btn_row, text='📁', relief='flat',
                             bg=T['btn_bg'], fg=T['btn_fg'],
                             font=(T['font_fam'], T['font_sz'] - 1), padx=6, pady=3,
                             command=_open_cfg_folder)
        b_folder.pack(side=tk.LEFT)
        self._tw_buttons.append((b_folder, 'normal'))

        # Статус: внедрён ли конфиг
        if model_name_cur.lower() in EMBEDDED_CONFIGS:
            embed_status = '📌 Внедрён в EXE — пересоберите для включения в .exe'
            embed_fg = '#a6e3a1'
        else:
            embed_status = ''
            embed_fg = T['fg_muted']

        tk.Label(self._mat_panel,
                 text=f'item_configs/{model_name_cur}.json',
                 bg=T['bg_side'], fg=T['fg_muted'],
                 font=(T['font_fam'], T['font_sz'] - 3),
                 wraplength=None).pack(padx=16, anchor='w', pady=(0, 2))

        embed_lbl = tk.Label(self._mat_panel,
                             text=embed_status,
                             bg=T['bg_side'], fg=embed_fg,
                             font=(T['font_fam'], T['font_sz'] - 3),
                             wraplength=None)
        embed_lbl.pack(padx=16, anchor='w', pady=(0, 8))

        self._bind_scroll(self._mat_panel)

        # v3.1.6: синхронизация состояния Auto-Tune кнопки
        if hasattr(self, '_tune_btn') and self._tune_btn is not None:
            try:
                if self._active_idx is not None and self._active_idx in self._models:
                    self._tune_btn.config(state='normal', fg='#cba6f7')
                else:
                    self._tune_btn.config(state='disabled', fg=T['fg_muted'])
            except Exception: pass

    def _recompute_mats(self, split=None, split2=None, swap=None, uv_limit=None, edge_mult=None,
                          splits=None):
        """Перестраивает mat_groups с новыми границами раздела и перерисовывает модель.

        Поддерживает 2 режима:
          - Legacy (n_mats=2): split + split2 (как раньше)
          - Generic (n_mats>=2): splits = [s1, s2, ..., s_{n-1}] — k-1 границ для k материалов.
            face[0:s1) → mat[1]
            face[s1:s2) → mat[0]
            face[s2:s3) → mat[1]
            ... (поочерёдно)

        Параметр splits имеет приоритет над split/split2.
        """
        raw = self._raw_model
        if raw is None or not self.current_file:
            return
        if swap is None:
            swap = self._cur_swap

        face_map  = raw['face_map']
        mat_list  = raw['mat_list']
        nf        = raw['nf']
        n_mats    = raw['n_mats']
        best_tris = raw['best_tris']
        pts_arr   = raw['pts_arr']
        uvs_arr   = raw['uvs_arr']
        face_mats = raw['face_mats']
        if uv_limit is None:
            uv_limit = raw.get('uv_limit', 1.5)
        if edge_mult is None:
            edge_mult = raw.get('edge_mult', 50.0)
        # v2.8: используем robust_limit вместо base_limit (защита от outlier'ов).
        # Если raw уже содержит robust_limit (новый парс), берём его; иначе fallback на base_limit.
        base_limit = raw['base_limit']
        robust_limit = raw.get('robust_limit') or base_limit
        # Для высоких edge_mult (>=500) пользователь хочет ВСЕ грани — используем max edge × 2.
        # (Раньше был min — это ОШИБКА, см. комментарий в parse_p3d.)
        if float(edge_mult) >= 500:
            try:
                all_lengths = []
                for tri in best_tris[:500]:
                    for i in range(3):
                        all_lengths.append(float(np.linalg.norm(
                            pts_arr[tri[i]] - pts_arr[tri[(i+1)%3]])))
                if all_lengths:
                    robust_limit = max(all_lengths) * 2.0
            except Exception:
                pass
        limit = robust_limit * float(edge_mult)

        # Собираем splits в единый список (n_mats-1 элементов)
        if splits is None:
            # Используем legacy split/split2
            if split is None:
                split = self._cur_split
            if split2 is None:
                split2 = self._cur_split2
            splits = [s for s in (split, split2) if s and s > 0]
            splits.sort()
        else:
            # Обновляем self._cur_split/2 для обратной совместимости
            if len(splits) >= 1:
                self._cur_split = splits[0]
            if len(splits) >= 2:
                self._cur_split2 = splits[1]
            self._cur_swap = swap

        def get_mi(fi):
            """Возвращает индекс материала для face fi (0..n_mats-1).

            Линейное разделение: face[0:s1)→mat[0], face[s1:s2)→mat[1], ..., face[sk:N)→mat[k].
            Без splits: всё → mat[0].
            Swap инвертирует mat_index.
            """
            if n_mats < 2 or not splits:
                return 0
            mi = 0
            for s in splits:
                if fi < s:
                    break
                mi += 1
            if swap:
                mi = (n_mats - 1) - mi
            return min(max(mi, 0), n_mats - 1)

        def is_good(tri):
            if not all(np.linalg.norm(pts_arr[tri[i]] - pts_arr[tri[(i+1)%3]]) < limit
                       for i in range(3)):
                return False
            if uvs_arr is not None:
                us = [float(uvs_arr[tri[i]][0]) for i in range(3)]
                vs = [float(uvs_arr[tri[i]][1]) for i in range(3)]
                if max(us) - min(us) > uv_limit or max(vs) - min(vs) > uv_limit:
                    return False
            return True

        # v3.2.0: VECTORIZED batch is_good
        def _is_good_vec(tri_arr):
            if tri_arr is None or len(tri_arr) == 0:
                return np.zeros(0, dtype=bool)
            p0 = pts_arr[tri_arr[:, 0]]
            p1 = pts_arr[tri_arr[:, 1]]
            p2 = pts_arr[tri_arr[:, 2]]
            d01 = np.linalg.norm(p1 - p0, axis=1)
            d12 = np.linalg.norm(p2 - p1, axis=1)
            d20 = np.linalg.norm(p0 - p2, axis=1)
            edge_ok = (d01 < limit) & (d12 < limit) & (d20 < limit)
            if uvs_arr is None:
                return edge_ok
            uv0 = uvs_arr[tri_arr[:, 0]]
            uv1 = uvs_arr[tri_arr[:, 1]]
            uv2 = uvs_arr[tri_arr[:, 2]]
            us = np.column_stack([uv0[:, 0], uv1[:, 0], uv2[:, 0]]).astype(float)
            vs = np.column_stack([uv0[:, 1], uv1[:, 1], uv2[:, 1]]).astype(float)
            u_ok = (us.max(axis=1) - us.min(axis=1)) <= uv_limit
            v_ok = (vs.max(axis=1) - vs.min(axis=1)) <= uv_limit
            return edge_ok & u_ok & v_ok

        # v3.2.0: VECTORIZED recompute (replaces Python loop with is_good)
        mat_groups_dict = {}
        all_tris = []
        covered_faces = set()

        try:
            best_tris_arr = np.asarray(best_tris) if best_tris else np.zeros((0, 3), dtype=int)
        except Exception:
            best_tris_arr = np.zeros((0, 3), dtype=int)

        ti_to_mi = []
        for fi, (t_start, t_end) in enumerate(face_map):
            mi = get_mi(fi)
            for ti in range(t_start, t_end):
                covered_faces.add(ti)
                if ti < len(best_tris):
                    ti_to_mi.append((ti, mi))

        if ti_to_mi and len(best_tris_arr) > 0:
            ti_arr = np.array([t for t, _ in ti_to_mi], dtype=int)
            mi_arr = np.array([m for _, m in ti_to_mi], dtype=int)
            cand_tris = best_tris_arr[ti_arr]
            good_mask = _is_good_vec(cand_tris)
            good_ti = ti_arr[good_mask]
            good_mi = mi_arr[good_mask]
            for ti, mi in zip(good_ti.tolist(), good_mi.tolist()):
                mat = mat_list[mi] if mat_list else ''
                if not mat:
                    mat = face_mats[ti] if ti < len(face_mats) else ''
                key = mat.lower().replace('\\', '/').strip()
                if key not in mat_groups_dict:
                    mat_groups_dict[key] = {'path': mat, 'tris': []}
                mat_groups_dict[key]['tris'].append(best_tris[ti])
                all_tris.append(best_tris[ti])

        # v2.8 fix: добавляем uncovered faces в default group
        if len(covered_faces) < nf:
            default_key = '__uncovered_default__'
            added = 0
            uncovered_ti = [ti for ti in range(nf) if ti not in covered_faces and ti < len(best_tris)]
            if uncovered_ti:
                cand_tris_unc = best_tris_arr[np.array(uncovered_ti, dtype=int)]
                good_mask_unc = _is_good_vec(cand_tris_unc)
                for idx, ti in enumerate(uncovered_ti):
                    if good_mask_unc[idx]:
                        mat_groups_dict.setdefault(default_key, {'path': '', 'tris': []})['tris'].append(best_tris[ti])
                        all_tris.append(best_tris[ti])
                        added += 1
            if added > 0:
                _dbg(f'recompute: {len(covered_faces)}/{nf} covered, added {added} uncovered')

        new_mat_groups = [(v['path'], v['tris'])
                          for v in mat_groups_dict.values() if v['tris']]

        # v2.8: логируем результат для отладки
        _dbg(f'recompute: uv={uv_limit} edge={edge_mult} robust={robust_limit:.4f} limit={limit:.2f} → {len(all_tris)}/{nf} tris, {len(new_mat_groups)} groups')
        dbg_cat('recompute', f'uv={uv_limit} edge={edge_mult} → {len(all_tris)}/{nf} tris, {len(new_mat_groups)} groups')

        self._cur_split    = split
        self._cur_split2   = split2
        self._cur_swap     = swap
        self._cur_uv_limit = uv_limit
        self._cur_edge_mult = edge_mult
        raw['uv_limit']    = uv_limit
        raw['edge_mult']   = edge_mult
        raw['limit']       = limit
        self.mat_groups   = new_mat_groups
        self.tris         = all_tris

        # v2.3: обновляем state активной модели (tex_data cache + mat_groups),
        # иначе _resolve_textures_for использует устаревший список.
        if self._active_idx is not None and self._active_idx in self._models:
            self._models[self._active_idx]['mat_groups'] = new_mat_groups
            self._models[self._active_idx]['tex_data'] = {}

        if USE_GL:
            self._status('Перестройка меша...', '#f9e2af')
            self.root.update_idletasks()
            self.viewport.load_mesh(raw['pts'], raw['nrms'], all_tris,
                                    raw['uvs'], new_mat_groups)
            self.root.update_idletasks()
            self._auto_tex(self.current_file, None, new_mat_groups)

        two_split_mode = (n_mats >= 2 and len(splits) >= 2)
        swap_s = ' своп' if swap else ''
        s2_s   = f'..{split2}' if two_split_mode else ''
        self._status(f'✔ [{split}{s2_s}]{swap_s}', '#a6e3a1')
        _dbg(f'_recompute_mats: s1={split} s2={split2} swap={swap} '
             f'two_split={two_split_mode} groups={len(new_mat_groups)} tris={len(all_tris)}')

    # ── Применение темы ──────────────────────────────────────────────────────

    def _full_repaint(self, old_theme):
        """Рекурсивно заменяет все цвета старой темы на новую во всех виджетах."""
        T = self._T()

        # Строим карту замены: old_color_hex → new_color_hex
        color_keys = ('bg_main', 'bg_side', 'bg_widget', 'bg_sep',
                      'fg_text', 'fg_label', 'fg_muted',
                      'btn_bg', 'btn_fg', 'accent_bg', 'accent_fg',
                      'accent_hov', 'btn_hover',
                      'c_ok', 'c_warn', 'c_err', 'gl_bg')
        color_map = {}
        for key in color_keys:
            old_val = old_theme.get(key, '').lower()
            new_val = T.get(key, '').lower()
            if old_val and old_val != new_val:
                color_map[old_val] = T.get(key, '')

        def _remap(color):
            """Возвращает новый цвет если старый в карте замены."""
            if not color:
                return None
            try:
                # tk может вернуть цвет в формате #rrrrggggbbbb — нормализуем
                if len(color) == 13 and color.startswith('#'):
                    r = int(color[1:5], 16) >> 8
                    g = int(color[5:9], 16) >> 8
                    b = int(color[9:13], 16) >> 8
                    color = f'#{r:02x}{g:02x}{b:02x}'
                c = color.lower()
                return color_map.get(c)
            except Exception:
                return None

        def _repaint_widget(w):
            try:
                wclass = w.winfo_class()
                if wclass in ('Frame', 'Canvas'):
                    try:
                        new = _remap(w.cget('bg'))
                        if new: w.configure(bg=new)
                    except Exception: pass

                elif wclass in ('Label', 'Message'):
                    try:
                        new = _remap(w.cget('bg'))
                        if new: w.configure(bg=new)
                    except Exception: pass
                    try:
                        new = _remap(w.cget('fg'))
                        if new: w.configure(fg=new)
                    except Exception: pass

                elif wclass == 'Button':
                    try:
                        new = _remap(w.cget('bg'))
                        if new: w.configure(bg=new)
                    except Exception: pass
                    try:
                        new = _remap(w.cget('fg'))
                        if new: w.configure(fg=new)
                    except Exception: pass
                    try:
                        new = _remap(w.cget('activebackground'))
                        if new: w.configure(activebackground=new)
                    except Exception: pass

                elif wclass == 'Checkbutton':
                    try:
                        new = _remap(w.cget('bg'))
                        if new: w.configure(bg=new, activebackground=new)
                    except Exception: pass
                    try:
                        new = _remap(w.cget('fg'))
                        if new: w.configure(fg=new, activeforeground=new)
                    except Exception: pass
                    try:
                        new = _remap(w.cget('selectcolor'))
                        if new: w.configure(selectcolor=new)
                    except Exception: pass

                elif wclass in ('Scale', 'Scrollbar'):
                    try:
                        new = _remap(w.cget('bg'))
                        if new: w.configure(bg=new)
                    except Exception: pass
                    try:
                        new = _remap(w.cget('troughcolor'))
                        if new: w.configure(troughcolor=new)
                    except Exception: pass

                elif wclass in ('Entry', 'Spinbox'):
                    try:
                        new = _remap(w.cget('bg'))
                        if new: w.configure(bg=new)
                    except Exception: pass
                    try:
                        new = _remap(w.cget('fg'))
                        if new: w.configure(fg=new)
                    except Exception: pass

            except Exception:
                pass

            # Рекурсивно обходим дочерние виджеты
            try:
                for child in w.winfo_children():
                    _repaint_widget(child)
            except Exception:
                pass

        # Обходим всё дерево виджетов
        _repaint_widget(self.root)

        # Дополнительно вызываем стандартный _apply_theme для шрифтов и спецвиджетов
        self._apply_theme()

        # Перекрашиваем иконки под новую тему
        T2 = self._T()
        for k, btn in self._icon_btns.items():
            try:
                if self._active_icon[0] == k:
                    btn.config(fg=T2['accent_bg'], bg=T2['bg_widget'])
                else:
                    btn.config(fg=T2['fg_muted'], bg=T2['bg_main'])
            except Exception:
                pass
        for k, outer in self._icon_btn_outers.items():
            try:
                outer.config(bg=T2['bg_widget'] if self._active_icon[0] == k else T2['bg_main'])
            except Exception:
                pass

        # Обновляем GL фон
        try:
            c = T['gl_bg'].lstrip('#')
            r, g, b = int(c[0:2],16)/255, int(c[2:4],16)/255, int(c[4:6],16)/255
            self.viewport.bg_color = (r, g, b)
        except Exception:
            pass

        # Обновляем кнопки оси proxy
        self._refresh_axis_btns()

    def _get_font(self, level: str, size_delta: int = 0):
        """Возвращает кортеж шрифта с учётом сохранённых стилей текста."""
        T = self.theme
        styles = self._app_settings.get('text_styles', {})
        defaults = _SETTINGS_DEFAULTS['text_styles']
        st = styles.get(level, defaults.get(level, {}))
        fam  = T.get('font_fam', 'Segoe UI')
        sz   = T.get('font_sz', 9) + size_delta
        mods = []
        if st.get('bold'):      mods.append('bold')
        if st.get('italic'):    mods.append('italic')
        if st.get('underline'): mods.append('underline')
        return (fam, sz, *mods) if mods else (fam, sz)

    def _apply_theme(self):
        T = self.theme
        fn       = self._get_font('body')
        fn_sm    = self._get_font('body', -1)
        fn_sm_b  = self._get_font('h3',   -1)
        fn_big_b = self._get_font('h1',   +5)
        relief = T.get('btn_relief', 'flat')

        self.root.configure(bg=T['bg_main'])
        try: self._side.configure(bg=T['bg_side'], width=T['side_w'])
        except Exception: pass
        try: self._vp_frame.configure(bg=T['gl_bg'])
        except Exception: pass

        # Рамки
        for w, key in self._tw_frames:
            try: w.configure(bg=T.get(key, T['bg_side']))
            except Exception: pass

        # Разделители
        for w in self._tw_seps:
            try: w.configure(bg=T['bg_sep'])
            except Exception: pass

        # Метки
        for w, bg_key, fg_key, role in self._tw_labels:
            try:
                f = fn_big_b if role == 'title' else (fn_sm_b if role == 'section' else fn_sm)
                w.configure(bg=T.get(bg_key, T['bg_side']),
                            fg=T.get(fg_key, T['fg_text']), font=f)
            except Exception: pass

        # Кнопки
        for w, kind in self._tw_buttons:
            try:
                bg  = T['accent_bg']  if kind == 'accent' else T['btn_bg']
                fg  = T['accent_fg']  if kind == 'accent' else T['btn_fg']
                hov = T['accent_hov'] if kind == 'accent' else T['btn_hover']
                w.configure(bg=bg, fg=fg, activebackground=hov, activeforeground=fg,
                            font=fn, relief=relief)
            except Exception: pass

        # Слайдеры
        for w in self._tw_scales:
            try: w.configure(bg=T['bg_side'], fg=T['fg_text'], troughcolor=T['bg_widget'])
            except Exception: pass

        # Особые виджеты
        try:
            self._title_lbl.configure(bg=T['bg_side'], fg=T['fg_text'], font=fn_big_b)
            self._ver_lbl.configure(bg=T['bg_side'], fg=T['fg_label'], font=fn)
            self.info.configure(bg=T['bg_side'], fg=T['fg_label'], font=fn_sm)
            self.status.configure(bg=T['bg_side'], font=fn_sm)
            self._hints_lbl.configure(bg=T['bg_side'], fg=T['fg_muted'], font=fn_sm)
            self._diff_ind.configure(bg=T['bg_widget'], font=fn_sm)
            self._nrm_ind.configure(bg=T['bg_widget'], font=fn_sm)
            self._ind_frame.configure(bg=T['bg_widget'])
        except Exception: pass

        # GL фон
        try:
            c = T['gl_bg'].lstrip('#')
            r, g, b = int(c[0:2],16)/255, int(c[2:4],16)/255, int(c[4:6],16)/255
            self.viewport.bg_color = (r, g, b)
        except Exception: pass

        try:
            self._toggle_btn.configure(bg=T['bg_main'], fg=T['fg_label'])
        except Exception:
            pass

    def _reorder_sections(self):
        """Перепаковать секции в новом порядке."""
        for f in self._section_frames.values():
            f.pack_forget()
        for name in self._section_order:
            if self._section_vis.get(name, True) and name in self._section_frames:
                self._section_frames[name].pack(fill=tk.X)

    def _reorder_icon_strip_widgets(self):
        """v0.1.23: live apply icon_strip — перепаковать btn_outer в новом порядке (НЕ destroy)."""
        order = self._app_settings.get('icon_strip_order',
                                       ['mods', 'mats', 'textures', 'file', 'proxy'])
        for k in ('mods', 'mats', 'textures', 'file', 'proxy'):
            if k not in order:
                order.append(k)
        for k in list(self._icon_btn_outers.keys()):
            try:
                self._icon_btn_outers[k].pack_forget()
            except Exception: pass
        for k in order:
            if k in self._icon_btn_outers:
                try:
                    self._icon_btn_outers[k].pack(fill=tk.X, pady=1)
                except Exception: pass
        self._icon_strip_order = order

    def _show_section(self, name, visible):
        self._section_vis[name] = visible
        self._reorder_sections()

    # ── Редактор оформления ──────────────────────────────────────────────────

    def _open_theme_editor(self):
        """Современное окно настроек: выбор темы + порядок секций."""
        if hasattr(self, '_settings_win') and self._settings_win and self._settings_win.winfo_exists():
            self._settings_win.lift()
            return

        T = self._T()
        win = tk.Toplevel(self.root)
        win.title('Настройки интерфейса')
        win.geometry('500x620')
        win.configure(bg=T['bg_main'])
        win.resizable(False, True)
        self._settings_win = win

        # ── Header ────────────────────────────────────────────────────────────
        hdr = tk.Frame(win, bg=T['bg_side'], height=48)
        hdr.pack(fill=tk.X)
        hdr.pack_propagate(False)
        tk.Label(hdr, text='Настройки интерфейса',
                 bg=T['bg_side'], fg=T['fg_text'],
                 font=(T['font_fam'], 13, 'bold')).pack(side=tk.LEFT, padx=20, pady=14)

        # ── Scrollable body ───────────────────────────────────────────────────
        _bc = [None]  # will hold body_canvas reference
        body_canvas = tk.Canvas(win, bg=T['bg_main'], bd=0, highlightthickness=0)
        _bc[0] = body_canvas
        body_sb = tk.Scrollbar(win, orient='vertical', command=body_canvas.yview,
                               width=12, bd=0, highlightthickness=0,
                               troughcolor=T['bg_widget'])
        body_sb.pack(side=tk.RIGHT, fill=tk.Y)
        body_canvas.pack(fill=tk.BOTH, expand=True)
        body_canvas.configure(yscrollcommand=body_sb.set)

        body = tk.Frame(body_canvas, bg=T['bg_main'])
        body_win = body_canvas.create_window((0, 0), window=body, anchor='nw')

        def _on_body_cfg(e):
            body_canvas.configure(scrollregion=body_canvas.bbox('all'))
        body.bind('<Configure>', _on_body_cfg)

        def _on_canvas_cfg(e):
            body_canvas.itemconfig(body_win, width=e.width)
        body_canvas.bind('<Configure>', _on_canvas_cfg)

        def _win_scroll(e):
            if _bc[0] and _bc[0].winfo_exists():
                _bc[0].yview_scroll(int(-e.delta/120), 'units')

        body_canvas.bind('<MouseWheel>', _win_scroll)
        body.bind('<MouseWheel>', _win_scroll)
        win.bind('<MouseWheel>', _win_scroll)

        win.bind('<Enter>', lambda e: win.bind_all('<MouseWheel>', _win_scroll))
        win.bind('<Leave>', lambda e: win.unbind_all('<MouseWheel>'))

        def _bind_all_scroll(w):
            try:
                w.bind('<MouseWheel>', _win_scroll)
                for c in w.winfo_children():
                    _bind_all_scroll(c)
            except Exception: pass

        win.after(100, lambda: _bind_all_scroll(win))

        def section_header(parent, text):
            f = tk.Frame(parent, bg=T['bg_main'])
            f.pack(fill=tk.X, padx=20, pady=(18, 6))
            tk.Label(f, text=text.upper(),
                     bg=T['bg_main'], fg=T['accent_bg'],
                     font=(T['font_fam'], 8, 'bold')).pack(side=tk.LEFT)
            tk.Frame(f, bg=T['bg_sep'], height=1).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(10, 0), pady=7)

        def card(parent):
            f = tk.Frame(parent, bg=T['bg_side'],
                         highlightbackground=T['bg_sep'],
                         highlightthickness=1)
            f.pack(fill=tk.X, padx=20, pady=3)
            return f

        # ═══════════════════════════════════════════
        # SECTION 1: ТЕМА
        # ═══════════════════════════════════════════
        section_header(body, 'Цветовая тема')

        themes_card = card(body)
        themes_inner = tk.Frame(themes_card, bg=T['bg_side'])
        themes_inner.pack(fill=tk.X, padx=14, pady=14)

        current_theme_name = self._app_settings.get('theme_name', 'Catppuccin')
        selected_theme_var = tk.StringVar(value=current_theme_name)

        swatch_frames = {}
        col = 0
        row = 0
        SWATCHES_PER_ROW = 4

        for theme_name, theme_data in PRESET_THEMES.items():
            accent = theme_data.get('accent_bg', '#888888')
            bg = theme_data.get('bg_side', '#1e1e2e')
            fg = theme_data.get('fg_text', '#cdd6f4')

            swatch_outer = tk.Frame(themes_inner, bg=T['bg_side'], padx=2, pady=2)
            swatch_outer.grid(row=row, column=col, padx=4, pady=4, sticky='w')
            swatch_frames[theme_name] = swatch_outer

            swatch = tk.Frame(swatch_outer, bg=bg, width=88, height=58,
                              highlightbackground=T['bg_sep'], highlightthickness=1,
                              cursor='hand2')
            swatch.pack()
            swatch.pack_propagate(False)

            # Mini preview inside swatch
            tk.Frame(swatch, bg=theme_data.get('bg_main', bg), height=14).pack(fill=tk.X)
            accent_bar = tk.Frame(swatch, bg=accent, height=3)
            accent_bar.pack(fill=tk.X)
            tk.Label(swatch, text=theme_name, bg=bg, fg=fg,
                     font=(T['font_fam'], 7), wraplength=80).pack(expand=True)

            def _select_theme(name=theme_name, outer=swatch_outer):
                selected_theme_var.set(name)
                # Reset all borders
                for n, f in swatch_frames.items():
                    f.configure(bg=T['bg_side'])
                    for child in f.winfo_children():
                        if isinstance(child, tk.Frame):
                            try:
                                child.configure(highlightbackground=T['bg_sep'], highlightthickness=1)
                            except Exception:
                                pass
                # Highlight selected
                outer.configure(bg=T['accent_bg'])

            swatch.bind('<Button-1>', lambda e, n=theme_name, o=swatch_outer: _select_theme(n, o))
            for child in swatch.winfo_children():
                child.bind('<Button-1>', lambda e, n=theme_name, o=swatch_outer: _select_theme(n, o))

            # Mark current selection
            if theme_name == current_theme_name:
                swatch_outer.configure(bg=T['accent_bg'])

            col += 1
            if col >= SWATCHES_PER_ROW:
                col = 0
                row += 1

        # ═══════════════════════════════════════════
        # v0.1.23: SECTION 2: ПОРЯДОК ИКОНОК SIDEBAR (drag + Up/Down, live apply)
        # ═══════════════════════════════════════════
        section_header(body, 'Порядок иконок')

        order_hint = tk.Label(body,
            text='Перетащи иконки или используй ↑↓ чтобы изменить порядок в боковой панели',
            bg=T['bg_main'], fg=T['fg_label'],
            font=(T['font_fam'], 8), justify=tk.LEFT)
        order_hint.pack(anchor='w', padx=20, pady=(0, 6))

        order_card = card(body)
        order_inner = tk.Frame(order_card, bg=T['bg_side'])
        order_inner.pack(fill=tk.X, padx=8, pady=8)

        SECTION_DISPLAY_NAMES = {
            'mods':     '⊞ Моды',
            'mats':     '◈ Материалы',
            'textures': '▦ Текстуры',
            'file':     '⊡ Файл',
            'proxy':    '⊕ Прокси',
        }
        DEFAULT_ICON_KEYS = ['mods', 'mats', 'textures', 'file', 'proxy']

        # Current order from settings (icon_strip_order — реальные ключи icon_strip)
        current_order = list(self._app_settings.get('icon_strip_order', DEFAULT_ICON_KEYS))
        # фильтруем только валидные
        current_order = [k for k in current_order if k in SECTION_DISPLAY_NAMES]
        # добавляем недостающие
        for k in DEFAULT_ICON_KEYS:
            if k not in current_order:
                current_order.append(k)

        order_state = {'items': list(current_order), 'drag_idx': None, 'rows': {}}

        def rebuild_order_list():
            for w in order_inner.winfo_children():
                w.destroy()
            order_state['rows'] = {}

            for i, sec_key in enumerate(order_state['items']):
                display = SECTION_DISPLAY_NAMES.get(sec_key, sec_key)

                row_frame = tk.Frame(order_inner, bg=T['bg_widget'],
                                     highlightbackground=T['bg_sep'],
                                     highlightthickness=1)
                row_frame.pack(fill=tk.X, pady=2, padx=4)
                order_state['rows'][i] = row_frame

                # Drag handle
                handle = tk.Label(row_frame, text='≡',
                                   bg=T['bg_widget'], fg=T['fg_muted'],
                                   font=(T['font_fam'], 12),
                                   cursor='fleur', padx=10, pady=6)
                handle.pack(side=tk.LEFT)

                # Section name — v0.1.23: anchor='w' для выравнивания по левому краю
                tk.Label(row_frame, text=display,
                         bg=T['bg_widget'], fg=T['fg_text'],
                         font=(T['font_fam'], 9),
                         padx=12, pady=6, anchor='w').pack(side=tk.LEFT, fill=tk.X, expand=True)

                # Up/Down arrows
                btn_frame = tk.Frame(row_frame, bg=T['bg_widget'])
                btn_frame.pack(side=tk.RIGHT, padx=6)

                def make_up(idx=i):
                    def do_up():
                        if idx > 0:
                            lst = order_state['items']
                            lst[idx-1], lst[idx] = lst[idx], lst[idx-1]
                            rebuild_order_list()
                    return do_up

                def make_dn(idx=i):
                    def do_dn():
                        lst = order_state['items']
                        if idx < len(lst)-1:
                            lst[idx], lst[idx+1] = lst[idx+1], lst[idx]
                            rebuild_order_list()
                    return do_dn

                up_btn = tk.Button(btn_frame, text='↑', relief='flat',
                                   bg=T['bg_widget'], fg=T['fg_label'],
                                   font=(T['font_fam'], 9),
                                   padx=4, pady=2,
                                   command=make_up(i))
                up_btn.pack(side=tk.LEFT)

                dn_btn = tk.Button(btn_frame, text='↓', relief='flat',
                                   bg=T['bg_widget'], fg=T['fg_label'],
                                   font=(T['font_fam'], 9),
                                   padx=4, pady=2,
                                   command=make_dn(i))
                dn_btn.pack(side=tk.LEFT)

                # Drag events on handle and row
                drag_state = {'start_y': None, 'my_idx': i}

                def on_drag_start(e, idx=i):
                    order_state['drag_idx'] = idx

                def on_drag_motion(e, row=row_frame, idx=i):
                    # Find which row the cursor is over
                    root_y = row.winfo_rooty()
                    cur_y = e.widget.winfo_rooty() + e.y
                    target = order_state['drag_idx']
                    lst = order_state['items']
                    # Determine direction
                    rows_list = list(order_state['rows'].values())
                    for j, rf in enumerate(rows_list):
                        ry = rf.winfo_rooty()
                        rh = rf.winfo_height()
                        if ry <= cur_y <= ry + rh and j != target:
                            lst[target], lst[j] = lst[j], lst[target]
                            order_state['drag_idx'] = j
                            rebuild_order_list()
                            break

                def on_drag_end(e):
                    order_state['drag_idx'] = None

                handle.bind('<ButtonPress-1>', on_drag_start)
                handle.bind('<B1-Motion>', on_drag_motion)
                handle.bind('<ButtonRelease-1>', on_drag_end)

        rebuild_order_list()

        # v0.1.23: старая секция "Иконки сайдбара" (Up/Down) удалена — объединена
# с секцией "Порядок иконок" выше (которая использует drag + Up/Down + live apply)

        # ═══════════════════════════════════════════
        # SECTION 3: ЦВЕТА ЭЛЕМЕНТОВ
        # ═══════════════════════════════════════════
        section_header(body, 'Цвета элементов')

        colors_card = card(body)
        colors_inner = tk.Frame(colors_card, bg=T['bg_side'])
        colors_inner.pack(fill=tk.X, padx=14, pady=10)

        # Словарь: ключ темы → отображаемое название
        COLOR_LABELS = [
            ('bg_main',   'Фон окна'),
            ('bg_side',   'Фон панели'),
            ('bg_widget', 'Фон виджетов'),
            ('bg_sep',    'Разделители'),
            ('fg_text',   'Текст основной'),
            ('fg_label',  'Текст заголовков'),
            ('fg_muted',  'Текст приглушённый'),
            ('accent_bg', 'Акцент (фон)'),
            ('accent_fg', 'Акцент (текст)'),
            ('btn_bg',    'Кнопки (фон)'),
            ('btn_fg',    'Кнопки (текст)'),
            ('gl_bg',     'Фон вьюпорта'),
            ('c_ok',      'Статус: OK'),
            ('c_warn',    'Статус: Warning'),
            ('c_err',     'Статус: Error'),
        ]

        # Хранилище текущих цветов для редактирования
        saved_custom = self._app_settings.get('custom_colors', {})
        custom_colors = {k: saved_custom.get(k, T.get(k, '#000000')) for k, _ in COLOR_LABELS}

        def _make_color_row(parent, key, label):
            row = tk.Frame(parent, bg=T['bg_side'])
            row.pack(fill=tk.X, pady=2)
            tk.Label(row, text=label, bg=T['bg_side'], fg=T['fg_text'],
                     font=(T['font_fam'], 8), width=22, anchor='w').pack(side=tk.LEFT)
            swatch = tk.Frame(row, bg=custom_colors[key],
                              width=28, height=18,
                              highlightbackground=T['bg_sep'],
                              highlightthickness=1,
                              cursor='hand2')
            swatch.pack(side=tk.LEFT, padx=(4, 0))
            swatch.pack_propagate(False)
            hex_var = tk.StringVar(value=custom_colors[key])
            hex_lbl = tk.Label(row, textvariable=hex_var,
                               bg=T['bg_side'], fg=T['fg_muted'],
                               font=('Courier New', 8))
            hex_lbl.pack(side=tk.LEFT, padx=6)

            def _pick(k=key, sw=swatch, hv=hex_var):
                from tkinter import colorchooser
                result = colorchooser.askcolor(color=custom_colors[k],
                                               title=f'Выбор цвета — {k}',
                                               parent=win)
                if result and result[1]:
                    chosen = result[1].lower()
                    custom_colors[k] = chosen
                    sw.configure(bg=chosen)
                    hv.set(chosen)

            swatch.bind('<Button-1>', lambda e, fn=_pick: fn())

        for key, label in COLOR_LABELS:
            _make_color_row(colors_inner, key, label)

        reset_btn = tk.Button(colors_inner, text='↩ Сбросить к пресету',
                              relief='flat', bg=T['bg_widget'], fg=T['fg_label'],
                              font=(T['font_fam'], 8), padx=8, pady=3,
                              command=lambda: [custom_colors.update(
                                  {k: PRESET_THEMES.get(selected_theme_var.get(), T).get(k, T.get(k,'#000000'))
                                   for k, _ in COLOR_LABELS})])
        reset_btn.pack(anchor='e', pady=(6, 0))

        # ═══════════════════════════════════════════
        # SECTION 4: ГРАДИЕНТ ПАНЕЛИ
        # ═══════════════════════════════════════════
        section_header(body, 'Градиент панели')

        grad_card = card(body)
        grad_inner = tk.Frame(grad_card, bg=T['bg_side'])
        grad_inner.pack(fill=tk.X, padx=14, pady=10)

        grad_enabled = tk.BooleanVar(value=self._app_settings.get('gradient_enabled', False))
        grad_color2_val = [self._app_settings.get('gradient_color2', T['bg_main'])]

        grad_toggle_row = tk.Frame(grad_inner, bg=T['bg_side'])
        grad_toggle_row.pack(fill=tk.X)
        tk.Label(grad_toggle_row, text='Включить градиент панели',
                 bg=T['bg_side'], fg=T['fg_text'],
                 font=(T['font_fam'], 9)).pack(side=tk.LEFT)
        tk.Checkbutton(grad_toggle_row, variable=grad_enabled,
                       bg=T['bg_side'], fg=T['fg_text'],
                       selectcolor=T['bg_widget'],
                       activebackground=T['bg_side']).pack(side=tk.RIGHT)

        grad_color_row = tk.Frame(grad_inner, bg=T['bg_side'])
        grad_color_row.pack(fill=tk.X, pady=(6, 0))
        tk.Label(grad_color_row, text='Второй цвет градиента',
                 bg=T['bg_side'], fg=T['fg_text'],
                 font=(T['font_fam'], 8), width=22, anchor='w').pack(side=tk.LEFT)
        grad_swatch = tk.Frame(grad_color_row, bg=grad_color2_val[0],
                               width=28, height=18,
                               highlightbackground=T['bg_sep'], highlightthickness=1,
                               cursor='hand2')
        grad_swatch.pack(side=tk.LEFT, padx=(4,0))
        grad_swatch.pack_propagate(False)
        grad_hex_var = tk.StringVar(value=grad_color2_val[0])
        tk.Label(grad_color_row, textvariable=grad_hex_var,
                 bg=T['bg_side'], fg=T['fg_muted'],
                 font=('Courier New', 8)).pack(side=tk.LEFT, padx=6)

        def _pick_grad_color():
            from tkinter import colorchooser
            r = colorchooser.askcolor(color=grad_color2_val[0],
                                       title='Второй цвет градиента', parent=win)
            if r and r[1]:
                grad_color2_val[0] = r[1].lower()
                grad_swatch.configure(bg=grad_color2_val[0])
                grad_hex_var.set(grad_color2_val[0])

        grad_swatch.bind('<Button-1>', lambda e: _pick_grad_color())

        # ═══════════════════════════════════════════
        # SECTION 5: ДОПОЛНИТЕЛЬНО
        # ═══════════════════════════════════════════
        section_header(body, 'Интерфейс')

        extra_card = card(body)
        extra_inner = tk.Frame(extra_card, bg=T['bg_side'])
        extra_inner.pack(fill=tk.X, padx=14, pady=10)

        # Font size
        font_row = tk.Frame(extra_inner, bg=T['bg_side'])
        font_row.pack(fill=tk.X, pady=4)
        tk.Label(font_row, text='Размер шрифта', bg=T['bg_side'], fg=T['fg_text'],
                 font=(T['font_fam'], 9)).pack(side=tk.LEFT)
        font_var = tk.IntVar(value=self._app_settings.get('font_sz', T.get('font_sz', 9)))
        font_spin = tk.Spinbox(font_row, from_=7, to=14, textvariable=font_var,
                               width=4, bg=T['bg_widget'], fg=T['fg_text'],
                               buttonbackground=T['bg_sep'],
                               highlightthickness=0, relief='flat',
                               font=(T['font_fam'], 9))
        font_spin.pack(side=tk.RIGHT)

        # Шрифт
        font_fam_row = tk.Frame(extra_inner, bg=T['bg_side'])
        font_fam_row.pack(fill=tk.X, pady=4)
        tk.Label(font_fam_row, text='Шрифт интерфейса',
                 bg=T['bg_side'], fg=T['fg_text'],
                 font=(T['font_fam'], 9)).pack(side=tk.LEFT)

        FONT_OPTIONS = ['Segoe UI', 'Arial', 'Calibri', 'Consolas',
                        'Courier New', 'Tahoma', 'Verdana', 'Trebuchet MS']
        font_fam_var = tk.StringVar(value=self._app_settings.get('font_fam', T.get('font_fam', 'Segoe UI')))

        font_combo_frame = tk.Frame(font_fam_row, bg=T['bg_side'])
        font_combo_frame.pack(side=tk.RIGHT)

        try:
            import tkinter.ttk as _ttk
            font_combo = _ttk.Combobox(font_combo_frame, textvariable=font_fam_var,
                                        values=FONT_OPTIONS, width=14, state='readonly')
            font_combo.pack()
        except Exception:
            tk.Entry(font_combo_frame, textvariable=font_fam_var,
                     bg=T['bg_widget'], fg=T['fg_text'], width=14,
                     highlightthickness=0, relief='flat').pack()

        # Ширина боковой панели
        sw_row = tk.Frame(extra_inner, bg=T['bg_side'])
        sw_row.pack(fill=tk.X, pady=4)
        tk.Label(sw_row, text='Ширина панели (px)', bg=T['bg_side'], fg=T['fg_text'],
                 font=(T['font_fam'], 9)).pack(side=tk.LEFT)
        sw_var = tk.IntVar(value=self._app_settings.get('sidebar_width', 350))
        sw_spin = tk.Spinbox(sw_row, from_=160, to=400, textvariable=sw_var,
                             width=5, bg=T['bg_widget'], fg=T['fg_text'],
                             buttonbackground=T['bg_sep'],
                             highlightthickness=0, relief='flat',
                             font=(T['font_fam'], 9))
        sw_spin.pack(side=tk.RIGHT)

        # ═══════════════════════════════════════════
        # СТИЛИ ТЕКСТА
        # ═══════════════════════════════════════════
        tk.Frame(body, bg=T['bg_sep'], height=1).pack(fill=tk.X, padx=20, pady=(16, 4))
        tk.Label(body, text='Стили текста', bg=T['bg_main'], fg=T['fg_text'],
                 font=(T['font_fam'], 10, 'bold')).pack(anchor='w', padx=20, pady=(4, 2))
        tk.Label(body, text='Заголовок 1 — названия секций (крупный)\nЗаголовок 2 — подзаголовки\nЗаголовок 3 — метки полей\nОбычный — основной текст',
                 bg=T['bg_main'], fg=T['fg_muted'],
                 font=(T['font_fam'], 8), justify='left').pack(anchor='w', padx=20, pady=(0, 6))

        saved_ts = self._app_settings.get('text_styles', _SETTINGS_DEFAULTS['text_styles'])
        text_style_vars = {}  # level → {'bold': BooleanVar, 'italic': BooleanVar, 'underline': BooleanVar}

        ts_frame = tk.Frame(body, bg=T['bg_main'])
        ts_frame.pack(fill=tk.X, padx=20, pady=(0, 8))

        _level_labels = [('h1', 'Заголовок 1'), ('h2', 'Заголовок 2'),
                         ('h3', 'Заголовок 3'), ('body', 'Обычный')]
        _style_labels = [('bold', 'Ж'), ('italic', 'К'), ('underline', 'П')]

        # Заголовок таблицы
        hdr_row = tk.Frame(ts_frame, bg=T['bg_main'])
        hdr_row.pack(fill=tk.X)
        tk.Label(hdr_row, text='', bg=T['bg_main'], width=14).pack(side=tk.LEFT)
        for _, sl in _style_labels:
            tk.Label(hdr_row, text=sl, bg=T['bg_main'], fg=T['fg_label'],
                     font=(T['font_fam'], 9, 'bold'), width=4).pack(side=tk.LEFT)

        for level, label in _level_labels:
            row_f = tk.Frame(ts_frame, bg=T['bg_main'])
            row_f.pack(fill=tk.X, pady=1)
            tk.Label(row_f, text=label, bg=T['bg_main'], fg=T['fg_text'],
                     font=(T['font_fam'], 9), width=14, anchor='w').pack(side=tk.LEFT)
            st_defaults = _SETTINGS_DEFAULTS['text_styles'][level]
            st_saved    = saved_ts.get(level, st_defaults)
            vars_for_level = {}
            for key_s, _ in _style_labels:
                var = tk.BooleanVar(value=st_saved.get(key_s, st_defaults.get(key_s, False)))
                vars_for_level[key_s] = var
                tk.Checkbutton(row_f, variable=var, bg=T['bg_main'],
                               selectcolor=T['bg_widget'],
                               activebackground=T['bg_main'],
                               relief='flat', bd=0).pack(side=tk.LEFT, padx=4)
            text_style_vars[level] = vars_for_level

        # ═══════════════════════════════════════════
        # ═══════════════════════════════════════════
        # DEV TOOLS (v3.1.9) — кнопка в Настройки → _open_dev_tools()
        # ═══════════════════════════════════════════
        tk.Frame(body, bg=T['bg_sep'], height=1).pack(fill=tk.X, padx=20, pady=(16, 0))

        dev_tools_card = card(body)
        dev_tools_inner = tk.Frame(dev_tools_card, bg=T['bg_side'])
        dev_tools_inner.pack(fill=tk.X, padx=14, pady=10)

        dev_tools_hdr = tk.Label(dev_tools_inner, text='🛠 ИНСТРУМЕНТЫ РАЗРАБОТЧИКА',
                                  bg=T['bg_side'], fg=T['fg_label'],
                                  font=(T['font_fam'], 10, 'bold'))
        dev_tools_hdr.pack(anchor='w')

        dev_tools_hint = tk.Label(dev_tools_inner,
            text='Редактирование UI текстов, dev-mode, advanced diagnostics',
            bg=T['bg_side'], fg=T['fg_muted'],
            font=(T['font_fam'], 8), justify=tk.LEFT, wraplength=480)
        dev_tools_hint.pack(anchor='w', pady=(2, 6))

        tk.Button(dev_tools_inner, text='🛠  Открыть инструменты разработчика',
                  relief='flat', bg=T['btn_bg'], fg=T['btn_fg'],
                  font=(T['font_fam'], 10), padx=14, pady=6,
                  command=self._open_dev_tools).pack(fill=tk.X, pady=(2, 4))

        # APPLY BUTTON
        # ═══════════════════════════════════════════
        tk.Frame(body, bg=T['bg_sep'], height=1).pack(fill=tk.X, padx=20, pady=(16, 0))

        btn_row = tk.Frame(body, bg=T['bg_main'])
        btn_row.pack(fill=tk.X, padx=20, pady=12)

        def apply_settings():
            old_theme = self._T().copy()   # сохраняем старую тему ДО смены
            name = selected_theme_var.get()
            new_theme = PRESET_THEMES.get(name, DEFAULT_THEME).copy()
            # Apply font size override
            new_theme['font_sz'] = font_var.get()
            # Применяем кастомные цвета (если есть изменения)
            for k, v in custom_colors.items():
                new_theme[k] = v
            new_theme['font_fam'] = font_fam_var.get()
            # Градиент
            self._app_settings['gradient_enabled'] = grad_enabled.get()
            self._app_settings['gradient_color2'] = grad_color2_val[0]
            if grad_enabled.get():
                self._apply_sidebar_gradient(new_theme['bg_side'], grad_color2_val[0])
            else:
                self._clear_sidebar_gradient()
            self.theme = new_theme
            self._app_settings['theme_name'] = name
            # v0.1.23: используем icon_strip_order (тот же order_state) для icon_strip
            self._app_settings['icon_strip_order'] = list(order_state['items'])
            self._app_settings['custom_colors'] = dict(custom_colors)
            self._app_settings['font_fam'] = font_fam_var.get()
            self._app_settings['font_sz'] = font_var.get()
            # v0.1.23: live apply — перепаковать icon_btns в новом порядке (НЕ destroy)
            try:
                self._reorder_icon_strip_widgets()
            except Exception: pass
            # Сохраняем стили текста
            new_ts = {}
            for lvl, vars_d in text_style_vars.items():
                new_ts[lvl] = {k: v.get() for k, v in vars_d.items()}
            self._app_settings['text_styles'] = new_ts
            try:
                self._app_settings['sidebar_width'] = sw_var.get()
                self._side_outer.configure(width=sw_var.get())
            except Exception:
                pass
            _save_app_settings(self._app_settings)
            self._full_repaint(old_theme)   # рекурсивная замена цветов
            win.destroy()

        def cancel():
            win.destroy()

        tk.Button(btn_row, text='Применить', relief='flat',
                  bg=T['accent_bg'], fg=T['accent_fg'],
                  font=(T['font_fam'], 10, 'bold'),
                  padx=20, pady=8,
                  command=apply_settings).pack(side=tk.RIGHT, padx=(6, 0))

        tk.Button(btn_row, text='Отмена', relief='flat',
                  bg=T['bg_widget'], fg=T['fg_text'],
                  font=(T['font_fam'], 9),
                  padx=16, pady=8,
                  command=cancel).pack(side=tk.RIGHT)

    def _apply_sidebar_gradient(self, color1, color2):
        """Рисует вертикальный градиент на фоне боковой панели."""
        # Сохраняем параметры для перерисовки при изменении размера
        self._grad_colors = (color1, color2)
        self._app_settings['gradient_enabled'] = True
        self._app_settings['gradient_color2'] = color2
        self._draw_gradient_image()

        # Биндим resize только один раз
        if not getattr(self, '_grad_resize_bound', False):
            self._side_canvas.bind('<Configure>', self._on_grad_resize, '+')
            self._grad_resize_bound = True

    def _draw_gradient_image(self):
        """Фактически рисует градиент в canvas (вызывается при создании и resize)."""
        try:
            from PIL import Image, ImageTk
            color1, color2 = getattr(self, '_grad_colors', ('#000000', '#000000'))
            h = max(self._side_canvas.winfo_height(), 800)
            w = max(self._side_canvas.winfo_width(), 20)
            img = Image.new('RGB', (w, h))
            px = img.load()
            r1,g1,b1 = int(color1[1:3],16), int(color1[3:5],16), int(color1[5:7],16)
            r2,g2,b2 = int(color2[1:3],16), int(color2[3:5],16), int(color2[5:7],16)
            for y in range(h):
                t = y / max(h-1, 1)
                row = (int(r1+(r2-r1)*t), int(g1+(g2-g1)*t), int(b1+(b2-b1)*t))
                for x in range(w):
                    px[x, y] = row
            photo = ImageTk.PhotoImage(img)
            self._side_canvas.delete('gradient')
            self._side_canvas.create_image(0, 0, image=photo, anchor='nw', tags='gradient')
            self._side_canvas.tag_lower('gradient')
            self._side_canvas._grad_photo = photo
        except Exception:
            pass

    def _on_grad_resize(self, event=None):
        if getattr(self, '_grad_colors', None) and self._app_settings.get('gradient_enabled'):
            self._draw_gradient_image()

    def _clear_sidebar_gradient(self):
        """Убирает градиент с боковой панели."""
        try:
            self._side_canvas.delete('gradient')
            del self._side_canvas._grad_photo
        except Exception:
            pass
        self._app_settings['gradient_enabled'] = False

    def _repaint_ui(self):
        """Перекрашивает все виджеты под новую тему без перезапуска."""
        T = self._T()
        self.root.configure(bg=T['bg_main'])
        for w, key in self._tw_frames:
            try: w.configure(bg=T[key])
            except Exception: pass
        for w, bg_key, fg_key, role in self._tw_labels:
            try: w.configure(bg=T[bg_key], fg=T[fg_key])
            except Exception: pass
        for w, kind in self._tw_buttons:
            try:
                if kind == 'accent':
                    w.configure(bg=T['accent_bg'], fg=T['accent_fg'])
                else:
                    w.configure(bg=T['btn_bg'], fg=T['btn_fg'])
            except Exception: pass
        for w in self._tw_scales:
            try: w.configure(bg=T['bg_side'], troughcolor=T['bg_widget'],
                             fg=T['fg_text'], activebackground=T['accent_bg'])
            except Exception: pass
        # Update side canvas + scrollbar color
        try:
            self._side_canvas.configure(bg=T['bg_side'])
            # v3.1.8: _side_scroll убран
        except Exception: pass

    # ── Drag & Drop ─────────────────────────────────────────────────────────

    def _on_drop(self, event):
        path = event.data.strip().strip('{}')
        ext  = os.path.splitext(path)[1].lower()
        if   ext == '.p3d':   self._load_p3d(path)
        elif ext == '.paa':
            n = os.path.basename(path).lower()
            if '_nohq' in n or '_nrm' in n: self._load_nrm(path)
            else:                            self._load_paa(path)
        elif ext == '.rvmat': self._load_rvmat(path)
        else: messagebox.showerror('Ошибка', 'Поддерживается: .p3d  .paa  .rvmat')

    # ── Кнопки ──────────────────────────────────────────────────────────────

    # ── Blender интеграция ────────────────────────────────────────────────────

    def _get_blender_exe(self):
        """Берёт путь к Blender из настроек или ищет автоматически."""
        saved = self._app_settings.get('blender_path', '')
        if saved and os.path.exists(saved):
            return saved
        found = _find_blender_exe()
        if found:
            self._app_settings['blender_path'] = found
            _save_app_settings(self._app_settings)
        return found

    def _export_to_blender(self, p3d_paths):
        """
        Экспортирует список p3d файлов в OBJ+PNG и передаёт в Blender.
        Запускается в фоновом потоке.
        """
        if not p3d_paths:
            self.root.after(0, lambda: self._status('Нет моделей для экспорта', '#f38ba8'))
            return

        blender_exe = self._get_blender_exe()
        if not blender_exe:
            self.root.after(0, lambda: self._status('Blender не найден. Укажите путь в настройках.', '#f38ba8'))
            return

        export_dir = os.path.join(os.environ.get('TEMP', ''), 'p3d_viewer_export')
        os.makedirs(export_dir, exist_ok=True)
        queue_file = os.path.join(os.environ.get('TEMP', ''), 'p3d_blender_queue.txt')

        exported_objs = []

        for p3d_path in p3d_paths:
            try:
                model_name = os.path.splitext(os.path.basename(p3d_path))[0]
                norm_path  = os.path.normcase(os.path.abspath(p3d_path))

                # Берём уже загруженную модель с правильным сплитом если есть
                pts = uvs = nrms = tris = mat_groups = tex_hint = None
                if (self.current_file and
                        os.path.normcase(os.path.abspath(self.current_file)) == norm_path):
                    pts, uvs, nrms = self.pts, self.uvs, self.nrms
                    tris, mat_groups = self.tris, self.mat_groups
                    tex_hint = (self._raw_model or {}).get('tex_hint', '')
                else:
                    for st in self._models.values():
                        if (st.get('status') == 'ready' and st.get('path') and
                                os.path.normcase(os.path.abspath(st['path'])) == norm_path):
                            pts, uvs, nrms = st['pts'], st['uvs'], st['nrms']
                            tris, mat_groups = st['tris'], st['mat_groups']
                            tex_hint = st.get('tex_hint', '')
                            break

                if pts is None:
                    # Не загружена — парсим с учётом item_config
                    cfg = _load_item_config(model_name)
                    uv_lim  = cfg.get('uv_threshold',  1.5) if cfg else 1.5
                    edge_m  = cfg.get('edge_threshold', 50)  if cfg else 50
                    pts, uvs, nrms, tris, tex_hint, mat_groups, _ = parse_p3d(
                        p3d_path, uv_limit=uv_lim, edge_mult=edge_m)

                # Ищем текстуру для каждой группы материалов
                tex_pngs = {}
                try:
                    from PIL import Image as _PILImage
                    paa_index   = self._build_paa_index(p3d_path)
                    rvmat_index = self._build_rvmat_index(p3d_path)
                    for gi, (mat_path, _) in enumerate(mat_groups):
                        diff, _ = self._resolve_group_tex(
                            paa_index, rvmat_index, p3d_path, mat_path)
                        if diff and os.path.exists(diff):
                            rgba = read_paa_cached(diff)
                            if rgba is not None:
                                png_name = (os.path.splitext(
                                    os.path.basename(diff))[0] + '.png')
                                png_path = os.path.join(export_dir, png_name)
                                _PILImage.fromarray(rgba).save(png_path)
                                tex_pngs[gi] = png_path
                except Exception:
                    pass

                obj_path = os.path.join(export_dir, model_name + '.obj')
                export_obj_multi(obj_path, pts, uvs, nrms, mat_groups, tex_pngs)
                exported_objs.append(obj_path)

                # Export proxy data as JSON if available
                proxies = []
                try:
                    if (self.current_file and
                            os.path.normcase(os.path.abspath(self.current_file)) == norm_path):
                        proxies = (self._raw_model or {}).get('proxies', [])
                    else:
                        for st in self._models.values():
                            if (st.get('status') == 'ready' and st.get('path') and
                                    os.path.normcase(os.path.abspath(st['path'])) == norm_path):
                                proxies = st.get('raw', {}).get('proxies', [])
                                break
                    if proxies:
                        prx_path = os.path.join(export_dir, model_name + '_proxies.json')
                        with open(prx_path, 'w', encoding='utf-8') as f:
                            json.dump(proxies, f)
                except Exception:
                    pass
            except Exception as e:
                _write_log('blender_export_error.txt', [str(p3d_path), str(e)])

        if not exported_objs:
            self.root.after(0, lambda: self._status('Ошибка экспорта моделей', '#f38ba8'))
            return

        if _is_blender_running():
            with open(queue_file, 'a', encoding='utf-8') as f:
                for obj in exported_objs:
                    f.write(obj + '\n')
            n = len(exported_objs)
            self.root.after(0, lambda: self._status(f'Добавлено в Blender: {n} моделей', '#a6e3a1'))
        else:
            if os.path.exists(queue_file):
                os.remove(queue_file)
            bridge_path = os.path.join(export_dir, 'p3d_bridge.py')
            with open(bridge_path, 'w', encoding='utf-8') as f:
                f.write(_BLENDER_BRIDGE_SCRIPT)
            args = [blender_exe, '--python', bridge_path, '--'] + exported_objs
            subprocess.Popen(args)
            n = len(exported_objs)
            self.root.after(0, lambda: self._status(f'Blender запущен: {n} моделей', '#a6e3a1'))

    def _resolve_tex(self, mat_path, p3d_path):
        """Ищет PAA текстуру по пути материала рядом с p3d файлом."""
        if not mat_path:
            return None
        base = os.path.splitext(mat_path)[0]
        p3d_dir = os.path.dirname(p3d_path)
        candidates = [
            os.path.join(p3d_dir, os.path.basename(base) + '_co.paa'),
            os.path.join(p3d_dir, os.path.basename(base) + '.paa'),
        ]
        for c in candidates:
            if os.path.exists(c):
                return c
        return None

    def _build_mods_panel(self, parent):
        """Объединённая панель: список модов + загруженные модели + кнопки."""
        dbg(f'_build_mods_panel START: parent={parent}')
        T = self._T()
        # v0.1.0: "Загружено" строится в правой панели, а не в sidebar
        loaded_parent = getattr(self, '_loaded_outer', parent)

        if not hasattr(self, '_models_search_var'):
            self._models_search_var = tk.StringVar()
            self._models_search_var.trace_add('write', lambda *_: self._update_models_panel())

        unpacked = _find_unpacked_dir()
        self._mods_unpacked = unpacked

        if not unpacked:
            tk.Label(parent,
                     text='Папка _unpacked не найдена.\nОткройте мод в dos_mod_editor.',
                     bg=T['bg_side'], fg=T['fg_muted'],
                     font=(T['font_fam'], T['font_sz'] - 2),
                     wraplength=190, justify=tk.LEFT).pack(padx=12, anchor='w', pady=6)

            def _pick():
                d = filedialog.askdirectory(title='Папка _unpacked')
                if d:
                    self._mods_unpacked = d
                    for w in parent.winfo_children():
                        w.destroy()
                    self._build_mods_panel(parent)

            tk.Button(parent, text='📁 Указать вручную', relief='flat',
                      bg=T['btn_bg'], fg=T['btn_fg'],
                      font=(T['font_fam'], T['font_sz'] - 1), padx=6, pady=2,
                      command=_pick).pack(padx=12, anchor='w', pady=(0, 8))
            # Создаём секцию "Загружено" и выходим
            tk.Frame(loaded_parent, bg=T['bg_sep'], height=1).pack(fill=tk.X, padx=8, pady=(4, 2))
            tk.Label(loaded_parent, text='Загружено:',
                     bg=T['bg_side'], fg=T['fg_label'],
                     font=(T['font_fam'], T['font_sz'] - 1)).pack(anchor='w', padx=12)
            _lsf = tk.Frame(loaded_parent, bg=T['bg_side'])
            _lsf.pack(fill=tk.X, padx=8, pady=(2, 2))
            tk.Label(_lsf, text='🔍', bg=T['bg_side'], fg=T['fg_muted'],
                     font=(T['font_fam'], T['font_sz'])).pack(side=tk.LEFT)
            tk.Entry(_lsf, textvariable=self._models_search_var,
                     bg=T['bg_widget'], fg=T['fg_text'],
                     insertbackground=T['fg_text'], relief='flat',
                     font=(T['font_fam'], T['font_sz'] - 1)).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))
            # v2.6: scrollable список моделей (Canvas + Scrollbar + inner Frame)
            # Решает проблему: фиксированная высота + скролл когда много моделей + нет моргания
            # Контейнер чтобы canvas и scrollbar были рядом
            _ml_container = tk.Frame(loaded_parent, bg=T['bg_side'])
            # v2.9: адаптивный — растягивается когда окно увеличивается
            _ml_container.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 6))
            # v3.1.9: _models_scroll (Scrollbar widget) удалён — KirrieD: быть не должно визуально.
            # Скролл мышью через <MouseWheel> bind ниже.
            self._models_canvas = tk.Canvas(_ml_container, bg=T['bg_side'], bd=0, highlightthickness=0,
                                              height=400)
            self._models_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            self._models_canvas.bind('<MouseWheel>',
                lambda e: self._models_canvas.yview_scroll(int(-1 * (e.delta / 120)), 'units'))
            self._models_inner = tk.Frame(self._models_canvas, bg=T['bg_side'])
            self._models_inner_win = self._models_canvas.create_window((0, 0),
                                                                         window=self._models_inner,
                                                                         anchor='nw')
            # Настройка scrollregion при изменении содержимого
            def _on_inner_configure(e):
                self._models_canvas.configure(scrollregion=self._models_canvas.bbox('all'))
            self._models_inner.bind('<Configure>', _on_inner_configure, add='+')
            # v3.1.9: MouseWheel bind уже добавлен выше (при создании canvas)
            # Словарь строк: idx -> (frame, label, close_btn) для in-place обновления
            self._model_rows = {}
            self._update_models_panel()
            return

        tk.Label(parent, text='Доступные моды:',
                 bg=T['bg_side'], fg=T['fg_label'],
                 font=(T['font_fam'], T['font_sz'] - 1)).pack(anchor='w', padx=12, pady=(6, 2))

        mods_search_var = tk.StringVar()
        _msf = tk.Frame(parent, bg=T['bg_side'])
        _msf.pack(fill=tk.X, padx=8, pady=(0, 2))
        tk.Label(_msf, text='🔍', bg=T['bg_side'], fg=T['fg_muted'],
                 font=(T['font_fam'], T['font_sz'])).pack(side=tk.LEFT)
        tk.Entry(_msf, textvariable=mods_search_var,
                 bg=T['bg_widget'], fg=T['fg_text'],
                 insertbackground=T['fg_text'], relief='flat',
                 font=(T['font_fam'], T['font_sz'] - 1)).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))

        # ── Прокручиваемый список (v0.1.26: ttk.Treeview — native virtual list) ───────
        list_outer = tk.Frame(parent, bg=T['bg_side'])
        # v3.1.4: expand=True → растягивается на всё свободное место (50/50 с загружено)
        list_outer.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 4))
        list_outer.pack_propagate(False)

        # v0.1.26: ttk.Treeview с нативным виртуальным рендерингом
        # Вместо 500-1000 виджетов tk рендерит только ~15 видимых строк
        import tkinter.ttk as ttk
        tree_cols = ('load',)
        self._mods_tree = ttk.Treeview(
            list_outer, columns=tree_cols, show='tree headings',
            selectmode='extended', height=15)
        self._mods_tree.heading('#0', text='Мод / Файл', anchor='w')
        self._mods_tree.heading('load', text='✓')
        self._mods_tree.column('#0', width=240, anchor='w', stretch=True)
        self._mods_tree.column('load', width=24, anchor='center', stretch=False)
        self._mods_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        # v0.1.27: убрать белый focus border вокруг Treeview
        tree_style = ttk.Style()
        try:
            tree_style.theme_use('clam')
        except Exception:
            pass
        tree_style.configure('Treeview',
            background=T.get('bg_side', '#131520'),
            foreground=T.get('fg_text', '#e8eaf0'),
            fieldbackground=T.get('bg_side', '#131520'),
            rowheight=22)
        tree_style.configure('Treeview.Heading',
            background=T.get('bg_widget', '#1d1f2e'),
            foreground=T.get('fg_label', '#9ca0b0'),
            relief='flat')
        tree_style.map('Treeview',
            background=[('selected', T.get('accent_bg', '#3b82f6'))],
            foreground=[('selected', '#ffffff')])
        # ttk.Treeview использует style для border — widget-level borderwidth не поддерживается
        # backward-compat alias для _mods_collect_selected
        self._mods_scroll_frame = None  # больше не используется
        canvas = None
        scroll_frame = None

        # ── Строим список модов (lazy loading) ───────────────────────────────
        self._mods_data = {}
        mods = _get_all_mods(unpacked)
        dbg(f'_build_mods_panel: unpacked={unpacked} mods_count={len(mods)}')

        MAX_CHILDREN = 300  # v0.1.26: limit files per mod (Treeview не имеет hard limit)

        def _build_children(mod_name, files):
            """v0.1.26: вставить файлы как child rows в Treeview."""
            tree = self._mods_tree
            disabled_set = set(self._app_settings.get('disabled_items', []))
            self._mods_data.setdefault('files', {})
            for fpath in files[:MAX_CHILDREN]:
                fname = os.path.basename(fpath)
                item_key = f'{mod_name}/{fname}'
                if item_key in disabled_set:
                    continue
                child_var = tk.BooleanVar(value=False)
                iid = item_key
                tree.insert(mod_name, 'end', iid=iid,
                            text=f'      {fname}',
                            values=('☐', ''))
                self._mods_data['files'][iid] = {
                    'var': child_var, 'path': fpath, 'mod': mod_name, 'fname': fname
                }
            self._mods_data[mod_name]['files'] = files
            self._mods_data[mod_name]['children'] = [
                (f, self._mods_data['files'][f'{mod_name}/{os.path.basename(f)}']['var'])
                for f in files[:MAX_CHILDREN]
                if f'{mod_name}/{os.path.basename(f)}' in self._mods_data['files']
            ]
            # update parent count
            n = len(self._mods_data[mod_name]['children'])
            tree.set(mod_name, 'count', str(n))

        # v0.1.26/27: lazy expand — при <<TreeviewOpen>> сканируем файлы
        def _on_tree_open(event):
            iid = self._mods_tree.focus()
            if not iid or iid not in self._mods_data:
                return
            mn = iid
            # пропустить placeholder child (iid начинается с __loading_)
            children = self._mods_tree.get_children(mn)
            if children and not any(c.startswith('__loading_') for c in children):
                return  # уже загружены
            def _load():
                files = _get_mod_p3d_files(unpacked, mn)
                def _update():
                    # удаляем placeholder
                    for c in list(self._mods_tree.get_children(mn)):
                        if c.startswith('__loading_'):
                            self._mods_tree.delete(c)
                    if files:
                        _build_children(mn, files)
                        # обновить parent text с count
                        n = sum(1 for c in self._mods_tree.get_children(mn)
                                if not c.startswith('__loading_'))
                        self._mods_tree.item(mn, text=f'  {mn}  ({n})')
                    else:
                        # нет файлов — пустой placeholder
                        self._mods_tree.insert(mn, 'end', iid=f'__empty_{mn}',
                                                 text='  нет .p3d файлов',
                                                 values=(' ',))
                try: self.root.after(0, _update)
                except Exception: pass
            import threading as _th
            _th.Thread(target=_load, daemon=True).start()
        self._mods_tree.bind('<<TreeviewOpen>>', _on_tree_open)

        def _legacy_build_children(children_frame, files, mod_name, mod_var, child_var_list, mod_cb_ref):
            """v0.1.26: deprecated — Treeview uses _build_children(mod_name, files) instead."""
            pass  # legacy code, не вызывается после v0.1.26

        disabled = self._app_settings.get('disabled_mods', [])
        active_mods   = [m for m in mods if m not in disabled]
        disabled_mods = [m for m in mods if m in disabled]

        _mod_row_refs = []  # [(mod_name, mod_container, toggle_fn)]
        self._mods_file_index = {}  # v3.1.5: mod_name → list[basename] для поиска по файлам в свёрнутых папках

        def _disable_mod(mn, container):
            """Move mod to disabled list and remove its widget."""
            d = self._app_settings.get('disabled_mods', [])
            if mn not in d:
                d.append(mn)
            self._app_settings['disabled_mods'] = d
            _save_app_settings(self._app_settings)
            container.destroy()
            _rebuild_disabled_section()

        def _enable_mod(mn, dis_row):
            """Remove mod from disabled list AND clean up any disabled items for this mod, rebuild."""
            d = self._app_settings.get('disabled_mods', [])
            if mn in d:
                d.remove(mn)
            self._app_settings['disabled_mods'] = d
            # v3.1: также удаляем все выключенные отдельные файлы этого мода
            di = self._app_settings.get('disabled_items', [])
            di = [k for k in di if not k.startswith(f'{mn}/')]
            self._app_settings['disabled_items'] = di
            _save_app_settings(self._app_settings)
            for w in parent.winfo_children():
                w.destroy()
            self._build_mods_panel(parent)

        def _disable_item(mod_name, file_path, row):
            """v3.1: отключить отдельный файл. Добавить в disabled_items, удалить виджет."""
            fname = os.path.basename(file_path)
            item_key = f'{mod_name}/{fname}'
            di = self._app_settings.get('disabled_items', [])
            if item_key not in di:
                di.append(item_key)
            self._app_settings['disabled_items'] = di
            _save_app_settings(self._app_settings)
            try: row.destroy()
            except Exception: pass
            _rebuild_disabled_section()

        def _enable_item(mod_name, file_name):
            """v3.1: включить отдельный файл обратно. Удалить из disabled_items, пересобрать панель."""
            item_key = f'{mod_name}/{file_name}'
            di = self._app_settings.get('disabled_items', [])
            if item_key in di:
                di.remove(item_key)
            self._app_settings['disabled_items'] = di
            _save_app_settings(self._app_settings)
            for w in parent.winfo_children():
                w.destroy()
            self._build_mods_panel(parent)

        def _add_mod_row(mn, target_frame):
            """v0.1.26/27: Treeview-based mod row — native virtual rendering."""
            mod_var = tk.BooleanVar(value=False)
            child_var_list = []
            tree = self._mods_tree
            # v0.1.27: вставляем parent row + placeholder child чтобы Treeview показал chevron ▶
            tree.insert('', 'end', iid=mn,
                        text=f'  {mn}  (…)',
                        values=('☐',))
            tree.insert(mn, 'end', iid=f'__loading_{mn}', text='  ⏳ загрузка...', values=(' ',))
            _mod_row_refs.append([mn, None, None, None])
            self._mods_data[mn] = {'var': mod_var, 'files': [], 'children': [], 'iid': mn}
            # клик на колонке 'load' → toggle
            def _on_click(event):
                region = tree.identify('region', event.x, event.y)
                if region != 'cell':
                    return
                col = tree.identify_column(event.x)
                item = tree.identify_row(event.y)
                if not item:
                    return
                if col == '#1':  # load
                    if item in self._mods_data:
                        # parent mod row
                        d = self._mods_data[item]
                        new_val = not d['var'].get()
                        d['var'].set(new_val)
                        tree.set(item, 'load', '☑' if new_val else '☐')
                        # каскад на детей
                        for c in tree.get_children(item):
                            if c in self._mods_data.get('files', {}):
                                cv = self._mods_data['files'][c]['var']
                                cv.set(new_val)
                                tree.set(c, 'load', '☑' if new_val else '☐')
                    else:
                        # file row: iid format 'mod/file'
                        mn2, fn2 = item.rsplit('/', 1)
                        file_data = self._mods_data.get('files', {}).get(item, {})
                        if file_data:
                            new_val = not file_data['var'].get()
                            file_data['var'].set(new_val)
                            tree.set(item, 'load', '☑' if new_val else '☐')
            tree.bind('<Button-1>', _on_click)
            # v0.1.29: eager load — сразу сканируем и вставляем файлы в Treeview
            # (lazy через <<TreeviewOpen>> ненадёжен — событие не всегда срабатывает)
            def _eager_load(_mn=mn):
                dbg(f'_eager_load start: mod={_mn} unpacked={unpacked}')
                try:
                    _files = _get_mod_p3d_files(unpacked, _mn)
                    self._mods_file_index[_mn] = [os.path.basename(f).lower() for f in _files]
                except Exception as _e:
                    dbg(f'_eager_load error: {_e}')
                    _files = []
                dbg(f'_eager_load scanned: mod={_mn} files_count={len(_files)}')
                def _update():
                    # удаляем placeholder если есть
                    for c in list(self._mods_tree.get_children(_mn)):
                        if c.startswith('__loading_'):
                            self._mods_tree.delete(c)
                    if _files:
                        _build_children(_mn, _files)
                        n = sum(1 for c in self._mods_tree.get_children(_mn)
                                if not c.startswith('__loading_') and not c.startswith('__empty_'))
                        self._mods_tree.item(_mn, text=f'  {_mn}  ({n})')
                        _dbg(f'_eager_load inserted: mod={_mn} n={n}')
                    else:
                        # нет файлов — пустой placeholder
                        if not self._mods_tree.get_children(_mn):
                            self._mods_tree.insert(_mn, 'end', iid=f'__empty_{_mn}',
                                                     text='  нет .p3d файлов',
                                                     values=(' ',))
                try: self.root.after(0, _update)
                except Exception: pass
            import threading as _th
            _th.Thread(target=_eager_load, daemon=True).start()

        for mod_name in active_mods:
            _add_mod_row(mod_name, None)
            # v0.1.20: spacer (теперь не нужен — Treeview имеет native padding)

        def _on_mods_search(*_):
            term = mods_search_var.get().lower().strip()
            file_index = getattr(self, '_mods_file_index', {})
            file_rows_map = getattr(self, '_mods_file_rows', {})
            for _entry in _mod_row_refs:
                _mn = _entry[0]
                _mc = _entry[1]
                _tf = _entry[2] if len(_entry) > 2 else None
                _expand_lbl = _entry[3] if len(_entry) > 3 else None
                # v3.1.7: матч по имени мода ИЛИ по любому файлу в индексе
                mod_match = bool(term) and term in _mn.lower()
                file_match = bool(term) and any(term in fn for fn in file_index.get(_mn, []))
                match = not term or mod_match or file_match
                try:
                    if match:
                        _mc.pack(fill=tk.X)
                        # v3.1.7: разворачиваем ТОЛЬКО если свёрнут
                        if term and file_match and _tf and _expand_lbl is not None:
                            try:
                                if _expand_lbl.cget('text') == '▶':
                                    _tf()
                            except Exception: pass
                    else:
                        _mc.pack_forget()
                        continue  # мод скрыта — файлы внутри не трогаем
                    # v3.1.7: фильтруем файлы внутри раскрытого мода
                    if term:
                        for fname, row in file_rows_map.get(_mn, []):
                            try:
                                if term in fname.lower():
                                    row.pack(fill=tk.X)
                                else:
                                    row.pack_forget()
                            except Exception: pass
                    else:
                        # пустой терм — показать все файлы
                        for fname, row in file_rows_map.get(_mn, []):
                            try: row.pack(fill=tk.X)
                            except Exception: pass
                except Exception:
                    pass
            canvas.update_idletasks()
            canvas.configure(scrollregion=canvas.bbox('all'))

        mods_search_var.trace_add('write', _on_mods_search)

        # ── Секция "Выключено" ────────────────────────────────────────────────
        dis_section_ref = [None]

        def _rebuild_disabled_section():
                    if dis_section_ref[0]:
                        try: dis_section_ref[0].destroy()
                        except Exception: pass
                    dis_now = self._app_settings.get('disabled_mods', [])
                    dis_items = self._app_settings.get('disabled_items', [])
                    items_by_mod = {}
                    for k in dis_items:
                        if '/' in k:
                            mn, fn = k.split('/', 1)
                            items_by_mod.setdefault(mn, []).append(fn)
                    total_count = len(dis_now) + len(dis_items)
                    # v0.1.11: даже если 0 — очищаем disabled_frame (он теперь отдельная панель)
                    # и добавляем заголовок "Выключено (0)" чтобы был виден разделитель
                    target = getattr(self, '_disabled_frame', None)
                    if target is None:
                        return
                    # destroy все старые children
                    for w in target.winfo_children():
                        w.destroy()
                    # Header "Выключено" с лейблом-счётчиком (всегда виден, поверх scroll)
                    hdr_frame = tk.Frame(target, bg=T['bg_side'])
                    hdr_frame.pack(side=tk.TOP, fill=tk.X, padx=8, pady=(6, 2))
                    hdr_lbl = tk.Label(hdr_frame,
                                       text=f'▼  Выключено ({total_count})',
                                       bg=T['bg_side'], fg=T['fg_label'],
                                       font=(T['font_fam'], T['font_sz'] - 1))
                    hdr_lbl.pack(side=tk.LEFT)
                    # Search box
                    _dis_search_var = tk.StringVar(value='')
                    srch = tk.Frame(target, bg=T['bg_side'])
                    srch.pack(side=tk.TOP, fill=tk.X, padx=8, pady=(0, 4))
                    tk.Label(srch, text='🔍', bg=T['bg_side'], fg=T['fg_muted'],
                             font=(T['font_fam'], T['font_sz'])).pack(side=tk.LEFT)
                    tk.Entry(srch, textvariable=_dis_search_var,
                             bg=T.get('bg_widget', '#1f2235'), fg=T['fg_text'],
                             insertbackground=T['fg_text'], relief='flat',
                             font=(T['font_fam'], T['font_sz'] - 1)
                             ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))
                    if total_count == 0:
                        # показать "пусто" hint
                        empty = tk.Label(target, text='  нет выключенных модов',
                                         bg=T['bg_side'], fg=T['fg_muted'],
                                         font=(T['font_fam'], T['font_sz'] - 2))
                        empty.pack(anchor='w', padx=12)
                        return
                    # Canvas для скролла выключенных items
                    dis_canvas = tk.Canvas(target, bg=T['bg_side'],
                                            bd=0, highlightthickness=0)
                    dis_canvas.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
                    dis_canvas.bind('<MouseWheel>',
                        lambda e: dis_canvas.yview_scroll(-1*(e.delta//120) if e.delta else -1, 'units'))
                    dis_inner = tk.Frame(dis_canvas, bg=T['bg_side'])
                    dis_cw = dis_canvas.create_window((0, 0), window=dis_inner, anchor='nw')
                    dis_inner.bind('<Configure>',
                        lambda e: dis_canvas.configure(scrollregion=dis_canvas.bbox('all')))
                    dis_canvas.bind('<Configure>',
                        lambda e: dis_canvas.itemconfig(dis_cw, width=e.width))
        # контейнер-обёртка для rows (compat с предыдущим API)
                    ds = dis_inner
                    dis_section_ref[0] = ds

                    # v0.1.11: search filter для выключено
                    _dis_search_var = tk.StringVar(value='')
                    _dis_row_refs = [[]]

                    def _on_dis_search(*_):
                        term = _dis_search_var.get().lower().strip()
                        for mn, w in _dis_row_refs[0]:
                            try:
                                if not term or term in mn.lower():
                                    w.pack(fill=tk.X)
                                else:
                                    w.pack_forget()
                            except Exception:
                                pass
                    _dis_search_var.trace_add('write', _on_dis_search)

                    # 1. Полностью выключенные моды
                    for mn in dis_now:
                        row = tk.Frame(ds, bg=T['bg_side'])
                        row.pack(fill=tk.X)
                        _dis_row_refs[0].append((mn, row))
                        tk.Label(row, text=mn, bg=T['bg_side'], fg=T['fg_muted'],
                                 font=(T['font_fam'], T['font_sz'] - 1)).pack(side=tk.LEFT, padx=14)
                        en_btn = tk.Label(row, text='↩', bg=T['bg_side'], fg=T['fg_label'],
                                          font=(T['font_fam'], T['font_sz']), cursor='hand2', padx=4)
                        en_btn.pack(side=tk.RIGHT, padx=(0, 4))
                        en_btn.bind('<Button-1>', lambda e, _mn=mn: _enable_mod(_mn, None))

                    # 2. мод с частично выключенными файлами
                    for mn in sorted(items_by_mod.keys()):
                        if mn in dis_now:
                            continue
                        mod_row = tk.Frame(ds, bg=T['bg_side'])
                        mod_row.pack(fill=tk.X, pady=(4, 0))
                        _dis_row_refs[0].append((mn, mod_row))
                        tk.Label(mod_row, text=mn, bg=T['bg_side'], fg=T['fg_label'],
                                 font=(T['font_fam'], T['font_sz'] - 1)).pack(side=tk.LEFT, padx=14)
                        for fn in sorted(items_by_mod[mn]):
                            f_row = tk.Frame(ds, bg=T['bg_side'])
                            f_row.pack(fill=tk.X)
                            tk.Label(f_row, text='    ├ ' + fn, bg=T['bg_side'], fg=T['fg_muted'],
                                     font=(T['font_fam'], T['font_sz'] - 2)).pack(side=tk.LEFT, padx=22)
                            en_btn = tk.Label(f_row, text='↩', bg=T['bg_side'], fg=T['fg_label'],
                                              font=(T['font_fam'], T['font_sz']), cursor='hand2', padx=4)
                            en_btn.pack(side=tk.RIGHT, padx=(0, 4))
                            en_btn.bind('<Button-1>',
                                        lambda e, _mn=mn, _fn=fn: _enable_item(_mn, _fn))

        _rebuild_disabled_section()

        # ── Кнопки действий ──────────────────────────────────────────────────
        btn_frame = tk.Frame(getattr(self, '_footer_frame', parent), bg=T['bg_side'])
        btn_frame.pack(fill=tk.X, padx=8, pady=(2, 4))

        def _load_selected():
            dbg('_load_selected: CLICKED')
            try:
                paths = self._mods_collect_selected()
            except Exception as _e:
                import traceback as _tb
                dbg(f'_load_selected EXCEPTION: {_e}')
                dbg(_tb.format_exc())
                self._status(f'Ошибка сбора: {_e}', '#f38ba8')
                return
            dbg(f'_load_selected: paths count={len(paths)} sample={paths[:2]}')
            if not paths:
                self._status('Выберите модели для загрузки', '#f9e2af')
                return
            # v0.0.5: single-file subprocess + semaphore (max 3 параллельно)
            self._load_paths_parallel(paths, max_concurrent=3)

        def _open_blender():
            paths = self._mods_collect_selected()
            if not paths:
                self._status('Выберите модели для Blender', '#f9e2af')
                return
            import threading as _th
            _th.Thread(target=self._export_to_blender, args=(paths,), daemon=True).start()

        tk.Button(btn_frame, text='👁 Загрузить', relief='flat',
                  bg=T['btn_bg'], fg=T['btn_fg'],
                  font=(T['font_fam'], T['font_sz'] - 1), padx=6, pady=3,
                  command=_load_selected).pack(side=tk.LEFT, padx=(0, 4))

        tk.Button(btn_frame, text='🎨 Blender', relief='flat',
                  bg=T['btn_bg'], fg=T['btn_fg'],
                  font=(T['font_fam'], T['font_sz'] - 1), padx=6, pady=3,
                  command=_open_blender).pack(side=tk.LEFT)

        # ── Список загруженных моделей (скроллируемый холст) ─────────────────────
        tk.Frame(loaded_parent, bg=T['bg_sep'], height=1).pack(fill=tk.X, padx=8, pady=(4, 0))
        tk.Label(loaded_parent, text='Загружено:',
                 bg=T['bg_side'], fg=T['fg_label'],
                 font=(T['font_fam'], T['font_sz'] - 1)).pack(anchor='w', padx=12, pady=(4, 2))
        _lsf2 = tk.Frame(loaded_parent, bg=T['bg_side'])
        _lsf2.pack(fill=tk.X, padx=8, pady=(0, 2))
        tk.Label(_lsf2, text='🔍', bg=T['bg_side'], fg=T['fg_muted'],
                 font=(T['font_fam'], T['font_sz'])).pack(side=tk.LEFT)
        tk.Entry(_lsf2, textvariable=self._models_search_var,
                 bg=T['bg_widget'], fg=T['fg_text'],
                 insertbackground=T['fg_text'], relief='flat',
                 font=(T['font_fam'], T['font_sz'] - 1)).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))

        loaded_outer = tk.Frame(loaded_parent, bg=T['bg_side'])
        # v3.1.4: expand=True → растягивается на всё свободное место (50/50 с доступные моды)
        loaded_outer.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 6))
        loaded_outer.pack_propagate(False)
        loaded_canvas = tk.Canvas(loaded_outer, bg=T['bg_side'], highlightthickness=0)
        # v3.1.9: scrollbar убран — Mousewheel bind ниже
        loaded_canvas.bind('<MouseWheel>',
            lambda e: loaded_canvas.yview_scroll(-1 * (e.delta // 120) if e.delta else -1, 'units'))
        loaded_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        # Backward compat: _models_canvas alias для _update_models_panel
        self._models_canvas = loaded_canvas

        self._models_inner = tk.Frame(loaded_canvas, bg=T['bg_side'])
        _lcw = loaded_canvas.create_window((0, 0), window=self._models_inner, anchor='nw')
        self._models_inner.bind(
            '<Configure>',
            lambda e: loaded_canvas.configure(scrollregion=loaded_canvas.bbox('all')))
        loaded_canvas.bind('<Configure>',
                           lambda e: loaded_canvas.itemconfig(_lcw, width=e.width))
        # Backward compat alias
        self._models_list_frame = self._models_inner

        def _on_loaded_wheel(event):
            loaded_canvas.yview_scroll(int(-1 * (event.delta / 120)), 'units')
        loaded_canvas.bind('<Enter>', lambda e: loaded_canvas.bind_all('<MouseWheel>', _on_loaded_wheel))
        loaded_canvas.bind('<Leave>', lambda e: loaded_canvas.unbind_all('<MouseWheel>'))

        self._update_models_panel()

    def _mods_collect_selected(self):
        """Собирает список выбранных p3d путей из mods_data['files'], исключая disabled_items и уже загруженные.
        v0.1.31b: post-Treeview структура — все файлы в self._mods_data['files']={iid: {var, path, mod, fname}}.
        """
        paths = []
        disabled_set = set(self._app_settings.get('disabled_items', []))
        loaded_paths = {
            state['path'] for state in self._models.values()
            if state.get('path') and state.get('status') in ('ready', 'loading')
        }
        files_map = getattr(self, '_mods_data', {}).get('files', {}) or {}
        for iid, fdata in files_map.items():
            var = fdata.get('var')
            fpath = fdata.get('path')
            fname = fdata.get('fname')
            mod = fdata.get('mod')
            if not (var and fpath and fname and mod):
                continue
            item_key = f'{mod}/{fname}'
            if item_key in disabled_set:
                continue
            if fpath in loaded_paths:
                continue
            if var.get():
                paths.append(fpath)
        return paths

    def _build_blender_panel(self, parent):
        """Панель выбора моделей для экспорта в Blender."""
        T = self._T()

        unpacked = _find_unpacked_dir()
        self._blender_unpacked = unpacked

        if not unpacked or not os.path.exists(unpacked):
            tk.Label(parent, text='Папка _unpacked не найдена.\nРаспакуйте мод через dos_mod_editor.',
                     bg=T['bg_side'], fg=T['fg_muted'],
                     font=(T['font_fam'], T['font_sz'] - 2),
                     wraplength=190, justify=tk.LEFT).pack(padx=16, anchor='w', pady=8)
            return

        mode_var = tk.StringVar(value='all')
        mode_frame = tk.Frame(parent, bg=T['bg_side'])
        mode_frame.pack(fill=tk.X, padx=16, pady=(4, 2))

        check_vars = {}
        list_frame_holder = [None]

        def _refresh():
            if list_frame_holder[0]:
                for w in list_frame_holder[0].winfo_children():
                    w.destroy()
            self._blender_refresh_list(list_frame_holder[0], mode_var, check_vars, unpacked)

        for label, val in [('Все', 'all'), ('По моду', 'mod'), ('По одиночке', 'single')]:
            rb = tk.Radiobutton(mode_frame, text=label, variable=mode_var, value=val,
                                bg=T['bg_side'], fg=T['fg_text'],
                                selectcolor=T['bg_widget'],
                                activebackground=T['bg_side'],
                                font=(T['font_fam'], T['font_sz'] - 1),
                                command=_refresh)
            rb.pack(side=tk.LEFT, padx=(0, 8))

        list_outer = tk.Frame(parent, bg=T['bg_side'])
        list_outer.pack(fill=tk.X, padx=16, pady=(0, 4))

        canvas = tk.Canvas(list_outer, bg=T['bg_side'], highlightthickness=0, height=150)
        sb = ttk.Scrollbar(list_outer, orient='vertical', command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        list_frame = tk.Frame(canvas, bg=T['bg_side'])
        canvas.create_window((0, 0), window=list_frame, anchor='nw')
        list_frame.bind('<Configure>',
                        lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
        list_frame_holder[0] = list_frame

        self._blender_refresh_list(list_frame, mode_var, check_vars, unpacked)

        def _open_blender():
            paths = self._blender_collect_paths(mode_var.get(), check_vars, unpacked)
            import threading as _th
            _th.Thread(target=self._export_to_blender, args=(paths,), daemon=True).start()

        tk.Button(parent, text='🎨  Открыть в Blender', relief='flat',
                  bg=T['btn_bg'], fg=T['btn_fg'],
                  font=(T['font_fam'], T['font_sz']), padx=8, pady=4,
                  command=_open_blender).pack(fill=tk.X, padx=16, pady=(4, 8))

    def _blender_refresh_list(self, list_frame, mode_var, check_vars, unpacked):
        """Перестраивает список чекбоксов."""
        if list_frame is None:
            return
        T = self._T()
        for w in list_frame.winfo_children():
            w.destroy()
        check_vars.clear()
        mode = mode_var.get()

        try:
            mods = sorted([d for d in os.scandir(unpacked) if d.is_dir()],
                          key=lambda d: d.name.lower())
        except Exception:
            return

        if mode == 'all':
            tk.Label(list_frame,
                     text=f'Все модели из {len(mods)} модов будут экспортированы',
                     bg=T['bg_side'], fg=T['fg_muted'],
                     font=(T['font_fam'], T['font_sz'] - 2),
                     wraplength=180, justify=tk.LEFT).pack(anchor='w', pady=4)

        elif mode == 'mod':
            for mod in mods:
                try:
                    p3ds = [f for f in os.scandir(mod.path) if f.name.lower().endswith('.p3d')]
                except Exception:
                    continue
                if not p3ds:
                    continue
                var = tk.BooleanVar(value=False)
                check_vars[mod.path] = var
                tk.Checkbutton(list_frame,
                               text=f'{mod.name}  ({len(p3ds)} p3d)',
                               variable=var,
                               bg=T['bg_side'], fg=T['fg_text'],
                               selectcolor=T['bg_widget'],
                               activebackground=T['bg_side'],
                               font=(T['font_fam'], T['font_sz'] - 1),
                               anchor='w').pack(fill=tk.X, pady=1)

        elif mode == 'single':
            for mod in mods:
                try:
                    p3ds = sorted([f for f in os.scandir(mod.path) if f.name.lower().endswith('.p3d')],
                                  key=lambda f: f.name.lower())
                except Exception:
                    continue
                if not p3ds:
                    continue
                tk.Label(list_frame, text=mod.name,
                         bg=T['bg_side'], fg=T['fg_label'],
                         font=(T['font_fam'], T['font_sz'] - 1)).pack(anchor='w', pady=(4, 0))
                for p3d in p3ds:
                    var = tk.BooleanVar(value=False)
                    check_vars[p3d.path] = var
                    tk.Checkbutton(list_frame,
                                   text=f'  {p3d.name}',
                                   variable=var,
                                   bg=T['bg_side'], fg=T['fg_text'],
                                   selectcolor=T['bg_widget'],
                                   activebackground=T['bg_side'],
                                   font=(T['font_fam'], T['font_sz'] - 2),
                                   anchor='w').pack(fill=tk.X)

    def _blender_collect_paths(self, mode, check_vars, unpacked):
        """Собирает список p3d путей по выбранному режиму."""
        paths = []
        if mode == 'all':
            try:
                for mod in os.scandir(unpacked):
                    if not mod.is_dir():
                        continue
                    for f in os.scandir(mod.path):
                        if f.name.lower().endswith('.p3d'):
                            paths.append(f.path)
            except Exception:
                pass
        elif mode == 'mod':
            for mod_path, var in check_vars.items():
                if var.get():
                    try:
                        for f in os.scandir(mod_path):
                            if f.name.lower().endswith('.p3d'):
                                paths.append(f.path)
                    except Exception:
                        pass
        elif mode == 'single':
            for p3d_path, var in check_vars.items():
                if var.get():
                    paths.append(p3d_path)
        return paths

    # ── Браузер P3D ───────────────────────────────────────────────────────────

    def _refresh_browser(self):
        """Заполняет дерево браузера из self._browser_root."""
        tree = getattr(self, '_browser_tree', None)
        if tree is None or not self._browser_root:
            return
        for item in tree.get_children():
            tree.delete(item)
        self._browser_populate(tree, '', self._browser_root)

    def _browser_populate(self, tree, parent, folder):
        """Рекурсивно заполняет ветку дерева папками и .p3d файлами."""
        try:
            entries = sorted(os.scandir(folder), key=lambda e: (not e.is_dir(), e.name.lower()))
        except PermissionError:
            return
        for entry in entries:
            if entry.is_dir():
                node = tree.insert(parent, 'end', text=f'📁 {entry.name}',
                                   values=[entry.path], open=False)
                tree.insert(node, 'end', text='...', values=['__placeholder__'])
            elif entry.name.lower().endswith('.p3d'):
                tree.insert(parent, 'end', text=f'  {entry.name}',
                            values=[entry.path])

    def _browser_on_open(self, event):
        """Ленивая загрузка содержимого папки при раскрытии."""
        tree  = self._browser_tree
        node  = tree.focus()
        children = tree.get_children(node)
        if len(children) == 1 and tree.item(children[0], 'values') == ('__placeholder__',):
            tree.delete(children[0])
            folder = tree.item(node, 'values')[0]
            self._browser_populate(tree, node, folder)

    def _browser_on_double_click(self, event):
        """Двойной клик на .p3d файле → загрузить модель."""
        tree = self._browser_tree
        node = tree.focus()
        vals = tree.item(node, 'values')
        if not vals:
            return
        path = vals[0]
        if path != '__placeholder__' and path.lower().endswith('.p3d'):
            self._load_p3d(path)

    def _open_p3d(self):
        p = filedialog.askopenfilename(title='Открыть P3D',
            filetypes=[('P3D', '*.p3d'), ('Все', '*.*')])
        if p: self._load_p3d(p)

    def _open_paa(self):
        p = filedialog.askopenfilename(title='Диффузная текстура (_co.paa)',
            filetypes=[('PAA', '*.paa'), ('Все', '*.*')])
        if p: self._load_paa(p)

    def _open_nrm(self):
        p = filedialog.askopenfilename(title='Карта нормалей (_nohq.paa)',
            filetypes=[('PAA', '*.paa'), ('Все', '*.*')])
        if p: self._load_nrm(p)

    def _open_rvmat(self):
        p = filedialog.askopenfilename(title='Открыть RVMAT материал',
            filetypes=[('RVMAT', '*.rvmat'), ('Все', '*.*')])
        if p: self._load_rvmat(p)

    def _clear_all_tex(self):
        if USE_GL:
            self.viewport.clear_texture()
            self.viewport.clear_normal()
        self._ind(self._diff_ind, False); self._ind(self._nrm_ind, False)
        self._status('Текстуры убраны', '#6c7086')

    def _toggle_light(self):
        if not USE_GL: return
        self.viewport.lighting = not self.viewport.lighting
        s = 'ВКЛ' if self.viewport.lighting else 'ВЫКЛ'
        self._light_btn.config(text=f'💡  Освещение: {s}')

    # v3.1.7: _toggle_alpha удалён — теперь per-material настройка через чекбоксы в Material panel

    def _on_brightness(self, _=None):
        if USE_GL:
            self.viewport.brightness = self._bright_var.get()

    # ── Вспомогательные методы работы с моделями ─────────────────────────────

    def _model_state_snapshot(self):
        """Снимок текущего состояния для сохранения в _models."""
        return {
            'path':               self.current_file,
            'pts':                self.pts,
            'uvs':                self.uvs,
            'nrms':               self.nrms,
            'tris':               self.tris,
            'mat_groups':         self.mat_groups,
            'raw':                self._raw_model,
            'cur_split':          self._cur_split,
            'cur_split2':         self._cur_split2,
            'cur_splits':         getattr(self, '_cur_splits', []),  # v2.3: N-материалов
            'cur_swap':           self._cur_swap,
            'cur_uv_limit':       self._cur_uv_limit,
            'cur_edge_mult':      self._cur_edge_mult,
            'config_loaded_name': self._config_loaded_name,
            'status':             'ready',
        }

    def _restore_model_state(self, state):
        """Восстанавливает состояние из снимка."""
        self.current_file        = state['path']
        self.pts                 = state['pts']
        self.uvs                 = state['uvs']
        self.nrms                = state['nrms']
        self.tris                = state['tris']
        self.mat_groups          = state['mat_groups']
        self._raw_model          = state['raw']
        self._cur_split          = state['cur_split']
        self._cur_split2         = state['cur_split2']
        self._cur_swap           = state['cur_swap']
        self._cur_uv_limit       = state['cur_uv_limit']
        self._cur_edge_mult      = state['cur_edge_mult']
        self._config_loaded_name = state['config_loaded_name']
        # v2.3: восстанавливаем cur_splits если есть
        self._cur_splits         = list(state.get('cur_splits', []))

    def _switch_to_model(self, idx):
        """Переключиться на модель с данным индексом. v2.3: текстуры берутся из кэша модели."""
        if idx not in self._models:
            return
        state = self._models[idx]
        if state['status'] != 'ready':
            return
        # Сохранить текущее состояние
        if self._active_idx is not None and self._active_idx in self._models:
            snap = self._model_state_snapshot()
            self._models[self._active_idx].update(snap)
        # Восстановить новое
        # v3.1.8: запоминаем предыдущую активную для skip _recompute_mats при switch на ту же модель
        prev_active_idx = self._active_idx
        self._active_idx = idx
        self._restore_model_state(state)
        # v2.7.3: lazy resolve textures (silent load — текстуры применяются только при переключении)
        try:
            self._resolve_textures_for(idx)
        except Exception as _tex_e:
            _dbg(f'_resolve_textures_for on switch failed: {_tex_e}')
        # Обновить GL
        if USE_GL and self.pts:
            self.viewport.pan_x = 0.0
            self.viewport.pan_y = 0.0
            # v3.1.8: skip весь proxy handling если нет proxies — экономит до ~50ms
            _prx = (self._raw_model or {}).get('proxies', [])
            self.viewport.proxies = _prx
            self._current_p3d_path = state.get('path', '')
            if _prx:
                # Cancel any pending delayed load
                if hasattr(self, '_proxy_load_after'):
                    try: self.root.after_cancel(self._proxy_load_after)
                    except Exception: pass
                    del self._proxy_load_after
                # Check if all proxy files already exist locally
                _already_local = all(
                    os.path.isfile(os.path.join(PROXIES_DIR, _proxy_basename_normalize(p.get('name', ''))))
                    for p in _prx if p.get('name')
                )
                if _already_local:
                    self._load_proxy_meshes_to_viewport(_prx)
                else:
                    self._auto_copy_proxies(
                        _prx,
                        on_done=lambda p=list(_prx): self._load_proxy_meshes_to_viewport(p)
                    )
                self.viewport.selected_proxy = -1
                self.viewport.on_proxy_select = self._on_viewport_proxy_select
                self.viewport.on_proxy_transform = self._on_proxy_transform_live
            else:
                # v3.1.8: нет прокси — clear state, не вызываем тяжёлые методы
                self.viewport.proxies = []
                self.viewport.selected_proxy = -1
            self._update_proxy_list()
            try:
                _dbg(f'load_mesh: pts={len(self.pts or [])} tris={len(self.tris or [])}')
                # v3.1.8: skip load_mesh если mesh уже загружен для этой модели — экономит 50-200ms (VBO upload)
                if getattr(self.viewport, '_mesh_loaded_for_idx', None) != idx:
                    self.viewport.load_mesh(self.pts, self.nrms, self.tris, self.uvs, self.mat_groups)
                    self.viewport._mesh_loaded_for_idx = idx
                    _dbg(f'load_mesh RUN: idx={idx} pts={len(self.pts or [])}')
                else:
                    _dbg(f'load_mesh SKIPPED: idx={idx} already loaded')
                # v3.1.8: skip _recompute_mats если switch на ту же модель — экономит 100-500ms
                if prev_active_idx != idx:
                    self._recompute_mats(
                        split=self._cur_split, split2=self._cur_split2,
                        swap=self._cur_swap, uv_limit=self._cur_uv_limit,
                        edge_mult=self._cur_edge_mult)
                else:
                    _dbg(f'_recompute_mats SKIPPED: same model idx={idx}')
                # v2.3: после загрузки меша — накатить текстуры ИЗ КЭША ЭТОЙ МОДЕЛИ,
                # а не из текущего _auto_tex (который мог быть про другую модель).
                # Это решает баг "перезаписалась текстура активной модели".
                # v3.1.8: skip bind если уже забиндено для этой модели + textures populated
                state_obj = self._models.get(idx, {})
                tex_populated = bool(state_obj.get('tex_data', {}).get('populated'))
                tex_bound = (getattr(self.viewport, '_tex_bound_for_idx', None) == idx)
                if not (tex_populated and tex_bound):
                    if self.viewport._pending_mesh is not None:
                        _cf = self.current_file
                        _idx = idx
                        self.viewport._pending_mesh_cb = lambda: self._bind_textures_for_model(_idx)
                    else:
                        self._bind_textures_for_model(idx)
                    if tex_populated:
                        self.viewport._tex_bound_for_idx = idx
                else:
                    _dbg(f'_bind_textures_for_model SKIPPED: idx={idx} already bound')
            except Exception as _gl_e:
                _dbg(f'load_mesh ERROR: {_gl_e}')
        else:
            _dbg(f'load_mesh SKIPPED: USE_GL={USE_GL} pts={len(self.pts) if self.pts else 0}')
        # Обновить панели
        n = len(self.mat_groups)
        self.info.config(text=(
            f'📄 {os.path.basename(self.current_file)}\n\n'
            f'Вершины:       {len(self.pts or [])}\n'
            f'Треугольники:  {len(self.tris or [])}\n'
            f'UV:            {len(self.uvs or [])}\n'
            f'Материалы:     {n}'))
        self._update_mat_panel()
        self._update_models_panel()
        self._status(f'Активна: {os.path.basename(self.current_file)}', '#a6e3a1')

    def _close_model(self, idx):
        """Закрыть модель из списка."""
        _dbg(f'_close_model START: idx={idx} active={self._active_idx} models={list(self._models.keys())}')
        if idx not in self._models:
            _dbg(f'_close_model: idx {idx} not in _models — skip')
            return
        try:
            was_active = (self._active_idx == idx)
            del self._models[idx]
            _dbg(f'_close_model: deleted {idx}, was_active={was_active}, models now={list(self._models.keys())}')
            if was_active:
                _dbg(f'_close_model: clearing active state...')
                self._active_idx = None
                self.current_file = None
                self._raw_model = None
                self.pts = self.uvs = self.nrms = self.tris = []
                self.mat_groups = []
                if USE_GL:
                    self.viewport.proxies = []
                    self.viewport.proxy_meshes = {}
                    self.viewport.selected_proxy = -1
                    try:
                        self._update_proxy_list()
                        self.viewport.load_mesh([], [], [], [], [])
                    except Exception as _gl_e:
                        _dbg(f'_close_model: GL clear error: {_gl_e}')
                try:
                    self._update_mat_panel()
                    _dbg(f'_close_model: _update_mat_panel OK')
                except Exception as _mat_e:
                    _dbg(f'_close_model: _update_mat_panel ERROR: {_mat_e}')
            # Переключиться на первую готовую модель если есть
            switched = False
            for i, s in self._models.items():
                if s['status'] == 'ready':
                    try:
                        self._switch_to_model(i)
                        switched = True
                    except Exception as _sw_e:
                        _dbg(f'_close_model: _switch_to_model ERROR: {_sw_e}')
                    break
            _dbg(f'_close_model: switched={switched}, calling _do_update_models_panel')
            # v3.1.6: принудительное обновление (bypass debounce чтобы избежать race)
            if getattr(self, '_models_panel_after', None):
                try: self.root.after_cancel(self._models_panel_after)
                except Exception: pass
                self._models_panel_after = None
            try:
                self._do_update_models_panel()
                _dbg(f'_close_model: _do_update_models_panel OK')
            except Exception as _up_e:
                _dbg(f'_close_model: _do_update_models_panel ERROR: {_up_e}')
            _dbg(f'_close_model END')
        except Exception as _e:
            _dbg(f'_close_model FATAL ERROR: {_e}')
            try:
                import traceback as _tb
                _dbg(_tb.format_exc())
            except Exception: pass

    def _show_auto_tune_popup(self, message: str):
        """v3.1.6: показать popup с результатом Auto-Tune."""
        try:
            win = tk.Toplevel(self.root)
            win.title('Auto-Tune — результат')
            win.geometry('420x260')
            win.configure(bg=self._T()['bg_main'])
            # Центрируем относительно главного окна
            try:
                win.transient(self.root)
                win.attributes('-topmost', True)
            except Exception: pass
            tk.Label(win, text=message,
                     bg=self._T()['bg_main'], fg=self._T()['fg_text'],
                     font=(self._T()['font_fam'], self._T()['font_sz']),
                     wraplength=380, justify=tk.LEFT).pack(padx=16, pady=16, anchor='w')
            tk.Button(win, text='OK', relief='flat',
                      bg=self._T()['btn_bg'], fg=self._T()['btn_fg'],
                      font=(self._T()['font_fam'], self._T()['font_sz']),
                      padx=12, pady=4, command=win.destroy).pack(pady=(0, 12))
            # Автозакрытие через 15 сек (на случай если забыли)
            win.after(15000, win.destroy)
        except Exception as _e:
            _dbg(f'_show_auto_tune_popup error: {_e}')
            # Фолбек — status bar
            self._status(message.replace('\n', ' | ')[:200], '#89b4fa')

    def _update_models_panel(self):
        """v2.7: показывает ready модели в основном меню.
        Loading и Errors — в отдельных Toplevel окнах (popup).
        Dedup по basename (basename → один idx).
        v2.7.1: debounce — откладывает обновление чтобы не блокировать UI при batch загрузке.
        """
        # Debounce: если уже запланировано обновление в ближайшее время — не планируем ещё одно
        if getattr(self, '_models_panel_after', None):
            return  # уже запланировано
        self._models_panel_after = self.root.after(50, self._do_update_models_panel)

    @staticmethod
    def _extract_mod_name(p):
        """v3.1.8: helper — извлекает имя мода из пути `_unpacked/<MOD>/<file>` → MOD.
        None если путь не из _unpacked."""
        parts = str(p).replace('\\', '/').split('/')
        for i, part in enumerate(parts):
            if part == '_unpacked' and i + 1 < len(parts):
                return parts[i + 1]
        return None

    def _close_mod_all(self, mod_name):
        """v3.1.8: закрывает ВСЕ модели данного мода разом.
        Если mod_name is None — закрывает все модели без _unpacked."""
        _dbg(f'_close_mod_all START: mod={mod_name!r} models={list(self._models.keys())}')
        if not self._models:
            return
        to_close = []
        for idx, info in self._models.items():
            path = info.get('path', '')
            if self._extract_mod_name(path) == mod_name:
                if info.get('status') in ('ready', 'loading', 'failed', 'cancelled'):
                    to_close.append(idx)
        if not to_close:
            return
        # Закрываем по одному через существующий метод (обрабатывает active_idx, scan и т.д.)
        for idx in sorted(to_close, reverse=True):
            try:
                self._close_model(idx)
            except Exception as _e:
                _dbg(f'_close_mod_all: _close_model({idx}) failed: {_e}')
        # Перерисовать панель
        if hasattr(self, '_do_update_models_panel'):
            try: self._do_update_models_panel()
            except Exception: pass
        self._status(f'Удалено моделей из {mod_name or "Без мода"}: {len(to_close)}', '#a6e3a1')

    def _do_update_models_panel(self):
        """Реальная логика _update_models_panel — вызывается через debounce."""
        self._models_panel_after = None
        _dbg(f'_do_update_models_panel START: models={list(self._models.keys())} ready_count={sum(1 for s in self._models.values() if s.get("status")=="ready")}')
        inner = getattr(self, '_models_inner', None)
        if inner is None:
            return
        T = self._T()

        _sv = getattr(self, '_models_search_var', None)
        _term = _sv.get().lower().strip() if _sv else ''

        def _match(path):
            if not _term:
                return True
            return _term in os.path.basename(path).lower() or _term in path.lower()

        # Dedup по basename: для каждого basename — ОДНА запись с лучшим приоритетом
        # Приоритет: ready > loading > failed/cancelled
        def _priority(s):
            st = s.get('status', '')
            if st == 'ready':   return 0
            if st == 'loading': return 1
            return 2

        deduped = {}  # basename -> (idx, state)
        for idx, state in self._models.items():
            if not _match(state.get('path', '')):
                continue
            key = os.path.basename(state.get('path', '')).lower()
            cur = deduped.get(key)
            if cur is None or _priority(state) < _priority(cur[1]):
                deduped[key] = (idx, state)

        # Разделяем deduped по статусу
        ready   = sorted([v for v in deduped.values() if v[1].get('status') == 'ready'])
        loading = sorted([v for v in deduped.values() if v[1].get('status') == 'loading'])
        failed  = sorted([v for v in deduped.values() if v[1].get('status') == 'failed'])

        existing_rows = getattr(self, '_model_rows', None)
        if existing_rows is None:
            self._model_rows = {}
            existing_rows = self._model_rows

        # v3.1: определяем мод (папку) из пути — `_unpacked/<MOD>/<file>` → MOD
        def _extract_mod_name(p):
            parts = str(p).replace('\\', '/').split('/')
            for i, part in enumerate(parts):
                if part == '_unpacked' and i + 1 < len(parts):
                    return parts[i + 1]
            return None

        # Группируем по моду
        mod_groups = {}  # mod_name → list[(idx, state)]
        for idx, state in ready:
            mod = _extract_mod_name(state.get('path', ''))
            mod_groups.setdefault(mod, []).append((idx, state))

        # Сохраняем scroll position чтобы не сбрасывался
        canvas = getattr(self, '_models_canvas', None)
        prev_yview = (0.0, 1.0)
        if canvas is not None:
            try: prev_yview = canvas.yview()
            except Exception: pass

        # ── Полная пересборка: чистим inner и existing_rows ──
        # v0.1.12: чтобы не было белого моргания — обработать очередь tk
        # ДО destroy чтобы все pending redraw завершились
        try: inner.update_idletasks()
        except Exception: pass
        for w in inner.winfo_children():
            try: w.destroy()
            except Exception: pass
        existing_rows.clear()

        if not ready:
            ph = tk.Label(inner, text='Нет загруженных моделей',
                          bg=T['bg_side'], fg=T['fg_muted'],
                          font=(T['font_fam'], T['font_sz'] - 2))
            ph.pack(anchor='w', pady=4, padx=4)
            existing_rows['placeholder'] = {'label': ph}
        else:
            # Сортируем: моды по имени, None в конец
            sorted_mods = sorted(mod_groups.keys(),
                                 key=lambda m: (m is None, (m or '').lower()))
            for mod in sorted_mods:
                items = sorted(mod_groups[mod],
                               key=lambda x: os.path.basename(x[1]['path']).lower())

                # v3.1.8: заголовок мода в строке с × кнопкой для удаления всех моделей мода
                if mod:
                    hdr_row = tk.Frame(inner, bg=T['bg_side'])
                    hdr_row.pack(fill=tk.X, padx=8, pady=(6, 2), anchor='w')
                    hdr = tk.Label(hdr_row, text=f'▼ {mod}  ({len(items)})',
                                   bg=T['bg_side'], fg=T['accent_fg'],
                                   font=(T['font_fam'], T['font_sz'] - 1, 'bold'),
                                   anchor='w')
                    hdr.pack(side=tk.LEFT)
                    close_all = tk.Button(hdr_row, text='✕', relief='flat',
                                          bg=T['bg_side'], fg='#f38ba8',
                                          font=(T['font_fam'], T['font_sz'] - 2),
                                          padx=6, pady=0, bd=0, cursor='hand2',
                                          activebackground='#45475a',
                                          activeforeground='#f38ba8',
                                          highlightthickness=1,
                                          highlightbackground='#f38ba8',
                                          command=lambda m=mod: self._close_mod_all(m))
                    close_all.pack(side=tk.RIGHT)
                else:
                    if any(True for _ in items):  # есть модели без мода
                        hdr_row = tk.Frame(inner, bg=T['bg_side'])
                        hdr_row.pack(fill=tk.X, padx=8, pady=(6, 2), anchor='w')
                        hdr = tk.Label(hdr_row, text=f'▼ Без мода  ({len(items)})',
                                       bg=T['bg_side'], fg=T['fg_muted'],
                                       font=(T['font_fam'], T['font_sz'] - 2, 'bold'),
                                       anchor='w')
                        hdr.pack(side=tk.LEFT)
                        close_all = tk.Button(hdr_row, text='✕', relief='flat',
                                              bg=T['bg_side'], fg='#f38ba8',
                                              font=(T['font_fam'], T['font_sz'] - 2),
                                              padx=6, pady=0, bd=0, cursor='hand2',
                                              activebackground='#45475a',
                                              activeforeground='#f38ba8',
                                              highlightthickness=1,
                                              highlightbackground='#f38ba8',
                                              command=lambda m=None: self._close_mod_all(m))
                        close_all.pack(side=tk.RIGHT)

                # Рендер моделей мода (отступ слева)
                for idx, state in items:
                    is_active = (idx == self._active_idx)
                    name = os.path.basename(state['path'])
                    fg = T['accent_fg'] if is_active else T['fg_text']
                    bg = T['accent_bg'] if is_active else T['bg_side']

                    row = tk.Frame(inner, bg=bg)
                    row.pack(fill=tk.X, pady=1, padx=(24 if mod else 8, 8), anchor='w')
                    close_btn = tk.Button(row, text='✕', relief='flat',
                                           bg=bg, fg='#f38ba8',
                                           font=(T['font_fam'], T['font_sz'] - 1),
                                           padx=6, pady=0, bd=0, cursor='hand2',
                                           # v3.1.6: фикс — activebackground ≠ bg для активной модели
                                           # иначе клик сливается с зелёным фоном row
                                           activebackground='#45475a',
                                           activeforeground='#f38ba8',
                                           highlightthickness=1,
                                           highlightbackground='#f38ba8',
                                           command=lambda i=idx: self._close_model(i))
                    close_btn.pack(side=tk.RIGHT)
                    lbl = tk.Label(row, text=name, bg=bg, fg=fg,
                                   font=(T['font_fam'], T['font_sz'] - 1),
                                   cursor='hand2', anchor='w')
                    lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)
                    lbl.bind('<Button-1>', lambda e, i=idx: self._switch_to_model(i))
                    existing_rows[idx] = {
                        'row': row,
                        'label': lbl,
                        'close_btn': close_btn,
                    }

        # Обновляем scrollregion + восстанавливаем scroll position
        try:
            self.root.update_idletasks()
            if canvas:
                canvas.configure(scrollregion=canvas.bbox('all'))
                try: canvas.yview_moveto(prev_yview[0])
                except Exception: pass
        except Exception:
            pass

        # ── Popup окна для loading и failed ──────────────────────────────────────
        self._update_loading_popup(loading)
        # v3.1.5: errors popup показываем только если НЕТ поиска (иначе раздражает)
        self._update_errors_popup(failed if not _term else [])
        _dbg(f'_do_update_models_panel END: ready={len(ready)} loading={len(loading)} failed={len(failed)}')

    def _update_loading_popup(self, loading_list):
        """v2.7: popup окно для загружающихся моделей. Открыто пока есть loading."""
        T = self._T()
        win = getattr(self, '_loading_win', None)

        if not loading_list:
            # Закрываем окно если было
            if win is not None:
                try: win.destroy()
                except Exception: pass
                self._loading_win = None
            return

        # Создаём окно если нет
        if win is None or not win.winfo_exists():
            self._loading_win = tk.Toplevel(self.root)
            self._loading_win.title('Загрузка...')
            # Высоту подгоняем под кол-во моделей, чтобы не было пустого места
            h = min(80 + len(loading_list) * 22, 250)
            self._loading_win.geometry(f'400x{h}+50+50')
            self._loading_win.configure(bg=T['bg_side'])
            try: self._loading_win.attributes('-topmost', True)
            except Exception: pass

            inner = tk.Frame(self._loading_win, bg=T['bg_side'])
            inner.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

            self._loading_label = tk.Label(inner, text='Загружается:',
                                            bg=T['bg_side'], fg=T['fg_label'],
                                            font=(T['font_fam'], T['font_sz']))
            self._loading_label.pack(anchor='w', pady=(0, 4))

            self._loading_canvas = tk.Canvas(inner, bg=T['bg_side'], highlightthickness=0)
            self._loading_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            self._loading_sb = tk.Scrollbar(inner, orient='vertical',
                                            command=self._loading_canvas.yview,
                                            bg=T['bg_sep'], troughcolor=T['bg_main'],
                                            width=0, bd=0, highlightthickness=0)
            self._loading_sb.pack(side=tk.RIGHT, fill=tk.Y)
            self._loading_canvas.configure(yscrollcommand=self._loading_sb.set)

            self._loading_inner = tk.Frame(self._loading_canvas, bg=T['bg_side'])
            self._loading_canvas.create_window((0, 0), window=self._loading_inner, anchor='nw')
            self._loading_inner.bind(
                '<Configure>',
                lambda e: self._loading_canvas.configure(scrollregion=self._loading_canvas.bbox('all')))

            self._loading_rows = {}
        else:
            inner = self._loading_inner
            # Подгоняем высоту окна
            try:
                h = min(80 + len(loading_list) * 22, 250)
                self._loading_win.geometry(f'400x{h}+50+50')
            except Exception:
                pass

        # Update rows
        existing = self._loading_rows
        current_idxs = {i for i, _ in loading_list}
        for idx in list(existing.keys()):
            if idx not in current_idxs:
                widgets = existing[idx]
                for w in widgets.values():
                    if hasattr(w, 'destroy'):
                        try: w.destroy()
                        except Exception: pass
                del existing[idx]

        for idx, state in loading_list:
            name = os.path.basename(state['path'])
            progress = state.get('progress_msg', '...')
            text = f'⏳ {name} — {progress}'

            if idx in existing:
                lbl = existing[idx]['label']
                lbl.config(text=text)
            else:
                row = tk.Frame(inner, bg=T['bg_side'])
                row.pack(fill=tk.X, pady=1, anchor='w')
                lbl = tk.Label(row, text=text, bg=T['bg_side'], fg=T['fg_text'],
                               font=(T['font_fam'], T['font_sz'] - 1), anchor='w')
                lbl.pack(fill=tk.X)
                # Кнопка отмены
                cancel = tk.Button(row, text='✕', relief='flat',
                                    bg=T['bg_side'], fg='#f38ba8',
                                    font=(T['font_fam'], T['font_sz'] - 2),
                                    padx=4, pady=0, bd=0, cursor='hand2',
                                    command=lambda i=idx: self._cancel_one_load(i))
                cancel.pack(side=tk.RIGHT)
                existing[idx] = {'row': row, 'label': lbl}

        try:
            self._loading_canvas.configure(scrollregion=self._loading_canvas.bbox('all'))
        except Exception:
            pass

    def _update_errors_popup(self, failed_list, force=False):
        """force=True — показать popup даже если все ошибки уже в showed_errors (для кнопки)."""
        """v2.7: popup окно для failed моделей + логирование в debuglog.

        v3.1.8: показывает popup только если есть НОВЫЕ ошибки (другая модель
        или другое сообщение). Если все ошибки уже были показаны ранее — молча
        обновляет лог. Не пересоздаёт окно если пользователь его закрыл и нет
        новых ошибок (флаг _errors_dismissed).
        """
        # Кэшируем failed модели для re-open через кнопку
        self._errors_log = list(failed_list)
        self._update_err_btn_state()

        T = self._T()
        win = getattr(self, '_errors_win', None)

        if not failed_list:
            # Нет failed — destroy окно. Persistent set НЕ сбрасываем (KirrieD: ошибки уже показанные не повторяются после перезагрузки)
            if win is not None:
                try: win.destroy()
                except Exception: pass
            self._errors_win = None
            return

        # v3.1.9: persistent-based логика через settings.json.
        # Popup показывается только если есть НОВЫЕ (path, error) не в showed_errors.
        # KirrieD: "ошибка выводится только если количество ошибок прибавилось"
        current_keys = set()
        for _idx, _state in failed_list:
            try:
                _p = str(_state.get('path', ''))
                _e = str(_state.get('error', ''))[:200]
                if _p and _e:
                    current_keys.add(f'{_p}|{_e}')
            except Exception:
                pass

        showed = set(self._app_settings.get('showed_errors', []))
        new_keys = current_keys - showed
        if not new_keys and not force:
            # Все ошибки уже были показаны ранее — popup НЕ показываем
            return

        if not force:
            # Есть новые — добавляем ВСЕ текущие keys в persistent set + сохраняем
            showed.update(current_keys)
            self._app_settings['showed_errors'] = sorted(showed)
            _save_app_settings(self._app_settings)
        # else: force=True (нажата кнопка "Ошибки") — НЕ модифицируем persistent set
        self._errors_dismissed = False  # re-show если раньше закрыли

        if win is None or not win.winfo_exists():
            self._errors_win = tk.Toplevel(self.root)
            self._errors_win.title('Ошибки загрузки')
            # v3.1.9: динамический размер — min(50 + N*22, screen_height - 80). Не превышает экран.
            try:
                _screen_h = self.root.winfo_screenheight()
            except Exception:
                _screen_h = 800
            h = min(50 + len(failed_list) * 22, _screen_h - 80, 500)
            self._errors_win.geometry(f'500x{h}+100+100')
            self._errors_win.configure(bg=T['bg_side'])

            def _close_errors():
                self._errors_dismissed = True
                if self._errors_win:
                    try: self._errors_win.destroy()
                    except Exception: pass
                self._errors_win = None

            self._errors_win.protocol('WM_DELETE_WINDOW', _close_errors)

            # Кнопки вверху
            btn_frame = tk.Frame(self._errors_win, bg=T['bg_side'])
            btn_frame.pack(fill=tk.X, padx=8, pady=(8, 0))
            tk.Button(btn_frame, text='💾 Сохранить лог',
                      relief='flat', bg=T['btn_bg'], fg=T['fg_text'],
                      font=(T['font_fam'], T['font_sz'] - 1), padx=8, pady=3,
                      command=self._save_errors_log).pack(side=tk.LEFT, padx=(0, 4))
            tk.Button(btn_frame, text='✕ Закрыть',
                      relief='flat', bg=T['btn_bg'], fg='#f38ba8',
                      font=(T['font_fam'], T['font_sz'] - 1), padx=8, pady=3,
                      command=_close_errors).pack(side=tk.RIGHT)

            inner = tk.Frame(self._errors_win, bg=T['bg_side'])
            inner.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)
            self._errors_inner = inner  # v3.1.9: для re-open

            # v3.1.9: canvas + scrollbar вернулись, header ВНУТРИ canvas (нет пробела между header и rows)
            self._errors_canvas = tk.Canvas(inner, bg=T['bg_side'], highlightthickness=0)
            self._errors_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            self._errors_sb = tk.Scrollbar(inner, orient='vertical',
                                            command=self._errors_canvas.yview,
                                            bg=T['bg_sep'], troughcolor=T['bg_main'],
                                            width=0, bd=0, highlightthickness=0)
            self._errors_sb.pack(side=tk.RIGHT, fill=tk.Y)
            self._errors_canvas.configure(yscrollcommand=self._errors_sb.set)

            self._errors_inner = tk.Frame(self._errors_canvas, bg=T['bg_side'])
            self._errors_canvas.create_window((0, 0), window=self._errors_inner, anchor='nw')
            self._errors_inner.bind(
                '<Configure>',
                lambda e: self._errors_canvas.configure(scrollregion=self._errors_canvas.bbox('all')))

            hdr = tk.Label(self._errors_inner, text='Модели не загружены:',
                            bg=T['bg_side'], fg='#f38ba8',
                            font=(T['font_fam'], T['font_sz'] - 1, 'bold'))
            hdr.pack(anchor='w', padx=4, pady=(2, 2))
            # Mousewheel для scroll внутри canvas
            self._errors_canvas.bind('<MouseWheel>',
                lambda e: self._errors_canvas.yview_scroll(-1 * (e.delta // 120) if e.delta else -1, 'units'))

            self._errors_rows = {}
        else:
            inner = self._errors_inner  # re-open: rows идут в этот Frame
            # Пересоздать window для scrollregion update
            try:
                self._errors_canvas.configure(scrollregion=self._errors_canvas.bbox('all'))
            except Exception:
                pass

        existing = self._errors_rows
        current_idxs = {i for i, _ in failed_list}
        for idx in list(existing.keys()):
            if idx not in current_idxs:
                widgets = existing[idx]
                for w in widgets.values():
                    if hasattr(w, 'destroy'):
                        try: w.destroy()
                        except Exception: pass
                del existing[idx]

        for idx, state in failed_list:
            name = os.path.basename(state['path'])
            err = state.get('error', 'неизв.')
            text = f'✗ {name}: {err[:200]}'
            # Логируем в debug
            _dbg(f'ERROR {name}: {err}')

            if idx in existing:
                lbl = existing[idx]['label']
                lbl.config(text=text)
            else:
                row = tk.Frame(inner, bg=T['bg_side'])
                row.pack(fill=tk.X, pady=0, anchor='w')
                lbl = tk.Label(row, text=text, bg=T['bg_side'], fg='#f38ba8',
                               font=(T['font_fam'], T['font_sz'] - 2), anchor='w', wraplength=460, justify=tk.LEFT)
                lbl.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))
                close = tk.Button(row, text='✕', relief='flat',
                                   bg=T['bg_side'], fg='#f38ba8',
                                   font=(T['font_fam'], T['font_sz'] - 2),
                                   padx=4, pady=0, bd=0, cursor='hand2',
                                   command=lambda i=idx: self._close_model(i))
                close.pack(side=tk.RIGHT)
                existing[idx] = {'row': row, 'label': lbl}

        try:
            self._errors_canvas.configure(scrollregion=self._errors_canvas.bbox('all'))
        except Exception:
            pass

    def _show_errors_popup(self):
        """Публичный метод — re-open popup ошибок (сбрасывает _errors_dismissed)."""
        self._errors_dismissed = False
        # Берём текущий список failed моделей из _models
        failed = [(i, s) for i, s in self._models.items() if s.get('status') == 'failed']
        if not failed:
            self._status('Нет ошибок загрузки', T['fg_muted'] if hasattr(self, '_T') else '#888')
            return
        # Если старое окно ещё живо — закрываем, чтобы _update_errors_popup создал новое
        old_win = getattr(self, '_errors_win', None)
        if old_win is not None:
            try: old_win.destroy()
            except Exception: pass
        self._errors_win = None
        self._update_errors_popup(failed, force=True)  # кнопка открытия — игнорировать showed_errors

    def _save_errors_log(self):
        """v2.7: сохранить все failed модели в текстовый лог-файл."""
        from tkinter import filedialog
        try:
            failed = [(i, s) for i, s in self._models.items() if s.get('status') == 'failed']
            if not failed:
                messagebox.showinfo('Лог ошибок', 'Нет failed моделей для сохранения.')
                return
            default_name = f'p3d_errors_{datetime.datetime.now().strftime("%Y%m%d_%H%M%S")}.txt'
            path = filedialog.asksaveasfilename(
                defaultextension='.txt',
                initialfile=default_name,
                title='Сохранить лог ошибок',
                filetypes=[('Text files', '*.txt'), ('All files', '*.*')])
            if not path:
                return
            with open(path, 'w', encoding='utf-8') as f:
                f.write(f'=== P3D Viewer {VERSION} — лог ошибок загрузки ===\n')
                f.write(f'Дата: {datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\n')
                f.write(f'Всего ошибок: {len(failed)}\n\n')
                for idx, state in failed:
                    name = os.path.basename(state.get('path', '?'))
                    err = state.get('error', 'неизв.')
                    f.write(f'[{idx}] {name}\n    {err}\n\n')
            messagebox.showinfo('Лог сохранён', f'Сохранено {len(failed)} записей в:\n{path}')
            _dbg(f'errors log saved to: {path}')
        except Exception as e:
            messagebox.showerror('Ошибка', f'Не удалось сохранить лог:\n{e}')

    def _cancel_one_load(self, idx):
        """v2.3: отменить загрузку конкретной модели."""
        info = self._load_procs.pop(idx, None)
        if info:
            try:
                proc = info['proc']
                if proc.poll() is None:
                    proc.terminate()
            except Exception as e:
                _dbg(f'cancel_one: terminate failed: {e}')
        if idx in self._models:
            self._models[idx]['status'] = 'cancelled'
            self._models[idx]['progress_msg'] = 'Отменено'
        # Если больше нет загрузок — снимаем блокировку
        if not self._load_procs:
            self._stop_spinner()
            self._set_loading(False)
            self._load_cancelled = False
        self._update_models_panel()
        self._status('Загрузка отменена', '#f9e2af')

    # ── Загрузка P3D ─────────────────────────────────────────────────────────

    _SPINNER_FRAMES = ['⠋','⠙','⠹','⠸','⠼','⠴','⠦','⠧','⠇','⠏']

    def _start_spinner(self):
        self._spinner_running = True
        self._spinner_idx = 0
        self._spin_tick()

    def _spin_tick(self):
        if not self._spinner_running:
            return
        frame = self._SPINNER_FRAMES[self._spinner_idx % len(self._SPINNER_FRAMES)]
        self._status(f'Загрузка {frame}', '#f9e2af')
        self._spinner_idx += 1
        if self._spinner_idx > 1200:  # 120 sec timeout
            self._stop_spinner()
            self._load_cancelled = True
            self._set_loading(False)
            self._status('Загрузка заняла слишком долго — возможно файл повреждён', '#f38ba8')
            return
        self.root.after(100, self._spin_tick)

    def _stop_spinner(self):
        self._spinner_running = False

    def _set_loading(self, loading: bool):
        self._loading = loading
        state = 'disabled' if loading else 'normal'
        for btn in self._open_buttons:
            try:
                btn.config(state=state)
            except Exception:
                pass

    def _load_p3d(self, path):
        """v2.3: запускает загрузку P3D в ОТДЕЛЬНОМ ПРОЦЕССЕ.

        Это даёт:
        - Настоящий параллелизм: несколько моделей грузятся одновременно (DLL state изолирован).
        - Жёсткий cancel через process.terminate() — флаг _load_cancelled только сигнал UI.
        - Не блокирует ни main thread, ни worker thread.

        Если модель уже загружена — переключиться на неё без перезапуска процесса.
        """
        # Если модель уже загружена — переключиться на неё
        norm_path = os.path.normcase(os.path.abspath(path))
        for idx, state in self._models.items():
            existing = state.get('path', '')
            if existing and os.path.normcase(os.path.abspath(existing)) == norm_path:
                self._switch_to_model(idx)
                return

        self._load_cancelled = False
        idx = self._next_idx
        self._next_idx += 1

        # Сразу загружаем конфиг, чтобы знать uv_limit/edge_mult
        model_name = os.path.splitext(os.path.basename(path))[0]
        cfg = _load_item_config(model_name)
        cfg_uv_limit  = cfg.get('uv_threshold',  1.5) if cfg else 1.5
        cfg_edge_mult = cfg.get('edge_threshold', 50)  if cfg else 50

        self._models[idx] = {
            'status': 'loading', 'path': path,
            'cfg': cfg, 'model_name': model_name,
            'cfg_uv_limit': cfg_uv_limit, 'cfg_edge_mult': float(cfg_edge_mult),
            'tex_data': {},  # будет заполнено в _load_p3d_finish
            'progress_msg': 'Запуск...',
        }
        self._update_models_panel()
        self._set_loading(True)
        self._start_spinner()
        self._spawn_p3d_subprocess(idx, path, cfg_uv_limit, cfg_edge_mult)

    def _spawn_p3d_subprocess(self, idx, path, uv_limit, edge_mult):
        """Запускает _p3d_subprocess_main в отдельном python.exe процессе."""
        # Уникальный префикс для tmp-файлов результата
        out_prefix = os.path.join(
            tempfile.gettempdir(),
            f'p3d_v23_{os.getpid()}_{idx}_{int(time.time()*1000) % 1000000}'
        )
        cmd_in = {
            'cmd': 'parse',
            'p3d_path': path,
            'uv_limit': uv_limit,
            'edge_mult': edge_mult,
            'out_prefix': out_prefix,
        }
        try:
            # Не передаём __file__ — PyInstaller frozen mode сам знает entry script.
            # Передаём только --p3d-subproc. Это работает и для python.exe, и для EXE.
            proc = subprocess.Popen(
                [sys.executable, '--p3d-subproc'],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=SCRIPT_DIR,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
        except Exception as e:
            _dbg(f'subprocess.Popen failed: {e}')
            self._load_p3d_error(path, idx, e)
            return

        try:
            proc.stdin.write(json.dumps(cmd_in).encode('utf-8'))
            proc.stdin.close()
        except Exception as e:
            _dbg(f'subprocess stdin write failed: {e}')
            proc.terminate()
            self._load_p3d_error(path, idx, e)
            return

        self._load_procs[idx] = {
            'proc': proc,
            'out_prefix': out_prefix,
            'path': path,
            'started_at': time.time(),
        }
        # Запускаем опрос завершения
        if self._load_poll_after is None:
            self._poll_load_progress()

    def _load_paths_parallel(self, paths, max_concurrent=3):
        """v0.0.5: запускает max_concurrent subprocess'ов параллельно.
        Когда один завершается — берёт следующий из queue. Main UI не блокируется."""
        import threading as _th
        if not paths:
            return
        self._load_pending_paths = list(paths)
        self._load_concurrency_limit = _th.Semaphore(max_concurrent)
        for _ in range(min(max_concurrent, len(paths))):
            self._launch_next_path()

    def _launch_next_path(self):
        """v0.0.5: берёт следующий путь из queue и запускает subprocess (если есть слот)."""
        if not getattr(self, '_load_pending_paths', None):
            return
        if not self._load_pending_paths:
            return
        if not self._load_concurrency_limit.acquire(blocking=False):
            return
        path = self._load_pending_paths.pop(0)
        try:
            self._load_p3d(path)
        except Exception as e:
            _dbg(f'_launch_next_path error: {e}')
            try:
                self._load_concurrency_limit.release()
            except Exception:
                pass

    def _on_subprocess_finished(self):
        """v0.0.5: после завершения subprocess — release slot + launch next."""
        try:
            self._load_concurrency_limit.release()
        except Exception:
            pass
        try:
            self._launch_next_path()
        except Exception:
            pass

    def _poll_load_progress(self):
        """Периодически проверяет завершение subprocess-загрузок.

        Применяется в main thread — все GL-операции здесь безопасны.
        Несколько моделей могут завершиться одновременно.
        """
        finished = []
        for idx, info in list(self._load_procs.items()):
            proc = info['proc']
            # Если процесс уже завершился — забираем результат
            if proc.poll() is not None:
                finished.append(idx)
                continue
            # Обновляем прогресс-сообщение пока процесс идёт
            elapsed = time.time() - info['started_at']
            if idx in self._models:
                state = self._models[idx]
                state['progress_msg'] = f'Парсинг... {elapsed:.1f}с'
                state['progress_pct'] = min(95, int(elapsed * 5))  # условный процент

        # Обрабатываем завершённые
        for idx in finished:
            info = self._load_procs.pop(idx, None)
            if not info:
                continue
            proc = info['proc']
            try:
                stdout_data, stderr_data = proc.communicate(timeout=5)
            except Exception as e:
                _dbg(f'proc.communicate failed: {e}')
                self._load_p3d_error(info['path'], idx, e)
                continue
            try:
                stdout_text = stdout_data.decode('utf-8', errors='replace').strip()
                # Последняя строка — наш JSON-результат
                # (subprocess может напечатать что-то ещё до этого)
                last_line = stdout_text.splitlines()[-1] if stdout_text else '{}'
                resp = json.loads(last_line)
            except Exception as e:
                _dbg(f'subprocess stdout parse failed: {e}; stdout={stdout_text[:200]!r}; stderr={stderr_data[:200]!r}')
                self._load_p3d_error(info['path'], idx, RuntimeError(f'subprocess parse: {e}'))
                continue

            if not resp.get('ok'):
                err = RuntimeError(resp.get('error', 'unknown subprocess error'))
                self._load_p3d_error(info['path'], idx, err)
                continue

            result_json = resp.get('result_json')
            if not result_json or not os.path.isfile(result_json):
                self._load_p3d_error(info['path'], idx, RuntimeError('result json missing'))
                continue
            try:
                with open(result_json, 'r', encoding='utf-8') as f:
                    meta = json.load(f)
            except Exception as e:
                self._load_p3d_error(info['path'], idx, e)
                continue
            # v2.7.2: применяем apply через after — даёт UI обновляться между моделями
            # v2.8 fix: НЕ удаляем .npy файлы здесь! Apply запускается через after(0) — между
            # unlink и apply файлы бы удалились и apply получил бы None (race condition).
            # Файлы лежат в TEMP, OS почистит позже.
            diag = meta.get('diagnostics', {}) or {}
            if diag:
                _dbg(f'parse diag {os.path.basename(info["path"])}: dll_nf={diag.get("dll_nf")} good={diag.get("good_tris")} base_limit={diag.get("base_limit", 0):.4f} robust={diag.get("robust_limit", 0):.4f} em={diag.get("edge_mult")} uv={diag.get("uv_limit")}')
                dbg_cat('parse', f'{os.path.basename(info["path"])}: nf={diag.get("dll_nf")} good={diag.get("good_tris")} base={diag.get("base_limit", 0):.4f} robust={diag.get("robust_limit", 0):.4f}')
            self.root.after(0, lambda i=idx, p=info['path'], m=dict(meta), rp=result_json: self._load_p3d_apply(i, p, m, result_json_path=rp))

        # Обновляем UI панель моделей (статусы и прогресс)
        if finished or self._load_procs:
            try:
                self._update_models_panel()
            except Exception:
                pass

        # Продолжаем опрос, пока есть живые процессы
        # v0.0.5: вызываем _on_subprocess_finished для каждого завершённого
        for _ in finished:
            try:
                self._on_subprocess_finished()
            except Exception:
                pass
        if self._load_procs:
            self._load_poll_after = self.root.after(150, self._poll_load_progress)
        else:
            self._load_poll_after = None

    def _load_p3d_apply(self, idx, path, meta, result_json_path=None):
        """Загружает результат subprocess в self._models[idx] и применяет.
        v2.8: result_json_path — путь к result.json subprocess. Удаляется в конце вместе с .npy.
        """
        try:
            files = meta.get('files', {})

            def _load_npy(p):
                if not p or not os.path.isfile(p):
                    return None
                try:
                    return np.load(p, allow_pickle=False)
                except Exception:
                    return None

            pts  = _load_npy(files.get('pts'))
            nrms = _load_npy(files.get('nrms'))
            tris = _load_npy(files.get('tris'))
            uvs  = _load_npy(files.get('uvs'))
            _dbg(f'subproc files for {os.path.basename(path)}: pts={files.get("pts")!r} tris={files.get("tris")!r} uvs={files.get("uvs")!r}')
            dbg_cat('subproc', f'{os.path.basename(path)}: pts={files.get("pts")!r} tris={files.get("tris")!r} uvs={files.get("uvs")!r}')
            mat_tris_files = files.get('mat_tris') or []
            mat_tris = []
            for pf in mat_tris_files:
                arr = _load_npy(pf)
                if arr is not None:
                    mat_tris.append(arr)

            # Proxies: могут быть в pickle (с list/dict) или npy (object array)
            proxies = None
            proxies_p = files.get('proxies')
            if proxies_p and proxies_p.endswith('.pkl'):
                try:
                    import pickle as _pickle
                    with open(proxies_p, 'rb') as _pf:
                        proxies = _pickle.load(_pf)
                except Exception as e:
                    _dbg(f'proxy pickle load failed: {e}')
            elif proxies_p and proxies_p.endswith('.npy'):
                try:
                    arr = np.load(proxies_p, allow_pickle=True)
                    proxies = arr.tolist() if hasattr(arr, 'tolist') else None
                except Exception:
                    pass

            # mat_groups: пары (path, tris_list). subprocess даёт нам только tris arrays,
            # а имена материалов — из meta['mat_paths']
            mat_paths = meta.get('mat_paths') or []
            mat_groups = []
            for i, mp in enumerate(mat_paths):
                if i < len(mat_tris):
                    mat_groups.append((mp, mat_tris[i].tolist() if hasattr(mat_tris[i], 'tolist') else list(mat_tris[i])))
                else:
                    mat_groups.append((mp, []))

            # Загружаем extra pickle с face_map/mat_list/face_mats/base_limit/best_tris/pts_arr/uvs_arr.
            # Viewport._recompute_mats и _update_mat_panel требуют эти ключи в raw.
            extra = {}
            extra_p = files.get('extra')
            if extra_p and os.path.isfile(extra_p):
                try:
                    import pickle as _pickle_e
                    with open(extra_p, 'rb') as _ef:
                        extra = _pickle_e.load(_ef)
                except Exception as _ee:
                    _dbg(f'extra pickle load failed: {_ee}')

            # raw — собираем полную структуру, совместимую с v2.2 кодом Viewport
            pts_t  = pts.tolist()  if pts  is not None else []
            nrms_t = nrms.tolist() if nrms is not None else []
            tris_t = tris.tolist() if tris is not None else []
            uvs_t  = uvs.tolist()  if uvs  is not None else None

            # v2.7.5: диагностика — логируем размеры массивов из subprocess (raw будет ниже)
            _dbg(f'apply mesh: {os.path.basename(path)} pts={len(pts_t)} tris={len(tris_t)} uvs={len(uvs_t or [])} nrms={len(nrms_t)}')
            dbg_cat('apply', f'{os.path.basename(path)}: pts={len(pts_t)} tris={len(tris_t)} uvs={len(uvs_t or [])} nrms={len(nrms_t)}')
            # v3.0: UV stats в MAIN process (subproc не доходит до debuglog)
            if uvs_t:
                us = [u for u, v in uvs_t]; vs = [v for u, v in uvs_t]
                u_uniq = len(set(round(u, 3) for u in us)); v_uniq = len(set(round(v, 3) for v in vs))
                dbg_cat('uv', f'{os.path.basename(path)}: U=[{min(us):.4f},{max(us):.4f}] V=[{min(vs):.4f},{max(vs):.4f}] uniq_U={u_uniq} uniq_V={v_uniq} pts={len(pts_t)} tris={len(tris_t)}')
                # Также в dll_sections.txt (лог UV диагностики)
                try:
                    _uv_log_path = os.path.join(_CACHE_DIR, 'uv_diag.txt')
                    with open(_uv_log_path, 'a', encoding='utf-8') as _ulf:
                        _ulf.write(f'{os.path.basename(path)}: nf={len(tris_t)} pts={len(pts_t)} uvs={len(uvs_t)} '
                                   f'U=[{min(us):.4f},{max(us):.4f}] V=[{min(vs):.4f},{max(vs):.4f}] '
                                   f'uniq_U={u_uniq} uniq_V={v_uniq}\n')
                        if len(uvs_t) > 0:
                            _ulf.write(f'  UV first 12: {uvs_t[:12]}\n')
                            _ulf.write(f'  UV last  12: {uvs_t[-12:]}\n')
                except Exception:
                    pass
            raw = {
                # Subprocess-данные
                'initial_split': meta.get('raw_initial_split', 0),
                'proxies': proxies or [],
                # Viewport-совместимые поля (из extra pickle)
                'face_map':   extra.get('face_map', []),
                'mat_list':   extra.get('mat_list', mat_paths),
                'face_mats':  extra.get('face_mats', []),
                'base_limit': extra.get('base_limit', 1.0),
                'robust_limit': extra.get('robust_limit', extra.get('base_limit', 1.0)),  # v2.8: подхватываем из extra
                # v2.8 BUGFIX: раньше тут было tris_t (184 отфильтрованных), из-за чего
                # _recompute_mats не мог добавить грани при увеличении порогов.
                # Теперь берём ВСЕ 228 best_tris из extra pickle.
                'best_tris':  extra.get('best_tris', tris_t),
                'pts':        pts_t,   # list-of-tuples (для Viewport.load_mesh)
                'nrms':       nrms_t,  # list-of-tuples
                'uvs':        uvs_t,   # list-of-tuples (или None)
                'pts_arr':    pts,     # numpy (для быстрых операций)
                'uvs_arr':    uvs,     # numpy (или None)
                'nf':         len(tris_t),
                # v2.3 fix: используем max(mat_groups, mat_list). Если mat_groups=1 пустой,
                # а mat_list=2 — split slider должен работать по mat_list (это даёт пользователю
                # возможность вручную разделить face'ы по материалам для моделей без named selections).
                'n_mats':     max(len(mat_groups), len(extra.get('mat_list') or mat_paths)),
                'uv_limit':   self._models[idx].get('cfg_uv_limit', 1.5),
                'edge_mult':  float(self._models[idx].get('cfg_edge_mult', 50)),
            }

            cfg = self._models[idx].get('cfg')
            cfg_uv_limit  = self._models[idx].get('cfg_uv_limit', 1.5)
            cfg_edge_mult = self._models[idx].get('cfg_edge_mult', 50.0)

            cur_split = raw.get('initial_split', 0)
            cur_split2 = 0
            cur_swap = False
            config_loaded_name = None
            model_name = self._models[idx].get('model_name', os.path.splitext(os.path.basename(path))[0])

            # v2.8: детальная диагностика raw данных (после raw создан)
            _dbg(f'raw check {os.path.basename(path)}: best_tris={len(raw.get("best_tris", []))} pts={len(raw.get("pts", []))} base_limit={raw.get("base_limit", 0):.4f} robust={raw.get("robust_limit", 0):.4f}')

            if cfg is not None:
                cur_split  = cfg.get('split1', cur_split)
                cur_split2 = cfg.get('split2', 0)
                cur_swap   = cfg.get('swap', False)
                config_loaded_name = model_name

            # v2.3: cur_splits из config (массив для N материалов)
            cur_splits = []
            if cfg is not None:
                cfg_splits = cfg.get('splits', [])
                if cfg_splits:
                    cur_splits = sorted([int(s) for s in cfg_splits if s and s > 0])
                # Backward compat: split1/split2 если splits нет
                if not cur_splits:
                    if cur_split and cur_split > 0:
                        cur_splits.append(int(cur_split))
                    if cur_split2 and cur_split2 > 0:
                        cur_splits.append(int(cur_split2))
                    cur_splits.sort()

            state = {
                'status': 'ready', 'path': path,
                'pts': pts_t, 'uvs': uvs_t, 'nrms': nrms_t, 'tris': tris_t,
                'mat_groups': mat_groups, 'raw': raw,
                # v2.8: трекаем сколько граней реально доступно для пересчёта
                'best_tris_count': len(raw.get('best_tris', [])),
                'pts_count_in_raw': len(raw.get('pts', [])),
                'cur_split': cur_split, 'cur_split2': cur_split2,
                'cur_splits': cur_splits,  # v2.3: список для N материалов
                'cur_swap': cur_swap,
                'cur_uv_limit': cfg_uv_limit, 'cur_edge_mult': cfg_edge_mult,
                'config_loaded_name': config_loaded_name,
                'tex_hint': meta.get('tex_hint', ''),
                'cfg': cfg, 'model_name': model_name,
                'tex_data': {},  # заполняется в _resolve_textures_for
                'progress_msg': 'Готово',
                'progress_pct': 100,
            }
            self._models[idx] = state

            # v2.7.4: SILENT LOAD — убираем ВСЕ тяжёлые операции из apply.
            # Auto-split и proxy augment делаются LAZY при первом _switch_to_model.
            # Если модель никогда не просматривают — она просто лежит в self._models.

            # Остановить спиннер, если больше нет загрузок
            if not self._load_procs:
                self._stop_spinner()
                self._set_loading(False)

            # v2.7.3: SILENT LOAD — НЕ применяем текстуры и не загружаем в viewport здесь.
            if self._active_idx is None:
                self._switch_to_model(idx)
            else:
                self._update_models_panel()

        except Exception as e:
            _dbg(f'_load_p3d_apply failed: {e}')
            self._load_p3d_error(path, idx, e)
        finally:
            # v2.8: cleanup TEMP-файлов ПОСЛЕ apply (после _load_npy).
            # Раньше unlink был в _poll_load_progress ДО after(0) — race condition.
            if result_json_path and os.path.isfile(result_json_path):
                try:
                    os.unlink(result_json_path)
                    for k, p in meta.get('files', {}).items():
                        if isinstance(p, str) and os.path.isfile(p):
                            os.unlink(p)
                        elif isinstance(p, list):
                            for pp in p:
                                if isinstance(pp, str) and os.path.isfile(pp):
                                    os.unlink(pp)
                except Exception:
                    pass

    def _load_p3d_error(self, path, idx, e):
        """v2.3: ошибка загрузки — корректно убирает модель и снимает loading-блок."""
        try:
            self._stop_spinner()
            if idx in self._models:
                # помечаем как failed чтобы было видно в UI
                self._models[idx]['status'] = 'failed'
                self._models[idx]['progress_msg'] = f'Ошибка: {e}'
                self._models[idx]['error'] = str(e)
            self._update_models_panel()
            self._status(f'Ошибка загрузки: {e}', '#f38ba8')
        finally:
            # Если это была последняя активная загрузка — снимаем блокировку кнопок
            if not self._load_procs:
                self._set_loading(False)
                self._load_cancelled = False

    def cancel_all_loads(self):
        """v2.3: жёсткая отмена всех текущих загрузок — terminate subprocess."""
        for idx, info in list(self._load_procs.items()):
            try:
                proc = info['proc']
                if proc.poll() is None:
                    proc.terminate()
            except Exception as e:
                _dbg(f'terminate failed for {idx}: {e}')
            # Удаляем запись модели со статусом loading
            if idx in self._models and self._models[idx].get('status') == 'loading':
                self._models[idx]['status'] = 'cancelled'
                self._models[idx]['progress_msg'] = 'Отменено'
        self._load_procs.clear()
        self._stop_spinner()
        self._set_loading(False)
        self._load_cancelled = False
        self._update_models_panel()
        self._status('Загрузки отменены', '#f9e2af')

    # ── Авто-загрузка текстур по материалам ──────────────────────────────────

    def _build_paa_index(self, p3d_path):
        """Словарь {имя_файла_lower: абс_путь} для всех PAA рядом с p3d_path.
        Результат кэшируется по mtime корневой папки — повторные загрузки мгновенны."""
        p3d_dir = os.path.dirname(os.path.abspath(p3d_path))

        scan_roots = []
        cur = p3d_dir
        for _ in range(5):
            if cur not in scan_roots:
                scan_roots.append(cur)
            parent = os.path.dirname(cur)
            if parent == cur: break
            cur = parent

        # Ключ кэша: пути + mtime каждой корневой папки
        mtime_parts = []
        for r in scan_roots:
            try:
                mtime_parts.append(f'{r}:{int(os.stat(r).st_mtime_ns)}')
            except Exception:
                mtime_parts.append(r)
        scan_key = '|'.join(mtime_parts)
        cache_p  = _paa_index_cache_path(scan_key)

        if os.path.exists(cache_p):
            try:
                with open(cache_p, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass

        # Сканируем файловую систему
        index = {}
        for root_dir in scan_roots:
            try:
                for dirpath, dirnames, filenames in os.walk(root_dir):
                    rel   = os.path.relpath(dirpath, root_dir)
                    depth = 0 if rel == '.' else len(rel.split(os.sep))
                    if depth >= 4:
                        dirnames.clear(); continue
                    for fn in filenames:
                        if fn.lower().endswith('.paa'):
                            key = fn.lower()
                            if key not in index:
                                index[key] = os.path.join(dirpath, fn)
            except PermissionError:
                pass

        try:
            with open(cache_p, 'w', encoding='utf-8') as f:
                json.dump(index, f)
        except Exception:
            pass
        return index

    def _build_rvmat_index(self, p3d_path):
        """То же самое что _build_paa_index, но для .rvmat файлов."""
        index = {}
        p3d_dir = os.path.dirname(os.path.abspath(p3d_path))
        scan_roots = set()
        cur = p3d_dir
        for _ in range(5):
            scan_roots.add(cur)
            parent = os.path.dirname(cur)
            if parent == cur: break
            cur = parent
        for root_dir in scan_roots:
            try:
                for dirpath, dirnames, filenames in os.walk(root_dir):
                    rel = os.path.relpath(dirpath, root_dir)
                    depth = 0 if rel == '.' else len(rel.split(os.sep))
                    if depth >= 4:
                        dirnames.clear(); continue
                    for fn in filenames:
                        if fn.lower().endswith('.rvmat'):
                            key = fn.lower()
                            if key not in index:
                                index[key] = os.path.join(dirpath, fn)
            except PermissionError:
                pass
        return index

    def _resolve_group_tex(self, paa_index, rvmat_index, ref_path, mat_path):
        """Найти (diff_path, nrm_path) для одной группы.
        Стратегия:
          1. Если mat_path — RVMAT → распарсить → найти диффуз+нормаль по индексу
          2. Если mat_path — PAA  → найти по индексу (basename)
          3. Нормаль: замена _co → _nohq в индексе
        """
        if not mat_path or not mat_path.strip():
            return None, None
        mat_path = mat_path.replace('\\', os.sep).replace('/', os.sep)
        ext = os.path.splitext(mat_path)[1].lower()
        diff = nrm = None
        try:
            if ext == '.rvmat':
                # Поиск RVMAT: 1) прямой путь  2) rvmat_index по имени  3) find_texture_near
                rvmat_name = os.path.basename(mat_path).lower()
                if os.path.isfile(mat_path):
                    rvmat = mat_path
                elif rvmat_name in rvmat_index:
                    rvmat = rvmat_index[rvmat_name]
                else:
                    rvmat = find_texture_near(ref_path, mat_path)
                if rvmat and os.path.isfile(rvmat):
                    parsed = parse_rvmat(rvmat)
                    if 'diffuse' in parsed:
                        dp = os.path.basename(
                            parsed['diffuse'].replace('\\', os.sep)).lower()
                        diff = paa_index.get(dp)
                        if not diff:
                            diff = find_texture_near(rvmat, parsed['diffuse'])
                    if 'normal' in parsed:
                        np_ = os.path.basename(
                            parsed['normal'].replace('\\', os.sep)).lower()
                        nrm = paa_index.get(np_)
                        if not nrm:
                            nrm = find_texture_near(rvmat, parsed['normal'])
            elif ext == '.paa':
                basename = os.path.basename(mat_path).lower()
                diff = paa_index.get(basename)
                if not diff:
                    diff = (mat_path if os.path.isfile(mat_path)
                            else find_texture_near(ref_path, mat_path))
                if diff:
                    nrm_name = basename.replace('_co.paa', '_nohq.paa')
                    if nrm_name != basename:
                        nrm = paa_index.get(nrm_name)
        except Exception:
            pass
        return diff, nrm

    def _auto_tex(self, p3d_path, tex_hint, mat_groups):
        """Legacy wrapper — оставлен для совместимости.
        v2.3: вся текстурная логика переехала в _resolve_textures_for (per-model).
        Этот метод теперь просто запускает _resolve_textures_for для активной модели.
        """
        if not USE_GL: return
        if self._active_idx is not None:
            self._resolve_textures_for(self._active_idx)

    def _resolve_textures_for(self, idx):
        """v2.3: разрешает текстуры для модели idx, КЭШИРУЕТ в _models[idx]['tex_data'],
        и (если модель активна) пушит в viewport.

        Структура tex_data:
          {
            'group_paths': {gi: {'diff': path|None, 'nrm': path|None}},
            'decoded_diff': {path: rgba_or_None},
            'decoded_nrm':  {path: rgba_or_None},
            'fb_diff': rgba_or_None, 'fb_nrm': rgba_or_None,
            'paa_index_size': int,
          }

        Это позволяет при _switch_to_model(idx) восстановить текстуры из кэша
        без повторного парсинга PAA-файлов и без перезаписи текстур другой модели.
        """
        if not USE_GL: return
        if idx not in self._models:
            return
        state = self._models[idx]
        if state.get('status') != 'ready':
            return
        if state.get('tex_data', {}).get('populated'):
            # Уже разрешены — сразу пушим в viewport если активна
            if idx == self._active_idx:
                self._bind_textures_for_model(idx)
            return

        p3d_path   = state['path']
        mat_groups = state['mat_groups']
        tex_hint   = state.get('tex_hint', '')

        # v2.7.3: UI feedback
        self._status(f'Разрешение текстур: {os.path.basename(p3d_path)}...', '#f9e2af')
        self.root.update_idletasks()

        paa_index   = self._build_paa_index(p3d_path)
        rvmat_index = self._build_rvmat_index(p3d_path)
        # v2.7.5: BUGFIX — ранний return удалял DEAD CODE и не давал populated=True.
        # Теперь код продолжается и populate=True ставится в конце.

        # ── Шаг 1: разрешаем пути для всех групп ─────────────────────────────
        group_paths = {}  # gi -> {'diff': path|None, 'nrm': path|None}
        tasks = {}        # path -> set of kinds ('diff', 'nrm')
        for gi, (mat_path, _) in enumerate(mat_groups):
            diff, nrm = self._resolve_group_tex(paa_index, rvmat_index,
                                                p3d_path, mat_path)
            group_paths[gi] = {'diff': diff, 'nrm': nrm}
            for kind, p in (('diff', diff), ('nrm', nrm)):
                if p:
                    tasks.setdefault(p, set()).add(kind)

        # ── Шаг 2: параллельное декодирование ────────────────────────────────
        decoded_diff = {}
        decoded_nrm  = {}
        n_tasks = len(tasks)
        if n_tasks > 0:
            self._status(f'Декодирование {n_tasks} текстур...', '#f9e2af')
            self.root.update_idletasks()
            with ThreadPoolExecutor(max_workers=min(4, n_tasks)) as ex:
                # Submit один раз на каждый path
                fut_to_kind = {}
                for p, kinds in tasks.items():
                    if 'diff' in kinds:
                        fut = ex.submit(read_paa_cached, p)
                        fut_to_kind[fut] = ('diff', p)
                    if 'nrm' in kinds:
                        fut = ex.submit(read_paa_cached, p)
                        fut_to_kind[fut] = ('nrm', p)
                for fut in as_completed(fut_to_kind):
                    kind, p = fut_to_kind[fut]
                    try:
                        arr = fut.result()
                        if kind == 'diff':
                            decoded_diff[p] = arr
                        else:
                            decoded_nrm[p] = arr
                    except Exception:
                        if kind == 'diff':
                            decoded_diff[p] = None
                        else:
                            decoded_nrm[p] = None

        # ── Шаг 3: fallback для групп без текстуры ───────────────────────────
        fb_diff_rgba = None
        fb_nrm_rgba  = None
        unloaded_paths = [gi for gi, paths in group_paths.items() if not paths['diff']]
        if unloaded_paths and paa_index:
            fb_diff = fb_nrm = None
            if tex_hint:
                d, n = self._resolve_group_tex(paa_index, rvmat_index,
                                               p3d_path, tex_hint)
                if d: fb_diff, fb_nrm = d, n
            if not fb_diff:
                for k, v in paa_index.items():
                    if k.endswith('_co.paa'):
                        fb_diff = v
                        fb_nrm  = paa_index.get(k.replace('_co.paa', '_nohq.paa'))
                        break
            if fb_diff:
                fb_diff_rgba = decoded_diff.get(fb_diff) or read_paa_cached(fb_diff) if False else (decoded_diff.get(fb_diff))
                if fb_diff_rgba is None:
                    try:
                        fb_diff_rgba = read_paa_cached(fb_diff)
                    except Exception:
                        fb_diff_rgba = None
                if fb_nrm:
                    fb_nrm_rgba = decoded_nrm.get(fb_nrm)
                    if fb_nrm_rgba is None:
                        try:
                            fb_nrm_rgba = read_paa_cached(fb_nrm)
                        except Exception:
                            fb_nrm_rgba = None

        # ── Шаг 4: сохранить в кэш модели ───────────────────────────────────
        state['tex_data'] = {
            'group_paths': group_paths,
            'decoded_diff': decoded_diff,
            'decoded_nrm':  decoded_nrm,
            'fb_diff_rgba': fb_diff_rgba,
            'fb_nrm_rgba':  fb_nrm_rgba,
            'paa_index_size': len(paa_index),
            'populated': True,
        }

        # ── Шаг 5: если модель активна — пушим в viewport ───────────────────
        if idx == self._active_idx:
            self._bind_textures_for_model(idx)

    def _bind_textures_for_model(self, idx):
        """v2.3: загружает текстуры модели idx в viewport из её кэша.
        Вызывается из _switch_to_model и из _resolve_textures_for (когда модель активна).
        """
        if not USE_GL: return
        if idx not in self._models:
            return
        state = self._models[idx]
        td = state.get('tex_data') or {}
        if not td.get('populated'):
            # Если текстуры ещё не разрешены — запускаем resolve
            self._resolve_textures_for(idx)
            return

        group_paths = td.get('group_paths', {})
        decoded_diff = td.get('decoded_diff', {})
        decoded_nrm  = td.get('decoded_nrm', {})
        fb_diff_rgba = td.get('fb_diff_rgba')
        fb_nrm_rgba  = td.get('fb_nrm_rgba')

        loaded_d = loaded_n = 0
        # ── Привязка диффуз + нормаль для каждой группы ─────────────────────
        for gi, paths in group_paths.items():
            diff_p = paths.get('diff')
            nrm_p  = paths.get('nrm')
            if diff_p and diff_p in decoded_diff and decoded_diff[diff_p] is not None:
                try:
                    self.viewport.load_group_texture(gi, decoded_diff[diff_p])
                    loaded_d += 1
                except Exception:
                    pass
            if nrm_p and nrm_p in decoded_nrm and decoded_nrm[nrm_p] is not None:
                try:
                    self.viewport.load_group_normal(gi, decoded_nrm[nrm_p])
                    loaded_n += 1
                except Exception:
                    pass

        # ── Fallback для групп, у которых нет своей диффузной текстуры ──────
        unloaded_groups = [gi for gi, g in enumerate(self.viewport.mesh_groups)
                            if not g['has_tex']]
        if unloaded_groups and fb_diff_rgba is not None:
            for gi in unloaded_groups:
                try:
                    self.viewport.load_group_texture(gi, fb_diff_rgba)
                    if fb_nrm_rgba is not None:
                        self.viewport.load_group_normal(gi, fb_nrm_rgba)
                    loaded_d += 1
                except Exception:
                    pass

        # ── Обновить UI индикаторы ──────────────────────────────────────────
        try:
            has_d = any(g['has_tex'] for g in self.viewport.mesh_groups)
            has_n = any(g['has_nrm'] for g in self.viewport.mesh_groups)
            self._ind(self._diff_ind, has_d)
            self._ind(self._nrm_ind,  has_n)
            n_total  = len(self.viewport.mesh_groups)
            n_with_d = sum(1 for g in self.viewport.mesh_groups if g['has_tex'])
            n_with_n = sum(1 for g in self.viewport.mesh_groups if g['has_nrm'])
            n_idx    = td.get('paa_index_size', 0)
            if has_d:
                msg = f'✔ {n_with_d}/{n_total} мат.  [{n_idx} paa]'
                if has_n: msg += f'  +{n_with_n} норм.'
                self._status(msg, '#a6e3a1')
            else:
                self._status(f'Текстуры не найдены  [{n_idx} paa в индексе]', '#f9e2af')
        except Exception:
            pass

    # ── Загрузка текстур вручную ─────────────────────────────────────────────

    def _load_paa(self, path, silent=False):
        try:
            self._status('Диффуз...', '#f9e2af'); self.root.update()
            rgba = read_paa(path)
            if USE_GL: self.viewport.load_texture(rgba)
            self._ind(self._diff_ind, True)
            self._status(f'✔ {os.path.basename(path)}\n{rgba.shape[1]}×{rgba.shape[0]}', '#a6e3a1')
        except Exception as e:
            if not silent: self._status(f'PAA ошибка:\n{e}', '#f38ba8')

    def _load_nrm(self, path, silent=False):
        try:
            self._status('Нормаль...', '#f9e2af'); self.root.update()
            rgba = read_paa(path)
            if USE_GL: self.viewport.load_normal_map(rgba)
            self._ind(self._nrm_ind, True)
            self._status(f'✔ Нормаль: {os.path.basename(path)}\n{rgba.shape[1]}×{rgba.shape[0]}', '#a6e3a1')
        except Exception as e:
            if not silent: self._status(f'Нормаль ошибка:\n{e}', '#f38ba8')

    def _load_rvmat(self, path, silent=False):
        try:
            mat = parse_rvmat(path)
            loaded = []
            if 'diffuse' in mat:
                tex  = mat['diffuse'].replace('\\', os.sep)
                f    = tex if os.path.isfile(tex) else find_texture_near(path, tex)
                if f: self._load_paa(f, silent=True); loaded.append('диффуз')
                elif not silent:
                    self._status(f'RVMAT: диффуз не найден\n{os.path.basename(tex)}', '#f9e2af')
            if 'normal' in mat:
                tex  = mat['normal'].replace('\\', os.sep)
                f    = tex if os.path.isfile(tex) else find_texture_near(path, tex)
                if f: self._load_nrm(f, silent=True); loaded.append('нормаль')
            if loaded: self._status(f'✔ RVMAT: {", ".join(loaded)}', '#a6e3a1')
            elif not silent: self._status('RVMAT: текстуры не найдены', '#f9e2af')
        except Exception as e:
            if not silent: self._status(f'RVMAT ошибка:\n{e}', '#f38ba8')

    def _export(self):
        if not self.tris:
            messagebox.showwarning('', 'Сначала загрузите P3D файл'); return
        default = os.path.splitext(self.current_file or 'model.p3d')[0] + '.obj'
        path = filedialog.asksaveasfilename(title='Экспорт в OBJ',
            defaultextension='.obj', initialfile=os.path.basename(default),
            filetypes=[('OBJ', '*.obj')])
        if not path: return
        try:
            out_dir  = os.path.dirname(path)
            name     = os.path.splitext(os.path.basename(path))[0]
            p3d_dir  = os.path.dirname(self.current_file) if self.current_file else out_dir
            paa_idx  = self._build_paa_index(self.current_file) if self.current_file else {}

            # Конвертируем PAA → PNG и собираем список материалов (mat_name → png_name)
            mat_png  = {}   # mat_path → png_filename
            exported_paa = 0
            if self.mat_groups and IMAGE_TO_PAA:
                self._status('Конвертация текстур...', '#f9e2af'); self.root.update()
                for mat_path, _ in self.mat_groups:
                    if not mat_path: continue
                    bname = os.path.basename(mat_path.replace('\\', os.sep)).lower()
                    ext   = os.path.splitext(bname)[1]
                    if ext not in ('.paa', '.rvmat'): continue
                    paa_src = paa_idx.get(bname) if ext == '.paa' else None
                    if ext == '.rvmat':
                        rv_idx  = self._build_rvmat_index(self.current_file)
                        rv_name = os.path.basename(mat_path.replace('\\', os.sep)).lower()
                        rv_file = rv_idx.get(rv_name)
                        if rv_file:
                            parsed = parse_rvmat(rv_file)
                            if 'diffuse' in parsed:
                                db = os.path.basename(parsed['diffuse'].replace('\\', os.sep)).lower()
                                paa_src = paa_idx.get(db)
                    if paa_src and os.path.isfile(paa_src):
                        png_name = os.path.splitext(os.path.basename(paa_src))[0] + '.png'
                        png_dst  = os.path.join(out_dir, png_name)
                        try:
                            rgba = read_paa(paa_src)
                            Image.fromarray(rgba, 'RGBA').save(png_dst)
                            mat_png[mat_path] = png_name
                            exported_paa += 1
                        except Exception:
                            pass

            # Записываем OBJ
            with open(path, 'w', encoding='utf-8') as f:
                f.write(f'# P3D Viewer {VERSION}\nmtllib {name}.mtl\n')
                for v in self.pts:  f.write(f'v  {v[0]:.6f} {v[1]:.6f} {v[2]:.6f}\n')
                for u in self.uvs:  f.write(f'vt {u[0]:.6f} {1.0-u[1]:.6f}\n')
                for n in self.nrms: f.write(f'vn {n[0]:.6f} {n[1]:.6f} {n[2]:.6f}\n')

                if self.mat_groups:
                    for gi, (mat_path, gtris) in enumerate(self.mat_groups):
                        mname = f'mat_{gi}'
                        f.write(f'o group_{gi}\nusemtl {mname}\n')
                        for t in gtris:
                            i,j,k = t[0]+1, t[1]+1, t[2]+1
                            f.write(f'f {i}/{i}/{i} {j}/{j}/{j} {k}/{k}/{k}\n')
                else:
                    f.write('usemtl default\n')
                    for t in self.tris:
                        i,j,k = t[0]+1, t[1]+1, t[2]+1
                        f.write(f'f {i}/{i}/{i} {j}/{j}/{j} {k}/{k}/{k}\n')

            # Записываем MTL
            mtl_path = os.path.splitext(path)[0] + '.mtl'
            with open(mtl_path, 'w', encoding='utf-8') as f:
                if self.mat_groups:
                    for gi, (mat_path, _) in enumerate(self.mat_groups):
                        mname   = f'mat_{gi}'
                        png_tex = mat_png.get(mat_path)
                        f.write(f'newmtl {mname}\nKa 0.2 0.2 0.2\nKd 0.8 0.8 0.8\nKs 0.1 0.1 0.1\n')
                        if png_tex:
                            f.write(f'map_Kd {png_tex}\n')
                        f.write('\n')
                else:
                    f.write('newmtl default\nKa 0.2 0.2 0.2\nKd 0.8 0.8 0.8\nKs 0.1 0.1 0.1\n')

            msg = f'Сохранено:\n{path}\n\n'
            if exported_paa:
                msg += f'Текстуры ({exported_paa} PNG) → {out_dir}\n\n'
            msg += ('MTL файл:\n'
                    '• Blender: File → Import → Wavefront (.obj)\n'
                    '• MTL подхватывается автоматически при импорте\n'
                    '• Текстуры (PNG) должны быть в той же папке что и OBJ')
            self._status(f'✔ {os.path.basename(path)}', '#a6e3a1')
            messagebox.showinfo('Готово', msg)
        except Exception as e:
            messagebox.showerror('Ошибка', str(e))

    # ── Конвертация PAA ↔ PNG ────────────────────────────────────────────────

    def _convert_paa_to_png_single(self):
        """PAA → PNG: выбрать один PAA файл."""
        path = filedialog.askopenfilename(title='Выбрать PAA файл',
            filetypes=[('PAA', '*.paa'), ('Все', '*.*')])
        if not path: return
        out = os.path.splitext(path)[0] + '.png'
        try:
            self._status('Конвертация...', '#f9e2af'); self.root.update()
            rgba = read_paa(path)
            Image.fromarray(rgba, 'RGBA').save(out)
            self._status(f'✔ {os.path.basename(out)}', '#a6e3a1')
            messagebox.showinfo('Готово', f'Сохранено:\n{out}')
        except Exception as e:
            messagebox.showerror('Ошибка', str(e))

    def _convert_paa_to_png(self):
        """PAA → PNG: все текстуры текущей модели."""
        if not self.current_file or not self.mat_groups:
            messagebox.showwarning('', 'Сначала загрузите P3D файл'); return
        out_dir = filedialog.askdirectory(title='Папка для PNG файлов')
        if not out_dir: return
        paa_idx = self._build_paa_index(self.current_file)
        done = err = 0
        self._status('Конвертация...', '#f9e2af'); self.root.update()
        seen = set()
        for mat_path, _ in self.mat_groups:
            bname = os.path.basename((mat_path or '').replace('\\', os.sep)).lower()
            if bname in seen or not bname.endswith('.paa'): continue
            seen.add(bname)
            src = paa_idx.get(bname)
            if src and os.path.isfile(src):
                dst = os.path.join(out_dir, os.path.splitext(bname)[0] + '.png')
                try:
                    rgba = read_paa(src)
                    Image.fromarray(rgba, 'RGBA').save(dst)
                    done += 1
                except Exception:
                    err += 1
        if done == 0 and err == 0:
            self._status('Нет PAA текстур в модели', '#f9e2af')
        else:
            self._status(f'✔ {done} PNG  ошибок: {err}', '#a6e3a1')
            messagebox.showinfo('Готово', f'Конвертировано: {done} файлов\n→ {out_dir}')

    def _convert_png_to_paa(self):
        """PNG → PAA: выбрать PNG и конвертировать через ImageToPAA."""
        if not IMAGE_TO_PAA:
            messagebox.showerror('Ошибка',
                'ImageToPAA.exe не найден.\n'
                'Установи DayZ Tools (Steam) или положи в папку imagetopaa/')
            return
        paths = filedialog.askopenfilenames(title='Выбрать PNG файл(ы)',
            filetypes=[('PNG/TGA/BMP', '*.png *.tga *.bmp'), ('Все', '*.*')])
        if not paths: return
        done = err = 0
        self._status('Конвертация...', '#f9e2af'); self.root.update()
        for src in paths:
            dst = os.path.splitext(src)[0] + '.paa'
            try:
                r = subprocess.run(
                    [IMAGE_TO_PAA, src, dst],
                    capture_output=True, timeout=60,
                    cwd=os.path.dirname(IMAGE_TO_PAA),
                    creationflags=_NO_WIN
                )
                if r.returncode == 0 and os.path.isfile(dst):
                    done += 1
                else:
                    err += 1
            except Exception:
                err += 1
        self._status(f'✔ {done} PAA  ошибок: {err}', '#a6e3a1' if err == 0 else '#f9e2af')
        messagebox.showinfo('Готово', f'Конвертировано: {done} файлов\nОшибок: {err}')

    def _status(self, text, color=None):
        self.status.config(text=text, fg=color or self.theme['c_ok'])


# ── Редактор оформления ────────────────────────────────────────────────────

class ThemeEditor(tk.Toplevel):

    def __init__(self, app):
        super().__init__(app.root)
        self.app   = app
        self._work = app.theme.copy()   # рабочая копия темы
        self._cvars = {}                # key → (StringVar, swatch_label)
        self.title('🎨  Настройка оформления')
        self.resizable(True, True)
        self.geometry('740x600')
        T = self._work
        self.configure(bg=T['bg_main'])
        self._build()
        self.grab_set()

    # ── Построение ───────────────────────────────────────────────────────────

    def _build(self):
        T = self._work
        nb = ttk.Notebook(self)
        nb.pack(fill=tk.BOTH, expand=True, padx=10, pady=(10, 6))

        tab_colors = tk.Frame(nb, bg=T['bg_main'])
        tab_style  = tk.Frame(nb, bg=T['bg_main'])
        tab_layout = tk.Frame(nb, bg=T['bg_main'])
        nb.add(tab_colors, text='  Цвета  ')
        nb.add(tab_style,  text='  Стиль  ')
        nb.add(tab_layout, text='  Макет  ')

        self._build_colors(tab_colors)
        self._build_style(tab_style)
        self._build_layout(tab_layout)

        # Пресеты
        preset_f = tk.LabelFrame(self, text=' Готовые темы ', bg=T['bg_main'],
                                  fg=T['fg_label'], font=(T['font_fam'], 8),
                                  relief=tk.GROOVE, bd=1)
        preset_f.pack(fill=tk.X, padx=10, pady=(0, 4))
        for name in PRESET_THEMES:
            tk.Button(preset_f, text=name,
                     command=lambda n=name: self._load_preset(n),
                     bg=T['bg_widget'], fg=T['fg_text'],
                     relief=tk.FLAT, font=(T['font_fam'], 8),
                     padx=10, pady=5, cursor='hand2').pack(side=tk.LEFT, padx=4, pady=6)

        # Нижние кнопки
        bf = tk.Frame(self, bg=T['bg_main'])
        bf.pack(fill=tk.X, padx=10, pady=(0, 10))
        for text, cmd, acc in [
            ('Применить', self._apply, True),
            ('Сохранить', self._save,  True),
            ('Сброс',     self._reset, False),
            ('Закрыть',   self.destroy, False),
        ]:
            tk.Button(bf, text=text, command=cmd,
                     bg=T['accent_bg'] if acc else T['bg_widget'],
                     fg=T['accent_fg'] if acc else T['fg_text'],
                     relief=tk.FLAT, font=(T['font_fam'], 9),
                     padx=14, pady=6, cursor='hand2').pack(side=tk.LEFT, padx=4)

    def _build_colors(self, parent):
        T = self._work
        canvas = tk.Canvas(parent, bg=T['bg_main'], highlightthickness=0)
        sb = tk.Scrollbar(parent, orient=tk.VERTICAL, command=canvas.yview)
        inner = tk.Frame(canvas, bg=T['bg_main'])
        inner.bind('<Configure>', lambda e: canvas.configure(
            scrollregion=canvas.bbox('all')))
        canvas.create_window((0, 0), window=inner, anchor='nw')
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.bind('<MouseWheel>', lambda e: canvas.yview_scroll(
            -1 if e.delta > 0 else 1, 'units'))

        for key, label in COLOR_NAMES.items():
            row = tk.Frame(inner, bg=T['bg_main'])
            row.pack(fill=tk.X, padx=10, pady=3)

            color = T.get(key, '#888888')
            swatch = tk.Label(row, text='  ', bg=color, width=3,
                             relief=tk.GROOVE, cursor='hand2')
            swatch.pack(side=tk.LEFT, padx=(0, 8))
            swatch.bind('<Button-1>', lambda e, k=key: self._pick_color(k))

            tk.Label(row, text=label, bg=T['bg_main'], fg=T['fg_text'],
                    font=(T['font_fam'], 9), width=24, anchor='w').pack(side=tk.LEFT)

            var = tk.StringVar(value=color)
            self._cvars[key] = (var, swatch)
            ent = tk.Entry(row, textvariable=var, bg=T['bg_widget'],
                          fg=T['fg_text'], font=('Consolas', 9), width=10,
                          relief=tk.FLAT, insertbackground=T['fg_text'])
            ent.pack(side=tk.LEFT, padx=4)
            ent.bind('<Return>',   lambda e, k=key: self._entry_changed(k))
            ent.bind('<FocusOut>', lambda e, k=key: self._entry_changed(k))

            tk.Button(row, text='⬛', bg=color, cursor='hand2',
                     relief=tk.FLAT, font=('Segoe UI', 9),
                     command=lambda k=key: self._pick_color(k),
                     padx=4).pack(side=tk.LEFT, padx=2)

    def _build_style(self, parent):
        T = self._work
        pad = dict(padx=20, pady=10)

        def row(lbl, w):
            f = tk.Frame(parent, bg=T['bg_main'])
            f.pack(fill=tk.X, **pad)
            tk.Label(f, text=lbl, bg=T['bg_main'], fg=T['fg_text'],
                    font=(T['font_fam'], 9), width=20, anchor='w').pack(side=tk.LEFT)
            w(f).pack(side=tk.LEFT)

        # Шрифт
        self._v_font = tk.StringVar(value=T.get('font_fam', 'Segoe UI'))
        fonts = ['Segoe UI', 'Arial', 'Helvetica', 'Tahoma', 'Verdana',
                 'Consolas', 'Courier New', 'Calibri']
        row('Шрифт:', lambda f: ttk.Combobox(f, textvariable=self._v_font,
            values=fonts, width=18, state='readonly'))

        # Размер шрифта
        self._v_fsz = tk.IntVar(value=T.get('font_sz', 9))
        row('Размер шрифта:', lambda f: tk.Spinbox(f, from_=7, to=18,
            textvariable=self._v_fsz, bg=T['bg_widget'], fg=T['fg_text'],
            width=6, relief=tk.FLAT, buttonbackground=T['bg_widget']))

        # Стиль кнопок
        self._v_relief = tk.StringVar(value=T.get('btn_relief', 'flat'))
        def relief_row(f):
            ff = tk.Frame(f, bg=T['bg_main'])
            for r in ('flat', 'raised', 'groove', 'ridge', 'solid'):
                tk.Radiobutton(ff, text=r, variable=self._v_relief, value=r,
                              bg=T['bg_main'], fg=T['fg_text'],
                              selectcolor=T['bg_widget'],
                              activebackground=T['bg_main'],
                              font=(T['font_fam'], 9)).pack(side=tk.LEFT, padx=5)
            return ff
        row('Стиль кнопок:', relief_row)

        # Ширина панели
        self._v_sidew = tk.IntVar(value=T.get('side_w', 235))
        def sidew_row(f):
            ff = tk.Frame(f, bg=T['bg_main'])
            sc = tk.Scale(ff, variable=self._v_sidew, from_=160, to=400,
                         orient=tk.HORIZONTAL, length=180,
                         bg=T['bg_main'], fg=T['fg_text'],
                         troughcolor=T['bg_widget'],
                         highlightthickness=0, bd=0)
            sc.pack(side=tk.LEFT)
            tk.Label(ff, textvariable=self._v_sidew, bg=T['bg_main'],
                    fg=T['fg_text'], font=('Consolas', 9), width=4).pack(side=tk.LEFT)
            return ff
        row('Ширина панели (px):', sidew_row)

        # v2.7: длина списка загруженных моделей
        self._v_modlist_h = tk.IntVar(value=self._app_settings.get('models_list_height', 500))
        def modlist_row(f):
            ff = tk.Frame(f, bg=T['bg_main'])
            sc = tk.Scale(ff, variable=self._v_modlist_h, from_=100, to=800,
                          orient=tk.HORIZONTAL, length=180,
                          bg=T['bg_main'], fg=T['fg_text'],
                          troughcolor=T['bg_widget'],
                          highlightthickness=0, bd=0)
            sc.pack(side=tk.LEFT)
            tk.Label(ff, textvariable=self._v_modlist_h, bg=T['bg_main'],
                     fg=T['fg_text'], font=('Consolas', 9), width=4).pack(side=tk.LEFT)
            return ff
        row('Длина списка "Загружено":', modlist_row)

    def _build_layout(self, parent):
        T = self._work
        tk.Label(parent, text='Порядок и видимость секций боковой панели:',
                bg=T['bg_main'], fg=T['fg_text'],
                font=(T['font_fam'], 9)).pack(padx=16, pady=(14, 6), anchor='w')

        main_f = tk.Frame(parent, bg=T['bg_main'])
        main_f.pack(padx=16, fill=tk.BOTH, expand=True)

        # Список секций
        self._sec_lb = tk.Listbox(main_f, bg=T['bg_widget'], fg=T['fg_text'],
                                   selectbackground=T['accent_bg'],
                                   selectforeground=T['accent_fg'],
                                   font=(T['font_fam'], 10),
                                   relief=tk.FLAT, height=6, bd=0,
                                   activestyle='none')
        self._sec_lb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        for s in self.app._section_order:
            vis = self.app._section_vis.get(s, True)
            self._sec_lb.insert(tk.END, ('✔  ' if vis else '✖  ') + s)

        # Кнопки справа
        ctrl_f = tk.Frame(main_f, bg=T['bg_main'])
        ctrl_f.pack(side=tk.LEFT, padx=(10, 0), fill=tk.Y)
        for txt, cmd in [
            ('▲  Вверх',      self._sec_up),
            ('▼  Вниз',       self._sec_down),
            ('👁  Показать',   lambda: self._sec_vis(True)),
            ('🙈  Скрыть',    lambda: self._sec_vis(False)),
        ]:
            tk.Button(ctrl_f, text=txt, command=cmd,
                     bg=T['bg_widget'], fg=T['fg_text'],
                     relief=tk.FLAT, font=(T['font_fam'], 9),
                     padx=10, pady=6, cursor='hand2',
                     anchor='w').pack(fill=tk.X, pady=3)

        tk.Label(parent,
                text='Выберите секцию → ▲ ▼ для перемещения, 👁/🙈 для показа/скрытия',
                bg=T['bg_main'], fg=T['fg_label'],
                font=(T['font_fam'], 8)).pack(pady=8)

    # ── Действия ─────────────────────────────────────────────────────────────

    def _pick_color(self, key):
        current = self._work.get(key, '#888888')
        result  = colorchooser.askcolor(color=current,
                                        title=COLOR_NAMES.get(key, key), parent=self)
        if result and result[1]:
            self._work[key] = result[1].lower()
            var, sw = self._cvars[key]
            var.set(result[1].lower())
            try: sw.configure(bg=result[1])
            except Exception: pass

    def _entry_changed(self, key):
        var, sw = self._cvars[key]
        val = var.get().strip()
        if not val.startswith('#'): val = '#' + val
        try:
            self.winfo_rgb(val)           # validate hex
            self._work[key] = val
            sw.configure(bg=val)
        except Exception:
            pass

    def _load_preset(self, name):
        p = PRESET_THEMES.get(name, {})
        self._work.update(p)
        # Обновить swatches и entries
        for key, (var, sw) in self._cvars.items():
            c = self._work.get(key, '#888888')
            var.set(c)
            try: sw.configure(bg=c)
            except Exception: pass
        self._v_font.set(self._work.get('font_fam', 'Segoe UI'))
        self._v_fsz.set(self._work.get('font_sz', 9))
        self._v_relief.set(self._work.get('btn_relief', 'flat'))
        self._v_sidew.set(self._work.get('side_w', 235))

    def _collect(self):
        """Собрать все значения из виджетов в self._work."""
        self._work['font_fam']   = self._v_font.get()
        self._work['font_sz']    = int(self._v_fsz.get())
        self._work['btn_relief'] = self._v_relief.get()
        self._work['side_w']     = int(self._v_sidew.get())
        # v2.7: длина списка загруженных моделей
        self.app._app_settings['models_list_height'] = int(self._v_modlist_h.get())

    def _apply(self):
        self._collect()
        self.app.theme = self._work.copy()
        self.app._apply_theme()
        # v2.7: применяем высоту списка моделей
        try:
            canvas = getattr(self.app, '_models_canvas', None) or getattr(self.app, 'loaded_canvas', None)
            if canvas:
                canvas.configure(height=self.app._app_settings.get('models_list_height', 500))
        except Exception:
            pass

    def _save(self):
        self._apply()
        _save_theme(self.app.theme)
        messagebox.showinfo('Сохранено', 'Настройки оформления сохранены!', parent=self)

    def _reset(self):
        self._work = DEFAULT_THEME.copy()
        self._load_preset('Catppuccin')
        self._apply()

    # ── Секции ───────────────────────────────────────────────────────────────

    def _sec_up(self):
        sel = self._sec_lb.curselection()
        if not sel or sel[0] == 0: return
        i = sel[0]
        items = list(self._sec_lb.get(0, tk.END))
        items[i], items[i-1] = items[i-1], items[i]
        self._sec_lb.delete(0, tk.END)
        for it in items: self._sec_lb.insert(tk.END, it)
        self._sec_lb.select_set(i - 1)
        self._sync_section_order(items)

    def _sec_down(self):
        sel = self._sec_lb.curselection()
        if not sel or sel[0] >= self._sec_lb.size() - 1: return
        i = sel[0]
        items = list(self._sec_lb.get(0, tk.END))
        items[i], items[i+1] = items[i+1], items[i]
        self._sec_lb.delete(0, tk.END)
        for it in items: self._sec_lb.insert(tk.END, it)
        self._sec_lb.select_set(i + 1)
        self._sync_section_order(items)

    def _sec_vis(self, show):
        sel = self._sec_lb.curselection()
        if not sel: return
        i    = sel[0]
        text = self._sec_lb.get(i)
        name = text[3:]   # убрать prefix "✔  " или "✖  "
        self._sec_lb.delete(i)
        self._sec_lb.insert(i, ('✔  ' if show else '✖  ') + name)
        self._sec_lb.select_set(i)
        self.app._show_section(name, show)

    def _sync_section_order(self, items):
        order = [it[3:] for it in items]   # убрать prefix
        self.app._section_order = order
        self.app._reorder_sections()


if '--p3d-subproc' in sys.argv:
    # Subprocess-режим: парсим P3D и отдаём JSON. Запускается ДО if __name__,
    # потому что PyInstaller frozen mode может не выполнить __main__ блок
    # (когда скрипт импортируется как модуль).
    _p3d_subprocess_main()
    sys.exit(0)

if __name__ == '__main__' or getattr(sys, 'frozen', False):
    # GUI-режим при прямом запуске `python p3d_viewer.py`
    # ИЛИ при PyInstaller frozen mode (--onefile/--onedir).
    # В frozen mode sys.frozen = True; sys.argv[0] = путь к EXE.

    root = TkinterDnD.Tk() if USE_DND else tk.Tk()
    root.withdraw()  # скрываем главное окно пока строится интерфейс

    splash = _show_splash(root)
    splash.update()

    def _on_close():
        _dbg('App closing')
        if _dbg_file:
            try: _dbg_file.close()
            except Exception: pass
        root.destroy()
    root.protocol('WM_DELETE_WINDOW', _on_close)
    app  = App(root)

    if len(sys.argv) > 1 and sys.argv[1].lower().endswith('.p3d'):
        root.after(300, lambda: app._load_p3d(sys.argv[1]))

    def _close_splash():
        try:
            splash.destroy()
        except Exception:
            pass
        root.deiconify()  # показываем главное окно

    root.after(1800, _close_splash)
    root.mainloop()
