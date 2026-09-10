# AGENTS.md

Read fully before writing code.

## Files

- `AGENTS.md` — these rules.
- `plans/<feature>.md` — spec for the current work.
- `handoff.md` — where the work stands, next step. Overwritten each session.
- `BACKLOG.md` — work not yet started.
- `README.md` — human setup.
- `docs/gotchas.md` — known issues, symptom → fix. Read it if it exists. Delete entries whose cause is fixed.

## Commands

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

# <typecheck>     none configured — see below
# <lint>
(cd frontend && npm run lint)

# <build>
(cd frontend && npm run build)
```

`<typecheck>` has no command and `<lint>` covers the frontend only: the backend
has neither a linter nor a type checker, and the frontend is plain JSX with no
`tsc`. **Decided 2026-09-09: no `ruff`, no `mypy`** — that role is filled by
review rather than by a tool. So on the backend those two checks are satisfied
vacuously: say so plainly, never report them as passing. A backend change is not
verified by `<test-full>` alone; read it for the classes of error a type checker
would have caught — wrong argument names and counts, a `None` reaching something
that cannot take one, an exception path that is raised but never mapped.

`backend/scripts/testdb.sh` runs a throwaway Postgres on 127.0.0.1:55432, never
the compose `db` service. Tests marked `integration` are the only ones needing
it. See `README.md`.

## Sessions

One role per session. Never take two.

| Role | Reads | Writes |
|---|---|---|
| Plan | `AGENTS.md`, `handoff.md`, code | `plans/<feature>.md` |
| Grade the plan | plan, code | `plans/<feature>.review.md` |
| Write the gates | frozen plan | failing tests only |
| Build | frozen plan, failing tests | code, `handoff.md` |
| Review the build | plan, diff | `plans/<feature>.impl-review.md` |

- Identify your role from the prompt. Ambiguous → ask before starting.
- Read `handoff.md` first. Rewrite it whole before ending. Never append to it.
- Write all output to disk before the session ends.
- If you produced it, say so before grading it.
- Grading output: severity, location, why it breaks, smallest fix. No praise, no summary of the artifact.
- Never edit an artifact you are grading.
- Never edit the plan while building against it.
- Findings are arbitrated by a human. Do not act on them.
- Build one phase at a time. Stop at the boundary.
- Plan is wrong → stop and say so. Do not work around it.

## Tests first

- After the plan freezes, the first code written is its gates. No implementation in that session.
- Derive gates from the plan's invariants, not from an implementation.
- Run each gate. Show it failing. Confirm it fails for missing behavior — not a typo, missing import, or unbuilt fixture.
- A gate that passes before the work exists is not a gate. Stop and report it as a plan defect.
- Never relax, skip, or delete a gate to reach green.
- Assert the invariant, not the shape.

## Verification

Done requires all of: gates observed failing before the change, then `<test-full>`, `<typecheck>`, `<lint>`, `<build>` pass.

A clean review is not verification.

Handing off with a failure: name it, paste the output.

**Without asking:** read files, read-only diagnostics, any command above, start or restart the dev server.

**Ask first:** writes outside the repo, spending money, publishing, deploying, production data.

## Scope

- No product-direction change without a user decision. Need an assumption → state it, continue.
- Smallest change that fully satisfies the task.
- No drive-by renames, unrelated refactors, or reformatting.
- Match surrounding idiom, naming, and comment density.
- No new dependency where ~20 lines of local code would do.
- Secrets stay server-side. Never in client code, bundles, or logs.
- Out-of-scope findings → `BACKLOG.md`, under "Found while working".

## Libraries

Check the lockfile and read the installed source, vendored docs, or `--help` before calling any library API. Never write a call from memory.

## Assertions

Assert on numbers: widths, counts, timings, actual output. Never on whether something looks right or resembles an expected shape.

## Before ending a session

Update what your work invalidated:

- decision made, or plan deviated from → `handoff.md`
- direction, scope or rules changed → `handoff.md`
- setup, commands, routes or env vars changed → `README.md`
- something cost over ten minutes and the cause was non-obvious → `docs/gotchas.md`, one line, symptom → fix. Create the file if it doesn't exist.

## Replies

- Lead with the answer. No preamble, no restating the question.
- Match length to the question. A yes/no gets a yes/no, then the one caveat that matters.
- Prose for connected reasoning. Bullets only for parallel items. Tables only for three or more things.
- Cut filler openers, hedges, and any closing paragraph that re-summarizes.
- Say the hard thing plainly: "This won't work, because X."
- Write like a senior colleague answering in Slack.

After a task, report:

1. What changed — files and behavior.
2. What was verified — commands run, and results.
3. What's open — stubs, skipped scope, limits.

State failing tests with their output. State skipped steps. State verified work plainly.

---

## Stack

<!-- versions and anything non-obvious about the runtime -->

## Style

<!-- one rule per line, plus a 3–10 line snippet from real code where a pattern is ambiguous -->

## Invariants

<!-- properties that cannot be inferred from the code. State as absolutes. -->

## Boundaries

- Never commit, push, tag, merge, or open a pull request.
- Never modify CI config, deploy manifests, or release tooling.
- Never add, upgrade, or remove a dependency without approval.
- Never rewrite git history.
- Never write to `AGENTS.md`. Ask.
- Never touch production data or non-local environments.
