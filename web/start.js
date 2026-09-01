'use strict';

/* ===========================================================================
   The front door: what to run, and something worth running.

   Two things that were one problem. The setup card used to end in three primary
   buttons stacked on top of each other with no hierarchy — a visitor had to
   guess which one they wanted before they knew what any of them did. And the
   page opened on a default bid table that answered no question in particular.

   So: a mode switch that shows one run button at a time, and a row of worked
   examples that fill the whole form in one click. The page opens on the first
   of them, already run.

   THE ONE RULE holds. Every preset — its mechanism, its strategies, its
   numbers, the sentence explaining it — arrives from GET /presets. Nothing
   below names a mechanism or a strategy, and the only thing this file decides
   is which of the three existing run buttons to press.
   =========================================================================== */

// Which button each mode uses. These are the page's own three buttons, kept
// exactly where they were with exactly the listeners they already had — the
// mode switch only decides which one is on screen.
const RUN_BUTTON = {
  single: 'run',
  series: 'run-series',
  equilibrium: 'run-equilibrium',
};

// What each mode is for, in the reader's terms rather than the engine's.
const MODE_DESC = {
  single: 'One auction, stepped through a stage at a time.',
  series: 'The same bidders meet round after round and adjust as they learn.',
  equilibrium: 'Sweep every bid, not just these ones, and find the profile nobody wants to leave.',
};

const start = { presets: [], active: null, mode: 'single' };

// ============================================================= mode switch ==

/** Show one run button, and only the fields that button reads.
 *
 *  Rounds and the world belong to a series and nothing else: a world is what
 *  draws each round's value, and a single run has one round to draw for. Leaving
 *  them on screen in the other modes advertised settings that would be ignored.
 */
function setMode(mode) {
  if (!RUN_BUTTON[mode]) return;
  start.mode = mode;

  for (const button of $('mode-switch').querySelectorAll('[data-mode]')) {
    button.setAttribute('aria-pressed', String(button.dataset.mode === mode));
  }
  for (const [key, id] of Object.entries(RUN_BUTTON)) $(id).hidden = key !== mode;

  $('rounds-field').hidden = mode !== 'series';
  $('mode-desc').textContent = MODE_DESC[mode] || '';

  const world = $('world-box');
  world.hidden = mode !== 'series';
  // A hidden <details> left open would come back open in a mode where readWorld
  // is never called — and then reappear, still open, in one where it is.
  if (world.hidden) world.open = false;
}

function wireModeSwitch() {
  for (const button of $('mode-switch').querySelectorAll('[data-mode]')) {
    button.addEventListener('click', () => {
      setMode(button.dataset.mode);
      clearError();
      // The other half of the front-door question: whether anyone ever leaves the
      // mode the page opens in.
      track(`mode/${button.dataset.mode}`, `Mode: ${button.dataset.mode}`);
    });
  }
  setMode('single');
}

// ============================================================ filling in it ==

/** Set a param input, if the currently selected mechanism declares it.
 *  It may not: presets carry params the server already checked against the
 *  mechanism, but renderParams has to have run first. */
function setParam(key, value) {
  const input = $(`param-${key}`);
  if (input) input.value = String(value);
}

function fillBidders(entrants) {
  $('bidder-rows').replaceChildren();
  for (const entry of entrants) {
    addBidderRow({ id: entry.id, value: entry.value, bid: entry.bid });
  }

  // The strategy column is web/series.js's, and it may not be loaded.
  const rows = [...$('bidder-rows').children];
  rows.forEach((tr, i) => {
    const select = tr.querySelector('[data-strategy]');
    const name = entrants[i].strategy;
    if (!select || !name) return;
    select.value = name;
    if (typeof syncStrategyRow === 'function') syncStrategyRow(tr);
  });
  if (typeof renderStrategyNotes === 'function') renderStrategyNotes();
  if (typeof syncBudgetRows === 'function') syncBudgetRows();
}

function fillPackages(entrants) {
  $('package-rows').replaceChildren();
  for (const entry of entrants) {
    addPackageRow({
      bidder: entry.bidder,
      items: entry.items,
      value: entry.value,
      bid: entry.bid,
    });
  }
}

/** Open the world box and fill it, or close it for a preset that has no world.
 *  Closed is not cosmetic: readWorld returns null for a closed box, which is
 *  what makes a preset without a world a plain fixed-value series. */
function fillWorld(world) {
  const box = $('world-box');
  if (!world) { box.open = false; return; }

  box.open = true;
  if (world.seed !== null && world.seed !== undefined) $('world-seed').value = String(world.seed);
  if (world.value_low !== null && world.value_low !== undefined) {
    $('value-low').value = String(world.value_low);
    $('value-high').value = String(world.value_high);
  }

  if (typeof syncBudgetRows === 'function') syncBudgetRows();
  const budgets = world.budgets || {};
  for (const tr of $('budget-rows').children) {
    const input = tr.querySelector('input');
    // Blank, not zero: an unbudgeted bidder is unlimited, and a leftover number
    // from the previous preset would put them on a budget nobody chose.
    input.value = tr.dataset.bidder in budgets ? String(budgets[tr.dataset.bidder]) : '';
  }
}

/** Write a preset into the setup form. It does not run — same split as the
 *  randomize button, so what a preset did stays visible and editable. */
function applyPreset(preset) {
  clearError();

  const select = $('mechanism');
  select.value = preset.mechanism;
  // The change listener renders the params and swaps the setup table between
  // scalar rows and package rows. Setting .value alone fires nothing.
  select.dispatchEvent(new Event('change'));

  for (const [key, value] of Object.entries(preset.params || {})) setParam(key, value);

  if (preset.kind === 'package') fillPackages(preset.entrants);
  else fillBidders(preset.entrants);

  setMode(preset.mode);
  if (preset.mode === 'series') {
    if (preset.rounds) $('rounds').value = String(preset.rounds);
    fillWorld(preset.world);
  }

  start.active = preset;
  renderChips();
}

// ================================================================== chips ===

function renderChips() {
  const host = $('starter-chips');
  host.replaceChildren();

  for (const preset of start.presets) {
    const chip = h('button', 'chip starter', preset.label);
    chip.type = 'button';
    // The hook is the reason to click. It is also the accessible name's
    // companion, so the chip is never a bare label to a screen reader.
    chip.title = preset.teaches;
    chip.setAttribute('aria-label', `${preset.label}: ${preset.teaches}`);
    chip.setAttribute('aria-pressed', String(start.active === preset));
    chip.addEventListener('click', () => {
      // Counted here rather than inside runPreset: boot() runs the first preset
      // too, and counting that would report a click nobody made — permanently
      // inflating whichever example happens to be first.
      track(`preset/${preset.name}`, preset.label);
      runPreset(preset);
    });
    host.append(chip);
  }

  $('starters-now').textContent = start.active ? start.active.teaches : '';
}

function runPreset(preset) {
  applyPreset(preset);
  // Press the page's own button rather than calling a runner directly: series.js
  // also listens on #run to clear the previous series, and skipping that would
  // leave a stale chart next to a fresh trace.
  //
  // An equilibrium preset runs the single auction first: its card opens with
  // "not what happens at these bids", so "these bids" had better be the
  // walkthrough beside it, not whatever the previous example left there.
  if (preset.mode === 'equilibrium') $(RUN_BUTTON.single).click();
  $(RUN_BUTTON[preset.mode]).click();
}

// =================================================================== boot ===

/** Wire the mode switch, then open on the first example.
 *
 *  Returns true when it ran something, so app.js knows not to run the default
 *  setup on top of it. A missing or empty /presets is not an error worth showing
 *  anyone — the form works, and app.js falls back to the default run.
 */
async function bootStart() {
  wireModeSwitch();

  try {
    const presets = await api('/presets');
    start.presets = Array.isArray(presets) ? presets.filter((p) => RUN_BUTTON[p.mode]) : [];
  } catch {
    start.presets = [];
  }
  if (!start.presets.length) return false;

  $('starters').hidden = false;
  runPreset(start.presets[0]);
  return true;
}

window.startExt = { boot: bootStart, setMode, applyPreset };
