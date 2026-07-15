const test = require('node:test')
const assert = require('node:assert/strict')

const parentContext = {
  user: { id: 1, role: 'parent', nickname: '家长' },
  family: { id: 10, name: '家长的家庭' },
  students: [{ id: 20, name: '小禾' }]
}

const studentContext = {
  user: { id: 2, role: 'student', nickname: '学生' },
  family: null,
  students: []
}

function createFixture(options = {}) {
  const values = {
    token: options.token || 'parent-token',
    currentRole: options.currentRole || 'parent'
  }
  const storage = {
    values,
    getStorageSync(key) {
      return values[key]
    },
    setStorageSync(key, value) {
      values[key] = value
    },
    removeStorageSync(key) {
      delete values[key]
    }
  }
  const loginCalls = []
  const authApi = {
    loginCalls,
    async login(role) {
      loginCalls.push(role)
      return { token: `${role}-token`, user: { role } }
    },
    async me() {
      return parentContext
    }
  }
  const app = {
    globalData: {
      token: options.token || 'parent-token',
      currentRole: options.currentRole || 'parent',
      currentUser: parentContext.user,
      currentFamily: parentContext.family,
      currentStudent: parentContext.students[0],
      currentStudentId: parentContext.students[0].id
    }
  }
  return { app, storage, authApi }
}

test('restores a valid parent session without logging in again', async () => {
  const { restore } = require('../services/session')
  const fixture = createFixture({ token: 'cached-token' })
  fixture.authApi.me = async () => parentContext

  const result = await restore('parent', 'cached-token', fixture)

  assert.equal(fixture.authApi.loginCalls.length, 0)
  assert.equal(result.url, '/pages/parent/home/index')
  assert.equal(fixture.app.globalData.currentRole, 'parent')
  assert.equal(fixture.storage.values.currentStudentId, 20)
})

test('silently logs in with the stored role after a 401', async () => {
  const { restore } = require('../services/session')
  const fixture = createFixture({ token: 'expired-token', currentRole: 'student' })
  let meCalls = 0
  fixture.authApi.me = async () => {
    meCalls += 1
    if (meCalls === 1) throw { statusCode: 401 }
    return studentContext
  }

  const result = await restore('student', 'expired-token', fixture)

  assert.deepEqual(fixture.authApi.loginCalls, ['student'])
  assert.equal(fixture.storage.values.token, 'student-token')
  assert.equal(fixture.storage.values.currentRole, 'student')
  assert.equal(result.url, '/pages/student/today/index')
})

test('relogs when the cached token belongs to another role', async () => {
  const { restore } = require('../services/session')
  const fixture = createFixture({ token: 'parent-token', currentRole: 'student' })
  let meCalls = 0
  fixture.authApi.me = async () => {
    meCalls += 1
    return meCalls === 1 ? parentContext : studentContext
  }

  await restore('student', 'parent-token', fixture)

  assert.deepEqual(fixture.authApi.loginCalls, ['student'])
  assert.equal(fixture.app.globalData.currentRole, 'student')
})

test('does not silently login after a network error', async () => {
  const { restore } = require('../services/session')
  const fixture = createFixture({ token: 'cached-token' })
  fixture.authApi.me = async () => { throw { detail: 'network down' } }

  await assert.rejects(restore('parent', 'cached-token', fixture), (err) => err.detail === 'network down')

  assert.equal(fixture.authApi.loginCalls.length, 0)
  assert.equal(fixture.app.globalData.token, 'cached-token')
})

test('does not replace the active session when switching fails', async () => {
  const { loginAs } = require('../services/session')
  const fixture = createFixture({ token: 'parent-token', currentRole: 'parent' })
  fixture.authApi.me = async () => { throw { detail: 'network down' } }

  await assert.rejects(loginAs('student', fixture), (err) => err.detail === 'network down')

  assert.equal(fixture.app.globalData.token, 'parent-token')
  assert.equal(fixture.app.globalData.currentRole, 'parent')
  assert.equal(fixture.storage.values.token, 'parent-token')
  assert.equal(fixture.storage.values.currentRole, 'parent')
})

test('validates roles and resolves role homes', async () => {
  const { isValidRole, loginAs, roleHome } = require('../services/session')
  const fixture = createFixture()

  assert.equal(isValidRole('parent'), true)
  assert.equal(isValidRole('teacher'), false)
  assert.equal(roleHome('student'), '/pages/student/today/index')
  assert.equal(roleHome('teacher'), '')
  await assert.rejects(loginAs('teacher', fixture), /无效身份/)
})
