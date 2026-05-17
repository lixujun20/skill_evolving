# AgentOptimizer 文献笔记

## 1. 基本信息

- 论文：Offline Training of Language Model Agents with Functions as Learnable Weights
- 常用名称：AgentOptimizer / Training Language Model Agents without Modifying Language Models
- arXiv：2402.11359
- 作者：Shaokun Zhang, Jieyu Zhang, Jiale Liu, Linxin Song, Chi Wang, Ranjay Krishna, Qingyun Wu
- 发布时间：2024-02-17
- 来源核对：arXiv 页面与 AutoGen/AG2 文档（https://arxiv.org/abs/2402.11359，https://microsoft.github.io/autogen/0.2/docs/reference/agentchat/contrib/agent_optimizer/）
- 任务定位：不修改 LLM 权重，而是把 agent functions 当成 learnable weights，通过离线训练持续更新函数集合。

## 2. 问题设定与动机

AgentOptimizer 研究如何训练 LLM agents without modifying language model weights。作者把 agent 可调用的 functions 视为 agent parameters：就像神经网络通过参数学习任务分布，agent 可以通过函数集合的新增、修改和删除来适配下游任务。

这个设定是 skill evolution 的早期重要脉络。它不是现在意义上的多文件 skill repository，但它清楚提出了一个核心观点：外部函数/工具层可以作为可学习对象，承担一部分参数更新的角色。

## 3. 核心方法

AgentOptimizer 使用 LLM 来更新 agent functions，并设计离线训练算法。主要机制包括：

- 把 functions 作为 learnable agent weights。
- 在训练任务上执行 agent，收集失败或不足。
- 由 optimizer LLM 提议函数新增、修改或删除。
- 用 rollback 和 early-stop 控制训练过程，避免错误更新持续污染 agent。

AutoGen/AG2 中也提供了 AgentOptimizer 相关实现接口，用于优化 agent 使用的 functions。

## 4. 实验设置

论文在多个代表性 agent 下游任务上评估 function optimization 的效果，重点观察：

- agent training 是否能提升任务表现；
- 函数集合的 learning curve；
- domain transferability；
- rollback / early-stop 对训练稳定性的作用。

具体任务和数值应以后续从 PDF 表格进一步核对为准；当前笔记只使用 arXiv 摘要与官方文档确认的方法定位。

## 5. 主要实验结果

arXiv 摘要报告，AgentOptimizer 能在多种下游任务中显著提升代表性 LLM agents 的表现，并分析了 learning curve 与 domain transferability。官方 AutoGen 文档将其作为优化 agent functions 的实验性模块。

## 6. 与我们工作的不同

- AgentOptimizer 的优化对象是 functions，形式上更接近 callable tool/function set；我们的 skill 资产更泛化，可以是文档、workflow、rule card、shared sub-doc、工具调用协议或可执行函数。
- AgentOptimizer 关注 agent training algorithm，本质是用函数集合模拟参数学习；我们的主线关注长期 skill repository maintenance，包括 unit bundle tests、integration tests、versioning、dependency pins、stale propagation、refactor 和 selection。
- AgentOptimizer 已经覆盖“外部函数作为可学习权重”的大方向，因此我们不能声称“首次不改模型权重而优化外部能力”。我们的差异应是把外部能力当成软件资产做测试、版本、依赖和群体选择。
- AgentOptimizer 的 rollback / early-stop 是训练稳定化策略；我们的 rollback 需要与 per-skill bundle tests、下游依赖测试和 legacy version pinning 结合。

## 7. 对我们的借鉴

- 论文叙事中可以把 AgentOptimizer 作为“function-as-parameter”早期代表，再说明我们进一步把 parameter-like external functions 扩展为 versioned skill repository。
- rollback 和 early-stop 是最低限度的安全机制；我们的系统需要更细粒度地回答“为什么回滚、回滚到哪个版本、下游是否 pin 旧版本、是否继续 refine”。
- 如果要提出“rejection sampling instead of training”的视角，可以把 AgentOptimizer 当成训练式外部函数优化基线：我们不是持续梯度式更新函数，而是采样多个 skill 候选，再用测试和群体目标选择保留者。
