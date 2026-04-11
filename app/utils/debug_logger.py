from datetime import datetime
import os
import threading

# Thread-safe file handle for LLM trace logging
_trace_file = None
_trace_lock = threading.Lock()

def _get_trace_file():
    """Get or open the LLM trace log file. Returns None if tracing is disabled."""
    global _trace_file
    trace_path = os.getenv("LLM_TRACE_FILE")
    if not trace_path:
        return None
    with _trace_lock:
        if _trace_file is None or _trace_file.closed:
            _trace_file = open(trace_path, "a", encoding="utf-8", buffering=1)
    return _trace_file


def llm_trace(message: str):
    """Write an LLM trace entry to the trace file (set LLM_TRACE_FILE env var to enable).
    Writes to file only — never to stdout — to avoid pytest capture inflation."""
    f = _get_trace_file()
    if f is None:
        return
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _trace_lock:
        f.write(f"\n[{timestamp}] {message}\n")
        f.flush()


def truncate_msg_content(msg: dict, max_len: int = 400) -> dict:
    """Return a shallow copy of a message dict with long content fields truncated."""
    if not isinstance(msg, dict):
        return msg
    result = dict(msg)
    for key in ("content",):
        val = result.get(key)
        if isinstance(val, str) and len(val) > max_len:
            result[key] = val[:max_len] + f" ...[+{len(val)-max_len}chars]"
        elif isinstance(val, list):
            # multi-part content (e.g. vision): just stringify and truncate
            s = str(val)
            result[key] = s[:max_len] + f" ...[+{len(s)-max_len}chars]" if len(s) > max_len else s
    return result


def debug_print(message: str):
    # Legacy: still supports DEBUG_MODE=1 for stdout output
    if os.getenv("DEBUG_MODE") == "1":
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        import inspect
        frame = inspect.currentframe().f_back
        filename = frame.f_code.co_filename
        lineno = frame.f_lineno
        print(f"\n[{timestamp}] [{filename}:{lineno}] {message}")
