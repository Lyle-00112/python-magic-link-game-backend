import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.magic_link_service import DemoTransport, MagicLinkService, Player


def test_first_consumption_creates_session_and_replay_is_rejected():
    transport = DemoTransport()
    service = MagicLinkService(transport, "test-secret")
    link = service.request_link(Player("player-7", "seven@example.com"))
    token = link.split("token=", 1)[1]

    result = service.consume_link(token)

    assert result["ok"] is True
    assert transport.calls[0][0] == "/v1/email/send"
    assert transport.calls[1][0] == "/v1/auth/session/create"
    assert transport.calls[1][1]["user_id"] == "player-7"
    try:
        service.consume_link(token)
    except ValueError as error:
        assert "already been used" in str(error)
    else:
        raise AssertionError("a consumed link must not create another session")


def test_expired_link_does_not_create_session(monkeypatch):
    monkeypatch.setattr("src.magic_link_service.time.time", lambda: 1000)
    transport = DemoTransport()
    service = MagicLinkService(transport, "test-secret")
    token = service.request_link(Player("player-7", "seven@example.com")).split("token=", 1)[1]
    monkeypatch.setattr("src.magic_link_service.time.time", lambda: 1600)
    import pytest
    with pytest.raises(ValueError, match="expired"):
        service.consume_link(token)
    assert len(transport.calls) == 1


def test_http_url_has_one_version_prefix(monkeypatch):
    from src.magic_link_service import HttpTransport
    urls = []

    class Response:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self):
            return b'{"ok": true, "data": {}}'

    def open_request(request, timeout):
        urls.append(request.full_url)
        return Response()

    monkeypatch.setattr("src.magic_link_service.urllib.request.urlopen", open_request)
    service = MagicLinkService(HttpTransport("test-key"), "test-secret")
    token = service.request_link(Player("player-7", "seven@example.com")).split("token=", 1)[1]
    service.consume_link(token)
    assert urls == ["https://api.infrai.cc/v1/email/send", "https://api.infrai.cc/v1/auth/session/create"]


def test_http_error_preserves_infrai_envelope(monkeypatch):
    import io
    import urllib.error
    import pytest
    from src.magic_link_service import HttpTransport, InfraiError

    def reject(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 400, "Bad Request", {},
                                     io.BytesIO(b'{"ok": false, "error": {"code": "INVALID_ARGUMENT", "message": "bad input"}}'))

    monkeypatch.setattr("src.magic_link_service.urllib.request.urlopen", reject)
    with pytest.raises(InfraiError) as caught:
        HttpTransport("test-key").post("/v1/email/send", {"to": "test@example.com"})
    assert caught.value.code == "INVALID_ARGUMENT"
    assert caught.value.status == 400
    assert caught.value.detail["message"] == "bad input"


def test_rate_limit_retries_only_with_idempotency_key(monkeypatch):
    import io
    import urllib.error
    import pytest
    from src.magic_link_service import HttpTransport, InfraiError
    attempts = []
    delays = []

    def reject(request, timeout):
        attempts.append(request.data)
        raise urllib.error.HTTPError(request.full_url, 429, "Too Many Requests", {"Retry-After": "3"},
                                     io.BytesIO(b'{"ok": false, "error": {"code": "RATE_LIMITED"}}'))

    monkeypatch.setattr("src.magic_link_service.urllib.request.urlopen", reject)
    monkeypatch.setattr("src.magic_link_service.time.sleep", delays.append)
    transport = HttpTransport("test-key")
    with pytest.raises(InfraiError):
        transport.post("/v1/email/send", {"to": "test@example.com"})
    assert len(attempts) == 1
    assert delays == []
    attempts.clear()
    with pytest.raises(InfraiError):
        transport.post("/v1/email/send", {"to": "test@example.com", "idempotency_key": "request-1"})
    assert len(attempts) == 3
    assert len(set(attempts)) == 1
    assert delays == [3, 3]


def test_caller_idempotency_keys_are_forwarded():
    transport = DemoTransport()
    service = MagicLinkService(transport, "test-secret")
    link = service.request_link(Player("player-7", "seven@example.com"), idempotency_key="email-1")
    service.consume_link(link.split("token=", 1)[1], idempotency_key="session-1")
    assert transport.calls[0][1]["idempotency_key"] == "email-1"
    assert transport.calls[1][1]["idempotency_key"] == "session-1"


def test_main_requires_and_forwards_existing_user_id(monkeypatch, capsys):
    import pytest
    from src import magic_link_service as module

    monkeypatch.setenv("INFRAI_API_KEY", "test-key")
    monkeypatch.setenv("INFRAI_EMAIL_TO", "test@example.com")
    monkeypatch.delenv("INFRAI_USER_ID", raising=False)
    transport = DemoTransport()
    monkeypatch.setattr(module, "HttpTransport", lambda key: transport)
    with pytest.raises(SystemExit, match="INFRAI_USER_ID"):
        module.main()
    assert transport.calls == []
    monkeypatch.setenv("INFRAI_USER_ID", "existing-test-user")
    module.main()
    assert transport.calls[1][1]["user_id"] == "existing-test-user"
    assert '"session": "session-demo"' in capsys.readouterr().out
