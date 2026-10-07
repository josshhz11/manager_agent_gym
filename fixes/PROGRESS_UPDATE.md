# Progress Update (07/10/2026) — Joshua

## Current State

Phase 1 (baseline reproduction) is scoped to a single-workflow smoke test of MA-Gym, confirming the environment and tooling work — not yet full baseline reproduction across all 20 workflows or challenge-task implementation. That full-scale work is intentionally paused, because a single real run (ICAAP, random manager, 20 timesteps) turned up MA-Gym problems severe enough that any numbers produced before fixing them would not be trustworthy: e.g. 0 of 47 tasks received any input from a predecessor, and the engine reported 3 completed tasks where only 1 held up under inspection.

So the current work is: **fix the specific MA-Gym bugs that would otherwise invalidate our own challenge tasks and metrics**, scoped narrowly (not a general MA-Gym repair), before Phase 1 proper resumes. We evaluated Ergon (the successor platform the supervisor's group is also considering) as a source of fixes to reuse; it solves one of our problems well (artifact handoff) and partially helps one more (judge context length), but leaves most of what we depend on unaddressed and has open bugs of the same shape in its own audit. I am fixing on my own fork of MA-Gym (`josshhz11/manager_agent_gym`, branch `fyp/ma-gym-fixes`) rather than migrating, porting Ergon's design where it's the best evidence available.

## Why the fixes are split into three phases

1. **Cheap and foundational first.** Phase A fixes don't depend on anything else and are small; every later result is read through them.
2. **Prerequisites before what depends on them.** Reassigning work after a worker leaves (Phase B) only means something if the new worker can see what the old one produced — so handoff comes before reassignment within Phase B.
3. **Closest to our primary tasks first.** I down-scoped to prioritize the preference-shift and team-churn challenge tasks (compound and cross-episode as later/stretch work). Phase A unblocks preference-shift, Phase B unblocks team-churn, Phase C (statistical trust) matters before comparing conditions across many seeds, not before a smoke test.

## Phase A — Make the recorded facts honest — **complete**

Question answered: *is what the simulator says happened actually what
happened?*

- **Fix 1 (NEW-001 — preference weight mutated in place).** The stakeholder's preference-change history was stored by reference, not copied, so applying a weight update at a later timestep silently rewrote what earlier timesteps show as having been true. This directly undermines the preference-shift task: you can't measure "did the manager react to an injected shift" if the shift's own history is corrupted. Fixed with one change (copy-on-read instead of mutate-in-place); verified against all 20 registered scenarios' real scripted preference schedules, not just one — every one fails on the original code and passes with the fix.
- **Fix 4 (fake completion — ML-003/009/011/015).** MA-Gym marks a task "COMPLETED" as soon as the worker call returns without crashing, with no check that the output is any good. Built an independent post-hoc verifier (`src/eval/completion_verifier.py`) that flags junk output (no output, dummy data, blank form fields, self-reported non-execution). On our real smoke run: engine reported 3/37 (8.1%) completed; the verifier's checks passed only 1/37 (2.7%). Both numbers are now logged side by side (`src/eval/run_metrics.py` writes `metrics.json` per our schema) — here I'm not silently replacing the engine's number, but instead reporting both.

## Phase B — Make the team actually work together — **complete**

Question answered: *can workers pass work to each other, and does the system
cope when the team changes?*

- **Fix 2 (no artifact handoff — ML-001/002) — complete, verified end-to-end.** `Task.input_resource_ids` was declared in the schema but nothing ever wrote it, so a downstream worker never saw what an upstream worker produced — confirmed directly on our smoke run (0 of 47 tasks had any input). This is the single bug most central to our stated research gap (coordination in ad hoc teams): there was no team coordination to measure if nothing was ever handed off. Fixed by propagating a completed task's outputs to its dependents' inputs via the existing (already-working) "must-wait-for" edges — much smaller than Ergon's full artifact-store redesign. Verification, in order of how directly it answers "does this actually work":
  - A free, deterministic test calls the real worker-agent class's own prompt-building method directly (no network call) and confirms a predecessor's real content lands in the prompt text — and pins down a related bug in passing (ML-006: that content is cut to the first 200 characters).
  - 4 engine-level tests (no LLM calls) all fail on the original code for the right reason and pass with the fix.
  - 5 independent real runs across different scenarios (ICAAP plus 4 more) all show correct propagation — 0% of tasks had any input before, every post-fix run shows predecessor content reaching every dependent and never an unrelated task.
  - Honestly flagged as still open: no run's timestep budget has gone deep enough into a dependency chain to show a worker *executing* with that content already staged (checked why — it's a property of these workflows needing 3-8 completed predecessors before a downstream task is even ready, not a gap in the fix, and the deterministic test already answers the part that was actually in question). Decomposition-time copying (ML-008) also remains untested — initially thought our 4 extra runs might have exercised it, checked the actual scenario code, and they didn't (pre-authored task trees, not LLM-driven decomposition).
- **Fix 3 (churn/reassignment integrity — ML-051/052/053) — complete, unit-verified.** When a worker left mid-task, MA-Gym had no retry path: the task sat forever pointing at an agent that no longer existed. The assign action also accepted obviously-invalid assignments (a composite task, a task with unmet dependencies, an already-completed task) as "success" with no validation. Fixed both: the engine now clears a not-yet-started task's assignment when its worker is gone (so it becomes reassignable), and `AssignTaskAction` now actually validates before accepting. 6 engine-level tests, all failing on the original code for the right reason and passing with the fix. One audit claim I checked and didn't fix: that rejections were hidden from the manager's next observation — traced the actual code path and confirmed they already reach the manager correctly via the returned result, independent of this fix; wrote a test proving that directly rather than assume the audit was right, and corrected the plan doc instead of "fixing" something that wasn't broken. Still open: no shipped scenario naturally schedules a worker leaving (all 20 are add-only), so the full team-churn-task-style real run (inject a leave/join at 25/50/75% depth, confirm nothing's orphaned at episode end) is deferred to when that challenge task's own injection harness is built — building a one-off version now would just duplicate that work, and the engine-level tests already exercise the same code paths.

## Phase C — Make the statistics trustworthy — **not started**

Question answered: *when we compare two managers, is the difference real or
just noise?*

- **Fix 5 (seed non-reproducibility — ML-092).** Identical seeds have produced meaningfully different runs, which would invalidate our planned paired significance testing. Plan: measure the noise empirically first (same seed twice, diff the outcome) before deciding whether a code fix is worthwhile or whether to fall back to an unpaired test with more seeds.
- **Fix 6 (dead scoring-aggregation formulas — ML-016/033/034).** The declared per-preference aggregation strategy (e.g. a hard-constraint zeroing rule) is silently bypassed by a different formula whenever any rubric results exist, so `constraint_violations` can't be trusted as-is. Plan: write our own scoped aggregation for just the constraint rubrics we use, rather than repairing MA-Gym's general engine. Until then, `constraint_violations` is logged as `null`, not a misleading number.
- **Fix 7 / Fix 8 (optional).** Judge context length/blind spots (only matters if we lean on LLM-judge rubrics for the Phase 2/3 ablation), and per-timestep evaluation cadence (only matters if we want within-episode degradation curves rather than end-of-episode comparison at three injection depths, which is our current design).

## Deliberately deferred (not part of this fix effort)

- Archive/restore fidelity (ML-070/072/073) — only matters for the cross-episode task, itself a stretch goal.
- LLM-judge scoring noise (ML-036) — a property of LLM judges, not a code bug; mitigated by using multiple judges/seeds and reporting agreement, not by a patch.
- A handful of lower-severity bugs not on our critical path (monitored, not pre-fixed).

## Next steps

1. **Phase C**, scoped to what's needed before running paired comparisons across seeds/conditions: Fix 5 (measure seed reproducibility) and Fix 6 (scoped constraint aggregation) are the two that matter regardless of which challenge tasks we run first; Fix 7/8 stay optional pending the ablation design.
2. **Once Phase C's two must-fix items are verified**, resume the actual research problem: full baseline reproduction (CoT/Random/Assign-All) on the preference-shift and team-churn challenge tasks specifically (our down-scoped priority order), since those are the two tasks Phases A and B unblock. Compound and cross-episode stay later/stretch. Building the team-churn task's own injection harness (deferred from Fix 3) happens as part of this, not before it.
3. Bring this fix list to the supervisor's group's platform-decision meeting — several of these (esp. Fix 2/3, the coordination-mechanics gap) are likely shared pain points for the other students' MA-Gym projects, and are candidates for the shared modular refactor rather than solo work.
