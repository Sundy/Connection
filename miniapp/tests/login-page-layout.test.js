const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

test('login page separates initialization, first choice, and retry states', () => {
  const root = path.resolve(__dirname, '..')
  const markup = fs.readFileSync(path.join(root, 'pages/auth/login/index.wxml'), 'utf8')
  const controller = fs.readFileSync(path.join(root, 'pages/auth/login/index.js'), 'utf8')

  assert.match(markup, /viewState === 'initializing'/)
  assert.match(markup, /viewState === 'selecting'/)
  assert.match(markup, /viewState === 'error'/)
  assert.match(markup, /重试/)
  assert.match(controller, /onLoad\(\)/)
  assert.match(controller, /session\.restore/)
  assert.match(controller, /wx\.reLaunch/)
  assert.doesNotMatch(controller, /wx\.redirectTo/)
})
