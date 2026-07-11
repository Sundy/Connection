const planApi = require('../../../services/plan')
const { previewSourceFile } = require('../../../utils/file-preview')

Page({
  data: {
    planId: null,
    draft: { plan: {}, assignment_items: [], daily_preview: [] },
    loading: false
  },

  onLoad(options) {
    this.setData({ planId: options.plan_id })
    planApi.draft(options.plan_id).then((draft) => this.setData({ draft }))
  },

  openSourceFile(e) {
    const item = this.data.draft.assignment_items[e.currentTarget.dataset.index]
    if (!item || !item.source_file) return
    previewSourceFile(item.source_file)
  },

  confirm() {
    this.setData({ loading: true })
    planApi.confirm(this.data.planId, {}).then(() => {
      const app = getApp()
      app.globalData.currentPlanId = Number(this.data.planId)
      wx.setStorageSync('currentPlanId', Number(this.data.planId))
      wx.redirectTo({ url: `/pages/parent/plan-calendar/index?plan_id=${this.data.planId}` })
    }).finally(() => this.setData({ loading: false }))
  }
})
