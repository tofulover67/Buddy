/* The pet's window (pet_window.py): draws the pet Python describes ("pet"),
   shows what it says ("bubble"), and reports what the person did - a click
   (a pat), a press that moves (it's picked up: "grab", then "drop" when
   let go - Python moves the window and throws it), a right-click (a menu),
   and a question typed into the bubble ("ask"). Before the bubble shows,
   Python is told to make room ("bubble" {open}); after, to take it back.
   While it's held or in the air Python sends how it swings and stretches
   ("pose") and when it hits a floor ("land"). */
"use strict";

const {send} = Buddy;
const $ = id => document.getElementById(id);

const main = document.querySelector(".pet-main");
const stage = $("stage");
const pose = $("pose");
const squash = $("squash");
const sprite = $("sprite");
const bubble = $("bubble");
const bubbleText = $("bubble-text");
const askForm = $("ask-form");
const askInput = $("ask-input");

let pet = {species: "cat", name: "", size: "medium", px: 128, state: "idle", facing: 1, ask: true};
let hideTimer = 0;

function size(px, below) {
    document.documentElement.style.setProperty("--pet-h", `${px}px`);
    document.documentElement.style.setProperty("--pet-w", `${Math.round(px * 1.3)}px`);
    document.documentElement.style.setProperty("--pet-below", `${below || 0}px`);      // room to swing into
}

Buddy.on("pet", p => {
    const was = pet.state;
    pet = {...pet, ...p};
    Sprites.render(sprite, pet.species, pet.skin || "classic", pet.colour);
    sprite.dataset.state = pet.state;
    sprite.dataset.facing = String(pet.facing);
    size(pet.px, pet.below);
    stage.title = pet.physics === false
        ? (pet.name ? `${pet.name} – click to pat, right-click for more, drag to move`
                    : "Click to pat, right-click for more, drag to move")
        : (pet.name ? `${pet.name} – click to pat, right-click for more, drag to pick up and let go to throw`
                    : "Click to pat, right-click for more, drag to pick up and let go to throw");
    // Python let go of it without us (the button came up somewhere we never heard of).
    if (was === "held" && pet.state !== "held" && press && press.grabbing) endPress();
});

/* ------------------------------------------------------- room above -- */

/* The bubble and the hover pill both sit above the pet: Python makes room
   while either is up (the window grows upwards) and takes it back after.
   The bubble is this page's to open, so Python is told; the pill is
   Python's call (it watches where the mouse is - "hover"), so it already knows. */
let roomFor = new Set();
function room(what, want) {
    const hadBubble = roomFor.has("bubble");
    if (want) roomFor.add(what); else roomFor.delete(what);
    const hasBubble = roomFor.has("bubble");
    if (hasBubble !== hadBubble) send("bubble", {open: hasBubble});
    main.classList.toggle("bubble-open", roomFor.size > 0);
}

/* At a screen's edge the window's see-through side hangs off it ("room"
   {shift}: how far, + off the left, - off the right): the bubble and the
   pill slide back in as far as they need to, the bubble's tail still
   pointing at the pet. */
let shift = 0;
function fitRoom() {
    for (const node of [bubble, pill]) {
        if (node.hidden) continue;
        const spare = Math.max(0, (main.clientWidth - node.offsetWidth) / 2 - 4);
        const by = Math.sign(shift) * Math.max(0, Math.abs(shift) - spare);
        node.style.setProperty("--shift", `${Math.round(by)}px`);
    }
}
Buddy.on("room", r => { shift = (r && r.shift) || 0; fitRoom(); });

/* ------------------------------------------------------------ bubble -- */

function openBubble(kind, text) {
    clearTimeout(hideTimer);
    hidePill();
    room("bubble", true);
    bubbleText.textContent = text || "";
    bubbleText.classList.toggle("cheer", kind === "cheer");
    askForm.hidden = kind !== "ask";
    bubble.hidden = false;
    fitRoom();
    if (kind === "ask") {
        askInput.value = "";
        setTimeout(() => askInput.focus(), 60);
    } else {
        hideTimer = setTimeout(closeBubble, kind === "cheer" ? 6000 : 3500);
    }
}

function closeBubble() {
    clearTimeout(hideTimer);
    bubble.hidden = true;
    askForm.hidden = true;
    room("bubble", false);
}

/* -------------------------------------------------------- hover pill -- */

const pill = $("pill");

/* Shown and hidden as Python says ("hover"): it watches the mouse against
   the pet on screen, so the window growing for the pill can't end the
   hover that asked for it. */
function showPill() {
    if (!bubble.hidden) return;
    room("pill", true);
    pill.hidden = false;
    fitRoom();
    stage.classList.add("hover");
}

function hidePill() {
    pill.hidden = true;
    stage.classList.remove("hover");
    room("pill", false);
}

Buddy.on("hover", h => (h && h.on ? showPill() : hidePill()));
stage.addEventListener("animationend", () => stage.classList.remove("hover"));
$("pill-ask").addEventListener("click", () => openBubble("ask", ""));
$("pill-open").addEventListener("click", () => { hidePill(); send("open"); });

Buddy.on("bubble", b => {
    if (!b) return closeBubble();
    if (b.kind === "ask") openBubble("ask", "");
    else if (b.kind === "cheer") openBubble("cheer", `Achievement unlocked: ${b.title}`);
    else openBubble("say", b.text || "");
});

askForm.addEventListener("submit", e => {
    e.preventDefault();
    const text = askInput.value.trim();
    if (!text) return closeBubble();
    send("ask", {text});
    closeBubble();
});
askInput.addEventListener("keydown", e => {
    if (e.key === "Escape") closeBubble();
});
document.addEventListener("keydown", e => {
    if (e.key === "Escape" && !bubble.hidden) closeBubble();
});

/* ------------------------------------------- held, swung and thrown -- */

/* Python moves the window after the mouse and says how the pet swings and
   stretches about its scruff ("pose" {deg, sx, sy}; null when it's still
   again - the scruff goes back to pet.css's, physics.SCRUFF, which a pet
   that falls without being picked up swings about). A floor hit squashes
   it from the feet ("land" {hard, depth}), deeper the harder it came down. */
Buddy.on("pose", p => {
    pose.style.transform = p ? `rotate(${p.deg}deg) scale(${p.sx}, ${p.sy})` : "";
    if (!p) pose.style.transformOrigin = "";
});

Buddy.on("land", l => {
    const depth = Math.min(1, Math.max(0.25, Number(l && l.depth) || 0));
    squash.style.setProperty("--land-sx", (1 + 0.15 * depth).toFixed(3));
    squash.style.setProperty("--land-sy", (1 - 0.2 * depth).toFixed(3));
    squash.classList.remove("landing");
    void squash.offsetWidth;            // the same class again starts the bump again
    squash.classList.add("landing");
});
squash.addEventListener("animationend", e => {
    if (e.target === squash) squash.classList.remove("landing");
});

/* Where the pet hangs from: its scruff, just below the top of its head
   (sprites.js says where the head is, in the 130 x 100 drawing - facing
   left, sprites.css mirrors the drawing and the head with it), as
   fractions of its box. */
function scruff() {
    const headX = parseFloat(sprite.dataset.headX);
    const headTop = parseFloat(sprite.dataset.headTop);
    const x = Number.isFinite(headX) ? headX : 65;
    const fx = (pet.facing < 0 ? 130 - x : x) / 130;
    const fy = ((Number.isFinite(headTop) ? headTop : 20) + 8) / 100;
    return {fx: Math.min(1, Math.max(0, fx)), fy: Math.min(1, Math.max(0, fy))};
}

/* ----------------------------------------------------- click and drag -- */

const DRAG_PX = 5;
let press = null;       // {x, y, grabbing, moving (the system's move, Physics off)}

/* Picked up: Python takes it from here, watching the mouse itself - the
   page only says when it's let go. With Physics off, the system moves the
   window instead ("drag") and no pointerup comes back. */
function grab(e) {
    if (pet.physics === false) {
        stage.classList.add("dragging");
        if (!bubble.hidden) closeBubble();
        send("drag");
        const was = press;
        was.moving = true;
        setTimeout(() => { if (press === was) endPress(); }, 400);
        return;
    }
    press.grabbing = true;
    stage.classList.add("dragging");
    if (!bubble.hidden) closeBubble();
    hidePill();
    try { stage.setPointerCapture(e.pointerId); } catch (_) { /* gone already: Python checks the button too */ }
    const at = scruff();
    pose.style.transformOrigin = `${(at.fx * stage.clientWidth).toFixed(1)}px ${(at.fy * stage.clientHeight).toFixed(1)}px`;
    send("grab", at);
}

function endPress() {
    press = null;
    stage.classList.remove("dragging");
}

function drop() {
    const was = press;
    endPress();
    if (was && was.grabbing) send("drop");
}

stage.addEventListener("pointerdown", e => {
    if (e.button !== 0) return;
    press = {x: e.clientX, y: e.clientY, grabbing: false, moving: false};
    if (pet.state === "fly" && pet.physics !== false) grab(e);       // caught in the air
});
stage.addEventListener("pointermove", e => {
    if (!press || press.grabbing || press.moving) return;
    if (Math.hypot(e.clientX - press.x, e.clientY - press.y) >= DRAG_PX) grab(e);
});
stage.addEventListener("pointerup", e => {
    if (!press || e.button !== 0) return;
    if (press.grabbing) return drop();
    if (press.moving) return endPress();
    endPress();
    if (!bubble.hidden && !askForm.hidden) return closeBubble();     // a second click puts the field away
    send("click");
});
stage.addEventListener("pointercancel", drop);
stage.addEventListener("lostpointercapture", () => { if (press && press.grabbing) drop(); });
stage.addEventListener("contextmenu", e => {
    e.preventDefault();
    if (press && press.grabbing) return;
    if (!bubble.hidden) closeBubble();
    send("menu");
});
bubble.addEventListener("contextmenu", e => e.preventDefault());

Sprites.render(sprite, pet.species);
size(pet.px);
