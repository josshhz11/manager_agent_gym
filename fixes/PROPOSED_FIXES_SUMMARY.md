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

**Last updated:** 2026-10-07

### Phase A — Foundational integrity (do first; cheap, isolated, unblocks everything downstream)

**Status:** Complete — Fix 1 verified end-to-end; Fix 4 unit-verified with one real-run check (see its open items)

#### Fix 1 — NEW-001: preference weight mutated in place

**Status:** Complete (verified end-to-end)

- **Where:** `manager_agent_gym/core/workflow_agents/stakeholder_agent.py:191-274` (`apply_weight_update`), reading via `get_preferences_for_timestep` (lines 177-181).
- **Root cause:** `get_preferences_for_timestep` returns the stored `PreferenceWeights` object *by reference*. `apply_weight_update` builds `name_to_pref = {p.name: p for p in current.preferences}` — a dict of references to the `Preference` instances inside that live timeline entry — and mutates `.weight` on them directly. The `PreferenceWeights(...)` built afterward (line 262) then normalizes those same instances in place via its `model_validator`. The *new* timeline entry is safe (it's stored via `.normalize()`, which copies), but the *earlier* entry `current` was already rewritten: after an update at `t=7`, `_preference_timeline[0]` reads the `t=7` weights.
- **Fix (implemented):** build `name_to_pref` from `preference.model_copy()` so every in-place mutation downstream (the `delta`/`multiplier`/`absolute`/clamp branches and the validator) lands on copies. One change at the construction site instead of one per branch.
- **Ergon relevance:** none — nothing to port, this is a pure MA-Gym patch.
- **Effort:** small, self-contained, one file plus regression tests (`tests/test_stakeholder_preference_history.py`, `tests/test_icaap_preference_shift_history.py`).

**How to confirm it is fixed and working (DoD):**
- [x] Unit test: `apply_weight_update` at `t=7` (parametrized over `delta`/`multiplier`/`absolute`) leaves `get_preferences_for_timestep(0)` and `(6)` at the original weights. Verified failing before the fix, passing after.
- [x] Chained updates (`t=3` then `t=7`) preserve every earlier entry.
- [x] Existing non-integration suite unchanged: 50 passed before the fix, all pass after (78 with the new tests).
- [x] End-to-end: replayed ICAAP's real scripted schedule (`t=0,10,30,60`) through `apply_weight_updates`; every timestep sees the weights scheduled for it. The 20-step ICAAP smoke run also logged the expected weights at each step (initial through `t=9`, shifted from `t=10`). On the original code the same replay fails: `t=0` read `quality=0.286` (the `t=10` value) instead of `0.429`.
- [x] Invariant test over all 20 registered scenarios with scripted shifts (an update at `T` must not change what any `t<T` sees): all pass with the fix, all 20 fail on the original code.
- [x] Audited callers of `get_preferences_for_timestep` (engine.py:312 and its uses, `run_examples.py:243`): all read-only, and no other library code mutates a returned timeline entry.

**Summary of Changes Done:**
- `stakeholder_agent.py` (`apply_weight_update`): `name_to_pref` is now built from `preference.model_copy()`, so in-place mutation no longer rewrites the earlier timeline entry.
- Added `tests/test_stakeholder_preference_history.py` (5 tests: three update modes, chained updates, `previous_weights`).
- Added `tests/test_icaap_preference_shift_history.py` (21 tests): replay of ICAAP's scripted schedule, plus the no-earlier-timestep-rewritten invariant across all 20 registered scenarios.
- Confirmed the tests fail on the original code and pass after the fix; full non-integration suite: 78 passed.
- Corrected this doc's root-cause description (the earlier timeline entry is the one corrupted, not the new one).

**What's left to verify:**
- Nothing blocking. The manager's prompt text was not inspected directly; the engine passes `get_preferences_for_timestep` output into the manager observation, so it now shows the scheduled weights by construction.
- The audit measured "shown vs scored `t=0` vector differs in 16/20 scenarios"; our invariant (no earlier timestep rewritten) fails in 20/20 on the original code. These are different measurements, so the numbers are not directly comparable.

---

#### Fix 4 — ML-003/009/011/015: fake completion (independent verification layer)

**Status:** Complete (unit-verified); real-run check partial

- **Where:** lives in the parent repo, not this submodule: `src/eval/completion_verifier.py` (tests: `tests/test_completion_verifier.py`). Doesn't touch engine internals. Deliberately built as an external layer, run as a post-hoc pass over run output — avoids engine surgery and reduces merge-conflict risk against the eventual shared team refactor.
- **Root cause (for context):** `engine.py:639` marks a task `COMPLETED` on `result.success` alone (comment at line 650: "Validation system removed; skip resource validations") — no check that the output is actually adequate.
- **Proposed fix:**
  1. Cross-check each `COMPLETED` task's `execution_notes`/output text for self-reported non-execution phrases (the ML-011 pattern — `"not_executed"`, `"no input data"`) against the engine's status; flag disagreements.
  2. For task types with checkable acceptance criteria, run a cheap deterministic placeholder-marker check (`[Date]`, `"Seller Legal Name"`-style stubs — how the original audit found this).
  3. Log a `verified_completion` field alongside the engine's own `goal_completion_rate` in the metrics schema; report both.
- **Ergon relevance:** none — not addressed there either; this is fully project-owned tooling, independent of platform choice.

**How to confirm it is fixed and working (DoD):**
- [~] Real trace: checked on one ICAAP run (random manager, gpt-4o-mini, 20 timesteps, seed 42). It flagged the two unusable deliverables (dummy rows such as `ExamplePD01` / `John Doe`; a blank access-control form) and verified the third (generic but filled in), matching a manual read of all three outputs. Not yet seen flagging `[Date]`-style stubs or `not_executed` bodies on real output, and no released 20-workflow trace has been tried.
- [x] `verified_completion_rate` differs from raw completion on a real run: engine 3/37 (8.1%) vs verified 1/37 (2.7%) on the ICAAP smoke run.
- [x] Both fields are written to `metrics.json` per the logging schema: `python -m eval.run_metrics <run_dir> --condition ... --challenge-task ...` produced it for the ICAAP run (`tests/test_run_metrics.py`). No experiment runner calls it automatically yet, because none exists.
- [x] Unit tests for each flag, the hard/review split, composite exclusion, rate maths, the empty-workflow case and `metrics.json` construction, including one built from a real `Workflow.model_dump` and one using an excerpt of real blank-form output.

**Summary of Changes Done:**
- Added `src/eval/completion_verifier.py`: reads the workflow summary JSON and checks each engine-`COMPLETED` leaf task. Hard flags (fail verification): `no_assigned_agent`, `never_started`, `no_output_resources`, `empty_output`, `self_reported_non_execution`, `placeholder_content` (brackets, `TBD`, dummy names, "to be populated"), `blank_fields` (bullet labels or table cells with no value). Review flag (reported only): `template_resource`. CLI: `python -m eval.completion_verifier <summary.json>`.
- Ran it on a real ICAAP run. The first version reported 3/3 verified, but reading the outputs showed two were unusable; added the blank-fields and dummy-data checks and made `template_resource` review-only. Result: engine 3/37 (8.1%), verified 1/37 (2.7%).
- Added `src/eval/run_metrics.py`: builds and writes `metrics.json` per `docs/metrics.md` (validating `condition`, `challenge_task`, `change_depth`), logging both completion rates. `constraint_violations` is null until Fix 6.
- Added `tests/test_completion_verifier.py` (25 tests with the engine importable) and `tests/test_run_metrics.py` (6 tests).
- Updated `docs/metrics.md` (`verified_completion_rate`, `completion_flags`, `completion_review_flags`, the null `constraint_violations`) and added `pythonpath = ["src"]` to the parent pytest config.

**What's left to verify:**
- Only one run (3 completed tasks) has been checked; the flags were tuned on it, so how well they generalize is unknown. Try a longer run or a released trace.
- False-positive rate of the placeholder and blank-field heuristics is unmeasured.
- Blanks written in plain prose (not as bullet labels or table cells) are not detected.
- Nothing calls `run_metrics` automatically; it must be run per experiment until a runner exists.
- Only leaf tasks are scored; confirm that matches how `goal_completion_rate` should treat decomposed tasks.

---

### Phase B — Coordination substrate (needed before team-churn / compound experiments)

**Status:** In progress — Fix 2 verified end-to-end; Fix 3 not started

#### Fix 2 — ML-001/ML-002: no real artifact handoff

**Status:** Complete (verified end-to-end)

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
- [x] Integration test: Task B depends on Task A; after Task A completes with `output_resource_ids=[r1]`, Task B's `input_resource_ids` contains `r1` before B starts, and `_get_task_resources(B)` returns that resource's actual content (`tests/test_task_handoff.py`, 4 tests — handoff, worker-prompt content, no-false-positive for an unrelated task, and the audit's own measurement style). All 4 fail on the original code (3 for the real reason — no propagation; the 4th is a no-false-positive check that passes either way) and pass with the fix.
- [x] Ran ICAAP end-to-end (random manager, gpt-4o-mini, 20 timesteps, seed 42). 6 pending tasks picked up real content from a completed predecessor (e.g. `Reverse Stress Test Framework Example`, 2193 chars, propagated to `3-Year Capital Planning (Normative)` and 3 other dependents), none of it from unrelated co-dependencies — confirmed by cross-checking each dependent's other (incomplete) dependencies received nothing. None of the 3 tasks the engine completed this run happened to have any dependency themselves (they were root tasks), so this run doesn't show a *worker's prompt* containing real predecessor content — that's covered by the synthetic test above, not yet by a real run.
- [x] Recomputed on the fresh run: 6 of 37 leaf tasks (16%) now carry a populated `input_resource_id`, vs. 0% on every run before this fix (confirmed on two independent real ICAAP runs pre-fix).
- [x] Worker-prompt content, settled deterministically rather than by chance in a real run: `tests/test_worker_prompt_receives_handoff.py` calls the real `AIAgent._create_task_prompt` (the exact method the engine uses; no network call) with a predecessor's `Resource` and confirms the real content appears in the prompt text, the no-resources fallback message disappears, and — separately — that content over 200 characters is cut to exactly the first 200 (ML-006, now precisely characterized rather than just "unaddressed").
- [x] Ran 4 more real scenarios beyond ICAAP (`marketing_campaign`, `legal_contract_negotiation`, `tech_company_acquisition`, `pharmaceutical_product_launch`; random manager, gpt-4o-mini, 15 timesteps, seed 42): in every one, the completed task's output was correctly propagated to every pending task that depended on it (multiple dependents each, e.g. 1 completed task's output reached 3 pending tasks in `legal_contract_negotiation`), and never to an unrelated task. Consistent with ICAAP — generalizes beyond one scenario.

**Summary of Changes Done:**
- `engine.py` (`_execute_ready_tasks`, on task completion): after a task's `output_resource_ids` are updated, every other task whose `dependency_task_ids` includes it now gets those resource ids unioned into its own `input_resource_ids`. Relies on `get_ready_tasks()` having already expanded `dependency_task_ids` to leaf-task ids (it runs every tick before any task can complete), so a plain membership check is sufficient — no separate expansion logic needed.
- Added `tests/test_task_handoff.py` (4 tests, engine-level, using a stub worker agent and a minimal always-assign manager — no LLM calls, so free to run): resource id propagation, the worker's prompt actually receiving the content, no propagation to an unrelated task, and the audit's own "% of executions with ≥1 populated input" measurement style.
- Added `tests/test_worker_prompt_receives_handoff.py` (3 tests, calls the real `AIAgent` class directly, no network call): confirms real predecessor content reaches the actual prompt-building code path, and pins down the exact 200-character truncation point.
- Confirmed on 5 independent real scenario runs (ICAAP plus 4 more): 0% handoff on every pre-fix run, correct multi-dependent propagation on every post-fix run, across scenario types.

**What's left to verify:**
- Still haven't seen a real run where a *worker actually executes* with real predecessor content already in hand (as opposed to the content being correctly staged in `input_resource_ids` while the task waits to be picked up). Checked specifically why across 5 real runs: every task with 0 dependencies reached READY/COMPLETED quickly, while every task that had already received propagated input needed 3-8 *total* dependencies satisfied before becoming READY, and none of the runs' budgets (15-20 timesteps) got that far. This is a property of these workflows' dependency fan-in after their authored (non-decomposed) subtask trees are flattened, not a gap in the fix — the DoD item above (the real `AIAgent` prompt-builder test) already proves the only part of this that was actually in question: that real content, once staged, does reach the worker. Not pursuing further live real-run budget on this specifically.
- ML-008 (decomposition copies a parent's `input_resource_ids` at decomposition time, before they're wired) is a known, documented limitation of this fix — still untested. Initially suspected the 4 new runs' large subtask counts (21-40 leaves from workflows authored with only a handful of top-level tasks) might have exercised the LLM-driven `decompose_task` action, which would have let this be checked; re-checked and these are pre-authored nested subtask trees declared directly in each scenario's `workflow.py`, not runtime decomposition — so this remains open.
- ML-002 (scenario-level resource *declaration*) — this fix only wires outputs that already exist; scenarios that never produce `output_resources` for some tasks still hand off nothing. Not separately measured here.

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
