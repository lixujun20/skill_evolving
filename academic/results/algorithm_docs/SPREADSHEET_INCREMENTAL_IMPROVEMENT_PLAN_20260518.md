# Spreadsheet 分章节改进计划

本计划用于后续逐章实现。执行规则：每次只改一个章节，跑该章节测试，通过后再进入下一章；每章独立提交，不混入后续优化。

## Chapter 1: Credit 输入收窄

目标：Spreadsheet credit assigner 只对 executor 实际暴露或调用过的 skill 做归因，不再把 retrieved-only skill 当作候选。

实现要点：
- trace projection 区分 `retrieved_skills`、`prompt_injected_skills`、`callable_skills`、`called_skill_functions`。
- single-run 与 notebook executor 从生成代码中解析 `skill_library` import/call。
- `assign_credit()` 使用 exposed/called skill names，而不是 retrieved names。
- retrieved-only skill 只进入 audit metadata，不进入 candidate skill prompt。
- function skill 的 evidence 中区分 `retrieved`、`injected`、`used`，实际 import/call 时 `used=true`。

测试：
- retrieved-only 不进入 credit prompt。
- prompt-injected workflow skill 进入 credit prompt。
- callable function skill 被 import/call 后进入 credit prompt，且 evidence 标记 `used=true`。
- heuristic credit fallback 同样只处理 exposed/called skills。

### Chapter 1 维护记录

提交：`fa6aec2 Narrow spreadsheet credit candidates`

状态：已完成，测试通过。

验证命令：

```bash
pytest -q academic/benchmarks/tests/test_spreadsheet_evolution.py academic/benchmarks/tests/test_skill_injector_budget.py
python -m py_compile \
  academic/benchmarks/spreadsheet/adapter.py \
  academic/benchmarks/spreadsheet/maintenance/adapter.py \
  academic/benchmarks/spreadsheet/executor.py \
  academic/benchmarks/spreadsheet/skill_runtime.py \
  academic/benchmarks/spreadsheet/trace_projection.py \
  academic/benchmarks/spreadsheet/models.py \
  academic/benchmarks/tests/test_spreadsheet_evolution.py
```

结果：`20 passed`。pytest 只出现已有的 pytest config / pydantic deprecation warnings。

### Chapter 1.5: 跨 benchmark 的 credit exposure 公共层

提交：本节对应提交 `Cross-benchmark credit scope`。

状态：已完成初版，定向测试通过。

动机：Chapter 1 先在 Spreadsheet adapter 内实现了“只对 exposed/called skill 做 credit”，但 BFCL 仍在相关实验路径中用 `_mentioned_skill_names()` 把 `retrieved_skills`、`prompt_injected_skills`、`tool_injected_skills`、`used_skills`、`called_skill_tools` 全部混在一起。这会导致两个问题：

1. 同一个算法策略在不同 benchmark 下各写一套，容易分叉。
2. BFCL 的 retrieved-only skill 仍可能进入 credit assigner，被错误归因。

设计边界：

- 公共层只做 benchmark-agnostic 的 exposure policy：字段归一化、去重、credit candidate 选择、retrieved-only audit。
- adapter 仍负责 benchmark-native evidence extraction。
- Spreadsheet 可以用 AST 解析 Python code，得到 `called_skill_functions`。
- BFCL 不能用 AST，因为 executor 输出是 tool-call trace；它用 `prompt_injected_skills`、`tool_injected_skills`、`called_skill_tools`、`used_skills` 作为非 AST 的暴露/调用证据。

验证命令：

```bash
pytest -q \
  academic/benchmarks/tests/test_credit_scope.py \
  academic/benchmarks/tests/test_spreadsheet_evolution.py::test_spreadsheet_credit_ignores_retrieved_only_skill \
  academic/benchmarks/tests/test_spreadsheet_evolution.py::test_spreadsheet_credit_fallback_ignores_retrieved_only_skill \
  academic/benchmarks/tests/bfcl_related/test_experiment.py::test_bfcl_credit_candidates_exclude_retrieved_only_skill \
  academic/benchmarks/tests/bfcl_related/test_experiment.py::test_credit_helpers_build_summary_and_disable_only_strongly_negative_skill
```

结果：`6 passed`。pytest 只出现已有 warning。

#### 1. 新增公共 credit scope 模块

位置：`academic/benchmarks/core/credit_scope.py:1-99`

改动目的：把“retrieved 不等于 credit candidate”这条算法规则从 Spreadsheet adapter 抽出，供 BFCL / Spreadsheet / 后续 benchmark 共享。

代码片段：

```python
def skill_exposure_from_mappings(*mappings: Mapping[str, Any] | None) -> Dict[str, List[str]]:
    """Normalize skill exposure fields from benchmark metrics/trace mappings.

    For each canonical field, the first non-empty list wins.  This mirrors the
    existing adapter convention of preferring score metrics and falling back to
    raw trace fields.
    """

    exposure = {
        "retrieved_skills": first_list("retrieved_skills"),
        "prompt_injected_skills": first_list("prompt_injected_skills"),
        "tool_injected_skills": first_list("tool_injected_skills"),
        "used_skills": first_list("used_skills"),
        "called_skill_tools": first_list("called_skill_tools"),
        "called_skill_functions": first_list("called_skill_functions"),
    }
    exposed = unique_skill_names(
        [
            *exposure["prompt_injected_skills"],
            *exposure["tool_injected_skills"],
            *exposure["used_skills"],
            *exposure["called_skill_tools"],
            *exposure["called_skill_functions"],
        ]
    )
    direct_used = unique_skill_names(
        [
            *exposure["used_skills"],
            *exposure["called_skill_tools"],
            *exposure["called_skill_functions"],
        ]
    )
    retrieved_only = [
        name for name in exposure["retrieved_skills"] if name not in set(exposed)
    ]
    exposure.update(
        {
            "exposed_skill_names": exposed,
            "direct_used_skill_names": direct_used,
            "credit_candidate_names": exposed,
            "retrieved_only_skills": retrieved_only,
        }
    )
    return exposure
```

#### 2. Spreadsheet credit candidate 改为复用公共层

重构后的当前位置：

- `academic/benchmarks/core/credit_scope.py:24-81`
- `academic/benchmarks/spreadsheet/maintenance/adapter.py:17-20`
- `academic/benchmarks/spreadsheet/maintenance/adapter.py:175-193`
- `academic/benchmarks/spreadsheet/trace_projection.py:10-30`
- `academic/benchmarks/spreadsheet/trace_projection.py:102-125`

改动目的：删除 Spreadsheet 本地 `_spreadsheet_credit_candidate_names()` 与 `_list_difference_preserve_order()`，避免算法策略只存在于单个 adapter。最终结构中，`spreadsheet/adapter.py` 只是兼容门面；credit 逻辑在 `spreadsheet/maintenance/adapter.py`。

公共 credit scope 代码：

```python
def skill_exposure_from_mappings(*mappings: Mapping[str, Any] | None) -> Dict[str, List[str]]:
    """Normalize skill exposure fields from benchmark metrics/trace mappings.

    For each canonical field, the first non-empty list wins.  This mirrors the
    existing adapter convention of preferring score metrics and falling back to
    raw trace fields.
    """

    def first_list(key: str) -> List[str]:
        for mapping in mappings:
            if not mapping:
                continue
            value = mapping.get(key)
            names = unique_skill_names(value if isinstance(value, list) else [])
            if names:
                return names
        return []

    exposure = {
        "retrieved_skills": first_list("retrieved_skills"),
        "prompt_injected_skills": first_list("prompt_injected_skills"),
        "tool_injected_skills": first_list("tool_injected_skills"),
        "used_skills": first_list("used_skills"),
        "called_skill_tools": first_list("called_skill_tools"),
        "called_skill_functions": first_list("called_skill_functions"),
    }
    exposed = unique_skill_names(
        [
            *exposure["prompt_injected_skills"],
            *exposure["tool_injected_skills"],
            *exposure["used_skills"],
            *exposure["called_skill_tools"],
            *exposure["called_skill_functions"],
        ]
    )
    direct_used = unique_skill_names(
        [
            *exposure["used_skills"],
            *exposure["called_skill_tools"],
            *exposure["called_skill_functions"],
        ]
    )
    retrieved_only = [
        name for name in exposure["retrieved_skills"] if name not in set(exposed)
    ]
    exposure.update(
        {
            "exposed_skill_names": exposed,
            "direct_used_skill_names": direct_used,
            "credit_candidate_names": exposed,
            "retrieved_only_skills": retrieved_only,
        }
    )
    return exposure


def credit_candidate_skill_names(*mappings: Mapping[str, Any] | None) -> List[str]:
    return skill_exposure_from_mappings(*mappings)["credit_candidate_names"]
```

Spreadsheet maintenance adapter 当前使用位置：

```python
from academic.benchmarks.core.credit_scope import (
    credit_candidate_skill_names,
    skill_exposure_flags,
)
```

```python
projection = _spreadsheet_trace_projection(detail)
candidate_names = credit_candidate_skill_names(projection)
candidate_artifacts = [
    artifact for name in candidate_names for artifact in [store.get(str(name))] if artifact
]
if not candidate_artifacts:
    return []
try:
    payload = await _compat_ask_json(
        system=SPREADSHEET_CREDIT_SYSTEM,
        user=json_block(
            {
                "task": _spreadsheet_task_fragment(detail),
                "trace_projection": projection,
                "retrieval_audit": {
                    "retrieved_only_skills": projection.get("retrieved_only_skills") or [],
                    "candidate_policy": "prompt_injected_or_called_only",
                },
                "candidate_skills": [
```

Trace projection 当前直接从公共层取 exposure：

```python
def spreadsheet_trace_projection(detail: Dict[str, Any]) -> Dict[str, Any]:
    runs = detail.get("runs") or []
    first = runs[0] if runs else {}
    metrics = first.get("metrics") or {}
    trace = first.get("trace") or {}
    exposure = skill_exposure_from_mappings(metrics, trace)
    return {
        "task_id": detail.get("task_id"),
        "success": first.get("success"),
        "score": first.get("score"),
        "answer_sheet": metrics.get("answer_sheet"),
        "answer_position": metrics.get("answer_position"),
        "checked_cells": metrics.get("checked_cells"),
        "mismatched_cells": metrics.get("mismatched_cells", [])[:5],
        "execution_ok": metrics.get("execution_ok"),
        "llm_api_style": metrics.get("llm_api_style"),
        "retrieved_skills": exposure["retrieved_skills"],
        "prompt_injected_skills": exposure["prompt_injected_skills"],
        "callable_skills": metrics.get("callable_skills") or trace.get("callable_skills") or [],
        "called_skill_functions": exposure["called_skill_functions"],
        "retrieved_only_skills": exposure["retrieved_only_skills"],
        "stderr_tail": str(trace.get("stderr") or "")[-800:],
    }
```

Skill projection 当前复用同一个 flag 计算：

```python
def spreadsheet_skill_projection(
    artifact: SkillArtifact,
    *,
    projection: Dict[str, Any],
) -> Dict[str, Any]:
    exposure = skill_exposure_flags(artifact.name, projection)
    return {
        "skill_name": artifact.name,
        "version": artifact.version,
        "kind": artifact.kind,
        "status": artifact.status,
        "description": artifact.description,
        "body": artifact.body,
        "interface": artifact.interface.as_dict(),
        "metadata": {
            "domains": artifact.metadata.get("domains") or [],
            "allowed_tools": artifact.metadata.get("allowed_tools") or [],
            "intent_keywords": artifact.metadata.get("intent_keywords") or [],
            "source_task_ids": artifact.metadata.get("source_task_ids") or [],
            "non_applicability": artifact.metadata.get("non_applicability"),
        },
        "retrieved": exposure["retrieved"],
        "injected": exposure["injected"],
        "used": exposure["used"],
```

#### 3. BFCL credit candidate 改为 exposed/called only

位置：`academic/benchmarks/bfcl/related/experiment.py:27-30`、`305-328`

改动目的：BFCL 相关实验路径之前 `_mentioned_skill_names()` 会把 retrieved-only skill 也交给 credit assigner。现在它改为公共层的 `credit_candidate_names`，即 prompt/tool injected、explicit used、called skill tools，而不是 raw retrieved。

代码片段：

```python
from academic.benchmarks.core.credit_scope import (
    skill_exposure_from_mappings,
    unique_skill_names,
)

def _mentioned_skill_names(detail: Dict[str, Any]) -> List[str]:
    run = _first_run(detail)
    metrics = dict(run.get("metrics") or {})
    trace = dict(run.get("trace") or {})
    return skill_exposure_from_mappings(metrics, trace)["credit_candidate_names"]

def _retrieved_only_skill_names(detail: Dict[str, Any]) -> List[str]:
    run = _first_run(detail)
    metrics = dict(run.get("metrics") or {})
    trace = dict(run.get("trace") or {})
    return skill_exposure_from_mappings(metrics, trace)["retrieved_only_skills"]
```

BFCL 的“不能用 AST”对应机制：

```python
def _used_skill_names(detail: Dict[str, Any]) -> List[str]:
    run = _first_run(detail)
    metrics = dict(run.get("metrics") or {})
    trace = dict(run.get("trace") or {})
    exposure = skill_exposure_from_mappings(metrics, trace)
    return unique_skill_names(
        list(metrics.get("used_skills") or [])
        + list(metrics.get("called_skill_tools") or [])
        + exposure["direct_used_skill_names"]
    )
```

其中 `called_skill_tools` 由 BFCL executor 的 tool-call handler 写入 trace，而不是从代码 AST 解析。

#### 4. 新增测试

位置：`academic/benchmarks/tests/test_credit_scope.py:1-43`

覆盖：

- `retrieved_only` 不进入 `credit_candidate_names`。
- prompt-injected、tool-injected、called function、explicit used 都进入候选。
- trace 字段 fallback 生效。

代码片段：

```python
def test_credit_scope_uses_exposed_and_called_skills_not_retrieved_only() -> None:
    metrics = {
        "retrieved_skills": ["retrieved_only", "prompt_skill", "tool_skill", "called_function", "used_skill"],
        "prompt_injected_skills": ["prompt_skill"],
        "tool_injected_skills": ["tool_skill"],
        "called_skill_functions": ["called_function"],
        "used_skills": ["used_skill"],
    }

    exposure = skill_exposure_from_mappings(metrics)

    assert exposure["credit_candidate_names"] == [
        "prompt_skill",
        "tool_skill",
        "used_skill",
        "called_function",
    ]
    assert exposure["retrieved_only_skills"] == ["retrieved_only"]
```

位置：`academic/benchmarks/tests/bfcl_related/test_experiment.py:1889-1890`

覆盖：

- BFCL retrieved-only skill 不进入 `_mentioned_skill_names()`。
- BFCL prompt/tool/used skill 仍进入候选。

代码片段：

```python
assert _mentioned_skill_names(detail) == ["prompt_skill", "tool_skill", "used_skill"]
assert _retrieved_only_skill_names(detail) == ["retrieved_only"]
```

### Chapter 1 重构后当前位置修正

说明：Chapter 1 最初实现时 Spreadsheet 代码仍主要集中在 `academic/benchmarks/spreadsheet/adapter.py`。Chapter 1.6/1.7 之后，`adapter.py` 已变成兼容门面，真实实现分布在 `prompts.py`、`models.py`、`executor.py`、`skill_runtime.py`、`trace_projection.py` 和 `maintenance/adapter.py`。以下标注按当前代码结构修正。

#### 1. Credit prompt 语义改为 exposed/called candidate skills

位置：`academic/benchmarks/spreadsheet/prompts.py:119-124`

改动目的：避免 prompt 继续暗示 credit assigner 可以对 retrieved-only skills 做归因。

代码片段：

```python
SPREADSHEET_CREDIT_SYSTEM = """\
You are the SpreadsheetBench credit assigner and maintenance-attribution judge.

You receive one compact task trace and only the exposed/called candidate skills.
Judge whether each skill was helpful, harmful, neutral, or uncertain for this
specific task.
```

#### 2. Trace schema 增加 `called_skill_functions`

位置：`academic/benchmarks/spreadsheet/models.py:8-51`

改动目的：把“function skill 是否真的被模型 import/call”变成显式 trace 字段，而不是只看 prompt 中是否出现。

代码片段：

```python
@dataclass
class SpreadsheetTrace:
    task_id: str
    prompt: str = ""
    code: str = ""
    stdout: str = ""
    stderr: str = ""
    elapsed_s: float = 0.0
    retrieved_skills: List[str] = field(default_factory=list)
    prompt_injected_skills: List[str] = field(default_factory=list)
    called_skill_functions: List[str] = field(default_factory=list)
    callable_skills: List[Dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "retrieved_skills": self.retrieved_skills,
            "prompt_injected_skills": self.prompt_injected_skills,
            "called_skill_functions": self.called_skill_functions,
            "callable_skills": self.callable_skills,
        }
```

#### 3. Single-run executor 记录实际 callable 调用

位置：`academic/benchmarks/spreadsheet/executor.py:147-163` 与 `188-209`

改动目的：模型返回代码后、执行代码前，用 AST 解析 `skill_library` 调用，并写入 trace/metrics。

代码片段：

```python
trace.code = extract_code(response.content)
if not trace.code:
    trace.elapsed_s = round(time.monotonic() - t0, 3)
    return BenchmarkResult(
        benchmark="spreadsheet",
        task_id=task.task_id,
        success=False,
        score=0.0,
        metrics={"reason": "no_python_code"},
        trace=trace.as_dict(),
    )
trace.called_skill_functions = called_spreadsheet_skill_functions(
    trace.code,
    callable_prompt["skills"],
)
stdout, stderr, returncode = await asyncio.to_thread(
    run_code, trace.code, input_copy, output_path, base_work_dir
)
```

```python
trace.elapsed_s = round(time.monotonic() - t0, 3)
verify["total_tokens"] = trace.total_tokens
verify["input_tokens"] = trace.input_tokens
verify["cache_input_tokens"] = trace.cache_input_tokens
verify["completion_tokens"] = trace.completion_tokens
verify["cost_events"] = trace.cost_events
verify["elapsed_s"] = trace.elapsed_s
verify["retrieved_skills"] = trace.retrieved_skills
verify["prompt_injected_skills"] = trace.prompt_injected_skills
verify["called_skill_functions"] = trace.called_skill_functions
verify["filtered_skills"] = trace.filtered_skills
verify["injector_events"] = trace.injector_events
verify["skill_injector_mode"] = injector_mode
verify["model_name"] = response.model_name
verify["llm_api_style"] = response.api_style
```

#### 4. Notebook executor 逐 turn 记录 callable 调用

位置：`academic/benchmarks/spreadsheet/executor.py:336-356` 与 `424-449`

改动目的：notebook 多轮中每个 code cell 都可能调用 skill；需要按 turn 记录，并在 trace 里累积去重。

代码片段：

```python
if code:
    called_this_turn = called_spreadsheet_skill_functions(
        code,
        callable_prompt["skills"],
    )
    exec_result = session.run_cell(code, timeout=CODE_EXEC_TIMEOUT)
    turn.update(exec_result)
    history.append(
        {
            "turn_index": turn_index,
            "code": code,
            "stdout": exec_result.get("stdout", ""),
            "stderr": exec_result.get("stderr", ""),
            "returncode": exec_result.get("returncode"),
            "called_skill_functions": called_this_turn,
        }
    )
    trace.called_skill_functions = list(
        dict.fromkeys([*trace.called_skill_functions, *called_this_turn])
    )

verify["total_tokens"] = trace.total_tokens
verify["input_tokens"] = trace.input_tokens
verify["cache_input_tokens"] = trace.cache_input_tokens
verify["completion_tokens"] = trace.completion_tokens
verify["cost_events"] = trace.cost_events
verify["elapsed_s"] = trace.elapsed_s
verify["retrieved_skills"] = trace.retrieved_skills
verify["prompt_injected_skills"] = trace.prompt_injected_skills
verify["called_skill_functions"] = trace.called_skill_functions
verify["filtered_skills"] = trace.filtered_skills
verify["injector_events"] = trace.injector_events
verify["skill_injector_mode"] = injector_mode
verify["execution_mode"] = "notebook"
verify["notebook_turn_count"] = len(trace.notebook_turns)
verify["notebook_stopped_by_done"] = stopped_by_done
verify["model_name"] = response_model
verify["llm_api_style"] = response_api_style
```

#### 5. 新增 callable 调用解析器

位置：`academic/benchmarks/spreadsheet/skill_runtime.py:137-189`

改动目的：用 AST 稳定识别模型是否真的调用 `skill_library` 中的 function skill，支持 direct import、alias import、module import；语法错误时用文本 fallback。

代码片段：

```python
def called_spreadsheet_skill_functions(
    code: str,
    callable_rows: Sequence[Dict[str, Any]],
) -> List[str]:
    function_to_skill = {
        str(row.get("function_name") or ""): str(row.get("skill_name") or "")
        for row in callable_rows
        if str(row.get("function_name") or "") and str(row.get("skill_name") or "")
    }
    if not function_to_skill or not str(code or "").strip():
        return []
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return called_spreadsheet_skill_functions_from_text(code, function_to_skill)
    imported_aliases: Dict[str, str] = {}
    module_aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "skill_library":
            for alias in node.names:
                imported = str(alias.name)
                if imported in function_to_skill:
                    imported_aliases[str(alias.asname or alias.name)] = imported
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "skill_library":
                    module_aliases.add(str(alias.asname or alias.name))
    called: List[str] = []
    for node in ast.walk(tree):
        func = getattr(node, "func", None)
        function_name = ""
        if isinstance(func, ast.Name):
            function_name = imported_aliases.get(func.id, func.id if func.id in function_to_skill else "")
        elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            if func.value.id in module_aliases and func.attr in function_to_skill:
                function_name = func.attr
        if function_name:
            skill_name = function_to_skill.get(function_name)
            if skill_name and skill_name not in called:
                called.append(skill_name)
    return called
```

Fallback：

```python
def called_spreadsheet_skill_functions_from_text(
    code: str,
    function_to_skill: Dict[str, str],
) -> List[str]:
    called: List[str] = []
    for function_name, skill_name in function_to_skill.items():
        if re.search(rf"\b{re.escape(function_name)}\s*\(", str(code or "")):
            if skill_name not in called:
                called.append(skill_name)
    return called
```

#### 6. `assign_credit()` 不再使用 retrieved skills 作为候选

位置：`academic/benchmarks/spreadsheet/maintenance/adapter.py:165-225`

改动目的：这是本章核心行为变化。credit candidate 从 `retrieved_skills` 改为 `prompt_injected_skills + called_skill_functions`，并把 retrieved-only 放进 audit。

代码片段：

```python
async def assign_credit(...):
    del round_index
    projection = _spreadsheet_trace_projection(detail)
    candidate_names = credit_candidate_skill_names(projection)
    candidate_artifacts = [
        artifact for name in candidate_names for artifact in [store.get(str(name))] if artifact
    ]
    if not candidate_artifacts:
        return []
    try:
        payload = await _compat_ask_json(
            system=SPREADSHEET_CREDIT_SYSTEM,
            user=json_block(
                {
                    "task": _spreadsheet_task_fragment(detail),
                    "trace_projection": projection,
                    "retrieval_audit": {
                        "retrieved_only_skills": projection.get("retrieved_only_skills") or [],
                        "candidate_policy": "prompt_injected_or_called_only",
                    },
                    "candidate_skills": [
                        _spreadsheet_skill_projection(artifact, projection=projection)
                        for artifact in candidate_artifacts
                    ],
                }
            ),
            role="spreadsheet_credit_assigner",
        )
```

#### 7. Trace projection 显式产出 retrieved-only 与 candidate list

位置：`academic/benchmarks/spreadsheet/trace_projection.py:10-32`

改动目的：把 candidate selection 所需字段统一放在 projection 中，LLM prompt 和 heuristic fallback 使用同一份事实。

代码片段：

```python
def spreadsheet_trace_projection(detail: Dict[str, Any]) -> Dict[str, Any]:
    runs = detail.get("runs") or []
    first = runs[0] if runs else {}
    metrics = first.get("metrics") or {}
    trace = first.get("trace") or {}
    exposure = skill_exposure_from_mappings(metrics, trace)
    return {
        "task_id": detail.get("task_id"),
        "success": first.get("success"),
        "score": first.get("score"),
        "retrieved_skills": exposure["retrieved_skills"],
        "prompt_injected_skills": exposure["prompt_injected_skills"],
        "callable_skills": metrics.get("callable_skills") or trace.get("callable_skills") or [],
        "called_skill_functions": exposure["called_skill_functions"],
        "retrieved_only_skills": exposure["retrieved_only_skills"],
        "stderr_tail": str(trace.get("stderr") or "")[-800:],
    }
```

Candidate helper 已删除，当前统一使用 `credit_candidate_skill_names(projection)`。

#### 8. Skill projection 增加 retrieved/injected/used 三态

位置：`academic/benchmarks/spreadsheet/trace_projection.py:102-130`

改动目的：credit assigner 需要知道 skill 是“检索到”“注入到 prompt”“被 function 调用”中的哪一种。

代码片段：

```python
exposure = skill_exposure_flags(artifact.name, projection)
return {
    "skill_name": artifact.name,
    "version": artifact.version,
    "kind": artifact.kind,
    "status": artifact.status,
    "description": artifact.description,
    "body": artifact.body,
    "interface": artifact.interface.as_dict(),
    "retrieved": exposure["retrieved"],
    "injected": exposure["injected"],
    "used": exposure["used"],
}
```

#### 9. Heuristic fallback 同样使用 injected/used evidence

位置：`academic/benchmarks/spreadsheet/maintenance/adapter.py:777-840`

改动目的：当 LLM credit assigner 失败时，fallback 不能退回到 retrieved-only 归因。因为 `candidate_artifacts` 已经来自 `credit_candidate_skill_names(projection)`，fallback 只会处理 exposed/called skills；同时 evidence 记录三态。

代码片段：

```python
events.append(
    {
        "benchmark": "spreadsheet",
        "task_id": detail.get("task_id"),
        "skill_name": artifact.name,
        "judgment": judgment,
        "effect_type": effect,
        "confidence": confidence,
        "reason": reason,
        "maintenance_actions": [],
        "refine_required": False,
        "filter_candidate": False,
        "evidence_strength": "weak",
        "attribution_scope": "prompt_influence" if judgment == "helpful" else "none",
        "bundle_case_suggestions": suggestions,
        "evidence": {
            **skill_exposure_flags(artifact.name, projection),
            "trace_signals": [reason],
        },
        "projection": copy.deepcopy(projection),
    }
)
```

#### 10. 测试改动

位置：`academic/benchmarks/tests/test_spreadsheet_evolution.py`

关键新增/修改：

- `_detail()` helper 增加 `prompt_injected` 与 `called` 参数，默认保持旧测试兼容。
- `test_spreadsheet_function_skill_can_be_imported_and_called` 断言 `called_skill_functions == ["spreadsheet_double_a1_to_b1"]`。
- `test_spreadsheet_executable_tool_marked_informational_still_callable` 断言 legacy callable 也记录调用。
- 新增 `test_spreadsheet_called_skill_function_parser_handles_alias_and_module_import`，覆盖 alias import 与 module import。
- 新增 `test_spreadsheet_credit_ignores_retrieved_only_skill`，解析 credit prompt JSON，断言 candidate list 只包含 injected/called skill。
- 新增 `test_spreadsheet_credit_fallback_ignores_retrieved_only_skill`，覆盖 LLM credit 失败时 heuristic fallback 不处理 retrieved-only skill。

关键测试片段：

```python
def test_spreadsheet_called_skill_function_parser_handles_alias_and_module_import() -> None:
    rows = [
        {"skill_name": "spreadsheet_double_a1_to_b1", "function_name": "spreadsheet_double_a1_to_b1"},
        {"skill_name": "spreadsheet_sum_column", "function_name": "spreadsheet_sum_column"},
    ]

    direct = _called_spreadsheet_skill_functions(
        "from skill_library import spreadsheet_double_a1_to_b1 as dbl\n"
        "dbl(INPUT_XLSX, OUTPUT_XLSX)\n",
        rows,
    )
    module = _called_spreadsheet_skill_functions(
        "import skill_library as skills\n"
        "skills.spreadsheet_sum_column(INPUT_XLSX, OUTPUT_XLSX)\n",
        rows,
    )

    assert direct == ["spreadsheet_double_a1_to_b1"]
    assert module == ["spreadsheet_sum_column"]
```

```python
async def test_spreadsheet_credit_ignores_retrieved_only_skill(...):
    detail = _detail(
        task,
        success=True,
        score=1.0,
        retrieved=[retrieved_only.name, injected.name, called.name],
        prompt_injected=[injected.name],
        called=[called.name],
        code="from skill_library import spreadsheet_called_function\n"
             "spreadsheet_called_function(INPUT_XLSX, OUTPUT_XLSX)",
    )

    async def fake_ask_json(**kwargs):
        payload = json.loads(kwargs["user"])
        candidate_names = [item["skill_name"] for item in payload["candidate_skills"]]
        assert candidate_names == [injected.name, called.name]
        assert payload["retrieval_audit"]["retrieved_only_skills"] == [retrieved_only.name]
        ...

    events = await SpreadsheetMaintenanceAdapter().assign_credit(...)

    assert [event["skill_name"] for event in events] == [injected.name, called.name]
    assert store.get(retrieved_only.name).usage_count == 0
```

```python
async def test_spreadsheet_credit_fallback_ignores_retrieved_only_skill(...):
    async def failing_ask_json(**kwargs):
        raise RuntimeError("mock credit failure")

    events = await SpreadsheetMaintenanceAdapter().assign_credit(...)

    assert [event["skill_name"] for event in events] == [injected.name]
    assert events[0]["evidence"]["retrieved"] is True
    assert events[0]["evidence"]["injected"] is True
    assert events[0]["evidence"]["used"] is False
    assert store.get(retrieved_only.name).usage_count == 0
```

#### 11. 本章未改内容

- 没有改 micro target 规则；helpful 是否触发 refine/test 留到 Chapter 2。
- 没有改 bundle replay 执行顺序；strict gate / with-before-without 留到 Chapter 3。
- 没有改 bundle case cap；训练期固定预算留到 Chapter 4。
- 没有改 compact skill projection，`body[:1800]` 仍保留；留到 Chapter 5。
- 没有把 generic runner 并发下沉；留到 Chapter 7。

## Chapter 1.6: 公共层与 Spreadsheet 文件结构重构

提交：待提交。

状态：已完成，测试通过。

动机：Spreadsheet adapter 已经超过 2800 行，混合了 executor、verifier、prompt、trace projection、credit、bundle、micro/macro adapter。为了后续逐章改功能时不继续把算法结构写散，本章只做结构重构，不改变算法行为；BFCL 业务路径暂不改，等后续口令再迁移。

本章新增两个可由 BFCL/Spreadsheet/后续 benchmark 共用的公共层：

- `academic/benchmarks/core/maintenance_utils.py`
- `academic/benchmarks/core/bundle_policy.py`

同时把 Spreadsheet 的 prompt 与维护输入 projection 拆到独立文件：

- `academic/benchmarks/spreadsheet/prompts.py`
- `academic/benchmarks/spreadsheet/trace_projection.py`

验证命令：

```bash
python -m py_compile \
  academic/benchmarks/core/maintenance_utils.py \
  academic/benchmarks/core/bundle_policy.py \
  academic/benchmarks/spreadsheet/prompts.py \
  academic/benchmarks/spreadsheet/trace_projection.py \
  academic/benchmarks/spreadsheet/adapter.py \
  academic/benchmarks/tests/test_spreadsheet_evolution.py \
  academic/benchmarks/tests/test_credit_scope.py

pytest -q \
  academic/benchmarks/tests/test_credit_scope.py \
  academic/benchmarks/tests/test_spreadsheet_evolution.py \
  academic/benchmarks/tests/test_skill_injector_budget.py
```

结果：`22 passed`。pytest 只出现已有 warning。

### 1. 公共 maintenance utils 层

位置：`academic/benchmarks/core/maintenance_utils.py:1-28`

职责：维护代码里反复出现的小工具，避免 BFCL/Spreadsheet 各自定义 `_json_block`、`_now_iso`、`_stable_id`、`_env_int`。

代码片段：

```python
def json_block(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_id(*parts: Any, length: int = 10) -> str:
    raw = "\n".join(str(part) for part in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:length]


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)) or default)
    except Exception:
        return default
```

Spreadsheet adapter 对应迁移位置：`academic/benchmarks/spreadsheet/adapter.py:43`、`1272`、`1922`、`2309`、`2379`、`2541`、`2554`、`2574`、`2618`。

示例代码片段：

```python
from academic.benchmarks.core.maintenance_utils import json_block, now_iso, stable_id

payload = await _ask_json(
    system=SPREADSHEET_CREDIT_SYSTEM,
    user=json_block({...}),
)

case_id = f"{skill_name}:{polarity}:{stable_id(task.get('task_id'), reason, source)}"
```

### 2. 公共 bundle policy 层

位置：`academic/benchmarks/core/bundle_policy.py:1-154`

职责：统一 bundle case bucket、case budget env、通用 trim 策略。该层不懂 BFCL 或 Spreadsheet replay，只处理 `SkillArtifact.bundle` 的 `positive_cases` / `negative_cases` / `integration_cases`。

代码片段：

```python
BUNDLE_CASE_ATTRS = ("positive_cases", "negative_cases", "integration_cases")


def bundle_bucket(polarity: str) -> str:
    return {
        "positive": "positive_cases",
        "negative": "negative_cases",
        "integration": "integration_cases",
    }.get(str(polarity), "integration_cases")


def trim_bundle_cases_to_budget(
    artifact: SkillArtifact,
    *,
    per_polarity_limit: int | None = None,
    total_limit: int | None = None,
    priority_fn: Callable[[SkillBundleCase, int], tuple[Any, ...]] | None = None,
) -> bool:
    limit = max(1, int(per_polarity_limit or bundle_case_limit_per_polarity()))
    total = max(1, int(total_limit or bundle_max_total_cases()))
    priority = priority_fn or default_bundle_case_priority
    ...
```

Spreadsheet adapter 对应迁移位置：`academic/benchmarks/spreadsheet/adapter.py:31-35`、`1549`、`2348-2352`。

代码片段：

```python
from academic.benchmarks.core.bundle_policy import (
    bundle_bucket,
    default_bundle_case_priority,
    trim_bundle_cases_to_budget,
)

bucket = bundle_bucket(case.polarity)

def _trim_spreadsheet_bundle_cases(artifact: SkillArtifact) -> None:
    trim_bundle_cases_to_budget(artifact, priority_fn=_spreadsheet_case_priority)

def _spreadsheet_case_priority(case: SkillBundleCase, index: int = 0) -> tuple[Any, ...]:
    return default_bundle_case_priority(case, index)
```

说明：本次只让 Spreadsheet 使用公共 bundle policy。BFCL 的 `trim_bundle_cases()` 暂不迁移，避免在用户要求前改 BFCL 业务路径。

### 3. Spreadsheet prompt 拆分

位置：`academic/benchmarks/spreadsheet/prompts.py:1-219`

职责：集中存放 Spreadsheet executor/extractor/credit prompt，不再放在 adapter 顶部。

Spreadsheet adapter 对应迁移位置：`academic/benchmarks/spreadsheet/adapter.py:56-63`。

代码片段：

```python
from academic.benchmarks.spreadsheet.prompts import (
    DATASET_URL,
    SPREADSHEET_CREDIT_SYSTEM,
    SPREADSHEET_DONE_PATTERN,
    SPREADSHEET_EXTRACT_SYSTEM,
    SPREADSHEET_NOTEBOOK_SYSTEM,
    SPREADSHEET_SYSTEM,
)
```

### 4. Spreadsheet trace projection 拆分

位置：`academic/benchmarks/spreadsheet/trace_projection.py:1-130`

职责：维护 agent 的 compact input projection，包括 task fragment、result projection、skill projection 和 code snippet。它仍然是 Spreadsheet-specific，因为 official workbook、answer range、openpyxl code/stdout/stderr 都是 benchmark 原生概念。

代码片段：

```python
def spreadsheet_trace_projection(detail: Dict[str, Any]) -> Dict[str, Any]:
    runs = detail.get("runs") or []
    first = runs[0] if runs else {}
    metrics = first.get("metrics") or {}
    trace = first.get("trace") or {}
    exposure = skill_exposure_from_mappings(metrics, trace)
    return {
        "task_id": detail.get("task_id"),
        "success": first.get("success"),
        "score": first.get("score"),
        "retrieved_skills": exposure["retrieved_skills"],
        "prompt_injected_skills": exposure["prompt_injected_skills"],
        "called_skill_functions": exposure["called_skill_functions"],
        "retrieved_only_skills": exposure["retrieved_only_skills"],
        "stderr_tail": str(trace.get("stderr") or "")[-800:],
    }
```

Spreadsheet adapter 保留旧私有函数名作为 import alias，降低本章改动面：

```python
from academic.benchmarks.spreadsheet.trace_projection import (
    spreadsheet_code_snippet as _spreadsheet_code_snippet,
    spreadsheet_result_projection as _spreadsheet_result_projection,
    spreadsheet_skill_projection as _spreadsheet_skill_projection,
    spreadsheet_task_fragment as _spreadsheet_task_fragment,
    spreadsheet_trace_projection as _spreadsheet_trace_projection,
)
```

### 5. 本章刻意不做的事

- 不改 BFCL 的 `maintenance/adapter.py` 和 `related/experiment.py` 业务流程。
- 不迁移 Spreadsheet executor/verifier/notebook runtime。
- 不改变 skill extraction、credit、bundle replay、micro/macro 的行为。
- 不重跑真实实验，只跑结构重构相关 regression tests。

## Chapter 1.7: Spreadsheet 按 BFCL 粒度继续拆分

提交：待提交。

状态：已完成，测试通过。

动机：Chapter 1.6 只拆出了 prompt 和 trace projection，`spreadsheet/adapter.py` 仍然包含 loader、executor、verifier、callable skill runtime、notebook runtime。用户指出“抽取不够充分”，本章继续把 Spreadsheet 拆到接近 BFCL 的粒度，同时保持 `academic.benchmarks.spreadsheet.adapter` 作为兼容门面，避免 core runner 和现有测试大范围改 import。

拆分后当前文件体量：

```text
 110 academic/benchmarks/spreadsheet/adapter.py
1405 academic/benchmarks/spreadsheet/maintenance/adapter.py
 550 academic/benchmarks/spreadsheet/executor.py
 458 academic/benchmarks/spreadsheet/skill_runtime.py
 213 academic/benchmarks/spreadsheet/prompts.py
 165 academic/benchmarks/spreadsheet/verifier.py
 130 academic/benchmarks/spreadsheet/trace_projection.py
  74 academic/benchmarks/spreadsheet/loader.py
  51 academic/benchmarks/spreadsheet/models.py
```

验证命令：

```bash
python -m py_compile \
  academic/benchmarks/spreadsheet/models.py \
  academic/benchmarks/spreadsheet/loader.py \
  academic/benchmarks/spreadsheet/verifier.py \
  academic/benchmarks/spreadsheet/skill_runtime.py \
  academic/benchmarks/spreadsheet/executor.py \
  academic/benchmarks/spreadsheet/prompts.py \
  academic/benchmarks/spreadsheet/trace_projection.py \
  academic/benchmarks/spreadsheet/adapter.py

pytest -q \
  academic/benchmarks/tests/test_spreadsheet_evolution.py \
  academic/benchmarks/tests/test_skill_injector_budget.py

pytest -q \
  academic/benchmarks/tests/test_generic_evolution.py::test_spreadsheet_adapter_uses_generic_run_task_contract \
  academic/benchmarks/tests/bfcl/test_benchmark_adapters.py::test_spreadsheet_loader_and_verifier_contract \
  academic/benchmarks/tests/bfcl/test_benchmark_adapters.py::test_spreadsheet_verifier_accepts_single_cell_range \
  academic/benchmarks/tests/bfcl/test_benchmark_adapters.py::test_spreadsheet_runner_forwards_model_name \
  academic/benchmarks/tests/bfcl/test_benchmark_adapters.py::test_spreadsheet_task_passes_model_override_to_llm \
  academic/benchmarks/tests/bfcl/test_benchmark_adapters.py::test_spreadsheet_answer_range_parser_handles_official_multi_ranges
```

结果：第一组 `20 passed`，第二组 `6 passed`，`py_compile` 通过。pytest 只出现已有 warning。

### 1. `models.py`: trace schema

位置：`academic/benchmarks/spreadsheet/models.py:1-51`

职责：只保存 Spreadsheet rollout trace dataclass，和 BFCL 的 `models.py` 对齐。

代码片段：

```python
@dataclass
class SpreadsheetTrace:
    task_id: str
    prompt: str = ""
    code: str = ""
    stdout: str = ""
    stderr: str = ""
    elapsed_s: float = 0.0
    retrieved_skills: List[str] = field(default_factory=list)
    prompt_injected_skills: List[str] = field(default_factory=list)
    called_skill_functions: List[str] = field(default_factory=list)
    callable_skills: List[Dict[str, Any]] = field(default_factory=list)
```

### 2. `loader.py`: dataset loading

位置：`academic/benchmarks/spreadsheet/loader.py:1-74`

职责：`ensure_spreadsheetbench()` 和 `load_spreadsheet_tasks()`。对应 BFCL 的 loader 层，不再放在 maintenance adapter 中。

代码片段：

```python
def load_spreadsheet_tasks(
    *,
    cache_dir: Path,
    n_train: int = 200,
    n_test: int = 200,
    split_seed: int = 42,
    refresh: bool = False,
) -> Tuple[List[BenchmarkTask], List[BenchmarkTask]]:
    root = ensure_spreadsheetbench(cache_dir, refresh=refresh)
    dataset_path = root / "dataset.json"
    raw = json.loads(dataset_path.read_text())
```

### 3. `verifier.py`: workbook/range verifier

位置：`academic/benchmarks/spreadsheet/verifier.py:1-165`

职责：official workbook comparison、answer range parsing、cell value normalization。对应 BFCL 的 scoring/verifier 类边界。

代码片段：

```python
def verify_spreadsheet_output(
    *,
    predicted_xlsx: Path,
    golden_xlsx: Path,
    sheet_name: Optional[str],
    answer_range: Optional[str],
) -> Dict[str, Any]:
    pred_wb = openpyxl.load_workbook(predicted_xlsx, data_only=False)
    gold_wb = openpyxl.load_workbook(golden_xlsx, data_only=False)
    requested_sheet = first_sheet_name(sheet_name)
    refs = answer_range_refs(answer_range, default_sheet=requested_sheet)
```

兼容导出：`adapter.py` 仍 re-export `verify_spreadsheet_output`、`_answer_range_refs`、`_cells_in_range` 等旧测试/调用路径。

### 4. `skill_runtime.py`: callable skill and Python runtime

位置：`academic/benchmarks/spreadsheet/skill_runtime.py:1-458`

职责：function skill 写入 `skill_library.py`、AST 检测实际调用、snippet wrapping、single code subprocess、notebook persistent Python session。

代码片段：

```python
def write_spreadsheet_skill_library(skills: Sequence[SkillArtifact], work_dir: Path) -> Dict[str, Any]:
    callable_rows: List[Dict[str, Any]] = []
    chunks = [
        "from pathlib import Path\n",
        "import openpyxl\n",
        "from openpyxl import load_workbook\n",
        ...
    ]
```

注意：本章修复了搬迁过程中的 helper 名称兼容，生成的 wrapped function 继续调用 `skill_library.py` 内的 `_spreadsheet_callable_kwargs(...)`，保证旧测试通过。

### 5. `executor.py`: single/notebook rollout

位置：`academic/benchmarks/spreadsheet/executor.py:1-550`

职责：`run_spreadsheet_task()` 和 `run_spreadsheet_task_notebook()`。它引用 prompt、runtime、verifier、models，但不包含 maintenance 逻辑。

代码片段：

```python
async def run_spreadsheet_task(
    task: BenchmarkTask,
    *,
    llm_config: str,
    model_name: Optional[str] = None,
    artifact_store: Optional[ArtifactStore] = None,
    top_k_skills: int = 5,
    skill_injector_mode: str | None = None,
    skill_context_budget_chars: int | None = None,
    work_dir: Optional[Path] = None,
) -> BenchmarkResult:
    t0 = time.monotonic()
    trace = SpreadsheetTrace(task_id=task.task_id)
```

### 6. `maintenance/adapter.py` 与 `adapter.py`

位置：`academic/benchmarks/spreadsheet/maintenance/adapter.py:1-1405`、`academic/benchmarks/spreadsheet/adapter.py:1-110`

职责：`maintenance/adapter.py` 保留 `SpreadsheetMaintenanceAdapter`、credit/extraction/bundle/refine/macro helpers；`adapter.py` 只作为旧路径兼容门面，re-export executor/runtime/verifier/projection/maintenance 符号。

兼容 wrapper：

```python
async def run_spreadsheet_task(*args: Any, **kwargs: Any) -> BenchmarkResult:
    _spreadsheet_executor.ask_text_llm = ask_text_llm
    return await _run_spreadsheet_task_impl(*args, **kwargs)


async def run_spreadsheet_task_notebook(*args: Any, **kwargs: Any) -> BenchmarkResult:
    _spreadsheet_executor.ask_text_llm = ask_text_llm
    return await _run_spreadsheet_task_notebook_impl(*args, **kwargs)
```

这段是为了保持旧测试和外部代码对 `academic.benchmarks.spreadsheet.adapter.ask_text_llm` 的 monkeypatch 仍然生效。长期可以把 monkeypatch 路径迁到 `spreadsheet.executor.ask_text_llm` 后删除。

### 7. 本章刻意不做的事

- 不继续拆 `maintenance/adapter.py` 内的 credit/extraction/bundle/refine/macro helpers；这可以作为下一轮结构重构。
- 不改 BFCL 路径。
- 不改算法行为、prompt 内容、runner 参数或实验配置。

## Chapter 2: Micro target 收窄

目标：helpful credit 默认只记录 evidence，不触发 immediate refine/test。

实现要点：
- `_spreadsheet_micro_targets()` 只对 strong harmful、`filter_candidate`、`refine_required`、negative/integration bundle case 触发维护。
- helpful positive case 进入 evidence 或 bundle candidate，但不立即 test/refine。
- 增加 target reason audit。

测试：
- helpful high confidence 不调用 refiner/bundle test。
- harmful high confidence 调用 pre-refine。
- neutral/uncertain credit 本身不触发；如果同一 skill 有 integration bundle case，则触发 bundle test 但 refiner 因无 strong credit 返回 keep。

### Chapter 2 维护记录

提交：待提交。

状态：已完成，测试通过。

验证命令：

```bash
pytest -q academic/benchmarks/tests/test_spreadsheet_evolution.py

pytest -q \
  academic/benchmarks/tests/test_benchmark_refactor_contracts.py \
  academic/benchmarks/tests/test_common_maintenance_core.py \
  academic/benchmarks/tests/test_skill_injector_budget.py

python -m py_compile \
  academic/benchmarks/spreadsheet/maintenance/adapter.py \
  academic/benchmarks/spreadsheet/adapter.py \
  academic/benchmarks/tests/test_spreadsheet_evolution.py
```

结果：`20 passed`、`25 passed`，`py_compile` 通过。pytest 只出现已有 pytest config / pydantic deprecation warnings。

#### 1. 引入 strong harmful credit 判断

位置：`academic/benchmarks/spreadsheet/maintenance/adapter.py:12-23`

改动目的：micro target 不再把所有 credit/bundle rows 交给 generic micro，而是先按 Spreadsheet 策略筛选；其中 strong harmful 的定义复用公共 credit event helper。

代码片段：

```python
from academic.benchmarks.core.credit_events import (
    apply_credit_evidence,
    is_strong_harmful_credit,
    normalize_credit_events,
)
from academic.benchmarks.core.credit_scope import (
    credit_candidate_skill_names,
    skill_exposure_flags,
)
from academic.benchmarks.core.maintenance_adapter import MaintenanceRunConfig, NoOpMaintenanceAdapter
from academic.benchmarks.core.maintenance_utils import json_block, now_iso, stable_id
from academic.benchmarks.core.micro_maintenance import MicroMaintenanceHooks, run_generic_micro_maintenance
```

#### 2. `run_micro_maintenance()` 先筛选维护输入

位置：`academic/benchmarks/spreadsheet/maintenance/adapter.py:339-368`

改动目的：helpful positive credit/case 仍保留在 report audit 中，但不再作为 immediate refine/test 的输入；generic micro 只收到 harmful/filter/refine 和 negative/integration bundle case。

代码片段：

```python
micro_target_reasons = _spreadsheet_micro_target_reasons(
    credit_events=credit_events,
    credit_bundle_cases=credit_bundle_cases,
    extracted=extracted,
)
maintenance_credit_events = _spreadsheet_micro_credit_events(credit_events)
maintenance_credit_bundle_cases = _spreadsheet_micro_credit_bundle_cases(credit_bundle_cases)
report = await run_generic_micro_maintenance(
    detail=detail,
    credit_events=maintenance_credit_events,
    credit_bundle_cases=maintenance_credit_bundle_cases,
    store=store,
    config=config,
    hooks=MicroMaintenanceHooks(refine_skill=refine_skill, run_bundle_test=run_bundle_test),
    round_index=int(kwargs.get("round_index") or 0),
    task_index=task_index,
    relevant_skill_names=list(micro_target_reasons),
)
report["refine_decisions"] = [
    {k: v for k, v in item.items() if k != "updated_artifact"}
    for item in report.get("refine_decisions", [])
]
report["extraction_reports"] = extraction_reports
report["reason"] = "spreadsheet_micro_maintenance"
report["trace_projection"] = _spreadsheet_trace_projection(detail)
report["micro_target_reasons"] = micro_target_reasons
report["micro_maintenance_credit_events"] = copy.deepcopy(maintenance_credit_events)
report["micro_maintenance_credit_bundle_cases"] = copy.deepcopy(maintenance_credit_bundle_cases)
report["credit_bundle_cases"] = copy.deepcopy(credit_bundle_cases)
return report
```

#### 3. `_spreadsheet_micro_targets()` 改为 reason map 的兼容封装

位置：`academic/benchmarks/spreadsheet/maintenance/adapter.py:940-952`

改动目的：保留旧 helper 名字给兼容调用，但实际 target 来源改成带原因的 `_spreadsheet_micro_target_reasons()`。

代码片段：

```python
def _spreadsheet_micro_targets(
    *,
    credit_events: Sequence[Dict[str, Any]],
    credit_bundle_cases: Sequence[Dict[str, Any]],
    extracted: Sequence[SkillArtifact],
) -> List[str]:
    return list(
        _spreadsheet_micro_target_reasons(
            credit_events=credit_events,
            credit_bundle_cases=credit_bundle_cases,
            extracted=extracted,
        )
    )
```

#### 4. 新增 micro target reason 规则

位置：`academic/benchmarks/spreadsheet/maintenance/adapter.py:955-984`

改动目的：明确 Chapter 2 的触发规则：`filter_candidate`、`refine_required`、strong harmful credit、negative bundle case、integration bundle case。helpful positive credit/case 不会进入 target map。

代码片段：

```python
def _spreadsheet_micro_target_reasons(
    *,
    credit_events: Sequence[Dict[str, Any]],
    credit_bundle_cases: Sequence[Dict[str, Any]],
    extracted: Sequence[SkillArtifact],
) -> Dict[str, List[str]]:
    reasons: Dict[str, List[str]] = {}

    def add(name: Any, reason: str) -> None:
        value = str(name or "").strip()
        if not value:
            return
        rows = reasons.setdefault(value, [])
        if reason not in rows:
            rows.append(reason)

    del extracted
    for event in credit_events or []:
        name = event.get("skill_name")
        if event.get("filter_candidate"):
            add(name, "filter_candidate")
        if event.get("refine_required"):
            add(name, "refine_required")
        if is_strong_harmful_credit(event):
            add(name, "strong_harmful_credit")
    for row in credit_bundle_cases or []:
        polarity = str(row.get("polarity") or "").strip().lower()
        if polarity in {"negative", "integration"}:
            add(row.get("skill_name"), f"{polarity}_bundle_case")
    return reasons
```

#### 5. 新增 micro credit/bundle 输入过滤

位置：`academic/benchmarks/spreadsheet/maintenance/adapter.py:987-1006`

改动目的：generic micro 的 refine/test 阶段只看到需要立即维护的事件；正向 evidence 仍由 `assign_credit()` 和 full `credit_bundle_cases` audit 保存。

代码片段：

```python
def _spreadsheet_micro_credit_events(
    credit_events: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    return [
        copy.deepcopy(event)
        for event in credit_events or []
        if event.get("filter_candidate")
        or event.get("refine_required")
        or is_strong_harmful_credit(event)
    ]


def _spreadsheet_micro_credit_bundle_cases(
    credit_bundle_cases: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    return [
        copy.deepcopy(row)
        for row in credit_bundle_cases or []
        if str(row.get("polarity") or "").strip().lower() in {"negative", "integration"}
    ]
```

#### 6. 新增测试 import

位置：`academic/benchmarks/tests/test_spreadsheet_evolution.py:7-11`

改动目的：integration bundle case 测试需要直接构造 `SkillBundleCase`。

代码片段：

```python
from academic.benchmarks.core.artifacts import ArtifactStore
from academic.benchmarks.core.evolution import OnlineSkillEvolutionRunner
from academic.benchmarks.core.llm_text import TextLLMResponse
from academic.benchmarks.core.maintenance_adapter import MaintenanceRunConfig
from academic.benchmarks.core.types import BenchmarkResult, BenchmarkTask, SkillArtifact, SkillBundleCase, SkillInterface, SkillTestResult
```

#### 7. helpful positive credit 不触发 refine/test

位置：`academic/benchmarks/tests/test_spreadsheet_evolution.py:610-680`

代码片段：

```python
async def test_spreadsheet_micro_does_not_refine_helpful_positive_credit(monkeypatch, tmp_path: Path) -> None:
    task = _task(tmp_path, "sheet_helpful_micro", "Double A1 into B1.", source=7, answer=14)
    skill = SkillArtifact(
        name="spreadsheet_helpful_double",
        kind="workflow_guardrail_card",
        description="Helpful doubling guidance.",
        body="Use the source cell value instead of hard-coded constants.",
        metadata={"domains": ["SpreadsheetBench", "formula_generation"], "intent_keywords": ["double"]},
    )
    store = ArtifactStore([skill])
    detail = _detail(
        task,
        success=True,
        score=1.0,
        retrieved=[skill.name],
        prompt_injected=[skill.name],
        code="ws['B1']=ws['A1'].value*2\nwb.save(OUTPUT_XLSX)",
    )
    calls: List[str] = []
```

```python
positive_case = {
    "skill_name": skill.name,
    "case_id": f"{skill.name}:positive:sheet_helpful_micro",
    "polarity": "positive",
    "created": True,
}
report = await SpreadsheetMaintenanceAdapter().run_micro_maintenance(
    detail=detail,
    credit_events=[
        {
            "skill_name": skill.name,
            "judgment": "helpful",
            "effect_type": "correctness_gain",
            "confidence": 0.95,
            "reason": "The injected skill aligned the formula.",
            "refine_required": False,
            "filter_candidate": False,
        }
    ],
    credit_bundle_cases=[positive_case],
    store=store,
    config=MaintenanceRunConfig(llm_config="mock"),
    round_index=0,
    task_index=0,
)

assert report["maintenance_targets"] == []
assert report["micro_target_reasons"] == {}
assert report["credit_bundle_cases"] == [positive_case]
assert report["micro_maintenance_credit_events"] == []
assert report["micro_maintenance_credit_bundle_cases"] == []
assert calls == []
```

#### 8. strong harmful credit 无 bundle case 也触发 pre-refine

位置：`academic/benchmarks/tests/test_spreadsheet_evolution.py:683-732`

代码片段：

```python
async def test_spreadsheet_micro_refines_strong_harmful_without_bundle_case(monkeypatch, tmp_path: Path) -> None:
    task = _task(tmp_path, "sheet_harmful_micro", "Double A1 into B1.", source=7, answer=14)
    skill = SkillArtifact(
        name="spreadsheet_harmful_double",
        kind="workflow_guardrail_card",
        description="Bad doubling guidance.",
        body="Write zero for doubling tasks.",
        metadata={"domains": ["SpreadsheetBench", "formula_generation"], "intent_keywords": ["double"]},
    )
    store = ArtifactStore([skill])
    detail = _detail(
        task,
        success=False,
        score=0.0,
        retrieved=[skill.name],
        prompt_injected=[skill.name],
        code="ws['B1']=0\nwb.save(OUTPUT_XLSX)",
    )
    calls: List[str] = []
```

```python
report = await SpreadsheetMaintenanceAdapter().run_micro_maintenance(
    detail=detail,
    credit_events=[
        {
            "skill_name": skill.name,
            "judgment": "harmful",
            "effect_type": "correctness_harm",
            "confidence": 0.9,
            "reason": "The skill caused a wrong output.",
            "refine_required": False,
            "filter_candidate": False,
        }
    ],
    credit_bundle_cases=[],
    store=store,
    config=MaintenanceRunConfig(llm_config="mock"),
    round_index=0,
    task_index=0,
)

assert report["maintenance_targets"] == [skill.name]
assert report["micro_target_reasons"] == {skill.name: ["strong_harmful_credit"]}
assert report["maintenance_test_results"][0]["aggregate"]["reason"] == "no_bundle_cases"
assert calls == ["refine"]
```

#### 9. integration case 触发 bundle test，但 neutral credit 不触发 refiner

位置：`academic/benchmarks/tests/test_spreadsheet_evolution.py:735-803`

代码片段：

```python
skill.bundle.integration_cases.append(
    SkillBundleCase(
        case_id="integration_case",
        source="manual_integration",
        prompt="Double A1 into B1.",
        polarity="integration",
    )
)
report = await SpreadsheetMaintenanceAdapter().run_micro_maintenance(
    detail=detail,
    credit_events=[
        {
            "skill_name": skill.name,
            "judgment": "neutral",
            "confidence": 0.5,
            "reason": "No direct causal evidence.",
        }
    ],
    credit_bundle_cases=[
        {
            "skill_name": skill.name,
            "case_id": "integration_case",
            "polarity": "integration",
            "created": True,
        }
    ],
    store=store,
    config=MaintenanceRunConfig(llm_config="mock"),
    round_index=0,
    task_index=0,
)

assert report["maintenance_targets"] == [skill.name]
assert report["micro_target_reasons"] == {skill.name: ["integration_bundle_case"]}
assert report["refine_decisions"][0]["action"] == "keep"
assert report["refine_decisions"][0]["reason"] == "no_strong_credit_signal"
assert calls == ["bundle_test"]
```

## Chapter 3: Bundle replay 分级

目标：先 strict gate，再 with-skill，只有 with-skill 通过才跑 without-skill。

实现要点：
- strict gate 检查 official task snapshot、workbook/golden/range。
- with-skill fail 时跳过 without-skill，直接记录 failure。
- result schema 记录 strict gate、with/without 结果、counterfactual map 和 integration failures。

测试：
- gate fail 不调用 LLM。
- with fail 不跑 without。
- with pass 才跑 without。

### Chapter 3 维护记录

提交：待提交。

状态：已完成，测试通过。

验证命令：

```bash
pytest -q academic/benchmarks/tests/test_spreadsheet_evolution.py

python -m py_compile \
  academic/benchmarks/spreadsheet/maintenance/adapter.py \
  academic/benchmarks/spreadsheet/adapter.py \
  academic/benchmarks/tests/test_spreadsheet_evolution.py
```

结果：`23 passed`，`py_compile` 通过。pytest 只出现已有 pytest config / pydantic deprecation warnings。

#### 1. `_execute_spreadsheet_bundle_tests()` 改为 strict gate + with-before-without

位置：`academic/benchmarks/spreadsheet/maintenance/adapter.py:1139-1245`

改动目的：每个 bundle case 先做本地 strict gate；gate 失败直接记录 `strict_gate` run，不调用 LLM。gate 通过后先跑 `with_skill`，只有 `with_skill` 通过且 case 的 `contrast_protocol.without_skill` 允许时，才跑 `without_skill`。

代码片段：

```python
async def _execute_spreadsheet_bundle_tests(
    *,
    artifact: SkillArtifact,
    config: MaintenanceRunConfig,
) -> SkillTestResult:
    case_runs: List[SkillTestCaseRun] = []
    integration_failures: List[Dict[str, Any]] = []
    with_skill_valid_by_task: Dict[str, bool | None] = {}
    without_skill_valid_by_task: Dict[str, bool | None] = {}
    comparable_case_count = 0
    improved = 0
    regressed = 0
    tokens_delta = 0
    for case in artifact.bundle.all_cases():
        gate = _spreadsheet_bundle_strict_gate(case)
        task = gate.get("task")
        task_id = str((task.task_id if isinstance(task, BenchmarkTask) else "") or case.case_id)
        if gate["failures"]:
            failure_summary = json.dumps(gate["failures"], ensure_ascii=False)
            run = _spreadsheet_bundle_gate_failure_run(
                case=case,
                task=task if isinstance(task, BenchmarkTask) else None,
                variant="strict_gate",
                failures=gate["failures"],
                artifact=artifact,
            )
            case_runs.append(run)
            integration_failures.append(
                {
                    "task_id": task_id,
                    "case_id": case.case_id,
                    "error": failure_summary,
                    "contract_failures": copy.deepcopy(gate["failures"]),
                }
            )
            regressed += 1
            comparable_case_count += 1
            with_skill_valid_by_task[task_id] = False
            continue

        assert isinstance(task, BenchmarkTask)
        with_result = await _compat_module().run_spreadsheet_task(
            task,
            llm_config=config.llm_config,
            model_name=config.model_name,
            artifact_store=ArtifactStore([copy.deepcopy(artifact)]),
            top_k_skills=1,
        )
        with_run = _spreadsheet_bundle_case_run(
            case=case,
            task=task,
            artifact=artifact,
            result=with_result,
            variant="with_skill",
            top_k_skills=1,
            skill_injection_mode="full",
        )
        case_runs.append(with_run)
        with_skill_valid_by_task[task.task_id] = bool(with_result.success)
        if not with_run.passed:
            integration_failures.append(
                {
                    "task_id": task.task_id,
                    "case_id": case.case_id,
                    "metrics": copy.deepcopy(with_result.metrics or {}),
                    "trace": copy.deepcopy(with_result.trace or {}),
                    "error": with_result.error or with_run.failure_summary,
                    "contract_failures": copy.deepcopy(with_run.metadata.get("contract_failures") or []),
                }
            )
            regressed += 1
            comparable_case_count += 1
            continue

        should_run_without = bool((case.contrast_protocol or {}).get("without_skill", True))
        if not should_run_without:
            comparable_case_count += 1
            if case.polarity in {"positive", "integration"}:
                improved += 1
            continue

        without_result = await _compat_module().run_spreadsheet_task(
            task,
            llm_config=config.llm_config,
            model_name=config.model_name,
            artifact_store=ArtifactStore([]),
            top_k_skills=0,
        )
```

#### 2. Result schema 增加 strict/counterfactual 汇总

位置：`academic/benchmarks/spreadsheet/maintenance/adapter.py:1247-1300`

代码片段：

```python
strict_failures = [
    {
        "case_id": run.case_id,
        "variant": run.variant,
        "contract_failures": copy.deepcopy(run.metadata.get("contract_failures") or []),
    }
    for run in case_runs
    if not run.passed and run.metadata.get("contract_failures")
]
with_runs = [run for run in case_runs if run.variant == "with_skill"]
passed = bool(case_runs) and all(run.passed for run in case_runs if run.variant != "without_skill")
avg_accuracy = round(
    sum(float(run.accuracy or 0.0) for run in with_runs) / max(len(with_runs), 1),
    4,
)
result = SkillTestResult(
    result_id=f"{artifact.name}:spreadsheet_bundle:{stable_id(artifact.version, now_iso())}",
    skill_name=artifact.name,
    skill_version=artifact.version,
    bundle_id=artifact.bundle.bundle_id or f"{artifact.name}.bundle",
    bundle_version=artifact.bundle.bundle_version,
    run_label="spreadsheet_bundle_unit",
    unit_case_runs=case_runs,
    aggregate={
        "passed": passed,
        "pass_all_tests": passed and not strict_failures and not integration_failures,
        "n_cases": len(artifact.bundle.all_cases()),
        "n_case_runs": len(case_runs),
        "n_comparable_cases": comparable_case_count,
        "n_passed": sum(1 for run in case_runs if run.passed),
        "n_improved": improved,
        "n_regressed": regressed,
        "n_strict_failures": len(strict_failures),
        "strict_failures": strict_failures,
        "strict_contract_gate": True,
        "with_before_without": True,
        "avg_accuracy": avg_accuracy,
        "unit_utility_report": {
            "delta_accuracy": round((improved - regressed) / max(comparable_case_count, 1), 4),
            "delta_tokens": tokens_delta,
        },
    },
    counterfactual={
        "without_skill_valid_by_task": without_skill_valid_by_task,
        "with_skill_valid_by_task": with_skill_valid_by_task,
        "with_without_delta": {
            "n_improved": improved,
            "n_regressed": regressed,
        },
    },
    integration_failures=integration_failures,
    created_at=now_iso(),
)
```

#### 3. 新增 Spreadsheet strict gate helper

位置：`academic/benchmarks/spreadsheet/maintenance/adapter.py:1303-1339`

改动目的：在 LLM replay 前验证 case 是否有可复现的 official workbook snapshot，包括 `task_fragment`、`input_xlsx`、`golden_xlsx`、`answer_position`、路径存在性、answer range 可解析性。

代码片段：

```python
def _spreadsheet_bundle_strict_gate(case: SkillBundleCase) -> Dict[str, Any]:
    failures: List[Dict[str, Any]] = []
    task_fragment = dict((case.context or {}).get("task_fragment") or {})
    if not task_fragment:
        failures.append({"type": "missing_task_fragment", "reason": "bundle case has no replayable task_fragment"})
    expected = copy.deepcopy(dict(task_fragment.get("expected") or {}))
    input_artifacts = copy.deepcopy(dict(task_fragment.get("input_artifacts") or {}))
    metadata = copy.deepcopy(dict(task_fragment.get("metadata") or {}))
    task = BenchmarkTask(
        benchmark="spreadsheet",
        task_id=str(task_fragment.get("task_id") or case.case_id),
        question=task_fragment.get("question") or case.prompt,
        expected=expected,
        input_artifacts=input_artifacts,
        metadata=metadata,
    )
    if not input_artifacts.get("input_xlsx"):
        failures.append({"type": "missing_input_xlsx", "reason": "bundle case task_fragment lacks input workbook path"})
    if not expected.get("golden_xlsx"):
        failures.append({"type": "missing_golden_xlsx", "reason": "bundle case task_fragment lacks golden workbook path"})
    if not expected.get("answer_position"):
        failures.append({"type": "missing_answer_position", "reason": "bundle case task_fragment lacks answer range"})
    for label, path_value in (
        ("input_xlsx", input_artifacts.get("input_xlsx")),
        ("golden_xlsx", expected.get("golden_xlsx")),
    ):
        if path_value and not Path(str(path_value)).exists():
            failures.append({"type": f"{label}_not_found", "path": str(path_value)})
    if expected.get("answer_position"):
        try:
            refs = _answer_range_refs(str(expected.get("answer_position")), default_sheet=_first_sheet_name(expected.get("answer_sheet")))
        except Exception as exc:
            failures.append({"type": "invalid_answer_range", "reason": type(exc).__name__})
        else:
            if not refs:
                failures.append({"type": "empty_answer_range", "reason": "answer range produced no replay cells"})
    return {"task": task, "failures": failures}
```

#### 4. Gate failure run payload

位置：`academic/benchmarks/spreadsheet/maintenance/adapter.py:1342-1377`

代码片段：

```python
def _spreadsheet_bundle_gate_failure_run(
    *,
    case: SkillBundleCase,
    task: BenchmarkTask | None,
    variant: str,
    failures: Sequence[Dict[str, Any]],
    artifact: SkillArtifact,
) -> SkillTestCaseRun:
    return SkillTestCaseRun(
        case_id=case.case_id,
        variant=variant,
        passed=False,
        accuracy=0.0,
        validity=False,
        failure_summary=json.dumps(list(failures), ensure_ascii=False),
        trace_ref=(task.task_id if task is not None else case.case_id),
        input_payload={
            "task": task.as_dict() if task is not None else {},
            "variant": variant,
            "llm_test_scope": "spreadsheet_strict_gate",
        },
        expected_behavior={
            "bundle_case_expected": copy.deepcopy(case.expected or {}),
            "contrast_protocol": copy.deepcopy(case.contrast_protocol or {}),
            "polarity": case.polarity,
        },
        skill_snapshot={"name": artifact.name, "version": artifact.version},
        bundle_case_snapshot=case.as_dict(),
        metadata={
            "polarity": case.polarity,
            "source": case.source,
            "contract_passed": False,
            "contract_failures": copy.deepcopy(list(failures)),
            "strict_gate": True,
        },
    )
```

#### 5. with/without case run payload

位置：`academic/benchmarks/spreadsheet/maintenance/adapter.py:1380-1428`

代码片段：

```python
def _spreadsheet_bundle_case_run(
    *,
    case: SkillBundleCase,
    task: BenchmarkTask,
    artifact: SkillArtifact,
    result: BenchmarkResult,
    variant: str,
    top_k_skills: int,
    skill_injection_mode: str,
) -> SkillTestCaseRun:
    metrics = dict(result.metrics or {})
    passed = bool(result.success)
    return SkillTestCaseRun(
        case_id=case.case_id,
        variant=variant,
        passed=passed,
        accuracy=float(result.score or 0.0),
        validity=passed,
        tokens=int(metrics.get("total_tokens") or 0),
        failure_summary="" if passed else str(result.error or metrics.get("reason") or metrics.get("mismatched_cells") or "failed"),
        trace_ref=result.task_id,
        trace=copy.deepcopy(result.trace),
        input_payload={
            "task": task.as_dict(),
            "variant": variant,
            "top_k_skills": top_k_skills,
            "skill_injection_mode": skill_injection_mode,
            "llm_test_scope": "spreadsheet_with_before_without_counterfactual",
        },
        expected_behavior={
            "bundle_case_expected": copy.deepcopy(case.expected or {}),
            "task_expected": copy.deepcopy(task.expected or {}),
            "contrast_protocol": copy.deepcopy(case.contrast_protocol or {}),
            "polarity": case.polarity,
        },
        actual_output={"metrics": metrics, "score": result.score, "success": result.success},
        trace_summary=_spreadsheet_trace_projection(
            {"task_id": result.task_id, "task": task.as_dict(), "runs": [result.as_dict()]}
        ),
        skill_snapshot={"name": artifact.name, "version": artifact.version} if variant == "with_skill" else {},
        bundle_case_snapshot=case.as_dict(),
```

#### 6. 测试：existing replay now records with/without

位置：`academic/benchmarks/tests/test_spreadsheet_evolution.py:986-1036`

代码片段：

```python
result = await _execute_spreadsheet_bundle_tests(
    artifact=skill,
    config=MaintenanceRunConfig(llm_config="mock", model_name="mock-model"),
)

assert result.aggregate["passed"] is True
assert result.aggregate["avg_accuracy"] == 1.0
assert [run.variant for run in result.unit_case_runs] == ["with_skill", "without_skill"]
assert result.unit_case_runs[0].tokens == 30
assert result.aggregate["with_before_without"] is True
assert result.counterfactual["with_skill_valid_by_task"] == {"sheet_bundle_1": True}
assert result.counterfactual["without_skill_valid_by_task"] == {"sheet_bundle_1": True}
```

#### 7. 测试：gate fail 不调用 LLM

位置：`academic/benchmarks/tests/test_spreadsheet_evolution.py:1039-1083`

代码片段：

```python
async def test_spreadsheet_bundle_strict_gate_rejects_missing_paths_before_llm(monkeypatch) -> None:
    skill = SkillArtifact(
        name="spreadsheet_strict_gate_skill",
        kind="workflow_guardrail_card",
        description="Gate test skill.",
        body="Use openpyxl carefully.",
        metadata={"domains": ["SpreadsheetBench"], "intent_keywords": ["double"]},
    )
    skill.bundle.positive_cases.append(
        SkillBundleCase(
            case_id="strict_gate_missing_paths",
            source="manual",
            prompt="Double A1 into B1.",
            expected={"verifier": "spreadsheet_golden_range"},
            context={
                "task_fragment": {
                    "task_id": "strict_gate_missing_paths",
                    "question": "Double A1 into B1.",
                    "expected": {"answer_sheet": "Sheet1", "answer_position": "B1"},
                    "input_artifacts": {},
                    "metadata": {},
                }
            },
            polarity="positive",
        )
    )
    calls: List[str] = []
```

```python
result = await _execute_spreadsheet_bundle_tests(
    artifact=skill,
    config=MaintenanceRunConfig(llm_config="mock", model_name="mock-model"),
)

assert calls == []
assert result.aggregate["passed"] is False
assert result.aggregate["strict_contract_gate"] is True
assert result.aggregate["n_strict_failures"] == 1
assert result.unit_case_runs[0].variant == "strict_gate"
assert result.unit_case_runs[0].metadata["contract_failures"][0]["type"] == "missing_input_xlsx"
```

#### 8. 测试：with fail 不跑 without

位置：`academic/benchmarks/tests/test_spreadsheet_evolution.py:1086-1136`

代码片段：

```python
result = await _execute_spreadsheet_bundle_tests(
    artifact=skill,
    config=MaintenanceRunConfig(llm_config="mock", model_name="mock-model"),
)

assert calls == ["llm"]
assert [run.variant for run in result.unit_case_runs] == ["with_skill"]
assert result.unit_case_runs[0].passed is False
assert result.aggregate["passed"] is False
assert result.counterfactual["without_skill_valid_by_task"] == {}
```

#### 9. 测试：with pass 才跑 without

位置：`academic/benchmarks/tests/test_spreadsheet_evolution.py:1139-1190`

代码片段：

```python
result = await _execute_spreadsheet_bundle_tests(
    artifact=skill,
    config=MaintenanceRunConfig(llm_config="mock", model_name="mock-model"),
)

assert [run.variant for run in result.unit_case_runs] == ["with_skill", "without_skill"]
assert injected_by_call == [[skill.name], []]
assert result.aggregate["passed"] is True
assert result.aggregate["n_comparable_cases"] == 1
```

## Chapter 4: Bundle case 固定预算

目标：Spreadsheet 训练期每个 skill 只保留少量 active replay cases。

实现要点：
- `SPREADSHEET_BUNDLE_CASE_LIMIT_PER_POLARITY=1`。
- `SPREADSHEET_BUNDLE_MAX_TOTAL_CASES=2`。
- 超额 case 进入 audit/evidence ledger。
- 优先保留 recent negative/integration、高 confidence、regression。

测试：
- 超额 positive 被 trim。
- high-confidence negative 优先保留。
- dropped cases 可追溯。

### Chapter 4 维护记录

状态：跳过。

跳过原因：这个章节原计划会固定裁剪 active replay cases，但当前算法需要对所有 skill 的 credit 和 replay 责任链负责。训练期直接丢弃超额 case 会让后续 credit/refine/macro 无法追溯所有相关证据，因此不实现本章的固定 active case budget。后续如果需要降成本，应改成“执行调度预算”或“分层采样 replay”，而不是删除/裁剪责任证据。

## Chapter 5: Compact skill projection 与 prompt schema

目标：credit/refiner 不再读取无关长 skill body，降低 prompt 长度和乱填字段；但必须保留必要实现内容，尤其 executable skill 的完整可执行代码不能丢。

实现要点：
- 新增 Spreadsheet compact skill card。
- credit/refiner 使用同一投影策略。
- workflow/guardrail 长 body 压缩到 compact body。
- executable skill 若包含可执行代码且 body 不超过安全阈值，保留完整 body/code。
- refiner 使用 compact prompt artifact，但 apply refine payload 时仍作用到原始 artifact，避免原 skill body 被预先裁剪。
- prompt schema 明确 `body_projection` 语义，要求 LLM 不要对隐藏 body 乱猜。

测试：
- 超长 workflow body 被压缩。
- executable skill 的完整代码在 projection 中保留。
- refiner 看到 compact body，但原 artifact 更新时不丢原始 body。

### Chapter 5 维护记录

提交：待提交。

状态：已完成，测试通过。

验证命令：

```bash
pytest -q academic/benchmarks/tests/test_spreadsheet_evolution.py

pytest -q \
  academic/benchmarks/tests/test_benchmark_refactor_contracts.py \
  academic/benchmarks/tests/test_common_maintenance_core.py \
  academic/benchmarks/tests/test_skill_injector_budget.py

python -m py_compile \
  academic/benchmarks/spreadsheet/trace_projection.py \
  academic/benchmarks/spreadsheet/maintenance/adapter.py \
  academic/benchmarks/spreadsheet/prompts.py \
  academic/benchmarks/spreadsheet/adapter.py \
  academic/benchmarks/tests/test_spreadsheet_evolution.py
```

结果：`25 passed`、`25 passed`，`py_compile` 通过。pytest 只出现已有 pytest config / pydantic deprecation warnings。

#### 1. `spreadsheet_skill_projection()` 使用 compact card

位置：`academic/benchmarks/spreadsheet/trace_projection.py:103-133`

代码片段：

```python
def spreadsheet_skill_projection(
    artifact: SkillArtifact,
    *,
    projection: Dict[str, Any],
) -> Dict[str, Any]:
    exposure = skill_exposure_flags(artifact.name, projection)
    compact = compact_spreadsheet_skill_card(artifact)
    return {
        "skill_name": artifact.name,
        "version": artifact.version,
        "kind": artifact.kind,
        "status": artifact.status,
        "description": artifact.description,
        "body": compact["body"],
        "body_projection": compact,
        "interface": artifact.interface.as_dict(),
        "metadata": {
            "domains": artifact.metadata.get("domains") or [],
            "allowed_tools": artifact.metadata.get("allowed_tools") or [],
            "intent_keywords": artifact.metadata.get("intent_keywords") or [],
            "source_task_ids": artifact.metadata.get("source_task_ids") or [],
            "non_applicability": artifact.metadata.get("non_applicability"),
        },
        "retrieved": exposure["retrieved"],
        "injected": exposure["injected"],
        "used": exposure["used"],
        "usage_count": artifact.usage_count,
        "success_count": artifact.success_count,
        "recent_helpful": artifact.evidence.helpful_cases[-3:],
        "recent_harmful": artifact.evidence.harmful_cases[-3:],
    }
```

#### 2. Compact card 保留 executable code

位置：`academic/benchmarks/spreadsheet/trace_projection.py:136-176`

代码片段：

```python
def compact_spreadsheet_skill_card(artifact: SkillArtifact) -> Dict[str, Any]:
    body = str(artifact.body or "")
    executable_code = _spreadsheet_executable_code_block(body)
    include_full_body = bool(executable_code) and len(body) <= 6000
    projected_body = body if include_full_body else _compact_spreadsheet_body_text(body)
    return {
        "projection_kind": "full_executable_body" if include_full_body else "compact_body",
        "body": projected_body,
        "body_chars": len(body),
        "body_truncated": projected_body != body,
        "executable_code": executable_code,
        "executable_code_chars": len(executable_code),
        "executable_code_preserved": bool(executable_code) and executable_code in projected_body,
        "applicability": _extract_spreadsheet_body_section(body, "Applicability"),
        "non_applicability": artifact.metadata.get("non_applicability")
        or _extract_spreadsheet_body_section(body, "Non-applicability"),
    }


def _spreadsheet_executable_code_block(body: str) -> str:
    match = re.search(r"```(?:python)?\s*(.*?)```", str(body or ""), re.S | re.I)
    if match:
        return match.group(1).strip()
    text = str(body or "").strip()
    if any(marker in text for marker in ("INPUT_XLSX", "OUTPUT_XLSX", "openpyxl", "load_workbook(")):
        return text
    return ""
```

#### 3. Refiner prompt 保留完整 artifact body

位置：`academic/benchmarks/spreadsheet/maintenance/adapter.py:1069-1127`

代码片段：

```python
prompt_artifact = _spreadsheet_refiner_prompt_artifact(artifact)
try:
    payload = await _compat_refine_skill_artifact_llm(
        prompt_artifact,
        test_result=test_result,
        credit_context=list(credit_context),
        refinement_history=artifact.history[-3:],
        dependency_summaries=summarize_dependency_context(store.all()),
        llm_config=config.llm_config,
        model_name=config.model_name,
        audit_context={"phase": phase, "benchmark": "spreadsheet"},
    )
```

```python
def _spreadsheet_refiner_prompt_artifact(artifact: SkillArtifact) -> SkillArtifact:
    view = copy.deepcopy(artifact)
    compact = _compact_spreadsheet_skill_card(artifact)
    view.metadata = {
        **dict(view.metadata or {}),
        "spreadsheet_compact_projection": {
            "projection_kind": compact.get("projection_kind"),
            "body_chars": compact.get("body_chars"),
            "body_truncated": False,
            "prompt_body_preserved": True,
            "executable_code_chars": compact.get("executable_code_chars"),
            "executable_code_preserved": compact.get("executable_code_preserved"),
        },
    }
    return view
```

注意：refiner prompt artifact 现在也保留完整 `artifact.body`；只在 metadata 中记录 body 长度和 executable code 统计。`apply_refine_payload(artifact, payload)` 仍然使用原始 `artifact`。

#### 4. Credit prompt 说明 `body_projection`

位置：`academic/benchmarks/spreadsheet/prompts.py:143-150`

代码片段：

```python
- `candidate_skills[].body_projection`: compact wrapper around the skill body.
  The `body`, `code`, `code_preview`, and `executable_code` fields are never
  truncated; use the complete shown implementation/body for attribution.
```

#### 5. 测试：compact projection 不丢 executable code

位置：`academic/benchmarks/tests/test_spreadsheet_evolution.py:612-648`

代码片段：

```python
workflow_view = _spreadsheet_skill_projection(workflow, projection=projection)
executable_view = _spreadsheet_skill_projection(executable, projection=projection)

assert workflow_view["body_projection"]["body_truncated"] is False
assert workflow_view["body_projection"]["body"] == long_guidance
assert workflow_view["body_projection"]["code_preview"] == ""
assert executable_view["body_projection"]["projection_kind"] == "full_executable_body"
assert code.strip() in executable_view["body_projection"]["code_preview"]
```

#### 6. 测试：refiner prompt 和原 artifact body 都保留

位置：`academic/benchmarks/tests/test_spreadsheet_evolution.py:651-694`

代码片段：

```python
async def fake_refine(artifact: SkillArtifact, **kwargs: Any) -> Dict[str, Any]:
    captured["body"] = artifact.body
    captured["metadata"] = dict(artifact.metadata)
    return {
        "decision": {"action": "refine_minor", "reason": "append caveat", "version_kind": "minor"},
        "artifact": {"metadata": {"refined": True}},
        "bundle": {},
    }
```

```python
assert captured["body"] == original_body
assert captured["metadata"]["spreadsheet_compact_projection"]["body_truncated"] is False
assert captured["metadata"]["spreadsheet_compact_projection"]["prompt_body_preserved"] is True
assert decision["updated_artifact"].body == original_body
assert decision["updated_artifact"].metadata["refined"] is True
```

## Chapter 6: Notebook trace projection

目标：维护 agent 只读 notebook 关键执行信号，不读完整多轮 trace。

实现要点：
- projection 保留所有 notebook step，但每个 step 只保留 compact 后的 raw execution signal：code snippet、stdout tail、stderr tail。
- `exception` 是唯一保留的派生字段，用于让维护 agent 直接看到最终 Python 异常。
- 长 Python stderr/traceback 不做普通尾截断；优先截到最后一个 traceback frame 和最终异常行，避免噪声淹没真正报错。
- 不加入 `changed_variables`、`final_answer_candidate`、`returncode`、`done_requested`、`called_skill_functions` 等额外字段。
- expected range 继续通过已有 task/metrics projection 暴露，不重复塞进每个 notebook step。

测试：
- 多轮 trace 被压缩。
- exception/stderr 保留。
- 所有 notebook step 都保留。
- projection 不包含 `changed_variables`。

### Chapter 6 维护记录

提交：待提交。

状态：已完成，测试通过。

验证命令：

```bash
pytest -q academic/benchmarks/tests/test_spreadsheet_evolution.py

python -m py_compile \
  academic/benchmarks/spreadsheet/trace_projection.py \
  academic/benchmarks/spreadsheet/adapter.py \
  academic/benchmarks/tests/test_spreadsheet_evolution.py
```

结果：`26 passed`，`py_compile` 通过。pytest 只出现已有 pytest config / pydantic deprecation warnings。

#### 1. `spreadsheet_trace_projection()` 接入 notebook step projection

位置：`academic/benchmarks/spreadsheet/trace_projection.py:11-36`

代码片段：

```python
def spreadsheet_trace_projection(detail: Dict[str, Any]) -> Dict[str, Any]:
    runs = detail.get("runs") or []
    first = runs[0] if runs else {}
    metrics = first.get("metrics") or {}
    trace = first.get("trace") or {}
    exposure = skill_exposure_from_mappings(metrics, trace)
    notebook_steps = spreadsheet_notebook_steps_projection(trace)
    return {
        "task_id": detail.get("task_id"),
        "success": first.get("success"),
        "score": first.get("score"),
        "answer_sheet": metrics.get("answer_sheet"),
        "answer_position": metrics.get("answer_position"),
        "checked_cells": metrics.get("checked_cells"),
        "mismatched_cells": metrics.get("mismatched_cells", [])[:5],
        "execution_ok": metrics.get("execution_ok"),
        "llm_api_style": metrics.get("llm_api_style"),
        "retrieved_skills": exposure["retrieved_skills"],
        "prompt_injected_skills": exposure["prompt_injected_skills"],
        "callable_skills": metrics.get("callable_skills") or trace.get("callable_skills") or [],
        "called_skill_functions": exposure["called_skill_functions"],
        "retrieved_only_skills": exposure["retrieved_only_skills"],
        "stderr_tail": compact_spreadsheet_stderr(trace.get("stderr") or "", limit=800),
        "notebook_step_count": len(notebook_steps),
        "notebook_steps": notebook_steps,
    }
```

#### 2. 新增 notebook step compact projection

位置：`academic/benchmarks/spreadsheet/trace_projection.py:79-108`

代码片段：

```python
def spreadsheet_notebook_steps_projection(trace: Dict[str, Any]) -> List[Dict[str, Any]]:
    steps: List[Dict[str, Any]] = []
    for index, turn in enumerate(trace.get("notebook_turns") or []):
        if not isinstance(turn, dict):
            steps.append(
                {
                    "code_snippet": "",
                    "stdout_tail": "",
                    "stderr_tail": _tail_text(turn, limit=1000),
                    "exception": "",
                }
            )
            continue
        stderr = str(turn.get("stderr") or "")
        step = {
            "code_snippet": spreadsheet_code_snippet(turn.get("code") or "", limit=1200),
            "stdout_tail": _tail_text(turn.get("stdout") or "", limit=600),
            "stderr_tail": compact_spreadsheet_stderr(stderr, limit=1000),
            "exception": _python_exception_line(stderr),
        }
        steps.append(step)
    return steps
```

注意：这里有意不输出 `changed_variables`、`returncode`、`done_requested`、`called_skill_functions`。所有原始 `notebook_turns` entry 都会进入 projection：标准 dict step 走正常 compact projection；非 dict/异常形态 step 也会保留一个占位 projection entry，不静默丢弃。

#### 3. 长 traceback 优先保留最后一个 frame

位置：`academic/benchmarks/spreadsheet/trace_projection.py:99-128`

代码片段：

```python
def compact_spreadsheet_stderr(stderr: Any, *, limit: int = 1200) -> str:
    text = str(stderr or "").strip()
    if len(text) <= limit:
        return text
    traceback_start = text.rfind("Traceback (most recent call last):")
    traceback_text = text[traceback_start:] if traceback_start >= 0 else text
    lines = traceback_text.splitlines()
    frame_indices = [i for i, line in enumerate(lines) if line.lstrip().startswith("File ")]
    if frame_indices:
        final_frame_start = frame_indices[-1]
        snippet = "\n".join(lines[final_frame_start:]).strip()
        if len(snippet) <= limit:
            return "[stderr truncated to final traceback frame]\n" + snippet
        return "[stderr truncated to final traceback frame]\n" + _tail_text(snippet, limit=limit)
    return _tail_text(text, limit=limit)
```

```python
def _python_exception_line(stderr: Any) -> str:
    lines = [line.strip() for line in str(stderr or "").splitlines() if line.strip()]
    for line in reversed(lines):
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*(?:Error|Exception|Warning|Exit|Interrupt)\b", line):
            return line
    return ""
```

#### 4. `spreadsheet_result_projection()` 暴露 notebook projection

位置：`academic/benchmarks/spreadsheet/trace_projection.py:131-158`

代码片段：

```python
def spreadsheet_result_projection(detail: Dict[str, Any]) -> Dict[str, Any]:
    run = dict((detail.get("runs") or [{}])[0] or {})
    trace = dict(run.get("trace") or {})
    metrics = dict(run.get("metrics") or {})
    notebook_steps = spreadsheet_notebook_steps_projection(trace)
    return {
        "task": spreadsheet_task_fragment(detail),
        "success": run.get("success"),
        "score": run.get("score"),
        "metrics": {
            "answer_sheet": metrics.get("answer_sheet"),
            "answer_position": metrics.get("answer_position"),
            "cell_accuracy": metrics.get("cell_accuracy"),
            "checked_cells": metrics.get("checked_cells"),
            "mismatched_cells": (metrics.get("mismatched_cells") or [])[:8],
            "execution_ok": metrics.get("execution_ok"),
            "returncode": metrics.get("returncode"),
            "total_tokens": metrics.get("total_tokens"),
        },
        "trace": {
            "retrieved_skills": trace.get("retrieved_skills") or metrics.get("retrieved_skills") or [],
            "code_snippet": spreadsheet_code_snippet(trace.get("code") or ""),
            "stderr_tail": compact_spreadsheet_stderr(trace.get("stderr") or "", limit=1200),
            "stdout_tail": _tail_text(trace.get("stdout") or "", limit=800),
            "notebook_step_count": len(notebook_steps),
            "notebook_steps": notebook_steps,
        },
    }
```

#### 5. 测试：所有 step 保留，长 traceback 保留最后 frame，不输出 changed variables

位置：`academic/benchmarks/tests/test_spreadsheet_evolution.py:363-432`

代码片段：

```python
result_projection = _spreadsheet_result_projection(detail)
trace_projection = _spreadsheet_trace_projection(detail)
steps = result_projection["trace"]["notebook_steps"]

assert result_projection["trace"]["notebook_step_count"] == 4
assert [set(step) for step in steps] == [
    {"code_snippet", "stdout_tail", "stderr_tail", "exception"},
    {"code_snippet", "stdout_tail", "stderr_tail", "exception"},
    {"code_snippet", "stdout_tail", "stderr_tail", "exception"},
    {"code_snippet", "stdout_tail", "stderr_tail", "exception"},
]
assert "final_cell.py" in steps[1]["stderr_tail"]
assert "NameError: name 'missing_name' is not defined" in steps[1]["stderr_tail"]
assert "/tmp/noisy_0.py" not in steps[1]["stderr_tail"]
assert steps[1]["exception"] == "NameError: name 'missing_name' is not defined"
assert steps[2]["stderr_tail"] == "raw malformed notebook step"
assert all("changed_variables" not in step for step in steps)
assert trace_projection["notebook_step_count"] == 4
assert "final_cell.py" in trace_projection["stderr_tail"]
```

## Chapter 7: Generic runner 并发下沉

目标：Spreadsheet 训练使用 benchmark-agnostic window concurrency 和 skill/dependency-neighborhood locks。

实现要点：
- core runner 增加 window concurrency、micro concurrency、skill/dependency-neighborhood write lock。
- Spreadsheet adapter 只声明 target skills/dependency neighborhood。
- macro 仍在 window barrier 后执行。

测试：
- 12 个独立 skill 同时进入 micro refine，mock sleep 下比较串行/并行 wall-clock 加速。
- 同 skill refine 串行，不同 skill 并发。
- dependency 变化标 stale。

### Chapter 7 维护记录

提交：待提交。

状态：已完成，测试通过。

验证命令：

```bash
pytest -q \
  academic/benchmarks/tests/test_generic_evolution.py \
  academic/benchmarks/tests/test_spreadsheet_evolution.py

pytest -q \
  academic/benchmarks/tests/test_benchmark_refactor_contracts.py \
  academic/benchmarks/tests/test_common_maintenance_core.py \
  academic/benchmarks/tests/test_skill_injector_budget.py

python -m py_compile \
  academic/benchmarks/core/evolution.py \
  academic/benchmarks/core/maintenance_adapter.py \
  academic/benchmarks/core/runner.py \
  academic/benchmarks/spreadsheet/maintenance/adapter.py \
  academic/benchmarks/spreadsheet/trace_projection.py \
  academic/benchmarks/tests/test_generic_evolution.py \
  academic/benchmarks/tests/test_spreadsheet_evolution.py
```

结果：`31 passed`、`25 passed`，后续补充 wall-clock 加速测试后 `test_generic_evolution.py` 为 `6 passed`；`py_compile` 通过。pytest 只出现已有 pytest config / pydantic deprecation warnings。

#### 1. Generic config 增加 train/micro concurrency

位置：`academic/benchmarks/core/maintenance_adapter.py:23-35`

代码片段：

```python
@dataclass
class MaintenanceRunConfig:
    """Shared runtime knobs for a benchmark-agnostic evolution run."""

    llm_config: str
    model_name: str | None = None
    tag: str = "evolve"
    n_train_runs: int = 1
    n_test_runs: int = 1
    micro_maintenance_step: int = 1
    macro_maintenance_step: int = 10
    train_concurrency: int = 1
    micro_maintenance_concurrency: int = 1
    test_concurrency: int = 1
    max_task_seconds: float | None = None
    top_k_skills: int = 5
    extra: Dict[str, Any] = field(default_factory=dict)
```

默认值均为 `1`，因此现有实验不显式传参时仍走原来的串行训练/维护语义。

#### 2. Adapter contract 增加维护写锁声明

位置：`academic/benchmarks/core/maintenance_adapter.py:223-230`、`academic/benchmarks/core/maintenance_adapter.py:318-326`

代码片段：

```python
def maintenance_lock_names(
    self,
    *,
    detail: Dict[str, Any],
    store: ArtifactStore,
    config: MaintenanceRunConfig,
) -> List[str]:
    """Return skill/dependency names that must be write-locked for this detail."""
```

```python
def maintenance_lock_names(
    self,
    *,
    detail: Dict[str, Any],
    store: ArtifactStore,
    config: MaintenanceRunConfig,
) -> List[str]:
    del detail, store, config
    return []
```

#### 3. Runner 默认仍走旧串行路径

位置：`academic/benchmarks/core/evolution.py:47-64`

代码片段：

```python
for round_index in range(effective_rounds):
    if (
        max(1, int(self.config.train_concurrency or 1)) <= 1
        and max(1, int(self.config.micro_maintenance_concurrency or 1)) <= 1
    ):
        await self._run_serial_train_round(
            train_tasks=train_tasks,
            store=store,
            train_details=train_details,
            skill_credit_events=skill_credit_events,
            micro_reports=micro_reports,
            maintenance_windows=maintenance_windows,
            macro_skill_snapshots=macro_skill_snapshots,
            round_index=round_index,
            micro_step=micro_step,
            macro_step=macro_step,
        )
        continue
```

#### 4. 并发路径按 macro window 分批，macro 保持 barrier

位置：`academic/benchmarks/core/evolution.py:65-108`

代码片段：

```python
for window_start in range(0, len(train_tasks), macro_step):
    window_tasks = list(enumerate(train_tasks))[window_start: window_start + macro_step]
    final_window = window_start + macro_step >= len(train_tasks)
    window_store = self._copy_store(store)
    window_details = await self._run_train_window_tasks(
        window_tasks,
        store=window_store,
        round_index=round_index,
    )
    train_details.extend(window_details)

    window_credit_events: List[Dict[str, Any]] = []
    window_micro_reports = await self._run_window_maintenance(
        window_details=window_details,
        store=store,
        round_index=round_index,
        micro_step=micro_step,
    )
    for item in window_micro_reports:
        window_credit_events.extend(copy.deepcopy(item.pop("_credit_events", [])))
    skill_credit_events.extend(copy.deepcopy(window_credit_events))
    micro_reports.extend(window_micro_reports)

    macro_report = await self.adapter.run_macro_maintenance(
        window_details=window_details,
        all_train_details=train_details,
        credit_events=skill_credit_events,
        store=store,
        config=self.config,
        round_index=round_index,
        window_index=len(maintenance_windows),
        final_window=final_window,
    )
```

说明：窗口内 rollout 使用 `window_store` snapshot，避免并发任务读取半更新 store；macro 只在整个 window 的 rollout 和 micro 之后执行。

#### 5. Window rollout 并发执行且保持输出顺序

位置：`academic/benchmarks/core/evolution.py:239-278`

代码片段：

```python
async def _run_train_window_tasks(
    self,
    indexed_tasks: Sequence[tuple[int, Any]],
    *,
    store: ArtifactStore,
    round_index: int,
) -> List[Dict[str, Any]]:
    concurrency = max(1, int(self.config.train_concurrency or 1))
    if concurrency <= 1 or len(indexed_tasks) <= 1:
        return [
            await self._run_task_repeats(
                task,
                store=store,
                phase=f"{self.adapter.benchmark}_train_round_{round_index}",
                task_index=task_index,
                n_runs=max(1, int(self.config.n_train_runs or 1)),
            )
            for task_index, task in indexed_tasks
        ]

    sem = asyncio.Semaphore(concurrency)

    async def guarded(task_index: int, task: Any) -> tuple[int, Dict[str, Any]]:
        task_store = self._copy_store(store)
        async with sem:
            return (
                task_index,
                await self._run_task_repeats(
                    task,
                    store=task_store,
                    phase=f"{self.adapter.benchmark}_train_round_{round_index}",
                    task_index=task_index,
                    n_runs=max(1, int(self.config.n_train_runs or 1)),
                ),
            )

    completed = await asyncio.gather(
        *[guarded(task_index, task) for task_index, task in indexed_tasks]
    )
    return [detail for _, detail in sorted(completed, key=lambda item: item[0])]
```

#### 6. Micro maintenance 并发使用 skill/dependency-neighborhood lock

位置：`academic/benchmarks/core/evolution.py:280-335`

代码片段：

```python
async def _run_window_maintenance(
    self,
    *,
    window_details: Sequence[Dict[str, Any]],
    store: ArtifactStore,
    round_index: int,
    micro_step: int,
) -> List[Dict[str, Any]]:
    concurrency = max(1, int(self.config.micro_maintenance_concurrency or 1))
    if concurrency <= 1 or len(window_details) <= 1:
        reports: List[Dict[str, Any]] = []
        for detail in window_details:
            reports.append(
                await self._run_one_task_maintenance(
                    detail=detail,
                    store=store,
                    round_index=round_index,
                    micro_step=micro_step,
                )
            )
        return reports

    locks: Dict[str, asyncio.Lock] = {}

    async def guarded(detail: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        task_index = _detail_task_index(detail)
        lock_names = _unique_names(
            self.adapter.maintenance_lock_names(
                detail=detail,
                store=store,
                config=self.config,
            )
        )
        lock_names = _dependency_lock_neighborhood(store, lock_names)
        acquired = [locks.setdefault(name, asyncio.Lock()) for name in lock_names]
        for lock in sorted(acquired, key=id):
            await lock.acquire()
        try:
            report = await self._run_one_task_maintenance(
                detail=detail,
                store=store,
                round_index=round_index,
                micro_step=micro_step,
            )
        finally:
            for lock in reversed(sorted(acquired, key=id)):
                lock.release()
        return task_index, report

    sem = asyncio.Semaphore(concurrency)

    async def bounded(detail: Dict[str, Any]) -> tuple[int, Dict[str, Any]]:
        async with sem:
            return await guarded(detail)

    completed = await asyncio.gather(*[bounded(detail) for detail in window_details])
    return [report for _, report in sorted(completed, key=lambda item: item[0])]
```

#### 7. Generic dependency-neighborhood lock helper

位置：`academic/benchmarks/core/evolution.py:514-542`

代码片段：

```python
def _detail_task_index(detail: Dict[str, Any]) -> int:
    try:
        return int(detail.get("task_index"))
    except Exception:
        return 0


def _credit_skill_names(credit_events: Sequence[Dict[str, Any]]) -> List[str]:
    return _unique_names(event.get("skill_name") for event in credit_events or [])


def _unique_names(values: Sequence[Any]) -> List[str]:
    names: List[str] = []
    seen = set()
    for item in values or []:
        value = str(item or "").strip()
        if value and value not in seen:
            names.append(value)
            seen.add(value)
    return names


def _dependency_lock_neighborhood(store: ArtifactStore, names: Sequence[str]) -> List[str]:
    locked = set(_unique_names(names))
    if not locked:
        return []
    changed = True
    while changed:
        changed = False
        for artifact in store.all():
            deps = {str(item) for item in (artifact.dependencies or [])}
            if artifact.name in locked or not deps.intersection(locked):
                continue
            locked.add(artifact.name)
            changed = True
    return sorted(locked)
```

说明：如果一个 detail 要写上游 skill，则依赖它的下游 skill 也纳入锁集合；`ArtifactStore.add()` 已负责在上游更新时把 dependents 标 stale。

#### 8. Spreadsheet adapter 声明维护锁目标

位置：`academic/benchmarks/spreadsheet/maintenance/adapter.py:372-381`

代码片段：

```python
def maintenance_lock_names(
    self,
    *,
    detail: Dict[str, Any],
    store: ArtifactStore,
    config: MaintenanceRunConfig,
) -> List[str]:
    del store, config
    projection = _spreadsheet_trace_projection(detail)
    return credit_candidate_skill_names(projection)
```

#### 9. CLI 暴露并发参数并传给 Spreadsheet evolve

位置：`academic/benchmarks/core/runner.py:96-100`、`academic/benchmarks/core/runner.py:364-380`

代码片段：

```python
parser.add_argument("--n-runs", type=int, default=1)
parser.add_argument("--n-train-runs", type=int, default=1)
parser.add_argument("--train-concurrency", type=int, default=1)
parser.add_argument("--micro-maintenance-concurrency", type=int, default=1)
parser.add_argument("--test-concurrency", type=int, default=1)
```

```python
config=MaintenanceRunConfig(
    llm_config=args.llm_config,
    model_name=args.model_name,
    tag=args.tag,
    n_train_runs=args.n_train_runs,
    n_test_runs=args.n_runs,
    micro_maintenance_step=1,
    macro_maintenance_step=10,
    train_concurrency=args.train_concurrency,
    micro_maintenance_concurrency=args.micro_maintenance_concurrency,
    test_concurrency=args.test_concurrency,
```

#### 10. 测试：默认串行、skill/dependency lock、并行 wall-clock 加速

位置：`academic/benchmarks/tests/test_generic_evolution.py:119-275`

代码片段：

```python
assert adapter.max_active_train == 1
assert adapter.max_active_test == 2
assert [item["task_id"] for item in summary["test_details"]] == [f"test_{idx}" for idx in range(4)]
assert len(summary["skill_credit_events"]) == 3
assert len(summary["micro_maintenance_reports"]) == 3
assert [window["task_ids"] for window in summary["maintenance_windows"]] == [
    ["train_0", "train_1"],
    ["train_2"],
]
```

```python
summary = await runner.run(train_tasks=train, test_tasks=[], seed_store=ArtifactStore())

assert adapter.max_active_train > 1
assert adapter.max_active_micro_total > 1
assert adapter.max_micro_by_skill["skill_a"] == 1
assert adapter.macro_saw_after_window is True
assert [item["task_id"] for item in summary["train_details"]] == [f"train_{idx}" for idx in range(4)]
assert [window["task_ids"] for window in summary["maintenance_windows"]] == [
    ["train_0", "train_1", "train_2", "train_3"],
]
```

```python
await runner.run(train_tasks=train, test_tasks=[], seed_store=store)

assert any(
    skill == "skill_other" or "skill_other" in active
    for skill, active in adapter.overlapping_pairs
)
assert not any(
    (skill == "skill_base" and "skill_child" in active)
    or (skill == "skill_child" and "skill_base" in active)
    for skill, active in adapter.overlapping_pairs
)
```

```python
async def test_generic_micro_refine_parallel_wall_clock_speedup_for_independent_skills() -> None:
    n_tasks = 12
    sleep_s = 0.05
    skill_by_task = {f"train_{idx}": f"skill_{idx}" for idx in range(n_tasks)}
    details = [
        {
            "task_id": f"train_{idx}",
            "task_index": idx,
            "task": {"task_id": f"train_{idx}"},
            "runs": [],
        }
        for idx in range(n_tasks)
    ]

    class SlowRefineAdapter(ConcurrentFakeAdapter):
        async def run_micro_maintenance(self, **kwargs: Any) -> Dict[str, Any]:
            detail = kwargs["detail"]
            skill_name = self.skill_by_task[detail["task_id"]]
            self.active_micro_total += 1
            self.max_active_micro_total = max(self.max_active_micro_total, self.active_micro_total)
            await asyncio.sleep(sleep_s)
            self.active_micro_total -= 1
            return {"phase": "micro", "task_id": detail["task_id"], "maintenance_targets": [skill_name]}
```

```python
serial_s, serial_adapter, serial_reports = await timed_run(1)
parallel_s, parallel_adapter, parallel_reports = await timed_run(6)

assert len(serial_reports) == n_tasks
assert len(parallel_reports) == n_tasks
assert serial_adapter.max_active_micro_total == 1
assert parallel_adapter.max_active_micro_total > 1
assert parallel_s < serial_s * 0.6
```


### Chapter 7.1: Robust resume, LLM timeout, and real speedup run

背景：50/50 notebook speedup run 在第一版并发实现中被单个 task-level `asyncio.wait_for` timeout 杀掉。修复目标是：LLM timeout 发生时转成失败结果继续跑；每个 task 完成后落盘状态，恢复时不重跑已完成 task；输出 token request curve；保持 train=4、micro=8、test=4 不降并发。

#### 1. Generic evolve 从 partial 恢复 train/test/micro/macro/store

位置：`academic/benchmarks/core/evolution.py:54-73`

代码内容：

```python
resume = self._load_partial_state(seed_store=seed_store)
store = resume["store"]
train_details: List[Dict[str, Any]] = list(resume["train_details"])
test_details: List[Dict[str, Any]] = list(resume["test_details"])
skill_credit_events: List[Dict[str, Any]] = list(resume["skill_credit_events"])
micro_reports: List[Dict[str, Any]] = list(resume["micro_reports"])
maintenance_windows: List[Dict[str, Any]] = list(resume["maintenance_windows"])
macro_skill_snapshots: List[Dict[str, Any]] = list(resume["macro_skill_snapshots"])
self._resume_maintenance_events = list(resume.get("maintenance_token_events") or [])
self._partial_state = _EvolutionPartialState(
    train_details=train_details,
    test_details=test_details,
    skill_credit_events=skill_credit_events,
    micro_reports=micro_reports,
    maintenance_windows=maintenance_windows,
    macro_skill_snapshots=macro_skill_snapshots,
    store=store,
)
self._maintenance_token_start_index = maintenance_token_event_count()
self._persist_partial_state(stage="start")
```

#### 2. Resume 时按 window 补齐缺失阶段，不重跑已完成 rollout

位置：`academic/benchmarks/core/evolution.py:97-165`

代码内容：

```python
for window_start in range(0, len(train_tasks), macro_step):
    indexed_window_tasks = list(enumerate(train_tasks))[window_start: window_start + macro_step]
    pending_window_tasks = [
        (task_index, task)
        for task_index, task in indexed_window_tasks
        if not self._detail_done(train_details, round_index=round_index, phase="train", task_index=task_index)
    ]
    final_window = window_start + macro_step >= len(train_tasks)
    if pending_window_tasks:
        window_store = self._copy_store(store)
        await self._run_train_window_tasks(
            pending_window_tasks,
            store=window_store,
            round_index=round_index,
            record_details=train_details,
        )
        self._persist_partial_state(stage="train_window_done")

    window_task_indexes = [task_index for task_index, _ in indexed_window_tasks]
    window_details = [
        detail for detail in train_details
        if _detail_task_index(detail) in window_task_indexes
        and int(detail.get("round_index") or 0) == round_index
        and str(detail.get("evolution_phase") or "train") == "train"
    ]
    window_details.sort(key=lambda item: _detail_task_index(item))
    pending_micro_details = [
        detail for detail in window_details
        if not self._micro_done(micro_reports, task_index=_detail_task_index(detail))
    ]
```

#### 3. Task timeout / exception 转失败结果继续跑

位置：`academic/benchmarks/core/evolution.py:630-697`

代码内容：

```python
try:
    if self.config.max_task_seconds and not self.config.extra.get("disable_task_wall_timeout"):
        result = await asyncio.wait_for(coro, timeout=self.config.max_task_seconds)
    else:
        result = await coro
except asyncio.TimeoutError:
    result = self._failed_task_result(
        task=task,
        phase=phase,
        task_index=task_index,
        run_idx=run_idx,
        reason="task_timeout",
        error=f"Task exceeded {self.config.max_task_seconds} seconds",
    )
except Exception as exc:
    result = self._failed_task_result(
        task=task,
        phase=phase,
        task_index=task_index,
        run_idx=run_idx,
        reason=f"task_exception:{type(exc).__name__}",
        error=str(exc),
    )
```

#### 4. 每个 task 状态和 store 写入 partial

位置：`academic/benchmarks/core/evolution.py:741-774`

代码内容：

```python
payload = {
    "version": 1,
    "benchmark": self.adapter.benchmark,
    "mode": "generic_online_skill_evolve_partial",
    "tag": self.config.tag,
    "stage": stage,
    "completed": bool(completed),
    "updated_at": datetime.now(timezone.utc).isoformat(),
    "config_summary": self.config.as_dict(),
    "task_state": {
        "train": _task_state_rows(state.train_details),
        "test": _task_state_rows(state.test_details),
    },
    "train_details": state.train_details,
    "test_details": state.test_details,
    "skill_credit_events": state.skill_credit_events,
    "micro_maintenance_reports": state.micro_reports,
    "maintenance_windows": state.maintenance_windows,
    "macro_skill_snapshots": [row for row in state.macro_skill_snapshots if row],
    "maintenance_token_events": self._all_maintenance_token_events(),
    "store_snapshot": self.adapter.store_snapshot(state.store),
    "skills": [artifact.as_dict() for artifact in state.store.all()],
}
path.parent.mkdir(parents=True, exist_ok=True)
tmp = path.with_suffix(path.suffix + ".tmp")
tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
tmp.replace(path)
```

#### 5. Spreadsheet CLI 把 timeout 放到 LLM request，并关闭外层 task wall timeout

位置：`academic/benchmarks/core/runner.py:379-388`

代码内容：

```python
extra={
    "skill_injector_mode": args.skill_injector_mode,
    "skill_context_budget_chars": args.skill_context_budget_chars,
    "spreadsheet_execution_mode": args.spreadsheet_execution_mode,
    "spreadsheet_max_turns": args.spreadsheet_max_turns,
    "macro_snapshot_dir": str(args.macro_snapshot_dir) if args.macro_snapshot_dir else "",
    "partial_output": str(args.partial_output) if args.partial_output else "",
    "llm_request_timeout_s": args.max_task_seconds,
    "disable_task_wall_timeout": True,
},
```

位置：`academic/benchmarks/spreadsheet/executor.py:37-47`、`academic/benchmarks/spreadsheet/executor.py:114-120`、`academic/benchmarks/spreadsheet/executor.py:215-225`、`academic/benchmarks/spreadsheet/executor.py:316-322`

代码内容：

```python
llm_request_timeout_s: float | None = None,
```

```python
response = await ask_text_llm(
    llm_config=llm_config,
    model_name=model_name,
    system=system,
    prompt=prompt,
    max_request_wall_s=llm_request_timeout_s,
)
```

#### 6. Token request curve 和完整恢复汇总

位置：`academic/benchmarks/core/cost_accounting.py:187-275`

代码内容：

```python
def summarize_token_request_curve(
    events: Iterable[Dict[str, Any]],
    *,
    bucket_seconds: int = 60,
) -> Dict[str, Any]:
    rows = [copy.deepcopy(dict(event or {})) for event in events or [] if isinstance(event, dict)]
    bucket_s = max(1, int(bucket_seconds or 60))
    parsed: List[tuple[datetime | None, int, Dict[str, Any]]] = []
    for idx, event in enumerate(rows):
        parsed.append((_parse_event_ts(event.get("ts")), idx, event))
    dated = [item for item in parsed if item[0] is not None]
    start = min((item[0] for item in dated if item[0] is not None), default=None)
```

位置：`academic/benchmarks/core/evolution.py:196-225`

代码内容：

```python
maintenance_events = self._all_maintenance_token_events()
maintenance_stats = _summarize_maintenance_token_events(
    maintenance_events,
    start_index=0,
    end_index=len(maintenance_events),
)
maintenance_stats["n_resumed_prior_events"] = len(self._resume_maintenance_events)
token_curve = summarize_token_request_curve(
    [*cost_events, *maintenance_events],
    bucket_seconds=int(self.config.extra.get("token_curve_bucket_seconds") or 60),
)
```

#### 7. Tests

位置：`academic/benchmarks/tests/test_generic_evolution.py:285-322`

代码内容：

```python
async def test_generic_online_runner_converts_task_timeout_to_failed_detail() -> None:
    class TimeoutAdapter(FakeAdapter):
        async def run_task(...):
            if task_index == 0:
                await asyncio.sleep(0.05)
            return BenchmarkResult(...)

    runner = OnlineSkillEvolutionRunner(
        adapter=TimeoutAdapter(),
        config=MaintenanceRunConfig(
            llm_config="fake",
            max_task_seconds=0.01,
            train_concurrency=2,
            micro_maintenance_concurrency=2,
            macro_maintenance_step=2,
        ),
    )
```

位置：`academic/benchmarks/tests/test_generic_evolution.py:325-370`

代码内容：

```python
async def test_generic_online_runner_partial_resume_skips_completed_tasks(tmp_path) -> None:
    partial_path = tmp_path / "partial.json"
    adapter = FakeAdapter()
    runner = OnlineSkillEvolutionRunner(
        adapter=adapter,
        config=MaintenanceRunConfig(
            llm_config="fake",
            tag="resume",
            train_concurrency=2,
            micro_maintenance_concurrency=2,
            macro_maintenance_step=3,
            extra={"partial_output": str(partial_path)},
        ),
    )
```

验证命令：

```bash
python -m py_compile   academic/benchmarks/core/evolution.py   academic/benchmarks/core/runner.py   academic/benchmarks/core/cost_accounting.py   academic/benchmarks/spreadsheet/executor.py   academic/benchmarks/spreadsheet/maintenance/adapter.py   academic/skill_repository/llm_maintenance.py

pytest -q   academic/benchmarks/tests/test_generic_evolution.py   academic/benchmarks/tests/test_spreadsheet_evolution.py
```

结果：`34 passed`，仅有已有 pytest config / pydantic deprecation warnings。

## Chapter 8: LLM role input compaction across benchmarks

目标：减少 injector / credit assigner / extractor / bundle builder / bundle maintainer / refiner / stale resolver / refactorer / SkillsBench selector 的输入包装和重复字段；保留关键 task、result、contract、exposure、bundle/test failure 信息。注意：这里压缩的是 LLM prompt payload，不改变 bundle replay 需要的内部 `task_fragment.input_artifacts` / `golden_xlsx` 等结构。

### 8.1 通用 role JSON compaction

位置：`academic/skill_repository/llm_maintenance.py:766-886`

代码内容：

```python
def _role_json_block(value: Any) -> str:
    return json.dumps(_compact_role_payload(value), ensure_ascii=False, indent=2)
```

```python
_ROLE_DROP_KEYS = {
    "raw",
    "raw_response",
    "input_artifacts",
    "golden_xlsx",
    "prompt_txt_preview",
    "history_tail",
    "lineage",
    "fixtures",
    "version_id",
}
```

```python
_ROLE_TEXT_LIMITS = {
    "description": 360,
    "reason": 420,
    "failure_summary": 900,
    "stderr": 900,
    "stderr_tail": 900,
    "stdout": 500,
    "stdout_tail": 500,
    "question": 1400,
    "question_preview": 1400,
    "instruction": 1400,
    "user_messages": 1400,
    "raw_error": 700,
}
```

```python
_ROLE_PRESERVE_TEXT_KEYS = {
    "body",
    "code",
    "code_preview",
    "code_snippet",
    "executable_code",
    "implementation",
    "source_code",
}
```

```python
_ROLE_LIST_LIMITS = {
    "positive_cases": 4,
    "negative_cases": 4,
    "integration_cases": 4,
    "unit_case_runs": 8,
    "failed_cases": 6,
    "passed_cases": 4,
    "candidate_skills": 8,
    "recent_helpful": 2,
    "recent_harmful": 2,
    "dependency_summaries": 6,
    "refinement_history": 3,
    "integration_failures": 6,
    "contract_validation_failures": 6,
    "credit_cases": 6,
    "credit_context": 6,
    "question": 4,
    "expected": 6,
    "expected_calls": 6,
    "expected_focused": 6,
    "focused_turns": 6,
    "turns": 6,
    "tool_calls": 8,
    "tool_results": 4,
    "notebook_steps": 10,
    "tool_summary": 80,
    "error_summary": 40,
}
```

```python
def _compact_role_payload(value: Any, *, key: str = "", depth: int = 0) -> Any:
    if value in (None, "", [], {}):
        return None
    if isinstance(value, str):
        if key in _ROLE_PRESERVE_TEXT_KEYS:
            return value
        limit = _ROLE_TEXT_LIMITS.get(key, 1800 if len(value) > 2400 else 0)
        return _trim_text(value, limit=limit) if limit else value
    if isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for child_key, child_value in value.items():
            if child_key in _ROLE_DROP_KEYS:
                continue
            if child_key == "metadata" and isinstance(child_value, dict):
                child_value = {
                    k: child_value.get(k)
                    for k in (
                        "domains",
                        "allowed_tools",
                        "intent_keywords",
                        "source_task_ids",
                        "source_success",
                        "non_applicability",
                        "promotion_state",
                        "disabled_reason",
                    )
                    if child_value.get(k) not in (None, "", [], {})
                }
            compacted = _compact_role_payload(child_value, key=str(child_key), depth=depth + 1)
            if compacted not in (None, "", [], {}):
                out[str(child_key)] = compacted
        return out
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        limit = _ROLE_LIST_LIMITS.get(key)
        truncated = False
        if limit is not None and len(items) > limit:
            items = items[:limit]
            truncated = True
        compacted_items = [
            compacted
            for item in items
            for compacted in [_compact_role_payload(item, key=key, depth=depth + 1)]
            if compacted not in (None, "", [], {})
        ]
        if truncated:
            compacted_items.append({"truncated_count": len(value) - len(items)})
        return compacted_items
    return str(value)
```

### 8.2 通用 maintenance roles 使用 compact block

位置：`academic/skill_repository/llm_maintenance.py:2149-2156`

代码内容：

```python
prompt_payload = _credit_assignment_prompt_block(
    detail=detail,
    candidate_artifacts=candidate_artifacts,
)
user = (
    "## Completed Task Trace\n"
    f"{_role_json_block(prompt_payload)}\n"
)
```

位置：`academic/skill_repository/llm_maintenance.py:2240-2250`

代码内容：

```python
user = (
    "## Existing Artifacts\n"
    f"{_role_json_block(existing_summary)}\n\n"
    "## Tool Schemas\n"
    f"{_role_json_block(tool_summary[:80])}\n\n"
    "## Call Error Evidence\n"
    f"{_role_json_block(error_summary[:40])}\n\n"
    "## Error Focus Hints\n"
    f"{_role_json_block(_error_focus_hints(results)[:40])}\n\n"
    "## Benchmark Results\n"
    f"{_role_json_block([_result_prompt_block(item) for item in results])}\n"
)
```

位置：`academic/skill_repository/llm_maintenance.py:2348-2357`

代码内容：

```python
user = (
    "## Target Skill Artifact\n"
    f"{_role_json_block(_artifact_prompt_block(artifact))}\n\n"
    "## Source Results (focused to turns relevant to this skill)\n"
    f"{_role_json_block([_result_prompt_block(item, focus_artifact=artifact) for item in source_results])}\n\n"
    "## Replay Results (focused)\n"
    f"{_role_json_block([_result_prompt_block(item, focus_artifact=artifact) for item in (replay_results or [])])}\n\n"
    "## Integration Failures\n"
    f"{_role_json_block(list(integration_failures or []))}\n"
)
```

位置：`academic/skill_repository/llm_maintenance.py:2469-2479`

代码内容：

```python
user = (
    "## Target Skill Artifact\n"
    f"{_role_json_block(_artifact_prompt_block(artifact))}\n\n"
    "## Current Bundle\n"
    f"{_role_json_block(_bundle_projection(artifact.bundle))}\n\n"
    "## Credit-Assigned Bundle Cases\n"
    f"{_role_json_block(list(credit_cases or []))}\n\n"
    "## Integration Failures\n"
    f"{_role_json_block(list(integration_failures or []))}\n\n"
    "## Contract Validation Failures\n"
    f"{_role_json_block(list(contract_validation_failures or []))}\n"
)
```

位置：`academic/skill_repository/llm_maintenance.py:2520-2532`

代码内容：

```python
user = (
    "## Current Artifact\n"
    f"{_role_json_block(_artifact_prompt_block(artifact))}\n\n"
    "## Test Result\n"
    f"{_role_json_block(test_result)}\n\n"
    "## Integration Failures\n"
    f"{_role_json_block(list(integration_failures or []))}\n\n"
    "## Refinement History\n"
    f"{_role_json_block(list(refinement_history or []))}\n\n"
    "## Neighbor Dependency Summaries\n"
    f"{_role_json_block(list(dependency_summaries or []))}\n\n"
    "## Recent Credit Assignment Context\n"
    f"{_role_json_block(list(credit_context or []))}\n"
)
```

### 8.3 Injector gate 输入精简

位置：`academic/benchmarks/core/skill_injector.py:375-392`

代码内容：

```python
payload = {
    "benchmark": benchmark,
    "task_id": task_id,
    "phase": phase,
    "query": _clip(query, 1800),
    "max_selected": limit,
    "candidates": [_llm_injector_candidate_payload(artifact) for artifact in candidates],
}
try:
    from academic.skill_repository.llm_maintenance import _ask_json, _role_json_block

    parsed = await _ask_json(
        system=LLM_SKILL_INJECTOR_SYSTEM,
        user=_role_json_block(payload),
        llm_config=llm_config,
        model_name=model_name,
        role="skill_injector",
        metadata={"benchmark": benchmark, "task_id": task_id, "phase": phase, **dict(metadata or {})},
    )
```

位置：`academic/benchmarks/core/skill_injector.py:453-471`

代码内容：

```python
def _llm_injector_candidate_payload(artifact: SkillArtifact) -> Dict[str, Any]:
    metadata = artifact.metadata or {}
    return {
        "skill_name": artifact.name,
        "status": artifact.status,
        "injection_type": artifact.injection_type(),
        "summary": _clip(artifact.description or artifact.interface.summary or artifact.interface.usage, 360),
        "contract": {
            "input": artifact.interface.input_contract,
            "output": artifact.interface.output_contract,
            "invocation": artifact.interface.invocation_contract,
        },
        "match_hints": {
            "domains": list(metadata.get("domains") or []),
            "intent_keywords": list(metadata.get("intent_keywords") or []),
            "allowed_tools": list(metadata.get("allowed_tools") or []),
        },
        "non_applicability": _clip(metadata.get("non_applicability") or artifact.interface.compatibility_notes, 260),
    }
```

### 8.4 Spreadsheet-specific roles 使用 compact block

位置：`academic/benchmarks/spreadsheet/maintenance/adapter.py:92-98`

代码内容：

```python
from academic.skill_repository.llm_maintenance import (
    _ask_json,
    _role_json_block,
    apply_refine_payload,
    normalize_skill_name,
    refine_skill_artifact_llm,
    summarize_dependency_context,
)
```

位置：`academic/benchmarks/spreadsheet/maintenance/adapter.py:217-255`

代码内容：

```python
payload = await _compat_ask_json(
    system=SPREADSHEET_CREDIT_SYSTEM,
    user=_role_json_block(
        {
            "task": _spreadsheet_task_fragment(detail),
            "result": {
                key: projection.get(key)
                for key in (
                    "success",
                    "score",
                    "answer_sheet",
                    "answer_position",
                    "checked_cells",
                    "mismatched_cells",
                    "execution_ok",
                    "stderr_tail",
                    "notebook_step_count",
                    "notebook_steps",
                )
                if projection.get(key) not in (None, "", [], {})
            },
            "skill_exposure": {
                "retrieved": projection.get("retrieved_skills") or [],
                "prompt_injected": projection.get("prompt_injected_skills") or [],
                "called": projection.get("called_skill_functions") or [],
                "candidate_policy": "prompt_injected_or_called_only",
            },
            "retrieval_audit": {
                "retrieved_only_skills": projection.get("retrieved_only_skills") or [],
            },
            "candidate_skills": [
                _spreadsheet_skill_projection(artifact, projection=projection)
                for artifact in candidate_artifacts
            ],
        }
    ),
```

位置：`academic/benchmarks/spreadsheet/maintenance/adapter.py:865-878`

代码内容：

```python
payload = await _compat_ask_json(
    system=SPREADSHEET_EXTRACT_SYSTEM,
    user=_role_json_block(
        _spreadsheet_extraction_user_payload(
            existing=existing,
            detail=detail,
            limits=limits,
            previous_artifacts=previous_artifacts,
            rubric_failures=rubric_failures,
        )
    ),
```

### 8.5 SkillsBench selector 输入精简

位置：`academic/benchmarks/skillsbench/adapter.py:17-22`

代码内容：

```python
from academic.benchmarks.core.artifacts import ArtifactStore
from academic.benchmarks.core.cost_accounting import make_cost_event
from academic.benchmarks.core.llm_text import TextLLMResponse, ask_text_llm
from academic.benchmarks.core.skill_injector import select_skill_context_with_llm
from academic.benchmarks.core.types import BenchmarkResult, BenchmarkTask, SkillArtifact, SkillInterface
from academic.skill_repository.llm_maintenance import _role_json_block, _trim_text
```

位置：`academic/benchmarks/skillsbench/adapter.py:518-528`

代码内容：

```python
prompt = _role_json_block(
    {
        "task": {
            "task_id": task.task_id,
            "category": task.metadata.get("category", ""),
            "tags": task.metadata.get("tags") or [],
            "instruction": _trim_text(instruction, limit=1400),
        },
        "retrieved_skill_cards": _trim_text(injected_skills, limit=2600),
    }
)
```

### 8.6 验证

命令：

```bash
python -m py_compile \
  academic/skill_repository/llm_maintenance.py \
  academic/skill_repository/refactor_overlap.py \
  academic/benchmarks/core/skill_injector.py \
  academic/benchmarks/spreadsheet/trace_projection.py \
  academic/benchmarks/spreadsheet/maintenance/adapter.py \
  academic/benchmarks/skillsbench/adapter.py

pytest -q \
  academic/benchmarks/tests/test_skill_injector_budget.py \
  academic/benchmarks/tests/test_spreadsheet_evolution.py \
  academic/skill_repository/test_llm_maintenance_feedback.py \
  academic/benchmarks/tests/test_skillsbench_adapter.py \
  academic/benchmarks/tests/test_generic_evolution.py \
  academic/benchmarks/tests/test_common_maintenance_core.py \
  academic/benchmarks/tests/test_benchmark_refactor_contracts.py -q

pytest -q academic/benchmarks/tests/bfcl/test_benchmark_adapters.py -q \
  -k 'skill_injector or baseline_serial_passes_skill_injector_knobs or retrieval_audit_reports_injector_guard_reason'
```

结果：

```text
py_compile: passed
87 passed
2 passed
```

仅有已有 pytest config / pydantic deprecation warnings。

---

## 2026-05-20 Follow-up: Pre-store Refine Loop And Non-forcing Retrieval

### Diagnosis

The post-fix Spreadsheet `20/0` run showed two implementation mismatches with the intended algorithm:

1. Freshly extracted Spreadsheet candidates were only bundle-tested once before `store.add_pending`; they did not run the intended `test -> refine -> test` repair loop before the final accept/reject decision.
2. Pending retrieval with `top_k=3, pending_skill_fraction=1/3` effectively forced one pending skill into every turn when any pending artifact existed. In the run, `skill_injector_mode` was unset, so Spreadsheet defaulted to `full` and did not instantiate `BudgetSkillInjector`; no injector-side rejection happened.

### Code Changes

File: `academic/benchmarks/spreadsheet/maintenance/adapter.py`

Precise changed logic in `SpreadsheetMaintenanceAdapter.run_micro_maintenance`:

```python
prestore_gate = await _run_spreadsheet_prestore_bundle_gate(
    artifact=artifact,
    store=store,
    config=config,
)
artifact = prestore_gate["artifact"]
prestore_result = prestore_gate.get("final_result")
if not prestore_gate.get("passed"):
    extraction_reports.append(
        {
            "skill_name": artifact.name,
            "status": "rejected",
            "description": artifact.description,
            "source_task_ids": artifact.metadata.get("source_task_ids") or [],
            "rejection_reason": prestore_gate.get("rejection_reason") or "prestore_bundle_test_failed",
            "prestore_test_result": prestore_result.as_dict() if prestore_result is not None else {},
            "prestore_test_results": copy.deepcopy(prestore_gate.get("test_results") or []),
            "prestore_refine_decisions": copy.deepcopy(prestore_gate.get("refine_decisions") or []),
        }
    )
    continue
store.add_pending(artifact)
```

New helper:

```python
async def _run_spreadsheet_prestore_bundle_gate(
    *,
    artifact: SkillArtifact,
    store: ArtifactStore,
    config: MaintenanceRunConfig,
) -> Dict[str, Any]:
    repair_limit = max(
        0,
        int(
            config.extra.get(
                "spreadsheet_prestore_refine_max_rounds",
                config.extra.get("micro_refine_max_repair_rounds", 1),
            )
            or 0
        ),
    )
    current = artifact
    for attempt in range(repair_limit + 1):
        final_result = await _compat_module()._execute_spreadsheet_bundle_tests(
            artifact=current,
            config=config,
        )
        if bool((final_result.aggregate or {}).get("pass_all_tests") or (final_result.aggregate or {}).get("passed")):
            return {"artifact": current, "passed": True, "final_result": final_result, ...}
        if attempt >= repair_limit:
            break
        decision = await _refine_spreadsheet_skill_from_bundle(
            artifact=current,
            test_result=final_result,
            credit_context=[],
            store=store,
            config=config,
        )
        updated = decision.get("updated_artifact")
        if isinstance(updated, SkillArtifact) and str(decision.get("action") or "") not in {"disable", "rollback"}:
            current = updated
            continue
        break
```

File: `academic/benchmarks/spreadsheet/executor.py`

Retrieval now takes a hard score floor:

```python
min_skill_score: float = 0.01
```

Pending mixed retrieval now uses audit with `min_score`, so the selected set can be empty:

```python
active_audit = artifact_store.retrieve_audit(..., top_k=active_k, min_score=min_score, ...)
pending_audit = artifact_store.retrieve_audit(..., top_k=pending_k, min_score=min_score, include_pending=True)
```

Correction after review: `full` and `compact` are now presentation modes only. They do not decide whether the injector runs. Spreadsheet now always runs an LLM skill-injector gate for retrieved candidates before adding them to executor context; the mode only controls whether selected skills are rendered as full prompt blocks or compact cards.

```python
def _spreadsheet_presentation_mode(mode: str | None) -> str:
    value = str(mode or "").strip().lower()
    if value:
        return value
    return "full"
```

File: `academic/benchmarks/core/skill_injector.py`

The previous `BudgetSkillInjector` was a deterministic rubric/budget renderer, not the paper role's LLM injector. A new shared LLM gate now performs relevance selection outside executor context:

```python
async def select_skill_context_with_llm(
    artifacts: Sequence[SkillArtifact],
    *,
    query: str,
    llm_config: str,
    model_name: str | None = None,
    presentation_mode: str = "full",
    allowed_injection_types: Iterable[str] | None = None,
    max_selected: int | None = None,
    budget_chars: int = 0,
    compact_chars_per_skill: int = 900,
    benchmark: str = "",
    task_id: str = "",
    phase: str = "executor",
    metadata: Dict[str, Any] | None = None,
) -> SkillInjectionResult:
    ...
    parsed = await _ask_json(
        system=LLM_SKILL_INJECTOR_SYSTEM,
        user=json.dumps(payload, ensure_ascii=False, indent=2),
        llm_config=llm_config,
        model_name=model_name,
        role="skill_injector",
        metadata={"benchmark": benchmark, "task_id": task_id, "phase": phase, **dict(metadata or {})},
    )
```

`render_skill_prompt_blocks(...)` then renders already-selected skills in `full` or `compact` form. BFCL was also using the deterministic injector path; it now uses the same LLM gate for prompt-skill selection.

File: `academic/benchmarks/core/runner.py`

Spreadsheet evolve/baseline now passes the CLI `--min-skill-score` value into Spreadsheet execution:

```python
"min_skill_score": args.min_skill_score,
```

```python
result = await run_spreadsheet_task_notebook(
    ...
    min_skill_score=min_skill_score,
    ...
)
```

### Tests

Added/updated:

```python
def test_spreadsheet_pending_retrieval_respects_min_score(...)
```

```python
async def test_spreadsheet_extractor_refines_candidate_before_prestore_reject(...)
```

Verification:

```bash
python -m py_compile \
  academic/benchmarks/spreadsheet/executor.py \
  academic/benchmarks/spreadsheet/maintenance/adapter.py \
  academic/benchmarks/core/skill_injector.py \
  academic/benchmarks/core/runner.py

pytest -q academic/benchmarks/tests/test_spreadsheet_evolution.py -q
```

Result:

```text
44 passed
```

Additional LLM injector regression:

```python
async def test_llm_skill_injector_gate_runs_independent_of_full_presentation(...)
```

This test mocks the injector LLM, sets `presentation_mode="full"`, and verifies a rejected retrieved skill does not enter the prompt. This protects the intended invariant: `full` means full rendering after LLM selection, not "skip injector".

Adjacent regression check:

```bash
pytest -q \
  academic/benchmarks/tests/test_skill_injector_budget.py \
  academic/benchmarks/tests/test_generic_evolution.py \
  academic/benchmarks/tests/bfcl/test_benchmark_adapters.py -q
```

Result: all selected tests passed except `test_historical_bfcl_train_details_produce_nonempty_maintenance_assets`, which fails because the local historical fixture file is missing:

```text
Missing historical BFCL fixture: /home/lixujun/skill_evolving/academic/results/bfcl_v3_glm47_official_tracecheck_evolve_3x3_partial_train.json
```

#### 8. 50/50 notebook speedup 真实结果

命令：

```bash
python -m academic.benchmarks.core.runner   --benchmark spreadsheet --mode evolve   --llm-config local_claude_proxy   --model-name claude-sonnet-4-5   --n-train 50 --n-test 50   --n-train-runs 1 --n-runs 1   --train-concurrency 4   --micro-maintenance-concurrency 8   --test-concurrency 4   --top-k-skills 3   --spreadsheet-execution-mode notebook   --spreadsheet-max-turns 5   --max-task-seconds 180   --tag 0519-speedup   --macro-snapshot-dir academic/results/macro_snapshots/spreadsheet_0519-speedup   --partial-output academic/results/spreadsheet_0519-speedup_partial.json   --output academic/results/spreadsheet_0519-speedup.json
```

产物：
- `academic/results/spreadsheet_0519-speedup.json`
- `academic/results/spreadsheet_0519-speedup_partial.json`
- `academic/results/spreadsheet_0519-speedup_rerun.log`

结果：
- train: `50` tasks, success `13/50 = 0.26`, avg_score `0.3176`, executor tokens `690826`.
- test: `50` tasks, success `14/50 = 0.28`, avg_score `0.335`, executor tokens `677868`.
- maintenance: `168` LLM calls, total_tokens `1511532`.
  - spreadsheet_extractor: `50` calls, `508734` tokens, total duration `1519.480s`.
  - spreadsheet_credit_assigner: `40` calls, `518795` tokens, total duration `1250.016s`.
  - refiner: `78` calls, `484003` tokens, total duration `1170.517s`.
- observed wall-clock span from token event buckets: `7740.0s`.
- previous baseline `academic/results/spreadsheet_evolve_50_50_true_20260518_022120.json`: `8610.942s`.
- actual speedup: `8610.942 / 7740.0 = 1.1125x`.

结论：并发没有失效，但加速只有约 `1.11x`，因为 notebook 多轮 rollout 加上每个成功/修复样本的 extractor、credit、refiner 维护成本非常高。`micro_maintenance_concurrency=8` 没有造成 timeout 或明显队列崩溃；最大单次 observed maintenance request duration 约 `70s`，主要瓶颈是维护请求数量和 prompt/token 规模，而不是 micro 并发过大。

## Chapter 8: 小规模真实验证

目标：验证改动确实降低维护开销且不破坏效果。

协议：
- 章节 1-3 后跑 3 train / 2 test smoke。
- 全章节后跑 10 train / 10 test。
- 报告 success、avg_score、input/output tokens、maintenance role tokens、credit/refiner/bundle calls、wall-clock、actual callable imports/calls。

## Chapter 9: Callable progressive disclosure and pending exposure

目标：验证 Spreadsheet callable skill 不只作为 prompt body 参考，也能以函数 API 形式渐进式披露；同时允许 pending skill 以低权重进入测试期检索，收集复用证据，但默认不污染训练 rollout。

### Chapter 9 维护记录

提交：待提交。

状态：已完成，测试通过。

用户问题与观察：

1. fixed-split `20/0` debug 中有 `10/20` 个 train runs 暴露 callable skills，但 `called_skill_functions=0/20`。
2. 抽查前 5 个 callable-exposed trace：模型没有 import/call，也不是明显照着 callable signature 改写；它直接按 workbook preview 和题目重新 inspect / 写 openpyxl。
3. skill 名称来自 extractor 生成的 artifact name，经 `normalize_skill_name(...)` 归一化后保留 instruction type 和若干关键词；训练样本里关键词质量不高，所以出现 `spreadsheet_sheet_level_manipulation_level_manipulation_need_shift` 这类名字。中间没有人工 rubric 改名。
4. pending skill 生成频繁是因为 micro maintenance 每个 train detail 都会尝试 `_extract_spreadsheet_skills_from_detail(...)`；当前 extractor 可以从 success trace 产生 seed skill，也可以从 failed trace 产生 repair evidence。修复后 failed source 不再 promote，但仍会留下 pending/negative candidate。

#### 1. Callable progressive disclosure

位置：`academic/benchmarks/spreadsheet/skill_runtime.py:25-104`

代码内容：

```python
def write_spreadsheet_skill_library(
    skills: Sequence[SkillArtifact],
    work_dir: Path,
    *,
    disclosure_mode: str = "full",
) -> Dict[str, Any]:
    callable_rows: List[Dict[str, Any]] = []
    skill_objects: List[str] = []
```

```python
callable_rows.append(
    {
        "skill_name": skill.name,
        "function_name": func_name,
        "description": spreadsheet_callable_description(skill),
        "signature": f"{func_name}(INPUT_XLSX, OUTPUT_XLSX, **kwargs)",
        "code_preview": spreadsheet_skill_code_preview(code),
    }
)
skill_objects.append(
    f"{func_name}_skill = SimpleNamespace("
    f"name={skill.name!r}, "
    f"function={func_name}, "
    f"signature={f'{func_name}(INPUT_XLSX, OUTPUT_XLSX, **kwargs)'!r}, "
    f"description={spreadsheet_callable_description(skill)!r}, "
    f"code={code!r})\n"
)
```

`progressive` prompt 语义：

```python
"Executable Spreadsheet skills are already available in the Python namespace. "
"If a signature matches the task, you can complete the operation with one direct function call. "
"You may also inspect the implementation before deciding by running "
"`print(<function_name>_skill.code)` in a notebook cell, then either call the function or adapt/rewrite its code.\n"
```

效果：测试期可以只给 signature + implementation gist；executor 可以直接 call，也可以 `print(<function>_skill.code)` 查看完整实现后改写。

#### 2. Executor 开关

位置：`academic/benchmarks/spreadsheet/executor.py:37-117` 和 `academic/benchmarks/spreadsheet/executor.py:239-324`

代码内容：

```python
callable_disclosure_mode: str | None = None,
pending_skill_fraction: float = 0.0,
```

```python
callable_prompt = write_spreadsheet_skill_library(
    injected,
    base_work_dir,
    disclosure_mode=callable_disclosure_mode or os.environ.get("SPREADSHEET_CALLABLE_DISCLOSURE_MODE") or "full",
)
```

默认 `full`，不改变旧实验行为。设置为 `progressive` 后，system prompt 明确说明 callable 已经可用、可以一行调用、也可以打印 skill object 的 `code`。

#### 3. Pending / active 混合检索

位置：`academic/skill_repository/store.py:411-487`

代码内容：

```python
def retrieve(
    self,
    query: str,
    top_k: int = 5,
    *,
    min_score: float = 0.0,
    predicate: Callable[[SkillArtifact], bool] | None = None,
    rerank_key: Callable[[SkillArtifact], tuple] | None = None,
    debug_context: Dict[str, Any] | None = None,
    include_pending: bool = False,
) -> List[SkillArtifact]:
```

```python
pending_allowed = bool(include_pending) and (
    artifact.status == "pending" or bool(artifact.metadata.get("is_pending_skill"))
)
if not artifact.retrieval_enabled() and not pending_allowed:
    row.update({"predicate_passed": False, "filter_reason": "retrieval_disabled", ...})
    candidates.append(row)
    continue
```

位置：`academic/benchmarks/spreadsheet/executor.py:484-532`

代码内容：

```python
def _retrieve_spreadsheet_skills(
    artifact_store: Optional[ArtifactStore],
    *,
    query: str,
    top_k: int,
    pending_skill_fraction: float = 0.0,
) -> tuple[List[Any], Dict[str, Any] | None]:
```

```python
pending_k = min(top_k, int(round(top_k * fraction)))
active_k = max(0, top_k - pending_k)
active = artifact_store.retrieve(... predicate=active_predicate)
pending = artifact_store.retrieve(... predicate=pending_predicate, include_pending=True)
```

语义：
- 默认 `pending_skill_fraction=0.0`，pending 完全不进 executor 检索。
- 打开后 active 和 pending 分开打分，各自取 top，再合并。
- 例如 `top_k=3, pending_skill_fraction=1/3` 会取 active top2 + pending top1。
- trace/metrics 的 `injector_events` 会记录 `spreadsheet_pending_mixed_retrieval`，包含 active/pending selected names。

#### 4. Runner / test-only override

位置：`academic/benchmarks/core/runner.py`

新增 CLI：

```python
--spreadsheet-callable-disclosure-mode {full,progressive}
--spreadsheet-test-callable-disclosure-mode {full,progressive}
--spreadsheet-pending-skill-fraction FLOAT
--spreadsheet-test-pending-skill-fraction FLOAT
```

evolve config 写入：

```python
"spreadsheet_callable_disclosure_mode": args.spreadsheet_callable_disclosure_mode,
"spreadsheet_pending_skill_fraction": args.spreadsheet_pending_skill_fraction,
"test_extra": {
    "spreadsheet_callable_disclosure_mode": args.spreadsheet_test_callable_disclosure_mode,
    "spreadsheet_pending_skill_fraction": args.spreadsheet_test_pending_skill_fraction,
},
```

位置：`academic/benchmarks/core/evolution.py`

heldout test 调用 `_phase_config("test")`，只在 test 阶段合并 `config.extra["test_extra"]`，因此可以做到训练阶段保守、测试阶段 progressive/pending 暴露。

#### 5. 测试

新增测试：

```python
async def test_spreadsheet_callable_progressive_disclosure_exposes_signature_and_code_object(...)
```

断言：
- system prompt 包含 `Available callable signatures`。
- system prompt 包含 `<function>(INPUT_XLSX, OUTPUT_XLSX, **kwargs)`。
- system prompt 包含 `<function>_skill.code`。
- 生成的 `skill_library.py` 包含 `<function>_skill = SimpleNamespace(...)`。

```python
def test_spreadsheet_pending_retrieval_requires_explicit_include(...)
```

断言：
- 默认 `store.retrieve(...)` 不返回 pending。
- 显式 `include_pending=True` 且 predicate 选择 pending 时才返回 pending。

```python
async def test_spreadsheet_pending_fraction_mixes_active_and_pending(...)
```

断言：
- `top_k=3, pending_skill_fraction=1/3` 返回 2 个 active + 1 个 pending。
- `injector_events` 记录 `spreadsheet_pending_mixed_retrieval`。

验证命令：

```bash
python -m py_compile \
  academic/benchmarks/spreadsheet/skill_runtime.py \
  academic/benchmarks/spreadsheet/executor.py \
  academic/skill_repository/store.py \
  academic/benchmarks/core/evolution.py \
  academic/benchmarks/core/runner.py \
  academic/benchmarks/spreadsheet/maintenance/adapter.py \
  academic/benchmarks/tests/test_spreadsheet_evolution.py

pytest -q academic/benchmarks/tests/test_spreadsheet_evolution.py
```

结果：`34 passed`，仅有已有 pytest config / pydantic deprecation warnings。
## 2026-05-20 Chapter 7 follow-up: file-backed callable skill disclosure

Goal: make Spreadsheet `bash_react` callable skills closer to Trace2Skill's skill-directory setting.  The executor prompt should not need full callable bodies; it should know that skill implementations live in readable/writable task files and can be imported, inspected, edited, or run as scripts.

Trace2Skill reference behavior:

- Official repo: `https://github.com/Qwen-Applications/Trace2Skill`.
- Spreadsheet released skills are directories containing `SKILL.md`, scripts such as `recalc.py`, and optional `references/`.
- Its `cli_skill_preloaded_full_system_v1.txt` tells the agent that `SKILL.md` and resources are stored under a skill directory and can be used by path, e.g. a script under the skill directory.
- Our adaptation keeps per-artifact callable functions, but backs every callable with real files in the task directory.

Code changes:

1. `academic/benchmarks/spreadsheet/skill_runtime.py`

Exact changed lines:

```python
8: import stat
34:     skill_scripts_dir = work_dir / "skills"
91:                 "library_path": "skill_library.py",
92:                 "script_path": str(Path("skills") / f"{func_name}.py"),
107:         work_dir.mkdir(parents=True, exist_ok=True)
108:         (work_dir / "skill_library.py").write_text("".join(chunks))
109:         skill_scripts_dir.mkdir(parents=True, exist_ok=True)
110:         for row in callable_rows:
111:             script_path = work_dir / str(row["script_path"])
112:             script_path.write_text(render_spreadsheet_skill_script(str(row["function_name"])))
113:             script_path.chmod(script_path.stat().st_mode | stat.S_IXUSR)
123:         rows = "\n".join(
124:             f"- `{row['signature']}` from skill `{row['skill_name']}`: {row['description'][:220]} "
125:             f"Files: `{row.get('library_path') or 'skill_library.py'}`, `{row.get('script_path') or ''}`."
128:         object_rows = "\n".join(
129:             f"- `{row['function_name']}_skill.code`, `sed -n '1,220p' skill_library.py`, or "
130:             f"`sed -n '1,220p' {row.get('script_path') or ''}` shows the implementation for `{row['function_name']}`."
145:             "Executable Spreadsheet skills are available as readable/writable files in the current task directory: "
146:             "`skill_library.py` exports the functions and `skills/*.py` provides runnable wrappers. "
147:             "If a signature matches the task, you can complete the operation with one direct function call. "
148:             "You may inspect or edit these files before deciding, then either call the function, run the script wrapper, "
149:             "or adapt/rewrite its code.\n"
```

New helper added:

```python
def render_spreadsheet_skill_script(function_name: str) -> str:
    return (
        "#!/usr/bin/env python3\n"
        "\"\"\"Runnable wrapper for an evolved Spreadsheet callable skill.\"\"\"\n"
        "from __future__ import annotations\n\n"
        "import json\n"
        "import os\n"
        "import sys\n"
        "from pathlib import Path\n\n"
        "ROOT = Path(__file__).resolve().parents[1]\n"
        "if str(ROOT) not in sys.path:\n"
        "    sys.path.insert(0, str(ROOT))\n\n"
        f"from skill_library import {function_name}\n\n\n"
        "def main() -> None:\n"
        "    input_xlsx = sys.argv[1] if len(sys.argv) > 1 else os.environ.get('INPUT_XLSX')\n"
        "    output_xlsx = sys.argv[2] if len(sys.argv) > 2 else os.environ.get('OUTPUT_XLSX')\n"
        "    kwargs = json.loads(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3].strip() else {}\n"
        "    if not input_xlsx or not output_xlsx:\n"
        "        raise SystemExit('Usage: python skills/"
        + function_name
        + ".py INPUT_XLSX OUTPUT_XLSX [json_kwargs]')\n"
        f"    {function_name}(input_xlsx, output_xlsx, **kwargs)\n\n\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
    )
```

2. `academic/benchmarks/spreadsheet/prompts.py`

Exact changed lines:

```python
42: - `skill_library.py`: readable/writable importable callable Spreadsheet skills when any are available.
43: - `skills/`: readable/writable script wrappers for callable skills when any are available.
55: When a callable skill matches the workbook and requested answer range, prefer a
56: direct import/call or its `skills/<function>.py` wrapper over rewriting it. You
57: may inspect or edit `skill_library.py` and `skills/*.py` in this task directory.
```

3. `academic/benchmarks/spreadsheet/executor.py`

Exact changed lines:

```python
1327:             "and calling it from `skill_library.py` or running its `skills/<function_name>.py` wrapper; "
1328:             "if unsure, inspect the readable/writable files with `sed -n '1,220p' skill_library.py` or "
1329:             "`sed -n '1,220p' skills/<function_name>.py` before adapting or rewriting.\n\n"
1384:                     f"sed -n '1,220p' {row.get('script_path') or 'skills/' + row['function_name'] + '.py'}",
1385:                     "sed -n '1,260p' skill_library.py",
1396:             f"- `{row['signature']}` from skill `{row['skill_name']}`: {str(row.get('description') or '')[:220]} "
1397:             f"Files: `{row.get('library_path') or 'skill_library.py'}`, `{row.get('script_path') or ''}`."
1403:                     "```bash",
1404:                     f"python {row.get('script_path') or 'skills/' + row['function_name'] + '.py'} \"$INPUT_XLSX\" \"$OUTPUT_XLSX\"",
1412:                 "Executable Spreadsheet skills are available as readable/writable files in the current task directory. "
1413:                 "`skill_library.py` exports the functions and `skills/*.py` contains runnable wrappers. "
1414:                 "You may call a matching function directly, run its wrapper, inspect the files, or edit/adapt them before use.\n"
1417:                 + "\nScript wrapper examples:\n"
```

4. `academic/benchmarks/tests/test_spreadsheet_evolution.py`

Exact changed lines:

```python
2: import os
4: import subprocess
5: import sys
441:     assert "skills/spreadsheet_double_progressive.py" in captured["system"]
442:     assert "Implementation gist:" not in captured["system"]
445:     assert (tmp_path / "work_progressive" / "skills" / "spreadsheet_double_progressive.py").exists()
786:         assert "skills/spreadsheet_bash_double_skill.py" in kwargs["prompt"]
787:         assert "readable/writable files" in kwargs["prompt"]
788:         assert "Implementation gist:" not in kwargs["prompt"]
```

New execution test:

```python
def test_spreadsheet_callable_script_wrapper_executes_skill(tmp_path: Path) -> None:
    input_xlsx = tmp_path / "input.xlsx"
    output_xlsx = tmp_path / "output.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws["A1"] = 7
    wb.save(input_xlsx)

    skill = SkillArtifact(
        name="spreadsheet_script_double",
        kind="executable_tool",
        description="Double A1 into B1.",
        body="""```python
def apply_double(INPUT_XLSX, OUTPUT_XLSX, **kwargs):
    import openpyxl
    wb = openpyxl.load_workbook(INPUT_XLSX)
    ws = wb["Sheet1"]
    ws["B1"] = ws["A1"].value * 2
    wb.save(OUTPUT_XLSX)
```""",
        metadata={"domains": ["SpreadsheetBench"], "allowed_tools": ["openpyxl"]},
    )
    info = _write_spreadsheet_skill_library([skill], tmp_path, disclosure_mode="progressive")
    assert info["skills"][0]["script_path"] == "skills/spreadsheet_script_double.py"
    script = tmp_path / "skills" / "spreadsheet_script_double.py"
    assert script.exists()

    env = os.environ.copy()
    env["INPUT_XLSX"] = str(input_xlsx)
    env["OUTPUT_XLSX"] = str(output_xlsx)
    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    out = openpyxl.load_workbook(output_xlsx)
    assert out["Sheet1"]["B1"].value == 14
```

Validation:

```bash
pytest -q academic/benchmarks/tests/test_spreadsheet_evolution.py -q
```

Result: 52 passed.  Warnings are unrelated existing pytest/Pydantic deprecation warnings.

## 2026-05-20 follow-up: Trace2Skill-style local `skills.md` manifest

Reason:

- The file-backed callable run `0520-filebacked-evolve20-call-observe-haikuinj`
  completed `n_train=20/n_test=0` and produced callable skills, but the executor
  still did not read or call them.
- Final observed metrics from
  `academic/results/spreadsheet_0520-filebacked-evolve20-call-observe-haikuinj.json`:
  `train=20`, `skills=8`, `micro_reports=20`, `success=6/20`,
  `avg_score=0.401345`, `prompt_context_tasks=1`,
  `real_skill_action_tasks=0`, `called_skill_tasks=0`.
- The only injected task was task `247-24`, with
  `prompt_injected_skills=["insert_blank_row_above_marker_value"]`; actual bash
  turns contained no `sed`, no `cat`, no `skills.md`, no
  `from skill_library import`, no `_skill.code`, and no `python skills/...`.
- Per user instruction, this follow-up changes only the callable presentation
  format, not retrieval, injector, credit, promote, or the main algorithm flow.

Changed files:

1. `academic/benchmarks/spreadsheet/skill_runtime.py`

Exact changed lines:

```python
90:                 "manifest_path": "skills.md",
114:         (work_dir / "skills.md").write_text(render_spreadsheet_skills_manifest(callable_rows))
146:             "Executable Spreadsheet skills are available through a local skill manifest. "
147:             "Read `skills.md` first when deciding whether a skill applies. `skill_library.py` exports functions and "
148:             "`skills/*.py` provides runnable wrappers. If a signature matches the task, you can complete the operation "
149:             "with one direct function call. You may inspect or edit these files before deciding, then either call the "
150:             "function, run the script wrapper, or adapt/rewrite its code.\n"
```

New manifest renderer:

```python
def render_spreadsheet_skills_manifest(callable_rows: Sequence[Dict[str, Any]]) -> str:
    chunks = [
        "# Spreadsheet Skills",
        "",
        "This file lists local executable Spreadsheet skills available in this task directory.",
        "Use a skill only when its scope matches the workbook, target sheet/range, and requested operation.",
        "You may inspect or edit the referenced Python files before calling a skill.",
        "",
        "Common invocation patterns:",
        "```bash",
        "python - <<'PY'",
        "import os",
        "from skill_library import <function_name>",
        "<function_name>(os.environ['INPUT_XLSX'], os.environ['OUTPUT_XLSX'])",
        "PY",
        "```",
        "```bash",
        "python skills/<function_name>.py \"$INPUT_XLSX\" \"$OUTPUT_XLSX\" '{\"sheet_name\": \"Sheet1\"}'",
        "```",
    ]
    for row in callable_rows:
        chunks.extend(
            [
                "",
                f"## {row['skill_name']}",
                "",
                f"- Signature: `{row['signature']}`",
                f"- Function: `{row['function_name']}`",
                f"- Use when: {str(row.get('description') or '').strip()}",
                f"- Import: `from skill_library import {row['function_name']}`",
                f"- Wrapper: `python {row.get('script_path') or 'skills/' + row['function_name'] + '.py'} \"$INPUT_XLSX\" \"$OUTPUT_XLSX\" '{{}}'`",
                f"- Implementation files: `{row.get('library_path') or 'skill_library.py'}`, `{row.get('script_path') or ''}`",
            ]
        )
        preview = str(row.get("code_preview") or "").strip()
        if preview:
            chunks.append(f"- Code preview: `{preview}`")
    return "\n".join(chunks).rstrip() + "\n"
```

2. `academic/benchmarks/spreadsheet/executor.py`

Exact changed lines:

```python
1327:             "and calling it from `skill_library.py` or running its `skills/<function_name>.py` wrapper; "
1328:             "read `skills.md` first if deciding whether a skill applies, then inspect `skill_library.py` or "
1329:             "`skills/<function_name>.py` before adapting or rewriting.\n\n"
1384:                     "sed -n '1,220p' skills.md",
1397:             f"- `{row['signature']}` from skill `{row['skill_name']}`: {str(row.get('description') or '')[:220]} "
1398:             f"Files: `{row.get('manifest_path') or 'skills.md'}`, `{row.get('library_path') or 'skill_library.py'}`, `{row.get('script_path') or ''}`."
1413:                 "Executable Spreadsheet skills are available as readable/writable files in the current task directory. "
1414:                 "`skills.md` is the local skill manifest; read it when deciding whether a skill applies. "
1415:                 "`skill_library.py` exports the functions and `skills/*.py` contains runnable wrappers. "
1426:             "`skills.md` is the local skill manifest; `skill_library.py` exports the functions and `skills/*.py` contains runnable wrappers. "
1449:             f"Files: `{row.get('manifest_path') or 'skills.md'}`, `{row.get('library_path') or 'skill_library.py'}`, `{row.get('script_path') or ''}`."
1470:             "`skills.md` lists local skill cards, `skill_library.py` exports the functions, and `skills/*.py` provides wrappers. "
```

3. `academic/benchmarks/tests/test_spreadsheet_evolution.py`

Exact changed lines:

```python
441:     assert "skills.md" in captured["system"]
446:     manifest = (tmp_path / "work_progressive" / "skills.md").read_text()
447:     assert "# Spreadsheet Skills" in manifest
448:     assert "## spreadsheet_double_progressive" in manifest
449:     assert "from skill_library import spreadsheet_double_progressive" in manifest
786:         assert "sed -n '1,220p' skills.md" in kwargs["prompt"]
1080:     assert info["skills"][0]["manifest_path"] == "skills.md"
1082:     manifest = tmp_path / "skills.md"
1083:     assert manifest.exists()
1084:     manifest_text = manifest.read_text()
1085:     assert "## spreadsheet_script_double" in manifest_text
1086:     assert "python skills/spreadsheet_script_double.py" in manifest_text
```

Validation:

```bash
python -m py_compile academic/benchmarks/spreadsheet/skill_runtime.py academic/benchmarks/spreadsheet/executor.py academic/benchmarks/spreadsheet/prompts.py
pytest -q academic/benchmarks/tests/test_spreadsheet_evolution.py -q
```

Result: py_compile passed; spreadsheet evolution tests passed with `52 passed`.

Follow-up experiment:

```bash
SPREADSHEET_SKILL_INJECTOR_MODEL_NAME=claude-haiku-4-5 \
python -m academic.benchmarks.core.runner \
  --benchmark spreadsheet \
  --mode evolve \
  --llm-config local_claude_proxy \
  --model-name claude-sonnet-4-5 \
  --n-train 20 \
  --n-test 0 \
  --train-offset 3 \
  --n-runs 1 \
  --train-concurrency 1 \
  --micro-maintenance-concurrency 1 \
  --test-concurrency 1 \
  --top-k-skills 3 \
  --train-task-ids academic/experiments/spreadsheet_case_lists/shuffled_seed42_train_200.json \
  --test-task-ids academic/experiments/spreadsheet_case_lists/shuffled_seed42_test_200.json \
  --spreadsheet-execution-mode bash_react \
  --spreadsheet-max-turns 20 \
  --spreadsheet-callable-disclosure-mode progressive \
  --spreadsheet-pending-skill-fraction 0.33 \
  --skill-injector-mode full \
  --llm-request-timeout-s 180 \
  --tag 0520-skillsmd-evolve20-call-observe-haikuinj
```

Interim observation:

- PID: `2407091`.
- By task 7 (`247-24`), the injector selected
  `insert_blank_rows_above_marker_in_range`.
- The executor still did not read `skills.md`, inspect `skill_library.py`, import
  the callable, or run the wrapper. It continued hand-written openpyxl code.
- This means a local manifest presentation alone is not sufficient to produce
  callable use in this setting. The remaining issue is likely the executor usage
  protocol/action design or stronger per-turn skill use contract, not lack of a
  readable local file.

## 2026-05-20 follow-up: explicit `[NEW SKILLS]` decision protocol

Reason:

- A small manual LLM probe showed that Sonnet can use mid-conversation skills
  when the skill exactly solves the current next operation.
- The same probe with a complex spreadsheet task and a partial sub-skill showed
  that the model ignored both the old update format and a simple `[NEW SKILLS]`
  marker when the skill solved a later sub-operation.
- Therefore the next minimal change is not to force calls or modify retrieval,
  but to make the runtime update auditable: after each injected skill block, the
  executor must explicitly classify each skill as `USE_NOW`, `USE_LATER`, or
  `SKIP` before the next command.

Changed files:

1. `academic/benchmarks/spreadsheet/prompts.py`

Exact added bash system protocol:

```python
Runtime skill injection protocol:
- After any bash turn, the user may append a block delimited by `[NEW SKILLS]`
  and `[/NEW SKILLS]`.
- This block is not workbook output or verifier feedback. It is an authoritative
  runtime update from the skill injector for the next action.
- Before your next bash command after receiving `[NEW SKILLS]`, explicitly decide
  for each listed skill: `USE_NOW`, `USE_LATER`, or `SKIP`.
- Use `USE_NOW` when a callable skill directly solves the next operation; import
  it from `skill_library.py` or run its wrapper instead of rewriting that
  operation.
- Use `USE_LATER` when a callable skill solves a later sub-operation; name the
  exact later sub-operation. Do not silently rewrite that sub-operation later.
- Use `SKIP` only when the skill is irrelevant or unsafe for this workbook/range.
```

2. `academic/benchmarks/spreadsheet/executor.py`

Exact added runtime update wrapper for bash:

```python
        protocol_header = (
            "[NEW SKILLS]\n"
            "source: runtime skill injector after the previous bash turn\n"
            "status: actionable candidates for the next bash action\n"
            "decision_required: before your next bash command, decide for each listed skill as USE_NOW, USE_LATER, or SKIP\n"
            "decision_rules:\n"
            "- USE_NOW: the skill directly solves the next operation; import it or run its wrapper instead of rewriting that operation.\n"
            "- USE_LATER: the skill directly solves a later sub-operation; name that exact sub-operation and use the skill when you reach it.\n"
            "- SKIP: the skill is irrelevant or unsafe for this workbook, answer range, or user intent.\n\n"
        )
        protocol_footer = "\n[/NEW SKILLS]"
```

Exact added runtime update wrapper for notebook:

```python
        protocol_header = (
            "[NEW SKILLS]\n"
            "source: runtime skill injector after the previous notebook turn\n"
            "status: actionable candidates for the next notebook cell\n"
            "decision_required: before your next code cell, decide for each listed skill as USE_NOW, USE_LATER, or SKIP\n"
            "decision_rules:\n"
            "- USE_NOW: the skill directly solves the next operation; import it instead of rewriting that operation.\n"
            "- USE_LATER: the skill directly solves a later sub-operation; name that exact sub-operation and use the skill when you reach it.\n"
            "- SKIP: the skill is irrelevant or unsafe for this workbook, answer range, or user intent.\n\n"
        )
        protocol_footer = "\n[/NEW SKILLS]"
```

Exact changed content assembly:

```python
            f"{protocol_header}"
            f"Runtime skill retrieval update for this same Spreadsheet {mode_name}.\n"
            f"Newly retrieved local rules/functions: {names}.\n"
            "Use these only if they directly match the workbook, answer range, and user intent; "
            "ignore them if they are irrelevant. If a callable function matches, prefer importing "
            f"{inspect_instruction}"
            f"{skill_prompt}"
            f"{callable_block}"
            f"{protocol_footer}"
```

3. `academic/benchmarks/tests/test_spreadsheet_evolution.py`

Exact added assertions:

```python
        assert "[NEW SKILLS]" in kwargs["prompt"]
        assert "[/NEW SKILLS]" in kwargs["prompt"]
        assert "USE_NOW" in kwargs["prompt"]
        assert "USE_LATER" in kwargs["prompt"]
        assert "SKIP" in kwargs["prompt"]
        assert "decision_required" in kwargs["prompt"]
```

and for recorded update messages:

```python
    assert "[NEW SKILLS]" in update["message"]["content"]
    assert "[/NEW SKILLS]" in update["message"]["content"]
    assert "USE_NOW" in update["message"]["content"]
    assert "USE_LATER" in update["message"]["content"]
    assert "SKIP" in update["message"]["content"]
```

Validation:

```bash
python -m py_compile academic/benchmarks/spreadsheet/prompts.py academic/benchmarks/spreadsheet/executor.py academic/benchmarks/spreadsheet/skill_runtime.py
pytest -q academic/benchmarks/tests/test_spreadsheet_evolution.py -q
```

Result: py_compile passed; spreadsheet evolution tests passed with `52 passed`.

Manual LLM sanity check:

- Exact-match mock skill (`spreadsheet_double_a1_to_b1`) was called under both
  the old format and the new tagged protocol.
- Complex-task partial skill (`insert_blank_rows_above_marker_in_range`) was
  ignored under the old format and simple marker.
- With the explicit decision protocol, the model emitted:

```text
Skill Decision:
- `insert_blank_rows_above_marker_in_range`: USE_LATER - This skill directly solves the sub-operation "Insert two blank rows after each remaining Ahmed Sons row". I will use this skill after I complete the deletion and Bill Rate update operations...
```

This does not prove eventual real callable use yet, but it changes the failure
mode from silent ignore to auditable `USE_LATER` planning, which can be checked
in real traces.

## 2026-05-20 Chapter: Spreadsheet Folder-Style Skill Package Support

Goal: support Anthropic/Trace2Skill-style folder skills under the Spreadsheet
benchmark only, without changing the benchmark-agnostic `SkillArtifact` schema
or breaking existing callable experiments. The supported package shape is:

```text
skills/<skill_name>/
  SKILL.md
  scripts/*.py
  references/*.md
bundles/<skill_name>/
  run_tests.py
```

Implementation notes:

- Official Anthropic skill guidance is filesystem/progressive-disclosure based:
  `SKILL.md` is the entry point, with optional `scripts/` and `references/`.
  This implementation mirrors that shape locally for spreadsheet task work dirs.
- Package content is stored in existing artifact metadata:
  `metadata.package_files` and `metadata.bundle_files`.
- Package artifacts use `kind="skill_package"` and
  `metadata.package_format="skills_md"`.
- Existing callable skills still use `skill_library.py`, root `skills.md`, and
  `skills/<function>.py`; no core framework type was changed.

Changed files and exact code content:

1. `academic/benchmarks/spreadsheet/skill_runtime.py`

Added package kind declarations at lines 20-21:

```python
SPREADSHEET_PACKAGE_KINDS = {"skill_package", "script_package", "folder_skill"}
SPREADSHEET_PACKAGE_FORMATS = {"skills_md", "skill_package", "folder_skill"}
```

Added package detection at lines 123-128:

```python
def is_spreadsheet_package_skill(skill: SkillArtifact) -> bool:
    kind = str(skill.kind or "").strip().lower()
    metadata = dict(skill.metadata or {})
    package_format = str(metadata.get("package_format") or "").strip().lower()
    package_files = metadata.get("package_files")
    return kind in SPREADSHEET_PACKAGE_KINDS or package_format in SPREADSHEET_PACKAGE_FORMATS or isinstance(package_files, dict)
```

Added folder materialization at lines 131-202:

```python
def write_spreadsheet_skill_packages(
    skills: Sequence[SkillArtifact],
    work_dir: Path,
    *,
    disclosure_mode: str = "progressive",
) -> Dict[str, Any]:
    package_rows: List[Dict[str, Any]] = []
    for skill in skills:
        if not is_spreadsheet_package_skill(skill):
            continue
        files = spreadsheet_package_files(skill)
        if not files:
            continue
        safe_name = spreadsheet_package_dir_name(skill.name)
        skill_dir = work_dir / "skills" / safe_name
        bundle_dir = work_dir / "bundles" / safe_name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "scripts").mkdir(parents=True, exist_ok=True)
        (skill_dir / "references").mkdir(parents=True, exist_ok=True)
        (skill_dir / "__init__.py").write_text("")
        (skill_dir / "scripts" / "__init__.py").write_text("")
        script_paths: List[str] = []
        reference_paths: List[str] = []
        for rel_path, content in files.items():
            target_rel = safe_package_relative_path(rel_path)
            if not target_rel:
                continue
            target = skill_dir / target_rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(content or ""))
            if target.suffix == ".py":
                target.chmod(target.stat().st_mode | stat.S_IXUSR)
            display_path = str(Path("skills") / safe_name / target_rel)
            if str(target_rel).startswith("scripts/") and target.suffix == ".py":
                script_paths.append(display_path)
            elif str(target_rel).startswith("references/"):
                reference_paths.append(display_path)
        bundle_files = spreadsheet_bundle_files(skill)
        bundle_paths: List[str] = []
        if bundle_files:
            bundle_dir.mkdir(parents=True, exist_ok=True)
            for rel_path, content in bundle_files.items():
                target_rel = safe_package_relative_path(rel_path)
                if not target_rel:
                    continue
                target = bundle_dir / target_rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(str(content or ""))
                if target.suffix == ".py":
                    target.chmod(target.stat().st_mode | stat.S_IXUSR)
                bundle_paths.append(str(Path("bundles") / safe_name / target_rel))
```

Added package prompt renderer at lines 246-279:

```python
def render_spreadsheet_package_prompt(package_rows: Sequence[Dict[str, Any]], *, disclosure_mode: str) -> str:
    if not package_rows:
        return ""
    rows: List[str] = []
    for row in package_rows:
        scripts = ", ".join(f"`{path}`" for path in row.get("script_paths") or []) or "(no scripts listed)"
        refs = ", ".join(f"`{path}`" for path in row.get("reference_paths") or []) or "(no references listed)"
        tests = ", ".join(f"`{path}`" for path in row.get("bundle_paths") or []) or "(no bundle tests listed)"
        rows.append(
            "\n".join(
                [
                    f"- Skill package `{row['skill_name']}` at `{row['skill_dir']}`",
                    f"  - Read first: `{row['skill_md_path']}`",
                    f"  - Use when: {str(row.get('description') or '')[:260]}",
                    f"  - Scripts: {scripts}",
                    f"  - References: {refs}",
                    f"  - Bundle tests: {tests}",
                ]
            )
        )
    return (
        "Folder-style Spreadsheet skills are available as local readable/writable directories. "
        "Each package follows Anthropic-style progressive disclosure: read `SKILL.md` first, inspect `scripts/` "
        "only when the skill applies, and then run/import/adapt the script for this workbook. Package files are "
        "inside the current task directory and may be edited if needed.\n"
```

Added path safety at lines 286-298:

```python
def safe_package_relative_path(path_value: Any) -> Path | None:
    raw = str(path_value or "").strip().replace("\\", "/")
    if not raw:
        return None
    path = Path(raw)
    if path.is_absolute():
        return None
    parts = path.parts
    if any(part in {"", ".", ".."} for part in parts):
        return None
    if parts[0] in {"skills", "bundles"}:
        return None
    return Path(*parts)
```

2. `academic/benchmarks/spreadsheet/executor.py`

Single-shot executor now materializes and injects packages at lines 122-144:

```python
    callable_prompt = write_spreadsheet_skill_library(
        injected,
        base_work_dir,
        disclosure_mode=callable_disclosure_mode or os.environ.get("SPREADSHEET_CALLABLE_DISCLOSURE_MODE") or "full",
    )
    package_prompt = write_spreadsheet_skill_packages(
        injected,
        base_work_dir,
        disclosure_mode=os.environ.get("SPREADSHEET_PACKAGE_DISCLOSURE_MODE") or "progressive",
    )
    trace.callable_skills = callable_prompt["skills"]
    trace.package_skills = package_prompt["skills"]
    trace.prompt_injected_skills = [skill.name for skill in injected]
    system = SPREADSHEET_SYSTEM.format(
        skills=skill_prompt,
        callable_skills="\n\n".join(
            item
            for item in [
                callable_prompt["prompt"] or "(no callable function skills available)",
                package_prompt["prompt"],
            ]
            if item
        ),
    )
```

Notebook per-turn package materialization and update prompt at lines 393-437:

```python
            package_prompt = write_spreadsheet_skill_packages(
                injected,
                base_work_dir,
                disclosure_mode=os.environ.get("SPREADSHEET_PACKAGE_DISCLOSURE_MODE") or "progressive",
            )
            trace.callable_skills = _merge_callable_rows(trace.callable_skills, callable_prompt["skills"])
            trace.package_skills = _merge_package_rows(trace.package_skills, package_prompt["skills"])
...
                added_package_prompt = _spreadsheet_package_update_prompt(
                    [
                        row
                        for row in package_prompt["skills"]
                        if str(row.get("skill_name") or "") in set(new_injected_names)
                    ],
                    execution_mode="notebook",
                )
                step_context_msg = _spreadsheet_step_skill_context_message(
                    added_skill_names=new_injected_names,
                    skill_prompt=added_skill_prompt,
                    callable_skill_prompt=added_callable_prompt,
                    package_skill_prompt=added_package_prompt,
                )
```

Bash ReAct per-turn package materialization and update prompt at lines 742-787:

```python
            package_prompt = write_spreadsheet_skill_packages(
                injected,
                base_work_dir,
                disclosure_mode=os.environ.get("SPREADSHEET_PACKAGE_DISCLOSURE_MODE") or "progressive",
            )
            trace.callable_skills = _merge_callable_rows(trace.callable_skills, callable_prompt["skills"])
            trace.package_skills = _merge_package_rows(trace.package_skills, package_prompt["skills"])
...
                added_package_prompt = _spreadsheet_package_update_prompt(
                    [
                        row
                        for row in package_prompt["skills"]
                        if str(row.get("skill_name") or "") in set(new_injected_names)
                    ],
                    execution_mode="bash_react",
                )
                step_context_msg = _spreadsheet_step_skill_context_message(
                    added_skill_names=new_injected_names,
                    skill_prompt=added_skill_prompt,
                    callable_skill_prompt=added_callable_prompt,
                    package_skill_prompt=added_package_prompt,
                    execution_mode="bash_react",
                )
```

The `[NEW SKILLS]` message now includes package blocks at lines 1435-1452:

```python
    package_block = (
        "\n\nNew folder-style Spreadsheet skill packages:\n" + package_skill_prompt
        if package_skill_prompt
        else ""
    )
...
            f"{skill_prompt}"
            f"{callable_block}"
            f"{package_block}"
            f"{protocol_footer}"
```

Added package-specific per-turn usage prompt at lines 1615-1653:

```python
def _spreadsheet_package_update_prompt(
    rows: Sequence[Dict[str, Any]],
    *,
    execution_mode: str = "notebook",
) -> str:
    if not rows:
        return ""
...
    if execution_mode == "bash_react":
        return (
            "Folder-style skills are local directories with `SKILL.md`, optional `scripts/`, and optional `references/`. "
            "Read `SKILL.md` first; if it matches, inspect/run/import a script instead of rewriting the whole operation.\n"
            "Inspection/run examples:\n"
            "```bash\n"
            "find skills/<skill_name> -maxdepth 3 -type f -print\n"
            "sed -n '1,220p' skills/<skill_name>/SKILL.md\n"
            "sed -n '1,240p' skills/<skill_name>/scripts/<script>.py\n"
            "python skills/<skill_name>/scripts/<script>.py \"$INPUT_XLSX\" \"$OUTPUT_XLSX\"\n"
            "```\n"
            + row_text
        )
```

3. `academic/benchmarks/spreadsheet/prompts.py`

Extractor prompt now permits `skill_package` and requires package files:

```python
- `kind`: use `skill_package` when the reusable behavior is best represented as
  a folder with `SKILL.md`, scripts, and tests; use `executable_tool` when the
  body contains one short concrete reusable openpyxl code idiom; use
  `workflow_guardrail_card` for ordering/inspection workflows; use
  `interface_contract_card` for exact workbook/range contracts.
```

```python
- `metadata.package_format`: for `skill_package`, use "skills_md".
- `metadata.package_files`: for `skill_package`, an object mapping relative
  paths such as "SKILL.md", "scripts/apply.py", and "references/notes.md" to
  exact file contents. Do not use absolute paths or "..".
- `metadata.bundle_files`: for `skill_package`, an object mapping relative
  paths such as "run_tests.py" to unit-test file contents. The test entrypoint
  should import/call files under the sibling skill package and fail nonzero on
  broken behavior.
```

4. `academic/benchmarks/spreadsheet/maintenance/adapter.py`

Package extraction rubric checks at lines 614-640:

```python
    if kind == "skill_package":
        package_files = metadata.get("package_files")
        bundle_files = metadata.get("bundle_files")
        if not isinstance(package_files, dict):
            failures.append("skill_package metadata.package_files must be an object")
        else:
            if "SKILL.md" not in package_files:
                failures.append("skill_package metadata.package_files must include SKILL.md")
            for path, content in package_files.items():
                rel_path = _safe_package_relative_path(path)
                if rel_path is None:
                    failures.append(f"invalid package file path: {path}")
                    continue
                text = str(content or "")
                if str(rel_path) == "SKILL.md" and _word_count(text) > 180:
                    failures.append(f"SKILL.md has {_word_count(text)} words > 180")
                if str(rel_path).endswith(".py") and len(_nonempty_lines(text)) > 80:
                    failures.append(f"package script {rel_path} has {len(_nonempty_lines(text))} non-empty lines > 80")
```

Package raw artifact coercion at lines 1085-1118:

```python
    if kind_lower == "skill_package":
        injection_type = "functional"
...
    if kind_lower == "skill_package":
        package_files = metadata.get("package_files")
        if not isinstance(package_files, dict):
            return None
        cleaned_package_files = {
            str(_safe_package_relative_path(path)): str(content or "")
            for path, content in package_files.items()
            if _safe_package_relative_path(path) is not None
        }
        if "SKILL.md" not in cleaned_package_files:
            return None
        metadata["package_format"] = metadata.get("package_format") or "skills_md"
        metadata["package_files"] = cleaned_package_files
```

Package refiner routing at lines 1943-1952:

```python
    if _is_spreadsheet_package_skill(artifact):
        package_decision = await _run_spreadsheet_package_refiner(
            artifact=artifact,
            test_result=test_result,
            credit_context=credit_context,
            config=config,
            phase=phase,
        )
        if package_decision is not None:
            return package_decision
```

Lightweight terminal package refiner loop at lines 2041-2124:

```python
async def _run_spreadsheet_package_refiner(
    *,
    artifact: SkillArtifact,
    test_result: Dict[str, Any],
    credit_context: Sequence[Dict[str, Any]],
    config: MaintenanceRunConfig,
    phase: str,
) -> Dict[str, Any] | None:
    max_turns = int(config.extra.get("spreadsheet_package_refiner_max_turns", 2) or 2)
    if max_turns <= 0:
        return None
    with tempfile.TemporaryDirectory(prefix="spreadsheet_package_refine_") as tmp:
        work_dir = Path(tmp)
        _write_spreadsheet_skill_packages([artifact], work_dir)
        result_path = work_dir / "failure_report.json"
        result_path.write_text(json.dumps(test_result, ensure_ascii=False, indent=2))
...
            response = await ask_text_llm(
                llm_config=config.llm_config,
                model_name=config.model_name,
                system=(
                    "You are a lightweight terminal refiner for a SpreadsheetBench folder-style skill package. "
                    "Repair SKILL.md, scripts, or bundle tests so the package is reusable and its tests pass. "
                    "Use concise shell commands and Python snippets. Return only one fenced bash block."
                ),
```

Package bundle unit runner at lines 2391-2458:

```python
def _spreadsheet_package_has_bundle_tests(artifact: SkillArtifact) -> bool:
    return bool(_spreadsheet_bundle_files(artifact).get("run_tests.py"))


def _run_spreadsheet_package_bundle_unit(
    artifact: SkillArtifact,
    *,
    config: MaintenanceRunConfig,
) -> SkillTestCaseRun | None:
    if not _spreadsheet_package_has_bundle_tests(artifact):
        return None
    with tempfile.TemporaryDirectory(prefix="spreadsheet_package_bundle_") as tmp:
        work_dir = Path(tmp)
        _write_spreadsheet_skill_packages([artifact], work_dir)
        safe_name = _spreadsheet_package_dir_name(artifact.name)
        entrypoint = work_dir / "bundles" / safe_name / "run_tests.py"
...
            proc = subprocess.run(
                ["python", str(entrypoint)],
                cwd=str(work_dir),
                env=env,
                text=True,
                capture_output=True,
                timeout=env_timeout,
            )
```

5. `academic/benchmarks/tests/test_spreadsheet_evolution.py`

Added test fixture package at lines 2590-2638 and tests at lines 2641-2745.
Key exact assertions:

```python
    assert _is_spreadsheet_package_skill(skill) is True
    assert result["skills"][0]["skill_dir"] == "skills/spreadsheet_package_double"
    assert (tmp_path / "skills" / "spreadsheet_package_double" / "SKILL.md").exists()
    assert (tmp_path / "skills" / "spreadsheet_package_double" / "scripts" / "apply_double.py").exists()
    assert (tmp_path / "bundles" / "spreadsheet_package_double" / "run_tests.py").exists()
```

```python
        assert "New folder-style Spreadsheet skill packages:" in kwargs["prompt"]
        assert "skills/spreadsheet_package_double/SKILL.md" in kwargs["prompt"]
        assert "skills/spreadsheet_package_double/scripts/apply_double.py" in kwargs["prompt"]
```

```python
    assert result.aggregate["passed"] is True
    assert result.aggregate["n_cases"] == 1
    assert [run.variant for run in result.unit_case_runs] == ["package_unit"]
    assert result.unit_case_runs[0].metadata["package_unit"] is True
```

Validation:

```bash
python -m py_compile academic/benchmarks/spreadsheet/skill_runtime.py academic/benchmarks/spreadsheet/models.py academic/benchmarks/spreadsheet/executor.py academic/benchmarks/spreadsheet/prompts.py academic/benchmarks/spreadsheet/maintenance/adapter.py academic/benchmarks/spreadsheet/adapter.py academic/benchmarks/tests/test_spreadsheet_evolution.py
pytest -q academic/benchmarks/tests/test_spreadsheet_evolution.py -q
```

Result:

```text
py_compile passed
56 spreadsheet evolution tests passed
```
