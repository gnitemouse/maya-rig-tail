# Rig Tail Documentation

## Author
Daisy Jane @gnitemouse

Complete API reference for the Maya Tail Rig system.

---

## Module Overview

### Core Modules

| Module | Import Alias | Description |
|--------|--------------|-------------|
| `rig_tail` | - | Main entry point, build orchestration |
| `rig_tail_constants` | `rt_cst` | Global constants, naming templates, caches |
| `rig_tail_ui` | `rt_ui` | Qt-based user interface |

### Utility Modules

| Module | Import Alias | Description |
|--------|--------------|-------------|
| `rig_tail_naming` | `rt_nam` | Template strings, naming conventions |
| `rig_tail_maya` | `rt_mya` | Maya scene operations, node creation, geometry binding |
| `rig_tail_math` | `rt_mat` | Vector math, orientation helpers |
| `rig_tail_matrix` | `rt_mtx` | Matrix offset network builder |
| `rig_tail_cache` | `rt_che` | Control caching and validation |
| `rig_tail_joint` | `rt_jnt` | Joint chain utilities |

### Build Modules

| Module | Import Alias | Description |
|--------|--------------|-------------|
| `rig_tail_setup` | `rt_set` | Scene setup and cleanup |
| `rig_tail_control` | `rt_ctl` | Control creation |
| `rig_tail_curve` | `rt_crv` | Curve and spline creation |
| `rig_tail_fk` | `rt_fk` | FK system with SDK groups |
| `rig_tail_stretch` | `rt_str` | Stretch/squash system |
| `rig_tail_connect` | `rt_con` | IK/FK connections and blending |
| `rig_tail_anim` | `rt_ani` | Wave and dynamic FX |

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

#### `main()`
Launch Qt UI.

---

## rig_tail_naming.py (rt_nam)

Template string formatting and naming conventions.

### Functions

#### `fstr(rigname, template, typ='', nn=0, tag='', suffix='', *args)`
Format template string with rig name and placeholders.

**Example:**
```python
rt_nam.fstr('tail', '{rigname}_{type}_ctrl', 'ik')  # Returns: 'tail_ik_ctrl'
```

#### `get_rigname(node, template)`
Extract rig name from node name using template pattern.

#### `get_index_from_name(name)`
Parse joint index (##) from naming convention.

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

#### `validate_cache(rigname)`
Validate cached data exists.

#### `validate_cache_joints(rigname)`
Validate joint cache.

---

## rig_tail_constants.py (rt_cst)

Global constants, naming templates, and data caches.

### Key Constants

- `RIGPARTS` - List of rig component names
- `JOINTS_FK`, `JOINTS_IK`, `JOINTS_BN` - Joint caches per rigname
- `EFFECTS` - Effect toggles (stretchy, wave, curl, noise, loop)
- Naming templates: `JOINT`, `CTRL`, `CTRL_GRP`, `SDK_GRP`, etc.
