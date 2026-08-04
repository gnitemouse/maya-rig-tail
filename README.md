# Rig Tail

A modular Maya rigging system for creating stretchy tails with IK/FK modes.

Three tools, run in order. Only the last one is required:

```
[Chain Builder]  ->  [Tail Setup]  ->  [Tail Build]
 joint positions      orient / mirror   the rig
   (optional)           (optional)
```

## Features

- **Spline IK** - Smooth curve-based tail deformation with a fixed
  bot/mid/top control structure
- **IK & Float modes** - Chained or free-floating cluster controls
  (count set by `NUM_CTRL_IK`)
- **Variable FK** - Sliding FK controls with falloff rotation
  (count set by `NUM_CTRL_FK`)
- **IK/FK Switching** - Switch between modes
- **Stretch/Squash** - Optional volume preservation
- **Wave/Curl FX** - Built-in procedural animation effects
- **Main Controller** - Optional cog dashboard driving every tail at once,
  with a per-tail override (for multi-tail rigs)
- **Chain Builder** - Optional pre-Setup step that creates BN chains and
  re-spaces existing ones at any joint count
- **Setup phase** - Optional pre-build step that orients each chain (fixes
  twist), mirrors matching L/R tails (orientation and/or positions), and
  rolls individual chains onto the right plane
- **Skin preservation** - Rebuilds keep existing skinClusters and their
  painted weights (toggle in the Build UI)
- **JSON Configs** - Save/load custom configuration through UI

## Install

The tool ships as a ready-to-use Maya module. The repo layout is:

```
install.py       drag-and-drop installer
uninstall.py     drag-and-drop uninstaller
rigTail.mod      module descriptor, for installing by hand
                 (the installer writes its own)
rigTail/
    scripts/     rig_tail*.py + logger_config.py
    icons/       octopus_{white,light_grey,dark_grey,black}.png
                 (+ _200 variants)
```

**Drag-and-drop (recommended)**

Drag `install.py` from a file browser into the Maya viewport. It asks where
to install and which shelf buttons to add, then puts them on the active
shelf: **ChainBuild**, **TailSetup**, **TailBuild** and **TailReload**. All
four are ticked by default; **TailBuild** is the tool itself, so its tick is
greyed out and always installs. Works immediately, with no restart and no
`userSetup.py` edits. Keep `install.py` next to `rigTail/` when you drag it.

| Choice | What it does |
| --- | --- |
| **Default (maya/modules)** | Copies `rigTail/` into `~/Documents/maya/modules/`. Self-contained - this folder can then be moved or deleted. |
| **Current (this folder)** | Copies nothing; runs from where it already is, so a `git pull` takes effect on the next click. Moving the folder breaks it. |
| **Other...** | Pick a folder in a file browser; `rigTail/` is copied into it - for a shared network location or a per-project tools folder. |

The two copying choices install only the files the ticked buttons need, so
unticking Chain Builder leaves the `rig_tail_chain_*` modules behind, and
re-running with fewer buttons removes the ones dropped. **Current (this
folder)** never copies or deletes anything - a clone keeps every file it has
and only the shelf buttons follow the ticks.

Whichever is chosen, one resolved path drives everything:

- `rigTail.mod` is written to `~/Documents/maya/modules/` (the only place
  Maya scans) pointing at the chosen location - relative when the tree sits
  alongside it, absolute otherwise. So a clone or a picked folder is
  registered on every Maya start, and `import rig_tail` works in a bare
  Script Editor, not just from the shelf buttons.
- `TOOL_DIR` is baked into every shelf button, which put it at the front of
  `sys.path`. A button therefore always runs the install it was made from,
  even with another copy of Rig Tail registered as a module.
- `rigTail.install.json` records what went where, so `uninstall.py` knows
  what to remove.

**Re-installing** over an existing install overwrites it file by file rather
than deleting it first, so a file Windows has locked costs that file rather
than the whole tool, and the installer says which one. Files left behind by
an older version are cleared afterwards. If Maya has already imported the
old modules, click **TailReload** (or restart Maya) after installing - the
files on disk change, but code already loaded into the session does not.

**Manual**

Copy `rigTail/` and `rigTail.mod` into `~/Documents/maya/modules/` (create
the `modules` folder if needed), then restart Maya. Launch from the Script
Editor with `import rig_tail; rig_tail.main()`, or make a shelf button that
runs the same two lines.

**Uninstall** - drag `uninstall.py` into the viewport. It reads
`rigTail.install.json` (falling back to the `.mod`, then to the `TOOL_DIR`
baked into the shelf buttons) to find the install wherever it went, then
removes the buttons, the `.mod` and the module folder. A folder it only
pointed at - a clone installed with **Current** - is left untouched, and
deleting a copy outside the Maya modules folder asks first. By hand: delete
`rigTail.mod` and `rigTail.install.json` from `~/Documents/maya/modules/`,
delete the `rigTail` folder if it was copied there, and remove the shelf
buttons.

## Quick Start

```python
import rig_tail as rt

# --- Chain Builder (optional; before Setup, on the raw BN skeleton) ---
import rig_tail_chain_build as rt_chain
rt_chain.rebuild_selected(21, 'keep')               # re-space, same profile
rt_chain.rebuild_selected(30, 'power', param=1.7)   # taper base -> tip

import rig_tail_chain_build_ui as rt_chain_ui
rt_chain_ui.show_ui()   # or launch the Chain Builder UI

# --- Setup phase (optional; orient / mirror before building) ---
rt.setup_tails(root='tail', dry_run=True)   # preview only, logs the changes
rt.setup_tails(root='tail')                 # apply
rt.main_setup()                             # or launch the Setup UI

# --- Build ---
# a single tail from a single joint chain
rt.rig_tail_single(root='tail', fk=True, ik=True)
# multiple tails, each part defined in rig_tail_constants.RIGPARTS
rt.rig_tail_multiple(root='tail', fk=True, ik=True)
rt.main()                                   # or launch the Builder UI
```

Editing a module mid-session? Importing again on its own will not pick the
change up. The three tool buttons reload every `rig_tail*` module except
`rig_tail_constants`; click **TailReload** (see [Tail Reload](#tail-reload))
when that one changes, or for a clean session.

## Rig Hierarchy

```
ROOT                                        (ROOT_GRP)

- geometry                                  (GEOMETRY_GRP)

- controls                                  (CONTROL_GRP)
    └─ ROOT_ctrl                            (ROOT_CTRL)
       └─ cog_ctrl                          (COG_CTRL)
          └─ {rigname}_base_ctrl_grp        (BASECTRL_GRP)
             └─ {rigname}_base_ctrl         (BASECTRL)
                └─ FK_{rigname}_root_grp    (CTRLROOT_GRP)
                   └─ {rigname}_NN_ctrl_grp (CTRL_GRP)
                      └─ {rigname}_NN_ctrl  (CTRL)

- skeleton                                  (SKELETON_GRP)
    └─ BN_{rigname}_NN_jnt                  (JNT)

- FK_skeleton                               (FK_SKELETON_GRP)
    └─ FK_{rigname}_grp                     (FK_GRP)
       └─ FK_{rigname}_NN_01_sdk            (SDK_GRP)
          └─ FK_{rigname}_NN_02_sdk
             └─ FK_{rigname}_NN_03_sdk
                └─ FK_{rigname}_NN_jnt_sdk  (SDK_JNT)
                   └─ FK_{rigname}_NN_jnt   (JNT)

- IK_skeleton                               (IK_SKELETON_GRP)
    └─ IK_{rigname}_grp                     (IK_GRP)
       └─ IK_{rigname}_NN_jnt               (IK Joints)
```

The BN chain is the input; everything else is generated. Component naming
can be changed through the UI or in `rig_tail_constants.py`.

## Chain Builder (optional)

Launch from the **ChainBuild** shelf button or
`rig_tail_chain_build_ui.show_ui()`. It creates BN joint chains between two
objects and re-spaces existing ones at any joint count, before Setup and
before any rig exists.

Pick the chains (**Select** reads them from the viewport, one entry per
chain however many of their joints are picked), set **Joint Count**, choose
a **Spacing** profile — *Keep* holds the current pattern, *Uniform* evens the
segments, and *Power* and *Ratio* both taper the segments from long at the
base to short at the tip — and click **Build Joints**. One click, one undo
step, every listed chain. A rebuild covers the whole chain by default;
**Build from selected joint** narrows it to the run below the joint that was
picked.

### Highlights

- Joints are reused in place, so names, rotate orders and custom attributes
  survive wherever the count allows.
- Shrinking is the only lossy operation: re-spacing at an unchanged count,
  and re-applying a profile to its own result, both leave the chain put.
- Each chain's original shape is remembered per session and always
  re-measured from, so 18 → 14 → 22 joints costs one lossy pass, not three.
  **Bake Joint Chain** makes the current shape the new baseline.
- Refuses rather than half-applies: a skinned, rig-driven, branching or
  degenerate chain is reported and skipped, and the other chains still run.
- Removable: no core module imports it (a test enforces this), it stores
  nothing in the scene and keeps no config file.

### Modules

| Module | Alias | Purpose |
|--------|-------|---------|
| `rig_tail_chain_spacing` | `rt_chain_spacing` | Spacing maths; no Maya imports |
| `rig_tail_chain_build` | `rt_chain` | Maya layer: detect, guard, create, write |
| `rig_tail_chain_build_ui` | `rt_chain_ui` | Joint Chain Builder window |
| `rig_tail_chain_test` | `rt_chain_test` | Tests; the math half runs outside Maya |

## Tail Setup (optional)

Launch from the **TailSetup** shelf button or `rig_tail.main_setup()`. It
re-orients the raw BN skeleton so tails move coherently, and never runs
during the build. Three independent toggles: **Orient Joints** (stop the
up-axis twisting down the chain), **Mirror Orient** (make matching `L_`/`R_`
tails face as mirror images) and **Mirror Joints** (mirror their positions
too). **Roll Chain** is a separate per-tail fix-up that rolls listed chains
onto the right plane without moving a joint.

A typical run enables Orient Joints + Mirror Orient, adding Mirror Joints
only when the two sides are positionally off. Enable **Dry Run** first to
log the intended changes without touching anything. Skip the phase entirely
if the skeleton is already oriented.

### Highlights

- Everything except Mirror Joints preserves joint positions.
- Orient runs before the mirrors, so one run with both ticked is always
  correct.
- Up Mode *Cascade* (default) keeps the roll a chain already has, so a
  mirrored pair and a Roll Chain fix-up survive a re-run; *Best-fit*
  re-derives roll from the bend plane and overwrites both.
- **Edit Rig Parts** splits the roster into Include / Exclude; an excluded
  tail is left alone by Setup *and* by the build, so a finished tail can be
  frozen while the rest is iterated on.

### Modules

| Module | Alias | Purpose |
|--------|-------|---------|
| `rig_tail_setup` | `rt_setup` | Orient / mirror / roll the BN skeleton |
| `rig_tail_setup_ui` | `rt_setup_ui` | Tail Rig Setup window |
| `rig_tail_setup_test` | `rt_setup_test` | Tests for the Setup phase |

## Tail Build

Launch from the **TailBuild** shelf button or `rig_tail.main()`. Pick the
build options (FK/IK, stretch, FX, Main Controller) and click **Build Rig**:
tails whose joints are unchanged since the last build are kept as they are,
so iterating is fast. **Force Rebuild** tears everything down first — a
one-click action that is never saved as a setting.

**Preserve skinClusters** (on by default) keeps existing skins across
rebuilds: rig joints are added to the cluster (new ones at weight 0) and
painted weights survive. Turn it off to unbind and rebind from scratch.

### Highlights

- BN joints are driven purely through `offsetParentMatrix` — no
  constraints, no Euler decomposition, and joint channels stay zeroed.
- Rebuild-safe: nodes are found by templated name and reused, and a stored
  rest pose keeps repeated rebuilds from degrading the curve.
- Skin- and shape-preserving: painted weights and hand-edited control
  shapes survive a rebuild.
- Excluded rig parts are frozen: the build neither tears them down nor
  rebuilds them.

### Modules

| Module | Alias | Purpose |
|--------|-------|---------|
| `rig_tail` | `rt` | Entry point and build orchestration |
| `rig_tail_build_ui` | `rt_build_ui` | Tail Rig Builder window |
| `rig_tail_cleanup` | `rt_cleanup` | Teardown + build-structure setup |
| `rig_tail_control` | `rt_control` | Control creation |
| `rig_tail_curve` | `rt_curve` | Curve/spline creation |
| `rig_tail_fk` | `rt_fk` | FK system building |
| `rig_tail_stretch` | `rt_stretch` | Stretch system |
| `rig_tail_connect` | `rt_connect` | IK/FK connections |
| `rig_tail_anim` | `rt_anim` | Animation effects |
| `rig_tail_ctrlall` | `rt_ctrlall` | Main Controller dashboard (multi-tail) |
| `rig_tail_build_test` | `rt_build_test` | Diagnostics for a built rig |

Shared by all three tools:

| Module | Alias | Purpose |
|--------|-------|---------|
| `rig_tail_constants` | `rt_constants` | Global constants, settings and caches |
| `rig_tail_naming` | `rt_naming` | Naming templates and name parsing |
| `rig_tail_maya` | `rt_maya` | Maya scene/node operations |
| `rig_tail_joint` | `rt_joint` | Joint chain utilities |
| `rig_tail_math` | `rt_math` | Vector/matrix math |
| `rig_tail_matrix` | `rt_matrix` | Matrix network builder |
| `rig_tail_cache` | `rt_cache` | Control caching |
| `rig_tail_restpose` | `rt_rest` | Rest-pose store for the IK rebuild fix |
| `logger_config` | - | Shared logging setup |

## Tail Reload

The **TailReload** button opens no window. It drops every `rig_tail*` module
(and `logger_config`) from `sys.modules`, imports them all again under the
aliases above, and leaves a commented workflow in the Script Editor — chain,
setup, build and test calls — ready to uncomment.

Use it after editing a module, after re-installing, or to get a clean
session.

The other three buttons reload every `rig_tail*` module **except**
`rig_tail_constants`, so an edit anywhere in the package — including the
shared core (`rig_tail_maya`, `rig_tail_joint`, `rig_tail_naming`,
`rig_tail_cleanup`) — is picked up by launching the tool itself. They hold
`rig_tail_constants` back on purpose: it carries the roster and settings for
the session, and re-importing it would discard RIGPARTS edits made in the UI
but not yet saved to a config.

**TailReload** is therefore the only button that picks up an edit to
`rig_tail_constants` without restarting Maya. The trade-off is that it
resets that session state: the settings edited in the UI return to the
loaded config.

## Requirements

- Maya 2020+ (Python 3)
- UI was tested in Maya 2024, 2025
- Joint chain following naming template (customizable)

## Configuration

Customize through the UI editors (Edit Rig Parts, Edit Naming, Edit
Constants) or by editing `rig_tail_constants.py`:
- Naming templates
- Rig component names and root name
- IKFK switch mode names (`IKFK_MODES_ALL`, positional:
  SplineIK, IK, Float, FK - e.g. `['spline', 'ik', 'float', 'fk']`)
- Number of controls (`NUM_CTRL_FK`, `NUM_CTRL_IK`); the SplineIK
  bot/mid/top set stays fixed while IK/Float control counts vary
- Effect settings (stretch, wave, curl, noise, loop)
- Control colors and shapes

All settings round-trip through a JSON config file with the UI's Load/Save
Config buttons, so setups can be shared per show or user.

Full API reference, architecture notes and the reasoning behind each design
decision: [rig_tail_documentation.md](rig_tail_documentation.md).

## Credits

Shelf icon: [Octopus](https://icons8.com/icons/set/octopus) icon by
[Icons8](https://icons8.com). Free use requires this link; keep it if you
ship the icon, or replace `icons/octopus*.png` with your own art.

## License

Released under the [MIT License](LICENSE) — free to use, modify, and
redistribute, provided the copyright notice and license text are retained.

© 2026 Daisy Jane (@gnitemouse)
