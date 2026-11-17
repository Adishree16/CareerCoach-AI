const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

test('CSRF bootstrap and API requests include cross-origin credentials', () => {
  const apiSource = fs.readFileSync(
    path.join(__dirname, '../js/services/api.js'),
    'utf8',
  );
  const calls = apiSource.split('fetch(').slice(1);

  assert.equal(calls.length, 2, 'expected the CSRF bootstrap and shared API request');
  for (const call of calls) {
    const requestOptions = call.slice(0, call.indexOf('});'));
    assert.match(requestOptions, /credentials:\s*['"]include['"]/);
  }
});
