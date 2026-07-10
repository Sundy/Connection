const auth = require('../../../services/auth')
const taskApi = require('../../../services/task')
const { previewSourceFile } = require('../../../utils/file-preview')

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

  openTask(e) {
    wx.navigateTo({ url: `/pages/student/task-detail/index?task_id=${e.currentTarget.dataset.id}` })
  },

  previewFile(e) {
    previewSourceFile(this.data.tasks[e.currentTarget.dataset.index].source_file)
  },

  goProfile() {
    wx.navigateTo({ url: '/pages/profile/index/index' })
  }
})
