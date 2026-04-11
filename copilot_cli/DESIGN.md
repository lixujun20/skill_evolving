# skill_evolving_v1 设计文档

本文件为当前实现的设计说明，覆盖整体架构、各组件职责、已实现特性、关键实现细节、运行与测试说明、以及后续待办（TODO）。文档以中文撰写，面向项目开发者与维护者。

## 1. 总体目标

skill_evolving_v1 目标是建立一个「配备历史 workflow/skill 经验的 react agent」平台（又称 Tool-Cosmos / meta agent），用于：
- 从 Agent 执行 trace 中自动提炼/重构技能（extractor / gardener）
- 对重构后技能进行验证测试（reviewer / tester）
- 维持技能库（版本、检索、embedding）并保证回溯兼容
- 生成可读测试报告与 LLM trace 日志以供人工审查

设计要点：将模糊的 LLM 决策与可复用的确定性 workflow 结合，保证高效、稳定与成本可控。

## 2. 架构概览

主要组件：
- Agent 层
  - Gardener (Extractor)：分析 AgentTrace，生成 RefactoringPlan 与 RefinedSkillResult，提交新版本到 DB
  - Reviewer (Tester)：根据测试用例执行功能/兼容性验证，给出 test report
- 工具层
  - LLM 接口 (`app/llm.py`)：统一 API 封装、prompt 格式化、history trimming 与 Anthropic prompt cache 支持
  - Skills / Tools：可被 Agent 调用的原子能力与封装接口
- 设施层
  - Skill DB (`app/meta_agent/skills/database/`)：Skill、SkillGroup、版本控制、embedding 列（Vector(1024)）
  - Retrieval (`app/meta_agent/skills/retrieval.py`)：基于 ZhipuAI embedding-3 + pgvector 的相似度检索
  - LLM Response Cache (`app/meta_agent/skills/tests/llm_response_cache.py`)：disk-based cache 用于测试阶段降费
  - Test Report Generator (`scripts/generate_skill_test_report.py`)：将 pytest + LLM trace 生成 HTML 报告

目录关键文件：
- app/meta_agent/skills/gardener_agent.py
- app/meta_agent/skills/reviewer_agent.py
- app/llm.py
- app/meta_agent/skills/retrieval.py
- app/meta_agent/skills/tests/llm_response_cache.py
- scripts/generate_skill_test_report.py
- app/meta_agent/skills/tests/integration/*（集成测试）

## 3. 关键设计与实现细节

3.1 LLM 层与 Token 优化
- 问题：System prompt 每次都重复导致 token 爆炸（system_prompt ≈ 1.7k tokens × 多次调用）。
- 优化措施：
  - 在 agent 子类（gardener/reviewer）中设置 `next_step_prompt = ""` 来避免每步注入 NEXT_STEP_PROMPT。
  - 在 `app/llm.py::ask_tool()` 中对 tool/function 历史消息做 trimming，保留最近 N=4 步的完整信息，旧消息截断为 300 字符并标注为已截断。
  - 针对 Anthropic（claude-*）模型，将 system message 包装为带 `cache_control:{"type":"ephemeral"}` 的结构，以利用后端 prompt cache 特性（测试阶段可显著降费）。

3.2 LLM 测试缓存（Disk-based）
- 目的：测试阶段复现/降费。实现位于 `app/meta_agent/skills/tests/llm_response_cache.py`。
- 特点：
  - 基于 SHA-256 的键（model + messages + tool metadata）。
  - 存储目录默认 `~/.skill_llm_cache/`，可通过环境变量 `LLM_CACHE_DIR` 覆盖。
  - pytest fixture (`llm_cache_fixture.py`) 提供自动 patch：当 `LLM_CACHE_ENABLED=1` 且运行 `-m llm` 测试时自动替换 `LLM.ask` / `LLM.ask_tool`。

3.3 检索系统（Embedding + pgvector）
- Embedding：ZhipuAI `embedding-3`，强制 `dimensions=1024` 以匹配 DB 列（Vector(1024)）。
- 检索：`search_similar_skills()` 使用 pgvector 的 `<=>` 距离运算符进行余弦检索；返回带有估计成本与耗时信息的 `RetrievalResult`。
- 测试：7 个 non-LLM 单元测试覆盖生成维度、失败回退、相关性结果等。

3.4 测试报告生成
- 工具：`scripts/generate_skill_test_report.py`。
- 功能：将 LLM trace（trace.log）和 pytest 输出（可选）关联，按测试分组，展示每次工具调用、token 消耗、成本统计与可折叠详情。
- 输出：默认 `~/llm_test_logs/test_report.html`。

3.5 集成测试与技能演化流程
- 目录：`app/meta_agent/skills/tests/integration/`。
- 场景：Dimension 1..N 的多场景 trace（如 code-debug、grade-analysis、multi-role discussion），用于验证 extractor 能否根据 trace 生成正确的重构。
- 策略：第一轮以 minor/major 分类（参数提取、分支增强、算法修改等），后续轮次验证长期演化（版本数、major bump）。

## 4. 运行与测试说明

前提：
- Python 解释器：/data/lixujun/miniconda3/envs/meta-agent/bin/python3
- 项目根：/home/lixujun/AICosmos
- 设置： export PYTHONPATH=/home/lixujun/AICosmos
- 测试 DB：默认 `postgresql+psycopg2://edumanus_user:edumanus_password@localhost:15432/aicosmos_test`（conftest 可覆盖）

常用命令：
- 非 LLM 测试： pytest -q -m "not llm"
- 启用 LLM 缓存并运行 LLM 测试：
  ```bash
  export LLM_CACHE_ENABLED=1
  export PYTHONPATH=/home/lixujun/AICosmos
  /data/lixujun/miniconda3/envs/meta-agent/bin/python3 -m pytest -vv -m llm
  ```
- 生成报告：
  ```bash
  python3 scripts/generate_skill_test_report.py --trace ~/llm_test_logs/trace.log --output ~/llm_test_logs/test_report.html
  ```

注意：conftest 已在 `@pytest.mark.llm` 测试后加入 8 秒 cooldown，避免 API 429。

## 5. 已做改动清单（摘录）
- Gardener/Reviewer: disabled `next_step_prompt` 注入用于减少重复 system prompt
- app/llm.py: 增加 Anthropic prompt cache 包装与历史 trimming
- LLM 缓存模块与 pytest fixture 已加入并在 conftest 自动导入
- Retrieval 模块与 7 个检索测试已实现并通过
- Test report 脚本已实现并能生成 HTML
- Integration 测试文件已创建并可被 pytest 收集

## 6. 成本控制策略
- 利用 prompt 缓存（Anthropic）与本地 disk cache（测试阶段）控费
- 压缩历史消息、移除逐步 NEXT_STEP 提示以减少重复系统 prompt
- 检索 embedding 使用低成本 ZhipuAI embedding-3，并在 DB 中持久化以避免重复调用

## 7. 风险与后续计划

已知风险：
- 真实 LLM 运行仍受网络与速率限制（需代理/Clash 或可用公网），并有一定不可预见的开销
- pgvector/DB 配置在 CI 环境需显式启用 `CREATE EXTENSION vector`；测试 DB 需可达

后续 TODO（从项目 TODO.md 衍生）：
- cost-reduction（验证整套 LLM 测试在启用缓存后 < $0.30/test）
- 完善 retrieval 单元到 CI（目前依赖外网 embedding）
- 为 extractor/refactoring 增加更详细单元测试与回滚策略

## 8. 联系与调试建议
- 本地调试首选：先跑非 LLM 测试，再对单个 LLM 测试启用缓存进行回归
- 若遇到网络/代理问题，检查 Clash/代理节点并替换到可用节点（文档中有示例 curl 切换方法）

---

文档最后更新：2026-04-03T06:42:21.269Z
由 Copilot CLI 自动生成并写入项目目录：`/home/lixujun/AICosmos/app/copilot_cli/DESIGN.md`。

如果需要，我可以把这份文档转成 README 片段或直接提交为 git commit（需你确认 commit message）。


## 9. E2E CLI (`scripts/skill_evolve_cli.py`)

提供一个交互式终端界面，支持从命令行提交 query，并实时查看 retriever、extractor 和 tester 三个阶段的详细行为输出。

### 功能概述
- **Retrieval 阶段**：用 ZhipuAI embedding-3 检索 DB 中已有 skill，以表格展示匹配结果（ID、版本、docstring、耗时、成本）
- **Extractor 阶段**：实时流式打印 Gardener Agent 的 thoughts（`✨`）、工具选择（`🛠️`）、工具参数（`🔧`）、调用结果
- **Tester 阶段**：实时流式打印 Reviewer Agent 的执行过程及测试报告
- **Summary**：汇总三阶段耗时、新生成代码（带语法高亮）、测试通过/失败信息

### 使用方法

```bash
cd /home/lixujun/AICosmos
export PYTHONPATH=/home/lixujun/AICosmos

# 基本用法：传入 query（交互式选择 skill）
python3 scripts/skill_evolve_cli.py --query "student transcript as JSON"

# 使用内置 demo trace（自动 seed skill 并运行）
python3 scripts/skill_evolve_cli.py --demo dim_1_1
python3 scripts/skill_evolve_cli.py --demo int_A

# 提供 AgentTrace JSON 文件
python3 scripts/skill_evolve_cli.py --query "..." --trace /path/to/trace.json

# 指定已有 skill ID（跳过检索交互）
python3 scripts/skill_evolve_cli.py --query "..." --skill-id 3

# 只运行检索，不运行 LLM（快速预览）
python3 scripts/skill_evolve_cli.py --query "..." --dry-run

# 跳过 tester 阶段
python3 scripts/skill_evolve_cli.py --demo int_B --no-tester

# 保存 LLM trace 日志 + 运行
python3 scripts/skill_evolve_cli.py --demo dim_1_1 --log-file ~/llm_test_logs/trace.log

# 查看可用 demo 列表
python3 scripts/skill_evolve_cli.py --list-demos
```

可用 demos：`dim_1_1`, `dim_1_2`, `int_A`, `int_B`, `int_C`

### CLI 选项表

| 选项 | 简写 | 说明 |
|------|------|------|
| `--query TEXT` | `-q` | 检索用的 free-text query |
| `--trace PATH` | `-t` | AgentTrace JSON 文件路径 |
| `--skill-id INT` | `-s` | 直接指定目标 skill DB ID |
| `--demo NAME` | `-d` | 使用内置 demo trace |
| `--list-demos` | | 列出所有内置 demo |
| `--top-k INT` | | 检索返回的最大数量（默认 5） |
| `--threshold FLOAT` | | 相似度阈值（默认 0.3） |
| `--no-tester` | | 跳过 reviewer/tester 阶段 |
| `--dry-run` | | 仅运行 retrieval，不调用 LLM |
| `--db-url TEXT` | | 指定 PostgreSQL URL（或 `SKILL_DB_URL` 环境变量） |
| `--log-file PATH` | | 同时写入 LLM trace 日志 |
| `--full-output` | | 在 CLI 中显示完整的 LLM/工具输出，不做截断 |

### 实现原理（Live Streaming）

Agent 的思考与工具调用过程通过 Python 标准 `logging` 模块暴露（logger 名分别为 `skill_gardener` 和 `skill_reviewer`）。CLI 注册了一个自定义 `RichAgentLogHandler`，将这些日志实时格式化并打印到 Rich Console，支持图标识别（`✨` 思考、`🛠️` 工具选择、`🔧` 参数、`🚨` 错误等）和长文本自动截断。

---

## 10. 单元/子模块测试 (按子模块运行)

为了方便按子模块运行测试，仓库提供了一个小脚本 `scripts/run_tests.sh`（可选可执行）来运行常见的子模块测试集。下面是可用目标及示例：

- all: 运行仓库所有测试（包含 llm 标记测试）
  - 使用： ./scripts/run_tests.sh all
- non-llm: 快速运行仅非 LLM 的单元测试（节省时间与费用）
  - 使用： ./scripts/run_tests.sh non-llm
- llm: 运行所有带有 `@pytest.mark.llm` 的测试（将触发真实 LLM 请求）
  - 使用： LLM_CACHE_ENABLED=1 ./scripts/run_tests.sh llm
- retrieval: 只运行检索相关单元测试
  - 使用： ./scripts/run_tests.sh retrieval
- integration: 运行集成测试目录（通常为 LLM 标记）
  - 使用： ./scripts/run_tests.sh integration
- report: 基于已存在的 LLM trace 日志生成 HTML 报告
  - 使用： ./scripts/run_tests.sh report /path/to/trace.log /path/to/output.html

脚本默认使用项目推荐的 Python 解释器：
`/data/lixujun/miniconda3/envs/meta-agent/bin/python3`，并自动设置 `PYTHONPATH=/home/lixujun/AICosmos`。

注意事项：
- 若需要可执行权限： `chmod +x scripts/run_tests.sh`
- 对于 LLM 测试，建议先设置 `export LLM_CACHE_ENABLED=1` 以降低费用并提高重现性
- conftest 中包含 8 秒的 cooldown（仅对 `@pytest.mark.llm` 测试），脚本不会覆盖该行为


### 若当前不支持按子模块运行（场景说明）

本项目已经支持按子模块执行测试（通过 pytest 的路径/marker 选择以及本次提供的脚本）。若在某个环境中遇到限制（例如 CI 环境没有外网，或测试 DB 不可访问），建议：

1. 在 CI 中把网络相关测试标记为 `llm` 并在 CI 配置中跳过（`pytest -m "not llm"`）。
2. 在需要在 CI 运行检索测试时，mock 掉外部 Embedding API 或使用已缓存 embeddings（`app/meta_agent/skills/tests/test_retrieval.py` 已包含 mock 分支）。
3. 若 conftest 的自动 fixture 导致测试隔离问题，可通过环境变量禁用缓存自动注入（`unset LLM_CACHE_ENABLED`）或修改 `conftest.py` 临时注释 `_llm_cache_autouse` 以排查问题。


---

已将此部分内容追加到本设计文档，并创建了辅助脚本 `scripts/run_tests.sh`。
