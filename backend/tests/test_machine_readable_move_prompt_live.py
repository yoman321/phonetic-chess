"""The live gate for plans/machine-readable-move-prompt.md.

Invariant 5 and the live halves of 10-24. It spends money and calls a judge
CLI, so it is skipped unless both flags are set:

    (cd backend && LLM_LIVE=1 JUDGE_LIVE=1 JUDGE_CLI=claude \
        .venv/bin/pytest -q tests/test_machine_readable_move_prompt_live.py)

A skipped run is a skip. It is never a pass — see AGENTS.md section 7.

The whole benchmark runs once per session:

    run.run_benchmark(cases, old_prompts, out_dir) -> report

`report` is what run.build_report returns, and out_dir is
backend/.benchmark-results/machine-readable-move-prompt/<run-id>/.
"""
import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import benchmark_support as bench          # noqa: E402

LIVE = bool(os.environ.get("LLM_LIVE")) and bool(os.environ.get("JUDGE_LIVE"))

pytestmark = pytest.mark.skipif(
    not LIVE,
    reason="needs a real provider and judge; set LLM_LIVE=1 and JUDGE_LIVE=1",
)

MIN_TOKEN_SAVING = 90        # invariant 5
MIN_STABILITY = 0.90         # invariant 17
MIN_COMPARABLE = 18          # invariant 19
TONE_MARGIN = 0.15           # invariant 20
OFF_LIST_MARGIN = 0.15       # invariant 23

RESULTS_DIR = (
    bench.BACKEND_DIR / ".benchmark-results" / "machine-readable-move-prompt"
)


@pytest.fixture(scope="module")
def report():
    run = bench.require_module("run.py")
    return bench.require_attr(run, "run_benchmark")(
        bench.cases(), bench.read_old_prompts()["prompts"], RESULTS_DIR,
    )


def test_the_run_claims_live_results_only_with_both_flags_set(report):
    """Invariant 24."""
    assert report["env"]["LLM_LIVE"] == os.environ["LLM_LIVE"] == "1"
    assert report["env"]["JUDGE_LIVE"] == os.environ["JUDGE_LIVE"] == "1"


def test_the_run_used_the_frozen_fixtures(report):
    assert report["cases_sha256"] == bench.sha256_of(bench.CASES_PATH)
    assert report["old_prompts_sha256"] == bench.sha256_of(bench.OLD_PROMPTS_PATH)


def test_sixty_invocations_ran_and_retries_were_counted(report):
    """Invariant 10."""
    assert report["invocations"] == bench.INVOCATION_COUNT
    assert report["provider_requests"] >= bench.INVOCATION_COUNT
    assert report["content_retry_requests"] == (
        report["provider_requests"] - bench.INVOCATION_COUNT
    )


def test_the_new_prompt_saves_at_least_ninety_input_tokens(report):
    """Invariant 5. The whole point of the change, measured by the provider."""
    tokens = report["metrics"]["tokens"]
    assert tokens["old_mean"] is not None and tokens["new_mean"] is not None, (
        "a reply carried no usage, so the saving is unmeasured, not zero"
    )
    assert tokens["saved"] >= MIN_TOKEN_SAVING, (
        f"the new prompt saved {tokens['saved']:.1f} input tokens "
        f"({tokens['old_mean']:.1f} -> {tokens['new_mean']:.1f}); "
        f"invariant 5 requires at least {MIN_TOKEN_SAVING}"
    )


def test_the_judge_was_stable_enough_to_read(report):
    """Invariant 17."""
    stability = report["metrics"]["stability"]
    assert stability["total"] == bench.INVOCATION_COUNT
    assert stability["rate"] >= MIN_STABILITY, (
        f"the judge agreed with itself on {stability['matched']}/60 items "
        f"({stability['rate']:.2f}); below {MIN_STABILITY} the run is "
        "inconclusive, not a pass"
    )


def test_both_metrics_had_enough_comparable_cases(report):
    """Invariant 19."""
    for name in ("floor", "cross"):
        block = report["metrics"][name]
        assert block["comparable"] >= MIN_COMPARABLE, (
            f"{name} had only {block['comparable']}/20 comparable cases "
            f"({block['unparsed']} unparsed)"
        )


def test_the_new_prompt_keeps_its_tone_labels(report):
    """Invariant 20. Gross regression only; one case is worth 0.05."""
    floor = report["metrics"]["floor"]["rate"]
    cross = report["metrics"]["cross"]["rate"]
    assert cross >= floor - TONE_MARGIN, (
        f"cross {cross:.2f} fell more than {TONE_MARGIN} below the noise "
        f"floor {floor:.2f}"
    )


def test_every_new_invocation_answered_cleanly(report):
    """Invariant 22."""
    validity = report["metrics"]["validity"]
    assert validity == {
        "total": bench.CASE_COUNT,
        "first_attempt_parsed": bench.CASE_COUNT,
        "legal_uci": bench.CASE_COUNT,
        "nonempty_tone": bench.CASE_COUNT,
    }


def test_the_new_prompt_does_not_wander_off_the_candidate_list(report):
    """Invariant 23."""
    old = report["metrics"]["off_list"]["A"]
    new = report["metrics"]["off_list"]["C"]
    assert old["total"] == new["total"] == bench.CASE_COUNT
    assert new["rate"] <= old["rate"] + OFF_LIST_MARGIN, (
        f"NEW went off-list {new['off']}/20 against OLD A's {old['off']}/20"
    )


def test_the_report_was_written_to_disk(report):
    """Phase 5. The run is only worth as much as its record."""
    run_dir = RESULTS_DIR / report["run_id"]
    assert (run_dir / "report.json").exists()
    assert list(run_dir.glob("*.jsonl")), "no attempt log was kept"


def test_the_verdict_is_a_pass(report):
    """Invariants 5, 17, 19, 20, 23 together."""
    assert report["verdict"]["status"] == "pass", report["verdict"]["reasons"]
