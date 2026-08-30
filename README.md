# Rig Tail

A modular Maya rigging system for stretchy tails with IK/FK modes.

You supply a joint chain. The tool generates the controls, curves and node
networks that drive it, and rebuilds them on demand while preserving painted
weights and hand-edited control shapes.

You will find this tool useful if you want a spline-driven tail with IK/FK
switching that can be rebuilt repeatedly as the model changes. Every mode is
driven by matrix connections and built from stock Maya nodes, so a rig stays
portable across Maya versions and opens on any machine without a plugin. The
UI covers the whole workflow, and the source is open and modular for anyone
who wants to adapt it.

## Overview

Rig Tail is four shelf buttons over one shared core. Two of them prepare the
skeleton, one builds the rig, and one re-imports the package.

```
[Chain Builder]  ->  [Tail Setup]  ->  [Tail Build]
 joint positions      orient / mirror   the rig
   (optional)           (optional)
```

| Sub-tool | Shelf button | Purpose | Required |
| --- | --- | --- | --- |
| **Tail Build** | `TailBuild` | Build the rig | yes |
| **Tail Setup** | `TailSetup` | Orient, mirror and roll the raw skeleton | optional |
| **Joint Chain Build** | `ChainBuild` | Create a BN chain, or re-space one at a different joint count | optional |
| **Tail Reload** | `TailReload` | Re-import the package after editing it | utility |

The handover between them is the BN joint chain. Chain Build writes
positions, Setup writes orientations, and Build reads both. None of them
stores state in the scene for the next one, so each can be re-run, skipped,
or left uninstalled.

## Features

- **Spline IK:** curve-based deformation with a fixed bot/mid/top control
  structure
- **IK and Float modes:** chained or free-floating cluster controls
- **Variable FK:** sliding FK controls with falloff rotation
- **IK/FK switching:** per tail, or globally from the cog
- **Stretch and squash:** with optional volume preservation
- **Wave and curl FX:** procedural animation effects
- **Main Controller:** a cog dashboard driving every tail at once, with a
  per-tail override, for multi-tail rigs
- **Skin preservation:** rebuilds keep existing skinClusters and their
  painted weights
- **JSON configs:** every setting round-trips to a file, so a setup can be
  shared per show or per user

## Install

The tool ships as a Maya module:

```
install.py       drag-and-drop installer
uninstall.py     drag-and-drop uninstaller
rigTail.mod      module descriptor, for installing by hand
rigTail/
    scripts/     the Python package
    icons/       shelf icons
```

**Drag-and-drop.** Drag `install.py` from a file browser into the Maya
viewport. It asks where to install and which shelf buttons to add, then puts
them on the active shelf. All four are ticked by default, and **TailBuild**
is the tool itself, so it always installs. This takes effect immediately,
with no restart and no `userSetup.py` edits. Keep `install.py` next to
`rigTail/` when dragging it.

| Choice | Result |
| --- | --- |
| **Default (maya/modules)** | Copies `rigTail/` into `~/Documents/maya/modules/`. Self-contained, so the folder can then be moved or deleted. |
| **Current (this folder)** | Runs from the current location, so a `git pull` takes effect on the next click. |
| **Other...** | Installs into a chosen folder, for a shared network location or a per-project tools folder. |

In each case `rigTail.mod` is written to `~/Documents/maya/modules/` pointing
at the install, so `import rig_tail` resolves in the Script Editor as well as
from the shelf buttons.

Re-installing over an existing install overwrites it file by file. Click
**TailReload** afterwards to pick up the new files in a running session.

**Manual.** Copy `rigTail/` and `rigTail.mod` into
`~/Documents/maya/modules/`, creating the `modules` folder if needed, then
restart Maya.

**Uninstall.** Drag `uninstall.py` into the viewport. It locates the install,
then removes the shelf buttons, the `.mod` and the module folder. A folder it
only pointed at (a clone installed with **Current**) is left in place, and
deleting a copy outside the Maya modules folder asks for confirmation.

## Quick Start

```python
import rig_tail as rt

# --- Joint Chain Build (optional; on the raw BN skeleton, before Setup) ---
import rig_tail_chain_build_ui as rt_chain_ui
rt_chain_ui.show_ui()

# --- Tail Setup (optional; orient / mirror before building) ---
rt.setup_tails(root='tail', dry_run=True)   # log the changes only
rt.main_setup()                             # or launch the Setup UI

# --- Tail Build ---
rt.rig_tail_single(root='tail', fk=True, ik=True)     # one chain
rt.rig_tail_multiple(root='tail', fk=True, ik=True)   # every part in RIGPARTS
rt.main()                                             # or launch the Builder UI
```

A module edited mid-session is picked up by clicking any of the three tool
buttons, which re-import the package. Click **TailReload** after editing
`rig_tail_constants.py`.

## Rig Hierarchy

The BN chain is the input, and everything below it is generated. Component
names are set through the UI editors or in `rig_tail_constants.py`, and the
constant behind each node is given on the right.

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

## Tail Build

Launch from the **TailBuild** shelf button or `rig_tail.main()`. Select the
build options (FK/IK, stretch, FX, Main Controller) and click **Build Rig**.
Tails whose joints are unchanged since the last build are kept as they are,
so iterating is fast. **Force Rebuild** tears everything down first.

Two checkboxes control the geometry, both on by default. **Bind Geometry**
binds each mesh named after a rig part to that part's BN joints. With it off
the tool leaves every skinCluster alone, which suits meshes another
department owns or a model that is not final. **Keep Weights** applies while
Bind Geometry is on: existing skins survive a rebuild with painted weights
intact, or the mesh is unbound and rebound from scratch.

### Highlights

- BN joints are driven entirely through `offsetParentMatrix`, with no
  constraints and no Euler decomposition, and their channels stay zeroed.
  Because FK and IK are blended as matrices, switching modes needs no
  separate switching network.
- Rebuilds are safe by name: every node is looked up by its templated name
  and reused, and a stored rest pose keeps repeated rebuilds reproducing the
  same rig rather than compounding curve smoothing.
- Rebuilds are incremental. A joint cache decides per rig part whether the
  rebuild needs a full teardown or only re-wiring, so iterating on one tail
  does not cost the others.
- Painted weights and hand-edited control shapes survive a rebuild. A build
  that would add rig joints to an already-painted cluster at weight zero asks
  for confirmation first.
- Spline IK holds a fixed bot/mid/top control set, while the IK and Float
  control counts vary through `NUM_CTRL_IK`. Variable FK slides
  `NUM_CTRL_FK` controls along the chain, each rotating the joints around it
  with a falloff.
- IK/FK switches live on the cog control, and every control carrying those
  dials shows proxies of them, so an animator can switch from whichever
  control is already selected. On a multi-tail rig the Main Controller adds
  ALL dials plus a per-tail override.
- Stretch and squash, wave, curl and noise are separate options, each driven
  from the same control set.
- Excluded rig parts are frozen. The build neither tears them down nor
  rebuilds them, so a finished tail can be left alone while the rest of the
  roster is iterated on.

## Tail Setup

Launch from the **TailSetup** shelf button or `rig_tail.main_setup()`. It
re-orients the raw BN skeleton so tails move coherently, and it never runs
during a build. Three independent toggles: **Orient Joints** stops the
up-axis twisting down the chain, **Mirror Orient** makes matching `L_`/`R_`
tails face as mirror images, and **Mirror Joints** mirrors their positions.
**Roll Chain** is a per-tail fix-up that rolls listed chains onto the right
plane while holding every joint in place.

A typical run enables Orient Joints and Mirror Orient, adding Mirror Joints
when the two sides are positionally off. Use Setup when a chain twists along
its length, when an `L_`/`R_` pair does not behave symmetrically, or to build
one side from the other.

### Highlights

- Setup changes how joints rotate, not where they sit. A chain's positions
  are the shape the modeller gave it, so orientation is written into
  `jointOrient` with `rotate` left at zero: the twist comes out, the
  skeleton still reads as a clean rest pose, and the model it drives does not
  move.
- Mirror Joints is the step that does move joints, which is its purpose. It
  places the target side at the exact mirror of the source, and builds the
  chain outright when the target side has none. Selecting one side's joints
  through **Edit Rig Parts** and then running Setup is a complete
  mirror-duplicate workflow.
- Orient runs before the mirrors, so a single run with several toggles
  enabled is correct and needs no second pass.
- Up Mode decides a chain's roll. *Cascade* keeps the roll a chain already
  has, so a mirrored pair and a Roll Chain fix-up survive a re-run.
  *Best-fit* re-derives roll from the chain's bend plane, which lands a raw
  skeleton on its own plane in one pass.
- **Dry Run** reports every intended change to the Script Editor without
  modifying the scene, which matters because a real run also re-baselines
  the bound geometry.
- **Edit Rig Parts** splits the roster into Include and Exclude, and the
  build honours the same split, so one roster covers both phases.

## Joint Chain Build

Launch from the **ChainBuild** shelf button or
`rig_tail_chain_build_ui.show_ui()`. It creates BN joint chains between two
objects and re-spaces existing ones at any joint count, before Setup and
before any rig exists. Select the chains, set **Joint Count**, choose a
**Spacing** profile, and click **Build Joints**.

Use it when a chain does not exist yet, has the wrong joint count, or needs a
taper.

### Highlights

- Four spacing profiles cover the common cases. *Keep* holds the current
  pattern at a new count, *Uniform* evens the segments, and *Power* and
  *Ratio* taper from long at the base to short at the tip. **Invert**
  reverses any of them.
- Joints are reused in place, so names, rotate orders and custom attributes
  survive wherever the count allows.
- The profiles are analytic functions of the joint index rather than of the
  chain, which makes them repeatable: re-spacing at an unchanged count
  returns the chain untouched, and re-applying a profile to its own result
  changes nothing.
- Each chain's original shape is remembered for the session and always
  re-measured from, so a run of different joint counts costs one approximation
  rather than one per step. **Bake Joint Chain** makes the current shape the
  new baseline.
- Skinned, rig-driven, branching and degenerate chains are reported and
  skipped before anything moves, leaving the remaining chains to build. One
  click covers every listed chain in a single undo step.
- It writes positions only. Orientation belongs to Tail Setup, and the
  optional **Orient joints** tick calls the same code Setup uses, so the two
  tools agree.

## Tail Reload

Runs script only, no UI. It drops every `rig_tail*` module from
`sys.modules`, imports them again under their usual aliases, and leaves a
commented workflow in the Script Editor.

The other three buttons re-import the package as well, holding
`rig_tail_constants` back so that the session's roster and unsaved settings
survive a relaunch. **TailReload** is therefore the button that picks up an
edit to `rig_tail_constants`, and it resets that session state to the loaded
config.

## Requirements

- Maya 2024 to 2027 (Python 3). The UI selects PySide6 or PySide2 to match.
- A joint chain following the naming template, which is customizable.

## Configuration

Settings are edited through the UI editors (Edit Rig Parts, Edit Naming, Edit
Constants) or in `rig_tail_constants.py`: naming templates, component and
root names, IK/FK mode names, control counts, effect settings, and control
colors and shapes. All of them round-trip through a JSON config with the
UI's Load and Save Config buttons.

## Documentation

API reference, per-module breakdown, architecture notes and the reasoning
behind each design decision:
[rig_tail_documentation.md](rig_tail_documentation.md).

## Credits

Variable FK follows the elephant-trunk rig Jeff Brodsky presented for
*Tembo* ([vimeo.com/72424469](https://vimeo.com/72424469)). Serguei
Kalentchouk's [Variable FK Revisited](https://medium.com/@k_serguei/variable-fk-revisited-9e8435c0c337)
covers the same method built as a compiled C++ node.

Shelf icon: [Octopus](https://icons8.com/icons/set/octopus) icon by
[Icons8](https://icons8.com). Free use requires this link, so keep it if you
ship the icon, or replace `icons/octopus*.png` with your own art.

## License

Released under the [MIT License](LICENSE): free to use, modify, and
redistribute, provided the copyright notice and license text are retained.

© 2026 Daisy Jane (@gnitemouse)
