# Maintenance Frontend V2 Checklist

本文档是 `/maintenance` 和 `/method-tests` React V2 重写的验收清单。`/refactor-graph` 本轮保持独立页面。

## Route Scope

| Route | V2 behavior |
| --- | --- |
| `/maintenance` | 加载 React/Vite app，默认显示非 method-validation 实验。 |
| `/method-tests` | 加载同一个 React/Vite app，但默认筛选 `kind=method_validation`。 |
| `/maintenance-docs` | 继续使用现有 docs viewer。 |
| `/refactor-graph` | 继续使用独立 graph 页面；按 skill evolve 时间帧展示 macro/micro overlap graph。 |

## Preserved Features

| Legacy capability | V2 component |
| --- | --- |
| 搜索和选择 experiment/method test | `FileTree` experiment list |
| task/round/global 页面导航 | `FileTree` folder tree |
| “Algorithm Roles” 页面概念 | `Global Pipeline Pages` folder |
| overview metrics | `MetricGrid` |
| player progress slider | `Player` |
| Prev / Next / Next Role | `Player` controls |
| marker rail | `Player` marker rail |
| task 原始 query/messages | `TaskQueryPanel` reads `flow_cards[].run.detail.task.question` |
| 可调导航宽度 | draggable sidebar separator, persisted in localStorage |
| executor/retriever/extractor/bundle/test/refine/store flow | `FlowBoard` React Flow graph |
| role/artifact Inspector | `Inspector` |
| role input/output/debug raw | `Inspector` + payload modal |
| artifact body/interface/bundle/lineage/history | artifact selection + payload modal |
| bundle positive/negative/integration cases | bundle builder card payload tree |
| test with/without case runs and unavailable reason | test card payload tree |
| method case given/model output/algorithm output/assertions | method case card payload tree |
| raw JSON without large default dumps | collapsed `JsonTree` |

## Data Contracts Used By V2

V2 does not read experiment result JSON directly. It uses only:

- `GET /api/maintenance/experiments`
- `GET /api/maintenance/experiment?id=...`
- `GET /api/maintenance/player?id=...`
- docs/navigation links to `/maintenance-docs` and `/refactor-graph`

The adapter is intentionally thin:

- `buildFileTree(detail)` groups normalized `pages` into Train Tasks, Replay Tasks, Rounds, Global Pipeline Pages, Method Cases, and Artifacts.
- `buildFlowModel(detail, pageId, player, frameIndex)` maps `flow_cards[].type/role/title/detail` into fixed industrial role nodes.
- `JsonTree` renders all unknown/raw payloads as collapsed structured trees.

## Legacy Compatibility

The UI must show explicit unavailable states instead of inventing content when historical logs omit data:

- missing role audit rows
- missing `SkillTestCaseRun.input_payload`
- missing `actual_output`
- missing tool calls or raw executor trace
- missing player debug events

These unavailable notes are produced by the API where possible and displayed unchanged by V2.

## Smoke Sample

Use current result data:

```text
bfcl_real_glm_maintenance_2026-05-12__overlap_refactor_debug_5case
```

Expected smoke coverage:

- experiment appears in `/maintenance`
- at least one page has executor, extractor, bundle builder, unit tester, refiner, and skill store nodes
- player has frames and marker rail is clickable
- clicking a flow chip opens the card payload in Inspector/modal
- clicking an artifact opens body/interface/bundle/lineage data
- `/refactor-graph` link opens the independent evolve-frame graph page

## Gap Review Against Original Design

已补齐：

- task 页面显示题目原始 query/messages，而不是只显示 task id 或 role slot。
- 左侧 navigation 支持拖拽调宽，并持久化宽度。
- player 只显示当前 task scoped frames；role-colored progress rail 保留。
- React Flow 节点支持自由拖动并持久化位置。
- role Inspector 以当前 event 的真实 text input/output/messages 为主，raw payload 默认折叠。
- 顶栏仍由各 URL 页面自己的模板/React `TopBar` 提供，未合并成新的全局导航。

仍需人工 smoke 的高风险点：

- 历史实验缺失 `task.question` 或 role audit 时，应显示 unavailable/空状态而不是伪造内容。
- 部分旧 result 没有 `debug_events.jsonl` 时，player scoped API 仍可能需要解析大 JSON，首屏较慢。

## Refactor Graph V2 Checklist

`/refactor-graph` 仍是独立页面，但本轮改为 skill evolving 视角：

- timeline 每帧对应一次 `task_overlap_graph_updated`，即一个 task 完成后的 evidence/skill-library state。
- macro 模式：节点是当前 skill library 中的 skill；segment 证据会优先根据 `source_segments/source_task_ids` 归属到真实 skill，缺失时 fallback 为 `task:<task_id>` group；边颜色和数字表示两个 skill/group 间最大 segment similarity。
- micro 模式：节点是 segment；segment 按 skill/group 包装显示；边表示 segment similarity。
- edge label 可 toggle；选择某个 skill 后可淡化无关边。
- 节点位置使用稳定 deterministic layout，并支持拖拽后持久化；拖动时间轴不会重新随机布局。
- 右侧 inspector 显示当前 evolve event、相关 old/new/updated skills、skill 内容 diff、LLM refactor input/output，以及 `decision.reason`。
