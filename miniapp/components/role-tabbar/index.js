const { navigationItems, navigationTarget } = require('../../utils/role-navigation')

Component({
  properties: {
    active: { type: String, value: '' }
  },

  data: {
    items: []
  },

  lifetimes: {
    attached() {
      const app = getApp()
      const role = app.globalData.currentRole || wx.getStorageSync('currentRole') || 'student'
      this.role = role
      this.setData({ items: navigationItems(role) })
    }
  },

  methods: {
    navigate(e) {
      const key = e.currentTarget.dataset.key
      if (key === this.data.active) return
      const planId = getApp().globalData.currentPlanId || wx.getStorageSync('currentPlanId')
      const target = navigationTarget(this.role, key, planId)
      if (target.missingPlan) wx.showToast({ title: '还没有学习计划，请先导入作业', icon: 'none' })
      wx.redirectTo({ url: target.url })
    }
  }
})
