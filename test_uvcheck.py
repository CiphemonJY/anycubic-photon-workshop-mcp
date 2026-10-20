"""uvcheck parser/classifier self-test on recorded UVtools 7.1.0 output lines (no UVtools needed).
Each flag must fire on an issue built to trigger it and stay quiet on its counterpart."""
import uvcheck as U

# Two lines carry a mangled unit glyph (seen when UVtoolsCmd runs under another code page).
# M5s geometry as read from a real .pm5s: 19 um x 24 um pixels, 0.05 mm layers
PROPS = {"ResolutionX": 11520, "ResolutionY": 5120, "DisplayWidth": 218.88, "DisplayHeight": 122.88,
         "LayerHeight": 0.05, "LayerCount": 956}
TEXT = """Opening file x.pm5s: Done in 1s
Detecting issues: Done in 1s
Issues: 7
SuctionCup, 0-15  (16), 973589px³, {X=5213,Y=4134,Width=185,Height=168}
SuctionCup, 513-535  (23), 65961px³, {X=6652,Y=808,Width=84,Height=74}
SuctionCup, 300-400  (101), 600000px?, {X=4000,Y=2000,Width=200,Height=200}
Island, 100, 3px², {X=8271,Y=1009,Width=3,Height=1}
Island, 640, 200px�, {X=6831,Y=3551,Width=20,Height=10}
ResinTrap, 16-89  (74), 4775px³, {X=8669,Y=1845,Width=8,Height=7}
EmptyLayer, 955
"""

rows, other = U.parse_issues(TEXT)
ok = len(rows) == 6 and len(other) == 1 and other[0]["type"] == "EmptyLayer"
s = U.summarize(rows, PROPS)
by = {(r["type"], r["l0"]): r for r in rows}
raft_cup, small_cup, big_cup = by[("SuctionCup", 0)], by[("SuctionCup", 513)], by[("SuctionCup", 300)]
tiny_island, big_island = by[("Island", 100)], by[("Island", 640)]
checks = [
    ("pixel pitch 19x24 um", s["pixel_mm"] == [0.019, 0.024]),
    ("raft cup is raft zone, not flagged", raft_cup["zone"] == "raft" and not raft_cup["flag"]),
    ("1.5 mm3 model cup not flagged (< 5)", 1.4 < small_cup["amount"] < 1.6 and not small_cup["flag"]),
    ("13.7 mm3 model cup flagged", big_cup["amount"] > 13 and big_cup["flag"]),
    ("1-pixel island not flagged", not tiny_island["flag"]),
    ("0.09 mm2 island flagged", big_island["amount"] > 0.05 and big_island["flag"]),
    ("z of the big cup is 15.0-20.05 mm", big_cup["z_mm"] == [15.0, 20.05]),
    ("verdict WARN", s["verdict"] == "WARN"),
    ("summary counts", s["by_type"]["SuctionCup"] == {"total": 3, "raft_zone": 1, "model_zone": 2,
                                                       "model_largest": big_cup["amount"],
                                                       "raft_largest": raft_cup["amount"], "flagged": 1}),
]
# A suction cup in the bottom 1 mm far bigger than a raft pocket (~22 mm3) - e.g. a recessed part
# printed straight on the plate - must not hide in the raft count.
huge, _ = U.parse_issues("Issues: 1\nSuctionCup, 2-18  (17), 15000000px³, {X=100,Y=100,Width=900,Height=700}\n")
hs = U.summarize(huge, PROPS)
checks.append(("342 mm3 cup in the raft zone flagged", huge[0]["zone"] == "raft" and huge[0]["flag"]
               and hs["verdict"] == "WARN" and hs["flagged"][0]["zone"] == "raft"))
nr = U.summarize(U.parse_issues(TEXT)[0], PROPS, raft_mm=0)
checks.append(("raft_mm=0 judges the raft-zone cup as model zone", nr["by_type"]["SuctionCup"]["raft_zone"] == 0))

# check(): unparsed issue lines force WARN, and a count mismatch raises. Stub out UVtools.
U.properties = lambda path: dict(PROPS, MachineName="stub")
import os, tempfile
fake = os.path.join(tempfile.mkdtemp(), "x.pm5s")
open(fake, "wb").close()
U._run = lambda args, timeout: "Issues: 1\nEmptyLayer, 955\n"
c1 = U.check(fake)
checks.append(("check(): an EmptyLayer-only file is WARN", c1["verdict"] == "WARN" and c1["other_issues"]))
U._run = lambda args, timeout: "Issues: 3\nIsland, 100, 3px², {X=1,Y=1,Width=3,Height=1}\n"
try:
    U.check(fake)
    raised = False
except RuntimeError:
    raised = True
checks.append(("check(): reported 3 but parsed 1 raises", raised))

for name, c in checks:
    print(("ok   " if c else "FAIL ") + name)
    ok &= bool(c)
quiet = U.summarize([r for r in rows if not r["flag"]], PROPS)
print(("ok   " if quiet["verdict"] == "PASS" else "FAIL ") + "PASS when only sub-threshold issues remain")
ok &= quiet["verdict"] == "PASS"
print("ALL PASS" if ok else "FAIL")
