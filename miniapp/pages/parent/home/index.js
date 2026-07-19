const auth = require('../../../services/auth')
const reportApi = require('../../../services/report')
const { selectStoredStudent } = require('../../../utils/context-selection')
const { startNotificationPolling, stopNotificationPolling } = require('../../../utils/notification-poller')

Page({
  data: {
    student: {},
    report: { today: {}, period: {} }
  },

  onShow() {
    return this.loadHome().then(() => this.startSubmissionPolling())
  },

  onHide() {
    stopNotificationPolling(this)
  },

  onUnload() {
    stopNotificationPolling(this)
  },

  loadHome() {
    const app = getApp()
    return auth.me().then((context) => {
      const student = selectStoredStudent(context.students, app.globalData.currentStudentId || wx.getStorageSync('currentStudentId'))
      app.globalData.currentStudent = student
      app.globalData.currentStudentId = student.id || null
      if (student.id) wx.setStorageSync('currentStudentId', student.id)
      this.setData({ student })
      if (student.id) {
        return reportApi.home(student.id)
      }
      return null
    }).then((report) => {
      if (report) {
        this.setData({ report })
        const planId = report.period && report.period.plan_id
        const app = getApp()
        app.globalData.currentPlanId = planId || null
        if (planId) wx.setStorageSync('currentPlanId', planId)
        else wx.removeStorageSync('currentPlanId')
      }
    }).catch(() => {})
  },

  startSubmissionPolling() {
    stopNotificationPolling(this)
    const studentId = this.data.student && this.data.student.id
    if (!studentId) return
    startNotificationPolling(this, {
      studentId,
      types: ['submission_uploaded'],
      onNotifications: () => this.loadHome()
    })
  },

  goImport() {
    wx.navigateTo({ url: '/pages/parent/import-home/index' })
  },

  goPlan() {
    const planId = this.data.report.period.plan_id
    if (planId) wx.navigateTo({ url: `/pages/parent/plan-calendar/index?plan_id=${planId}` })
  }
})
