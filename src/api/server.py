"""Standalone HTTP adapter for HCR-C2-060."""

from __future__ import annotations

import os
import secrets
import json
from pathlib import Path
from uuid import uuid4

import yaml
from fastapi import FastAPI, HTTPException, Request
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from framework.secrets.context import bound_secrets
from langgraph.checkpoint.memory import MemorySaver
from pydantic import BaseModel
from shared.secrets import factory as secrets_factory

from src.graph.graph import CTDClinicalSummaryAgent
from src.services.store import wire_services

app = FastAPI(title="HCR-C2-060")


def _runtime_config() -> dict:
    path = Path("config") / "config.yaml"
    return yaml.safe_load(path.read_text()) or {}


agent = CTDClinicalSummaryAgent(config=_runtime_config())
wire_services()
agent.compile(checkpointer=MemorySaver())
agent.provision_secrets(secrets_factory(namespace="hcr", agent_name="HCR-C2-060"))


class InvokeRequest(BaseModel):
    input: str
    session_id: str = ""


class ResumeRequest(BaseModel):
    feedback: dict | str


def _coerce_trust(value) -> TrustLevel:
    if isinstance(value, TrustLevel):
        return value
    try:
        return TrustLevel(value)
    except Exception:
        return TrustLevel.ANONYMOUS


def _authenticate(request: Request) -> tuple[TrustLevel, str, str]:
    trust = _coerce_trust(getattr(request.state, "trust_level", TrustLevel.ANONYMOUS))
    expected = os.environ.get("INVOKE_AUTH_TOKEN")
    if expected and trust == TrustLevel.ANONYMOUS:
        supplied = request.headers.get("authorization", "")
        if not secrets.compare_digest(supplied.encode(), f"Bearer {expected}".encode()):
            raise HTTPException(status_code=401, detail="Token is invalid or expired.")
        trust = TrustLevel.VERIFIED_EXTERNAL
    caller_id = str(getattr(request.state, "caller_id", "")).strip()
    company_id = str(getattr(request.state, "company_id", "")).strip()
    return trust, caller_id, company_id


def _bind_company_to_trusted_identity(user_input: str, company_id: str) -> str:
    if not company_id:
        raise HTTPException(status_code=403, detail="Trusted tenant identity is required.")
    try:
        payload = json.loads(user_input)
    except Exception:
        raise HTTPException(status_code=400, detail="Input must be JSON.")
    if str(payload.get("company_id", "")).strip() != company_id:
        raise HTTPException(status_code=403, detail="Input tenant does not match authenticated tenant.")
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _checkpoint_values(thread_id: str) -> dict:
    try:
        snap = agent._compiled.get_state({"configurable": {"thread_id": thread_id}})
    except Exception:
        raise HTTPException(status_code=404, detail="Thread not found.")
    values = getattr(snap, "values", None) or {}
    if not values:
        raise HTTPException(status_code=404, detail="Thread not found.")
    return values


def _authorize_resume(thread_id: str, caller_id: str, company_id: str) -> None:
    if not caller_id or not company_id:
        raise HTTPException(status_code=403, detail="Resume ownership check failed.")
    values = _checkpoint_values(thread_id)
    checkpoint_caller = str(values.get("caller_id", "")).strip()
    checkpoint_company = str(values.get("company_id", "")).strip()
    if not checkpoint_caller or not checkpoint_company:
        raise HTTPException(status_code=403, detail="Resume ownership check failed.")
    if checkpoint_caller != caller_id or checkpoint_company != company_id:
        raise HTTPException(status_code=403, detail="Resume ownership check failed.")


@app.post("/invoke")
async def invoke(req: InvokeRequest, request: Request):
    trust, caller_id, company_id = _authenticate(request)
    # Bind BOTH authoritative identities at checkpoint creation. A missing trusted
    # caller_id would create an orphaned HITL checkpoint that _authorize_resume()
    # fail-closes, locking the rightful user out of their own pending review.
    if not caller_id:
        raise HTTPException(status_code=403, detail="Trusted caller identity is required.")
    bound_input = _bind_company_to_trusted_identity(req.input, company_id)
    with bound_secrets(agent._secrets_provider):
        ctx = InvocationContext(
            session_id=req.session_id or str(uuid4()),
            caller_trust_level=trust,
            caller_id=caller_id,
        )
        return agent.invoke(bound_input, ctx=ctx)


@app.post("/resume/{thread_id}")
async def resume(thread_id: str, req: ResumeRequest, request: Request):
    _trust, caller_id, company_id = _authenticate(request)
    _authorize_resume(thread_id, caller_id, company_id)
    with bound_secrets(agent._secrets_provider):
        return agent.resume(thread_id=thread_id, feedback=req.feedback)


@app.get("/health")
def health():
    return {"status": "ok", "agent": "HCR-C2-060"}
