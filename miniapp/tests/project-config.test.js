const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

test('does not drop CommonJS utility modules from development builds', () => {
  const root = path.resolve(__dirname, '..')
  const publicConfig = JSON.parse(fs.readFileSync(path.join(root, 'project.config.json'), 'utf8'))
  const privateConfig = JSON.parse(fs.readFileSync(path.join(root, 'project.private.config.json'), 'utf8'))
  assert.equal(publicConfig.setting.ignoreDevUnusedFiles, false)
  assert.equal(privateConfig.setting.ignoreDevUnusedFiles, false)
})
