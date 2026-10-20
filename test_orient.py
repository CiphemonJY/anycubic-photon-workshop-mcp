"""Detector self-tests for orient.py: each cost term must fire on a shape built to trigger it
and stay quiet on its mirror image."""
import numpy as np
import trimesh
import orient as O


def cup(open_up=True):
    outer = trimesh.creation.box((30, 30, 15))
    inner = trimesh.creation.box((26, 26, 15))
    inner.apply_translation((0, 0, 2))           # cavity open toward +z, 2 mm floor
    m = outer.difference(inner)
    if not open_up:
        m.apply_transform(trimesh.transformations.rotation_matrix(np.pi, (1, 0, 0)))
    return m


ok = True
up_cup = O.peel_and_cup(O.oriented(cup(True), np.eye(3)))[1]
dn_cup = O.peel_and_cup(O.oriented(cup(False), np.eye(3)))[1]
print("cup opening toward FEP (+z): cup=%.1f   toward plate: cup=%.1f" % (up_cup, dn_cup))
ok &= up_cup > 50 and dn_cup < 1
# the fast path used by optimize(): voxelise once, rotate the points
pts = O.solid_points(cup(True), 0.25)
g_up = O.peel_and_cup(None, 0.5, solid=O.grid_from_points(pts, np.eye(3), 0.5))[1]
g_dn = O.peel_and_cup(None, 0.5, solid=O.grid_from_points(pts, O.rot_to_z(np.array([0, 0, -1.0])), 0.5))[1]
g_tl = O.peel_and_cup(None, 0.5, solid=O.grid_from_points(pts, O.rot_to_z(np.array([0, np.sin(0.6), -np.cos(0.6)])), 0.5))[1]
print("fast path: toward FEP cup=%.1f  toward plate cup=%.1f  tilted-to-plate cup=%.1f" % (g_up, g_dn, g_tl))
ok &= g_up > 50 and g_dn < 1 and g_tl < 1

plate = trimesh.creation.box((60, 40, 2))
flat = O.peel_and_cup(O.oriented(plate, np.eye(3)))[0]
tilt = O.peel_and_cup(O.oriented(plate, O.rot_to_z(np.array([0, np.sin(0.7), np.cos(0.7)]))))[0]
print("plate peel flat=%.0f mm2  tilted 40deg=%.0f mm2" % (flat, tilt))
ok &= flat > 2000 and tilt < flat / 2

s_dn, _ = O.support_terms(plate, np.array([0, 0, 1.0]), 30, [])
print("flat plate support area (bottom face) = %.0f mm2 (expect ~2400)" % s_dn)
ok &= abs(s_dn - 2400) < 50
_, sp = O.support_terms(plate, np.array([0, 0, 1.0]), 30, [np.array([0, 0, -1.0])])
print("same, bottom marked protected -> protected support = %.0f" % sp)
ok &= abs(sp - 2400) < 50
best = O.optimize(cup(True), protect=[(0, 0, -1)], n_dirs=200, top_k=6)[0]
print("optimizer on the cup with its outer floor protected: up=%s cup=%.1f tilt=%.0f"
      % (np.round(best["up"], 2), best["cup_mm2"], best["tilt_deg"]))
ok &= best["up"][2] < 0 and best["cup_mm2"] < 1     # floor faces away from the plate => opening to plate
print("ALL PASS" if ok else "FAIL")
