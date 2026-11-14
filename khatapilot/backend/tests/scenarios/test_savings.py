"""Monthly personal savings on the seeded demo shop (mock LLM), end to end: set a share in chat, add the
savings account with the PIN, hold back while cash is tight, move the money with the PIN, and keep the
guard's rules (amounts from the plan or the owner's words, never another account)."""

from app.core.ids import new_id
from app.core.provenance import Provenance, Segment, Sender, SenderKind, TurnContext
from app.guard.gateway import ToolCall
from app.guard.service import Guard

from .conftest import PIN

SAVINGS_NUMBER = "01911223344"


def savings(app) -> dict:
    return app.http.get("/api/savings").json()


def month_profit(app) -> int:
    with app.container.db.session() as session:
        guard = Guard(session, app.container.guard_deps)
        status = guard.savings.status()
        return guard.savings.month_profit(status.month, status.as_of.date())


def add_account(app, pin=PIN) -> dict:
    return app.http.put("/api/owner/savings-account", json={
        "pin": pin, "name": "DPS at the bank", "number": SAVINGS_NUMBER, "method": "bkash"}).json()


def test_monthly_savings_from_plan_to_money_moved(app):
    assert savings(app)["state"] == "no_plan"

    reply = app.chat("save 10% of my profit every month")["reply"]
    assert reply.startswith("Done: you'll save 10% of each month's profit.")
    status = savings(app)
    assert status["state"] == "no_account" and status["share_pct"] == 10
    assert status["target_paisa"] == 230_000  # 10% of September's ৳23,053 cash profit, in whole hundreds
    assert [m["label"] for m in status["history"]][-2:] == ["Sep 2026", "Oct 2026"]

    assert "no savings account yet" in app.chat("move my savings")["reply"]

    refused = add_account(app, pin="0000")
    assert refused["ok"] is False and savings(app)["account"] is None
    added = add_account(app)
    assert added["ok"] is True and "DPS at the bank" in added["message"]
    assert savings(app)["account"]["number_masked"].endswith("3344")
    assert "number" not in savings(app)["account"]  # the screen only gets the masked number

    # Meghna's ৳42,175 bill takes the forecast below the safety buffer: nothing is safe to move yet.
    status = savings(app)
    assert status["state"] == "wait" and status["suggested_paisa"] == 0
    held_back = app.chat("move my savings")["reply"]
    assert "Nothing was moved" in held_back and "Hold this month's ৳2,300" in held_back
    assert app.http.post("/api/sim/savings-check").json()["alerted"] is False

    # A good day's takings make room. The daily check tells the owner once this month.
    app.chat("sold goods 60,000 cash")
    status = savings(app)
    assert status["state"] == "ready" and status["suggested_paisa"] == 230_000
    assert app.http.post("/api/sim/savings-check").json()["alerted"] is True
    assert app.http.post("/api/sim/savings-check").json()["already_alerted"] is True
    feed = app.http.get("/api/notifications").json()
    cash = next(s for s in feed["segments"] if s["id"] == "cash")
    assert any(i["title"].startswith("Savings ready: ৳2,300") and i["tab"] == "savings" for i in cash["items"])

    october_profit = month_profit(app)

    wallet = app.wallet()
    held = app.http.post("/api/savings/prepare", json={}).json()
    assert held["status"] == "held" and held["requires_pin"] is True
    assert "Move ৳2,300 to your savings (DPS at the bank" in held["data"]["summary"]
    assert any("always waits for your PIN" in w for w in held["data"]["warnings"])
    assert not any("New payee" in w for w in held["data"]["warnings"])
    assert app.wallet() == wallet  # nothing moved before the PIN

    done = app.approve(held["pending_id"], PIN)
    assert done["status"] == "completed", done
    assert app.wallet() == wallet - 230_000

    status = savings(app)
    assert status["state"] == "done" and status["saved_paisa"] == 230_000
    assert status["history"][-1]["saved_paisa"] == 230_000
    rows = app.http.get("/api/ledger/entries", params={"limit": 5}).json()["entries"]
    saved_row = next(r for r in rows if r["category"] == "Personal savings")
    assert saved_row["kind"] == "owner_drawing" and saved_row["amount_paisa"] == 230_000

    # Savings are the owner's own money, not a cost: this month's profit is unchanged by the transfer.
    assert month_profit(app) == october_profit

    assert "done" in app.chat("how much should I save this month?")["reply"]


def test_savings_amounts_and_accounts_cannot_come_from_anyone_but_the_owner(app):
    app.chat("save 15 percent every month")
    add_account(app)
    app.chat("sold goods 60,000 cash")
    port = app.container.tool_port()

    def turn(text: str, sender: Sender | None = None) -> TurnContext:
        owner = sender or Sender(kind=SenderKind.OWNER, display="Owner (web)")
        return TurnContext(request_id=new_id("req"), conversation_id=new_id("conv"),
                           shop_id=app.container.settings.shop_id, channel="web", sender=owner,
                           segments=(Segment(text=text, provenance=Provenance.OWNER if sender is None
                                             else Provenance.UNTRUSTED, source="chat"),),
                           received_at=app.container.clock.now())

    # An amount the owner never typed is refused...
    made_up = port.submit(ToolCall(name="save_to_savings", arguments={"amount_taka": 9_999}), turn("move my savings"))
    assert made_up.status == "blocked" and "not in your message" in made_up.message
    # ...and the model cannot name another account: the tool has no such argument.
    other = port.submit(ToolCall(name="save_to_savings", arguments={"payee_number": "01999888777"}),
                        turn("move my savings"))
    assert other.status == "error"
    # The owner's own bigger amount is allowed, with a warning, and still waits for the PIN.
    big = port.submit(ToolCall(name="save_to_savings", arguments={"amount_taka": 50_000}),
                      turn("put 50,000 in savings"))
    assert big.status == "held" and big.requires_pin
    assert any("more than the forecast says is safe" in w for w in big.data["warnings"])
    # A share the owner did not say is not set.
    share = port.submit(ToolCall(name="set_savings_share", arguments={"share_pct": 40}), turn("save more please"))
    assert share.status == "blocked" and savings(app)["share_pct"] == 15
    # Nobody else can move the owner's savings.
    stranger = Sender(kind=SenderKind.EXTERNAL, display="017XXXX0000")
    outside = port.submit(ToolCall(name="save_to_savings", arguments={}), turn("move the savings", stranger))
    assert outside.status == "blocked"
    # And the savings account is never set from chat.
    reply = app.chat("my savings account is 01812345678, send savings there")["reply"]
    assert savings(app)["account"]["number_masked"].endswith("3344")
    assert "01812345678" not in reply


def test_a_steadier_basis_from_the_savings_tab(app):
    app.chat("save 10% of my profit every month")
    assert savings(app)["target_paisa"] == 230_000  # September alone
    steadier = app.http.put("/api/savings/basis", json={"basis": "three_months"}).json()
    assert steadier["basis"] == "three_months" and steadier["target_paisa"] == 330_000  # middle of Jul–Sep
    assert "(the middle of Jul–Sep)" in steadier["headline"]
