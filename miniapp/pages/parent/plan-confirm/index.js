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

  onAnswerInput(e) {
    const index = e.currentTarget.dataset.index
    this.setData({ [`draft.assignment_items[${index}].answer_text`]: e.detail.value })
  },

  confirm() {
    this.setData({ loading: true })
    const adjustments = this.data.draft.assignment_items.map((item) => ({
      id: item.id,
      answer_text: item.answer_text || ''
    }))
    planApi.confirm(this.data.planId, { adjustments }).then(() => {
      wx.redirectTo({ url: `/pages/parent/plan-calendar/index?plan_id=${this.data.planId}` })
    }).finally(() => this.setData({ loading: false }))
  }
})
