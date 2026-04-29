2. Tool-Cosmos as a infra
2.1 目标
支持AI Cosmos其他模式的非预定义工具的获取和使用需求。
2.2 关键设计：Agent, workflow, skill, tool的关系
1. 若干历史上的不当理解
- agent中可以包含workflow调用，workflow中可以包含agent调用
- workflow、skill、tool区分不清
2. 关于「workflow复用的意义」的理解
- 绝大多数市面的agentic产品都是react agent，通过给一个主agent配备各种可调用工具/subagent，由其自行决定下一步行为，属于模糊条件分支，即分支行为由大模型动态决定
- Workflow则是可以代码化的一段固定处理逻辑，属于确定条件分支，绝大部分纯粹意义上的workflow分支都可以写成if else。
- 我们希望总结和维护workflow的动机就在于：虽然react agent是万能的，可以解决任何需求，但是LLM驱动的模糊逻辑判别调用方式存在调用逻辑不确定、计算开销巨大的问题。Workflow复用的用意在于：对于特定的query场景，将react agent的模糊决策总结成确定性决策，通过代码解释器确定性地执行，确保效果稳定、成本较低。
- Workflow复用并没有否定react agent的性能，用意不是扩大其可解问题集合的范围，而是改进其解决问题的效能。
3. 关于「Tool, Skill和Workflow的区别」的理解
- 在保留目前比较火的skill概念的前提下，tool的含义应该降级。
- Tool：MCP，特定api服务对LLM暴露的反向调用代理
- Skill：解决某一特定问题的库
- Workflow：解决某一特定问题的脚本
- tool=system call, workflow=function, skill=class object, agent=programmer
- Workflow和skill都可以包含若干tool call，workflow偏向面向过程，skill偏向面向对象。workflow内可以包含skill，skill内可以包含workflow。
- 这些都可以称为agent的experience，是沉淀的方法论，而不是和agent平级的智能体。agent可以主动参考和调用这些经验，来辅助自己当下的智能决策。
4. 关于「workflow和agent的区别及结合方式」的理解
（本节的workflow泛指上面提到的experience，具体可能是上面所指的狭义workflow/skill）
- 如果将agent和workflow理解成平级对象，会存在的问题是workflow事实上并不如agent全能，现实query中存在模糊逻辑时，一般的workflow无法解决，整合agent tool的workflow可以tricky地认为能够解决，但本质上还是agent在解决模糊逻辑。因此两者的地位事实上并不对等。
- 更合理的理解：两者地位不对等。永远是react agent在通过决定下一步动作的方式来解决用户query。但是在解决的过程中，可以参考/直接调用历史上总结的workflow，来高效、稳定、经济地解决当前query。
- 把我们的系统定位成「配备历史workflow经验的react agent」，好处是
  - 既没有削减react agent可解的问题范围
  - 也整合了workflow复用的各个优势
  - 可以兼容目前大多数平台的agentic设计
  - 对人暴露的交互接口始终是大模型，交互更灵活
5. 关于「agentic和workflow具体形态」的理解
- 对于workflow的描述方式，可以是非代码的流程结构，比如可以用dify表征的流程图，但是只要是确定性的逻辑，转写成代码都是最dense的表征方式，对于LLM的理解、整合、调用、生成、修改都是莫大的便利。
- React agent主体和workflow发生交互的环节主要有二：
  - 参考workflow执行操作
  - 执行流最终总结成新的workflow
  当workflow是代码形态时，agent执行流的形态不同决定了最终的开销结构不同：
  - 如果agent执行流是正常的react形态（工具调用通过LLM function call发生）
    - 执行：LLM native，开销较低，性能优势
    - 总结：从chat history总结成代码，需要理解，开销略高，方差较大
  - 如果agent执行流是CodeAct形态（工具调用=写代码）
    - 执行：产生较多输入输出，开销略大
    - 总结：也需要进行提炼总结，但指令是代码格式，提炼结果和历史结果的一致性较高
  - 结合想法：将agent的native执行流转成CodeAct执行流，过滤失败操作，用于总结
2.3 架构
所有meta-os-powered Platform/Agent都可以在原有基础上做如下包装：
- 维护公有/专有的tool/skill/workflow library，提炼沉淀方法论
- 回答query前，通过设计的检索机制检索到相关的历史方法论，拼接到context中，既作为参考，又可作为工具调用
- Agent通过对这些tool/skill/workflow的调用来高效复用历史经验，提升回答的效率、性能和稳定性，结合其本身的万能模糊决策，解决workflow没有考虑的边界情况，完成本次现实query
- Query response结束之后，从中总结经验教训，提炼成新的tool/workflow/skill，做完善的功能实现和文档撰写，入库。
2.4 关键环节
1. 提取 & 维护
总体原则：workflow提取不是一劳永逸的，需要根据不同query情形长期进行维护
提取是针对当前query的经验抽取，维护是针对全局workflow的经验优化
- 即便是后验提取，也只有一次query的参考，难以考虑到所有的corner case。如果希望提取的workflow具有复用性，必须做升维考虑更多可能出现的情况。但高复用不意味着低可用，需要代码确实可以执行
- 对于不同的接口调用（爬取不同的网站，调用不同LLM），可能有无穷无尽的分支情况。不仅需要在处理逻辑内兼容，还可能需要主动包装目标接口，在下一次query可以现场重写接口包装方案从而直接复用工作流，而不需要修改工作流
- 当一个工作流内存在太多分支时，需要做分裂，多态代替多分支，接口可能需要重写；对于制造的相似工作流可能需要做合并，接口需要重写。
- 多个工作流之间存在内部依赖，修改单个工作流需要同时修改多个其他下游工作流的接口
- 需要加入单测，对实现逻辑的正确性及性能进行测评
2. 检索
- 需要对workflow文档做优化，便利检索
- 配备agent做关键词检索，调用embedding模型做余弦相似度计算

以上是新的架构的设计思路，toolcosmos即meta agent。现在整体架构有了：
1. 根据本次交互trace和相关skills，extractor综合参考「本次执行结果」、「之前的skill设计」，针对本skill的功能和原先的接口设计，给出主动重构规划，既需要做功能的优化，也需要尽可能考虑前向兼容性。（接口不修改，行为不大变->小版本更新，接口可能修改，行为大变->大版本更新）
2. extractor着重考虑「skill依赖的上游skill的更新情况」，给出被动重构规划，即做出决策：如果上游的更新是当前可以较好兼容的，则对现有调用上游的方式做至多微小修改；如果上游更新过大，则仍旧使用旧版本skill，不做兼容更新，且对这个版本的依赖加以强制版本控制约束（这样上游legacy skill不能被删除）
3. extractor综合主动和被动重构规划，执行（可能的）重构，撰写详细的update log
4. 重构后的代码交由tester测试，tester根据重构前后的代码，对测例进行更新（添加、修改、删除测例，对于小版本更新禁止删除、修改已有的测例），并执行测试；同时（可选），视情况拉取一些有代表性的（比如被检索到较多的）下游skill，运行他们的测例。最终给出test report，至少详细评估「功能性」和「兼容性」的好坏。
5. extractor根据返还的report，做进一步的重构，tester再做进一步的测试，直到测试通过。

我们把这个设计叫做【skill_evolving_v1】。目前已经实现了数据库模型、extractor和tester的初步逻辑和测例编写.


请在progress.md中记录实现过程中遇到的任何问题和解决方案，以及任何设计上的调整和优化。

## Documentation Routing

长期维护时，优先使用以下文档入口：

- `AGENT.md`
  - 长期稳定约束
- `progress.md`
  - 每次计划、执行记录、已完成事项、阻塞项
- `academic_doc.md`
  - `academic` 主实验线的设计、论文写作要点、benchmark 协议与文档路由
- `DESIGN.md`
  - `skill_evolving_v1`
- `DESIGN_V2.md`
  - `skill_evolving_v2`

默认规则：

- 每次代码修改之后，都同步检查对应文档是否需要更新
- 如果某个子系统进入长期维护阶段，应给它建立单独文档，并在 `academic_doc.md` 中登记引用入口
