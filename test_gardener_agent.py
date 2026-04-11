import sys
import os
import asyncio
from loguru import logger
import traceback
import re
import json
import ast # Added ast

# Add AICosmos to path
sys.path.append(os.getcwd())

try:
    from app.config import config, LLMSettings
    from app.llm import LLM
    from app.meta_agent.skills.gardener_agent import SkillGardenerAgent
    from app.meta_agent.skills.schemas import AgentTrace, TraceStep, TraceFormat
    
    # Inject a cheap model configuration for testing
    # We copy the default config but change the model to gpt-4o-mini
    default_config = config.llm['default']
    cheap_config = default_config.copy()
    cheap_config.model = "gpt-4o-mini"
    config.llm['tool_maker'] = cheap_config # Override tool_maker to use cheap model
    config.llm['cheap_test'] = cheap_config

    print(f"Using model: {cheap_config.model}")

    def parse_log_chunk(file_path):
        """
        Rudimentary parser to convert specific log format to AgentTrace.
        """
        # Detect if this is a CodeAct style trace (IPython style)
        is_codeact = False
        with open(file_path, 'r', encoding='utf-8') as f:
            first_lines = f.readline()
            if "Step 1:" in first_lines or "Thought:" in first_lines:
                is_codeact = True
        
        if is_codeact:
            return parse_codeact_log(file_path)

        steps = []
        current_step = None
        query = "Unknown Query"
        
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            
        i = 0
        while i < len(lines):
            line = lines[i]
            
            # Extract Query
            if "Initial User Request:" in line:
                match = re.search(r"Initial User Request: (.*)", line)
                if match:
                    query = match.group(1).strip()

            # Extract Tool Call
            if "Activating tool:" in line:
                match = re.search(r"Activating tool: '(.*?)'", line)
                if match:
                    tool_name = match.group(1)
                    
                    # Look ahead for arguments
                    args_dict = {}
                    if i+1 < len(lines) and "Tool arguments:" in lines[i+1]:
                        args_match = re.search(r"Tool arguments: (.*)", lines[i+1])
                        if args_match:
                            try:
                                args_str = args_match.group(1).strip()
                                # Using ast.literal_eval to parse python dict string safely
                                args_dict = ast.literal_eval(args_str)
                            except Exception as e:
                                print(f"Warning: Failed to parse arguments for {tool_name}: {e}")
                                args_dict = {"raw_args": args_str}

                    current_step = TraceStep(
                        step_id=f"step_{len(steps)}",
                        status="pending",
                        tool_call=tool_name,
                        tool_input=args_dict,
                        thought=f"Decided to call {tool_name}"
                    )

            # Extract Tool Output
            if "completed its mission! Result:" in line and current_step:
                match = re.search(r"Result: (.*)", line)
                if match:
                    result = match.group(1).strip()
                    current_step.tool_output = result
                    current_step.status = "success"
                    steps.append(current_step)
                    current_step = None # Reset
            
            i += 1
            
        return AgentTrace(
            query=query,
            trace_format=TraceFormat.REACT, # Assuming ReAct for general tool calls
            steps=steps,
            final_answer="[Trace Ended]"
        )

    def parse_codeact_log(file_path):
        """
        Parses a CodeAct style trace text file.
        Format:
        Step N:
        Thought: ...
        Code:
        ```python
        ...
        ```
        Output:
        ...
        """
        steps = []
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Split by "Step "
        raw_steps = re.split(r"Step \d+:", content)
        
        for i, raw_step in enumerate(raw_steps):
            if not raw_step.strip():
                continue
                
            thought = ""
            code = ""
            output = ""
            
            # Extract Thought
            thought_match = re.search(r"Thought:(.*?)(?=Code:|Output:|$)", raw_step, re.DOTALL)
            if thought_match:
                thought = thought_match.group(1).strip()
            
            # Extract Code
            code_match = re.search(r"Code:\s*```python(.*?)```", raw_step, re.DOTALL)
            if code_match:
                code = code_match.group(1).strip()
            
            # Extract Output
            output_match = re.search(r"Output:(.*)", raw_step, re.DOTALL)
            if output_match:
                output = output_match.group(1).strip()
            
            if code or thought:
                steps.append(TraceStep(
                    step_id=f"step_{i}",
                    status="success" if "Traceback" not in output else "error",
                    thought=thought,
                    code_block=code,
                    tool_output=output
                ))
                
        return AgentTrace(
            query="Analyze stock data and simulate trading strategy", # Implicit query for this file
            trace_format=TraceFormat.CODEACT,
            steps=steps,
            final_answer="Analysis complete."
        )

    async def main():
        print("Initializing SkillGardenerAgent...")
        agent = SkillGardenerAgent(llm=LLM(config_name='cheap_test'), logger_instance=logger)
        print("Successfully initialized SkillGardenerAgent")
        
        # Parse Real Log
        # log_path = "/home/lixujun/AICosmos/logs/meta_os_extractor_case1.log"
        log_path = "/home/lixujun/AICosmos/mock_traces/simulation_data_analysis.txt"
        print(f"Parsing log file: {log_path}...")
        trace = parse_log_chunk(log_path)
        
        print(f"Constructed Trace with {len(trace.steps)} steps.")
        print(f"Query: {trace.query}")
        
        if len(trace.steps) == 0:
            print("Warning: No steps parsed! Check log format.")
            return

        print("\n" + "="*50)
        print("STARTING EXTRACTION (Real LLM Call)")
        print("="*50)
        
        extracted_code = await agent.run_extraction(trace)
        
        print("\n" + "="*50)
        print("EXTRACTION RESULT:")
        print("="*50)
        if extracted_code:
            print(extracted_code)
        else:
            print("No code extracted.")
        print("="*50)

    if __name__ == "__main__":
        asyncio.run(main())

except Exception as e:
    print(f"Error: {e}")
    traceback.print_exc()
