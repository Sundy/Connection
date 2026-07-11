const auth = require('../../../services/auth')
const taskApi = require('../../../services/task')
const { previewSourceFile } = require('../../../utils/file-preview')
const { dateLabel, shiftDate, todayIso } = require('../../../utils/date')
const { groupTasks } = require('../../../utils/task-groups')

Page({
  data: {
    date: '',
    selectedDate: '',
    isToday: true,
    dateLabel: '',
    selectedSubject: '全部',
    subjects: [],
    taskGroups: [],
    summary: {},
    tasks: [],
    loading: false
  },

  onShow() {
    if (!this.data.selectedDate) this.setData({ selectedDate: todayIso() })
    this.loadTasks()
  },

  loadTasks() {
    const app = getApp()
    this.setData({ loading: true })
    auth.me().then((context) => {
      const student = app.globalData.currentStudent || context.students[0] || {}
      app.globalData.currentStudent = student
      if (student.id) return taskApi.today(student.id, this.data.selectedDate)
      return { date: '', summary: {}, tasks: [] }
    }).then((data) => {
      const grouped = groupTasks(data.tasks || [], this.data.selectedSubject)
      const selectedSubject = grouped.subjects.includes(this.data.selectedSubject) ? this.data.selectedSubject : '全部'
      const visible = groupTasks(data.tasks || [], selectedSubject)
      this.setData({
        ...data,
        selectedSubject,
        subjects: visible.subjects,
        taskGroups: visible.groups,
        dateLabel: dateLabel(this.data.selectedDate),
        isToday: this.data.selectedDate === todayIso(),
        loading: false
      })
    }).catch((err) => {
      this.setData({ loading: false })
      wx.showToast({ title: err.detail || '加载任务失败', icon: 'none' })
    })
  },

  changeDate(date) {
    this.setData({ selectedDate: date, selectedSubject: '全部' })
    this.loadTasks()
  },

  previousDay() { this.changeDate(shiftDate(this.data.selectedDate, -1)) },
  nextDay() { this.changeDate(shiftDate(this.data.selectedDate, 1)) },
  backToday() { this.changeDate(todayIso()) },
  onDateChange(e) { this.changeDate(e.detail.value) },

  selectSubject(e) {
    const selectedSubject = e.currentTarget.dataset.subject
    this.setData({ selectedSubject, taskGroups: groupTasks(this.data.tasks, selectedSubject).groups })
  },

  startTask(e) {
    wx.navigateTo({ url: `/pages/student/focus-timer/index?task_id=${e.currentTarget.dataset.id}` })
  },

  openTask(e) {
    wx.navigateTo({ url: `/pages/student/task-detail/index?task_id=${e.currentTarget.dataset.id}` })
  },

  previewFile(e) {
    const task = this.data.tasks.find((item) => item.id === Number(e.currentTarget.dataset.id))
    if (task) previewSourceFile(task.source_file)
  },

  goProfile() {
    wx.navigateTo({ url: '/pages/profile/index/index' })
  }
})
