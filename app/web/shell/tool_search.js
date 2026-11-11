/* The tool search - the sidebar's search field (rail.js) and the desktop
   taskbar's Search box (deskmenu.js). The tools come from the shell
   (core/tool_search.py): {id, label, where, about: [what it can do]}.

   Every word typed has to be found - in the tool's name (in English or the
   language Buddy is in), where it is, or what it can do - and name matches
   come first. A tool found by what it does shows the line that matched. */
"use strict";

const ToolSearch = (() => {
    const fold = s => String(s || "").toLowerCase().normalize("NFKD").replace(/[̀-ͯ]/g, "");
    const words = query => fold(query).split(/\s+/).filter(Boolean);
    const startsWord = (text, word) => text.startsWith(word) || text.includes(" " + word) || text.includes("-" + word);

    // Around the first word found, trimmed at the front so it shows in a
    // line or two.
    function snippet(line, found) {
        const lower = line.toLowerCase();
        const at = Math.min(...found.map(w => lower.indexOf(w)).filter(i => i >= 0));
        if (!Number.isFinite(at) || at < 48) return line;
        const from = line.lastIndexOf(" ", at - 24) + 1;
        return "…" + line.slice(from);
    }

    /* [{tool, name, note, fromAbout}], best first: name the tool's name as
       shown (translated), note what to show under it. */
    function match(tools, query) {
        const want = words(query);
        if (!want.length) return [];
        const t = Buddy.t || (s => s);
        const found = [];
        (tools || []).forEach((tool, order) => {
            const name = t(tool.label);
            const names = fold(tool.label + " " + name);
            const where = fold(tool.where + " " + t(tool.where));
            const about = (tool.about || []).map(fold);
            const everything = [names, where, ...about].join(" ");
            if (!want.every(w => everything.includes(w))) return;
            let score = 0;
            const inName = want.filter(w => names.includes(w));
            if (inName.length === want.length) {
                score = 1000 + (fold(name).startsWith(want[0]) || fold(tool.label).startsWith(want[0]) ? 400 : 0)
                      + (want.every(w => startsWord(names, w)) ? 200 : 0);
            } else {
                // What it's for (the summary, first) counts for more than one of
                // the many things it can do.
                score = inName.length * 100 + want.filter(w => where.includes(w)).length * 50
                      + want.filter(w => about.length && about[0].includes(w)).length * 40
                      + want.filter(w => about.some(a => startsWord(a, w))).length * 20;
            }
            let note = tool.where ? t(tool.where) : "", fromAbout = false;
            if (inName.length < want.length && about.length) {
                // The line that has the most of what was typed, first of equals.
                let best = -1, hits = 0;
                about.forEach((line, i) => {
                    const n = want.filter(w => line.includes(w)).length;
                    if (n > hits) { best = i; hits = n; }
                });
                if (best >= 0) {
                    note = snippet(tool.about[best], want.filter(w => about[best].includes(w)));
                    fromAbout = true;
                }
            }
            found.push({tool, name, note, fromAbout, score, order});
        });
        return found.sort((a, b) => b.score - a.score || a.order - b.order);
    }

    /* `text` with each word typed marked, as nodes. */
    function marked(text, query) {
        const want = words(query);
        const lower = text.toLowerCase();
        const spans = [];
        for (const w of want) {
            let at = lower.indexOf(w);
            while (at >= 0) { spans.push([at, at + w.length]); at = lower.indexOf(w, at + w.length); }
        }
        spans.sort((a, b) => a[0] - b[0]);
        const out = [];
        let pos = 0;
        for (const [start, end] of spans) {
            if (start < pos) continue;
            if (start > pos) out.push(text.slice(pos, start));
            out.push(Buddy.el("mark", {text: text.slice(start, end)}));
            pos = end;
        }
        if (pos < text.length) out.push(text.slice(pos));
        return out;
    }

    return {match, marked};
})();
