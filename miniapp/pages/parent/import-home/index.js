const importApi = require('../../../services/import')

function todayText() {
  const now = new Date()
  const year = now.getFullYear()
  const month = String(now.getMonth() + 1).padStart(2, '0')
  const day = String(now.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

Page({
  data: {
    title: '',
    period_type: 'daily',
    start_date: '',
    end_date: '',
    loading: false
  },

  onLoad() {
    const today = todayText()
    this.setData({
      title: `${today} 作业`,
      start_date: today,
      end_date: today
    })
  },

  onTitle(e) { this.setData({ title: e.detail.value }) },
  onStartDate(e) { this.setData({ start_date: e.detail.value }) },
  onEndDate(e) { this.setData({ end_date: e.detail.value }) },

  next() {
    const app = getApp()
    const student = app.globalData.currentStudent
    if (!student || !student.id) {
      wx.showToast({ title: '未选择孩子', icon: 'none' })
      return
    }
    this.setData({ loading: true })
    importApi.createBatch({
      student_id: student.id,
      title: this.data.title,
      period_type: this.data.period_type,
      start_date: this.data.start_date || null,
      end_date: this.data.end_date || null,
      raw_text: ''
    }).then((data) => {
      wx.navigateTo({ url: `/pages/parent/import-upload/index?batch_id=${data.id}` })
    }).finally(() => this.setData({ loading: false }))
  }
})
