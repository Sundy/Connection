const importApi = require('../../../services/import')

Page({
  data: {
    title: '寒假作业计划',
    period_type: 'winter_vacation',
    start_date: '',
    end_date: '',
    raw_text: '',
    loading: false
  },

  onTitle(e) { this.setData({ title: e.detail.value }) },
  onRawText(e) { this.setData({ raw_text: e.detail.value }) },
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
      raw_text: this.data.raw_text
    }).then((data) => {
      wx.navigateTo({ url: `/pages/parent/import-upload/index?batch_id=${data.id}` })
    }).finally(() => this.setData({ loading: false }))
  }
})
