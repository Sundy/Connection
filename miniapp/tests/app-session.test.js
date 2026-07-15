const test = require('node:test')
const assert = require('node:assert/strict')

test('app launch hydrates the saved session synchronously', () => {
  const stored = {
    token: 'student-token',
    currentRole: 'student',
    currentStudentId: 12,
    currentPlanId: 34
  }
  let appConfig = null
  global.wx = {
    getStorageSync(key) {
      return stored[key]
    }
  }
  global.App = (config) => {
    appConfig = config
  }
  delete require.cache[require.resolve('../app')]
  require('../app')

  appConfig.onLaunch.call(appConfig)

  assert.equal(appConfig.globalData.token, 'student-token')
  assert.equal(appConfig.globalData.currentRole, 'student')
  assert.equal(appConfig.globalData.currentStudentId, 12)
  assert.equal(appConfig.globalData.currentPlanId, 34)
})
