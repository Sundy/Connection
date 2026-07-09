const reportApi = require('../../../services/report')

Page({
  data: {
    taskId: null,
    result: { task: {}, result: null }
  },

  onLoad(options) {
    this.setData({ taskId: options.task_id })
    this.refresh()
  },

  refresh() {
    reportApi.result(this.data.taskId).then((result) => this.setData({ result }))
  },

  backToday() {
    wx.redirectTo({ url: '/pages/student/today/index' })
  }
})
