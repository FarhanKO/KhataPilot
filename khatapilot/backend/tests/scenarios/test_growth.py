"""Growing the shop on the seeded demo shop (mock LLM): the plan is read from the books, changes when the
books change, and is reachable from chat without moving any money."""


def growth(app) -> dict:
    response = app.http.get("/api/growth")
    assert response.status_code == 200, response.text
    return response.json()


def test_growth_plan_from_the_books(app):
    plan = growth(app)
    flow = plan["flow"]
    assert flow["label"] == "September" and flow["money_in"][0]["key"] == "sales"
    assert flow["costs"][0]["key"] == "stock"
    inflow = sum(p["amount_paisa"] for p in flow["money_in"])
    outflow = sum(p["amount_paisa"] for p in flow["costs"])
    assert inflow - outflow == flow["profit_paisa"] == 2_305_300  # the savings plan's September profit
    assert sum(p["amount_paisa"] for p in flow["uses"]) == flow["profit_paisa"]
    assert 10 < flow["gross_margin_pct"] < 30

    # Meghna's bill takes cash below the buffer on the demo day, so growth waits for the cushion.
    assert plan["profile"]["stage"] == "cushion_first" and plan["profile"]["reinvest_pct"] == 0
    assert plan["levers"][0]["kind"] == "cushion"
    kinds = [lv["kind"] for lv in plan["levers"]]
    assert "collect_baki" in kinds and "supplier_terms" in kinds

    paths = {s["key"]: s for s in plan["scenarios"]}
    assert len(paths["as_now"]["months"]) == 12
    assert 0 < paths["reinvest"]["extra_vs_now_paisa"] < paths["collect_and_reinvest"]["extra_vs_now_paisa"]
    assert "once the cushion holds" in paths["reinvest"]["label"]

    # A good day's takings fill the cushion: the advice moves on to growing.
    app.chat("sold goods 60,000 cash")
    plan = growth(app)
    assert plan["profile"]["stage"] != "cushion_first" and plan["profile"]["reinvest_pct"] > 0
    assert plan["levers"][0]["kind"] != "cushion"


def test_growth_from_chat_reads_and_moves_nothing(app):
    wallet = app.wallet()
    reply = app.chat("how can I grow my business?")["reply"]
    assert reply.startswith("Cushion first:") and "What would help most: 1." in reply
    assert "estimates from your books" in reply
    assert app.wallet() == wallet
    assert not app.http.get("/api/approvals").json()


def test_while_the_cushion_is_short_collecting_baki_starts_now(app):
    plan = growth(app)
    assert plan["headline"].startswith("Cushion first. Start now: Collect ৳")
    profile = plan["profile"]
    assert profile["reinvest_choice_pct"] is None and profile["reinvest_wait"].startswith("once the cushion holds")
    assert "A usual month lately made about ৳" in profile["reinvest_why"]
    stock = next(s for s in plan["scenarios"] if s["key"] == "reinvest")
    assert stock["label"] == f"Put {profile['reinvest_suggested_pct'] or 5}% of profit back into stock {profile['reinvest_wait']}"


def test_the_owner_chooses_how_much_goes_back_into_stock_in_chat_and_on_the_page(app):
    wallet = app.wallet()
    out = app.chat("put 15% of profit back into stock")
    assert [o["status"] for o in out["outcomes"]] == ["ok"] and "your choice" in out["reply"]
    plan = growth(app)
    assert plan["profile"]["reinvest_choice_pct"] == 15
    stock = next(s for s in plan["scenarios"] if s["key"] == "reinvest")
    assert stock["label"].startswith("Put 15% of profit back into stock (your choice)")
    assert any("15% you chose" in line for line in plan["method"])

    # The Savings page's chips do the same; Auto hands it back to the books.
    response = app.http.put("/api/growth/reinvest", json={"share_pct": 25})
    assert response.status_code == 200 and response.json()["profile"]["reinvest_choice_pct"] == 25
    out = app.chat("let KhataPilot decide how much goes back into stock")
    assert growth(app)["profile"]["reinvest_choice_pct"] is None and "sizes it from your books" in out["reply"]
    assert app.wallet() == wallet and not app.http.get("/api/approvals").json(), "nothing moves"


def test_the_year_ahead_has_ramadan_and_eid_in_it(app):
    months = growth(app)["scenarios"][0]["months"]
    seasons = {m["label"]: m["season"] for m in months}
    assert seasons["Feb ’27"] == "Ramadan" and seasons["Mar ’27"] == "Ramadan and Eid" and seasons["Oct ’26"] is None
