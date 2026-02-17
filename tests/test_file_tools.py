"""File tools 单元测试。"""

from __future__ import annotations

import os
import tempfile

import pytest

from tyrant_core.runtime.file_tools import build_file_tools


@pytest.fixture
def tools():
    """获取 file_read 和 file_grep 函数。"""
    fns = build_file_tools()
    return {"file_read": fns[0], "file_grep": fns[1]}


@pytest.fixture
def sample_file():
    """创建一个临时文件用于测试。"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
        for i in range(1, 101):
            f.write(f"line {i}: content here\n")
        path = f.name
    yield path
    os.unlink(path)


class TestFileRead:
    """测试 file_read tool。"""

    async def test_read_entire_file(self, tools, sample_file):
        result = await tools["file_read"](sample_file)
        assert result["total_lines"] == 100
        assert "1|line 1:" in result["content"]
        assert "100|line 100:" in result["content"]

    async def test_read_range(self, tools, sample_file):
        result = await tools["file_read"](sample_file, 10, 15)
        assert result["range"] == "10-15"
        assert "10|line 10:" in result["content"]
        assert "15|line 15:" in result["content"]
        # 不应包含第 9 行和第 16 行
        assert "9|" not in result["content"]
        assert "16|" not in result["content"]

    async def test_read_from_start(self, tools, sample_file):
        result = await tools["file_read"](sample_file, 1, 3)
        assert result["range"] == "1-3"
        lines = result["content"].split("\n")
        assert len(lines) == 3

    async def test_read_end_exceeds_total(self, tools, sample_file):
        result = await tools["file_read"](sample_file, 95, 200)
        assert result["range"] == "95-100"

    async def test_read_start_exceeds_total(self, tools, sample_file):
        result = await tools["file_read"](sample_file, 200, 300)
        assert result["content"] == ""
        assert result["total_lines"] == 100

    async def test_read_nonexistent_file(self, tools):
        result = await tools["file_read"]("/nonexistent/path.txt")
        assert "error" in result
        assert "不存在" in result["error"]

    async def test_read_empty_file(self, tools):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            path = f.name
        try:
            result = await tools["file_read"](path)
            assert result["total_lines"] == 0
            assert result["content"] == ""
        finally:
            os.unlink(path)


class TestFileGrep:
    """测试 file_grep tool。"""

    async def test_grep_basic_pattern(self, tools, sample_file):
        result = await tools["file_grep"](sample_file, "line 42:")
        assert result["total_matches"] == 1
        assert result["matches"][0]["line"] == 42
        assert "line 42:" in result["matches"][0]["content"]

    async def test_grep_regex(self, tools, sample_file):
        result = await tools["file_grep"](sample_file, r"line [1-9]0:")
        # 匹配 line 10, 20, 30, ..., 90
        assert result["total_matches"] == 9

    async def test_grep_no_matches(self, tools, sample_file):
        result = await tools["file_grep"](sample_file, "NONEXISTENT")
        assert result["total_matches"] == 0
        assert result["matches"] == []

    async def test_grep_max_matches(self, tools, sample_file):
        result = await tools["file_grep"](sample_file, "line", max_matches=5)
        assert result["total_matches"] == 5
        assert result["truncated"] is True

    async def test_grep_invalid_regex(self, tools, sample_file):
        result = await tools["file_grep"](sample_file, "[invalid")
        assert "error" in result

    async def test_grep_nonexistent_file(self, tools):
        result = await tools["file_grep"]("/nonexistent/path.txt", "test")
        assert "error" in result
