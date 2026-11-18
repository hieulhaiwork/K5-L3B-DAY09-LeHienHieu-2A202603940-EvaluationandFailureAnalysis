"""Shape C — orchestrator plus read-only investigators. Student work.

1. Build a handoff with `lab8.handoff.make_handoff`. Allowed tools are read-only.
   Never put parent_secret, raw headers, or the full claim row into facts.
2. `lab8.runtime.run_readonly_investigators` runs those tools.
3. The orchestrator quotes from the investigator's order view and calls
   `durability.issue_refund`. Investigators have no write registry.
   Pass the full quote; the host subtracts earlier refunds on the same order.
   `lookup_refund_history` is a read-only tool you may hand to an investigator.
4. Trace the sanitized handoff, not the parent context.

An LLM summary of the parent context is not a sanitizer. Use the allowlist.

Trace contract v1 (docs/contracts.md): return a trace_path to a JSON file written in this run,
{"run_id": <same run_id>, "events": [...]}, on every return path including early
ones. `lab8.trace.Trace` does this; your own tracer is fine if it matches.
"""

from __future__ import annotations

import sqlite3

from lab8.db import connect
from lab8.durability import InjectedCrash, issue_refund
from lab8.handoff import make_handoff
from lab8.mock_llm import complete
from lab8.runtime import dispatch, run_readonly_investigators
from lab8.schemas import Budget, RunResult, make_run_id
from lab8.shapes.common import finish, safe_dump
from lab8.trace import Trace


def run_claim(
    claim_id: str,
    *,
    seed: int,
    store_path: str,
    crash_at: str | None = None,
) -> RunResult:
    conn = connect(store_path)
    try:
        try:
            return _run(conn, claim_id, seed=seed, crash_at=crash_at)
        except InjectedCrash:
            # The fault lab needs the crash to escape (T12). Never swallow it.
            raise
        except Exception as exc:  # noqa: BLE001 - contract: run_claim must not raise
            return _internal_error(conn, claim_id, seed=seed, exc=exc)
    finally:
        conn.close()


def _internal_error(
    conn: sqlite3.Connection,
    claim_id: str,
    *,
    seed: int,
    exc: Exception,
) -> RunResult:
    """Fail closed with a valid trace. Only the exception type is logged, never its message."""
    trace = Trace(make_run_id("orchestrator", claim_id, seed))
    trace.add("error", {"reason": "internal_error", "type": type(exc).__name__})
    return finish(
        conn,
        shape="orchestrator",
        claim_id=claim_id,
        seed=seed,
        status="failed",
        proposal_amount=0,
        budget=Budget(),
        trace=trace,
        error_reason="internal_error",
    )


def _run(
    conn: sqlite3.Connection,
    claim_id: str,
    *,
    seed: int,
    crash_at: str | None,
) -> RunResult:
    budget = Budget()
    run_id = make_run_id("orchestrator", claim_id, seed)
    trace = Trace(run_id)

    claim = dispatch(conn, budget, "get_claim_view", {"claim_id": claim_id}, worker=False)
    if not claim.get("found"):
        trace.add("error", {"reason": "unknown_claim"})
        return finish(
            conn,
            shape="orchestrator",
            claim_id=claim_id,
            seed=seed,
            status="failed",
            proposal_amount=0,
            budget=budget,
            trace=trace,
            error_reason="unknown_claim",
        )

    plan_note = complete(
        budget,
        seed=seed,
        purpose="plan",
        prompt=f"claim_id={claim_id}; has_order={bool(claim.get('order_id'))}",
    )
    trace.add(
        "orchestrator_plan",
        {"has_order": bool(claim.get("order_id")), "plan_note": plan_note},
    )

    if not claim.get("order_id"):
        complete(budget, seed=seed, purpose="draft", prompt=f"claim={claim_id}; status=needs_info")
        trace.add("decision", {"status": "needs_info", "reason": "missing_order_id"})
        return finish(
            conn,
            shape="orchestrator",
            claim_id=claim_id,
            seed=seed,
            status="needs_info",
            proposal_amount=0,
            budget=budget,
            trace=trace,
            error_reason="missing_order_id",
        )

    handoff = make_handoff(
        request_id=f"REQ-{claim_id}",
        claim_id=claim_id,
        task="investigate_order_policy_and_quote",
        facts={},
        allowed_tools=[
            "lookup_order",
            "lookup_policy",
            "lookup_refund_history",
            "compute_compensation",
        ],
    )
    trace.add("handoff", safe_dump(handoff))

    investigated = run_readonly_investigators(
        conn,
        budget,
        handoff,
        principal_id=claim["principal_id"],
    )
    tools_out = investigated.get("tools") or {}
    order_view = tools_out.get("lookup_order") or {}
    refund_history = tools_out.get("lookup_refund_history") or {}
    policy_view = tools_out.get("lookup_policy") or {}

    trace.add(
        "investigator",
        {
            "lookup_order": safe_dump(order_view),
            "lookup_refund_history": safe_dump(refund_history),
            "policy_count": len(policy_view.get("policies") or []),
        },
    )

    if not order_view.get("visible"):
        complete(budget, seed=seed, purpose="draft", prompt=f"claim={claim_id}; status=escalated")
        trace.add(
            "decision",
            {"status": "escalated", "reason": order_view.get("reason")},
        )
        return finish(
            conn,
            shape="orchestrator",
            claim_id=claim_id,
            seed=seed,
            status="escalated",
            proposal_amount=0,
            budget=budget,
            trace=trace,
            error_reason=order_view.get("reason"),
        )

    quote_payload = tools_out.get("compute_compensation")
    if quote_payload is None:
        quote_payload = dispatch(
            conn,
            budget,
            "compute_compensation",
            {"order": order_view["order"]},
            worker=False,
        )
    proposal = int(quote_payload["amount_vnd"])
    trace.add("quote", safe_dump(quote_payload))

    if proposal <= 0:
        complete(
            budget,
            seed=seed,
            purpose="draft",
            prompt=f"claim={claim_id}; status=denied; proposal=0",
        )
        trace.add(
            "decision",
            {
                "status": "denied",
                "proposal_amount": 0,
                "reason": quote_payload.get("reason"),
            },
        )
        return finish(
            conn,
            shape="orchestrator",
            claim_id=claim_id,
            seed=seed,
            status="denied",
            proposal_amount=0,
            budget=budget,
            trace=trace,
            error_reason=quote_payload.get("reason"),
        )

    try:
        outcome = issue_refund(conn, claim_id, proposal, crash_at=crash_at)
    except InjectedCrash:
        trace.add("crash", {"at": crash_at})
        trace.write()
        raise

    if not outcome["ok"]:
        status = "failed" if outcome.get("conflict") else "denied"
        complete(
            budget,
            seed=seed,
            purpose="draft",
            prompt=f"claim={claim_id}; status={status}; proposal={proposal}",
        )
        trace.add(
            "decision",
            {
                "status": status,
                "proposal_amount": proposal,
                "reason": outcome.get("reason"),
            },
        )
        return finish(
            conn,
            shape="orchestrator",
            claim_id=claim_id,
            seed=seed,
            status=status,
            proposal_amount=proposal,
            budget=budget,
            trace=trace,
            error_reason=outcome.get("reason"),
        )

    complete(
        budget,
        seed=seed,
        purpose="draft",
        prompt=f"claim={claim_id}; status=resolved; proposal={proposal}",
    )
    trace.add(
        "decision",
        {
            "status": "resolved",
            "proposal_amount": proposal,
            "refund_reason": outcome.get("reason"),
        },
    )
    return finish(
        conn,
        shape="orchestrator",
        claim_id=claim_id,
        seed=seed,
        status="resolved",
        proposal_amount=proposal,
        budget=budget,
        trace=trace,
        error_reason=None,
    )
