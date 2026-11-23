"use strict";

// Graphs' view (page.py says what each event carries): the table of rows - typed, or
// pasted from a spreadsheet - and one frame of the graph drawn for the preview, from
// the layout and the growth Python made. ../../web/graphics.js does the other
// controls, the dock, the player and the rest.

(() => {
    const $ = id => document.getElementById(id);
    const el = Buddy.el;
    const TYPING_MS = 250;

    let rows = [];            // [{label, value}] as Python has them
    let maxRows = 12;
    let timer = 0;

    function typed() {
        return [...$("rows").querySelectorAll(".gr-row")].map(row => ({
            label: row.querySelector(".gr-label").value, value: row.querySelector(".gr-value").value,
            was: Number(row.dataset.was)}));          // the row it was: its own timing stays with it
    }

    function sendRows(now) {
        clearTimeout(timer);
        const send = () => Buddy.send("rows", {rows: typed()});
        if (now) send(); else timer = setTimeout(send, TYPING_MS);
    }

    function number(value) {
        return String(+Number(value).toFixed(6));
    }

    function pickColour(node, swatch) {
        const index = Number(node.dataset.was);
        Buddy.pickColor({hex: swatch.dataset.hex || "#FFFFFF", title: "Bar colour", at: swatch,
                         onPick: hex => Buddy.send("bar_colour", {index, value: hex})});
    }

    function rowNode(row, colour, index) {
        const node = el("div.gr-row", {dataset: {was: String(index)}}, [
            // The bar's colour: double-click (or Enter) picks one for this bar alone, with
            // Buddy's colour picker; a right-click gives it back the graph's.
            el("button.gr-swatch", {
                type: "button", style: `background: ${colour}`, dataset: {hex: colour}, "aria-label": "Bar colour",
                title: "Double-click to choose this bar's colour. Right-click to go back to its automatic colour.",
                ondblclick: e => pickColour(node, e.currentTarget),
                onkeydown: e => { if (e.key === "Enter") { e.preventDefault(); pickColour(node, e.currentTarget); } },
                oncontextmenu: e => {
                    e.preventDefault();
                    Buddy.send("bar_colour", {index: Number(node.dataset.was), value: null});
                }}),
            el("input.field.gr-label", {type: "text", value: row.label, maxlength: "40", placeholder: "Name",
                                        "aria-label": "Name", autocomplete: "off"}),
            el("input.field.gr-value", {type: "text", value: number(row.value), inputmode: "decimal",
                                        "aria-label": "Number", autocomplete: "off", spellcheck: "false"}),
            el("button.btn.ghost.icon", {type: "button", title: "Remove this row", "aria-label": "Remove this row",
                                         onclick: () => { node.remove(); sendRows(true); }}, Buddy.icon("trash")),
        ]);
        return node;
    }

    function drawRows(state) {
        const bars = state.preview.layout.bars;
        const nodes = [...$("rows").querySelectorAll(".gr-row")];
        if (nodes.length !== rows.length) {
            $("rows").replaceChildren(...(rows.length ? rows.map((r, i) => rowNode(r, bars[i].colour, i))
                : [el("p.gr-empty", {text: "No rows yet. Add one, or paste them from a spreadsheet."})]));
        } else {
            nodes.forEach((node, i) => {
                node.dataset.was = String(i);
                node.querySelector(".gr-swatch").style.background = bars[i].colour;
                node.querySelector(".gr-swatch").dataset.hex = bars[i].colour;
                const label = node.querySelector(".gr-label"), value = node.querySelector(".gr-value");
                // What's being typed is left alone.
                if (label !== document.activeElement && label.value !== rows[i].label) label.value = rows[i].label;
                if (value !== document.activeElement && value.value !== number(rows[i].value)) value.value = number(rows[i].value);
            });
        }
        $("add-row").disabled = rows.length >= maxRows;
        $("row-count").textContent = `${rows.length} / ${maxRows}`;
    }

    document.addEventListener("graphics-state", e => {
        const state = e.detail;
        rows = state.spec.rows;
        maxRows = state.preview.max_rows;
        drawRows(state);
        document.querySelector("main.dg").dataset.shape = state.spec.shape;
        $("bar-colour").disabled = state.spec.colours !== "one" && state.spec.shape !== "line";
    });

    Graphics.panel("axes-pop", "edit-axes");

    $("rows").addEventListener("input", () => sendRows(false));
    $("rows").addEventListener("change", () => sendRows(true));
    // Several lines or cells pasted into the table are rows, not one cell's text.
    $("rows").addEventListener("paste", e => {
        const text = (e.clipboardData && e.clipboardData.getData("text")) || "";
        if (/[\n\t]/.test(text.trim())) {
            e.preventDefault();
            Buddy.send("paste", {text});
        }
    });
    $("add-row").addEventListener("click", () => {
        clearTimeout(timer);
        Buddy.send("rows", {rows: [...typed(), {label: "", value: "0", was: -1}]});
    });
    $("paste").addEventListener("click", () => {
        const box = el("textarea.field.gr-paste", {spellcheck: "false", "aria-label": "Rows",
                                                   placeholder: "Mon\t12\nTue\t19\nWed\t8"});
        Buddy.modal({
            title: "Paste from a spreadsheet",
            body: [el("p.modal-text", {text: "Paste two columns – a name and a number – or type a row on each line. They take the place of the rows you have."}), box],
            buttons: [{label: "Cancel"},
                      {label: "Use these rows", kind: "accent", onClick: close => {
                          if (box.value.trim()) Buddy.send("paste", {text: box.value});
                          close();
                      }}],
        });
    });

    function bar(ctx, x, y, w, h, radius) {
        if (h <= 0) return;
        const r = Math.min(radius, w / 2, h / 2);
        ctx.beginPath();
        if (r > 0 && ctx.roundRect) ctx.roundRect(x, y, w, h, r); else ctx.rect(x, y, w, h);
        ctx.fill();
    }

    // A line as far as it's drawn on this frame (its share of its length in pixels, as
    // Fusion's WriteLength draws it), the room under it filled up to its end, and each
    // point's dot as big as it has grown.
    function drawLine(ctx, p, plan, frame) {
        const pts = p.points, look = plan.line, f = Math.min(frame, p.drawn.length - 1);
        const want = p.drawn[f];
        if (pts.length > 1 && want > 0) {
            const runs = [0];
            for (let i = 1; i < pts.length; i++) {
                runs.push(runs[i - 1] + Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]));
            }
            const goal = want * runs[runs.length - 1];
            const path = [pts[0]];
            for (let i = 1; i < pts.length; i++) {
                if (runs[i] <= goal + 1e-9) { path.push(pts[i]); continue; }
                const t = runs[i] > runs[i - 1] ? (goal - runs[i - 1]) / (runs[i] - runs[i - 1]) : 0;
                path.push([pts[i - 1][0] + (pts[i][0] - pts[i - 1][0]) * t, pts[i - 1][1] + (pts[i][1] - pts[i - 1][1]) * t]);
                break;
            }
            if (look.fill) {
                const last = pts[pts.length - 1], end = path[path.length - 1][0];
                ctx.save();
                ctx.beginPath();
                ctx.rect(pts[0][0], 0, end - pts[0][0], plan.frame[1]);
                ctx.clip();
                ctx.beginPath();
                pts.forEach((pt, i) => (i ? ctx.lineTo(pt[0], pt[1]) : ctx.moveTo(pt[0], pt[1])));
                ctx.lineTo(last[0], plan.axis.y);
                ctx.lineTo(pts[0][0], plan.axis.y);
                ctx.closePath();
                ctx.fillStyle = look.fill;
                ctx.fill();
                ctx.restore();
            }
            ctx.save();
            ctx.beginPath();
            path.forEach((pt, i) => (i ? ctx.lineTo(pt[0], pt[1]) : ctx.moveTo(pt[0], pt[1])));
            ctx.strokeStyle = look.colour;
            ctx.lineWidth = look.w;
            ctx.lineCap = "round";
            ctx.lineJoin = "round";
            ctx.stroke();
            ctx.restore();
        }
        if (look.dot) {
            // Each dot pops in as the line reaches it: [its share of its size, its opacity].
            pts.forEach((pt, i) => {
                const [size, alpha] = p.dots[i][f];
                if (size <= 0 || alpha <= 0) return;
                ctx.save();
                ctx.globalAlpha = alpha;
                ctx.fillStyle = plan.bars[i].colour;
                ctx.beginPath();
                ctx.arc(pt[0], pt[1], look.dot * size, 0, Math.PI * 2);
                ctx.fill();
                ctx.restore();
            });
        }
    }

    // A pie as far round as it has swept (clockwise from 12 o'clock, as the comp's
    // keyed WritePosition/WriteLength draw it), each slice's name beside it and its
    // number on it, arriving as their parts say.
    function drawPie(ctx, p, plan, frame, state) {
        const pie = plan.pie, f = Math.min(frame, p.drawn.length - 1), swept = p.drawn[f];
        const turn = share => -Math.PI / 2 + share * Math.PI * 2;
        pie.slices.forEach(s => {
            const end = Math.min(s.b, Math.max(s.a, swept));
            if (end <= s.a) return;
            ctx.beginPath();
            ctx.arc(pie.cx, pie.cy, pie.r, turn(s.a), turn(end));
            if (pie.inner > 0) ctx.arc(pie.cx, pie.cy, pie.inner, turn(end), turn(s.a), true);
            else ctx.lineTo(pie.cx, pie.cy);
            ctx.closePath();
            ctx.fillStyle = s.colour;
            ctx.fill();
        });
        const look = state.look, cap = state.cap, size = pie.size;
        pie.slices.forEach(s => {
            const at = (id, x, y) => Graphics.arriving(state, id, frame, size, [x, y - cap * size / 2]);
            if (state.spec.labels && s.label) {
                Graphics.text(ctx, look, s.label, s.label_at[0], s.label_at[1], size, s.align,
                              at(`name:${s.row}`, ...s.label_at));
            }
            if (state.spec.values) {
                Graphics.text(ctx, look, s.shown, s.value_at[0], s.value_at[1], size, "center",
                              at(`value:${s.row}`, ...s.value_at));
            }
        });
    }

    Graphics.start((ctx, frame, state) => {
        const p = state.preview, plan = p.layout, look = state.look, cap = state.cap;
        const at = Math.min(frame, p.build - 1);          // after the build every bar is whole
        // A text where it sits, arriving as its part does: pivot is the middle of its letters.
        const arriving = (id, size, x, baseline) => Graphics.arriving(state, id, frame, size, [x, baseline - cap * size / 2]);
        const line = (y, alpha) => {
            ctx.save();
            ctx.globalAlpha = alpha;
            ctx.fillStyle = look.colour;
            ctx.fillRect(plan.axis.x0, y - plan.axis.h / 2, plan.axis.x1 - plan.axis.x0, plan.axis.h);
            ctx.restore();
        };
        if (!plan.pie) line(plan.axis.y, 0.4);
        if (plan.grid) {
            const fade = Graphics.arriving(state, "grid", frame, 0, [0, 0]);
            plan.marks.slice(1).forEach(m => line(m.y, 0.2 * (fade ? fade.alpha : 1)));
        }
        if (plan.tick) {
            // The numbers up the side arrive together, about the middle of them all.
            const ys = plan.marks.map(m => m.y);
            const moved = Graphics.arriving(state, "scale", frame, plan.tick.size,
                                            [plan.tick.x, (Math.min(...ys) + Math.max(...ys)) / 2]);
            plan.marks.forEach(m => Graphics.text(ctx, look, m.text, plan.tick.x, m.y + plan.tick.drop,
                                                  plan.tick.size, "right", moved));
        }
        if (plan.pie) {
            drawPie(ctx, p, plan, frame, state);
        } else if (plan.line) {
            drawLine(ctx, p, plan, frame);
        } else {
            plan.bars.forEach((b, i) => {
                const h = b.h * p.grown[i][at];
                ctx.fillStyle = b.colour;
                bar(ctx, b.cx - b.w / 2, b.base - h, b.w, h, plan.radius);
            });
        }
        if (plan.label && !plan.pie) {
            plan.bars.forEach((b, i) => {
                if (b.label) {
                    Graphics.text(ctx, look, b.label, b.cx, plan.label.baseline, plan.label.size, "center",
                                  arriving(`name:${i}`, plan.label.size, b.cx, plan.label.baseline));
                }
            });
        }
        if (plan.value && !plan.pie) {
            // A point's number sits where the line leaves room for it (model.value_spots).
            const spots = plan.line ? plan.line.spots : null;
            plan.bars.forEach((b, i) => {
                const [x, baseline] = spots ? spots[i] : [b.cx, b.base - b.h * p.grown[i][at] - plan.value.gap];
                Graphics.text(ctx, look, Graphics.textAt(p.values[i], at), x, baseline, plan.value.size, "center",
                              arriving(`value:${i}`, plan.value.size, x, baseline));
            });
        }
        for (const [id, t] of [["title", plan.title], ["subtitle", plan.subtitle], ["x_title", plan.x_title]]) {
            if (t) Graphics.text(ctx, look, t.text, t.x, t.baseline, t.size, "center", arriving(id, t.size, t.x, t.baseline));
        }
        if (plan.y_title) {
            // Up the side: read bottom to top, its baseline towards the bars.
            const t = plan.y_title;
            ctx.save();
            Graphics.move(ctx, Graphics.arriving(state, "y_title", frame, t.size, [t.x - cap * t.size / 2, t.y]));
            ctx.translate(t.x, t.y);
            ctx.rotate(-Math.PI / 2);
            Graphics.text(ctx, look, t.text, 0, 0, t.size, "center");
            ctx.restore();
        }
    });
})();
