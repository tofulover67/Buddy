/*
 * The page's tabs: Timeline (timeline.js) and Sound Design (design.js) -
 * the second drawn the first time it shows. (Keys, the third, became the DAW:
 * the tab row's last button opens that tool.) The tab chosen isn't saved: the page
 * loads on Timeline (while Buddy keeps it loaded it stays on whichever tab
 * was last chosen).
 * A hidden panel has no width, so timeline.js lays the tracks out again when
 * Timeline shows (its ResizeObserver), and leaves its keys alone meanwhile.
 * Its own scope: timeline.js's top-level names are shared with it otherwise.
 */
(() => {
    const tabs = document.getElementById("tabs");
    // The way to the DAW is a button, not a tab: an arrow says it goes to another tool.
    tabs.querySelector(".tab-link").append(Buddy.icon("arrow"));

    function showTab(name) {
        for (const button of tabs.querySelectorAll("button[data-tab]")) {
            button.setAttribute("aria-selected", String(button.dataset.tab === name));
        }
        for (const panel of document.querySelectorAll(".panel")) panel.hidden = panel.id !== `panel-${name}`;
    }

    tabs.addEventListener("click", e => {
        const button = e.target.closest("button[data-tab]");
        if (button) showTab(button.dataset.tab);
    });
})();
