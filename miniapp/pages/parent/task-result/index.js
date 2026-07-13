const reportApi = require('../../../services/report')
const { downloadCorrectionPage } = require('../../../services/correction-media')
const { previewSourceFile } = require('../../../utils/file-preview')
const { resultViewState } = require('../../../utils/result-state')

Page({
  data: { result: { task: {}, result: null, submission: null, questions: [], pages: [] }, viewState: resultViewState({ submission: null }), loadError: '', taskId: null, reviewLoading: false },
  onLoad(options) {
    this.setData({ taskId: options.task_id })
    this.loadResult()
  },
  preparePages(result) {
    return Promise.all((result.pages || []).map((page) => {
      return downloadCorrectionPage(page.image_url)
        .then((localImageUrl) => Object.assign({}, page, { localImageUrl, total_pages: result.pages.length }))
        .catch(() => Object.assign({}, page, { localImageUrl: '', total_pages: result.pages.length, imageError: true }))
    })).then((pages) => Object.assign({}, result, { pages }))
  },
  loadResult() {
    reportApi.result(this.data.taskId).then((result) => {
      const prepareResult = (result.pages || []).length ? this.preparePages(result) : Promise.resolve(result)
      return prepareResult.then((preparedResult) => this.setData({
        result: preparedResult,
        viewState: resultViewState(preparedResult),
        loadError: ''
      }))
    }).catch((err) => {
      this.setData({ loadError: err.detail || '加载批改结果失败。' })
    })
  },
  retryPageImage(e) {
    const mediaId = e.detail.mediaId
    const page = (this.data.result.pages || []).find((item) => item.media_id === mediaId)
    if (!page) return
    downloadCorrectionPage(page.image_url).then((localImageUrl) => {
      const pages = this.data.result.pages.map((item) => item.media_id === mediaId
        ? Object.assign({}, item, { localImageUrl, imageError: false })
        : item)
      this.setData({ 'result.pages': pages })
    }).catch(() => wx.showToast({ title: '作业图片加载失败', icon: 'none' }))
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
