"""
Wrapper that combines cpp_parser + p3d_proxy_parser.

Returns a unified proxy model:
{
  'item_class': 'TaloonBag_ColorBase',
  'item_kind': 'parent'|'child',  # parent = has attachments, child = only inventorySlot
  'attachments': [{name, icon, cpp_file, proxy_in_p3d (synthetic|bin|missing)}],
  'inventory_slot': [{name, icon, cpp_file}],
  'proxies_in_p3d': {normalized_name: {type, transform, ...}},
}
"""
import os
from cpp_parser import (
    parse_cfg_patches,
    parse_cfg_vehicles,
    parse_cfg_non_ai_vehicles,
    parse_cfg_slots,
)
from p3d_proxy_parser import parse_proxy_points, get_unique_proxies


def normalize_slot_name(name):
    """Normalize slot name for matching: lowercase, strip path & extension."""
    if isinstance(name, (list, tuple)):
        if not name:
            return ''
        name = name[0]
    if not name or not isinstance(name, str):
        return ''
    n = name.lower()
    # Drop leading 'proxy:'
    if n.startswith('proxy:'):
        n = n[6:]
    # Drop leading 'proxy\\'
    n = n.replace('proxy\\', '')
    # Take last segment (basename)
    n = n.split('\\')[-1].split('/')[-1]
    # Drop .001 / .p3d
    for ext in ('.001', '.p3d'):
        if n.endswith(ext):
            n = n[:-len(ext)]
    return n


def build_proxy_index(cpp_path, p3d_path):
    """
    Build a unified proxy index for a cpp + p3d pair.

    Returns dict with:
      - item_class: cfgVehicles class that references p3d
      - item_kind: 'parent' (has attachments) or 'child' (only inventorySlot)
      - attachments: list of slot dicts
      - inventory_slot: list of slot dicts
      - proxies_in_p3d: dict of unique proxy points from p3d
      - cfg_non_ai_vehicles: list of Proxy* definitions from cpp
      - cfg_slots: list of Slot_* definitions from cpp
    """
    result = {
        'item_class': None,
        'item_kind': None,
        'attachments': [],
        'inventory_slot': [],
        'proxies_in_p3d': {},
        'cfg_non_ai_vehicles': [],
        'cfg_slots': [],
        'errors': [],
    }
    if not cpp_path or not os.path.isfile(cpp_path):
        result['errors'].append(f'config.cpp not found: {cpp_path}')
        return result

    text = open(cpp_path, 'r', encoding='utf-8', errors='replace').read()

    # Parse cpp
    cfg_slots = parse_cfg_slots(text)
    cfg_proxies = parse_cfg_non_ai_vehicles(text)
    cfg_slots_by_name = {s.get('name_slot') or s['name'].replace('Slot_', ''): s
                          for s in cfg_slots}
    cfg_proxies_by_slot = {normalize_slot_name(p.get('inventorySlot') or ''): p
                            for p in cfg_proxies if p.get('inventorySlot')}

    # Find item class matching p3d (check model= AND ClothingTypes.male/female)
    import re as _re
    from cpp_parser import _find_block_end
    p3d_basename = os.path.basename(p3d_path).lower() if p3d_path else ''
    item_class = None
    # Direct model=
    for v in parse_cfg_vehicles(text):
        if not v.get('model'):
            continue
        model_lower = v['model'].lower().replace('/', '\\')
        if p3d_basename and p3d_basename in model_lower:
            item_class = v
            break
    # ClothingTypes.male/female — search inside each cfgVehicles child class
    if not item_class and p3d_basename:
        cfgvehicles_match = _re.search(r'class\s+cfgVehicles\s*\{', text)
        if cfgvehicles_match:
            cv_cb = text.index('{', cfgvehicles_match.start())
            cv_ce = _find_block_end(text, cv_cb)
            cv_body = text[cv_cb + 1:cv_ce]
            # Iterate top-level child classes only
            for m in _re.finditer(r'class\s+(\w+)(?:\s*:\s*[\w:]+)?\s*\{', cv_body):
                cls_cb = cv_body.index('{', m.start())
                cls_ce = _find_block_end(cv_body, cls_cb)
                cls_body = cv_body[cls_cb + 1:cls_ce]
                # Look for ClothingTypes inside this class only
                ct_match = _re.search(r'class\s+ClothingTypes\s*\{', cls_body)
                if not ct_match:
                    continue
                ct_cb = cls_body.index('{', ct_match.start())
                ct_ce = _find_block_end(cls_body, ct_cb)
                ct_body = cls_body[ct_cb + 1:ct_ce]
                for ref in _re.finditer(r'(?:male|female)\s*=\s*"([^"]+)"', ct_body):
                    ref_lower = ref.group(1).lower().replace('/', '\\')
                    if p3d_basename in ref_lower:
                        target_name = m.group(1)
                        for v in parse_cfg_vehicles(text):
                            if v['name'] == target_name:
                                item_class = v
                                break
                        if item_class is None:
                            item_class = {'name': target_name, 'model': ref.group(1),
                                          'attachments': [], 'inventorySlot': []}
                        break
                if item_class:
                    break

    if not item_class:
        result['errors'].append(f'No cfgVehicles class found for p3d: {p3d_basename}')
        return result

    result['item_class'] = item_class['name']
    result['kind'] = 'parent' if item_class['attachments'] else 'child'
    result['item_kind'] = result['kind']

    # Build slot entries
    if item_class['attachments']:
        for slot_name in item_class['attachments']:
            slot_info = cfg_slots_by_name.get(slot_name) or {}
            entry = {
                'name': slot_name,
                'display_name': slot_info.get('displayName'),
                'icon': slot_info.get('ghostIcon'),
                'cpp_file': cpp_path,
            }
            result['attachments'].append(entry)
    if item_class['inventorySlot']:
        for slot_name in item_class['inventorySlot']:
            slot_info = cfg_slots_by_name.get(slot_name) or {}
            entry = {
                'name': slot_name,
                'display_name': slot_info.get('displayName'),
                'icon': slot_info.get('ghostIcon'),
                'cpp_file': cpp_path,
            }
            result['inventory_slot'].append(entry)

    # Parse P3D if provided
    if p3d_path and os.path.isfile(p3d_path):
        p3d_points = parse_proxy_points(p3d_path)
        result['proxies_in_p3d'] = get_unique_proxies(p3d_points)

    # Map P3D proxies to slots
    if result['attachments']:
        for slot in result['attachments']:
            slot_norm = normalize_slot_name(slot['name'])
            slot['p3d_proxy'] = result['proxies_in_p3d'].get(slot_norm)

    result['cfg_non_ai_vehicles'] = cfg_proxies
    result['cfg_slots'] = cfg_slots
    return result


def main_test():
    """Test on taloon_f (parent) and compass (child)."""
    # Parent: taloon
    cpp = r'O:\Users\KirrieD\AppData\Local\Temp\dos_mod_editor_67p48a9q\_unpacked\characters_backpacks\DZ\characters\backpacks\config.cpp'
    p3d = r'O:\Users\KirrieD\AppData\Local\Temp\dos_mod_editor_67p48a9q\_unpacked\characters_backpacks\DZ\characters\backpacks\taloon_f.p3d'
    idx = build_proxy_index(cpp, p3d)
    print('=== Taloon (parent) ===')
    print(f'class: {idx["item_class"]}, kind: {idx["item_kind"]}')
    print(f'attachments ({len(idx["attachments"])}):')
    for a in idx['attachments']:
        proxy_kind = a.get('p3d_proxy', {}).get('type') if a.get('p3d_proxy') else 'MISSING'
        print(f'  {a["name"]:15s}  icon={a["icon"]!r:50s}  p3d={proxy_kind}')
    print(f'proxies_in_p3d: {list(idx["proxies_in_p3d"].keys())}')

    # Child: compass
    print()
    cpp = r'O:\Users\KirrieD\AppData\Local\Temp\dos_mod_editor_67p48a9q\_unpacked\gear_navigation\DZ\gear\navigation\config.cpp'
    p3d = r'O:\Users\KirrieD\AppData\Local\Temp\dos_mod_editor_67p48a9q\_unpacked\gear_navigation\DZ\gear\navigation\compass.p3d'
    idx = build_proxy_index(cpp, p3d)
    print('=== Compass (child) ===')
    print(f'class: {idx["item_class"]}, kind: {idx["item_kind"]}')
    print(f'inventorySlot ({len(idx["inventory_slot"])}):')
    for s in idx['inventory_slot']:
        print(f'  {s["name"]:15s}  icon={s["icon"]!r}')
    print(f'attachments: {len(idx["attachments"])}')


if __name__ == '__main__':
    main_test()