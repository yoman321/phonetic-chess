# Handoff
<!-- role: Review the build | model: claude-opus-5 | base: 17c8b69278a67c280d93186aef3f9927b5582246 | date: 2026-09-19 -->

Feature:  machine-readable-move-prompt   Plan: plans/machine-readable-move-prompt.md   Status: frozen
Phase:    6 of 6 — CLOSED. Shipped, reviewed, findings accepted, docs updated

State:    Build is done and section 7 is green; I re-ran all of it myself rather
          than trusting the Build handoff. The frozen OLD prompts were rebuilt
          from base 17c8b69 and matched byte-for-byte. "## Build review" is
          written into the plan. All 6 findings were accepted and deferred by
          the user on 2026-09-19 ("close them, they are not high priority") —
          agreed to be real, not fixed. No code changed for any of them. Zero
          [open] findings remain. All 6 are carried into BACKLOG.md in full.
          Closing the first one retires the `defensive` tone label on purpose.
Docs:     README.md — new prompt-token row and the live benchmark command, its
          three env vars, and where results land.
          docs/architecture.md — step 4 of the say_move walkthrough rewritten for
          build_move_prompt and the compact JSON user message; the quoted "prefer
          one of the suggested moves" line no longer exists and was replaced with
          the RULE key; llm.py:135 corrected to :281.
          docs/gotchas.md — one line for the Codex-only judge failing last.
          BACKLOG.md — stale llm.py line numbers in the blank-tone_summary entry
          corrected after the refactor, and all 6 accepted findings written out
          in full under "Found while working" so they outlive the plan.

Next:     Nothing scheduled for this feature. It is done. Pick up the next piece
          of work; see BACKLOG.md. If the `defensive` label is ever wanted back,
          the frozen case set and old-prompt baseline are committed and the
          benchmark can be re-run as-is.
Blocked:  none
Gates:    94/94 offline pass, 11 live gates skipped by default. Failing: none.
Verified: (cd backend && .venv/bin/pytest -q) with testdb up -> 219 passed, 11 skipped;
          all 11 skips are tests/test_machine_readable_move_prompt_live.py;
          (cd frontend && npm test) -> 17 passed;
          (cd frontend && npm run lint) -> clean;
          (cd frontend && npm run build) -> built in 118ms;
          typecheck -> no command; backend lint and typecheck vacuous, not passing;
          backend diff read for wrong argument names and counts, unexpected None,
          and unmapped exceptions -> clean;
          old-prompts.json regenerated from git show 17c8b69 -> 20 cases, 0 mismatches;
          live report 20260919T192811867459Z -> 60 invocations, 62 provider requests,
          171.95 tokens saved (434.55 -> 262.60), stability 56/60, floor 0.75,
          cross 0.65, off-list 0/20 both arms, verdict pass;
          second blind judge pass by this session -> floor 0.80, cross 0.75,
          intensity 0.15, 50/60 label agreement with Codex, invariant 20 still met.

Rules:    AGENTS.md was edited by the human, confirmed 2026-09-19. Stated intent
          was three things: the "talk to me like I am five" reply format, Build
          builds every phase before testing instead of stopping at each boundary,
          and verify fully before reporting back. All three are in the diff.
          The diff also carries a fourth change the human did not name: the Grade
          role may now rewrite the plan body, "acting on a finding" is no longer
          forbidden, and "edit the body of an artifact you are grading" was
          deleted from section 11. plans/machine-readable-move-prompt.md was
          already graded that way — provenance says Grade/gpt-5 over a draft by
          Plan/claude-opus-5. Flagged to the human; still uncommitted.
          README.md's setup section is still wrong at the first command — the
          .env.example path and the port. Pre-existing, logged in BACKLOG.md
          under Process, left alone as out of scope for this feature.
Note:     Nothing is committed. Every change above is working-tree only.
          .gitignore now excludes backend/.benchmark-results/ — per-run output,
          which the plan calls local and not a committed fixture. The frozen
          fixtures under backend/tests/fixtures/ are committed on purpose.
          Scanned everything stageable for keys, tokens, passwords, home paths
          and emails: clean.
          BACKLOG.md is itself gitignored (.gitignore:29), so the six findings
          written there will never be committed unless that line is removed.
