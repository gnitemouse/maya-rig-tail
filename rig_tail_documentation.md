# Rig Tail Documentation

## Author
Daisy Jane @gnitemouse

Complete API reference for the Maya Tail Rig system.

---

## Two phases: Setup then Build

The tool has two phases, run in order and launched from two shelf buttons
(`TailSetup`, then `TailRig`):

1. **Setup** (optional, `rig_tail_setup` + `rig_tail_setup_ui`): a pre-build
   step that orients and mirrors the raw BN skeleton so tails move
   coherently. It changes only joint orientation, never positions, and
   never runs during the build. If the skeleton is already well oriented,
   skip it entirely; the build is unaffected.
2. **Build** (`rig_tail` and the modules below): tear down any previous
   rig, then create joints, curves, controls, node networks, and bind the
   geometry.

## Module Overview

### Core Modules

| Module | Import Alias | Description |
|--------|--------------|-------------|
| `rig_tail` | - | Main entry point, build orchestration, phase launchers |
| `rig_tail_constants` | `rt_cst` | Global constants, naming templates, caches |
| `rig_tail_ui` | `rt_ui` | Build UI (Tail Rig Builder) |

### Setup Phase Modules

| Module | Import Alias | Description |
|--------|--------------|-------------|
| `rig_tail_setup` | `rt_set` | Skeleton orient / mirror, run before the build |
| `rig_tail_setup_ui` | - | Setup UI (Tail Rig Setup) |

### Utility Modules

| Module | Import Alias | Description |
|--------|--------------|-------------|
| `rig_tail_naming` | `rt_nam` | Template strings, naming conventions |
| `rig_tail_maya` | `rt_mya` | Maya scene operations, node creation, geometry binding |
| `rig_tail_math` | `rt_mat` | Vector math, orientation helpers |
| `rig_tail_matrix` | `rt_mtx` | Matrix offset network builder |
| `rig_tail_cache` | `rt_che` | Control caching and validation |
| `rig_tail_joint` | `rt_jnt` | Joint chain utilities |
| `rig_tail_restpose` | `rt_rest` | Rest-pose store for the IK rebuild fix (Method D) |

### Build Modules

| Module | Import Alias | Description |
|--------|--------------|-------------|
| `rig_tail_cleanup` | `rt_cln` | Teardown of a previous rig, plus build-structure setup |
| `rig_tail_control` | `rt_ctl` | Control creation |
| `rig_tail_curve` | `rt_crv` | Curve and spline creation |
| `rig_tail_fk` | `rt_fk` | FK system with SDK groups |
| `rig_tail_stretch` | `rt_str` | Stretch/squash system |
| `rig_tail_connect` | `rt_con` | IK/FK connections and blending |
| `rig_tail_anim` | `rt_ani` | Wave and dynamic FX |
| `rig_tail_mainctrl` | `rt_mc` | Main Controller dashboard (multi-tail) |

> Note: `rig_tail_setup` was previously the teardown/setup module; that
> module is now `rig_tail_cleanup`, and `rig_tail_setup` is the Setup phase
> (formerly `rig_tail_orient`).

---

## rig_tail.py

Main entry point for building tail rigs.

### Functions

#### `build_rig_tail(fk, ik)`
Rebuild-safe build with caching.

#### `rig_tail_fk(rigname, typ='fk')`
Create FK tail using variable FK method.

#### `rig_tail_ik(rigname, typ='ik')`
Create IK tail with spline solver.

#### `rig_tail_single(root, fk=True, ik=True, start_jnt=None, end_jnt=None)`
Rig a single tail from a joint chain.

#### `rig_tail_multiple(root, fk=True, ik=True)`
Rig multiple tails defined in RIGPARTS.

#### `rig_tail_selected(root, fk=True, ik=True)`
Rig tail on user-selected joints.

#### `setup_tails(root=None, dry_run=None)`
Run the pre-build Setup phase (delegates to `rig_tail_setup.setup_tails`).

#### `main()`
Launch the Tail Rig Builder UI.

#### `main_setup()`
Launch the Tail Rig Setup UI.

---

## rig_tail_setup.py (rt_set)

Setup phase: orient and mirror the BN skeleton before the build. Optional
and never runs during the build. Two independent toggles in
`rig_tail_constants`: `MIRROR_ORIENT` (aim-orient, removes intra-chain
twist) and `MIRROR_JOINTS` (behavior-mirror `L_`/`R_` pairs). Positions
are never changed; `MIRROR_ORIENT_DRYRUN` previews without modifying.

### Functions

#### `setup_tails(root=None, dry_run=None)`
Detect BN chains, unbind affected geometry, run the enabled steps.

#### `run_setup(dry_run=None)`
Run the enabled orient/mirror steps on `rt_cst.JOINTS_BN`.

#### `orient_chains(dry_run)`
Aim-orient every BN chain to remove intra-chain twist.

#### `mirror_joints(dry_run)`
Behavior-mirror each L/R pair's BN chain from the source side.

#### `find_mirror_pairs(rigparts)`
Pair rig parts into (source, target) by `L_`/`R_` prefix.

#### `aim_frames(positions, aim_axis, up_axis)`
Per-joint world frames aimed down a chain with a twist-free up-axis.

#### `mirror_frames(src_matrices, axis)`
Behavior-mirror source world matrices for the target side.

---

## rig_tail_cleanup.py (rt_cln)

Teardown of a previous rig and preparation of the scene structure, run at
the start of every build. Formerly `rig_tail_setup`.

### Functions

#### `cleanup_rig(fk, ik)`
Entry point; per rig part choose full vs light teardown from the cache.

#### `cleanup_rigname(rigname, fk, ik)`
Full teardown of one rig part (controls, curves, clusters, FX nodes).

#### `cleanup_connections(rigname, fk, ik)`
Light teardown: break connections only, keep nodes for reuse.

#### `setup_rig(fk, ik)`
Create the rig root, cog, and hierarchy groups.

#### `set_root(root)`
Set `ROOT` and reconcile the scene root group.

#### `find_existing_root_grp()`
Locate the current rig root group in the scene.

#### `set_joints_auto()`
Detect and (re)build the BN/FK/IK chains for all RIGPARTS.

#### `set_joints(rigname, start_jnt=None, end_jnt=None)`
Detect/build the chains for one rig part.

#### `detect_joints_bn()`
Fill `JOINTS_BN` by chain detection only (used by the Setup phase).

#### `rigpart_has_joints(rigname)`
Does the scene hold BN joints for a rig part.

#### `rename_rigpart(old, new)`
Rename a rig part in place across scene nodes and caches.

---

## rig_tail_mainctrl.py (rt_mc)

Main Controller dashboard for rigs with multiple tails. Built during the
connect phase when `MAIN_CONTROLLER` is on and RIGPARTS has 2+ parts. The
cog gets an ALL section (one `all_*` copy of each routed attribute) and an
OVERRIDE section (a per-tail flag choosing ALL vs the tail's own values).

### Functions

#### `active()`
Is the dashboard enabled for the current settings.

#### `add_dashboard_to_cog(cog_ctrl, fk, ik)`
Add the ALL and OVERRIDE sections to the cog control.

#### `add_override_to_basectrl(rigname, basectrl)`
Proxy a tail's override flag onto its base control.

#### `build_override_conditions(rigname, fk, ik)`
Create/rewire the per-tail condition nodes (local vs ALL).

#### `resolved_plug(rigname, attr)`
Source plug a consumer reads for a routed attribute (falls back to the
base control plug when the dashboard is off).

#### `ikfk_driver(rigname)`
Driver plug for a tail's IKFK mode SDKs.

#### `cleanup_mainctrl(fk, ik)`
Remove stale dashboard nodes, or all of them when the dashboard is off.

---

## rig_tail_restpose.py (rt_rest)

Rest-pose store for the IK rebuild-degradation fix (Method D). Captures
each BN joint's rest world matrix once and builds the IK curve from it on
every rebuild, so rebuilds reproduce the same shape instead of compounding.

### Functions

#### `capture_rest_pose(rignames=None)`
Store each BN joint's rest world matrix, once, on the first build.

#### `curve_source_positions(rigname, joints)`
Positions the IK curve is built from: the stored rest, else live.

#### `rest_positions(rigname, joints)`
Stored rest positions aligned to a target chain, or None.

#### `clear_rest_pose(rignames=None)`
Remove the stored rest pose (for re-capture or testing).

---

## rig_tail_setup_ui.py

Setup UI (Tail Rig Setup window). Exposes the orient/mirror toggles,
source-side and axis dropdowns, and Dry Run, then calls
`rig_tail_setup.setup_tails`. Launched by `rig_tail.main_setup()`.

#### `show_ui()`
Build and show the Setup window, closing any previous instance.

---

## rig_tail_naming.py (rt_nam)

Template string formatting and naming conventions.

### Functions

#### `fstr(rigname, template, TYPE='', NN='', nn='', TAG='')`
Format template string with rig name and placeholders.

**Example:**
```python
rt_nam.fstr('tail', rt_cst.JOINT, 'IK', 3)  # Returns: 'IK_tail_03_jnt'
```

#### `get_rigname(node, template)`
Extract rig name from node name using template pattern.

#### `get_index_from_name(name)`
Parse joint index (##) from naming convention.

#### `strip_group_suffix(name)`
Strip a trailing group label, e.g. `'tail_root_grp'` -> `'tail_root'`.

#### `titlecase(name)`
Convert snake_case to Title Case.

#### `rename_shapes(node, name)`
Rename shape nodes to match transform.

#### `name_contains_rigname_terms(rigname, name, terms)`
Check if name contains rigname and matching terms.

---

## rig_tail_maya.py (rt_mya)

Maya scene operations and node creation.

### Object Existence

#### `obj_exists(node)`
Check if Maya object exists.

#### `remove(node)`
Delete Maya object safely.

### Parenting

#### `parent_to(node, parent, a=False, r=False)`
Parent node to given parent.

#### `is_parent(node, parent)`
Check if node is already a child of parent.

### Scene Queries

#### `get_geometry_from_scene()`
Collect ungrouped mesh geometry from scene.

#### `get_joints_from_scene()`
Collect ungrouped top-level joints.

#### `get_controls_from_scene()`
Collect curve controls from scene.

#### `is_control(node)`
Check if node is curve control.

#### `is_geometry(node)`
Check if node is mesh geometry.

#### `list_hierarchy(root, end=None, predicate=None)`
Iterative traversal of transform hierarchy.

### Connections

#### `disconnect_all(node, source=True, destination=True, attrs=None)`
Disconnect all connections from/to a node.

#### `break_connection(plug)`
Break single plug connection.

### Transforms

#### `opm(node)`
Move transform values to offsetParentMatrix.

#### `reset_opm(node, unlock=True)`
Reset offsetParentMatrix to identity.

#### `reset_transforms(node, unlock=True)`
Reset translate, rotate, scale to defaults.

#### `match_transform(source, target, pos=False, rot=False, scl=False)`
Match transforms between nodes.

### Visibility

#### `set_visibility(node, value, k=1, cb=1, l=0)`
Set visibility attribute.

#### `set_transform_visibility(node, k=1, cb=1, l=0)`
Set transform attribute visibility.

#### `set_curve_visibility(curve, visibility=1)`
Set curve visibility nonkeyable.

#### `set_group_visibility(group, visibility=1)`
Set group visibility nonkeyable.

### Node Creation

#### `create_group(group, parent=None)`
Create transform group.

#### `create_condition(node, firstTerm=None, secondTerm=0, op=0)`
Create condition node.

#### `create_condition_multi(driveattr, drivenattrs, node=None)`
Create condition with multiple outputs.

### Curve Utilities

#### `get_num_cv(curve)`
Get curve CV count, spans, and degree.

#### `create_curveinfo(rigname, curve, typ='')`
Create curveInfo node for length measurement.

### SDK

#### `sdk(driver, driven, dv, v)`
Create Set Driven Key entry.

### Attributes

#### `add_attribute_enum(plug, ln, nn, en=None, dv=0, pxy=None)`
Add enum attribute to node.

### Geometry Binding

#### `bind_geometry(rigname)`
Search for geometry matching rigname and bind to BN joints.

#### `unbind_geometry(rigname)`
Unbind geometry from rig.

#### `unbind_geometry_all()`
Unbind all geometry in scene.

#### `bind_skincluster(joints, node, name)`
Create skinCluster binding.

#### `unbind_skincluster(node, delete_history=True)`
Unbind skinCluster from node.

---

## rig_tail_joint.py (rt_jnt)

Joint chain utilities.

### Functions

#### `get_joint_chain(start_jnt, end_jnt=None)`
Get ordered joint chain from start to end.

#### `get_joint_hierarchy(root)`
Get all joints under root.

#### `get_joint_position_from_list(joints)`
Get world positions for joint list.

#### `set_joint_attributes(joints)`
Configure joint display and attributes.

#### `is_equal_joint(jnt1, jnt2)`
Compare joint transforms for equality.

---

## rig_tail_math.py (rt_mat)

Vector and matrix math utilities.

### Functions

#### `linspace(start, stop, num)`
Generate evenly spaced values.

#### `get_axis_orientation(node)`
Get primary axis orientation.

#### `get_local_orientation(node)`
Get local axis vectors.

#### `get_world_pos(node)`
Get world position as vector.

#### `get_vec_length(vec)`
Calculate vector magnitude.

#### `axis_vector_colinearity(vec1, vec2)`
Check vector alignment.

---

## rig_tail_matrix.py (rt_mtx)

Matrix offset network construction.

### Functions

#### `build_matrix_offset_network(rigname, fk, ik)`
Build matrix blend network for IK/FK switching with FX offsets.

---

## rig_tail_cache.py (rt_che)

Control caching and validation.

### Functions

#### `get_cached_controls_ik(rigname)`
Get cached IK controls and groups.

#### `clear_control_cache()`
Clear all control caches.

#### `validate_cache()`
Clear cached data if RIGPARTS or ROOT changed since the last build.

#### `validate_cache_structure()`
Check whether NUM_CTRL_FK / NUM_CTRL_IK changed since the last build.
A change forces the full teardown path: reusing nodes built for a
different control count would mix old and new layouts.

#### `validate_cache_joints(rigname)`
Check that cached joints still exist and have not moved beyond
JOINT_POS_TOLERANCE; returns True when a full rebuild is needed.

---

## rig_tail_constants.py (rt_cst)

Global constants, naming templates, and data caches. All
user-editable values can be exported/imported as a JSON config file
via `save_config(filepath)` / `load_config(filepath)` (the UI's
Load/Save Config buttons).

### Key Constants

- `RIGPARTS` - List of rig component names
- `ROOT` - Root group name (trailing group label is stripped)
- `JOINTS_FK`, `JOINTS_IK`, `JOINTS_BN` - Joint caches per rigname
- `LAST_BUILD` - State of the previous build (rigparts, root, joint
  positions, control counts) used to pick the teardown path on re-rig
- `EFFECTS` - Effect toggles (stretchy, wave, curl, noise, loop)
- `INDIV_FK` - Build individual per-joint FK controls alongside the
  variable-FK sliding controls (requires FK). A change toggles the full
  teardown path on re-rig, like a control-count change.
- `NUM_CTRL_FK`, `NUM_CTRL_IK` - Number of Variable FK controls and of
  IK/Float controls (and curve clusters). The SplineIK control set is
  a fixed bot/mid/top structure and does not change with NUM_CTRL_IK.
- Naming templates: `JOINT`, `CTRL`, `CTRL_GRP`, `SDK_GRP`, etc.

### IKFK Modes

- `IKFK_MODES_ALL` - Full user-configured mode name list. Positional:
  `[0]=SplineIK, [1]=IK, [2]=Float, [3]=FK`; the names themselves are
  free to change (e.g. `['spline', 'ik', 'float', 'fk']`).
- `IKFK_MODES` - Active subset for the current build options, derived
  by `update_ikfk_modes(fk, ik)`: IK-only drops the FK mode, FK-only
  builds have no switch attribute.
- `ikfk_fk_mode_index()` - Index of the FK mode in the active list
  (matched by name, falling back to position for custom names).
- `IKFK_SWITCH` - Switch attribute template; its enum string is
  rebuilt from `IKFK_MODES` by `rebuild_derived()`.
