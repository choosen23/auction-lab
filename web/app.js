'use strict';

/* ===========================================================================
   Auction walkthrough — the renderer.

   THE ONE RULE: nothing in this file knows what the mechanisms are.

   There is no `if (mechanism === ...)` anywhere below, and there must never be.
   Every mechanism-specific thing on screen is read out of the trace the Python
   engine emitted:

     step.label      -> the stage list entry and the step heading
     step.detail     -> the sentence under it
     step.formula    -> the monospace line (already substituted; not LaTeX)
     step.highlight  -> which bidders to emphasise, and the stage name
     step.state      -> a FULL snapshot, rendered by the SHAPE of each value:

         bids       (id -> number)  the bars
         payments   (id -> number)  a notch on the bid bar
         any number                 a vertical reference line at that x
         any string that is an id   marks that bidder
         anything else              a text chip

   So `reserve`, `price`, `clock` all draw as reference lines without being
   named here, and a sixth mechanism that emits `budget: 40` gets a line too.

   render(i) redraws everything for step i from scratch. The transport controls
   only move the index. There is no incremental mutation anywhere.
   =========================================================================== */

// ---------------------------------------------------------------- helpers --

const $ = (id) => document.getElementById(id);
const SVG_NS = 'http://www.w3.org/2000/svg';

/** Build an HTML element. `text` always goes in via textContent — trace data
 *  (bidder ids, labels, error strings) is untrusted and never becomes markup. */
function h(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function s(tag, attrs, text) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v !== null && v !== undefined) node.setAttribute(k, String(v));
  }
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

const isNum = (v) => typeof v === 'number' && Number.isFinite(v);
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

/** 95 -> "95", 41.5 -> "41.5", 1200 -> "1,200". */
function fmt(v) {
  if (!isNum(v)) return String(v);
  const r = Math.round(v * 1000) / 1000;
  return Number.isInteger(r) ? r.toLocaleString() : String(r);
}

/** Bidder ids are free text from the user; keep the chart gutter from blowing up. */
// ponytail: truncate long ids instead of measuring and wrapping them. Ceiling:
// ids past 10 chars read as "Contestan…" on the chart. Upgrade path: measure with
// getComputedTextLength and widen the left gutter to the longest label.
function short(id, max = 10) {
  const str = String(id);
  return str.length > max ? str.slice(0, max - 1) + '…' : str;
}

// ------------------------------------------------------------------ state --

const app = {
  registry: null,   // GET /mechanisms, verbatim
  trace: null,      // POST /run, verbatim
  index: 0,         // which step is on screen
  scale: null,      // { max, step } — computed once per trace so the axis never jumps
  timer: null,      // auto-play interval
};

const PLAY_MS = 1300;

// ================================================================= transport

function render(index) {
  const trace = app.trace;
  if (!trace) return;

  app.index = clamp(index, 0, trace.steps.length - 1);
  const step = trace.steps[app.index];

  renderStageList(trace, app.index);
  renderStep(trace, step, app.index);
  renderChart(trace, step);
  renderTableView(trace, step);
  renderResult(trace, app.index);
  renderTransport(trace, app.index);
}

function goTo(index) { stopPlaying(); render(index); }

function startPlaying() {
  if (!app.trace || app.timer) return;
  if (app.index >= app.trace.steps.length - 1) app.index = -1; // replay from the top
  app.timer = setInterval(() => {
    if (!app.trace || app.index >= app.trace.steps.length - 1) { stopPlaying(); renderTransport(app.trace, app.index); return; }
    render(app.index + 1);
  }, PLAY_MS);
  render(app.index + 1);
}

function stopPlaying() {
  if (app.timer) { clearInterval(app.timer); app.timer = null; }
}

function renderTransport(trace, index) {
  const last = trace ? trace.steps.length - 1 : 0;
  $('btn-prev').disabled = !trace || index <= 0;
  $('btn-next').disabled = !trace || index >= last;
  $('btn-reset').disabled = !trace;
  $('btn-play').disabled = !trace;
  $('btn-play').textContent = app.timer ? '⏸ Pause' : '▶ Play';
}

// ============================================================== step panel =

function renderStageList(trace, index) {
  const list = $('stage-list');
  list.replaceChildren();

  trace.steps.forEach((step, i) => {
    const li = h('li');
    li.className = i === index ? 'is-current' : (i < index ? 'is-done' : 'is-todo');

    const btn = h('button');
    btn.type = 'button';
    if (i === index) btn.setAttribute('aria-current', 'step');
    // Done stages get a check; the current one an arrow; the rest their number.
    btn.append(h('span', 'marker', i < index ? '✓' : (i === index ? '▶' : String(i + 1))));
    btn.append(h('span', 'stage-name', step.label));
    btn.addEventListener('click', () => goTo(i));

    li.append(btn);
    list.append(li);
  });
}

function renderStep(trace, step, index) {
  $('step-count').textContent = `Step ${index + 1} of ${trace.steps.length}`;
  $('step-h').textContent = step.label || '';
  $('step-detail').textContent = step.detail || '';

  const stage = step.highlight && step.highlight.stage;
  const chip = $('step-stage');
  chip.textContent = stage || '';
  chip.hidden = !stage;

  const formula = $('step-formula');
  formula.textContent = step.formula || '';
  formula.hidden = !step.formula;
}

// =================================================================== chart =

const CHART = {
  width: 760,      // logical units; the SVG scales to its container via viewBox
  padLeft: 84,     // bidder name gutter
  padRight: 62,    // room for the value label at a full-length bar's tip
  padTop: 46,      // two staggered label rows for the reference lines
  padBottom: 36,   // x axis
  rowH: 46,
  barH: 15,
  barGap: 2,       // the surface gap between a bidder's two bars
  radius: 4,       // rounded data-end
  ticks: 5,
};

/** Round the axis up to a clean maximum with clean intervals. */
function niceScale(max, targetTicks) {
  if (!(max > 0)) return { max: 1, step: 1 };
  const rough = max / targetTicks;
  const magnitude = Math.pow(10, Math.floor(Math.log10(rough)));
  const norm = rough / magnitude;
  const step = (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 2.5 ? 2.5 : norm <= 5 ? 5 : 10) * magnitude;
  return { max: Math.ceil(max / step) * step, step };
}

/** The axis is computed once for the whole trace, so bars never rescale between
 *  steps — the point of stepping is watching things change, not the axis move. */
// ponytail: one axis for the whole trace, sized to the largest number anywhere in
// it. Ceiling: a trace containing a far-out value (a descending clock that opens at
// twice the top bid) squeezes the bidder bars into the left half of the plot.
// Upgrade path: give the outlier rules their own inset strip above the plot, or clip
// the axis and mark the rule as off-scale.
function traceScale(trace) {
  const seen = [0];
  for (const b of trace.bidders) seen.push(b.value, b.bid);
  for (const step of trace.steps) {
    for (const value of Object.values(step.state || {})) {
      if (isNum(value)) seen.push(value);
      else if (value && typeof value === 'object' && !Array.isArray(value)) {
        for (const inner of Object.values(value)) if (isNum(inner)) seen.push(inner);
      }
    }
  }
  return niceScale(Math.max(...seen), CHART.ticks);
}

/** Any state value that is a plain string naming a bidder marks that bidder.
 *  `winner` is the one the contract guarantees; a future mechanism's
 *  `challenger: "B"` would be picked up the same way, with no change here. */
function markedBidders(state, bids) {
  return new Set(Object.values(state).filter(
    (v) => typeof v === 'string' && Object.prototype.hasOwnProperty.call(bids, v)));
}

const star = (id, isMarked) => (isMarked ? `★ ${id}` : String(id));

/** A bar with a rounded data-end and a square baseline. */
function barPath(x0, y, width, height, radius) {
  if (width <= 0.5) return `M${x0} ${y} L${x0} ${y + height}`;
  const r = Math.min(radius, width, height / 2);
  return `M${x0} ${y} H${x0 + width - r} Q${x0 + width} ${y} ${x0 + width} ${y + r}` +
         ` V${y + height - r} Q${x0 + width} ${y + height} ${x0 + width - r} ${y + height} H${x0} Z`;
}

function renderChart(trace, step) {
  const host = $('chart');
  host.replaceChildren();

  const state = step.state || {};
  const bids = state.bids || {};
  const payments = state.payments || null;
  const highlighted = new Set((step.highlight && step.highlight.bidders) || []);
  const anyHighlight = highlighted.size > 0;
  // When a step highlights everybody (or nobody), emphasis has nothing to say —
  // banding every row just adds ink.
  const banded = anyHighlight && highlighted.size < trace.bidders.length;

  const rows = trace.bidders;                        // setup order, never rank order:
  const scale = app.scale;                           // a row must not move under the reader
  const plotW = CHART.width - CHART.padLeft - CHART.padRight;
  const plotBottom = CHART.padTop + rows.length * CHART.rowH;
  const height = plotBottom + CHART.padBottom;
  const x = (v) => CHART.padLeft + clamp(v / scale.max, 0, 1) * plotW;

  const svg = s('svg', {
    viewBox: `0 0 ${CHART.width} ${height}`,
    preserveAspectRatio: 'xMinYMin meet',
    role: 'img',
    'aria-label': `Values and bids for ${rows.length} bidders at stage "${step.label}". ` +
                  'The same numbers are in the table view below.',
  });

  // --- gridlines + x axis ---------------------------------------------------
  const axis = s('g');
  for (let v = 0; v <= scale.max + 1e-9; v += scale.step) {
    axis.append(s('line', { class: 'grid-line', x1: x(v), y1: CHART.padTop - 8, x2: x(v), y2: plotBottom }));
    axis.append(s('text', { class: 'tick-label', x: x(v), y: plotBottom + 18, 'text-anchor': 'middle' }, fmt(v)));
  }
  axis.append(s('line', { class: 'axis-line', x1: CHART.padLeft, y1: plotBottom, x2: CHART.padLeft + plotW, y2: plotBottom }));
  svg.append(axis);

  // --- reference lines: every numeric scalar in the snapshot ----------------
  // `reserve`, `price`, `clock` and anything a future mechanism invents all land
  // here purely because they are numbers. Zeros are skipped: a rule at x=0 just
  // redraws the baseline.
  const rules = Object.entries(state)
    .filter(([, v]) => isNum(v) && v !== 0)
    .sort((a, b) => a[1] - b[1]);

  rules.forEach(([key, value], i) => {
    const rx = x(value);
    const g = s('g');
    g.append(s('line', { class: 'rule-line', x1: rx, y1: CHART.padTop - 14, x2: rx, y2: plotBottom }));
    // ponytail: labels alternate between two rows so two nearby rules don't collide.
    // Ceiling: three rules within a few pixels still overlap. Upgrade: measure the
    // rendered labels and pack them greedily.
    const labelY = CHART.padTop - 30 + (i % 2) * 15;
    const anchor = rx < CHART.padLeft + 34 ? 'start' : (rx > CHART.width - 44 ? 'end' : 'middle');
    g.append(s('text', { class: 'rule-label', x: rx, y: labelY, 'text-anchor': anchor }, `${key} ${fmt(value)}`));
    svg.append(g);
  });

  // --- one group per bidder -------------------------------------------------
  const marked = markedBidders(state, bids);

  rows.forEach((bidder, r) => {
    const id = bidder.id;
    const bid = isNum(bids[id]) ? bids[id] : bidder.bid;
    const dim = anyHighlight && !highlighted.has(id);

    const rowTop = CHART.padTop + r * CHART.rowH;
    const barsTop = rowTop + (CHART.rowH - (2 * CHART.barH + CHART.barGap)) / 2;
    const valueY = barsTop;
    const bidY = barsTop + CHART.barH + CHART.barGap;

    const g = s('g', { class: `row${dim ? ' is-dim' : ''}${marked.has(id) ? ' is-marked' : ''}` });

    if (!dim && banded) {
      g.append(s('rect', { class: 'row-band', x: 6, y: rowTop + 2, width: CHART.width - 12, height: CHART.rowH - 4, rx: 7 }));
    }

    g.append(s('text', { class: 'name-label', x: CHART.padLeft - 12, y: rowTop + CHART.rowH / 2 + 4, 'text-anchor': 'end' },
                star(short(id), marked.has(id))));

    g.append(s('path', { class: 'bar-value', d: barPath(x(0), valueY, x(bidder.value) - x(0), CHART.barH, CHART.radius) }));
    g.append(s('path', { class: 'bar-bid', d: barPath(x(0), bidY, x(bid) - x(0), CHART.barH, CHART.radius) }));

    g.append(tipLabel(x(bidder.value), valueY, bidder.value));
    g.append(tipLabel(x(bid), bidY, bid));

    // Payment notch: what they actually hand over, marked on what they bid.
    if (payments && isNum(payments[id]) && payments[id] > 0) {
      const px = x(payments[id]);
      g.append(s('line', { class: 'pay-gap', x1: px, y1: bidY - 4, x2: px, y2: bidY + CHART.barH + 4 }));
      g.append(s('line', { class: 'pay-tick', x1: px, y1: bidY - 4, x2: px, y2: bidY + CHART.barH + 4 }));
    }

    // Hit target: the whole row band, comfortably past the 24px minimum.
    const hit = s('rect', {
      class: 'hit', x: 0, y: rowTop, width: CHART.width, height: CHART.rowH,
      tabindex: '0', role: 'button',
      'aria-label': `${id}: value ${fmt(bidder.value)}, bid ${fmt(bid)}` +
                    (payments && isNum(payments[id]) ? `, pays ${fmt(payments[id])}` : ''),
    });
    const show = (ev) => showTooltip(ev, bidder, bid, payments, marked.has(id));
    hit.addEventListener('pointermove', show);
    hit.addEventListener('pointerenter', show);
    hit.addEventListener('focus', show);
    hit.addEventListener('pointerleave', hideTooltip);
    hit.addEventListener('blur', hideTooltip);
    g.append(hit);

    svg.append(g);
  });

  host.append(svg);
  renderChips(state);
  renderLegend({ hasRules: rules.length > 0, hasPayments: !!payments, hasMarked: marked.size > 0 });
}

/** The value at the bar's tip — outside if it fits, tucked inside if it doesn't. */
function tipLabel(tipX, barY, value) {
  const text = fmt(value);
  // ponytail: label width is estimated from the character count rather than
  // measured. Ceiling: an unusual font could make a very long number overhang by a
  // few px. Upgrade: insert, call getComputedTextLength, then place.
  const estimated = text.length * 6.6;
  const outside = tipX + 7 + estimated <= CHART.width - 4;
  return s('text', {
    class: 'bar-label',
    x: outside ? tipX + 7 : tipX - 7,
    y: barY + CHART.barH - 3,
    'text-anchor': outside ? 'start' : 'end',
  }, text);
}

/** Everything in the snapshot that isn't a bar or a line, as plain text. */
function renderChips(state) {
  const host = $('state-chips');
  host.replaceChildren();

  for (const [key, value] of Object.entries(state)) {
    if (key === 'bids' || key === 'payments') continue;   // already drawn, and in the table
    let text;
    if (value === null) text = '—';
    else if (Array.isArray(value)) text = value.length ? value.join(', ') : '—';
    else if (isNum(value)) text = fmt(value);
    else if (typeof value === 'object') continue;         // maps other than the two above: table's job
    else text = String(value);

    const chip = h('span', 'chip');
    chip.append(h('span', 'k', `${key}: `));
    chip.append(document.createTextNode(text));
    host.append(chip);
  }
}

function renderLegend({ hasRules, hasPayments, hasMarked }) {
  const host = $('legend');
  host.replaceChildren();

  const entry = (keyNode, label) => {
    const li = h('li');
    li.append(keyNode);
    li.append(h('span', null, label));
    host.append(li);
  };

  const swatch = (cssVar) => {
    const node = h('span', 'swatch');
    node.style.background = `var(${cssVar})`;
    return node;
  };

  entry(swatch('--series-value'), 'value (what it is worth to them)');
  entry(swatch('--series-bid'), 'bid (what they submitted)');
  if (hasRules) entry(h('span', 'key-line'), 'reference line, labelled above');
  if (hasPayments) entry(h('span', 'key-tick'), 'payment');
  if (hasMarked) entry(h('span', 'key-glyph', '★'), 'winner');
}

// ---------------------------------------------------------------- tooltip --

function showTooltip(event, bidder, bid, payments, isWinner) {
  const tip = $('tooltip');
  tip.replaceChildren();

  const head = h('div', 'tt-head', isWinner ? `★ ${bidder.id}` : bidder.id);
  tip.append(head);

  const row = (cssVar, key, value) => {
    const line = h('div', 'tt-row');
    if (cssVar) {
      const stroke = h('span', 'tt-line');
      stroke.style.color = `var(${cssVar})`;
      line.append(stroke);
    }
    line.append(h('span', 'tt-key', key));
    line.append(h('span', 'tt-val', fmt(value)));
    tip.append(line);
  };

  row('--series-value', 'value', bidder.value);
  row('--series-bid', 'bid', bid);
  if (payments && isNum(payments[bidder.id])) row(null, 'pays', payments[bidder.id]);

  tip.hidden = false;

  // Anchor to the pointer, or to the mark itself on keyboard focus.
  const box = tip.getBoundingClientRect();
  let left, top;
  if (event.clientX || event.clientY) {
    left = event.clientX + 14;
    top = event.clientY + 14;
  } else {
    const target = event.target.getBoundingClientRect();
    left = target.left + 24;
    top = target.bottom + 6;
  }
  tip.style.left = `${clamp(left, 6, window.innerWidth - box.width - 6)}px`;
  tip.style.top = `${clamp(top, 6, window.innerHeight - box.height - 6)}px`;
}

function hideTooltip() { $('tooltip').hidden = true; }

// ============================================================= table views =

/** Build a table. A cell is either a string or a [text, className] pair. */
function buildTable(headers, rows) {
  const table = h('table', 'data-table');

  const headRow = h('tr');
  for (const label of headers) {
    const th = h('th', null, label);
    th.scope = 'col';
    headRow.append(th);
  }
  const thead = h('thead');
  thead.append(headRow);
  table.append(thead);

  const tbody = h('tbody');
  for (const cells of rows) {
    const tr = h('tr');
    for (const cell of cells) {
      const [text, className] = Array.isArray(cell) ? cell : [cell, null];
      tr.append(h('td', className, text));
    }
    tbody.append(tr);
  }
  table.append(tbody);
  return table;
}

/** The colour-free twin of the chart: every number the chart draws, in text. */
function renderTableView(trace, step) {
  const state = step.state || {};
  const bids = state.bids || {};
  const payments = state.payments || null;
  const marked = markedBidders(state, bids);

  const headers = ['Bidder', 'Value', 'Bid', 'Shading'];
  if (payments) headers.push('Pays');

  const rows = trace.bidders.map((bidder) => {
    const bid = isNum(bids[bidder.id]) ? bids[bidder.id] : bidder.bid;
    const cells = [star(bidder.id, marked.has(bidder.id)), fmt(bidder.value),
                   fmt(bid), fmt(bidder.value - bid)];
    if (payments) cells.push(isNum(payments[bidder.id]) ? fmt(payments[bidder.id]) : '—');
    return cells;
  });

  $('chart-table').replaceChildren(buildTable(headers, rows));
}

// ================================================================== result =

function renderResult(trace, index) {
  const card = $('result-card');
  const body = $('result-body');
  const finished = index === trace.steps.length - 1;

  card.hidden = !finished;
  if (!finished) return;

  const result = trace.result || {};
  body.replaceChildren();

  const stats = h('div', 'stat-row');
  const stat = (label, value, cls) => {
    const box = h('div', 'stat');
    box.append(h('span', 'stat-label', label));
    box.append(h('span', `stat-value${cls ? ' ' + cls : ''}`, value));
    stats.append(box);
  };

  const noSale = result.winner === null || result.winner === undefined;
  if (noSale) {
    stat('Winner', 'No sale', 'muted');
  } else {
    stat('Winner', result.winner, 'hero');
    stat('Price', fmt(result.price));
  }
  stat('Revenue', fmt(result.revenue));
  stat('Welfare', fmt(result.welfare));
  body.append(stats);

  const badge = h('p', `badge ${result.efficient ? 'is-good' : 'is-bad'}`);
  badge.append(h('span', null, result.efficient ? '✓' : '✗'));
  badge.append(h('span', null, result.efficient
    ? 'Efficient — the item went to the bidder who valued it most.'
    : 'Not efficient — the item did not go to the bidder who valued it most.'));
  body.append(badge);

  if (noSale) {
    body.append(h('p', 'note', 'No sale — the reserve was not met, so the seller kept the item.'));
  }

  body.append(resultTable(trace, result));
}

function resultTable(trace, result) {
  const payments = result.payments || {};
  const utilities = result.utilities || {};

  const rows = trace.bidders.map((bidder) => {
    const utility = utilities[bidder.id];
    return [
      star(bidder.id, bidder.id === result.winner),
      fmt(bidder.value),
      fmt(bidder.bid),
      isNum(payments[bidder.id]) ? fmt(payments[bidder.id]) : '—',
      // A negative utility carries its own minus sign, so the colour is never the
      // only thing saying "this bidder lost money".
      [isNum(utility) ? fmt(utility) : '—', isNum(utility) && utility < 0 ? 'neg' : null],
    ];
  });

  const wrap = h('div', 'scroll-x');
  wrap.append(buildTable(['Bidder', 'Value', 'Bid', 'Pays', 'Utility'], rows));
  return wrap;
}

// ============================================================= setup panel =

const DEFAULT_BIDDERS = [
  { id: 'A', value: 100, bid: 95 },
  { id: 'B', value: 72, bid: 72 },
  { id: 'C', value: 41, bid: 41 },
];

function addBidderRow(bidder) {
  const tbody = $('bidder-rows');
  const tr = h('tr');

  const cell = (field, type, value) => {
    const td = h('td');
    const input = h('input');
    input.type = type;
    input.value = value;
    input.dataset.field = field;
    input.setAttribute('aria-label', field);
    if (type === 'number') { input.step = 'any'; input.min = '0'; }
    td.append(input);
    tr.append(td);
  };

  cell('id', 'text', bidder.id);
  cell('value', 'number', bidder.value);
  cell('bid', 'number', bidder.bid);

  const td = h('td');
  const remove = h('button', 'remove', '✕');
  remove.type = 'button';
  remove.title = `Remove ${bidder.id}`;
  remove.addEventListener('click', () => {
    if (tbody.children.length <= 1) return;   // an auction needs at least one bidder
    tr.remove();
  });
  td.append(remove);
  tr.append(td);

  tbody.append(tr);
}

function readBidders() {
  return [...$('bidder-rows').children].map((tr) => {
    const get = (field) => tr.querySelector(`[data-field="${field}"]`).value;
    return {
      id: get('id').trim(),
      value: Number(get('value')),
      bid: Number(get('bid')),
    };
  });
}

function validateBidders(bidders) {
  if (bidders.length === 0) return 'Add at least one bidder.';
  const seen = new Set();
  for (const b of bidders) {
    if (!b.id) return 'Every bidder needs a name.';
    if (seen.has(b.id)) return `Two bidders are both called "${b.id}". Names must be unique.`;
    seen.add(b.id);
    if (!isNum(b.value) || b.value < 0) return `${b.id} needs a value of 0 or more.`;
    if (!isNum(b.bid) || b.bid < 0) return `${b.id} needs a bid of 0 or more.`;
  }
  return null;
}

function nextBidderId() {
  const taken = new Set(readBidders().map((b) => b.id));
  for (const code of 'ABCDEFGHIJKLMNOPQRSTUVWXYZ') if (!taken.has(code)) return code;
  return `B${taken.size + 1}`;
}

/** Every mechanism and every parameter input comes from GET /mechanisms. */
function renderMechanismOptions(registry) {
  const select = $('mechanism');
  select.replaceChildren();
  for (const [name, spec] of Object.entries(registry)) {
    const option = h('option', null, spec.label || name);
    option.value = name;
    select.append(option);
  }
}

function renderParams(spec) {
  const host = $('params');
  host.replaceChildren();
  $('mech-desc').textContent = (spec && spec.description) || '';

  for (const [key, param] of Object.entries((spec && spec.params) || {})) {
    const field = h('div', 'field');
    const inputId = `param-${key}`;

    const label = h('label', null, param.label || key);
    label.htmlFor = inputId;
    field.append(label);

    const numeric = param.type === 'number' || param.type === 'integer';
    const input = h('input');
    input.id = inputId;
    input.dataset.param = key;
    if (param.type === 'boolean') {
      input.type = 'checkbox';
      input.checked = !!param.default;
    } else {
      input.type = numeric ? 'number' : 'text';
      if (numeric) input.step = param.type === 'integer' ? '1' : 'any';
      if (isNum(param.min)) input.min = String(param.min);
      if (isNum(param.max)) input.max = String(param.max);
      // A null default means "the engine will pick one" — leave the box empty and
      // omit the key from the request so the server's own default applies.
      input.value = param.default === null || param.default === undefined ? '' : String(param.default);
      if (param.default === null || param.default === undefined) input.placeholder = 'auto';
    }
    field.append(input);

    if (param.description) field.append(h('p', 'param-desc', param.description));
    host.append(field);
  }
}

function readParams() {
  const params = {};
  for (const input of $('params').querySelectorAll('[data-param]')) {
    const key = input.dataset.param;
    if (input.type === 'checkbox') { params[key] = input.checked; continue; }
    const raw = input.value.trim();
    if (raw === '') continue;
    params[key] = input.type === 'number' ? Number(raw) : raw;
  }
  return params;
}

// ============================================================== networking =

async function api(path, options) {
  let response;
  try {
    response = await fetch(path, options);
  } catch {
    throw new Error(`Could not reach the server at ${path}. Is it running?`);
  }

  const text = await response.text();
  let body = null;
  try { body = text ? JSON.parse(text) : null; } catch { /* server sent something that isn't JSON */ }

  if (!response.ok) {
    const detail = (body && body.error) || text.trim() || `HTTP ${response.status}`;
    throw new Error(detail);
  }
  if (body === null) throw new Error(`The server sent an unreadable response from ${path}.`);
  return body;
}

function showError(message) {
  const box = $('setup-error');
  box.textContent = message;
  box.hidden = false;
}

function clearError() { $('setup-error').hidden = true; }

async function runAuction() {
  stopPlaying();
  clearError();

  const bidders = readBidders();
  const problem = validateBidders(bidders);
  if (problem) { showError(problem); return; }

  const button = $('run');
  button.disabled = true;
  button.textContent = 'Running…';
  try {
    const trace = await api('/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mechanism: $('mechanism').value, bidders, params: readParams() }),
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

// =================================================================== boot ==

function wireControls() {
  $('mechanism').addEventListener('change', () => {
    renderParams(app.registry[$('mechanism').value]);
    clearError();
  });

  $('add-bidder').addEventListener('click', () => {
    const id = nextBidderId();
    addBidderRow({ id, value: 50, bid: 50 });
  });

  $('run').addEventListener('click', runAuction);
  $('btn-prev').addEventListener('click', () => goTo(app.index - 1));
  $('btn-next').addEventListener('click', () => goTo(app.index + 1));
  $('btn-reset').addEventListener('click', () => goTo(0));
  $('btn-play').addEventListener('click', () => {
    if (app.timer) { stopPlaying(); renderTransport(app.trace, app.index); } else { startPlaying(); }
  });

  document.addEventListener('keydown', (event) => {
    const tag = event.target.tagName;
    if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return;
    if (!app.trace) return;

    switch (event.key) {
      case 'ArrowLeft':  event.preventDefault(); goTo(app.index - 1); break;
      case 'ArrowRight': event.preventDefault(); goTo(app.index + 1); break;
      case 'Home':       event.preventDefault(); goTo(0); break;
      case 'End':        event.preventDefault(); goTo(app.trace.steps.length - 1); break;
      case ' ':
        if (tag === 'BUTTON') return;   // let space activate the focused button
        event.preventDefault();
        if (app.timer) { stopPlaying(); renderTransport(app.trace, app.index); } else { startPlaying(); }
        break;
      default: break;
    }
  });

  window.addEventListener('blur', hideTooltip);
}

async function boot() {
  for (const bidder of DEFAULT_BIDDERS) addBidderRow(bidder);
  wireControls();
  renderTransport(null, 0);

  try {
    app.registry = await api('/mechanisms');
  } catch (err) {
    $('run').disabled = true;
    showError(`${err.message} The bidder setup is still editable, but nothing can be run.`);
    return;
  }

  if (!app.registry || Object.keys(app.registry).length === 0) {
    $('run').disabled = true;
    showError('The server reported no mechanisms.');
    return;
  }

  renderMechanismOptions(app.registry);
  renderParams(app.registry[$('mechanism').value]);
  await runAuction();   // the default setup is meant to be useful the moment the page loads
}

boot();
