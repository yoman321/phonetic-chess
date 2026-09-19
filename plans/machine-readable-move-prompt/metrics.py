"""Pure calculations for the machine-readable move-prompt benchmark."""


def stability(first, second):
    keys = set(first) | set(second)
    matched = sum(
        1 for key in keys
        if first.get(key) is not None
        and second.get(key) is not None
        and first[key]["label"] == second[key]["label"]
    )
    total = len(keys)
    return {"matched": matched, "total": total, "rate": matched / total if total else 0.0}


def agreement(first, second):
    keys = set(first) | set(second)
    comparable_keys = [
        key for key in keys
        if first.get(key) is not None and second.get(key) is not None
    ]
    matched = sum(1 for key in comparable_keys if first[key] == second[key])
    comparable = len(comparable_keys)
    return {
        "matched": matched,
        "comparable": comparable,
        "unparsed": len(keys) - comparable,
        "rate": matched / comparable if comparable else 0.0,
    }


def intensity_delta(first, second):
    keys = [
        key for key in set(first) | set(second)
        if first.get(key) is not None and second.get(key) is not None
    ]
    total = sum(abs(first[key] - second[key]) for key in keys)
    return {"mean_abs": total / len(keys) if keys else None, "n": len(keys)}


def off_list_rate(invocations):
    total = len(invocations)
    off = sum(bool(item["off_list"]) for item in invocations)
    return {"off": off, "total": total, "rate": off / total if total else 0.0}


def validity(invocations):
    return {
        "total": len(invocations),
        "first_attempt_parsed": sum(bool(item["first_attempt_parsed"]) for item in invocations),
        "legal_uci": sum(bool(item["legal_uci"]) for item in invocations),
        "nonempty_tone": sum(bool(item["tone_summary"].strip()) for item in invocations),
    }


def token_saving(old_tokens, new_tokens):
    if not old_tokens or not new_tokens or any(
        token is None for token in [*old_tokens, *new_tokens]
    ):
        return {"old_mean": None, "new_mean": None, "saved": None}
    old_mean = sum(old_tokens) / len(old_tokens)
    new_mean = sum(new_tokens) / len(new_tokens)
    return {
        "old_mean": old_mean,
        "new_mean": new_mean,
        "saved": old_mean - new_mean,
    }


def verdict(values):
    reasons = []
    prerequisites = [
        (values["tokens_complete"], "provider token usage is incomplete"),
        (values["stability_rate"] >= 0.90, "judge stability is below 0.90"),
        (values["floor_comparable"] >= 18, "OLD floor has fewer than 18 pairs"),
        (values["cross_comparable"] >= 18, "NEW cross has fewer than 18 pairs"),
    ]
    for passed, reason in prerequisites:
        if not passed:
            reasons.append(reason)
    if reasons:
        return {"status": "inconclusive", "reasons": reasons}

    failures = [
        (values["saved_tokens"] >= 90, "prompt saves fewer than 90 tokens"),
        (
            values["cross_rate"] >= values["floor_rate"] - 0.15,
            "tone agreement fell more than 0.15 below the noise floor",
        ),
        (
            values["off_list_new"] <= values["off_list_old"] + 0.15,
            "NEW off-list rate exceeds OLD A by more than 0.15",
        ),
    ]
    reasons = [reason for passed, reason in failures if not passed]
    return {"status": "fail" if reasons else "pass", "reasons": reasons}
