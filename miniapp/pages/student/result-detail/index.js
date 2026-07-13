const reportApi = require('../../../services/report')
const { downloadCorrectionPage } = require('../../../services/correction-media')
const { resultViewState } = require('../../../utils/result-state')

Page({
  data: {
    taskId: null,
    result: { task: {}, result: null, submission: null, questions: [], pages: [] },
    viewState: resultViewState({ submission: null }),
    pollCount: 0,
    loadError: '',
    refreshError: ''
  },

  onLoad(options) {
    this.setData({ taskId: options.task_id })
  },

  onShow() {
    if (this.data.taskId) this.refresh()
  },

  onHide() {
    this.stopPolling()
  },

  onUnload() {
    this.stopPolling()
  },

  stopPolling() {
    if (this.pollTimer) clearTimeout(this.pollTimer)
    this.pollTimer = null
  },

  preparePages(result) {
    return Promise.all((result.pages || []).map((page) => {
      return downloadCorrectionPage(page.image_url)
        .then((localImageUrl) => Object.assign({}, page, { localImageUrl, total_pages: result.pages.length }))
        .catch(() => Object.assign({}, page, { localImageUrl: '', total_pages: result.pages.length, imageError: true }))
    })).then((pages) => Object.assign({}, result, { pages }))
  },

  schedulePoll() {
    this.stopPolling()
    const delay = this.data.pollCount < 10 ? 2000 : 5000
    this.pollTimer = setTimeout(() => this.refresh(), delay)
  },

  refresh() {
    return reportApi.result(this.data.taskId).then((result) => {
      const prepareResult = (result.pages || []).length ? this.preparePages(result) : Promise.resolve(result)
      return prepareResult.then((preparedResult) => {
        const pollCount = this.data.pollCount + 1
        const viewState = resultViewState(preparedResult, pollCount >= 60)
        this.setData({ result: preparedResult, viewState, pollCount, loadError: '', refreshError: '' })
        if (viewState.shouldPoll) this.schedulePoll()
        else this.stopPolling()
      })
    }).catch((err) => {
      const message = err.detail || '网络异常，点击重试。'
      const hasCurrentDisplay = Boolean(this.data.result && (this.data.result.submission || this.data.result.result))
      if (!hasCurrentDisplay) {
        this.setData({ loadError: message, refreshError: '' })
        return
      }
      this.setData({ loadError: '', refreshError: message })
      if (this.data.viewState.shouldPoll) this.schedulePoll()
    })
  },

  retryLoad() { this.refresh() },

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

  resubmit() {
    wx.redirectTo({ url: `/pages/student/upload-homework/index?task_id=${this.data.taskId}` })
  },

  backToday() {
    wx.redirectTo({ url: '/pages/student/today/index' })
  }
})
