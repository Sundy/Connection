const planApi = require('../../../services/plan')

Page({
  data: { planId: null, items: [] },
  onLoad(options) {
    this.setData({ planId: options.plan_id })
    planApi.calendar(options.plan_id).then((data) => this.setData({ items: data.items || [] }))
  },
  openTask(e) {
    wx.navigateTo({ url: `/pages/parent/task-result/index?task_id=${e.currentTarget.dataset.id}` })
  }
})
