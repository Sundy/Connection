const planApi = require('../../../services/plan')
const { previewSourceFile } = require('../../../utils/file-preview')
const { dateLabel, initialPlanDate, shiftDate } = require('../../../utils/date')
const { groupTasks, tasksForDate } = require('../../../utils/task-groups')

Page({
  data: { planId: null, plan: {}, items: [], selectedDate: '', dateLabel: '', selectedSubject: '全部', subjects: [], taskGroups: [] },
  onLoad(options) {
    this.setData({ planId: options.plan_id })
    const app = getApp()
    app.globalData.currentPlanId = Number(options.plan_id)
    wx.setStorageSync('currentPlanId', Number(options.plan_id))
    planApi.calendar(options.plan_id).then((data) => {
      const selectedDate = initialPlanDate(data.plan || {}, data.items || [])
      this.setData({ plan: data.plan || {}, items: data.items || [], selectedDate })
      this.refreshGroups()
    })
  },
  refreshGroups() {
    const tasks = tasksForDate(this.data.items, this.data.selectedDate)
    const grouped = groupTasks(tasks, this.data.selectedSubject)
    const selectedSubject = grouped.subjects.includes(this.data.selectedSubject) ? this.data.selectedSubject : '全部'
    const visible = groupTasks(tasks, selectedSubject)
    this.setData({ dateLabel: dateLabel(this.data.selectedDate), selectedSubject, subjects: visible.subjects, taskGroups: visible.groups })
  },
  changeDate(date) {
    this.setData({ selectedDate: date, selectedSubject: '全部' })
    this.refreshGroups()
  },
  previousDay() { this.changeDate(shiftDate(this.data.selectedDate, -1)) },
  nextDay() { this.changeDate(shiftDate(this.data.selectedDate, 1)) },
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
