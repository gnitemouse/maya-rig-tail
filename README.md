# Rig Tail

A modular Maya rigging system for creating stretchy tails with IK/FK modes.

## Features

- **Spline IK** - Smooth curve-based tail deformation
- **Variable FK** - Sliding FK controls with falloff rotation
- **IK/FK Switching** - Seamless blend between modes
- **Stretch/Squash** - Optional volume preservation
- **Wave & Dynamic FX** - Built-in procedural animation effects

## Quick Start

```python
import importlib as il
import rig_tail

il.reload(rig_tail)

# Single tail from joint chain
rig_tail.rig_tail_single('tail', root='tail_spline_grp', fk=True, ik=True)

# Launch UI
rig_tail.main()
```

## Requirements

- Maya 2020+ (Python 3)
- Joint chain following naming convention

## Expected Hierarchy

```
controls                         (CONTROL_GRP)
  └─ {rigname}_ctrl_grp          (BASECTRL_GRP)
     └─ {rigname}_base_ctrl      (BASECTRL)
        └─ FK_{rigname}_root_grp (CTRLROOT_GRP)
           └─ {rigname}_##_ctrl_grp (CTRL_GRP)
              └─ {rigname}_##_ctrl  (CTRL)

FK_skeleton                      (fk_SKELETON_GRP)
  └─ FK_{rigname}_grp            (fk_GRP)
     └─ FK_{rigname}_##_01_sdk   (SDK groups)
        └─ FK_{rigname}_##_jnt   (Joint)
```

## Module Structure

| Module | Alias | Purpose |
|--------|-------|---------|
| `rig_tail_constants` | `rt_cst` | Global constants and caches |
| `rig_tail_naming` | `rt_nam` | Template strings and naming |
| `rig_tail_maya` | `rt_mya` | Maya scene/node operations |
| `rig_tail_util` | `rt_utl` | Transform/connection helpers |
| `rig_tail_joint` | `rt_jnt` | Joint chain utilities |
| `rig_tail_math` | `rt_mat` | Vector/matrix math |
| `rig_tail_matrix` | `rt_mtx` | Matrix network builder |
| `rig_tail_cache` | `rt_che` | Control caching |
| `rig_tail_control` | `rt_ctl` | Control creation |
| `rig_tail_curve` | `rt_crv` | Curve/spline creation |
| `rig_tail_fk` | `rt_fk` | FK system building |
| `rig_tail_stretch` | `rt_str` | Stretch system |
| `rig_tail_anim` | `rt_ani` | Animation effects |
| `rig_tail_connect` | `rt_con` | IK/FK connections |
| `rig_tail_setup` | `rt_set` | Rig setup/cleanup |
| `rig_tail_ui` | `rt_ui` | Qt-based UI |

## Configuration

Edit `rig_tail_constants.py` to customize:
- Naming templates
- Rig component names
- Effect settings (stretch, wave, dynamics)
- Control colors and shapes

## License

Internal use only. © Daisy Jane @dayzl
