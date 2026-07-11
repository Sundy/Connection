const test = require('node:test')
const assert = require('node:assert/strict')
const { dateLabel, shiftDate } = require('../utils/date')

test('shifts local ISO dates across month and year boundaries', () => {
  assert.equal(shiftDate('2026-12-31', 1), '2027-01-01')
  assert.equal(shiftDate('2026-03-01', -1), '2026-02-28')
})

test('labels today yesterday tomorrow and regular dates', () => {
  assert.match(dateLabel('2026-07-11', '2026-07-11'), /^今天 · 7月11日/)
  assert.match(dateLabel('2026-07-10', '2026-07-11'), /^昨天 · 7月10日/)
  assert.match(dateLabel('2026-07-12', '2026-07-11'), /^明天 · 7月12日/)
  assert.match(dateLabel('2026-07-15', '2026-07-11'), /^7月15日 · 星期/)
})
