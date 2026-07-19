const planApi = require('../../../services/plan')
const { previewSourceFile } = require('../../../utils/file-preview')
const { dateLabel, initialPlanDate, todayIso } = require('../../../utils/date')
const { groupTasks, tasksForDate } = require('../../../utils/task-groups')
const { startNotificationPolling, stopNotificationPolling } = require('../../../utils/notification-poller')

Page({
  data: { planId: null, plan: {}, items: [], selectedDate: '', dateLabel: '', isToday: true, selectedSubject: '全部', subjects: [], taskGroups: [] },
  onLoad(options) {
    this.firstShowPending = true
    this.setData({ planId: options.plan_id })
    const app = getApp()
    app.globalData.currentPlanId = Number(options.plan_id)
    wx.setStorageSync('currentPlanId', Number(options.plan_id))
    return this.loadCalendar().then(() => this.startSubmissionPolling())
  },
  onShow() {
    if (this.firstShowPending) {
      this.firstShowPending = false
      return null
    }
    return this.loadCalendar().then(() => this.startSubmissionPolling())
  },
  onHide() {
    stopNotificationPolling(this)
  },
  onUnload() {
    stopNotificationPolling(this)
  },
  loadCalendar() {
    if (!this.data.planId) return Promise.resolve(null)
    return planApi.calendar(this.data.planId).then((data) => {
      const selectedDate = this.data.selectedDate || initialPlanDate(data.plan || {}, data.items || [])
      this.setData({ plan: data.plan || {}, items: data.items || [], selectedDate })
      this.refreshGroups()
      return data
    })
  },
  startSubmissionPolling() {
    stopNotificationPolling(this)
    const app = getApp()
    const studentId = app.globalData.currentStudentId || wx.getStorageSync('currentStudentId')
    if (!studentId) return
    startNotificationPolling(this, {
      studentId,
      types: ['submission_uploaded'],
      onNotifications: () => this.loadCalendar()
    })
  },
  refreshGroups() {
    const tasks = tasksForDate(this.data.items, this.data.selectedDate)
    const grouped = groupTasks(tasks, this.data.selectedSubject)
    const selectedSubject = grouped.subjects.includes(this.data.selectedSubject) ? this.data.selectedSubject : '全部'
    const visible = groupTasks(tasks, selectedSubject)
    this.setData({ dateLabel: dateLabel(this.data.selectedDate), isToday: this.data.selectedDate === todayIso(), selectedSubject, subjects: visible.subjects, taskGroups: visible.groups })
  },
  changeDate(date) {
    this.setData({ selectedDate: date, selectedSubject: '全部' })
    this.refreshGroups()
  },
  backToday() { this.changeDate(todayIso()) },
  onDateChange(e) { this.changeDate(e.detail.value) },
  selectSubject(e) {
    const selectedSubject = e.currentTarget.dataset.subject
    const tasks = tasksForDate(this.data.items, this.data.selectedDate)
    this.setData({ selectedSubject, taskGroups: groupTasks(tasks, selectedSubject).groups })
  },
  openTask(e) {
    wx.navigateTo({ url: `/pages/parent/task-result/index?task_id=${e.currentTarget.dataset.id}` })
  },
  previewFile(e) {
    const task = this.data.items.find((item) => item.id === Number(e.currentTarget.dataset.id))
    if (task) previewSourceFile(task.source_file)
  }
})
