"use strict";

// Counters' view (page.py says what each event carries): the ways of writing it to
// start from, and one frame of the counter drawn for the preview - the text Python
// keyed for that frame, where and how the Text+ will draw it, arriving as it will.
// ../../web/graphics.js does the controls, the dock, the player and the rest.

(() => {
    const presets = document.getElementById("presets");
    let listed = "";

    document.addEventListener("graphics-state", e => {
        const names = e.detail.preview.presets || [];
        if (names.join("|") === listed) return;
        listed = names.join("|");
        presets.replaceChildren(...names.map(name => Buddy.el("button.btn", {
            type: "button", text: name, onclick: () => Buddy.send("preset", {name})})));
    });

    Graphics.start((ctx, frame, state) => {
        const p = state.preview, at = p.place;
        const moved = Graphics.arriving(state, "arrive", frame, at.size, [at.x, at.baseline - state.cap * at.size / 2]);
        Graphics.text(ctx, state.look, Graphics.textAt(p.keys, frame), at.x, at.baseline, at.size, p.align, moved);
    });
})();
