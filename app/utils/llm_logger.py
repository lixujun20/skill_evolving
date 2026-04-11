"""
LLM Call Logger - Comprehensive logging for debugging agent decision-making
Logs all inputs and outputs to LLM for analysis
"""

import json
from datetime import datetime
from typing import Any, Dict, List, Optional
from pathlib import Path
import traceback
import re

class LLMCallLogger:
    """Logger for LLM interactions to understand agent decision-making"""

    def __init__(self, base_path: str = "llm_call", session_id: Optional[str] = None):
        self.base_path = Path(base_path)
        self.session_id = session_id or datetime.now().strftime("%Y%m%d_%H%M%S")
        self.session_path = self.base_path / self.session_id
        self.call_counter = 0

        # Create session directory
        self.session_path.mkdir(parents=True, exist_ok=True)

        # Create index file for this session
        self.index_file = self.session_path / "index.json"
        self.index_data = {
            "session_id": self.session_id,
            "start_time": datetime.now().isoformat(),
            "calls": []
        }
        self._save_index()

    def _save_index(self):
        """Save index file"""
        # Ensure directory exists before writing (in case it was deleted)
        self.session_path.mkdir(parents=True, exist_ok=True)
        with open(self.index_file, 'w', encoding='utf-8') as f:
            json.dump(self.index_data, f, ensure_ascii=False, indent=2)

    def _format_nested_json_strings(self, data: Any) -> Any:
        """Recursively format JSON strings within the data structure for better readability"""
        if isinstance(data, dict):
            formatted = {}
            for key, value in data.items():
                if isinstance(value, str):
                    # Format all string content for better readability
                    formatted[key] = self._format_string_content(value)
                elif key == 'platform_state' and isinstance(value, dict):
                    # Format platform_state as pretty JSON
                    try:
                        formatted[key] = json.dumps(value, ensure_ascii=False, indent=2)
                    except:
                        formatted[key] = value
                else:
                    formatted[key] = self._format_nested_json_strings(value)
            return formatted
        elif isinstance(data, list):
            return [self._format_nested_json_strings(item) for item in data]
        else:
            return data

    def _format_string_content(self, content: str) -> Any:
        """Format string content - split by newlines for readability or format JSON"""
        if not content:
            return content

        # Handle Platform State JSON content specially
        if '[Platform State BEGIN]' in content:
            return self._pretty_format_string_content(content)

        # For regular text, split by newlines for better readability
        if '\n' in content and len(content) > 100:  # Only split long text
            lines = content.split('\n')
            # Remove empty lines at start and end
            while lines and not lines[0].strip():
                lines.pop(0)
            while lines and not lines[-1].strip():
                lines.pop()
            return lines

        return content

    def _pretty_format_string_content(self, content: str) -> str:
        """Format string content that may contain JSON or structured data"""
        if not content:
            return content

        # Handle platform state content that starts with [Platform State BEGIN]
        if '[Platform State BEGIN]' in content:
            try:
                # Extract the JSON part
                start_marker = '[Platform State BEGIN]'
                end_marker = '[Platform State END]'
                start_idx = content.find(start_marker)
                end_idx = content.find(end_marker)

                if start_idx != -1 and end_idx != -1:
                    before = content[:start_idx + len(start_marker)]
                    json_part = content[start_idx + len(start_marker):end_idx].strip()
                    after = content[end_idx:]

                    # Try to parse and reformat the JSON part
                    try:
                        # Handle both cases: starting with newline or directly with {
                        json_to_parse = json_part
                        if json_to_parse.startswith('\n'):
                            json_to_parse = json_to_parse[1:]

                        if json_to_parse.startswith('{'):
                            parsed = json.loads(json_to_parse)
                            formatted_json = json.dumps(parsed, ensure_ascii=False, indent=2)
                            return f"{before}\n{formatted_json}\n{after}"
                    except json.JSONDecodeError as e:
                        # If JSON parsing fails, return original but add debug info
                        return f"{before}\n{json_part}\n{after}\n<!-- JSON Parse Error: {str(e)} -->"
            except Exception as e:
                # Return original with debug info if anything goes wrong
                return f"{content}\n<!-- Format Error: {str(e)} -->"

        # Try to detect and format JSON objects within the string
        try:
            # Improved JSON pattern to handle nested structures better
            json_pattern = r'\{(?:[^{}]|(?:\{[^{}]*\}))*\}'
            matches = re.findall(json_pattern, content)

            formatted_content = content
            for match in matches:
                try:
                    # Try to parse as JSON
                    parsed = json.loads(match)
                    pretty_json = json.dumps(parsed, ensure_ascii=False, indent=2)
                    formatted_content = formatted_content.replace(match, pretty_json)
                except json.JSONDecodeError:
                    continue

            return formatted_content
        except Exception:
            return content

    def log_llm_call(
        self,
        call_type: str,  # "ask_tool", "ask", etc.
        agent_name: str,
        processor: Optional[str] = None,
        substage: Optional[str] = None,
        messages: Optional[List[Any]] = None,
        system_prompt: Optional[str] = None,
        tools: Optional[List[Dict]] = None,
        tool_choice: Optional[Any] = None,
        response: Optional[Any] = None,
        error: Optional[str] = None,
        filtered_messages: Optional[List[Any]] = None,  # Messages after filtering
        platform_state: Optional[Dict] = None,
        force_wait_status: Optional[bool] = None,
        available_tool_names: Optional[List[str]] = None,
        metadata: Optional[Dict] = None
    ) -> str:
        """
        Log a complete LLM call with all context
        Returns the log file path
        """
        self.call_counter += 1
        timestamp = datetime.now().isoformat()

        # Create call log filename
        filename = f"{self.call_counter:04d}_{call_type}_{agent_name}_{timestamp.replace(':', '-')}.json"
        filepath = self.session_path / filename

        # Prepare log data
        log_data = {
            "call_number": self.call_counter,
            "timestamp": timestamp,
            "call_type": call_type,
            "agent_name": agent_name,
            "processor": processor,
            "substage": substage,

            # Input context
            "input": {
                "system_prompt": system_prompt,
                "original_messages_count": len(messages) if messages else 0,
                "filtered_messages_count": len(filtered_messages) if filtered_messages else 0,
                "messages": self._serialize_messages(messages) if messages else [],
                "filtered_messages": self._serialize_messages(filtered_messages) if filtered_messages else [],
                "tools": tools if tools else [],
                "tool_choice": str(tool_choice) if tool_choice else None,
                "available_tool_names": available_tool_names,
                "force_wait_status": force_wait_status,
                "platform_state": platform_state
            },

            # Output
            "output": {
                "response": self._serialize_response(response) if response else None,
                "error": error,
                "selected_tools": self._extract_selected_tools(response) if response else []
            },

            # Additional metadata
            "metadata": metadata or {},

            # Stack trace for debugging
            "call_stack": self._get_call_stack()
        }

        # Format the log data for better readability
        formatted_log_data = self._format_nested_json_strings(log_data)

        # Ensure directory exists before writing (in case it was deleted)
        self.session_path.mkdir(parents=True, exist_ok=True)

        # Save log file
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(formatted_log_data, f, ensure_ascii=False, indent=2)

        # Update index
        self.index_data["calls"].append({
            "number": self.call_counter,
            "timestamp": timestamp,
            "type": call_type,
            "agent": agent_name,
            "processor": processor,
            "substage": substage,
            "file": filename,
            "selected_tools": self._extract_selected_tools(response) if response else [],
            "error": bool(error)
        })
        self._save_index()

        # Also create a human-readable summary
        # self._create_summary(log_data, self.call_counter)  # Commented out - summary files are redundant

        return str(filepath)

    def _serialize_messages(self, messages: List[Any]) -> List[Dict]:
        """Convert message objects to serializable format"""
        serialized = []
        for msg in messages:
            if hasattr(msg, '__dict__'):
                msg_dict = {
                    "role": getattr(msg, 'role', 'unknown'),
                    "content": getattr(msg, 'content', ''),
                    "name": getattr(msg, 'name', None),
                    "tool_calls": self._serialize_tool_calls(getattr(msg, 'tool_calls', None)),
                    "tool_call_id": getattr(msg, 'tool_call_id', None)
                }
            elif isinstance(msg, dict):
                msg_dict = msg
            else:
                msg_dict = {"content": str(msg)}

            # Format content for readability (split by newlines, format JSON)
            if msg_dict.get('content'):
                content = str(msg_dict['content'])
                # Apply formatting to content
                formatted_content = self._format_string_content(content)

                # Truncate very long content for readability after formatting
                if isinstance(formatted_content, str) and len(formatted_content) > 10000:
                    formatted_content = formatted_content[:10000] + "... [TRUNCATED]"
                elif isinstance(formatted_content, list) and len('\n'.join(formatted_content)) > 10000:
                    # If it's a list of lines, truncate the joined content
                    joined = '\n'.join(formatted_content)
                    formatted_content = joined[:10000] + "... [TRUNCATED]"

                msg_dict['content'] = formatted_content

            serialized.append(msg_dict)
        return serialized

    def _serialize_tool_calls(self, tool_calls: Any) -> Optional[List[Dict]]:
        """Serialize tool calls"""
        if not tool_calls:
            return None

        serialized = []
        for tc in tool_calls:
            if hasattr(tc, '__dict__'):
                tc_dict = {
                    "id": getattr(tc, 'id', ''),
                    "type": getattr(tc, 'type', 'function'),
                    "function": {
                        "name": getattr(getattr(tc, 'function', None), 'name', ''),
                        "arguments": getattr(getattr(tc, 'function', None), 'arguments', '{}')
                    }
                }
            else:
                tc_dict = tc
            serialized.append(tc_dict)
        return serialized

    def _serialize_response(self, response: Any) -> Dict:
        """Serialize LLM response"""
        if hasattr(response, '__dict__'):
            return {
                "content": getattr(response, 'content', ''),
                "tool_calls": self._serialize_tool_calls(getattr(response, 'tool_calls', None)),
                "role": getattr(response, 'role', 'assistant')
            }
        elif isinstance(response, dict):
            return response
        else:
            return {"content": str(response)}

    def _extract_selected_tools(self, response: Any) -> List[str]:
        """Extract tool names from response"""
        tools = []
        if hasattr(response, 'tool_calls') and response.tool_calls:
            for tc in response.tool_calls:
                if hasattr(tc, 'function') and hasattr(tc.function, 'name'):
                    tools.append(tc.function.name)
        return tools

    def _get_call_stack(self) -> List[str]:
        """Get simplified call stack for debugging"""
        stack = []
        for frame in traceback.extract_stack()[:-2]:  # Exclude this function
            if '/edumanus' in frame.filename or '/app/' in frame.filename:
                stack.append(f"{frame.filename.split('/')[-1]}:{frame.lineno} in {frame.name}")
        return stack[-10:]  # Last 10 relevant frames

    def _create_summary(self, log_data: Dict, call_number: int):
        """Create a human-readable summary file"""
        summary_file = self.session_path / f"{call_number:04d}_summary.txt"

        lines = [
            f"=" * 80,
            f"LLM Call #{call_number} - {log_data['timestamp']}",
            f"=" * 80,
            f"Agent: {log_data['agent_name']}",
            f"Processor: {log_data['processor']} | Substage: {log_data['substage']}",
            f"Call Type: {log_data['call_type']}",
            ""
        ]

        # Input summary
        lines.append("INPUT CONTEXT:")
        lines.append("-" * 40)
        lines.append(f"Messages: {log_data['input']['original_messages_count']} original -> {log_data['input']['filtered_messages_count']} filtered")
        lines.append(f"Available Tools: {log_data['input'].get('available_tool_names', [])}")
        lines.append(f"Force Wait: {log_data['input'].get('force_wait_status', False)}")

        if log_data['input'].get('system_prompt'):
            lines.append("\nSYSTEM PROMPT (first 500 chars):")
            lines.append(log_data['input']['system_prompt'][:500] + "...")

        # Show last few messages
        if log_data['input'].get('filtered_messages'):
            lines.append("\nLAST 3 FILTERED MESSAGES:")
            for msg in log_data['input']['filtered_messages'][-3:]:
                role = msg.get('role', 'unknown')
                content = str(msg.get('content', ''))[:200]
                lines.append(f"  [{role}]: {content}...")

        # Platform state summary
        if log_data['input'].get('platform_state'):
            lines.append("\nPLATFORM STATE:")
            ps = log_data['input']['platform_state']
            if isinstance(ps, dict):
                for key, value in list(ps.items())[:5]:  # First 5 items
                    lines.append(f"  {key}: {str(value)[:100]}")

        # Output summary
        lines.append("\nOUTPUT:")
        lines.append("-" * 40)
        if log_data['output'].get('selected_tools'):
            lines.append(f"Selected Tools: {log_data['output']['selected_tools']}")
        if log_data['output'].get('response'):
            content = log_data['output']['response'].get('content', '')
            if content:
                lines.append(f"Response: {content[:300]}...")
        if log_data['output'].get('error'):
            lines.append(f"ERROR: {log_data['output']['error']}")

        lines.append("")

        with open(summary_file, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))


# Global logger instances per session (session_id -> LLMCallLogger)
_llm_loggers: Dict[str, LLMCallLogger] = {}

def get_llm_logger(session_id: Optional[str] = None) -> LLMCallLogger:
    """Get or create an LLM logger for the given session"""
    global _llm_loggers

    # If no session_id provided, use a default one
    if session_id is None:
        session_id = "default"

    # Create a new logger for this session if it doesn't exist
    if session_id not in _llm_loggers:
        _llm_loggers[session_id] = LLMCallLogger(session_id=session_id)
        
    return _llm_loggers[session_id]

def cleanup_session_logger(session_id: str):
    """Remove logger for a finished session to free up memory"""
    global _llm_loggers
    if session_id in _llm_loggers:
        del _llm_loggers[session_id]

