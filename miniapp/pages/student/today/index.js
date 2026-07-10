const auth = require('../../../services/auth')
const taskApi = require('../../../services/task')

Page({
  data: {
    date: '',
    summary: {},
    tasks: []
  },

  onShow() {
    const app = getApp()
    auth.me().then((context) => {
      const student = app.globalData.currentStudent || context.students[0] || {}
      app.globalData.currentStudent = student
      if (student.id) return taskApi.today(student.id)
      return { date: '', summary: {}, tasks: [] }
    }).then((data) => this.setData(data))
  },

  startTask(e) {
    wx.navigateTo({ url: `/pages/student/focus-timer/index?task_id=${e.currentTarget.dataset.id}` })
  },

  goProfile() {
    wx.navigateTo({ url: '/pages/profile/index/index' })
  }
})
