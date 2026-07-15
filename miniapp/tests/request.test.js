const test = require('node:test')
const assert = require('node:assert/strict')

test('request errors retain the HTTP status code', async () => {
  global.getApp = () => ({ globalData: { token: 'expired' } })
  global.wx = {
    request(options) {
      options.success({ statusCode: 401, data: { detail: 'Invalid token' } })
    }
  }
  const { request } = require('../services/request')

  await assert.rejects(request({ url: '/auth/me' }), (err) => {
    assert.equal(err.statusCode, 401)
    assert.equal(err.detail, 'Invalid token')
    return true
  })
})
