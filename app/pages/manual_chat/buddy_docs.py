#!/usr/bin/env python3
"""
Buddy's own documentation, searchable - what Ask Buddy looks in when the
question is about Buddy rather than about Resolve ("where are my chats
kept?", "how does Footage Search index a project?").

Two sources, read when first searched:

  README.md        cut into its sections (a long one into pieces), each
                   under its heading path. In the source tree it sits at
                   the repo's root; build_buddy_zip.py puts a copy at the
                   package's root, which is where an installed Buddy finds it.
  tools_kb.json    every tool's full description (ToolInfo.detail) - the
                   same text describe_tool returns, here so a search across
                   all the tools finds the one that does a thing.

Keyword search only (retrieval.BM25): a few hundred short passages, where
the words of a question about Buddy are the words its docs use. No Qt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from core.tools_kb import load_tools

from .retrieval import BM25, tokenize

README_NAME = "README.md"
MAX_PASSAGE = 1400
_HEADING = re.compile(r"^(#{1,4})\s+(.*\S)\s*$")


@dataclass
class DocPassage:
    title: str      # "Ask Buddy > Changing your project", or "Tool: Media Relink"
    text: str


def readme_path() -> Path | None:
    here = Path(__file__).resolve()
    for root in (here.parents[2], here.parents[3]):     # the package (installed), the repo (source)
        path = root / README_NAME
        if path.is_file():
            return path
    return None


def _pieces(text: str) -> list[str]:
    """A section's text in passages of MAX_PASSAGE or less, cut between paragraphs."""
    out, current = [], ""
    for paragraph in re.split(r"\n\s*\n", text.strip()):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if current and len(current) + len(paragraph) + 2 > MAX_PASSAGE:
            out.append(current)
            current = ""
        current = f"{current}\n\n{paragraph}" if current else paragraph
        while len(current) > MAX_PASSAGE * 2:           # one enormous paragraph (a long list)
            out.append(current[:MAX_PASSAGE])
            current = current[MAX_PASSAGE:]
    if current:
        out.append(current)
    return out


def split_markdown(text: str) -> list[DocPassage]:
    """A markdown document as passages, each titled with its heading path.
    Headings inside a code fence are code, not headings."""
    passages, path, body, fenced = [], [], [], False

    def flush():
        title = " > ".join(p for p in path if p) or "Buddy"
        passages.extend(DocPassage(title, piece) for piece in _pieces("\n".join(body)))
        body.clear()

    for line in text.replace("\r\n", "\n").split("\n"):
        if line.strip().startswith("```"):
            fenced = not fenced
        heading = None if fenced else _HEADING.match(line)
        if heading:
            flush()
            depth = len(heading.group(1))
            del path[depth - 1:]
            path.extend([""] * (depth - 1 - len(path)))
            path.append(heading.group(2).strip("# ").strip())
        else:
            body.append(line)
    flush()
    return passages


class BuddyDocs:
    def __init__(self, registry=None, readme: Path | None = None):
        self.passages: list[DocPassage] = []
        path = readme or readme_path()
        if path is not None:
            try:
                self.passages += split_markdown(path.read_text(encoding="utf-8"))
            except OSError:
                pass
        self.passages += [DocPassage(f"Tool: {tool.name}", tool.detail()) for tool in load_tools(registry)]
        self._bm25 = BM25([tokenize(f"{p.title} {p.title} {p.text}") for p in self.passages])

    def search(self, query: str, limit: int = 4) -> list[DocPassage]:
        return [self.passages[i] for i, _score in self._bm25.search(query, limit=limit)]
