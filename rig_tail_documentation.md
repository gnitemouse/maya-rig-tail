# Rig Tail Documentation

## Author
Daisy Jane @gnitemouse

Complete API reference for the Maya Tail Rig system.

> **Naming.** The toolset is **Rig Tail**, and it ships four sub-tools:
> **Joint Chain Builder** (`ChainBuild`), **Tail Setup** (`TailSetup`),
> **Tail Builder** (`TailBuild`) and **Tail Reload** (`TailReload`). The
> repository is `maya-rig-tail`, the Maya module folder is `rigTail/`, and
> every script and import alias keeps the `rig_tail_*` / `rt_*` prefix.

---

## Three tools: Chain Builder, Tail Setup, Tail Build

Three tools run in order, each with its own shelf button. Only the last is
required; the first two prepare the skeleton and never run during a build.

```
[Chain Builder]  ->  [Tail Setup]  ->  [Tail Build]
 joint positions      orient / mirror   the rig
   (optional)           (optional)
```

1. **Chain Builder** (optional, `rig_tail_chain_*`): creates BN joint chains
   between two objects and re-spaces existing ones at any joint count. Moves
   joints; leaves orientation to Setup. Removable — no core module imports
   it.
2. **Tail Setup** (optional, `rig_tail_setup` + `rig_tail_setup_ui`):
   orients, mirrors and rolls the raw BN skeleton so tails move coherently.
   Most of it changes only joint orientation; the exception is
   `MIRROR_JOINTS`, which mirrors positions too. Skip it if the skeleton is
   already well oriented; the build is unaffected either way.
3. **Tail Build** (`rig_tail` and the build modules): tears down any previous
   rig, then creates joints, curves, controls and node networks, and binds
   the geometry.

The handover between them is the BN chain and nothing else. Chain Builder
writes positions, Setup writes orientations, Build reads both and generates
everything downstream. None of the three leaves state in the scene for the
next, which is why any of them can be re-run, skipped or removed.

## How the modules get loaded

`install.py` bakes the chosen install's `scripts/` folder into every shelf
button as `TOOL_DIR` and puts it at the front of `sys.path`, so a button runs
the install it was made from even when another copy of Rig Tail is registered
as a Maya module. The same path goes into the `rigTail.mod` under
`~/Documents/maya/modules/`, which is what makes a bare `import rig_tail`
work in the Script Editor.

Every button refreshes the same way: drop modules from `sys.modules`, then
import. What differs is how much gets dropped.

- **`ChainBuild` / `TailSetup` / `TailBuild`** drop only their own prefix
  (`rig_tail_chain*`, `rig_tail_setup*`, `rig_tail_build*`). Shared modules,
  `rig_tail_constants` included, stay loaded — so the settings edited in the
  UI, the loaded config path and the joint/rest caches survive a relaunch.
- **`TailReload`** drops every `rig_tail*` module and `logger_config`, then
  re-imports the lot under the `rt_*` aliases and leaves a commented
  workflow in the Script Editor. It is the only path that picks up an edit
  to `rig_tail_constants` without a Maya restart, at the cost of resetting
  session state — the config auto-load restores the saved config.

No module reloads its dependencies on import. `rig_tail.py` used to sweep
`importlib.reload` down the chain on every import, which ran each module
twice per launch and made correctness depend on keeping the reload order in
step with the dependency graph. Purging `sys.modules` in the launcher does
the same job once, in the right order, for free. The two exceptions are
`rig_tail.main()` and `main_setup()`, which reload their UI module so a
hand-typed launch from the Script Editor cannot reopen a stale window.

So a button refreshes its own modules and nothing else: editing
`rig_tail_chain_build.py` and clicking `ChainBuild` picks the change up,
editing `rig_tail_maya.py` and clicking anything but `TailReload` does not.
When in doubt, `TailReload` — it is the only button that guarantees every
module on disk is the one running, and the only one that picks up an edit to
`rig_tail_constants`.

## Module Overview

### Chain Builder (removable sub-tool)

Runs before the Setup phase. One-way containment: these modules import from
the core, no core module imports them — `rig_tail_chain_test.test_containment`
greps for it on every test pass.

| Module | Import Alias | Description |
|--------|--------------|-------------|
| `rig_tail_chain_spacing` | `rt_chain_spacing` | Pure spacing math, zero Maya imports |
| `rig_tail_chain_build` | `rt_chain` | Maya layer: detect, guard, create, write |
| `rig_tail_chain_build_ui` | `rt_chain_ui` | Joint Chain Builder window |
| `rig_tail_chain_test` | `rt_chain_test` | Tests (the math half runs outside Maya) |

### Tail Setup

| Module | Import Alias | Description |
|--------|--------------|-------------|
| `rig_tail_setup` | `rt_setup` | Skeleton orient / mirror / roll, run before the build |
| `rig_tail_setup_ui` | `rt_setup_ui` | Setup UI (Tail Setup) |
| `rig_tail_setup_test` | `rt_setup_test` | Tests for the Setup phase (math + scene) |

### Tail Build

| Module | Import Alias | Description |
|--------|--------------|-------------|
| `rig_tail` | `rt` | Main entry point, build orchestration, phase launchers |
| `rig_tail_build_ui` | `rt_build_ui` | Build UI (Tail Builder) |
| `rig_tail_cleanup` | `rt_cleanup` | Teardown of a previous rig, plus build-structure setup |
| `rig_tail_control` | `rt_control` | Control creation |
| `rig_tail_curve` | `rt_curve` | Curve and spline creation |
| `rig_tail_fk` | `rt_fk` | FK system with SDK groups |
| `rig_tail_stretch` | `rt_stretch` | Stretch/squash system |
| `rig_tail_connect` | `rt_connect` | IK/FK connections and blending |
| `rig_tail_anim` | `rt_anim` | Wave and dynamic FX |
| `rig_tail_ctrlall` | `rt_ctrlall` | Main Controller dashboard (multi-tail) |
| `rig_tail_build_test` | `rt_build_test` | Diagnostics for a built rig |

### Shared

| Module | Import Alias | Description |
|--------|--------------|-------------|
| `rig_tail_constants` | `rt_constants` | Global constants, naming templates, caches |
| `rig_tail_naming` | `rt_naming` | Template strings, naming conventions |
| `rig_tail_maya` | `rt_maya` | Maya scene operations, node creation, geometry binding |
| `rig_tail_math` | `rt_math` | Vector math, orientation helpers |
| `rig_tail_matrix` | `rt_matrix` | Matrix offset network builder |
| `rig_tail_mirror` | `rt_mirror` | L/R dial mirror signs (`MIRROR_BEHAVIOR` on all three axes) |
| `rig_tail_cache` | `rt_cache` | Control caching and validation |
| `rig_tail_joint` | `rt_joint` | Joint chain utilities |
| `rig_tail_restpose` | `rt_rest` | The rest anchor: canonical rest pose the IK curve is built from |
| `logger_config` | - | Shared logging setup |

Every module imports its dependencies as `import rig_tail_x as rt_x`, using
the same alias `install.py` binds in the `TailReload` button. One name per
module, everywhere, so a symbol read in the Script Editor after a reload
means what it means in the source.

> Note: `rig_tail_setup` was previously the teardown/setup module; that
> module is now `rig_tail_cleanup`, and `rig_tail_setup` is the Setup phase
> (formerly `rig_tail_orient`).

---

## Architecture and key decisions

### Chain Builder

**Architecture.** Three layers, split so the hard part is testable without
Maya. `rig_tail_chain_spacing` is pure maths — no Maya import at all — and
holds the curve reconstruction and the distribution profiles.
`rig_tail_chain_build` is the only layer that touches the scene: it resolves
a selection into chain specs, guards them, resamples through the spacing
layer and writes the result. `rig_tail_chain_build_ui` is a stateless
window: every option is a widget read at click time, with no config file and
no preferences.

- **Shape and distribution are separate.** Shape is a centripetal
  Catmull–Rom curve through the chain's own positions; distribution is where
  along that arclength each joint sits. Because the profiles are analytic
  functions of `j/(n-1)`, they are count-independent and idempotent.
- **Interpolating, not approximating.** Catmull–Rom and PCHIP both reproduce
  their knots, so re-spacing at an unchanged count returns the chain
  untouched, and re-applying a profile to its own result is a no-op.
  Shrinking is the only lossy operation, and only because curvature between
  retained joints is unrecoverable by any algorithm.
- **Resample from the original, not the last result.** `_ORIGINALS` maps a
  chain root's long DAG path to the positions first seen this session. "18,
  then 14, then 22" therefore costs one lossy pass rather than three. Module
  global, dies on reload, nothing written to the scene; it re-baselines
  itself when the chain stops matching what was last written (a hand edit).
- **Reuse joints in place.** Names, rotate orders and custom attributes
  survive wherever the count allows, rather than being recreated from a
  template.
- **Refuse rather than half-apply.** A skinned, rig-driven, branching or
  degenerate chain aborts before anything moves. Each listed chain is
  guarded on its own, so one bad chain in four still leaves three rebuilt.
- **Positions only.** Orientation belongs to Setup; **Orient joints** is an
  opt-in convenience that calls `rig_tail_setup.aim_frames` read-only, so
  the two tools agree instead of fighting. The one other channel a rebuild
  writes is the display `radius`, and only because leaving it alone is what
  made a grown chain look wrong: new joints arrived at Maya's default 1.0
  beside the artist's own, and a chain that got denser kept a radius sized
  for the spacing it used to have.
- **Both parametric profiles taper the same way.** Power and Ratio start
  with long segments at the base and shorten toward the tip, so a bigger
  number is a stronger taper in either mode and Invert is the single control
  that swaps direction. Power evaluates `t**(1/k)` rather than `t**k` to get
  there; the earlier arrangement had the two defaults tapering opposite ways
  and made Invert mean different things depending on the mode.
- **Removable by construction.** One-way imports, no scene state, no config
  file. Deleting `rig_tail_chain_*.py` and the marked blocks in
  `install.py` / `uninstall.py` returns the repo to its previous state.

### Tail Setup

**Architecture.** `rig_tail_setup` computes per-joint world frames and
writes them into `jointOrient`, leaving `rotate` zeroed, so a corrected
skeleton still reads as a clean rest pose. Three independent batch toggles
(orient, mirror-orient, mirror-joints) plus a per-chain `roll_chain` fix-up;
`rig_tail_setup_ui` exposes them and nothing else. It reads and restores the
same caches the build uses, so running it never invalidates a later build.

- **Orient before mirror, always.** A single run with both ticked is
  correct by construction; it was the *second* run that used to undo the
  first.
- **Cascade is the default up mode.** Roll is carried down the chain by
  parallel transport from its own first joint, so twist goes while the roll
  the chain already has — a mirror, a hand fix-up — survives. Best-fit
  re-derives roll from the bend plane and overwrites both, which is right
  only on a first pass over a raw skeleton.
- **Positions are preserved unless asked otherwise.** Only `MIRROR_JOINTS`
  moves a joint.
- **Dry Run reports without touching.** A real run unbinds geometry and
  clears the rest pose before the toggles are consulted, which is exactly
  the kind of side effect that has to be previewable.
- **Include / Exclude is one roster.** An excluded tail is skipped by Setup
  *and* by the build, so a finished tail can be frozen while the rest of the
  roster is iterated on.

### Tail Build

**Architecture.** One pipeline per rig part, run by `rig_tail.py`: cleanup
(tear down or reuse the previous rig, decided by the joint cache), joints
(detect or rebuild the BN/FK/IK chains), curves + clusters + controls, then
connect (switches, matrix network, stretch, FX, geometry binding). Each
stage is a module that only knows how to build its own nodes and find them
again by templated name.

- **Pure matrix drive.** BN joints are driven entirely through
  `offsetParentMatrix` (blendMatrix + multMatrix + composeMatrix): no
  constraints, no Euler decomposition, and joint channels stay zeroed
  (`rig_tail_matrix`).
- **Rebuild-safe by name.** Every node is looked up by its templated name
  and reused; the joint cache decides per part whether a rebuild needs a
  full teardown or just re-wiring (`rig_tail_cache`, `rig_tail_cleanup`).
- **The rest anchor.** The IK curve is built from a stored rest
  pose, so repeated rebuilds reproduce the same rig instead of compounding
  curve smoothing (`rig_tail_restpose`).
- **The geometry is opt-in, twice over.** `BIND_GEOMETRY` decides whether
  the tool touches skinClusters at all; with it off nothing is bound and
  nothing is unbound. `KEEP_WEIGHTS` decides what happens when it would
  unbind: on, rebuilds and Setup re-baseline the existing cluster instead,
  so painted weights survive (`rig_tail_maya`, `rig_tail_setup`).
- **Session state lives in `rig_tail_constants`,** which the per-tool
  buttons deliberately leave loaded; modules install missing defaults onto
  it so new features work in a stale session (see "How the modules get
  loaded").
- **Global truth on the cog.** Per-tail IKFK switches and the optional
  ALL/override dashboard live on the cog control; base controls carry
  proxies (`rig_tail_ctrlall`).

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
Launch the Tail Builder UI.

#### `main_setup()`
Launch the Tail Setup UI.

---

## rig_tail_build_ui.py (rt_build_ui)

Build UI (Tail Builder window). Shows the loaded config and a summary
of the current settings, the build options (FK/IK, Indiv FK, Stretchy,
FX, All Tail Controls on Cog, Bind Geometry, Keep Weights), and buttons
opening pop-up editors for RIGPARTS, naming templates and constants.
Settings live in `rig_tail_constants` and round-trip through JSON configs
(Load/Save Config); the main-window checkboxes are committed on build and
on close, so they survive reopening the window.

Two build buttons: **Build Rig** keeps unchanged tails as they are (the
joint cache decides what changed), **Force Rebuild** tears every included
part down first. The force flag lasts one click and is never saved, so a
config cannot leave every build forcing.

**Bind Geometry** (`BIND_GEOMETRY`) decides whether the build touches
skinClusters at all; off, nothing is bound and nothing is unbound, and the
rig builds around the geometry untouched. **Keep Weights (skinClusters)**
(`KEEP_WEIGHTS`) decides what happens when it would unbind: on keeps
existing skins and painted weights across rebuilds, off rebinds from
scratch. Keep Weights greys out while Bind Geometry is off — it stays
ticked, because clearing it would rewrite the answer for the next build
that turns binding back on.

#### `show_ui()`
Build and show the Builder window, closing any previous instance.

#### `RigTailUI.confirm_unpainted_influences(parts)`
Warn, and offer to cancel, before a preserving bind adds rig joints to an
already-painted skinCluster at weight 0. The joint-count-change case:
nothing errors, the mesh keeps following the joints it was painted to, and
the rig looks built while deforming as though the chain had not changed.
Only asked when both Bind Geometry and Keep Weights are on.

---

## rig_tail_setup.py (rt_setup)

Setup phase: orient, mirror and roll the BN skeleton before the build.
Optional and never runs during the build.

Three independent batch toggles in `rig_tail_constants`:

| Constant | Effect | Positions |
|----------|--------|-----------|
| `ORIENT_JOINTS` | Aim-orient each chain so its up-axis stops twisting from joint to joint. No mirroring: both sides are oriented from their own geometry. `ORIENT_UP_MODE` picks the roll. | kept |
| `MIRROR_ORIENT` | Reflect matching `L_`/`R_` pairs' **orientation** across the symmetry plane, so the two sides face as mirror images. | kept |
| `MIRROR_JOINTS` | Reflect matching `L_`/`R_` pairs' **positions** across the symmetry plane, so the target side's joints sit at the exact mirror of the source side's. Also **creates** a target side that has no chain at all, mirrored from its source. | **moved** |

A missing chain is only ever created by `MIRROR_JOINTS`: it is the one
toggle that derives the target's positions in full. The other two can only
rewrite joints that already exist, so an included rig part with no chain is
reported instead — as a source side that blocks its pair, as a target side
that `MIRROR_JOINTS` would build, or as an unpaired part with nothing to
build from. Every operation, pairing and warning is scoped to the included
parts.

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

`MIRROR_BEHAVIOR` picks how `MIRROR_ORIENT` orients the mirrored side.

**Three of six, always.** A frame has six things it could mirror: a
rotation about each of its three axes, and a translation along each.
Compare each target axis to the reflection of its partner's — a rotation
mirrors when the two point *opposite*, a translation when they point the
*same* way. A reflection flips handedness, so a right-handed frame can
point an **odd** number of its axes opposite: one, or all three, never
two. Rotations mirrored plus translations mirrored is therefore always
exactly three. The behavior never changes how much mirrors, only which
three:

| Value | Axes | Rotations mirror | Translations mirror |
|-------|------|------------------|---------------------|
| `mirror` (default) | `-aim -roll -up` | all three | none |
| `symmetric` | `+aim +roll -up` | up | aim, roll |
| `parallel` | `+aim -roll +up` | roll | aim, up |

Read each axis `+` when it points the **same** way as the mirror image of
its partner's matching axis and `-` when it points the **opposite** way.
`aim` runs down the chain (`ORIENT_AIM_AXIS`), `up` is `ORIENT_UP_AXIS`,
`roll` is the remaining one. A rotation about an axis mirrors when it is
`-`, a translation along it when it is `+`, and the count of `-` is always
odd — so three of the six always mirror.

`mirror` is Maya's `mirrorJoint -mirrorBehavior`, and it is the default
because **everything this rig is posed by is a rotation**: curl, wave,
noise, twist and roll, every FK control gizmo, and the spline `mid_rot`
control. It spends its three there. The one dial it costs is `offset`, a
slide along the aim, and a sign covers that.

Its price is the reversed aim, which two places are told about rather than
left to discover: the spline IK's advanced twist takes a negative forward
axis (`rig_tail_stretch.build_advanced_twist`, derived from the Setup UI's
own **Aim Axis** — it used to assume Maya's default), and a translation
along the aim reverses, which `rig_tail_mirror.translation_signs` reports.
Setup's **Orient** step re-derives the aim forward, but `mirror_chains`
runs *after* `orient_chains`, so a Setup re-run re-establishes it.

`symmetric` and `parallel` differ only by a 180 degree roll about the aim,
so **Roll Chain** at 180 on the target side converts one into the other
for a single chain. `mirror` reverses the aim and no roll about it can
reach that. Ignored when `MIRROR_ORIENT` is off (a positions-only mirror
does not touch orientation).

Whatever the behavior, the **dials** land on it exactly:
`rig_tail_mirror` measures how a pair's chains actually relate and negates
what does not already agree, so curl, wave, noise, twist, roll and offset
obey the behavior on all three axes (`MIRROR_SLIDERS`). Under `mirror` the
rotations need no signs at all and only `offset` is negated; under
`symmetric` it is the other way about.

A **stored** config keeps its own value — moving the default from
`symmetric` to `mirror` does not silently re-orient an existing rig. Re-run
**Mirror Orient** to move one across deliberately.

Because of that, the setting and the skeleton can disagree: pick `Mirror`
in the Setup UI, run Mirror Orient, then build in a session that reloads a
config still holding `symmetric`. Nothing the build does reads the setting
— every sign and the spline's forward axis are measured off the joints —
so the rig comes out right either way, and `rt_mirror.aim_reversed` logs
the disagreement so it can be tidied.

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
`rt_constants.active_rigparts()` is the underlying computation.

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
is re-baselined onto the new pose afterwards (`KEEP_WEIGHTS`, painted weights
kept — see Skin Preservation under rig_tail_maya) or, with that off, unbound and
left for the build to rebind. With `BIND_GEOMETRY` off there is no rebind to
follow, so Setup preserves and re-baselines regardless. Any stored rest pose is
cleared either way, so the build recaptures it.

> Note: these constants were renamed. `MIRROR_ORIENT` previously meant the
> aim-orient (now `ORIENT_JOINTS`) and `MIRROR_JOINTS` previously meant the
> orientation mirror (now `MIRROR_ORIENT`). `PRESERVE_SKIN` was split into
> `BIND_GEOMETRY` and `KEEP_WEIGHTS`, and migrates to the latter. Older
> config files are migrated automatically on load.

### Functions

#### `setup_tails(root=None, dry_run=None)`
Detect BN chains, run the enabled steps, then re-baseline the skinned meshes
(or, with `KEEP_WEIGHTS` off and `BIND_GEOMETRY` on, unbind them up front
instead).

#### `run_setup(dry_run=None)`
Run the enabled batch orient/mirror steps on `rt_constants.JOINTS_BN`.

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
Pair rig parts into (source, target) by `L_`/`R_` prefix. Only pairs the
roster *states* — both sides must be listed.

#### `_implied_mirror_pairs(detected)`
The pairs the roster *implies*: a part on the mirror source side that has a
chain and whose opposite-side name no rig part carries names its own target,
so `L_leg` alone implies `R_leg`. Only ever adds the target side, so the
authored side is never overwritten. `create_missing_chains` builds these and
appends the implied name to `RIGPARTS`; nothing is added on a dry run, or if
the build fails.

Created joints carry the source joint's own index, **including its absence**
— `BN_L_leg_jnt` mirrors to `BN_R_leg_jnt`, not `BN_R_leg_00_jnt` — so the
two sides read as the same name but for the side token. This is what lets
`_mirror_parent` find the new pivot: it looks for the side-swap of the
source's own parent name, so a renumbered target would leave every chain
hanging off that pivot parented back under the source side. Chains are built
in the source side's own order — shallower roots first, siblings as they sit
under their parent — so the mirror reads as a mirror in the outliner.

#### `aim_frames(positions, aim_axis, up_axis, up_ref=None)`
Per-joint world frames aimed down a chain with a twist-free up-axis. With
`up_ref` (the `cascade` seed) that vector is carried down the chain by
parallel transport, keeping the chain's existing roll; without it the roll
comes from the chain's best-fit plane normal. A zero-length `up_ref` is
ignored, so a failed lookup falls back to best-fit rather than failing.

#### `mirror_frames(src_matrices, axis, aim_axis, up_axis)`
Reflect source world orientations across the symmetry plane for the target.

---

## rig_tail_cleanup.py (rt_cleanup)

Teardown of a previous rig and preparation of the scene structure, run at
the start of every build. Formerly `rig_tail_setup`.

### Functions

#### `cleanup_rig(fk, ik)`
Entry point; per rig part choose full vs light teardown from the cache.

#### `cleanup_rigname(rigname, fk, ik)`
Full teardown of one rig part (controls, curves, clusters, FX nodes).

#### `cleanup_connections(rigname, fk, ik)`
Light teardown: break connections only, keep nodes for reuse.

#### `remove_rig()`
Strip the rig back to bare skeleton + geometry — the reverse of a build,
for handing a scene on or starting over. Scoped to the **Included** rig
parts, the same roster the builder works on: an Excluded part is left built
and untouched, and when anything is excluded the rig hierarchy stays
standing (their controls and joints live in it). Hands back the BN joints as
plain joints **at rest** with the geometry still bound to them (see
`capture_bn_poses`); removes controls, curves, clusters, ikHandles, FX and
utility networks, the FK/IK duplicate chains and the whole rig hierarchy.
**Destructive and not an undo:** animation on the controls goes with the
controls. Aborts rather than deleting the root group when a mesh or joint
could not be moved out of it first. Verified by
`rt_build_test.test_remove_rig()`.

#### `capture_bn_poses(parts, rest=True)`
World matrix per BN joint, for restoring the skeleton later.

**Rest, not live, wherever a rest pose is stored.** The rest anchor
(`rig_tail_restpose`) records each joint's rest world matrix once and never
re-captures, so it is the one description of the skeleton a posed rig
cannot corrupt. Reading live instead means Remove Rig clicked on a posed rig
hands back a skeleton frozen in that pose — correct-looking and wrong — and
a rebuild re-anchors the whole setup to it. Falls back to the live matrix
per joint when nothing is stored (a chain never built, or one Joint Chain
Builder just re-spaced and cleared), which is the old behaviour.

Every matrix is made rigid on the way out (`_rigid`). A BN joint's world
matrix is not: volume preservation drives `.sy`/`.sz`, so a chain at rest
reads a scale near 1.0004, and composing the OPM chain leaves shear in the
low digits. A joint has no shear attribute, so writing that back bakes
scale into the skeleton.

#### `restore_bn_skeleton(parts, poses=None, bn_paths=None)`
Turn the BN chains back into plain joints at their captured pose: pose in
`jointOrient`, `offsetParentMatrix` at identity, no incoming drivers, the
outgoing skinCluster bind untouched. Callers that tear the rig down must
capture *before* the teardown and pass `poses` in — by then the live
matrices are gone. Shared by `remove_rig` and, through
`rig_tail.restore_bn_for_build`, by every build.

#### `cleanup_dangling_curveinfo()`
Delete curveInfo nodes with no input curve. `cmds.ikHandle` creates one on
the temporary curve it makes for every spline build, and that curve is
thrown away immediately — leaving a node that can only print
`curveInfoNN (Curve Info): No valid NURBS curve`, twice per evaluation,
forever. A curveInfo with no input curve is dead by construction, which is
what makes the scene-wide sweep safe (same rule as
`cleanup_dangling_unit_conversions`).

#### `unique_path(node)`
One full DAG path for a name, or None when the name is missing or matches
more than one node. The teardown addresses everything by full path: in a
scene holding two nodes with the same name (a duplicated `rivets` group)
every command given the short name fails with *More than one object matches
name*.

#### `rig_leftovers(parts=None)`
Rig nodes still in the scene after a removal — root group,
`FK_`/`IK_`/`FX_`-prefixed nodes, and orphaned utility/anim nodes carrying
a part's name. Read-only; the check behind `test_remove_rig`.

#### `setup_rig(fk, ik)`
Create the rig root, cog, and hierarchy groups.

#### `set_root(root)`
Set `ROOT` and reconcile the scene root group.

#### `find_existing_root_grp()`
Locate the current rig root group in the scene.

#### `set_joints_auto()`
Detect and (re)build the BN/FK/IK chains for all RIGPARTS.

#### `fk_ik_match_bn(rigname, tol=None)`
Are the cached FK and IK chains still one-to-one with BN, and on it? One
test covers count, membership and position without keeping any history:
the duplicates are made from BN and sit on it at rest, so ask whether they
still do. Safe to apply to the IK chain only because the solver curve now
rests on the joints exactly (`connect_driver_to_solver_curve`).

#### `set_joints(rigname, start_jnt=None, end_jnt=None)`
Detect/build the chains for one rig part.

#### `detect_joints_bn()`
Fill `JOINTS_BN` by chain detection only (used by the Setup phase).

#### `rigpart_has_joints(rigname)`
Does the scene hold BN joints for a rig part.

#### `rename_rigpart(old, new)`
Rename a rig part in place across scene nodes and caches.

---

## rig_tail_control.py (rt_control)

Control-curve creation: the root/cog/base hierarchy, the sliding
variable-FK set, and the IK sets (ik, float, spline, up-vector), plus
colours, shapes and channel-box attributes. Controls are found and reused
by name on rebuild; colours are written on the shape nodes so a rebuild
updates them, and `PRESERVE_CTRL_SHAPES` keeps hand-edited shapes. The
SplineIK set is fixed at 5 controls on fixed tail fractions regardless of
`NUM_CTRL_IK`; `spline_control_index` maps the clusters onto them.

Key functions: `create_root_cog`, `create_basectrl`, `create_controls_fk`,
`create_controls_ik` (+ `create_spline_controls_ik`/`_float`/`_spline`,
`create_spline_up_vectors`), `get_controls_ik`, `set_control_color`,
`add_fk_attributes_to_controls`.

---

## rig_tail_curve.py (rt_curve)

Curves, spline IK handles and clusters. The FK curve follows the joints
exactly. The IK side is a pair: a **driver** curve carrying one CV per
cluster plus two up-vector CVs, and a **solver** curve with one CV per
joint, which is what the ikHandle reads. Handles and clusters are found
and renamed rather than duplicated on rebuild.

The driver curve is deliberately low-resolution — one CV per control is
what gives each control a single CV to move — so it cannot describe the
chain's real shape. It therefore drives the solver curve as an **offset
from rest**, not as an absolute position:

```
solver_cv[i] = driver_sample(t_i) + (rest_cv[i] - driver_rest(t_i))
```

The bracketed term is a build-time constant: how far the low-CV driver
curve falls short at that CV. At rest the two cancel and the solver curve
puts the joints where they belong, so there is no flattening and its
length matches the chain's.

`rest_cv` is **not** the joint positions — a degree-3 curve does not pass
through its own CVs, so aiming there leaves the spline settling the joints
off it (1.7° at the base of the squid C_fintail, and 0.014 units short
overall, enough to drop the last joint off the end). `solver_curve_cvs`
solves for the CVs that put the joints where they belong, by fixed-point
iteration on the condition that actually matters — not "the curve passes
through the joints" but "the joints, placed by their own bone lengths,
land on the joints", since the ikSpline places by arclength:

```
P = joints
repeat:  P += joints - place_by_arclength(curve(P))
```

Four passes takes the base error to 0.06° and the worst joint to 0.005
units, costs ~40 ms per rig part, and needs **no extra nodes** — it only
changes what the constant above aims at. The constant is held in the base control's local space and
multiplied back out through `basectrl.worldMatrix`, so it turns, scales
and travels with the rig.

Because sampling a B-spline at a parameter is a weighted sum of its CVs
whose weights total 1, this reduces to `rest[i] + SUM_j w_ij * (control
j's translation)` — which is exactly linear blend skinning for
translating influences. Driving absolute positions instead is what used
to flatten a tail's base and shorten the curve below the chain length.

Clusters are **IK only**, up-vectors included: nothing deforms the FK
curve (the varFK controls only read positions off it), and FK twist/roll
comes from its own SDK-layer network. `create_clusters_on_curve` carried
an unreachable FK branch for a long time before it was removed.

Key functions: `driver_curve_positions`, `solver_curve_cvs`,
`create_curve`, `connect_driver_to_solver_curve`, `create_spline_handle`,
`get_spline_handle`, `create_clusters_on_curve`.

---

## rig_tail_fk.py (rt_fk)

Variable FK: N sliding controls whose rotation is distributed to the
joints by position and falloff. Each joint carries a stack of SDK groups
(one per control) receiving the weighted rotation; the control's Position
attribute moves it along the FK curve and Falloff widens or narrows the
joints it affects. Based on Jeff Brodsky's elephant-trunk rig.

Each joint in a control's range takes a share of its rotation:

```
weight = max(0, 1 - |joint_pos - ctrl_pos| / falloff)
joint_rotation = rotation * weight / num_joints
```

A symmetric linear tent — full strength under the control, fading to zero
at the edge of the falloff. Dividing by `num_joints` means widening the
falloff spreads the same total bend further rather than adding more of it.
A control also carries the rotation of every control before it, so the
chain reads as FK: bending control 1 carries 2 and 3 with it.

Four nodes per joint per control draw that curve: a `plusMinusAverage` for
`joint_pos - ctrl_pos`, a `multiplyDivide` to scale it by falloff, a
`remapValue` holding the tent (its `outputMax` carries the `1/num_joints`
normalisation), and a `multiplyDivide` applying the result to the
accumulated rotation. No condition nodes — the ramp clamps out-of-range
input to zero on its own.

**Two metrics meet here and must not be confused.**

- `position` (0–10, base to tip) is animator-facing: a fraction of **tail
  length**, so 5 is genuinely halfway down the tail.
- `joint_pos` (0–1, base to tip) is what the network compares against: a
  normalised **Greville abscissa**, i.e. a fraction of the curve's
  *parameter* range, which is what a `pointOnCurveInfo` needs in order to
  land on a given joint.

A `remapValue` per control converts the first into the second, and both
`set_curveinfo_fk` (which draws the control) and `falloff_rotation`
(which rotates the joints) read that same output via
`control_position_plug`. That is what keeps a control drawn on the joints
it actually moves. Feeding a length fraction straight to
`turnOnPercentage` is what used to draw the first control at 60% of the
tail while its rotation landed at 44%.

`falloff` deliberately stays in `joint_pos` units — a width cannot go
through a point-wise remap, and joint units are what make `num_joints` (a
joint count) consistent.

`connect_twist_roll` builds the FK-side equivalent of the three basectrl
dials that drive the IK spline handle, so twist, roll and offset work in
either mode; the BN chain blends between the FK and IK drivers, so there
is no switching network. FK offset is an approximation — the IK handle
re-samples joints along the curve, which has no exact analog in a chain
shaped by rotations — and `offset_unit_scale` converts between the two so
the same dial value reads the same in both modes.

**Why stock nodes.** The whole system is built from nodes that ship with
Maya and wired with `maya.cmds`. That keeps the module install-by-copy: no
compiled plugin, no per-Maya-version or per-platform builds, and a rig
opens anywhere Maya does, render nodes included. Every intermediate value
stays an inspectable plug, so the network can be debugged in the Node
Editor and repaired in a scene without a rebuild. The cost is node count,
which scales with controls × joints — hence the deliberately lean
four-node weighting. For the compiled alternative, Serguei Kalentchouk's
[Variable FK Revisited](https://medium.com/@k_serguei/variable-fk-revisited-9e8435c0c337)
does the same job as a single C++ node, at the price of a build matrix and
scenes that will not open without the plugin.

Key functions: `control_position_plug`, `set_curveinfo_fk`,
`falloff_rotation`, `create_sdk_groups`, `get_sdk_groups`,
`put_jnt_under_sdk_groups`, `connect_twist_roll`, `offset_unit_scale`.

---

## rig_tail_stretch.py (rt_stretch)

Squash and stretch. One stretch ratio (current curve length over cached
rest length) drives Length (joints spread along the tail) and Thickness
(BN scaleY/Z via ratio^-0.5, blended by `preserveVolume`, trimmed by
`squash`, divided by global scale). Split across the build:
`build_stretch` creates the nodes early, `connect_stretch_to_joints`
wires the basectrl sliders once they exist; nodes are reused by name on
re-runs. The parent's squash would shear OPM children — `rig_tail_matrix`
cancels it with a `squashInv` term.

Key functions: `build_stretch`, `connect_stretch_to_joints`,
`add_stretch_attributes_to_basectrl`, `add_jntscale_attributes_to_basectrl`,
`set_curveinfo_stretch`, `build_advanced_twist`.

---

## rig_tail_connect.py (rt_connect)

Final wiring phase: parents the systems into the hierarchy, creates the
switch and channel-box attributes (per-tail IKFK switch on the cog;
stretch/twist/FX attributes on the basectrl, proxied onto every control),
wires the IKFK mode SDKs that fade constraint weights and visibility, and
hands off to `rig_tail_matrix`, `rig_tail_anim` and `rig_tail_stretch`
before binding geometry to the BN joints. With the dashboard active,
consumers read `rt_ctrlall.resolved_plug()` / `rt_ctrlall.ikfk_driver()` instead of
the plain plugs. Caches `get_controls_ik` results per build.

Key functions: `connect_rig_tail`, `connect_root`, `connect_cog`,
`connect_basectrl`, `connect_fk`, `connect_ik`, `connect_spline_ik`,
`connect_stretch`, `add_attributes_ikfk_switch`,
`setup_switch_fk`/`_ik`/`_upvec`, `enforce_attr_order`.

---

## rig_tail_anim.py (rt_anim)

Animation FX layered on the rig: Curl (static, falloff), Wave (traveling
sine), Noise (jitter) and Loop (modulo time for seamless cycling). Each
FX writes per-joint rotations into its own composeMatrix, multiplied into
the BN offsetParentMatrix by `rig_tail_matrix` — joints rotate about
their own pivots and their channels stay untouched. Attribute sources go
through `rt_ctrlall.resolved_plug` so the dashboard can route them.

**Curl** shares one falloff profile between the three axes: each joint's
share of the bend is `u ** curl_falloff`, normalised by the live sum of
those shares, so a curl value names the **total** wrap of the whole chain
(`CURL_DEGREES_PER_UNIT`, 108 — three full turns at the top of the slider)
whatever the joint count, and the falloff only decides how that wrap is
spread. A per-joint clamp (`CURL_MAX_JOINT_DEGREES`, 90) keeps the tip
inside the coil: the profile peaks at the tip, and an unclamped last joint
folds out of the spiral. A chain with enough joints spreads the wrap
thinly enough never to reach the guard and coils twice; a sparse one
saturates its last joints and stops tightening, which is the honest limit
— a 12-bone chain cannot draw two clean turns. Lowering `curl_falloff`
buys most of it back by spreading the same total over the whole chain.

**L/R symmetry** comes from `rt_mirror.rotation_signs` — see
`rig_tail_mirror` below.

Key functions: `build_anim_effects`, `add_anim_attributes_to_basectrl`,
`build_loop`, `build_wave`, `build_curl`, `build_noise`,
`delete_expression`.

---

## rig_tail_ctrlall.py (rt_ctrlall)

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

The rest anchor: the canonical rest pose the IK curve is built from. Captures
each BN joint's rest world matrix once and builds the IK curve from it on
every rebuild, so rebuilds reproduce the same shape instead of compounding.

The anchor only ever made the smoothing *reproducible*, not smaller. Most
of it is gone now — `rig_tail_curve.connect_driver_to_solver_curve`
drives the solver curve as an offset from rest — but **this module is
still necessary**, for two reasons.

The degradation loop is slowed, not closed. `solver_curve_cvs` reduced
the per-rebuild loss by roughly 25× (base aim drifting 0.05°, 0.10°,
0.16° over rebuilds instead of 1.7°, 2.9°, 3.8°), but it still
accumulates toward a straight line without an anchor. With one, rebuild 1
repeats forever.

And the rest correction is now *measured against* these stored positions,
so they define what "rest" means rather than merely seeding it.

> **Migration hazard.** A scene whose `restMatrix` was captured before the
> curve fix stored an already-degraded pose, and the rig will now
> reproduce that degraded shape faithfully as rest. On such a scene, call
> `clear_rest_pose()` and rebuild once from a clean setup skeleton.

**The anchor now also defines what the skeleton is restored to.** It is no
longer only the IK curve's input: `rig_tail_cleanup.capture_bn_poses` reads
`restMatrix` first and falls back to live, so Remove Rig and every rebuild
hand the BN chains back at rest rather than at whatever pose the controls
were holding. That makes clearing a stored rest pose a heavier act than it
was — the fallback is the *current* pose, so clearing while a rig is posed
and then rebuilding anchors the setup to that pose. Both callers that clear
are scoped accordingly: Setup clears only the included parts it actually
moved, and Joint Chain Builder clears only the chain it is writing.

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

## rig_tail_setup_ui.py (rt_setup_ui)

Setup UI (Tail Setup window). Exposes the three batch toggles
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

## Joint Chain Builder

A removable sub-tool that creates and re-spaces BN joint chains *before*
Setup. Launched from its own shelf button
(`rig_tail_chain_build_ui.show_ui()`), never from `rig_tail.py`. The
reasoning behind the split is under
[Architecture and key decisions](#chain-builder).

### rig_tail_chain_spacing.py (rt_chain_spacing)

Pure maths, no Maya import, so every claim about the spacing is testable in
plain Python. Shape is a centripetal Catmull–Rom curve through the source
positions; distribution is where along its arclength each joint sits.

Tunables: `K_DEFAULT` 1.7 in `K_RANGE` (0.2, 5.0) for Power, `R_DEFAULT`
0.90 in `R_RANGE` (0.5, 1.0) for Ratio, `ARC_SAMPLES` 16 sub-samples per
span, `SNAP_TOL` 1e-4 normalised arclength. Ratio stops at 0.5 because a
smaller one collapses the far segments and a zero-length bone breaks
aim-orient downstream.

#### `resample(source_points, n, mode, param=None, invert=False, snap=True)`
The whole pipeline: knots → arclength table → source arclengths → distribute
→ snap → evaluate. Returns `(positions, snapped_indices)`.

#### `distribute(mode, n, param=None, invert=False, source=None)`
`n` normalised positions along [0, 1]. `uniform` is `u = t`; `power` is
`u = t**(1/k)`; `ratio` is a geometric series of ratio `r`; `keep` is a
monotone PCHIP resample of `source`. `invert` mirrors the profile end for
end, for all four modes including `keep`.

Power and Ratio taper the same way — segments start long at the base and
shorten toward the tip, so the joints bunch at the tip — and raising `k`
above 1 or dropping `r` below 1 strengthens that taper. Power is `1/k`
rather than `k` precisely so the two agree: with `t**k` the same dial would
taper the opposite way from Ratio, and Invert would be needed to line them
up. `k < 1` reverses it, which is the same result as Invert.

#### `snap_to_source(u_list, s_hat, tol=SNAP_TOL)`
Source index per target that lands within `tol` of an existing joint, else
None. Ends always snap; indices must strictly increase, so two targets can
never claim one source and collapse a segment.

#### `catmull_rom_knots(points)` / `catmull_rom_eval(points, knots, t)`
Centripetal knot parameters, and Barry–Goldman evaluation at one of them.
`N == 2` falls back to a straight lerp.

#### `arclength_table(points)` / `eval_at_arclength(points, knots, table, total, u)`
Cumulative arclength sampled per span, and its inverse by binary search plus
linear interpolation.

#### `pchip_tangents(x, y)` / `pchip_eval(x, y, m, xq)`
Fritsch–Carlson monotone tangents and Hermite evaluation — the interpolant
behind `keep`, chosen because it cannot overshoot into a non-monotone
distribution.

### rig_tail_chain_build.py (rt_chain)

The only layer that touches the scene. Every mutating entry point runs
inside `rig_tail_maya.build_performance_scope`, so a click is one undo step.

**Guards.** A rebuild aborts before moving anything if a chain joint
influences a `skinCluster` (found through the joint's *future* —
`worldMatrix` feeds `skinCluster.matrix`, so its history holds nothing), has
incoming connections on translate or `offsetParentMatrix` (a built rig is
driving it — remove the rig first), has branch children that shrinking would
orphan, has locked translates, or if the chain is shorter than 2 joints or
wholly coincident.

**Writing.** Joints are reused in place: at an unchanged count only
positions move and names are left alone; shrinking keeps the first `n` and
deletes the surplus; growing moves the existing ones and creates the rest,
copying `rotateOrder`, `preferredAngle` and the display `radius` from the
nearest surviving neighbour. On a count change a chain whose names all parse against the
configured template is renumbered sequentially (logged at INFO); one with
arbitrary names keeps them positionally. The `_ee_` end joint is excluded
from the chain by `get_joint_chain`, so it is looked up from the tip and
re-parented onto the new one — Setup's end-joint handling depends on it.

**Where the tail ends.** A chain with an `_ee_` ends *at the `_ee_`*, not at
its last BN joint, so the `_ee_` is the final point of the resample rather
than a fixed-length stub dragged along behind the tip. It stays exactly where
the artist put it at every joint count, and the BN joints are spread over the
whole length up to it: the last BN joint stops one segment short, so that gap
narrows as the count rises (30 joints leave a visibly bigger gap than 50 do).
A chain with no `_ee_` — or one sitting on top of its tip, which would hand
the resample a zero-length final span — keeps the older rule instead: the
last BN joint is the end and is pinned there, and any coincident `_ee_` is
placed at its original distance along the new final segment. The session
original cache records which of the two applied, so an `_ee_` added or
deleted between clicks re-baselines rather than resampling a stale length.

**Display radius.** Every joint of a rebuilt chain, `_ee_` included, ends
up at one radius: the chain's own, capped at `RADIUS_SEGMENT_FRACTION` (0.5)
of the new mean segment so adjacent spheres at most touch. Without the
single pass a grown chain mixes the artist's radius with Maya's default 1.0
on the joints just created; without the cap a chain taken from 21 joints to
80 keeps a radius set for the old spacing and draws as one blob. The
uncapped value comes from the session original cache, not from the chain as
it stands, so lowering the count again restores the radius instead of
ratcheting it permanently small. A locked or driven `radius` is skipped, not
an abort — it is a display attribute. A brand-new chain has no artist radius
to keep, so it is sized from its spacing outright.

#### `rebuild(root_joint, n, mode='keep', param=None, invert=False, snap=True, orient=False, start_joint=None)`
Re-space an existing chain at a new joint count, resampling from the session
original rather than the previous result. Returns the BN joints.

`start_joint` rebuilds only the span from that joint down to the tip and
leaves everything above it untouched — its names, its positions and its
numbering. The start joint is an endpoint of the resample, so it does not
move and the joint above it goes on aiming exactly where it was; new joints
in the span continue the numbering above rather than restarting at 0.
Guards, the min-length check and the session original cache all key on the
span, so a from-the-base rebuild and a from-partway one keep separate
baselines. `None` (the default) rebuilds the whole chain, base to tip.

#### `build_new(start, end, n, rigname=None, mode='uniform', param=None, invert=False, orient=False)`
Create a new BN chain between two transforms. The two objects mark the ends
and are left untouched; the new root is parented under `start`'s parent.

#### `rebuild_selected(n, mode='keep', param=None, invert=False, snap=True, orient=False, from_selected=False)`
`resolve_selection`, then build or rebuild each resolved chain.
`from_selected` rebuilds each chain from the joint that was picked rather
than from its base joint.

#### `resolve_selection()`
Interpret the viewport selection as `ChainSpec`s. Any joint in the selection
means rebuild: each is walked up to its chain root — stopping at a non-joint
parent, a branch point, *or* a parent belonging to a different rig part, so
clicking one tentacle joint cannot climb into the spine — and the roots are
de-duplicated, giving one spec per chain however many of its joints are
selected. Each spec also carries the joint that was picked (the highest one,
where several of a chain are selected), which is what 'Build from selected
joint' rebuilds down from. Two plain transforms with no joint among them
mean build a new chain between them.

#### `chain_root(joint)`
The root of the chain a joint belongs to, or None if it is not a joint.

#### `clear_cache(root=None)`
Forget the session original for one chain (any of its joints will do) or for
all of them. Returns the number of entries removed.

### rig_tail_chain_build_ui.py (rt_chain_ui)

Stateless window: every option is a widget read at click time, with no
config file and no preferences. **Select** fills the chain list from the
viewport and sets Joint Count to the first chain's own length, so the first
click re-spaces rather than resizing by surprise; the count goes on
following the detected one until it is typed over. **Build from base joint**
/ **Build from selected joint**, at the foot of Source, choose whether a
rebuild covers the whole chain or only the run from the picked joint down to
the tip — the latter needs Select to have been used, since a typed rig part
name says which chain but never which joint of it, and falls back to the
base joint if it was not. **Build Joints** runs each listed chain on its
own, so one guarded chain in four still leaves three rebuilt. **Bake Joint
Chain** clears the session original for the listed chains only, making their
current shape and joint size the new baseline; it moves nothing. A one-line
**Status** bar under Spacing says which button last ran, over how many
chains, and with which options — the same role `File:` plays in the Build
window.

#### `show_ui()`
Build and show the Joint Chain Builder window, closing any previous
instance.

---

## rig_tail_chain_test.py (rt_chain_test)

Tests for the Chain Builder, split by whether they need a scene.

**Math tests** are deterministic and need no Maya at all — the spacing layer
imports nothing from it, so `run_math()` runs under plain Python as well as
in the Script Editor. They pin the properties the tool's promises rest on:
endpoints land on 0 and 1 for every mode, invert is its own inverse, Power
and Ratio taper base to tip while Invert flips all four modes, both
interpolants reproduce their knots, re-applying a profile to its own result
does not drift, a clean count change snaps to every other original exactly,
and `test_containment` greps every core module to enforce the one-way import
rule. **Scene tests** are MUTATING — they create, rebuild and re-space real
chains through the same entry points the UI uses, so reload the scene
afterwards. Each one snapshots the chain's names, positions and joint radii
and puts them back before the next runs, but a shrink deletes joints and they come
back as new nodes, so the reload is what makes it clean. `test_guards` is
the exception: it builds and deletes its own scratch chain and never
touches the loaded skeleton.

### Functions

#### `run_math()`
Every math test, with a PASS/FAIL summary. Safe, and runnable outside Maya.

#### `run_scene(chain='C_tail')`
Every scene test, with a PASS/FAIL summary. **Mutating.** Takes a rig part
name or the name of any joint in the chain.

#### `run_all()`
`run_math()` plus a pointer to the mutating scene tests.

Individual tests: `test_containment`, `test_distribution_endpoints`,
`test_uniform_equivalence`, `test_invert_symmetry`, `test_pchip_monotone`,
`test_pchip_knot_exact`, `test_catmullrom_interpolates`, `test_arclength`,
`test_keep_idempotent`, `test_keep_preserves_distribution`,
`test_resample_noop`, `test_respace_same_count`, `test_param_defaults`,
`test_roundtrip_drift`, `test_snap_exact`, `test_degenerate` (math);
`test_rebuild_count`, `test_names_preserved`, `test_guards`,
`test_cache_no_compounding`, `test_undo` (scene).

What the scene tests pin down:

- `test_rebuild_count(chain)` — grow, shrink and same-count in one pass.
  The chain **as it stands in the scene**, walked from the root rather
  than read off the return value, is n joints in one parent-to-child
  line; both ends of the *tail* stay where they were (the base, and the
  `_ee_` where there is one, otherwise the last BN joint); no two joints
  coincide; the `_ee_` hangs off the new tip; and the last BN joint stops
  one segment short of it, a gap that narrows as the count rises.
- `test_names_preserved(chain)` — at `n == N` (what the UI opens on) the
  joints move and nothing else changes: no renumbering, no new nodes, no
  renamed `_ee_`. Uses Power at k=3 so the claim is not vacuous on a chain
  that is already evenly spaced.
- `test_guards()` — skinned, rig-driven (translate and
  offsetParentMatrix), locked and branching chains are refused, and
  refused without moving anything; a lone joint is refused too, and an
  `_ee_` child is *not* mistaken for a branch. Every hazard is armed and
  disarmed with a clean rebuild either side, on a scratch chain the test
  builds and deletes. The refusal must be `RigTailBuildError`
  specifically — "it raised something" would have passed the bug that
  made `_find_influence_skin` throw `TypeError` on every rebuild.
- `test_cache_no_compounding(chain)` — ten count changes and back to N
  land where a single N→n→N pass does, to float noise, because every
  rebuild resamples the chain's first-seen shape. Also checks the escape
  hatch: an edit past `JOINT_POS_TOLERANCE` re-baselines the cache.
- `test_undo(chain)` — one undo restores count, names, positions and the
  `_ee_`, for a grow and for a shrink. The session cache is a module
  global and is deliberately *not* undone.

---

## rig_tail_setup_test.py (rt_setup_test)

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
`test_aim_frames`, `test_up_mode`, `test_mirror_frames`,
`test_find_mirror_pairs` (math); `test_orient`, `test_end_joint`,
`test_mirror_orient`, `test_mirror_joints`, `test_roll`,
`test_skin_rebaseline`, `test_rigname_from_selection` (scene).

---

## rig_tail_build_test.py (rt_build_test)

Diagnostics for a built rig (the matrix/OPM architecture). Read-only
`test_*`/`check_*` functions validate wiring and alignment; `fix_*`
helpers mutate and are opt-in. Ships inside the module so exactly one
copy is on `sys.path`.

#### `run_all(rigname='tail')`
Every read-only check with a PASS/FAIL/RAN/ERROR summary; True when
nothing failed.

#### `report_bend(rigparts)`
Per-chain bend angles (total degrees a chain turns through), read-only —
the before/after measure for rebuild degradation.

#### `probe(stage, rigname)`
Quick joint probe: where a chain's shape is currently held (jointOrient,
rotate or OPM) at a build stage.

#### `measure_rebuild_degradation(rigparts, rebuilds=2)`
Curvature loss across repeated rebuilds. **Mutating** (rebuilds the rig).

#### `test_build_exclusion(rigname)`
An Excluded part survives a rebuild untouched. **Mutating.**

#### `test_remove_rig(tolerance=0.001)`
Remove Rig leaves a clean scene: root group gone, every BN joint still in
the pose the rig held it in, geometry still skinned with the same
influences, and nothing rig-shaped left behind (`rig_leftovers`).
**Mutating**, and not undoable — run it on a scene you can reload.

#### `profile_build(root=None, fk=None, ik=None)` / `profile_cmds()`
Which Maya command the build time goes to: calls, total and mean per
command name, and what share of wall time is spent inside commands at all.
`profile_build` runs a rebuild under `profile_cmds` (**mutating**);
`profile_cmds` is a context manager for profiling any block.

Individual checks (all taking `rigname`): `test_matrix`,
`test_local_trs`, `test_fx_order`, `test_alignment`, `test_matrix_opm`,
`test_joint_orient`, `check_expression_flags`, `test_ikfk_drive`,
`test_wave`, `test_curl`, `test_time_evaluation`, `show_data_flow`.

---

## rig_tail_naming.py (rt_naming)

Template string formatting and naming conventions.

### Functions

#### `fstr(rigname, template, TYPE='', NN='', nn='', TAG='')`
Format template string with rig name and placeholders.

**Example:**
```python
rt_naming.fstr('tail', rt_constants.JOINT, 'IK', 3)  # Returns: 'IK_tail_03_jnt'
```

#### `get_rigname(node, template)`
Extract rig name from node name using template pattern. The index token is
optional, so an unnumbered one-joint chain reads normally —
`BN_L_leg_jnt` -> `L_leg`. A present index still wins: `BN_L_tail3_00_jnt`
is `L_tail3` index `00`, never `L_tail3_00`.

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

## rig_tail_maya.py (rt_maya)

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

#### `remove_nodes(nodes)`
`remove` for a list of nodes in a handful of commands: one disconnect pass
for the whole list (so a delete still cannot cascade through a connection
web), then a single `cmds.delete`. Teardown deletes utility nodes by the
thousand and per-node `remove` spent ~8 commands on each.

#### `curveinfo_consumers(nodes)`
The curveInfo nodes fed by a list of nodes, **including through their
shapes** — a curve feeds a curveInfo from `curveShape.worldSpace[0]`, so a
transform-only query misses it and deleting the curve leaves a curveInfo
with no input, which then prints `No valid NURBS curve` on every evaluation
for the rest of the session. `remove`/`remove_nodes` take these down with
the curve.

#### `existing(nodes)`
The nodes in a list that exist, in one `cmds.ls` instead of an `objExists`
per node.

#### `set_channel_flags(node, attrs, k=None, cb=None, l=None, compound=False)`
Keyable / channel-box / lock flags written straight to the plugs via the
API, with a `cmds.setAttr` fallback. A compound name flags its per-axis
children, as the loops it replaces did. **Not undoable** — which is why
attribute VALUES still go through `cmds.setAttr`, and why only display
flags use this.

#### `disconnect_nodes(nodes, source=True, destination=True)`
`disconnect_all` for a list of nodes in two commands — `listConnections`
answers for a whole joint chain at once. Used by the teardown, where the
per-node version cost several commands per joint per chain per rig part.

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
Search for geometry matching rigname and bind to BN joints. A no-op with
`BIND_GEOMETRY` off.

#### `unbind_geometry(rigname, force=False)`
Unbind geometry from rig. Already-skinned meshes are left bound and returned
instead whenever an unbind would not be made good again — with `KEEP_WEIGHTS`
on (the paint is worth keeping) or with `BIND_GEOMETRY` off (nothing would
rebind them). `force=True` unbinds regardless.

#### `unbind_geometry_all()`
Unbind all geometry in scene.

#### `geometry_transforms(root=None)`
Mesh transforms under the geometry group, from one typed subtree query.
Shared by bind, unbind and the missing-geometry report, which each used to
walk every descendant transform and ask about it node by node — once per rig
part, in two phases.

### Skin Preservation

`KEEP_WEIGHTS` (default on) stops the rig throwing away painted weights. A
closest-distance rebind is only ever right the first time: afterwards it wipes
the paint work, and on a mesh the rig shares with the rest of the character it
drops the other influences entirely. `BIND_GEOMETRY` (default on) is the
question asked before it — whether the tool touches the skinning at all.

#### `bind_enabled()`
Read the `BIND_GEOMETRY` setting, defaulting to on.

#### `keep_weights()`
Read the `KEEP_WEIGHTS` setting, falling back to the legacy `PRESERVE_SKIN`
before the default (the reload sweep skips constants, so a session that
predates the split still holds the old name and the answer its user gave —
`TailReload` re-imports it).

#### `joints_missing_from_skin(rigname)`
The BN joints an already-skinned mesh does not yet have as influences —
exactly the joints a preserving bind is about to add at weight 0. Reads the
cached `JOINTS_BN`. The Build UI asks this before building, so a chain that
has grown since the mesh was painted is caught before it produces a rig that
looks built and deforms wrong.

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

## rig_tail_joint.py (rt_joint)

Joint chain utilities.

### Functions

#### `get_joint_chain(start_jnt, end_jnt=None)`
Get ordered joint chain from start to end. Follows the first child at each
level, stopping at an `_ee_` joint or where the next joint parses to a
*different* rig part — so a branch point such as `BN_L_leg_jnt`, carrying a
rear wing and a rear eye, reports the leg alone rather than an arbitrary one
of its branches. An unreadable name on either side keeps the walk going, so
hand-named chains behave as before.

#### `get_joint_hierarchy(root)`
Get all joints under root.

#### `get_joint_position_from_list(joints)`
Get world positions for joint list.

#### `set_joint_attributes(joints)`
Stamp each joint's `joint_pos` (0 at the base, 1 at the tip): its
normalised Greville abscissa on the FK curve, **not** a distance. See
`rig_tail_fk.py` for why that is the metric and how the animator-facing
`position` dial converts into it.

#### `is_equal_joint(jnt1, jnt2)`
Compare joint transforms for equality.

---

## rig_tail_math.py (rt_math)

Vector and matrix math utilities.

### Functions

#### `linspace(start, stop, num)`
Generate evenly spaced values.

#### `cumulative_lengths(points)` / `length_fractions(points)`
Running distance along a chain of points, raw and normalised to 0–1. The
metric an animator reads as "how far along the tail" — evenly spaced in
space, unlike joint index, which on a tapered chain (8:1 bone ratio on
the squid fintails) bunches badly toward the tip.

#### `nearest_index(values, target)`
Index of the entry closest to a target value.

#### `transform_vector(vec, matrix)`
Transform a displacement by a matrix, 3×3 part only (no translation).
Row-major, `v * M` — the convention `pointMatrixMult` uses in
`vectorMultiply` mode, so a value baked with this comes back out of that
node unchanged when the matrix is the inverse of the one used here.

#### `clamp_degree(num, degree)` / `clamped_uniform_knots(num, degree)`
The degree and full knot vector of the curve `create_curve` builds.
Maya's own `.knots` data omits the outermost knot at each end; this
returns the mathematical vector, which is what an evaluator needs.

#### `greville_fractions(num, degree=3)`
Normalised Greville abscissae. A CV does not sit at one parameter — it
influences a stretch of curve; its Greville abscissa is the parameter
where its basis function peaks. On a curve with one CV per joint, joint
*j*'s Greville fraction is the parameter fraction that lands on joint
*j*, which is what makes `joint_pos` and the varFK control placement one
metric. Computed analytically, so it is strictly increasing by
construction — `joint_pos` must be monotonic or `falloff_rotation`'s ramp
hands one control's rotation to two separate stretches of tail.

#### `bspline_arclength_table(cvs, degree=3, samples=0)` / `bspline_at_arclength(points, cumulative, target)`
Arclength along a clamped uniform B-spline, and its inverse. The B-spline
counterpart of `rig_tail_chain_spacing.arclength_table`, which is
Catmull–Rom (an interpolating curve) and cannot describe this one. The
inverse interpolates *inside* the sample interval — snapping to the
nearest sample quantises the result enough to stop `solver_curve_cvs`
converging.

#### `bspline_point(cvs, u, degree=3)`
Evaluate a clamped uniform B-spline at parameter *u*, by de Boor. Used to
work out where the low-CV IK driver curve sits **without reading the
scene** — reading it off the live curve would pick up whatever the
animator has the controls doing on a rebuild, and bake a posed shape in
as rest.

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

## rig_tail_matrix.py (rt_matrix)

Matrix offset network construction.

### Functions

#### `build_matrix_offset_network(rigname, fk, ik)`
Build matrix blend network for IK/FK switching with FX offsets.

---

## rig_tail_mirror.py (rt_mirror)

Makes an L/R pair's **dials** agree with `MIRROR_BEHAVIOR` on all three
axes — `mirror` and `symmetric` move the pair as mirror images, `parallel`
moves it the same way round the world. Consumed by `rig_tail_anim` (curl,
wave, noise), `rig_tail_fk` (twist, roll, offset), `rig_tail_connect` (the
IK spline handle's twist/roll/offset) and `rig_tail_stretch`, which asks
`aim_reversed` which way down the chain a mirrored side runs.

The value set and the default live here (`BEHAVIORS`, `BEHAVIOR_DEFAULT`)
so Setup and the build cannot disagree about what is valid.

**Why it has to exist.** A mirrored skeleton delivers exactly one mirrored
axis, and no orientation scheme can do better with the aim pinned down the
chain — see the `MIRROR_BEHAVIOR` section for the parity argument. A sign
on the value going in is not bound by that argument at all: a scalar
negates freely. So the frames stay as they are and the dials are corrected
on the way in.

The signs are **measured**, not derived: each axis of the target chain is
compared against the reflection of its partner's across the symmetry
plane, averaged over the chain, and negated only when what it does now
disagrees with what the behavior asked for. Only the sign of a dot product
is read, so two hand-placed chains still resolve; an axis that is not a
mirror at all (|cos| under `MIRROR_TOLERANCE`) is left alone with a
warning. Only the non-`MIRROR_SOURCE_SIDE` half of a pair is signed, so
exactly one side moves; centre and unpaired parts are untouched.

**Rotations and translations take opposite signs.** A rotation about a
local axis mirrors when that axis points *against* the reflection; a
translation along one mirrors when it points *along* it. So a dial that
slides a joint (FK `offset`, the spline handle's `offset`) reads
`translation_signs`. Under the default `symmetric` that means twist and
roll are negated on the mirrored side while offset is left alone — sliding
both tails toward their own tips already *is* the mirrored motion.

`translation_signs` is **not** the negation of `rotation_signs`, though it
looks like one on a mirrored part: a part with nothing to mirror — the
source side, a centre part, an unpaired part — has to come back unsigned
for both, and negating the dict turned every one of those to −1.

**The frames are read from the stored rest pose** (`restMatrix`), not from
the joints as they stand. The answer must not depend on where in the build
it is asked, and `rig_tail_matrix` zeroes every BN joint and rebuilds the
network under it partway through — measuring live caught the chains
mid-rebuild.

Set `MIRROR_SLIDERS` to False for the old per-side-raw behavior.

### Controls are a second case

A dial is a number, so a sign on it is free. A control is a **gizmo**, so
its axes have to point where its motion goes — which pins the control
frame to the joint frame with each axis scaled by these same signs, and
that frame still has to be right-handed. The three signs must therefore
multiply to +1:

| MIRROR_BEHAVIOR | signs | product | control frame |
|---|---|---|---|
| `mirror` | none negated | +1 | **nothing to do** — the joints already are it |
| `symmetric` | two negated | +1 | buildable — the full behavior mirror |
| `parallel` | one negated | −1 | left-handed, **refused** |

So behavior-mirrored FK controls are free under `mirror`, built under
`symmetric`, and cannot exist under `parallel`. That is not an omission: `parallel` asks all three axes
to move the pair the same way round the world, and a right-handed frame
manages that on at most two. The dials still honour it; a gizmo cannot.

Under `symmetric` the frame that falls out is exactly Maya's
`mirrorJoint -mirrorBehavior` result — every axis the negated reflection
of its partner's, so the mirrored side's local X runs back up the chain
and the same channel values pose the pair as mirror images on all three
axes. Mirror-pose tools become a straight value copy. The variable-FK and
`INDIV_FK` control groups are re-stood by
`rt_control.mirror_control_frames`, and the matching negation goes on the
rotation they send out (`rig_tail_fk.falloff_rotation` and
`rig_tail_connect.connect_fk`), so gizmo and bend still agree.

Set `MIRROR_CONTROLS` to False to leave the controls facing their own
joints. The IK spline controls are **not** covered — see below.

### Functions

#### `rotation_signs(rigname)`
Per-axis sign for a rotation about each local axis, as `{'X','Y','Z'}`.

#### `translation_signs(rigname)`
The same for a translation along each local axis (the negative).

#### `control_signs(rigname)`
Signs for a behavior-mirrored control, or None when there is no such frame
(unpaired, source side, `MIRROR_CONTROLS` off, or `parallel`).

#### `mirrored_matrix(node, signs)`
A node's world frame with each axis scaled by its sign, as 16 floats.

#### `aim_axis()`
`ORIENT_AIM_AXIS` as a signs key, or None when it is not a usable axis.

#### `aim_reversed(rigname)`
Whether this part's joints aim back up their own chain — **measured off the
skeleton**, not read from `MIRROR_BEHAVIOR`. The setting says what the next
Mirror Orient will do; only what the joints actually are is safe to build
against. Logs when the two disagree.

#### `behavior()`
The validated `MIRROR_BEHAVIOR`, defaulting to `BEHAVIOR_DEFAULT`.

### The IK spline controls

They do not inherit the joint convention. `orient_control_aims` aims each
row at itself — `+Y` down the row, `+Z` rolled toward a world up reference
— so `MIRROR_BEHAVIOR` never reaches them. Re-standing them is safe: each
spline cluster owns a single CV with the handle's rotate pivot on it, so a
rotated cluster handle deforms nothing (the one-CV property
`rig_tail_curve.connect_driver_to_solver_curve` documents).

Two things decide their frame, and both are now stated rather than
inherited:

**The up reference** was the basectrl's `+Z`, which reaches it from the
joints through `get_local_orientation` — so the frames were a by-product
of `MIRROR_BEHAVIOR`, not a convention. It is now
`rt_mirror.spline_up_vector()`: `ORIENT_UP_AXIS` read as a world
direction, required to lie **in** the symmetry plane. A world axis is
always invariant under the reflection up to sign, so either way the two
sides come out cleanly related; what the plane's own normal would cost is
*which* axis carries the negation — the up resolves negated too, and with
the aim reversed on top of that all three end up negated, the one frame
with no mirrored translation at all. So it is refused, and the aim flip
stands down with it.

**The aim direction** is reversed on the mirrored side
(`rt_mirror.flip_control_aim`), which moves the negation onto the aim:

| | Frame | Rotations mirror | Translations mirror |
|---|---|---|---|
| before | `+aim -roll +up` | roll | aim, up |
| after | `-aim +roll +up` | aim | **roll, up** |

Both are one-negated, so both mirror two translations — but `roll` and
`up` are the two axes that bend the curve, and `aim` is the slide along
the tail's own length. An unmirrored bend axis is the one an animator
sees; an unmirrored slide is not. It is done by negating the constraint's
aim vector, so the frame is built right rather than corrected afterwards
and a rebuild cannot flip the flip.

`mid_rot` is the exception in the set — a pure rotation control — and
`mirror` already serves it.

---

## rig_tail_constants.py (rt_constants)

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
