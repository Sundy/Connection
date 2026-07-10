const importApi = require('../../../services/import')
const planApi = require('../../../services/plan')

Page({
  data: {
    batchId: null,
    batch: {},
    loading: false,
    timer: null
  },

  onLoad(options) {
    this.setData({ batchId: options.batch_id })
    this.refresh()
    this.data.timer = setInterval(() => this.refresh(), 1500)
  },

  onUnload() {
    if (this.data.timer) clearInterval(this.data.timer)
  },

  refresh() {
    importApi.getBatch(this.data.batchId).then((batch) => this.setData({ batch })).catch(() => {})
  },

  generate() {
    if (this.data.batch.status !== 'parsed') {
      wx.showToast({ title: '资料还在解析中', icon: 'none' })
      return
    }
    this.setData({ loading: true })
    planApi.generate(this.data.batchId).then((data) => {
      wx.navigateTo({ url: `/pages/parent/plan-confirm/index?plan_id=${data.assignment_batch_id}` })
    }).catch((err) => {
      wx.showToast({ title: err.detail || '生成计划失败', icon: 'none' })
    }).finally(() => this.setData({ loading: false }))
  }
})
