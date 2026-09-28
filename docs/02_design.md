# Template Design Specification — HCR-C2-060 CTD Clinical Summary Drafting & Regulatory Inquiry-History Agent

> 本書は 2026-07-13 に承認された提案に基づく設計仕様である。
> 本書は設計の正本であり、実装はここで確定した契約に従う。

## 🔴 初回実装スコープ（MVP — 2026-07-15 確定）

> 本書は**テンプレートの完全形**を定義する。初回実装は**価値の核（`/invoke` による照会予防ドラフト生成）を
> 1 セクションで薄く通す MVP** に絞り、残りは後続イテレーションに回す。フル機能を一度に実装すると
> 設計乖離・fail-closed の払いが後段で多発するため、価値実証を優先する。**設計章の契約自体は変更しない**
> — スコープの線引きだけを本節で宣言する。

| 領域 | 初回実装（今回）| 後続イテレーション |
|---|---|---|
| **`/invoke` パイプライン** | ✅ **Stage 1〜4 + HITL を完全実装**（これが価値の核） | — |
| **section_spec** | ✅ **2.7.3 の 1 本のみ**（`config/section_specs/2.7.3.yaml`。照会が最も集中する有効性セクション） | 2.5.1〜2.5.5 / 2.7.1 / 2.7.2 / 2.7.4 を追加（config 追加のみでコード不変が設計目標） |
| **S-3（Provenance / 効能断定 / PHI 再スキャン）** | ✅ **完全実装**（2 箇所配置。規制文書の必須ガード） | — |
| **S-2（PHI 検知・経路非対称）** | ✅ **完全実装**（invoke=mask / ingest・feedback=reject） | — |
| **`/ingest`** | ⚠️ **mode 分岐と write_records の骨格のみ**。**入力は構造化済み JSON を前提**とし、`parse_documents`（PDF/Word/Excel 決定論パース）は **`structured_json` 単一経路の最小実装**にとどめる（他形式は `no_applicable_parser` として `validation_errors` へ・明示スタブではなく fail-closed 拒否） | `parse_documents` に PDF(`pypdf`)/Word(`python-docx`)/Excel(`openpyxl`) の 3 形式パーサを追加。`structure_with_llm` / `classify_inquiry_category` の LLM 経路を拡充 |
| **`/feedback`** | ⚠️ **mode 分岐と `resolve_inquiry`/`update_inquiry_qa`/`record_adopted_text` の骨格のみ**。E2E 配線（invoke→feedback→次回 invoke で hit）は**通す**が、文体学習の高度化は後回し | 文体学習シグナルの高度化・想定照会的中率の分析 |
| **KB（`submission_sections` / `inquiry_qa`）** | ✅ **スキーマ定義 + テナント fail-closed 検索 + 品目跨ぎ再利用**を実装。初期投入は最小サービス経由（大量 ingest UI は範囲外） | ingest パーサ経由の一括投入 |

- **MVP の受け入れ線**: 治験データサマリー（構造化 JSON）を `/invoke` に投入 → 2.7.3 のドラフト + 想定照会リスト
  + 根拠マップ + 欠落データフラグが返り、HITL 壁打ちが機能し、S-2/S-3 が設計どおり fail-closed で効くこと。
- **骨格のみの領域（`/ingest` パーサ拡張・追加 section）も、`mode` 分岐・DB 契約・テナント境界は MVP で通す**
  （後から中身を足すだけでよい形にする）。**「未実装だから素通り」を作らない** — 未対応入力は fail-closed 拒否し
  `validation_errors` / `degradation_reason` で明示する（黙って落とす経路を残さない）。
- セキュリティ受入基準（§(c) (i)〜(viii)）は **MVP スコープ内で全項目を満たす**（S-2/S-3 は今回完全実装のため）。

## Position in AgentCore Architecture

- **Agent Class**: `CTDClinicalSummaryAgent`
- **L1 Base**: `AgentBaseGraph`（L1 直接継承。main スロットに `GraphNode` を置く Cat 2 標準構成）
- **Three-Layer Separation**:
  - State: flat TypedDict composition（Pydantic 不使用 — msgpack 非互換のため）
  - Node: L1 継承（Template Method: `execute(self, state) -> dict` のみ override）
  - Graph: composition（`register_nodes()` によるノード差し替え。`add_edges()` / `route()` は override しない）

> 旧 4 層モデル（Level 2 base 継承）は廃止済み。本テンプレートは **L1 Base = `AgentBaseGraph`** を直接継承する。

## Architecture Overview

固定 5 ノード backbone（`initialize` / `finalize` は framework 自動注入。テンプレートは
`pre_process` / `main` / `post_process` の 3 スロットを埋める）。

```
[薬事担当者 / メディカルライター]
  --(mode: ingest / invoke / feedback + payload_ref)-->
  pre_process   入力検証・テナント解決（壁 = company_id の fail-closed。product_id は
                必須の業務コンテキストであり隔離の壁ではない — §Stage 2 の検索スコープ）・
                セクション種別（2.5.x / 2.7.x）検証・S-2 入力ゲート（症例レベル記述の検知 →
                ingest/feedback=reject・invoke=mask）
      |
  main (GraphNode → domain_workflow_graph)
      ├ ingest   : 異種パース（PDF/Word/Excel）→ LLM 構造化 → 照会カテゴリ分類 → 2 系列 INSERT
      ├ invoke   : Stage1 ソース構造化(決定論) → Stage2 テナント内 KB 検索 → Stage3 LLM サブセクション生成
      │            → Stage4 想定照会リスト＋根拠マップ＋欠落データフラグ → hitl_refine（D6 interrupt）
      └ feedback : inquiry_qa 確定記録・最終採用文の文体学習ソース化
      |
  post_process  S-3（効能断定ガード・PHI 再スキャン）→ 出力整形
                ※ S-3 Provenance ID 検証は main（GraphNode）の _extra_security_gate_output() が担う
                  （照合の正本を outer state = checkpoint に載せないため）
      |
  finalize
[出力: サブセクションドラフト ＋ 想定照会リスト ＋ 根拠マップ ＋ 欠落データフラグ]
```

### Node Configuration（outer graph）

| Node | Responsibility | Input State | Output State | Inherits/Overrides |
|------|---------------|-------------|--------------|-------------------|
| initialize | セッション初期化・trace_id 発行 | — | — | `InitializeNode`（framework default） |
| pre_process | 入力検証・**テナント解決（隔離の壁 = `company_id` のみ・fail-closed。`product_id` は必須の業務コンテキスト兼スコアの優先軸であり、隔離の壁ではない — §Stage 2 の検索スコープ）**・セクション種別検証・S-2 入力ゲート（症例レベル記述の検知 → `/ingest` `/feedback` は fail-closed reject、`/invoke` は mask。§S-2 で症例レベル記述を検知したときの動作 参照） | `user_input`（JSON 文字列: `mode`, `company_id`, `product_id`, `section_id`, `payload_ref`） | `validated_input`, `tenant_context`, `section_spec`, `status` | `CTDPreProcessNode`（`FunctionNode` 継承） |
| main | `GraphNode` — 内側 `domain_workflow_graph` を `mode` で分岐実行 | `validated_input` | mode により異なる（次項「main（GraphNode）契約」参照） | `CTDWorkflowGraphNode`（`GraphNode` 継承） |
| post_process | **S-3 の残り 2 ゲート（効能断定ガード・PHI 再スキャン）**＋出力整形。**Provenance ID 検証は担わない**（照合の正本が届かない ＝ 正本を outer state に押し出すと checkpoint 汚染になるため、GraphNode 境界で実施する。§S-3 Provenance 検証は GraphNode 境界に置く） | `draft_output` / `ingest_summary` / `feedback_result`（mode 依存） | `formatted_output`, `status` | `CTDPostProcessNode`（`FunctionNode` 継承） |
| finalize | 最終出力の確定 | — | — | `FinalizeNode`（framework default） |

### `main`（GraphNode）契約 — `CTDWorkflowGraphNode`

| メソッド / 属性 | 設計方針 |
|---|---|
| `get_subgraph()` | `CTDWorkflowGraph`（`src/graph/domain_workflow_graph.py`）を **lazy 生成してインスタンスにキャッシュ**（`self._subgraph is None` のときのみ構築）。毎 `execute()` で新規生成すると HITL resume が収束せず無限ループになる（Cat2 HITL 実機知見）。⚠️ **キャッシュしてよいのは「コンパイル済みグラフ構造」だけ**であり、呼び出しごとに変わるデータ（`self._result` / テナント値 / 中間結果）を node インスタンスに持たせてはならない（node インスタンスは全呼び出しで共有されるため）。テナント・セッション分離は checkpointer の `thread_id` 名前空間が担保しており、不変のグラフ構造を共有しても呼び出し間でデータは混ざらない |
| `extract_input()` | **`state["validated_input"]` のみを読む（fail-closed）**。`validated_input` が無い＝`pre_process` の S-2 入力ゲートを通っていない状態であり、`state["user_input"]`（生入力）へフォールバックすると **S-2 未通過の生データが inner graph に流れ込む fail-open** になる。本 AG は PHI・機微申請文書を扱うため、欠落時は `SecurityViolationError` を送出する（同系のテンプレートは `user_input` フォールバックを持つが、本 AG では意図的に採用しない）。渡す内容は `pre_process` が正規化した JSON 文字列（`mode` / `company_id` / `product_id` / `section_id` / `payload_ref` / `tenant_context` / `section_spec`）。`BaseGraph.invoke()` は単一 str 入力のため JSON 直列化が必須 |
| `merge_output()` | inner の `get_output()` が返す `sub_result` から **実行されたモードのフィールドのみ**を差分反映する: `/invoke` → `gate_kind="invoke_provenance"`, `draft_output`, `anticipated_inquiries`, `evidence_map`, `missing_data_flags`, `status`（HITL 中は `hitl_draft`, `refine_action`）＋ **S-3 検証用に一時的に** `claim_sources`, `retrieved_context`（**`_extra_security_gate_output()` が検証後に落とすため outer state には残らない**）／`/ingest` → `gate_kind="ingest"`, `ingest_summary`, `status`／`/feedback` → `gate_kind="feedback"`, `feedback_result`, `status`。**存在しないフィールドを `None` で埋めない**（空表示の原因）。`node_history` は返さない（framework の reducer 管理）。`error_log` は非空時のみ返す |
| `error_strategy` | `"propagate"`（デフォルト）— inner のエラーは `SubgraphError` として再送出し outer を `status="error"` にする。規制申請文書という性質上、部分的な空ドラフトを返すより明示的なエラー停止を優先する |
| `propagate_hitl` | **`True`** — 薬事担当者は外側 API 呼び出し元。inner の `hitl_refine` が発する `interrupt()` を `/invoke` 応答 `status: awaiting_human` として表面化し、`agent.resume(thread_id=…, feedback=…)` で再開する |
| `required_trust_level` | **`VERIFIED_EXTERNAL`**（明示宣言）— `GraphNode` の既定は `ANONYMOUS` であり、内側ノードの S-1 要求と不整合になる（SubcontractorQuotationDisputeHistory で実バグ検出） |
| `_extra_security_gate_output()` | **S-3 Provenance ID 機械検証をここで実施する**（PostProcess ではない）。`merge_output()` が `gate_kind` と照合の正本（`claim_sources` / `retrieved_context`）を載せ、本フックが ①`gate_kind` で経路判定 ②必須フィールド欠落なら raise（fail-closed）③引用 ID の実在照合 ④**検証後に正本キーを落として返す**（checkpoint 汚染の防止）。§S-3 Provenance 検証は GraphNode 境界に置く |

### Data Flow

```
START → initialize → pre_process → main → {route} → post_process → finalize → END
                                            ↓ (RETRY, max 3)
                                          pre_process
```

`route()` は framework 標準（`state["status"]` を読み `success`→post_process / `retry`→pre_process /
他→finalize）。`add_edges()` / `route()` は override しない。

---

## `/invoke` の 4 ステージパイプライン（inner `domain_workflow_graph`）

> 「照会予防の材料を集めるのは決定論と検索、書くのが LLM」— Stage 1/2 を Tool 相当の決定論・検索処理として
> 切り出し、LLM 必須性を Stage 3/4 に集約する。

| Stage | 処理内容 | 実装方式 | 入力 | 出力 |
|---|---|---|---|---|
| **Stage 1: ソース構造化** | 治験データサマリー（CSR サマリー・統計解析結果サマリー）を **claim 単位に構造化**し、全 claim に `source_id` を決定論的に付番 | **決定論・非 LLM**（同一入力→同一出力・単体テスト可能） | `payload_ref` から service 層が解決した入力ソース | `claim_sources`: `[{source_id, source_type, section_ref, content_digest, value}]` |
| **Stage 2: KB 検索** | ①類似する自社過去セクション（`submission_sections`）②当該セクション種別への過去照会（`inquiry_qa`）を **テナント境界 = `company_id` の fail-closed フィルタ**で検索（§Stage 2 の検索スコープ 参照。品目跨ぎの再利用は意図的に許可し、品目固有の医学的内容は引き当て条件に使わない） | **検索**（LLM 推論を介さない） | Stage 1 の `claim_sources` + `section_spec` + `tenant_context` | `retrieved_context`: `{past_sections: [{section_ref_id, …}], past_inquiries: [{inquiry_id, category, …}]}` |
| **Stage 3: サブセクション生成** | `section_spec` のスケルトン（ICH M4E 記載要件）＋自社過去文体＋照会予防観点で **サブセクション単位**のドラフトを生成。全数値・主張に `source_id` 引用必須 | **LLM**（LLM 不在時は deterministic fallback = スケルトン＋claim 列挙、`degradation_reason` 付き） | Stage 1 `claim_sources` + Stage 2 `retrieved_context` + `section_spec` | `draft_output`: `{section_id, body, cited_source_ids: [...]}` |
| **Stage 4: 想定照会・根拠マップ** | ①想定照会リスト（`inquiry_qa` の照会カテゴリ×セクション種別×論点構造から予測）②根拠マップ（claim → source 対応表・決定論生成）③欠落データフラグ（`section_spec` の必須記載要素のうち claim が無いもの・決定論生成） | **LLM（①）＋ 決定論（②③）** | Stage 1〜3 の全出力 | `anticipated_inquiries`: `[{category, question, basis_inquiry_ids: [...]}]`, `evidence_map`: `[{claim, source_id}]`, `missing_data_flags`: `[{required_element, reason}]` |
| **HITL: hitl_refine** | D6 `interrupt()` — サブセクションドラフト＋想定照会リストを薬事担当者に提示 → `refine`（修正指示）/ `confirm`（確定）を受領。**`hitl_allowed` ガード必須** | 人間 + LLM（refine 時の再生成） | Stage 3/4 の出力 | `hitl_draft`, `refine_action`, 確定 `draft_output` |

### 🔴 Stage 2 の検索スコープ — テナント境界と再利用軸を分けて考える（設計の核心）

**テナント境界（＝越えてはならない壁）と 再利用軸（＝越えることに価値がある軸）は別の概念である。**

| | 何を | 動作 |
|---|---|---|
| **テナント境界** | `company_id` | **fail-closed**。他社の `submission_sections` / `inquiry_qa` は一切参照しない。`company_id` が解決不能なら `status="error"`（デフォルトテナントへのフォールバック禁止）。クロステナント学習なし |
| **再利用軸（品目跨ぎ）** | `section_id` × `inquiry_category` × `issue_structure` | **同一社内であれば `product_id` を跨いで引き当てる**。「A 品目の 2.7.3 で統計手法（`STAT`）の照会を受けた」という**論点構造の知見は B 品目の 2.7.3 にも効く** — これが本 AG の moat（照会予防）の成立条件そのものである |
| **再利用しないもの** | `product_context`（対象疾患・エンドポイント・有効性データ等の医学的内容） | **引き当て条件にも生成材料にも使わない**。品目固有の医学的内容を跨いで再利用すると誤った類推を生む |

- **`inquiry_qa`（照会 KB）のクエリ**:
  `WHERE company_id = :company_id AND section_id = :section_id AND inquiry_category IN (:frequent_categories)`
  — `product_id` は**引き当て条件に含めない**（同一社内の他品目の照会も引く）。
  自品目の過去照会（`product_id` 一致）は**スコアを優先**するが、他品目を排除はしない
  （新規品目には自品目の履歴が存在しないため、`product_id` で絞ると初回起動時に KB が常に空になり
  照会予防が原理的に成立しない）。

### 🔴 品目跨ぎで渡してよいもの / 渡してはならないもの（実装契約・必須）

> ⚠️ **他品目の申請文書本文をそのまま Stage 3 の生成材料に渡してはならない。**
> `submission_sections.body` には旧品目の**薬剤名・用量・試験 ID・エンドポイント**が literal に含まれる。
> これを LLM に「文体参考」として渡すと、LLM が他品目の一文をほぼそのまま引き写し、
> **現行品目の `source_id` を貼り付けた「別の薬剤名が書かれたドラフト」**を生成する。
> Provenance ID 検証は「引用 ID の実在」しか見ないため、この誤りを検出できない（ID は実在するから）。
> **規制申請文書としては致命的な欠陥であり、機械的に遮断する。**

| KB | 自品目（`product_id` 一致） | 他品目（同一 `company_id`・`product_id` 不一致） |
|---|---|---|
| `inquiry_qa`（照会） | 照会文の骨子・カテゴリ・論点構造を渡す（`product_context` は除外） | **論点構造（`issue_structure`）・カテゴリのみ**を渡す。照会文の生テキストは渡さない |
| `submission_sections`（申請文書本文） | **本文（`body_ref` から解決した実体）を文体参考として渡してよい** | **本文を渡してはならない**。渡してよいのは**セクション骨格メタデータ**（見出し構成・段落数・記載要素の充足パターン）のみ。本文テキスト・薬剤名・用量・試験 ID は一切渡さない |

- Stage 2 は `retrieved_context` を組み立てる時点で、**他品目レコードから本文フィールドを落とす**
  （service 層で `body_ref` を解決しない）。「Stage 3 に渡ってから消す」のではなく、
  **取得段階で構造的に持たせない**（fail-closed）。
- 実装契約: `retrieved_context.past_sections[]` の各要素は `{section_ref_id, product_id, is_same_product,
  skeleton_meta, body?}` とし、**`body` は `is_same_product == True` のときのみ存在する**。
  Stage 3 のプロンプト構築コードは `body` の有無で分岐し、**他品目レコードの本文を要求しない**。
- 受入基準 (vii)（下記）で、他品目の薬剤名・用量が生成ドラフトに現れないことを検証する。

> ⚠️ 「テナント fail-closed = company × product」と書くのは**誤り**。それでは新規品目で KB が常に空になり、
> 本 AG の価値（照会履歴フライホイール）が原理的に成立しない。**壁は他社との間にだけ立てる。
> 社内では「論点構造」は品目を跨いで学び、「本文・医学的内容」は品目を跨がせない。**

**Stage 3/4 の制約（S-3 出力ゲートで機械的に強制）**:
- LLM は Stage 1/2 が提示した材料にない主張・数値を追加できない（プロンプトで「`source_id` 引用必須・
  提示ソースのみ参照」を制約）。
- 全 claim に根拠 ID（Stage 1 の `source_id`）、全想定照会に根拠 ID（Stage 2 の `inquiry_id`）の引用を必須とする。
- **Provenance 検証の守備範囲**: 本検証は「入力に実在しない ID・数値ソースの捏造を機械遮断」するものであり、
  **「数値解釈の正しさ」は保証しない**。解釈の正しさは根拠マップ（claim → source 対応表）経由の人間レビュー
  （薬事担当・メディカルライター）が担保する — この役割分担を設計として明示する。

## `/ingest` `/feedback` の内部ノード構成

### `/ingest`（過去申請文書・照会履歴の取込）

```
START → parse_documents → structure_with_llm → classify_inquiry_category → validate_records → write_records → END
```

| ノード | 責務 | LLM 使用 |
|---|---|---|
| `parse_documents` | PDF / Word（.docx）/ Excel（.xlsx）を **決定論のテキスト＋表抽出**（§(b) パース方式）。抽出不能な入力はエラー行へ | 不使用 |
| `structure_with_llm` | 抽出テキストから構造化フィールド（セクション番号・照会本文・回答本文・審査結果メタ）を抽出 | **使用**（構造化抽出のみ。医学的解釈は行わない） |
| `classify_inquiry_category` | 照会を **6 カテゴリ**（§(a)）に分類。品目固有の医学的文脈は `product_context` メタデータへ分離格納 | **使用**（分類のみ） |
| `validate_records` | 必須フィールド充足チェック（§(b) 最小データ要件）。不足行は `validation_errors` へ | 不使用 |
| `write_records` | `submission_sections` / `inquiry_qa` へ INSERT（テナントスコープ付与）。書込件数・エラー行を `ingest_summary` へ集約 | 不使用 |

### `/feedback`（実照会・最終採用文の記録）

```
START → resolve_inquiry → update_inquiry_qa → record_adopted_text → END
```

| ノード | 責務 | LLM 使用 |
|---|---|---|
| `resolve_inquiry` | 対象 `inquiry_id`（新規なら採番）を解決。テナントスコープ検証 | 不使用 |
| `update_inquiry_qa` | 実際に受けた照会・回答・審査結果を確定 UPDATE / INSERT。`predicted_by_agent`（本 AG の想定照会と一致したか）を決定論照合して記録 | 不使用 |
| `record_adopted_text` | 最終採用文を `submission_sections` に `operator_edited=true` で記録（文体学習ソース・改善シグナル）。**`/invoke` で生成したドラフトとの差分**も記録し、次回 Stage 2 検索の品質シグナルとする | 不使用 |

`/ingest` `/feedback` はいずれも DB 書込副作用を持つため、S-1 は `VERIFIED_EXTERNAL` 以上を要求する。

> **feedback ループの配線（必須）**: `record_adopted_text` を「作ったが呼ばれない」状態にしてはならない
> （AssetInspectionHistoryAdvisoryAgent で同型のレビュー指摘を受けた前例がある）。`/invoke`（S-3 通過後の確定ドラフト）→ `/feedback`（採用文記録）→
> 次回 `/invoke` Stage 2 で hit する、の E2E を integration テストで固定する。

### 🔴 inner graph の state 型注釈方針（実装契約・必須）

- inner graph は自身の state スキーマ **`CTDWorkflowState`** を持つ。**この型名は本 Design で確定とし、
  実装は改名せずこの名前を使う。**
- **`mode` 分岐の route 関数・`hitl_refine` 後の route 関数・`get_output()` は、`AgentState` ではなく
  `CTDWorkflowState` で `state` を型注釈しなければならない。**
- 理由: route 関数の `state` を `AgentState`（基底スキーマ）で注釈すると、LangGraph の状態射影により
  カスタムフィールド（`mode` 等）が脱落し、常に既定分岐へ落ちる（Business Travel Hotel & Plan Recommendation Agent で実機発生した既知の落とし穴）。

### 🔴 HITL checkpointer の分離（実装契約・必須）

- **inner / outer の checkpointer は別インスタンス**とする。同一 saver を共有すると同一 `thread_id` +
  空 namespace で checkpoint が相互汚染し、resume が収束しない（BusinessDiningAndEntertainmentPlannerAgent 実測）。
- inner の checkpointer は `CTDWorkflowGraphNode.get_subgraph()` 側で**専用 saver を新規生成**して
  `compile(checkpointer=…)` に明示的に渡す。**引数なし `compile()` は HITL 有効時 RuntimeError**。

---

## State Definition（`src/schemas/state.py` — `AgentState` 継承 flat TypedDict）

> **実装ステータス**: `src/schemas/state.py` は本書の執筆時点では雛形のままだった（実装フェーズのスコープ）。
> 下表は `State(AgentState)` に反映すべきフィールド契約であり、**本設計書が正本**。

| Field | Type | Purpose | Required |
|-------|------|---------|----------|
| `validated_input` | `str` | **`pre_process` が S-2 入力ゲート通過後に生成する正規化済み JSON 文字列**（`mode` / `company_id` / `product_id` / `section_id` / `payload_ref` / `tenant_context` / `section_spec` を含む）。`CTDWorkflowGraphNode.extract_input()` と `CTDPostProcessNode` の S-3 経路判定が読む**唯一の信頼できる入力**。**checkpoint を跨いで保持されなければならない**（retry / HITL resume 後も残ること。欠落＝S-2 未通過とみなし fail-closed で raise する契約のため、本フィールドは必ず State 宣言に含め、途中のノードが握り潰さないこと） | Yes |
| `mode` | `str` | `"ingest"` / `"invoke"` / `"feedback"` の 3 値 | Yes |
| `company_id` | `str` | テナント識別子（会社）。fail-closed の起点 | Yes |
| `product_id` | `str` | **業務コンテキスト（品目）。3 モードすべてで必須だが、隔離の壁ではない** — `/ingest` `/feedback` では書込レコードに付与し、`/invoke` の Stage 2 では**引き当て条件ではなくスコアの優先軸**として使う（`product_id` で絞ると新規品目で KB が常に空になる。§Stage 2 の検索スコープ）。**KB クエリの `WHERE` に `product_id` を入れる実装は defect** | Yes |
| `section_id` | `str` | 対象サブセクション（`"2.5.4"` / `"2.7.3.3"` 等）。`/invoke` で必須 | invoke 時 Yes |
| `payload_ref` | `str` | **リクエスト生本文の不透明参照 ID**（本文実体は State に載せない。§申請文書の State 非格納 参照） | Yes |
| `tenant_context` | `dict` | `pre_process` が解決したテナントメタ。**`company_id`（隔離の壁・fail-closed 検証済フラグ付き）と `product_id`（業務コンテキスト・優先軸）を明確に区別して保持する**（`{"company_id": …, "isolation_verified": true, "product_id": …}`。実装者が両者を同格の「テナントキー」として扱わないための構造） | No |
| `section_spec` | `dict` | 選択されたサブセクションスケルトン（ICH M4E 記載要件・必須記載要素リスト・照会頻出観点） | No |
| ~~`claim_sources`~~ | — | **outer State に宣言しない**。Stage 1 の claim には**治験データの実値（`value`）**が入るため、outer state に載せると checkpoint DB に平文で永続化される（本設計の不変条件違反）。inner の `CTDWorkflowState` にのみ存在し、`CTDWorkflowGraphNode._extra_security_gate_output()` が provenance 検証に使った後に落とす（§S-3 Provenance 検証は GraphNode 境界に置く） | — |
| ~~`retrieved_context`~~ | — | **outer State に宣言しない**（同上。過去照会の本文を含み得るため）。inner にのみ存在し、GraphNode の S-3 で消費されて消える | — |
| `gate_kind` | `str` | **`merge_output()` が明示的に宣言する S-3 経路種別**（`"invoke_provenance"` 等）。GraphNode の S-3 フックはこの値で経路を判定する。**キーの有無で分岐しない**（欠落時に素通りする fail-open の防止。SoilInvestigationNeighbouringCaseReferenceAgent の実事故） | invoke 時 Yes |
| `draft_output` | `dict` | Stage 3 が生成したサブセクションドラフト（`{section_id, body, cited_source_ids}`） | No |
| `anticipated_inquiries` | `list[dict]` | Stage 4 の想定照会リスト（`{category, question, basis_inquiry_ids}`） | No |
| `evidence_map` | `list[dict]` | Stage 4 の根拠マップ（`{claim, source_id}`） | No |
| `missing_data_flags` | `list[dict]` | Stage 4 の欠落データフラグ（`{required_element, reason}`） | No |
| `ingest_summary` | `dict` | `/ingest` 結果サマリ（取込件数・エラー行・分類結果内訳） | No |
| `feedback_result` | `dict` | `/feedback` 確定結果（更新 `inquiry_id`・想定照会の的中/空振り照合結果） | No |
| `refine_action` | `str` | HITL feedback の正規化済みアクション: `"refine"` / `"confirm"`。`hitl_refine` 後の**決定論 route が読む唯一の欄** | No |
| `formatted_output` | `dict` | `post_process` が整形した最終出力 | No |
| `status` | `str` | ノードが返す実行状態。**`AgentStatus` の値のみ**（下記 🔴 参照） | Yes |
| `degradation_reason` | `str` | 縮退動作の理由（`"llm_unavailable"` / `"no_inquiry_history"` 等）。正常時は未設定 | No |

**HITL state 欄**: schema 1.4 の許可 7 欄（`hitl_feedback` / `hitl_metadata` / `hitl_status` / `hitl_draft` /
`hitl_count` / `hitl_allowed` / `subgraph_thread_id`）のみ使用。**`hitl_count` / `hitl_allowed` は
テンプレートから書き込まない**（framework / Gateway 専用）。

### 🔴 `status` の有効値（実装契約・必須）

`status` に代入してよいのは framework の `AgentStatus` enum の値のみ。本テンプレートで使用するのは:

| 値 | 用途 | `route()` の遷移先 |
|---|---|---|
| `AgentStatus.SUCCESS.value`（`"success"`） | 正常完了（**縮退動作を含む**） | `post_process` |
| `AgentStatus.RETRY.value`（`"retry"`） | 一時障害。`pre_process` からやり直す価値がある場合のみ | `pre_process`（`max_retry` まで） |
| `AgentStatus.ERROR.value`（`"error"`） | 恒久障害・fail-closed（テナント解決失敗・provenance 検証失敗等）。リトライしない | `finalize` |

- **`status` には必ず `.value`（文字列）を代入する**。bare Enum（`AgentStatus.SUCCESS`）の返却は
  bare Enum を返すと `status` の文字列規約に反する。
- **`"degraded"` は `AgentStatus` に存在しない。絶対に代入してはならない**（`UnknownStatusError` で停止）。
  縮退は `status="success"` のまま `degradation_reason` に理由を格納し、`post_process` が出力冒頭に明示する。
- `awaiting_human` は HITL interrupt に伴い framework が設定する。テンプレートのノードが直接返す値ではない。

**State Constraints（必須）**:
- Flat TypedDict のみ（primitives + JSON シリアライズ可能な型）。Pydantic / dataclass 禁止（msgpack 非互換）
- JWT / API キー / credential を State に置かない
- `InvocationContext` は `config["configurable"]` 経由のみ（State に含めない）。ノード内では
  **`InvocationContext.from_state(state)`** を使う（直接構築 `InvocationContext(...)` は `NullProvider` が
  束縛され `ctx.secrets.require()` が常に `MissingSecret` を送出する）
- **申請文書・照会本文の実体を State に載せない**（下記 🔴 参照）

### 🔴 申請文書・照会本文の State 非格納（本テンプレ固有の不変条件）

- **申請文書本文・照会本文・回答本文の実体を、どのノードの戻り値にも載せない**。checkpointer は
  super-step ごとに State を永続化するため、ノード戻り値に載った時点で checkpoint DB に平文で残る。
- リクエスト生本文は **`payload_ref`（TTL 付き transient store の不透明参照 ID）**として State に載せ、
  実体解決は inner ノードの `execute()` 内でのみ一時的に行う（`/ingest` `/feedback` は fail-closed、
  `/invoke` は縮退）。API アダプタ（`src/api/server.py`）が本文を transient store に預ける
  （SubcontractorQuotationDisputeHistory でレビュー指摘への対応として確立したパターン）。
- KB からの読出しも同様に、`submission_sections.body_ref` / `inquiry_qa.inquiry_text_ref` は参照 ID であり、
  実体は service 層でのみ解決する。State には参照 ID のみ載せる。

---

## データモデル（AG がスキーマを設計・所有）

```
submission_sections   自社の過去申請文書セクション ★moat の核（文体・記載粒度の学習ソース）
  section_ref_id, company_id, product_id, submission_id, section_id(2.5.4 等),
  body_ref(本文実体は文書ストア。DB には参照のみ), body_digest,
  skeleton_meta(見出し構成・段落数・記載要素の充足パターン — 品目跨ぎで渡してよい唯一の要素),
  therapeutic_area, submission_type(新規承認/一部変更), review_outcome,
  operator_edited(bool), adopted_at, created_at
  ※ body_ref の解決（本文実体の取得）は product_id が現在の対象品目と一致する場合のみ。
     他品目のレコードは skeleton_meta のみを返す（§品目跨ぎで渡してよいもの・fail-closed）

inquiry_qa            PMDA 照会事項・回答ペア ★moat の核（照会予防の学習ソース）
  inquiry_id, company_id, product_id, submission_id,
  section_id(照会が向けられたセクション), inquiry_category(6分類・§(a)),
  issue_structure(論点構造 — 品目非依存の再利用軸), inquiry_text_ref, response_text_ref,
  product_context(品目固有の医学的文脈 — 分離格納・品目跨ぎ再利用しない),
  predicted_by_agent(bool — 本AGの想定照会と一致したか), received_at, resolved_at
```

**再利用性の設計原則（設計判定時のレビュー指摘の反映）**:
> **品目横断で再利用できるのは「照会カテゴリ × セクション種別 × 論点構造」の 3 軸のみ。
> 品目固有の医学的内容（対象疾患・エンドポイント・有効性データ）は `product_context` に分離格納し、
> 品目を跨いで再利用しない。**

**クロステナント学習なし（明示宣言）**: **他社**の照会・申請文書は一切参照しない。Stage 2 の KB 検索クエリは
`company_id` フィルタを **fail-closed** で強制する（解決不能なら `status="error"`。
デフォルトテナントへのフォールバック禁止）。保存時暗号化はプラットフォーム責務として区分する。

> **同一社内の品目跨ぎ参照は意図的に許可する**（§Stage 2 の検索スコープ）。これは「クロステナント」ではない
> — テナント境界は `company_id` であり、`product_id` は再利用軸であって境界ではない。

---

## 🔴 Design 段階チェックリスト（design-entry blocker #1〜#4 の確定）

> 設計開始時に特定された 4 件の検討事項に対する確定回答を記録する。

### (a) 照会カテゴリ体系（6 分類）と `inquiry_qa` スキーマ ← blocker #1

**照会カテゴリ（初期セット・品目非依存の再利用軸）**:

| コード | カテゴリ | 典型的な論点構造 |
|---|---|---|
| `STAT` | 統計手法 | 解析対象集団の定義／欠測値の取扱い／多重性調整／感度分析の妥当性 |
| `SAFETY_SIGNAL` | 安全性シグナル評価 | 有害事象の因果関係評価／用量依存性／特定集団での発現率差 |
| `EFFICACY_BASIS` | 有効性主張の根拠 | 主要評価項目の臨床的意義／副次評価項目からの主張の妥当性／対照群設定 |
| `CONSISTENCY` | 記載整合性 | モジュール間の数値不一致／2.5 と 2.7 の記述矛盾／CSR との齟齬 |
| `QUALITY_REF` | 製造・品質参照 | 臨床データが参照する製剤ロット／規格との整合 |
| `OTHER` | その他 | 上記に該当しない照会（分類精度の改善シグナルとして蓄積） |

- カテゴリは **config（`config/inquiry_categories.yaml`）で定義**し、コード変更なしに追加可能とする。
  **schema_owner = 顧客の薬事部門**（版管理付き。`schema_version` / `owner` を config に明記）。
- `issue_structure` は照会文から LLM が抽出する構造化論点（例: `"missing_sensitivity_analysis"`）。
  カテゴリより粒度が細かい再利用軸。
- **品目固有の医学的文脈は `product_context`（対象疾患・エンドポイント・投与経路等）へ分離格納**し、
  Stage 2 の検索は「同一 `company_id` かつ 同一 `section_id` かつ 同一 `inquiry_category`」で引き当て、
  **`product_context` は引き当て条件にも生成材料にも使わない**（品目跨ぎの医学的内容の再利用を構造的に禁止）。
  `product_id` は**引き当て条件ではなくスコアの優先要素**として扱う（§Stage 2 の検索スコープ）。

**グレースフルデグレード（照会 KB ゼロでも動く）**: 顧客の過去申請文書・照会記録の電子化状況はばらつく。
`inquiry_qa` が空でも、`section_spec` スケルトン＋`source_id` 引用付きドラフトは生成できる
（`degradation_reason="no_inquiry_history"` 付き `status="success"`）。蓄積が進むほど照会予防・文体学習が効く。

### (b) `/ingest` の最小データ要件とパース方式 ← blocker #2

**サポート入力形式とパース方式（決定論・LLM 不使用）**:

| 形式 | パース方式 | ライブラリ（`pyproject.toml` に `==` 固定ピン） |
|---|---|---|
| PDF | テキスト層抽出。**スキャン PDF（テキスト層なし）は OCR せずエラー行として拒否**（OCR 誤読が数値捏造の温床になるため、本 AG のスコープ外と明示） | `pypdf==5.1.0` |
| Word (.docx) | 段落・表の構造抽出（見出しレベルからセクション番号を推定） | `python-docx==1.1.2` |
| Excel (.xlsx) | シート・セル値抽出（照会一覧表の典型形式に対応） | `openpyxl==3.1.5` |

- **旧形式（.doc / .xls）は非対応**（明示宣言）。顧客側で変換して投入する前提。
- パース結果のテキストは `structure_with_llm` に渡す前に**サイズ上限**（既定 64KB/文書）を課す。
  超過分は**切り捨てず**、エラー行として `validation_errors` に記録する（黙って落とさない）。

**最小データ要件**:

| 系列 | 必須フィールド | 欠落時の扱い |
|---|---|---|
| `submission_sections` | `company_id`, `product_id`, `section_id`, 本文 | `validation_errors` へ。その行のみスキップ（他行は取り込む） |
| `inquiry_qa` | `company_id`, `product_id`, `section_id`, 照会本文 | 同上。回答本文・審査結果は任意（未回答の照会も取り込み可） |

### (c) S-3 二重ゲートの検知時動作 ← blocker #3

> ⚠️ **fail-closed 設計（SoilInvestigationNeighbouringCaseReferenceAgent の fail-open 事故の再発防止）**: ゲートの実行経路を
> **`execute()` が書いた state キーの有無で分岐させない**。`merge_output()` が **`gate_kind`** を
> 明示的に宣言し、S-3 フックはその値で経路を判定する。**`gate_kind` が未宣言なら raise**（設計違反）。
> **`invoke_provenance` 経路では必須フィールド（`claim_sources` / `retrieved_context` / `draft_output` /
> `evidence_map` / `anticipated_inquiries`）が欠落していたら素通りさせず `SecurityViolationError` を送出する**。

### 🔴 S-3 Provenance 検証は `CTDWorkflowGraphNode`（GraphNode 境界）に置く — PostProcess ではない

> **framework 1.0.0 の実体で確認した構造**:
> `GraphNode.execute()` は `merge_output(state, sub_result)` の戻り値を返し、`BaseNode.__call__()` が
> それを `_security_gate_output(result)` に渡す。`GraphNode._security_gate_output()` は
> **`_extra_security_gate_output(result)` に委譲し、フックは dict を返せる**。

**なぜ PostProcess ではダメか（本設計の初版の誤り）**:

Provenance 照合の**正本**は `claim_sources`（Stage 1 が付番した claim + **治験データの実値**）と
`retrieved_context`（Stage 2 が引いた過去照会）である。これらは inner graph が生成する。
PostProcess で照合しようとすると、**正本を `merge_output()` で outer state に押し出す**しかない。
すると:

- `claim_sources[].value` には **CSR サマリー由来の治験データ実値**が入る。
- outer state は **checkpointer が super-step ごとに永続化する** → **機微データが checkpoint DB に平文で残る**。
- これは本設計の不変条件「**申請文書本文・照会本文の実体をどのノードの戻り値にも載せない**」
  （§申請文書・照会本文の State 非格納）と**正面から矛盾する**。

**正しい設計（fail-closed かつ checkpoint を汚さない）**:

`CTDWorkflowGraphNode._extra_security_gate_output(result)` で検証し、**検証後に正本キーを落として返す**。

```python
# src/nodes/main_node.py — CTDWorkflowGraphNode（GraphNode 継承）
_PROVENANCE_KEYS = ("claim_sources", "retrieved_context")   # 検証にだけ使い、outer state には残さない

def merge_output(self, state, sub_result) -> dict:
    mode = sub_result.get("mode")           # inner が確定した mode（pre_process 由来）
    if mode == "ingest":
        return {"ingest_summary": ..., "status": ...}
    if mode == "feedback":
        return {"feedback_result": ..., "status": ...}
    # invoke: 正本（claim_sources / retrieved_context）を S-3 検証のために一時的に載せる
    return {
        "draft_output": ..., "anticipated_inquiries": ..., "evidence_map": ...,
        "missing_data_flags": ..., "status": ...,
        "gate_kind": "invoke_provenance",    # ← 経路の明示宣言（キーの有無で分岐しない）
        "claim_sources": ..., "retrieved_context": ...,
    }

def _extra_security_gate_output(self, result: dict) -> dict:
    gate_kind = result.get("gate_kind")
    if gate_kind is None:                    # 経路が宣言されていない = 設計違反
        raise SecurityViolationError("S-3: gate_kind not declared by merge_output()")
    if gate_kind != "invoke_provenance":     # ingest / feedback は provenance 検証の対象外
        return result
    # --- fail-closed: 正本が無ければ素通りさせない ---
    for k in (*_PROVENANCE_KEYS, "draft_output", "evidence_map", "anticipated_inquiries"):
        if k not in result:
            raise SecurityViolationError(f"S-3: required field missing on invoke path: {k}")
    # --- 引用 ID の実在照合（捏造の構造遮断）---
    known_sources = {c["source_id"] for c in result["claim_sources"]}
    known_inquiries = {q["inquiry_id"] for q in result["retrieved_context"].get("past_inquiries", [])}
    cited = set(result["draft_output"].get("cited_source_ids", []))
    cited |= {e["source_id"] for e in result["evidence_map"]}
    unknown_src = cited - known_sources
    unknown_inq = {i for a in result["anticipated_inquiries"]
                     for i in a.get("basis_inquiry_ids", [])} - known_inquiries
    if unknown_src or unknown_inq:
        emit_trace_event("provenance_violation",
                         {"unknown_source_ids": sorted(unknown_src),
                          "unknown_inquiry_ids": sorted(unknown_inq)}, result)
        raise SecurityViolationError("S-3: LLM cited source/inquiry IDs that do not exist")
    emit_trace_event("provenance_verified", {"cited": len(cited)}, result)
    # --- 検証が済んだら正本を落とす（checkpoint 汚染の防止）---
    return {k: v for k, v in result.items() if k not in _PROVENANCE_KEYS}
```

**帰結（実装契約）**:

| | 置き場所 | 理由 |
|---|---|---|
| **Provenance ID 検証** | **`CTDWorkflowGraphNode._extra_security_gate_output()`**（GraphNode 境界） | 照合の正本が見える唯一の場所であり、**検証後に正本を落として返せる**。同系テンプレートで確立したパターン（SoilInvestigationNeighbouringCaseReferenceAgent） |
| **効能断定ガード / PHI 再スキャン** | `CTDPostProcessNode._extra_security_gate_output()` | 検証対象は `draft_output.body`（本文テキスト）のみで、正本を必要としない。post_process で足りる |

- **`claim_sources` / `retrieved_context` は outer State に宣言しない**（§State Definition から除外）。
  inner の `CTDWorkflowState` にのみ存在し、GraphNode の S-3 で消費されて消える。
- **経路判定は `gate_kind`（`merge_output()` が明示的に宣言する値）で行う**。
  `execute()` が書いたキーの有無で分岐すると、キー欠落時に素通りする fail-open になる
  （SoilInvestigationNeighbouringCaseReferenceAgent の実事故。セキュリティゲートの判定を `execute()` が書いた state に依存させると、
  そのキーを書かない経路でゲートが黙って素通しになる）。

---

**① Provenance ID 機械検証**（**`CTDWorkflowGraphNode._extra_security_gate_output()`** — 上記参照）:

| 検証項目 | 動作 |
|---|---|
| `draft_output.cited_source_ids` の各 ID が `claim_sources` に実在するか | 実在しない ID が 1 件でもあれば **`SecurityViolationError` を raise**（LLM の source 捏造を構造遮断） |
| `evidence_map` の各 `source_id` が `claim_sources` に実在するか | 同上 |
| `anticipated_inquiries[].basis_inquiry_ids` の各 ID が `retrieved_context.past_inquiries` に実在するか | 同上 |
| `/invoke` 経路で `claim_sources` / `retrieved_context` / `draft_output` のいずれかが欠落 | **`SecurityViolationError` を raise**（fail-closed。キー欠落による素通りを禁止） |

- ID 構成要素は `[0-9A-Za-z_-]` に**サニタイズ**する（非 ASCII の ID が照合に失敗する実測事例あり）。
- **本検証は「実在しない ID の捏造」を遮断するが、「数値解釈の正しさ」は保証しない**（§Stage 3/4 の制約参照）。

**② 効能断定ガード**（同フック内・lexicon ベース）:

| 検知対象 | 動作 |
|---|---|
| ソースに紐づかない有効性・安全性の**断定表現**（`config/assertion_lexicon.yaml` の禁止語彙。NFKC 正規化 + casefold → **文単位分割**して照合） | 該当文を**非断定形に置換**（例: 「有効である」→「〜のデータが得られた（§2.7.3.x 参照）」）。置換後も断定が残る場合、または**全文が該当**する場合は `AgentStatus.ERROR.value` |
| lexicon の schema_owner | **顧客の薬事部門**（版管理付き。`config/assertion_lexicon.yaml` に `schema_version` / `owner` を明記） |

- 「プロンプトに追記するだけ」の対策は不可（SubcontractorQuotationDisputeHistory でレビューが Critical と判定した前例）。
  **`_extra_security_gate_output()` 内で実際に文字列を検査・置換する**。

### 🔴 S-2 で症例レベル記述を検知したときの動作 — 経路で非対称にする（確定・実装契約）

**`mask` か `reject` かは経路で決まる。両論併記のまま実装に入らない。**

| 経路 | 検知時の動作 | 理由 |
|---|---|---|
| **`/ingest`**（KB へ永続化する） | **fail-closed reject**（`SecurityViolationError`）。該当行を `validation_errors` に記録し、その行は取り込まない（他の行は取り込む） | KB に PHI が入ると**永続的な汚染**になり、以後すべての生成・検索がその汚染を引き継ぐ。checkpoint とは違い「消して終わり」にできない |
| **`/feedback`**（KB へ永続化する） | **同上（fail-closed reject）** | 同じ理由（DB 書込経路） |
| **`/invoke`**（生成の一時入力） | **mask して続行**（該当箇所を除去し `degradation_reason="phi_masked_in_input"` を立てて `status="success"`） | 顧客の CSR サマリー・統計解析サマリーに症例への言及が混ざるのは実務上ありがち。ここを毎回 reject にすると**実務で使えず、ingest も進まないので moat が育たない**。invoke の入力は一時的であり、mask で足りる |

- `/invoke` の mask 経路でも、**mask した事実は必ず出力に明示する**（`post_process` が出力冒頭に
  「入力から症例レベル記述を N 件除去しました」と表示）。黙って消さない。
- S-4 イベント `phi_masked_in_input`（**件数のみ**・除去内容は記録しない）を emit。
- 実装は `CTDPreProcessNode._extra_security_gate_input()` 内で **`validated_input` の `mode` を見て
  分岐**する（`execute()` が書いた state キーの有無で分岐しない — fail-open の原因になる）。

**③ PHI 再スキャン**（同フック内）:
- LLM 出力に症例レベル記述（患者イニシャル・症例番号・生年月日等）が混入していないかを再スキャンする。
  検出時は該当箇所をマスクし、S-4 イベント `phi_masked_in_output`（**件数のみ**・内容は記録しない）を emit。
- SDK の `pii_detector` は日本語氏名を検知しない・日本語直後の `\b` が失敗するギャップがある。
  **`detect_pii` 単体に依存せず、補完サニタイザ（`src/security.py`）を併用する**（CorporateGroupTripPlannerAgent 実測）。

**セキュリティ受入基準（`docs/03_test_spec.md` の PB 項目に反映する）**:

| # | 受入基準 |
|---|---|
| (i) | PHI 混入フィクスチャ（PDF/Word/Excel 各 1 件以上）を **`/ingest` に投入 → S-2 が fail-closed reject**（該当行は KB に入らない・他行は取り込まれる）。同じフィクスチャを **`/invoke` に投入 → mask されて `status="success"` + `degradation_reason="phi_masked_in_input"`** となり、出力冒頭に除去件数が明示されること（§S-2 で症例レベル記述を検知したときの動作） |
| (ii) | LLM が実在しない `source_id` を引用した出力 → S-3 が `SecurityViolationError` を raise すること |
| (iii) | `/invoke` 経路で `claim_sources` を欠落させた state → S-3 が素通りせず raise すること（fail-closed 検証） |
| (iv) | 断定表現を含む LLM 出力 → 非断定形に置換される／全文該当時は `status="error"` になること |
| (v) | `invoke` → `interrupt` → `resume` → 完了後の**全 checkpoint 断面**に、申請文書本文・照会本文・PHI・credential が含まれないこと |
| (vi) | 他テナント（**別 `company_id`**）の `inquiry_qa` / `submission_sections` が Stage 2 検索に混入しないこと（fail-closed 検証）。**同一 `company_id` の別 `product_id` は引ける**ことも同時に検証する（§Stage 2 の検索スコープ — 品目跨ぎ再利用が moat の成立条件のため、これを塞ぐ実装は defect） |
| (vii) | **品目跨ぎ汚染の遮断**（§品目跨ぎで渡してよいもの）: ①Stage 2 が引いた他品目レコードに `body`（申請文書本文）が含まれないこと（`is_same_product == False` なら `body` キーが存在しない）②他品目の `product_context` が Stage 3 の生成材料に渡っていないこと ③**他品目の薬剤名・用量・試験 ID を含む KB を仕込んだ状態で `/invoke` し、生成ドラフトにそれらの識別子が 1 つも現れないこと**（literal 混入の end-to-end 検証。Provenance ID 検証では検出できない欠陥のため必須） |
| (viii) | `validated_input` が欠落した state で `CTDWorkflowGraphNode.extract_input()` を呼ぶと `SecurityViolationError` が送出されること（S-2 未通過の生入力が inner graph へ流入する fail-open の遮断） |

### (d) CTD 2.5 / 2.7 サブセクションスケルトンの config 構造 ← blocker #4

**ICH M4E(R2) の一次ソースで確認した正式構造**（2026-07-14 実確認・
[ICH M4E(R2) Guideline](https://database.ich.org/sites/default/files/M4E_R2__Guideline.pdf)）:

| Module 2.5 Clinical Overview | Module 2.7 Clinical Summary |
|---|---|
| 2.5.1 Product Development Rationale | 2.7.1 Summary of Biopharmaceutic Studies and Associated Analytical Methods（2.7.1.1〜2.7.1.4） |
| 2.5.2 Overview of Biopharmaceutics | 2.7.2 Summary of Clinical Pharmacology Studies（2.7.2.1〜2.7.2.5） |
| 2.5.3 Overview of Clinical Pharmacology | 2.7.3 Summary of Clinical Efficacy（2.7.3.1〜2.7.3.6） |
| 2.5.4 Overview of Efficacy | 2.7.4 Summary of Clinical Safety（2.7.4.1〜2.7.4.7） |
| 2.5.5 Overview of Safety | 2.7.5 Literature References |
| 2.5.6 Benefits and Risks Conclusions | 2.7.6 Synopses of Individual Studies |
| 2.5.7 Literature References | |

> ⚠️ **Proposal 本文の「2.5.1〜2.5.6 / 2.7.1〜2.7.4」は概数表記**であり、一次ソース確認の結果、
> 2.5 は 2.5.7 まで、2.7 は 2.7.6 まで存在する。本 Design が生成対象の正本となる。

**本 AG が生成対象とするサブセクション（`config/section_specs/`）**:

| 対象 | セクション | 理由 |
|---|---|---|
| ✅ 生成する | 2.5.1〜2.5.5、2.7.1〜2.7.4（サブ小節を含む。ただし 2.7.4.2.2 を除く） | 治験データサマリーから claim を構造化してドラフト可能。照会が集中する領域 |
| ❌ 生成しない（スコープ外・明示） | **2.5.6 Benefits and Risks Conclusions** | ベネフィット・リスクの最終結論は医学専門家・薬事責任者の判断（decision §スコープで宣言済み） |
| ❌ 生成しない（スコープ外・明示） | **2.7.4.2.2 Narratives**（症例ナラティブ） | 症例レベルナラティブは本テンプレートの対象外。PHI 核を本 AG に持ち込まない設計判断。`section_spec` に本小節を含めず、S-2 で症例レベル記述を検知した場合は `/ingest` `/feedback` では reject、`/invoke` では mask する |
| ❌ 生成しない（機械生成が不適切） | 2.5.7 / 2.7.5 Literature References、2.7.6 Synopses of Individual Studies | 文献リスト・個別試験概要は原資料からの機械転記であり LLM 生成の対象ではない（誤引用リスクのみが増える） |

**`section_spec` の config 構造**（`config/section_specs/{section_id}.yaml`）:

```yaml
section_id: "2.7.3"
title_en: "Summary of Clinical Efficacy"
title_ja: "臨床的有効性の概要"
parent_module: "2.7"
# ICH M4E が要求する記載要素（欠落データフラグの判定基準）
required_elements:
  - id: "study_populations"
    label_ja: "解析対象集団の定義"
    source_types: ["statistical_summary", "csr_summary"]
  - id: "efficacy_comparison_across_studies"
    label_ja: "全試験の有効性結果の比較"
    source_types: ["statistical_summary"]
  # ...
# 当該セクションで照会が頻出する観点（Stage 3 のプロンプト制約・Stage 4 の想定照会生成に使う）
frequent_inquiry_categories: ["STAT", "EFFICACY_BASIS", "CONSISTENCY"]
# 本文の章立てスケルトン（ICH M4E の小節構成）
skeleton:
  - "2.7.3.1 Background and Overview of Clinical Efficacy"
  - "2.7.3.2 Summary of Results of Individual Studies"
  - "2.7.3.3 Comparison and Analyses of Results Across Studies"
  # ...
```

- `required_elements` の各要素に対応する claim が `claim_sources` に無い場合、Stage 4 が
  `missing_data_flags` を立てる（決定論判定）。
- スケルトンは **config で定義**し、コード変更なしに ICH ガイドライン改訂へ追随できる。

---

## セキュリティ設計方針（S-1〜S-5）

| 層 | 設計 |
|---|---|
| **S-1** | 全 `FunctionNode` サブクラスで `required_trust_level` を**クラス body に明示宣言**（暗黙 `ANONYMOUS` 継承は不可）。DB 書込副作用を持つ `/ingest` `/feedback` 系ノード・`CTDWorkflowGraphNode`（GraphNode）は **`VERIFIED_EXTERNAL`**。読取系も本 AG は機微文書を扱うため `VERIFIED_EXTERNAL` を既定とする |
| **S-2** | `CTDPreProcessNode._extra_security_gate_input()`: ①症例レベル記述（患者イニシャル・症例番号・生年月日）の検知 → **`/ingest` `/feedback` は fail-closed reject・`/invoke` は mask**（§S-2 で症例レベル記述を検知したときの動作）②`mode` / `section_id` の enum 検証 ③入力サイズ上限。`FunctionNode` の `@final` メソッドは override しない。**inner の `parse_documents` / `feedback` 系ノードにも同フックを実装**（取込データの PHI 検出）。⚠️ **resume 経路は S-2 を通らない** → `hitl_refine` の応答解釈先頭で PII 除去を必ず実施（§HITL Design） |
| **S-3** | **2 箇所に分割配置する**（§S-3 Provenance 検証は GraphNode 境界に置く）。①**`CTDWorkflowGraphNode._extra_security_gate_output()`**（GraphNode 境界）= **Provenance ID 機械検証**。照合の正本が見える唯一の場所であり、検証後に正本を落として返せる（outer state = checkpoint を汚さない）。`gate_kind` で経路を明示宣言し、必須フィールド欠落は raise（**fail-closed**）②`CTDPostProcessNode._extra_security_gate_output()` = **効能断定ガード・PHI 再スキャン**（検証対象は `draft_output.body` のみで正本を必要としない） |
| **S-4** | 全ノードの `execute()` 内で `emit_trace_event()` を最低 1 回（副作用の有無を問わない）。イベント例: `input_validated` / `tenant_resolved` / `documents_parsed` / `inquiry_classified` / `records_written` / `claims_structured` / `kb_retrieved` / `draft_generated`（LLM 不在時は `draft_generated_fallback`）/ `inquiries_anticipated` / `hitl_refine_round` / `hitl_response_sanitized`（除去件数のみ）/ `phi_masked_in_output`（件数のみ）/ `provenance_verified` / `feedback_recorded`。**`node_start` / `node_complete` / `node_error` は `__call__()` が自動発行するため execute() 内で重複発行しない** |
| **S-5** | credential を `src/` に置かない。secrets は **`ctx.secrets.require()` のみ**（`os.environ` 禁止。例外は `src/api/server.py` の `INVOKE_AUTH_TOKEN` — エントリポイント例外）。`pyproject.toml` の全依存を `==` 固定ピン |

---

## HITL Design（D6）

- **パターン**: D6 `interrupt()` のみ。inner の `hitl_refine` ノードの `execute()` 内で
  `interrupt({"draft": …, "anticipated_inquiries": …, "prompt": …})` を呼ぶ。`hitl_draft` に提示済み
  ドラフトを格納し resume 時の LLM 再実行を防ぐ（idempotency guard）。
- **🔴 `hitl_allowed` ガード必須**: すべての `interrupt()` 呼び出しは `state.get("hitl_allowed", True)` で
  ガードする。`hitl_allowed=False`（`GraphNode` 経由の自動パイプライン呼び出し）のときは interrupt を
  スキップし、人間レビューなしで続行する。**ガードのない `interrupt()` は本番デッドロックを起こす
  blocking defect**（ガードのない `interrupt()` は自動パイプラインをデッドロックさせる）。
- **resume 応答の S-2 相当ガード**: resume 経路は `pre_process` の S-2 を再実行しない ＝ `hitl_feedback` が
  入力ゲート未通過のまま checkpoint に永続化される穴がある。薬事担当者の修正指示に症例記述・患者識別子が
  混入する蓋然性があるため、`hitl_refine` の応答解釈処理の**先頭**で PII 除去を必ず実施し、
  **除去後テキストのみ**を action 正規化・State 反映・再生成に使う。除去発生時は S-4 イベント
  `hitl_response_sanitized`（**除去件数のみ**・除去内容は記録しない）を emit。
- **`HitlStatus` の 3 値ハンドリング**: `APPROVED` / `CORRECTED` / `REJECTED` のすべてを処理する
  （`APPROVED` のみ実装して `CORRECTED` を無視すると修正分岐が黙ってスキップされる）。
- **config**: `memory_enabled: true` 必須。`hitl.enabled: true`、`hitl.max_hitl: 12`。

### 🔴 `max_hitl` は Cat 2 では強制されない（実 wheel 検証済み・実装契約）

**`agenticstar-agentcore==1.0.0` の実体を読んで確認した事実**（推測ではない）:

| | Cat 3（`AutonomousBaseGraph`） | **Cat 2（`AgentBaseGraph`）← 本 AG** |
|---|---|---|
| `max_hitl` の扱い | `ToolActNode` が `hitl_count >= max_hitl` で**ツール実行を止める**（`framework/nodes/defaults/tool_act_node.py:117` — circuit breaker として実装済み） | **正の整数かバリデーションするだけ**（`framework/graph/agent_base_graph.py:66-74`）。**どのノードにも渡されず、実行時に何も止めない** |

- したがって本 AG の `max_hitl: 12` は **マニフェスト宣言値**であり、**回数制限として機能しない**。
  `hitl.enabled: true` のときの必須キーとして要求されるため宣言する
  （PB-7 stub と AgentRegistry が読む）。
- **`hitl_count` はテンプレートから書き込まない**（framework 専用フィールド）。
  Cat 2 で「テンプレ側で `hitl_count` をインクリメントして上限を自作せよ」という指摘は
  **framework の契約違反**であり採らない（同系テンプレートでレビューが誤指摘した前例あり）。
- **実効的な暴走防止は決定論的な境界で担保する**（回数ではない）:
  `hitl_refine` の resume 応答から正規化した `refine_action` が **`"refine"` のときだけ** LLM 再生成へ進み、
  `"confirm"` なら確定して END へ抜ける。自由文フィードバックの LLM 解釈結果で勝手に再生成ループへ
  落とさない（`refine_action` は route が読む唯一の欄・決定論分岐）。壁打ち refine 自体は
  **ユースケースの価値そのもの＝正常系**であり、回数で縛る設計にはしない。
- **伝播**: `CTDWorkflowGraphNode.propagate_hitl = True`。
- **PB-7**（HITL interrupt propagation）を `docs/03_test_spec.md` に含める（HITL 有効のため必須）。
  加えて **resume → success 到達テスト**（subgraph キャッシュ検証 — suspend だけの PB では
  無限ループを捕捉できない）を必須とする。

### 🔴 PB-7 を「偽の緑」にしないための 3 点（実装契約・必須）

PB-7 stub（`tests/proof_of_boundary/test_pb7_hitl_interrupt_propagation.py`）の条件付き SKIP は、
**HITL 有効テンプレでも黙って SKIP される罠が 2 つ**ある。CI は SKIP を失敗と扱わないため、
気づかないまま緑になる。以下を適用済み（実機で `_hitl_enabled() → True` を確認）。

| # | 罠 | 対策 |
|---|---|---|
| 1 | stub は `agent.yaml` の**トップレベル** `hitl.enabled` を読む。`agent.config.hitl` にネストすると **`False` と誤判定 → PB-7 が永久 SKIP** | `config/agent.yaml` の**トップレベルに `memory_enabled` / `hitl` を宣言**する（`agent.config` 配下はランタイム config のミラーとして併記）。HITL 有効時の必須宣言どおり |
| 2 | stub の `import yaml` は `except Exception: return False` で握り潰される。**PyYAML が入っていないと HITL 有効でも SKIP** | `pyproject.toml` の dev extras に **`pyyaml==6.0.1` を exact pin**（`gate-dep-pinning` があるので `==` 必須） |
| 3 | 上記 2 つを踏んでも CI は緑のまま（SKIP は失敗ではない） | stub の `_hitl_enabled()` を **fail-closed 化**する（manifest が読めない＝ error であって skip の理由ではない）。本テンプレは HITL 有効なので、読めないなら落ちるべき |

---

## Framework Utilization

### Shared Components Used
- [x] `InvocationContext`（`from_state(state)` 経由。correlation_id / session_id / secrets）
- [x] `SecurityViolationError`（S-2 / S-3 の fail-closed）
- [x] S-2: `_extra_security_gate_input()` — 症例レベル記述検知・enum 検証・サイズ上限
- [x] S-3: `_extra_security_gate_output()` — Provenance ID 検証・効能断定ガード・PHI 再スキャン
- [x] S-4: `emit_trace_event()`（`shared.utils.audit_logger`）— 全ノードの `execute()` 内で最低 1 回

### Composition Pattern

- **Pattern**: `GraphNode`（in-process subgraph）
- **Composition target**: `CTDWorkflowGraph`（`src/graph/domain_workflow_graph.py`）
- **Error propagation strategy**: `propagate`（inner のエラーは `SubgraphError` として outer へ再送出）

### LLM 注入シーム

- テンプレートは**具体的な LLM モデル ID・クライアント実装を書かない**。`graph.py` が
  `config.get("llm")` で LLM インスタンスを取り出してノードへ渡す（注入シーム）。
- LLM 不在時（`None`）は各ノードが **deterministic fallback** に落ち、`degradation_reason` を立てて
  `status="success"` を返す（同系テンプレート共通の規約）。

## Import Isolation Confirmation
- [x] Level 0（`agenticstar` SDK）を import しない
- [x] import 先は `framework/` と `shared/` のみ。他テンプレートを import しない

## Design Decision Record

| Decision | Option A | Option B | Chosen | Rationale |
|----------|----------|----------|--------|-----------|
| L1 base type | `AgentBaseGraph` | `AutonomousBaseGraph` | **A** | 固定パイプライン（mode ごとに実行するステージが決定論的に決まる）。think→act→observe の自律ループを持たない |
| Composition pattern | Standalone（main を `FunctionNode` 1 個） | `GraphNode` + inner subgraph | **B** | 3 モード分岐 + 4 ステージ + HITL の多段トポロジは inner graph が適切（Cat 2 標準） |
| 生成粒度 | セクション一括（2.5 全体・2.7 全体） | **サブセクション単位**（2.5.4 / 2.7.3.3 等） | **B** | 数十ページの一括生成は HITL レビュー粒度と合わず、provenance 検証も破綻する。サブセクション単位なら人間レビューの単位と一致する（設計判定時のレビュー Med 指摘の反映） |
| 照会の再利用軸 | 品目横断で照会本文をそのまま再利用 | **カテゴリ × セクション種別 × 論点構造**の 3 軸のみ再利用し、品目固有の医学的内容は分離格納 | **B** | 品目固有の医学的内容を跨いで再利用すると誤った類推を生む。再利用可能なのは論点の構造だけ（設計判定時のレビュー High 指摘の反映） |
| Provenance 検証 | プロンプト制約のみ | **`_extra_security_gate_output()` で ID 実在を機械照合し raise** | **B** | プロンプト制約だけでは LLM の source 捏造を防げない。同系テンプレートで確立したパターン |
| S-3 ゲートの経路判定 | `execute()` が書いた state キーの有無で分岐 | **`merge_output()` が `gate_kind` を明示宣言し、S-3 フックはその値で判定。未宣言・必須フィールド欠落は raise** | **B** | キーの有無で分岐すると欠落時に素通りする fail-open になる（SoilInvestigationNeighbouringCaseReferenceAgent の実事故） |
| **S-3 Provenance 検証の置き場所** | `CTDPostProcessNode`（PostProcess） | **`CTDWorkflowGraphNode`（GraphNode 境界）の `_extra_security_gate_output()`** | **B** | **初版は A で書いていたが自己レビューで欠陥と判明**。照合の正本（`claim_sources` は治験データの実値を含む）を PostProcess に届けるには `merge_output()` で outer state に押し出すしかなく、**checkpoint DB に機微データが平文で永続化される**（本設計の不変条件「本文実体をノード戻り値に載せない」と矛盾）。GraphNode 境界なら**検証後に正本キーを落として返せる**ため、検証を成立させつつ checkpoint を汚さない。同系テンプレートで確立したパターン（SoilInvestigationNeighbouringCaseReferenceAgent — S-3 の Provenance 検証は GraphNode 境界に置く） |
| `extract_input()` の欠落時挙動 | `state["user_input"]`（生入力）へフォールバック（同系テンプレートの慣行） | **`validated_input` 欠落は `SecurityViolationError`（fail-closed）** | **B** | フォールバックは S-2 未通過の生入力を inner graph に流す fail-open。本 AG は PHI・機微申請文書を扱うため許容できない（設計フェーズのレビュー High 指摘の反映） |
| テナント境界と再利用軸 | 境界 = `company_id` × `product_id`（品目でも壁を立てる） | **境界 = `company_id` のみ。`product_id` は再利用軸（品目跨ぎで論点構造を引く）** | **B** | 品目で壁を立てると**新規品目では KB が常に空**になり、照会予防（本 AG の moat）が原理的に成立しない。壁は他社との間にだけ立て、社内の論点構造は品目を跨いで学ぶ |
| 品目跨ぎで渡す粒度 | 他品目の申請文書本文も「文体参考」として LLM に渡す | **他品目からは論点構造・セクション骨格メタのみ。本文・医学的内容は渡さない（取得段階で fail-closed）** | **B** | 他品目の本文を渡すと LLM が旧品目の一文（薬剤名・用量・試験 ID 込み）を引き写し、**現行品目の `source_id` を貼った「別の薬剤名のドラフト」**を生成する。Provenance ID 検証は ID の実在しか見ないためこの誤りを検出できない — 規制申請文書として致命的（設計フェーズのレビュー 2 巡目 High 指摘の反映） |
| PDF スキャン文書 | OCR して取り込む | **エラー行として拒否**（明示スコープ外） | **B** | OCR 誤読が数値捏造の温床になる。規制申請文書で許容できない |
| HITL 上限 | `max_hitl` を小さくして強く有界化 | **`max_hitl: 12`（マニフェスト宣言値。Cat 2 では framework が強制しない）+ 暴走防止は `refine_action` の決定論境界で担保** | **B** | 実 wheel 検証の結果、**`max_hitl` の circuit breaker は Cat 3（`ToolActNode`）にしか実装されておらず、Cat 2（`AgentBaseGraph`）はバリデーションするだけで何も止めない**。値を小さくしても実効性がない。サブセクション単位の壁打ち refine は本 AG の価値の中核＝正常系であり、そもそも回数で縛るのはユースケース殺し。実効的な暴走防止は「`refine_action == "refine"` のときだけ LLM 再生成へ進む」決定論分岐で担保する |
| HITL 伝播 | inner 内で閉じる（`propagate_hitl=False`） | **outer へ伝播（`True`）** | **B** | 薬事担当者は外側 API 呼び出し元。interrupt を API 応答に表面化しないと壁打ちにならない |
| 2.5.6 Benefits/Risks | 生成する | **生成しない（スコープ外）** | **B** | ベネフィット・リスクの最終結論は医学専門家・薬事責任者の判断（decision で宣言済み） |
| 2.7.4.2.2 Narratives | 生成する | **生成しない（スコープ外）** | **B** | 症例レベルナラティブは本テンプレートの対象外。PHI 核を本 AG に持ち込まない |

## テスト方針（`docs/03_test_spec.md` で詳細化）

- TC-01〜08（framework 適合テスト）+ PB-1〜7（境界証明テスト。**HITL 有効のため PB-7 必須**）
- 本テンプレ固有:
  1. §(c) セキュリティ受入基準 (i)〜(vi) の全項目
  2. `resume` → `success` 到達テスト（subgraph キャッシュ検証）
  3. `mode` 3 分岐の route 検証（inner state 注釈）
  4. `extract_input()` → inner の envelope round-trip（キー集合の drift 検出）
  5. `/invoke` → `/feedback` → 次回 `/invoke` Stage 2 で hit する E2E（feedback ループの配線固定）
  6. LLM 不在時の deterministic fallback（`degradation_reason` 付き `status="success"`）
  7. 照会 KB ゼロ時のグレースフルデグレード
