const planApi = require('../../../services/plan')
const { previewSourceFile } = require('../../../utils/file-preview')

Page({
  data: { planId: null, items: [] },
  onLoad(options) {
    this.setData({ planId: options.plan_id })
    planApi.calendar(options.plan_id).then((data) => this.setData({ items: data.items || [] }))
  },
  openTask(e) {
    wx.navigateTo({ url: `/pages/parent/task-result/index?task_id=${e.currentTarget.dataset.id}` })
  },
  previewFile(e) {
    previewSourceFile(this.data.items[e.currentTarget.dataset.index].source_file)
  }
})
