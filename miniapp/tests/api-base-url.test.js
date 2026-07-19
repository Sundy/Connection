const test = require('node:test')
const assert = require('node:assert/strict')
const path = require('node:path')

const constantsPath = path.resolve(__dirname, '../utils/constants.js')

function loadForPlatform(platform) {
  const previousWx = global.wx
  global.wx = { getSystemInfoSync: () => ({ platform }) }
  delete require.cache[constantsPath]
  const constants = require(constantsPath)
  delete require.cache[constantsPath]
  global.wx = previousWx
  return constants.API_BASE_URL
}

test('developer tools use the local API', () => {
  assert.equal(loadForPlatform('devtools'), 'http://127.0.0.1:8000/api/v1')
})

test('phones use the deployed HTTPS API instead of their own loopback', () => {
  assert.equal(loadForPlatform('ios'), 'https://connection.aceflow.top/api/v1')
  assert.equal(loadForPlatform('android'), 'https://connection.aceflow.top/api/v1')
})
