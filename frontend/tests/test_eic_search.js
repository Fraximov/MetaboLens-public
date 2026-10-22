// Run with: node frontend/tests/test_eic_search.js
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

global.window = {dash_clientside: {}};
const asset = path.join(__dirname, '../assets/eic_search.js');
assert.ok(fs.existsSync(asset), 'The browser EIC filter must be implemented');
vm.runInThisContext(fs.readFileSync(asset, 'utf8'));
const filter = window.dash_clientside.eic.filterOptions;
const archive = 'bundle:128:90';
const options = [
    {value: 'opaque/0', label: 'Caffeine', search: 'caffeine 195.08765 1.23 [m+h]+', flags: ['reference', 'lowscore']},
    {value: 'engine/id:2', label: 'Unmatched Reference standard', search: 'unmatched reference standard 200.123', flags: []},
    {value: '0', label: 'Caffeine isomer', search: 'caffeine isomer 195.08765 2.50', flags: ['unmatched']},
    ...Array.from({length: 250}, (_, i) => ({value: `id${i}`, label: `Compound ${i}`, search: `compound ${i}`, flags: ['reference']})),
];
const catalogue = {archive, options};
const run = (query, selected = null, source = catalogue, selectedArchive = archive) =>
    filter(source, query, selected, selectedArchive);
const ids = rows => rows.map(row => row.value);

assert.equal(run('').length, 253, 'Opening and clearing must expose every feature');
assert.deepEqual(ids(run('  REFERENCE   caffeine  ')), ['opaque/0'], 'Semantic tokens AND text tokens are case insensitive');
assert.deepEqual(ids(run('unmatched')), ['0'], 'Labels cannot turn a semantic flag on');
assert.deepEqual(ids(run('lowscore 195.087')), ['opaque/0']);
assert.deepEqual(ids(run('[m+h]+ 1.23')), ['opaque/0']);
assert.equal(run('reference').length, 251, 'Matching lists must not be capped at 50 or 200');
const selected = {bundle: archive, feature_id: 'engine/id:2'};
assert.deepEqual(ids(run('lowscore', selected)), ['opaque/0', 'engine/id:2'], 'Search must keep a selected feature outside matches');
assert.ok(run('reference caffeine')[0].search.includes('reference caffeine'), 'Dash textual filtering must retain semantic matches');
assert.equal(run(null).length, 253, 'Clearing after a semantic search restores the full catalogue');
assert.deepEqual(run('reference', selected, catalogue, 'bundle:129:92'), [], 'Old catalogue cannot appear after an archive change');
assert.deepEqual(ids(run('lowscore', {...selected, bundle: 'bundle:129:92'})), ['opaque/0']);
assert.deepEqual(run('anything', null, null), []);
const legacy = {archive: 91, options: [{value: 0, label: 'Legacy', search: 'legacy', flags: []}]};
assert.deepEqual(ids(run('reference', {feature_index: 0}, legacy, 91)), [0], 'Legacy index zero stays selected');
assert.equal(options.length, 253, 'Filtering never mutates the source catalogue');
console.log('EIC browser search regression checks passed');
