"""Thin UI-Automation driver for Anycubic Photon Workshop (Qt Quick / QML app).

Photon Workshop has no API or CLI, but its QML controls are exposed through Windows UI
Automation with their visible text as the name, so we drive it the way a user would:
find a control by name, invoke it, read the result back from the tree / a screenshot.
"""
import os
import subprocess
import time

from pywinauto import Desktop, mouse, keyboard

EXE = r"C:\Program Files\AnycubicPhotonWorkshop\AnycubicPhotonWorkshop.exe"
TITLE_RE = r"AnycubicPhotonWorkshop.*"


def window(timeout=0):
    """The main Photon Workshop window (UIA wrapper), or None."""
    end = time.time() + timeout
    while True:
        for w in Desktop(backend="uia").windows():
            if w.window_text().startswith("AnycubicPhotonWorkshop"):
                return w
        if time.time() >= end:
            return None
        time.sleep(0.5)


def launch(files=()):
    w = window()
    if w is None:
        subprocess.Popen([EXE, *files], cwd=os.path.dirname(EXE))
        w = window(timeout=60)
        if w is None:
            raise RuntimeError("Photon Workshop did not open within 60 s")
        time.sleep(3)
        return w, "launched"
    if files:
        # A second process with file args hands them to the running instance? Unverified, so
        # import through the app's own File > Open dialog instead.
        import_files(files)
    return w, "already running"


def elements(win=None, max_nodes=3000):
    """Flat list of (depth, control_type, name, rect, wrapper) for the whole window."""
    win = win or window()
    out = []

    def walk(el, depth):
        if len(out) >= max_nodes:
            return
        try:
            ei = el.element_info
            out.append((depth, ei.control_type, ei.name or "", ei.rectangle, el))
            for c in el.children():
                walk(c, depth + 1)
        except Exception:
            pass

    walk(win, 0)
    return out


def find(name, control_type=None, exact=True, index=0, win=None):
    hits = []
    for d, ct, n, r, el in elements(win):
        if control_type and ct != control_type:
            continue
        if (n == name) if exact else (name.lower() in n.lower()):
            if r.width() > 0 and r.height() > 0:
                hits.append(el)
    if len(hits) <= index:
        return None
    return hits[index]


def click(el_or_name, control_type=None, double=False, settle=0.6):
    el = el_or_name
    if isinstance(el_or_name, str):
        el = find(el_or_name, control_type)
        if el is None:
            # labels are often a Text child inside the clickable item
            el = find(el_or_name, None)
        if el is None:
            raise LookupError("no visible control named %r" % el_or_name)
    r = el.element_info.rectangle
    x, y = (r.left + r.right) // 2, (r.top + r.bottom) // 2
    (mouse.double_click if double else mouse.click)(coords=(x, y))
    time.sleep(settle)
    return (x, y)


def click_xy(x, y, settle=0.6):
    mouse.click(coords=(int(x), int(y)))
    time.sleep(settle)


def screenshot(path, win=None):
    from PIL import ImageGrab
    win = win or window()
    win.set_focus()
    time.sleep(0.3)
    r = win.rectangle()
    img = ImageGrab.grab(bbox=(r.left, r.top, r.right, r.bottom), all_screens=True)
    img.save(path)
    return path, (r.left, r.top)


IMPORT_BTN = (88, 18)    # window-relative: the toolbar "import model" icon (Ctrl+O opens *scene* files)


def _dialog(title, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        for c in window().children():
            if c.element_info.control_type == "Window" and c.window_text() == title:
                return c
        time.sleep(0.3)
    return None


def import_files(files):
    """Import models through the toolbar's Import Files dialog (a native child of the main window)."""
    win = window()
    win.set_focus()
    r = win.rectangle()
    click_xy(r.left + IMPORT_BTN[0], r.top + IMPORT_BTN[1], settle=0.5)
    dlg = _dialog("Import Files")
    if dlg is None:
        raise RuntimeError("Import Files dialog did not appear")
    by_dir = {}
    for f in files:
        by_dir.setdefault(os.path.dirname(os.path.abspath(f)), []).append(os.path.basename(f))
    for i, (folder, names) in enumerate(by_dir.items()):
        if i:
            click_xy(r.left + IMPORT_BTN[0], r.top + IMPORT_BTN[1], settle=0.5)
            dlg = _dialog("Import Files")
        edit = [e for e in dlg.descendants(control_type="Edit") if e.window_text() == "File name:" or e.element_info.name == "File name:"][0]
        edit.set_edit_text(folder)          # navigate first, then multi-select by quoted names
        edit.type_keys("{ENTER}")
        time.sleep(0.8)
        edit.set_edit_text(" ".join('"%s"' % n for n in names))
        edit.type_keys("{ENTER}")
        end = time.time() + 10
        while _dialog("Import Files", timeout=0.5) and time.time() < end:
            pass
        if _dialog("Import Files", timeout=0.2):
            raise RuntimeError("Import Files dialog stayed open (bad file name?)")
        time.sleep(2)


def value(el):
    """Current text/state of an Edit / ComboBox / CheckBox."""
    try:
        ct = el.element_info.control_type
        if ct == "CheckBox":
            return bool(el.get_toggle_state())
        if ct == "Edit":
            return el.iface_value.CurrentValue
        return el.selected_text() if hasattr(el, "selected_text") else el.window_text()
    except Exception:
        try:
            return el.legacy_properties().get("Value")
        except Exception:
            return None


def dump(win=None, named_only=True):
    lines = []
    for d, ct, n, r, el in elements(win):
        if named_only and not n and ct not in ("Button", "CheckBox", "Edit", "ComboBox", "RadioButton", "Slider", "Spinner"):
            continue
        if r.width() <= 0 or r.height() <= 0:
            continue
        val = ""
        if ct in ("Edit", "ComboBox", "CheckBox"):
            val = " = %r" % value(el)
        lines.append("%s%s %r%s @(%d,%d %dx%d)" % ("  " * min(d, 12), ct, n, val, r.left, r.top, r.width(), r.height()))
    return "\n".join(lines)
