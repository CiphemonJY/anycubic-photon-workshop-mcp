# photon-workshop-mcp

An MCP server that prepares resin prints in **Anycubic Photon Workshop** (tested on 4.2.1, Windows):
it orients each model, loads it, repairs flagged meshes, auto-supports, and lays out the plate.

Photon Workshop has no API or CLI. Its Qt Quick controls are exposed through Windows UI Automation
by their visible text, so the server drives the app the way a user would (`pwlib.py`) and returns a
screenshot or a state read back from the UI after each action.

Photon Workshop has no auto-orient, so orientation is computed here (`orient.py`) and baked into
`<name>_oriented.stl` before loading. Supports, repair, layout and slicing use Photon Workshop's own engine.

## Orientation scoring

Inverted MSLA frame: plate at z=0, layers grow toward +z, the FEP caps the current layer.
About 600 candidate "up" directions are scored on:

| term | meaning |
|---|---|
| support | down-facing area steeper than the overhang angle; area on **protected** (cosmetic) normals counts x10 |
| peel | largest single-layer cross-section |
| cup | resin cavities sealed against the FEP during printing (voxel flood-fill per layer) - suction cups |
| height | print height (time), small weight |

The cheap terms rank every direction; peel and cup are evaluated on the best 12.
`python test_orient.py` checks each term on shapes built to trigger it: a cup facing the FEP must
score as a suction cup, the same cup facing the plate must not, a flat plate must peel more than a
tilted one, and protected faces must end up facing away from the plate.

## Tools

| tool | does |
|---|---|
| `pw_prepare(paths, protect)` | whole job: orient, load onto a cleared plate, repair, auto-support, lay out |
| `pw_orient(paths, protect, overhang_deg)` | orientation only (writes `*_oriented.stl`, optionally loads) |
| `pw_repair()` | repair every object flagged "Deficient model" |
| `pw_support(script, support_angle, platform_only)` | auto-generate supports for all objects |
| `pw_layout()` | Layout All |
| `pw_slice()` | Slice Plate |
| `pw_save_slice(path, overwrite=False)` | save the sliced plate to `path` (never answers Yes to a replace prompt unless `overwrite` and it names `path`) |
| `pw_check_slice(path)` | independent post-slice check with [UVtools](https://github.com/sn4k3/UVtools), see below |
| `pw_open`, `pw_clear_scene`, `pw_select`, `pw_rotate` | scene control |
| `pw_status`, `pw_screenshot` | objects, flagged meshes, layer/exposure settings, screenshot |
| `pw_click`, `pw_keys`, `pw_controls` | escape hatches: click by name or position, send keys, list controls |

`protect` maps a file name to cosmetic face normals in that file's own coordinates, for example
`{"case_front.stl": [[0, 0, -1]]}` keeps the -Z face (a screen face) free of supports.

## Post-slice check (UVtools)

`pw_check_slice` runs `UVtoolsCmd print-issues` (islands, resin traps, suction cups, empty layers,
print height, touching bounds) on the sliced file. This checks the layer images the printer will
actually expose, independently of `orient.py`'s mesh-based score. `uvcheck.py`:

- converts pixels and layer indices to mm using the file's own resolution, display size and layer height;
- splits issues into the **raft zone** (the bottom `raft_mm`, default 1.0) and the **model zone**.
  Photon Workshop's honeycomb raft always reads as hundreds of suction cups (each hex pocket,
  about 22 mm3, is sealed by the build plate), so raft-zone issues are counted, with their largest
  size reported, but not flagged. The exception is a raft-zone suction cup bigger than
  `raft_pocket_max_mm3` (default 40), which cannot be a pocket and is flagged. For a plate printed
  without a raft, pass `raft_mm=0`;
- flags model-zone suction cups and resin traps of 5 mm3 or more and islands of 0.05 mm2 or more
  (all adjustable). The verdict is `PASS` or `WARN`. An issue line it cannot parse always forces `WARN`.

`python test_uvcheck.py` checks the parser and flags on recorded UVtools output, including the
mangled unit glyph UVtools prints under some console code pages.

Example, the 4-part plate this was built on: 307 suction cups reported, 251 of them in the raft;
largest model-zone cup 1.5 mm3, largest island 0.006 mm2 (about one pixel), so the verdict is `PASS`.
UVtools' resin-trap count varies slightly between runs (2049-2061 on the same file, all under 0.11 mm3).

UVtools is found via `UVTOOLS_CMD`, `../UVtools/v7.1.0/UVtoolsCmd.exe`, `C:\Program Files\UVtools`
or `PATH`. Tested with UVtools 7.1.0. A full plate takes about 1-2 minutes.

## Install

Windows only (UI Automation). Tested with Python 3.12, Photon Workshop 4.2.1 at 100% display scaling.

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
```

Register with Claude Code (`~/.claude.json`, top-level `mcpServers`):

```json
"photon-workshop": {
  "type": "stdio",
  "command": "C:/path/to/photon_mcp/.venv/Scripts/python.exe",
  "args": ["C:/path/to/photon_mcp/server.py"],
  "env": {"PYTHONIOENCODING": "utf-8"}
}
```

`python smoke_mcp.py [sliced-file]` starts the server over stdio, lists its tools, calls `pw_status`
and, given a sliced file, `pw_check_slice`.

## Known behaviour and limits

- **Ctrl+O opens scene files, not models.** Models are imported through the toolbar icon, which
  opens an "Import Files" dialog as a child of the main window.
- **Repair drops supports.** Photon Workshop recreates a repaired object as `<name>_fixed` without
  its supports, so `pw_prepare` runs repair, then support, then layout.
- **"Deficient model" is Photon Workshop's own check.** It flagged a mesh that trimesh reports as
  watertight, with 4 bad edges. Trust its flag and repair.
- **The save dialog needs real keystrokes.** Setting its file-name box through UI Automation changes
  what it shows but not what it saves: the file goes to the default name in the last folder. The
  tool types the path and reads it back before clicking Save.
- **Some controls are clicked by position.** The Repair panel buttons, the slice view's Save to Disk
  button, the Placing section headers and the view cube are not exposed to UI Automation. Their positions assume the default
  window layout (tested at 1300x1391), so check the returned screenshot.
- **Fixed build volume.** The build volume in `orient.BUILD` is set for the Photon Mono M5s; change
  it for other printers.
- **English UI assumed.** Controls and dialogs are found by their English labels ("Support",
  "Save to Disk", "Confirm Save As"). On another language a control is not found, or a save waits
  until its timeout; it does not overwrite anything.
- **It moves your mouse.** It drives the real Photon Workshop window, so don't use the app while a
  tool call is running.

## License

MIT - see `LICENSE`. Not affiliated with Anycubic or UVtools.
