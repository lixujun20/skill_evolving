# Maintenance API Reference

本文档是当前实现的查询手册。它按对象、状态机、role API、HTTP API、实验 JSON schema 组织。

## 1. Dataclass Schema

所有通用对象定义在：

- `academic/skill_repository/types.py`

### 1.1 `SkillInterface`

```python
@dataclass
class SkillInterface:
    summary: str = ""
    usage: str = ""
    input_contract: Dict[str, Any] = field(default_factory=dict)
    output_contract: Dict[str, Any] = field(default_factory=dict)
    invocation_contract: Dict[str, Any] = field(default_factory=dict)
    compatibility_notes: str = ""
```

| 字段 | 类型 | 含义 | 写入方 | 读取方 |
| --- | --- | --- | --- | --- |
| `summary` | `str` | 一句话接口摘要 | extractor/refiner | UI/prompt/docs |
| `usage` | `str` | 何时使用、如何使用 | extractor/refiner | executor/refiner |
| `input_contract` | `dict` | 使用该 skill 需要的上下文和前提 | extractor/refiner | bundle builder/tester |
| `output_contract` | `dict` | 使用后应保证的行为或结果 | extractor/refiner | bundle builder/tester |
| `invocation_contract` | `dict` | prompt-only、tool-backed、顺序约束等调用方式 | extractor/refiner | executor adapter |
| `compatibility_notes` | `str` | major/minor/stale 判断需要的兼容说明 | refiner/stale resolver | store/refiner |

### 1.2 `SkillBundleCase`

```python
@dataclass
class SkillBundleCase:
    case_id: str
    source: str
    prompt: str
    expected: Dict[str, Any]
    context: Dict[str, Any]
    tags: List[str]
    polarity: str = "positive"
    contrast_protocol: Dict[str, Any] = {"with_skill": True, "without_skill": True}
```

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `case_id` | `str` | bundle 内稳定 id |
| `source` | `str` | 来源，例如 `distilled_success`、`integration_failure`、`manual` |
| `prompt` | `str` | 轻量任务说明 |
| `expected` | `dict` | oracle/期望行为 |
| `context` | `dict` | 执行上下文，最重要的是 `task_fragment` |
| `tags` | `list[str]` | 分类标签 |
| `polarity` | `str` | `positive`、`negative`、`integration` 等 |
| `contrast_protocol` | `dict` | 是否跑 with/without 对照 |

推荐 `context.task_fragment` 格式：

```json
{
  "task_fragment": {
    "task_id": "multi_turn_base_143",
    "question": [[{"role": "user", "content": "..."}]],
    "expected": [["tool_name(arg='value')"]],
    "input_artifacts": {},
    "metadata": {}
  }
}
```

### 1.3 `SkillBundle`

```python
@dataclass
class SkillBundle:
    bundle_id: str = ""
    bundle_version: int = 1
    positive_cases: List[SkillBundleCase] = field(default_factory=list)
    negative_cases: List[SkillBundleCase] = field(default_factory=list)
    integration_cases: List[SkillBundleCase] = field(default_factory=list)
    fixtures: Dict[str, Any] = field(default_factory=dict)
    contrast_protocol: Dict[str, Any] = {"with_skill": True, "without_skill": True}
    maintenance_notes: str = ""
```

约束：

- bundle 是长期测试资产。
- bundle 不存本次测试结果。
- `bundle_version` 可以独立于 `skill.version` 增长。
- minor skill update 不应删除旧 tests，只能追加。
- major skill update 可以迁移 tests，但必须记录 lineage 和 migration reason。

### 1.4 `SkillTestCaseRun`

```python
@dataclass
class SkillTestCaseRun:
    case_id: str
    variant: str
    passed: bool
    accuracy: Optional[float]
    validity: Optional[bool]
    tokens: Optional[int]
    steps: Optional[int]
    failure_summary: str
    trace_ref: str
    trace: Dict[str, Any]
    input_payload: Dict[str, Any]
    expected_behavior: Dict[str, Any]
    actual_output: Dict[str, Any]
    tool_calls: List[Dict[str, Any]]
    trace_summary: Dict[str, Any]
    skill_snapshot: Dict[str, Any]
    bundle_case_snapshot: Dict[str, Any]
    metadata: Dict[str, Any]
```

| 字段 | 含义 |
| --- | --- |
| `variant` | `without_skill`、`with_skill`、`bundle_only` |
| `input_payload` | 真实 executor/test 输入，包括 task、variant、top_k、skill injection mode |
| `expected_behavior` | bundle expected + task expected + contrast protocol |
| `actual_output` | executor 输出摘要、metrics、error、trace summary |
| `tool_calls` | 该 variant 实际发出的 tool calls |
| `trace_summary` | 轻量 trace 摘要，用于 UI 不打开 raw trace 也能审查 |
| `skill_snapshot` | with_skill 时实际可见的 skill snapshot；without_skill 为空 |
| `bundle_case_snapshot` | 本次使用的 bundle case 快照 |

### 1.5 `SkillTestResult`

```python
@dataclass
class SkillTestResult:
    result_id: str
    skill_name: str
    skill_version: int
    bundle_id: str
    bundle_version: int
    dependency_versions: Dict[str, int]
    run_label: str
    unit_case_runs: List[SkillTestCaseRun]
    aggregate: Dict[str, Any]
    counterfactual: Dict[str, Any]
    integration_failures: List[Dict[str, Any]]
    created_at: str
```

关键 aggregate 字段：

- `n_cases`
- `n_comparable_cases`
- `n_improved`
- `n_regressed`
- `pass_all_tests`
- `unit_utility_report.delta_accuracy`
- `unit_utility_report.delta_tokens`
- `unit_utility_report.delta_steps`

### 1.6 `SkillArtifact`

```python
@dataclass
class SkillArtifact:
    name: str
    kind: str
    description: str
    body: str
    metadata: Dict[str, Any]
    tags: List[str]
    version: int
    usage_count: int
    success_count: int
    interface: SkillInterface
    bundle: SkillBundle
    evidence: SkillEvidence
    status: str
    lineage: SkillLineage
    dependency_pins: List[DependencyPin]
    dependencies: List[str]
    history: List[Dict[str, Any]]
    stale: bool
```

重要方法：

- `injection_type()`
- `is_disabled()`
- `retrieval_enabled()`
- `version_kind()`
- `version_id()`
- `dependency_version_map()`
- `retrieval_text()`
- `prompt_block()`
- `as_dict()`

`tags` 是受控检索标签，只允许三类前缀：

- `domain:*`，例如 `domain:TicketAPI`
- `tool:*`，例如 `tool:create_ticket`
- `intent:*`，例如 `intent:reuse_id`

旧字段 `metadata.domains`、`metadata.allowed_tools`、`metadata.intent_keywords` 会在进入 `ArtifactStore` 时自动派生成等价 tags；非法/free-form tag 不进入检索。

## 2. Store API

代码位置：

- `academic/skill_repository/store.py`

### 2.1 `ArtifactStore.add(artifact)`

行为：

- coerce artifact schema。
- 如果是同名更新，继承 durable assets。
- 自动 bump version。
- 保留旧版本到 `history`。
- 检测依赖。
- 标记下游 stale。

### 2.2 `ArtifactStore.retrieve_audit(...)`

签名：

```python
retrieve_audit(
    query: str,
    top_k: int = 5,
    predicate: Callable[[SkillArtifact], bool] | None = None,
    rerank_key: Callable[[SkillArtifact], tuple] | None = None,
    debug_context: Dict[str, Any] | None = None,
) -> Dict[str, Any]
```

返回：

```json
{
  "query": "...",
  "top_k": 3,
  "context": {},
  "store_summary": {
    "n_total": 4,
    "n_active": 3,
    "n_stale": 1,
    "n_disabled": 0
  },
  "candidates": [
    {
      "name": "skill",
      "version": 2,
      "status": "active",
      "stale": false,
      "retrieval_enabled": true,
      "predicate_passed": true,
      "filter_reason": "",
      "score": 0.31,
      "base_score": 0.19,
      "tag_score": 0.12,
      "tag_matches": ["tool:create_ticket"],
      "rank": 1,
      "selected": true
    }
  ],
  "selected": [
    {"name": "skill", "rank": 1, "score": 0.31, "base_score": 0.19, "tag_score": 0.12}
  ]
}
```

检索分数为 `base_score + tag_score`。`base_score` 是原 token cosine；`tag_score` 来自 `debug_context.query_tags` 或 query 文本派生的受控标签命中，不做硬过滤。

### 2.3 Stale 与 rollback

相关 API：

- `stale_artifacts()`
- `clear_stale(name)`
- `pin_dependency(skill_name, dependency_name, pinned_version=...)`
- `rollback(name, target_version=None)`

语义：

- 上游更新后，下游先 stale，不强制升级。
- stale skill 被维护时再由 stale resolver 决定 update / pin / keep stale。
- rollback 不删除当前版本，而是把当前版本也放回 history 以保持审计。

## 3. LLM Role API

代码位置：

- `academic/skill_repository/llm_maintenance.py`

### 3.1 Extractor

函数：

```python
extract_skill_artifacts_from_results_llm(
    results,
    tool_schemas,
    existing_artifacts,
    llm_config,
    model_name=None,
    audit_context=None,
)
```

输入包括：

- completed traces
- metrics
- tool schemas
- existing skill summaries

输出：

- list of `SkillArtifact`

audit role：

- `extractor`

### 3.2 Bundle Builder

函数：

```python
distill_skill_bundle_llm(
    artifact,
    source_results,
    replay_results,
    integration_failures,
    llm_config,
    model_name=None,
    audit_context=None,
)
```

输出：

- updated `SkillBundle`

audit role：

- `bundle_builder`

### 3.3 Refiner

函数：

```python
refine_skill_artifact_llm(
    artifact,
    test_result,
    integration_failures,
    refinement_history,
    dependency_summaries,
    llm_config,
    model_name=None,
    audit_context=None,
)
```

输出 payload 通常包含：

```json
{
  "decision": {
    "action": "update",
    "reason": "...",
    "version_kind": "minor",
    "migration_reason": ""
  },
  "artifact_updates": {},
  "interface_updates": {},
  "bundle_updates": {}
}
```

audit role：

- `refiner`

### 3.4 Stale Resolver

函数：

```python
resolve_stale_skill_llm(
    artifact,
    upstream_context,
    llm_config,
    model_name=None,
    audit_context=None,
)
```

输出动作：

- compatible update
- pin legacy dependency
- keep stale
- rollback

## 4. BFCL Adapter API

代码位置：

- `academic/benchmarks/bfcl/maintenance/adapter.py`

### 4.1 `execute_bfcl_bundle_tests`

签名：

```python
async def execute_bfcl_bundle_tests(
    artifact: SkillArtifact,
    *,
    tools: List[Dict[str, Any]],
    llm_config: str,
    model_name: str | None,
    adapter_mode: str,
    execution_backend: str,
    prompt_style: str,
    tool_api_style: str,
    max_steps_per_turn: int,
    temperature: float | None,
    synthetic_continue: bool,
    explicit_skill_tool: bool,
    max_case_seconds: float = 180.0,
) -> SkillTestResult
```

执行流程：

1. `artifact.bundle.all_cases()`。
2. 每个 case 转成 BFCL `BenchmarkTask`。
3. without-store 跑一次。
4. with-store 跑一次。
5. 写入 `SkillTestCaseRun`。
6. 聚合 `SkillTestResult`。

### 4.2 `refine_bfcl_skill_store_llm`

输入：

- `ArtifactStore`
- `maintenance_test_results`
- model config

行为：

- 对 stale artifact 调 stale resolver。
- 对 pass_all_tests 的 artifact keep。
- 对失败 artifact 调 LLM refiner。
- 根据 action rollback 或 add updated artifact。

## 5. State Machine API

代码位置：

- `academic/skill_repository/maintenance_state_machine.py`

### 5.1 `PlayerElement`

```python
@dataclass
class PlayerElement:
    element_id: str
    kind: str
    label: str
    icon: str
    state: Dict[str, Any]
    position: Dict[str, int]
```

### 5.2 `PlayerFrame`

```python
@dataclass
class PlayerFrame:
    frame_id: str
    index: int
    name: str
    action_kind: str
    summary: str
    changed_elements: List[str]
    highlighted_elements: List[str]
    delta: Dict[str, Any]
    elements: Dict[str, PlayerElement]
    created_at: str
```

### 5.3 `MaintenanceState`

```python
@dataclass
class MaintenanceState:
    run_id: str
    phase: str
    step_index: int
    terminal: bool
    context: Dict[str, Any]
    elements: Dict[str, PlayerElement]
    frames: List[PlayerFrame]
    pending_actions: List[str]
```

关键方法：

- `snapshot_frame(...)`
- `as_trace(...)`

### 5.4 Builders

```python
build_player_trace_from_pages(...)
build_player_trace_from_debug_events(...)
build_player_trace(...)
compact_debug_event_for_player(...)
```

选择逻辑：

- 如果 result payload 有 `debug_events`，优先用 debug events。
- 否则用 legacy pages。

长 trace 返回：

```json
{
  "snapshot_mode": "delta",
  "initial_elements": {...},
  "frames": [
    {"index": 0, "elements": {...}, "element_deltas": {...}},
    {"index": 1, "elements": {}, "element_deltas": {"role:executor": {...}}}
  ]
}
```

### 5.5 `compact_debug_event_for_player(event)`

用途：把完整 debug event 转换成播放器可承载的 compact event。

输入：

- 原始 `DebugEventSink` event。

输出：

- 保留 `event_id/event_type/loop_index/turn_index/task_id/phase`。
- 保留 compact `input/output/metrics`。
- 添加 `raw_event_ref` 指向源事件。

不会保留：

- 完整 long prompt。
- 完整 raw response。
- 完整 store。
- 完整 executor trace。
- 完整 maintenance rounds。

注意：这不是审计源。完整 payload 仍在 result JSON 中。

## 6. HTTP API

代码位置：

- `academic/webapp/app.py`

### 6.1 `GET /api/maintenance/experiments`

返回所有 maintenance experiments 和 method tests 的 metadata。

### 6.2 `GET /api/maintenance/experiment?id=...`

返回一个实验的 detail view model：

- experiment metadata
- overview metrics
- docs
- artifacts
- pages
- flow cards

V2 前端依赖字段清单：

| 字段 | V2 用途 |
| --- | --- |
| `kind` | 决定 `/maintenance` 与 `/method-tests` 的默认筛选和页面语义。 |
| `experiment.id/title/subtitle/folder_name/kind` | 顶部 context bar、左侧 experiment list。 |
| `overview_metrics[].label/value/tone/help` | overview 和 Inspector 指标卡。 |
| `files.result_json/readme/suite_readme/role_log` | Inspector 文件来源说明和 raw detail。 |
| `artifacts[]` | 左侧 Artifacts folder、artifact Inspector、payload modal。 |
| `artifacts[].name/kind/description/status/version/body/interface/bundle/lineage/history` | skill store、interface、body、bundle、版本变化和 lineage 展示。 |
| `pages[].page_id/label/title/status_tone/summary_metrics` | 文件树页面、当前页标题和指标。 |
| `pages[].flow_cards[]` | React Flow role 节点、flow chip、Inspector role card。 |
| `flow_cards[].type/title/subtitle/tone/role` | 节点归类和摘要。 |
| `flow_cards[].detail.input/output/debug_raw` | role 输入、输出和 debug raw 展示。 |
| `maintenance_test.detail.unit_case_runs[]` | case run with/without skill、tokens、steps、accuracy、validity、unavailable reason。 |
| `method_case.detail.given/model_output/algorithm_output/assertions` | `/method-tests` 的方法级验证视图。 |

### 6.3 `GET /api/maintenance/player?id=...`

返回 state player trace：

```json
{
  "run_id": "...",
  "kind": "exp3",
  "title": "...",
  "terminal": true,
  "current_phase": "terminal",
  "snapshot_mode": "delta",
  "initial_elements": {},
  "frames": []
}
```

字段说明：

| 字段 | 含义 |
| --- | --- |
| `snapshot_mode` | `delta` 表示长 trace 使用增量编码 |
| `initial_elements` | delta 模式下的初始元素状态 |
| `frames[].element_deltas` | 当前帧发生变化的元素 |
| `frames[].delta.event` | compact debug event，不是完整原始日志 |
| `frames[].consumed_slots` | 当前 role 消费的数据槽 |
| `frames[].produced_slots` | 当前 role 产生的数据槽 |
| `frames[].condition_result` | pass/fail/update/rollback 等分支结果 |

V2 前端额外使用：

| 字段 | V2 用途 |
| --- | --- |
| `frames[].index/name/summary` | player caption 和 slider position。 |
| `frames[].is_marker_candidate` | marker rail 的 major marker。 |
| `frames[].role_group/action_kind` | active React Flow role node 和 Next Role 跳转。 |
| `frames[].changed_elements/highlighted_elements` | Inspector raw frame tree。 |
| `frames[].delta.event` | frame delta 和 last event tree。 |
| `initial_elements` + `element_deltas` | 保留给后续更细粒度节点状态；当前 V2 主要用 role-level highlight。 |

frame action 映射：

| event type | action kind | role group |
| --- | --- | --- |
| `retrieval` | `retrieval_step` | `retriever` |
| `initial_skill_selection` | `retrieval_step` | `retriever` |
| `prompt_injection` | `retrieval_step` | `retriever` |
| `executor_step/tool_call/tool_result/turn_end` | `executor_step` | `executor` |
| `extractor_*` | `extractor_step` | `extractor` |
| `bundle_builder_*` | `bundle_builder_step` | `bundle_builder` |
| `unit_test_* / post_refine_test_*` | `unit_test_step` | `unit_tester` |
| `refiner_* / refine_*` | `refiner_step` | `refiner` |
| `store_update/store_snapshot/fault_injection/integration_cases_appended` | `skill_store_step` | `skill_store` |

### 6.4 `GET /api/maintenance/docs`

返回文档浏览器使用的 Markdown 文档：

```json
{
  "docs": [
    {
      "id": "maintenance_readme",
      "title": "Overview",
      "kind": "reference",
      "path": ".../README.md",
      "text": "# ..."
    }
  ]
}
```

### 6.5 HTML Routes

- `/maintenance`
- `/method-tests`
- `/maintenance-docs`

## 7. Frontend Model

### 7.1 Maintenance Lab V2

文件：

- `academic/webapp/frontend`
- build 输出：`academic/webapp/static/maintenance-v2`
- Flask template：`academic/webapp/templates/maintenance.html`

核心设计：

- React + Vite + TypeScript。
- React Flow 绘制固定工业 role graph：retriever、executor、extractor、bundle builder、unit tester、refiner、skill store。
- `/maintenance` 和 `/method-tests` 共享同一 app；后者根据 route 默认筛选 method validation cases。
- V2 只消费 HTTP API view model，不直接读取 result JSON。
- `JsonTree` 默认折叠 raw payload，避免大段裸 JSON。
- `/refactor-graph` 不并入 React V2，仍是独立页面；该页面消费 `/api/refactor-graph` 的 `frames` 和 `skill_events`。

### 7.2 Refactor Graph API Fields

`GET /api/refactor-graph?id=<experiment_id>` 返回：

- `frames`: 只包含 evolve timeline frames。每帧对应一个 `task_overlap_graph_updated`，用于时间轴和图状态。
- `frames[].output.segments`: 当前 prefix 的 trace segments。
- `frames[].output.overlap_graph.edges`: segment similarity edges，`weight/text_score/error_score/shared_ngrams/shared_error_ngrams` 用于边颜色、数字和 inspector。
- `frames[].output.store_state.skills`: 当前 skill library 摘要，用于 macro skill nodes。
- `skill_events`: extractor/refactor/commit events 的归一化列表，用于右侧 inspector。
- `skill_events[].related_skills`: `{old,new,updated}` 名称列表。
- `skill_events[].new_skills`, `old_skills`, `updated_skills`: diff 面板使用的 skill payload。
- `skill_events[].llm_input`, `llm_output`, `decision`: LLM refactor 输入输出和 `decision.reason`。

状态：

- `selectedExperimentId`
- `detail`
- `player`
- `selection`
- `selectedNodeId`
- `frameIndex`
- `modal`

Adapter：

- `buildFileTree(detail)`：把 pages/artifacts 转成 filesystem-style tree。
- `buildFlowModel(detail, pageId, player, frameIndex)`：把 flow cards 转成 role graph node buckets。
- `roleFromCard(card)`：按 card type/role/title 映射到 role group。

### 7.2 Legacy Maintenance Lab

文件：

- `academic/webapp/static/maintenance.js`

核心状态：

- `currentDetail`
- `currentPlayer`
- `currentFrameIndex`
- `selectedPlayerElementId`
- `currentPageId`
- `modalPayload`

页面模型：

- overview：实验总览 + state player。
- round：单个 loop 的顺序 role cards。
- executor：完整对话和 tool calls。
- artifact：skill/bundle/test result 详情。
- docs：实验关联文档。

该文件不再作为 `/maintenance` 和 `/method-tests` 主入口。保留它是为了兼容历史脚本检查和后续迁移参考。

### 7.3 Docs Viewer

文件：

- `academic/webapp/static/maintenance_docs.js`

交互：

- 左侧文档选择。
- 顶部章节 chips。
- Markdown block 被渲染成 card/accordion。
- Mermaid sequence diagram 被转成可读 sequence card。

## 8. Experiment Result Schema

### 8.1 Common

每个实验通常包含：

- `experiment`
- `passed`
- `loops`
- `final_skills`
- `debug_events`

### 8.2 Loop

常见字段：

- `loop_index`
- `label`
- `kind`
- `run`
- `evolve`
- `refine`
- `debug_event_refs`

### 8.3 Refine Payload

常见字段：

- `maintenance_targets`
- `maintenance_rounds`
- `maintenance_test_results`
- `post_refine_test_results`
- `refine_decisions`
- `integration_cases_appended`
- `skills_after_refine`

### 8.4 Debug Event

```json
{
  "event_id": "debug_event_000001",
  "ts": "2026-05-10T...",
  "event_type": "retrieval",
  "experiment": "exp3",
  "loop_index": 0,
  "task_id": "multi_turn_base_134",
  "phase": "executor",
  "input": {},
  "output": {},
  "metrics": {}
}
```

### 8.5 Real Probe Output Directories

真实 GLM 维护实验入口：

```bash
python -m academic.benchmarks.bfcl.legacy.real_maintenance_probe --experiment exp2 --timeout-s 900
```

默认目录由当前日期决定：

```text
academic/results/bfcl_real_glm_maintenance_YYYY-MM-DD/
academic/results/real_glm_maintenance_YYYY-MM-DD/full_logs/
```

可通过环境变量固定：

```bash
SKILL_MAINTENANCE_DATE=2026-05-11 python -m academic.benchmarks.bfcl.legacy.real_maintenance_probe --experiment exp2 --timeout-s 900
```

这保证 result JSON、role audit log、debug event JSONL 使用同一个日期目录，前端才能把 experiment detail 和 player trace 对齐。

## 9. Compatibility Notes

旧实验结果可能缺少：

- `debug_events`
- `SkillTestCaseRun.input_payload`
- `actual_output`
- `tool_calls`
- unique `event_id`

兼容策略：

- 没有 debug events 时从 pages/flow_cards 重建 player。
- 缺少 per-case output 时 API 给 `unit_case_runs[]` 增加 `io_available=false` 和 `io_unavailable_reason`，UI 显示 legacy unavailable，不伪造数据。
- 重复 event id 在 player builder 中加 `#2/#3` 后缀。

## 10. Verification

推荐检查：

```bash
python -m pytest academic/method_validation/tests -q
python -m py_compile academic/skill_repository/*.py academic/benchmarks/bfcl/maintenance/adapter.py academic/webapp/app.py
node --check academic/webapp/static/maintenance.js
node --check academic/webapp/static/maintenance_docs.js
```
