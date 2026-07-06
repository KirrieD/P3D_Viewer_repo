"""
Parser for DayZ ArmA C++ config files (.cpp).

Handles 4 main class types:
- CfgPatches (addon declarations)
- cfgVehicles (item classes with model, attachments, inventorySlot)
- CfgNonAIVehicles (Proxy* classes for proxy meshes)
- CfgSlots (Slot_* classes with GhostIcon)

Supports nested classes (class X { class Y { ... } }) and string fields
like GhostIcon = "set:ModName image:IconName".
"""
import re


def _find_block_end(text, start):
    """Given position of '{', find matching '}' (depth-aware)."""
    depth = 0
    i = start
    while i < len(text):
        c = text[i]
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return len(text) - 1


def _split_top_level(body, sep=','):
    """Split body by separator at top level (not inside {} or "")."""
    parts = []
    depth = 0
    in_str = False
    esc = False
    last = 0
    i = 0
    while i < len(body):
        c = body[i]
        if esc:
            esc = False
        elif c == '\\' and in_str:
            esc = True
        elif c == '"':
            in_str = not in_str
        elif not in_str:
            if c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
            elif c == sep and depth == 0:
                parts.append(body[last:i].strip())
                last = i + 1
        i += 1
    if last < len(body):
        rest = body[last:].strip()
        if rest:
            parts.append(rest)
    return parts


def _parse_string_field(text, name):
    """Parse 'name = "value";' or 'name[] = { "v1", "v2" };'"""
    # Array form first
    m_arr = re.search(rf'{re.escape(name)}\s*\[\s*\]\s*=\s*\{{([^}}]*)\}}', text)
    if m_arr:
        items = re.findall(r'"([^"]*)"', m_arr.group(1))
        return items
    # Scalar string
    m_str = re.search(rf'{re.escape(name)}\s*=\s*"([^"]*)"', text)
    if m_str:
        return m_str.group(1)
    # Scalar non-string
    m_val = re.search(rf'{re.escape(name)}\s*=\s*([^;]+);', text)
    if m_val:
        v = m_val.group(1).strip()
        # Try to convert number
        try:
            if '.' in v:
                return float(v)
            return int(v)
        except ValueError:
            return v
    return None


def parse_cfg_patches(cpp_text):
    """Extract CfgPatches classes with units[], weapons[], requiredAddons[]."""
    result = []
    m = re.search(r'class\s+CfgPatches\s*\{', cpp_text)
    if not m:
        return result
    bpos = cpp_text.index('{', m.start())
    epos = _find_block_end(cpp_text, bpos)
    body = cpp_text[bpos + 1:epos]
    # Each child class
    for cm in re.finditer(r'class\s+(\w+)\s*\{', body):
        cb = body.index('{', cm.start())
        ce = _find_block_end(body, cb)
        class_body = body[cb + 1:ce]
        result.append({
            'name': cm.group(1),
            'units': _parse_string_field(class_body, 'units') or [],
            'weapons': _parse_string_field(class_body, 'weapons') or [],
            'requiredAddons': _parse_string_field(class_body, 'requiredAddons') or [],
        })
    return result


def parse_cfg_vehicles(cpp_text, model_filter=None):
    """
    Extract cfgVehicles classes with relevant fields:
    - name (class name)
    - parent (parent class)
    - model (path to .p3d)
    - attachments (list of slot names)
    - inventorySlot (list of slot names)
    - displayName
    - descriptionShort
    - scope
    - itemInfo (e.g. ['Clothing', 'Back'])

    If model_filter is given, returns only classes whose model= contains filter
    (case-insensitive).
    """
    result = []
    m = re.search(r'class\s+cfgVehicles\s*\{', cpp_text)
    if not m:
        m = re.search(r'class\s+CfgVehicles\s*\{', cpp_text)
        if not m:
            return result
    bpos = cpp_text.index('{', m.start())
    epos = _find_block_end(cpp_text, bpos)
    body = cpp_text[bpos + 1:epos]

    # Walk recursively — cfgVehicles has nested classes (parent declarations)
    def walk(text, parent_chain):
        for cm in re.finditer(r'class\s+(\w+)(?:\s*:\s*([\w:]+))?\s*\{', text):
            cb = text.index('{', cm.start())
            ce = _find_block_end(text, cb)
            class_body = text[cb + 1:ce]
            cname = cm.group(1)
            cparent = cm.group(2) if cm.group(2) else (parent_chain[-1] if parent_chain else None)
            new_chain = parent_chain + [cname]

            # Extract relevant fields
            model = _parse_string_field(class_body, 'model')
            if model:
                model_lower = model.lower().replace('/', '\\')
                # Apply filter if provided
                if model_filter is None or model_filter.lower() in model_lower:
                    entry = {
                        'name': cname,
                        'parent': cparent,
                        'model': model,
                        'displayName': _parse_string_field(class_body, 'displayName'),
                        'descriptionShort': _parse_string_field(class_body, 'descriptionShort'),
                        'attachments': _parse_string_field(class_body, 'attachments') or [],
                        'inventorySlot': _parse_string_field(class_body, 'inventorySlot') or [],
                        'scope': _parse_string_field(class_body, 'scope'),
                        'itemInfo': _parse_string_field(class_body, 'itemInfo') or [],
                        'hiddenSelections': _parse_string_field(class_body, 'hiddenSelections') or [],
                    }
                    result.append(entry)
                # Don't recurse into item — already have its model
                continue
            # Recurse for parent class declarations
            walk(class_body, new_chain)

    walk(body, [])
    return result


def parse_cfg_non_ai_vehicles(cpp_text):
    """
    Extract Proxy* classes from CfgNonAIVehicles.
    Each entry: {name, parent, inventorySlot (string), model}
    """
    result = []
    m = re.search(r'class\s+CfgNonAIVehicles\s*\{', cpp_text)
    if not m:
        return result
    bpos = cpp_text.index('{', m.start())
    epos = _find_block_end(cpp_text, bpos)
    body = cpp_text[bpos + 1:epos]

    for cm in re.finditer(r'class\s+(\w+)(?:\s*:\s*([\w:]+))?\s*\{', body):
        cb = body.index('{', cm.start())
        ce = _find_block_end(body, cb)
        class_body = body[cb + 1:ce]
        cname = cm.group(1)
        # Skip abstract parent class "ProxyAttachment"
        if cname == 'ProxyAttachment' and (cm.group(2) is None):
            continue
        result.append({
            'name': cname,
            'parent': cm.group(2),
            'inventorySlot': _parse_string_field(class_body, 'inventorySlot'),
            'model': _parse_string_field(class_body, 'model'),
            'scope': _parse_string_field(class_body, 'scope'),
        })
    return result


def parse_cfg_slots(cpp_text):
    """
    Extract Slot_* classes from CfgSlots.
    Each entry: {name, displayName, ghostIcon}
    """
    result = []
    m = re.search(r'class\s+CfgSlots\s*\{', cpp_text)
    if not m:
        return result
    bpos = cpp_text.index('{', m.start())
    epos = _find_block_end(cpp_text, bpos)
    body = cpp_text[bpos + 1:epos]

    for cm in re.finditer(r'class\s+(\w+)(?:\s*:\s*([\w:]+))?\s*\{', body):
        cb = body.index('{', cm.start())
        ce = _find_block_end(body, cb)
        class_body = body[cb + 1:ce]
        cname = cm.group(1)
        if cname == 'Slot__Back':
            continue  # abstract
        result.append({
            'name': cname,
            'displayName': _parse_string_field(class_body, 'displayName'),
            'ghostIcon': _parse_string_field(class_body, 'GhostIcon'),
            'name_slot': _parse_string_field(class_body, 'name'),
        })
    return result


def find_class_in_cfg_vehicles(cpp_text, class_name):
    """Locate a specific class by name in cfgVehicles. Returns (start, end, body) or None."""
    m = re.search(r'class\s+cfgVehicles\s*\{', cpp_text)
    if not m:
        m = re.search(r'class\s+CfgVehicles\s*\{', cpp_text)
        if not m:
            return None
    bpos = cpp_text.index('{', m.start())
    epos = _find_block_end(cpp_text, bpos)
    body = cpp_text[bpos + 1:epos]

    def walk(text):
        for cm in re.finditer(r'class\s+(\w+)(?:\s*:\s*([\w:]+))?\s*\{', text):
            cb = text.index('{', cm.start())
            ce = _find_block_end(text, cb)
            if cm.group(1) == class_name:
                return (cb, ce, text[cb + 1:ce])
            sub = walk(text[cb + 1:ce])
            if sub:
                return sub
        return None

    return walk(body)


def main_test():
    """Quick test using compass config.cpp."""
    import os
    p = r'O:\Users\KirrieD\AppData\Local\Temp\dos_mod_editor_67p48a9q\_unpacked\gear_navigation\DZ\gear\navigation\config.cpp'
    if not os.path.isfile(p):
        print(f'NOT FOUND: {p}')
        return
    text = open(p, 'r', encoding='utf-8', errors='replace').read()
    print('=== CfgPatches ===')
    for p in parse_cfg_patches(text):
        print(f'  {p["name"]}: units={p["units"][:3]}{"..." if len(p["units"]) > 3 else ""}')
    print('\n=== cfgVehicles (with model) ===')
    for v in parse_cfg_vehicles(text):
        if v['model']:
            print(f'  {v["name"]}: model={v["model"]}')
            print(f'    attachments={v["attachments"]}')
            print(f'    inventorySlot={v["inventorySlot"]}')
    print('\n=== CfgNonAIVehicles ===')
    for p in parse_cfg_non_ai_vehicles(text):
        print(f'  {p["name"]}: slot={p["inventorySlot"]}  model={p["model"]}')
    print('\n=== CfgSlots ===')
    for s in parse_cfg_slots(text):
        print(f'  {s["name"]}: name={s["name_slot"]}  icon={s["ghostIcon"]}')


if __name__ == '__main__':
    main_test()