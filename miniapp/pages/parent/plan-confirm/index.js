const planApi = require('../../../services/plan')

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

  confirm() {
    this.setData({ loading: true })
    planApi.confirm(this.data.planId, {}).then(() => {
      wx.redirectTo({ url: `/pages/parent/plan-calendar/index?plan_id=${this.data.planId}` })
    }).finally(() => this.setData({ loading: false }))
  }
})
