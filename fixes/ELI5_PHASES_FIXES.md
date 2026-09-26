# ELI5: The Phases and Fixes

A plain-language guide to what is wrong with MA-Gym, why it matters for this
project, and what each fix does. It explains; it does not track progress. For
status, evidence and the Definition of Done for each fix, see
[PROPOSED_FIXES_SUMMARY.md](./PROPOSED_FIXES_SUMMARY.md). For the full list of
bugs, see [MA_GYM_ERGON_EVALUATION.md](./MA_GYM_ERGON_EVALUATION.md).

How much has been checked, per fix:

- **Phase A (Fixes 1 and 4): built and tested.** What is written below is what
  happened.
- **Phases B and C (Fixes 2, 3, 5, 6, 7, 8): not built yet.** What is written
  below is the plan. Where I read the code myself, it says *(checked in the
  code)*. Where I am relying on the audit's claim, it says *(audit's claim, not
  re-checked)*.

---

## The big picture

**What the project is doing.** You are building a smarter "manager" (an LLM)
that runs a team of AI workers on a project, and can adapt when things change:
the boss's priorities shift, or a worker joins or leaves. To show it works, you
run experiments comparing your manager to simpler ones, and measure who does
better.

**The catch.** Those experiments only mean something if the *measuring stick*
and the *simulation itself* are honest. MA-Gym, the simulator, has bugs that
make both unreliable. The fixes here don't make your manager smarter. They make
the test fair.

**Three kinds of dishonesty, three phases:**

| Phase | Plain description | The question it answers |
|---|---|---|
| **A** | Make the recorded facts honest | "Is what the simulator says happened actually what happened?" |
| **B** | Make the team actually work together | "Can workers pass work to each other, and does the system cope when the team changes?" |
| **C** | Make the statistics trustworthy | "When I compare two managers, is the difference real or just luck and noise?" |

### Why the fixes were split into these phases

The order follows three rules:

1. **Cheap and foundational first.** Phase A fixes don't depend on anything
   else, they are small, and every later result is read through them. There's no
   point building on numbers you can't trust.
2. **Things that unblock other things go before the things they unblock.**
   Reassigning work after a worker leaves (Fix 3) only helps if the new worker
   can see what the old one made (Fix 2). So Fix 2 comes first.
3. **Closest to the project's priorities first.** Preference-shift and
   team-churn are the primary tasks, so Phase A (which preference-shift needs)
   and Phase B (which team-churn needs) come before the statistics work in
   Phase C. Phase C matters before you *compare* conditions across many seeds,
   not before a smoke test.

---

# Phase A: make the recorded facts honest

**Status: built and tested.**

Two fixes, both about a number or a record that was quietly wrong. Fix 1 makes
the *input* honest (the boss's priorities really change when scheduled). Fix 4
makes the *output* honest ("done" means done).

## Fix 1: the boss's changing priorities (NEW-001)

### 1. The problem, and how it fits

In the simulation there's a **stakeholder** (the boss) with a priority list,
like "quality 40%, speed 10%, cost 10%...". Partway through, the boss changes
their mind ("now compliance matters most"). That's your **preference-shift
task**: does the manager notice and adapt?

The bug made it as if the boss had **always** wanted the new priorities. The
manager never saw a *change*, so there was nothing to adapt to. Your first
challenge task was untestable.

### 2. What exactly went wrong

**Analogy:** the boss keeps a diary with one page per change. "Day 0:
priorities A. Day 10: priorities B."

When it wrote the Day 10 page, the code did this:
1. Look up the latest page before Day 10 (the Day 0 page).
2. **Erase and rewrite the numbers on that same page** to make B.
3. Save a copy of it as the Day 10 page.

So the Day 0 page was silently overwritten, and the diary said "B" everywhere.

**Why it did that:** in Python, when you "look something up" you often get the
*original object*, not a photocopy. Editing it edits the original. On top of
that, the wrapper class that holds the priority list also re-normalizes numbers
in place. So the code was written as "edit the numbers", which is natural when
you don't think of the history as sacred.

**Real evidence:** in ICAAP, `quality` on Day 0 should be 0.429. On the original
code it read 0.286, which is the Day 10 value. It happened in all 20 registered
scenarios.

### 3. Where it sits in the flow

```
BEFORE   scenario says: shifts at t=0, 10, 30, 60
         run_examples loads them one by one into the boss's diary
              |
DURING   apply_weight_update (t=10):
   (bug)   grabs the t=0 page -> edits its numbers in place -> saves a copy as t=10
              |                          ^ the bug is here
AFTER    every step the engine asks the diary "what does the boss want at step t?"
         -> the manager's observation and the final score both get the answer
         -> for t<10 the answer is already B, not A
```

**The fix** is at the "DURING" step, in one place: `name_to_pref` is now built
from `preference.model_copy()`, so it **photocopies each sticky note before
editing**. The edits land on the photocopies, the old pages are never touched,
and the Day 0 page stays A.

**Why it works:** the bug only existed because edits reached the original
pages. Now they can't. Tests confirm it: every timestep in all 20 scenarios sees
exactly the priorities scheduled for it, and the same tests fail on the old
code.

## Fix 4: "completed" doesn't mean done (ML-003 / 009 / 011 / 015)

### 1. The problem, and how it fits

MA-Gym reports "X% of tasks completed". Your metric `goal_completion_rate` is
that number. **It's used in all four challenge tasks and every comparison.**

But "completed" doesn't mean the work was done. It's like a class where handing
in *anything* gets a "homework done" tick, even a blank worksheet. If every
manager's score is inflated by junk, you can't tell which one truly did better.
Worse, a manager whose workers turn in junk quickly could look great.

### 2. What exactly went wrong

In `engine.py` line 639, the check is roughly `if the worker's call didn't
crash: mark COMPLETED`. The comment beside it says the validation system was
removed. Nothing reads the output.

**How it came about:**
- The validation was removed, so nothing checks the output.
- The worker is an LLM, and LLMs almost always return *something*.
- The worker also never sees earlier tasks' output (the handoff bug, Fix 2), so
  it invents generic content.

**Real evidence:** in the smoke run, the engine said 3 tasks completed. Two of
them were junk:
- one handed in a model inventory with dummy rows (`ExamplePD01`, `John Doe`,
  `Jane Smith`);
- one handed in a blank form (`- Version:`, `- Date:`, an empty column).

### 3. Where it sits in the flow

```
BEFORE   manager assigns a task -> worker LLM runs -> returns some output
              |
DURING   engine sees "no crash" -> marks COMPLETED. Never opens the output.
   (bug)      ^ the bug is here
              |
AFTER    summary counts "completed / total" -> goal_completion_rate = 8.1%
         (a judge also scores it, but that has its own problems, see Fix 7)
```

**The fix** doesn't touch the engine. It's a **second, independent reader that
runs after the experiment**. `src/eval/completion_verifier.py` opens the final
summary and, for every task the engine called "completed", asks:
- Was an agent assigned, and did it start?
- Is there output, and is it non-empty?
- Does it say "not executed"?
- Does it contain placeholders, dummy names or "to be populated"?
- Does it have blank form fields?

The smoke run gives 3 completed by the engine and 1 passing all checks. So
`goal_completion_rate` is 8.1% and `verified_completion_rate` is 2.7%, and you
log both. `src/eval/run_metrics.py` writes them to `metrics.json`. (Both numbers
count leaf tasks only, because parent tasks that were split into subtasks have
no output of their own.)

**Why it works:** it's an independent check on the actual content. **Why it
stays outside the engine:** it keeps the engine's original number for
comparison, and it won't clash with the group's shared refactor.

### Common questions about Fix 4

**Does it change a task's status from COMPLETED to something else?** No. The
engine still says COMPLETED, and the manager still sees COMPLETED during the
run. The verifier is a separate check made after the run, so it produces a
second number and does not rewrite any status.

**Is `verified_completion_rate` used instead of `goal_completion_rate`?** No,
both are logged. Use the verified one when you make a claim about how much work
got done, and keep the raw one so you can compare with published MA-Gym
numbers.

**What it can't do.** It detects junk. It doesn't stop the manager or the
workers from producing it, and it can't warn the manager mid-run. Its checks are
heuristics tuned on one run of three completed tasks, so they will miss some
things and might flag some fine output.

### How Phase A connects

- **Fix 1** makes the *input* to your experiment honest.
- **Fix 4** makes the *output* honest.
- The junk that Fix 4 detects exists mainly because workers never see each
  other's output. That's Fix 2, the first fix of Phase B.

---

# Phase B: make the team actually work together

**Status: not started. What follows is the plan.**

Phase A made the records honest. Phase B is about the *simulation itself*: does
a team of workers actually behave like a team? Your **team-churn task** ("a
worker leaves or joins, and the manager must reassign") and your **research
gap** (coordination in ad hoc teams) both assume workers can hand work to each
other and that the system copes when the team changes. Right now neither is
true.

**Why Fix 2 comes before Fix 3:** reassigning a task to a new worker is only
meaningful if that worker can see what came before. Testing reassignment while
workers can't see each other's output would test the wrong thing.

## Fix 2: workers never see each other's work (ML-001 / 002 / 008)

### 1. The problem, and how it fits

**Analogy:** a relay race where nobody ever hands over the baton. Every runner
runs alone, and the "team" is just several people running separately.

In MA-Gym, task B can be set to wait for task A, but B's worker never receives
what A produced. Your research gap is *coordination in ad hoc teams*, and the
team-churn task asks whether a new worker can pick up where the old one left
off. If there's nothing to pick up, that question has no meaning.

**Real evidence:** in the smoke run, 0 of 47 tasks had any input resources, even
though 3 tasks finished and produced 6 resources. The workers invented generic
templates, which is exactly what Fix 4 caught.

### 2. What exactly went wrong

Each task carries two separate lists *(checked in the code, `schemas/core/tasks.py`)*:
- `dependency_task_ids`: "wait for these tasks to finish". This one works.
- `input_resource_ids`: "read these outputs before you start". This one is
  never filled in.

Nothing in the code writes to `input_resource_ids`. The only things that touch
it are a field declaration, one read in `engine._get_task_resources`, and one
copy in `decomposition/service.py` that copies a parent's list, which is empty.
So the worker's prompt says "No specific input resources provided".

**How it came about:** the "wait for" wiring was needed for scheduling, so it
was built. The "here is what to read" wiring was declared in the schema and
never finished. The example scenarios don't declare it either (ML-002), so even
a correct engine would have nothing to hand over.

### 3. Where it sits in the flow

```
BEFORE   task A runs and finishes; engine stores A's outputs
         (engine.py, on completion: output_resource_ids updated)
              |
DURING   task B becomes ready (A is done).
   (bug)   engine calls _get_task_resources(B) -> reads B.input_resource_ids
              |                                 ^ always empty
AFTER    B's worker prompt has no output from A -> B's worker makes something up
```

**The planned fix:** at the moment A completes, look for every task that lists A
in `dependency_task_ids`, and add A's output ids to that task's
`input_resource_ids`. Then `_get_task_resources(B)` finds them.

**Why it should work:** it reuses "who waits for whom", which already exists and
works. It's a few lines instead of Ergon's full artifact-store redesign.

**Honest limits:**
- Subtasks created by decomposition copy their parent's (empty) list when
  they're created, so this won't reach them (ML-008).
- Workers cut each input to 200 characters in their prompt (ML-006), so they'd
  see a headline, not the full document *(audit's claim, not re-checked)*.
- The audit tried wiring real artifacts in and found it recovered **0.0
  percentage points** of adequacy (ML-010) *(audit's claim, not re-checked)*.
  So this fix makes coordination *possible*. It doesn't promise better scores.
  Scenarios must also declare their resource wiring.

## Fix 3: when the team changes, the system doesn't cope (ML-051 / 052 / 053)

### 1. The problem, and how it fits

**Analogy:** a restaurant kitchen. A cook leaves. The orders assigned to him sit
on the counter forever, and nobody says so. The waiter (your manager) says "give
this order to cook X" and the system answers "done!", even if the order was
already cooked, or can't be cooked yet. It never says "no", and it never says
"that order failed".

Your **team-churn task** asks whether the manager adapts when a worker joins or
leaves. If the simulator silently swallows reassignments, you're measuring "does
the system stall?", not "does the manager adapt?".

### 2. What exactly went wrong

Four separate weaknesses:

- **(a) Leaving strands work.** *(Checked in the code.)* `remove_agent` deletes
  the worker from the registry, and each step the engine mirrors the registry
  into the workflow's agent list, deleting anyone who is gone. The engine only
  starts a task if it can find the assigned worker in that list. So tasks
  assigned to a departed worker that haven't started yet are never started,
  are never marked failed, and the manager isn't told. (A task that is already
  running keeps running to the end.) Note also
  that the registry *supports* removals but no scenario uses them (ICAAP's team
  timeline only ever adds workers), so this path is probably barely exercised.
  I've reasoned this from the code and haven't run it, so the Fix 3 tests will
  confirm it.
- **(b) Assigning never says no.** *(Checked in the code,
  `AssignTaskAction.execute`.)* It checks only that the task id and agent id
  exist, then sets the assignee and returns `success=True`. That includes a
  parent task, a task still waiting on dependencies, or one that is already
  running or complete.
- **(c) A rejection isn't recorded on the action.** *(Checked in part.)* In the
  unknown-id branches, the returned result says failure, but the action's own
  `success` field isn't updated. The audit says this hides rejections from the
  manager's next observation *(audit's claim, not re-checked)*.
- **(d) FAILED is a dead end.** *(Checked in the code.)* A task is ready when
  all its dependencies are in the "completed" set, and a failed task never
  enters it. So anything downstream of a failure waits forever, with no retry.

**How it came about:** the system was written for the happy path (workers stay,
assignments are sensible). The manager is an LLM, so the checks were left to it.

### 3. Where it sits in the flow

```
BEFORE   manager assigns tasks; workers start on them
              |
CHURN    an injected event removes a worker from the registry
              |
DURING   tasks assigned to that worker: engine can't find them -> skips silently
   (bug)   manager assigns again -> accepted no matter what (no validation)
              |                             ^ (a), (b), (c) are here
AFTER    stuck tasks; the manager can't tell why; downstream tasks wait forever
```

**The planned fix:**
1. When a worker is removed, put its not-yet-finished tasks back to READY and
   tell the manager.
2. Make `AssignTaskAction` refuse impossible assigns (a parent, a task with
   unmet dependencies, a non-assignable state) with a real failure message.
3. Write that rejection back so the manager sees it next turn.

**Why it should work:** it turns silent no-ops into visible errors and gives
stranded tasks a way out.

---

# Phase C: make the statistics trustworthy

**Status: not started. What follows is the plan.**

Phases A and B make single runs honest. Phase C is about *comparing* runs. Your
evaluation compares managers across many seeds with paired significance tests
(`docs/metrics.md`). That needs (1) the same seed to give the same run, (2)
scores that measure what they claim, and (3) judges that see what they're
supposed to see. These fixes matter before the big comparison, not before a
smoke test, which is why they come last. Two of the four are optional.

## Fix 5: the same seed doesn't give the same run (ML-092)

### 1. The problem, and how it fits

**Analogy:** a "seed" is a recipe for shuffling a deck: the same recipe should
give the same shuffle. If two runs with the same seed produce different games,
then "method A vs method B" is really "one random game vs another random game".

Your statistics plan is a **paired** test: run both methods under the same
conditions and compare. That only works if "same conditions" really means the
same. The audit saw the same seed give 96 vs 51 manager calls *(audit's claim,
not re-checked)*.

### 2. What exactly went wrong

- The seed is passed to the LLM API call as a hint *(checked in the code,
  `llm_interface.py`)*. Providers treat it as best-effort, not a guarantee.
- Local random choices are seeded (the engine calls `configure_seed` on the
  manager and the stakeholder) *(checked in the code)*. LLM answers still vary.
- The audit adds that worker calls go through a path the seed never reaches, and
  that timing and the order in which asynchronous tasks finish add more
  variation *(audit's claim, not re-checked)*.

**How it came about:** "seed=42" was added everywhere as a habit, but the
system depends on an outside LLM service that can't promise identical answers.

### 3. Where it sits in the flow

```
BEFORE   run starts with seed=42; local random generators are seeded
              |
DURING   each LLM call carries a seed hint, but the LLM may still answer
   (bug)   differently; async tasks may finish in a different order
              |
AFTER    two "identical" runs diverge -> a paired comparison is invalid
```

**The planned fix:** no code can force a cloud LLM to be deterministic, so the
plan is to **measure first**: run the same seed twice and compare. If the runs
differ, either reduce the sources of variation, or switch to an *unpaired* test
with more seeds. The choice is made before the big sweep, not after.

**Why it should work:** it doesn't pretend the problem is solvable. It measures
the noise and picks a statistical method that can live with it. I haven't run
the same-seed test yet.

## Fix 6: the scoring formulas ignore their own settings (ML-016 / 033 / 034)

### 1. The problem, and how it fits

**Analogy:** a report card. Each subject (quality, compliance, cost...) has
several mini-tests. The designers said how to combine them: an average for some,
"fail one hard rule and the subject scores zero" for others. But the combining
step ignores those instructions and always does "add up the points". So a hard
rule you break doesn't zero anything.

Your `constraint_violations` metric, and any preference-alignment score, depend
on this combining step. If breaking a hard constraint doesn't change the score,
you can't measure constraint adherence.

### 2. What exactly went wrong

*(Checked in the code, `validation_engine.py` around line 213.)* The code says:
if there are any rubric results, compute `sum(score) / sum(max)`; **only if
there are none**, use the aggregation strategy the evaluator declared. So the
declared strategy (minimum, maximum, hard-zero gate...) is effectively dead
code. The audit adds that a deterministic hard-constraint checker exists but is
never called (an LLM judges instead), and that some categories score zero in
every run *(audit's claim, not re-checked)*.

**How it came about:** a simpler "weighted by max" formula replaced the
strategy dispatch, and the old dispatch was left behind as a fallback that
almost never runs.

### 3. Where it sits in the flow

```
BEFORE   the judge scores each mini-test (rubric)
              |
DURING   combine step: "if any results: sum/sum-of-max"
   (bug)   the evaluator's declared strategy is skipped
              |
AFTER    weighted_preference_total etc. come from that formula;
         a hard-constraint violation can't zero anything
```

**The planned fix:** don't repair MA-Gym's general scoring engine. Write our own
combining function for exactly the constraint rubrics this project uses, test it
on made-up cases where the answer is known ("all rules broken -> zero"), and use
it from `src/eval/`. Until then, `constraint_violations` is logged as null.

**Why it should work:** it's small, testable in isolation, and independent of
the shared refactor.

## Fix 7 (optional): the judge reads only the first 300 characters (ML-007 / 023)

### 1. The problem, and how it fits

**Analogy:** a teacher marks an essay after reading only the first 300
characters, and isn't given the supporting notes the marking scheme says they
should have. That matters only if you use LLM-judge scores, for example to
compare Phase 2 against Phase 3 in your ablation.

### 2. What exactly went wrong

- Each resource is shown to the judge cut to 300 characters *(checked in the
  code: `max_preview_chars: int = 300` in `workflow.py`)*.
- Rubrics declare extra context they need (messages, manager actions, past
  preferences). The audit says several kinds are never filled in *(audit's
  claim, not re-checked)*.

**How it came about:** short prompts are cheap, and the context fields were
declared but never all wired up.

### 3. Where it sits in the flow

```
BEFORE   the run ends; a judge scores each rubric
DURING   the judge's prompt is built from a summary where each output is cut to
   (bug)   300 chars, with the declared context missing
AFTER    the judge scores the plan, not the product
```

**The planned fix:** only for the rubric types we keep, show the whole artifact
(Ergon does this for one evaluator type, up to 30,000 characters) and fill in
the declared context. Test it by logging prompt length and asserting the context
fields are populated.

**An awkward side effect to know about (ML-036, audit's claim, not
re-checked):** the short preview accidentally keeps judge scores *stable*, by
hiding information. Showing the full artifact makes scores *noisier*. That's
part of why this fix is optional, and why judge noise is handled by using
several judges and seeds, not by a code fix.

## Fix 8 (optional): grading only at the end (ML-025 / 026)

### 1. The problem, and how it fits

**Analogy:** a report card issued only at the end of the year, not weekly. The
simulator can grade at every step but is set to grade only at the end. Your
current design measures at the end of the episode and compares across shift
depths (25/50/75%). You'd only need per-step grades if you want to plot how
performance *drops and recovers within a single episode* right after a shift.

### 2. What exactly went wrong

*(Checked in the code, `engine.py`.)* The evaluation cadence is set to
`ON_COMPLETION` at startup. The audit adds that the filter deciding which
rubrics run at each step is bypassed, so end-of-run-only rubrics would fire
every step *(audit's claim, not re-checked)*.

**How it came about:** the comment in the code says the default should be both,
but the code sets it to the end-only option.

### 3. Where it sits in the flow

```
BEFORE   the run starts; cadence is hard-set to end-of-run only
DURING   per-step rubrics never fire
AFTER    you get one score at the end, and no curve
```

**The planned fix:** only if you decide you want within-episode curves. Set the
cadence to per-step and close the filter bypass. Check that per-step records
appear and that end-only rubrics stop firing every step.

---

# What we chose not to fix (and why)

- **Restoring saved runs faithfully (ML-070 / 072 / 073).** Only the
  cross-episode task needs it, and that's a stretch goal. Revisit if you pick it
  up.
- **Judge noise (ML-036).** That's a property of LLM judges, not a code bug. We
  handle it by practice: several judges and seeds, and reporting how much they
  agree.
- **The rest (ML-006, 045, 047, 050).** Real bugs, but not on the critical path
  for your experiments. We monitor them and fix them only if a run hits them.
