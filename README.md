# Rig Tail

A modular Maya rigging system for creating stretchy tails with IK/FK modes.

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
- **Setup phase** - Optional pre-build step that orients each chain (fixes
  twist), mirrors matching L/R tails (orientation and/or positions), and
  rolls individual chains onto the right plane
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
    icons/       octopus{,_black,_grey}.png (+ _200 variants)
```

**Drag-and-drop (recommended)**

Drag `install.py` from a file browser into the Maya viewport. It asks
where to install, then adds three shelf buttons to the active shelf:
**TailSetup** (skeleton orient / mirror), **TailRig** (the builder) and
**TailReload** (load/reload the modules and run commands). Works
immediately, with no restart and no `userSetup.py` edits. Keep
`install.py` next to `rigTail/` when you drag it.

| Choice | What it does |
| --- | --- |
| **Default (maya/modules)** | Copies `rigTail/` into `~/Documents/maya/modules/`. Self-contained - this folder can then be moved or deleted. |
| **Current (this folder)** | Copies nothing; runs from where it already is, so a `git pull` takes effect on the next click. Moving the folder breaks it. |
| **Other...** | Pick a folder in a file browser; `rigTail/` is copied into it - for a shared network location or a per-project tools folder. |

Whichever is chosen, one resolved path drives everything:

- `rigTail.mod` is written to `~/Documents/maya/modules/` (the only place
  Maya scans) pointing at the chosen location - relative when the tree
  sits alongside it, absolute otherwise. So a clone or a picked folder is
  registered on every Maya start, and `import rig_tail` works in a bare
  Script Editor, not just from the shelf buttons.
- `TOOL_DIR` is baked into all three shelf buttons, which put it at the
  front of `sys.path`. A button therefore always runs the install it was
  made from, even with another copy of Rig Tail registered as a module.
- `rigTail.install.json` records what went where, so `uninstall.py` knows
  what to remove.

**TailReload** additionally purges every loaded `rig_tail*` module before
importing, so even `rig_tail_constants` (which the normal reload sweep
skips) is picked up without restarting Maya.

**Re-installing** over an existing install overwrites it file by file
rather than deleting it first, so a file Windows has locked costs that
file rather than the whole tool, and the installer says which one.
Files left behind by an older version are cleared afterwards. If Maya has
already imported the old modules, click **TailReload** (or restart Maya)
after installing - the files on disk change, but code already loaded into
the session does not.

**Manual**

Copy `rigTail/` and `rigTail.mod` into
`~/Documents/maya/modules/` (create the `modules` folder if needed), then
restart Maya. Launch from the Script Editor with `import rig_tail;
rig_tail.main()`, or make a shelf button that runs the same two lines.

**Uninstall** - drag `uninstall.py` into the viewport. It reads
`rigTail.install.json` (falling back to the `.mod`, then to the
`TOOL_DIR` baked into the shelf buttons) to find the install wherever it
went, then removes the buttons, the `.mod` and the module folder. A
folder it only pointed at - a clone installed with **Current** - is left
untouched, and deleting a copy outside the Maya modules folder asks
first. By hand: delete `rigTail.mod` and `rigTail.install.json` from
`~/Documents/maya/modules/`, delete the `rigTail` folder if it was copied
there, and remove the shelf buttons.

## Quick Start

```python
import importlib as il
import rig_tail

il.reload(rig_tail)

# Optional Setup phase: orient / mirror the skeleton before building.
# Preview first (logs only), then apply.
rig_tail.setup_tails(root='tail', dry_run=True)
rig_tail.setup_tails(root='tail')
rig_tail.main_setup()   # or launch the Setup UI

# Build a single tail from a single joint chain
rig_tail.rig_tail_single(root='tail', fk=True, ik=True)
# Build multiple tails with each part defined in rig_tail_constants.RIGPARTS
rig_tail.rig_tail_multiple(root='tail', fk=True, ik=True)
rig_tail.main()         # or launch the Builder UI
```

## Setup phase (optional)

Run before building, from the **TailSetup** shelf button or
`rig_tail.main_setup()`. It re-orients the raw BN skeleton so tails move
coherently, and never affects the build itself. Three independent options:

- **Orient Joints** (`ORIENT_JOINTS`): aim-orient each chain so its up-axis
  stops twisting down the chain. The joint positions fix the aim, so the
  dropdown in the same row only picks the chain's **roll** about it
  (`ORIENT_UP_MODE`): *Cascade* (default) seeds from the chain's own first
  joint as it stands now, so the twist goes but the roll the chain already
  has is kept — a mirrored pair stays mirrored and a **Roll Chain** fix-up
  survives, which makes a re-run safe. *Best-fit* takes the roll from the
  chain's bend plane instead, ignoring how the joints stand now: right for
  the first pass on a raw skeleton, but it overwrites any mirrored or
  hand-rolled orientation.
- **Mirror Orient** (`MIRROR_ORIENT`): reflect matching `L_`/`R_` tails'
  *orientation* so the two sides face as mirror images. The dropdown in the
  same row picks the **behavior** (`MIRROR_BEHAVIOR`): *Symmetric* (default)
  moves the two sides as exact mirrors for the same channel value — curl the
  right tail up and the left curls up too — while *Parallel* moves them
  opposite ways, so a splayed pair reads as one up, one down. The two differ
  by a 180 deg roll about the aim, so **Roll Chain** at 180 flips a single
  tail between them.
- **Mirror Joints** (`MIRROR_JOINTS`): reflect matching `L_`/`R_` tails'
  *positions*, so the target side's joints sit at the exact mirror of the
  source side's.

**Edit Rig Parts** splits the roster into **Include** and **Exclude**
columns — move parts across with the arrow buttons or by double-clicking,
as in Maya's channel editor. Excluded tails stay in `RIGPARTS` and keep
their skin bound; Setup just leaves their joints alone, which is what you
want when fixing one tail without disturbing the rest. Excluding one side
of an L/R pair stops that pair mirroring. The exclusion covers the **build**
too — an excluded tail's rig is neither torn down nor rebuilt — so a
finished tail can be frozen while the rest of the roster is iterated on.

A typical run enables Orient Joints + Mirror Orient, adding Mirror Joints
only when the two sides are positionally off. Everything except Mirror
Joints preserves joint positions. Enable **Dry Run** first to log the
intended changes without modifying anything, then apply and build. Skip
this phase entirely if the skeleton is already oriented.

Orient runs before the mirrors, so a single run with both ticked is always
correct. It is the *second* run that used to undo the first: with
*Best-fit* the orient step re-derives every chain from its bend plane and
throws the mirror away. Leave Up Mode on *Cascade* once a skeleton has been
mirrored or hand-rolled.

**Roll Chain** is a separate per-tail fix-up below the Setup options, for
chains that are cleanly oriented but facing the wrong way: list the chains
in the Chain box (type them comma separated, or **Select** them from
selected joints) and use the left/right arrows to roll them onto the right
plane. Every listed chain is rolled, so a whole set of tails is corrected
in one click. It applies immediately and never moves joints.

A typical pass on a messy rig: run Orient Joints + Mirror Joints, roll any
individual chains that face the wrong way, then build.

## Requirements

- Maya 2020+ (Python 3)
- UI was tested in Maya 2024, 2025
- Joint chain following naming template (customizable)

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
Component naming can be changed through UI or in rig_tail_constants.py

## Module Structure

| Module | Alias | Purpose |
|--------|-------|---------|
| `rig_tail_constants` | `rt_cst` | Global constants and caches |
| `rig_tail_naming` | `rt_nam` | Template strings and naming |
| `rig_tail_maya` | `rt_mya` | Maya scene/node operations |
| `rig_tail_joint` | `rt_jnt` | Joint chain utilities |
| `rig_tail_math` | `rt_mat` | Vector/matrix math |
| `rig_tail_matrix` | `rt_mtx` | Matrix network builder |
| `rig_tail_cache` | `rt_che` | Control caching |
| `rig_tail_restpose` | `rt_rest` | Rest-pose store for the IK rebuild fix |
| `rig_tail_setup` | `rt_set` | Setup phase: skeleton orient / mirror (pre-build) |
| `rig_tail_setup_ui` | - | Setup UI (Tail Rig Setup) |
| `rig_tail_cleanup` | `rt_cln` | Teardown + build-structure setup |
| `rig_tail_control` | `rt_ctl` | Control creation |
| `rig_tail_curve` | `rt_crv` | Curve/spline creation |
| `rig_tail_fk` | `rt_fk` | FK system building |
| `rig_tail_stretch` | `rt_str` | Stretch system |
| `rig_tail_anim` | `rt_ani` | Animation effects |
| `rig_tail_connect` | `rt_con` | IK/FK connections |
| `rig_tail_ctrlall` | `rt_ca` | Main Controller dashboard (multi-tail) |
| `rig_tail_ui` | `rt_ui` | Build UI (Tail Rig Builder) |

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

All settings round-trip through a JSON config file with the UI's
Load/Save Config buttons, so setups can be shared per show or user.

## Credits

Shelf icon: [Octopus](https://icons8.com/icons/set/octopus) icon by
[Icons8](https://icons8.com). Free use requires this link; keep it if you
ship the icon, or replace `icons/octopus*.png` with your own art.

## License

Released under the [MIT License](LICENSE) — free to use, modify, and
redistribute, provided the copyright notice and license text are retained.

© 2026 Daisy Jane (@gnitemouse)
