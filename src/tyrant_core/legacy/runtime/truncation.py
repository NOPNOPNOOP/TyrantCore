"""Agent 输入截断工具。保护 Agent 上下文窗口，避免超长内容。"""

from __future__ import annotations

import os
from datetime import datetime, timezone


def truncate_if_needed(
    content: str,
    *,
    data_dir: str,
    max_lines: int,
    max_chars: int,
    context_label: str,
) -> str:
    """如果内容超出阈值，存文件并返回尾部截取 + 指引。

    阈值取先到的（行数或字符数）。

    Args:
        content: 原始内容
        data_dir: 数据目录（存放截断文件）
        max_lines: 最大行数
        max_chars: 最大字符数
        context_label: 上下文标签（用于文件名，如 "tool_result" / "message"）

    Returns:
        原始内容（未超出）或截断内容 + 指引（超出时）
    """
    lines = content.split("\n")
    num_lines = len(lines)
    num_chars = len(content)

    if num_lines <= max_lines and num_chars <= max_chars:
        return content

    # 存完整内容到文件
    output_dir = os.path.join(data_dir, "truncated_outputs")
    os.makedirs(output_dir, exist_ok=True)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    filename = f"{context_label}_{ts}.log"
    filepath = os.path.join(output_dir, filename)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

    # 计算尾部截取（保留约 max_chars 的一半或 max_lines 的一半，取较小值）
    tail_lines = min(max_lines // 2, 50)
    tail = "\n".join(lines[-tail_lines:])
    if len(tail) > max_chars // 2:
        tail = tail[-(max_chars // 2):]

    return (
        f"[内容已截断] 完整输出共 {num_lines} 行 / {num_chars} 字符，"
        f"已保存到: {filepath}\n"
        f"你可以使用以下工具查看完整内容：\n"
        f'  file_read("{filepath}", 1, 50)  # 查看开头 50 行\n'
        f'  file_read("{filepath}", {max(1, num_lines - 50)}, {num_lines})'
        f"  # 查看结尾 50 行\n"
        f'  file_grep("{filepath}", "关键词")  # 搜索关键内容\n'
        f"\n--- 以下是尾部截取 ---\n{tail}"
    )
