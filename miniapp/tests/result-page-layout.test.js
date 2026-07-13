const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')
const assert = require('node:assert/strict')

test('student result uses full annotated pages instead of question cards', () => {
  const root = path.join(__dirname, '..')
  const pageWxml = fs.readFileSync(path.join(root, 'pages/student/result-detail/index.wxml'), 'utf8')
  const pageJson = JSON.parse(fs.readFileSync(path.join(root, 'pages/student/result-detail/index.json'), 'utf8'))
  const componentWxml = fs.readFileSync(path.join(root, 'components/annotated-homework-page/index.wxml'), 'utf8')

  assert.match(pageWxml, /wx:for="{{result.pages}}"/)
  assert.match(pageWxml, /annotated-homework-page/)
  assert.doesNotMatch(pageWxml, /wx:for="{{result.questions}}"/)
  assert.equal(pageJson.usingComponents['annotated-homework-page'], '/components/annotated-homework-page/index')
  assert.match(componentWxml, /annotation-error_circle/)
  assert.match(componentWxml, /annotation-correct_tick/)
})
