# Rig Tail Documentation

Author: Daisy Jane @gnitemouse

Reference for the Rig Tail toolkit: what it builds, how a build runs, and
which constants and functions control each stage. The README covers
installation and day-to-day use.

---

## Overview

Rig Tail turns a BN joint chain into a spline-driven tail rig with IK/FK
switching. It ships as four shelf buttons over one shared core:

```
[Chain Builder]  ->  [Tail Setup]  ->  [Tail Build]
 joint positions      orient / mirror   the rig
   (optional)           (optional)
```

1. **Tail Build** (`rig_tail` and the build modules) tears down any previous
   rig, then creates joints, curves, controls and node networks, and binds
   the geometry. This is the tool proper.
2. **Tail Setup** (`rig_tail_setup`, `rig_tail_setup_ui`) orients, mirrors
   and rolls the raw skeleton so tails move coherently.
3. **Joint Chain Build** (`rig_tail_chain_*`) creates BN chains and re-spaces
   existing ones at any joint count.
4. **Tail Reload** re-imports the package.

The handover between the three tools is the BN chain. Chain Build writes
positions, Setup writes orientations, and Build reads both. None of them
stores state in the scene for the next one, which is what lets each be
re-run, skipped or left uninstalled.

The generated hierarchy is listed in the README under
[Rig Hierarchy](README.md#rig-hierarchy).

---

## Architecture

### Design principles

Five decisions shape everything below.

**Pure matrix drive.** BN joints are driven entirely through
`offsetParentMatrix`: a `blendMatrix` between the FK and IK drivers, fed
through `multMatrix` and `composeMatrix` by `rig_tail_matrix`. There are no
constraints and no Euler decomposition, and joint translate, rotate and scale
stay zeroed. Because FK and IK are blended as matrices, mode switching needs
no separate switching network.

**Rebuild-safe by name.** Every node is looked up by its templated name and
reused rather than recreated. `rig_tail_cache` compares the current joints
against the cached ones and decides, per rig part, whether a rebuild needs a
full teardown or only re-wiring. Painted weights and hand-edited control
shapes survive.

**A stored rest anchor.** The IK curve is built from a rest pose held by
`rig_tail_restpose` rather than from live joint positions, so repeated
rebuilds reproduce the same rig instead of compounding curve smoothing.

**Stock nodes only.** The whole system is built from nodes that ship with
Maya and wired with `maya.cmds`. The module installs by copying scripts, a
scene opens anywhere Maya does, and every intermediate value stays an
inspectable plug that can be debugged in the Node Editor. The cost is node
count, which scales with controls times joints, so the weighting network is
kept deliberately lean.

**One-way containment.** Joint Chain Build imports from the core, and no core
module imports it. `rig_tail_chain_test.test_containment` enforces this,
which is what makes the sub-tool removable.

### Module map

Tail Build:

| Module | Alias | Role |
|--------|-------|------|
| `rig_tail` | `rt` | Entry points and build orchestration |
| `rig_tail_build_ui` | `rt_build_ui` | Tail Builder window |
| `rig_tail_cleanup` | `rt_cleanup` | Teardown and build-structure setup |
| `rig_tail_control` | `rt_control` | Control curves, colors, shapes |
| `rig_tail_curve` | `rt_curve` | Curves, spline handles, clusters |
| `rig_tail_fk` | `rt_fk` | Variable FK with SDK groups |
| `rig_tail_stretch` | `rt_stretch` | Stretch and squash |
| `rig_tail_connect` | `rt_connect` | Switching, attributes, final wiring |
| `rig_tail_anim` | `rt_anim` | Curl, wave, noise and loop FX |
| `rig_tail_ctrlall` | `rt_ctrlall` | Main Controller dashboard |
| `rig_tail_build_test` | `rt_build_test` | Diagnostics for a built rig |

Tail Setup:

| Module | Alias | Role |
|--------|-------|------|
| `rig_tail_setup` | `rt_setup` | Orient, mirror and roll the BN skeleton |
| `rig_tail_setup_ui` | `rt_setup_ui` | Tail Setup window |
| `rig_tail_setup_test` | `rt_setup_test` | Tests for the Setup phase |

Joint Chain Build, a removable sub-tool:

| Module | Alias | Role |
|--------|-------|------|
| `rig_tail_chain_spacing` | `rt_chain_spacing` | Spacing math; imports nothing from Maya |
| `rig_tail_chain_build` | `rt_chain` | Maya layer: detect, guard, create, write |
| `rig_tail_chain_build_ui` | `rt_chain_ui` | Joint Chain Builder window |
| `rig_tail_chain_test` | `rt_chain_test` | Tests; the math half runs outside Maya |

Shared:

| Module | Alias | Role |
|--------|-------|------|
| `rig_tail_constants` | `rt_constants` | Constants, settings, caches |
| `rig_tail_naming` | `rt_naming` | Naming templates and name parsing |
| `rig_tail_maya` | `rt_maya` | Scene operations, node creation, binding |
| `rig_tail_joint` | `rt_joint` | Joint chain utilities |
| `rig_tail_math` | `rt_math` | Vector, matrix and B-spline math |
| `rig_tail_matrix` | `rt_matrix` | Matrix offset network builder |
| `rig_tail_mirror` | `rt_mirror` | L/R mirror signs for controls and FX |
| `rig_tail_cache` | `rt_cache` | Joint and control caching |
| `rig_tail_restpose` | `rt_rest` | Rest pose the IK curve is built from |
| `rig_tail_qt` | `rt_qt` | Qt binding: PySide6 or PySide2 |
| `logger_config` | (none) | Shared logging setup |

Every module imports its dependencies as `import rig_tail_x as rt_x`, using
the same alias the `TailReload` button binds.

### Module loading

`install.py` bakes the chosen install's `scripts/` folder into every shelf
button as `TOOL_DIR` and puts it at the front of `sys.path`, so a button runs
the install it was made from even when another copy is registered as a Maya
module. The same path goes into `rigTail.mod` under
`~/Documents/maya/modules/`, which is what makes a bare `import rig_tail`
resolve in the Script Editor.

Every button drops modules from `sys.modules` and imports again. What differs
is how much it drops.

- **`ChainBuild`, `TailSetup` and `TailBuild`** drop every `rig_tail*` module
  except `rig_tail_constants`. The purge is whole rather than per-tool
  because each tool sits on the shared core, and dropping only a tool's own
  modules would leave that core at whatever version it was first imported at.
  `rig_tail_constants` is held back because it carries the session's roster
  and settings, so unsaved RIGPARTS edits, the loaded config path and the
  joint and rest caches survive a relaunch.
- **`TailReload`** drops every `rig_tail*` module and `logger_config`, then
  re-imports the lot under the `rt_*` aliases and leaves a commented workflow
  in the Script Editor.

An edit anywhere in the package is therefore picked up by launching the tool
itself. `rig_tail_constants` is the exception, and `TailReload` is the button
that re-imports it. Nothing patches that module at import time: every module
reads the names it needs directly, so adding or renaming a constant means
clicking `TailReload`.

---

## Tail Build

`rig_tail.build_rig_tail` runs one pipeline per rig part. Each stage below
names the module that owns it and the constants that steer it.

### 1. Teardown decision

`rig_tail_cleanup` and `rig_tail_cache`. The joint cache compares the current
BN chain against the cached one. An unchanged chain takes the light path,
where nodes are found by name and re-wired, while a changed joint count or
position triggers a full teardown of that part. **Force Rebuild** skips the
comparison and tears down unconditionally. It is a per-click action and is
never saved to a config.

`rig_tail_cache.active_parts()` returns the included parts in `RIGPARTS`
order, and every phase iterates that rather than `RIGPARTS` itself.

### 2. Joints and the rest anchor

`rig_tail_joint` detects or rebuilds the BN, FK and IK chains, and
`rig_tail_restpose.capture_rest_pose` runs once at build start.

The rest pose exists because the IK curve is built from joint positions, and
a curve fitted to joints that a previous build already smoothed drifts
further with every rebuild. Capturing once and building from the capture
makes repeated rebuilds reproduce the same rig. The store has a second
reader: `rig_tail_cleanup.capture_bn_poses` reads the stored rest first and
falls back to live positions, so it is also what Remove Rig and every rebuild
restore the BN skeleton to. Clearing it while a rig is posed and then
rebuilding would anchor the setup to that pose, so every caller that clears
scopes the clear to what that caller itself moved.

### 3. Curves and clusters

`rig_tail_curve`. The FK curve follows the joints exactly. The IK side is a
pair: a **driver** curve carrying one CV per cluster plus two up-vector CVs,
and a **solver** curve with one CV per joint, which is what the ikHandle
reads. Clusters are IK only, since nothing deforms the FK curve, whose only
job is to give the variable-FK controls positions to read.

The driver curve is deliberately low-resolution, because one CV per control
is what gives each control a single CV to move. It therefore drives the
solver curve as an offset from rest rather than as an absolute position:

```
solver_cv[i] = driver_sample(t_i) + (rest_cv[i] - driver_rest(t_i))
```

The bracketed term is a build-time constant: how far the low-CV driver curve
falls short at that CV. At rest the two cancel, so the solver curve puts the
joints where they belong and its length matches the chain's. Driving absolute
positions instead flattens the tail's base and shortens the curve below the
chain length.

`rest_cv` is not the joint positions. A degree-3 curve does not pass through
its own CVs, so aiming there leaves the spline settling the joints off it.
`solver_curve_cvs` solves for the CVs that place the joints correctly by
fixed-point iteration on the condition that matters. That condition is not
that the curve passes through the joints, but that the joints, placed by
their own bone lengths, land on the joints, since the ikSpline places by
arclength:

```
P = joints
repeat:  P += joints - place_by_arclength(curve(P))
```

Four passes is enough, needs no extra nodes, and only changes what the
constant above aims at. The constant is held in the base control's local
space and multiplied back out through `basectrl.worldMatrix`, so it turns,
scales and travels with the rig.

Because sampling a B-spline at a parameter is a weighted sum of its CVs whose
weights total 1, this reduces to `rest[i] + Σⱼ wᵢⱼ × (control j's
translation)`, which is linear blend skinning for translating influences.

### 4. Controls

`rig_tail_control` builds the root/cog/base hierarchy, the sliding
variable-FK set, and the IK sets (ik, float, spline, up-vector). Controls are
found and reused by name on rebuild. Colors are written on the shape nodes so
a rebuild updates them, and `PRESERVE_CTRL` keeps hand-edited shapes, keyed
per control type.

The SplineIK set is fixed at five controls on fixed tail fractions whatever
`NUM_CTRL_IK` is, and `spline_control_index` maps the clusters onto them.

The up-vector pair takes its position from its cluster handle and its
orientation from `orient_control_aims`, the same frame every other IK control
row gets: +Y down the row, +Z rolled to `spline_up_vector`. A cluster handle
carries no rotation, so matching it for both would leave these world-aligned
on every tail. The pair brackets the chain (base before the first joint, end
past the last) so their shapes offset along the row in local ±Y, signed by
the same `flip_aim` factor the row is aimed with, which keeps both pointing
away from the tail on a mirrored side. The cluster is constrained to the
control with `maintainOffset`, so the handle the twist solver reads keeps its
world-aligned rest and the twist at rest is unchanged.

### 5. Variable FK

`rig_tail_fk`. N sliding controls whose rotation is distributed to the joints
by position and falloff, after Jeff Brodsky's elephant-trunk rig. Each joint
carries a stack of SDK groups, one per control, receiving the weighted
rotation. A control's Position attribute moves it along the FK curve, and
Falloff widens or narrows the joints it affects.

```
weight = max(0, 1 - |joint_pos - ctrl_pos| / falloff)
joint_rotation = rotation * weight / num_joints
```

A symmetric linear tent: full strength under the control, fading to zero at
the edge of the falloff. Dividing by `num_joints` means widening the falloff
spreads the same total bend further rather than adding more of it. A control
also carries the rotation of every control before it, so the chain reads as
FK.

Four nodes per joint per control draw that curve: a `plusMinusAverage` for
`joint_pos - ctrl_pos`, a `multiplyDivide` scaling it by falloff, a
`remapValue` holding the tent, whose `outputMax` carries the `1/num_joints`
normalisation, and a `multiplyDivide` applying the result to the accumulated
rotation. The ramp clamps out-of-range input to zero, so no condition nodes
are needed.

**Two metrics meet here and must not be confused.**

- `position` (0 to 10, base to tip) is animator-facing: a fraction of **tail
  length**, so 5 is halfway down the tail.
- `joint_pos` (0 to 1, base to tip) is what the network compares against: a
  normalised **Greville abscissa**, a fraction of the curve's *parameter*
  range, which is what a `pointOnCurveInfo` needs to land on a given
  joint.

A `remapValue` per control converts the first into the second. Both
`set_curveinfo_fk`, which draws the control, and `falloff_rotation`, which
rotates the joints, read that same output through `control_position_plug`,
which is what keeps a control drawn on the joints it moves. `falloff` stays
in `joint_pos` units, since a width cannot go through a point-wise remap and
joint units are what make `num_joints` consistent.

`connect_twist_roll` builds the FK-side equivalent of the three basectrl
dials that drive the IK spline handle, so twist, roll and offset work in
either mode. FK offset is an approximation, since the IK handle re-samples
joints along the curve and a chain shaped by rotations has no exact analog,
and `offset_unit_scale` converts between the two so the same dial value reads
the same in both modes.

### 6. Stretch and squash

`rig_tail_stretch`. One stretch ratio, current curve length over cached rest
length, drives Length, which spreads joints along the tail, and Thickness,
which scales BN scaleY/Z by `ratio^-0.5`, blended by `preserveVolume`,
trimmed by `squash` and divided by global scale. The work is split across the
build: `build_stretch` creates the nodes early, and
`connect_stretch_to_joints` wires the basectrl sliders once they exist. The
parent's squash would shear OPM children, so `rig_tail_matrix` cancels it
with a `squashInv` term.

The `stretch` dial takes a different route per mode. FK adds it onto the
ratio directly. The IK ratio is reactive only, so the dial spreads the
controls instead through `connect_stretch_to_ik_controls`: spreading them
grows the driver curve, and the reactive ratio follows the curve on its own.
That keeps the controls sitting on the tail, and it keeps the network
acyclic, because the controls are upstream of the curve that feeds the ratio,
so a control may read the dial and its baked rest but never the curve, its
length or the joints.

All three IK modes share one ratio and one spread factor because they share
one spline, and every set spreads about its own first control. What differs
is how each hierarchy carries the factor, which is decided by a group's
**parent** rather than by its place in the row:

- **Nested** (`spread_nested`): the group hangs off another control of its
  set, so its translate is the segment to that control, and scaling every
  segment lets the DAG accumulate the spread. Covers the whole IK row and
  SplineIK's `bot_sml`, `top` and `top_sml`.
- **Flat** (`spread_flat`, `rest₁ + (restᵢ − rest₁) × factor`): the group
  hangs off the base control, so its translate is already a whole offset and
  scaling it whole would centre the set on the base's origin instead of on
  its anchor. Covers all of Float, whose groups are siblings, and SplineIK's
  `mid_rot`, a sibling of `bot`.

SplineIK's `bot` is its anchor. Its `mid` sits out of the spread entirely: it
is parentConstrained to `bot` and `top`, and those weights carry it
(`weight_mid_to_its_place`). All three sets spread at once with no mode
gating, since only the active mode's controls drive the clusters.

### 7. Connection and switching

`rig_tail_connect` is the final wiring phase. It parents the systems into the
hierarchy, creates the switch and channel-box attributes (the per-tail IKFK
switch on the cog, and stretch, twist and FX attributes on the basectrl
proxied onto every control), wires the IKFK mode SDKs that fade constraint
weights and visibility, and hands off to `rig_tail_matrix`, `rig_tail_anim`
and `rig_tail_stretch` before binding geometry.

`IKFK_MODES_ALL` is positional: SplineIK, IK, Float, FK.

On a rig with two or more parts and `MAIN_CONTROLLER` on, `rig_tail_ctrlall`
adds a dashboard to the cog: an ALL section holding one `all_*` copy of each
routed attribute, and an OVERRIDE section holding a per-tail flag choosing
ALL against the tail's own values. The flag reads `Cog` or `Basectrl`, naming
which control wins rather than reporting a state, since both sit in the same
channel box.

**Consumers must read the routed value.** `rt_ctrlall.resolved_plug(rigname,
attr)` returns the plug to read, and `rt_ctrlall.ikfk_driver(rigname)`
returns the mode. Reading the raw per-tail switch or the plain basectrl
attribute bypasses the override flag silently: the controls still obey it, so
only the thing that skipped it misbehaves.

### 8. Animation FX

`rig_tail_anim` layers Curl (static, with falloff), Wave (a traveling sine),
Noise (jitter) and Loop (modulo time for seamless cycling). Each writes
per-joint rotations into its own `composeMatrix`, which `rig_tail_matrix`
multiplies into the BN `offsetParentMatrix`, so joints rotate about their own
pivots with their channels untouched. Attribute sources go through
`resolved_plug` so the dashboard can route them.

**Curl** shares one falloff profile between the three axes. Each joint's
share of the bend is `u ** curl_falloff`, normalised by the live sum of those
shares, so a curl value names the **total** wrap of the whole chain
(`CURL_DEGREES_PER_UNIT`, 108, which is three full turns at the top of the
slider) whatever the joint count, and the falloff decides only how that wrap
is spread. A per-joint clamp (`CURL_MAX_JOINT_DEGREES`, 90) keeps the tip
inside the coil, since the profile peaks at the tip and an unclamped last
joint folds out of the spiral. A chain with enough joints spreads the wrap
thinly enough to coil twice, while a sparse one saturates its last joints,
and lowering `curl_falloff` recovers most of the range by spreading the same
total over the whole chain.

Rebuilding an effect over an unchanged rig settles to a pass of queries.
Curl's nodes are created only when absent and connected only when
unconnected, while Wave, Noise and Loop go through `sync_expressions`, which
compares each expression against the code it should hold and rewrites only
what differs. An expression's code is fixed by the rig it describes, so an
unchanged rig wants what is already there. The light teardown therefore
leaves the FX network standing.

L/R symmetry comes from `rt_mirror.rotation_signs`.

### 9. Geometry binding

`rig_tail_maya`, controlled by two settings.

`BIND_GEOMETRY` decides whether the tool touches skinClusters at all. With it
off, nothing is bound and nothing is unbound, in the build or in Setup, which
suits meshes another department owns, wrap or blendshape setups, and weights
arriving from an imported file. The rig still builds and still drives its
joints.

`KEEP_WEIGHTS` decides what happens when the tool would otherwise unbind. On,
existing skins survive: rig joints are added to the cluster at weight 0 and
painted weights are untouched. Off, the mesh is unbound and rebound from
scratch. It applies only while `BIND_GEOMETRY` is on, since unbinding with no
rebind to follow never happens.

A build that would add rig joints to an already-painted cluster at weight
zero asks for confirmation first.

---

## Tail Setup

`rig_tail_setup` orients, mirrors and rolls the BN skeleton before the build.
It is optional and never runs during a build.

A chain's positions are the shape the modeller gave it, so Setup changes how
joints rotate rather than where they sit: orientation is written into
`jointOrient` with `rotate` left at zero, which takes the twist out while the
skeleton still reads as a clean rest pose. `MIRROR_JOINTS` is the one step
that moves joints, and moving them is its purpose.

### Toggles

| Constant | Effect | Positions |
|----------|--------|-----------|
| `ORIENT_JOINTS` | Aim-orient each chain so its up-axis stops twisting from joint to joint. Both sides are oriented from their own geometry. | kept |
| `MIRROR_ORIENT` | Reflect matching `L_`/`R_` pairs' **orientation** across the symmetry plane. | kept |
| `MIRROR_JOINTS` | Reflect matching `L_`/`R_` pairs' **positions**, reconcile the target side's **hierarchy**, and build a target side that has no chain at all. | **moved** |

`ORIENT_JOINTS` runs first, then the mirrors, so a mirror copies a clean
source and one run with several toggles enabled is correct.
`MIRROR_SOURCE_SIDE` (default `R`) picks which side is authored,
`MIRROR_AXIS` is the symmetry-plane normal, and the plane passes through the
world origin. `MIRROR_DRYRUN` previews every batch operation.

`MIRROR_JOINTS` is the only toggle that creates a chain, because it is the
one that derives the target's positions in full. The other two rewrite joints
that already exist, so an included rig part with no chain is reported
instead.

It also owns the target side's **hierarchy**. A mirror writes world
matrices, so a target chain hanging off the wrong parent still lands every
joint in the right place and still reports a successful mirror, while
deforming through the wrong parent. `reconcile_chain_structure` moves each
target chain's root under the counterpart of its source root's parent, so a
pair agrees on structure and not only on where its joints sit. Only that
root moves; whatever hangs below it rides along and is reconciled on its own
turn against its own source, which is what keeps a fix aimed at one part
from tearing a nested part off the rig. A target whose mirrored parent
cannot be named unambiguously is left where it is and reported — the source
side's own parent is a worse home than the wrong one the chain already has.

### Strays and ambiguous chains

`mark_stray_nodes` runs first and renames any joint that carries a rig
part's name but that no rig part can own, to `<name>_delN`. Two kinds:

| Kind | Example | Why it cannot be owned |
|------|---------|------------------------|
| Uniquified | `BN_L_wing_base_jnt1` | Maya appends digits to a name already in use. The naming template is the only lens the tool has and it rejects the trailing digits, so detection cannot see the joint — and a part it cannot see reads as missing, which is what had every run leave one more copy behind. |
| Cross-side | `BN_L_finridge_jnt` under `BN_R_fin_jnt` | The two sides are separate by construction, so a left chain hanging off a right one is damage, not a choice. |
| Misplaced twin | a second `BN_L_fin_jnt`, where the source side puts only one | Two valid chains for one name are normally a choice Setup refuses. They stop being one when the pair settles it: if exactly **one** candidate sits under the parent the mirror describes, the others are somewhere the mirror does not. Left alone when the answer is not unarguable — no source side, an unresolvable mirrored parent, or several candidates equally well placed. |

Marking is scoped by **name, not by the roster**. A joint competing for a
rig part's name blocks that name whether or not the roster lists it: an
unlisted `BN_L_fin_jnt` matching two nodes stops a listed `L_finridge`
reconciling just as surely as a listed one would. Only an *excluded* part
is off limits — an unlisted one was never spoken for.

Renamed, never deleted. A joint that looks like garbage may still carry
skin, a constraint, or unfinished work, and a run is a single undo chunk
holding hundreds of operations — being wrong costs far more than the
clutter. The rename is reversible, reported, and enough on its own: it
takes the name out of the convention, so a rig part that two chains
answered to resolves to one and the run carries on. Marked nodes go into
`rig_tail_review_SET` for you to delete once satisfied.

What is left after that is a genuine ambiguity — two chains both validly
named for one rig part. Setup cannot resolve it: the pick would be
arbitrary, and orienting, reparenting or mirroring the wrong chain of a
pair leaves correct-looking joints on a chain nothing is bound to. That
part is held back from the whole run, as if excluded, and every candidate
goes into the review set. The Setup dialog reports this as an incomplete
run rather than a successful one.

`roll_chain` is an interactive per-chain fix-up with no constant. It rolls a
single chain about its aim axis to turn a correctly-oriented but wrong-facing
chain onto the right plane.

### Up mode

`ORIENT_UP_MODE` picks where `ORIENT_JOINTS` takes its up reference. Joint
positions fix the aim, so this decides only the chain's roll about it.

| Value | Up reference | Effect |
|-------|--------------|--------|
| `cascade` (default) | The chain's own first joint as it stands, carried down by parallel transport | Twist goes, and the roll the chain already has is kept, so a mirrored pair stays mirrored and a **Roll Chain** fix-up survives a re-run |
| `best-fit` | The chain's best-fit bend plane normal | Lands a raw skeleton on its own plane in one pass, and overwrites any mirrored or hand-rolled orientation |

The two agree on a `symmetric` pair whose positions are already mirrored.
Elsewhere `best-fit` re-derives a `parallel` pair back to `symmetric`, undoes
a `roll_chain` by exactly the angle rolled, and on a nearly straight chain
falls back to a world axis, which is not mirrored between sides. Use
`best-fit` for the first pass on a raw skeleton and `cascade` from then on.

### Mirror behavior

`MIRROR_BEHAVIOR` picks how `MIRROR_ORIENT` orients the mirrored side.

A frame has six things it could mirror: a rotation about each of its three
axes, and a translation along each. Compare each target axis to the
reflection of its partner's. A rotation mirrors when the two point opposite,
and a translation mirrors when they point the same way. A reflection flips
handedness, so a right-handed frame points an odd number of its axes
opposite: one, or all three. Rotations mirrored plus translations mirrored is
therefore always exactly three, and the behavior decides which three.

| Value | Axes | Rotations mirror | Translations mirror |
|-------|------|------------------|---------------------|
| `mirror` (default) | `-aim -roll -up` | all three | none |
| `symmetric` | `+aim +roll -up` | up | aim, roll |
| `parallel` | `+aim -roll +up` | roll | aim, up |

`aim` runs down the chain (`ORIENT_AIM_AXIS`), `up` is `ORIENT_UP_AXIS`, and
`roll` is the remaining axis.

`mirror` is Maya's `mirrorJoint -mirrorBehavior`, and it is the default
because everything this rig is posed by is a rotation: curl, wave, noise,
twist and roll, every FK control gizmo, and the spline `mid_rot` control. It
spends its three there. The one dial it costs is `offset`, a slide along the
aim, which a sign covers.

Its price is the reversed aim, which two places are told about rather than
left to discover. The spline IK's advanced twist takes a negative forward
axis (`rig_tail_stretch.build_advanced_twist`), and a translation along the
aim reverses, which `rig_tail_mirror.translation_signs` reports.

`symmetric` and `parallel` differ by a 180 degree roll about the aim, so
**Roll Chain** at 180 on the target side converts one into the other for a
single chain. `mirror` reverses the aim, which no roll about it can reach.

Whatever the behavior, `rig_tail_mirror` measures how a pair's chains
actually relate and negates what does not already agree, so curl, wave,
noise, twist, roll and offset obey the behavior on all three axes
(`MIRROR_SLIDERS`). A stored config keeps its own value, so changing the
default does not silently re-orient an existing rig.

The setting and the skeleton can therefore disagree. Nothing the build reads
depends on the setting, since every sign and the spline's forward axis are
measured off the joints, so the rig comes out right either way, and
`rt_mirror.aim_reversed` logs the disagreement.

### Include / Exclude

`RIGPARTS_EXCLUDE` holds rig parts that both the batch Setup operations and
the build skip. Parts move between the **Include** and **Exclude** columns in
the *Edit Rig Parts* editor.

Excluded names stay in `RIGPARTS`, so they keep their place in the roster,
stay renameable, and still resolve for L/R pairing.

| Phase | An excluded part |
|-------|------------------|
| Setup | Is not oriented, not mirrored and not unbound, so its skin survives a run aimed at another tail. Excluding one side of a pair stops that pair mirroring. |
| Build | Is not torn down and not rebuilt. Its controls, curves, clusters, SDK curves, FX network and geometry bind are left as they are, and its IKFK mode is not reset. |

Two things stay roster-wide. Anything the cog owns per tail (the IKFK switch,
the dashboard override flag) is still created for excluded parts, because
their rig is still in the scene and still needs those channels. Cleanup
treats such a node as stale only when its part leaves `RIGPARTS` entirely.
And `cleanup_rig`'s SDK animation-curve sweep spares the curves whose driven
node belongs to an excluded part (`excluded_sdk_curves`).

`rig_tail_single` and `rig_tail_selected` name their parts outright and lift
the exclusion on what they were asked to build, so an explicit request is
never a silent no-op. `rig_tail_multiple` honours the exclusion as it stands.

### Geometry during Setup

Re-orienting or moving a bound joint would drag the mesh, so affected
geometry is re-baselined onto the new pose afterwards with painted weights
kept, or with `KEEP_WEIGHTS` off, unbound and left for the build to rebind.
With `BIND_GEOMETRY` off there is no rebind to follow, so Setup preserves and
re-baselines regardless. Any stored rest pose is cleared either way, so the
build recaptures it.

---

## Joint Chain Build

Three layers, split so the math is testable without Maya.

**Spacing.** Shape is a centripetal Catmull-Rom curve through the chain's own
positions, and distribution is where along that arclength each joint sits.
The profiles are analytic functions of `j/(n-1)`, which makes them
count-independent and idempotent. Catmull-Rom and PCHIP both reproduce their
knots, so re-spacing at an unchanged count returns the chain untouched, and
re-applying a profile to its own result is a no-op. Shrinking is the one
lossy operation, because curvature between retained joints is unrecoverable
by any algorithm.

Both parametric profiles taper the same way. Power and Ratio start with long
segments at the base and shorten toward the tip, so a larger number is a
stronger taper in either mode, and Invert is the single control that swaps
direction. Power evaluates `t**(1/k)` to get there.

**Scene layer.** `rig_tail_chain_build` resolves a selection into chain
specs, guards them, resamples through the spacing layer and writes the
result. Joints are reused in place, so names, rotate orders and custom
attributes survive wherever the count allows. `_ORIGINALS` maps a chain
root's long DAG path to the positions first seen this session, so a sequence
of count changes costs one approximation rather than one per step. It is a
module global, dies on reload, writes nothing to the scene, and re-baselines
when the chain stops matching what was last written.

A skinned, rig-driven, branching or degenerate chain aborts before anything
moves, and each listed chain is guarded on its own, so one bad chain still
leaves the others rebuilt.

The tool writes positions only. Orientation belongs to Setup, and **Orient
joints** is an opt-in convenience that calls `rig_tail_setup.aim_frames`
read-only so the two tools agree. The one other channel a rebuild writes is
the display `radius`, fitted to the new spacing.

**Window.** `rig_tail_chain_build_ui` is stateless: every option is a widget
read at click time, with no config file and no preferences. What it remembers
is scene state, meaning which chains Select found and which joint of each was
picked.

---

## Tail Reload

`TailReload` opens no window. It drops every `rig_tail*` module and
`logger_config` from `sys.modules`, re-imports them under the `rt_*` aliases,
and leaves a commented workflow in the Script Editor covering the chain,
setup, build and test calls.

It is the only button that re-imports `rig_tail_constants`, so it is what
picks up an edit to that file without a Maya restart. The trade-off is that
it resets the session state that module carries: the settings edited in the
UI return to the loaded config.

---

## Configuration / Constants

`rig_tail_constants` holds every setting, the session's roster and the
caches. It is data and config helpers only, with no dependency on the modules
that reload around it.

| Group | Constants |
|-------|-----------|
| Roster | `RIGPARTS`, `RIGPARTS_EXCLUDE`, `ROOT` |
| Naming | `JOINT`, `CTRL`, `CTRL_GRP`, `SDK_GRP`, and the rest of the templates |
| Build options | `BUILD_FK`, `BUILD_IK`, `INDIV_FK`, `MAIN_CONTROLLER` |
| Control counts | `NUM_CTRL_FK`, `NUM_CTRL_IK` |
| Modes | `IKFK_MODES_ALL` (positional: SplineIK, IK, Float, FK), `IKFK_SWITCH` |
| Setup | `ORIENT_JOINTS`, `MIRROR_ORIENT`, `MIRROR_JOINTS`, `MIRROR_DRYRUN`, `MIRROR_AXIS`, `MIRROR_SOURCE_SIDE`, `MIRROR_BEHAVIOR`, `ORIENT_AIM_AXIS`, `ORIENT_UP_AXIS`, `ORIENT_UP_MODE` |
| Geometry | `BIND_GEOMETRY`, `KEEP_WEIGHTS`, `JOINT_POS_TOLERANCE` |
| Appearance | `COLOR_OVERRIDE`, `COLOR_SKELETON`, `BN_COLOR`, `IK_COLOR`, `FK_COLOR`, `PRESERVE_CTRL` |
| Effects | `EFFECTS` |

`IKFK_SWITCH` embeds `':'.join(IKFK_MODES)` as its enum string, so
`rebuild_derived()` regenerates it after `IKFK_MODES` changes.
`COLOR_OVERRIDE` maps a name to a Maya override index, 1 to 31.
`PRESERVE_CTRL` is keyed by control type, so shapes can be held per set.

Two effect limits live with the code that applies them, in `rig_tail_anim`:
`CURL_DEGREES_PER_UNIT` and `CURL_MAX_JOINT_DEGREES`. `MIRROR_SLIDERS`, the
list of dials that carry a mirror sign, lives in `rig_tail_mirror`.

Settings round-trip through a JSON config. `FORCE_REBUILD` is deliberately
not loaded, since forcing is a per-click action of the Build UI's button.

| Function | Purpose |
|----------|---------|
| `save_config(path=None)` | Write every user-editable value to JSON |
| `load_config(path=None)` | Read a config and apply it to the module |
| `get_user_editable_config()` | The dict that round-trips through a config |
| `active_rigparts()` | `RIGPARTS` minus `RIGPARTS_EXCLUDE` |
| `effects_enabled()` | Whether any FX option is on |
| `ikfk_mode_index(name)` | Index of a mode in `IKFK_MODES`, or None |
| `ikfk_fk_mode_index()` / `ikfk_default_index()` | The FK slot, and the startup mode |
| `update_ikfk_modes(modes)` | Replace the mode list and rebuild what derives from it |
| `rebuild_derived()` | Regenerate values derived from other constants |

`CONFIG_FILE` is the default path, and `LOADED_CONFIG` names the file
currently in effect.

---

## Testing

Three test modules, one per tool. None is needed to run the rig, since they
are for changing it. Each prints a PASS/FAIL summary and returns a bool.

| Module | Covers | Needs a scene |
|--------|--------|---------------|
| `rig_tail_chain_test` | Joint Chain Build | math half no, scene half yes |
| `rig_tail_setup_test` | Tail Setup | yes |
| `rig_tail_build_test` | A built rig | yes |

```python
import rig_tail_chain_test as rt_chain_test
rt_chain_test.run_math()      # safe, and runs outside Maya
rt_chain_test.run_scene()     # MUTATING: reload the scene afterwards

import rig_tail_setup_test as rt_setup_test
rt_setup_test.run_all()

import rig_tail_build_test as rt_build_test
rt_build_test.run_all()
```

`run_math()` is the only entry point that needs no Maya, since the spacing
layer imports nothing from it:

```bash
python -c "import rig_tail_chain_test as t; t.run_math()"
```

run from `rigTail/scripts/`. It pins the properties the tool's promises rest
on: endpoints land on 0 and 1 for every mode, invert is its own inverse,
Power and Ratio taper base to tip, both interpolants reproduce their knots,
re-applying a profile to its own result does not drift, and a clean count
change snaps to every other original exactly.

**Mutating tests restore what they can, not everything.** The scene tests
snapshot names, positions and radii and put them back between tests, but a
shrink deletes joints and they return as new nodes. Reload the scene after a
scene run. `test_guards` is the exception, since it works on its own scratch
chain and never touches the loaded skeleton.

**`test_containment` is an architecture test.** It reads every core module
and fails if any imports `rig_tail_chain*`, which is what keeps Joint Chain
Build removable. Comments and docstrings may name the sub-tool, and only an
import couples the core to it.

---

## Module reference

### rig_tail.py (rt)

Entry points and build orchestration.

| Function | Purpose |
|----------|---------|
| `build_rig_tail(fk, ik)` | Run the full pipeline over the active rig parts |
| `rig_tail_single(root, fk=True, ik=True, start_jnt=None, end_jnt=None)` | Build one tail from one chain |
| `rig_tail_multiple(root, fk=True, ik=True)` | Build every part in `RIGPARTS` |
| `rig_tail_selected(root, fk=True, ik=True)` | Build the parts of the selected joints |
| `rig_tail_fk(rigname, typ='fk')` | FK system for one part |
| `rig_tail_ik(rigname, typ='ik')` | IK system for one part |
| `restore_bn_for_build(parts)` | Put BN back on its rest pose before building |
| `guard_unique_rigparts()` | Refuse to build while two chains share a rig part name |
| `setup_tails(root=None, dry_run=None)` | Delegate to the Setup phase |
| `main()` / `main_setup()` | Launch the Builder or Setup window |

### rig_tail_constants.py (rt_constants)

Every setting, the session's roster and the caches, plus the JSON config
round-trip. Listed in full under
[Configuration / Constants](#configuration--constants).

| Function | Purpose |
|----------|---------|
| `save_config(path=None)` / `load_config(path=None)` | Write and read a JSON config |
| `get_user_editable_config()` | The dict that round-trips through a config |
| `active_rigparts()` | `RIGPARTS` minus `RIGPARTS_EXCLUDE` |
| `effects_enabled()` | Whether any FX option is on |
| `ikfk_mode_index(name)` | Index of a mode in `IKFK_MODES`, or None |
| `ikfk_fk_mode_index()` / `ikfk_default_index()` | The FK slot, and the startup mode |
| `update_ikfk_modes(modes)` | Replace the mode list and rebuild what derives from it |
| `rebuild_derived()` | Regenerate values derived from other constants |

### rig_tail_cleanup.py (rt_cleanup)

Teardown of a previous rig, plus the group structure a build needs.

| Function | Purpose |
|----------|---------|
| `cleanup_rig(fk, ik)` / `cleanup_rigname(rigname, fk, ik)` | Tear down all parts, or one |
| `cleanup_connections(rigname, fk, ik)` | Light path: break connections, keep nodes |
| `cleanup_anim_effects(rigname)` | Remove the FX network for a part |
| `excluded_sdk_curves()` | SDK curves the sweep must spare |
| `cleanup_dangling_curveinfo()` / `cleanup_dangling_unit_conversions()` | Drop orphaned utility nodes |
| `remove_rig()` | Remove the rig, keeping the BN skeleton |
| `capture_bn_poses(parts, rest=True)` | Stored rest first, live positions as fallback |
| `restore_bn_skeleton(parts, poses=None, bn_paths=None)` | Put BN back on a captured pose |
| `restore_fk_joint_chain(rigname)` | Rebuild the FK chain under its SDK groups |
| `fk_sdk_structure_is_current(rigname)` | Whether the SDK stack matches the settings |
| `setup_rig(fk, ik)` | Create the group structure a build needs |
| `set_root(root)` / `find_existing_root_grp()` | Resolve the rig root |
| `set_joints(rigname, start_jnt=None, end_jnt=None)` / `set_joints_auto()` | Populate the joint caches |
| `detect_joints_bn()` / `bn_start_candidates()` | Find BN chains in the scene |
| `duplicate_rigparts()` | Rig part names claimed by more than one chain |
| `fk_ik_match_bn(rigname, tol=None)` | Whether the rig chains still match BN |
| `rigpart_has_joints(rigname)` | Whether a part has a chain in the scene |
| `rename_rigpart(old, new)` / `rename_components(...)` | Rename a part in place, by whole name token |
| `rig_leftovers(parts=None)` | Nodes a teardown would remove |
| `resolve_node_types(types)` | Map build-time node type names onto the running Maya's |

### rig_tail_control.py (rt_control)

Control curves: the root/cog/base hierarchy, the variable-FK set, and the IK
sets, plus colors, shapes and channel-box attributes.

| Function | Purpose |
|----------|---------|
| `create_root_cog()` / `create_basectrl(rigname)` | The shared top of the hierarchy |
| `create_controls_fk(rigname, ...)` | The sliding variable-FK set |
| `create_controls_ik(rigname, ...)` | The chained IK set |
| `create_spline_controls_ik/_float/_spline(...)` | The Float and SplineIK sets |
| `create_spline_up_vectors(rigname, ...)` | The bracketing up-vector pair |
| `spline_control_index(i, n)` | Map a cluster onto its SplineIK control |
| `get_controls_ik(rigname)` / `get_control_hierarchy(...)` | Find an existing control set |
| `get_control_position(ctrl)` | A control's position along the chain |
| `create_control(...)` / `create_control_match_list(...)` | Build one control, or a row |
| `build_control_shapes(...)` / `create_control_shape(...)` | Shape creation and replacement |
| `create_circle_control` / `create_sphere_control` / `create_cube_control` | The shape primitives |
| `set_control_color(ctrl, color)` | Write the override color onto the shapes |
| `orient_control_aims(...)` / `orient_aim_controls_nulls(...)` | The shared IK row frame |
| `mirror_control_frames(rigname, ...)` | Apply the mirror signs to a control row |
| `add_fk_attributes_to_controls(...)` | Position and Falloff on the FK controls |
| `set_attributes_visibility_fk/_ik(...)` | Channel-box visibility per mode |

### rig_tail_curve.py (rt_curve)

Curves, spline IK handles and clusters, including the driver and solver
curve pair.

| Function | Purpose |
|----------|---------|
| `create_curve(rigname, positions, typ)` | Create or reuse a curve through positions |
| `driver_curve_positions(rigname, joints)` | CV positions for the low-resolution driver |
| `solver_curve_cvs(joints, ...)` | CVs that place joints correctly by arclength |
| `connect_driver_to_solver_curve(rigname, ...)` | Wire the offset-from-rest relationship |
| `rest_aim_frames(rigname, joints)` | Per-CV rest frames the offset is held in |
| `wire_aim_frame(...)` / `base_up_node(rigname)` | The frame nodes the offset multiplies through |
| `create_spline_handle(rigname, ...)` / `get_spline_handle(rigname)` | The ikSpline handle |
| `rename_spline_handle(rigname, handle)` | Conform a handle's name on rebuild |
| `create_cluster(...)` / `create_clusters_on_curve(...)` | Clusters on the driver curve |

### rig_tail_fk.py (rt_fk)

Variable FK: N sliding controls whose rotation is distributed to the joints
by position and falloff.

| Function | Purpose |
|----------|---------|
| `control_position_plug(rigname, ctrl)` | The shared `joint_pos` output for a control |
| `set_curveinfo_fk(rigname, ctrl, ...)` | Draw a control on the FK curve |
| `falloff_rotation(rigname, ...)` | The four-node weighting network per joint |
| `create_sdk_groups(rigname, ...)` / `get_sdk_groups(rigname, ...)` | The per-joint SDK stack |
| `put_jnt_under_sdk_groups(rigname, ...)` | Parent each FK joint under its stack |
| `connect_twist_roll(rigname, ...)` | FK-side twist, roll and offset |
| `offset_unit_scale(rigname)` | Convert offset units between FK and IK |

### rig_tail_stretch.py (rt_stretch)

Squash and stretch: one ratio driving joint spread and joint scale, plus the
IK control spread.

| Function | Purpose |
|----------|---------|
| `build_stretch(rigname, ...)` | Create the ratio and scale nodes |
| `connect_stretch_to_joints(rigname, ...)` | Wire the basectrl sliders to the joints |
| `connect_fk_stretch_to_joints(...)` / `connect_ik_stretch_to_joints(...)` | The per-mode routes |
| `connect_stretch_to_ik_controls(rigname, ...)` | Spread the IK controls from the dial |
| `spread_nested(...)` / `spread_flat(...)` | The two spread rules, chosen by a group's parent |
| `spread_factor_node(rigname)` / `spread_rest(...)` | The shared factor and its baked rest |
| `ik_control_groups` / `float_control_groups` / `spline_control_groups` | The groups each rule covers |
| `create_stretch(...)` / `create_squash(...)` | The ratio and its inverse-square scale |
| `connect_preserve_volume(...)` / `connect_joint_squash(...)` | Volume preservation and trim |
| `create_world_scale(...)` / `connect_world_scale(...)` | Divide out global scale |
| `add_stretch_attributes_to_basectrl(...)` | The stretch dials |
| `add_jntscale_attributes_to_basectrl(...)` | The thickness dials |
| `set_curveinfo_stretch(...)` / `fallback_curve_length(...)` | Live and rest curve length |
| `build_advanced_twist(rigname, ...)` | Advanced twist on the spline handle |

### rig_tail_connect.py (rt_connect)

The final wiring phase: parenting, switch and channel-box attributes, and the
IKFK mode SDKs.

| Function | Purpose |
|----------|---------|
| `connect_rig_tail(rigname, fk, ik)` | Run the whole connect phase for a part |
| `connect_root()` / `connect_cog()` / `connect_basectrl(rigname)` | Parent the shared hierarchy |
| `connect_fk(rigname)` / `connect_spline_fk(rigname)` | Parent and wire the FK system |
| `connect_ik(rigname)` / `connect_spline_ik(rigname)` | Parent and wire the IK systems |
| `connect_twist_roll_ik(rigname)` | Twist, roll and offset on the spline handle |
| `connect_stretch(rigname)` / `connect_effects(rigname)` | Hand off to stretch and FX |
| `add_attributes_ikfk_switch(rigname, ...)` | The per-tail IKFK switch on the cog |
| `add_ikfk_attributes_to_basectrl(...)` / `add_twist_attributes_to_basectrl(...)` | Basectrl dials |
| `add_switch_proxies_to_control(...)` / `add_proxy_attributes_to_controls(...)` | Proxy the dials onto every control |
| `setup_switch_fk/_ik/_upvec(...)` | Mode SDKs fading weights and visibility |
| `constrain_spline_controls(rigname)` | Constrain the SplineIK set to the clusters |
| `weight_mid_to_its_place(rigname)` | Weight `mid` between `bot` and `top` |
| `match_fk_to_ik_rest(rigname)` | Align the FK chain to the IK rest |
| `enforce_attr_order(...)` / `rootctrl_attr_specs()` | Hold channel-box order stable |
| `cache_controls_ik` / `get_cached_controls_ik` / `clear_control_cache` | Per-build control cache |

### rig_tail_anim.py (rt_anim)

Curl, wave, noise and loop FX, written into per-effect matrices.

| Function | Purpose |
|----------|---------|
| `build_anim_effects(rigname, ...)` | Build every enabled effect for a part |
| `build_curl(rigname, ...)` | Static bend with a shared falloff profile |
| `build_wave(rigname, ...)` | Traveling sine along the chain |
| `build_noise(rigname, ...)` | Per-joint jitter |
| `build_loop(rigname, ...)` | Modulo time for seamless cycling |
| `add_anim_attributes_to_basectrl(rigname, ...)` | The FX dials |
| `sync_expressions(rigname, ...)` | Rewrite only the expressions that differ |
| `remove_expressions(rigname)` | Drop a part's FX expressions |

### rig_tail_ctrlall.py (rt_ctrlall)

The Main Controller dashboard for rigs with two or more tails.

| Function | Purpose |
|----------|---------|
| `active()` | Whether the dashboard should be built |
| `add_dashboard_to_cog(cog_ctrl, fk, ik)` | Build the ALL and OVERRIDE sections |
| `add_all_ikfk_to_cog(cog_ctrl, ...)` | The ALL IKFK dial |
| `order_all_section(cog_ctrl, specs)` | Hold the ALL section in a stable order |
| `add_override_to_control(rigname, control)` | Add the per-tail override flag |
| `build_override_conditions(rigname, fk, ik)` | Condition network routing ALL against own |
| `resolved_plug(rigname, attr)` | The plug a consumer must read |
| `ikfk_driver(rigname)` | The mode a consumer must read |
| `routed_attr_specs()` / `all_attr(attr)` / `condition_node(...)` | Dashboard naming helpers |
| `hide_resolved_attrs(rigname)` | Keep the routing nodes out of the channel box |
| `cleanup_ctrlall(fk, ik)` | Remove dashboard nodes for parts that left the roster |

### rig_tail_setup.py (rt_setup)

The Setup phase: orient, mirror and roll the BN skeleton before the build.

| Function | Purpose |
|----------|---------|
| `setup_tails(root=None, dry_run=None)` | Detect BN chains, run the enabled steps, re-baseline skinned meshes |
| `run_setup(dry_run=None, skip=None)` | Run the enabled batch steps on `JOINTS_BN` |
| `orient_chains(dry_run, skip=None)` | Aim-orient every included chain |
| `mirror_chains(dry_run, do_orient, do_positions, skip=None)` | Mirror orientation and/or positions across pairs |
| `create_missing_chains(dry_run, detected=None, skip=None)` | Build a target side that has no joints |
| `reconcile_chain_structure(dry_run, skip=None)` | Hang each target chain under the mirror of its source's parent |
| `mark_stray_nodes(dry_run, skip=None)` | Rename joints no rig part can own to `<name>_delN` |
| `roll_chain(rigname, degrees)` | Roll one chain about its aim axis |
| `aim_frames(positions, aim_axis, up_axis, up_ref=None)` | Per-joint world frames for a chain |
| `mirror_frames(src_matrices, axis, aim_axis, up_axis)` | Reflect a set of frames across the plane |
| `rignames_from_selection()` / `rigname_from_selection()` | Rig part names for the selected joints |
| `show_joint_orients(show=True)` | Toggle local-axis display on BN joints |
| `find_mirror_pairs(rigparts)` | Pair rig parts by side prefix (re-exported from `rig_tail_naming`) |

### rig_tail_chain_spacing.py (rt_chain_spacing)

Spacing math for Joint Chain Build. Imports nothing from Maya, so it runs
under plain Python.

| Function | Purpose |
|----------|---------|
| `resample(source_points, n, mode, param=None, invert=False, snap=True)` | Full pipeline: curve, distribute, sample |
| `distribute(mode, n, param=None, invert=False, source=None)` | Normalised arclength positions for a profile |
| `snap_to_source(u_list, s_hat, tol=SNAP_TOL)` | Snap near-exact matches onto original joints |
| `catmull_rom_knots(points)` / `catmull_rom_eval(points, knots, t)` | Centripetal Catmull-Rom curve |
| `arclength_table(points)` / `eval_at_arclength(...)` | Arclength parameterisation |
| `pchip_tangents(x, y)` / `pchip_eval(x, y, m, xq)` | Monotone interpolation, used by Keep |

### rig_tail_chain_build.py (rt_chain)

The Maya layer for Joint Chain Build: detect, guard, create and write.

| Function | Purpose |
|----------|---------|
| `rebuild(root_joint, n, mode='keep', ...)` | Re-space one chain |
| `rebuild_selected(n, mode='keep', ...)` | Re-space every chain in the selection |
| `build_new(start, end, n, rigname=None, ...)` | Create a chain between two objects |
| `rename_chain(root_joint, new_rigname)` | Move one chain onto a different rig part name |
| `resolve_selection()` | Selection to chain specs, one per chain |
| `chain_root(joint)` | Walk to the root of a joint's chain |
| `clear_cache(root=None)` | Drop the remembered original shape |

### rig_tail_maya.py (rt_maya)

Maya scene operations, node creation and geometry binding, grouped by area.

| Area | Functions |
|------|-----------|
| Existence | `obj_exists`, `remove`, `existing`, `unique_path`, `leaf`, `is_end_joint` |
| Parenting | `parent_to`, `is_parent`, `get_constraint` |
| Scene queries | `get_geometry_from_scene`, `get_joints_from_scene`, `get_controls_from_scene`, `is_control`, `is_geometry`, `list_hierarchy` |
| Connections | `disconnect_all`, `disconnect_nodes`, `ensure_connect`, `break_connection`, `remove_nodes`, `curveinfo_consumers`, `plug_and_children` |
| Transforms | `opm`, `reset_opm`, `reset_transforms`, `match_transform` |
| Channels | `set_channel_flags`, `has_non_default_locked_attributes`, `set_joint_channels`, `finalize_joint_channels` |
| Visibility | `set_visibility`, `set_transform_visibility`, `set_curve_visibility`, `set_group_visibility` |
| Color | `set_joint_color`, `color_skeletons` |
| Node creation | `create_group`, `create_condition`, `create_condition_multi`, `create_curveinfo`, `get_num_cv`, `sdk`, `swap_shapes` |
| Attributes | `add_attribute_enum`, `set_attr_value`, `remove_attribute`, `attribute_is_reusable`, `attribute_is_proxy` |
| Binding | `bind_geometry`, `unbind_geometry`, `unbind_geometry_all`, `geometry_transforms`, `find_geometry_for_rigname`, `geometry_matches_rigname`, `report_missing_geometry` |
| Skin preservation | `bind_enabled`, `keep_weights`, `joints_missing_from_skin`, `find_skincluster`, `skin_influence_indices`, `add_missing_influences`, `rebaseline_skin`, `bind_skincluster`, `unbind_skincluster` |
| Session | `force_refresh`, `ensure_plugins`, `build_performance_scope`, `build_timer`, `timed` |

### rig_tail_naming.py (rt_naming)

Naming templates and name parsing.

| Function | Purpose |
|----------|---------|
| `fstr(rigname, template, TYPE='', NN='', nn='', TAG='')` | Evaluate a naming template |
| `get_rigname(node, template)` | Extract a rig name from a node name |
| `compile_template_to_regex(template)` / `parse_placeholder(token)` | Template to matcher |
| `get_index_from_name(name)` / `replace_index_in_name(name, index)` | Read and rewrite the index token |
| `strip_group_suffix(name)` / `titlecase(name)` | Name helpers |
| `find_mirror_pairs(rigparts)` / `mirror_partner(rigname)` | Pair rig parts by side prefix |
| `name_matches_rigname(...)` / `name_contains_rigname_terms(...)` | Whole-token matching |
| `rename_shapes(node, name)` | Rename shapes to match their transform |

`fstr` evaluates the template against locals holding the component tokens, so
a template may reference `GRP`, `CTRL`, `JNT`, `SDK`, `CRV`, `CSR`, `HDL`,
`EFF`, `VIS`, `COND` and `CST`.

### rig_tail_mirror.py (rt_mirror)

Measures how an L/R pair's chains actually relate, and reports the signs that
make their dials agree.

| Function | Purpose |
|----------|---------|
| `rotation_signs(rigname)` | Per-axis sign for rotation dials |
| `translation_signs(rigname)` | Per-axis sign for translation dials |
| `control_signs(rigname)` | Signs for mirroring a control row |
| `mirrored_matrix(matrix, signs)` | Apply those signs to a matrix |
| `spline_up_vector()` / `aim_axis()` | The stated up direction and the aim axis |
| `flip_control_aim(rigname)` | Whether a side's control row aims backwards |
| `behavior()` | The active `MIRROR_BEHAVIOR` |
| `aim_reversed(rigname)` | Log when the skeleton and the setting disagree |

### rig_tail_cache.py (rt_cache)

The joint and control caches that make a rebuild incremental.

| Function | Purpose |
|----------|---------|
| `active_parts()` | Included parts, in `RIGPARTS` order |
| `excluded_parts()` / `include_parts(parts)` | Read and edit the exclusion |
| `validate_cache()` | Whether the cached joints still describe the scene |
| `validate_cache_structure()` / `validate_cache_joints()` | The two halves of that check |
| `cache_controls_ik(...)` / `get_cached_controls_ik(...)` / `clear_control_cache()` | Per-build control cache |

### rig_tail_restpose.py (rt_rest)

The rest anchor: the canonical pose the IK curve is built from.

| Function | Purpose |
|----------|---------|
| `capture_rest_pose(rignames=None)` | Store the current BN pose as the anchor |
| `curve_source_positions(rigname, joints)` | Positions the IK curve is built from |
| `rest_positions(rigname, joints)` | The stored rest, or live as fallback |
| `clear_rest_pose(rignames=None)` | Drop the stored pose for the named parts |

The build touches this module in two places: `build_rig_tail` calls
`capture_rest_pose` once at build start, and `rig_tail_ik` calls
`curve_source_positions` to decide what the IK curve is built from.
`curve_source_positions` always falls back to live positions.

### rig_tail_joint.py (rt_joint)

Joint chain utilities.

| Function | Purpose |
|----------|---------|
| `get_joint_chain(start_jnt, end_jnt=None)` | Walk a chain, stopping where the rig part changes |
| `get_joint_hierarchy(root)` | Every joint under a root |
| `get_joint_position_from_list(joints)` | World positions for a joint list |
| `set_joint_attributes(joints)` | Apply the standard joint channel settings |
| `is_equal_joint(jnt1, jnt2)` | Compare two joints by position and orientation |

### rig_tail_math.py (rt_math)

Vector, matrix and B-spline math. No Maya dependency beyond node queries.

| Area | Functions |
|------|-----------|
| Sequences | `linspace`, `cumulative_lengths`, `length_fractions`, `nearest_index` |
| B-spline | `clamp_degree`, `clamped_uniform_knots`, `greville_fractions`, `bspline_point`, `bspline_arclength_table`, `bspline_at_arclength` |
| Vectors | `transform_vector`, `invert_matrix`, `get_vec_length`, `axis_vector_colinearity` |
| Positions | `get_world_pos`, `get_local_pos`, `get_local_vec`, `get_local_vec_to_worldspace` |
| Orientation | `get_axis_orientation`, `get_local_orientation` |

### rig_tail_matrix.py (rt_matrix)

The matrix network that drives every BN joint.

| Function | Purpose |
|----------|---------|
| `build_matrix_offset_network(rigname, fk, ik)` | Build the network for a whole rig part |
| `create_matrix_nodes_for_joint(rigname, jnt, ...)` | The per-joint blend, mult and compose nodes |

The network blends the FK and IK drivers with a `blendMatrix`, multiplies in
the FX matrices from `rig_tail_anim` and the `squashInv` term from
`rig_tail_stretch`, and writes the result to `offsetParentMatrix`.

### rig_tail_qt.py (rt_qt)

The Qt binding for the tool windows, resolved once for the running Maya.

| Maya | Qt | Binding | Python |
|------|-----|---------|--------|
| 2024 | 5.15.2 | PySide2 / shiboken2 | 3.10.8 |
| 2025 | 6.5.3 | PySide6 / shiboken6 | 3.11.4 |
| 2026 | 6.5.3 | PySide6 / shiboken6 | 3.11.4 |
| 2027 | 6.8.3 | PySide6 / shiboken6 | 3.13.9 |

No Maya ships both, so the module imports one and falls back to the other.
PySide6 is tried first, because a studio that has pip-installed PySide2 into
a Qt6 Maya would otherwise bind a Qt5 wrapper over a Qt6 runtime, which
crashes rather than raising `ImportError`.

The windows stay binding-agnostic because they use a small surface
(`QtWidgets`, `QtCore.Qt` and `wrapInstance`), every name of which is spelled
the same in both, and PySide6 keeps the short enum form (`Qt.AlignCenter`)
alongside the qualified one. `QAction`, which moved from `QtWidgets` to
`QtGui` in Qt6, is not used. Adding a Qt class that differs between versions
is what would break this, so import from here rather than from `PySide*`
directly.

| Name | Purpose |
|------|---------|
| `QtWidgets`, `QtCore` | The resolved binding's modules |
| `wrapInstance` | shiboken's pointer wrapper, for parenting to Maya |
| `QT_BINDING` | `'PySide6'` or `'PySide2'`, for logging and errors |

### logger_config.py

Shared logging setup for every module.

| Function | Purpose |
|----------|---------|
| `logger_setup(name)` | The module logger each file binds at import |
| `set_level(level)` / `reset_levels()` | Raise or restore verbosity for a session |
| `abort_build(message)` | Raise `RigTailBuildError` to stop a build cleanly |

### UI modules

Each opens a single window and holds no state the build reads.

| Module | Entry point | Window |
|--------|-------------|--------|
| `rig_tail_build_ui` | `show_ui()` | Tail Builder: current configuration, build options, and the pop-up editors for RIGPARTS, naming templates and constants |
| `rig_tail_setup_ui` | `show_ui()` | Tail Setup: the three toggles, the axis and behavior dropdowns, and Roll Chain |
| `rig_tail_chain_build_ui` | `show_ui()` | Joint Chain Builder: chain list, joint count, spacing profile |

All three take their Qt binding from `rig_tail_qt` and parent to the Maya
main window through `get_maya_window()`.

### Test modules

| Module | Entry points |
|--------|--------------|
| `rig_tail_chain_test` | `run_math()`, `run_scene(chain='C_tail')`, `run_all()` |
| `rig_tail_setup_test` | `run_math()`, `run_scene(base='fintail', chain='C_tail')`, `check_mirror(base='fintail')`, `check_skin(rigname='C_tail')`, `run_all()` |
| `rig_tail_build_test` | `run_all(rigname='tail')`, `report_bend(rigparts)`, `probe(stage, rigname)`, `measure_rebuild_degradation(rigparts, rebuilds=2)`, `test_build_exclusion(rigname)`, `test_remove_rig(tolerance=0.001)`, `profile_build(...)`, `profile_cmds()` |

`rig_tail_build_test` also carries per-system checks (`test_matrix`,
`test_ikfk_drive`, `test_override_routing`, `test_stretch`, `test_wave`,
`test_curl`, `test_solver_curve_shape`, and others) plus scene inspection
helpers (`show_data_flow`, `print_chain`, `dump_chain`, `print_matrix`).
