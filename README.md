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
rigTail.mod      module descriptor
rigTail/
    scripts/     rig_tail*.py + logger_config.py
    icons/       octopus.png, octopus_200.png
```

**Drag-and-drop (recommended)**

Drag `install.py` from a file browser into the Maya viewport. It copies
`rigTail/` and `rigTail.mod` into
`~/Documents/maya/modules/` and adds two shelf buttons to the active
shelf: **TailSetup** (skeleton orient / mirror) and **TailRig** (the
builder). Works immediately, with no restart and no `userSetup.py` edits.
Keep `install.py` next to `rigTail/` and `rigTail.mod` when you drag it,
since it copies them.

**Manual**

Copy `rigTail/` and `rigTail.mod` into
`~/Documents/maya/modules/` (create the `modules` folder if needed), then
restart Maya. Launch from the Script Editor with `import rig_tail;
rig_tail.main()`, or make a shelf button that runs the same two lines.

**Uninstall** - delete `rigTail.mod` and the `rigTail` folder
from `~/Documents/maya/modules/`, and remove the shelf button.

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

- **Orient Joints** (`ORIENT_JOINTS`): aim-orient each chain so a tail
  bends in one plane. Fixes joints whose orientation twists down the chain.
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

**Roll Chain** is a separate per-tail fix-up in the same window, for a
chain that is cleanly oriented but facing the wrong way: pick the chain
(or **Select** it from a selected joint) and use the left/right arrows to
roll it onto the right plane. It applies immediately and never moves
joints.

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
| `rig_tail_mainctrl` | `rt_mc` | Main Controller dashboard (multi-tail) |
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
