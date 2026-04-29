# OpenClaw-RL 文献笔记

## 1. 基本信息

- 论文：OpenClaw-RL: Train Any Agent Simply by Talking
- arXiv：2603.10165v1
- 作者：Yinjie Wang, Xuyang Chen, Xiaolong Jin, Mengdi Wang, Ling Yang
- 时间：2026-03-10
- 代码页面：https://github.com/Gen-Verse/OpenClaw-RL
- 主题：从在线交互的 next-state signal 中恢复 reward 和 directive supervision，用统一异步 RL 框架训练个人 Agent 与通用 Agent
- 主要来源：arXiv API 摘要与 arXiv PDF 原文核对

## 2. 问题设定与动机

论文的核心观察是：Agent 每次 action 后都会收到 next-state signal，例如用户回复、工具输出、终端状态、GUI 状态变化或测试结果。现有 Agent 系统通常只把这些信号作为下一轮上下文，而没有把它们作为在线学习来源。

作者认为 next-state signal 中包含两类可学习信息。第一类是 evaluative signal，表示上一步 action 好不好，例如用户重问、测试通过、stderr 报错等，可被 PRM 转成过程奖励。第二类是 directive signal，表示上一步应该如何改变，例如用户明确指出“你应该先检查文件”，这类信号可被转成 token-level 的方向性监督。

因此，OpenClaw-RL 试图用同一套在线异步基础设施覆盖个人对话、终端、GUI、SWE 和 tool-call 等多种 Agent 交互流，使 Agent 能在被正常使用的同时持续训练。

## 3. 核心方法

系统层面，OpenClaw-RL 采用四个解耦的异步组件：policy serving、environment hosting、PRM judging、policy training。论文实现中使用 SGLang 做 policy serving，HTTP/API 连接环境，SGLang/API 做 PRM judge，Megatron 做训练。四个循环互不阻塞，目标是边服务、边收集 rollout、边打分、边更新权重。

对个人 Agent，系统通过 session-aware environment server 区分 main-line turn 和 side turn。main-line turn 产生可训练样本，side turn 只转发不训练。新 main-line 请求中的用户回复或工具执行结果会被视为上一轮 action 的 next-state signal。

学习方法包括三类。

第一，Binary RL：用 PRM 判断 `(action, next_state)`，得到 `+1/-1/0` 的标量奖励，并用 PPO-style clipped surrogate 更新策略。PRM 可多次独立查询后多数投票。

第二，Hindsight-Guided On-Policy Distillation，即 OPD：当 next-state signal 中有明确纠错方向时，judge 从中抽取 1-3 句 hint，把 hint 追加到原用户消息中形成 enhanced teacher context。然后比较同一模型在 enhanced context 和原 context 下对原 response token 的 log-prob 差异，形成 token-level directional advantage。论文强调 OPD 只保留 hint 足够清晰的样本，牺牲样本量换取更高质量的方向性监督。

第三，Binary RL + OPD 联合：二者共享 PPO loss，只是 advantage 来源不同。默认将 binary reward 和 OPD token-level log-prob 差加权相加，论文默认 `w_binary = w_opd = 1`。

对通用 Agent，论文把 outcome reward 和 step-wise PRM process reward 相加，用于长程任务的密集 credit assignment。支持的环境包括 OpenClaw personal devices、Terminal shell sandbox、GUI screen/accessibility tree、SWE code repository/test suite、Tool-call API/function execution。

## 4. 实验设置

论文分为 personal agent track 和 general agent track。

Personal agent track 使用 LLM 模拟用户与 OpenClaw 交互。场景一是学生用 OpenClaw 做 GSM8K 作业，但希望答案不要显得像 AI 写的；场景二是老师用 OpenClaw 批改作业，希望评语具体且友好。两个场景的 policy model 都是 Qwen3-4B，学习率 `1e-5`，KL coefficient 为 0，每收集 16 个训练样本触发训练。

General agent track 覆盖 Terminal、GUI、SWE、Tool-call。模型分别为 Qwen3-8B、Qwen3VL-8B-Thinking、Qwen3-32B、Qwen3-4B-SFT。训练数据分别使用 SETA RL data、OSWorld-Verified、SWE-Bench-Verified、DAPO RL data。GUI 在训练集上评估并排除 chrome 和 multi-apps tasks；Tool-call 在 AIME 2024 上评估；Terminal 和 SWE 报告 RL steps 窗口内的平均 rollout-task accuracy。

通用 Agent 的主要超参数包括：学习率 `1e-6`，KL coefficient `0.01`，clip ratio `0.2 / 0.28`，GUI/SWE/Terminal/Tool-call 每步采样任务数分别为 8/8/16/32，每个任务独立采样 8 次，最大交互步数 GUI 30、SWE 20、Terminal 10。并行环境数量为 Terminal 128，GUI 和 SWE 各 64，Tool-call 32。

## 5. 主要实验结果

Personal agent track 中，论文用同一个 LLM simulator 给 OpenClaw 对每个 GSM8K 问题的第一条回复打 personalization score，报告前 36 个问题的平均分。base score 为 0.17。

表 3 的已确认结果：

- Binary RL：updated 8 steps 为 0.25，updated 16 steps 为 0.23
- OPD：updated 8 steps 为 0.25，updated 16 steps 为 0.72
- Combined：updated 8 steps 为 0.76，updated 16 steps 为 0.81

论文结论是 Combined 最强，OPD 后期优于 Binary RL，但由于可用 hint 样本更稀疏，效果显现更慢。论文还报告，在 Combined 设置下，学生场景 36 次 problem-solving interactions 后有明显改进，老师场景 24 次 grading interactions 后有明显改进。

General agent track 中，论文强调框架可在 Terminal、GUI、SWE、Tool-call 多环境上进行大规模并行训练。对于 outcome reward 与 process reward 的比较，表 4 的已确认结果为：

- Tool-call：Integrated 0.30，Outcome only 0.17
- GUI：Integrated 0.33，Outcome only 0.31

论文结论是集成 outcome 和 process reward 优于只用 outcome reward，但需要额外资源部署 PRM。其他曲线或图中细节数值未在文本表格中完整给出，如需使用应从 PDF 图表进一步核对。

## 6. 与我们工作的不同

OpenClaw-RL 的核心是在线参数更新和 RL 基础设施，强调从 live next-state signal 中恢复奖励与 token-level 监督；我们的工作如果主要围绕 skill 文本/经验库的生成、选择、复用和演化，则优化对象不是模型参数，而是外部 skill artifact。

OpenClaw-RL 把用户回复、工具输出、GUI 状态、测试结果等都统一为 MDP transition 后的 next state；我们的任务中如果没有持续在线交互流，可能更接近离线 batch evolution，需要从解题轨迹、错误日志或评测结果中构造类似的反馈信号。

OpenClaw-RL 的实验强调 personal agent personalization 和通用 agentic RL；它没有重点讨论如何维护可解释、可版本化、可人工审查的 skill 文件，也没有解决 skill 检索、skill 去重、skill 冲突和长期知识库治理问题。

OpenClaw-RL 对 next-state signal 的使用粒度是 reward/advantage；我们的 skill 演化更可能需要把失败原因转成可读规则、反例、适用条件和操作流程，而不只是梯度信号。

## 7. 对我们的借鉴

可以借鉴“next-state signal 不只是下一轮上下文，也是上一轮 action 的反馈”这一视角。对于我们的系统，模型解题后的验证结果、执行报错、用户纠错、judge 评语都可以归档为上一条 skill 调用的反馈，用来更新 skill 质量评分或触发 skill 改写。

可以借鉴 Binary RL 与 OPD 的区分：隐式反馈适合转成粗粒度 reward，显式纠错适合转成更高分辨率的改写指导。对应到 skill 层面，可以把“失败/成功”用于 skill ranking，把“应该如何做”的文本反馈用于 skill 内容更新。

可以借鉴其异步解耦设计。即使我们不做在线 RL，也可以把任务执行、judge 评估、失败归因、skill 更新、版本发布拆成独立队列，避免执行链路被演化过程阻塞。

可以借鉴 process reward 的思想。长链路任务中只看最终答案会丢失中间步骤信息；我们的 skill 评估也可以记录每一步是否正确检索、是否调用合适工具、是否识别题型、是否遵守答案格式，从而给 skill 更细粒度的信用分配。
