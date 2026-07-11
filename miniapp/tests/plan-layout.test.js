const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

const root = path.resolve(__dirname, '..')

test('student and parent share a non-overflowing three-column date layout', () => {
  const appStyles = fs.readFileSync(path.join(root, 'app.wxss'), 'utf8')
  const student = fs.readFileSync(path.join(root, 'pages/student/today/index.wxml'), 'utf8')
  const parent = fs.readFileSync(path.join(root, 'pages/parent/plan-calendar/index.wxml'), 'utf8')

  assert.match(appStyles, /\.plan-date-nav[\s\S]*grid-template-columns:\s*minmax\(0, 1fr\)\s+minmax\(0, 1\.1fr\)\s+minmax\(0, 1fr\)/)
  assert.match(appStyles, /\.date-step[\s\S]*min-width:\s*0/)
  assert.match(appStyles, /\.date-picker-wrap[\s\S]*min-width:\s*0/)
  assert.match(student, /plan-header[\s\S]*plan-title[\s\S]*<\/view>\s*<view class="plan-toolbar">/)
  const studentHeader = student.match(/<view class="plan-header">([\s\S]*?)<\/view>\s*<view class="plan-toolbar">/)[1]
  assert.doesNotMatch(studentHeader, /plan-settings/)
  assert.match(parent, /plan-header[\s\S]*plan-title/)
})
