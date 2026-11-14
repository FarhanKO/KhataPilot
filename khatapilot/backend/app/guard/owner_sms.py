"""Alerts to the owner by SMS, so an urgent one reaches them when the app is closed.

Every SMS KhataPilot sends takes the same road: the guard mints a `message.send` token bound to the number
and the hash of the exact text, the messaging gateway (the sandbox) checks it, and with `SMS_VIA_PHONE` the
same text then leaves from the owner's phone. An alert SMS can only go to the owner's own number
(`OWNER_PHONE`, set by whoever runs the shop's server, never by the model or a message), says only what
the alert already says (numbers masked, never a PIN) and is at most a few a day."""

from __future__ import annotations

from app.core.actions import sha256_hex
from app.core.ids import new_id
from app.core.masking import e164, mask_phone, mask_text, normalize_msisdn
from app.guard.capability import CapabilitySigner
from app.guard.recorder import Recorder
from app.infra.phone_gateway import PhoneGateway, PhoneGatewayError
from app.infra.sandbox_client import SandboxClient, SandboxError

KIND = "message.send"
MAX_CHARS = 306  # two SMS segments


class OwnerSms:
    def __init__(self, *, signer: CapabilitySigner, sandbox: SandboxClient | None, phone: PhoneGateway | None,
                 recorder: Recorder) -> None:
        self.signer = signer
        self.sandbox = sandbox
        self.phone = phone
        self.recorder = recorder

    def send(self, *, owner_number: str, from_account: str, text: str, request_id: str) -> tuple[bool, str]:
        """(sent, what happened). Never raises: an alert that cannot be texted is still in the app."""
        number = normalize_msisdn(owner_number)
        if number is None:
            return False, "OWNER_PHONE is not a Bangladeshi mobile number"
        if self.sandbox is None:
            return False, "no messaging gateway"
        body = mask_text(" ".join(text.split()))[:MAX_CHARS]
        params = {"from_account": from_account, "to_number": number, "channel": "sms", "body_sha256": sha256_hex(body)}
        decision_id = new_id("dec")
        auth = self.signer.mint(kind=KIND, params=params, idempotency_key=new_id("alertsms"), tier=0,
                                decision_id=decision_id)
        try:
            result = self.sandbox.send_message({"from_account": from_account, "to_number": number, "channel": "sms",
                                                "body": body}, authorization=auth.header,
                                               idempotency_key=auth.idempotency_key)
        except SandboxError as exc:
            return self._done(False, f"{exc.code}: {exc.message}", number, request_id, decision_id)
        ref = result.get("message_id", "")
        if self.phone is not None:
            try:
                self.phone.send_sms([e164(number)], body)
                ref = f"{ref}, from your phone"
            except PhoneGatewayError as exc:
                return self._done(False, f"the gateway took it ({ref}) but the phone did not: {exc}", number,
                                  request_id, decision_id)
        return self._done(True, ref, number, request_id, decision_id)

    def _done(self, ok: bool, detail: str, number: str, request_id: str, decision_id: str) -> tuple[bool, str]:
        self.recorder.event(request_id=request_id, channel="system", actor="system", provenance="system",
                            event="execution", summary=f"Alert SMS to the owner ({mask_phone(number)})",
                            decision="SENT" if ok else "FAILED", reason=detail, tier=0, decision_id=decision_id)
        return ok, detail
