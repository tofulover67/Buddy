/* The tour (core/onboarding.py): a see-through page over Buddy's whole
   window. Each step lights up parts of it - Python says where, in this
   page's pixels - with a card beside them; a step with nothing to point at
   is a card in the middle. The dim is one SVG path with a hole cut for
   each part, so two parts with something between them are lit apart. */
"use strict";

const {el, send} = Buddy;
const $ = id => document.getElementById(id);

const PAD = 6;          // the light's margin round what it lights up
const RADIUS = 8;       // the light's corners
const GAP = 16;         // between the light and the card, and from the window's edges
let step = null;

$("skip").onclick = () => send("skip", {never: $("never").checked});
$("back").onclick = () => send("back");
$("next").onclick = () => send(step && step.welcome ? "start" : "next");
document.addEventListener("keydown", e => {
    if (!step) return;
    if (e.key === "Escape") { e.preventDefault(); send("skip", {never: $("never").checked}); }
    else if (step.welcome) return;
    else if (e.key === "ArrowRight") { e.preventDefault(); send("next"); }
    else if (e.key === "ArrowLeft" && !step.first) { e.preventDefault(); send("back"); }
});

function rounded(b) {
    const r = Math.min(RADIUS, (b.right - b.left) / 2, (b.bottom - b.top) / 2);
    return `M${b.left + r} ${b.top}H${b.right - r}A${r} ${r} 0 0 1 ${b.right} ${b.top + r}` +
           `V${b.bottom - r}A${r} ${r} 0 0 1 ${b.right - r} ${b.bottom}H${b.left + r}` +
           `A${r} ${r} 0 0 1 ${b.left} ${b.bottom - r}V${b.top + r}A${r} ${r} 0 0 1 ${b.left + r} ${b.top}Z`;
}

/* Dims the window but for `rects`, outlines each, and gives back the box
   round all of them - or null. */
function light(rects) {
    const boxes = (rects || []).map(r => ({
        left: Math.max(2, r.x - PAD), top: Math.max(2, r.y - PAD),
        right: Math.min(innerWidth - 2, r.x + r.w + PAD), bottom: Math.min(innerHeight - 2, r.y + r.h + PAD),
    })).filter(b => b.right > b.left && b.bottom > b.top);
    $("dim").setAttribute("d", `M0 0H${innerWidth}V${innerHeight}H0Z` + boxes.map(rounded).join(""));
    $("holes").replaceChildren(...boxes.map(b => el("div.tour-hole", {style:
        `left:${b.left}px;top:${b.top}px;width:${b.right - b.left}px;height:${b.bottom - b.top}px`})));
    if (!boxes.length) return null;
    return {left: Math.min(...boxes.map(b => b.left)), top: Math.min(...boxes.map(b => b.top)),
            right: Math.max(...boxes.map(b => b.right)), bottom: Math.max(...boxes.map(b => b.bottom))};
}

/* Beside the light where the card fits - `side` first: "below" for what's
   along the top (the header), "right" for the rail - else in the middle,
   over what's lit (a whole tool's pane). */
function place(box, side) {
    const card = $("card");
    const w = card.offsetWidth, h = card.offsetHeight;
    const clampX = x => Math.max(GAP, Math.min(x, innerWidth - w - GAP));
    const clampY = y => Math.max(GAP, Math.min(y, innerHeight - h - GAP));
    let at = null;
    if (box) {
        const tries = {
            right: () => box.right + GAP + w <= innerWidth - GAP && [box.right + GAP, clampY(box.top)],
            left: () => box.left - GAP - w >= GAP && [box.left - GAP - w, clampY(box.top)],
            below: () => box.bottom + GAP + h <= innerHeight - GAP
                && [clampX((box.left + box.right - w) / 2), box.bottom + GAP],
            above: () => box.top - GAP - h >= GAP && [clampX((box.left + box.right - w) / 2), box.top - GAP - h],
        };
        for (const s of side === "below" ? ["below", "left", "right", "above"] : ["right", "below", "left", "above"]) {
            at = tries[s]();
            if (at) break;
        }
    }
    if (!at) at = [clampX((innerWidth - w) / 2), clampY((innerHeight - h) / 2)];
    card.style.left = `${Math.round(at[0])}px`;
    card.style.top = `${Math.round(at[1])}px`;
}

Buddy.on("step", s => {
    step = s;
    const card = $("card");
    card.hidden = false;
    card.classList.toggle("welcome", s.welcome);
    $("count").textContent = s.welcome ? "" : `Step ${s.number} of ${s.count}`;
    $("count").hidden = s.welcome;
    $("title").textContent = s.title;
    $("text").replaceChildren(...s.text.map(t => el("p", {text: t})));
    $("never-row").hidden = !s.welcome;
    $("skip").textContent = s.welcome ? "Skip" : "Skip tour";
    $("skip").hidden = s.last;
    $("back").hidden = s.welcome || s.first;
    $("next").textContent = s.welcome ? "Take the tour" : s.last ? "Done" : "Next";
    // Laid out with its new text before it's placed.
    requestAnimationFrame(() => {
        place(light(s.rects), s.side);
        $("next").focus();
    });
});

// The window resized: Python sends the step again with the light moved.
// Until then the card keeps inside the window.
addEventListener("resize", () => { if (step) place(light(step.rects), step.side); });
