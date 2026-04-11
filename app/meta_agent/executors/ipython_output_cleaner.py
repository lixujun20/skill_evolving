from __future__ import annotations
from typing import Optional, Dict, Any
from app.llm import LLM


class IPythonOutputCleaner:
    """使用 LLM 智能清洗 IPython/Sandbox 输出
    
    职责：
    - 识别并提取 print 输出
    - 保留重要的错误信息
    - 移除代码回显、IPython 提示符等干扰内容
    - 提供简洁的执行结果总结
    """
    
    def __init__(
        self,
        *,
        llm: Optional[LLM] = None,
    ) -> None:
        self._llm: LLM = llm or LLM(config_name="text")
    
    async def clean_output(self, raw_output: str, command: str = None) -> Dict[str, Any]:
        """清洗 IPython 输出
        
        Args:
            raw_output: IPython/Sandbox 的原始输出
            command: 执行的命令
            
        Returns:
            包含清洗结果的字典：
            - original_output: 原始输出
            - output: 清洗后的输出内容
            - summary: 简单总结
            - has_error: 是否包含错误
            - error_info: 错误信息
        """
        if not raw_output or not raw_output.strip():
            return {
                "original_output": "",
                "output": "",
                "summary": "执行完成，无输出",
                "has_error": False,
                "error_info": None
            }
        
        try:
            prompt = self._build_cleaning_prompt(raw_output, command)
            messages = [{"role": "user", "content": prompt}]
            response = await self._llm.ask(messages=messages)

            if not response:
                return {
                    "original_output": raw_output,
                    "output": raw_output,
                    "summary": "ipython返回结果清洗失败，未知错误",
                    "has_error": True,
                    "error_info": "Unknown Error"
                }

            response_text = response.content if hasattr(response, "content") else str(response)

            result = self._parse_llm_response(response_text, raw_output)
            return result
            
        except Exception as e:
            return {
                "original_output": raw_output,
                "output": raw_output,
                "summary": "发生错误",
                "has_error": True,
                "error_info": str(e)
            }
    
    def _build_cleaning_prompt(self, raw_output: str, command: str = None) -> str:
        prompt = """You are a specialized assistant for processing IPython/Jupyter outputs. Your task is to extract useful information from raw IPython execution results. You will be provided with the raw output of an IPython session and code that was executed. Your goal is to extract all the 'print' function outputs from the code.

**Your task:**
1. Extract all print function outputs from the code
2. Retain important error information (error type and error message)
3. Remove all code echo, IPython prompts, ANSI color codes, etc.
4. Provide a simple summary of the execution result.

**Content to Remove:**
- IPython Prompts: In [N]:, Out[N]:, >>>, ...
- Code Echoes: All lines of input code.
- ANSI Color Codes: e.g., [31m, [32m, [39m, etc.
- Line Number Markers: e.g., ----> 1, 2, etc.
- Empty lines and duplicated content.

**Content to Keep:**
- All print function outputs from the code
- Important error information (error type and error message)

**Important:**
- Though there is code echo in the input, it may be not complete or too confusing due to ipython output format, when you work, please refer to the code that was executed.
- You should not include any code echo in your output.
- Only include the print function outputs and important error information in your output.
- About summary, if no error occurred, you should provide a simple summary of the execution result. If an error occurred, you should provide a simple summary of the error and simply illustrate the reason of the error.

**URL Decoding / Normalization (MUST):**
- If the output or error_info contains percent-encoded URL fragments (e.g. `%E5%B9%B4`), you MUST decode them to human-readable Unicode in BOTH `output` and `error_info`.
- Example: `/s?wd=2022%E5%B9%B4%E6%88%91%E5%9B%BD%E4%BA%BA%E5%9D%87GDP` should become `/s?wd=2022年我国人均GDP`.

**Output Format:**
Please strictly follow the following JSON format:

```json
{
    "output": "The cleaned output content, containing only print outputs",
    "summary": "A simple summary of the execution result",
    "has_error": true/false,
    "error_info": "The error infomation if an error occurred, or null if no error occurred"
}
```

**Input Information:**"""
        
        prompt += f"\n\nCode that was executed: \n```\n{command}\n```"
        prompt += f"\n\nIPython original output: \n```\n{raw_output}\n```\n\nPlease return the JSON result:"
        
        return prompt
    
    def _parse_llm_response(self, response_text: str, raw_output: str) -> Dict[str, Any]:
        """解析 LLM 响应"""
        try:
            import json
            import re
            
            json_match = re.search(r'```json\s*(.*?)\s*```', response_text, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
            else:
                json_str = response_text.strip()
            
            json_str = json_str.replace('```', '').strip()
            result = json.loads(json_str)
            
            default_result = {
                "original_output": raw_output,
                "output": raw_output,
                "summary": "执行完成",
                "has_error": False,
                "error_info": None
            }
            
            for key in default_result:
                if key not in result:
                    result[key] = default_result[key]
            
            return result
            
        except Exception as e:
            return {
                "original_output": raw_output,
                "output": raw_output,
                "summary": "llm解析失败",
                "has_error": True,
                "error_info": str(e)
            }

# **Fetch/HTTP Errors Handling (MUST):**
# - If you see messages like:
#   - `Failed to fetch content from <url>: HTTP 403/404/...`
#   - `Error fetching content from <url>...`
#   Treat them as REAL errors.
# - These fetch/HTTP error lines MUST NOT appear inside `output`. Put them into `error_info` instead (decoded, readable), and set `has_error` to `true`.
# - `summary` must explicitly mention the fetch/HTTP failure (e.g. blocked by 403 / access restricted) when such errors exist.
