from __future__ import annotations

import json
import re
from urllib.parse import unquote, urlsplit

from app.config import PROJECT_ROOT

DOCUMENTS = (PROJECT_ROOT / "README.md", PROJECT_ROOT / "MVP开发指导.md")


def test_relative_markdown_links_resolve_to_repository_files() -> None:
    missing: list[str] = []
    pattern = re.compile(r"\[[^]]+\]\(([^)]+)\)")
    for document in DOCUMENTS:
        for target in pattern.findall(document.read_text(encoding="utf-8")):
            destination = target.split("#", 1)[0].strip().strip("<>")
            parsed = urlsplit(destination)
            if not destination or parsed.scheme or destination.startswith("#"):
                continue
            resolved = (document.parent / unquote(destination)).resolve()
            if not resolved.exists():
                missing.append(f"{document.name}: {destination}")
    assert missing == []


def test_json_examples_parse_and_mermaid_fences_are_balanced() -> None:
    for document in DOCUMENTS:
        text = document.read_text(encoding="utf-8")
        json_blocks = re.findall(r"```json\s*\n(.*?)```", text, flags=re.DOTALL)
        for index, block in enumerate(json_blocks, start=1):
            try:
                json.loads(block)
            except json.JSONDecodeError as exc:
                raise AssertionError(f"{document.name} JSON block {index}: {exc}") from exc
        mermaid_blocks = re.findall(r"```mermaid\s*\n(.*?)```", text, flags=re.DOTALL)
        assert all(block.strip() for block in mermaid_blocks)
        assert text.count("```mermaid") == len(mermaid_blocks)
