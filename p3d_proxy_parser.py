"""
Parser for ODOL v54 P3D proxy points.

Two types of proxy in P3D:
1. SYNTHETIC  ("proxy\\Name\\0") - transform right after nul-terminator
2. BIN-PROXY  ("proxy:\\path\\file.001\\0") - transform in separate section

Returns list of proxy point dicts.
"""
import os
import struct
import re


def _read_file(path):
    with open(path, 'rb') as f:
        return f.read()


def _find_proxy_strings(data):
    """Find all proxy strings in P3D data."""
    results = []
    for m in re.finditer(rb'proxy[\\\\/:_\.a-zA-Z0-9\-]{1,80}', data):
        raw = m.group(0)
        s = raw.decode('latin-1', errors='replace').rstrip('\x00')
        if 'proxy' not in s.lower():
            continue
        nul = raw.find(b'\x00')
        end = m.start() + nul if nul >= 0 else m.end()
        is_synthetic = s.startswith('proxy\\') and not s.startswith('proxy:\\')
        results.append({
            'start': m.start(),
            'end': end,
            'name': s,
            'type': 'synthetic' if is_synthetic else 'bin',
        })
    return results


def _parse_synthetic_transform(data, end_offset):
    """
    For synthetic proxy: transform starts at end_offset + 1 (skip padding byte).
    Returns 12 floats (3x3 mat + 3 pos) or None.
    """
    start = end_offset + 1
    if start + 48 > len(data):
        return None
    floats = struct.unpack('<12f', data[start:start + 48])
    # Validate: rotation matrix (diag non-zero, off-diag moderate)
    if abs(floats[0]) < 0.001 or abs(floats[4]) < 0.001:
        return None
    if not all(-50 < v < 50 for v in floats):
        return None
    return {
        'offset': start,
        'mat': list(floats[0:9]),
        'pos': list(floats[9:12]),
    }


def _parse_bin_proxy_flags(data, end_offset):
    """
    For bin-proxy: parse flags after nul-terminator.
    Returns (id, extra_props) where id is the proxy type id (1/2/3/etc).
    """
    # Flags: 4 ints (type=1, id, ?, ?)
    if end_offset + 1 + 24 > len(data):
        return None
    flags = struct.unpack('<6I', data[end_offset + 1:end_offset + 1 + 24])
    return {
        'offset': end_offset + 1,
        'flags': list(flags),
        'id': flags[1],  # proxy id (1=chemlight, 2=backpack, 3=walkietalkie)
    }


def parse_proxy_points(p3d_path):
    """
    Parse all proxy points in a P3D file.
    Returns list of dicts:
    {
        'name': str,                 # proxy string
        'type': 'synthetic'|'bin',   # type
        'start': int,                # string start offset
        'end': int,                  # string end offset (after nul)
        'transform': {offset, mat, pos} or None,
        'flags': {offset, flags, id} or None,  # for bin-proxy
    }
    """
    if not os.path.isfile(p3d_path):
        return []
    data = _read_file(p3d_path)
    proxy_strings = _find_proxy_strings(data)
    results = []
    seen_names = set()

    for ps in proxy_strings:
        # Deduplicate by (name, lod_position) — same name can appear 6 times (one per LOD)
        # Keep only FIRST occurrence per LOD group (every 4 proxies = 1 LOD)
        entry = {
            'name': ps['name'],
            'type': ps['type'],
            'start': ps['start'],
            'end': ps['end'],
            'transform': None,
            'flags': None,
        }
        if ps['type'] == 'synthetic':
            entry['transform'] = _parse_synthetic_transform(data, ps['end'])
        else:
            entry['flags'] = _parse_bin_proxy_flags(data, ps['end'])
        results.append(entry)

    return results


def get_unique_proxies(proxy_points):
    """
    Get unique proxy points (one per LOD group).
    Each LOD has 4 proxies (1 synthetic + 3 bin-proxy).
    Returns dict {normalized_name: {type, name, transform, flags, count}}
    """
    unique = {}
    for pp in proxy_points:
        # Normalize name
        name = pp['name']
        if name.startswith('proxy:'):
            # bin-proxy: extract filename
            basename = os.path.basename(name.replace('proxy:', '').replace('.001', ''))
            norm = basename
        else:
            # synthetic: strip "proxy\\"
            norm = name.replace('proxy\\', '').lstrip('_')
        if norm not in unique:
            unique[norm] = {
                'name': name,
                'type': pp['type'],
                'normalized': norm,
                'transform': pp['transform'],
                'flags': pp['flags'],
                'count': 1,
            }
        else:
            unique[norm]['count'] += 1
            # Keep first transform (they're identical across LODs for synthetic)
    return unique


def main_test():
    import os
    p = r'O:\Users\KirrieD\AppData\Local\Temp\dos_mod_editor_67p48a9q\_unpacked\characters_backpacks\DZ\characters\backpacks\taloon_f.p3d'
    if not os.path.isfile(p):
        print(f'NOT FOUND: {p}')
        return
    points = parse_proxy_points(p)
    print(f'=== {os.path.basename(p)} ===')
    print(f'Total proxy strings: {len(points)}')
    synth = [pp for pp in points if pp['type'] == 'synthetic']
    bins = [pp for pp in points if pp['type'] == 'bin']
    print(f'  synthetic: {len(synth)}')
    print(f'  bin-proxy: {len(bins)}')
    print('\n--- Synthetic (with transform) ---')
    for pp in synth[:3]:
        t = pp['transform']
        if t:
            print(f'  {pp["name"]!r}: mat(diag)={t["mat"][0]:+.3f},{t["mat"][4]:+.3f},{t["mat"][8]:+.3f}  pos={t["pos"]}')
        else:
            print(f'  {pp["name"]!r}: NO TRANSFORM')
    print('\n--- Unique proxies ---')
    uniq = get_unique_proxies(points)
    for k, v in uniq.items():
        print(f'  {k} ({v["type"]}, count={v["count"]})')


if __name__ == '__main__':
    main_test()