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
    importApi.getBatch(this.data.batchId).then((batch) => this.setData({ batch }))
  },

  generate() {
    this.setData({ loading: true })
    planApi.generate(this.data.batchId).then((data) => {
      wx.navigateTo({ url: `/pages/parent/plan-confirm/index?plan_id=${data.assignment_batch_id}` })
    }).finally(() => this.setData({ loading: false }))
  }
})
