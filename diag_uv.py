"""Быстрая диагностика UV в P3D."""
import ctypes, struct, os, sys
from ctypes import c_void_p, c_uint32, c_uint64, c_int

dll = ctypes.WinDLL(r"O:\Users\KirrieD\Desktop\Dev_DoS\Cast_Prog\P3D_Viewer_v3.0\_internal\DePbo64.dll")

# Сигнатуры — копирую из p3d_viewer.py
dll['?p3d_get_face_table@DeP3d@@QEAAPEAUFace@@PEBX_KPEA_K@Z'].restype = c_void_p
dll['?p3d_get_face_table@DeP3d@@QEAAPEAUFace@@PEBX_KPEA_K@Z'].argtypes = [c_void_p, c_void_p, c_uint64, ctypes.POINTER(c_uint64)]
dll['?p3d_get_points_table@DeP3d@@QEAAPEBMPEBX_KPEA_K@Z'].restype = c_void_p
dll['?p3d_get_points_table@DeP3d@@QEAAPEBMPEBX_KPEA_K@Z'].argtypes = [c_void_p, c_void_p, c_uint64, ctypes.POINTER(c_uint64)]
dll['?p3d_get_normals_table@DeP3d@@QEAAPEBMPEBX_KPEA_K@Z'].restype = c_void_p
dll['?p3d_get_normals_table@DeP3d@@QEAAPEBMPEBX_KPEA_K@Z'].argtypes = [c_void_p, c_void_p, c_uint64, ctypes.POINTER(c_uint64)]
dll['?p3d_get_uvset_table@DeP3d@@QEAAPEAUUVFloats@@PEBXIPEA_K@Z'].restype = c_void_p
dll['?p3d_get_uvset_table@DeP3d@@QEAAPEAUUVFloats@@PEBXIPEA_K@Z'].argtypes = [c_void_p, c_void_p, c_int, ctypes.POINTER(c_uint64)]
dll['?p3d_get_lod_count@DeP3d@@QEAAIXZ'].restype = c_uint32
dll['?p3d_get_lod_count@DeP3d@@QEAAIXZ'].argtypes = [c_void_p]
dll['?p3d_get_lod@DeP3d@@QEAAPEBXLod@XZ'].restype = c_void_p
dll['?p3d_get_lod@DeP3d@@QEAAPEBXLod@XZ'].argtypes = [c_void_p]
dll['?p3d_lod_get_face_count@DeP3d@@QEAA_KPEBX@Z'].restype = c_uint64
dll['?p3d_lod_get_face_count@DeP3d@@QEAA_KPEBX@Z'].argtypes = [c_void_p, c_void_p]

# ctor/op signatures — нужны для открытия файла
ctor = dll['?ctor@DeP3d@@QEAA@PEAX@Z']
ctor.restype = c_void_p
ctor.argtypes = [c_void_p]

op = dll['?open_file@DeP3d@@QEAAHPEBDHPEAD@Z']
op.restype = c_int
op.argtypes = [c_void_p, ctypes.c_char_p, c_int, c_void_p]

gp3d = dll['?get_p3d@DeP3d@@QEAAAEAVDeP3d@@XZ']
gp3d.restype = c_void_p
gp3d.argtypes = [c_void_p]

gf  = dll['?p3d_get_face_table@DeP3d@@QEAAPEAUFace@@PEBX_KPEA_K@Z']
gpts = dll['?p3d_get_points_table@DeP3d@@QEAAPEBMPEBX_KPEA_K@Z']
gnrm = dll['?p3d_get_normals_table@DeP3d@@QEAAPEBMPEBX_KPEA_K@Z']
guv  = dll['?p3d_get_uvset_table@DeP3d@@QEAAPEAUUVFloats@@PEBXIPEA_K@Z']
glc  = dll['?p3d_get_lod_count@DeP3d@@QEAAIXZ']
gl_  = dll['?p3d_get_lod@DeP3d@@QEAAPEBXLod@XZ']
gnt  = dll['?p3d_lod_get_face_count@DeP3d@@QEAA_KPEBX@Z']

def read_floats(get_fn, p3d, lod, dim):
    cnt = c_uint64(0)
    pcnt = ctypes.cast(ctypes.addressof(cnt), ctypes.POINTER(c_uint64))
    ptr = get_fn(p3d, lod, 0, pcnt) if get_fn is guv else get_fn(p3d, lod, pcnt)
    n = cnt.value
    if not ptr or not n: return []
    raw = list(struct.unpack_from(f'<{n*dim}f', (ctypes.c_uint8 * (n*dim*4)).from_address(ptr)))
    return [tuple(raw[i*dim:(i+1)*dim]) for i in range(n)]

paths = sys.argv[1:] or [
    r"O:\Users\KirrieD\AppData\Local\Temp\dos_mod_editor_kfcgdgfd\_unpacked\CJ_Materials\CJ_Materials\bolti\bolti.p3d",
    r"O:\Users\KirrieD\AppData\Local\Temp\dos_mod_editor_kfcgdgfd\_unpacked\CJ_Materials\CJ_Materials\CementBag\cementbag.p3d",
]

for path in paths:
    print(f"\n=== {os.path.basename(path)} ===")
    buf = ctypes.create_string_buffer(1024 * 1024)
    ctor(buf)
    p3d = gp3d(buf)
    op(p3d, path.encode('mbcs'), 0, None)

    best_lod, best_nt = None, 0
    for li in range(glc(p3d)):
        lod = gl_(p3d, li)
        if lod:
            nt = gnt(p3d, lod)
            if nt > best_nt:
                best_nt = nt
                best_lod = lod
    if not best_lod:
        print("  NO LOD"); continue

    pts = read_floats(gpts, p3d, best_lod, 3)
    nrms = read_floats(gnrm, p3d, best_lod, 3)
    uvs = read_floats(guv, p3d, best_lod, 2)

    cnt = c_uint64(0)
    pcnt = ctypes.cast(ctypes.addressof(cnt), ctypes.POINTER(c_uint64))
    face_ptr = gf(p3d, best_lod, pcnt)
    nf = cnt.value

    print(f"  nf={nf}  pts={len(pts)}  nrms={len(nrms)}  uvs={len(uvs)}")
    if uvs:
        us = [u for u,v in uvs]
        vs = [v for u,v in uvs]
        print(f"  U range: min={min(us):.4f} max={max(us):.4f} unique={len(set(round(u,3) for u in us))}")
        print(f"  V range: min={min(vs):.4f} max={max(vs):.4f} unique={len(set(round(v,3) for v in vs))}")
        print(f"  First 12 UVs: {uvs[:12]}")
        print(f"  Last 12 UVs: {uvs[-12:]}")
        # Если U/V > 1.0 — текстура wrapping/repeating. Если в диапазоне [0,1] — обычный layout
        # Если >1 или <0 — значит UVs scale + offset, текстура tile-ится