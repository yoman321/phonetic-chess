"""Shared helpers for the machine-readable-move-prompt gates.

Not a test module. Both gate files import it by path so neither depends on
pytest's collection order.

The benchmark code itself lives outside the import path, under
plans/machine-readable-move-prompt/. `require_module` loads it by file path and
fails with the phase that was supposed to build it, so a red gate names missing
planned behaviour rather than an accidental ImportError.
"""
import hashlib
import importlib.util
import json
import pathlib

import pytest

TESTS_DIR = pathlib.Path(__file__).resolve().parent
BACKEND_DIR = TESTS_DIR.parent
REPO_ROOT = BACKEND_DIR.parent
FIXTURE_DIR = TESTS_DIR / "fixtures" / "machine_readable_move_prompt"
BENCH_DIR = REPO_ROOT / "plans" / "machine-readable-move-prompt"

CASES_PATH = FIXTURE_DIR / "cases.json"
OLD_PROMPTS_PATH = FIXTURE_DIR / "old-prompts.json"

CASE_COUNT = 20
ARMS = ("A", "B", "C")
INVOCATION_COUNT = 60

PHASE_OF_MODULE = {
    "generate_cases.py": "Phase 2 — frozen-set tools",
    "judge.py": "Phase 3 — CLI judge",
    "metrics.py": "Phase 4 — pure metrics",
    "run.py": "Phase 5 — runner and report",
}


def read_cases():
    return json.loads(CASES_PATH.read_text(encoding="utf-8"))


def cases():
    return read_cases()["cases"]


def read_old_prompts():
    return json.loads(OLD_PROMPTS_PATH.read_text(encoding="utf-8"))


def sha256_of(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def require_module(filename):
    """Import one benchmark module, or fail naming the phase that owns it."""
    path = BENCH_DIR / filename
    if not path.exists():
        pytest.fail(
            f"{path.relative_to(REPO_ROOT)} does not exist. "
            f"It is built by {PHASE_OF_MODULE.get(filename, 'the plan')}."
        )
    name = f"_bench_{filename[:-3]}"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def require_attr(module, attr):
    if not hasattr(module, attr):
        pytest.fail(
            f"{module.__name__.removeprefix('_bench_')}.py does not define "
            f"{attr!r}; the gates require it."
        )
    return getattr(module, attr)


# --- a provider stand-in ----------------------------------------------------
#
# Shaped like llm._client: chat.completions.with_raw_response.create(**kwargs)
# returning an object with .retries_taken and .parse().


class Usage:
    def __init__(self, prompt_tokens, completion_tokens):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.completion_tokens_details = None


class _Message:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content):
        self.message = _Message(content)


class _Completion:
    def __init__(self, content, usage):
        self.choices = [_Choice(content)]
        self.usage = usage


class _Raw:
    def __init__(self, completion, retries_taken):
        self._completion = completion
        self.retries_taken = retries_taken

    def parse(self):
        return self._completion


class FakeProvider:
    """Replies from a queue (or a callable) and records every create()."""

    def __init__(self, reply, usage=None, retries_taken=0, before_create=None):
        self.reply = reply              # str, list of str, or callable(kwargs)
        self.usage = usage or Usage(400, 40)
        self.retries_taken = retries_taken
        self.before_create = before_create
        self.calls = []

    def create(self, **kwargs):
        if self.before_create is not None:
            self.before_create(kwargs)
        self.calls.append(kwargs)
        reply = self.reply
        if callable(reply):
            reply = reply(kwargs)
        elif isinstance(reply, list):
            reply = reply[min(len(self.calls) - 1, len(reply) - 1)]
        if isinstance(reply, BaseException):
            raise reply
        usage = self.usage
        if callable(usage):
            usage = usage(kwargs)
        return _Raw(_Completion(reply, usage), self.retries_taken)

    def messages(self, index=-1):
        by_role = {m["role"]: m["content"] for m in self.calls[index]["messages"]}
        return by_role["system"], by_role["user"]

    @property
    def with_raw_response(self):
        return self

    @property
    def completions(self):
        return self

    @property
    def chat(self):
        return self
