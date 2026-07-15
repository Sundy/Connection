const session = require('../../../services/session')

function getStorage(key) {
  try {
    return wx.getStorageSync(key)
  } catch (err) {
    console.warn(`[storage] read ${key} failed`, err)
    return ''
  }
}

Page({
  data: {
    viewState: 'initializing',
    loadingRole: '',
    errorMessage: ''
  },

  onLoad() {
    const role = getStorage('currentRole')
    if (!session.isValidRole(role)) {
      this.setData({ viewState: 'selecting' })
      return
    }
    this.restoreSession(role)
  },

  restoreSession(role) {
    if (this.restoring) return
    this.restoring = true
    this.setData({ viewState: 'initializing', errorMessage: '' })
    session.restore(role, getStorage('token')).then((result) => {
      wx.reLaunch({ url: result.url })
    }).catch((err) => {
      this.setData({
        viewState: 'error',
        errorMessage: err.detail || '请检查网络后重试'
      })
    }).finally(() => {
      this.restoring = false
    })
  },

  retryRestore() {
    const role = getStorage('currentRole')
    if (!session.isValidRole(role)) {
      this.setData({ viewState: 'selecting', errorMessage: '' })
      return
    }
    this.restoreSession(role)
  },

  loginParent() {
    this.doLogin('parent')
  },

  loginStudent() {
    this.doLogin('student')
  },

  doLogin(role) {
    if (this.data.loadingRole) return
    this.setData({ loadingRole: role })
    session.loginAs(role).then((result) => {
      wx.reLaunch({ url: result.url })
    }).catch((err) => {
      wx.showToast({ title: err.detail || '登录失败', icon: 'none' })
    }).finally(() => {
      this.setData({ loadingRole: '' })
    })
  }
})
