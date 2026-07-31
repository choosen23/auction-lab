'use strict';

/* ===========================================================================
   The slot ladder — everything that appears when an auction sells more than
   one thing.

   THE ONE RULE from app.js still holds: no mechanism name appears below. The
   ladder is drawn because `trace.result.allocation` is present, and for no
   other reason. A single-winner trace has no allocation, onRender() leaves the
   card hidden and returns, and the page is exactly the phase 2 page — same
   step panel, same chart, same Outcome card, nothing restyled.

   SLOT INDICES STAY 0-BASED. The engine's own step prose and formulas say
   "slot 0"; renumbering them "position 1" here would put the ladder and the
   trace text in visible disagreement in front of the learner. Round numbers in
   series.js are +1'd because the API is the only thing that ever says "round
   0"; slots are different — the reader is shown "slot 0" by the engine itself.

   ALLOCATION IS READ IN ITERATION ORDER. The engine lists winners best-first,
   and that ordering is the contract that defines which bidder `result.winner`
   names. Sorting the rungs here — by gain, by slot, by anything — would be a
   second, disagreeing opinion about who came first.

   app.js exposes one seam (window.positionsExt.onRender) and its helpers as
   globals; this file uses those and adds nothing to app.js's state.
   =========================================================================== */

// The bar reuses the bidder chart's two-step blue ordinal ramp rather than
// opening a second colour language on one page: the light step is the gross
// quantity, the strong step is the committed one. See the style.css header.
const LADDER_SEGMENTS = [
  { key: 'pays', cssVar: '--series-bid', label: 'pays — this goes to the seller' },
  { key: 'utility', cssVar: '--series-value', label: 'utility — what the holder keeps' },
];

/** The snapshot the engine finished on: `ctrs` and `prices` accumulate as the
 *  auction proceeds, so the last step is the only one guaranteed to have them. */
function finalState(trace) {
  const step = trace.steps[trace.steps.length - 1];
  return (step && step.state) || {};
}

/** One rung per allocated bidder, or null when this trace allocated nothing —
 *  which is how every single-winner mechanism arrives here. */
function ladderRows(trace) {
  const result = (trace && trace.result) || {};
  const allocation = result.allocation;
  if (!allocation || typeof allocation !== 'object' || Array.isArray(allocation)) return null;

  // A combinatorial auction publishes an `allocation` too, but its values are the
  // items a bidder won, not a slot index. The ladder is the numeric-slot view;
  // web/packages.js draws the bundle view. The *shape* decides which one runs —
  // reading the mechanism name here would break the one rule this file opens with.
  const held = Object.values(allocation);
  if (!held.length || !held.every((slot) => isNum(slot))) return null;

  const state = finalState(trace);
  const ctrs = Array.isArray(state.ctrs) ? state.ctrs : [];
  const prices = state.prices && typeof state.prices === 'object' ? state.prices : {};
  const payments = result.payments || {};
  const utilities = result.utilities || {};
  const gains = result.gains || {};

  return Object.entries(allocation).map(([id, slot]) => {
    const ctr = isNum(ctrs[slot]) ? ctrs[slot] : null;
    const pays = isNum(payments[id]) ? payments[id] : 0;
    const gain = isNum(gains[id]) ? gains[id] : 0;
    const utility = isNum(utilities[id]) ? utilities[id] : gain - pays;
    // The engine publishes the per-click price when its rule has one to name;
    // otherwise it is the payment spread back over the clicks that slot buys,
    // which is the same number wherever both are present. A slot that draws no
    // clicks has no per-click price at all rather than a division by zero.
    const perClick = isNum(prices[id]) ? prices[id]
      : (isNum(ctr) && ctr > 0 ? pays / ctr : null);
    return { id, slot, ctr, perClick, pays, gain, utility };
  });
}

/** A number, or an em dash — never the string "null" in front of a reader. */
const ladderCell = (v) => (isNum(v) ? fmt(v) : '—');

// ponytail: a slot nobody won gets a sentence, not an empty rung. Rungs come
// from the allocation and the allocation is the only thing whose order is
// contractual; inventing rows for the gaps would mean sorting by slot index
// instead, which is a second opinion about the order. Ceiling: you can see
// that two slots went unsold but not which two. Upgrade path: place the rungs
// by index once the engine names the unfilled slots itself.
function ladderCaption(rows, trace) {
  const ctrs = finalState(trace).ctrs;
  const sold = Array.isArray(ctrs) ? ctrs.length - rows.length : 0;

  let text = 'One rung per slot, in the order the engine filled them. Slot 0 draws the most ' +
             'clicks and every rung below it draws fewer, so the same per-click value is worth ' +
             'less further down. Slots are numbered from 0, exactly as the steps and formulas ' +
             'above number them.';
  if (sold > 0) {
    text += ` ${sold} slot${sold === 1 ? '' : 's'} drew no eligible bid and went unsold.`;
  }
  return text;
}

/** The split bar: what the slot was worth to its holder, cut into what they
 *  handed over and what they kept. Same rungs under any payment rule — only
 *  the cut moves, which is the whole reason this view exists. */
// ponytail: no hover tooltip on the bar. Everything it encodes — the two
// segments and the total — is already a labelled cell in the same table row,
// so a tooltip would repeat the row back at the reader and gate nothing.
// Ceiling: no per-segment readout of "72 of 100" as a percentage. Upgrade
// path: reuse app.js's showTooltip once the bar carries a value the row does
// not already spell out.
// ponytail: a negative utility (a holder who paid more than the slot was worth
// to them) draws as a full pays bar with no utility segment; the minus sign in
// the Utility column is what says so. Ceiling: the ladder cannot show how far
// underwater they went. Upgrade path: a diverging bar centred on zero, once a
// mechanism that can actually overcharge exists.
function ladderBar(row, max) {
  const wrap = h('div', 'ladder-bar');

  const plot = h('div', 'ladder-plot');
  const total = Math.max(row.gain, row.pays);
  if (total > 0 && max > 0) {
    const track = h('div', 'ladder-track');
    track.setAttribute('aria-hidden', 'true');   // every number is a cell in this same row
    track.style.width = `${(total / max) * 100}%`;

    const parts = [['seg-pays', row.pays], ['seg-utility', Math.max(0, row.utility)]]
      .filter(([, value]) => value > 0);
    parts.forEach(([cls, value], i) => {
      const seg = h('span', `${cls}${i === parts.length - 1 ? ' is-end' : ''}`);
      // Grow-proportional rather than percentage widths, so the 2px surface gap
      // between the segments comes out of the bar instead of overflowing it.
      seg.style.flex = `${value} 1 0`;
      track.append(seg);
    });
    plot.append(track);
  }
  wrap.append(plot);

  // The one direct label the ladder carries: the bar's own total at its tip.
  wrap.append(h('span', 'ladder-total', ladderCell(row.gain)));
  return wrap;
}

function renderLadder(trace, rows) {
  const max = Math.max(0, ...rows.map((r) => Math.max(r.gain, r.pays)));

  const table = h('table', 'data-table ladder-table');
  const headRow = h('tr');
  for (const label of ['Slot', 'Bidder', 'CTR', 'Price / click', 'Pays', 'Utility', 'Slot value']) {
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
    tr.append(h('td', 'slot', String(row.slot)));
    tr.append(h('td', null, star(row.id, row.id === (trace.result || {}).winner)));
    tr.append(h('td', null, ladderCell(row.ctr)));
    tr.append(h('td', null, ladderCell(row.perClick)));
    tr.append(h('td', null, ladderCell(row.pays)));
    tr.append(h('td', isNum(row.utility) && row.utility < 0 ? 'neg' : null, ladderCell(row.utility)));

    const barCell = h('td', 'bar');
    barCell.append(ladderBar(row, max));
    tr.append(barCell);

    tbody.append(tr);
  }
  table.append(tbody);

  $('ladder').replaceChildren(table);
  $('ladder-caption').textContent = ladderCaption(rows, trace);

  const legend = $('ladder-legend');
  legend.replaceChildren();
  for (const segment of LADDER_SEGMENTS) {
    const li = h('li');
    const swatch = h('span', 'swatch');
    swatch.style.background = `var(${segment.cssVar})`;
    li.append(swatch);
    li.append(h('span', null, segment.label));
    legend.append(li);
  }
}

// =================================================================== boot ==

window.positionsExt = {
  /** Called at the end of every app.js render, for every trace. */
  onRender(trace, index) {
    const card = $('ladder-card');
    if (!card) return;

    const rows = ladderRows(trace);
    // Utilities are settled only at the end, so the ladder appears exactly when
    // the Outcome card does — one rule for "the auction is over", not two.
    // ponytail: the ladder does not fill in rung by rung as the per-winner
    // payment steps run, even though the steps' own state carries the partial
    // `payments` map. Utility never appears there, so a mid-auction ladder
    // would have a column that is blank until the last step. Ceiling: the
    // arithmetic for one slot is legible in the step panel, not on the ladder.
    // Upgrade path: draw the rungs from step state and leave utility to the end.
    const finished = index === trace.steps.length - 1;

    card.hidden = !rows || rows.length === 0 || !finished;
    if (card.hidden) return;

    renderLadder(trace, rows);
  },
};
