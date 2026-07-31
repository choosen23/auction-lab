'use strict';

/* ===========================================================================
   Combinatorial auctions — bidding on bundles instead of on one thing.

   THE ONE RULE from app.js still holds: no mechanism name appears below. The
   setup panel switches because the registry says the selected mechanism has
   `input_kind: "package"`, and the allocation view draws because the trace's
   allocation hands out lists of items instead of slot numbers. Shape and
   declaration decide; names never do.

   Two seams into app.js: `runAuction()` hands the whole submit path over here
   for package mechanisms (they post `packages`, not `bidders`), and the
   mechanism <select> tells us to swap the panel. Everything else — `api`,
   `render`, `traceScale`, `showError`, `app` — is used exactly as app.js
   exposes it, and nothing in app.js's state is written from here except
   `app.trace` / `app.index` via its own `render`.
   =========================================================================== */

// The instance that makes the point on first load: A wants both licences and bids
// 10; B and C each want one and bid 6. Greedy takes A's single big bid and is then
// blocked, scoring 10; the optimum splits the pair for 12. The gap is the reason
// this whole sub-phase exists, so the page opens already showing it.
const DEFAULT_PACKAGES = [
  { bidder: 'A', items: 'north, south', value: 10, bid: 10 },
  { bidder: 'B', items: 'north', value: 6, bid: 6 },
  { bidder: 'C', items: 'south', value: 6, bid: 6 },
];

// Mirrors the engine's own limits (agt/winner_determination.py). Checked here so a
// too-large problem is refused next to the form rather than after a round trip; the
// server checks them again, because a browser is not a trust boundary.
const MAX_BIDS = 20;
const MAX_ITEMS = 12;

/** Items are typed as free text; the universe is whatever was typed. Commas or
 *  spaces both separate, because people reach for either. */
function parseItems(raw) {
  return raw.split(/[,\s]+/).map((s) => s.trim()).filter(Boolean);
}

const currentSpec = () => (app.registry || {})[$('mechanism').value] || {};

// ============================================================ the bid table =

function addPackageRow(seed) {
  const tbody = $('package-rows');
  const tr = h('tr');

  const cell = (field, type, value, extra) => {
    const td = h('td', extra || null);
    const input = h('input');
    input.type = type;
    input.value = value;
    input.dataset.field = field;
    input.setAttribute('aria-label', field);
    if (type === 'number') { input.step = 'any'; input.min = '0'; }
    if (field === 'bidder') input.addEventListener('input', markAlternatives);
    td.append(input);
    tr.append(td);
    return input;
  };

  cell('bidder', 'text', seed.bidder);
  cell('items', 'text', seed.items, 'items-cell');
  cell('value', 'number', seed.value);
  cell('bid', 'number', seed.bid);

  const td = h('td');
  const remove = h('button', 'remove', '✕');
  remove.type = 'button';
  remove.title = `Remove ${seed.bidder}'s bid for ${seed.items}`;
  remove.addEventListener('click', () => {
    if (tbody.children.length <= 1) return;   // an auction needs at least one bid
    tr.remove();
    markAlternatives();
  });
  td.append(remove);
  tr.append(td);

  tbody.append(tr);
  markAlternatives();
}

/** "I can win at most one of these" is the part of XOR bidding people get wrong,
 *  so rows that share a bidder are marked as alternatives in the table itself
 *  rather than explained once in prose above it. */
function markAlternatives() {
  const rows = [...$('package-rows').children];
  const counts = new Map();
  for (const tr of rows) {
    const name = (tr.querySelector('[data-field="bidder"]').value || '').trim();
    counts.set(name, (counts.get(name) || 0) + 1);
  }

  let grouped = 0;
  for (const tr of rows) {
    const name = (tr.querySelector('[data-field="bidder"]').value || '').trim();
    const isAlt = name !== '' && counts.get(name) > 1;
    tr.classList.toggle('xor-alt', isAlt);
    const cell = tr.querySelector('[data-field="bidder"]').parentElement;
    let tag = cell.querySelector('.xor-tag');
    if (isAlt && !tag) {
      tag = h('span', 'xor-tag', 'either/or');
      tag.title = `${name} submitted more than one bundle and can win at most one`;
      cell.append(tag);
    } else if (!isAlt && tag) {
      tag.remove();
    }
    if (isAlt) grouped += 1;
  }

  const note = $('package-note');
  note.textContent = grouped > 0
    ? `${grouped} rows are marked either/or: those bidders submitted alternative bundles and can win at most one each.`
    : 'Give two rows the same bidder name to offer alternative bundles — that bidder can then win at most one of them.';
}

function readPackages() {
  return [...$('package-rows').children].map((tr) => {
    const get = (field) => tr.querySelector(`[data-field="${field}"]`).value;
    return {
      bidder: get('bidder').trim(),
      items: parseItems(get('items')),
      value: Number(get('value')),
      bid: Number(get('bid')),
    };
  });
}

function validatePackages(bids) {
  if (bids.length === 0) return 'Add at least one package bid.';
  if (bids.length > MAX_BIDS) {
    return `${bids.length} package bids is more than the exhaustive search will take on. The limit is ${MAX_BIDS}.`;
  }

  const universe = new Set();
  for (const b of bids) {
    if (!b.bidder) return 'Every package bid needs a bidder name.';
    if (b.items.length === 0) return `${b.bidder} needs at least one item — a bundle of nothing would conflict with nothing and win for free.`;
    if (new Set(b.items).size !== b.items.length) {
      return `${b.bidder} listed the same item twice in one bundle.`;
    }
    if (!isNum(b.value) || b.value < 0) return `${b.bidder} needs a value of 0 or more.`;
    if (!isNum(b.bid) || b.bid < 0) return `${b.bidder} needs a bid of 0 or more.`;
    for (const item of b.items) universe.add(item);
  }

  if (universe.size > MAX_ITEMS) {
    return `${universe.size} distinct items is more than the exhaustive search will take on. The limit is ${MAX_ITEMS}.`;
  }
  return null;
}

// ================================================================ the panel =

/** Swap the setup panel between the scalar bidder table and the package table,
 *  and hide the series controls — a strategy produces one number, which means
 *  nothing over bundles, and POST /run_series refuses these mechanisms outright. */
function applyMode() {
  const isPackage = currentSpec().input_kind === 'package';

  $('single-setup').hidden = isPackage;
  $('package-setup').hidden = !isPackage;

  for (const selector of ['.series-run', '#strategy-notes']) {
    const node = document.querySelector(selector);
    if (node) node.hidden = isPackage;
  }
  if (isPackage) {
    $('timeline-card').hidden = true;
    $('series-card').hidden = true;
    $('decision-card').hidden = true;
  }
}

// ============================================================== the results =

/** The bundles that were won, in the order the engine ranked them, or null when
 *  this trace allocated slot numbers (the ladder's job) or nothing at all. */
function bundleRows(trace) {
  const result = (trace && trace.result) || {};
  const allocation = result.allocation;
  if (!allocation || typeof allocation !== 'object' || Array.isArray(allocation)) return null;

  const entries = Object.entries(allocation);
  if (!entries.length || !entries.every(([, items]) => Array.isArray(items))) return null;

  const payments = result.payments || {};
  const utilities = result.utilities || {};
  const gains = result.gains || {};
  return entries.map(([id, items]) => ({
    id,
    items,
    pays: isNum(payments[id]) ? payments[id] : 0,
    gain: isNum(gains[id]) ? gains[id] : 0,
    utility: isNum(utilities[id]) ? utilities[id] : 0,
  }));
}

/** Two bars on one welfare scale: what greedy found, and what was there to find.
 *  Not two charts and not two axes — the whole point is that they are the same
 *  measure, so the shortfall is a length you can see rather than a subtraction
 *  you have to do. Colour reuses the page's validated ordinal pair (the strong
 *  step for what was achieved, the light step for what was available) instead of
 *  opening a second colour language; identity comes from each row's own label,
 *  so there is no legend and no colour-only encoding. */
function renderGap(result) {
  const greedy = result.greedy_welfare;
  const optimal = result.optimal_welfare;
  const panel = $('gap-panel');

  if (!isNum(greedy) || !isNum(optimal)) {
    panel.replaceChildren();
    panel.hidden = true;
    $('gap-note').hidden = true;
    return;
  }
  panel.hidden = false;

  const gap = isNum(result.welfare_gap) ? result.welfare_gap : optimal - greedy;
  const max = Math.max(greedy, optimal, 1);

  const hero = h('div', 'gap-hero');
  hero.append(h('span', 'gap-figure', gap > 0 ? `−${fmt(gap)}` : '0'));
  hero.append(h('span', 'gap-caption', gap > 0
    ? 'welfare the greedy rule left on the table'
    : 'greedy found the best allocation here'));
  panel.replaceChildren(hero);

  const bars = h('div', 'gap-bars');
  const row = (label, amount, cls) => {
    const line = h('div', 'gap-row');
    line.append(h('span', 'gap-label', label));

    const plot = h('div', 'gap-plot');
    const bar = h('span', `gap-bar ${cls}`);
    bar.style.width = `${(amount / max) * 100}%`;
    bar.setAttribute('aria-hidden', 'true');   // the value is the cell right beside it
    plot.append(bar);
    line.append(plot);

    line.append(h('span', 'gap-value', fmt(amount)));
    bars.append(line);
  };
  row('Greedy took', greedy, 'is-greedy');
  row('Best possible', optimal, 'is-optimal');
  panel.append(bars);

  // Two numbers on one screen that are measured differently will read as a
  // contradiction unless the difference is stated. `welfare` scores what the
  // items were worth to whoever got them; these two total the submitted bids,
  // which is what the solver actually maximises. They agree only under truthful
  // bidding — which is exactly what one of these two mechanisms does not get.
  const note = $('gap-note');
  note.hidden = false;
  note.textContent = 'These two totals are in submitted bids — the quantity the solver maximises. ' +
    'The Outcome card scores welfare in private values instead, so the two agree only when bidding is truthful.';
}

function renderBundles(trace, rows) {
  const result = trace.result || {};

  const table = h('table', 'data-table bundle-table');
  const headRow = h('tr');
  for (const label of ['Bidder', 'Bundle won', 'Pays', 'Utility']) {
    const th = h('th', null, label);
    th.scope = 'col';
    headRow.append(th);
  }
  const thead = h('thead');
  thead.append(headRow);
  table.append(thead);

  const tbody = h('tbody');
  for (const row of rows) {
    const tr = h('tr');
    tr.append(h('td', null, star(row.id, row.id === result.winner)));
    tr.append(h('td', 'items-cell', row.items.join(' + ')));
    tr.append(h('td', null, fmt(row.pays)));
    tr.append(h('td', isNum(row.utility) && row.utility < 0 ? 'neg' : null, fmt(row.utility)));
    tbody.append(tr);
  }
  table.append(tbody);
  $('bundle-table').replaceChildren(table);

  // What nobody won is as much the outcome as what somebody did — an item left
  // unsold is welfare that simply did not happen.
  const universe = itemUniverse(trace);
  const taken = new Set(rows.flatMap((r) => r.items));
  const unsold = universe.filter((item) => !taken.has(item));
  $('unsold-note').textContent = unsold.length
    ? `Unsold: ${unsold.join(', ')} — nobody who bid for ${unsold.length === 1 ? 'it' : 'them'} could be fitted into the allocation.`
    : 'Every item found a buyer.';
}

/** The engine publishes the item universe on the step that establishes it; fall
 *  back to the bids themselves so the unsold line still works if it ever stops. */
function itemUniverse(trace) {
  for (const step of trace.steps) {
    if (Array.isArray(step.state && step.state.items)) return step.state.items;
  }
  const seen = new Set();
  for (const bid of trace.bidders || []) for (const item of bid.items || []) seen.add(item);
  return [...seen];
}

// =================================================================== submit =

async function runPackageAuction() {
  const bids = readPackages();
  const problem = validatePackages(bids);
  if (problem) { showError(problem); return; }

  const button = $('run');
  button.disabled = true;
  button.textContent = 'Running…';
  try {
    const trace = await api('/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mechanism: $('mechanism').value, packages: bids, params: readParams() }),
    });
    if (!trace || !Array.isArray(trace.steps) || trace.steps.length === 0) {
      throw new Error('The server returned a trace with no steps.');
    }
    app.trace = trace;
    app.scale = traceScale(trace);
    render(0);
  } catch (err) {
    showError(err.message);
  } finally {
    button.disabled = false;
    button.textContent = 'Run auction';
  }
}

// ===================================================================== boot =

window.packagesExt = {
  active() {
    return currentSpec().input_kind === 'package';
  },

  boot() {
    for (const seed of DEFAULT_PACKAGES) addPackageRow(seed);
    $('add-package').addEventListener('click', () => {
      addPackageRow({ bidder: 'A', items: '', value: 10, bid: 10 });
    });
    applyMode();
  },

  onMechanismChange() {
    applyMode();
  },

  runAuction: runPackageAuction,

  /** Called at the end of every app.js render, for every trace. */
  onRender(trace, index) {
    const card = $('packages-card');
    if (!card) return;

    const rows = bundleRows(trace);
    // Utilities settle only at the end, so this appears exactly when the Outcome
    // card does — one rule for "the auction is over", not two. Same choice the
    // slot ladder makes, for the same reason.
    // ponytail: the allocation does not fill in bundle by bundle as the per-winner
    // price steps run. Ceiling: the arithmetic for one bundle is legible in the
    // step panel, not here. Upgrade: draw rows from step state, utility last.
    const finished = index === trace.steps.length - 1;

    card.hidden = !rows || !finished;
    if (card.hidden) return;

    renderGap(trace.result || {});
    renderBundles(trace, rows);
  },
};
