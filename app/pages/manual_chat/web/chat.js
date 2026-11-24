/*
 * Ask Buddy's view. Draws what pages/manual_chat/page.py sends and reports
 * clicks and keystrokes back - no chat state lives here beyond what is on
 * screen. Answer HTML arrives already rendered and escaped by Python
 * (conversation.md_to_html); everything else is set as text.
 */
"use strict";

const $ = id => document.getElementById(id);
const {el, icon, send} = Buddy;

const messages = $("messages");
const transcript = $("transcript");
const input = $("input");
const sendBtn = $("send");
let sending = false;
let canStop = false;        // a question is with the model: the Send button stops it
let replacing = false;      // the composer holds the last question, being edited
let redo = null;            // {q, a}: the blocks Edit and Regenerate go on
let currentChatTitle = "New chat";

$("prev").append(icon("left"));
$("next").append(icon("right"));
$("export").append(icon("download"), "Export");
function drawSendButton() {
    sendBtn.replaceChildren(...(canStop ? [icon("stop"), "Stop"] : [icon("send"), "Send"]));
    sendBtn.title = canStop ? "Stop this answer" : "Send (Enter)";
    sendBtn.classList.toggle("accent", !canStop);
    sendBtn.disabled = canStop ? false : (sending || !canSend());
}
$("attach").append(icon("image"));
$("attach").onclick = () => send("attach_image");

// ------------------------------------------------------------ transcript

// Whether the reader is following the conversation (at the bottom) or has
// scrolled back to read. Following, the newest message stays in view as
// replies land and the cards below the transcript come and go; reading
// back, nothing moves under them.
let following = true;

function nearBottom() {
    return transcript.scrollHeight - transcript.scrollTop - transcript.clientHeight < 60;
}

function scrollToBottom(smooth) {
    transcript.style.scrollBehavior = smooth ? "smooth" : "auto";
    transcript.scrollTop = transcript.scrollHeight;
    following = true;
}

// Only the reader moves the transcript up, so any move up is them reading
// back - by however little, and while an answer is still being written
// under them - and it stops the following there and then. It starts again
// once they are back down at the newest line. A move up that ends at the
// very bottom is the transcript getting shorter (a draft taken back), not
// the reader.
let lastTop = 0;

function readerScrolled() {
    const top = transcript.scrollTop;
    const gap = transcript.scrollHeight - top - transcript.clientHeight;
    if (top < lastTop - 1 && gap > 2) following = false;
    else if (top > lastTop && nearBottom()) following = true;
    lastTop = top;
}

transcript.addEventListener("scroll", readerScrolled, {passive: true});
// The wheel says so before the transcript has moved: an answer growing at
// the same moment must not pull it back down first.
transcript.addEventListener("wheel", event => {
    if (event.deltaY < 0 && transcript.scrollHeight > transcript.clientHeight) following = false;
}, {passive: true});
new ResizeObserver(() => { if (following) scrollToBottom(false); }).observe(transcript);
new ResizeObserver(() => { if (following) scrollToBottom(false); }).observe(messages);

function traceNode(trace) {
    if (!trace.length) return null;
    const label = trace.length === 1 ? "Used 1 tool" : `Used ${trace.length} tools`;
    return el("details.trace", {}, [
        el("summary", {text: label}),
        el("div.chips", {translate: "no"}, trace.map(t => el("span.chip", {text: t}))),
    ]);
}

function copyButton(block) {
    if (!block.copyable) return null;
    const btn = el("button.btn.ghost", {title: "Copy this reply", onclick: () => {
        send("copy", {i: block.i});
        btn.replaceChildren(icon("check"), "Copied");
        setTimeout(() => btn.replaceChildren(icon("copy"), "Copy"), 1500);
    }}, [icon("copy"), "Copy"]);
    return btn;
}

// The manual pages Buddy read for an answer: each opens the manual there.
function sourcesNode(sources) {
    if (!sources || !sources.length) return null;
    return el("details.trace.sources", {}, [
        el("summary", {text: sources.length === 1 ? "1 manual page read" : `${sources.length} manual pages read`}),
        el("div.source-list", {}, sources.map(s => {
            const open = () => send("open_manual", {page: s.page});
            return el("div.source-row", {tabIndex: 0, role: "link", title: `Open the manual at page ${s.page}`,
                                         onclick: open,
                                         onkeydown: e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); } }}, [
                el("div.source-cite", {text: s.cite, translate: "no"}),
                s.snippet ? el("div.muted.small", {text: s.snippet, translate: "no"}) : null,
            ]);
        })),
    ]);
}

// Pages an answer cites that Buddy never handed the model.
function unverifiedNode(pages) {
    if (!pages || !pages.length) return null;
    const text = pages.length === 1
        ? `Page ${pages[0]} is cited here, but it isn't among the manual pages Buddy read for this conversation – check it before relying on it.`
        : `Pages ${pages.join(", ")} are cited here, but they aren't among the manual pages Buddy read for this conversation – check them before relying on them.`;
    return el("div.unverified", {}, [icon("warning"), el("span", {text})]);
}

// Edit on the last question, Regenerate on its answer (Try again when
// there was none). Drawn from "controls", which says which blocks they are.
function drawRedo() {
    messages.querySelectorAll(".redo").forEach(n => n.remove());
    if (!redo || sending) return;
    const row = i => messages.querySelector(`.msg[data-i="${i}"] .actions`);
    const again = (label, title) => el("button.btn.ghost.redo", {title, onclick: () => send("regenerate")},
                                       [icon("refresh"), label]);
    const question = row(redo.q);
    if (question) {
        question.append(el("button.btn.ghost.redo", {title: "Change this question and ask again",
                                                     onclick: () => send("edit_last")}, [icon("undo"), "Edit"]));
        if (redo.a === null) question.append(again("Try again", "Ask this question again"));
    }
    const answer = redo.a === null ? null : row(redo.a);
    if (answer) answer.append(again("Regenerate", "Ask the question again for a new answer"));
}

function picturesNode(block) {
    const images = block.images;
    if (!images || !images.length) return null;
    // Small data: URLs Python made from the pictures sent (pictures.py);
    // a click asks for the larger copy kept beside the chats.
    return el("div.sent-pictures", {}, images.map((src, n) => el("img", {
        src, alt: "Picture sent with the question", title: "Click to see it larger", tabIndex: 0,
        onclick: () => send("open_picture", {i: block.i, n}),
        onkeydown: e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); send("open_picture", {i: block.i, n}); } },
    })));
}

// Like Buddy Network's: the picture as large as the window allows.
Buddy.on("lightbox", l => {
    Buddy.modal({title: "Picture", wide: true, body: el("img.lightbox-img", {src: l.url, alt: "Picture"}), buttons: [
        {label: "Save…", onClick: () => send("save_picture", {i: l.i, n: l.n})},
        {label: "Close", kind: "accent"},
    ]});
});

// A citation opens the manual PDF at its page. The page comes from Python
// (conversation.py's _cite), read off the citation text itself.
function openableCitations(body) {
    body.querySelectorAll(".cite[data-page]").forEach(cite => {
        cite.classList.add("openable");
        cite.tabIndex = 0;
        cite.setAttribute("role", "link");
        cite.title = `Open the manual at page ${cite.dataset.page}`;
        const open = () => send("open_manual", {page: Number(cite.dataset.page)});
        cite.onclick = open;
        cite.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); } };
    });
    return body;
}

function messageNode(block) {
    return el(`article.msg.${block.role}`, {dataset: {i: block.i}}, [
        el("div.who", {text: block.who}),
        el("div.bubble", {}, [
            picturesNode(block),
            // What people asked and what the model answered stay as written;
            // Buddy's own messages (the welcome, help) are translated.
            openableCitations(el("div.body", {html: block.html, translate: block.raw ? "no" : undefined})),
            block.stopped ? el("div.stopped.muted.small", {text: "Stopped"}) : null,
            unverifiedNode(block.unverified),
            sourcesNode(block.sources),
            traceNode(block.trace),
        ]),
        el("div.actions", {}, [copyButton(block)]),
    ]);
}

// The questions under the conversation: a new chat's starters, or the ones
// offered with the last answer (the model's own words: left untranslated).
function drawSuggestions(items, raw) {
    $("suggestions").replaceChildren(...(items || []).map(text =>
        el("button.btn", {text, translate: raw ? "no" : undefined, onclick: () => ask(text)})));
}

Buddy.on("transcript", data => {
    // Another conversation, or this one redrawn: an edit under way was of
    // what stood here before, and must not replace a turn of what is here now.
    setReplacing(false);
    messages.replaceChildren(...data.blocks.map(messageNode));
    drawSuggestions(data.suggestions, data.suggestions_raw);
    drawRedo();
    requestAnimationFrame(() => scrollToBottom(false));
});

Buddy.on("suggestions", data => {
    drawSuggestions(data.items, data.raw);
    if (following) requestAnimationFrame(() => scrollToBottom(true));
});

Buddy.on("append", block => {
    const stick = following || block.role === "user";
    $("suggestions").replaceChildren();
    messages.append(messageNode(block));
    if (stick) requestAnimationFrame(() => scrollToBottom(true));
});

// The dots show until the answer starts arriving; then the answer itself
// does, as it's written ("stream").
let drafting = false;
let thinkingOn = false;

Buddy.on("thinking", data => {
    thinkingOn = data.on;
    $("thinking").hidden = !data.on || drafting;
    $("thinking-text").textContent = data.message || "Thinking…";
    if (data.on && following) requestAnimationFrame(() => scrollToBottom(true));
});

// An answer that has stopped growing but isn't finished - the model is
// writing its follow-up questions, or thinking again - gets the dots back
// under it after a moment, so it never just sits there looking hung.
const QUIET_MS = 1500;
let quietTimer = 0;
let draftHtml = "";

Buddy.on("stream", data => {
    const html = data.html || "";
    drafting = !!html;
    $("draft").hidden = !drafting;
    // The same draft again is not news: text the reader doesn't see (the
    // follow-ups line) is arriving, and the wait for the dots goes on.
    if (html === draftHtml) return;
    clearTimeout(quietTimer);
    draftHtml = html;
    $("draft-body").innerHTML = html;           // Python-rendered and escaped, like every answer
    $("thinking").classList.remove("under-draft");
    $("thinking").hidden = !thinkingOn || drafting;
    if (drafting) {
        quietTimer = setTimeout(() => {
            if (!drafting || !thinkingOn) return;
            $("thinking-text").textContent = "Thinking…";
            $("thinking").classList.add("under-draft");     // part of the answer above it, not a new message
            $("thinking").hidden = false;
            if (following) requestAnimationFrame(() => scrollToBottom(true));
        }, QUIET_MS);
        if (following) requestAnimationFrame(() => scrollToBottom(false));
    }
});

// -------------------------------------------------------------- controls

Buddy.on("controls", c => {
    const wasSending = sending;
    sending = c.sending;
    canStop = !!c.can_stop;
    redo = c.redo || null;
    drawRedo();
    $("prev").disabled = !c.can_prev;
    $("next").disabled = !c.can_next;
    $("checkup").disabled = sending || !c.connected;
    $("explain").disabled = sending || !c.connected;
    $("history").disabled = sending;
    $("rename-chat").disabled = sending;
    $("delete-all-chats").disabled = sending;
    $("switcher").hidden = c.total < 2;
    $("position").textContent = `Chat ${c.index + 1} of ${c.total}`;
    currentChatTitle = c.title || "New chat";
    $("history").title = c.title || "Search saved conversations";
    $("prev").title = c.can_prev ? `Back to conversation ${c.index} of ${c.total}` : "No earlier conversation";
    $("next").title = c.can_next ? `Forward to conversation ${c.index + 2} of ${c.total}` : "No later conversation";

    const newBtn = $("new");
    newBtn.disabled = !c.can_new;
    newBtn.replaceChildren(icon("plus"), "New chat");
    // "of N" makes the cap visible, so history quietly stopping at the
    // limit doesn't look like messages going missing.
    if (c.turns) newBtn.append(el("span.badge", {text: `${c.turns}/${c.limit}`}));
    newBtn.title = c.turns
        ? `Start a fresh conversation. This one has ${c.turns} of ${c.limit} remembered turns.`
        : "Start a fresh conversation";

    drawSendButton();
    if (wasSending && !sending) input.focus();
});

// Edit: the last question comes back to the composer; sending it replaces
// that question and its answer. Cancel leaves the conversation alone.
function setReplacing(on) {
    replacing = on;
    $("editing").hidden = !on;
}
Buddy.on("compose", data => {
    input.value = data.text || "";
    setReplacing(!!data.replace);
    autosize();
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
});
$("cancel-edit").onclick = () => { setReplacing(false); input.value = ""; autosize(); input.focus(); };

Buddy.on("status", s => { $("status").textContent = s.text || ""; });
Buddy.on("toast", t => Buddy.toast(t.text));

$("prev").onclick = () => send("prev_chat");
$("next").onclick = () => send("next_chat");
$("new").onclick = () => { send("new_chat"); input.focus(); };
$("export").onclick = () => send("export");
$("checkup").onclick = () => send("run_checkup");
$("explain").onclick = () => send("explain_clip");
$("history").onclick = () => { $("chat-browser").hidden = !$("chat-browser").hidden; if (!$("chat-browser").hidden) $("chat-search").focus(); };
$("close-browser").onclick = () => { $("chat-browser").hidden = true; };
$("chat-search").oninput = () => send("search_chats", {query: $("chat-search").value});
$("rename-chat").onclick = () => {
    $("rename-row").hidden = false;
    $("chat-title").value = currentChatTitle;
    $("chat-title").focus();
    $("chat-title").select();
};
function saveTitle() {
    const title = $("chat-title").value.trim();
    if (title) send("rename_chat", {title});
    $("rename-row").hidden = true;
}
$("save-title").onclick = saveTitle;
$("cancel-rename").onclick = () => { $("rename-row").hidden = true; };
$("chat-title").onkeydown = e => { if (e.key === "Enter") { e.preventDefault(); saveTitle(); } };
$("delete-all-chats").onclick = async () => {
    if (sending) return;
    if (await Buddy.confirm({title: "Delete all conversations?", ok: "Delete all", danger: true,
                             text: "Every saved conversation is removed from this PC. This can't be undone."})) {
        $("chat-search").value = "";
        send("delete_all_chats");
    }
};
async function deleteChat(item) {
    if (sending) return;
    if (await Buddy.confirm({title: `Delete "${item.title}"?`, ok: "Delete", danger: true,
                             text: "This conversation is removed from this PC. This can't be undone."})) {
        send("delete_chat", {index: item.index});
    }
}
Buddy.on("chat_list", data => {
    $("chat-results").replaceChildren(...data.items.map(item =>
        el("div.chat-row", {}, [
            el(`button.btn.icon.ghost.chat-pin${item.pinned ? ".on" : ""}`,
               {type: "button", title: item.pinned ? "Unpin this conversation" : "Pin this conversation to the top",
                "aria-pressed": item.pinned ? "true" : "false",
                onclick: () => send("pin_chat", {index: item.index, on: !item.pinned})}, [icon("pin")]),
            el("button.chat-result", {type: "button", onclick: () => {
                send("select_chat", {index: item.index});
                $("chat-browser").hidden = true;
            }}, [
                el("span.strong", {}, [
                    el("span", {text: item.title, translate: "no"}),   // what people named or asked
                    item.index === data.current ? el("span.muted", {text: " (current)"}) : null,
                ]),
                el("span.muted.small", {text: item.updated || ""}),
                item.preview ? el("span.small", {text: item.preview, translate: "no"}) : null,
            ]),
            el("button.btn.icon.ghost.chat-delete", {type: "button", title: "Delete this conversation",
                                                    disabled: sending, onclick: () => deleteChat(item)},
               [icon("trash")]),
        ])));
    if (!data.items.length) $("chat-results").append(el("div.muted", {text: "No matching conversations."}));
});

// ----------------------------------------------------------------- offer

Buddy.on("offer", offer => {
    const box = $("offer");
    box.hidden = !offer;
    if (!offer) return box.replaceChildren();
    box.replaceChildren(
        icon("tool"),
        el("div.reason", {}, [el("div.strong", {text: "Buddy suggests a tool"}),
                              el("div.muted.small", {text: offer.reason || "", translate: "no"}),
                              // What the tool will be started with - the model's words, shown before the click.
                              offer.prefill ? el("div.small.prefill", {}, [
                                  el("span.muted", {text: "Starts with:"}), " ",
                                  el("span", {text: offer.prefill, translate: "no"})]) : null]),
        el("button.btn.accent", {text: offer.label, onclick: () => send("open_tool", {tool_id: offer.tool_id})}),
    );
});

// ---------------------------------------------------------------- revert

// The change applied last, while it can still be put back.
Buddy.on("revert", r => {
    const box = $("revert");
    box.hidden = !r;
    if (!r) return box.replaceChildren();
    box.replaceChildren(
        icon("spark"),
        el("div.reason", {}, [el("div.strong", {text: "Applied"}),
                              el("div.muted.small", {text: r.summary, translate: "no"})]),
        el("button.btn", {text: r.busy ? "Reverting…" : "Revert last change", disabled: r.busy,
                          title: "Put back what this change set – only where nothing has changed since",
                          onclick: () => send("revert_change")}),
    );
});

// -------------------------------------------------------------- proposal

Buddy.on("proposal", p => {
    const box = $("proposal");
    box.hidden = !p;
    if (!p) return box.replaceChildren();
    const locked = p.state !== "ready";
    box.replaceChildren(
        el("div.title", {}, [p.destructive ? icon("warning") : icon("spark"), el("span", {text: p.title})]),
        el("div.items", {}, [
            p.reason ? el("div.muted", {text: p.reason, style: "margin-bottom:6px", translate: "no"}) : null,
            ...p.warnings.map(w => el("div.warning", {text: w})),
            ...p.details.map(d => el("div", {text: d, translate: "no"})),
        ]),
        el("div.row", {}, [
            el("div.note.muted.small", {text: p.note}),
            el("button.btn", {text: "Discard", disabled: p.state === "busy",
                              onclick: () => send("discard_proposal")}),
            el("button.btn.danger", {text: p.state === "busy" ? "Applying…" : p.apply_label,
                                     disabled: locked, onclick: () => send("apply_proposal")}),
        ]),
    );
});


// ------------------------------------------------------------- pictures

let pictureCount = 0;
const canSend = () => !!input.value.trim() || pictureCount > 0;

Buddy.on("pictures", p => {
    pictureCount = p.items.length;
    $("attach").hidden = !p.available;
    $("pictures").hidden = !pictureCount;
    $("picture-strip").replaceChildren(...p.items.map((item, index) => el("div.picture", {title: item.label}, [
        el("img", {src: item.preview, alt: "Picture to send"}),
        el("button.remove", {type: "button", title: "Don't send this picture", text: "✕",
                             onclick: () => send("remove_picture", {index})}),
    ])));
    $("picture-note").textContent = p.note || "";
    drawSendButton();
});

// A pasted picture goes through Python, which reads the clipboard itself.
input.addEventListener("paste", e => {
    const items = [...(e.clipboardData ? e.clipboardData.items : [])];
    if (!$("attach").hidden && items.some(i => i.kind === "file" && i.type.startsWith("image/"))) {
        e.preventDefault();
        send("paste_image");
    }
});

// -------------------------------------------------------------- composer

function autosize() {
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 200)}px`;
    input.style.overflowY = input.scrollHeight > 200 ? "auto" : "hidden";
    drawSendButton();
}

function ask(text) {
    text = (text || "").trim();
    if ((!text && !pictureCount) || sending) return;
    send("send", replacing ? {text, replace: true} : {text});
    setReplacing(false);
    input.value = "";
    autosize();
    input.focus();
}

input.addEventListener("input", autosize);
input.addEventListener("keydown", e => {
    // Enter sends, Shift+Enter is a new line. While an answer is on its way
    // the box stays editable, so the next question can be drafted.
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        ask(input.value);
    }
});
sendBtn.onclick = () => (canStop ? send("stop") : ask(input.value));
// Esc stops an answer on its way - or, with nothing to stop, an edit.
document.addEventListener("keydown", e => {
    if (e.key !== "Escape" || document.querySelector(".modal-backdrop")) return;
    if (canStop) send("stop");
    else if (replacing) $("cancel-edit").click();
});

autosize();
input.focus();
