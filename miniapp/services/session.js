const auth = require('./auth')
const { selectStoredStudent } = require('../utils/context-selection')

const HOME_BY_ROLE = {
  parent: '/pages/parent/home/index',
  student: '/pages/student/today/index'
}

function isValidRole(role) {
  return Boolean(HOME_BY_ROLE[role])
}

function roleHome(role) {
  return HOME_BY_ROLE[role] || ''
}

function resolveDependencies(options = {}) {
  return {
    app: options.app || getApp(),
    storage: options.storage || wx,
    authApi: options.authApi || auth
  }
}

function safeSetStorage(storage, key, value) {
  try {
    storage.setStorageSync(key, value)
  } catch (err) {
    console.warn(`[storage] write ${key} failed`, err)
  }
}

function safeRemoveStorage(storage, key) {
  try {
    storage.removeStorageSync(key)
  } catch (err) {
    console.warn(`[storage] remove ${key} failed`, err)
  }
}

function selectedStudent(context, storage) {
  let storedStudentId = null
  try {
    storedStudentId = storage.getStorageSync('currentStudentId')
  } catch (err) {
    console.warn('[storage] read currentStudentId failed', err)
  }
  return selectStoredStudent(context.students, storedStudentId)
}

function commitSession(role, token, loginData, context, dependencies) {
  const { app, storage } = dependencies
  const student = selectedStudent(context, storage)

  app.globalData.token = token
  app.globalData.currentUser = context.user || loginData.user || null
  app.globalData.currentRole = role
  app.globalData.currentFamily = context.family || null
  app.globalData.currentStudent = student
  app.globalData.currentStudentId = student.id || null

  safeSetStorage(storage, 'token', token)
  safeSetStorage(storage, 'currentRole', role)
  if (student.id) safeSetStorage(storage, 'currentStudentId', student.id)
  else safeRemoveStorage(storage, 'currentStudentId')

  return { role, url: roleHome(role), context }
}

function roleMismatch(context, role) {
  return !context.user || context.user.role !== role
}

async function loginAs(role, options = {}) {
  if (!isValidRole(role)) throw new Error('无效身份')

  const dependencies = resolveDependencies(options)
  const { app, authApi } = dependencies
  const previousToken = app.globalData.token
  const loginData = await authApi.login(role)
  app.globalData.token = loginData.token

  try {
    const context = await authApi.me()
    if (roleMismatch(context, role)) throw { detail: '登录身份不匹配' }
    return commitSession(role, loginData.token, loginData, context, dependencies)
  } catch (err) {
    app.globalData.token = previousToken
    throw err
  }
}

async function restore(role, token, options = {}) {
  if (!isValidRole(role)) throw new Error('无效身份')
  if (!token) return loginAs(role, options)

  const dependencies = resolveDependencies(options)
  const { app, authApi } = dependencies
  const previousToken = app.globalData.token
  app.globalData.token = token

  try {
    const context = await authApi.me()
    if (roleMismatch(context, role)) return loginAs(role, options)
    return commitSession(role, token, {}, context, dependencies)
  } catch (err) {
    if (err && err.statusCode === 401) return loginAs(role, options)
    app.globalData.token = previousToken
    throw err
  }
}

module.exports = {
  isValidRole,
  loginAs,
  restore,
  roleHome
}
