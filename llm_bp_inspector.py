#!/usr/bin/env python3
"""
llm_bp_inspector.py — LLM 断点交互检查器

用法:
  # 终端 A: 在工程根目录下运行检查器（前台，会在断点处暂停并开启 REPL）
  conda run -n meta-agent python llm_bp_inspector.py

  # 终端 B: 后台运行测试
  LLM_CACHE_ENABLED=1 LLM_BP_ENABLED=1 conda run -n meta-agent \\
    python -m pytest app/meta_agent/skills/tests/integration/ \\
    -vs -m llm -k test_round_by_round_with_verbose_output

在 REPL 中可用的变量:
  call_number, label, response, extra, state
  _messages_repr, _system_msgs_repr

退出 REPL（Ctrl-D 或 exit()）→ 自动 resume 测试继续运行到下一个断点
"""
import json
import os
import sys
import time

_PAUSE  = "/tmp/llm_bp_pause"
_RESUME = "/tmp/llm_bp_resume"
_STATE  = "/tmp/llm_bp_state.json"
_NS     = "/tmp/llm_bp_ns.py"
_LOG    = "/tmp/llm_debug_log.md"

SEP = "=" * 70


def _clear_stale():
    """Remove stale signal files from a previous run."""
    for p in (_PAUSE, _RESUME):
        try:
            os.remove(p)
        except FileNotFoundError:
            pass


def _load_state() -> dict:
    with open(_STATE, encoding="utf-8") as f:
        return json.load(f)


def _print_banner(state: dict):
    call_no = state.get("call_number", "?")
    label   = state.get("label", "?")
    msgs    = state.get("messages_count", "?")
    resp    = state.get("response", "")[:300]
    extra   = state.get("extra", {})

    print(f"\n{SEP}")
    print(f"  🔴  LLM BREAKPOINT #{call_no}: {label}")
    print(SEP)
    print(f"  messages_count : {msgs}")
    if extra.get("tool_calls_summary"):
        print(f"  tool_calls     : {extra['tool_calls_summary'][:150]}")
    print(f"\n  response[:300] :\n  {resp!r}\n")
    print(f"  Loaded vars: call_number, label, response, extra, state,")
    print(f"               _messages_repr, _system_msgs_repr")
    print(f"\n  Ctrl-D / exit() → resume test")
    print(f"  Log: {_LOG}")
    print(SEP + "\n")


def _open_repl(state: dict):
    ns: dict = {}
    if os.path.exists(_NS):
        try:
            exec(open(_NS, encoding="utf-8").read(), ns)
        except Exception as e:
            print(f"[inspector] warning: failed to exec ns file: {e}")
    ns.setdefault("state", state)

    print(f"\n[inspector] REPL 已就绪。退出 REPL (Ctrl-D) 后等待下一个断点（不会自动 resume）。")
    print(f"[inspector] 如需 resume 测试，请在另一个终端执行: touch /tmp/llm_bp_resume\n")

    # Try IPython first, fall back to code.interact
    try:
        import IPython
        IPython.embed(
            user_ns=ns,
            banner1=f"[IPython REPL — BP#{state.get('call_number')}] 退出后等待下一断点",
            banner2="",
            exit_msg="[inspector] REPL 已退出，等待下一断点...",
            colors="neutral",
        )
    except ImportError:
        import code
        code.interact(
            local=ns,
            banner=f"[Python REPL — BP#{state.get('call_number')}] Ctrl-D 退出（不会 resume）",
            exitmsg="[inspector] REPL 已退出，等待下一断点...",
        )


def main():
    _clear_stale()
    print(f"\n[inspector] 监听 LLM 断点中...")
    print(f"[inspector] 断点触发时将自动开启 IPython REPL — 变量: call_number, label, response, state")
    print(f"[inspector] 退出 REPL 后等待下一断点（resume 由 Copilot 控制，不需要手动操作）")
    print(f"[inspector] Ctrl-C 退出 inspector\n")

    try:
        last_ns_mtime = 0.0
        while True:
            if os.path.exists(_NS):
                try:
                    mtime = os.path.getmtime(_NS)
                except OSError:
                    mtime = 0.0
                if mtime > last_ns_mtime and os.path.exists(_PAUSE):
                    last_ns_mtime = mtime
                    try:
                        state = _load_state()
                    except Exception as e:
                        print(f"[inspector] failed to load state: {e}")
                        time.sleep(0.5)
                        continue
                    _print_banner(state)
                    _open_repl(state)
            # Poll every 200ms
            time.sleep(0.2)

    except KeyboardInterrupt:
        print("\n[inspector] 已退出")
        sys.exit(0)


if __name__ == "__main__":
    main()
