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
- Effect settings (stretch, wave, curl, noise, loop)
- Control colors and shapes

## License

Internal use only. © Daisy Jane @gnitemouse
