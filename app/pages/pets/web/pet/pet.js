/* The pet's window (pet_window.py): draws the pet Python describes ("pet"),
   shows what it says ("bubble"), and reports what the person did - a click
   (a pat), a drag (the OS moves the window), a right-click (a menu), and a
   question typed into the bubble ("ask"). Before the bubble shows, Python
   is told to make room ("bubble" {open}); after, to take it back. */
"use strict";

const {send} = Buddy;
const $ = id => document.getElementById(id);

const main = document.querySelector(".pet-main");
const stage = $("stage");
const sprite = $("sprite");
const bubble = $("bubble");
const bubbleText = $("bubble-text");
const askForm = $("ask-form");
const askInput = $("ask-input");

let pet = {species: "cat", name: "", size: "medium", px: 128, state: "idle", facing: 1, ask: true};
let hideTimer = 0;

function size(px) {
    document.documentElement.style.setProperty("--pet-h", `${px}px`);
    document.documentElement.style.setProperty("--pet-w", `${Math.round(px * 1.3)}px`);
}

Buddy.on("pet", p => {
    pet = {...pet, ...p};
    Sprites.render(sprite, pet.species, pet.skin || "classic", pet.colour);
    sprite.dataset.state = pet.state;
    sprite.dataset.facing = String(pet.facing);
    size(pet.px);
    stage.title = pet.name ? `${pet.name} – click to pat, right-click for more, drag to move`
                           : "Click to pat, right-click for more, drag to move";
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

/* ------------------------------------------------------------ bubble -- */

function openBubble(kind, text) {
    clearTimeout(hideTimer);
    hidePill();
    room("bubble", true);
    bubbleText.textContent = text || "";
    bubbleText.classList.toggle("cheer", kind === "cheer");
    askForm.hidden = kind !== "ask";
    bubble.hidden = false;
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

/* ---------------------------------------------------- click and drag -- */

const DRAG_PX = 5;
let press = null;       // {x, y, dragging}

stage.addEventListener("pointerdown", e => {
    if (e.button !== 0) return;
    press = {x: e.clientX, y: e.clientY, dragging: false};
});
stage.addEventListener("pointermove", e => {
    if (!press || press.dragging) return;
    if (Math.hypot(e.clientX - press.x, e.clientY - press.y) >= DRAG_PX) {
        press.dragging = true;
        stage.classList.add("dragging");
        if (!bubble.hidden) closeBubble();
        send("drag");       // the OS takes over the move; no pointerup reaches us
        setTimeout(() => { press = null; stage.classList.remove("dragging"); }, 400);
    }
});
stage.addEventListener("pointerup", e => {
    if (!press || e.button !== 0) return;
    const was = press;
    press = null;
    stage.classList.remove("dragging");
    if (was.dragging) return;
    if (!bubble.hidden && !askForm.hidden) return closeBubble();     // a second click puts the field away
    send("click");
});
stage.addEventListener("pointercancel", () => { press = null; stage.classList.remove("dragging"); });
stage.addEventListener("contextmenu", e => {
    e.preventDefault();
    if (!bubble.hidden) closeBubble();
    send("menu");
});
bubble.addEventListener("contextmenu", e => e.preventDefault());

Sprites.render(sprite, pet.species);
size(pet.px);
