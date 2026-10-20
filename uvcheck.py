"""Post-slice check of a sliced MSLA file with UVtools (UVtoolsCmd print-issues).

UVtools detects issues in the sliced layer images, which is an independent check of what the
printer will actually expose, and of orient.py's suction-cup score (which works on the mesh).
Its raw output is pixels and layer indices; this converts it to mm, and splits issues into the
raft zone (the support raft at the bottom of the plate - Photon Workshop's honeycomb raft
always shows up as suction cups there, by design) and the model zone, which is what matters.
"""
import json
import os
import re
import shutil
import subprocess

CANDIDATES = [
    os.environ.get("UVTOOLS_CMD", ""),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "UVtools", "v7.1.0", "UVtoolsCmd.exe"),
    r"C:\Program Files\UVtools\UVtoolsCmd.exe",
    shutil.which("UVtoolsCmd") or "",
]

# The unit glyph after "px" (² / ³) arrives mangled depending on the console code page of the
# process that launched UVtoolsCmd, so it is not used: a layer RANGE ("16-89  (74)") marks a
# volume issue, a single layer index marks an area issue.
LINE = re.compile(r"^(\w+), (\d+)(?:-(\d+))?\s*(?:\(\d+\))?, (\d+)px\S*, "
                  r"\{X=(-?\d+),Y=(-?\d+),Width=(\d+),Height=(\d+)\}")


def uvtools_cmd():
    for c in CANDIDATES:
        if c and os.path.isfile(c):
            return os.path.abspath(c)
    raise FileNotFoundError("UVtoolsCmd not found - install UVtools (github.com/sn4k3/UVtools) "
                            "or set UVTOOLS_CMD to UVtoolsCmd.exe")


def _run(args, timeout):
    p = subprocess.run([uvtools_cmd(), "--no-progress", *args], capture_output=True, timeout=timeout,
                       encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise RuntimeError("UVtoolsCmd %s failed (%d): %s" % (args[0], p.returncode, (p.stderr or p.stdout)[-800:]))
    return p.stdout


def properties(path, timeout=300):
    out = _run(["print-properties", path], timeout)
    want = ("ResolutionX", "ResolutionY", "DisplayWidth", "DisplayHeight", "LayerHeight", "LayerCount", "MachineName")
    props = {}
    for line in out.splitlines():
        m = re.match(r"^(\w+): (.*)$", line.strip())
        if m and m.group(1) in want and m.group(1) not in props:
            props[m.group(1)] = m.group(2).strip()
    for k in ("ResolutionX", "ResolutionY", "LayerCount"):
        props[k] = int(props[k])
    for k in ("DisplayWidth", "DisplayHeight", "LayerHeight"):
        props[k] = float(re.sub(r"[^0-9.]", "", props[k]))
    return props


def parse_issues(text):
    """Issue lines after the 'Issues: N' header. Lines with a pixel area/volume and a bounding box
    become full rows; any other issue line (EmptyLayer, PrintHeight, TouchingBound variants)
    is kept as type + raw text so nothing is silently dropped."""
    rows, other, started = [], [], False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("Issues:"):
            started = True
            continue
        if not started or not s:
            continue
        m = LINE.match(s)
        if m:
            t, l0, l1, px, x, y, w, h = m.groups()
            rows.append(dict(type=t, l0=int(l0), l1=int(l1 or l0), px=int(px), volume=l1 is not None,
                             x=int(x), y=int(y), w=int(w), h=int(h)))
        else:
            other.append({"type": s.split(",")[0], "raw": s})
    return rows, other


def summarize(rows, props, raft_mm=1.0, cup_warn_mm3=5.0, trap_warn_mm3=5.0, island_warn_mm2=0.05, top=10,
              raft_pocket_max_mm3=40.0):
    sx = props["DisplayWidth"] / props["ResolutionX"]
    sy = props["DisplayHeight"] / props["ResolutionY"]
    lh = props["LayerHeight"]
    warn_at = {"SuctionCup": cup_warn_mm3, "ResinTrap": trap_warn_mm3, "Island": island_warn_mm2}
    out = {"file_layers": props["LayerCount"], "height_mm": round(props["LayerCount"] * lh, 2),
           "pixel_mm": [round(sx, 4), round(sy, 4)], "raft_mm": raft_mm, "by_type": {}, "flagged": []}
    for r in rows:
        r["z_mm"] = [round(r["l0"] * lh, 2), round((r["l1"] + 1) * lh, 2)]
        r["xy_mm"] = [round(r["x"] * sx, 1), round(r["y"] * sy, 1)]
        r["size_mm"] = [round(r["w"] * sx, 2), round(r["h"] * sy, 2)]
        r["amount"] = round(r["px"] * sx * sy * (lh if r["volume"] else 1.0), 4)
        r["unit"] = "mm3" if r["volume"] else "mm2"
        r["zone"] = "raft" if (r["l1"] + 1) * lh <= raft_mm else "model"
        # A volume issue's amount is mm3; an area issue (single layer) is compared on mm2.
        # Raft zone: Photon Workshop's honeycomb pockets are ~22 mm3 each, so a raft-zone cup bigger
        # than raft_pocket_max_mm3 is not a pocket (e.g. a part printed straight on the plate) - flag it.
        if r["zone"] == "model":
            r["flag"] = r["type"] in warn_at and r["amount"] >= warn_at[r["type"]]
        else:
            r["flag"] = r["type"] == "SuctionCup" and r["amount"] > raft_pocket_max_mm3
    for t in sorted({r["type"] for r in rows}):
        g = [r for r in rows if r["type"] == t]
        m = [r for r in g if r["zone"] == "model"]
        out["by_type"][t] = {"total": len(g), "raft_zone": len(g) - len(m), "model_zone": len(m),
                             "model_largest": max((r["amount"] for r in m), default=0.0),
                             "raft_largest": max((r["amount"] for r in g if r["zone"] == "raft"), default=0.0),
                             "flagged": sum(r["flag"] for r in g)}
    fl = sorted((r for r in rows if r["flag"]), key=lambda r: -r["amount"])
    out["flagged"] = [{k: r[k] for k in ("type", "zone", "z_mm", "xy_mm", "size_mm", "amount", "unit")} for r in fl[:top]]
    out["thresholds"] = {"suction_cup_mm3": cup_warn_mm3, "resin_trap_mm3": trap_warn_mm3, "island_mm2": island_warn_mm2,
                         "raft_pocket_max_mm3": raft_pocket_max_mm3}
    out["verdict"] = "WARN" if fl else "PASS"
    return out


def check(path, raft_mm=1.0, cup_warn_mm3=5.0, trap_warn_mm3=5.0, island_warn_mm2=0.05, timeout=1800,
          raft_pocket_max_mm3=40.0):
    """Run UVtools on a sliced file and return the mm-converted, raft/model-split summary.
    For a plate printed without a raft pass raft_mm=0 so every issue is judged as model zone."""
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    props = properties(path)
    text = _run(["print-issues", path, "-i", "-r", "-s", "-p", "-e", "-t"], timeout)
    rows, other = parse_issues(text)
    m = re.search(r"Issues: (\d+)", text)
    reported = int(m.group(1)) if m else None
    if reported is None or reported != len(rows) + len(other):
        raise RuntimeError("parsed %d issue lines but UVtools reported %s - output format changed?"
                           % (len(rows) + len(other), reported))
    s = summarize(rows, props, raft_mm, cup_warn_mm3, trap_warn_mm3, island_warn_mm2,
                  raft_pocket_max_mm3=raft_pocket_max_mm3)
    if other:                          # empty layers, print height, touching bounds: always worth a look
        s["other_issues"] = other[:20]
        s["verdict"] = "WARN"
    s.update({"file": path, "machine": props.get("MachineName"), "issues_reported": reported})
    return s


if __name__ == "__main__":
    import sys
    print(json.dumps(check(sys.argv[1]), indent=1, ensure_ascii=False))
