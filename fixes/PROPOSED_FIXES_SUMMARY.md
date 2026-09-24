# Proposed Fixes Summary

Companion to [MA_GYM_ERGON_EVALUATION.md](./MA_GYM_ERGON_EVALUATION.md), which
covers the *why* (which MA-Gym bugs affect this project, and what Ergon does
or doesn't solve). This doc covers the *what to actually do*: a minimal,
project-scoped fix list, broken into ordered phases, each with a concrete
Definition of Done.

Scope discipline: this list is deliberately **not** "fix everything wrong
with MA-Gym." It's scoped to what blocks this project's specific challenge
tasks (preference-shift, team-churn, compound, cross-episode — see
`docs/challenge-tasks.md`) and metrics (`docs/metrics.md`) from producing
trustworthy results. Fixes live on the `fyp/ma-gym-fixes` branch of
[josshhz11/manager_agent_gym](https://github.com/josshhz11/manager_agent_gym),
one isolated commit per fix, so they're easy to cherry-pick into the
supervisor's group's shared MA-Gym refactor later.

---

## 1. Minimal fix list, scoped to this project

**MUST FIX — blocks core experiments outright**

| # | Bug(s) | Why it's must-fix for this project specifically |
|---|---|---|
| 1 | `NEW-001` (preference weight mutated in place) | Confirmed in `manager_agent_gym/core/workflow_agents/stakeholder_agent.py:191-274`. Directly corrupts the preference-shift task's core measurement — the manager's observed weight history and the scored weight history can silently diverge. |
| 2 | `ML-001`/`ML-002` (no artifact handoff, no scenario resource wiring) | Blocks team-churn, compound, and the ad-hoc-coordination half of the research gap — without it there's nothing for a reassigned worker to inherit from a predecessor. |
| 3 | `ML-051`/`ML-052`/`ML-053` (FAILED is absorbing with no retry; `AssignTaskAction` silently succeeds on un-executable tasks; engine rejections never reach the manager) | The closest thing MA-Gym has to documented reassignment mechanics, and they're broken. A worker "leaving" mid-task would strand that task permanently (ML-051), reassignment attempts would silently no-op (ML-052), and the manager would never see the rejection (ML-053) — so the team-churn task would test "does the system silently stall," not "does it recover." |
| 4 | `ML-003`/`ML-009`/`ML-011`/`ML-015` (fake completion — `COMPLETED` means only "the call didn't crash") | Every one of the four challenge tasks reports `goal_completion_rate`. This is a floor confound under all of them, not one task's problem. |

**SHOULD FIX — needed before trusting statistics/metrics, but narrowly scopable**

| # | Bug(s) | Why, and the narrow workaround |
|---|---|---|
| 5 | `ML-092` (seed non-reproducibility) | The paired t-test design in `docs/metrics.md` assumes seed-matched pairs. Needs empirical verification either way; may resolve to a fix, or to a documented fallback (unpaired test, more seeds). |
| 6 | `ML-016`/`ML-033`/`ML-034` (dead scoring-aggregation formulas, always-zero categories) | Only matters for `constraint_violations`. Doesn't require fixing MA-Gym's general aggregation system — just this project's own constraint rubrics running the formula they're supposed to. |
| 7 | `ML-007`/`ML-023` (300-char judge preview, judge blind to declared `required_context`) | Only matters if LLM-judge rubrics are used for goal-achievement/preference-alignment scoring in the Phase 2 vs. Phase 3 ablation. Scope to whichever rubrics are actually kept. |
| 8 | `ML-025`/`ML-026` (`EACH_TIMESTEP` evaluation cadence broken) | Only needed if fine-grained within-episode tracking near the injection point is wanted. Current challenge-task design injects at one fixed timestep and doesn't obviously require per-timestep sampling — treat as optional. |

**DEFER / SKIP — not needed for current phase scope**

- `ML-070`/`ML-072`/`ML-073` (archive/restore fidelity) — only matters for the cross-episode task, already a stretch goal. Revisit only if picked up.
- `ML-036` (judge sampling noise) — a property of LLM judges, not a fixable code bug. Mitigate via practice (multiple judges/seeds, report agreement), not a patch.
- `ML-006`, `ML-045`, `ML-047`, `ML-050` — real bugs, none on the critical path unless directly triggered. Monitor, don't pre-fix.

---

## 2. Implementation breakdown, organized into phases

Ordered so each phase either unblocks the next or matches the priority of
the down-scoped tasks (team-churn and preference-shift first, per prior
discussion).

**Status legend:** Not started · In progress · Complete (unit-verified) · Complete (verified end-to-end) · Blocked. Update with `/status-update`.

**Last updated:** 2026-09-24

### Phase A — Foundational integrity (do first; cheap, isolated, unblocks everything downstream)

**Status:** In progress — Fix 1 complete (unit-verified), Fix 4 not started

#### Fix 1 — NEW-001: preference weight mutated in place

**Status:** Complete (unit-verified); end-to-end check outstanding

- **Where:** `manager_agent_gym/core/workflow_agents/stakeholder_agent.py:191-274` (`apply_weight_update`), reading via `get_preferences_for_timestep` (lines 177-181).
- **Root cause:** `get_preferences_for_timestep` returns the stored `PreferenceWeights` object *by reference*. `apply_weight_update` builds `name_to_pref = {p.name: p for p in current.preferences}` — a dict of references to the `Preference` instances inside that live timeline entry — and mutates `.weight` on them directly. The `PreferenceWeights(...)` built afterward (line 262) then normalizes those same instances in place via its `model_validator`. The *new* timeline entry is safe (it's stored via `.normalize()`, which copies), but the *earlier* entry `current` was already rewritten: after an update at `t=7`, `_preference_timeline[0]` reads the `t=7` weights.
- **Fix (implemented):** build `name_to_pref` from `preference.model_copy()` so every in-place mutation downstream (the `delta`/`multiplier`/`absolute`/clamp branches and the validator) lands on copies. One change at the construction site instead of one per branch.
- **Ergon relevance:** none — nothing to port, this is a pure MA-Gym patch.
- **Effort:** small, self-contained, one file plus a regression test (`tests/test_stakeholder_preference_history.py`).

**How to confirm it is fixed and working (DoD):**
- [x] Unit test: `apply_weight_update` at `t=7` (parametrized over `delta`/`multiplier`/`absolute`) leaves `get_preferences_for_timestep(0)` and `(6)` at the original weights. Verified failing before the fix, passing after.
- [x] Chained updates (`t=3` then `t=7`) preserve every earlier entry.
- [x] Existing non-integration suite unchanged: 50 passed before and after.
- [ ] Re-run the preference-shift smoke test end-to-end; log "preferences observed by the manager at each timestep" alongside "preferences stored in the timeline at that timestep" and diff them across the full run — they must match at every step, not just the last one. (Needs the manager observation path traced first; deferred until a scenario run is set up.)

**Summary of Changes Done:**
- `stakeholder_agent.py` (`apply_weight_update`): `name_to_pref` is now built from `preference.model_copy()`, so in-place mutation no longer rewrites the earlier timeline entry.
- Added `tests/test_stakeholder_preference_history.py` (5 tests: three update modes, chained updates, `previous_weights`).
- Confirmed the regression tests fail on the unpatched code (4 failed) and pass after the fix; full non-integration suite unchanged (55 passed, 3 skipped).
- Corrected this doc's root-cause description (the earlier timeline entry is the one corrupted, not the new one).

**What's left to verify:**
- End-to-end check: log what the manager observes vs. what the timeline holds at each step of a run with a real injected shift, and diff them.
- `get_preferences_for_timestep` still returns the live timeline entry (e.g. via `engine.py:312`); other callers have not been audited for mutating it.
- The audit says shown-vs-scored weights differ in 16/20 scenarios; not yet confirmed that this fix alone accounts for all of it.

---

#### Fix 4 — ML-003/009/011/015: fake completion (independent verification layer)

**Status:** Not started

- **Where:** doesn't touch engine internals. Deliberately built as an external layer in `src/eval/`, run as a post-hoc pass over run output — avoids engine surgery and reduces merge-conflict risk against the eventual shared team refactor.
- **Root cause (for context):** `engine.py:639` marks a task `COMPLETED` on `result.success` alone (comment at line 650: "Validation system removed; skip resource validations") — no check that the output is actually adequate.
- **Proposed fix:**
  1. Cross-check each `COMPLETED` task's `execution_notes`/output text for self-reported non-execution phrases (the ML-011 pattern — `"not_executed"`, `"no input data"`) against the engine's status; flag disagreements.
  2. For task types with checkable acceptance criteria, run a cheap deterministic placeholder-marker check (`[Date]`, `"Seller Legal Name"`-style stubs — how the original audit found this).
  3. Log a `verified_completion` field alongside the engine's own `goal_completion_rate` in the metrics schema; report both.
- **Ergon relevance:** none — not addressed there either; this is fully project-owned tooling, independent of platform choice.

**How to confirm it is fixed and working (DoD):**
- [ ] Run the verification layer against at least one real trace with known issues (the ICAAP smoke-test run, or a released 20-workflow trace) and confirm it flags the same class of problem the original audit found (placeholder stubs / `not_executed` mismatches).
- [ ] Confirm `verified_completion` differs meaningfully from raw `goal_completion_rate` on at least one real run (sanity check it isn't a no-op that always agrees).
- [ ] Confirm both fields are present in `metrics.json` for every subsequent experiment run per the logging schema.

**Summary of Changes Done:**
- None yet.

**What's left to verify:**
- All items in the DoD checklist above.

---

### Phase B — Coordination substrate (needed before team-churn / compound experiments)

**Status:** Not started

#### Fix 2 — ML-001/ML-002: no real artifact handoff

**Status:** Not started

- **Where:**
  - `manager_agent_gym/schemas/core/tasks.py:45-53` — `input_resource_ids` field exists but nothing writes it; `dependency_task_ids` (precedence) is separate and already used for scheduling.
  - `manager_agent_gym/core/execution/engine.py:757-772` (`_get_task_resources`) — reads `task.input_resource_ids`, always empty.
  - `manager_agent_gym/core/execution/engine.py:639-675` — where a completed task's `output_resource_ids` get populated, but nothing propagates them to dependents.
  - `manager_agent_gym/core/decomposition/service.py:80-81` — subtask creation copies `task.input_resource_ids` *at decomposition time* (`ML-008`); a completion-time fix won't reach subtasks created before their parent's inputs were wired. Documented limitation, not blocking unless baselines rely heavily on decomposition.
- **Proposed fix (deliberately lighter than Ergon's full blob-store redesign):** at task completion in `_execute_ready_tasks` (engine.py:639-675), after `output_resource_ids` updates, walk the workflow's tasks and append the completed task's `output_resource_ids` to every task whose `dependency_task_ids` includes it:
  ```python
  for other in self.workflow.tasks.values():
      if task_id in other.dependency_task_ids:
          other.input_resource_ids = list(set(other.input_resource_ids) | set(resource_ids))
  ```
- **Ergon relevance:** `docs/architecture/cross_cutting/artifacts.md`'s content-addressed store is the "unlimited time" version — useful as a reference for the *shape* of a correct fix (explicit IDs, no silent empty-list fallback), not worth porting wholesale for this scope.

**How to confirm it is fixed and working (DoD):**
- [ ] Integration test: Task B depends on Task A; after Task A completes with `output_resource_ids=[r1]`, assert Task B's `input_resource_ids` contains `r1` *before* B starts, and `_get_task_resources(B)` returns that resource's actual content.
- [ ] Run one real workflow (ICAAP smoke test) end-to-end and manually confirm at least one downstream task's prompt contains upstream artifact content — not the `"No specific input resources provided"` fallback string.
- [ ] Recompute the audit's own measurement (`% of task executions with >=1 populated input_resource_id`) on a fresh run; confirm it's now meaningfully above 0% (the audit's baseline finding).

**Summary of Changes Done:**
- None yet.

**What's left to verify:**
- All items in the DoD checklist above.

---

#### Fix 3 — ML-051/052/053: churn/reassignment integrity

**Status:** Not started

- **Where (per audit CSV — line numbers not independently re-verified this session, confirm before implementing):**
  - Scheduling readiness logic (`is_ready_to_start`-equivalent) — FAILED tasks never re-enter the ready set, no retry path.
  - `AssignTaskAction.execute` (audit cites `manager_actions.py:131`) — validates only that task/agent ids exist, not whether the task is actually assignable (composite parent, unmet deps, already RUNNING/FAILED/COMPLETED).
  - Same method — doesn't mutate `self.success`/`self.result_summary` on rejection, so the manager never observes the rejection.
- **Proposed fix:**
  1. Add a requeue path: when a task's assigned agent becomes unavailable (the injected "leave" event), transition the task back to READY instead of leaving it permanently FAILED.
  2. Add real validation to `AssignTaskAction.execute` — reject assigns to composite parents, tasks with unmet dependencies, or non-assignable states, with an actual failure result rather than a silent success.
  3. Ensure the rejection writes back to `action_result.success`/`result_summary` so the manager's next observation reflects it.
- **Ergon relevance:** none found — no join/leave/reassignment handling documented in Ergon either. Fully project-owned; a good candidate to raise in the shared-refactor requirements doc given the other three students likely hit the same gap.

**How to confirm it is fixed and working (DoD):**
- [ ] Inject a synthetic "worker leaves mid-task" event in a test scenario; assert the task transitions back to READY (not permanently FAILED) and is picked up by a remaining/new worker within a bounded number of timesteps.
- [ ] Confirm `AssignTaskAction.execute` now returns `success=False` for an invalid assign (e.g. targeting a composite parent or an already-COMPLETED task), and that this is visible in the manager's next observation (not silently dropped).
- [ ] Run the team-churn challenge task end-to-end at each injection depth (25/50/75%) and confirm no task is left permanently orphaned at episode end.

**Summary of Changes Done:**
- None yet. Audit line numbers for `AssignTaskAction` still need re-verifying before implementing.

**What's left to verify:**
- All items in the DoD checklist above.

---

### Phase C — Statistical/metrics trust (needed before running comparative experiments across seeds/conditions)

**Status:** Not started

#### Fix 5 — ML-092: seed reproducibility

**Status:** Not started

- **Where:** manager/worker LLM calls pass `seed=42` unconditionally (audit cites `llm_interface.py:252` — not independently re-verified this session).
- **Proposed approach:** don't attempt a general fix first — test empirically. Run the identical scenario+seed twice, diff the action sequences/manager-call counts. If divergent, the pragmatic mitigation is procedural (more seeds, switch to an unpaired significance test) rather than chasing seed-threading through the full provider stack, which is disproportionate to project scope.
- **Ergon relevance:** none found.

**How to confirm it is fixed and working (DoD):**
- [ ] Run the identical scenario+seed twice; diff manager action sequences and call counts. Document the outcome either way (identical → fixed; still divergent → documented as a known limitation with the resulting stats-design fallback recorded in `docs/metrics.md`).
- [ ] If a code-level fix is attempted, confirm near-identical results (or quantify residual variance) across at least 5 repeated runs of the same seed before trusting paired comparisons built on it.

**Summary of Changes Done:**
- None yet.

**What's left to verify:**
- All items in the DoD checklist above.

---

#### Fix 6 — ML-016/033/034: dead scoring-aggregation formulas

**Status:** Not started

- **Where:** `manager_agent_gym/core/evaluation/validation_engine.py` (aggregation dispatch), `constraint_evaluator.py` (dead deterministic `hard_constraints_enforced` per the audit).
- **Proposed fix (scoped, not a general MA-Gym fix):** write a dedicated aggregation function for exactly the constraint rubrics this project's challenge tasks use; call it directly from `src/eval/` instead of routing through the broken shared weighted-max path; unit-test against a synthetic case with a known expected zero/non-zero outcome.
- **Ergon relevance:** Ergon's own audit found the same *shape* of bug (silent zero-scores) — confirms this isn't a MA-Gym-only quirk, but nothing to port; the scoped rubric aggregation sidesteps it either way.

**How to confirm it is fixed and working (DoD):**
- [ ] Unit test the scoped aggregation function against a synthetic case with a known expected result (e.g. all-zero rubric inputs → `hard_constraints_enforced == 0.0`, not a silent fallback to a different formula).
- [ ] Confirm `constraint_violations` values on a real run vary meaningfully across conditions expected to differ (not clustered at the previously-observed dead values, e.g. the audit's ~0.43–0.64 shadow range coexisting with a 0.0 hard-constraint score).

**Summary of Changes Done:**
- None yet.

**What's left to verify:**
- All items in the DoD checklist above.

---

#### Fix 7 (optional, should-fix) — ML-007/023: judge preview length and blind rubric context

**Status:** Not started (optional)

- **Where:** judge/rubric context assembly in `manager_agent_gym/core/evaluation/` (300-char resource preview; `required_context` fields declared but not populated — audit cites `validation_engine.py:396-468`, not independently re-verified this session).
- **Proposed fix:** only implement for rubric types actually retained for the Phase 2/3 ablation. Read full artifact content instead of a fixed preview (mirroring Ergon's pattern in `docs/superpowers/plans/2026-04-28-evaluation-resource-context-and-scoring.md`) and populate the declared `required_context` fields the rubric prompt actually consumes.
- **Ergon relevance:** Ergon has a proven fix for one evaluator type — usable as a direct reference, not proven to generalize; verify per rubric type used here.

**How to confirm it is fixed and working (DoD):**
- [ ] Log prompt character count per rubric call for rubrics in use; confirm none are capped at ~300 chars.
- [ ] Programmatically assert that every `required_context` field a used rubric declares is non-`None`/populated in the assembled context — not a spot check.

**Summary of Changes Done:**
- None yet.

**What's left to verify:**
- All items in the DoD checklist above.

---

#### Fix 8 (optional, should-fix) — ML-025/026: `EACH_TIMESTEP` evaluation cadence broken

**Status:** Not started (optional)

- **Where:** `manager_agent_gym/core/execution/engine.py:194` (`self.evaluation_cadence: RunCondition = RunCondition.ON_COMPLETION`) and the run_condition filter bypass elsewhere in the evaluation engine.
- **Proposed fix:** only pursue if within-episode fine-grained tracking near the injection point becomes a project requirement. Set `evaluation_cadence = RunCondition.EACH_TIMESTEP` for the relevant run configuration and fix the filter-bypass condition (`... or cadence is not None`) so `run_condition`-gated rubrics respect their declared cadence again.
- **Ergon relevance:** none found.

**How to confirm it is fixed and working (DoD):**
- [ ] Run a short test scenario with `evaluation_cadence = EACH_TIMESTEP`; confirm evaluation records are produced at every timestep, not only at task completion.
- [ ] Confirm an `ON_COMPLETION`-only rubric no longer fires at every timestep (the ML-026 bypass is actually closed).

**Summary of Changes Done:**
- None yet.

**What's left to verify:**
- All items in the DoD checklist above.

---

## Deferred items (tracked, not scheduled)

Kept out of the phased plan above deliberately — see Section 1 for reasoning:

- `ML-070`/`ML-072`/`ML-073` (archive/restore fidelity) — only in scope if the cross-episode task is picked up beyond stretch-goal status.
- `ML-036` (judge sampling noise) — mitigated by evaluation practice (multiple judges/seeds), not a code fix.
- `ML-006`, `ML-045`, `ML-047`, `ML-050` — monitor; revisit only if directly triggered during experiments.
