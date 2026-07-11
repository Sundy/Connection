const reportApi = require('../../../services/report')
const { resultViewState } = require('../../../utils/result-state')

Page({
  data: {
    taskId: null,
    result: { task: {}, result: null, submission: null, questions: [] },
    viewState: resultViewState({ submission: null }),
    pollCount: 0,
    loadError: ''
  },

  onLoad(options) {
    this.setData({ taskId: options.task_id })
    this.refresh()
    this.pollTimer = setInterval(() => this.refresh(), 2000)
  },

  onUnload() {
    this.stopPolling()
  },

  stopPolling() {
    if (this.pollTimer) clearInterval(this.pollTimer)
    this.pollTimer = null
  },

  refresh() {
    reportApi.result(this.data.taskId).then((result) => {
      const pollCount = this.data.pollCount + 1
      const viewState = resultViewState(result, pollCount >= 60)
      this.setData({ result, viewState, pollCount, loadError: '' })
      if (!viewState.shouldPoll) this.stopPolling()
    }).catch((err) => this.setData({ loadError: err.detail || '网络异常，点击重试。' }))
  },

  retryLoad() { this.refresh() },

  resubmit() {
    wx.redirectTo({ url: `/pages/student/upload-homework/index?task_id=${this.data.taskId}` })
  },

  backToday() {
    wx.redirectTo({ url: '/pages/student/today/index' })
  }
})
