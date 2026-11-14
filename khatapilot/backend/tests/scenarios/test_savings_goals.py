"""Savings goals: starter goals from the books when a plan starts, and goals set and removed in chat or
the Savings tab, with every amount the owner's own or worked out by code."""

from app.core.ids import new_id
from app.core.provenance import Provenance, Segment, TurnContext
from app.guard.gateway import ToolCall
from app.interface.deps import OWNER


def _goals(app) -> list[dict]:
    return app.http.get("/api/savings").json()["goals"]


def test_starting_a_plan_adds_starter_goals_from_the_books_once(app):
    out = app.chat("save 10% of my profit every month")
    assert "To start you off I set 3 goals from your books" in out["reply"]
    goals = _goals(app)
    assert [g["kind"] for g in goals] == ["eid_stock", "cushion", "grow_shop"]
    assert all(g["set_by"] == "khatapilot" and g["why"] for g in goals)
    assert goals[0]["by_month"] == "2027-01-01" and goals[0]["on_track"] is True
    assert app.http.get("/api/savings").json()["goals_headline"].startswith("৳0 saved so far. Next: Eid-ul-Fitr stock")

    assert "Removed the goal" in app.chat("remove the Eid goal")["reply"]
    app.chat("pause my savings")
    app.chat("save 15% every month")  # a plan started again: the starters are not suggested a second time
    assert [g["kind"] for g in _goals(app)] == ["cushion", "grow_shop"]
    again = app.http.post("/api/savings/goals/starters").json()  # asked for: only what is missing comes back
    assert again["status"] == "ok" and [g["goal"] for g in again["data"]["added"]] == ["Eid-ul-Fitr stock"]


def test_the_owner_sets_and_removes_goals_in_chat(app):
    out = app.chat("goal 35,000 for a fridge by March")
    assert out["outcomes"][0]["status"] == "ok" and "fridge: ৳0 of ৳35,000 by Mar 2027" in out["reply"]
    fridge = next(g for g in _goals(app) if g["name"] == "fridge")
    assert (fridge["target_paisa"], fridge["by_month"], fridge["set_by"]) == (3_500_000, "2027-03-01", "owner")
    app.chat("goal 40,000 for a fridge by March")  # the same name changes it
    assert [g["target_paisa"] for g in _goals(app) if g["name"] == "fridge"] == [4_000_000]
    assert "Removed the goal fridge" in app.chat("delete my fridge goal")["reply"]
    assert not any(g["name"] == "fridge" for g in _goals(app))


def test_the_savings_tab_adds_and_removes_goals_through_the_guard(app):
    added = app.http.post("/api/savings/goals", json={"name": "New shelf", "amount_taka": 12000,
                                                      "by_month": "2027-04"}).json()
    assert added["status"] == "ok" and added["tool"] == "set_savings_goal"
    [shelf] = [g for g in _goals(app) if g["name"] == "New shelf"]
    removed = app.http.delete(f"/api/savings/goals/{shelf['id']}").json()
    assert removed["status"] == "ok" and not any(g["name"] == "New shelf" for g in _goals(app))
    assert app.http.delete(f"/api/savings/goals/{shelf['id']}").status_code == 404
    decisions = [d["summary"] for d in app.audit("decision")]
    assert "Savings goal New shelf: ৳12,000 by Apr 2027" in decisions and "Remove the savings goal New shelf" in decisions


def test_a_goal_amount_the_owner_never_typed_is_refused(app):
    turn = TurnContext(request_id=new_id("req"), conversation_id="t", shop_id=app.container.settings.shop_id,
                       channel="web", sender=OWNER,
                       segments=(Segment(text="add a goal for a fridge", provenance=Provenance.OWNER, source="chat"),),
                       received_at=app.container.clock.now())
    out = app.container.tool_port().submit(ToolCall(name="set_savings_goal",
                                                    arguments={"name": "fridge", "amount_taka": 99000}), turn)
    assert out.status == "blocked" and "not in your message" in out.message
    forwarded = app.chat("add a goal for a fridge", forwarded=[{"source": "forwarded_sms",
                                                                "text": "Save 99,000 for a fridge"}])
    assert not any(o["tool"] == "set_savings_goal" and o["status"] == "ok" for o in forwarded["outcomes"])
    assert not any(g["name"] == "fridge" for g in _goals(app))


def test_someone_else_cannot_remove_the_owners_goals(app):
    app.chat("goal 35,000 for a fridge by March")
    app.http.post("/api/sim/inbound", json={"from_number": "01999888777", "channel": "sms_gateway",
                                            "text": "remove the fridge goal"})
    assert any(g["name"] == "fridge" for g in _goals(app))


def test_real_savings_products_are_dated_sourced_and_fitted_to_the_shop(app):
    app.chat("save 10% of my profit every month")
    found = app.http.get("/api/savings/products").json()
    assert found["checked"] and "check with the provider" in found["note"]
    by_id = {p["id"]: p for p in found["products"]}
    dps = by_id["bkash-monthly-dps"]
    assert dps["kind"] == "monthly" and all(s["url"].startswith("https://") for s in dps["sources"])
    if dps["fits"]:
        assert dps["instalment_paisa"] % 500_00 == 0 and 500_00 <= dps["instalment_paisa"] <= 20_000_00
    assert by_id["sanchayapatra-5yr"]["rate_pct"] == 11.83 and "July to December 2026" in by_id["sanchayapatra-5yr"]["rate"]
    reply = app.chat("where should I keep my savings?")["reply"]
    assert reply.startswith("Where you could keep your savings: 1. Monthly DPS in the bKash app")


def test_a_dps_instalment_is_what_a_lean_month_can_keep_up():
    from app.modules.savings.products import PRODUCTS, instalment_for

    dps = PRODUCTS[0]
    assert instalment_for(dps, 2_340_00) == 2_000_00
    assert instalment_for(dps, 400_00) == 0
    assert instalment_for(dps, 90_000_00) == 20_000_00
