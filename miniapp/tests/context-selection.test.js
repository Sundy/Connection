const test = require('node:test')
const assert = require('node:assert/strict')
const { selectStoredStudent } = require('../utils/context-selection')

test('restores a valid stored student and falls back when it is invalid', () => {
  assert.equal(selectStoredStudent([{ id: 2 }, { id: 3 }], 3).id, 3)
  assert.equal(selectStoredStudent([{ id: 2 }], 99).id, 2)
  assert.deepEqual(selectStoredStudent([], 2), {})
})
