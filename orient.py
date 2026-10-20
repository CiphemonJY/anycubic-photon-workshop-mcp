"""Resin (MSLA, inverted) print-orientation optimizer.

Frame: the build plate is z=0 and layers grow toward +z; the vat floor (FEP) is at the
current top layer. A candidate orientation is the mesh direction `up` that is mapped to +z.

Cost terms, all computed from the oriented mesh:
  support  - area of down-facing surface steeper than the self-supporting angle (needs
             supports); area on PROTECTED (cosmetic) faces is counted x10 so those faces end up
             facing away from the plate.
  peel     - largest single-layer cross-section (peel/suction force on the FEP).
  cup      - area of resin cavities sealed against the FEP while printing (suction cups):
             voxelise, and at each layer flood the resin from the open sides / plate side
             through the already-printed space; any cavity cell at the current layer that the
             flood can't reach is sealed between the part and the FEP.
  height   - print height (time), small weight.
The cheap terms (support, height) rank ~600 directions; the expensive ones (peel, cup) are
evaluated on the best few, and the final score uses all four.
"""
import math

import numpy as np
import trimesh
from scipy import ndimage

BUILD = (218.88, 122.88, 200.0)   # Photon Mono M5s build volume, mm


def fib_sphere(n):
    i = np.arange(n) + 0.5
    phi = np.arccos(1 - 2 * i / n)
    th = math.pi * (1 + 5 ** 0.5) * i
    return np.c_[np.cos(th) * np.sin(phi), np.sin(th) * np.sin(phi), np.cos(phi)]


def rot_to_z(up):
    """Rotation matrix (3x3) taking unit vector `up` to +z."""
    up = up / np.linalg.norm(up)
    z = np.array([0.0, 0, 1])
    v = np.cross(up, z)
    c = float(up @ z)
    if np.linalg.norm(v) < 1e-9:
        return np.eye(3) if c > 0 else np.diag([1.0, -1, -1])
    vx = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + vx + vx @ vx * (1 / (1 + c))


def support_terms(mesh, up, overhang_deg, protect):
    n = mesh.face_normals
    a = mesh.area_faces
    d = n @ up
    need = d < -math.sin(math.radians(90 - overhang_deg))   # facing down, steeper than allowed
    prot = np.zeros(len(a), bool)
    for p in protect:
        prot |= (n @ p) > 0.9
    return float(a[need & ~prot].sum()), float(a[need & prot].sum())


def oriented(mesh, R):
    m = mesh.copy()
    T = np.eye(4)
    T[:3, :3] = R
    m.apply_transform(T)
    m.apply_translation(-m.bounds[0])
    return m


def solid_points(mesh, pitch):
    """Centres of the filled voxels of `mesh` (done once per part, in its own frame)."""
    return mesh.voxelized(pitch).fill().points


def grid_from_points(pts, R, pitch):
    """Occupancy grid of rotated solid points; z = build direction."""
    p = pts @ np.asarray(R).T
    p -= p.min(axis=0)
    idx = np.floor(p / pitch).astype(int)
    shape = idx.max(axis=0) + 1
    g = np.zeros(shape, bool)
    g[idx[:, 0], idx[:, 1], idx[:, 2]] = True
    # close the 1-cell pinholes rotation-binning leaves inside solid walls
    return ndimage.binary_closing(g, iterations=1, border_value=0) | g


def peel_and_cup(m, pitch=0.5, solid=None):
    """(max layer area mm2, sealed-cavity area mm2 summed over layers / layer count)."""
    if solid is None:
        solid = m.voxelized(pitch).fill().matrix.astype(bool)   # (nx, ny, nz), z = build direction
    solid = np.pad(solid, ((1, 1), (1, 1), (0, 0)))      # open resin on every side
    area = solid.sum(axis=(0, 1)) * pitch * pitch
    peel = float(area.max())
    nz = solid.shape[2]
    sealed_total = 0
    st = ndimage.generate_binary_structure(3, 1)
    # Flood from the plate side (below z=0 the part sits on supports in open resin) and the
    # padded sides; only space already printed (z <= k) exists, the FEP caps layer k.
    step = max(1, nz // 60)
    for k in range(0, nz, step):
        air = ~solid[:, :, : k + 1]
        lab, nl = ndimage.label(air, st)
        if nl == 0:
            continue
        open_ids = set(np.unique(lab[0, :, :])) | set(np.unique(lab[-1, :, :])) | \
            set(np.unique(lab[:, 0, :])) | set(np.unique(lab[:, -1, :])) | set(np.unique(lab[:, :, 0]))
        top = lab[:, :, k]
        sealed = (top > 0) & ~np.isin(top, list(open_ids))
        sealed_total += int(sealed.sum()) * step
    cup = sealed_total * pitch * pitch / max(nz, 1)
    return peel, cup


def optimize(mesh, overhang_deg=30.0, protect=(), n_dirs=600, top_k=12, pitch=0.5,
             w=dict(support=1.0, protect=10.0, peel=0.6, cup=4.0, height=0.5)):
    protect = [np.asarray(p, float) / np.linalg.norm(p) for p in protect]
    dirs = fib_sphere(n_dirs)
    scale = mesh.area                                      # normalise areas by total surface
    cheap = []
    for up in dirs:
        s, sp = support_terms(mesh, up, overhang_deg, protect)
        h = float(np.ptp(mesh.vertices @ up))
        c = w["support"] * s / scale + w["protect"] * sp / scale + w["height"] * h / 100.0
        cheap.append((c, up, s, sp, h))
    cheap.sort(key=lambda t: t[0])
    # keep diverse top candidates (>= 8 deg apart)
    picked = []
    for c in cheap:
        if all(np.degrees(np.arccos(np.clip(c[1] @ p[1], -1, 1))) > 8 for p in picked):
            picked.append(c)
        if len(picked) >= top_k:
            break
    xy = mesh.extents.max()
    pts = solid_points(mesh, pitch * 0.5)
    res = []
    for c, up, s, sp, h in picked:
        R = rot_to_z(up)
        v = mesh.vertices @ R.T
        ext = np.ptp(v, axis=0)
        fits = (min(ext[0], ext[1]) <= min(BUILD[:2]) and max(ext[0], ext[1]) <= max(BUILD[:2])
                and ext[2] <= BUILD[2])
        peel, cup = peel_and_cup(None, pitch, solid=grid_from_points(pts, R, pitch))
        total = c + w["peel"] * peel / (xy * xy) + w["cup"] * cup / (xy * xy) + (0 if fits else 1e3)
        res.append(dict(score=total, up=up.tolist(), R=R.tolist(), support_mm2=round(s, 1),
                        support_on_protected_mm2=round(sp, 1), height_mm=round(h, 2),
                        peel_mm2=round(peel, 1), cup_mm2=round(cup, 2), fits=bool(fits),
                        tilt_deg=round(float(np.degrees(np.arccos(np.clip(abs(up[2]), 0, 1)))), 1)))
    res.sort(key=lambda r: r["score"])
    return res


def apply(path_in, path_out, R):
    m = trimesh.load(path_in, force="mesh")
    m = oriented(m, np.asarray(R))
    m.export(path_out)
    return m.extents.tolist()


if __name__ == "__main__":
    import json
    import sys
    p = sys.argv[1]
    prot = json.loads(sys.argv[2]) if len(sys.argv) > 2 else []
    m = trimesh.load(p, force="mesh")
    for r in optimize(m, protect=prot)[:5]:
        print(json.dumps({k: v for k, v in r.items() if k != "R"}))
