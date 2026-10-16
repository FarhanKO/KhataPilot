"""End-to-end flows through the guard, the payments module and the real sandbox provider."""

import json
from datetime import timedelta

from app.core.money import taka
from app.guard.models import PendingAction
from app.modules.payments.schemas import PAYMENT_KIND, ExecutionRefused
from tests.guard_harness import PIN, SHOP, WALLET, turn


def test_a_tier_2_payment_runs_end_to_end(world):
    before = world.wallet_balance()
    out = world.pay(turn("pay Rahim Traders 8000"), payee_name="Rahim Traders", amount_taka=8000)
    assert (out.status, out.tier) == ("ok", 2)
    assert "Paid ৳8,000 to Rahim Traders" in out.message
    assert world.wallet_balance() == before - taka(8000)

    payment = out.data["payment"]
    with world.guard() as guard:
        booked = guard.ledger.entries(SHOP, limit=1)[0]
        payable = next(o for o in guard.ledger.open_obligations(SHOP) if o.counterparty_name == "Rahim Traders")
    assert (booked.kind, booked.trx_id, booked.amount_paisa) == ("supplier_payment", payment["sandbox_txn_id"], taka(8000))
    assert payable.amount_paisa == taka(34_751 - 8000)

    decision = world.audit.entries(event="decision")[-1]
    execution = world.audit.entries(event="execution")[-1]
    assert (decision.decision, execution.decision) == ("ALLOWED", "EXECUTED")
    assert execution.sandbox_ref == payment["sandbox_txn_id"]
    provider = world.sandbox.oplog()[-1]
    assert (provider["operation"], provider["status"], provider["decision_id"]) == ("payment", "ok", decision.decision_id)
    assert world.audit.verify().ok


def test_a_scheduled_payment_runs_on_its_day(world):
    out = world.pay(turn("pay Rahim Traders 8000 on Friday"), payee_name="Rahim Traders", amount_taka=8000,
                    execute_on="2026-10-16")
    assert out.status == "ok" and out.data["payment"]["status"] == "scheduled"
    before = world.wallet_balance()
    with world.guard() as guard:
        assert guard.scheduler.run_due(request_id="mon") == []
    world.clock.advance(timedelta(days=4))
    with world.guard() as guard:
        [result] = guard.scheduler.run_due(request_id="fri")
    assert result.status == "completed"
    assert world.wallet_balance() == before - taka(8000)


def test_over_the_limit_waits_for_the_pin(world):
    out = world.pay(turn("pay Rahim Traders 15000"), payee_name="Rahim Traders", amount_taka=15000)
    assert (out.status, out.tier, out.requires_pin) == ("held", 3, True)
    before = world.wallet_balance()
    with world.guard() as guard:
        wrong = guard.approvals.approve(out.pending_id, pin="0000", request_id="r1")
    assert (wrong.status, wrong.attempts_left) == ("wrong_pin", 2)
    assert world.wallet_balance() == before
    with world.guard() as guard:
        approved = guard.approvals.approve(out.pending_id, pin=PIN, request_id="r2")
    assert approved.ok and approved.status == "completed"
    assert world.wallet_balance() == before - taka(15000)
    assert [e.decision for e in world.audit.entries(event="pin")] == ["PIN_WRONG", "PIN_OK"]


def test_three_wrong_pins_lock_money_actions_for_fifteen_minutes(world):
    held = world.pay(turn("pay Rahim Traders 15000"), payee_name="Rahim Traders", amount_taka=15000)
    with world.guard() as guard:
        results = [guard.approvals.approve(held.pending_id, pin="0000", request_id=f"r{i}") for i in range(3)]
    assert [r.status for r in results] == ["wrong_pin", "wrong_pin", "locked"]
    blocked = world.pay(turn("pay Rahim Traders 500"), payee_name="Rahim Traders", amount_taka=500)
    assert blocked.status == "blocked" and "locked" in blocked.message
    world.clock.advance(timedelta(minutes=15, seconds=1))
    with world.guard() as guard:
        assert guard.approvals.approve(held.pending_id, pin=PIN, request_id="r9").ok


def test_kill_switch_freezes_everything_and_resumes_with_the_pin(world):
    world.pay(turn("pay Rahim Traders 8000 on Friday"), payee_name="Rahim Traders", amount_taka=8000,
              execute_on="2026-10-16")
    with world.guard() as guard:
        stopped = world.owner(guard).stop(reason="Owner pressed STOP", request_id="stop")
    assert stopped.frozen and stopped.paused_payments == 1
    assert world.sandbox.account(WALLET)["frozen"], "the provider freezes the account too"

    refused = world.pay(turn("pay Rahim Traders 500"), payee_name="Rahim Traders", amount_taka=500)
    assert refused.status == "blocked" and "STOP is on" in refused.message

    # Defence in depth: even a correctly signed payment is refused by the provider while frozen.
    with world.guard() as guard:
        record = guard.payments.create(payee_name="Rahim Traders", payee_number="01712345678", method="bkash",
                                       amount_paisa=taka(100), execute_on=None, decision_id="test", request_id="t",
                                       tier=2, with_pin=False)
        auth = world.deps.signer.mint(kind=PAYMENT_KIND, params=guard.payments.command_for(record).hash_params(),
                                      idempotency_key=record.idempotency_key, tier=2, decision_id="test")
        attempt = guard.payments.execute(record.id, auth)
    assert attempt.status == "failed" and attempt.failure.startswith("ACCOUNT_FROZEN")

    world.clock.advance(timedelta(days=4))
    with world.guard() as guard:
        assert guard.scheduler.run_due(request_id="fri") == [], "paused payments do not run"
        assert world.owner(guard).resume("0000", request_id="r").frozen, "resuming needs the right PIN"
    with world.guard() as guard:
        resumed = world.owner(guard).resume(PIN, request_id="r2")
    assert not resumed.frozen and resumed.resumed_payments == 1
    assert not world.sandbox.account(WALLET)["frozen"]
    with world.guard() as guard:
        [result] = guard.scheduler.run_due(request_id="fri-2")
    assert result.status == "completed"
    assert [e.decision for e in world.audit.entries(event="kill_switch")] == ["FROZEN", "RESUMED"]


def test_a_retry_never_pays_twice(world):
    before = world.wallet_balance()
    with world.guard() as guard:
        record = guard.payments.create(payee_name="Rahim Traders", payee_number="01712345678", method="bkash",
                                       amount_paisa=taka(1000), execute_on=None, decision_id="d", request_id="r",
                                       tier=2, with_pin=False)
        command = guard.payments.command_for(record)
        auth = world.deps.signer.mint(kind=PAYMENT_KIND, params=command.hash_params(),
                                      idempotency_key=record.idempotency_key, tier=2, decision_id="d")
        assert guard.payments.execute(record.id, auth).status == "completed"
        try:
            guard.payments.execute(record.id, auth)
            raise AssertionError("a completed payment must not execute again")
        except ExecutionRefused:
            pass
    replay = world.sandbox.execute_payment(command.payload(), authorization=auth.header,
                                           idempotency_key=auth.idempotency_key)
    assert replay["idempotent_replay"] is True
    assert world.wallet_balance() == before - taka(1000)


def test_a_held_action_cannot_be_changed_before_approval(world):
    held = world.pay(turn("pay Rahim Traders 15000"), payee_name="Rahim Traders", amount_taka=15000)
    with world.db.session() as session:
        row = session.get(PendingAction, held.pending_id)
        data = json.loads(row.action_json)
        data["payment"]["amount_paisa"] = taka(150_000)
        row.action_json = json.dumps(data)
    before = world.wallet_balance()
    with world.guard() as guard:
        result = guard.approvals.approve(held.pending_id, pin=PIN, request_id="r")
    assert result.status == "refused" and "changed" in result.message
    assert world.wallet_balance() == before


def test_a_scheduled_payment_is_held_if_the_payee_is_no_longer_trusted(world):
    world.pay(turn("pay Rahim Traders 8000 on Friday"), payee_name="Rahim Traders", amount_taka=8000,
              execute_on="2026-10-16")
    with world.guard() as guard:
        rahim = guard.store.payee_by_name("Rahim Traders")
        assert world.owner(guard).remove_payee(PIN, payee_id=rahim.id, request_id="r").ok
    world.clock.advance(timedelta(days=4))
    before = world.wallet_balance()
    with world.guard() as guard:
        [result] = guard.scheduler.run_due(request_id="fri")
    assert result.status == "failed" and "no longer on your trusted list" in result.message
    assert world.wallet_balance() == before
    assert "Held scheduled payment" in world.audit.entries(event="alert")[-1].summary


def test_owner_controls_need_the_pin(world):
    with world.guard() as guard:
        owner = world.owner(guard)
        assert not owner.set_limits("0000", per_txn_taka=50_000, daily_taka=None, request_id="r").ok
        assert owner.set_limits(PIN, per_txn_taka=20_000, daily_taka=60_000, request_id="r").ok
        assert guard.store.limits().per_txn_paisa == taka(20_000)
        assert not owner.add_payee("0000", name="New Shop", number="01822334455", method="bkash", request_id="r").ok
    out = world.pay(turn("pay Rahim Traders 15000"), payee_name="Rahim Traders", amount_taka=15000)
    assert out.tier == 2, "the owner raised the limit, so ৳15,000 is now within it"


def test_the_audit_chain_verifies_after_a_busy_session(world):
    world.pay(turn("pay Rahim Traders 8000"), payee_name="Rahim Traders", amount_taka=8000)
    world.pay(turn("pay Rahim Traders 15000"), payee_name="Rahim Traders", amount_taka=15000)
    world.submit("get_balances", turn("balance?"))
    report = world.audit.verify()
    assert report.ok and report.entries == 4
    assert [e.decision for e in world.audit.entries()] == ["ALLOWED", "EXECUTED", "HELD_FOR_PIN", "ALLOWED"]
