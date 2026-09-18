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
writes:   plans/<feature>.md § "## Plan review"
done:     section replaced whole; Status set to reviewed
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
done:     phase gates green; §7 passes
```

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
- Build one phase at a time. Stop at the phase boundary.
- One feature, one active session. Two sessions never write one file.
- Grading sessions replace only their own section. Every other line stays byte-identical.

## 2. STOP conditions

Stop. Rewrite `handoff.md`. Report. Do not push through.

```
plan Status ≠ role requires          → STOP, name the status found
provenance model == your model       → STOP, do not grade your own output
3 turns, no gate changed state       → STOP, name what you tried and observed
gate is wrong                        → STOP, never edit a gate
gate passes before work exists       → STOP, report as plan defect
plan is wrong                        → STOP, never work around it
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
draft     Plan is writing. Nothing downstream may read it.
reviewed  A grading session wrote "## Plan review". Findings open.
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
forbidden: praise, summary of the artifact, acting on a finding, resolving your own
```

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

- Lead with the answer. No preamble, no restating the question.
- One idea per sentence. Most sentences under fifteen words.
- Use the plainest word that is still correct. "Use" not "utilize". "Fix" not "remediate". "Slow" not "suboptimal performance".
- A name from the code (a file, a function, a flag, an error) stays exactly as it is. Never simplify a real name. Say what it means right after, in plain words.
- A hard idea gets a small everyday picture, one line: "A cache is a box where we keep the answer so we don't have to go get it again."
- Match length to the question. A yes/no gets a yes/no, then the one thing that matters.
- Prose for connected reasoning. Bullets only for parallel items. Tables only for three or more things.
- Cut filler openers, hedges, and any closing paragraph that re-summarizes.
- Say the hard thing plainly: "This won't work. Here is why: X."
- Simple words, not baby talk. No "oopsie", no cheering, no emoji, no talking down. Say the real thing in easy words.
- Never make the answer less true to make it simple. If a thing is truly complicated, say so, then take it one small step at a time.

After a task, tell me three things in plain words:

1. What changed — which files, and what is different now.
2. What was checked — what you ran, and what it said.
3. What is still open — stubs, things skipped, things that do not work yet.

If a test failed, say so and paste what it printed. If you skipped a step, say so. If it works, just say it works.

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
edit the body of an artifact you are grading
touch production data or non-local environments
```

---

## Stack
<!-- versions; anything non-obvious about the runtime -->

## Invariants
<!-- properties not inferable from the code. State as absolutes. -->

## Style
<!-- one rule per line + a 3–10 line snippet from real code where a pattern is ambiguous -->
