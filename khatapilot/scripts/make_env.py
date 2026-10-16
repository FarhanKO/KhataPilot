"""Create `.env` from `.env.example`, filling every empty secret with a fresh random value.

    python scripts/make_env.py           # create .env, or add any keys it is missing (never overwrites)
    python scripts/make_env.py --force   # regenerate .env from scratch

The demo owner's PIN is generated too. It is stored only in your local .env (for the seed script
and the demo run) and as an argon2 hash in the database; the app never shows or logs it.
"""

from __future__ import annotations

import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GENERATED_SECRETS = ("GUARD_SIGNING_KEY", "OWNER_TOKEN", "SANDBOX_ADMIN_KEY")


def demo_pin() -> str:
    """A random 4-digit PIN that is not a repeated digit or a straight sequence."""
    while True:
        pin = f"{secrets.randbelow(10_000):04d}"
        steps = {int(b) - int(a) for a, b in zip(pin, pin[1:], strict=False)}
        if len(set(pin)) > 1 and steps not in ({1}, {-1}):
            return pin


def generated_value(key: str) -> str | None:
    if key in GENERATED_SECRETS:
        return secrets.token_urlsafe(32)
    if key == "DEMO_OWNER_PIN":
        return demo_pin()
    return None


def parse(lines: list[str]) -> dict[str, str]:
    values = {}
    for line in lines:
        key, sep, value = line.partition("=")
        if sep and not line.lstrip().startswith("#"):
            values[key.strip()] = value.strip()
    return values


def main(argv: list[str]) -> int:
    target = ROOT / ".env"
    template = (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()

    if target.exists() and "--force" not in argv:
        existing = parse(target.read_text(encoding="utf-8").splitlines())
        missing = []
        for line in template:
            key, sep, value = line.partition("=")
            key = key.strip()
            if sep and not line.lstrip().startswith("#") and key not in existing:
                missing.append(f"{key}={generated_value(key) or value.strip()}")
        if not missing:
            print(".env is complete; nothing changed (use --force to regenerate).")
            return 0
        with target.open("a", encoding="utf-8") as handle:
            handle.write("\n# Added by make_env.py\n" + "\n".join(missing) + "\n")
        print(f"Added {len(missing)} missing setting(s) to .env: " + ", ".join(m.split('=')[0] for m in missing))
        return 0

    lines = []
    for line in template:
        key, sep, value = line.partition("=")
        if sep and not line.lstrip().startswith("#") and not value.strip():
            fresh = generated_value(key.strip())
            if fresh:
                line = f"{key.strip()}={fresh}"
        lines.append(line)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {target} with fresh secrets and a demo owner PIN (see DEMO_OWNER_PIN in .env). "
          "Add ANTHROPIC_API_KEY there to use LLM_MODE=anthropic.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
