const reportApi = require('../../../services/report')

Page({
  data: {
    taskId: null,
    result: { task: {}, result: null },
    timer: null
  },

  onLoad(options) {
    this.setData({ taskId: options.task_id })
    this.refresh()
    this.data.timer = setInterval(() => this.refresh(), 2000)
  },

  onUnload() {
    if (this.data.timer) clearInterval(this.data.timer)
  },

  refresh() {
    reportApi.result(this.data.taskId).then((result) => {
      this.setData({ result })
      if (result.result && this.data.timer) {
        clearInterval(this.data.timer)
        this.setData({ timer: null })
      }
    }).catch(() => {})
  },

  backToday() {
    wx.redirectTo({ url: '/pages/student/today/index' })
  }
})
