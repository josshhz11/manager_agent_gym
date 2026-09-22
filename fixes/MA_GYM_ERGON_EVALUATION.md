# MA-Gym Problems vs. Ergon: A Plain-Language Evaluation

Source material: `magym-undocumented-behaviour - Critical and High bugs.csv`
(the supervisor's group's audit of `external/manager_agent_gym`), plus a
direct read of the code and docs in `external/manager_agent_gym` and
`external/ergon` as cloned into this repo.

This is a general evaluation of MA-Gym's problems and how Ergon (its
proposed successor) addresses them — not scoped to this project's specific
challenge tasks or metrics design. Written in plain terms.

## MA-Gym's problems, in plain terms

Grouping the 32 audited bugs into five themes — this is what anyone using
MA-Gym as-is should know, regardless of what their project is about:

### 1. "Task completed" doesn't mean the work actually happened

The engine marks a task `COMPLETED` just because the worker's function call
didn't crash — it never checks whether the output is actually any good. In
practice this let through unfilled templates (`[Date]`,
`"Seller Legal Name"`), tasks marked done with zero output and no assigned
worker, and tasks the worker itself said it didn't execute. Result: the
audit measured 42–66% of "completed" handoffs as actually inadequate,
while the tool reports ~100% completion. **The headline success number
lies.**

**Does this affect my project?** Yes, directly and structurally: our
`goal_completion_rate` field (docs/metrics.md's logging schema) is defined
as "% of task graph nodes completed," which is exactly the number this bug
group inflates. This isn't scoped to one challenge task — it's a floor
confound under all four (preference-shift, team-churn, compound,
cross-episode), because every one of them reports this same metric. Any
comparison we draw between conditions (CoT vs. our method, Phase 2 vs.
Phase 3 vs. combined) is only as trustworthy as this number, so this bug
group has to be dealt with before any result using `goal_completion_rate`
can be reported as-is.

### 2. Workers never actually see each other's work

The mechanism meant to pass one worker's output to the next worker was
never wired up — across all 20 official example runs, 0% of tasks ever
received input from a predecessor. Every "workflow" that looks like a
pipeline is actually a set of independent tasks running in parallel with
no real handoff between them. This is the single worst bug — it means the
core thing MA-Gym claims to simulate (a team coordinating on shared work)
has never actually been exercised in any published result.

**Does this affect my project?** Yes — this is the single most central bug
to our research gap. Our project's stated challenge (3) is coordination and
planning in ad hoc teams, and the team-churn task specifically tests
whether a reassigned/new worker can pick up where a departed one left off.
Neither of those means anything if no worker ever sees a predecessor's
output in the first place — we'd be measuring reassignment logic operating
on a coordination mechanism that has never actually existed in any run.
It also undermines the compound task, since that task layers churn on top
of preference shift and inherits team-churn's dependency on real handoff.

### 3. The scoring system (the "judge") is broken in many independent ways

An AI model grades how well each task was done, but: it only ever sees a
300-character preview of each artifact (so it's grading a snippet, not the
real deliverable); several of its scoring formulas are dead code that
silently fall back to a different, more forgiving formula; some entire
scoring categories always return zero no matter what; the judge is
sometimes blind to the exact evidence (messages, task history) it's
supposed to be looking at; and re-running the same grading twice on
identical input can flip a score from 1.0 to 0.0. **You cannot trust a
"condition A beat condition B" claim from this scoring pipeline without
independent verification.**

**Does this affect my project?** Yes, on two fronts. First,
`constraint_violations` and any rubric-scored share of `goal_completion_rate`
inherit these formula/preview/blind-spot bugs directly. Second, and more
specific to our design: docs/metrics.md's Evaluation Question 3 (ablation —
Phase 2 selector vs. Phase 3 memory vs. combined) and Question 4
(robustness to corrupted retrieval) both depend on detecting *small*
deltas between conditions — a contribution margin, a performance delta
under corruption. Judge noise large enough to flip which condition "wins"
(the ML-036 finding: an arm gap of 0.042 compressed to 0.003 under one
judge) is exactly the kind of noise that can hide or fabricate the small
effects those two questions are built to measure. Our ablation is the part
of the project most at risk from this bug group.

### 4. Runs aren't reproducible

Setting the same random seed twice gives meaningfully different runs (up
to double the number of actions taken). Any two "identical" experiments
are actually two different, uncontrolled experiments — this undermines any
comparison that depends on holding conditions constant.

**Does this affect my project?** Yes, at the level of our whole statistical
design rather than any one task. docs/metrics.md's Statistics section
specifies **paired** significance testing (e.g. paired t-test) between
method and baseline, run across multiple seeds. "Paired" specifically means
comparing two conditions on matched random draws so the treatment is
isolated from other noise — that's the entire point of controlling the
seed. If seed doesn't reproducibly control a run (ML-092: identical seeds
producing 96 vs. 51 manager calls), our "pairs" are actually two
independent, unpaired draws, which invalidates the paired test's variance
assumption and risks both false positives and false negatives on every
comparison we report, in every challenge task.

### 5. Saved/archived runs can't be reliably replayed or trusted

Restoring a saved run drops tasks, descriptions, and scoring
configuration; by default the tool doesn't even save enough detail to
re-score an old run without paying to re-run the whole thing from scratch;
and only the last 10 messages of a conversation are ever saved, so most
communication history is lost forever.

**Does this affect my project?** Yes, specifically for the cross-episode
task (Phase 3). That task is the direct test of the "continual" claim in
our project title — it depends on faithfully reconstructing what happened
in earlier episodes so memory retrieved from them means something. A lossy
restorer that drops tasks, descriptions, and preference/evaluator
configuration (ML-070), combined with timestep logging being off by
default (ML-072) and only a 10-message communication window surviving
(ML-073), means the exact state Phase 3 would need to read back from a
prior episode is the state most likely to have been silently dropped. Note
this is *separate* from Phase 3's own memory mechanism, which doesn't
exist yet on either platform (see the new section at the bottom) — this
bug group is about whether a *single* past episode's record is even
trustworthy once saved, which any memory mechanism we build would depend
on.

## What Ergon does about each of these

| Problem | Ergon's status | Evidence | Actionable items |
|---|---|---|---|
| **Fake completion / no quality check** | **Not addressed** that could be found | No doc or RFC targets this specifically | **1. Integrate:** nothing to port — this bug is orthogonal to what Ergon has built so far, so a platform migration alone buys us nothing here. **2. Further changes needed regardless of platform:** we have to build our own independent completion-verification layer, scoped only to what `goal_completion_rate` needs — e.g. flag disagreements between a worker's own stated status and the engine's `COMPLETED` marking (the ML-011 pattern), and/or a deterministic checklist check against task acceptance criteria for task types where that's feasible. Small and ours to own; not something either platform will hand us. |
| **No real handoff between workers** | **Fixed, and well-designed** | Old broken mechanism was deleted outright and replaced with a real system: workers write files to a proper storage location, get a unique ID, and the next worker reads them back by name — with the old "silent data loss" bug no longer being *possible* to reintroduce (`docs/architecture/cross_cutting/artifacts.md`) | **1. Integrate:** if we stay on MA-Gym, port the pattern (not necessarily the code) — replace the never-populated `input_resource_ids` field and the `decomposition/service.py:80` empty-list copy with a real artifact store + by-ID lookup, modelled on `docs/architecture/cross_cutting/artifacts.md`. If the platform decision favors Ergon, this fix comes for free by migrating onto `WorkflowGraphRepository`/the blob store directly. **2. Further changes needed either way:** the engine fix alone isn't sufficient — every scenario's `workflow.py`/`team.py` still has to be authored to actually declare resource wiring (ML-002 is the scenario-side half of this bug); and we still have to independently verify/test worker join-leave reassignment logic against the new artifact model ourselves, since neither platform documents that path (see the team-churn "Does this affect my project?" note above). |
| **300-char judge preview** | **Fixed, in at least one place** | A 2026-04-28 implementation plan (`docs/superpowers/plans/2026-04-28-evaluation-resource-context-and-scoring.md`) makes the judge read up to 30,000 real characters of the actual final output instead of a fixed 300-char snippet — but so far this is built for one specific evaluator type (ResearchRubrics), not proven to apply everywhere yet | **1. Integrate:** port the full-artifact-read pattern (`CriterionRuntime.get_all_files_for_task()`) into whichever rubric evaluators score our goal-achievement/preference-alignment metrics. **2. Further changes needed:** this fix is proven for exactly one evaluator type in Ergon — we cannot assume it generalizes to our rubrics; budget time to verify (or re-implement) the same full-context read for every evaluator type our four challenge tasks actually use, not just the one Ergon happened to fix first. |
| **Dead/broken scoring formulas, always-zero categories** | **Partially — a different but related bug already found and still open** | Ergon's own internal audit (`docs/integration-spec/4-violated-assumptions.md`, item I) already found "a completed run can show zero scores with no warning that anything went wrong" — same *shape* of silent-failure bug, not yet fixed | **1. Integrate:** nothing to port — this is unfixed on both platforms, and migrating to Ergon trades one unfixed instance of the bug for another of the same shape. **2. Further changes needed regardless of platform:** we have to independently audit whichever aggregation code we end up running (declared vs. actually-executed callables, e.g. MIN/MAX/PRODUCT/`zeroing_gate` on MA-Gym, or the silent-zero path Ergon's own audit flags) and add our own regression test asserting the aggregation we *think* is running is the one that actually runs, before trusting `constraint_violations`. |
| **Judge blind to the real evidence it's supposed to see** | **Partially fixed, partially still open** | Fixed for one evaluator (now reads real files). But Ergon's own audit *also* found a live case (item C) where the evaluator is currently given an empty/blank task description instead of the real one — same category of bug, different spot, unfixed | **1. Integrate:** same context-assembly fix as the 300-char row — port it for the rubric types we rely on. **2. Further changes needed:** because Ergon's own audit found a *new* instance of this same bug class (empty task descriptions reaching a different evaluator), we cannot treat "Ergon fixed the judge context problem" as true in general — we need to independently check, per rubric type we use, that the evidence the rubric declares as `required_context` is actually what reaches the prompt (the same audit method the supervisor's group used for MA-Gym's `ML-023`, applied to whichever platform we land on). |
| **Reproducibility (seeds don't work)** | **No evidence found either way** | No Ergon document addressing seeding/reproducibility was found | **1. Integrate:** nothing to port — there's no fix to bring over. **2. Further changes needed regardless of platform:** treat seed reproducibility as untrusted until we've empirically tested it ourselves — run the same seed twice under whichever platform we choose and diff the trajectories/action counts, the same way the audit surfaced `ML-092`. If it's not reproducible, our paired-test design (docs/metrics.md) needs either a fix at the seed-threading level (module RNG + provider param + whichever call path is in use) or a fallback to an unpaired test with a larger seed count to recover power — decide which before running the full sweep, not after. |
| **Archived runs can't be faithfully replayed** | **No evidence found either way** | No doc found addressing snapshot/restore fidelity | **1. Integrate:** nothing to port. If we do migrate to Ergon, its Postgres-backed persistence layer (`docs/architecture/04_persistence.md`) is a plausible foundation to build a restore path on, but it isn't one today — no doc claims episode-level restore fidelity, and it wasn't built for that use case (it feeds RL trajectory extraction for gradient-based training, not episodic recall for a frozen manager). **2. Further changes needed regardless of platform:** don't attempt to fix general-purpose restore — that's a large undertaking neither platform has finished. Instead, scope our own snapshot/restore to exactly what Phase 3's cross-episode task needs (whatever state our chosen memory mechanism reads), which is a much smaller, purpose-built problem than fixing `WorkflowStateRestorer` in general. |
| **Ergon's own reliability** | **Worth knowing** | Ergon's own test-coverage audit (`docs/integration-spec/1-audit.md`) found 3 tests that have never actually run (a `testresolve_*` vs `test_*` typo hid them from the test runner since they were written), and most of its "integration tests" are actually fully fake/mocked | **1. Integrate:** N/A — this is a risk factor about Ergon itself, not a fix to bring into MA-Gym. **2. Further changes needed if we adopt Ergon:** don't take "Ergon's test suite passes" as evidence of correctness for the pieces we depend on — re-run and spot-check the specific tests covering artifact handoff and evaluation context assembly ourselves (the two areas we're relying on), and budget extra verification time into the platform decision rather than assuming younger-but-audited beats older-and-broken by default. |

## Bottom line, simply put

Ergon fixes the single worst problem (workers never seeing each other's
real output) properly — not a patch, an actual redesign, with the old
broken path deleted so it can't come back by accident. It's also making
real progress on the judge-only-sees-a-snippet problem, at least in the
one place it's been applied so far.

But it hasn't touched two of MA-Gym's other big problems (completion being
a fake quality signal, and reproducibility), and — this is the important
part — **its own internal documentation already lists new bugs of the
exact same shape** as MA-Gym's worst ones (silent zero-scores with no
warning, evaluators being fed the wrong/empty data). So it's not "solved,
evaluation is broken," it's "fixed some, found more of the same kind,
hasn't fixed those yet." It's also simply younger and less tested than
MA-Gym — its own audit says as much.

## How we should approach tackling MA-Gym's problems for this project

Ergon refactoring is not a single yes/no switch that resolves this list —
the table above shows it resolves one problem well, partially helps two
more, and leaves the rest (including some of the ones most central to our
specific challenge tasks) either untouched or merely "not yet known to be
broken." The practical approach is to triage by *what our project actually
depends on*, not by chasing full parity with either platform, and to
separate "port a fix" from "build it ourselves" per problem rather than
assuming a platform decision settles all of them at once.

**Tier 1 — block on these; fix before trusting any result, regardless of
platform choice.**

- **NEW-001** (weight-vector mutation-in-place). Confirmed against the real
  code at `external/manager_agent_gym/manager_agent_gym/core/workflow_agents/stakeholder_agent.py:191-274`
  — a small, well-isolated patch (deep-copy in `get_preferences_for_timestep`,
  or build fresh `Preference` instances in `apply_weight_update` instead of
  mutating the looked-up ones), *not* something that requires migrating off
  MA-Gym. Ergon doesn't fix this either way. If MA-Gym is kept, patch it
  directly; if Ergon is chosen, verify the equivalent preference-update path
  doesn't share the same aliasing pattern before assuming it's clean.
- **Independent completion verification** for `goal_completion_rate`. Ergon
  doesn't solve this. Scope: a cheap layer (worker self-report vs. engine
  status cross-check, full-artifact rubric check instead of a preview)
  built once and reused across all four challenge tasks, since all four
  report this metric.
- **Empirical seed-reproducibility test.** Before relying on the paired
  significance-testing design in docs/metrics.md, run the same seed twice
  under whichever platform is chosen and diff the resulting trajectories.
  Neither platform gets a pass on this by default.

**Tier 2 — port from Ergon where the platform decision favors it; patch
directly on MA-Gym otherwise.**

- Artifact handoff (ML-001/002/008) and the 30k-char judge context fix are
  the two places Ergon has done real, reusable work. If the supervisor's
  group's platform decision lands on Ergon, these come largely for free by
  building on `WorkflowGraphRepository`/the blob store and the
  evaluation-resource-context pattern. If it lands on MA-Gym instead, these
  are the two fixes worth porting *the pattern* of (not necessarily the
  code) directly into `manager_agent_gym`, since they're the best-evidenced
  fixes available anywhere in either codebase.
- These two fixes are also the ones our team-churn task depends on most
  directly (real handoff) and our ablation depends on partially (judge
  context length, alongside Tier 3's noise problem below).

**Tier 3 — open on both platforms; build a minimal, task-scoped version
ourselves rather than attempting a general fix.**

- Dead/broken scoring formulas, judge blind spots elsewhere, and judge
  sampling noise (ML-036) all fall here. None of these are solvable by a
  platform migration — Ergon's own audit shows the same *shape* of bug
  recurring in its own code. The right scope for an FYP is not "fix the
  evaluation pipeline" (a multi-month effort neither team has finished) but
  "build just enough independent verification to trust the specific
  metrics our four challenge tasks report" — e.g. multi-judge agreement
  checks, a regression test asserting our aggregation formula is the one
  actually executing, per-rubric spot checks that declared `required_context`
  reaches the prompt.
- Archive/restore fidelity falls here too, scoped down specifically to
  what Phase 3's cross-episode task needs to read back — not a general
  `WorkflowStateRestorer` fix.

**Tier 4 — track as risk, not an action item.**

- Ergon's own reliability (untested/mocked integration tests). This
  doesn't get "fixed" by us; it's a factor to weigh in the platform
  decision itself, and if Ergon is chosen, a reason to independently
  re-verify the specific tests covering whatever we depend on rather than
  trusting a green test suite at face value.

**Sequencing relative to the down-scoping discussion:** Tier 1 items are
prerequisites for *any* challenge task's results being trustworthy, so
they come first regardless of which tasks we keep in scope. Given the
recommendation to prioritize team-churn and preference-shift as primary
tasks, Tier 2's handoff fix should be prioritized next (it's team-churn's
main dependency), followed by verifying NEW-001's fix actually restores
preference-shift's core measurement. Tier 3 work should be scoped strictly
to what those two tasks' metrics need — not attempted in general — and
cross-episode's Tier 3 item (archive fidelity) only needs to be tackled if
and when cross-episode is picked up as a stretch goal.
