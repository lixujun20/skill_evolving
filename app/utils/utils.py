import re
import json
from jsonschema import validate, ValidationError, SchemaError
import importlib.util
import inspect
import os
from typing import Union, Dict
from app.config import config

def extract_traceback_from_file(traceback_string, filenames):
    """
    Extracts traceback entries from a specific file.

    Parameters:
        traceback_string (str): The full traceback string.
        filename (list[str]): The name of the file to filter tracebacks from.

    Returns:
        str: Tracebacks from the specified file.
    """
    # Regular expression for traceback lines
    traceback_pattern = re.compile(r'  File "(.*?)", line (\d+), in (.*?)\n')
    result_pattern = re.compile(r'\w*Error:')
    
    # Split traceback into lines
    lines = traceback_string.splitlines(keepends=True)
    
    # Buffer to store relevant traceback lines
    filtered_traceback = []
    include_line = False

    # Process each line
    for line in lines:
        # Match against traceback line pattern
        match = traceback_pattern.match(line)
        if match:
            current_file = match.group(1)
            if any([filename in current_file for filename in filenames]):  # Check if filename matches
                include_line = True
                filtered_traceback.append(line)  # Add this trace line
            else:
                include_line = False
        elif include_line:
            # Include subsequent lines in the block if the current traceback is active
            filtered_traceback.append(line)
        else:
            match = result_pattern.match(line)
            if match:
                filtered_traceback.append(line)


    return ''.join(filtered_traceback)

def extract_python_code(response: str):
    """
    Extracts Python code from a response string.

    Parameters:
        response (str): The response string containing Python code.

    Returns:
        str: The extracted Python code.
    """
    # Regular expression to match Python code blocks
    print('try extracting from ```...')
    try:
        code_pattern = re.compile(r'```python\n(.*?)```', re.DOTALL)

        # Find all matches in the response
        matches = code_pattern.findall(response)

        # Join all matches to form the complete Python code
        # python_code = '\n'.join(matches)
        python_code = matches[-1]
        return python_code
    except:
        pass

    print('try searching with ast...')
    try:
        import ast
        lines = response.splitlines()  # Split the string into individual lines
        n = len(lines)
        longest_valid_block = ""
        max_length = 0

        # Iterate through all possible consecutive line subsets
        for i in range(n):
            for j in range(n, i, -1):
                if j - i <= max_length:
                    break
                subset = "\n".join(lines[i:j])  # Take lines from i to j-1
                try:
                    # Try to parse the subset as valid Python code
                    ast.parse(subset)
                    # If it's valid Python code and longer than current max_length, update results
                    if (j - i) > max_length:
                        max_length = j - i
                        longest_valid_block = subset
                    break
                except SyntaxError as e:
                    # Ignore invalid Python subsets
                    if e.lineno == 1:
                        # The error appears at the first line. i cannot be the starting line.
                        break

        return longest_valid_block
    except:
        pass

    return ""

def extract_tool_schema(response: str):
    """
    Extracts tool schema from a response string.

    Parameters:
        response (str): The response string containing tool schema.

    Returns:
        str: The extracted tool schema.
    """
    # Regular expression to match Python code blocks
    print('try extracting from ```...')
    try:
        code_pattern = re.compile(r'```json\n(.*?)```', re.DOTALL)

        # Find all matches in the response
        matches = code_pattern.findall(response)

        # Join all matches to form the complete Python code
        # python_code = '\n'.join(matches)
        tool_schema = matches[-1]
        return tool_schema
    except:
        pass

def is_schema(json_string: Union[str, dict]):
    JSON_SCHEMA_META_SCHEMA = {
        "type": "object",
        "properties": {
            "$schema": {
                "type": "string",
                "format": "uri"
            },
            "type": {
                "type": "string"
            },
            "properties": {
                "type": "object"
            },
            "items": {
                "type": ["object", "array"]
            },
            "required": {
                "type": "array",
                "items": {
                    "type": "string"
                }
            }
            # Here you can add additional fields from the JSON Schema specification if needed.
        }
    }

    try:
        # Parse the JSON string
        if isinstance(json_string, str):
            json_data = json.loads(json_string)
        elif isinstance(json_string, dict):
            json_data = json_string
        else:
            raise ValueError("Input must be a JSON string or a dictionary.")

        # Validate the JSON object against the meta-schema
        validate(instance=json_data, schema=JSON_SCHEMA_META_SCHEMA)

        return True, "The JSON string is a valid JSON Schema."

    except ValidationError as ve:
        return False, f"The JSON string is not a valid JSON Schema: {ve.message}"

    except SchemaError as se:
        return False, f"Meta-Schema error: {se.message}"

    except json.JSONDecodeError as jde:
        return False, f"Invalid JSON string: {jde.msg}"


def replace_last(string: str, old: str, new: str) -> str:
    """
    Replace the last occurrence of a substring in a string.
    
    :param string: Original string
    :param old: Substring to be replaced
    :param new: Substring to replace with
    :return: Modified string with the last occurrence replaced
    """
    # Find the last occurrence by splitting from the right
    parts = string.rsplit(old, 1)
    # If the substring exists, it will produce a list with two parts
    if len(parts) > 1:
        return new.join(parts)
    return string  # Return original if the substring is not found


def get_tool_path_from_name(tool_name: str) -> str:
    return os.path.join(config.library_config.base_path, config.library_config.tool_code_path, tool_name)


def get_workflow_path_from_name(workflow_name: str) -> str:
    return os.path.join(config.library_config.base_path, config.library_config.workflow_code_path, workflow_name)


def print_beautifully(objects):
    # Determine the maximum width for the name and description fields
    max_name_width = max(len(obj["name"]) for obj in objects)
    
    # Print each object
    for obj in objects:
        # Append '/' to the name if it's a folder
        name = obj["name"] + ("/" if obj["is_folder"] else "")
        # Print the name and description, aligned according to the maximum widths
        print(f"{name:<{max_name_width + 2}} {obj['description']}")


def import_module(file_path):
    module_path = file_path.replace(os.path.sep, ".")[:-3]  # Convert file path to module path
    spec = importlib.util.spec_from_file_location(module_path, file_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def get_tool_class_name_from_path(file_path):
    """Given a tool class path, find the tool class (derived from `BaseTool`)

    Args:
        file_path (str): The path.

    Returns:
        Tuple: (module, class_name)
    """
    module = import_module(file_path)

    subclasses = []
    for name, obj in inspect.getmembers(module, inspect.isclass):
        # Must be defined in the module, not imported
        if obj.__module__ != module.__name__:
            continue
        # Must be subclass of MadeTool, but not MadeTool itself
        if any(getattr(b, "__name__", None) == 'BaseTool' for b in inspect.getmro(obj)) and obj.__name__ != 'BaseTool':
            try:
                _, lineno = inspect.getsourcelines(obj)
            except (OSError, TypeError):
                lineno = -1  # If source can't be retrieved
            subclasses.append((lineno, obj.__name__))
    if not subclasses:
        return None
    # Sort by source line number
    subclasses.sort(key=lambda x: x[0])
    # Return the *last* by line number
    class_name = subclasses[-1][1]
    return module, class_name


def get_tool_class(file_path, class_name=None):
    if class_name:
        module = import_module(file_path)
    else:
        module, class_name = get_tool_class_name_from_path(file_path)
    try:
        # Add imported class to the result dictionary
        return getattr(module, class_name)
    except:
        raise RuntimeError(f"Failed to instantiate class {class_name} from file {file_path}")


def get_os_code_path(code_path):
    code_path = code_path.strip('/')
    if not code_path.endswith('.py'):
        code_path += '.py'
    return os.path.join(config.library_config.base_path, config.library_config.tool_code_path, code_path)


def is_os_code_path(code_path):
    return code_path.startswith(os.path.join(config.library_config.base_path, config.library_config.tool_code_path))


def get_library_path(os_code_path):
    assert is_os_code_path(os_code_path)
    lib_path = os_code_path.replace(os.path.join(config.library_config.base_path, config.library_config.tool_code_path), "")
    if not lib_path.startswith('/'):
        lib_path = '/' + lib_path
    if lib_path.endswith('.py'):
        lib_path = lib_path[:-3]
    return lib_path

def async_report_traceback(f):
    async def wrapped_f(*args, **kwargs):
        try:
            return await f(*args, **kwargs)
        except Exception as e:
            import traceback
            tb_str = traceback.format_exc()
            from app.utils.debug_logger import debug_print
            debug_print(f"Exception in {f.__name__}: {str(e)}\nTraceback:\n{tb_str}")
            raise e
    return wrapped_f