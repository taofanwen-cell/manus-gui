"""Validate the effective DashScope credential without printing it."""
import sys
from pathlib import Path

# Allow direct execution as python scripts\\test_dashscope_connection.py.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from openai import OpenAI

from app.config import config


def main() -> None:
    settings = config.llm["default"]
    if not settings.api_key:
        raise SystemExit(
            "No DashScope key found. Run scripts\\set_dashscope_key.ps1 or set DASHSCOPE_API_KEY."
        )
    client = OpenAI(api_key=settings.api_key, base_url=settings.base_url)
    response = client.chat.completions.create(
        model=settings.model,
        messages=[{"role": "user", "content": "Reply with OK."}],
        max_tokens=8,
        temperature=0,
    )
    content = (response.choices[0].message.content or "").strip()
    print(f"DashScope authentication OK; model={settings.model}; response={content[:32]!r}")


if __name__ == "__main__":
    main()
