const test = require('node:test')
const assert = require('node:assert/strict')
const { resultViewState } = require('../utils/result-state')

test('maps processing corrected review and failed states', () => {
  assert.equal(resultViewState({ submission: { status: 'processing' } }).shouldPoll, true)
  assert.equal(resultViewState({ submission: { status: 'corrected' }, result: {} }).kind, 'corrected')
  assert.equal(resultViewState({ submission: { status: 'needs_review' }, result: {} }).kind, 'needs_review')
  const failed = resultViewState({ submission: { status: 'failed', error_message: '批改失败' } })
  assert.equal(failed.kind, 'failed')
  assert.equal(failed.message, '批改失败')
  assert.equal(failed.shouldPoll, false)
})

test('provides useful empty and timeout states', () => {
  assert.equal(resultViewState({ submission: null }).kind, 'empty')
  assert.match(resultViewState({ submission: { status: 'processing' } }, true).message, /稍后/)
})

test('shows parent review waiting and resubmission as distinct student states', () => {
  const review = resultViewState({ submission: { status: 'needs_review' }, result: { review_reason: '字迹不清楚' } })
  assert.equal(review.kind, 'needs_review')
  assert.match(review.title, /等待家长/)
  const resubmit = resultViewState({ submission: { status: 'resubmit_required', error_message: '照片不清楚' } })
  assert.equal(resubmit.kind, 'resubmit_required')
  assert.equal(resubmit.message, '照片不清楚')
})
