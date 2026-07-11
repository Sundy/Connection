const test = require('node:test')
const assert = require('node:assert/strict')
const { profileVisibility } = require('../utils/profile-visibility')

test('shows family management only to parents', () => {
  const parent = profileVisibility('parent', true)
  assert.equal(parent.showInvite, true)
  assert.equal(parent.showChildren, true)
  assert.equal(parent.showAddChild, true)
  assert.equal(parent.showJoin, true)
})

test('students can only join when they do not have a family', () => {
  const bound = profileVisibility('student', true)
  assert.equal(bound.showInvite, false)
  assert.equal(bound.showAddChild, false)
  assert.equal(bound.showJoin, false)
  assert.equal(profileVisibility('student', false).showJoin, true)
})
