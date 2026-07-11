const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

const root = path.resolve(__dirname, '..')

test('student and parent use one calendar button beside the date title', () => {
  const appStyles = fs.readFileSync(path.join(root, 'app.wxss'), 'utf8')
  const student = fs.readFileSync(path.join(root, 'pages/student/today/index.wxml'), 'utf8')
  const parent = fs.readFileSync(path.join(root, 'pages/parent/plan-calendar/index.wxml'), 'utf8')

  assert.match(appStyles, /\.plan-header[\s\S]*grid-template-columns:\s*minmax\(0, 1fr\)\s+auto/)
  assert.match(appStyles, /\.calendar-button/)
  assert.match(student, /plan-header[\s\S]*plan-title/)
  assert.doesNotMatch(student, /plan-settings|家庭设置/)
  assert.match(parent, /plan-header[\s\S]*plan-title/)
  ;[student, parent].forEach((markup) => {
    assert.match(markup, /picker[\s\S]*calendar-button/)
    assert.doesNotMatch(markup, /前一天|后一天|plan-date-nav/)
  })
})
