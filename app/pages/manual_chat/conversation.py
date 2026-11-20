#!/usr/bin/env python3
"""
Ask Buddy's conversations as data - what was said, what the model will be
re-sent, and how an answer's markdown becomes HTML. No Qt, so the page's
behaviour (history trimming, New/Previous chat, export, rendering) is
tested on plain Python (tests/test_manual_chat_conversation.py), and the
web view only ever draws what this holds.
"""

from __future__ import annotations

import html
import random
import re
from datetime import datetime

from . import actions

WELCOME = (
    "Ask about DaVinci Resolve and I'll answer from the reference manual, "
    "with chapter and page citations. If Resolve is running I can also read "
    "your open project to answer about your actual settings."
)

# Typed into the chat box, answered locally. Deliberately NOT sent to the
# model: an LLM asked what it can do will cheerfully invent an action that
# does not exist, and this list is built from the registry so it cannot.
HELP_WORDS = {"help", "/help", "?", "/?", "what can you do", "what can you do?"}

# Shown under the welcome of an empty chat; clicking one sends it. Three come from the
# pool, one per group where it can (so they're never all the same kind), and "What can
# you do?" always ends the row. Which three is chosen once per run (shuffled_pool), so a
# new chat doesn't reshuffle them. "project" ones read the open project, so they're only
# offered while Buddy is connected to Resolve.
HELP_SUGGESTION = "What can you do?"   # one of HELP_WORDS: lists what Buddy can do
SUGGESTION_POOL = {
    "resolve": [
        "How do I add a Power Window on the Color page?",
        "What's the difference between Fusion and the Edit page titles?",
        "How do I stabilize a shaky clip?",
        "How do I speed ramp a clip?",
        "How do I make a J-cut or an L-cut?",
        "How do I track a mask onto a moving object?",
        "How does Magic Mask work?",
        "When should I use a serial, parallel or layer node?",
        "How do I copy a grade to other clips?",
        "How do I use adjustment clips?",
        "How do I set up proxies for smoother playback?",
        "What render settings should I use for YouTube?",
        "How do I replace a clip without losing its effects?",
        "How do I reduce background noise in dialogue?",
    ],
    "project": [
        "Check my timeline settings for problems",
        "Do any clips not match my timeline's frame rate?",
        "Am I on DaVinci Resolve Studio or the free version?",
        "Summarise the markers on my timeline",
        "What colour management is my project using?",
        "Is my timeline set up right for a 4K YouTube upload?",
    ],
    "tools": [
        "Which Buddy tool makes subtitles?",
        "How can Buddy help me relink offline media?",
        "Can Buddy rename a batch of clips for me?",
        "How do I animate my Text+ titles?",
        "What's the fastest way to import images into Resolve?",
    ],
}
SUGGESTIONS_PICKED = 3


def shuffled_pool(rng=random) -> dict[str, list[str]]:
    """The pool with each group in its own random order - made once per run."""
    return {group: rng.sample(questions, len(questions)) for group, questions in SUGGESTION_POOL.items()}


def suggestions(pool: dict[str, list[str]], connected: bool) -> list[str]:
    """The suggestion row: SUGGESTIONS_PICKED from the pool, taken round the groups in
    turn (a second from the same group only when a group is left out), then help."""
    groups = [g for g in SUGGESTION_POOL if connected or g != "project"]
    picked = [pool[groups[i % len(groups)]][i // len(groups)] for i in range(SUGGESTIONS_PICKED)]
    return picked + [HELP_SUGGESTION]

YOU, BUDDY, ERROR = "You", "Buddy", "Error"


def is_help(question: str) -> bool:
    return question.lower().strip().strip("!.") in HELP_WORDS


def help_text(writes_on: bool) -> str:
    """What Buddy can do, built from the registries rather than written
    out - a hand-maintained list would start lying the first time an
    action was added."""
    additive, destructive = actions.help_lines()

    lines = [
        "**Ask me about DaVinci Resolve.** I answer from the official "
        "Resolve 21 Reference Manual and cite the chapter and page, so "
        "you can check me.",
        "",
        "**I can read your open project** (Resolve has to be running):",
        "- your project and timeline settings, and whether you are on "
        "Studio or the free version",
        "- what is on your timeline, and any clips whose frame rate or "
        "resolution does not match it",
        "- your timeline markers",
        "- what every other Buddy tool does, and I can offer you one",
        "",
    ]

    if writes_on:
        lines.append(
            "**I can change your project** – this is ON. I never change "
            "anything directly: I show you a card listing exactly what "
            "would happen and you press Apply."
        )
        lines.append("")
        lines.append("Safe to undo, nothing already in place moves:")
        lines.extend(f"- {line}" for line in additive)
        lines.append("- several of these at once, on one card, applied in order")
        lines.append("")
        lines.append(
            "These move or destroy work you did not name, and the card "
            "says so in red:"
        )
        lines.extend(f"- {line}" for line in destructive)
    else:
        lines.append(
            "**I cannot change your project** – this is OFF, which is "
            "the default. Turn it on in Settings if you want it; you "
            "will be asked to type a sentence confirming you understand "
            "the risk. Even then I only ever propose a change and wait "
            "for you to press Apply."
        )

    lines += [
        "",
        "**I can also tell you about Buddy itself** – how each of its tools "
        "works and where it keeps things – from Buddy's own documentation.",
        "",
        "**Under an answer:** Sources lists the manual pages I read for it – "
        "click one to open the manual there – and I say so when I cite a "
        "page I didn't read. Regenerate asks again; Edit, on your last "
        "question, lets you change it and ask again. Stop ends an answer "
        "part-way.",
        "",
        "**Buttons:** New chat starts a fresh conversation; Chats searches "
        "saved conversations and lets you pin, rename or delete them. Check project reviews "
        "the current timeline's video formats. Explain clip reads the "
        "selected timeline clip or clip at the playhead. Export saves the "
        "conversation as Markdown or plain text.",
        "",
        "To see this again any time, type `help`.",
    ]
    return "\n".join(lines)


# ------------------------------------------------------------- markdown --

_CITATION = re.compile(r"\((Chapter \d+[^)]*)\)")
_CITED_PAGE = re.compile(r"\b(?:pp?\.|pages?)\s*(\d+)", re.IGNORECASE)
# The labels the system prompt asks for (agent.py), and "their project" for
# answers saved before the prompt said "your project".
_SOURCE = re.compile(r"\((general knowledge|your project|their project|Buddy docs)\)")
_PAGE_RUN = re.compile(
    r"\b(?:pp?\.|pages?)\s*(\d+(?:\s*(?:[-–—]|,|and|&amp;|&)\s*(?:pp?\.\s*)?\d+)*)", re.IGNORECASE)
_TABLE_RULE = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")
_CELL_SPLIT = re.compile(r"(?<!\\)\|")
# The line the system prompt asks an answer to end with (agent.FOLLOWUPS_RULE).
_FOLLOWUPS = re.compile(r"\n[ \t]*(?:\*\*)?Follow-ups?(?: questions?)?:(?:\*\*)?[ \t]*(.*)\s*$", re.IGNORECASE | re.S)
MAX_FOLLOWUPS = 3
MAX_SOURCES = 8
SNIPPET_CHARS = 220
# Turns that fall off the end of the history wait here until there are this
# many, then one request folds them into the conversation's summary - rather
# than a request per question once a chat is past its limit.
SUMMARY_BATCH = 3
MAX_OVERFLOW_TURNS = SUMMARY_BATCH * 3
MAX_SUMMARY_CHARS = 4000
_MD_LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)")
# Runs on ESCAPED text, so it must stop at an escaped quote or bracket too.
_BARE_URL = re.compile(r"\bhttps?://(?:(?!&quot;|&#x27;|&lt;|&gt;)[^\s<>\"')\]])+")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"(?<![*\w])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![*\w])")
_BULLET = re.compile(r"^\s*[-*•]\s+(.*)$")
_NUMBERED = re.compile(r"^\s*(\d+)[.)]\s+(.*)$")
_HEADING = re.compile(r"^\s*(#{1,4})\s+(.*)$")


def _cite(m):
    """A manual citation, carrying its page for the view to open the PDF
    at (the first page of a range, or of several citations in one)."""
    page = _CITED_PAGE.search(m.group(1))
    data = f' data-page="{page.group(1)}"' if page else ""
    return f'<span class="cite"{data}>({m.group(1)})</span>'


def _bare_link(keep):
    def link(m):
        # A sentence's full stop is not part of the address.
        url = m.group(0)
        tail = len(url) - len(url.rstrip(".,;:!?"))
        url, rest = url[:len(url) - tail], url[len(url) - tail:]
        return keep(f'<a href="{url}">{url}</a>') + rest
    return link


def _inline(text: str) -> str:
    """One line of prose -> HTML. Escaped FIRST, so nothing the model writes
    can become markup; the only tags that come out are the ones added here,
    and the only links are http(s)."""
    parts = re.split(r"(`[^`\n]+`)", text)
    out = []
    for part in parts:
        if len(part) > 1 and part.startswith("`") and part.endswith("`"):
            out.append(f"<code>{html.escape(part[1:-1])}</code>")
            continue
        s = html.escape(part, quote=True)
        links = []

        def keep(markup):
            links.append(markup)
            return f"\x00{len(links) - 1}\x00"

        s = _MD_LINK.sub(lambda m: keep(f'<a href="{m.group(2)}">{m.group(1)}</a>'), s)
        s = _BARE_URL.sub(_bare_link(keep), s)
        s = _BOLD.sub(r"<b>\1</b>", s)
        s = _ITALIC.sub(r"<i>\1</i>", s)
        s = _CITATION.sub(_cite, s)
        s = _SOURCE.sub(r'<span class="source">(\1)</span>', s)
        s = re.sub(r"\x00(\d+)\x00", lambda m: links[int(m.group(1))], s)
        out.append(s)
    return "".join(out)


def _cells(line: str) -> list[str]:
    """One table row's cells. A pipe written \\| is part of its cell."""
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|") and not line.endswith("\\|"):
        line = line[:-1]
    return [cell.strip().replace("\\|", "|") for cell in _CELL_SPLIT.split(line)]


def _table(header: str, rows: list[str]) -> str:
    """A pipe table. Short rows are padded and long ones cut to the header's
    width, so a model's miscounted row can't push the columns out of line."""
    head = _cells(header)
    out = ['<div class="table-wrap"><table><thead><tr>']
    out += [f"<th>{_inline(cell)}</th>" for cell in head]
    out.append("</tr></thead><tbody>")
    for row in rows:
        cells = (_cells(row) + [""] * len(head))[:len(head)]
        out.append("<tr>" + "".join(f"<td>{_inline(cell)}</td>" for cell in cells) + "</tr>")
    out.append("</tbody></table></div>")
    return "".join(out)


def _indent(line: str) -> int:
    lead = line[:len(line) - len(line.lstrip())]
    return len(lead.replace("\t", "    "))


def md_to_html(text: str) -> str:
    """Just enough markdown for what the system prompt asks the model to
    produce: paragraphs, bullet and numbered lists (nested by indentation),
    tables, small headings, code blocks, bold/italic, inline code, links,
    and the manual's citations."""
    lines = text.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    para: list[str] = []
    lists: list[tuple[int, str]] = []       # the open lists, outermost first: (indent, "ul"/"ol")
    code: list[str] | None = None
    skip = 0                                # table lines already drawn

    def flush_para():
        if para:
            out.append("<p>" + "<br>".join(_inline(p) for p in para) + "</p>")
            para.clear()

    def close_list(to: int = 0):
        while len(lists) > to:
            out.append(f"</li></{lists.pop()[1]}>")

    def list_item(indent, kind, number, body):
        # Deeper than the open list by two columns or more: a list inside
        # its last item. Shallower: back out to the list at that depth.
        while lists and indent < lists[-1][0] - 1 and len(lists) > 1:
            close_list(len(lists) - 1)
        if lists and indent > lists[-1][0] + 1:
            pass                                    # nests in the open item
        elif lists and lists[-1][1] == kind:
            out.append("</li>")
            out.append(f"<li>{_inline(body)}")
            return
        elif lists:
            close_list(len(lists) - 1)              # the other kind at this depth: a new list
        start = f' start="{int(number)}"' if kind == "ol" and number not in (None, "1") else ""
        out.append(f"<{kind}{start}>")
        lists.append((indent, kind))
        out.append(f"<li>{_inline(body)}")

    for n, line in enumerate(lines):
        if skip:
            skip -= 1
            continue
        if code is not None:
            if line.strip().startswith("```"):
                out.append("<pre><code>" + html.escape("\n".join(code)) + "</code></pre>")
                code = None
            else:
                code.append(line)
            continue
        if line.strip().startswith("```"):
            flush_para()
            close_list()
            code = []
            continue
        if not line.strip():
            flush_para()
            close_list()
            continue
        if "|" in line and n + 1 < len(lines) and "-" in lines[n + 1] and _TABLE_RULE.match(lines[n + 1]) \
                and ("|" in lines[n + 1] or len(_cells(line)) == 1):
            rows = []
            for row in lines[n + 2:]:
                if not row.strip() or "|" not in row:
                    break
                rows.append(row)
            flush_para()
            close_list()
            out.append(_table(line, rows))
            skip = 1 + len(rows)
            continue
        heading = _HEADING.match(line)
        bullet = _BULLET.match(line)
        numbered = _NUMBERED.match(line)
        if heading:
            flush_para()
            close_list()
            out.append(f"<h4>{_inline(heading.group(2))}</h4>")
        elif bullet or numbered:
            flush_para()
            if bullet:
                list_item(_indent(line), "ul", None, bullet.group(1))
            else:
                list_item(_indent(line), "ol", numbered.group(1), numbered.group(2))
        else:
            close_list()
            para.append(line)
    if code is not None:   # an unclosed fence still shows its contents
        out.append("<pre><code>" + html.escape("\n".join(code)) + "</code></pre>")
    flush_para()
    close_list()
    return "".join(out)


def md_to_plain(text: str) -> str:
    """Minimal markdown cleanup for exported text - enough to keep the
    transcript readable in a .txt file without turning it into markdown
    soup. Citations are dropped; bold markers go; nothing else matters."""
    text = _CITATION.sub("", text)
    text = _SOURCE.sub("", text)
    return _BOLD.sub(r"\1", text).strip()


# ------------------------------------------------- what an answer cites --

def split_followups(answer: str) -> tuple[str, list[str]]:
    """(the answer without its closing "Follow-ups:" line, the questions on
    it). The system prompt asks for that line; the page shows the questions
    as buttons instead of as text."""
    m = _FOLLOWUPS.search(answer)
    if not m:
        return answer, []
    found = []
    for part in re.split(r"\s*\|\s*|\n+", m.group(1)):
        part = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", part).strip().strip('"“”')
        if 3 < len(part) <= 140 and part not in found:
            found.append(part)
    if not found:
        return answer, []
    return answer[:m.start()].rstrip(), found[:MAX_FOLLOWUPS]


def hide_followups(draft: str) -> str:
    """An answer still being written, without the follow-ups line it may
    have reached - so the line never shows and then disappears."""
    m = re.search(r"\n[ \t]*(?:\*\*)?Follow-ups?\b.*\Z", draft, re.IGNORECASE | re.S)
    return draft[:m.start()].rstrip() if m else draft


def cited_pages(answer: str) -> list[tuple[int, int]]:
    """Every page an answer's manual citations name, as (first, last) -
    "p. 12" is (12, 12), "pp. 12–14" is (12, 14)."""
    found = []
    for cite in _CITATION.finditer(answer):
        for run in _PAGE_RUN.finditer(cite.group(1)):
            for part in re.split(r"\s*(?:,|and|&amp;|&)\s*", run.group(1)):
                numbers = [int(n) for n in re.findall(r"\d+", part)]
                if numbers:
                    span = (numbers[0], max(numbers[0], numbers[-1]))
                    if span not in found:
                        found.append(span)
    return found


def unverified_pages(answer: str, pages_read) -> list[str]:
    """The cited pages Buddy never handed the model - in this answer or
    earlier in the conversation - so the citation can't have come from the
    manual's text. A range passes when any page in it was read."""
    read = {int(p) for p in pages_read if isinstance(p, int)}
    out = []
    for first, last in cited_pages(answer):
        if not any(first <= page <= last for page in read):
            out.append(str(first) if first == last else f"{first}–{last}")
    return out


def sources_from(passages) -> list[dict]:
    """What the manual search returned for an answer, one entry per page,
    in the order found: the citation, its page and the start of the text."""
    out, seen = [], set()
    for p in passages or []:
        key = (getattr(p, "chapter_no", None), getattr(p, "page", None))
        if key in seen or getattr(p, "page", None) is None:
            continue
        seen.add(key)
        text = " ".join((getattr(p, "text", "") or "").split())
        snippet = text[:SNIPPET_CHARS].rstrip() + ("…" if len(text) > SNIPPET_CHARS else "")
        out.append({"cite": p.citation(), "page": int(p.page), "snippet": snippet})
    return out


def _clean_sources(value) -> list[dict]:
    out = []
    for item in value if isinstance(value, list) else []:
        if (isinstance(item, dict) and isinstance(item.get("cite"), str) and isinstance(item.get("page"), int)
                and not isinstance(item.get("page"), bool) and isinstance(item.get("snippet", ""), str)):
            out.append({"cite": item["cite"][:200], "page": item["page"],
                        "snippet": item.get("snippet", "")[:SNIPPET_CHARS + 1]})
    return out


def _strings(value, limit, length) -> list[str]:
    return [v[:length] for v in (value if isinstance(value, list) else []) if isinstance(v, str) and v][:limit]


# -------------------------------------------------------- conversations --

class _Chat:
    def __init__(self):
        self.title = "New chat"
        self.updated = datetime.now().isoformat(timespec="seconds")
        # What the model is re-sent: prose only, user/assistant pairs.
        self.history: list[dict] = []
        # What is on screen, oldest first. The only source the view is
        # drawn from, and what Export writes.
        self.blocks: list[dict] = []
        self.offer: dict | None = None
        self.pinned = False                 # kept at the top of Chats
        # The turns older than the history, in a few sentences the model is
        # given with each question; and the ones that have left the history
        # but aren't in the summary yet (SUMMARY_BATCH).
        self.summary = ""
        self.overflow: list[dict] = []


class ChatSessions:
    """Every saved conversation, oldest first. The live one is `current`;
    switching never discards anything - only delete() and delete_all() do."""

    def __init__(self):
        self.chats = [_Chat()]
        self.index = 0
        self.add(BUDDY, WELCOME)

    @property
    def current(self) -> _Chat:
        return self.chats[self.index]

    @property
    def blocks(self) -> list[dict]:
        return self.current.blocks

    @property
    def history(self) -> list[dict]:
        return self.current.history

    @property
    def turns(self) -> int:
        """One turn is a question plus its answer."""
        return len(self.current.history) // 2

    def add(self, who, body, trace=None, error=False, copyable=False, images=None, raw=False, views=None,
            sources=None, unverified=None, followups=None, asked=False, answer=False, stopped=False,
            own_followups=False) -> dict:
        block = {
            "who": who,
            "body": body,
            "trace": list(trace or []),
            "error": bool(error),
            "copyable": bool(copyable),
            "raw": bool(raw),   # someone's own words (a question, the model's answer): never translated
            "images": list(images or []),   # small data: URLs of the pictures sent with it
            "views": list(views or []),     # their larger copies' file names (chat_store.picture_path)
            "sources": list(sources or []),         # sources_from: the manual pages read for it
            "unverified": list(unverified or []),   # unverified_pages: cited, but never read
            "followups": list(followups or []),     # questions offered under it
            "own_followups": bool(own_followups),   # Buddy's own wording (translated), not the model's
            "asked": bool(asked),       # a question that went to the model
            "answer": bool(answer),     # the model's answer to one
            "stopped": bool(stopped),   # an answer the user stopped part-way
        }
        self.current.blocks.append(block)
        if who == YOU and self.current.title == "New chat":
            self.current.title = " ".join(body.split())[:72] or "Picture question"
        self.current.updated = datetime.now().isoformat(timespec="seconds")
        return block

    def summaries(self, query="") -> list[dict]:
        """Search full transcripts, while sending only short previews to the view."""
        needle = query.strip().casefold()
        found = []
        for index, chat in enumerate(self.chats):
            match = next((b["body"] for b in chat.blocks
                          if needle and needle in b["body"].casefold()), "")
            if needle and needle not in chat.title.casefold() and not match:
                continue
            found.append({"index": index, "title": chat.title, "updated": chat.updated, "pinned": chat.pinned,
                          "preview": " ".join(match.split())[:130] if match else ""})
        found.reverse()
        return [f for f in found if f["pinned"]] + [f for f in found if not f["pinned"]]

    def pin(self, index, on) -> bool:
        if not isinstance(index, int) or not 0 <= index < len(self.chats):
            return False
        self.chats[index].pinned = bool(on)
        return True

    # ------------------------------------------------------ the last turn

    def last_turn(self) -> tuple[int, int | None] | None:
        """(the last question's block, its answer's block or None) when the
        conversation ends on a question that went to the model - the turn
        Regenerate and Edit act on. None after anything else (help, a
        project check-up, an applied change)."""
        blocks = self.current.blocks
        q = next((i for i in range(len(blocks) - 1, -1, -1) if blocks[i]["who"] == YOU), None)
        if q is None or not blocks[q].get("asked"):
            return None
        after = blocks[q + 1:]
        if any(not (b.get("answer") or b["error"]) for b in after):
            return None                     # something else has happened since
        a = next((q + 1 + i for i, b in enumerate(after) if b.get("answer")), None)
        return q, a

    def drop_last_turn(self) -> dict | None:
        """Take the last turn off the conversation - its question, its
        answer and their place in the history - and hand back the question's
        block, for it to be asked again."""
        turn = self.last_turn()
        if turn is None:
            return None
        q, a = turn
        chat = self.current
        question = chat.blocks[q]
        if (a is not None and not chat.blocks[a].get("stopped") and len(chat.history) >= 2
                and chat.history[-1]["role"] == "assistant"
                and chat.history[-1]["content"] == chat.blocks[a]["body"]):
            del chat.history[-2:]
        del chat.blocks[q:]
        return question

    def rename(self, index, title):
        title = " ".join(str(title).split())[:100]
        if not title or not 0 <= index < len(self.chats):
            return False
        self.chats[index].title = title
        self.chats[index].updated = datetime.now().isoformat(timespec="seconds")
        return True

    def delete(self, index) -> bool:
        """Remove one conversation for good. The live one stays live unless
        it's the one deleted - then the next one down takes its place (the
        one before, when it was the last). Deleting the only conversation
        leaves a fresh empty one, never none. False when there's no such
        conversation."""
        if not isinstance(index, int) or not 0 <= index < len(self.chats):
            return False
        if len(self.chats) == 1:
            self.delete_all()
            return True
        del self.chats[index]
        if index < self.index or self.index >= len(self.chats):
            self.index -= 1
        return True

    def delete_all(self):
        """Every conversation gone; a fresh empty one to start from."""
        self.chats = [_Chat()]
        self.index = 0
        self.add(BUDDY, WELCOME)

    def to_data(self) -> dict:
        """Save only the small preview images, never full model uploads."""
        return {"version": 1, "index": self.index, "chats": [
            {"title": c.title, "updated": c.updated, "history": c.history,
             "blocks": c.blocks, "offer": c.offer, "pinned": c.pinned,
             "summary": c.summary, "overflow": c.overflow}
            for c in self.chats]}

    @classmethod
    def from_data(cls, data):
        if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("chats"), list):
            raise ValueError("Unsupported conversation file")
        instance = cls()
        restored = []
        for entry in data["chats"]:
            if not isinstance(entry, dict) or not isinstance(entry.get("blocks"), list) or not isinstance(entry.get("history"), list):
                raise ValueError("Invalid conversation")
            chat = _Chat()
            chat.title = str(entry.get("title") or "New chat")[:100]
            chat.updated = str(entry.get("updated") or "")[:40]
            offer = entry.get("offer")
            if (isinstance(offer, dict) and isinstance(offer.get("tool_id"), str)
                    and offer["tool_id"] and isinstance(offer.get("label"), str)
                    and isinstance(offer.get("reason", ""), str)):
                chat.offer = {"tool_id": offer["tool_id"], "label": offer["label"],
                              "reason": offer.get("reason", "")}
                if isinstance(offer.get("prefill"), str) and offer["prefill"]:
                    chat.offer["prefill"] = offer["prefill"][:300]    # what the tool starts with (agent.PREFILL_TOOLS)
            def messages(value):
                return [{"role": m["role"], "content": m["content"]} for m in (value if isinstance(value, list) else [])
                        if isinstance(m, dict) and m.get("role") in ("user", "assistant")
                        and isinstance(m.get("content"), str)]
            chat.history = messages(entry["history"])
            chat.pinned = entry.get("pinned") is True
            chat.summary = entry["summary"][:MAX_SUMMARY_CHARS] if isinstance(entry.get("summary"), str) else ""
            chat.overflow = messages(entry.get("overflow"))
            if len(chat.overflow) % 2:
                chat.overflow = []          # half a turn: not something to hand the model
            chat.blocks = []
            for block in entry["blocks"]:
                if not isinstance(block, dict) or not isinstance(block.get("body"), str):
                    raise ValueError("Invalid message")
                chat.blocks.append({"who": str(block.get("who") or BUDDY), "body": block["body"],
                                    "trace": list(block.get("trace") or []), "error": bool(block.get("error")),
                                    "copyable": bool(block.get("copyable")), "raw": bool(block.get("raw")),
                                    "images": [v for v in (block.get("images") or [])
                                               if isinstance(v, str) and v.startswith("data:image/jpeg;base64,")
                                               and len(v) < 100_000][:4],
                                    "views": [v for v in (block.get("views") or [])
                                              if isinstance(v, str) and re.match(r"^[0-9a-f]{20}\.jpg$", v)][:4],
                                    "sources": _clean_sources(block.get("sources"))[:MAX_SOURCES * 2],
                                    "unverified": _strings(block.get("unverified"), 20, 24),
                                    "followups": _strings(block.get("followups"), MAX_FOLLOWUPS + 3, 200),
                                    "own_followups": block.get("own_followups") is True,
                                    "asked": block.get("asked") is True,
                                    "answer": block.get("answer") is True,
                                    "stopped": block.get("stopped") is True})
            restored.append(chat)
        if restored:
            instance.chats = restored
            instance.index = min(max(0, int(data.get("index", len(restored)-1))), len(restored)-1)
        return instance

    def record_turn(self, question, answer, limit, pictures=0):
        """Only the prose goes into history - replaying tool traffic would
        grow the context every turn for no benefit. Pictures aren't sent
        again either (every later turn would pay for them): the question
        just says there were some, and the answer says what they showed."""
        if pictures:
            note = "a picture" if pictures == 1 else f"{pictures} pictures"
            question = f"{question}\n\n[I attached {note} with this question.]"
        self.current.history.append({"role": "user", "content": question})
        self.current.history.append({"role": "assistant", "content": answer})
        self.trim(limit)

    def trim(self, limit):
        """Keep only the most recent `limit` turns. Oldest first out, and
        always in whole pairs - half a turn (a question with no answer, or
        an answer with no question) reads as a non-sequitur to the model.

        What leaves waits in `overflow` to be folded into the summary
        (to_fold); a limit of 0 means every question stands alone, so
        nothing is kept at all."""
        keep = max(0, int(limit)) * 2
        chat = self.current
        if keep == 0:
            chat.history.clear()
            chat.overflow.clear()
            chat.summary = ""
        elif len(chat.history) > keep:
            chat.overflow.extend(chat.history[:-keep])
            del chat.history[:-keep]
            # A summary that keeps failing (the model's server down) mustn't
            # let this grow for ever: the oldest go unsummarised.
            del chat.overflow[:-MAX_OVERFLOW_TURNS * 2]

    def history_for_model(self) -> list[dict]:
        """What goes with a question: the turns waiting to be summarised,
        then the history proper. The summary itself is separate."""
        return list(self.current.overflow) + list(self.current.history)

    def to_fold(self) -> int:
        """How many of history_for_model's first messages should be folded
        into the summary by the next request - 0 until enough have gathered."""
        waiting = len(self.current.overflow)
        return waiting if waiting >= SUMMARY_BATCH * 2 else 0

    def folded(self, summary: str, count: int):
        """The model's new summary, covering `count` overflow messages."""
        chat = self.current
        chat.summary = " ".join(str(summary).split())[:MAX_SUMMARY_CHARS]
        del chat.overflow[:count]

    def is_empty(self) -> bool:
        """Nothing but the welcome - no question asked yet."""
        return not self.current.history and len(self.current.blocks) <= 1

    def new_chat(self) -> bool:
        """Start a fresh conversation, keeping this one to go back to.
        Returns False (and does nothing) when already on an empty last
        chat: pressing New chat twice must not bury the real one two
        steps back."""
        if self.is_empty() and self.index == len(self.chats) - 1:
            return False
        self.chats.append(_Chat())
        self.index = len(self.chats) - 1
        self.add(BUDDY, WELCOME)
        return True

    def start_fresh(self):
        """Buddy starting: a new, empty conversation in front, every saved
        one still in the list to go back to. Ones left with nothing asked
        in them are dropped first, so a launch never piles up empty chats."""
        self.chats = [c for c in self.chats if c.history or len(c.blocks) > 1]
        self.chats.append(_Chat())
        self.index = len(self.chats) - 1
        self.add(BUDDY, WELCOME)

    def go(self, index) -> bool:
        if not (0 <= index < len(self.chats)) or index == self.index:
            return False
        self.index = index
        return True

    def export_text(self) -> str:
        lines = ["Buddy conversation", "=" * 18, ""]
        for block in self.current.blocks:
            lines.append(f"{block['who']}:")
            for paragraph in block["body"].split("\n"):
                lines.append("  " + md_to_plain(paragraph))
            if block.get("images"):
                lines.append(f"  [{len(block['images'])} picture(s)]")
            if block["trace"]:
                lines.append(f"  [{md_to_plain(' · '.join(block['trace']))}]")
            lines.append("")
        return "\n".join(lines)

    def export_markdown(self) -> str:
        """The conversation as Markdown: answers as the model wrote them
        (citations and all), each with the manual pages it was given."""
        chat = self.current
        lines = [f"# {chat.title if chat.title != 'New chat' else 'Buddy conversation'}", ""]
        for block in chat.blocks:
            lines += [f"## {block['who']}", "", block["body"].strip(), ""]
            if block.get("images"):
                lines += [f"*{len(block['images'])} picture(s) attached.*", ""]
            if block.get("sources"):
                lines.append("**Sources**")
                lines += [f"- {s['cite']}" for s in block["sources"]]
                lines.append("")
            if block.get("unverified"):
                lines += ["*Cited, but not among the pages read for this answer: p. "
                          + ", ".join(block["unverified"]) + ".*", ""]
            if block["trace"]:
                lines += ["*" + " · ".join(block["trace"]) + "*", ""]
        return "\n".join(lines).rstrip() + "\n"


def block_view(index: int, block: dict) -> dict:
    """One block as the web view draws it. HTML is rendered here, in
    Python, from escaped text - the page inserts it as-is."""
    return {
        "i": index,
        "who": block["who"],
        "role": "error" if block["error"] else ("user" if block["who"] == YOU else "buddy"),
        "html": md_to_html(block["body"]),
        "trace": block["trace"],
        "copyable": block["copyable"],
        "raw": block.get("raw", False),
        "images": block.get("images") or [],
        "sources": block.get("sources") or [],
        "unverified": block.get("unverified") or [],
        "stopped": bool(block.get("stopped")),
    }


def followups_view(block: dict | None) -> dict:
    """The questions offered under the conversation's last message."""
    if not block or not block.get("followups"):
        return {"items": [], "raw": False}
    return {"items": list(block["followups"]), "raw": not block.get("own_followups")}
