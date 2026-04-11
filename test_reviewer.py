import asyncio
from app.meta_agent.skills.reviewer_agent import SkillReviewerAgent
from app.sandbox.sandbox_tool import SandboxTool
from app.schema import Message

async def test():
    sandbox = SandboxTool()
    await sandbox.init()
    
    agent = SkillReviewerAgent(sandbox=sandbox)
    msgs = [Message(role="user", content="请帮忙调用 run_command 动作，执行一下 `echo 'Hello World'`")]
    
    print("Testing Reviewer Agent...")
    res = await agent.run(msgs)
    print("Agent Response:", res[-1].content)
    
if __name__ == "__main__":
    asyncio.run(test())
