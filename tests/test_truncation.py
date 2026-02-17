"""截断函数单元测试。"""

from __future__ import annotations

import os
import tempfile

from tyrant_core.runtime.truncation import truncate_if_needed


class TestTruncateIfNeeded:
    """测试 truncate_if_needed 函数。"""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()

    def test_no_truncation_when_within_limits(self):
        """内容未超出阈值，原样返回。"""
        content = "line1\nline2\nline3"
        result = truncate_if_needed(
            content, data_dir=self.tmpdir, max_lines=10, max_chars=1000,
            context_label="test",
        )
        assert result == content

    def test_truncation_by_line_count(self):
        """超出行数阈值触发截断。"""
        content = "\n".join(f"line {i}" for i in range(300))
        result = truncate_if_needed(
            content, data_dir=self.tmpdir, max_lines=200, max_chars=999999,
            context_label="test",
        )
        assert "[内容已截断]" in result
        assert "300 行" in result
        assert "file_read" in result
        assert "file_grep" in result

    def test_truncation_by_char_count(self):
        """超出字符数阈值触发截断。"""
        content = "x" * 6000
        result = truncate_if_needed(
            content, data_dir=self.tmpdir, max_lines=999999, max_chars=5000,
            context_label="test",
        )
        assert "[内容已截断]" in result
        assert "6000 字符" in result

    def test_truncation_creates_file(self):
        """截断时应创建完整输出文件。"""
        content = "\n".join(f"line {i}" for i in range(300))
        result = truncate_if_needed(
            content, data_dir=self.tmpdir, max_lines=200, max_chars=999999,
            context_label="test",
        )
        # 检查文件存在
        output_dir = os.path.join(self.tmpdir, "truncated_outputs")
        assert os.path.isdir(output_dir)
        files = os.listdir(output_dir)
        assert len(files) == 1
        assert files[0].startswith("test_")
        assert files[0].endswith(".log")

        # 检查文件内容完整
        filepath = os.path.join(output_dir, files[0])
        with open(filepath) as f:
            saved = f.read()
        assert saved == content

    def test_truncation_contains_tail(self):
        """截断结果应包含尾部内容。"""
        lines = [f"line {i}" for i in range(300)]
        content = "\n".join(lines)
        result = truncate_if_needed(
            content, data_dir=self.tmpdir, max_lines=200, max_chars=999999,
            context_label="test",
        )
        # 应包含最后几行
        assert "line 299" in result

    def test_exact_boundary_no_truncation(self):
        """恰好在阈值边界，不触发截断。"""
        content = "\n".join(f"line {i}" for i in range(200))
        result = truncate_if_needed(
            content, data_dir=self.tmpdir, max_lines=200, max_chars=999999,
            context_label="test",
        )
        assert result == content

    def test_empty_content(self):
        """空内容不截断。"""
        result = truncate_if_needed(
            "", data_dir=self.tmpdir, max_lines=200, max_chars=5000,
            context_label="test",
        )
        assert result == ""

    def test_context_label_in_filename(self):
        """context_label 应出现在文件名中。"""
        content = "\n".join(f"line {i}" for i in range(300))
        truncate_if_needed(
            content, data_dir=self.tmpdir, max_lines=200, max_chars=999999,
            context_label="tool_query",
        )
        output_dir = os.path.join(self.tmpdir, "truncated_outputs")
        files = os.listdir(output_dir)
        assert files[0].startswith("tool_query_")
