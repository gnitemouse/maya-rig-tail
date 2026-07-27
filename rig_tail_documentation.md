# Rig Tail Documentation

## Author
Daisy Jane @gnitemouse

Complete API reference for the Maya Tail Rig system.

---

## Two phases: Setup then Build

The tool has two phases, run in order and launched from the first two of the
three shelf buttons (`TailSetup`, then `TailRig`; `TailReload` loads,
reloads and runs the modules, described below):

1. **Setup** (optional, `rig_tail_setup` + `rig_tail_setup_ui`): a pre-build
   step that orients, mirrors and rolls the raw BN skeleton so tails move
   coherently, and never runs during the build. Most of it changes only
   joint orientation; the one exception is `MIRROR_JOINTS`, which also
   mirrors joint positions. If the skeleton is already well oriented, skip
   the phase entirely; the build is unaffected.
2. **Build** (`rig_tail` and the modules below): tear down any previous
   rig, then create joints, curves, controls, node networks, and bind the
   geometry.

## How the modules get loaded

`install.py` bakes the chosen install's `scripts/` folder into all three
shelf buttons as `TOOL_DIR` and puts it at the front of `sys.path`, so a
button runs the install it was made from even when another copy of Rig Tail
is registered as a Maya module. The same path goes into the `rigTail.mod`
under `~/Documents/maya/modules/`, which is what makes a bare
`import rig_tail` work in the Script Editor.

Two different refresh strategies sit on top of that:

- **`TailSetup` / `TailRig`** call `il.reload()` down the chain from
  `rig_tail.py`. Fast, and it deliberately skips `rig_tail_constants` to
  keep session state (UI settings, joint caches, the loaded config) alive.
- **`TailReload`** deletes every `rig_tail*` module (and `logger_config`)
  from `sys.modules` first, so the import that follows is genuinely fresh.
  This is the only path that picks up edits to `rig_tail_constants` without
  a Maya restart, at the cost of resetting session state — the config
  auto-load restores the saved config. It also binds `rt_*` handles for
  console testing.

Editing a module and clicking a UI button therefore picks up the change;
adding a constant or a function to `rig_tail_constants` needs `TailReload`.

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
| `rig_tail_setup` | `rt_set` | Skeleton orient / mirror / roll, run before the build |
| `rig_tail_setup_ui` | - | Setup UI (Tail Rig Setup) |
| `rig_tail_test_setup` | `rt_ts` | Tests for the Setup phase (math + scene) |

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
| `rig_tail_ctrlall` | `rt_ca` | Main Controller dashboard (multi-tail) |

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

Setup phase: orient, mirror and roll the BN skeleton before the build.
Optional and never runs during the build.

Three independent batch toggles in `rig_tail_constants`:

| Constant | Effect | Positions |
|----------|--------|-----------|
| `ORIENT_JOINTS` | Aim-orient each chain so its up-axis stops twisting from joint to joint. No mirroring: both sides are oriented from their own geometry. `ORIENT_UP_MODE` picks the roll. | kept |
| `MIRROR_ORIENT` | Reflect matching `L_`/`R_` pairs' **orientation** across the symmetry plane, so the two sides face as mirror images. | kept |
| `MIRROR_JOINTS` | Reflect matching `L_`/`R_` pairs' **positions** across the symmetry plane, so the target side's joints sit at the exact mirror of the source side's. | **moved** |

`ORIENT_JOINTS` runs first, then the mirrors, so a mirror copies a clean
source. `MIRROR_SOURCE_SIDE` (default `R`) picks which side is authored;
the other is overwritten. `MIRROR_AXIS` is the symmetry-plane normal, and
the plane is assumed to pass through the world origin. `MIRROR_DRYRUN`
previews every batch operation without modifying anything.

`ORIENT_UP_MODE` picks where `ORIENT_JOINTS` takes its up reference. The
joint positions fix the aim, so this only decides the chain's **roll** about
it:

| Value | Up reference | Effect |
|-------|--------------|--------|
| `cascade` (default) | The chain's **own first joint**, as it stands now, carried down the chain by parallel transport. | Twist still goes, but the roll the chain already has is kept — so a mirrored pair stays mirrored (in either behavior) and a **Roll Chain** fix-up survives. Re-running Setup on a set-up skeleton is non-destructive. |
| `best-fit` | The chain's **best-fit bend plane** normal, ignoring how the joints stand now. | Lands a raw, arbitrarily oriented skeleton on its own plane in one pass, but **overwrites** any mirrored or hand-rolled orientation. |

The two agree exactly on the case they both handle well — a `symmetric`
pair whose positions are already mirrored — and diverge everywhere else:
`best-fit` re-derives a `parallel` pair back to `symmetric` (180 degrees
out), undoes a `roll_chain` by exactly the angle rolled, and on a nearly
straight chain falls back to a world axis, which is not mirrored between
sides and so flips one side of the pair. Use `best-fit` for the first pass
on a raw skeleton, `cascade` from then on.

`MIRROR_BEHAVIOR` picks how `MIRROR_ORIENT` rolls the mirrored side about
its aim axis. The aim must keep pointing down the chain (the spline IK and
the advanced twist both read it), so the roll is the only freedom left, and
there are exactly two right-handed choices, 180 degrees apart:

| Value | Same channel value on both sides | Equivalent to |
|-------|----------------------------------|---------------|
| `symmetric` (default) | Moves the target as the **exact mirror** of the source: both tails curl up together, both curl outward together. | Maya `mirrorJoint -mirrorBehavior` |
| `parallel` | Moves the two sides **opposite ways**: a splayed pair reads as one curling up while the other curls down. | a plain orientation mirror |

Because the two differ only by a 180 degree roll about the aim, running
**Roll Chain** at 180 on the target side converts one into the other for a
single chain — useful when one pair wants the opposite convention. Ignored
when `MIRROR_ORIENT` is off (a positions-only mirror does not touch
orientation).

### Include / Exclude

`RIGPARTS_EXCLUDE` holds rig parts that **both** the batch Setup operations
and the build should skip. Move parts between the **Include** and
**Exclude** columns in the *Edit Rig Parts* editor (arrow buttons, or
double-click an entry).

Excluded names stay in `RIGPARTS` — they keep their place in the roster,
stay renameable, and still resolve for L/R pairing. Both phases simply
leave them alone:

| Phase | What an excluded part gets |
|-------|----------------------------|
| Setup | Not oriented, not mirrored, and **not unbound**, so its skin survives a run aimed at another tail. Excluding one side of an `L_`/`R_` pair stops that pair mirroring altogether. |
| Build | Not torn down and not rebuilt. Its controls, curves, clusters, SDK curves, FX network and geometry bind are all left as they are, and its IKFK mode is not reset. |

Use it to freeze a finished tail while the rest of the roster is iterated
on. `rig_tail_cache.active_parts()` returns the included parts in
`RIGPARTS` order, and every phase iterates that instead of `RIGPARTS`;
`rt_cst.active_rigparts()` is the underlying computation.

Two things stay roster-wide on purpose. Anything the cog owns per tail (the
IKFK switch, the dashboard override flag) is still created for excluded
parts, because their rig is still in the scene and still needs those
channels — cleanup only treats such a node as stale when its part leaves
`RIGPARTS` entirely. And `cleanup_rig`'s one-call SDK animation-curve sweep
spares the curves whose driven node belongs to an excluded part
(`cleanup.excluded_sdk_curves`), since nothing is going to rebuild them.

The entry points that name their parts outright — `rig_tail_single`,
`rig_tail_selected` — lift the exclusion on what they were asked to build,
so an explicit request is never a silent no-op. `rig_tail_multiple`
honours the exclusion as it stands.

Plus one interactive per-chain fix-up, `roll_chain`, which has no constant:
it rolls a single chain about its aim axis to turn a
correctly-oriented-but-wrong-facing chain onto the right plane.

Orientation is written into `jointOrient` with `rotate` left at zero.
Re-orienting or moving a bound joint would drag the mesh, so affected geometry
is re-baselined onto the new pose afterwards (`PRESERVE_SKIN`, painted weights
kept — see Skin Preservation under rig_tail_maya) or, with that off, unbound and
left for the build to rebind. Any stored rest pose is cleared either way, so the
build recaptures it.

> Note: these constants were renamed. `MIRROR_ORIENT` previously meant the
> aim-orient (now `ORIENT_JOINTS`) and `MIRROR_JOINTS` previously meant the
> orientation mirror (now `MIRROR_ORIENT`). Older config files are migrated
> automatically on load.

### Functions

#### `setup_tails(root=None, dry_run=None)`
Detect BN chains, run the enabled steps, then re-baseline the skinned meshes
(or, with `PRESERVE_SKIN` off, unbind them up front instead).

#### `run_setup(dry_run=None)`
Run the enabled batch orient/mirror steps on `rt_cst.JOINTS_BN`.

#### `orient_chains(dry_run)`
Aim-orient every BN chain to remove intra-chain twist (`ORIENT_JOINTS`),
taking the roll from `ORIENT_UP_MODE`. In `cascade` it reads each chain's
first joint before touching anything, and uses that as the seed.

#### `mirror_chains(dry_run, do_orient, do_positions)`
Reflect each L/R pair's orientation and/or positions from the source side
(`MIRROR_ORIENT` / `MIRROR_JOINTS`).

#### `roll_chain(rigname, degrees)`
Roll one chain about its aim axis by an angle, keeping positions. The
interactive fix-up behind the Setup UI's Roll Chain arrows.

#### `rignames_from_selection()`
The RIGPARTS of every selected node, in order and de-duplicated, so chains
can be picked by clicking joints. Selecting whole chains across several
tails yields one name per tail. Used by the Setup UI's Select button.

#### `rigname_from_selection()`
Single-chain form of the above: the RIGPART of the first recognized
selected node, or None.

#### `show_joint_orients(show=True)`
Toggle `displayLocalAxis` on every BN chain joint, to eyeball the result.

#### `find_mirror_pairs(rigparts)`
Pair rig parts into (source, target) by `L_`/`R_` prefix.

#### `aim_frames(positions, aim_axis, up_axis, up_ref=None)`
Per-joint world frames aimed down a chain with a twist-free up-axis. With
`up_ref` (the `cascade` seed) that vector is carried down the chain by
parallel transport, keeping the chain's existing roll; without it the roll
comes from the chain's best-fit plane normal. A zero-length `up_ref` is
ignored, so a failed lookup falls back to best-fit rather than failing.

#### `mirror_frames(src_matrices, axis, aim_axis, up_axis)`
Reflect source world orientations across the symmetry plane for the target.

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

## rig_tail_ctrlall.py (rt_ca)

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

#### `cleanup_ctrlall(fk, ik)`
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

Setup UI (Tail Rig Setup window). Exposes the three batch toggles
(Orient Joints, Mirror Orient, Mirror Joints), the source-side and axis
dropdowns, and Dry Run, then calls `rig_tail_setup.setup_tails`. Launched
by `rig_tail.main_setup()`.

Two dropdowns sit in the rows of the toggle they shape, and are greyed out
unless that box is ticked: **Up Mode** (`Cascade` / `Best-fit` →
`ORIENT_UP_MODE`) in the Orient Joints row, and **Mirror Behavior**
(`Symmetric` / `Parallel` → `MIRROR_BEHAVIOR`) in the Mirror Orient row.

The **Roll Chain** group sits below Setup Options and is separate from Run
Setup: list one or more chains in the Chain box — type them comma
separated, or click **Select** to read them from the selected joints — set
a step angle, and the left/right arrows roll every listed chain by
minus/plus the step immediately. Several tails can therefore be corrected
in one click. It applies on click with no confirmation dialog, and ignores
Dry Run; a name that is not in `RIGPARTS` is refused before anything runs.
**Show Joint Local Axes** draws each BN joint's axes so the result is
visible in the viewport.

Running Setup with no batch toggle enabled does nothing and closes the
window with a warning, because a real run unbinds geometry and clears the
rest pose before the toggles are consulted.

#### `show_ui()`
Build and show the Setup window, closing any previous instance.

---

## rig_tail_test_setup.py (rt_ts)

Tests for the Setup phase, split by whether they need a scene.

**Math tests** are deterministic and safe: they exercise the geometry
helpers directly, so "is the mirror math correct?" is answered in
isolation. **Scene tests** are MUTATING: they run the real entry points on
the loaded skeleton and verify the result, so like a real Setup run they
detach OPM drivers, re-baseline (or unbind) geometry and clear the rest
pose — reload the scene afterwards before building. Each scene test saves
and restores `RIGPARTS` and the Setup flags.

### Functions

#### `run_math()`
Every math test, with a PASS/FAIL summary. Safe.

#### `run_scene(base='fintail', chain='C_tail')`
Every scene test, with a PASS/FAIL summary. **Mutating.**

#### `check_mirror(base='fintail')`
Focused mirror check: the mirror math plus one real L/R pair, orientation
and positions. **Mutating.**

#### `run_all()`
`run_math()` plus a pointer to the mutating scene tests.

#### `check_skin(rigname='C_tail')`
Read-only report: which meshes match the rig part, whether they are skinned,
how many of the chain's joints are influences, and how far the skinCluster's
rest pose has drifted from where the joints now are. Safe.

Individual tests: `test_reflect`, `test_assign_rows`, `test_roll_about`,
`test_aim_frames`, `test_mirror_frames`, `test_find_mirror_pairs` (math);
`test_orient`, `test_end_joint`, `test_mirror_orient`, `test_mirror_joints`,
`test_roll`, `test_skin_rebaseline`, `test_rigname_from_selection` (scene).

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

#### `unbind_geometry(rigname, force=False)`
Unbind geometry from rig. With `PRESERVE_SKIN` on, already-skinned meshes are
left bound and returned instead; `force=True` unbinds regardless.

#### `unbind_geometry_all()`
Unbind all geometry in scene.

### Skin Preservation

`PRESERVE_SKIN` (default on) stops the rig throwing away painted weights. A
closest-distance rebind is only ever right the first time: afterwards it wipes
the paint work, and on a mesh the rig shares with the rest of the character it
drops the other influences entirely.

#### `preserve_skin()`
Read the `PRESERVE_SKIN` setting, defaulting to on (the reload sweep skips
constants, so a session that predates the setting lacks it — `TailReload`
re-imports it).

#### `find_skincluster(node)`
First skinCluster in a node's history.

#### `skin_influence_indices(skincluster)`
Map each influence to its logical index. Indices are sparse on a mesh that has
had influences added and removed, so `bindPreMatrix[i]` must be found through
the `matrix` connections, never by counting influences.

#### `add_missing_influences(skincluster, joints)`
Add rig joints to an existing cluster at weight 0, leaving every painted weight
untouched. New influences do nothing until they are painted in.

#### `rebaseline_skin(rigname, tolerance=None)`
Accept the joints' current pose as the skin's rest pose, by writing each moved
influence's new world matrix into `bindPreMatrix`. Lets Setup re-orient a bound
skeleton without unbinding. Only the rig part's own joints are re-baselined;
other influences on a shared mesh did not move and are left alone.

#### `bind_skincluster(joints, node, name, preserve=False)`
Create skinCluster binding. A cluster with exactly these influences is always
reused; one with a different influence set is deleted and rebuilt unless
`preserve` is on. Only geometry passes `preserve` — the IK/FK driver curves are
rig-owned and must be bound to exactly their own joints.

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
