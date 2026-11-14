"""Watch that KhataPilot is up, and tell the owner by SMS when it goes down and when it is back.

    python scripts/uptime_check.py                 # check every 60 s, forever
    python scripts/uptime_check.py --once          # one check (Task Scheduler / cron), exit 1 if down
    python scripts/uptime_check.py --every 30 --fails 3

It checks the backend's /api/health and the provider's /health. After `--fails` failed checks in a row it
texts OWNER_PHONE through the owner's phone (PHONE_GATEWAY_URL; the backend itself may be the thing that is
down, so this script talks to the phone directly) and writes the event to data_dir/logs/uptime.jsonl. One SMS
when it goes down, one when it is back; state is kept in data_dir/logs/uptime_state.json between runs.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path[:0] = [str(ROOT / "backend"), str(ROOT)]

from app.core.config import get_settings  # noqa: E402
from app.core.masking import e164, normalize_msisdn  # noqa: E402
from app.infra.phone_gateway import PhoneGateway, PhoneGatewayError  # noqa: E402


def probe(url: str, timeout: float = 5.0) -> str | None:
    """None when it answers 200 in time, else what went wrong."""
    try:
        response = httpx.get(url, timeout=timeout)
    except httpx.HTTPError as exc:
        return f"{exc.__class__.__name__}"
    return None if response.status_code == 200 else f"HTTP {response.status_code}"


def text_owner(settings, message: str) -> str:
    number = normalize_msisdn(settings.owner_phone or "")
    if not (number and settings.phone_gateway_url):
        return "no SMS (set OWNER_PHONE and PHONE_GATEWAY_URL)"
    phone = PhoneGateway(settings.phone_gateway_url, username=settings.phone_gateway_user,
                         password=settings.phone_gateway_password.get_secret_value())
    try:
        phone.send_sms([e164(number)], message[:300])
        return "texted the owner"
    except PhoneGatewayError as exc:
        return f"SMS failed: {exc}"
    finally:
        phone.close()


def check(settings, targets: dict[str, str], state: dict, fails_needed: int) -> tuple[bool, str | None]:
    problems = {name: why for name, url in targets.items() if (why := probe(url))}
    now = datetime.now(UTC).isoformat(timespec="seconds")
    state["fails"] = state.get("fails", 0) + 1 if problems else 0
    event = None
    if problems and state["fails"] >= fails_needed and not state.get("down"):
        state["down"], state["down_since"] = True, now
        what = ", ".join(f"{n} ({w})" for n, w in problems.items())
        event = f"KhataPilot is DOWN since {now[11:16]} UTC: {what}. Payments and alerts wait until it is back."
    elif not problems and state.get("down"):
        event = f"KhataPilot is back up (it was down since {state.get('down_since', '?')[11:16]} UTC)."
        state["down"] = False
    return not problems, event


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--backend", default="http://127.0.0.1:8000/api/health")
    parser.add_argument("--sandbox", default=f"{settings.sandbox_url.rstrip('/')}/health")
    parser.add_argument("--every", type=int, default=60)
    parser.add_argument("--fails", type=int, default=2, help="failed checks in a row before it counts as down")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    logs = settings.data_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    state_file = logs / "uptime_state.json"
    state = json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else {}
    targets = {"backend": args.backend, "provider": args.sandbox}
    while True:
        up, event = check(settings, targets, state, args.fails)
        state_file.write_text(json.dumps(state), encoding="utf-8")
        if event:
            sent = text_owner(settings, event)
            line = {"ts": datetime.now(UTC).isoformat(timespec="seconds"), "event": event, "sms": sent}
            with (logs / "uptime.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(line) + "\n")
            print(json.dumps(line), flush=True)
        if args.once:
            return 0 if up else 1
        time.sleep(max(5, args.every))


if __name__ == "__main__":
    raise SystemExit(main())
