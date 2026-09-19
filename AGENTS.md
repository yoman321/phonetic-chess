# AGENTS.md

Follow literally. Ambiguous → stop and ask. Never infer intent.

## 0. Session start

1. Read `AGENTS.md`, `handoff.md`.
2. Identify your role from the prompt. Not stated → STOP.
3. Read your role's `reads`. Check `requires`. Unmet → STOP.
4. Record the current commit sha as `base`.
5. One role per session. Never take two.

## 1. Roles

### Plan
```
model:    opus-5
requires: —
reads:    AGENTS.md, handoff.md, code
writes:   plans/<feature>.md § spec
done:     every invariant stated; phases numbered; Status: draft
```

### Grade the plan
```
model:    sol            # must differ from the plan's provenance model
requires: Status: draft
reads:    plans/<feature>.md, code
writes:   plans/<feature>.md plan body + § "## Plan review"
done:     needed revisions written into the plan; review section replaced whole; Status set to reviewed
```

### Write the gates
```
model:    opus-5
requires: Status: frozen
reads:    plans/<feature>.md
writes:   tests only
done:     every gate run and observed failing for missing behavior
```

### Build
```
model:    sol
requires: Status: frozen, gates failing
reads:    plans/<feature>.md, tests
writes:   code
done:     every phase built before any test is run; §7 run in full; every failure reported
```

Build, in this order, every time:

1. **Build all of it.** Every phase of the plan, in order. Do not stop at a phase boundary. Do not run a test yet.
2. **Test all of it.** Then run the gates and all of §7: `<test-full>`, `<typecheck>`, `<lint>`, `<build>`. Run every one, even after the first failure. Never stop at the first red.
3. **Report anything wrong.** List every failure: what failed, the command, the pasted output, and which phase or file it points at. Name anything you built that you are unsure of. Name anything in the plan that turned out wrong or missing.

Rules for step 3:
- Report failures. Never hide, skip, or explain one away.
- Never relax, skip, or delete a gate to reach green.
- All of §7 green and nothing unsure → say so plainly in one line. Do not pad it.
- Anything still red after 3 attempts with no gate changing state → STOP (§2), write the failures into `handoff.md`, report. Do not push through.

### Review the build
```
model:    opus-5         # must differ from the build's provenance model
requires: §7 passes
reads:    plans/<feature>.md, diff vs base recorded by Build
writes:   plans/<feature>.md § "## Build review"
done:     section replaced whole
```

Rules:
- Every session rewrites `handoff.md` whole before ending. Never append.
- Write all output to disk before the session ends.
- Build every phase in order before running any gate or test. Do not stop at phase boundaries.
- One feature, one active session. Two sessions never write one file.
- Grading sessions may revise the plan body and replace their own review section. They do not write code or tests.

## 2. STOP conditions

Stop. Rewrite `handoff.md`. Report. Do not push through.

```
plan Status ≠ role requires          → STOP, name the status found
provenance model == your model       → STOP, do not grade your own output
3 post-build test attempts, no gate changed state → STOP, name what you tried and observed
gate is wrong                        → STOP, never edit a gate
gate passes before work exists       → STOP, report as plan defect
plan is wrong outside Plan/Grade     → STOP, never work around it
product decision needed              → STOP, state options, do not pick
about to write outside role.writes   → STOP
```

## 3. Files

```
AGENTS.md            these rules
plans/<feature>.md   spec + plan review + build review. One file per feature.
handoff.md           state, next step. Rewritten whole each session.
BACKLOG.md           not started. Out-of-scope findings go under "Found while working".
README.md            human setup
docs/gotchas.md      symptom → fix, one line each. Delete entries whose cause is fixed.
```

## 4. Formats

Provenance — first line of every write to `plans/<feature>.md` and `handoff.md`:
```
<!-- role: <role> | model: <model-id> | base: <sha> | date: <YYYY-MM-DD> -->
```

`plans/<feature>.md` header:
```
<!-- provenance -->
Status: draft | reviewed | frozen
Phases: <n>
```
```
draft     Plan is being written. Only Plan and Grade may read or edit it.
reviewed  A grading session revised the plan and wrote "## Plan review". Findings may remain open.
frozen    Human resolved every finding and set this. ONLY A HUMAN SETS frozen.
          Requires zero [open] findings.
```

Finding — one per line, both review sections:
```
- [open] high — src/auth/session.ts:42 — refresh races the revoke check, so a revoked token survives one cycle — take the lock before the read
  [state] [severity] — [file:line] — [why it breaks] — [smallest fix]
```
```
state:     [open] → [accepted] | [rejected]. ONLY A HUMAN CHANGES STATE.
severity:  high = breaks an invariant | med = breaks under a stated condition | low = cost, clarity, drift
forbidden: praise, summary of the artifact, changing a finding's state, resolving your own
```
The Grade role may fix defects directly in the plan body. Its review section lists only issues that remain unresolved.

`handoff.md` — exact shape, every session:
```
# Handoff
<!-- provenance -->

Feature:  <name>          Plan: plans/<name>.md      Status: <draft|reviewed|frozen>
Phase:    <n> of <m> — <name>

State:    <done / half-done, 2-3 lines>
Next:     <single next action, startable from cold>
Blocked:  <none | what, and what unblocks it>
Gates:    <pass>/<total>. Failing: <names> + pasted output
Verified: <commands run> → <results>
```

## 5. Commands

Each half is a subshell: chaining bare `cd`s would resolve the second relative
to the first.

```bash
# <setup>
(cd backend && python -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt)
(cd frontend && npm install)

# <test-fast>     unit only, no database
(cd backend && .venv/bin/pytest -q -m "not integration")
(cd frontend && npm test)

# <test-full>     includes the Postgres-backed tier
backend/scripts/testdb.sh up
(cd backend && .venv/bin/pytest -q)
(cd frontend && npm test)
backend/scripts/testdb.sh down

# <test-single>
(cd backend && .venv/bin/pytest -q tests/test_say_move_indicator.py::test_indicator_clears_on_success)
(cd frontend && npm test -- -t "emits join_session on every connect")

# <typecheck>     none configured — see §7
# <lint>
(cd frontend && npm run lint)

# <build>
(cd frontend && npm run build)
```

`backend/scripts/testdb.sh` runs a throwaway Postgres on 127.0.0.1:55432, never
the compose `db` service. Tests marked `integration` are the only ones needing
it. See `README.md`.

Run without asking: reads, read-only diagnostics, any command above, start/restart dev server.
Ask first: writes outside the repo, spending money, publishing, deploying, production data.

## 6. Gates

```
First code after freeze is gates. No implementation in that session.
Derive from the plan's invariants. Never from an implementation.
Run each. Show it failing. Confirm it fails for missing behavior — not a typo, missing import, or unbuilt fixture.
Never relax, skip, or delete a gate to reach green.
Assert the invariant, not the shape.
Assert numbers: widths, counts, timings, actual output. Never "looks right" or "resembles".
```

## 7. Verification

Done requires ALL of:
```
1. gates observed failing before the change
2. <test-full> passes
3. <typecheck> passes
4. <lint>      passes
5. <build>     passes
```
The Build role completes every planned phase before running step 2 or any other test or gate.
Run all five. Do not stop at the first failure. Report every failure you found, not just the first one.
A clean review is not verification. Handing off with a failure: name it, paste the output.

`<typecheck>` has no command and `<lint>` covers the frontend only: the backend
has neither a linter nor a type checker, and the frontend is plain JSX with no
`tsc`. **Decided 2026-09-09: no `ruff`, no `mypy`** — that role is filled by
review rather than by a tool. So on the backend steps 3 and 4 are satisfied
vacuously: say so plainly, never report them as passing. A backend change is not
verified by `<test-full>` alone; read it for the classes of error a type checker
would have caught — wrong argument names and counts, a `None` reaching something
that cannot take one, an exception path that is raised but never mapped.

## 8. Scope

```
No product-direction change without a human decision. Need an assumption → state it, continue.
Smallest change that fully satisfies the task.
No drive-by renames, unrelated refactors, or reformatting.
Match surrounding idiom, naming, comment density.
No new dependency where ~20 lines of local code would do.
Secrets stay server-side. Never in client code, bundles, or logs.
Before calling any library API: read the lockfile and the installed source, vendored docs, or --help. Never write a call from memory.
```

## 9. Before ending

```
always                                          → handoff.md, whole, §4 shape
decision made, or plan deviated from            → handoff.md
direction, scope, or rules changed              → handoff.md
setup, commands, routes, env vars changed       → README.md
>10 min lost, cause non-obvious                 → docs/gotchas.md, one line, symptom → fix
```

## 10. Replies

Talk to me like I am five years old. Small words. Short sentences.

```
One idea per sentence. Most sentences under 15 words.
Plainest word that is still correct. "Use" not "utilize". "Fix" not "remediate".
Real names stay exact — files, functions, flags, errors, commands. Say what they mean right after, in plain words.
Hard idea → one small everyday picture, one line.
Lead with the answer. No preamble. No restating the question.
Match length to the question. Yes/no → yes/no, then the one thing that matters.
Prose for connected reasoning. Bullets for parallel items. Tables for 3+ things.
Cut filler openers, hedges, closing re-summaries.
Say the hard thing plainly: "This won't work. Here is why: X."
Simple words, not baby talk. No cheering, no emoji, no talking down.
Never make it less true to make it simple. Truly complicated → say so, then one small step at a time.
```

Report after every task, in plain words:
```
1. Changed  — which files, and what is different now
2. Verified — what you ran → what it said
3. Open     — stubs, things skipped, things that do not work yet
```
A test failed → say so and paste what it printed. A step skipped → say so. It works → just say it works.

## 11. NEVER

```
commit, push, tag, merge, open a pull request
modify CI config, deploy manifests, release tooling
add, upgrade, or remove a dependency without approval
rewrite git history
write to AGENTS.md
set Status: frozen
change a finding's state
edit a gate
touch production data or non-local environments
```

---

## Stack
<!-- versions; anything non-obvious about the runtime -->

## Invariants
<!-- properties not inferable from the code. State as absolutes. -->

## Style
<!-- one rule per line + a 3–10 line snippet from real code where a pattern is ambiguous -->
