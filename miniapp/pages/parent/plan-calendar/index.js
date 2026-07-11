const planApi = require('../../../services/plan')
const { previewSourceFile } = require('../../../utils/file-preview')
const { dateLabel, initialPlanDate, shiftDate } = require('../../../utils/date')
const { groupTasks, tasksForDate } = require('../../../utils/task-groups')

Page({
  data: { planId: null, plan: {}, items: [], selectedDate: '', dateLabel: '', taskGroups: [] },
  onLoad(options) {
    this.setData({ planId: options.plan_id })
    planApi.calendar(options.plan_id).then((data) => {
      const selectedDate = initialPlanDate(data.plan || {}, data.items || [])
      this.setData({ plan: data.plan || {}, items: data.items || [], selectedDate })
      this.refreshGroups()
    })
  },
  refreshGroups() {
    const tasks = tasksForDate(this.data.items, this.data.selectedDate)
    this.setData({ dateLabel: dateLabel(this.data.selectedDate), taskGroups: groupTasks(tasks).groups })
  },
  changeDate(date) {
    if (this.data.plan.start_date && date < this.data.plan.start_date) return
    if (this.data.plan.end_date && date > this.data.plan.end_date) return
    this.setData({ selectedDate: date })
    this.refreshGroups()
  },
  previousDay() { this.changeDate(shiftDate(this.data.selectedDate, -1)) },
  nextDay() { this.changeDate(shiftDate(this.data.selectedDate, 1)) },
  onDateChange(e) { this.changeDate(e.detail.value) },
  openTask(e) {
    wx.navigateTo({ url: `/pages/parent/task-result/index?task_id=${e.currentTarget.dataset.id}` })
  },
  previewFile(e) {
    const groupIndex = e.currentTarget.dataset.group
    const itemIndex = e.currentTarget.dataset.index
    previewSourceFile(this.data.taskGroups[groupIndex].tasks[itemIndex].source_file)
  }
})
