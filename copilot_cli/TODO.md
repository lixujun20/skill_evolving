# TODO List for skill_evolving_academic

## 核心贡献

* 从「agent（完全自主模糊决策）」出发「提炼」「workflow（确定性流程）」，提升agent系统的效率和可控性。

* 实现高效的skill构建逻辑，通过foresight-executor-hindsight loop，快速提取历史相似经验，构建高质量的skill。

* 实现体系化的skill维护逻辑，通过完善的测试闭环 + 版本控制机制，实现运行时skill的长期维护和迭代。

## 方法

### 概览

1. query -> db -> history experience -> foresight workflow & skill

2. foresight workflow -> executor -> hindsight experience -> skill construction

3. skill construction -> test -> version control -> skill maintenance

### 部件实现

1. skill retrieval

使用协同过滤方法 + 向量检索，基于用户查询和历史经验，检索相关的trajectory和skill。

2. foresight workflow construction

根据历史信息，提取隐式可以复用的skill，构建前瞻性的workflow，创造复用机会。实现对其他skill的调用，形成复杂skills

3. executor

强调对历史skill的复用，同时保留灵活性，skill既可调用，也可以作为参考。

4. hindsight experience extraction

从执行结果提取经验，形成新的skill构建素材，形成闭环。形成对其他skill的调用。

5. skill test and version control

设计完善的测试用例，覆盖不同场景和边界情况，确保skill的可靠性和稳定性。引入版本控制机制，记录skill的演变历史，支持回滚和迭代。

## 实验

### 实验设计

1. 环境：包含多个测试情境

* Minecraft：开放世界游戏 + 脚本交互 + 复杂任务（如建造、探索）

* Math reasoning：数学问题求解 + 复杂推理 + 多步骤解答

* Terminal Bench: 终端命令执行 + 系统操作 + 复杂任务（如文件管理、系统配置）

* SkillsBench: 专门测试技能构建和复用能力的benchmark，包含多样化的任务和技能需求。但是是每个任务各自设计一个skill库，缺乏跨任务的技能复用。 

2. 对比方法：

* 基线方法：传统的agent系统，直接从query出发，完全自主决策，不进行skill提炼和复用。

* 隔离skill库：每道题目各自维护skill library。

* SkillsBench: 官方给了一些基线，比如claude写的skills，我们可以直接拿来对比。

* Ours：所有题目共享一个skill library。

3. 评估指标：

* 端到端：

    * 任务完成率：成功完成任务的比例。

    * 开销：执行任务花费的token数量

    * 复用率：被复用的skill数量占总skill数量的比例，复用次数分布

* skill层面：

    * utility：同一个目标query，使用skill vs 不使用skill的性能提升程度；使用这一版skill vs 使用上一版skill的性能提升程度（需要考虑下游skill的变化）。

    * 文档优化：对于文档优化的更新，评估优化前后被retriever检索到的概率是否下降（需要详细讨论如何设计，主要的困难一个是一次迭代中可能不止做了文档优化，另一个是大概率需要recommendation任务中的判断方法）

### 具体参数

executor模型：GLM-4.7B / Claude-4-6-Sonnet

retrieval embedding: Qwen3-Embedding-8B

planner, extractor & tester：GLM-4.7B / Claude-4-6-Sonnet

数据集：所有bench都在训练集上evolve skills，测试集上评估。简单任务多分配一些训练集（10^3+），困难任务少一点训练集（10^2+）。

### 实验结果分析

待补充 