'use strict';

/* ===========================================================================
   A day, not an auction — budgets, spend, and what steers them.

   THE ONE RULE still holds: no strategy name appears below. Every chart is
   drawn because the summary carries the series it needs — `spend` for the
   budget chart, a published `control` for the steering chart, `regret` for the
   regret chart — and for no other reason. A phase 2 series carries none of
   them and nothing here appears.

   Seams used: series.js calls readWorld() when building the request, onSeries()
   when one arrives, and onRound() as the timeline moves.
   =========================================================================== */

// Named DAY, not CHART: every script on this page shares one global scope, and
// app.js already owns `CHART`. A collision here is a SyntaxError that kills the
// whole file, silently taking phase 4 off the page.
const DAY = {
  width: 760,        // logical units; the SVG scales to its container via viewBox
  padLeft: 54,
  padRight: 96,      // end labels
  padTop: 22,
  padBottom: 40,
  plotH: 170,
  ticks: 4,
  dotR: 4,
  maxDots: 16,
  labelGap: 13,
};

const campaign = { data: null, order: [], round: 0 };

// ==================================================== the world (setup form) =

/** One budget row per bidder, kept in step with the bidder table. Blank means
 *  unlimited — a bidder with no budget is the phase 2 bidder, which is the
 *  honest default rather than a number nobody chose. */
function syncBudgetRows() {
  const tbody = $('budget-rows');
  if (!tbody) return;
  const existing = new Map(
    [...tbody.children].map((tr) => [tr.dataset.bidder, tr.querySelector('input').value]),
  );

  tbody.replaceChildren();
  for (const bidder of readBidders()) {
    if (!bidder.id) continue;
    const tr = h('tr');
    tr.dataset.bidder = bidder.id;
    tr.append(h('td', null, bidder.id));

    const td = h('td');
    const input = h('input');
    input.type = 'number';
    input.min = '0';
    input.step = 'any';
    input.placeholder = 'unlimited';
    input.value = existing.get(bidder.id) ?? '';
    input.setAttribute('aria-label', `budget for ${bidder.id}`);
    td.append(input);
    tr.append(td);
    tbody.append(tr);
  }
}

/** The world to send, or null for a plain phase 2 series. */
function readWorld() {
  const box = $('world-box');
  if (!box || !box.open) return null;

  const low = Number($('value-low').value);
  const high = Number($('value-high').value);
  const seed = Number($('world-seed').value);

  const budgets = {};
  for (const tr of $('budget-rows').children) {
    const raw = tr.querySelector('input').value.trim();
    if (raw !== '') budgets[tr.dataset.bidder] = Number(raw);
  }

  const world = { seed: Number.isFinite(seed) ? seed : 0, budgets };
  // Half a range says nothing about where values come from, so send both or
  // neither and let the server say so if they are wrong.
  if (Number.isFinite(low) && Number.isFinite(high)) {
    world.value_low = low;
    world.value_high = high;
  }
  return world;
}

// ================================================================== plotting =

/** One line chart. `series` is [{id, points: [n|null], dashed?}], where a null
 *  point is a round this bidder was absent for — a gap, never a zero. */
function lineChart(host, series, options) {
  const { yMax, caption, valueLabel } = options;
  const rounds = Math.max(1, ...series.map((s) => s.points.length));
  const scale = niceScale(yMax || 1, DAY.ticks);
  const plotW = DAY.width - DAY.padLeft - DAY.padRight;
  const x = (i) => DAY.padLeft + (rounds === 1 ? plotW / 2 : (i / (rounds - 1)) * plotW);
  const y = (v) => DAY.padTop + DAY.plotH - (v / scale.max) * DAY.plotH;

  const svg = s('svg', {
    viewBox: `0 0 ${DAY.width} ${DAY.padTop + DAY.plotH + DAY.padBottom}`,
    class: 'line-chart',
    role: 'img',
    'aria-label': caption,
  });
  svg.setAttribute('preserveAspectRatio', 'xMinYMin meet');

  // Recessive grid: a hairline per tick, and the tick's own value beside it.
  for (let t = 0; t <= DAY.ticks; t += 1) {
    const value = (scale.max / DAY.ticks) * t;
    const gy = y(value);
    svg.append(s('line', { x1: DAY.padLeft, x2: DAY.padLeft + plotW, y1: gy, y2: gy, class: 'grid' }));
    svg.append(s('text', { x: DAY.padLeft - 8, y: gy + 4, class: 'tick', 'text-anchor': 'end' }, fmt(value)));
  }

  // x ticks: first, last, and the midpoint — enough to locate a round, few
  // enough not to crowd. Rounds read 1-based, matching the timeline.
  for (const i of [...new Set([0, Math.floor((rounds - 1) / 2), rounds - 1])]) {
    svg.append(s('text', {
      x: x(i), y: DAY.padTop + DAY.plotH + 20, class: 'tick', 'text-anchor': 'middle',
    }, String(i + 1)));
  }
  svg.append(s('text', {
    x: DAY.padLeft + plotW / 2, y: DAY.padTop + DAY.plotH + 36, class: 'axis-title', 'text-anchor': 'middle',
  }, 'round'));

  const ends = [];
  series.forEach((line, index) => {
    const colour = `var(${slotVar(campaign.order.indexOf(line.id))})`;

    // A gap is a gap: split the path wherever the bidder was absent rather than
    // drawing a straight line across rounds that never happened.
    let run = [];
    const flush = () => {
      if (run.length > 1) {
        const path = s('polyline', {
          points: run.map((p) => `${x(p.i)},${y(p.v)}`).join(' '),
          class: `line${line.dashed ? ' is-dashed' : ''}`,
        });
        path.style.stroke = colour;
        svg.append(path);
      } else if (run.length === 1) {
        const dot = s('circle', { cx: x(run[0].i), cy: y(run[0].v), r: DAY.dotR, class: 'dot' });
        dot.style.fill = colour;
        svg.append(dot);
      }
      run = [];
    };
    line.points.forEach((v, i) => (isNum(v) ? run.push({ i, v }) : flush()));
    flush();

    if (rounds <= DAY.maxDots && !line.dashed) {
      line.points.forEach((v, i) => {
        if (!isNum(v)) return;
        const dot = s('circle', { cx: x(i), cy: y(v), r: DAY.dotR, class: 'dot' });
        dot.style.fill = colour;
        svg.append(dot);
      });
    }

    const last = [...line.points].reverse().find(isNum);
    if (isNum(last)) ends.push({ id: line.id, y: y(last), text: line.label || line.id, colour, index });
  });

  // Direct end labels, nudged apart so two bidders finishing together stay legible.
  ends.sort((a, b) => a.y - b.y);
  for (let i = 1; i < ends.length; i += 1) {
    if (ends[i].y - ends[i - 1].y < DAY.labelGap) ends[i].y = ends[i - 1].y + DAY.labelGap;
  }
  for (const end of ends) {
    const label = s('text', { x: DAY.padLeft + plotW + 8, y: end.y + 4, class: 'end-label' }, end.text);
    label.style.fill = end.colour;
    svg.append(label);
  }

  const figure = h('figure', 'chart-figure');
  figure.append(svg);
  const cap = h('figcaption', 'caption', caption);
  figure.append(cap);
  if (valueLabel) figure.append(h('p', 'note', valueLabel));
  host.append(figure);
}

// ================================================================== the data =

const pathsOf = (summary, key) => summary[key] || {};

/** Cumulative spend per bidder, with each budget drawn as its own dashed line so
 *  the ceiling and the climb toward it share one ruler. */
function spendChart(host, summary) {
  const spend = pathsOf(summary, 'spend');
  const budgets = summary.budgets || {};
  if (!Object.keys(spend).length) return false;

  const lines = campaign.order
    .filter((id) => Array.isArray(spend[id]))
    .map((id) => ({ id, points: spend[id] }));
  if (!lines.length) return false;

  const rounds = Math.max(...lines.map((l) => l.points.length));
  for (const [id, cap] of Object.entries(budgets)) {
    if (!isNum(cap)) continue;
    lines.push({ id, points: Array(rounds).fill(cap), dashed: true, label: `${id} budget` });
  }

  const yMax = Math.max(1, ...lines.flatMap((l) => l.points.filter(isNum)));
  lineChart(host, lines, {
    yMax,
    caption: 'Money spent so far, round by round. The dashed line of the same colour is that ' +
             'bidder’s budget — a curve that flattens early has run out and is sitting the rest of the day out.',
  });
  return true;
}

/** Share of rounds won so far. Derived from utilities rather than asked for,
 *  because a bidder with a non-zero utility in a round is a bidder that won it. */
function winRateChart(host, summary) {
  const utilities = pathsOf(summary, 'utilities');
  const ids = campaign.order.filter((id) => Array.isArray(utilities[id]));
  if (!ids.length) return false;

  const lines = ids.map((id) => {
    let wins = 0;
    const points = utilities[id].map((u, i) => {
      if (u) wins += 1;
      return wins / (i + 1);
    });
    return { id, points };
  });
  lineChart(host, lines, {
    yMax: 1,
    caption: 'Share of rounds won so far. A pacer holding a steady rate is spreading its budget; ' +
             'a line that climbs and then stops climbing has gone quiet.',
  });
  return true;
}

/** Whatever each strategy steers — μ, p, a multiplier, a chosen arm. Strategies
 *  publish it on the decision, so this chart neither knows nor asks which. */
function controlChart(host, data) {
  const tracked = new Map();
  data.rounds.forEach((record, i) => {
    for (const [id, decision] of Object.entries(record.decisions || {})) {
      const control = decision.control;
      if (!control || !isNum(control.value)) continue;
      if (!tracked.has(id)) tracked.set(id, { name: control.name, label: control.label, points: [] });
      const entry = tracked.get(id);
      while (entry.points.length < i) entry.points.push(null);
      entry.points.push(control.value);
    }
  });
  if (!tracked.size) return false;

  const lines = campaign.order
    .filter((id) => tracked.has(id))
    .map((id) => ({ id, points: tracked.get(id).points, label: `${id} · ${tracked.get(id).name}` }));

  const names = [...new Set([...tracked.values()].map((t) => t.label || t.name))];
  const yMax = Math.max(...lines.flatMap((l) => l.points.filter(isNum)), 1);
  lineChart(host, lines, {
    yMax,
    caption: `What each bidder is steering: ${names.join(', ')}. This is the knob the strategy ` +
             'turns after every round — watching it settle is watching the strategy find its price.',
  });
  return true;
}

/** Cumulative regret against the best fixed arm in hindsight. Only learners have
 *  arms, so only learners have a regret series to draw. */
function regretChart(host, summary) {
  const regret = pathsOf(summary, 'regret');
  const ids = campaign.order.filter((id) => Array.isArray(regret[id]));
  if (!ids.length) return false;

  const baseline = summary.regret_baseline || {};
  const lines = ids.map((id) => ({ id, points: regret[id] }));
  const yMax = Math.max(1, ...lines.flatMap((l) => l.points.filter(isNum)));
  const arms = ids
    .map((id) => (baseline[id] ? `${id} would have done best on ${fmt(baseline[id].arm)}` : null))
    .filter(Boolean);
  lineChart(host, lines, {
    yMax,
    caption: 'Cumulative regret: what the best single multiplier would have earned over the whole ' +
             'day, minus what was actually earned. A flat line has stopped paying to learn.',
    valueLabel: arms.join('; ') || null,
  });
  return true;
}

// =================================================================== summary =

function renderStats(summary) {
  const host = $('campaign-stats');
  host.replaceChildren();

  const stat = (label, value) => {
    const box = h('div', 'stat');
    box.append(h('span', 'stat-label', label));
    box.append(h('span', 'stat-value', value));
    host.append(box);
  };

  const budgets = summary.budgets || {};
  const spend = summary.spend || {};
  for (const [id, cap] of Object.entries(budgets)) {
    const path = spend[id] || [];
    const spent = path.length ? path[path.length - 1] : 0;
    const share = cap > 0 ? Math.round((spent / cap) * 100) : 0;
    stat(`${id} spent`, `${fmt(spent)} of ${fmt(cap)} · ${share}%`);
  }

  const quiet = summary.no_auction_rounds || [];
  if (quiet.length) {
    stat('Rounds with no auction', `${quiet.length} — nobody entered`);
  }
}

// ===================================================================== boot ==

window.campaignExt = {
  readWorld,

  boot() {
    syncBudgetRows();
    // Bidder names and rows change under us; rebuild on any setup edit rather
    // than trying to track individual mutations.
    const setup = $('single-setup');
    if (setup) setup.addEventListener('input', syncBudgetRows);
    const add = $('add-bidder');
    if (add) add.addEventListener('click', () => setTimeout(syncBudgetRows, 0));

    // Same idiom as series.js and equilibrium.js listening on the other run
    // buttons: a single run or an equilibrium analysis has no day behind it, so
    // yesterday's day card leaves with the run it described. A new series
    // repopulates it through onSeries.
    for (const id of ['run', 'run-equilibrium']) {
      const runButton = $(id);
      if (runButton) runButton.addEventListener('click', () => { $('campaign-card').hidden = true; });
    }
  },

  onSeries(data, order) {
    campaign.data = data;
    campaign.order = order || [];

    const host = $('campaign-charts');
    host.replaceChildren();
    const summary = data.summary || {};

    // Each chart appears only if the summary carries what it needs. A phase 2
    // series carries none of them, so the card stays hidden and the page is
    // exactly the page it was.
    const drew = [
      spendChart(host, summary),
      winRateChart(host, summary),
      controlChart(host, data),
      regretChart(host, summary),
    ].some(Boolean);

    const hasWorld = Object.keys(summary.budgets || {}).length > 0
      || (summary.no_auction_rounds || []).length > 0
      || Object.keys(summary.regret || {}).length > 0
      || data.rounds.some((r) => Object.values(r.decisions || {}).some((d) => d.control));

    $('campaign-card').hidden = !(drew && hasWorld);
    if ($('campaign-card').hidden) return;

    $('campaign-caption').textContent =
      'Every round is one impression with a freshly drawn value. What separates these bidders is ' +
      'not what they think things are worth — it is how they spread the same money across the day.';
    renderStats(summary);
  },

  onRound(data, index) {
    campaign.round = index;
  },
};
