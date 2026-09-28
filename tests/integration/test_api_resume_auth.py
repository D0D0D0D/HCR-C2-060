from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient

from framework.schemas.trust_level import TrustLevel
from src.api.server import app
from src.services.store import knowledge_base, payload_store


class TrustedIdentityApp:
    def __init__(self, wrapped, caller_id: str = "", company_id: str = "", trust: str = TrustLevel.ANONYMOUS.value):
        self._wrapped = wrapped
        self._caller_id = caller_id
        self._company_id = company_id
        self._trust = trust

    async def __call__(self, scope: dict[str, Any], receive, send) -> None:
        if scope["type"] == "http":
            scope.setdefault("state", {})
            scope["state"]["caller_id"] = self._caller_id
            scope["state"]["company_id"] = self._company_id
            scope["state"]["trust_level"] = self._trust
        await self._wrapped(scope, receive, send)


def _client(caller_id: str = "caller-a", company_id: str = "company-a") -> TestClient:
    return TestClient(TrustedIdentityApp(app, caller_id=caller_id, company_id=company_id))


def _headers(token: str = "good-token", caller_id: str = "spoof-caller", company_id: str = "spoof-company") -> dict:
    return {
        "authorization": f"Bearer {token}",
        "x-caller-id": caller_id,
        "x-company-id": company_id,
    }


def _invoke_payload(company_id: str = "company-a", product_id: str = "product-a") -> str:
    return json.dumps(
        {
            "mode": "invoke",
            "company_id": company_id,
            "product_id": product_id,
            "section_id": "2.7.3",
            "payload": {"claims": [{"element_id": "study_populations", "value": "FASは100例であった"}]},
        },
        ensure_ascii=False,
    )


def _create_pending_thread(caller_id: str = "caller-a", company_id: str = "company-a") -> str:
    client = _client(caller_id=caller_id, company_id=company_id)
    response = client.post(
        "/invoke",
        json={"input": _invoke_payload(company_id=company_id), "session_id": f"s-{caller_id}-{company_id}"},
        headers=_headers(),
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "awaiting_human"
    return data["thread_id"]


def test_resume_requires_token_when_configured(monkeypatch):
    monkeypatch.setenv("INVOKE_AUTH_TOKEN", "good-token")
    response = _client().post("/resume/thread-any", json={"feedback": "confirm"})
    assert response.status_code == 401


def test_resume_rejects_bad_token(monkeypatch):
    monkeypatch.setenv("INVOKE_AUTH_TOKEN", "good-token")
    response = _client().post("/resume/thread-any", json={"feedback": "confirm"}, headers=_headers(token="bad-token"))
    assert response.status_code == 401


def test_resume_unknown_thread_returns_404(monkeypatch):
    monkeypatch.setenv("INVOKE_AUTH_TOKEN", "good-token")
    response = _client().post("/resume/thread-does-not-exist", json={"feedback": "confirm"}, headers=_headers())
    assert response.status_code == 404


def test_resume_rejects_cross_caller_or_tenant_and_spoofed_headers(monkeypatch):
    monkeypatch.setenv("INVOKE_AUTH_TOKEN", "good-token")
    knowledge_base.reset()
    payload_store.clear()
    thread_id = _create_pending_thread(caller_id="caller-a", company_id="company-a")

    cross_caller = _client(caller_id="caller-b", company_id="company-a").post(
        f"/resume/{thread_id}",
        json={"feedback": "confirm"},
        headers=_headers(caller_id="caller-a", company_id="company-a"),
    )
    assert cross_caller.status_code == 403

    cross_tenant = _client(caller_id="caller-a", company_id="company-b").post(
        f"/resume/{thread_id}",
        json={"feedback": "confirm"},
        headers=_headers(caller_id="caller-a", company_id="company-a"),
    )
    assert cross_tenant.status_code == 403


def test_resume_rejects_empty_trusted_identity_even_with_valid_token_and_body_claims(monkeypatch):
    monkeypatch.setenv("INVOKE_AUTH_TOKEN", "good-token")
    knowledge_base.reset()
    payload_store.clear()
    thread_id = _create_pending_thread(caller_id="caller-a", company_id="company-a")
    response = _client(caller_id="", company_id="").post(
        f"/resume/{thread_id}",
        json={"feedback": "confirm", "caller_id": "caller-a", "company_id": "company-a"},
        headers=_headers(caller_id="caller-a", company_id="company-a"),
    )
    assert response.status_code == 403


def test_invoke_rejects_payload_company_mismatch(monkeypatch):
    monkeypatch.setenv("INVOKE_AUTH_TOKEN", "good-token")
    response = _client(caller_id="caller-a", company_id="trusted-company").post(
        "/invoke",
        json={"input": _invoke_payload(company_id="payload-company"), "session_id": "mismatch"},
        headers=_headers(),
    )
    assert response.status_code == 403


def test_invoke_rejects_missing_trusted_caller_and_creates_no_thread(monkeypatch):
    monkeypatch.setenv("INVOKE_AUTH_TOKEN", "good-token")
    knowledge_base.reset()
    payload_store.clear()
    # trusted company present, trusted caller empty -> /invoke must 403 and create
    # no resumable HITL checkpoint (would otherwise be orphaned and unresumable).
    response = _client(caller_id="", company_id="company-a").post(
        "/invoke",
        json={"input": _invoke_payload(company_id="company-a"), "session_id": "orphan"},
        headers=_headers(),
    )
    assert response.status_code == 403
    assert "thread_id" not in response.json()


def test_resume_authorized_owner_reaches_success(monkeypatch):
    monkeypatch.setenv("INVOKE_AUTH_TOKEN", "good-token")
    knowledge_base.reset()
    payload_store.clear()
    thread_id = _create_pending_thread(caller_id="caller-owner", company_id="company-owner")
    response = _client(caller_id="caller-owner", company_id="company-owner").post(
        f"/resume/{thread_id}",
        json={"feedback": "confirm"},
        headers=_headers(caller_id="attacker", company_id="attacker-company"),
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "success"
