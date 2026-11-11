#!/usr/bin/env python3
"""
What the tool search finds: the search field at the top of the sidebar,
and the taskbar's Search box in the desktop layout. Both match in the page
(web/shell/tool_search.js); this is the list they search.

One entry per tool that works (no placeholder), in the sidebar's order -
those hidden from the sidebar too, and a tool on another page's tab (Media
Relink on Media Manager) under its own name, which the shell opens on that
tab. Each says where it is (its sidebar heading, or the page it's a tab of)
and what it can do, from core/tools_kb.json, so a search for what a tool
does finds it as well as one for its name.

No Qt import: the classes and the layout are read, nothing is built.
"""

from core import nav_layout, tools_kb


def entries(registry, layout, kb=None):
    """[{id, label, where, about: [text, ...]}] for every tool the search
    can open. registry: [(category, page class)]; layout: the sidebar's
    (core/nav_layout.py), whose dividers give each tool its heading."""
    classes = {cls.tool_id: cls for _category, cls in registry}
    about = {t.tool_id: [t.summary, *t.capabilities]
             for t in (kb if kb is not None else tools_kb.load_tools(registry))}
    out, heading = [], ""
    for entry in layout:
        if entry["type"] == nav_layout.DIVIDER:
            heading = entry.get("label") or ""
            continue
        cls = classes.get(entry.get("id"))
        if cls is None or getattr(cls, "is_placeholder", False):
            continue
        out.append({"id": cls.tool_id, "label": cls.display_name, "where": heading,
                    "about": [a for a in about.get(cls.tool_id, []) if a]})
        for _tab, sub in getattr(cls, "TABS", ()):
            if getattr(sub, "tool_id", None) in getattr(cls, "SUB_TOOLS", ()):
                out.append({"id": sub.tool_id, "label": sub.display_name, "where": cls.display_name,
                            "about": [a for a in about.get(sub.tool_id, []) if a]})
    return out
