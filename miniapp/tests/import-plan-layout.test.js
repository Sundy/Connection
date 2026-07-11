const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

test('plan creation uses a child picker and automatic title', () => {
  const root = path.resolve(__dirname, '..')
  const markup = fs.readFileSync(path.join(root, 'pages/parent/import-home/index.wxml'), 'utf8')
  assert.match(markup, /为谁安排/)
  assert.match(markup, /mode="selector"/)
  assert.match(markup, /去添加孩子/)
  assert.doesNotMatch(markup, /计划名称|onTitle/)
  assert.match(markup, /添加作业资料/)
})
