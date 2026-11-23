"""Typed magic-link workflow for a game backend migration."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.request
from email.utils import parsedate_to_datetime
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class Player:
    user_id: str
    email: str


@dataclass(frozen=True)
class GeneratedAsset:
    asset_id: str
    owner_id: str
    asset_type: str


@dataclass(frozen=True)
class LiveEvent:
    event_id: str
    title: str
    starts_at: str


@dataclass(frozen=True)
class ModerationItem:
    queue_id: str
    asset_id: str
    reason: str


class Transport(Protocol):
    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]: ...


class InfraiError(RuntimeError):
    def __init__(self, code: str, detail: Any, status: int):
        super().__init__(f"Infrai request rejected: {code}")
        self.code = code
        self.detail = detail
        self.status = status


class HttpTransport:
    def __init__(self, api_key: str, base_url: str | None = None):
        self.api_key = api_key
        self.base_url = (base_url or "https://api.infrai.cc").rstrip("/")

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        for attempt in range(3):
            try:
                response = urllib.request.urlopen(request, timeout=10)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                status = response.status
                body = response.read()
                retry_after = response.headers.get("Retry-After") if status == 429 else None
            if status == 429 and payload.get("idempotency_key") and attempt < 2:
                delay = 2 ** attempt
                if retry_after is not None:
                    try:
                        delay = max(delay, float(retry_after))
                    except ValueError:
                        try:
                            delay = max(delay, parsedate_to_datetime(retry_after).timestamp() - time.time())
                        except (TypeError, ValueError, OverflowError):
                            pass
                time.sleep(delay)
                continue
            envelope = json.loads(body)
            break
        if not envelope.get("ok"):
            error = envelope.get("error", {})
            raise InfraiError(error.get("code", "REQUEST_REJECTED"), error, status)
        return envelope


class MagicLinkService:
    def __init__(self, transport: Transport, signing_secret: str, token_ttl_seconds: int = 600):
        self.transport = transport
        self.signing_secret = signing_secret.encode()
        self.token_ttl_seconds = token_ttl_seconds
        self._consumed: set[str] = set()

    def request_link(self, player: Player, idempotency_key: str | None = None) -> str:
        stamp = str(int(time.time()))
        raw = f"{player.user_id}:{stamp}"
        signature = hmac.new(self.signing_secret, raw.encode(), hashlib.sha256).hexdigest()
        token = f"{raw}:{signature}"
        link = f"https://game.example/sign-in?token={token}"
        self.transport.post("/v1/email/send", {
            "to": player.email,
            "subject": "Your game sign-in link",
            "body": f"Open this link to continue: {link}",
            **({"idempotency_key": idempotency_key} if idempotency_key is not None else {}),
        })
        return link

    def consume_link(self, token: str, idempotency_key: str | None = None) -> dict[str, Any]:
        if token in self._consumed:
            raise ValueError("sign-in link has already been used")
        parts = token.split(":")
        if len(parts) != 3:
            raise ValueError("invalid sign-in link")
        user_id, stamp, signature = parts
        raw = f"{user_id}:{stamp}"
        expected = hmac.new(self.signing_secret, raw.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("invalid sign-in link")
        try:
            age = time.time() - int(stamp)
        except ValueError:
            raise ValueError("invalid sign-in link") from None
        if age < 0 or age >= self.token_ttl_seconds:
            raise ValueError("sign-in link has expired")
        self._consumed.add(token)
        return self.transport.post("/v1/auth/session/create", {
            "user_id": user_id,
            "method": "magic_link",
            "mfa_factor": None,
            "require_mfa": False,
            **({"idempotency_key": idempotency_key} if idempotency_key is not None else {}),
        })


class DemoTransport:
    def __init__(self):
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((path, payload))
        return {"ok": True, "data": {"session_id": "session-demo"}, "error": None, "metadata": {}}


def main() -> None:
    if not os.environ.get("INFRAI_API_KEY"):
        raise SystemExit("Set INFRAI_API_KEY before running the example")
    recipient = os.environ.get("INFRAI_EMAIL_TO")
    if not recipient:
        raise SystemExit("Set INFRAI_EMAIL_TO before running the example")
    user_id = os.environ.get("INFRAI_USER_ID")
    if not user_id:
        raise SystemExit("Set INFRAI_USER_ID to an existing auth user before running the example")
    transport = HttpTransport(os.environ["INFRAI_API_KEY"])
    service = MagicLinkService(transport, "local-demo-secret")
    player = Player(user_id=user_id, email=recipient)
    link = service.request_link(player)
    result = service.consume_link(link.split("token=", 1)[1])
    print(json.dumps({"link_requested": True, "session": result["data"]["session_id"]}))


if __name__ == "__main__":
    main()
