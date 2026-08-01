'use strict';

/* ===========================================================================
   The 🎲 button — say how many bidders, get a whole setup.

   THE ONE RULE still holds: no mechanism name appears below. Which table gets
   filled is decided by the registry's declared `input_kind`, exactly as
   web/packages.js decides it, and the strategy pool is whatever GET /strategies
   returned. Nothing here knows what any of them are.

   It only writes the setup form — the same fields a person could have typed —
   and then leaves. Running is still the Run button's job.
   =========================================================================== */

// Item names for combinatorial setups. Two-item bundles over a small universe
// are where the greedy/optimal gap actually shows up, so the pool stays small.
const RAND_ITEMS = ['north', 'south', 'east', 'west', 'am', 'pm'];

const randInt = (lo, hi) => lo + Math.floor(Math.random() * (hi - lo + 1));
const pick = (list) => list[Math.floor(Math.random() * list.length)];

/** A value worth bidding on, and a bid somewhere between half of it and all of
 *  it — shading is the thing the charts are drawn to show, so every random
 *  setup has some. */
function randValueBid() {
  const value = randInt(20, 100);
  return { value, bid: Math.round(value * (0.5 + Math.random() * 0.5)) };
}

function randCount() {
  const n = Number($('rand-count').value);
  return isNum(n) ? clamp(Math.round(n), 1, 8) : 3;
}

const randIds = (n) => 'ABCDEFGH'.slice(0, n).split('');

/** Give every row a strategy at random. Params stay at their registry defaults:
 *  a random epsilon is a different experiment from a random cast of bidders,
 *  and mixing the two makes neither readable. */
function randomiseStrategies() {
  const names = Object.keys((typeof series !== 'undefined' && series.registry) || {});
  if (!names.length) return;
  for (const tr of $('bidder-rows').children) {
    const select = tr.querySelector('[data-strategy]');
    if (!select) continue;
    select.value = pick(names);
    syncStrategyRow(tr);
  }
  renderStrategyNotes();
}

function randomiseBidders() {
  $('bidder-rows').replaceChildren();
  for (const id of randIds(randCount())) addBidderRow({ id, ...randValueBid() });
  randomiseStrategies();
  // The budget column tracks the bidder table through input events, and nothing
  // typed here, so nudge it by hand.
  if (typeof syncBudgetRows === 'function') syncBudgetRows();
}

/** One or two bundles each, drawn from a universe just big enough that they
 *  collide — bids that never conflict make the winner determination trivial. */
function randomisePackages() {
  const ids = randIds(randCount());
  const universe = RAND_ITEMS.slice(0, clamp(Math.ceil(ids.length / 2) + 1, 2, RAND_ITEMS.length));

  $('package-rows').replaceChildren();
  for (const bidder of ids) {
    for (let n = randInt(1, 2); n > 0; n -= 1) {
      const size = randInt(1, Math.min(2, universe.length));
      const items = [...universe].sort(() => Math.random() - 0.5).slice(0, size);
      const { value, bid } = randValueBid();
      addPackageRow({ bidder, items: items.join(', '), value, bid });
    }
  }
}

function randomise() {
  clearError();
  const isPackage = window.packagesExt && window.packagesExt.active && window.packagesExt.active();
  if (isPackage) randomisePackages(); else randomiseBidders();
}

$('randomize').addEventListener('click', randomise);
