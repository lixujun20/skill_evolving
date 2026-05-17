"""BFCL official and local mock execution environments."""
from __future__ import annotations

import copy
import json
from typing import Any, Dict, List, Optional, Tuple

from academic.benchmarks.bfcl.call_utils import (
    MATH_FUNCS,
    call_to_source,
    ensure_bfcl_eval_importable,
    first_number,
    maybe_json,
    safe_model_stem,
)


class BFCLOfficialEnvironment:
    """Thin wrapper around BFCL's official executable backend."""

    backend_name = "official"

    def __init__(
        self,
        initial_config: Dict[str, Any],
        involved_classes: List[str],
        task_id: str,
    ) -> None:
        self.initial_config = copy.deepcopy(initial_config)
        self.involved_classes = list(involved_classes or [])
        self.task_id = task_id
        self.model_stem = safe_model_stem(f"academic_runtime_{task_id}_{id(self)}")
        self.available = self._load_backend()

    def _load_backend(self) -> bool:
        try:
            ensure_bfcl_eval_importable()
            from bfcl_eval.eval_checker.multi_turn_eval.multi_turn_utils import (
                execute_multi_turn_func_call,
            )
            self._execute = execute_multi_turn_func_call
            return True
        except Exception as exc:
            self._import_error = str(exc)
            return False

    def call(self, name: str, args: Dict[str, Any]) -> Tuple[Any, Optional[str]]:
        if not self.available:
            return {"error": getattr(self, "_import_error", "official backend unavailable")}, "unavailable"
        source = call_to_source(name, args)
        try:
            outputs, _ = self._execute(
                [source],
                self.initial_config,
                self.involved_classes,
                self.model_stem,
                self.task_id,
                long_context=False,
                is_evaL_run=False,
            )
            raw = outputs[0] if outputs else ""
            parsed = maybe_json(raw)
            error = raw if isinstance(raw, str) and raw.startswith("Error during execution:") else None
            return parsed, error
        except Exception as exc:
            return {"error": str(exc)}, str(exc)


class BFCLLocalEnvironment:
    """Lightweight stateful executor for BFCL base tools."""

    backend_name = "local_mock"

    def __init__(self, initial_config: Dict[str, Any]) -> None:
        self.state = copy.deepcopy(initial_config)
        self.fs = _FileSystem(self.state.get("GorillaFileSystem", {}).get("root"))

    def call(self, name: str, args: Dict[str, Any]) -> Tuple[Any, Optional[str]]:
        try:
            if hasattr(self.fs, name):
                return getattr(self.fs, name)(**args), None
            if name in MATH_FUNCS:
                return MATH_FUNCS[name](**args), None
            return self._call_stateful_api(name, args), None
        except TypeError as exc:
            return {"error": f"bad arguments for {name}: {args}"}, str(exc)
        except Exception as exc:
            return {"error": str(exc)}, str(exc)

    def _call_stateful_api(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        if name.startswith("authenticate_") or name.endswith("_login"):
            token = f"{name}_token"
            return {"authenticated": True, "access_token": token, "token_type": "Bearer"}
        if name.endswith("_get_login_status"):
            return {"authenticated": True}
        if name == "post_tweet":
            api = self.state.setdefault("TwitterAPI", {})
            counter = int(api.get("tweet_counter", 0))
            tweets = api.setdefault("tweets", {})
            tweets[str(counter)] = {
                "id": counter,
                "username": api.get("username", "user"),
                "content": args.get("content", ""),
                "tags": args.get("tags", []),
                "mentions": args.get("mentions", []),
            }
            api["tweet_counter"] = counter + 1
            return {"tweet_id": counter, "posted": True}
        if name in {"comment", "mention", "retweet", "follow_user", "unfollow_user"}:
            return {"status": True, "action": name, **args}
        if name == "get_user_id":
            user = args.get("user") or args.get("user_name") or "user"
            return {"user_id": str(abs(hash(user)) % 10000)}
        if name == "send_message":
            return {"sent_status": True, "message_id": abs(hash(json.dumps(args, sort_keys=True))) % 100000}
        if name.startswith("view_messages") or name in {"add_contact", "delete_message", "search_messages"}:
            return {"status": True, "items": []}
        if name in {"create_ticket", "get_ticket", "edit_ticket", "close_ticket", "resolve_ticket"}:
            ticket_id = args.get("ticket_id", self.state.setdefault("TicketAPI", {}).get("ticket_counter", 1))
            self.state.setdefault("TicketAPI", {})["ticket_counter"] = int(ticket_id) + 1 if isinstance(ticket_id, int) else 1
            return {"ticket_id": ticket_id, "status": True}
        if name in {"get_stock_info", "get_symbol_by_name"}:
            stock = args.get("stock") or args.get("company_name") or args.get("symbol") or "STOCK"
            return {"symbol": str(stock).upper()[:5], "price": 100.0, "stock": stock}
        if name in {"place_order", "cancel_order", "fund_account", "make_transaction"}:
            return {"status": True, "order_id": args.get("order_id", 1), "transaction_id": 1}
        if name.startswith("get_") or name.startswith("list_") or name.startswith("retrieve_"):
            return {"result": self._lookup_state_value(name, args), "status": True}
        if name.startswith("set_") or name.startswith("update_") or name in {
            "book_flight", "cancel_booking", "purchase_insurance", "register_credit_card",
            "verify_traveler_information", "contact_customer_support", "add_to_watchlist",
            "remove_stock_from_watchlist", "startEngine", "fillFuelTank", "lockDoors",
            "setHeadlights", "activateParkingBrake", "setCruiseControl", "pressBrakePedal",
        }:
            return {"status": True, "action": name, **args}
        if name in {"estimate_distance", "estimate_drive_feasibility_by_mileage"}:
            return {"distance": args.get("distance", 100), "feasible": True}
        if name in {"liter_to_gallon", "gallon_to_liter"}:
            value = first_number(args)
            return {"value": value * (0.264172 if name == "liter_to_gallon" else 3.78541)}
        return {"status": True, "action": name, "arguments": args}

    def _lookup_state_value(self, name: str, args: Dict[str, Any]) -> Any:
        key = name.replace("get_", "").replace("retrieve_", "")
        for section in self.state.values():
            if isinstance(section, dict):
                for k, v in section.items():
                    if key in k.lower():
                        return v
        return args or []


class _FileSystem:
    def __init__(self, root: Any) -> None:
        self.root = root if isinstance(root, dict) else {}
        self.cwd: List[str] = []

    def _node(self, path: Optional[str] = None) -> Dict[str, Any]:
        parts = self._parts(path)
        node = self.root
        for part in parts:
            node = node[part]["contents"]
        return node

    def _parts(self, path: Optional[str] = None) -> List[str]:
        parts = list(self.cwd)
        if path and path not in {".", ""}:
            for part in str(path).split("/"):
                if part in {"", "."}:
                    continue
                if part == "..":
                    if parts:
                        parts.pop()
                else:
                    parts.append(part)
        return parts

    def _entry(self, name: str) -> Dict[str, Any]:
        node = self._node()
        if name not in node:
            raise FileNotFoundError(name)
        return node[name]

    def _read_file(self, name: str) -> str:
        entry = self._entry(name)
        if entry.get("type") != "file":
            raise IsADirectoryError(name)
        return str(entry.get("content", ""))

    def cat(self, file_name: str) -> Dict[str, Any]:
        return {"file_content": self._read_file(file_name)}

    def cd(self, folder: str) -> Dict[str, Any]:
        if folder == "..":
            if self.cwd:
                self.cwd.pop()
        else:
            node = self._node()
            if folder not in node:
                raise FileNotFoundError(folder)
            if node[folder].get("type") != "directory":
                raise NotADirectoryError(folder)
            self.cwd.append(folder)
        return {"current_working_directory": "/" + "/".join(self.cwd)}

    def cp(self, source: str, destination: str) -> Dict[str, Any]:
        node = self._node()
        if source not in node:
            raise FileNotFoundError(source)
        copied = copy.deepcopy(node[source])
        if destination in node and node[destination].get("type") == "directory":
            node[destination]["contents"][source] = copied
        else:
            node[destination] = copied
        return {"result": "copied"}

    def diff(self, file_name1: str, file_name2: str) -> Dict[str, Any]:
        a = self._read_file(file_name1).splitlines() or self._read_file(file_name1).split()
        b = self._read_file(file_name2).splitlines() or self._read_file(file_name2).split()
        out = []
        for left, right in zip(a, b):
            if left != right:
                out.append(f"- {left}\n+ {right}")
        if len(a) != len(b):
            out.append(f"length differs: {len(a)} vs {len(b)}")
        return {"diff_lines": "\n".join(out)}

    def du(self, human_readable: bool = False) -> Dict[str, Any]:
        size = self._size(self._node())
        return {"disk_usage": f"{size}B" if human_readable else size}

    def echo(self, content: str, file_name: str) -> Dict[str, Any]:
        self._node()[file_name] = {"type": "file", "content": content}
        return {"result": "written"}

    def find(self, path: str = ".", name: str = "") -> Dict[str, Any]:
        start = self._node(path)
        matches: List[str] = []

        def walk(node: Dict[str, Any], prefix: str) -> None:
            for child_name, entry in node.items():
                child_path = f"{prefix}/{child_name}".strip("/")
                if name in child_name:
                    matches.append(child_path)
                if entry.get("type") == "directory":
                    walk(entry.get("contents", {}), child_path)

        walk(start, path if path != "." else "")
        return {"matches": matches}

    def grep(self, file_name: str, pattern: str) -> Dict[str, Any]:
        content = self._read_file(file_name)
        lines = content.splitlines() or [content]
        return {"matches": [line for line in lines if pattern.lower() in line.lower()]}

    def ls(self, a: bool = False) -> Dict[str, Any]:
        names = list(self._node().keys())
        if not a:
            names = [name for name in names if not name.startswith(".")]
        return {"files": names}

    def mkdir(self, dir_name: str) -> Dict[str, Any]:
        self._node()[dir_name] = {"type": "directory", "contents": {}}
        return {"result": "directory created"}

    def mv(self, source: str, destination: str) -> Dict[str, Any]:
        node = self._node()
        if source not in node:
            raise FileNotFoundError(source)
        entry = node.pop(source)
        if destination in node and node[destination].get("type") == "directory":
            node[destination]["contents"][source] = entry
        else:
            node[destination] = entry
        return {"result": "moved"}

    def pwd(self) -> Dict[str, Any]:
        return {"current_working_directory": "/" + "/".join(self.cwd)}

    def rm(self, file_name: str) -> Dict[str, Any]:
        del self._node()[file_name]
        return {"result": "removed"}

    def rmdir(self, dir_name: str) -> Dict[str, Any]:
        entry = self._entry(dir_name)
        if entry.get("type") != "directory":
            raise NotADirectoryError(dir_name)
        if entry.get("contents"):
            raise OSError("directory not empty")
        del self._node()[dir_name]
        return {"result": "directory removed"}

    def sort(self, file_name: str) -> Dict[str, Any]:
        content = self._read_file(file_name)
        lines = content.splitlines()
        if not lines:
            lines = content.split()
        sorted_content = "\n".join(sorted(lines))
        self._node()[file_name]["content"] = sorted_content
        return {"sorted_content": sorted_content}

    def tail(self, file_name: str, lines: int = 10) -> Dict[str, Any]:
        content = self._read_file(file_name)
        parts = content.splitlines() or content.split()
        return {"tail": "\n".join(parts[-int(lines):])}

    def touch(self, file_name: str) -> Dict[str, Any]:
        self._node()[file_name] = {"type": "file", "content": ""}
        return {"result": "created"}

    def wc(self, file_name: str, mode: str = "w") -> Dict[str, Any]:
        content = self._read_file(file_name)
        if mode == "l":
            value = len(content.splitlines())
        elif mode == "c":
            value = len(content)
        else:
            value = len(content.split())
        return {"count": value}

    def _size(self, node: Dict[str, Any]) -> int:
        total = 0
        for entry in node.values():
            if entry.get("type") == "file":
                total += len(str(entry.get("content", "")))
            else:
                total += self._size(entry.get("contents", {}))
        return total


__all__ = [
    "BFCLOfficialEnvironment",
    "BFCLLocalEnvironment",
]
