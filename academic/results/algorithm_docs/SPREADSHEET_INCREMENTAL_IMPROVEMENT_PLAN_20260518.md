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
python -m py_compile academic/benchmarks/spreadsheet/adapter.py academic/benchmarks/tests/test_spreadsheet_evolution.py
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

位置：`academic/benchmarks/spreadsheet/adapter.py:34-38`、`1456-1474`、`1701-1722`、`1826-1833`

改动目的：删除 Spreadsheet 本地 `_spreadsheet_credit_candidate_names()` 与 `_list_difference_preserve_order()`，避免算法策略只存在于单个 adapter。

代码片段：

```python
from academic.benchmarks.core.credit_scope import (
    credit_candidate_skill_names,
    skill_exposure_flags,
    skill_exposure_from_mappings,
)

projection = _spreadsheet_trace_projection(detail)
candidate_names = credit_candidate_skill_names(projection)
```

Trace projection 现在直接从公共层取 exposure：

```python
exposure = skill_exposure_from_mappings(metrics, trace)
return {
    "retrieved_skills": exposure["retrieved_skills"],
    "prompt_injected_skills": exposure["prompt_injected_skills"],
    "called_skill_functions": exposure["called_skill_functions"],
    "retrieved_only_skills": exposure["retrieved_only_skills"],
}
```

Skill projection / fallback evidence 复用同一个 flag 计算：

```python
exposure = skill_exposure_flags(artifact.name, projection)
return {
    "retrieved": exposure["retrieved"],
    "injected": exposure["injected"],
    "used": exposure["used"],
}
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

#### 1. Credit prompt 语义改为 exposed/called candidate skills

位置：`academic/benchmarks/spreadsheet/adapter.py:173-177`

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

位置：`academic/benchmarks/spreadsheet/adapter.py:270-313`

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

位置：`academic/benchmarks/spreadsheet/adapter.py:492-542`

改动目的：模型返回代码后、执行代码前，用 AST 解析 `skill_library` 调用，并写入 trace/metrics。

代码片段：

```python
if not trace.code:
    trace.elapsed_s = round(time.monotonic() - t0, 3)
    return BenchmarkResult(...)

trace.called_skill_functions = _called_spreadsheet_skill_functions(
    trace.code,
    callable_prompt["skills"],
)
stdout, stderr, returncode = await asyncio.to_thread(
    _run_code, trace.code, input_copy, output_path, base_work_dir
)

verify["retrieved_skills"] = trace.retrieved_skills
verify["prompt_injected_skills"] = trace.prompt_injected_skills
verify["called_skill_functions"] = trace.called_skill_functions
```

#### 4. Notebook executor 逐 turn 记录 callable 调用

位置：`academic/benchmarks/spreadsheet/adapter.py:680-700` 与 `768-778`

改动目的：notebook 多轮中每个 code cell 都可能调用 skill；需要按 turn 记录，并在 trace 里累积去重。

代码片段：

```python
if code:
    called_this_turn = _called_spreadsheet_skill_functions(
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

verify["called_skill_functions"] = trace.called_skill_functions
```

#### 5. 新增 callable 调用解析器

位置：`academic/benchmarks/spreadsheet/adapter.py:1054-1106`

改动目的：用 AST 稳定识别模型是否真的调用 `skill_library` 中的 function skill，支持 direct import、alias import、module import；语法错误时用文本 fallback。

代码片段：

```python
def _called_spreadsheet_skill_functions(
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
        return _called_spreadsheet_skill_functions_from_text(code, function_to_skill)
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
def _called_spreadsheet_skill_functions_from_text(
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

位置：`academic/benchmarks/spreadsheet/adapter.py:1442-1485`

改动目的：这是本章核心行为变化。credit candidate 从 `retrieved_skills` 改为 `prompt_injected_skills + called_skill_functions`，并把 retrieved-only 放进 audit。

代码片段：

```python
async def assign_credit(...):
    del round_index
    projection = _spreadsheet_trace_projection(detail)
    candidate_names = _spreadsheet_credit_candidate_names(projection)
    candidate_artifacts = [
        artifact for name in candidate_names for artifact in [store.get(str(name))] if artifact
    ]
    if not candidate_artifacts:
        return []
    try:
        payload = await _ask_json(
            system=SPREADSHEET_CREDIT_SYSTEM,
            user=_json_block(
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

位置：`academic/benchmarks/spreadsheet/adapter.py:1696-1743`

改动目的：把 candidate selection 所需字段统一放在 projection 中，LLM prompt 和 heuristic fallback 使用同一份事实。

代码片段：

```python
def _spreadsheet_trace_projection(detail: Dict[str, Any]) -> Dict[str, Any]:
    runs = detail.get("runs") or []
    first = runs[0] if runs else {}
    metrics = first.get("metrics") or {}
    trace = first.get("trace") or {}
    retrieved_skills = metrics.get("retrieved_skills") or trace.get("retrieved_skills") or []
    prompt_injected_skills = metrics.get("prompt_injected_skills") or trace.get("prompt_injected_skills") or []
    called_skill_functions = metrics.get("called_skill_functions") or trace.get("called_skill_functions") or []
    return {
        "retrieved_skills": retrieved_skills,
        "prompt_injected_skills": prompt_injected_skills,
        "callable_skills": metrics.get("callable_skills") or trace.get("callable_skills") or [],
        "called_skill_functions": called_skill_functions,
        "retrieved_only_skills": _list_difference_preserve_order(
            retrieved_skills,
            [*prompt_injected_skills, *called_skill_functions],
        ),
    }
```

Candidate helper：

```python
def _spreadsheet_credit_candidate_names(projection: Dict[str, Any]) -> List[str]:
    names: List[str] = []
    for key in ("prompt_injected_skills", "called_skill_functions"):
        for item in projection.get(key) or []:
            name = str(item).strip()
            if name and name not in names:
                names.append(name)
    return names
```

#### 8. Skill projection 增加 retrieved/injected/used 三态

位置：`academic/benchmarks/spreadsheet/adapter.py:1834-1853`

改动目的：credit assigner 需要知道 skill 是“检索到”“注入到 prompt”“被 function 调用”中的哪一种。

代码片段：

```python
return {
    "skill_name": artifact.name,
    "version": artifact.version,
    "kind": artifact.kind,
    "status": artifact.status,
    "description": artifact.description,
    "body": artifact.body[:1800],
    "interface": artifact.interface.as_dict(),
    "retrieved": artifact.name in set(projection.get("retrieved_skills") or []),
    "injected": artifact.name in set(projection.get("prompt_injected_skills") or []),
    "used": artifact.name in set(projection.get("called_skill_functions") or []),
}
```

#### 9. Heuristic fallback 同样使用 injected/used evidence

位置：`academic/benchmarks/spreadsheet/adapter.py:2250-2272`

改动目的：当 LLM credit assigner 失败时，fallback 不能退回到 retrieved-only 归因。因为 `candidate_artifacts` 已经来自 `_spreadsheet_credit_candidate_names()`，fallback 只会处理 exposed/called skills；同时 evidence 记录三态。

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
        "evidence": {
            "retrieved": artifact.name in set(projection.get("retrieved_skills") or []),
            "injected": artifact.name in set(projection.get("prompt_injected_skills") or []),
            "used": artifact.name in set(projection.get("called_skill_functions") or []),
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

拆分后文件体量：

```text
1318 academic/benchmarks/spreadsheet/adapter.py
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

### 6. `adapter.py`: maintenance adapter and compatibility facade

位置：`academic/benchmarks/spreadsheet/adapter.py:1-1318`

职责：现在主要保留 `SpreadsheetMaintenanceAdapter`、credit/extraction/bundle/refine/macro helpers，以及旧路径兼容导出。

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

- 不拆 maintenance adapter 内的 credit/extraction/bundle/refine/macro helpers；这可以作为下一轮结构重构。
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
- neutral/uncertain 不触发。

## Chapter 3: Bundle replay 分级

目标：先 strict gate，再 with-skill，只有 with-skill 通过才跑 without-skill。

实现要点：
- strict gate 检查 official task snapshot、workbook/golden/range、callable parse/import、prompt budget。
- with-skill fail 时跳过 without-skill，直接记录 failure。
- result schema 记录 strict gate、with/without 结果和 skipped reason。

测试：
- gate fail 不调用 LLM。
- with fail 不跑 without。
- with pass 才跑 without。

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

## Chapter 5: Compact skill projection 与 prompt schema

目标：credit/refiner 不再读取长 skill body，降低 prompt 长度和乱填字段。

实现要点：
- 新增 Spreadsheet compact skill card。
- credit/refiner 使用同一投影。
- extractor/refiner prompt 增加 body/code/applicability 长度约束、参数表、返回值、调用示例、非适用条件。

测试：
- prompt 不含 full body。
- 超长 function skill 被 trim/拒绝。
- mock LLM 多余字段 normalize 稳定。

## Chapter 6: Notebook trace projection

目标：维护 agent 只读 notebook 关键执行信号，不读完整多轮 trace。

实现要点：
- projection 包含关键 code cell、stdout/stderr、exception、changed variables、final answer candidate、expected range。
- credit/extractor/refiner 使用 projection。

测试：
- 多轮 trace 被压缩。
- exception/stderr 保留。
- projection size 有上限。

## Chapter 7: Generic runner 并发下沉

目标：Spreadsheet 训练使用 benchmark-agnostic window concurrency 和 per-skill locks。

实现要点：
- core runner 增加 window concurrency、micro concurrency、per-skill write lock。
- Spreadsheet adapter 只声明 target skills/dependency neighborhood。
- macro 仍在 window barrier 后执行。

测试：
- mock sleep 下 15-task 并发加速。
- 同 skill refine 串行，不同 skill 并发。
- dependency 变化标 stale。

## Chapter 8: 小规模真实验证

目标：验证改动确实降低维护开销且不破坏效果。

协议：
- 章节 1-3 后跑 3 train / 2 test smoke。
- 全章节后跑 10 train / 10 test。
- 报告 success、avg_score、input/output tokens、maintenance role tokens、credit/refiner/bundle calls、wall-clock、actual callable imports/calls。
