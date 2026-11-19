"use strict";

// The Data Graphics tab's sub-tab row, shared by its pages (counters/,
// graphs/): each page's own <nav id="graphics-tabs"> marks its own tab
// aria-selected; a click on another asks Python (DataGraphicsPage.show_tab).
document.getElementById("graphics-tabs").addEventListener("click", event => {
    const tab = event.target.closest("button[data-tab]");
    if (tab && tab.getAttribute("aria-selected") !== "true") {
        Buddy.send("switch_tab", {tab: tab.dataset.tab});
    }
});
