"""Run OLD A, OLD B, and shipped NEW C, then build the benchmark report."""
import hashlib
import json
import os
import pathlib
import sys
import time
from datetime import datetime, timezone

import chess

from controller_operations import llm


BENCH_DIR = pathlib.Path(__file__).resolve().parent
if str(BENCH_DIR) not in sys.path:
    sys.path.insert(0, str(BENCH_DIR))

import judge  # noqa: E402
import metrics  # noqa: E402


REPO_ROOT = BENCH_DIR.parents[1]
FIXTURE_DIR = (
    REPO_ROOT / "backend" / "tests" / "fixtures"
    / "machine_readable_move_prompt"
)
CASES_PATH = FIXTURE_DIR / "cases.json"
OLD_PROMPTS_PATH = FIXTURE_DIR / "old-prompts.json"
ARMS = ("A", "B", "C")
PACE_SECONDS = 4.0
TEMPERATURE = 0.7


def require_live():
    if os.environ.get("LLM_LIVE") != "1":
        raise RuntimeError("provider work requires LLM_LIVE=1")


def _sha256(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def _append_jsonl(path, row):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def plan_invocations(cases):
    return [
        {"arm": arm, "case_id": case["id"]}
        for arm in ARMS
        for case in cases
    ]


def _last_move(case):
    if case["last_move"] is None:
        return None
    return case["last_move"]["uci"], case["last_move"]["san"]


def render_messages(arm, case, old_prompts):
    if arm in ("A", "B"):
        frozen = old_prompts[case["id"]]
        return frozen["system"], frozen["user"]
    if arm == "C":
        return llm.build_move_prompt(
            case["message"], case["fen"], case["candidates"],
            case["prior_tone"], _last_move(case),
        )
    raise ValueError(f"unknown arm: {arm}")


class _DurableAttemptLog:
    def __init__(self, path, run_id, arm, case, invocation_index):
        self.path = pathlib.Path(path)
        self.run_id = run_id
        self.arm = arm
        self.case = case
        self.invocation_index = invocation_index
        self.rows = []
        self.parsed = []
        self.final = {"outcome": "unexpected"}

    def add_attempt(
        self, attempt, outcome, started_at, sdk_retries=None, status_code=None,
        raw_content=None, error_detail=None, usage=None,
    ):
        del started_at, status_code, error_detail
        uci = None
        tone_summary = None
        parsed_ok = False
        if raw_content is not None:
            try:
                payload = json.loads(raw_content)
                if isinstance(payload, dict):
                    uci = (payload.get("uci") or "").strip()
                    tone_summary = (payload.get("tone_summary") or "").strip()
                    parsed_ok = bool(uci)
            except (TypeError, ValueError):
                pass
        row = {
            "run_id": self.run_id,
            "arm": self.arm,
            "case_id": self.case["id"],
            "invocation_index": self.invocation_index,
            "content_attempt": attempt,
            "raw_content": raw_content,
            "uci": uci,
            "tone_summary": tone_summary,
            "outcome": outcome,
            "sdk_retries": sdk_retries,
            "prompt_tokens": usage.prompt_tokens if usage is not None else None,
            "completion_tokens": usage.completion_tokens if usage is not None else None,
        }
        _append_jsonl(self.path, row)
        self.rows.append(row)
        self.parsed.append(parsed_ok)

    def finish(
        self, outcome, chosen_uci=None, off_list=None, intent=None,
        rationale=None, tone_summary=None,
    ):
        del intent, rationale
        self.final = {
            "outcome": outcome,
            "uci": chosen_uci,
            "off_list": off_list,
            "tone_summary": tone_summary,
        }


def _existing_keys(path):
    path = pathlib.Path(path)
    if not path.exists():
        return set()
    keys = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        keys.add((row["arm"], row["case_id"]))
    return keys


def run_arm(
    arm, cases, old_prompts, client, results_path, run_id, sleeper=time.sleep,
    start_index=0, invocations_path=None,
):
    if arm not in ARMS:
        raise ValueError(f"unknown arm: {arm}")
    existing = _existing_keys(results_path)
    duplicate = next(
        (case["id"] for case in cases if (arm, case["id"]) in existing),
        None,
    )
    if duplicate is not None:
        raise ValueError(f"duplicate invocation key: {arm}/{duplicate}")

    invocations = []
    for offset, case in enumerate(cases):
        if offset:
            sleeper(PACE_SECONDS)
        valid_ucis = {move.uci() for move in chess.Board(case["fen"]).legal_moves}
        log = _DurableAttemptLog(
            results_path, run_id, arm, case, start_index + offset,
        )

        def _pace_retry(_attempt):
            sleeper(PACE_SECONDS)

        if arm == "C":
            uci, tone_summary = llm.pick_move_with_llm(
                case["message"], case["fen"], case["candidates"],
                case["prior_tone"], _last_move(case), valid_ucis=valid_ucis,
                on_retry=_pace_retry, log=log, _client_override=client,
                _sleeper=sleeper,
            )
        else:
            system, user = render_messages(arm, case, old_prompts)
            uci, tone_summary = llm._pick_move_from_messages(
                system, user, case["candidates"], valid_ucis,
                on_retry=_pace_retry, log=log, client=client,
                sleeper=sleeper,
            )
        if any(
            row["prompt_tokens"] is None or row["completion_tokens"] is None
            for row in log.rows
        ):
            raise RuntimeError(f"{arm}/{case['id']}: provider usage is missing")
        invocation = {
            "run_id": run_id,
            "arm": arm,
            "case_id": case["id"],
            "invocation_index": start_index + offset,
            "attempts": len(log.rows),
            "outcome": log.final["outcome"],
            "first_attempt_parsed": bool(log.parsed and log.parsed[0]),
            "legal_uci": uci in valid_ucis,
            "uci": uci,
            "tone_summary": tone_summary,
            "off_list": uci not in {candidate for candidate, _san in case["candidates"]},
        }
        invocations.append(invocation)
        if invocations_path is not None:
            _append_jsonl(invocations_path, invocation)
    return invocations


def _verdict_from(metrics_block):
    tokens = metrics_block["tokens"]
    floor = metrics_block["floor"]
    cross = metrics_block["cross"]
    return metrics.verdict({
        "stability_rate": metrics_block["stability"]["rate"],
        "floor_rate": floor["rate"],
        "floor_comparable": floor["comparable"],
        "cross_rate": cross["rate"],
        "cross_comparable": cross["comparable"],
        "saved_tokens": tokens["saved"],
        "tokens_complete": tokens["saved"] is not None,
        "off_list_new": metrics_block["off_list"]["C"]["rate"],
        "off_list_old": metrics_block["off_list"]["A"]["rate"],
    })


def build_report(payload):
    report = dict(payload)
    if report["cases_sha256"] != _sha256(CASES_PATH):
        raise ValueError("case fixture hash does not match the frozen input")
    if report["old_prompts_sha256"] != _sha256(OLD_PROMPTS_PATH):
        raise ValueError("old-prompt fixture hash does not match the frozen input")
    if report["invocations"] != 60:
        raise ValueError("the report must contain exactly 60 invocations")
    if report["provider_requests"] < report["invocations"]:
        raise ValueError("provider requests cannot be fewer than invocations")
    if report.get("env", {}).get("LLM_LIVE") != "1" or report.get("env", {}).get("JUDGE_LIVE") != "1":
        raise ValueError("a live report requires both live flags")
    for field in ("cli", "version", "model"):
        if not report.get("judge", {}).get(field):
            raise ValueError(f"judge metadata is missing {field}")
    report["content_retry_requests"] = (
        report["provider_requests"] - report["invocations"]
    )
    report["one_case_worth"] = 1 / 20
    report["caveat"] = "This sample can catch only a gross tone regression."
    report["verdict"] = _verdict_from(report["metrics"])
    return report


def _case_labels(pass_results, arm, cases):
    return {
        case["id"]: (
            pass_results[f"{arm}:{case['id']}"]["label"]
            if pass_results.get(f"{arm}:{case['id']}") is not None else None
        )
        for case in cases
    }


def _case_intensities(pass_results, arm, cases):
    return {
        case["id"]: (
            pass_results[f"{arm}:{case['id']}"]["intensity"]
            if pass_results.get(f"{arm}:{case['id']}") is not None else None
        )
        for case in cases
    }


def run_benchmark(cases, old_prompts, out_dir):
    require_live()
    judge.require_live()
    started = datetime.now(timezone.utc)
    run_id = started.strftime("%Y%m%dT%H%M%S%fZ")
    run_dir = pathlib.Path(out_dir) / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    attempts_path = run_dir / "attempts.jsonl"
    invocations_path = run_dir / "invocations.jsonl"
    judge_path = run_dir / "judge.jsonl"

    invocations = []
    for arm_index, arm in enumerate(ARMS):
        if arm_index:
            time.sleep(PACE_SECONDS)
        invocations.extend(run_arm(
            arm, cases, old_prompts, llm._client, attempts_path,
            run_id=run_id, start_index=arm_index * len(cases),
            invocations_path=invocations_path,
        ))

    call = lambda system, user: judge.codex_call(
        system, user, results_path=judge_path,
    )
    judge_one, judge_two, judge_meta = judge.run_two_passes(
        invocations, call, seeds=(11, 22),
    )
    judge_meta.update(judge.cli_info())

    attempts = [
        json.loads(line)
        for line in attempts_path.read_text(encoding="utf-8").splitlines()
    ]
    by_arm = {
        arm: [item for item in invocations if item["arm"] == arm]
        for arm in ARMS
    }
    old_tokens = [
        row["prompt_tokens"] for row in attempts
        if row["arm"] in ("A", "B") and row["outcome"] == "ok"
    ]
    new_tokens = [
        row["prompt_tokens"] for row in attempts
        if row["arm"] == "C" and row["outcome"] == "ok"
    ]
    floor = metrics.agreement(
        _case_labels(judge_one, "A", cases),
        _case_labels(judge_one, "B", cases),
    )
    cross = metrics.agreement(
        _case_labels(judge_one, "C", cases),
        _case_labels(judge_one, "A", cases),
    )
    metrics_block = {
        "stability": metrics.stability(judge_one, judge_two),
        "floor": floor,
        "cross": cross,
        "intensity": metrics.intensity_delta(
            _case_intensities(judge_one, "C", cases),
            _case_intensities(judge_one, "A", cases),
        ),
        "tokens": metrics.token_saving(old_tokens, new_tokens),
        "off_list": {
            "A": metrics.off_list_rate(by_arm["A"]),
            "C": metrics.off_list_rate(by_arm["C"]),
        },
        "validity": metrics.validity(by_arm["C"]),
    }
    finished = datetime.now(timezone.utc)
    report = build_report({
        "run_id": run_id,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "cases_sha256": _sha256(CASES_PATH),
        "old_prompts_sha256": _sha256(OLD_PROMPTS_PATH),
        "model": llm.LLM_MODEL,
        "reasoning_effort": llm.LLM_REASONING_EFFORT,
        "max_tokens": llm.LLM_MAX_TOKENS,
        "temperature": TEMPERATURE,
        "max_retries": llm.LLM_MAX_RETRIES,
        "sdk_max_retries": llm.SDK_MAX_RETRIES,
        "invocations": len(invocations),
        "provider_requests": len(attempts),
        "env": {
            "LLM_LIVE": os.environ.get("LLM_LIVE", ""),
            "JUDGE_LIVE": os.environ.get("JUDGE_LIVE", ""),
        },
        "judge": judge_meta,
        "metrics": metrics_block,
    })
    (run_dir / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return report
