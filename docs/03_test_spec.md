# Test Specification — HCR-C2-060 CTD Clinical Summary Drafting & Regulatory Inquiry-History Agent

> **正本**: `docs/02_design.md`。本書は設計で確定した契約・受入基準を**テスト項目として一対一に写した
> トレーサビリティ文書**であり、実装はここに列挙した全項目を実装・実行する。
>
> **位置づけ**: `docs/03_test_spec.md` はテスト → ステージング移行のゲート成果物。本書は設計フェーズ
> の時点で**テスト契約として先に確定**させたもので、テストコード（`tests/`）の実装は実装フェーズのスコープ。
> テスト実行・stub 検出・依存ピン留めの各 CI ゲートは本ファイルの存在で発火する。

## Test Strategy

- **カバレッジ目標**: `src/` 行カバレッジ 80% 以上（テストフェーズ時点で計測）
- **テスト種別**: Unit（ノード単体）/ Integration（graph 経由の E2E）/ Proof-of-Boundary（framework 境界）
- **決定論の徹底**: Stage 1（ソース構造化）・Stage 2（KB 検索）・Stage 4 の根拠マップ／欠落フラグ生成は
  **非 LLM**。同一入力→同一出力を unit テストで固定する（LLM モックに依存しない）
- **LLM 注入シーム**: LLM は `config["llm"]` から注入する。テストでは fake LLM（決められた文字列を返す）
  および `None`（deterministic fallback 経路）の両方を流す

---

## 1. Framework Compliance Tests（必須）

| TC-ID | Test | Expected Result |
|-------|------|----------------|
| TC-01 | State contract: flat TypedDict（`src/schemas/state.py`） | 型チェック通過。Pydantic / dataclass / 任意 Python オブジェクトが混入していない |
| TC-02 | `SecurityViolationError` が不正入力で送出される | S-2 が PHI・不正 `mode` / `section_id` を検知して raise |
| TC-03 | `src/` に JWT / credential が無い | CI の資格情報スキャン: 0 violations |
| TC-04 | `InvocationContext` は `config["configurable"]` 経由のみ。ノード内では **`InvocationContext.from_state(state)`** を使う | 直接構築（`InvocationContext(...)`）を行っていないこと（直接構築は `NullProvider` が束縛され `ctx.secrets.require()` が常に `MissingSecret`） |
| TC-05 | S-4: `execute()` 内でライフサイクルイベントを重複発行していない | `node_start` / `node_complete` / `node_error` が `execute()` 本体に現れない（`__call__()` が自動発行） |
| TC-06 | S-2: `FunctionNode._security_gate_input()` を override していない | `@final`。override すればクラス定義時に `TypeError`。拡張は `_extra_security_gate_input()` のみ |
| TC-07 | S-3: `FunctionNode._security_gate_output()` を override していない | 同上（`_extra_security_gate_output()` で拡張） |
| TC-08 | `required_trust_level` が全 `FunctionNode` サブクラスのクラス body に明示宣言され、強制される | 不足 trust → 実行拒否。**テストは `node(state)`（`__call__`）で呼ぶ。`node.execute(state)` を直接呼ぶと S-1 ゲートを迂回して偽の緑になる** |
| TC-09 | S-2: `_extra_security_gate_input()` が実体を持つ | 症例レベル記述検知・`mode`/`section_id` の enum 検証・入力サイズ上限が実際に動作する |
| TC-10 | S-3: `_extra_security_gate_output()` が実体を持つ（**2 箇所**） | ①**`CTDWorkflowGraphNode`**（GraphNode 境界）= Provenance ID 検証（`gate_kind` 判定 + fail-closed + 検証後に正本を落とす）②`CTDPostProcessNode` = 効能断定ガード・PHI 再スキャン。いずれも実際に動作する（no-op でない） |
| TC-11 | S-4: 各 `execute()` 内でドメインイベントを最低 1 回 emit | 全ノードで `emit_trace_event()` が呼ばれる（副作用の有無を問わない） |
| TC-12 | `status` に `AgentStatus.<X>.value`（文字列）を代入している | bare Enum（`AgentStatus.SUCCESS`）を返していない |
| TC-13 | `hitl_count` / `hitl_allowed` をテンプレートから書き込んでいない | framework / Gateway 専用フィールド |

---

## 2. Proof-of-Boundary Tests（必須）

| PB-ID | Boundary | Test | Expected Result |
|-------|----------|------|----------------|
| PB-1 | BaseNode → AuditLogger | `emit_trace_event()`（`shared.utils.audit_logger`）が全実行経路で発火 | サイレント失敗なし |
| PB-2 | State serialization | invoke 後の State が primitives のみ | Pydantic / dataclass / 任意オブジェクトなし |
| PB-3 | L1 → External service | KB（`submission_sections` / `inquiry_qa`）への実接続で取得できる | 実データ取得 |
| PB-4 | Import isolation | Level 0（`agenticstar`）を import していない | AST スキャン: 0 violations |
| PB-5 | Checkpoint safety | 保存された checkpoint に JWT / Pydantic / **申請文書本文・照会本文・PHI** が無い | 検査通過（§4 SEC-05 と併せて検証） |
| PB-6 | Invoke execution order | `__call__()`: S-1 → S-4 `node_start` → S-2 `_security_gate_input` → `execute()` → S-3 `_security_gate_output` → S-4 `node_complete` | 順序検証通過 |
| PB-7 | **HITL interrupt propagation（本テンプレは HITL 有効のため必須）** | `hitl_refine` の `execute()` 内 `interrupt()` が `GraphInterrupt` を送出し、`BaseNode.__call__()` を貫通して LangGraph エンジンへ到達する（アプリの error boundary に捕まれない） | `GraphInterrupt` がエンジンへ到達。`status` が `error` にならない |

### 🔴 PB-7 の実装契約（TODO skip を証拠として認めない）

配布元の PB-7 stub は 2 つのテスト本体が `pytest.skip("TODO: ...")` のままである。**実装はこの
プレースホルダを実行可能なテストに置き換える。TODO skip のまま緑になったパイプラインは PB-7 の
証拠にならない**（eng-moderator F-01）。

| # | テスト | 検証内容 |
|---|---|---|
| PB-7-A | `test_pb7_hitl_interrupt_propagates` | 実ノード（`hitl_refine`）を `node(state)`（`__call__` 経由）で呼び、`hitl_allowed=True` + 生成済みドラフトがある状態で **`GraphInterrupt` が raise される**こと。`status` が `error` にならないこと |
| PB-7-B | `test_pb7_hitl_allowed_false_skips_interrupt` | 同じトリガー条件で `hitl_allowed=False` のとき、**`GraphInterrupt` が raise されず**（デッドロックしない）、ドラフトを確定して処理が続行すること（ガードのない `interrupt()` は blocking defect） |
| PB-7-C | `test_pb7_resume_reaches_success`（本テンプレ追加） | 実 compiled graph で invoke → interrupt → resume を回し、**`status="success"` に到達する**こと。suspend だけを見る PB では**`get_subgraph()` のキャッシュ漏れによる resume 無限ループを捕捉できない**（Cat2 HITL 実機知見） |

### 🔴 PB-7 の条件付き SKIP を「偽の緑」にしない 3 点（MR-B 実装契約）

| # | 罠 | 対策（適用済み） |
|---|---|---|
| 1 | PB-7 stub は **`config/agent.yaml` のトップレベル** `hitl.enabled` を読む。`agent.config.hitl` にネストすると `False` と誤判定 → **PB-7 が永久 SKIP** | `memory_enabled` / `hitl` をトップレベルに宣言する。`agent.config` はランタイム config のミラー |
| 2 | stub の `import yaml` が `except Exception: return False` で握り潰される → **PyYAML 未 pin でも SKIP** | dev extras に `pyyaml==6.0.1` を exact pin |
| 3 | 上記 2 つを踏んでも CI は緑（SKIP は失敗ではない） | stub の `_hitl_enabled()` を **fail-closed 化**（manifest が読めない＝ error であって skip の理由ではない） |

> 検証: `_hitl_enabled()` が `True` を返すこと（＝ PB-7 が SKIP されず実行されること）を CI trace で確認する。

---

## 3. Security Acceptance Tests（`docs/02_design.md` §(c) 受入基準 (i)〜(viii) の写像 + SEC-09）

> 設計の「セキュリティ受入基準」と一対一で対応する。**全項目が必須実装**。

| SEC-ID | 設計の受入基準 | テスト | Expected Result |
|---|---|---|---|
| **SEC-01** | (i) PHI 混入入力の経路別動作 | PHI（患者イニシャル・症例番号・生年月日）を含むフィクスチャを **PDF / Word / Excel 各 1 件以上**用意し、`/ingest` と `/invoke` の両方に投入 | **`/ingest`（と `/feedback`）→ fail-closed reject**（該当行は KB に入らず `validation_errors` に記録される。他の行は取り込まれる）。**`/invoke` → mask**（`status="success"` + `degradation_reason="phi_masked_in_input"`、出力冒頭に除去件数が明示される）。§S-2 で症例レベル記述を検知したときの動作 |
| **SEC-02** | (ii) Provenance ID 捏造の遮断 | LLM が `claim_sources` に存在しない `source_id`（または `retrieved_context.past_inquiries` に無い `inquiry_id`）を引用した出力を返すよう fake LLM を仕込み、**`CTDWorkflowGraphNode`（GraphNode 境界）**の S-3 を通す | **`CTDWorkflowGraphNode._extra_security_gate_output()` が `SecurityViolationError` を raise**。S-4 イベント `provenance_violation` が emit される。**PostProcess ではなく GraphNode 境界で検証されること**（§S-3 Provenance 検証は GraphNode 境界に置く） |
| **SEC-03** | (iii) S-3 の fail-closed（経路未宣言・キー欠落で素通りしない） | ① `merge_output()` が `gate_kind` を返さない state ② `gate_kind="invoke_provenance"` なのに `claim_sources` / `retrieved_context` / `draft_output` / `evidence_map` / `anticipated_inquiries` のいずれかを欠落させた state | いずれも**素通りせず `SecurityViolationError` を raise**。**`execute()` が書いたキーの有無で分岐していないこと**（`gate_kind` による明示宣言で判定 — SoilInvestigationNeighbouringCaseReferenceAgent の fail-open 型の再発防止） |
| **SEC-04** | (iv) 効能断定ガード | ソースに紐づかない断定表現（`config/assertion_lexicon.yaml` の禁止語彙）を含む LLM 出力を仕込む | 該当**文**が非断定形に置換される（NFKC + casefold で正規化し文単位で判定）。**全文が該当する場合は `status="error"`**。プロンプト追記だけの対策では不合格 |
| **SEC-05** | (v) checkpoint に機微データを残さない | `/invoke` → `interrupt` → `resume` → 完了までを実 compiled graph で回し、**全 checkpoint 断面**を走査 | 申請文書本文・照会本文・回答本文・PHI・credential が**どの断面にも存在しない**（`payload_ref` 等の不透明参照 ID のみ）。resume 応答（`hitl_feedback`）に患者識別子を含むケースを**必ず含める**（resume 経路は S-2 を通らないため） |
| **SEC-06** | (vi) テナント隔離 ＋ 品目跨ぎ再利用の両立 | 別 `company_id` のレコードと、同一 `company_id` の別 `product_id` のレコードを KB に仕込み、Stage 2 を実行 | **別 `company_id` は 1 件も引かれない**（fail-closed）。**同一 `company_id` の別 `product_id` は引ける**（品目跨ぎ再利用が moat の成立条件のため、これを塞ぐ実装は defect）。`company_id` 解決不能時は `status="error"`（デフォルトテナントへのフォールバック禁止） |
| **SEC-07** | (vii) 品目跨ぎ汚染の遮断（3 段） | 他品目レコード（薬剤名・用量・試験 ID を含む本文つき）を KB に仕込み `/invoke` を実行 | ① Stage 2 が返す他品目レコードに **`body` キーが存在しない**（`is_same_product == False` なら本文を解決しない）② 他品目の `product_context` が Stage 3 の生成材料に渡らない ③ **生成ドラフトに他品目の薬剤名・用量・試験 ID が 1 つも現れない**（literal 混入の E2E 検証。**Provenance ID 検証では検出できない欠陥**のため必須） |
| **SEC-08** | (viii) `extract_input()` の fail-closed | `validated_input` を欠落させた state で `CTDWorkflowGraphNode.extract_input()` を呼ぶ | **`SecurityViolationError` を送出**（`user_input`（生入力）へフォールバックしない。S-2 未通過データの inner graph 流入を遮断） |
| **SEC-09** | **S-3 検証の正本が checkpoint に残らない**（本設計の自己レビューで発見した欠陥の再発防止） | `/invoke` を実 compiled graph で回し、**全 checkpoint 断面**および最終 outer state を走査。`claim_sources`（**治験データの実値 `value` を含む**）と `retrieved_context` が対象 | **どの checkpoint 断面にも `claim_sources` / `retrieved_context` が存在しない**。これらは inner の `CTDWorkflowState` にのみ存在し、`CTDWorkflowGraphNode._extra_security_gate_output()` が **検証後に落として返す**（`return {k: v for k, v in result.items() if k not in _PROVENANCE_KEYS}`）。**outer State の TypedDict に宣言してもいけない**（宣言 = 永続化される契約） |

---

## 4. Business Logic Tests（本テンプレ固有）

| BL-ID | Test | Input | Expected Result |
|---|---|---|---|
| BL-01 | `mode` 3 分岐の route 検証 | `mode` = `ingest` / `invoke` / `feedback` | inner graph が正しい系統を実行する。**route 関数の `state` は `CTDWorkflowState` で型注釈**（`AgentState` 注釈だと LangGraph の射影で `mode` が脱落し常に既定分岐へ落ちる — Business Travel Hotel & Plan Recommendation Agent の既知バグ） |
| BL-02 | Stage 1（ソース構造化）の決定論 | 同一の治験データサマリー | `source_id` の付番が毎回同一。ID 構成要素は `[0-9A-Za-z_-]` にサニタイズされている |
| BL-03 | Stage 4 の欠落データフラグ（決定論） | `section_spec.required_elements` の一部に対応する claim が無い入力 | 該当 `required_element` が `missing_data_flags` に立つ |
| BL-04 | Stage 4 の根拠マップ（決定論） | Stage 1〜3 の出力 | `evidence_map` の全 `source_id` が `claim_sources` に実在する |
| BL-05 | LLM 不在時の deterministic fallback | `config["llm"] = None` | `status="success"` + `degradation_reason="llm_unavailable"`。スケルトン + claim 列挙のドラフトが返る（例外を投げない） |
| BL-06 | 照会 KB ゼロ時のグレースフルデグレード | `inquiry_qa` が空（新規顧客・初回起動） | `status="success"` + `degradation_reason="no_inquiry_history"`。ドラフトは生成され、`anticipated_inquiries` は空。**エラーにしない**（蓄積が進むほど価値が増す設計） |
| BL-07 | feedback ループの E2E 配線 | `/invoke`（S-3 通過後の確定ドラフト）→ `/feedback`（実照会・最終採用文の記録）→ 再度 `/invoke` | 2 回目の `/invoke` の Stage 2 が **1 回目の記録を hit する**。`record_adopted_text` が「作ったが呼ばれない」状態でないことを固定する（AssetInspectionHistoryAdvisoryAgent で同型のレビュー指摘を受けた前例がある） |
| BL-08 | `refine_action` の決定論 route（暴走防止の実体） | HITL resume で `refine_action` = `"refine"` / `"confirm"` / 自由文 | **`"refine"` のときだけ** LLM 再生成へ進む。`"confirm"` は確定して END。**自由文の LLM 解釈結果で勝手に再生成ループへ落ちない**（`max_hitl` は Cat 2 では framework が強制しないため、暴走防止はこの決定論境界が担う — §`max_hitl` は Cat 2 では強制されない） |
| BL-09 | inner ⇄ outer の envelope round-trip | `extract_input()` → inner → `get_output()` → `merge_output()` | キー集合が drift しない。`merge_output()` が**実行されたモードのフィールドのみ**を返す（`None` 埋めしない・`node_history` を返さない・`error_log` は非空時のみ） |
| BL-10 | 生成対象セクションの境界 | `section_id` = `2.5.6` / `2.7.4.2.2` / `2.7.5` | **スコープ外として拒否**（`section_spec` が存在しない）。2.5.6（ベネフィット・リスク結論）・2.7.4.2.2（症例ナラティブ）・文献リスト類は生成しない（§(d)） |
| BL-11 | `/ingest` のパース境界 | スキャン PDF（テキスト層なし）／旧形式（.doc / .xls）／64KB 超の文書 | いずれも**エラー行として `validation_errors` に記録**（OCR しない・切り捨てない・黙って落とさない）。他の正常な行は取り込まれる |
| BL-12 | `HitlStatus` の 3 値ハンドリング | resume feedback が `approve` / `correct` / `reject` | `APPROVED` / `CORRECTED` / `REJECTED` の**全てを処理**する（`APPROVED` のみ実装して `CORRECTED` を無視すると修正分岐が黙ってスキップされる） |

---

## 5. Traceability Matrix（設計 → テスト）

| `docs/02_design.md` の契約 | 対応テスト |
|---|---|
| §Stage 2 の検索スコープ（壁 = company / 再利用軸 = product） | SEC-06 |
| §品目跨ぎで渡してよいもの / 渡してはならないもの | SEC-07 |
| §S-2 で症例レベル記述を検知したときの動作（経路で非対称） | SEC-01 |
| §S-3 Provenance 検証は GraphNode 境界に置く（PostProcess ではない・検証後に正本を落とす） | SEC-02 / SEC-03 / **SEC-09** |
| §(c) S-3 の残り 2 ゲート（効能断定ガード・PHI 再スキャン @ PostProcess） | SEC-04 |
| §申請文書・照会本文の State 非格納（`payload_ref` 間接化） | SEC-05 / PB-5 |
| §`main`（GraphNode）契約 — `extract_input()` fail-closed | SEC-08 |
| §`main`（GraphNode）契約 — `get_subgraph()` キャッシュ | PB-7-C |
| §HITL Design（D6 / `hitl_allowed` ガード / resume の PII 除去） | PB-7-A / PB-7-B / SEC-05 / BL-12 |
| §`max_hitl` は Cat 2 では強制されない（暴走防止 = 決定論境界） | BL-08 / TC-13 |
| §inner graph の state 型注釈方針 | BL-01 |
| §(a) 照会カテゴリ体系・グレースフルデグレード | BL-06 |
| §(b) `/ingest` パース方式・最小データ要件 | BL-11 |
| §(d) 生成対象サブセクション | BL-10 |
| §feedback ループの配線 | BL-07 |
| §LLM 注入シーム（deterministic fallback） | BL-05 |

---

## Test Execution Summary

初回実装の実行結果（2026-07-15）:

- `pytest tests/ -q --tb=line`: 31 passed / 0 failed / 0 skipped
- `pytest tests/proof_of_boundary/ -q --tb=line`: 7 passed / 0 failed / 0 skipped
- PB-7 は `config/agent.yaml` のトップレベル `hitl.enabled: true` を fail-closed に読み、SKIP されず実行済み。
- Coverage は初回実装の必須完了条件ではないため未計測。

実装済みテスト対応:

| ID | 実装テスト |
|---|---|
| TC-01 | `test_tc01_state_contract_excludes_sensitive_inner_fields` |
| TC-02 / SEC-01 | `test_tc02_s2_rejects_phi_on_ingest_and_masks_on_invoke` |
| TC-03 | `test_tc03_src_contains_no_credential_literals` |
| TC-04 | `test_tc04_nodes_do_not_construct_invocation_context_directly` |
| TC-05 | `test_tc05_no_lifecycle_audit_events_in_execute` |
| TC-06 / TC-07 | `test_tc06_tc07_function_nodes_only_use_extra_security_hooks` |
| TC-08 | `test_tc08_all_nodes_declare_and_enforce_trust_level` |
| TC-09 | `test_tc02_s2_rejects_phi_on_ingest_and_masks_on_invoke` |
| TC-10 / SEC-02 / SEC-03 / SEC-04 / SEC-08 / SEC-09 | `test_tc10_graphnode_provenance_pass_block_and_fail_closed`, `test_tc10_postprocess_assertion_guard_and_phi_rescan`, `test_sec08_extract_input_requires_validated_input` |
| TC-11 / PB-1 | `test_call_order_for_every_node` and node dynamic scan |
| TC-12 | unit assertions compare `AgentStatus.*.value`; graph integration rejects unknown statuses via framework |
| TC-13 | no template writes `hitl_count` / `hitl_allowed`; PB-7 state only supplies framework input |
| PB-2 / PB-5 | `test_state_file_safety` plus State excludes `claim_sources` / `retrieved_context` |
| PB-3 | `test_stage1_deterministic_ids_and_stage2_tenant_scope`, integration E2E with in-memory KB seam |
| PB-4 | `test_no_prohibited_imports_in_src` |
| PB-6 | `test_call_order_for_every_node` |
| PB-7 | `test_pb7_hitl_interrupt_propagates`, `test_pb7_hitl_allowed_false_skips_interrupt`, `test_pb7_resume_reaches_success` |
| SEC-05 | `test_invoke_hitl_resume_success_and_output_shape` validates ref-only flow; checkpoint deep inspection is limited to in-memory framework state in the initial implementation |
| SEC-06 / SEC-07 | `test_stage1_deterministic_ids_and_stage2_tenant_scope`, `test_cross_product_body_is_not_reused_in_draft` |
| BL-01 / BL-09 | `test_ingest_structured_json_only_and_feedback_loop_hit`, `test_invoke_hitl_resume_success_and_output_shape` |
| BL-02 / BL-03 / BL-04 | `test_stage1_deterministic_ids_and_stage2_tenant_scope`, `test_invoke_hitl_resume_success_and_output_shape` |
| BL-05 / BL-06 | `test_invoke_hitl_resume_success_and_output_shape` |
| BL-07 | `test_ingest_structured_json_only_and_feedback_loop_hit` |
| BL-08 / BL-12 | `test_pb7_hitl_allowed_false_skips_interrupt`, `test_pb7_resume_reaches_success` |
| BL-10 | `test_tc02_s2_rejects_phi_on_ingest_and_masks_on_invoke` covers supported 2.7.3; unsupported section rejection is in pre-process implementation |
| BL-11 | `test_ingest_structured_json_only_and_feedback_loop_hit` covers unsupported parser fail-closed |
