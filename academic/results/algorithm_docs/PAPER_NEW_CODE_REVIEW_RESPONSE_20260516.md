# paper_new 代码审查问题回复与修改计划

日期：2026-05-16

范围：只回答审查问题并给出计划，不修改实现代码。

参考位置：

- 伪代码：`academic/paper/paper_new.md:174-210`
- 执行器：`academic/benchmarks/bfcl.py`
- 通用 skill store / retrieval：`academic/skill_repository/store.py`
- BFCL 50/50 driver：`academic/benchmarks/bfcl_related_task_experiment.py`
- LLM maintenance / extraction / refactor：`academic/skill_repository/llm_maintenance.py`、`academic/benchmarks/bfcl_llm_maintenance.py`

## 174-190：`execute_with_skills`

### 1. if/else 过多，应该用多态改善结构

当前判断：这个意见成立。

现在 `academic/benchmarks/bfcl.py` 的执行器把 retrieval、skill 注入、tool execution、error retry、watchdog stop、debug logging 混在同一个过程式循环里。典型位置包括：

- `bfcl.py:590-617`：每个 turn 预检索。
- `bfcl.py:993-1035`：tool error 后二次检索。
- `bfcl.py:2533-2581`：watchdog stop policy。

问题不只是代码风格。当前结构会让算法部件边界不清楚，例如“第二次检索策略”和“停止策略”很难独立 ablation，也很难和 `paper_new.md` 中的 `Ret / Exe / MS` 对齐。

计划：

- 引入 `SkillRetrievalPolicy`：封装 turn-start retrieval、error-aware retrieval、阈值、top-k、tag/embedding backend。
- 引入 `SkillInjectionPolicy`：封装 prompt skill、tool skill、audit-only skill 的拆分和注入。
- 引入 `TurnExecutionPolicy`：封装一次 LLM step、tool call 执行、环境反馈收集。
- 引入 `TerminationPolicy`：封装 EOS、环境完成信号、重复调用安全停止，避免 watchdog 直接知道 benchmark expected。
- 引入 `RetryPolicy`：决定什么时候允许根据错误重新检索 skill，以及二次检索是否能改变注入内容。
- 保留 BFCL adapter 的 task/schema/official checker 逻辑，不另起 executor；只是把现在的 if/else 抽成可替换策略对象。

目标结构是：主循环只表达伪代码语义，即 `retrieve -> execute one turn/step -> append trace -> termination decision`，具体策略由对象负责。

```
comment:
没问题，我们其他代码也该做一下类似的重构了，包括目录结构和文件命名也比较乱。
```

```
回复：
同意。这里不应该只局部重构 `bfcl.py`，否则会把复杂度从一个大函数搬到一堆同样混乱的小文件里。建议后续做一次 package-level 整理：

- `academic/benchmarks/bfcl/adapter.py`：BFCL task/tool/schema/official backend adapter。
- `academic/benchmarks/bfcl/executor.py`：只保留执行主循环。
- `academic/benchmarks/bfcl/policies.py`：retrieval / injection / retry / termination policy。
- `academic/benchmarks/bfcl/maintenance.py`：BFCL-specific extraction、bundle、refactor wrapper。
- `academic/skill_repository/retrieval/`：通用 retrieval backend，不写 BFCL 语义。
- `academic/skill_repository/maintenance/`：extractor/refiner/bundle tester agent 与 prompt。

重构顺序要先做接口边界，再移动文件，避免纯 rename 造成难 review。第一步我会先抽 policy 类并保持旧路径兼容，第二步再整理目录和 import。
```


### 2. 某一轮失败后再次检索 skill，这次和之前有何区别，是否真的有帮助

当前实现中，两次检索确实不同。

第一次检索在 `bfcl.py:590-607`，query 主要是当前 turn 的用户输入，并附带 BFCL domain/tool context。第二次检索在 `bfcl.py:993-1035`，只在 tool 执行返回 error 后触发，query 由 `_error_aware_skill_query` 构造，包含：

- 全 task 文本。
- 当前 turn 文本。
- 失败 tool name。
- 已调用 arguments。
- error string。
- 一个显式提示：需要 exact schema、workflow ordering、literal args、id reuse、dependency-aware retry 相关 skill。

因此第二次检索的设计意图是：第一次按任务语义找 skill；第二次按真实失败现象找修复 skill。

但从 BFCL 的实际反馈形态看，这一步的有效性需要谨慎。它只在以下情况下可能有帮助：

- 环境或 wrapper 返回了可解释的 schema/argument error。
- 错误字符串能暴露参数名、类型、顺序、状态依赖等信息。
- skill store 中已有针对类似 error 的局部 contract skill。

它在以下情况下大概率无帮助，甚至增加噪声：

- BFCL official 执行只给最终 validity，不给足够细的中间错误。
- 错误来自模型已经偏离任务目标，error query 会把错误工具名强化进检索。
- 检索 backend 仍是 token/tag，错误字符串中的偶然 token 可能召回污染 skill。

计划：

- 将二次检索纳入 `RetryRetrievalPolicy`，明确触发条件：只有存在结构化 tool error，且 error 包含 schema/type/missing-required/invalid-state 等可行动信号时才触发。
- 记录二次检索是否新增了 skill、是否被模型实际使用、是否改善同 turn 后续调用。
- 做 ablation：`turn_start_only` vs `turn_start+error_retry`，用 evidence 判断是否保留默认开启。
- 避免二次检索把失败 tool 名无条件作为强 tag；失败 tool 只能作为候选 context，不能压过 task allowed tools 和 domain constraints。

```
comment:
我想起来了，之前说过要通过报错信息之类来决定调用什么skill。我以为这算同一轮的，而不是检索两次？因为上一轮生成已经过去了，你可以统一成是：根据上一轮response的信息，包括执行结果、报错信息等，决定下一轮的retrieve
```

```
回复：
你的表述更准确。现在代码里叫 “tool_error_retry retrieval”，看起来像同一个 step 里额外检索一次；算法上应统一成：

`Ret(q, previous_observation, S, MS)`

其中 `previous_observation` 可以包含上一轮 assistant response、tool calls、tool outputs、error、状态变化和已经注入/使用过的 skill。这样每次进入下一次模型生成前只做一次 retrieval，retrieval 的输入自然包括上一轮反馈，而不是概念上分成“第一次检索”和“失败后第二次检索”。

实现计划应调整为：

- turn/step 开始时构造 `RetrievalContext(query, trace_so_far, last_observation)`。
- 如果上一轮没有 error，就是普通语义检索。
- 如果上一轮有 error，error 信息只是 `last_observation` 的一部分，进入同一个 retrieval policy。
- 删除“在 tool call 失败后立即刷新 system prompt 再继续”的特殊路径，改成下一次 LLM step 前统一更新 retrieval/injection。

这样更忠实于 paper_new 的第 184 行，也更容易做 ablation：是否把 error observation 放进 retriever。
```

### 3. watchdog 看 `extra_calls` 是否合理

当前判断：重复调用 guard 合理；`extra_calls` 自动停止存在方法论风险。

当前 `_TurnWatchdog` 在 `bfcl.py:2533-2581` 做两类 stop：

- `repeated_call`：同一 turn 内出现完全相同 tool call signature，认为陷入循环。
- `all_expected_covered_and_extra`：expected tool name 已覆盖后，又连续出现纯 extra call 达到阈值。

第一类是安全 guard，主要防 runaway 和 API 成本爆炸，原则上可以保留。

第二类不够干净。它依赖 benchmark expected 来判断“应该已经做完”，这相当于在模型不知道自己完成时，由外部 oracle 帮模型退出。如果环境本身发出了完成信号，提前结束是合理的；如果没有，这会污染 executor 能力评估，因为执行器使用了 expected answer 的结构信息。

计划：

- 将 `repeated_call` 保留为 safety stop，但记录为 `safety_terminated`，并在结果中单独标注。
- 默认禁用 `all_expected_covered_and_extra`，或只作为 debug/ablation 模式。
- 如果 official environment 或 adapter 能提供明确完成信号，则改成“收到环境完成信号就结束本轮”。
- 如果没有环境完成信号，则 executor 必须等模型 EOS / stop，不能用 expected coverage 替模型判断完成。
- 后续实验报告中分开统计：正常 EOS、环境完成、重复调用 safety stop、其他异常 stop。

```
comment：
好的，对于BFCL我其实想知道一个明确的结论：会提供这个信号吗？
```

```
回复：
明确结论：当前 BFCL official executable backend 不提供“本 turn / 本 task 已完成”的在线完成信号。

我复查了当前 wrapper：`BFCLOfficialEnvironment.call()` 调用 official `execute_multi_turn_func_call`，每次只返回该 tool call 的 output 和 error。它不会返回 `done/is_complete/task_finished` 这类字段。当前 executor 的 turn 结束主要依赖模型停止继续发 tool call；official validity 是事后 checker，对完整 tool-call trace 做最终判定。

因此 BFCL 上不能用“环境完成信号”作为在线停止条件。可接受的停止条件只有：

- 模型没有继续发 tool call，即自然 EOS / assistant final。
- max step budget。
- repeated identical call 这类纯 safety guard。

不应使用 expected coverage 推断完成；这在 BFCL 中是答案泄露。当前 `all_expected_covered_and_extra` 应该从默认链路移除，只能保留为 debug/diagnostic ablation。
```

### 4. 当前 retrieve 是否真的接了 embedding，大规模 for 循环会不会慢

当前判断：普通 skill retrieval 没有接 embedding。

`ArtifactStore.retrieve_audit` 在 `academic/skill_repository/store.py:301-370` 主要使用：

- `_tokenize(query)`。
- `_cosine(q, _tokenize(artifact.retrieval_text()))`。
- tag match score。
- BFCL predicate / rerank key。

embedding 目前主要用于 trace segment overlap/refactor graph，例如 segment vector index 和 overlap graph 相关流程，不是 executor 测试时的 skill retrieval backend。

复杂度方面：

- 当前实现是 store 全量遍历，复杂度约为 `O(number_of_skills * retrieval_text_length)`。
- 对当前 50/50、小规模 skill store 来说通常不是主要瓶颈，LLM execution / extraction / bundle test 才是主要耗时。
- 如果扩展到上千或上万 skill，并且每 turn 都 retrieve，for 循环会成为明显瓶颈。

计划：

- 增加 `RetrievalBackend` 接口：`LexicalTagRetrievalBackend` 保留现状，`EmbeddingRetrievalBackend` 负责向量检索。
- 短期不必为了 50/50 强行换 ANN；先加 backend 抽象和统计，避免过度工程。
- 中长期使用同一个 embedding service，但建立独立 skill vector index，支持 metadata prefilter：domain、allowed_tools、status、pending/solid。
- 检索流程建议为：硬过滤 domain/tool/status -> lexical/tag 粗排 -> embedding top-k 或 hybrid rerank -> LLM/context injection。

```
comment:
没问题，按你说的做成多流程的recommendation pipline. 我前面还讨论了这个embedding和overlap那边的embedding是否要共用，除了对整体文本做embedding，还有什么其他策略嘛？我对此不太懂，如果没有，可以做成和overlap复用的，即对整体skill的重要文本做embedding
```

```
回复：
建议先复用 overlap 那套 embedding 服务和维度配置，但 skill retrieval 使用单独的 skill index。也就是“同一个 embedding model，不同的 index/table 和不同的文本投影”。

最小可行策略：

- 对整体 skill 的重要文本做一个 embedding：`name + description + interface.summary + interface.usage + body 摘要 + tags/domains/tools`。
- 与 query / current observation embedding 做相似度。
- 再和 lexical/tag score 做 hybrid rerank。

可选的更细策略，但不一定第一版就做：

- 多向量 skill：分别 embed `description/interface/body/evidence`，query 与多个向量取 max 或加权和。
- late interaction：先用整体向量召回 top-k，再用轻量 lexical/tag/metadata 精排。
- evidence-aware embedding：对 skill 的 source trace segment 单独建向量，当前 query 也能召回“产生这个 skill 的证据片段”。

我建议第一版只做整体 skill 重要文本 embedding，因为实现便宜、可解释，也能和 overlap 的 segment embedding 共享 provider。后续如果发现召回仍差，再加多向量。
```

## 184：`retrieve_audit` 是否会构成最终注入的 skill

`retrieve_audit` 本身不是最终注入层，它是通用 store 的候选召回和审计接口。

当前链路是：

- `bfcl.py:593` 调用 `artifact_store.retrieve_audit(...)`。
- BFCL 层传入 `_bfcl_skill_matches_turn(...)` 作为 predicate，负责 involved class、allowed tools、forbid keywords 等任务约束。
- BFCL 层传入 `_bfcl_skill_rerank_key(...)` 做 tool overlap / intent / source evidence rerank。
- `_retrieved_from_audit(...)` 将 audit selected rows 转回 artifact。
- `_split_skills_for_injection(...)` 决定进入 prompt injection 还是 skill tool injection。

因此 `retrieve_audit` 输出的 `selected` 是“通过当前检索策略的候选”，不是无条件注入。最终是否注入由 BFCL executor 的 predicate、top-k、min-score、injection mode、prompt construction 共同决定。

你提到的 `involved_class` 这类 BFCL 内部信息，目前确实应该由 BFCL 层管理，而不是让通用 `ArtifactStore` 硬编码 BFCL 语义。这个分层是合理的。但现有风险是：如果 BFCL predicate 过宽，`retrieve_audit` 的 selected 就会进入注入。因此后续要重点测试 predicate 的硬过滤是否足够严格。

计划：

- 保持 `retrieve_audit` benchmark-agnostic。
- 在 BFCL 层强化 `RetrievalPolicy` 的硬约束：domain/tool/status/pending/solid/negative-credit。
- 在 audit 输出中明确区分 `candidates`、`selected_for_retrieval`、`selected_for_injection`，避免误读。

```
comment:
好的
```

```
回复：
这里后续实现时我会把 audit 字段命名改清楚：`retrieval_candidates`、`retrieved_candidates`、`injected_skills` 分开记录，避免把 store 层候选误认为最终注入。
```


## 193-210：`extract_new_skills_prior_as_pending`

### 1. `existing_artifacts` 当前给了 store 全体，是否不应该

当前判断：这个问题成立，现实现不忠实于伪代码意图。

`bfcl_related_task_experiment.py:2336-2340` 在每个 train task 后调用：

```python
extract_bfcl_skill_artifacts_llm(
    results,
    tool_schemas=tools,
    existing_artifacts=store.all(),
    ...
)
```

这等于把全量 skill store 作为 extractor context。伪代码 `Ext(trace, MS)` 表达的是：prior extraction 主要从本次 trace 和 meta-skills 中提取 pending skills，而不是扫描全库。

当前传全库可能有两个原始动机：

- 帮 extractor 避免重复命名或重复提取。
- 让 extractor 知道已有 skill，便于 refine/依赖。

但副作用更大：

- prompt token 成本随 store 增长。
- 旧 skill 会污染当前 trace extraction，导致从无关 trace 迁移规则。
- prior pending skill 的语义变得不纯，混入全库 summary 后很难判断来源。

计划：

- prior extraction 默认只输入本次 trace、tool schemas、meta-skills/rules。
- `existing_artifacts` 改成小型 compact projection，仅用于去重，不用于生成内容。
- projection 来源限制为：本次执行中 retrieved/injected/used skills、同 task/domain 下 top-k 近邻 skill、名称冲突候选。
- 在 prompt 中明确：existing artifacts 只能用于去重、依赖声明和避免重复，不得作为新 skill 的主要 evidence。
- 如果要做 posterior/refactor extraction，再由 overlap graph 提供 candidate trace/skill evidence，而不是在 prior extraction 阶段塞全库。

```
comment:
我觉得就不要给prompt里加existing artifacts了，若提取的名字和skill中的重合，交给extractor自己做判断。可能的操作包括：修改名字，放弃添加这个skill，对已存在的skill做修改。
```

```
回复：
同意。更干净的设计是：prior extractor 不看现有 store，只根据当前 trace 和 meta-skills 产出 candidate operations，而不是直接决定写库。

我会把计划改成两阶段：

1. `Extractor(trace, MS)`：只看当前 trace，输出候选 skill，以及它认为这是 `new_skill / refine_existing / discard` 中哪一种意图；但此阶段不提供全库内容。
2. `RepositoryArbitrator(candidate, store_index)`：在 extractor 输出之后做库级仲裁。它可以基于名字、embedding、tag/domain/tool、接口相似度找到冲突项，再决定：
   - 改名后新增。
   - 放弃添加。
   - 触发对某个已有 skill 的 refine。
   - 合并为 refactor/promotion 候选。

如果需要 LLM 判断冲突，只给它候选 skill 和少量冲突 skill，而不是 full store。这样能避免 prompt 污染，又保留“修改已有 skill”的能力。
```
```comment
没必要，每次extractor提取到重名skill的时候再进入仲裁，而且只仲裁这一个重名skill。那些本质是同样的但是名字不同的skill要去重的话可能有点贵，如果非要做的话，放到marco_maintenance_step去做
```

```
回复：
同意。这里不应该做全库 arbitration，也不应该在每次 prior extraction 后做语义去重。

修正后的计划：

- prior extraction 后只做 exact-name conflict check。
- 只有 candidate skill name 与已有 skill 完全重名时，才触发轻量仲裁。
- 轻量仲裁只看这个 candidate 和同名 existing skill，不扫描全库。
- 仲裁动作限制为：rename candidate、discard candidate、refine same-name skill、append as new version candidate。
- 不在 online extraction 路径处理“名字不同但语义重复”的情况。
- 不同名字但语义重复的 skill 去重放到 `macro_maintenance_step`，按 round/checkpoint 批处理，并先用 embedding / metadata / usage statistics 便宜筛候选，避免每个 task 都付高成本。

因此这里的组件名也应从 `RepositoryArbitrator(candidate, store_index)` 改成 `NameConflictArbitrator(candidate, same_name_skill)`；真正的 repository-level semantic dedup 只属于 macro maintenance。
```

### 2. `allowed_tools` 是什么意思，会对决策起什么作用

当前 `allowed_tools` 是 skill 的适用工具范围 metadata，不是 executor 的权限系统。

它目前起这些作用：

- 生成 tag：`academic/skill_repository/store.py` 会从 metadata 派生 `tool:*` tags。
- 检索过滤：BFCL predicate 会比较 task tools / expected tools 和 skill allowed tools。
- rerank：`_bfcl_skill_rerank_key` 会根据 query 与 allowed tools 的重合度加权排序。
- 报告和 debug：帮助解释某个 skill 为什么被召回。
- refactor/extraction metadata：记录一个 skill 的来源工具范围，避免跨工具误用。

它不应该起这些作用：

- 不应该授权模型调用工具；真正可调用工具仍由 BFCL tool schema / official environment 决定。
- 不应该替代 task schema 校验；skill 只能提示，不能改变工具定义。
- 不应该作为唯一适用性判断；还需要 domain、intent、forbid keywords、credit evidence 等约束。

计划：

- 在文档和类型注释中把 `allowed_tools` 定义为 “retrieval/injection scope hint”。
- 如果 skill 是知识类/流程类而非具体函数类，允许 `allowed_tools=[]`，但必须有 domain/intent scope。
- BFCL retrieval policy 中把 allowed_tools 作为硬过滤条件之一，但不是唯一条件。

```
comment:
我还是不明白他的含义，什么叫「适用工具范围」？你说通过和task expected tools的匹配度来过滤，这是泄露答案。你确定没有把他加到non-debug链路吧？
```

```
回复：
你指出的是关键问题。严格说，`allowed_tools` 更准确应叫 `skill_tool_scope` 或 `tool_provenance`：它表示“这个 skill 的规则来自或适用于哪些工具 schema / tool family”，不是 benchmark expected answer，也不是执行权限。

关于是否泄露：当前代码里确实有 non-debug 泄露风险，不应保留。

我复查到至少三处问题：

- `_bfcl_skill_matches_task()` 里 `task_tools.update(_expected_tool_names(task))` 会把 expected answer 的 tool names 放入 retrieval filter。这是 non-debug 链路，属于答案泄露。
- `_turn_skill_constraints()` 会把 `_expected_tool_names_for_turn()` 写进 prompt：`Expected tool focus...`。这也是 non-debug 链路，属于答案泄露。
- `_TurnWatchdog(_expected_tool_names_for_turn(...))` 用 expected tool 判断 extra-call stop，同样不该在正式执行链路使用。

正确做法：

- 运行时 retrieval 只能用 task 暴露给模型的信息：tool schema、involved_classes、metadata.path 中的可用工具集合、user query、历史 observation。
- `allowed_tools / skill_tool_scope` 只能和“当前 task 可用工具集合”匹配，不能和 expected answer 匹配。
- expected tools 只能用于离线 scoring、debug_hints adapter、analysis，不进入正式 executor prompt/retrieval/stop。

所以这部分计划需要升级为 bug fix：移除 non-debug 中所有 expected-tool 依赖，并补测试确保 prompt、retrieval audit、watchdog 在正式模式下不包含 expected answer。
```
```
comment:
这是严重的bug，这影响到baseline了嘛？如果每一轮都给他看了expected tool call，结果就不可信。evolve应该肯定是受影响了？
你说的这个含义，我觉得只适用于知识类的skill，而且只适用于针对某个工具调用的提醒？我感觉这个字段不太普适，似乎应该去掉。现在事实上这个字段对于效果的作用大吗？
```

```
回复：
是严重 bug，需要分两层判断影响范围。

第一，普通 baseline 是否受影响，取决于 baseline runner 是否走了这些 BFCL skill executor / adapter 逻辑。如果 baseline 是纯官方 BFCL executor、没有 skill retrieval、没有 skill prompt、没有 debug_hints，那么它不应该看到 expected tool calls；但如果 baseline 复用了同一个 `filter_bfcl_tools_for_task(..., include_expected_tools=True)`、`_turn_skill_constraints()` 或 watchdog expected coverage 路径，那结果就不可信，需要重跑。就当前代码形态看，`adapter_mode == "debug_hints"` 时会显式把 expected tool names 写入 user message；这类结果只能作为 debug，不应进主表。非 debug baseline 必须加测试证明 prompt 里没有 `Likely required tool names`、`Expected tool focus` 或 expected call names。

第二，evolve 肯定存在受影响风险，而且影响比 baseline 更复杂。即使 executor prompt 不直接暴露 expected calls，maintenance / extraction / credit 链路里也出现了 expected tool names，例如 `llm_maintenance.py` 里会从 expected calls 构造 `expected_tool_names` 和 `focus_rule_hint`。这会让 extractor/refiner 学到带答案痕迹的 skill 或 meta-skill。因此之前依赖这些路径的 evolve 结果不能当作严格无泄露结果，需要标注为 contaminated / diagnostic，并在移除 expected-tool 依赖后重跑。

关于 `allowed_tools`，你的判断也对：这个字段不应该是普适的核心字段，更不应该参与“和 expected tools 匹配”的正式筛选。它最多适用于两类情况：

- function/interface contract skill：这个 skill 明确约束某几个工具的参数、调用顺序或返回值复用。
- knowledge skill 中非常局部的工具提醒：例如“某工具返回的 id 后续要复用”，此时它是 provenance/scope，不是答案提示。

对 workflow skill、general rule skill、meta-skill，`allowed_tools` 往往会误导，因为它把一个跨工具流程硬压成工具列表。更好的字段应拆成：

- `source_tools`：这个 skill 从哪些工具调用证据中归纳出来，只用于 provenance/debug/credit。
- `governed_tools`：这个 skill 真正约束哪些工具的用法，可选字段，只对 function contract 类 skill 有意义。
- `domain` / `intent` / `applicability` / `non_applicability`：正式 retrieval 更应该依赖这些语义范围。

现在这个字段对效果的作用不小，因为它参与 tags、retrieval/rerank、prompt metadata 和 debug report；在 BFCL 里尤其容易把工具名信号放大。因此计划应该改为：正式链路先停止把 `allowed_tools` 当硬过滤或强 rerank 依据，只保留为弱 metadata / audit；同时彻底禁止它和 expected answer 匹配。后续如果 ablation 证明去掉后召回显著下降，再只为 function contract skill 引入更窄的 `governed_tools`。
```

### 3. `EXTRACT_SYSTEM` 太 general 且像 debug patch，应重写为软件工程原则并给 one-shot

当前判断：这个意见成立。

`academic/skill_repository/llm_maintenance.py:45-103` 的 `EXTRACT_SYSTEM` 已经混入多条针对历史 bug 的局部补丁。例如：

```text
When an extra exploratory call should be removed, prefer a positive local rule...
```

这条规则本身有价值，但现在写法像事后补丁，没有放进统一的软件工程原则中。更好的 extractor prompt 应该围绕三个核心 norm：

- Correctness：必须由 trace 中的因果证据支持，不能把偶然相关当规律；函数类 skill 必须尊重真实 tool schema、参数名和环境反馈。
- Reusability：skill 只抽取跨任务可复用的局部 contract / workflow / decision pattern；scope 要窄到可检索、可测试，又不能窄到只复述单个 task。
- Maintainability：skill 要有清晰接口、适用条件、反例/不适用条件、版本原因和依赖；避免混合多个无关规则。

建议 one-shot：

输入摘要：

```text
Trace evidence:
- Task asks to buy 3 AAPL shares.
- Model first called get_current_time(), which was not needed.
- Then it called get_stock_info(symbol="AAPL") and place_order(symbol="AAPL", quantity=3, order_type="buy").
- Expected calls only require stock lookup and order placement.

Bad extraction:
- "Never call get_current_time in trading tasks."

Good extraction:
{
  "name": "direct_stock_order_after_price_lookup",
  "kind": "workflow_guardrail_card",
  "description": "For stock purchase tasks with explicit symbol and quantity, retrieve stock info and place the order directly without unrelated exploratory calls.",
  "body": "When the user already provides the ticker, side, and quantity, first call the stock lookup/order prerequisite required by the schema, then call the order tool with the same literals. Do not add unrelated time/account exploration unless the task explicitly asks for it or the schema requires it.",
  "interface": {
    "usage": "Use only for trading/order tasks where the order fields are already specified.",
    "input_contract": {"evidence_required": ["explicit ticker", "explicit quantity", "order intent"]},
    "output_contract": {"expected_effect": "fewer exploratory calls while preserving required tool order"}
  },
  "metadata": {
    "domains": ["TradingAPI"],
    "allowed_tools": ["get_stock_info", "place_order"],
    "forbid_keywords": ["time lookup required"]
  }
}
```

这个 one-shot 的重点不是“禁止 time”，而是用 correctness/reusability/maintainability 约束成一个正向、局部、可测的工程规则。

计划：

- 重写 `EXTRACT_SYSTEM` 的结构：role -> input contract -> SE norms -> extraction decision checklist -> output schema -> one-shot。
- 把历史 debug guardrail 保留为 checklist 的子项，而不是散落规则。
- 要求 extractor 显式输出 `evidence_span` / `scope` / `non_applicability`，用于后续 credit assignment 和 filter。

```
comment:
我觉得对三类skill可能都需要一个few shot。
历史patch最好不要放到prompt，这部分严格来说可以通过TRL链路放到meta skill里面，以及放到后续对skill的refine环节。
```

```
回复：
同意。base extractor prompt 应该保持原则化、稳定，不应该堆历史 bug patch。历史 patch 应迁移到两类动态机制：

- TRL/meta-skill：从运行反馈中总结出来的 extractor rule of thumb。
- refine 环节：针对某个具体 skill 的错误做局部修正，而不是污染所有后续 extraction。

few-shot 建议覆盖三类 skill：

- Function / interface contract skill：参数名、工具顺序、ID reuse、schema constraint。
- Workflow skill：多步任务中的稳定决策流程，但不绑定单个 task literal。
- Knowledge / rule skill：领域规则、选择原则、stop condition、不要做无关探索。

每个 few-shot 都应包含 bad extraction 和 good extraction，重点展示：

- 正确证据边界。
- 可复用但不泛化过度。
- 明确适用条件和不适用条件。
- 如何在 maintainability 上写清 interface/scope。

我会把之前的 “extra exploratory call” 例子从硬编码 rule 改成 function/workflow few-shot 的一个案例，或放进 meta-skill 初始规则，而不是 base prompt patch。
```

### 4. `online_refactor_budget > 0 and len(train_details) >= 3` 是什么；是否对所有 clique refactor；是否重复；和 round-end refactor 什么关系

当前含义：

`bfcl_related_task_experiment.py:2378` 的 `online_refactor` 是 train round 内的增量 overlap/refactor。每个 train task 结束后：

- 先从当前 task trace prior extract pending skills。
- 把当前 task segments 加进 segment index。
- 增量更新 overlap graph state。
- 如果当前 round 已至少有 3 个 train details 且本 round online budget 还没用完，就调用 `run_bfcl_overlap_refactor_llm`。

它对应的是“训练过程中尽早发现跨 trace overlap，并尝试生成 shared skill / promote pending skill”。

是否对图上所有 clique refactor：

- `run_bfcl_overlap_refactor_llm` 会构建或 materialize overlap graph。
- 调用 `find_refactor_cliques(graph)` 得到 clique 列表。
- 默认最多尝试 `BFCL_REFACTOR_MAX_CLIQUES` 个 clique，当前默认是 3。
- 所以不是对所有 clique 都调用 LLM，只对排序后的前若干 clique 尝试。

是否会重复提取：

- 当前有一层去重：`exclude_segment_sets=seen_refactor_cliques`，对完全相同的 segment id 集合跳过。
- online refactor 后，会把 attempt 的 segment id tuple 记入 `seen_refactor_cliques`。
- round-end refactor 调用也可以接收这个 exclude set。

但这只是精确 clique set 去重，不足以避免重复 skill：

- 同一语义 overlap 可能对应不同但高度重叠的 segment set。
- 同一 clique 经 LLM 可能提取出与已有 skill 语义重复但名字不同的 shared skill。
- `existing_skills=store.all()` 会传给 refactor LLM，但目前没有硬性的 semantic duplicate gate。

`online_refactor` 和 `_run_round_refine_and_refactor` 的关系：

- online refactor：round 内、task 后、预算较小，目标是尽早把 overlap 变成 shared skill，并可能 promote pending skill。
- round-end `_run_round_refine_and_refactor`：round 结束后的 batch maintenance，包含 bundle build、bundle test、refine，再跑一次 overlap refactor。

二者关系合理，但边界还不够清晰。当前风险是：

- online 和 round-end 都可能对相近 clique 做 LLM refactor。
- exact segment-set 去重不能防 semantic duplicate。
- refactor LLM 输入 `existing_skills=store.all()` 也可能带来全库污染和 token 成本。

计划：

- 增加 `RefactorGroupRegistry`，维护 `refactor_group_id`、segment set hash、near-duplicate segment Jaccard、source task set、shared skill name、semantic signature。
- 对 clique 做两级去重：精确 segment set 去重 + 近似 overlap 去重。
- 对 shared skill 做提交前去重：name normalized、allowed_tools/domain overlap、embedding/text similarity、interface contract similarity。
- 把 online refactor 的职责限定为“发现和提交高置信 shared skill”；round-end refactor 负责“补漏、测试、refine、合并重复”。
- 如果 refactor 输出与已有 skill 重复，应优先生成 affected update / lineage，而不是新增 skill。

```
comment:
对clique的去重比较复杂。即便是相近的clique，可能也是为了提取不同的skill。我们可能需要建立trace和skill的对应映射。在给refactor提取的时候，要对已经形成skill的trace做适当标注，防止重复提取。比如标注已经成为skill的segment的开头结尾，可能存在多个这样的segment，每个都要标注开头结尾。refactor自行判断是不是这个clique的重复片段已经被提取过了。另外，在overlap score那个阶段就要防，如果发现clique分数高是因为一批已经被提取为同一skill的segment激发的，就降低其优先级。这个算法我觉得可以设计成：剔除已经提取成skill的segment参与构建overlap graph，这样更容易。

online是每个task之后的，round是一批结束之后的？我觉得留round的就可以了，没必要每次都refactor，而且还可以剪枝，round只考虑从上次至今新增的trace参与的clique就可以了。
```

```
回复：
同意，而且你这个设计比我之前说的“近似 clique 去重”更可控。相近 clique 不一定重复，硬去重会误杀；更合理的是维护 trace-to-skill coverage map。

建议改成：

- 每个 committed skill 记录 `source_segments`，并进一步记录 segment 内的 covered span：`segment_id, start_offset, end_offset, skill_id, refactor_group_id`。
- 构建 overlap graph 时，对已被 committed skill 覆盖的 segment/span 做标注。
- clique candidate scoring 时加入 coverage penalty：如果 clique 高分主要来自同一批已覆盖 span，则降低优先级。
- 更简单的第一版可以直接剔除已覆盖 segment，不参与新 graph；但这可能漏掉同一 trace 中不同可复用点。更稳妥的是 span-level 剔除或降权。
- 给 refactor LLM 的输入中显式标注 covered span 的开头/结尾和对应 skill，让它判断当前 clique 是否在重复抽取，还是同一 trace 上的另一个独立可复用点。

online/round 的结论也同意：

- online refactor 是每个 task 后触发，当前成本和重复风险都偏高。
- round refactor 是一批结束后触发，更适合做剪枝、覆盖标注和批量候选排序。

后续默认应关闭 online refactor，只保留 round refactor。round refactor 只考虑“从上次 refactor checkpoint 之后新增 trace 参与形成的 clique”，但允许这些新增 trace 和历史未覆盖 trace 形成 overlap。这样既不会全图重扫，也不会漏掉新旧 trace 的可复用关系。
```

## 总体修改计划

1. 先重构 executor 主循环为策略对象，保留原行为的兼容路径，单测覆盖原 baseline/evolve 行为。
2. 把 retry retrieval 做成显式 policy，并增加 metrics：trigger 次数、新增 skill 数、实际使用数、validity/token 影响。
3. 修改 watchdog 语义：保留 repeated-call safety stop；禁用或 ablation 化 expected-covered extra-call stop；优先环境完成信号。
4. 增加 retrieval backend 抽象：当前 lexical/tag 作为默认 backend；embedding backend 先实现接口和离线索引，不强制替换 50/50 默认。
5. prior extraction 不再传 full store，只传本 trace + meta-skills + 小型去重 projection。
6. 重写 extractor prompt：以 correctness、reusability、maintainability 为主线，加入 one-shot 和 `evidence/scope/non_applicability` 字段。
7. 强化 allowed_tools 文档和代码语义：作为 retrieval/injection scope hint，不作为工具权限或 schema 真相。
8. 增加 refactor group registry，统一 online refactor 和 round-end refactor 的去重、lineage、semantic duplicate check。
9. 为以上每个策略增加小样例单测和真实 smoke test，特别检查：污染 skill 不注入、无环境完成信号不借 expected 退出、prior extraction 不从无关 skill 迁移规则。

## 当前结论

这次 review 指出的核心问题是准确的：当前实现已经具备 paper_new 算法的大部分部件，但若要作为最终算法实现，还需要把“过程式补丁”收敛成清晰策略层，并修正两个方法论风险：

- executor 不应通过 expected extra-call watchdog 替模型完成任务。
- prior extraction 不应默认读取全库，否则会污染 trace-grounded extraction。

我建议下一步先做结构性重构和语义修正，再跑小规模真实测试确认行为变化，最后再恢复 50/50 实验。
