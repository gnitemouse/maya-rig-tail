# Tail Joint Builder — Architecture & Implementation Plan

Status: **design agreed, not yet implemented.**
Branch: `worktree-joint-builder`.
This document is the starting point for the implementation session.

---

## 1. Purpose and scope

A standalone sub-tool that **creates and re-spaces BN joint chains** before
the rest of the pipeline touches them.

```
[Chain Builder]  ->  [Tail Rig Setup]  ->  [Tail Rig Builder]
 positions            orient / mirror       rig
```

It does one thing per click and exits. No construction history, no custom
scene metadata, no callbacks, no live preview. It is deliberately
**removable**: deleting three files and one marked block in `install.py`
returns the repo to its current state.

### In scope (v1)

- Build a new chain between two objects, named from the naming template.
- Rebuild an existing chain at a different joint count.
- Re-space an existing chain with a spacing profile.
- Four spacing modes: Uniform, Power, Ratio, Keep.
- Invert the distribution.
- Preserve chain shape through count changes with bounded, non-compounding error.
- Single undo step.

### Out of scope (v1)

- Live preview (deliberately — see §11).
- Bezier / ramp bias curves (v2).
- Twist preservation across a rebuild (v1.5 — see §6.8).
- Orienting the result (Setup already owns this — see §6.7).
- Any change to how the core tool builds or configures rigs.

---

## 2. Containment contract

**The rule: new modules may import existing ones; no existing module may
import a new one.**

Everything below exists to keep that rule true and to make removal a
mechanical operation rather than an archaeology exercise.

### 2.1 The only four core touchpoints

| File | Change | Why unavoidable |
|---|---|---|
| `install.py` | shelf button + 1 line in `LAUNCH_RELOAD_COMMAND` | a shelf button must be registered somewhere |
| `uninstall.py` | new label in `SHELF_LABELS` | so uninstall removes the button |
| `README.md` | one section | discoverability |
| `rig_tail_documentation.md` | one section | discoverability |

Every edit in `install.py` / `uninstall.py` goes inside a marked block:

```python
# --- CHAIN BUILDER (removable sub-tool) -------------------------------
...
# --- END CHAIN BUILDER ------------------------------------------------
```

### 2.2 Hard prohibitions

- **Never add keys to `rig_tail_constants.get_user_editable_config()`**
  ([rig_tail_constants.py:567](rigTail/scripts/rig_tail_constants.py:567)).
  That dict defines the config JSON schema shared by the Builder and Setup
  UIs. Adding chain-builder keys would bake the sub-tool into every config
  a user saves, and removing the sub-tool later would orphan those keys.
  Also: `rig_tail_constants` is deliberately excluded from `il.reload`
  ([rig_tail.py:93](rigTail/scripts/rig_tail.py:93)), so new values there are
  invisible to a running session — a known past source of build crashes.
- **Never add an entry point to `rig_tail.py`.** No `main_chain()`. The
  shelf button imports the UI module directly.
- **Never modify a shared widget in `rig_tail_ui.py`.** If the chain UI
  needs a variant of `RigPartsEditor` or a styling helper, *copy it* into
  the chain UI module. `rig_tail_setup_ui.py` already sets this precedent —
  it defines its own `create_group_box` / `style_button`
  ([rig_tail_setup_ui.py:487](rigTail/scripts/rig_tail_setup_ui.py:487)).
- **Never add tests to `rig_tail_test.py` or `rig_tail_test_setup.py`.**

### 2.3 Permitted reads from the core (one-way)

| Module | Used for |
|---|---|
| `rig_tail_constants` | `JOINT`, `TYPE_BN`, `JNT`, `DFORMAT`, `JOINT_POS_TOLERANCE`, `ORIENT_AIM_AXIS`, `ORIENT_UP_AXIS` — **read only** |
| `rig_tail_naming` | `fstr`, `get_rigname`, `get_index_from_name` |
| `rig_tail_joint` | `get_joint_chain`, `get_joint_position_from_list` |
| `rig_tail_math` | `get_world_pos`, `get_vec_length` |
| `rig_tail_maya` | `build_performance_scope` (single undo chunk + suspended refresh) |
| `rig_tail_setup` | `aim_frames` (optional orient-on-create only) |
| `logger_config` | `logger_setup`, `abort_build` |

### 2.4 Removal checklist

1. `rm rigTail/scripts/rig_tail_chain_*.py rigTail/scripts/rig_tail_test_chain.py`
2. `rm rigTail/scripts/rig_tail_chain_config.json` (if present)
3. Delete the marked blocks in `install.py` and `uninstall.py`.
4. Delete the doc sections.
5. Verify: `grep -rn "rig_tail_chain" rigTail/ install.py uninstall.py` returns nothing.

### 2.5 Automated enforcement

`rig_tail_test_chain.test_containment()` greps every core module for the
string `rig_tail_chain` and fails if any is found. It runs in `run_math()`,
so the rule is checked on every test pass rather than by memory.

---

## 3. Module layout

```
rig_tail_chain_spacing.py     pure math, ZERO Maya imports
rig_tail_chain_build.py       Maya I/O: detect, guard, create, write
rig_tail_chain_build_ui.py    PySide2 window
rig_tail_test_chain.py        tests (math half runs outside Maya)
```

Import graph — strictly one direction, no cycles:

```
rig_tail_chain_build_ui  ->  rig_tail_chain_build  ->  rig_tail_chain_spacing
         |                            |                        |
         v                            v                     (nothing)
   rt_cst, rt_nam, rt_ui      rt_cst, rt_nam, rt_jnt,
   (read only)                rt_mat, rt_mya, rt_set
```

`rig_tail_chain_spacing.py` importing nothing but `math` is a hard
requirement, not a preference: it is what lets the entire risky part of the
tool be tested in plain Python, outside Maya, before any scene code exists.

---

## 4. Core concept: shape and distribution are separate

This split is the whole design. Get it right and the "cumulative distortion"
problem mostly disappears.

- **Shape** — the path through space the joints lie on. Reconstructing it
  from N samples is the *only* lossy operation, and only when the count
  decreases.
- **Distribution** — where along that path each joint sits. Uniform, Power
  and Ratio are analytic functions of `j/(n-1)`. They are count-independent
  and idempotent: applying k=1.7 twice produces bit-identical results.
  **Zero drift, ever.**

So error is confined to one operation (shape reconstruction on a count
*decrease*), and three mechanisms keep it from accumulating (§5.7).

---

## 5. `rig_tail_chain_spacing.py` — the math

Pure functions on lists of floats. No Maya. No logging beyond returning
values a caller can validate.

### 5.1 Module constants

```python
ARC_SAMPLES = 16      # sub-samples per span when building the arclength table
SNAP_TOL    = 1e-4    # normalized-arclength window for snap-to-existing
EPS         = 1e-9
ALPHA       = 0.5     # centripetal Catmull-Rom
K_RANGE     = (0.2, 5.0)
R_RANGE     = (0.5, 1.5)
```

### 5.2 Centripetal Catmull–Rom

`catmull_rom_knots(points)` → knot parameters
`catmull_rom_eval(points, knots, t)` → `[x, y, z]`

Interpolating, local, no cusps or self-intersection. **Do not use a cubic
B-spline or `curve -d 3 -ep`** — those *approximate*, so they round the chain
even at unchanged count, which guarantees drift.

Knots: `t_0 = 0`, `t_{j+1} = t_j + |p_{j+1} - p_j|^ALPHA`.
Phantom end points: `p_{-1} = 2*p_0 - p_1`, `p_N = 2*p_{N-1} - p_{N-2}`.
Evaluation: Barry–Goldman recursive form (three lerp levels).

**Coincident-point guard.** Duplicate positions give `t_{j+1} == t_j` and a
division by zero. The repo already documents chains with stacked joints
([rig_tail_math.py:90](rigTail/scripts/rig_tail_math.py:90)). Collapse runs of
points closer than `EPS` before building knots and re-expand afterwards, or
abort with a clear message. Do not let a NaN reach `cmds.xform`.

Degenerate counts: `N == 2` → straight-line lerp, skip Catmull–Rom entirely.

### 5.3 Arclength table

`arclength_table(points)` → `(table, total)`
`eval_at_arclength(points, knots, table, total, u)` → `[x, y, z]`

Sample `ARC_SAMPLES` sub-points per span, accumulate chord distances into a
cumulative table, then invert by binary search plus linear interpolation.
Error is O(1/ARC_SAMPLES²); 16 is ample.

**Critical detail:** the normalized knot positions `ŝ_i` must come from this
**curve** arclength table, not from raw chord lengths between joints. On a
curved chain those differ, and using chord lengths breaks the exactness
property in §5.7.

Because sub-sample 0 of span *i* is the Catmull–Rom evaluation at knot
`t_i`, which equals `p_i` exactly, evaluating at `ŝ_i` returns `p_i`.

### 5.4 Distribution functions

All map `t ∈ [0,1] → u ∈ [0,1]`, strictly increasing, `f(0)=0`, `f(1)=1`.

```python
distribute(mode, n, param=None, invert=False, source=None) -> list[float]
```

| Mode | Formula | Parameter |
|---|---|---|
| `uniform` | `u = t` | none |
| `power` | `u = t**k` | `k`, default 1.7, clamped to `K_RANGE` |
| `ratio` | `u_j = (1 - r**j) / (1 - r**(n-1))` | `r`, default 0.90, clamped to `R_RANGE` |
| `keep` | PCHIP through the source chain's `(i/(N-1), ŝ_i)`, evaluated at `j/(n-1)` | none; requires `source` |

Notes:

- `power` with `k=1` and `ratio` with `r=1` both reduce to `uniform`.
  Implement all three in one function with a mode argument so switching
  modes is continuous rather than a jump. Special-case `|r - 1| < EPS` to
  avoid dividing by zero.
- **Direction, and a defaults inconsistency to resolve.** `k > 1` packs
  joints toward the **base**; `r < 1` packs them toward the **tip**. So the
  proposed defaults (k=1.7, r=0.90) taper in *opposite* directions. Decide
  one of: flip the power default to ~0.6, flip the ratio default to ~1.11,
  or keep both and rely on Invert. Whatever is chosen, state it in the
  tooltip. **Flagged for the implementation session.**
- Clamping matters: at n=30, r=0.7 produces a final segment of 2e-5 × chain
  length — a zero-length bone that breaks aim-orient downstream in Setup.

**Invert** is `u' = 1 - f(1 - t)` — mirror the profile. It is *not* `1/k`;
that would give a different curve family. Invert applies to all four modes,
including `keep` (which reverses base/tip density — legitimate and useful).

### 5.5 PCHIP (for `keep` mode)

`pchip_tangents(x, y)` → slopes
`pchip_eval(x, y, m, xq)` → value

Fritsch–Carlson monotone cubic Hermite:

```
Δ_i = (y_{i+1} - y_i) / (x_{i+1} - x_i)
interior:  if Δ_{i-1} * Δ_i <= 0:  m_i = 0
           else: w1 = 2h_i + h_{i-1}
                 w2 = h_i + 2h_{i-1}
                 m_i = (w1 + w2) / (w1/Δ_{i-1} + w2/Δ_i)
endpoints: m_0 = Δ_0,  m_{N-1} = Δ_{N-2}
Hermite:   h00 = 2s³-3s²+1   h10 = s³-2s²+s
           h01 = -2s³+3s²    h11 = s³-s²
```

Monotone by construction, so resampled `u` is strictly increasing and no
segment can invert or collapse.

**Use PCHIP, not model fitting.** The original plan's "fit the chosen
spacing model then re-evaluate" step is dropped: least-squares fitting a
power exponent to an arbitrary chain discards every deviation from that
model — strictly *more* distortion than direct resampling, and it silently
reshapes a chain that was never power-distributed. Keep fitting only as an
optional "read k from selection" convenience button (§7, deferred).

### 5.6 Snap to existing

```python
snap_to_source(u_list, s_hat, tol=SNAP_TOL) -> list[int|None]
```

For each target `u_j`, if it lands within `tol` of an existing `ŝ_i`, return
index `i` so the caller writes the **original position** `p_i` rather than a
curve evaluation.

Rules:

- `j = 0` and `j = n-1` always snap to `p_0` and `p_{N-1}` — endpoints are
  fixed by definition.
- Indices must strictly increase; never let two targets claim the same
  source (that would create a zero-length segment).

This is the "clean snapping value" case: reducing a uniform 21-joint chain
to 11 lands exactly on every other original joint, distortion literally
zero. Any count change that divides cleanly becomes lossless. Default on.

### 5.7 The three exactness mechanisms

1. **Exact no-op at unchanged count.** Both Catmull–Rom and PCHIP
   interpolate at their knots, so `n == N` reproduces the original
   positions to floating point. Repeated same-count re-spacing never
   drifts. Snap-to-existing makes this exact rather than
   FP-approximate. *Test: `test_resample_noop`.*

2. **Snap to existing** (§5.6) — makes clean-divisor count changes lossless.

3. **Session original cache** (§6.5) — the largest single win. The real
   workflow is "type 18, look, type 14, look, type 22". Reading the previous
   *result* each time stacks three lossy passes; resampling from a cached
   original gives one, regardless of edit count. Drift becomes O(1) instead
   of O(edits).

**What remains irreducible:** decreasing joint count destroys curvature
between retained samples. No algorithm recovers it. With the three
mechanisms, total session error equals *one* down-res pass. That is the
target, and the tests assert it.

### 5.8 Top-level entry point

```python
resample(source_points, n, mode, param=None, invert=False, snap=True)
    -> (positions, snapped_indices)
```

Pipeline: knots → arclength table → `ŝ` → `distribute` → `snap_to_source` →
`eval_at_arclength` for the unsnapped. Pure, testable, no Maya.

---

## 6. `rig_tail_chain_build.py` — the Maya layer

### 6.1 Public API

```python
build_new(start, end, n, rigname, mode='uniform', param=None, invert=False)
rebuild(root_joint, n, mode='keep', param=None, invert=False, snap=True)
rebuild_selected(n, mode, param=None, invert=False, snap=True)
resolve_selection()  -> list of ChainSpec
clear_cache(root=None)
```

Every mutating entry point wraps its work in
`rt_mya.build_performance_scope(name='Tail Chain Build')` — the existing
context manager gives a single undo chunk plus suspended refresh, is
re-entrant, and restores state in a `finally`
([rig_tail_maya.py:87](rigTail/scripts/rig_tail_maya.py:87)).

### 6.2 Selection resolution

| Selection | Interpretation |
|---|---|
| Two joints, one an ancestor of the other | **Rebuild** the span between them |
| Two transforms, unrelated | **Build new** chain from A's position to B's, parented under A's parent; A and B are left untouched |
| One joint | Walk up to the chain root, down to the tip; **rebuild** |
| Many joints | Resolve each to its root, dedupe, process each chain independently |
| Nothing / non-transforms | `abort_build` with a clear message |

Walking up: stop when the parent is not a joint, **or when the parent has
more than one joint child** (a branch point is the chain start). Without the
second condition, a single click on a tentacle joint climbs into the spine.

Walking down: reuse `rt_jnt.get_joint_chain`, which already stops at `_ee_`
([rig_tail_joint.py:42](rigTail/scripts/rig_tail_joint.py:42)).

### 6.3 Guards — abort before touching anything

- Chain shorter than 2 joints, or requested `n < 2`.
- Total chain length below `EPS` (all joints coincident).
- Any chain joint feeds a `skinCluster`. The tool runs *before* Setup and
  Build; renumbering a bound chain would sever a skin. Refuse.
- Any chain joint has incoming connections on `translate` or
  `offsetParentMatrix` (it is being driven by a built rig). Refuse and
  point the user at Remove Rig.
- A chain joint has joint children beyond the chain continuation — those
  would be orphaned when intermediates are deleted. Refuse, naming them.
- Locked or limited translate channels.

Fail loud and early via `abort_build`. Half-applied joint edits are far
worse than a refusal.

### 6.4 Writing the result

Rather than delete-and-recreate the whole chain, prefer **reuse in place**:

- `n == N`: move existing joints. No creation, no deletion, no renaming.
- `n < N`: move the first `n`, delete the surplus, re-parent the `_ee_`.
- `n > N`: move the existing `N`, create `n - N` new joints, re-chain.

Reuse keeps attributes, `rotateOrder`, `preferredAngle`, custom attrs and
outgoing connections intact for the joints that survive, which is most of
them. New joints copy `rotateOrder` and `preferredAngle` from their nearest
surviving neighbour.

**`_ee_` end joints.** Excluded from the chain by `get_joint_chain` but they
must still be repositioned: place the `_ee_` along the final segment's
direction at its original distance from the old last joint. Setup's
`_find_end_joint` depends on them existing and being sensibly placed.

### 6.5 Session original cache

```python
_ORIGINALS = {}   # root long name -> {'positions': [...], 'written': [...]}
```

On rebuild:

1. Key on the root joint's **long DAG path**.
2. If a cache entry exists **and** the chain's current positions match
   `entry['written']` within `rt_cst.JOINT_POS_TOLERANCE`, resample from
   `entry['positions']` — the original.
3. Otherwise the artist has hand-edited the chain (or it is new to this
   session): seed the cache from the current positions.
4. After writing, store the result in `entry['written']`.

Module global, dies on reload, no scene metadata, fully removable. This
mirrors how `rig_tail_cache.py` already works. Expose `clear_cache()` and a
"Reset Original" button so the artist can deliberately re-baseline.

### 6.6 Naming policy

**New chains** use the template
`rt_nam.fstr(rigname, rt_cst.JOINT, rt_cst.TYPE_BN, i)` with `i` starting at
**0** and `DFORMAT = '{:02d}'`, matching
[rig_tail_cleanup.py:814](rigTail/scripts/rig_tail_cleanup.py:814).

**Rebuilds:**

- `n == N` → names untouched. Guaranteed, no exceptions.
- `n != N` → try `rt_nam.get_rigname(joint, rt_cst.JOINT)` on the chain. If
  the names parse consistently, **renumber the whole chain from the
  template**, preserving rigname and TYPE, so indices stay sequential along
  the chain. If they do not parse, keep the first `min(n, N)` names
  positionally and generate names for the remainder.
- Log every rename at INFO. A silent renumber on a chain someone has
  scripted against is a nasty surprise.

Strictly preserving names through a count change is impossible for the new
joints; this is the closest honest reading of "preserve existing names".
**Confirm this rule in the implementation session before coding it.**

### 6.7 Orientation

**v1 default: positions only. Orientation belongs to Setup.**

Since the Chain Builder runs *before* Setup, and Setup exists precisely to
aim-orient chains (`ORIENT_JOINTS`, `ORIENT_UP_MODE` cascade), the clean
division is that the Chain Builder never writes orientation on an existing
chain. The UI must say so plainly:

> Run **Tail Rig Setup → Orient Joints** after changing joint count.

Optional checkbox **"Orient on create"** (new chains only): call
`rt_set.aim_frames(positions, aim_axis, up_axis, up_ref)`
([rig_tail_setup.py:598](rigTail/scripts/rig_tail_setup.py:598)) and write the
frames. This is a read-only use of a public function — allowed, no core
change. New chains built with `cmds.joint` top-down get a sane default
orient anyway, so this is convenience, not necessity.

### 6.8 Twist preservation (v1.5, documented not built)

If a chain carries deliberate roll from `roll_chain`, re-aiming after a
position change loses it. The fix: before moving, measure each joint's roll
about its aim relative to a parallel-transported frame, giving `twist(ŝ)`;
resample that with the same PCHIP; re-apply after re-aiming. Worth noting
now because `roll_chain` exists and users will have rolled chains.

### 6.9 Settings

```python
PREFS_FILE = os.path.join(os.path.dirname(__file__), 'rig_tail_chain_config.json')
DEFAULTS = {'mode': 'keep', 'power': 1.7, 'ratio': 0.90,
            'invert': False, 'snap': True, 'orient_on_create': False}
```

Own file, own dict. Never `rt_cst.get_user_editable_config()`. Loaded on
import, saved on demand. Add the file to `.gitignore`.

---

## 7. `rig_tail_chain_build_ui.py` — the UI

PySide2/Qt5, modelled on `rig_tail_setup_ui.py`. Defines its **own**
`create_group_box` / `style_button` copies (§2.2). Button styles: 0 grey,
1 blue, 2 yellow.

```
TAIL CHAIN BUILDER
Create and re-space joint chains before Setup
                                  author Daisy Jane @gnitemouse

+- Source ------------------------------------------+
| ( ) Rebuild selected chain                        |
| ( ) New chain between two selected objects        |
| Chain:  [C_fintail                    ] [Select]  |
| Rig Name: [                           ]  (new)    |
+---------------------------------------------------+

+- Spacing -----------------------------------------+
| Joint Count:  [ 21 ]        (detected: 21)        |
| Mode:         [ Keep      v]                      |
| Exponent:     [ 1.70 ]      (label swaps by mode) |
| [ ] Invert distribution                           |
| [x] Snap to existing joints                       |
| [ ] Orient on create           (new chains only)  |
+---------------------------------------------------+

  C_fintail:  21 -> 18 joints,  length 43.21
  Run Tail Rig Setup > Orient Joints after changing count.

  [ Build / Rebuild ]  (yellow)
  [ Reset Original ]  [ Load Prefs ]  [ Save Prefs ]  (grey)
```

Behaviour:

- The parameter field's label and range swap with the mode: **Exponent**
  0.2–5.0 for Power, **Ratio** 0.5–1.5 for Ratio, disabled for Uniform and
  Keep.
- **Keep** is disabled in New-chain mode (there is no source distribution).
  Falls back to Uniform with a tooltip explaining why.
- Joint Count defaults to the detected chain length on selection, so the
  first click is a no-op re-space rather than a surprise.
- The status line updates on selection change and on any field edit.
- The Setup-UI convention of a settings summary + config file row is
  **not** copied — the chain builder has six settings, and Load/Save Prefs
  buttons are enough.

`show_ui()` closes any previous instance, same as the other two windows.

### Deferred UI

- "Read k from selection" (fit an exponent to the selected chain, for
  convenience only — never in the rebuild path).
- Bezier / ramp bias editor (v2, replaces the single parameter field).

---

## 8. `rig_tail_test_chain.py`

Follows the `rig_tail_test_setup.py` split: `run_math()` is safe and needs
no scene; `run_scene()` mutates.

### Math tests (plain Python, no Maya)

| Test | Asserts |
|---|---|
| `test_containment` | no core module mentions `rig_tail_chain` |
| `test_distribution_endpoints` | every mode/param: `f(0)=0`, `f(1)=1`, strictly increasing |
| `test_uniform_equivalence` | power k=1 == ratio r=1 == uniform |
| `test_invert_symmetry` | `invert(invert(f)) == f` |
| `test_pchip_monotone` | random monotone data resamples monotonically |
| `test_pchip_knot_exact` | evaluation at knots returns knot values |
| `test_catmullrom_interpolates` | curve at knot params == input points |
| `test_arclength` | monotone; total ≈ chord sum for a straight chain |
| `test_keep_idempotent` | N→N returns `ŝ` to < 1e-9 |
| `test_resample_noop` | full pipeline N→N returns positions to < 1e-6 |
| `test_roundtrip_drift` | 20→30→20 max deviation < tol × length |
| `test_snap_exact` | 21→11 uniform snaps to every other original, exactly |
| `test_degenerate` | N=2, coincident points, r=1.0, k=1.0, n=2 |

`test_roundtrip_drift` and `test_resample_noop` are the ones that actually
answer the design question. Write them first.

### Scene tests (mutating)

| Test | Asserts |
|---|---|
| `test_rebuild_count` | count, order, parenting, `_ee_` position |
| `test_names_preserved` | `n == N` leaves every name untouched |
| `test_names_renumbered` | `n != N` renumbers sequentially and logs |
| `test_guards` | skinned / rig-driven / branching chains are refused |
| `test_cache_no_compounding` | 10 random count changes then back to N deviates the same as a single N→n→N pass, not 10× |
| `test_undo` | one Ctrl+Z restores the pre-click state |

`test_cache_no_compounding` is the empirical proof of §5.7.3.

---

## 9. Implementation order

**Phase 1 — math (no Maya).** `rig_tail_chain_spacing.py` plus the math half
of `rig_tail_test_chain.py`. Runs in plain Python. **Validate the drift
properties before any scene code exists.** Prior sessions on this repo lost
two round trips to buggy test metrics — verify the metrics here, offline,
where iteration is free.

**Phase 2 — Maya layer.** `rig_tail_chain_build.py`: selection resolution,
guards, cache, write. Drive from the Script Editor. Confirm the naming rule
(§6.6) before coding it.

**Phase 3 — UI.** `rig_tail_chain_build_ui.py`, prefs round-trip.

**Phase 4 — integration.** `install.py` shelf button (fourth octopus recolour
for the icon), `uninstall.py` label, README and documentation sections,
scene tests.

Commit at each phase boundary. Phases 1–3 touch no core file at all — only
Phase 4 does, and only inside marked blocks.

---

## 10. Open decisions for the implementation session

1. **Power/Ratio default direction** (§5.4). They currently taper opposite
   ways. Pick one convention.
2. **Rename rule on count change** (§6.6) — confirm "renumber from template
   when parseable" before coding.
3. **Shelf icon** — a fourth octopus recolour, or reuse the grey one?
4. **`_ee_` on new chains** — does the Chain Builder create one, or is that
   Setup's job? Setup's `_find_end_joint` expects it to exist.

---

## 11. Rejected, and why

**Live preview.** Requires temporary joints, scene cleanup, live callbacks,
UI sync, undo handling and refresh management — for an operation that takes
under a second and is a single undo. Apply-then-undo *is* the preview. The
session original cache (§6.5) already gives the thing preview would be for:
tweaking the count repeatedly without accumulating damage.

**Scene metadata.** Nothing to strip on removal, nothing to version, nothing
to go stale. The session cache covers the actual need.

**Fitting the spacing model during rebuild** (§5.5). More lossy than direct
resampling and unpredictable on chains that don't match the model.

**Approximating splines** for shape reconstruction (§5.2). They round the
chain even at unchanged count, which would break the no-op guarantee that
the whole anti-drift argument rests on.
