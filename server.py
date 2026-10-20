"""photon-workshop MCP: orient, support, lay out and slice in Anycubic Photon Workshop.

Photon Workshop (Qt Quick) has no API, so actions go through Windows UI Automation (pwlib),
and every action returns a screenshot path or a state read back from the UI, so the caller can
see what actually happened instead of assuming the click worked.

Orientation is computed here (orient.py: support area, protected cosmetic faces, peel area,
suction cups) and baked into `<name>_oriented.stl`, which is then loaded into Photon Workshop;
supports, layout and slicing use Photon Workshop's own engine.
"""
import json
import os
import re
import time

import numpy as np
import trimesh
from mcp.server.fastmcp import FastMCP, Image

import orient
import pwlib
from pywinauto import keyboard

mcp = FastMCP("photon-workshop")
SHOTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shots")
os.makedirs(SHOTS, exist_ok=True)

VIEWPORT_EMPTY = (480, 300)      # window-relative point in the 3D view, clear of panels/models
LEFT_TOOLS = ("Repair", "Placing", "Edit", "Hollow/Punch", "Support")


def _shot(tag):
    p = os.path.join(SHOTS, "%s_%d.png" % (tag, int(time.time())))
    pwlib.screenshot(p)
    return p


def _image(tag):
    p = _shot(tag)
    return Image(path=p)


def _win():
    w = pwlib.window()
    if w is None:
        raise RuntimeError("Photon Workshop is not running - call pw_open first")
    return w


def _abs(xy):
    r = _win().rectangle()
    return r.left + xy[0], r.top + xy[1]


def _objects():
    """Names in the Object List panel (right side, x > 940)."""
    out = []
    for d, ct, n, r, el in pwlib.elements():
        if ct == "Text" and r.left > 940 and re.search(r"\.(stl|obj|3mf|step|stp|ply|off|igs|iges)(_fixed)?$", n, re.I):
            out.append(n)
    return out


def _deficient():
    """Objects flagged with the Object List warning icon ('Deficient model'). Each row has an
    eye button at x~1241; a flagged row has a second button just left of it (x~1215)."""
    rows = []
    els = pwlib.elements()
    for d, ct, n, r, el in els:
        if ct == "Text" and r.left > 940 and re.search(r"\.(stl|obj|3mf|step|stp|ply|off|igs|iges)(_fixed)?$", n, re.I):
            rows.append((n, (r.top + r.bottom) / 2))
    warn_y = [(r.top + r.bottom) / 2 for d, ct, n, r, el in els
              if ct == "Button" and 1200 <= r.left <= 1230 and r.width() < 30]
    return [n for n, y in rows if any(abs(y - wy) < 8 for wy in warn_y)]


REPAIR_BTN = (341, 471)          # Repair panel buttons are not exposed to UIA (window-relative)
PANEL_X = (404, 66)              # close button shared by the left-toolbar panels


def _panel_open():
    """A left-toolbar panel is open iff something is drawn right of the toolbar at the panel's
    title row: sample the screenshot (panels are white, the empty viewport is light grey)."""
    from PIL import ImageGrab
    x, y = _abs((250, 66))
    px = ImageGrab.grab(bbox=(x, y, x + 1, y + 1), all_screens=True).getpixel((0, 0))
    return px[:3] == (255, 255, 255)


def _close_panel():
    if _panel_open():
        pwlib.click_xy(*_abs(PANEL_X), settle=0.6)


def _tool(name):
    """Open a left-toolbar panel (clicking the active one would close it, so check first)."""
    if pwlib.find("Automatic Generate Supports") and name == "Support":
        return
    if pwlib.find("Rotate by Face") and name == "Placing":
        return
    if pwlib.find("Layout All") and name == "Placing":
        return
    pwlib.click(name, "Button", settle=1.0)


def _select_all():
    _win().set_focus()
    pwlib.click_xy(*_abs(VIEWPORT_EMPTY), settle=0.3)
    keyboard.send_keys("^a")
    time.sleep(0.5)


def _wait_idle(timeout=600, poll=2.0, quiet=3):
    """Wait until the UI tree stops changing for `quiet` polls (no progress dialog / percent)."""
    end = time.time() + timeout
    last, same = None, 0
    while time.time() < end:
        try:
            names = tuple(n for d, ct, n, r, el in pwlib.elements() if n)
        except Exception:
            names = None
        busy = names is None or any(re.search(r"\d+\s*%|Generating|Slicing|Loading|Processing|Please wait", n, re.I)
                                    for n in (names or ()))
        if not busy and names == last:
            same += 1
            if same >= quiet:
                return True
        else:
            same = 0
        last = names
        time.sleep(poll)
    return False


@mcp.tool()
def pw_status() -> str:
    """Photon Workshop state: running?, printer/resin/layer settings, objects on the plate."""
    w = pwlib.window()
    if w is None:
        return json.dumps({"running": False})
    texts = [n for d, ct, n, r, el in pwlib.elements() if ct == "Text" and n]
    st = {"running": True, "title": w.window_text(), "objects": _objects(), "deficient": _deficient()}
    # label/value order differs between the Prepare and Slice views: take the first value of the
    # right shape after the label
    for label, key, pat in (("Layer Thickness", "layer_thickness", r"^[\d.]+\s*mm$"),
                            ("Exposure time", "exposure", r"^[\d.]+\s*s$")):
        if label in texts:
            i = texts.index(label)
            st[key] = next((t for t in texts[i + 1:i + 5] if re.match(pat, t)), None)
    return json.dumps(st, indent=1)


@mcp.tool()
def pw_screenshot() -> Image:
    """Screenshot of the Photon Workshop window."""
    _win()
    return _image("shot")


@mcp.tool()
def pw_open(paths: list[str], clear_scene: bool = False) -> str:
    """Launch Photon Workshop (if needed) and load model files. clear_scene deletes everything
    already on the plate first."""
    paths = [os.path.abspath(p) for p in paths]
    for p in paths:
        if not os.path.isfile(p):
            raise FileNotFoundError(p)
    w = pwlib.window()
    if w is None:
        pwlib.launch(paths)
        _wait_idle(timeout=180)
    else:
        if clear_scene:
            pw_clear_scene()
        pwlib.import_files(paths)
        _wait_idle(timeout=180)
    return json.dumps({"objects": _objects(), "screenshot": _shot("open")})


@mcp.tool()
def pw_clear_scene() -> str:
    """Delete every object on the plate."""
    _select_all()
    keyboard.send_keys("{DELETE}")
    time.sleep(1.0)
    left = _objects()
    return json.dumps({"objects_left": left})


@mcp.tool()
def pw_select(name: str = "all") -> str:
    """Select an object by its Object List name, or 'all'."""
    if name == "all":
        _select_all()
    else:
        pwlib.click(name, "Text")
    return json.dumps({"selected": name})


@mcp.tool()
def pw_orient(paths: list[str], protect: dict = None, overhang_deg: float = 30.0,
              load: bool = True, clear_scene: bool = True) -> str:
    """Compute a resin print orientation for each STL and write `<name>_oriented.stl` beside it.

    protect: {filename: [[x,y,z], ...]} cosmetic face normals in the file's own coordinates that
             must not get supports (they end up facing away from the plate).
    Scores: support area (protected x10), peel cross-section, sealed suction cups, height.
    load: then put the oriented files on the plate in Photon Workshop (clear_scene first)."""
    protect = protect or {}
    report, outs = [], []
    for p in paths:
        p = os.path.abspath(p)
        m = trimesh.load(p, force="mesh")
        prot = protect.get(os.path.basename(p), protect.get(p, []))
        best = orient.optimize(m, overhang_deg=overhang_deg, protect=prot)
        b = best[0]
        out = os.path.splitext(p)[0] + "_oriented.stl"
        ext = orient.apply(p, out, b["R"])
        outs.append(out)
        report.append({"file": os.path.basename(p), "out": out, "up_in_file_coords": np.round(b["up"], 3).tolist(),
                       "tilt_deg": b["tilt_deg"], "height_mm": round(ext[2], 2),
                       "support_mm2": b["support_mm2"], "support_on_protected_mm2": b["support_on_protected_mm2"],
                       "peel_mm2": b["peel_mm2"], "suction_cup_mm2": b["cup_mm2"],
                       "runner_up": {k: best[1][k] for k in ("tilt_deg", "support_mm2", "peel_mm2", "cup_mm2", "score")} if len(best) > 1 else None})
    res = {"parts": report}
    if load:
        res["photon"] = json.loads(pw_open(outs, clear_scene=clear_scene))
    return json.dumps(res, indent=1)


@mcp.tool()
def pw_rotate(x: float = 0.0, y: float = 0.0, z: float = 0.0) -> str:
    """Type absolute X/Y/Z rotation (deg) into the Placing > Rotate fields for the current selection."""
    _tool("Placing")
    edits = [el for d, ct, n, r, el in pwlib.elements() if ct == "Edit" and r.left < 300 and r.top < 260 and r.width() > 100]
    if len(edits) < 3:
        raise RuntimeError("Rotate fields not visible (is the Rotate section expanded?)")
    for el, v in zip(edits[:3], (x, y, z)):
        pwlib.click(el, settle=0.2)
        keyboard.send_keys("^a%s{ENTER}" % ("%.3f" % v))
        time.sleep(0.4)
    return json.dumps({"rotation_now": [pwlib.value(e) for e in edits[:3]], "screenshot": _shot("rotate")})


@mcp.tool()
def pw_layout() -> str:
    """Auto-arrange all objects on the plate (Placing > Layout > Layout All)."""
    _tool("Placing")
    if not pwlib.find("Layout All"):
        # Layout section collapsed: its header is not exposed to UIA, click it by position
        hdr = pwlib.find("Rotate by Face")
        pwlib.click_xy(*_abs((255, 436 if hdr else 235)), settle=0.8)
    pwlib.click("Layout All", "Button", settle=1.0)
    _wait_idle(timeout=120)
    return json.dumps({"screenshot": _shot("layout")})


@mcp.tool()
def pw_support(script: str = "", support_angle: float = None, platform_only: bool = False,
               remove_existing: bool = True, timeout_s: int = 900) -> str:
    """Generate supports for every object with Photon Workshop's auto-support.

    script: preset name in 'Support Script' (e.g. Light / Medium / Heavy); '' keeps current.
    support_angle: overhang angle (deg) above which surfaces get supports; None keeps current.
    platform_only: 'Support on Platform' (supports only from the plate, none on the model)."""
    _tool("Support")
    if script:
        cb = next((el for d, ct, n, r, el in pwlib.elements() if ct == "ComboBox" and r.left < 420), None)
        if cb is not None and cb.element_info.name != script:
            pwlib.click(cb, settle=0.6)
            pwlib.click(script, None, settle=0.8)
    if support_angle is not None:
        edits = [el for d, ct, n, r, el in pwlib.elements() if ct == "Edit" and r.left < 120 and r.width() > 250]
        if edits:
            pwlib.click(edits[0], settle=0.2)
            keyboard.send_keys("^a%.3f{ENTER}" % support_angle)
            time.sleep(0.4)
    _select_all()
    if remove_existing and pwlib.find("Remove All Supports"):
        pwlib.click("Remove All Supports", settle=1.0)
    t0 = time.time()
    if platform_only:
        btn = pwlib.find("Automatic Generate Supports")
        r = btn.element_info.rectangle
        pwlib.click_xy(r.right + 18, (r.top + r.bottom) // 2, settle=0.8)   # the dropdown arrow
        pwlib.click("Support on Platform", settle=1.0)
    else:
        pwlib.click("Automatic Generate Supports", "Button", settle=1.0)
    idle = _wait_idle(timeout=timeout_s)
    return json.dumps({"finished": idle, "seconds": round(time.time() - t0, 1),
                       "settings": _support_settings(), "screenshot": _shot("support")})


def _support_settings():
    out = {}
    els = pwlib.elements()
    for d, ct, n, r, el in els:
        if ct == "ComboBox" and r.left < 420:
            out["script"] = n
    vals = [pwlib.value(el) for d, ct, n, r, el in els if ct == "Edit" and r.left < 420]
    if len(vals) >= 2:
        out["support_angle"], out["anchor_distance"] = vals[0], vals[1]
    return out


@mcp.tool()
def pw_repair() -> str:
    """Repair every object Photon Workshop flags as 'Deficient model' (holes / bad edges).
    The repaired object is re-created as '<name>_fixed' and LOSES its supports, so run this
    before pw_support."""
    fixed = []
    for _ in range(10):
        bad = _deficient()
        if not bad:
            break
        name = bad[0]
        _close_panel()
        pwlib.click(name, "Text", settle=0.5)
        pwlib.click("Repair", "Button", settle=1.0)       # panel is closed, so this opens it
        pwlib.click_xy(*_abs(REPAIR_BTN), settle=2.0)
        _wait_idle(timeout=300)
        fixed.append(name)
        if name in _deficient():
            raise RuntimeError("Photon Workshop could not repair %s" % name)
    _close_panel()
    return json.dumps({"repaired": fixed, "still_deficient": _deficient(), "objects": _objects()})


@mcp.tool()
def pw_prepare(paths: list[str], protect: dict = None, overhang_deg: float = 30.0,
               script: str = "", platform_only: bool = False) -> str:
    """Whole job: orient each STL (pw_orient), load onto a cleared plate, repair flagged meshes,
    auto-support everything, then lay out the plate. Returns per-part orientation metrics,
    support settings and a final screenshot path. Slicing is left to pw_slice."""
    res = json.loads(pw_orient(paths, protect=protect, overhang_deg=overhang_deg, load=True, clear_scene=True))
    res["repair"] = json.loads(pw_repair())
    res["support"] = json.loads(pw_support(script=script, platform_only=platform_only))
    res["layout"] = json.loads(pw_layout())
    res["final"] = json.loads(pw_status())
    return json.dumps(res, indent=1)


@mcp.tool()
def pw_slice(timeout_s: int = 900) -> str:
    """Press 'Slice Plate' and wait for slicing to finish; returns a screenshot of the result.
    Save the result with pw_save_slice and check it with pw_check_slice."""
    pwlib.click("Slice Plate", "Button", settle=2.0)
    idle = _wait_idle(timeout=timeout_s)
    return json.dumps({"finished": idle, "screenshot": _shot("slice")})


SAVE_TO_DISK_FROM_BR = (208, 78)   # slice-view "Save to Disk": not in the UIA tree; offset from window bottom-right


@mcp.tool()
def pw_save_slice(path: str, overwrite: bool = False, timeout_s: int = 600) -> str:
    """Save the sliced plate to `path` (e.g. ...\\plate.pm5s) via Slice view > Save to Disk.
    Slices first if the plate is not sliced yet. Returns once the file exists and stops growing."""
    path = os.path.abspath(path)
    if not os.path.splitext(path)[1]:
        # the dialog would append its own extension, so the pre-check and the wait below would
        # look at the wrong name
        raise ValueError("give the sliced file an extension matching the printer, e.g. .pm5s: %s" % path)
    if os.path.exists(path) and not overwrite:
        raise FileExistsError(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not pwlib.find("Print Setup"):                       # not in the slice view yet
        pwlib.click("Slice Plate", "Button", settle=2.0)
        _wait_idle(timeout=timeout_s)
    r = _win().rectangle()
    dlg = None
    for _ in range(3):           # the first click can land while the slice view is still settling
        pwlib.click_xy(r.right - SAVE_TO_DISK_FROM_BR[0], r.bottom - SAVE_TO_DISK_FROM_BR[1], settle=1.0)
        dlg = pwlib._dialog("Save to Disk", timeout=5)
        if dlg is not None:
            break
    if dlg is None:
        raise RuntimeError("Save to Disk dialog did not appear (screenshot: %s)" % _shot("save_fail"))
    def btn(win, *labels):
        return next(b for b in win.descendants(control_type="Button") if b.window_text() in labels)

    def abort(msg):
        btn(dlg, "Cancel").click_input()
        raise RuntimeError(msg)

    # Setting the box through UIA (set_edit_text) changes what it shows but NOT what the dialog
    # saves - it then saves the default name in the last folder. Type real keystrokes, read back.
    name = next(e for e in dlg.descendants(control_type="Edit") if e.element_info.name == "File name:")
    name.click_input()
    keyboard.send_keys("^a{BACKSPACE}")
    keyboard.send_keys("".join("{%s}" % ch if ch in "+^%~(){}[]" else ch for ch in path), with_spaces=True, pause=0.01)
    time.sleep(0.4)
    if (pwlib.value(name) or "").strip().lower() != path.lower():
        abort("file name box reads %r, not %r - not saving" % (pwlib.value(name), path))
    btn(dlg, "Save", "&Save").click_input()
    time.sleep(1.5)
    # The replace prompt is a child of the Save dialog. Never replace anything unless overwrite=True
    # AND the prompt names our own target file.
    try:
        conf = next((d for d in dlg.descendants(control_type="Window") if "Confirm" in d.window_text()), None)
    except Exception:                 # dialog already closed = saved without a prompt
        conf = None
    if conf is not None:
        text = " ".join(t.window_text() for t in conf.descendants(control_type="Text"))
        if overwrite and os.path.basename(path).lower() in text.lower():
            btn(conf, "Yes", "&Yes").click_input()
        else:
            btn(conf, "No", "&No").click_input()
            time.sleep(0.5)
            abort("Photon Workshop asked to replace a file (%s) - answered No" % text.strip())
    end, last = time.time() + timeout_s, -1
    while time.time() < end:
        if os.path.exists(path):
            size = os.path.getsize(path)
            if size > 0 and size == last:
                return json.dumps({"saved": path, "bytes": size})
            last = size
        time.sleep(2)
    raise TimeoutError("%s did not appear within %d s (screenshot: %s)" % (path, timeout_s, _shot("save_fail")))


@mcp.tool()
def pw_check_slice(path: str, raft_mm: float = 1.0, cup_warn_mm3: float = 5.0,
                   trap_warn_mm3: float = 5.0, island_warn_mm2: float = 0.05,
                   raft_pocket_max_mm3: float = 40.0) -> str:
    """Independent post-slice check with UVtools (suction cups, resin traps, islands, empty
    layers, print height, touching bounds) on a sliced file (.pm5s, .pwmb, .ctb, ...).
    Issues are converted to mm and split into the raft zone (bottom `raft_mm`; Photon Workshop's
    honeycomb raft always reads as suction cups there) and the model zone. Model-zone issues at or
    above the thresholds are flagged, and so is any raft-zone suction cup bigger than a raft
    pocket (raft_pocket_max_mm3). For a plate printed without a raft pass raft_mm=0.
    Verdict PASS/WARN. Takes ~1-2 min for a full plate."""
    import uvcheck
    return json.dumps(uvcheck.check(path, raft_mm, cup_warn_mm3, trap_warn_mm3, island_warn_mm2,
                                    raft_pocket_max_mm3=raft_pocket_max_mm3),
                      indent=1, ensure_ascii=False)


@mcp.tool()
def pw_click(name: str = "", x: int = None, y: int = None) -> str:
    """Escape hatch: click a control by its visible name, or window-relative (x, y)."""
    if name:
        pwlib.click(name)
    else:
        pwlib.click_xy(*_abs((x, y)))
    return json.dumps({"screenshot": _shot("click")})


@mcp.tool()
def pw_keys(keys: str) -> str:
    """Escape hatch: send pywinauto key syntax to Photon Workshop (e.g. '^z', '{DELETE}')."""
    _win().set_focus()
    keyboard.send_keys(keys)
    time.sleep(0.5)
    return json.dumps({"sent": keys})


@mcp.tool()
def pw_controls() -> str:
    """Escape hatch: list the visible UI controls (type, name, value, window-relative rect)."""
    return pwlib.dump()


if __name__ == "__main__":
    mcp.run()
