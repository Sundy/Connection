const test = require('node:test')
const assert = require('node:assert/strict')
const { submissionHasHomework } = require('../utils/submission-state')

test('requires a persisted homework media record before completing', () => {
  assert.equal(submissionHasHomework({ homework_media_count: 1 }), true)
  assert.equal(submissionHasHomework({ homework_media_count: 0 }), false)
  assert.equal(submissionHasHomework(null), false)
})
