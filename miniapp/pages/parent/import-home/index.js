const auth = require('../../../services/auth')
const importApi = require('../../../services/import')
const { selectStoredStudent } = require('../../../utils/context-selection')

function todayText() {
  const now = new Date()
  const year = now.getFullYear()
  const month = String(now.getMonth() + 1).padStart(2, '0')
  const day = String(now.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

Page({
  data: {
    period_type: 'daily',
    start_date: '',
    end_date: '',
    students: [],
    studentNames: [],
    studentIndex: 0,
    selectedStudent: {},
    contextLoading: true,
    loadError: '',
    loading: false
  },

  onLoad() {
    const today = todayText()
    this.setData({ start_date: today, end_date: today })
    this.loadStudents()
  },

  loadStudents() {
    this.setData({ contextLoading: true, loadError: '' })
    auth.me().then((context) => {
      const students = context.students || []
      const app = getApp()
      const selectedStudent = selectStoredStudent(students, app.globalData.currentStudentId || wx.getStorageSync('currentStudentId'))
      const studentIndex = Math.max(students.findIndex((item) => item.id === selectedStudent.id), 0)
      app.globalData.currentStudent = selectedStudent
      app.globalData.currentStudentId = selectedStudent.id || null
      if (selectedStudent.id) wx.setStorageSync('currentStudentId', selectedStudent.id)
      else wx.removeStorageSync('currentStudentId')
      this.setData({
        students,
        studentNames: students.map((item) => item.name),
        studentIndex,
        selectedStudent,
        contextLoading: false
      })
    }).catch(() => this.setData({ contextLoading: false, loadError: '孩子信息加载失败，请重试' }))
  },

  onStudentChange(e) {
    const studentIndex = Number(e.detail.value)
    const selectedStudent = this.data.students[studentIndex] || {}
    const app = getApp()
    app.globalData.currentStudent = selectedStudent
    app.globalData.currentStudentId = selectedStudent.id || null
    if (selectedStudent.id) wx.setStorageSync('currentStudentId', selectedStudent.id)
    this.setData({ studentIndex, selectedStudent })
  },

  onStartDate(e) { this.setData({ start_date: e.detail.value }) },
  onEndDate(e) { this.setData({ end_date: e.detail.value }) },
  goProfile() { wx.redirectTo({ url: '/pages/profile/index/index' }) },

  next() {
    if (this.data.contextLoading || this.data.loading) return
    const student = this.data.selectedStudent
    if (!student.id) {
      wx.showToast({ title: '请学生通过家庭码先加入', icon: 'none' })
      return
    }
    if (this.data.end_date < this.data.start_date) {
      wx.showToast({ title: '结束日期不能早于开始日期', icon: 'none' })
      return
    }
    this.setData({ loading: true })
    importApi.createBatch({
      student_id: student.id,
      title: `${student.name} ${this.data.start_date} 学习计划`,
      period_type: this.data.period_type,
      start_date: this.data.start_date,
      end_date: this.data.end_date,
      raw_text: ''
    }).then((data) => {
      wx.navigateTo({ url: `/pages/parent/import-upload/index?batch_id=${data.id}` })
    }).catch((err) => {
      wx.showToast({ title: err.detail || '建立计划失败', icon: 'none' })
    }).finally(() => this.setData({ loading: false }))
  }
})
