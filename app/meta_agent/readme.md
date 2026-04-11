> Add database design.

I'm writing an agent tool in a meta-agent framework called OpenManus. Here's an example of tool development for LLM to call:
```python
from typing import Any, Dict, List, Optional
from pydantic import Field, BaseModel  # 需要添加BaseModel导入

from app.old_tool.base import BaseTool, ToolResult
from app.llm import LLM
from app.schema import Message
from app.logger import logger
import json

class SubTask(BaseModel):
    name: str
    description: str
    input_schema: Dict[str, Any]
    output_schema: Dict[str, Any]
    prompt_template: Optional[str] = None
    result: Optional[Any] = None

class EvoTool(BaseTool):
    name: str = "evo_tool"
    description: str = "Solves complex problems by breaking them into subtasks, generating prompts, and executing them sequentially."

    parameters: dict = {
        "type": "object",
        "properties": {
            "problem_description": {
                "type": "string",
                "description": "The complex problem to be solved."
            },
            "conversation_history": {
                "type": "array",
                "description": "The history of conversation messages.",
                "items": {
                    "type": "object",
                    "properties": {
                        "role": {"type": "string"},
                        "content": {"type": "string"}
                    }
                }
            }
        },
        "required": ["problem_description", "conversation_history"]
    }

    llm: LLM = Field(default_factory=LLM)

    async def _plan_tasks(self, problem_description: str) -> List[SubTask]:
        """Phase 1: Decompose the problem into subtasks using an LLM call."""
        self.logger_instance.info(f"EvoTool: Planning subtasks for problem: {problem_description[:100]}...")
        # This prompt needs to guide the LLM to output a list of subtasks
        # in a structured format (e.g., JSON) that can be parsed into SubTask objects.
        planning_prompt = f"""
        Given the problem: '{problem_description}'
        Break it down into a sequence of smaller, manageable subtasks that need to be executed to solve the problem.
        For each subtask, provide:
        1. A concise 'name' (e.g., 'gather_requirements', 'generate_code_snippet', 'summarize_findings').
        2. A 'description' of what the subtask aims to achieve.
        3. An 'input_schema' as a JSON object describing the necessary inputs for this subtask (e.g., {{"user_query": "string", "context_data": "string"}}).
        4. An 'output_schema' as a JSON object describing the expected output from this subtask (e.g., {{"summary": "string", "action_items": "list"}}).

        Respond with a JSON list of these subtask objects. For example:
        [
          {{
            "name": "subtask_1_name",
            "description": "Description of subtask 1",
            "input_schema": {{"input_param1": "type"}},
            "output_schema": {{"output_param1": "type"}}
          }},
          {{
            "name": "subtask_2_name",
            "description": "Description of subtask 2",
            "input_schema": {{}},
            "output_schema": {{}}
          }}
        ]
        """
        try:
            # Changed from self.llm.ask(messages=[Message(role="user", content=planning_prompt)])
            # to align with other tools, assuming ask returns a string response directly.
            raw_response = await self.llm.ask(
                messages=[Message.user_message(planning_prompt)],
                system_msgs=[Message.system_message("You are an expert in breaking down complex problems into sequential subtasks. Respond in JSON format.")],
                temperature=0.95
            )

            # Clean and parse JSON response
            cleaned_response = raw_response.replace("```json", "").replace("```", "").strip()
            if not cleaned_response:
                raise ValueError("LLM response for task planning was empty after cleaning.")

            subtask_data_list = json.loads(cleaned_response)

            subtasks = [SubTask(**data) for data in subtask_data_list]
            self.logger_instance.info(f"EvoTool: Planned {len(subtasks)} subtasks.")
            return subtasks
        except json.JSONDecodeError as e:
            self.logger_instance.error(f"EvoTool: Error decoding JSON from LLM during task planning: {e}")
            self.logger_instance.error(f"LLM Raw Response content: {raw_response}") # Log raw response for debugging
            # Consider returning ToolResult(error=...) or raising a specific exception
            raise ToolResult(error=f"Failed to parse subtasks from LLM response: {e}. Response: {raw_response[:200]}")
        except ValueError as e: # Catch empty or malformed content before JSON parsing
            self.logger_instance.error(f"EvoTool: ValueError during task planning: {e}")
            self.logger_instance.error(f"LLM Raw Response content: {raw_response}")
            raise ToolResult(error=f"LLM response issue during task planning: {e}. Response: {raw_response[:200]}")
        except Exception as e:
            self.logger_instance.error(f"EvoTool: Error during task planning: {e}")
            # Ensure raw_response is defined in this scope if an error occurs before its assignment
            error_response_content = raw_response if 'raw_response' in locals() else "<unavailable>"
            raise ToolResult(error=f"An unexpected error occurred during task planning: {e}. Response: {error_response_content[:200]}")

    async def _generate_prompt_templates(self, subtasks: List[SubTask]) -> None:
        """Phase 2: Generate a prompt template for each subtask using an LLM call."""
        self.logger_instance.info("EvoTool: Generating prompt templates for subtasks...")
        for task in subtasks:
            # This prompt guides the LLM to create a good prompt template for the subtask
            template_generation_prompt = f"""
            For the subtask named '{task.name}' with the description: '{task.description}'.

            This subtask expects inputs according to this schema:
            {json.dumps(task.input_schema)}

            And it should produce outputs according to this schema:
            {json.dumps(task.output_schema)}

            Create a detailed and effective prompt template that can be used with an LLM to perform this subtask.
            The template should clearly state the goal, use placeholders for all inputs defined in 'input_schema' (e.g., {{{{input_param1}}}} - note double braces for f-string literal), and instruct the LLM to provide output matching the 'output_schema'.

            Respond with ONLY the prompt template string.
            """
            try:
                # Changed from self.llm.generate_response to self.llm.ask
                response_content = await self.llm.ask(
                    messages=[Message.user_message(template_generation_prompt)],
                    system_msgs=[Message.system_message("You are an expert in crafting effective LLM prompt templates.")],
                    temperature=0.95
                )
                task.prompt_template = response_content.strip() # Assuming ask returns string directly
                self.logger_instance.info(f"EvoTool: Generated prompt template for subtask '{task.name}'.")
            except Exception as e:
                self.logger_instance.error(f"EvoTool: Error generating prompt template for subtask '{task.name}': {e}")
                # Decide if this should be a fatal error or if we can proceed without a template for this task
                task.prompt_template = f"Execute subtask: {task.name}. Description: {task.description}. Inputs: {task.input_schema}. Expected output: {task.output_schema}" # Fallback

    async def _extract_inputs_for_subtask(self, subtask: SubTask, problem_description: str, conversation_history: List[Dict], previous_subtask_results: List[SubTask]) -> Dict[str, Any]:
        """Helper to gather inputs for a subtask from various sources."""
        self.logger_instance.info(f"EvoTool: Extracting inputs for subtask '{subtask.name}'.")

        history_str = "\n".join([f"{msg['role']}: {msg['content']}" for msg in conversation_history[:]])
        prev_results_str = "\n".join([f"Subtask '{st.name}' output: {json.dumps(st.result)}" for st in previous_subtask_results if st.result])

        extraction_prompt = f"""
        You need to prepare inputs for the subtask: '{subtask.name}' which is described as: '{subtask.description}'.
        This subtask requires the following inputs: {json.dumps(subtask.input_schema)}.

        Available information:
        1. Original Problem: {problem_description}
        2. Recent Conversation History:
        {history_str}
        3. Results from previous subtasks:
        {prev_results_str}

        Based on the available information, determine the values for each required input for the subtask '{subtask.name}'.
        Respond with a JSON object where keys are the input names from 'input_schema' and values are the extracted information.
        If an input cannot be found or inferred, use a null value or a placeholder string indicating it's missing.
        Example response: {{"user_query": "What is the capital of France?", "context_data": null}}
        """
        raw_response = ""
        try:
            # Changed from self.llm.generate_response to self.llm.ask
            raw_response = await self.llm.ask(
                messages=[Message.user_message(extraction_prompt)],
                system_msgs=[Message.system_message("You are an expert in extracting structured information from text. Respond in JSON format.")],
                temperature=0.1 # Lower temperature for factual extraction
            )
            cleaned_response = raw_response.replace("```json", "").replace("```", "").strip()
            if not cleaned_response:
                self.logger_instance.debug(f"EvoTool: Empty response from LLM during input extraction for '{subtask.name}'.")
                return {{key: f"<missing_input_{key}>" for key in subtask.input_schema.keys()}}

            inputs = json.loads(cleaned_response)
            self.logger_instance.info(f"EvoTool: Extracted inputs for '{subtask.name}': {inputs}")
            return inputs
        except json.JSONDecodeError as e:
            self.logger_instance.error(f"EvoTool: Error decoding JSON from LLM during input extraction for '{subtask.name}': {e}")
            self.logger_instance.error(f"LLM Raw Response for input extraction: {raw_response}")
            return {{key: f"<error_extracting_{key}>" for key in subtask.input_schema.keys()}} # Fallback
        except Exception as e:
            self.logger_instance.error(f"EvoTool: Error extracting inputs for subtask '{subtask.name}': {e}")
            return {{key: f"<error_extracting_{key}>" for key in subtask.input_schema.keys()}} # Fallback

    async def _execute_subtask(self, subtask: SubTask, inputs: Dict[str, Any]) -> Any:
        """Phase 3.2: Execute a single subtask by filling its prompt and calling LLM."""
        self.logger_instance.info(f"EvoTool: Executing subtask '{subtask.name}'.")
        if not subtask.prompt_template:
            self.logger_instance.debug(f"EvoTool: No prompt template for subtask '{subtask.name}'. Using basic execution.")
            generic_prompt = f"Perform task: {subtask.name}. Description: {subtask.description}. Inputs: {inputs}. Expected output format: {subtask.output_schema}"
            # Changed from self.llm.generate_response to self.llm.ask
            response_content = await self.llm.ask(
                messages=[Message.user_message(generic_prompt)],
                temperature=0.5 # Example temperature
            )
            subtask.result = response_content # Or parse if structured output expected
            return subtask.result

        # Fill the prompt template
        filled_prompt = subtask.prompt_template
        for key, value in inputs.items():
            filled_prompt = filled_prompt.replace(f"{{{{{key}}}}}", str(value))

        self.logger_instance.debug(f"EvoTool: Filled prompt for '{subtask.name}':\n{filled_prompt}")
        raw_response_content = ""
        try:
            # Changed from self.llm.generate_response to self.llm.ask
            raw_response_content = await self.llm.ask(
                messages=[Message.user_message(filled_prompt)],
                temperature=0.5 # Example temperature, adjust based on task nature
            )

            cleaned_response = raw_response_content.replace("```json", "").replace("```", "").strip()
            if not cleaned_response:
                self.logger_instance.debug(f"EvoTool: Output for subtask '{subtask.name}' was empty after cleaning.")
                subtask.result = "<empty_llm_response>"
                return subtask.result

            try:
                if any(isinstance(v, (dict, list)) for v in subtask.output_schema.values()) or \
                   any(k for k in subtask.output_schema if isinstance(subtask.output_schema[k], (dict, list))):
                    subtask.result = json.loads(cleaned_response)
                else:
                    subtask.result = cleaned_response
            except json.JSONDecodeError:
                self.logger_instance.debug(f"EvoTool: Output for subtask '{subtask.name}' was not valid JSON, using raw string. Content: {cleaned_response[:100]}...")
                subtask.result = cleaned_response

            self.logger_instance.info(f"EvoTool: Subtask '{subtask.name}' executed. Result: {str(subtask.result)[:100]}...")
            return subtask.result
        except Exception as e:
            self.logger_instance.error(f"EvoTool: Error executing subtask '{subtask.name}': {e}")
            subtask.result = f"Error during execution: {e}"
            return subtask.result

    async def execute(self, problem_description: str, conversation_history: List[Dict]) -> ToolResult:
        self.logger_instance.info(f"EvoTool: Starting execution for problem: {problem_description[:100]}...")

        try:
            # Phase 1: Plan Tasks
            subtasks = await self._plan_tasks(problem_description)
            if not subtasks:
                return ToolResult(output="Failed to plan any subtasks.", error="No subtasks planned.")

            # Phase 2: Generate Prompt Templates
            await self._generate_prompt_templates(subtasks)

            # Phase 3: Execute Subtasks
            self.logger_instance.info("EvoTool: Starting subtask execution phase.")
            executed_subtasks: List[SubTask] = []
            for i, task in enumerate(subtasks):
                self.logger_instance.info(f"EvoTool: Processing subtask {i+1}/{len(subtasks)}: '{task.name}'")
                # Phase 3.1: Extract Inputs for current subtask
                # Pass results of already executed_subtasks
                current_inputs = self._extract_inputs_for_subtask(task, problem_description, conversation_history, executed_subtasks)

                # Phase 3.2: Execute current subtask
                await self._execute_subtask(task, current_inputs)
                executed_subtasks.append(task)
                if task.result is None or (isinstance(task.result, str) and "Error during execution" in task.result):
                    self.logger_instance.error(f"EvoTool: Subtask '{task.name}' failed or produced no result. Stopping further execution.")
                    # Optionally, could try to recover or ask for clarification here
                    # For now, we stop and report the failure.
                    final_error_summary = f"Execution stopped due to failure in subtask '{task.name}'. Result: {task.result}"
                    all_results = [{
                        "subtask_name": st.name,
                        "description": st.description,
                        "status": "Success" if st.result and not (isinstance(st.result, str) and "Error" in st.result) else "Failure",
                        "result": st.result
                    } for st in executed_subtasks]
                    return ToolResult(output={"summary": "EvoTool execution failed.", "subtask_results": all_results, "error_details": final_error_summary}, error=final_error_summary)

            # Phase 4: Consolidate and Present Final Result
            self.logger_instance.info("EvoTool: All subtasks executed. Consolidating results.")
            final_summary_prompt = f"""
            The following subtasks were executed to solve the problem: '{problem_description}'.
            Here are their results:
            """
            for task in executed_subtasks:
                final_summary_prompt += f"- Subtask '{task.name}': {json.dumps(task.result)}\n"
            final_summary_prompt += "\nProvide a comprehensive final answer to the original problem based on these subtask results."

            # Changed from self.llm.generate_response to self.llm.ask
            final_response_content = await self.llm.ask(
                messages=[Message.user_message(final_summary_prompt)],
                system_msgs=[Message.system_message("You are an expert in summarizing information and providing comprehensive answers.")],
                temperature=0.7 # Higher temperature for more creative summarization if needed
            )

            all_results_summary = [{
                "subtask_name": st.name,
                "description": st.description,
                "input_schema": st.input_schema,
                "output_schema": st.output_schema,
                # "prompt_template": st.prompt_template, # Can be very long
                "result": st.result
            } for st in executed_subtasks]

            return ToolResult(output={
                "final_answer": final_response_content, # Assuming ask returns string directly
                "problem_solved": problem_description,
                "subtask_execution_details": all_results_summary
            })

        except ToolResult as tr_error: # Catch errors raised as ToolResult from sub-methods
            self.logger_instance.error(f"EvoTool: Execution failed with ToolResult: {tr_error.error}")
            return tr_error
        except Exception as e:
            self.logger_instance.error(f"EvoTool: Unhandled exception during execution: {e}", exc_info=True)
            return ToolResult(error=f"An unexpected error occurred in EvoTool: {e}")

# Example Usage (conceptual, not directly runnable here without an environment)
async def example_run():
    evo = EvoTool()
    problem = "Plan a 3-day trip to Paris for a solo traveler interested in art and history. Provide an itinerary and budget estimation."
    history = [
        {"role": "user", "content": "I want to plan a trip."},
        {"role": "assistant", "content": "Sure, what kind of trip are you thinking of?"},
        {"role": "user", "content": problem}
    ]
    result = await evo.execute(problem_description=problem, conversation_history=history)
    if result.error:
        print(f"Error: {result.error}")
    else:
        print(json.dumps(result.output, indent=2))


class DynamicSubTaskTool(BaseTool):
    """动态生成的子任务工具"""

    def __init__(self, subtask: SubTask):
        super().__init__()
        self.name = f"subtask_{subtask.name}"
        self.description = subtask.description
        self.parameters = {
            "type": "object",
            "properties": subtask.input_schema,
            "required": list(subtask.input_schema.keys())
        }
        self.subtask = subtask
        self.llm = LLM()

    async def execute(self, **kwargs) -> ToolResult:
        # 使用子任务的prompt_template执行
        filled_prompt = self.subtask.prompt_template
        for key, value in kwargs.items():
            filled_prompt = filled_prompt.replace(f"{{{{{key}}}}}", str(value))

        response = await self.llm.generate_response(
            messages=[Message(role="user", content=filled_prompt)]
        )

        return ToolResult(output=response.message.content)
```
This define the description, function calling protocol for LLM to obey, and the detailed execution procedure. 
Now, I hope to support a **run-time workflow construction** with a meta-agent (a self-evolving agentic system without pre-defined procedure). This usually involves a "Zero agent" who breaks down the original complex task into several simpler ones, a tool library maintaining several basic tools (calculator, searcher, etc) or basic agents (chart drawer, ppt maker, etc) , and a workflow library maintaining several agent workflow (topology and collaboration logic for several basic tools or agents) for the Zero agent to utilize.
I hope to develop a toolkit with several sub tools to implement the view above.

* **Zero**: A leader agent that builds the final workflow with the help of the remaining agents below.
* **WorkflowLibrarian**: Maintain a hierarchical workflow library and helps Zero to navigate in it during workflow retrieval. It supports zooming in and out of a folder,  retrieving with workflow name, retrieving with workflow description, and so on.
* **WorkflowMaker**: Automatic, run-time workflow construction with tools. If Zero finds no suitable workflow after asking WorkflowLibrarian, it will call WorkflowMaker to build a new workflow tailored to current task.
* **ToolLibrarian**: Maintain a hierarchical tool library and helps Zero to navigate in it during new workflow building (If no existing workflow suits for current task in the workflow library). It supports zooming in and out of a folder,  retrieving with tool name, retrieving with tool description, and so on.
* **ToolMaker**: Automaticl, run-time tool construction (usually with python code). For example, Zero could find during its planning and navigating that a key tool is missing in current tool library. It could then call ToolMaker to write a python code to build such a tool.
* **ToolTester**: Generate and run test cases for the generated tool from ToolMaker. It guarantees the correctness of generated tools. Based on the feedback of it, ToolMaker could further refine its code until all tests are passed.

Imitating the example above, write several tool files to implement the meta-agent. Several elements are required:
* name, description
* execute()
Specifically, you should first implement the design of hierarchical library for tools and workflows that supports add, delete, change and select. Please do not use any data bases first. In-memory dataset with original python is enough. Specify the record format before coding. Then implement two librarian and two makers. Finally implement the rest.