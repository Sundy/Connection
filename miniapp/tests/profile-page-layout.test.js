const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

test('profile page shows profile form and removes parent child creation form', () => {
  const root = path.resolve(__dirname, '..')
  const markup = fs.readFileSync(path.join(root, 'pages/profile/index/index.wxml'), 'utf8')

  assert.match(markup, /个人资料/)
  assert.match(markup, /昵称/)
  assert.match(markup, /学校/)
  assert.match(markup, /年级/)
  assert.match(markup, /随机昵称/)
  assert.doesNotMatch(markup, /添加孩子/)
  assert.doesNotMatch(markup, /学生档案编号/)
})
