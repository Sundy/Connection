const taskApi = require('../../../services/task')

Page({
  data: { task: {} },
  onLoad(options) {
    this.setData({ taskId: options.task_id })
    taskApi.detail(options.task_id).then((task) => this.setData({ task }))
  },
  start() {
    wx.navigateTo({ url: `/pages/student/focus-timer/index?task_id=${this.data.taskId}` })
  }
})
