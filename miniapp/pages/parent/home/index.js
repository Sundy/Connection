const auth = require('../../../services/auth')
const reportApi = require('../../../services/report')

Page({
  data: {
    student: {},
    report: { today: {}, period: {} }
  },

  onShow() {
    const app = getApp()
    auth.me().then((context) => {
      const student = app.globalData.currentStudent || context.students[0] || {}
      app.globalData.currentStudent = student
      this.setData({ student })
      if (student.id) {
        return reportApi.home(student.id)
      }
      return null
    }).then((report) => {
      if (report) this.setData({ report })
    }).catch(() => {})
  },

  goImport() {
    wx.navigateTo({ url: '/pages/parent/import-home/index' })
  },

  goPlan() {
    const planId = this.data.report.period.plan_id
    if (planId) wx.navigateTo({ url: `/pages/parent/plan-calendar/index?plan_id=${planId}` })
  }
})
