"""What a panel is made from: the kinds of window, the styles, the accents to pick
from, the ready-made layouts (a whole page each, in their groups) and the components
(a piece to put into a page). No Qt, no Resolve.

Every layout and component is a function that returns a fresh tree of boxes, written
short: only what differs from a box's defaults, and no ids - model.clean() makes each
box whole and numbers it. A tree adapts to the window's width by a few rules on its
boxes ("wrap", "minw", "equal", "aspect", "hide_below"), never by a second layout.

All the sample text is made up: the people are Tess, Sam and Olive, the addresses
example.com's.
"""

KINDS = ("app", "browser", "phone", "dialog", "widget")
KIND_NAMES = {"app": "App", "browser": "Browser", "phone": "Phone", "dialog": "Dialog", "widget": "Widget"}
STYLES = ("mac", "plain", "glass", "windows", "cartoon")
STYLE_NAMES = {"mac": "Mac", "plain": "Plain", "glass": "Glass", "windows": "Windows", "cartoon": "Cartoon"}
ACCENTS = ("#3c82e0", "#e4572e", "#059669", "#7c3aed", "#d97706", "#db2777")
GROUPS = ("Website", "App", "Data", "Gallery")

PEOPLE = ("Tess", "Sam", "Olive")
DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


# ------------------------------------------------------ the building blocks --

def box(role, **more):
    return {"role": role, **more}


def col(children, **more):
    return box("section", **{"dir": "column", "gap": 8, "children": children, **more})


def row(children, **more):
    return box("section", **{"dir": "row", "gap": 8, "align": "center", "children": children, **more})


def text(words, variant="body", **more):
    return box("text", text=words, variant=variant, **more)


def button(words, variant="", **more):
    return box("button", **{"text": words, "variant": variant, "w": "hug", **more})


def field(words, **more):
    return box("field", text=words, **more)


def image(aspect=16 / 9, **more):
    return box("image", aspect=aspect, **more)


def icon(variant="", **more):
    return box("icon", variant=variant, **more)


def divider():
    return box("divider")


def badge(words):
    return box("badge", text=words, w="hug")


def progress(p, **more):
    return box("progress", p=p, **more)


def rows_of(labels, seed):
    """Made-up numbers for a chart, the same every time: they wander, and drift up."""
    return [{"label": label, "value": float(18 + (seed * 37 + i * 53) % 46 + i * 4)} for i, label in enumerate(labels)]


def chart(shape, labels=DAYS, seed=1, **more):
    round_one = shape in ("pie", "donut")
    return box("chart", chart={"shape": shape, "rows": rows_of(labels, seed)},
               **{"aspect": 1.0 if round_one else 2.5, **more})


def card(children, **more):
    return col(children, **{"variant": "card", "pad": 12, "name": "Card", **more})


def stat(label, value, change, up=True):
    return card([text(label, "label"), text(value, "num"), text(("+" if up else "-") + change, "up" if up else "down")],
                minw=110, gap=4, name="Stat")


def stats():
    return row([stat("Sales", "48,200", "12 %"), stat("Orders", "1,284", "8 %"),
                stat("Returns", "37", "3 %", up=False), stat("Visits", "90,400", "21 %")],
               wrap=True, align="stretch", name="Stat tiles")


SUBJECTS = ("Notes for Friday's meeting", "The draft is ready to read", "Invoice 1042 attached", "Can we move the call?",
            "Three colour options", "The export finished overnight", "Second pass, version 2")
WHENS = ("9:41", "8:02", "Mon", "Mon", "Sun", "Sat", "Fri")


def item(title, under, when="", **more):
    cells = [icon("avatar"), col([text(title, strong=True), text(under, "caption")], gap=1)]
    if when:
        cells.append(text(when, "caption", w="hug", hide_below=380))
    return row(cells, **{"variant": "row", "pad": [6, 8, 6, 8], "name": "Item", **more})


def listing(n=5):
    return col([item(PEOPLE[i % 3], SUBJECTS[i % 7], WHENS[i % 7], **({"on": True} if i == 0 else {})) for i in range(n)],
               gap=2, name="List")


TASKS = ("Opening screen", "Welcome email", "Price list", "Help pages", "Thank-you card", "Short trailer")
STATES = ("Live", "Review", "Draft")
AGES = ("2 h", "1 d", "3 d", "1 w")
SUMS = ("$1,200", "$860", "$2,400", "$540")


def table(n=5):
    head = row([text("Name", "label", w="fill"), text("Status", "label", w=84), text("Owner", "label", w=90, hide_below=460),
                text("Updated", "label", w=70, hide_below=540), text("Amount", "label", w=64, right=True)],
               pad=[4, 8, 4, 8])
    lines = [row([text(TASKS[i % 6], w="fill"), box("section", dir="row", w=84, children=[badge(STATES[i % 3])]),
                  text(PEOPLE[i % 3], "caption", w=90, hide_below=460), text(AGES[i % 4], "caption", w=70, hide_below=540),
                  text(SUMS[i % 4], w=64, right=True)], pad=[5, 8, 5, 8], variant="row") for i in range(n)]
    return col([head, divider(), *lines], gap=0, variant="card", name="Table")


def gallery(n=8, least=110, aspect=1.0):
    return row([image(aspect, hue=(i * 9) % 40) for i in range(n)], wrap=True, equal=least, name="Gallery")


def sidebar(items, on=0):
    return col([row([icon("sm"), text(words, hide_below=500, w="fill")], variant="navitem", pad=[5, 8, 5, 8],
                    on=(i == on), name=words) for i, words in enumerate(items)],
               variant="tint", pad=8, gap=2, w="hug", name="Sidebar")


def tabs(items):
    return row([text(words, "caption", w="hug") if i else badge(words) for i, words in enumerate(items)],
               gap=12, name="Tabs")


def chart_card(title, shape, labels=DAYS, seed=1, **more):
    return card([row([text(title, "h2", w="fill"), text("Last 30 days", "caption", w="hug", hide_below=420)]),
                 chart(shape, labels, seed)], **{"minw": 220, "name": title, **more})


def plan(name, price, picked=False):
    return card([text(name, "label"), text(price, "h1"), text("per month", "caption"), divider(),
                 text("Unlimited pages", "body"), text("Shared folders", "body"), text("Email support", "body"),
                 button("Choose", "" if picked else "ghost")], minw=150, name="Plan")


def bubble(words, mine=False):
    # A bubble is as wide as its words, up to a width it then wraps at.
    return row([col([text(words)], variant="bubble-me" if mine else "bubble", pad=[6, 10, 6, 10], w="hug", maxw=260)],
               justify="end" if mine else "start")


# ------------------------------------------------------------- the layouts --

def _landing():
    return col([
        row([text("Harbour", "h2", w="fill"), text("Product", "caption", w="hug", hide_below=460),
             text("Pricing", "caption", w="hug", hide_below=460), button("Sign in", "ghost")]),
        row([col([text("Plan the week in one place.", "h1"),
                  text("Lists, a calendar and notes that stay together, so nothing is looked for twice.", "body"),
                  row([button("Get started"), button("See how it works", "ghost")], wrap=True)], minw=220, gap=10),
             image(4 / 3, minw=200, hide_below=340)], wrap=True, variant="hero", pad=16, gap=14),
        row([card([icon(), text("Lists", "h2"), text("Write it down once and find it again.", "caption")], minw=150),
             card([icon(), text("Calendar", "h2"), text("See the whole week at a glance.", "caption")], minw=150),
             card([icon(), text("Notes", "h2"), text("Keep what was said next to what's due.", "caption")], minw=150)],
            wrap=True, align="stretch"),
    ], pad=14, gap=14)


def _pricing():
    return col([
        col([text("Simple pricing", "h1", center=True), text("Start free. Change plans whenever you like.", "caption", center=True)],
            gap=4),
        row([plan("Starter", "$0"), plan("Plus", "$12", picked=True), plan("Team", "$40")], wrap=True, align="stretch"),
    ], pad=16, gap=14)


def _article():
    return col([
        text("Field notes", "label"), text("A slower way to start the day", "h1"),
        row([icon("avatar"), text("Tess - 6 min read", "caption", w="fill")]),
        image(16 / 7),
        text("The first hour sets the pace for the rest. Leave the inbox shut, pick the one thing that matters, "
             "and give it your attention before anything else asks for it.", "body"),
        text("Write tomorrow's first step down tonight, where you will see it.", "body"),
        row([badge("Habits"), badge("Work"), badge("Mornings")], wrap=True),
    ], pad=18, gap=10, maxw=560, center=True)


def _product():
    return row([
        image(1.0, minw=200),
        col([text("Desk lamp", "label"), text("Lantern Two", "h1"), text("$89", "h2"),
             text("Warm light, a weighted base, and a dimmer you can find in the dark.", "body"),
             row([button("Add to bag"), button("Save", "ghost")], wrap=True), divider(),
             text("Free delivery - 30-day returns", "caption")], minw=200, gap=8),
    ], wrap=True, pad=16, gap=16, align="start")


def _signin():
    return col([
        text("Welcome back", "h1", center=True), text("Sign in to carry on where you stopped.", "caption", center=True),
        field("tess@example.com", name="Email"), field("Password"), button("Sign in", w="fill"),
        row([divider(), text("or", "caption", w="hug"), divider()]),
        button("Use a passkey", "ghost", w="fill"),
    ], pad=22, gap=10, maxw=340, center=True)


def _mail():
    return row([
        sidebar(["Inbox", "Sent", "Drafts", "Archive", "Bin"]),
        col([field("Search mail"), listing(6)], minw=180, gap=6),
        col([text(SUBJECTS[0], "h2"), text("Tess - to you", "caption"), divider(),
             text("The second section runs long. Could we cut the example and keep the summary?", "body"),
             button("Reply")], minw=200, hide_below=600, variant="card", pad=12, reacts=False, name="Message"),
    ], pad=10, gap=10, align="stretch")


def _dashboard():
    return row([
        sidebar(["Overview", "Sales", "Customers", "Reports", "Settings"]),
        col([row([text("Overview", "h1", w="fill"), button("Export", "ghost")]), stats(),
             row([chart_card("Sales", "area", MONTHS, 2), chart_card("Orders by day", "bars", DAYS, 3)],
                 wrap=True, align="stretch"),
             table(4)], minw=200, gap=10),
    ], pad=10, gap=10, align="stretch")


def _settings():
    options = (("Theme", "Dark"), ("Accent", "Blue"), ("Text size", "Medium"), ("Reduce motion", "Off"))
    return row([
        sidebar(["General", "Appearance", "Shortcuts", "Privacy", "Account"], on=1),
        col([text("Appearance", "h1"),
             *[row([text(name, w="fill"), field(value, w=130)], variant="row", pad=[4, 0, 4, 0], name=name)
               for name, value in options],
             divider(), row([button("Save"), button("Reset", "ghost")])], minw=200, gap=8),
    ], pad=10, gap=12, align="stretch")


def _chat():
    return col([
        row([icon("avatar"), col([text("Sam", "h2"), text("online", "caption")], gap=0)]), divider(),
        bubble("The draft is ready to read."), bubble("Reading it now.", mine=True),
        bubble("Page two is lovely. Page four feels rushed."), bubble("On it - I'll give it more room.", mine=True),
        row([field("Message", w="fill"), button("Send")]),
    ], pad=12, gap=8)


def _calendar():
    def day(n):
        marked = [badge("Call")] if n % 5 == 0 else []
        return col([text(str(n + 2), "caption"), *marked], variant="tint" if n == 19 else "card", pad=5, gap=3, minh=44,
                   name="Day")
    return col([
        row([text("March", "h1", w="fill"), button("Today", "ghost")]),
        row([text(letter, "label", w="fill", center=True) for letter in "MTWTFSS"], gap=4),
        *[row([day(week * 7 + d) for d in range(7)], gap=4, align="stretch") for week in range(4)],
    ], pad=12, gap=6)


def _kanban():
    jobs = ("Outline", "First draft", "Pictures", "Headings", "Proofread", "Publish")

    def lane(k, title):
        cards = [card([text(jobs[k * 2 + i], "body"), row([icon("avatar"), text("Fri", "caption", w="fill")])], pad=9, gap=6)
                 for i in range(3 - k)]
        return col([row([text(title, "h2", w="fill"), badge(str(3 - k))]), *cards],
                   variant="tint", pad=9, minw=150, gap=7, name=title)
    return row([lane(k, title) for k, title in enumerate(("To do", "Doing", "Done"))], wrap=True, pad=10, gap=10, align="start")


def _player():
    return col([
        image(1.0, maxw=220, center=True), text("Slow Morning", "h1", center=True),
        # Play starts the song: the bar fills to where it says, and the time runs up to it.
        text("Low Tide - Side A", "caption", center=True), progress(38, run={"by": "@Play", "seconds": 4.0}),
        row([text("1:24", "caption", w="fill", count=True, run={"by": "@Play", "seconds": 4.0}), text("3:41", "caption", w="hug")]),
        row([button("Back", "ghost"), button("Play"), button("Next", "ghost")], justify="center"),
    ], pad=18, gap=9, maxw=320, center=True)


def _files():
    return row([
        sidebar(["Recents", "Documents", "Pictures", "Music", "Shared"], on=1),
        col([row([field("Search", w="fill"), button("New", "ghost")]), gallery(9, 96, 4 / 3)], minw=180, gap=10),
    ], pad=10, gap=10, align="stretch")


def _search():
    found = ("Weekend walks near the coast", "A packing list, three versions", "Recipe cards", "Reading list")
    return col([
        field("weekend ideas"), tabs(["All", "Pages", "Pictures", "People"]),
        *[row([image(4 / 3, w=76, hide_below=360), col([text(title, "h2"), text("Page - updated this week", "caption")], gap=2)],
              variant="row", pad=6, name="Result") for title in found],
    ], pad=12, gap=8)


def _stats():
    return col([text("This week", "h1"), stats()], pad=14, gap=10)


def _linechart():
    return col([chart_card("Sessions", "line", MONTHS, 4),
                row([text("Peak 4,120", "caption", w="fill"), text("Average 2,870", "caption", w="hug")])], pad=14, gap=8)


def _barchart():
    return row([
        chart_card("Orders by day", "bars", DAYS, 3),
        card([text("Channels", "h2"), chart("donut", ("Direct", "Search", "Social"), 5, maxw=150, center=True),
              row([badge("Direct"), badge("Search"), badge("Social")], wrap=True, justify="center")], minw=170),
    ], wrap=True, pad=14, align="stretch")


def _table():
    return col([row([text("Projects", "h1", w="fill"), field("Filter", w=130, hide_below=420)]), table(6)], pad=14, gap=10)


def _leaderboard():
    scores = (("Morning walk", 92), ("Reading", 78), ("Stretching", 64), ("Journal", 51), ("Early night", 37))
    return col([
        text("Top habits", "h1"),
        *[row([text(str(i + 1), "h2", w=18),
               col([row([text(name, w="fill"), text(f"{p} %", "caption", w="hug")]), progress(p)], gap=3)], name=name)
          for i, (name, p) in enumerate(scores)],
    ], pad=14, gap=9)


def _photogrid():
    return col([row([text("Pictures", "h1", w="fill"), tabs(["All", "Picks"])]), gallery(12, 100, 1.0)], pad=12, gap=10)


def _cards():
    return row([card([image(4 / 3, hue=i * 7), text(TASKS[i], "h2"), text(f"{8 + i * 2} pages", "caption")],
                     minw=150, pad=8, gap=6) for i in range(6)], wrap=True, pad=12, align="stretch")


def _carousel():
    covers = ("Trip", "Garden", "Recipes", "Plans", "Letters", "Sketches")
    # A row can't scroll, so the covers that wouldn't fit a narrow window are left out of it.
    least = (0, 0, 0, 390, 480, 570)
    return col([
        text("Recently opened", "h1"),
        row([col([image(3 / 4, hue=i * 6), text(name, "caption", center=True)], w=84, gap=4, hide_below=least[i], name="Cover")
             for i, name in enumerate(covers)], align="start"),
        divider(), gallery(4, 120, 16 / 9),
    ], pad=12, gap=10)


LAYOUTS = {
    "landing": {"group": "Website", "name": "Landing page", "make": _landing},
    "pricing": {"group": "Website", "name": "Pricing", "make": _pricing},
    "article": {"group": "Website", "name": "Article", "make": _article},
    "product": {"group": "Website", "name": "Product page", "make": _product},
    "signin": {"group": "Website", "name": "Sign-in form", "make": _signin},
    "mail": {"group": "App", "name": "Mail", "make": _mail},
    "dashboard": {"group": "App", "name": "Dashboard", "make": _dashboard},
    "settings": {"group": "App", "name": "Settings", "make": _settings},
    "chat": {"group": "App", "name": "Chat", "make": _chat},
    "calendar": {"group": "App", "name": "Calendar", "make": _calendar},
    "kanban": {"group": "App", "name": "Board (kanban)", "make": _kanban},
    "player": {"group": "App", "name": "Music player", "make": _player},
    "files": {"group": "App", "name": "File browser", "make": _files},
    "search": {"group": "App", "name": "Search results", "make": _search},
    "stats": {"group": "Data", "name": "Stat tiles", "make": _stats},
    "linechart": {"group": "Data", "name": "Line chart", "make": _linechart},
    "barchart": {"group": "Data", "name": "Bars and a donut", "make": _barchart},
    "table": {"group": "Data", "name": "Table", "make": _table},
    "leaderboard": {"group": "Data", "name": "Leaderboard", "make": _leaderboard},
    "photogrid": {"group": "Gallery", "name": "Photo grid", "make": _photogrid},
    "cards": {"group": "Gallery", "name": "Card gallery", "make": _cards},
    "carousel": {"group": "Gallery", "name": "Cover row", "make": _carousel},
}

# A component's id is never a layout's, so a shelf tile is known by its id alone.
COMPONENTS = {
    "heading": {"name": "Heading", "make": lambda: text("A heading", "h1")},
    "text": {"name": "Text", "make": lambda: text("A line of text you can change.", "body")},
    "button": {"name": "Button", "make": lambda: button("Button")},
    "field": {"name": "Field", "make": lambda: field("Search")},
    "image": {"name": "Image", "make": lambda: image(16 / 9)},
    "card": {"name": "Card", "make": lambda: card([text("Card", "h2"), text("Put anything in here.", "caption")])},
    "columns": {"name": "Columns", "make": lambda: row([card([text("One", "h2")], minw=130), card([text("Two", "h2")], minw=130)],
                                                        wrap=True, align="stretch", name="Columns")},
    "list": {"name": "List", "make": lambda: listing(4)},
    "data_table": {"name": "Table", "make": lambda: table(4)},
    "stat_tiles": {"name": "Stat tiles", "make": stats},
    "line_chart": {"name": "Line chart", "make": lambda: chart_card("Sessions", "line", MONTHS, 4)},
    "area_chart": {"name": "Area chart", "make": lambda: chart_card("Sales", "area", MONTHS, 2)},
    "bar_chart": {"name": "Bar chart", "make": lambda: chart_card("Orders", "bars", DAYS, 3)},
    "donut": {"name": "Donut", "make": lambda: card([text("Share", "h2"),
                                                     chart("donut", ("Direct", "Search", "Social"), 5, maxw=140, center=True)],
                                                    name="Donut")},
    "gallery": {"name": "Gallery", "make": lambda: gallery(6, 100, 1.0)},
    "sidebar": {"name": "Sidebar", "make": lambda: sidebar(["Home", "Library", "Shared", "Settings"])},
    "tabs": {"name": "Tabs", "make": lambda: tabs(["All", "Recent", "Shared"])},
    "progress": {"name": "Progress", "make": lambda: progress(60)},
    "badge": {"name": "Badge", "make": lambda: badge("New")},
    "divider": {"name": "Divider", "make": divider},
}


def layout(layout_id):
    """A fresh tree of a layout's boxes, or None for one there isn't."""
    entry = LAYOUTS.get(layout_id)
    return entry["make"]() if entry else None


def component(component_id):
    """A fresh tree of a component's boxes, or None for one there isn't."""
    entry = COMPONENTS.get(component_id)
    return entry["make"]() if entry else None
