"""Governed model calls: a campaign's model calls obey the same halt, pause and budget as its lab submissions.

Before this, halt and pause were enforced at the lab gateway only. A halted or paused campaign still called the model for
proposals, the critic and the reviewer, and nothing capped how many calls a campaign could make. Every model call now passes a
guard: a global halt refuses, a campaign or program pause holds until resumed, a budget refuses when exhausted, and each call
and each refusal is a ledger record.
"""
from __future__ import annotations

import hashlib
import os
import time
from dataclasses import replace
from typing import Any

from proef.core.contracts import Envelope, EventType
from proef.core.interfaces import record_for


class ModelRefused(RuntimeError):
    """A model call the campaign is not allowed to make right now (halt, pause timeout, or budget)."""


class ModelGuard:
    def __init__(self, store: Any, actor: Any, problem_id: str, governance: dict | None, max_calls: int,
                 say=None, pause_poll: float = 1.0, pause_timeout: float | None = None):
        self.store, self.actor, self.pid = store, actor, problem_id
        self.gov, self.max_calls = governance or {}, int(max_calls)
        self.say = say or (lambda m: None)
        self.pause_poll, self.pause_timeout = pause_poll, pause_timeout
        self._exhausted_recorded = False
        self._n: int | None = None            # calls made so far: scanned from the ledger ONCE, then counted in memory

    @property
    def env(self) -> Envelope:
        return Envelope(experiment_branch_id=f"problem/{self.pid}")

    def used(self) -> int:
        """Model calls already recorded on this campaign's branch. Fails CLOSED: a store that cannot be read must not report
        zero calls used and let a campaign overspend."""
        if self._n is not None:
            return self._n
        try:
            self._n = sum(1 for r in self.store.branch_records(f"problem/{self.pid}")
                          if (r.decision or {}).get("kind") == "model_call")
        except Exception as exc:  # noqa: BLE001
            raise ModelRefused(f"the model-call budget cannot be read ({type(exc).__name__}): refusing") from exc
        return self._n

    def _refuse(self, role: str, why: str, kind: str) -> None:
        self.store.append(record_for(self.env, EventType.gate_decision, self.actor,
                                     decision={"kind": kind, "role": role, "reason": why}))
        self.say(f"model call refused ({role}): {why}")
        raise ModelRefused(why)

    def check(self, role: str, prompt: str = "") -> None:
        halt = (self.gov.get("halt").engaged() if self.gov.get("halt") else None)
        if halt:
            self._refuse(role, f"[halt] operator stop-condition engaged: {halt.get('reason', '')}", "halt_refusal")
        pause = self.gov.get("pause")
        if pause is not None:
            prog = os.environ.get("PROEF_PROGRAM")
            if prog:
                from proef.program import _slug
                prog = _slug(prog)
            held_at = None
            while True:
                blocking = pause.blocking(self.pid, prog)
                if not blocking:
                    if held_at is not None:
                        # the hold is closed in the ledger, as the lab gateway closes its own
                        self.store.append(record_for(self.env, EventType.gate_decision, self.actor, decision={
                            "kind": "pause_released", "title": f"model call ({role})",
                            "held_seconds": round(time.monotonic() - held_at, 2)}))
                    break
                if self.gov.get("halt") and self.gov["halt"].engaged():
                    self._refuse(role, "[halt] engaged while paused", "halt_refusal")
                if held_at is None:
                    held_at = time.monotonic()
                    self.store.append(record_for(self.env, EventType.gate_decision, self.actor, decision={
                        "kind": "paused_hold", "scope": blocking.get("scope"), "reason": blocking.get("reason"),
                        "paused_by": blocking.get("by"), "title": f"model call ({role})"}))
                if self.pause_timeout is not None and time.monotonic() - held_at >= self.pause_timeout:
                    self._refuse(role, f"[paused] {blocking.get('scope')}: {blocking.get('reason') or 'no reason given'}",
                                 "paused_refusal")
                time.sleep(self.pause_poll)
        n = self.used()
        if n >= self.max_calls:
            if not self._exhausted_recorded:
                self._exhausted_recorded = True
                self.store.append(record_for(self.env, EventType.tool_invocation, self.actor, decision={
                    "kind": "budget_exhausted", "resource": "model_calls", "used": n, "cap": self.max_calls}))
            self._refuse(role, f"model-call budget exhausted ({n}/{self.max_calls})", "budget_refusal")
        self._n = n + 1
        self.store.append(record_for(self.env, EventType.tool_invocation, self.actor, decision={
            "kind": "model_call", "role": role, "n": n + 1, "prompt_hash": hashlib.sha256(prompt.encode()).hexdigest()[:12]}))


class GovernedModel:
    """Wraps a ModelProvider: same interface, but every call passes the guard first."""

    def __init__(self, inner: Any, role: str, guard: ModelGuard):
        self._inner, self._role, self._guard = inner, role, guard

    def __getattr__(self, name: str) -> Any:      # name, model_version, model...: whatever the provider exposes
        return getattr(self._inner, name)

    def generate(self, invocation: Any, prompt: str, *a: Any, **kw: Any) -> Any:
        self._guard.check(self._role, prompt)
        return self._inner.generate(invocation, prompt, *a, **kw)

    def predict(self, invocation: Any, kind: Any, payload: Any, *a: Any, **kw: Any) -> Any:
        self._guard.check(self._role, str(kind))
        return self._inner.predict(invocation, kind, payload, *a, **kw)


def govern_models(cfg: Any, store: Any, actor: Any, governance: dict, say=None) -> Any:
    """A copy of the campaign config whose model and critic go through one guard. The config passed in is not mutated."""
    if cfg.model is None and cfg.critic_model is None:
        return cfg
    from proef.autoloop import ResourceBudget
    budget = cfg.budget or ResourceBudget()
    guard = ModelGuard(store, actor, cfg.problem.problem_id or "unnamed", governance, budget.max_model_calls, say)
    return replace(cfg,
                   model=GovernedModel(cfg.model, "model", guard) if cfg.model is not None else None,
                   critic_model=GovernedModel(cfg.critic_model, "critic", guard) if cfg.critic_model is not None else None)
