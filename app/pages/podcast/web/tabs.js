"use strict";

// The Podcast tab's sub-tab row, shared by its three pages (sync/, switch/,
// text_edit/): each page's own <nav id="podcast-tabs"> marks its own tab
// aria-selected; a click on another asks Python (PodcastPage.show_tab).
document.getElementById("podcast-tabs").addEventListener("click", event => {
    const tab = event.target.closest("button[data-tab]");
    if (tab && tab.getAttribute("aria-selected") !== "true") {
        Buddy.send("switch_tab", {tab: tab.dataset.tab});
    }
});
