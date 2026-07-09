const taskApi = require('../../../services/task')
const submissionApi = require('../../../services/submission')

Page({
  data: {
    taskId: null,
    sessionId: null,
    task: {},
    submissionId: null,
    media: [],
    loading: false
  },

  onLoad(options) {
    this.setData({ taskId: options.task_id, sessionId: options.session_id || null })
    taskApi.detail(options.task_id).then((task) => this.setData({ task }))
  },

  ensureSubmission(type) {
    if (this.data.submissionId) return Promise.resolve(this.data.submissionId)
    return submissionApi.create({
      daily_task_id: Number(this.data.taskId),
      submission_type: type,
      linked_study_session_id: this.data.sessionId ? Number(this.data.sessionId) : null
    }).then((data) => {
      this.setData({ submissionId: data.submission_id })
      return data.submission_id
    })
  },

  chooseImages() {
    wx.chooseMedia({
      count: 9,
      mediaType: ['image'],
      success: (res) => this.uploadPaths(res.tempFiles.map((item) => item.tempFilePath), 'image', 'photo')
    })
  },

  chooseVideo() {
    wx.chooseMedia({
      count: 1,
      mediaType: ['video'],
      success: (res) => this.uploadPaths(res.tempFiles.map((item) => item.tempFilePath), 'video', 'video')
    })
  },

  uploadPaths(paths, mediaType, submissionType) {
    this.ensureSubmission(submissionType).then((submissionId) => {
      return Promise.all(paths.map((path, index) => submissionApi.uploadMedia(submissionId, path, mediaType, this.data.media.length + index)))
    }).then((uploaded) => this.setData({ media: this.data.media.concat(uploaded) }))
  },

  submit() {
    if (!this.data.submissionId) {
      wx.showToast({ title: '请先上传作业', icon: 'none' })
      return
    }
    this.setData({ loading: true })
    submissionApi.complete(this.data.submissionId).then(() => {
      wx.redirectTo({ url: `/pages/student/result-detail/index?task_id=${this.data.taskId}` })
    }).finally(() => this.setData({ loading: false }))
  }
})
