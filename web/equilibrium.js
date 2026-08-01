'use strict';

/* ===========================================================================
   The mechanism as a game — reply curves, stable profiles, and what the
   seller collects.

   THE ONE RULE still holds: no mechanism name appears below. Everything drawn
   here is drawn because the response carries it. A reply curve appears because
   there are points; the equilibrium table appears because a search ran; the
   dominance verdict is read off the response rather than off a list of
   mechanisms kept in JavaScript. Point this at a mechanism written next year
   and it renders without being told anything about it.

   Seams used: app.js calls boot() once. Nothing else on the page knows this
   file exists, and the step renderer is untouched.
   =========================================================================== */

// Named EQ: every script on this page shares one global scope, where app.js
// already owns CHART and campaign.js owns DAY.
const EQ = {
  width: 760,
  padLeft: 56,
  padRight: 24,
  padTop: 22,
  padBottom: 44,
  plotH: 190,
  ticks: 4,
  dotR: 4,
};

const equilibrium = { data: null, focus: null };

// ================================================================ the request =

async function runEquilibrium() {
  stopPlaying();
  clearError();

  const bidders = readBidders();
  const problem = validateBidders(bidders);
  if (problem) { showError(problem); return; }

  const button = $('run-equilibrium');
  button.disabled = true;
  button.textContent = 'Analysing…';
  try {
    const data = await api('/equilibrium', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        mechanism: $('mechanism').value,
        bidders,
        params: readParams(),
      }),
    });
    loadEquilibrium(data);
  } catch (err) {
    // Whatever is on screen stays there, as it does for the other two runners.
    showError(err.message);
  } finally {
    button.disabled = false;
    button.textContent = 'Analyse equilibrium';
  }
}

function loadEquilibrium(data) {
  equilibrium.data = data;
  const ids = Object.keys(data.best_response.curves);
  // Keep the focused bidder across re-runs when they are still in the auction;
  // re-analysing after a tweak should not silently move the reader.
  if (!ids.includes(equilibrium.focus)) equilibrium.focus = ids[0] || null;

  $('equilibrium-card').hidden = false;
  renderBidderPicker(ids);
  renderCurve();
  renderNash(data.game);
  renderDominance(data.game);
  renderRevenue(data.revenue_equivalence);
}

// =================================================================== the chart =

/** A y-axis that can go below zero, because an all-pay loser really does. */
function signedScale(values) {
  const top = Math.max(0, ...values);
  const bottom = Math.min(0, ...values);
  const up = niceScale(top || 1, EQ.ticks);
  const down = niceScale(-bottom || 1, EQ.ticks);
  return { max: top > 0 ? up.max : 0, min: bottom < 0 ? -down.max : 0 };
}

/** One bidder's payoff across the grid, with its value and its best reply marked. */
function curveChart(host, curve, grid) {
  const points = curve.points;
  if (!points.length) {
    host.append(h('p', 'note', curve.why));
    return;
  }

  const scale = signedScale(points.map((p) => p.utility));
  const span = scale.max - scale.min || 1;
  const bidMax = Math.max(1, ...grid);
  const plotW = EQ.width - EQ.padLeft - EQ.padRight;
  const x = (bid) => EQ.padLeft + (bid / bidMax) * plotW;
  const y = (v) => EQ.padTop + EQ.plotH - ((v - scale.min) / span) * EQ.plotH;

  const caption = `What ${curve.bidder} earns at every bid on the grid, with every rival `
    + 'held at the bid on the table.';
  const svg = s('svg', {
    viewBox: `0 0 ${EQ.width} ${EQ.padTop + EQ.plotH + EQ.padBottom}`,
    class: 'line-chart',
    role: 'img',
    'aria-label': caption,
  });
  svg.setAttribute('preserveAspectRatio', 'xMinYMin meet');

  for (let t = 0; t <= EQ.ticks; t += 1) {
    const value = scale.min + (span / EQ.ticks) * t;
    const gy = y(value);
    svg.append(s('line', {
      x1: EQ.padLeft, x2: EQ.padLeft + plotW, y1: gy, y2: gy,
      class: value === 0 ? 'grid is-zero' : 'grid',
    }));
    svg.append(s('text', {
      x: EQ.padLeft - 8, y: gy + 4, class: 'tick', 'text-anchor': 'end',
    }, fmt(value)));
  }

  for (const bid of [0, bidMax / 2, bidMax]) {
    svg.append(s('text', {
      x: x(bid), y: EQ.padTop + EQ.plotH + 20, class: 'tick', 'text-anchor': 'middle',
    }, fmt(bid)));
  }
  svg.append(s('text', {
    x: EQ.padLeft + plotW / 2, y: EQ.padTop + EQ.plotH + 38,
    class: 'axis-title', 'text-anchor': 'middle',
  }, `${curve.bidder}'s bid`));

  // The bidder's own value: the line the whole lesson is measured against.
  svg.append(s('line', {
    x1: x(curve.value), x2: x(curve.value), y1: EQ.padTop, y2: EQ.padTop + EQ.plotH,
    class: 'rule-line',
  }));
  svg.append(s('text', {
    x: x(curve.value), y: EQ.padTop - 6, class: 'tick', 'text-anchor': 'middle',
  }, `value ${fmt(curve.value)}`));

  svg.append(s('polyline', {
    points: points.map((p) => `${x(p.bid)},${y(p.utility)}`).join(' '),
    class: 'line',
  }));
  for (const point of points) {
    svg.append(s('circle', {
      cx: x(point.bid), cy: y(point.utility), r: EQ.dotR,
      class: point.bid === curve.best ? 'dot is-best' : 'dot',
    }));
  }

  const figure = h('figure', 'chart-figure');
  figure.append(svg);
  figure.append(h('figcaption', 'caption', caption));
  host.append(figure);
}

function renderBidderPicker(ids) {
  const host = $('eq-bidders');
  host.replaceChildren();
  if (ids.length < 2) return;
  for (const id of ids) {
    const button = h('button', `chip${id === equilibrium.focus ? ' is-on' : ''}`, id);
    button.type = 'button';
    button.setAttribute('aria-pressed', String(id === equilibrium.focus));
    button.addEventListener('click', () => {
      equilibrium.focus = id;
      renderBidderPicker(ids);
      renderCurve();
    });
    host.append(button);
  }
}

function renderCurve() {
  const host = $('eq-curve');
  host.replaceChildren();
  const data = equilibrium.data;
  const curve = data.best_response.curves[equilibrium.focus];
  if (!curve) return;
  curveChart(host, curve, data.best_response.grid);
  // A curve with no points has no chart, and curveChart has already shown the
  // sentence explaining why — saying it twice reads as two separate problems.
  if (curve.points.length) host.append(h('p', 'note', curve.why));
}

// ============================================================== the equilibria =

function renderNash(game) {
  const host = $('eq-nash');
  host.replaceChildren();

  if (!game.searched) {
    host.append(h('p', 'note', game.note));
    return;
  }

  host.append(h('p', 'caption', game.nash.why));
  if (!game.nash.shown.length) return;

  const ids = Object.keys(game.nash.shown[0].bids);
  const rows = game.nash.shown.map((eq) => [
    ...ids.map((id) => fmt(eq.bids[id])),
    fmt(eq.revenue),
    eq.winner === null ? 'nobody' : eq.winner,
    eq.efficient ? 'yes' : ['no', 'is-bad'],
    eq.truthful ? ['everyone bids their value', 'is-good'] : '',
  ]);
  const table = buildTable(
    [...ids.map((id) => `${id} bids`), 'revenue', 'winner', 'efficient', ''],
    rows,
  );
  const box = h('div', 'scroll-x');
  box.append(table);
  host.append(box);

  if (game.nash.count > game.nash.shown.length) {
    host.append(h('p', 'note',
      `Showing ${game.nash.shown.length} of ${game.nash.count}, closest to truthful bidding first.`));
  }
}

function renderDominance(game) {
  const host = $('eq-dominance');
  host.replaceChildren();
  if (!game.searched || !game.dominance) return;

  host.append(h('h3', null, 'Is bidding your value dominant?'));
  host.append(h('p', 'caption',
    'Nash asks whether a bidder regrets its bid given what everyone else did. This asks '
    + 'whether it could regret it whatever anyone else does — the stronger promise, and '
    + 'the one "second-price is truthful" is actually about.'));

  for (const [id, verdict] of Object.entries(game.dominance)) {
    // Three states, not two. `null` means the mechanism refused the honest bid against
    // every rival profile, so the question was never put — which is neither a yes nor a
    // no, and colouring it as either would invent a finding.
    const answered = verdict.truthful_dominant !== null;
    const tone = !answered ? '' : verdict.truthful_dominant ? ' is-good' : ' is-bad';
    const label = !answered
      ? `${id}: could not be tested`
      : verdict.truthful_dominant ? `${id}: dominant` : `${id}: not dominant`;

    const box = h('div', `verdict${tone}`);
    box.append(h('span', 'verdict-flag', label));
    box.append(h('p', 'note', verdict.why));
    host.append(box);
  }
}

// ======================================================== revenue equivalence =

/** A verdict cell. The row everything else is measured against says so rather than
 *  claiming to agree with itself, which would read as a fourth result. */
function verdictCell(scored, isBaseline) {
  if (isBaseline) return 'the baseline';
  if (scored.identical) return ['identical, draw for draw', 'is-good'];
  return scored.agrees ? ['agrees', 'is-good'] : ['differs', 'is-bad'];
}

function revenueTable(report) {
  const rows = Object.values(report.mechanisms).map((scored) => [
    scored.label,
    fmt(scored.mean),
    scored.difference === undefined
      ? '—'
      : `${scored.difference >= 0 ? '+' : ''}${fmt(scored.difference)} ± ${fmt(scored.difference_stderr)}`,
    verdictCell(scored, scored.baseline),
  ]);
  const box = h('div', 'scroll-x');
  box.append(buildTable(
    ['Mechanism', 'Mean revenue', 'Paired difference vs second-price', 'Verdict'],
    rows,
  ));
  return box;
}

function renderRevenue(report) {
  const host = $('eq-revenue');
  host.replaceChildren();
  host.append(h('h3', null, 'Revenue equivalence'));

  const symmetric = report.symmetric;
  host.append(h('p', 'caption', symmetric.why));
  host.append(revenueTable(symmetric));
  host.append(h('p', 'note',
    `${symmetric.draws} seeded draws, ${symmetric.n} bidders. Closed-form benchmark: `
    + `${fmt(symmetric.theory)}, the expected second-highest value.`));

  if (report.asymmetric) {
    host.append(h('h3', null, 'The same check with the assumption removed'));
    host.append(h('p', 'caption', report.asymmetric.why));
    host.append(revenueTable(report.asymmetric));
  } else if (report.skipped) {
    host.append(h('p', 'note', report.skipped));
  }

  const excluded = Object.entries(symmetric.excluded || {});
  if (excluded.length) {
    const details = h('details', 'table-view');
    details.append(h('summary', null, `${excluded.length} mechanisms are not in this comparison`));
    for (const [name, reason] of excluded) {
      details.append(h('p', 'note', `${name}: ${reason}`));
    }
    host.append(details);
  }
}

// ===================================================================== boot ==

window.equilibriumExt = {
  boot() {
    const button = $('run-equilibrium');
    if (button) button.addEventListener('click', runEquilibrium);
  },
};
