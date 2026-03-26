"""通用文件操作 tools。所有 Agent 可用。"""

from __future__ import annotations

import re
from typing import Callable


def build_file_tools() -> list[Callable]:
    """构建文件操作 tool 列表。"""

    async def file_read(
        path: str,
        start_line: int = 1,
        end_line: int = -1,
    ) -> dict:
        """按行范围读取文件内容。

        Args:
            path: 文件路径
            start_line: 起始行号（从 1 开始，默认 1）
            end_line: 结束行号（包含，默认 -1 表示到末尾）
        """
        try:
            with open(path, "r", encoding="utf-8") as f:
                all_lines = f.readlines()
        except FileNotFoundError:
            return {"error": f"文件不存在: {path}"}
        except PermissionError:
            return {"error": f"无权限读取: {path}"}
        except UnicodeDecodeError:
            return {"error": f"文件非文本格式: {path}"}

        total = len(all_lines)
        if total == 0:
            return {"content": "", "total_lines": 0}

        # 规范化行号
        start = max(1, start_line)
        end = total if end_line < 0 else min(end_line, total)

        if start > total:
            return {"content": "", "total_lines": total, "note": f"起始行 {start} 超出文件总行数 {total}"}

        selected = all_lines[start - 1:end]
        # 添加行号前缀
        numbered = []
        for i, line in enumerate(selected, start=start):
            numbered.append(f"{i}|{line.rstrip()}")
        content = "\n".join(numbered)

        return {
            "content": content,
            "total_lines": total,
            "range": f"{start}-{end}",
        }

    async def file_grep(
        path: str,
        pattern: str,
        max_matches: int = 50,
    ) -> dict:
        """在文件中搜索匹配正则表达式的行。

        Args:
            path: 文件路径
            pattern: 正则表达式模式
            max_matches: 最大匹配数（默认 50）
        """
        try:
            compiled = re.compile(pattern)
        except re.error as e:
            return {"error": f"无效的正则表达式: {e}"}

        try:
            with open(path, "r", encoding="utf-8") as f:
                all_lines = f.readlines()
        except FileNotFoundError:
            return {"error": f"文件不存在: {path}"}
        except PermissionError:
            return {"error": f"无权限读取: {path}"}
        except UnicodeDecodeError:
            return {"error": f"文件非文本格式: {path}"}

        matches = []
        for i, line in enumerate(all_lines, start=1):
            if compiled.search(line):
                matches.append({"line": i, "content": line.rstrip()})
                if len(matches) >= max_matches:
                    break

        return {
            "matches": matches,
            "total_matches": len(matches),
            "truncated": len(matches) >= max_matches,
            "total_lines": len(all_lines),
        }

    return [file_read, file_grep]
