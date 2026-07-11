const reportApi = require('../../../services/report')
const { previewSourceFile } = require('../../../utils/file-preview')
const { resultViewState } = require('../../../utils/result-state')

Page({
  data: { result: { task: {}, result: null, submission: null, questions: [] }, viewState: resultViewState({ submission: null }), loadError: '', taskId: null, reviewLoading: false },
  onLoad(options) {
    this.setData({ taskId: options.task_id })
    this.loadResult()
  },
  loadResult() {
    reportApi.result(this.data.taskId).then((result) => this.setData({ result, viewState: resultViewState(result), loadError: '' })).catch((err) => {
      this.setData({ loadError: err.detail || '加载批改结果失败。' })
    })
  },
  confirmReview() {
    if (this.data.reviewLoading) return
    this.setData({ reviewLoading: true })
    reportApi.review(this.data.taskId, 'confirm').then(() => {
      wx.showToast({ title: '已确认批改结果', icon: 'success' })
      this.loadResult()
    }).catch((err) => wx.showToast({ title: err.detail || '确认失败', icon: 'none' }))
      .finally(() => this.setData({ reviewLoading: false }))
  },
  requestResubmit() {
    if (this.data.reviewLoading) return
    wx.showModal({
      title: '要求重新提交？',
      content: '学生需要重新拍摄清晰、完整的作业。',
      confirmText: '要求重交',
      success: (modal) => {
        if (!modal.confirm) return
        this.setData({ reviewLoading: true })
        reportApi.review(this.data.taskId, 'resubmit', '请重新拍摄清晰、完整的作业后提交。').then(() => {
          wx.showToast({ title: '已通知重新提交', icon: 'success' })
          this.loadResult()
        }).catch((err) => wx.showToast({ title: err.detail || '操作失败', icon: 'none' }))
          .finally(() => this.setData({ reviewLoading: false }))
      }
    })
  },
  previewFile() {
    previewSourceFile(this.data.result.task.source_file)
  }
})
