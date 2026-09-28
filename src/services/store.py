"""In-process stores used by the template and tests."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any
from uuid import uuid4


class TransientPayloadStore:
    """Opaque payload reference store. State carries only refs."""

    def __init__(self) -> None:
        self._payloads: dict[str, Any] = {}

    def put(self, payload: Any) -> str:
        ref = f"payload_{uuid4().hex}"
        self._payloads[ref] = deepcopy(payload)
        return ref

    def get(self, ref: str) -> Any:
        if not ref or ref not in self._payloads:
            raise KeyError("payload_ref_not_found")
        return deepcopy(self._payloads[ref])

    def clear(self) -> None:
        self._payloads.clear()


class CTDKnowledgeBase:
    """Fail-closed tenant-scoped in-memory KB."""

    def __init__(self) -> None:
        self.submission_sections: list[dict] = []
        self.inquiry_qa: list[dict] = []

    def reset(self) -> None:
        self.submission_sections.clear()
        self.inquiry_qa.clear()

    def add_submission_section(self, record: dict) -> dict:
        required = ["company_id", "product_id", "section_id", "body"]
        missing = [key for key in required if not record.get(key)]
        if missing:
            raise ValueError(f"missing_submission_fields:{','.join(missing)}")
        body = str(record["body"])
        stored = {
            "section_ref_id": record.get("section_ref_id") or f"sec_{uuid4().hex[:12]}",
            "company_id": record["company_id"],
            "product_id": record["product_id"],
            "submission_id": record.get("submission_id", ""),
            "section_id": record["section_id"],
            "body_ref": f"body_{sha256(body.encode()).hexdigest()[:16]}",
            "body_digest": sha256(body.encode()).hexdigest(),
            "body": body,
            "skeleton_meta": record.get("skeleton_meta", {"paragraph_count": max(1, body.count("。"))}),
            "operator_edited": bool(record.get("operator_edited", False)),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self.submission_sections.append(stored)
        return deepcopy(stored)

    def add_inquiry(self, record: dict) -> dict:
        required = ["company_id", "product_id", "section_id", "inquiry_category"]
        missing = [key for key in required if not record.get(key)]
        if missing:
            raise ValueError(f"missing_inquiry_fields:{','.join(missing)}")
        stored = {
            "inquiry_id": record.get("inquiry_id") or f"inq_{uuid4().hex[:12]}",
            "company_id": record["company_id"],
            "product_id": record["product_id"],
            "submission_id": record.get("submission_id", ""),
            "section_id": record["section_id"],
            "inquiry_category": record["inquiry_category"],
            "issue_structure": record.get("issue_structure", "unspecified_issue"),
            "inquiry_text": record.get("inquiry_text", ""),
            "response_text": record.get("response_text", ""),
            "product_context": record.get("product_context", {}),
            "predicted_by_agent": bool(record.get("predicted_by_agent", False)),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self.inquiry_qa.append(stored)
        return deepcopy(stored)

    def retrieve(self, company_id: str, product_id: str, section_id: str, categories: list[str]) -> dict:
        if not company_id:
            raise ValueError("company_id_required")
        category_set = set(categories or [])
        sections = []
        for row in self.submission_sections:
            if row.get("company_id") != company_id or row.get("section_id") != section_id:
                continue
            same = row.get("product_id") == product_id
            item = {
                "section_ref_id": row["section_ref_id"],
                "product_id": row["product_id"],
                "is_same_product": same,
                "skeleton_meta": deepcopy(row.get("skeleton_meta", {})),
            }
            if same:
                item["body"] = row.get("body", "")
            sections.append(item)
        inquiries = []
        for row in self.inquiry_qa:
            if row.get("company_id") != company_id or row.get("section_id") != section_id:
                continue
            if category_set and row.get("inquiry_category") not in category_set:
                continue
            same = row.get("product_id") == product_id
            item = {
                "inquiry_id": row["inquiry_id"],
                "product_id": row["product_id"],
                "is_same_product": same,
                "category": row["inquiry_category"],
                "issue_structure": row.get("issue_structure", ""),
            }
            if same:
                item["question"] = row.get("inquiry_text", "")
            inquiries.append(item)
        inquiries.sort(key=lambda x: (not x["is_same_product"], x["inquiry_id"]))
        sections.sort(key=lambda x: (not x["is_same_product"], x["section_ref_id"]))
        return {"past_sections": sections, "past_inquiries": inquiries}


payload_store = TransientPayloadStore()
knowledge_base = CTDKnowledgeBase()


def wire_services() -> dict:
    """Runtime wiring hook. Real clients can be attached by the server adapter."""

    return {"payload_store": payload_store, "knowledge_base": knowledge_base}
