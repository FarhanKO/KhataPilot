"""Fixed answers to questions about KhataPilot, their routing, and the calm reply when the AI is offline."""

from datetime import date, datetime, timezone

import pytest

from app.agent import faq
from app.agent.llm.mock_provider import MockProvider, plan_owner_text
from app.agent.llm.types import LLMError, SegmentBrief, TurnBrief
from app.agent.orchestrator import OFFLINE_REPLY, Orchestrator
from app.agent.routing import choose
from app.core.ids import new_id
from app.core.provenance import Provenance, Segment, Sender, SenderKind, TurnContext
from app.guard.api import ToolOutcome

TODAY = date(2026, 10, 12)


@pytest.mark.parametrize("question, key", [
    ("is this safe?", "safe"), ("Is my money safe with you", "safe"), ("can I trust you?", "safe"),
    ("is this connected to my real bank?", "real_bank"), ("is this real money?", "real_bank"),
    ("which AI model are you?", "model"), ("are you ChatGPT?", "model"),
    ("what if the AI is hacked?", "ai_fooled"), ("what about prompt injection", "ai_fooled"),
    ("where is my data stored?", "privacy"), ("do you sell my data?", "privacy"),
    ("why do you need my PIN?", "pin"), ("what does the STOP button do?", "stop"),
    ("what is the safety breaker?", "breaker"), ("why is everything frozen?", "breaker"),
    ("what is the invoice check", "invoice_check"), ("what are the tiers?", "tiers"),
    ("what is the audit log?", "audit"), ("do you work offline?", "offline"),
    ("do you speak bangla?", "languages"), ("how is my credit score calculated?", "credit"),
    ("should I invest in crypto?", "advice_limits"), ("what is baki?", "khata"),
    ("who made you?", "makers"), ("hello", "greeting"), ("thanks!", "thanks"), ("what can you do?", "abilities"),
    ("how do I delete my data?", "how_delete"), ("can you pay without my permission?", "own_initiative"),
    ("এটা কি নিরাপদ?", "safe"), ("তুমি কে?", "identity"), ("কোন এআই ব্যবহার করো?", "model"),
    ("আসল টাকা কি?", "real_bank"), ("তুমি কী করতে পারো?", "abilities"),
])
def test_questions_match_their_answer(question, key):
    found = faq.match(question)
    assert found is not None and found.key == key


@pytest.mark.parametrize("request_text", [
    "pay Rahim Traders 5000", "is it safe to pay Rahim 5000?", "sold 3 bags of rice ৳1,500 cash",
    "who owes me money?", "will I run short of cash?", "delete all my data", "make my report for september",
    "send 500 to 01712345678", "stop",
])
def test_requests_are_never_answered_from_the_fixed_text(request_text):
    assert faq.match(request_text) is None


def test_bangla_questions_get_bangla_answers():
    assert faq.answer("এটা কি নিরাপদ?") == faq.match("is this safe?").bn
    assert faq.answer("is this safe?") == faq.match("is this safe?").en


def brief(text: str) -> TurnBrief:
    return TurnBrief(sender="owner", sender_display="Owner", owner_text=text,
                     segments=(SegmentBrief(index=1, source="chat", text=text, kind="owner_text", parsed=None,
                                                  suspected_injection=False),),
                     today=TODAY, now=datetime(2026, 10, 12, 10, tzinfo=timezone.utc))


def test_fixed_answers_go_to_the_rules_even_in_bangla():
    assert choose(brief("which AI model are you?"), []).planner == "rules"
    assert choose(brief("তুমি কে?"), []).planner == "rules"
    assert choose(brief("why would I need a PIN, explain"), []).planner == "model"  # no fixed answer: the model


@pytest.mark.parametrize("text, tool, args", [
    ("make my report for september", "make_report", {"kind": "owner", "month": "2026-09"}),
    ("statement for the bank", "make_report", {"kind": "lender"}),
    ("last month er hisab er pdf dao", "make_report", {"kind": "owner", "month": "2026-09"}),
    ("ব্যাংকের জন্য রিপোর্ট", "make_report", {"kind": "lender"}),
    ("সেপ্টেম্বরের রিপোর্ট দাও", "make_report", {"kind": "owner", "month": "2026-09"}),
    ("export my data", "export_my_data", {}), ("আমার সব তথ্য ডাউনলোড", "export_my_data", {}),
    ("delete all my data", "erase_my_data", {}), ("amar sob data muche dao", "erase_my_data", {}),
    ("আমার সব তথ্য মুছে দাও", "erase_my_data", {}),
])
def test_the_offline_rules_read_data_requests(text, tool, args):
    [call] = plan_owner_text(text, TODAY)
    assert call.name == tool and call.arguments == args


def test_deleting_one_bill_is_not_deleting_everything():
    assert all(c.name != "erase_my_data" for c in plan_owner_text("delete the DESCO bill", TODAY))


class _Down:
    """A model that cannot be reached."""
    name, model = "down", "down-1"

    def session(self, **_):
        return self

    def user(self, *_):
        pass

    def step(self):
        raise LLMError("I could not reach the AI service. Check the internet connection.", transient=True)


class _Port:
    def descriptors(self):
        return []

    def submit(self, call, turn) -> ToolOutcome:  # pragma: no cover - nothing is planned
        raise AssertionError("no tool should run")


def turn(text: str) -> TurnContext:
    return TurnContext(request_id=new_id("req"), conversation_id="c", shop_id="s", channel="web",
                       sender=Sender(kind=SenderKind.OWNER, display="Owner"), received_at=datetime.now(timezone.utc),
                       segments=(Segment(text=text, provenance=Provenance.OWNER, source="chat"),))


def test_offline_without_the_rules_says_so_calmly():
    reply = Orchestrator(_Down()).handle(turn("tell me a joke"), history=[], port=_Port(),
                                         shop_name="Shop")
    assert reply.text == OFFLINE_REPLY and reply.route == "offline"


def test_offline_rules_that_do_not_understand_say_the_model_is_offline():
    agent = Orchestrator(_Down(), rules=MockProvider(), offline_fallback=True)
    reply = agent.handle(turn("tell me a joke please"), history=[], port=_Port(), shop_name="Shop")
    assert reply.route == "offline" and reply.text == OFFLINE_REPLY
