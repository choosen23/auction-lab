'use strict';

/* ===========================================================================
   Repeated rounds — everything that wraps AROUND the phase 1 renderer.

   THE ONE RULE still holds: nothing in this file knows what the mechanisms or
   the strategies are. The strategy dropdown and its parameter inputs are built
   from GET /strategies exactly as app.js builds the mechanism form from
   GET /mechanisms. There is no strategy name anywhere below.

   THE SECOND RULE: a round IS a phase 1 trace. Selecting a round hands that
   round's trace to the UNCHANGED render(stepIndex) in app.js. Nothing here
   draws a step, a stage or an outcome. Running a single auction still goes
   through app.js alone and this file only gets out of the way.

   app.js exposes two seams (window.seriesExt.onBidderRow / .boot) and its
   helpers as globals; this file uses those and adds nothing to app.js's state
   beyond reading and setting app.trace / app.scale when a round is picked.

   Round numbers: the API is 0-based (`record.round`, `converged_round`); every
   number shown to a reader is 1-based, matching "Step 1 of 5" in the step
   panel. The +1 lives at the render boundary and nowhere else.
   =========================================================================== */

// ------------------------------------------------------------------ state --

const series = {
  registry: null,    // GET /strategies, verbatim
  data: null,        // POST /run_series, verbatim
  round: 0,          // which round is on screen
  order: [],         // bidder ids in setup order — this is the colour slot order
  values: {},        // id -> private value (fixed across rounds in this phase)
  scale: null,       // y scale for the bid-path chart
  barScale: null,    // one axis for app.js's bar chart across EVERY round
};

// The server is the authority on this; the input's max and this constant only
// keep an obviously bad request from being sent.
const MAX_ROUNDS = 50;

/** Colour slot for the nth bidder in setup order. Colour follows the entity, so
 *  a bidder keeps its hue no matter who wins or how the bids re-rank. */
// ponytail: the categorical palette stops at eight, and generating a ninth hue is
// exactly the thing that breaks colourblind safety — so bidders past the eighth
// share one muted grey and are told apart in the tooltip and the table view.
// Ceiling: a nine-bidder chart has one identifiable line short of nine. Upgrade
// path: facet into small multiples past eight.
const slotVar = (index) => (index < 8 ? `--cat-${index + 1}` : '--cat-rest');

/** A number, or an em dash — never the string "undefined" in front of a reader. */
const cell = (v) => (isNum(v) ? fmt(v) : '—');

// ==================================================== strategy column (setup)

/** True unless the registry says otherwise. */
// ponytail: the registry does not declare whether a strategy reads the typed bid,
// so this honours an optional `uses_bid` flag if one ever appears, and otherwise
// waits for evidence — after a series runs, a bidder whose round-0 bid differs
// from what was typed plainly ignored it, and its input is dimmed. Ceiling: a
// strategy that ignores the typed bid but happens to reproduce it is not dimmed,
// which is harmless because the number it used was the same either way, and
// nothing dims until the first run. Upgrade path: add `uses_bid` to the
// @strategy decorator and delete the evidence branch below.
const usesTypedBid = (spec) => !(spec && spec.uses_bid === false);

function ensureStrategyHeader() {
  const headRow = document.querySelector('.bidder-table thead tr');
  if (!headRow || headRow.querySelector('[data-strategy-head]')) return;
  const th = h('th', null, 'Strategy');
  th.scope = 'col';
  th.dataset.strategyHead = '';
  headRow.insertBefore(th, headRow.lastElementChild);   // before the remove column
}

function ensureStrategyCell(tr) {
  if (!series.registry || tr.querySelector('[data-strategy]')) return;

  const td = h('td', 'strategy-cell');
  const select = h('select');
  select.dataset.strategy = '';
  select.setAttribute('aria-label', 'strategy');
  td.append(select, h('div', 'row-params'));
  tr.insertBefore(td, tr.lastElementChild);

  // Registry order decides the default; the engine documents the pass-through
  // strategy first, and naming it here is the one thing this file must not do.
  for (const [name, spec] of Object.entries(series.registry)) {
    const option = h('option', null, spec.label || name);
    option.value = name;
    select.append(option);
  }

  select.addEventListener('change', () => {
    syncStrategyRow(tr);
    renderStrategyNotes();
    clearError();
  });
  syncStrategyRow(tr);
}

function syncStrategyRow(tr) {
  const select = tr.querySelector('[data-strategy]');
  const spec = (series.registry || {})[select.value];
  renderRowParams(tr.querySelector('.row-params'), spec);
  // A fresh choice has produced no evidence yet, so undo any previous dimming.
  setBidIgnored(tr, !usesTypedBid(spec));
}

function renderRowParams(host, spec) {
  host.replaceChildren();

  for (const [key, param] of Object.entries((spec && spec.params) || {})) {
    const numeric = param.type === 'number' || param.type === 'integer';
    const field = h('label', 'row-param');
    field.append(h('span', null, param.label || key));

    const input = h('input');
    input.dataset.strategyParam = key;
    if (param.type === 'boolean') {
      input.type = 'checkbox';
      input.checked = !!param.default;
    } else {
      input.type = numeric ? 'number' : 'text';
      if (numeric) input.step = param.type === 'integer' ? '1' : 'any';
      if (isNum(param.min)) input.min = String(param.min);
      if (isNum(param.max)) input.max = String(param.max);
      input.value = param.default === null || param.default === undefined ? '' : String(param.default);
      if (param.default === null || param.default === undefined) input.placeholder = 'auto';
    }
    if (param.description) input.title = param.description;

    field.append(input);
    host.append(field);
  }
}

/** Dim the typed bid when we know the chosen strategy did not use it. The state
 *  is spelled out in the label too, so it is never conveyed by opacity alone. */
function setBidIgnored(tr, ignored) {
  const input = tr.querySelector('[data-field="bid"]');
  if (!input) return;
  input.classList.toggle('is-ignored', ignored);
  input.title = ignored ? 'The last series showed this strategy does not use the typed bid.' : '';
  input.setAttribute('aria-label', ignored ? 'bid (unused by the selected strategy)' : 'bid');
}

/** The descriptions of whichever strategies are currently in play, de-duplicated
 *  — one per distinct choice rather than one per row. */
function renderStrategyNotes() {
  const host = $('strategy-notes');
  host.replaceChildren();
  if (!series.registry) return;

  const seen = new Set();
  for (const tr of $('bidder-rows').children) {
    const select = tr.querySelector('[data-strategy]');
    if (!select || seen.has(select.value)) continue;
    seen.add(select.value);

    const spec = series.registry[select.value];
    if (!spec) continue;
    const note = h('p', 'strategy-note');
    note.append(h('span', 'k', `${spec.label || spec.name}: `));
    note.append(document.createTextNode(spec.description || ''));
    host.append(note);
  }
}

function readStrategies() {
  const out = {};
  for (const tr of $('bidder-rows').children) {
    const select = tr.querySelector('[data-strategy]');
    if (!select) continue;

    const params = {};
    for (const input of tr.querySelectorAll('[data-strategy-param]')) {
      const key = input.dataset.strategyParam;
      if (input.type === 'checkbox') { params[key] = input.checked; continue; }
      const raw = input.value.trim();
      if (raw === '') continue;
      params[key] = input.type === 'number' ? Number(raw) : raw;
    }
    out[tr.querySelector('[data-field="id"]').value.trim()] = { name: select.value, params };
  }
  return out;
}

/** Round 0's bid against the bid that was typed: the only evidence available
 *  that a strategy ignored the form. See the ponytail note on usesTypedBid. */
function markIgnoredBids(data, typed) {
  const decisions = (data.rounds[0] && data.rounds[0].decisions) || {};
  for (const tr of $('bidder-rows').children) {
    const id = tr.querySelector('[data-field="id"]').value.trim();
    const opening = decisions[id];
    if (!opening || !isNum(typed[id])) continue;
    setBidIgnored(tr, opening.bid !== typed[id]);
  }
}

// ================================================================== running =

function clearSeries() {
  series.data = null;
  hideTooltip();
  $('timeline-card').hidden = true;
  $('series-card').hidden = true;
  $('decision-card').hidden = true;
}

async function runSeries() {
  stopPlaying();
  clearError();

  const bidders = readBidders();
  const problem = validateBidders(bidders);
  if (problem) { showError(problem); return; }

  const rounds = Number($('rounds').value);
  if (!Number.isInteger(rounds) || rounds < 1 || rounds > MAX_ROUNDS) {
    showError(`Rounds must be a whole number from 1 to ${MAX_ROUNDS}.`);
    return;
  }

  const button = $('run-series');
  button.disabled = true;
  button.textContent = 'Running…';
  try {
    const data = await api('/run_series', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        mechanism: $('mechanism').value,
        bidders,
        strategies: readStrategies(),
        rounds,
        params: readParams(),
        // Phase 4 seam. `undefined` drops out of JSON.stringify entirely, so with
        // no world configured this is byte-for-byte the phase 2 request.
        world: (window.campaignExt && window.campaignExt.readWorld()) || undefined,
      }),
    });
    loadSeries(data, Object.fromEntries(bidders.map((b) => [b.id, b.bid])));
  } catch (err) {
    // Whatever is already on screen stays there, as it does when a single run
    // fails: the error says what went wrong without wiping the last good result.
    showError(err.message);
  } finally {
    button.disabled = false;
    button.textContent = 'Run series';
  }
}

/** The bidders in this series, in setup order — which is the colour slot order.
 *
 *  A round with `trace: null` is a real outcome ("no auction — nobody entered"),
 *  so the roster cannot be read off round 0 unconditionally. Prefer the first
 *  round that ran; if none did, every bidder is still named in `decisions`, with
 *  its value unknown because it never entered anything.
 */
function rosterOf(data) {
  const ran = data.rounds.find((r) => r.trace && Array.isArray(r.trace.bidders));
  if (ran) return ran.trace.bidders;
  return Object.keys(data.rounds[0].decisions || {}).map((id) => ({ id, value: null }));
}

function loadSeries(data, typedBids) {
  if (!data || !Array.isArray(data.rounds) || data.rounds.length === 0) {
    throw new Error('The server returned a series with no rounds.');
  }

  // A round where every bidder sat out has no auction and no trace, and with
  // budgets that can be round 0. Take the bidder roster from the first round that
  // actually ran, and fall back to the decisions — every bidder is listed there
  // whether it entered or not.
  const bidders = rosterOf(data);
  series.data = data;
  series.order = bidders.map((b) => b.id);
  series.values = Object.fromEntries(bidders.map((b) => [b.id, b.value]));
  series.scale = pathScale(data);
  series.barScale = widestTraceScale(data);
  markIgnoredBids(data, typedBids);

  $('timeline-card').hidden = false;
  $('series-card').hidden = false;
  $('decision-card').hidden = false;

  renderConvergence();
  renderSeriesStats();
  renderPathLegend();
  renderPathTable();

  // Phase 4 seam. web/campaign.js draws spend, win rate, the steering variable and
  // regret — each because the summary carries that series, never because of a
  // strategy name. A phase 2 series carries none of them and nothing appears.
  if (window.campaignExt && window.campaignExt.onSeries) window.campaignExt.onSeries(data, series.order);

  selectRound(0);
}

/** The whole point of the wrapper: hand a round's trace to the phase 1 renderer. */
function selectRound(index) {
  if (!series.data) return;
  stopPlaying();
  series.round = clamp(index, 0, series.data.rounds.length - 1);

  const record = series.data.rounds[series.round];
  app.trace = record.trace;
  // One axis for every round, for the same reason app.js uses one axis for every
  // step: walking the timeline should move the bars, not the ruler.
  app.scale = series.barScale;

  renderTimeline();
  renderPathChart();
  renderDecisions();

  // app.js's render() returns quietly on a null trace, which would leave the
  // *previous* round's steps on screen — a round that never happened, shown as
  // though it had. Say what actually happened instead.
  if (record.trace) render(0);      // ← the untouched phase 1 renderer
  else renderNoAuction();

  if (window.campaignExt && window.campaignExt.onRound) window.campaignExt.onRound(series.data, series.round);
}

/** Nobody entered this round: every bidder was out of budget or chose to sit out.
 *  A real outcome in a paced campaign, and not an error. */
function renderNoAuction() {
  $('step-count').textContent = `Round ${series.round + 1} — no auction`;
  $('step-h').textContent = 'Nobody entered';
  $('step-detail').textContent =
    'Every bidder sat this round out — out of budget, or throttled into skipping it. ' +
    'There was no auction to run, so nothing was sold and nobody paid anything.';
  $('step-formula').hidden = true;
  $('step-stage').hidden = true;
  for (const id of ['result-card', 'ladder-card', 'packages-card']) {
    const card = $(id);
    if (card) card.hidden = true;
  }
  $('chart').replaceChildren();
  $('state-chips').replaceChildren();
  $('legend').replaceChildren();
  renderTransport(null, 0);
}

/** app.js sizes the bar chart's axis to one trace; a series needs the widest. */
function widestTraceScale(data) {
  let widest = { max: 1, step: 1 };
  for (const record of data.rounds) {
    if (!record.trace) continue;         // a round nobody entered has nothing to scale
    const scale = traceScale(record.trace);
    if (scale.max > widest.max) widest = scale;
  }
  return widest;
}

// ============================================================ round timeline

function renderTimeline() {
  const list = $('round-timeline');
  list.replaceChildren();

  const rounds = series.data.rounds;
  const settled = settledIndex();

  $('timeline-caption').textContent =
    `Round ${series.round + 1} of ${rounds.length}. Pick one to walk through it stage by stage.`;

  rounds.forEach((record, i) => {
    const li = h('li');
    if (i === series.round) li.className = 'is-current';
    else if (i === settled) li.className = 'is-settled';

    const btn = h('button', null, String(i + 1));
    btn.type = 'button';
    btn.title = i === settled ? `Round ${i + 1} — bids stopped changing here` : `Round ${i + 1}`;
    btn.setAttribute('aria-label', btn.title);
    if (i === series.round) btn.setAttribute('aria-current', 'true');
    btn.addEventListener('click', () => selectRound(i));

    li.append(btn);
    list.append(li);
  });
}

// ============================================================== convergence =

/** The API's converged_round, or null — and null for a single round no matter
 *  what the API says, because one observation cannot demonstrate settling. */
function settledIndex() {
  const data = series.data;
  if (!data || data.rounds.length < 2) return null;
  const summary = data.summary || {};
  if (!summary.converged) return null;
  return isNum(summary.converged_round) ? summary.converged_round : null;
}

/** Three states, three honest sentences. "Did not converge" is never dressed up
 *  as an equilibrium, and a one-round series is never called "still moving"
 *  either — it simply has not been given enough rounds to show anything. */
function renderConvergence() {
  const note = $('converge-note');
  const total = series.data.rounds.length;
  const settled = settledIndex();
  note.replaceChildren();

  let glyph, text;
  if (total < 2) {
    glyph = '?';
    text = 'Not enough rounds. One round is a single set of bids — it cannot show whether ' +
           'anything settles, in either direction. Run at least two rounds before reading ' +
           'anything into this.';
  } else if (settled === null) {
    glyph = '↝';
    text = `Still moving. Bids were still changing when round ${total} ended, so this series ` +
           'never stopped. That is not an equilibrium and not a near-miss for one: it is a ' +
           'series that had not settled, and more rounds may or may not change that.';
  } else {
    glyph = '=';
    text = `Settled from round ${settled + 1}. From there every bidder's bid stopped changing ` +
           `and none of them moved again through round ${total}. That is where these particular ` +
           'strategies stopped moving against each other, which is weaker than a proven equilibrium.';
  }

  note.append(h('span', 'glyph', glyph));
  note.append(h('span', null, text));
}

function renderSeriesStats() {
  const host = $('series-stats');
  host.replaceChildren();

  const summary = series.data.summary || {};
  const stat = (label, value) => {
    const box = h('div', 'stat');
    box.append(h('span', 'stat-label', label));
    box.append(h('span', 'stat-value', value));
    host.append(box);
  };

  stat('Rounds', String(series.data.rounds.length));
  stat('Revenue, all rounds',
       fmt((summary.revenue || []).reduce((sum, v) => sum + (isNum(v) ? v : 0), 0)));
  if (isNum(summary.efficiency_rate)) {
    stat('Efficient rounds', `${Math.round(summary.efficiency_rate * 100)}%`);
  }

  $('series-note').textContent =
    'Efficient rounds: the share of rounds in which the item went to the bidder who valued it most.';
}

// ============================================================ bid-path chart

const PATH = {
  width: 760,       // logical units; the SVG scales to its container via viewBox
  padLeft: 54,      // y tick labels
  padRight: 96,     // end labels
  padTop: 30,       // the "settled" rule label
  padBottom: 46,    // x ticks + axis title
  plotH: 240,
  ticks: 5,
  dotR: 4,          // ≥ 8px marker
  maxDots: 16,      // past this many rounds the per-round dots crowd the line
  labelGap: 13,     // end labels closer than this collide
};

/** One y axis for the whole series — bids and values together, so the gap
 *  between a bid and its value (the shading) is read off the same ruler. */
function pathScale(data) {
  const seen = [0];
  for (const path of Object.values(data.summary.bid_paths || {})) {
    for (const v of path) if (isNum(v)) seen.push(v);
  }
  // Values are redrawn every round once a world is in play, so the ruler has to
  // cover every round's draws, not just the first one's.
  for (const record of data.rounds) {
    for (const bidder of (record.trace && record.trace.bidders) || []) {
      if (isNum(bidder.value)) seen.push(bidder.value);
    }
  }
  return niceScale(Math.max(...seen), PATH.ticks);
}

// ponytail: the y axis is plain linear from zero. Ceiling: a series that dives to
// near-zero and then creeps back a unit at a time (all-pay best-response, which
// has no pure-strategy equilibrium to settle into) draws that creep as a couple of
// pixels above the baseline — the dive is legible, the crawl is not, and the exact
// numbers have to come from the dots, the crosshair or the table view. It is still
// the honest picture: those bids really are ~2% of the opening ones. Upgrade path:
// a symlog y axis, or a second small-multiple panel zoomed to the tail.
function renderPathChart() {
  const host = $('path-chart');
  host.replaceChildren();
  if (!series.data) return;

  const data = series.data;
  const paths = data.summary.bid_paths || {};
  const drawn = series.order.map((id, i) => ({ id, slot: i, path: paths[id] }))
                            .filter((row) => Array.isArray(row.path) && row.path.length > 0);
  if (drawn.length === 0) return;

  const n = data.rounds.length;
  const scale = series.scale;
  const plotW = PATH.width - PATH.padLeft - PATH.padRight;
  const plotBottom = PATH.padTop + PATH.plotH;
  const height = plotBottom + PATH.padBottom;
  const x = (r) => PATH.padLeft + (n === 1 ? plotW / 2 : (r / (n - 1)) * plotW);
  const y = (v) => plotBottom - clamp(v / scale.max, 0, 1) * PATH.plotH;
  const settled = settledIndex();

  const svg = s('svg', {
    viewBox: `0 0 ${PATH.width} ${height}`,
    preserveAspectRatio: 'xMinYMin meet',
    role: 'img',
    'aria-label': `Bid paths for ${drawn.length} bidders over ${n} round${n === 1 ? '' : 's'}. ` +
                  'Every bid is listed in the table view below this chart.',
  });

  // --- gridlines, y ticks, x ticks -----------------------------------------
  const axis = s('g');
  for (let v = 0; v <= scale.max + 1e-9; v += scale.step) {
    axis.append(s('line', { class: 'grid-line', x1: PATH.padLeft, y1: y(v), x2: PATH.padLeft + plotW, y2: y(v) }));
    axis.append(s('text', { class: 'tick-label', x: PATH.padLeft - 9, y: y(v) + 4, 'text-anchor': 'end' }, fmt(v)));
  }
  axis.append(s('line', { class: 'axis-line', x1: PATH.padLeft, y1: plotBottom, x2: PATH.padLeft + plotW, y2: plotBottom }));

  const every = Math.max(1, Math.ceil(n / 10));
  for (let r = 0; r < n; r++) {
    if (r % every !== 0 && r !== n - 1) continue;
    axis.append(s('text', { class: 'tick-label', x: x(r), y: plotBottom + 18, 'text-anchor': 'middle' }, String(r + 1)));
  }
  axis.append(s('text', { class: 'axis-title', x: PATH.padLeft + plotW / 2, y: plotBottom + 38, 'text-anchor': 'middle' }, 'round'));
  svg.append(axis);

  // --- the round you are looking at, as a band behind everything -----------
  // Clipped to the plot, so the first and last rounds do not bleed into the tick
  // gutter and read as chrome rather than as a mark.
  const band = Math.min(24, n === 1 ? 24 : plotW / (n - 1));
  const bandLeft = Math.max(PATH.padLeft, x(series.round) - band / 2);
  const bandRight = Math.min(PATH.padLeft + plotW, x(series.round) + band / 2);
  svg.append(s('rect', {
    class: 'round-band', x: bandLeft, y: PATH.padTop,
    width: Math.max(0, bandRight - bandLeft), height: PATH.plotH, rx: 4,
  }));

  // --- where it settled, if it settled -------------------------------------
  if (settled !== null) {
    const sx = x(settled);
    svg.append(s('line', { class: 'settle-line', x1: sx, y1: PATH.padTop - 10, x2: sx, y2: plotBottom }));
    const anchor = sx < PATH.padLeft + 40 ? 'start' : (sx > PATH.padLeft + plotW - 40 ? 'end' : 'middle');
    svg.append(s('text', { class: 'rule-label', x: sx, y: PATH.padTop - 16, 'text-anchor': anchor },
                `settled · round ${settled + 1}`));
  }

  // --- one dashed value line + one solid bid line per bidder ---------------
  for (const row of drawn) {
    const value = series.values[row.id];
    if (isNum(value)) {
      svg.append(s('line', {
        class: 'value-line', x1: PATH.padLeft, y1: y(value), x2: PATH.padLeft + plotW, y2: y(value),
        style: `stroke: var(${slotVar(row.slot)})`,
      }));
    }
  }

  const showDots = n <= PATH.maxDots;
  for (const row of drawn) {
    const points = row.path.map((bid, r) => `${x(r)},${y(isNum(bid) ? bid : 0)}`).join(' ');
    svg.append(s('polyline', { class: 'bid-line', points, style: `stroke: var(${slotVar(row.slot)})` }));

    if (showDots) {
      row.path.forEach((bid, r) => {
        // The 2px surface ring is what keeps two bidders sitting on the same bid
        // from reading as one mark.
        svg.append(s('circle', {
          class: 'bid-dot', cx: x(r), cy: y(isNum(bid) ? bid : 0), r: PATH.dotR,
          style: `fill: var(${slotVar(row.slot)})`,
        }));
      });
    }
  }

  // --- end labels, but only when they do not collide -----------------------
  // ponytail: converging lines drop ALL their end labels rather than routing
  // leader lines to them; nudging labels apart detaches them from their lines and
  // reads as noise. Ceiling: on a converged series nobody is directly labelled and
  // the legend carries identity alone. Upgrade path: leader lines, or facet into
  // small multiples past ~4 converging bidders.
  const ends = drawn.map((row) => ({ ...row, last: row.path[n - 1] }))
                    .map((row) => ({ ...row, y: y(isNum(row.last) ? row.last : 0) }));
  const sorted = [...ends].sort((a, b) => a.y - b.y);
  const collide = sorted.some((row, i) => i > 0 && row.y - sorted[i - 1].y < PATH.labelGap);

  if (!collide) {
    for (const row of ends) {
      svg.append(s('text', { class: 'end-label', x: x(n - 1) + 11, y: row.y + 4, 'text-anchor': 'start' },
                  `${short(row.id, 8)} ${fmt(row.last)}`));
    }
  }

  // --- crosshair: aim at a round, read every bidder at once ----------------
  // ponytail: pointer only. Ceiling: no keyboard crosshair — keyboard and screen
  // reader users get the same numbers from the round buttons, the decision panel
  // and the table view, none of which are gated behind hover. Upgrade path: a
  // focusable plot with an arrow-key cursor, once the svg stops being role="img".
  const cross = s('line', {
    class: 'cross-line', x1: PATH.padLeft, y1: PATH.padTop, x2: PATH.padLeft, y2: plotBottom,
    visibility: 'hidden',
  });
  svg.append(cross);

  const roundAt = (event) => {
    const box = svg.getBoundingClientRect();
    if (!box.width || n === 1) return 0;
    const local = ((event.clientX - box.left) / box.width) * PATH.width;
    return Math.round(clamp((local - PATH.padLeft) / plotW, 0, 1) * (n - 1));
  };

  const hit = s('rect', {
    class: 'hit', x: PATH.padLeft - 10, y: PATH.padTop, width: plotW + 20, height: PATH.plotH,
  });
  const track = (event) => {
    const r = roundAt(event);
    cross.setAttribute('x1', x(r));
    cross.setAttribute('x2', x(r));
    cross.setAttribute('visibility', 'visible');
    showPathTooltip(event, r, drawn);
  };
  hit.addEventListener('pointerenter', track);
  hit.addEventListener('pointermove', track);
  hit.addEventListener('pointerleave', () => { cross.setAttribute('visibility', 'hidden'); hideTooltip(); });
  hit.addEventListener('click', (event) => selectRound(roundAt(event)));
  svg.append(hit);

  host.append(svg);
}

/** One tooltip, every bidder at that round — the pointer never has to land on a
 *  line. Values lead; the id follows, keyed by a stroke of the series colour. */
function showPathTooltip(event, roundIndex, drawn) {
  const tip = $('tooltip');
  tip.replaceChildren();
  tip.append(h('div', 'tt-head', `Round ${roundIndex + 1}`));

  for (const row of drawn) {
    const line = h('div', 'tt-row');
    const stroke = h('span', 'tt-line');
    stroke.style.color = `var(${slotVar(row.slot)})`;
    line.append(stroke);
    line.append(h('span', 'tt-key', row.id));
    line.append(h('span', 'tt-val', cell(row.path[roundIndex])));
    tip.append(line);
  }
  tip.hidden = false;

  // Same anchoring app.js uses for the bar tooltip; duplicated rather than
  // exported, because it is six lines and app.js is at its size ceiling.
  const box = tip.getBoundingClientRect();
  tip.style.left = `${clamp(event.clientX + 14, 6, window.innerWidth - box.width - 6)}px`;
  tip.style.top = `${clamp(event.clientY + 14, 6, window.innerHeight - box.height - 6)}px`;
}

function renderPathLegend() {
  const host = $('path-legend');
  host.replaceChildren();

  const entry = (keyNode, label) => {
    const li = h('li');
    li.append(keyNode);
    li.append(h('span', null, label));
    host.append(li);
  };

  const shown = series.order.slice(0, 8);
  shown.forEach((id, i) => {
    const key = h('span', 'key-stroke');
    key.style.color = `var(${slotVar(i)})`;
    entry(key, short(id, 12));
  });
  const rest = series.order.length - shown.length;
  if (rest > 0) {
    const key = h('span', 'key-stroke');
    key.style.color = 'var(--cat-rest)';
    entry(key, `${rest} more — see the table view`);
  }

  entry(h('span', 'key-dash'), 'that bidder’s private value');
  if (settledIndex() !== null) entry(h('span', 'key-line'), 'where the bids stopped changing');
}

/** The colour-free twin: every bid the chart draws, as text. */
function renderPathTable() {
  const data = series.data;
  const paths = data.summary.bid_paths || {};
  const summary = data.summary || {};
  const settled = settledIndex();
  const ids = series.order.filter((id) => Array.isArray(paths[id]));

  const rows = data.rounds.map((record, i) => {
    const cells = [settled !== null && i === settled ? `${i + 1} · settled` : String(i + 1)];
    for (const id of ids) cells.push(cell(paths[id][i]));
    cells.push(cell((summary.revenue || [])[i]));
    return cells;
  });

  $('path-table').replaceChildren(buildTable(['Round', ...ids, 'Revenue'], rows));

  const cumulative = summary.cumulative_utilities || {};
  const parts = ids.filter((id) => isNum(cumulative[id])).map((id) => `${id} ${fmt(cumulative[id])}`);
  $('path-totals').textContent = parts.length ? `Utility over the whole series — ${parts.join(' · ')}` : '';
}

// ============================================================ decision panel

function renderDecisions() {
  const record = series.data.rounds[series.round];
  const body = $('decision-body');
  body.replaceChildren();

  $('decision-caption').textContent =
    `Round ${series.round + 1} of ${series.data.rounds.length} — what each bidder submitted, in its own words.`;

  series.order.forEach((id, slot) => {
    const decision = (record.decisions || {})[id];
    if (!decision) return;

    const box = h('div', 'decision');

    const head = h('div', 'decision-head');
    const dot = h('span', 'series-dot');
    dot.style.background = `var(${slotVar(slot)})`;
    head.append(dot);
    head.append(h('span', 'decision-id', id));
    head.append(h('span', 'decision-bid', `bid ${fmt(decision.bid)}`));
    if (isNum(series.values[id])) head.append(h('span', 'decision-value', `value ${fmt(series.values[id])}`));
    box.append(head);

    box.append(h('p', 'decision-why', decision.why || ''));

    const considered = Array.isArray(decision.considered) ? decision.considered : null;
    if (considered && considered.length) {
      const details = h('details', 'considered');
      details.append(h('summary', null,
        `What it weighed — ${considered.length} candidate bid${considered.length === 1 ? '' : 's'}`));

      const rows = considered.map((candidate) => {
        const kept = candidate.bid === decision.bid;
        return [
          [cell(candidate.bid), kept ? 'is-chosen' : null],
          [cell(candidate.utility), isNum(candidate.utility) && candidate.utility < 0 ? 'neg' : null],
          [kept ? 'kept' : '', kept ? 'is-chosen' : null],
        ];
      });

      const wrap = h('div', 'scroll-x');
      wrap.append(buildTable(['Candidate bid', 'Utility it computed', 'Kept'], rows));
      details.append(wrap);
      box.append(details);
    }

    body.append(box);
  });
}

// =================================================================== boot ==

window.seriesExt = {
  onBidderRow(tr) {
    ensureStrategyCell(tr);
    renderStrategyNotes();
  },

  async boot() {
    try {
      series.registry = await api('/strategies');
    } catch (err) {
      $('run-series').disabled = true;
      $('strategy-notes').textContent =
        `${err.message} Strategies and repeated rounds are unavailable; single auctions still run.`;
      return;
    }

    if (!series.registry || Object.keys(series.registry).length === 0) {
      $('run-series').disabled = true;
      $('strategy-notes').textContent =
        'The server reported no strategies, so repeated rounds are unavailable. Single auctions still run.';
      return;
    }

    ensureStrategyHeader();
    for (const tr of [...$('bidder-rows').children]) ensureStrategyCell(tr);
    renderStrategyNotes();
  },
};

$('run-series').addEventListener('click', runSeries);
// A single auction is still a single auction: drop the series rather than leave a
// stale timeline pointing at rounds that are no longer on screen.
$('run').addEventListener('click', clearSeries);
