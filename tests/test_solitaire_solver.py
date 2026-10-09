"""Solitaire's solver (pages/games/web/solitaire_solver.js), run with Node:
every winning line it gives must play out under the real rules - drawing
one or three, turning the deck over, columns alternating colours - to a
won game, so deals are winnable and hints lead somewhere. Skipped without
Node."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest

import _paths

SOLVER = _paths.APP / "pages" / "games" / "web" / "solitaire_solver.js"
NODE = shutil.which("node")

CHECK = r"""
const {solitaireSolver} = require(process.argv[2]);
const S = solitaireSolver();
const suit = id => (id / 13) | 0, rank = id => id % 13 + 1, red = id => suit(id) === 1 || suit(id) === 2;
let seed = 7;
const rand = () => { seed = (seed * 1103515245 + 12345) % 2147483648; return seed / 2147483648; };

/* The line played out under the real rules: stock and waste tops last. */
function replay(start, draw, moves) {
  const tab = start.tab.map(c => c.slice()), up = start.down.slice(), found = start.found.slice();
  let waste = start.talon.slice(0, start.pos), stock = start.talon.slice(start.pos).reverse();
  const fail = msg => { throw new Error(msg); };
  const fits = (id, j) => !tab[j].length ? rank(id) === 13
    : red(tab[j].at(-1)) !== red(id) && rank(tab[j].at(-1)) === rank(id) + 1;
  const flip = i => { if (up[i] >= tab[i].length) up[i] = Math.max(0, tab[i].length - 1); };
  for (const m of moves) {
    if (m.t === "wf" || m.t === "wt") {
      const want = waste.concat(stock.slice().reverse())[m.p - 1];
      for (let n = 0; waste.at(-1) !== want; n++) {
        if (n > 200) fail("deck card never came up");
        if (!stock.length) { stock = waste.reverse(); waste = []; }
        else for (let k = 0; k < draw && stock.length; k++) waste.push(stock.pop());
      }
      const id = waste.pop();
      if (m.t === "wf") { if (found[suit(id)] !== rank(id) - 1) fail("wf"); found[suit(id)]++; }
      else { if (!fits(id, m.j)) fail("wt"); tab[m.j].push(id); }
    } else if (m.t === "tf") {
      const id = tab[m.i].at(-1);
      if (id === undefined || found[suit(id)] !== rank(id) - 1) fail("tf");
      tab[m.i].pop(); found[suit(id)]++; flip(m.i);
    } else if (m.t === "tt") {
      if (m.k < up[m.i]) fail("tt face down");
      const run = tab[m.i].slice(m.k);
      for (let q = 1; q < run.length; q++) if (red(run[q]) === red(run[q - 1]) || rank(run[q]) !== rank(run[q - 1]) - 1) fail("run");
      if (!fits(run[0], m.j)) fail("tt");
      tab[m.i].splice(m.k); tab[m.j].push(...run); flip(m.i);
    } else if (m.t === "ft") {
      const id = m.s * 13 + found[m.s] - 1;
      if (!fits(id, m.j)) fail("ft");
      found[m.s]--; tab[m.j].push(id);
    } else fail("unknown move " + m.t);
  }
  const allUp = up.every(u => u === 0);
  if (!(found.every(f => f === 13) || (allUp && !stock.length && !waste.length))) fail("not won");
}

const out = {dealt: {}, lines: 0};
for (const draw of [1, 3]) {
  const d = S.deal(draw, 60000, 4000, rand);
  const start = S.fromDeck(d.deck);
  const r = S.solve(start, draw);
  if (r.result !== "won") throw new Error("a dealt game the solver can't win");
  replay(start, draw, r.moves);
  out.dealt[draw] = d.solved;
  for (let n = 0; n < 15; n++) {
    const deck = Array.from({length: 52}, (_, i) => i);
    for (let i = 51; i > 0; i--) { const j = Math.floor(rand() * (i + 1)); [deck[i], deck[j]] = [deck[j], deck[i]]; }
    const s = S.fromDeck(deck), res = S.solve(s, draw, 60000);
    if (res.result === "won") { replay(s, draw, res.moves); out.lines++; }
  }
}
// The deals kept for a search that runs out of time: each wins, its suits swapped too.
out.kept = 0;
for (const draw of [1, 3]) {
  for (const deck of S.WON[draw]) {
    for (const swap of [S.SUIT_SWAPS[0], S.SUIT_SWAPS[3], S.SUIT_SWAPS[6]]) {
      const swapped = deck.map(id => swap[(id / 13) | 0] * 13 + id % 13);
      if (new Set(swapped).size !== 52) throw new Error("a swap lost a card");
      const s = S.fromDeck(swapped), res = S.solve(s, draw, 400000);
      if (res.result !== "won") throw new Error("a kept deal the solver can't win");
      replay(s, draw, res.moves);
      out.kept++;
    }
  }
  // No time to search (one position each, none to spare): a kept deal, still winnable.
  const late = S.deal(draw, 1, 0, rand);
  if (!late.solved || !late.kept) throw new Error("ran out of time and dealt an unproven deck");
  const s = S.fromDeck(late.deck), res = S.solve(s, draw, 400000);
  if (res.result !== "won") throw new Error("the late deal can't be won");
  replay(s, draw, res.moves);
}
// Only a king brought down from its foundation to an empty column wins this:
// every other card is up, and the spade 2 sits on the face-down ace.
const ids = (s, r) => s * 13 + r - 1;
const stuck = {tab: [[ids(0, 1), ids(0, 2)], [], [], [], [], [], []], down: [1, 0, 0, 0, 0, 0, 0],
               found: [0, 13, 13, 13], talon: [], pos: 0};
const r = S.solve(stuck, 1);
out.stuck = r.result;
if (r.result === "won") replay(stuck, 1, r.moves);
// Mid-game, from the page's own table: the solver reads it the way solitaire.js keeps it.
const game = {stock: [{s: 0, r: 13, up: false}], waste: [{s: 0, r: 12, up: true}], found: [[], [], [], []],
              tab: [[{s: 1, r: 1, up: true}], [], [], [], [], [], []]};
const pos = S.fromGame(game);
out.fromGame = {talon: pos.talon, pos: pos.pos, down: pos.down[0], tab0: pos.tab[0]};
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(NODE, "Node isn't installed")
class SolverTests(unittest.TestCase):
    def test_winning_lines_play_out_under_the_real_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = os.path.join(tmp, "check.js")
            with open(script, "w", encoding="utf-8") as f:
                f.write(CHECK)
            proc = subprocess.run([NODE, script, str(SOLVER)], capture_output=True, text=True, timeout=300)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertEqual(out["dealt"], {"1": True, "3": True})
        self.assertGreater(out["lines"], 10)                 # most random deals solved, each line played out
        self.assertEqual(out["stuck"], "won")
        self.assertEqual(out["kept"], 2 * 4 * 3)            # every kept deal, three suit swaps each, won
        # The deck in the order it's drawn: the waste first, then the stock from the top.
        self.assertEqual(out["fromGame"], {"talon": [11, 12], "pos": 1, "down": 0, "tab0": [13]})


if __name__ == "__main__":
    unittest.main()
