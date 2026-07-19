const test = require('node:test')
const assert = require('node:assert/strict')
const { buildJoinPayload, parseInviteCode } = require('../utils/family-invite')

test('family invite payload round trips invite codes for scanning', () => {
  const payload = buildJoinPayload('fam-000001')

  assert.equal(payload, 'connection://join-family?invite_code=FAM-000001')
  assert.equal(parseInviteCode(payload), 'FAM-000001')
  assert.equal(parseInviteCode('fam-000001'), 'FAM-000001')
  assert.equal(parseInviteCode('bad-code'), '')
})
