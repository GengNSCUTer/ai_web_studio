"""原任务的否定和标点不能把允许的文件工具误删。"""

import asyncio
import unittest
from types import SimpleNamespace

from app.services.tools.schemas import ToolDefinition
from app.services.tools.selector import ToolCandidateSelector


class SelectorScopeWordingTest(unittest.TestCase):
    def setUp(self):
        definitions = [
            ToolDefinition("workspace.files.read", "workspace", "workspace_file", "读取文件", "读取工作区文件",
                           when_to_use=["读取文件", "本地文件"], adapter_type="workspace_file"),
            ToolDefinition("tavily.search", "tavily", "web_search", "网页搜索", "联网搜索"),
        ]
        self.selector = ToolCandidateSelector(catalog=SimpleNamespace(
            list_definitions=lambda: definitions,
            get_or_none=lambda key: next((tool for tool in definitions if tool.tool_key == key), None),
        ))

    def test_local_read_with_no_web_remains_available_sync_and_async(self):
        query = "请搜索并读取 stage46a-readme.md，报告文件第一行及用途，只读文件，不联网不修改。"
        self.assertIsNone(self.selector.FILE_EXCLUSION_PATTERN.search(query))
        for tools, _ in [self.selector.select(query=query, enabled=True),
                         asyncio.run(self.selector.select_async(query=query, enabled=True))]:
            keys = {tool.tool_key for tool in tools}
            self.assertIn("workspace.files.read", keys)
            self.assertNotIn("tavily.search", keys)

    def test_explicit_online_only_still_excludes_files(self):
        for query in ["只使用网页查询资料", "仅从官网搜索资料", "只联网搜索最新资料"]:
            with self.subTest(query=query):
                tools, _ = self.selector.select(query=query, enabled=True)
                self.assertNotIn("workspace.files.read", {tool.tool_key for tool in tools})

    def test_explicit_no_files_still_excludes_files(self):
        tools, _ = self.selector.select(query="不要读取工作区文件，只回答问题", enabled=True)
        self.assertNotIn("workspace.files.read", {tool.tool_key for tool in tools})
