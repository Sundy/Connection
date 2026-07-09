const auth = require('../../../services/auth')

Page({
  data: {
    loadingRole: ''
  },

  loginParent() {
    this.doLogin('parent')
  },

  loginStudent() {
    this.doLogin('student')
  },

  doLogin(role) {
    const app = getApp()
    this.setData({ loadingRole: role })
    auth.login(role).then((data) => {
      app.globalData.token = data.token
      app.globalData.currentUser = data.user
      app.globalData.currentRole = role
      wx.setStorageSync('token', data.token)
      wx.setStorageSync('currentRole', role)
      return auth.me()
    }).then((context) => {
      app.globalData.currentFamily = context.family
      app.globalData.currentStudent = context.students[0] || null
      wx.setStorageSync('currentStudentId', app.globalData.currentStudent && app.globalData.currentStudent.id)
      wx.redirectTo({ url: role === 'parent' ? '/pages/parent/home/index' : '/pages/student/today/index' })
    }).catch(() => {
      wx.showToast({ title: '登录失败', icon: 'none' })
    }).finally(() => {
      this.setData({ loadingRole: '' })
    })
  }
})
