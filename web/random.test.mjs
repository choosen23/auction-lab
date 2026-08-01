// node web/random.test.mjs
//
// web/random.js writes into the setup form, so the check stubs the four globals
// it reaches for and collects what it would have typed. What it must never
// produce is a setup a person could not have typed themselves: a bid above the
// value, a bundle with the same item twice, or a problem past the engine's
// exhaustive-search limits (agt/winner_determination.py, mirrored in packages.js).

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const MAX_BIDS = 20;
const MAX_ITEMS = 12;

function runWith({ count, packageMode }) {
  const single = [];
  const packages = [];
  const rows = { 'bidder-rows': [], 'package-rows': [] };

  const nodes = {
    'rand-count': { value: String(count) },
    randomize: { addEventListener() {} },
  };

  const sandbox = {
    $: (id) => nodes[id] || { ...(rows[id] ? { children: rows[id] } : {}), replaceChildren() {} },
    isNum: (v) => typeof v === 'number' && Number.isFinite(v),
    clamp: (v, lo, hi) => Math.min(hi, Math.max(lo, v)),
    clearError() {},
    addBidderRow: (b) => single.push(b),
    addPackageRow: (p) => packages.push(p),
    series: { registry: null },
    window: { packagesExt: { active: () => packageMode } },
  };
  vm.createContext(sandbox);
  vm.runInContext(readFileSync(new URL('./random.js', import.meta.url), 'utf8'), sandbox);
  vm.runInContext('randomise()', sandbox);
  return { single, packages };
}

for (let trial = 0; trial < 200; trial += 1) {
  const { single } = runWith({ count: 5, packageMode: false });
  assert.equal(single.length, 5);
  assert.equal(new Set(single.map((b) => b.id)).size, 5, 'bidder ids must be unique');
  for (const b of single) {
    assert.ok(b.value > 0 && b.bid > 0, `positive numbers, got ${b.value}/${b.bid}`);
    assert.ok(b.bid <= b.value, `${b.id} bid ${b.bid} above value ${b.value}`);
  }

  const { packages } = runWith({ count: 8, packageMode: true });
  assert.ok(packages.length <= MAX_BIDS, `${packages.length} package bids exceeds ${MAX_BIDS}`);
  const universe = new Set();
  for (const p of packages) {
    const items = p.items.split(/[,\s]+/).filter(Boolean);
    assert.ok(items.length > 0, 'a bundle of nothing wins for free');
    assert.equal(new Set(items).size, items.length, `${p.bidder} listed an item twice`);
    assert.ok(p.bid <= p.value);
    for (const item of items) universe.add(item);
  }
  assert.ok(universe.size <= MAX_ITEMS, `${universe.size} items exceeds ${MAX_ITEMS}`);
}

// The count input is free text from a person; out-of-range must clamp, not throw.
assert.equal(runWith({ count: 99, packageMode: false }).single.length, 8);
assert.equal(runWith({ count: 0, packageMode: false }).single.length, 1);
assert.equal(runWith({ count: 'x', packageMode: false }).single.length, 3);

console.log('ok');
