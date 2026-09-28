# HCR-C2-060 — CTDClinicalSummaryAgent

> **Category**: Cat 2 (domain workflow)
> **Industry**: HCR

## Overview

CTDClinicalSummaryAgent helps regulatory affairs professionals and medical writers prepare
subsection-level draft clinical summaries for CTD Modules 2.5 and 2.7 from structured trial
summaries. It uses tenant-scoped prior submission sections and regulatory inquiry-response
histories to assemble drafts with anticipated inquiries, claim-to-source mappings, and
missing-data flags. Ingestion and feedback workflows allow approved source material, received
inquiries, and adopted text to be incorporated into the knowledge base over time. Its outputs
are drafting aids only: they are not clinically validated or regulator-approved, must not be
submitted directly, and require review and approval by qualified medical and regulatory
professionals.

The division of labour is the point of the design. The language model writes prose; nothing it
writes is trusted on its own:

- **Every claim in the draft must map to a source that actually exists.** Source identifiers in
  the generated text are matched against the retrieved records at the subgraph boundary, and a
  draft citing an identifier that is not there is refused rather than published. Prompt wording
  alone is not treated as a control.
- **The draft is produced one subsection at a time**, because that is the unit a human can review
  and the unit at which claim-to-source mapping stays checkable. Whole-section generation would
  make both impossible.
- **Only the shape of a question is reused across products, never its content.** Prior regulatory
  inquiries are reused as category, section type and issue structure. The medical content of
  another product — drug names, doses, study identifiers — is refused at retrieval, because a
  sentence copied from a different product and stamped with this product's source identifier is a
  draft that passes identifier checking while being factually about another drug.
- **Prior material is scoped to one tenant.** Retrieval is confined to the same `company_id`, and
  the identity used for that is the one established by upstream authentication, not a value the
  request body supplies.
- **Missing required elements are reported, not filled in.** An element of the section spec with
  no corresponding claim appears in `missing_data_flags`.
- **Degradation is always recorded.** When the model is unavailable the agent returns a
  deterministic skeleton of the section plus the supplied claims, and says so in
  `degradation_reason` — it never presents a reduced result as a complete one.

Outputs are drafting aids. They are not clinically validated or regulator-approved, must not be
submitted as they stand, and require review and approval by qualified medical and regulatory
professionals.

This is an agent template built with the **AGENTIC STAR** development platform and the
**AgentCore Framework**. It is intended to be taken as a starting point: fork it, adapt it to
your own data and policies, and run it inside your own AGENTIC STAR deployment.

## Integration

Provide:

- an AgentCore runtime;
- authentication middleware in front of the entry adapter that establishes a trusted `caller_id`
  and `company_id` on the request. These are **not** read from request headers or from the request
  body: `/invoke` refuses a request with no trusted caller identity, and the `company_id` inside
  the envelope must match the one authentication established;
- a `BaseLLM` implementation as `config["llm"]`. None is bundled and no model is named. Without
  one the agent still answers, via the deterministic fallback described above;
- durable storage, if you need it. The stores that ship here (`src/services/store.py`) are
  in-process — see the limitations below.

Runtime parameters read from `config/config.yaml`:

| Key | Meaning |
|---|---|
| `hitl.enabled` (`true`), `hitl.max_hitl` (`12`) | Human review is on; the draft pauses for confirmation before completing |
| `memory_enabled` (`true`) | Required for pause/resume |
| `ingest.supported_formats` (`["structured_json"]`) | Accepted ingest payload formats. Unstructured documents are not parsed |
| `ingest.max_document_bytes` (`65536`) | Ingest payload ceiling |
| `retrieval.top_k_past_sections` (`5`), `retrieval.top_k_past_inquiries` (`10`) | Retrieval budgets for prior sections and prior inquiries |
| `section_specs_dir`, `inquiry_categories_file`, `assertion_lexicon_file` | Section skeletons, the inquiry category taxonomy, and the assertion lexicon |
| `max_retry` (`3`), `timeout_seconds` (`120`) | Framework retry budget and timeout |

`config/section_specs/` currently contains **`2.7.3` only**. Other CTD sections are not supported
by the shipped configuration. The template does not parse PDF, Word or Excel input, does not write
back to any source system, and does not decide submission readiness.

## Requirements

**This template does not run standalone.** It requires:

| Requirement | Notes |
|---|---|
| **AGENTIC STAR platform** | The agent connects to the platform at start-up. Without it, start-up fails immediately (see *Behaviour without the platform* below). Deployment guides and API documentation: [AGENTIC STAR Developers](https://developers.fd.agenticstar.tm.softbank.jp/) |
| **AgentCore Framework** (`agenticstar-agentcore`) | Supplied by the platform's own package registry at build time. It is not published on public PyPI, so `pip install -e .` alone does not fetch it. |
| Python | >=3.11 |

```bash
pip install -e .
```

### Behaviour without the platform

The framework is the agent's import-time dependency: `src/` imports `framework.*` and `shared.*`
directly, so with no SDK installed the process does not start at all. That is what "does not run
standalone" means here — there is no degraded mode that runs without the framework.

With the SDK installed but no platform around it, the standalone server in `src/api/server.py`
does come up, and it is worth being exact about what it can and cannot do:

- `/health` answers `{"status": "ok", "agent": "HCR-C2-060"}`.
- It reads `config/config.yaml` **relative to the working directory**, so start it from the
  repository root.
- It injects no `config["llm"]`, so every draft takes the deterministic fallback path and reports
  `degradation_reason: llm_unavailable`.
- It compiles with an in-memory checkpointer, and the knowledge base and payload store are
  in-process too. Pending human reviews and all ingested material are lost on restart, and are not
  shared between processes.
- `INVOKE_AUTH_TOKEN`, if set on the server environment, promotes an anonymous caller to
  `VERIFIED_EXTERNAL` on a matching `Authorization: Bearer` header. This is the one value read
  directly from the process environment, because it authenticates the caller before any
  invocation context exists. It does **not** substitute for the trusted `caller_id` /
  `company_id` that middleware must establish — without those, `/invoke` returns **403**.

The test suite runs without a platform connection.

## Input

`POST /invoke` takes a JSON body with `input` and an optional `session_id`. `input` is itself a
JSON string carrying the request envelope:

| Field | Meaning |
|---|---|
| `mode` | `invoke` (draft), `ingest` (load prior material), or `feedback` (record an actual inquiry and the text finally adopted) |
| `company_id` | Tenant. Must equal the `company_id` authentication established for the request |
| `product_id` | Product the draft is for |
| `section_id` | CTD section — `2.7.3` in the shipped configuration |
| `payload` | Mode-specific. `invoke` takes `claims`; `ingest` takes `format: "structured_json"` and `records`; `feedback` takes `inquiry_category`, `issue_structure`, `adopted_text` and `predicted_by_agent` |

```bash
curl -X POST http://127.0.0.1:8000/invoke \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer ${INVOKE_AUTH_TOKEN}" \
  -d '{
    "input": "{\"mode\":\"invoke\",\"company_id\":\"company-a\",\"product_id\":\"product-a\",\"section_id\":\"2.7.3\",\"payload\":{\"claims\":[{\"element_id\":\"study_populations\",\"value\":\"FAS was 100 subjects\"}]}}",
    "session_id": "ctd-review-001"
  }'
```

Human review is enabled, so a normal `invoke` returns `status: awaiting_human` with a
`thread_id`. Resume it with `confirm` or `refine`:

```bash
curl -X POST http://127.0.0.1:8000/resume/THREAD_ID \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer ${INVOKE_AUTH_TOKEN}" \
  -d '{"feedback":"confirm"}'
```

Resume is authorised against the checkpoint, not just the token: the `caller_id` and `company_id`
that created the pending review must match the ones authentication establishes for the resume
request, or it is refused with **403**.

## Output

The standard AgentCore envelope is preserved. After `confirm`, `output` carries:

| Field | Meaning |
|---|---|
| `section_id` | The section drafted |
| `draft` | `body` (the subsection text) and `cited_source_ids` |
| `evidence_map` | Claim-to-source mapping, one entry per claim carried into the draft |
| `anticipated_inquiries` | Inquiries predicted from prior inquiry structure, when history is available |
| `missing_data_flags` | Required section elements with no corresponding claim, and why |
| `degradation_reason` | Present whenever the result is reduced — e.g. `no_inquiry_history+llm_unavailable`, or `phi_masked_in_input` |

Values in the design and test documents are illustrative; real output depends on the claims
supplied and on what has been ingested.

## Security and Limitations

Every domain node requires verified-external trust, and tenant scope comes from the
authenticated identity rather than from the request. Protected health information is handled
asymmetrically by mode, on purpose: `ingest` and `feedback` **refuse** a payload containing it,
because that material would be stored and reused, while `invoke` **masks** it and records
`phi_masked_in_input`, because refusing a live drafting request would push the user to strip the
text by hand. Document bodies are held behind opaque references rather than being carried in
state, so the checkpoint store does not accumulate plaintext trial content.

Known limits, recorded rather than hidden:

- **Identifier checking is not fact checking.** Verifying that a cited source exists does not
  establish that the sentence is a faithful summary of it. There is no semantic entailment check
  and none is claimed.
- **The shipped stores are in-process.** Ingested sections, inquiry history and pending human
  reviews live in memory: they do not survive a restart and are not shared across replicas.
  A deployment that needs durability supplies its own.
- **Only `2.7.3` ships.** Adding a section means adding its spec to `config/section_specs/`.
- **Structured input only.** `ingest` accepts `structured_json`; converting source documents into
  that shape is outside the template.
- **Inquiry prediction depends on accumulated history.** With none, `anticipated_inquiries` is
  empty and the run says so through `degradation_reason` rather than inventing plausible ones.

Responsibility for anything submitted to a regulator rests with a qualified human. Verify the
scientific and regulatory validity, the cited sources, the interpretation and the submission
decision before use.

## Release

- Version: 1.0.0
- Category: fixed multi-step domain workflow (Cat 2), with the domain workflow nested in the
  `main` slot as a subgraph
- Generation mode: `deterministic` as shipped — the narrative model is an injected option, and
  the default build answers through the deterministic fallback
- HITL: **enabled** — a draft pauses for human confirmation before completing
- Persistence: in-memory checkpointer and in-process stores in the standalone server
- External API clients: deployment-injected; none bundled

## Quick Start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest tests/ -v
```

Tests run without a platform connection. Running the agent itself does not — see
*Behaviour without the platform* above.

## Documentation

`docs/02_design.md` carries the node flow, the state schema, the claim-to-source verification
design and the security design. `docs/03_test_spec.md` records the test specification and the
verification results.

## Project Structure

```
src/          agent implementation (nodes, services, schemas)
tests/        unit, integration and boundary tests
config/       agent configuration
docs/         design and test specifications
```

See `docs/02_design.md` for the design and `docs/03_test_spec.md` for the test specification.

## Customising

1. Adjust `config/` for your own environment and policies.
2. Replace the knowledge sources and sample data with your own.
3. Review the node implementations under `src/nodes/` for domain-specific logic.
4. Re-run the test suite.

## License

MIT — see [LICENSE](LICENSE).

## Status of this repository

This template is published **as is**, by its individual author, under the MIT license. It carries
**no warranty and no support commitment**, and no organisation stands behind its behaviour or
fitness for any purpose. Issues and pull requests may or may not receive a response; that is at
the sole discretion of the repository owner.
