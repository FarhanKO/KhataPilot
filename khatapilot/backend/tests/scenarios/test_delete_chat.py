"""Deleting from the chat box on the seeded demo shop (mock LLM): one entry, a reminder, delete-all held."""


def test_entry_made_by_mistake_can_be_deleted(app):
    app.chat("sold 3 bags of rice 1,777 cash")
    reply = app.chat("delete that entry")["reply"]
    assert reply.startswith("Deleted the entry: sale ৳1,777"), reply

    app.chat("sold soap 444 cash to Rahim")
    reply = app.chat("delete the 444 sale")["reply"]
    assert "৳444" in reply and reply.startswith("Deleted the entry"), reply
    assert "nothing was deleted" in app.chat("delete the 444 sale")["reply"]


def test_delete_reminder_never_sends_one(app):
    debtor = app.http.get("/api/debtors").json()[0]["name"]
    first = debtor.split()[0]
    reply = app.chat(f"delete the reminder for {first}")["reply"]
    assert reply.startswith("Done:") and "reminder" in reply, reply
    assert not any(p["tool"] == "send_reminders" for p in app.http.get("/api/approvals").json())


def test_delete_reminder_takes_them_out_of_a_waiting_batch(app):
    app.chat("remind Babul and Skyline")
    held = [p for p in app.http.get("/api/approvals").json() if p["tool"] == "send_reminders"]
    assert held and "Babul" in held[0]["summary"]
    reply = app.chat("delete the reminder for Babul")["reply"]
    assert "taken out of the reminders waiting" in reply, reply
    held = [p for p in app.http.get("/api/approvals").json() if p["tool"] == "send_reminders"]
    assert held and "Babul" not in held[0]["summary"]


def test_delete_all_is_held_for_confirmation(app):
    for text in ("delete all", "Delete all my files."):
        assert "Waiting for you: deleting all your shop's data" in app.chat(text)["reply"]
