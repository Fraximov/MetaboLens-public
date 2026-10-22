// Load the actual DT filter bundle from stdin without a browser or report data.
// Minimal DOM/jQuery stubs suffice for library registration, not widget rendering.
// Debian's non-standalone replacement fails here because MicroPlugin is absent.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const errors = [];
const jquery = function () {};
jquery.fn = {};
jquery.extend = Object.assign;
const context = {
  jQuery: jquery,
  navigator: { userAgent: 'Dependency smoke test' },
  document: { createElement: () => ({ validity: {} }) },
  console: { error: message => errors.push(message), group() {}, groupEnd() {} },
};
context.window = context;
try {
  vm.runInNewContext(fs.readFileSync(0, 'utf8'), context, { timeout: 1000 });
} catch (error) {
  console.error([...errors, error.message].join('\n'));
  process.exit(1);
}
assert.deepEqual(errors, []);
assert.equal(typeof context.Selectize, 'function');
assert.equal(typeof jquery.fn.selectize, 'function');
console.log('DT standalone Selectize bundle initializes successfully');
