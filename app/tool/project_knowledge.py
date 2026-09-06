"""Read-only bridge to the OpenManus project course-material knowledge base."""
from __future__ import annotations

import sys
from pathlib import Path

from app.tool.base import BaseTool, ToolResult

PROJECT_ROOT = Path(__file__).resolve().parents[4]
KNOWLEDGE_SRC = PROJECT_ROOT / "src"
if str(KNOWLEDGE_SRC) not in sys.path:
    sys.path.insert(0, str(KNOWLEDGE_SRC))


class ProjectKnowledgeTool(BaseTool):
    """Search stable course/source material; never use it for live web facts."""

    name: str = "search_project_knowledge"
    description: str = (
        "检索本项目的课程资料、架构笔记和已沉淀的开发知识。"
        "当需要确认 OpenManus 架构、RAG、浏览器 DOM/GU​​I 策略、Daytona 或课程案例时使用。"
        "返回原文片段及 PDF 页码来源；不能用于查询实时网页、价格、新闻等信息。"
    )
    parameters: dict = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "要检索的具体问题或关键词。",
            },
            "top_k": {
                "type": "integer",
                "description": "最多返回几条结果，默认 3，最大 5。",
                "minimum": 1,
                "maximum": 5,
            },
        },
        "required": ["query"],
    }

    async def execute(self, query: str, top_k: int = 3) -> ToolResult:
        try:
            from knowledge_base import KnowledgeBase

            kb = KnowledgeBase(
                PROJECT_ROOT / "data" / "index" / "chroma",
                model_cache=PROJECT_ROOT / "data" / "models" / "huggingface",
            )
            results = kb.search(query, top_k=min(max(top_k, 1), 5), mode="hybrid")
            if not results:
                return self.success_response("课程知识库中没有找到相关内容。")
            chunks = []
            for index, item in enumerate(results, start=1):
                chunks.append(
                    f"[{index}] 来源：{item.citation}\n"
                    f"相关度：{item.score:.4f}\n"
                    f"内容：{item.text}"
                )
            return self.success_response("\n\n".join(chunks))
        except Exception as exc:
            return self.fail_response(f"课程知识库检索失败：{exc}")
