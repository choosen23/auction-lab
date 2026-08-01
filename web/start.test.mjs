// node web/start.test.mjs
//
// web/start.js is the one file that writes the WHOLE setup form at once, so the
// thing that can go wrong is a leftover: a budget from the last preset still in
// the box, a world left open for a mode that never reads it, a package preset
// filling the scalar table. Each of those produces a run that succeeds and
// silently answers a different question than the chip promised.
//
// Same shape as random.test.mjs: stub the globals, run the file, inspect what it
// wrote. No DOM, no browser, no dependency.

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

// ------------------------------------------------------------ a fake form ---

function fakeNode(extra = {}) {
  return {
    hidden: false,
    open: false,
    value: '',
    textContent: '',
    dataset: {},
    children: [],
    listeners: {},
    clicked: 0,
    setAttribute(k, v) { this.attrs = { ...this.attrs, [k]: v }; },
    getAttribute(k) { return (this.attrs || {})[k]; },
    addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); },
    dispatchEvent(event) { for (const fn of this.listeners[event.type] || []) fn(); },
    click() { this.clicked += 1; this.dispatchEvent({ type: 'click' }); },
    append(child) { this.children.push(child); },
    replaceChildren(...kids) { this.children = kids; },
    querySelector(sel) { return sel === 'input' ? this.input : this.strategy; },
    querySelectorAll() { return this.all || []; },
    ...extra,
  };
}

function makeHarness(presets) {
  const nodes = {};
  const node = (id, extra) => (nodes[id] = fakeNode({ id, ...extra }));

  const modes = ['single', 'series', 'equilibrium'].map((m) =>
    fakeNode({ dataset: { mode: m } }),
  );
  node('mode-switch', { all: modes });
  for (const id of ['run', 'run-series', 'run-equilibrium']) node(id);
  for (const id of ['rounds-field', 'mode-desc', 'world-box', 'starters-now',
    'starter-chips', 'mechanism', 'rounds', 'world-seed', 'value-low', 'value-high',
    'bidder-rows', 'package-rows', 'budget-rows']) node(id);
  // Authored `hidden` in index.html: nothing reveals the starter bar except a
  // boot that actually got presets, so the fake has to start where the markup does.
  node('starters', { hidden: true });

  const written = { bidders: [], packages: [], strategyNotes: 0, budgetSyncs: 0, tracked: [] };

  // addBidderRow is app.js's; the stub records the row and hands back something
  // carrying a strategy select, which is what series.js would have added.
  const addBidderRow = (b) => {
    written.bidders.push(b);
    const tr = fakeNode({ strategy: fakeNode() });
    nodes['bidder-rows'].children.push(tr);
  };

  const sandbox = {
    Event: class { constructor(type) { this.type = type; } },
    $: (id) => nodes[id],
    h: (tag, className, text) => fakeNode({ tag, className, textContent: text || '' }),
    api: async (path) => {
      if (path === '/presets') return presets;
      throw new Error(`unexpected fetch: ${path}`);
    },
    clearError() {},
    // app.js's helper, stubbed. The real one is a no-op unless the page was served
    // with analytics configured, so start.js must never depend on its return value.
    track: (path, title) => written.tracked.push([path, title]),
    addBidderRow,
    addPackageRow: (p) => written.packages.push(p),
    syncStrategyRow() {},
    renderStrategyNotes() { written.strategyNotes += 1; },
    syncBudgetRows() {
      written.budgetSyncs += 1;
      // The real one rebuilds a row per bidder, each keyed by id and each
      // carrying whatever was already typed. Seed one with a stale number so a
      // preset that fails to clear it is caught.
      nodes['budget-rows'].children = written.bidders.map((b) =>
        fakeNode({ dataset: { bidder: b.id }, input: fakeNode({ value: '999' }) }),
      );
    },
    window: {},
    console,
  };

  vm.createContext(sandbox);
  vm.runInContext(readFileSync(new URL('./start.js', import.meta.url), 'utf8'), sandbox);
  return { sandbox, nodes, written, modes };
}

const budgets = (h) =>
  Object.fromEntries(h.nodes['budget-rows'].children.map((tr) => [tr.dataset.bidder, tr.input.value]));

// ----------------------------------------------------------- the fixtures ---

const SINGLE = {
  name: 'one', label: 'One', teaches: 'a', mechanism: 'mech_a', mode: 'single',
  kind: 'single', params: { reserve: 80 }, rounds: null, world: null,
  entrants: [{ id: 'A', value: 90, bid: 90 }, { id: 'B', value: 70, bid: 70 }],
};

const SERIES = {
  name: 'two', label: 'Two', teaches: 'b', mechanism: 'mech_b', mode: 'series',
  kind: 'single', params: {}, rounds: 20,
  world: { seed: 7, value_low: 40, value_high: 100, budgets: { A: 300 } },
  entrants: [
    { id: 'A', value: 70, bid: 70, strategy: 'strat_x' },
    { id: 'B', value: 70, bid: 70, strategy: 'strat_y' },
  ],
};

const PACKAGE = {
  name: 'three', label: 'Three', teaches: 'c', mechanism: 'mech_c', mode: 'single',
  kind: 'package', params: {}, rounds: null, world: null,
  entrants: [{ bidder: 'A', items: 'north, south', value: 100, bid: 100 }],
};

const EQ = { ...SINGLE, name: 'four', label: 'Four', mode: 'equilibrium', params: {} };

// ------------------------------------------------------------- the checks ---

// A single-run preset must not leave rounds, the world box, or a series button
// on screen — none of them is read, and all three were visible before phase 6.
{
  const harness = makeHarness([SINGLE]);
  harness.nodes['param-reserve'] = fakeNode();
  harness.sandbox.window.startExt.applyPreset(SINGLE);

  assert.equal(harness.nodes['param-reserve'].value, '80', 'declared param not written');
  assert.deepEqual(harness.written.bidders.map((b) => b.id), ['A', 'B']);
  assert.equal(harness.nodes['run'].hidden, false);
  assert.equal(harness.nodes['run-series'].hidden, true);
  assert.equal(harness.nodes['run-equilibrium'].hidden, true);
  assert.equal(harness.nodes['rounds-field'].hidden, true);
  assert.equal(harness.nodes['world-box'].hidden, true);
  assert.equal(harness.nodes['world-box'].open, false, 'a hidden world must not stay open');
}

// A series with a world fills it — and blanks every bidder the world did NOT
// budget, rather than leaving the previous preset's number behind.
{
  const harness = makeHarness([SERIES]);
  harness.sandbox.window.startExt.applyPreset(SERIES);

  assert.equal(harness.nodes['rounds'].value, '20');
  assert.equal(harness.nodes['world-box'].hidden, false);
  assert.equal(harness.nodes['world-box'].open, true, 'readWorld ignores a closed box');
  assert.equal(harness.nodes['world-seed'].value, '7');
  assert.equal(harness.nodes['value-low'].value, '40');
  assert.equal(harness.nodes['value-high'].value, '100');
  assert.deepEqual(budgets(harness), { A: '300', B: '' }, 'unbudgeted bidder kept a stale number');

  const strategies = harness.nodes['bidder-rows'].children.map((tr) => tr.strategy.value);
  assert.deepEqual(strategies, ['strat_x', 'strat_y']);
  assert.ok(harness.written.strategyNotes > 0, 'strategy notes never refreshed');
}

// Switching back to a preset with no world must close the box, or the previous
// preset's day silently applies to a plain series.
{
  const harness = makeHarness([SERIES, { ...SERIES, name: 'plain', world: null }]);
  harness.sandbox.window.startExt.applyPreset(SERIES);
  harness.sandbox.window.startExt.applyPreset({ ...SERIES, name: 'plain', world: null });
  assert.equal(harness.nodes['world-box'].open, false, 'a stale world survived');
}

// A package preset fills the package table and never the scalar one.
{
  const harness = makeHarness([PACKAGE]);
  harness.sandbox.window.startExt.applyPreset(PACKAGE);
  assert.equal(harness.written.bidders.length, 0, 'package preset wrote scalar rows');
  // Spread across the realm boundary: objects built inside the vm carry that
  // context's Object.prototype, which deepStrictEqual counts as a difference.
  assert.deepEqual(harness.written.packages.map((p) => ({ ...p })), [
    { bidder: 'A', items: 'north, south', value: 100, bid: 100 },
  ]);
}

// Equilibrium takes neither rounds nor a world.
{
  const harness = makeHarness([EQ]);
  harness.sandbox.window.startExt.applyPreset(EQ);
  assert.equal(harness.nodes['run-equilibrium'].hidden, false);
  assert.equal(harness.nodes['run'].hidden, true);
  assert.equal(harness.nodes['rounds-field'].hidden, true);
  assert.equal(harness.nodes['world-box'].hidden, true);
}

// Booting opens on the first preset, pressing its own mode's button exactly once.
{
  const harness = makeHarness([SERIES, SINGLE]);
  assert.equal(await harness.sandbox.window.startExt.boot(), true);
  assert.equal(harness.nodes['starters'].hidden, false);
  assert.equal(harness.nodes['run-series'].clicked, 1);
  assert.equal(harness.nodes['run'].clicked, 0, 'pressed a button the preset did not name');
  assert.equal(harness.nodes['starter-chips'].children.length, 2);
  assert.equal(harness.nodes['starters-now'].textContent, SERIES.teaches);
}

// A preset naming a mode the UI has no button for is dropped, not rendered as a
// chip that throws when pressed.
{
  const harness = makeHarness([{ ...SINGLE, mode: 'teleport' }]);
  assert.equal(await harness.sandbox.window.startExt.boot(), false);
  assert.equal(harness.nodes['starters'].hidden, true);
}

// No presets, or a server that cannot supply them, must leave the form usable
// and tell app.js to run its own default instead.
for (const presets of [[], null]) {
  const harness = makeHarness(presets);
  assert.equal(await harness.sandbox.window.startExt.boot(), false);
  assert.equal(harness.nodes['starters'].hidden, true);
  assert.equal(harness.nodes['run'].hidden, false, 'the mode switch must still have wired');
}

// The mode switch works on its own, without any preset involved.
{
  const harness = makeHarness([]);
  await harness.sandbox.window.startExt.boot();
  harness.modes.find((b) => b.dataset.mode === 'series').click();
  assert.equal(harness.nodes['run-series'].hidden, false);
  assert.equal(harness.nodes['rounds-field'].hidden, false);
  assert.equal(harness.nodes['world-box'].hidden, false);
  assert.equal(harness.modes[1].getAttribute('aria-pressed'), 'true');
  assert.equal(harness.modes[0].getAttribute('aria-pressed'), 'false');
  assert.deepEqual(harness.written.tracked, [['mode/series', 'Mode: series']]);
}

// What gets counted is a *click*. Booting runs the first preset on everyone's
// behalf, so counting that would inflate whichever example happens to be first
// by exactly one per visit — the chip would look popular because it is default.
{
  const harness = makeHarness([SINGLE, SERIES]);
  await harness.sandbox.window.startExt.boot();
  assert.deepEqual(harness.written.tracked, [], 'the opening example is not a click');

  harness.nodes['starter-chips'].children[1].click();
  assert.deepEqual(harness.written.tracked, [['preset/two', 'Two']]);
  assert.equal(harness.nodes['run-series'].clicked, 1, 'the clicked chip still ran');

  harness.written.tracked.length = 0;
  harness.sandbox.window.startExt.applyPreset(SERIES);
  assert.deepEqual(harness.written.tracked, [], 'applyPreset must not count as a click');
}

console.log('ok');
