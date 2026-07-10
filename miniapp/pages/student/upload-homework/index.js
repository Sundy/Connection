const taskApi = require('../../../services/task')
const submissionApi = require('../../../services/submission')
const { previewSourceFile } = require('../../../utils/file-preview')

Page({
  data: {
    taskId: null,
    sessionId: null,
    task: {},
    submissionId: null,
    media: [],
    answerMedia: [],
    answerText: '',
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
      linked_study_session_id: this.data.sessionId ? Number(this.data.sessionId) : null,
      answer_text: this.data.answerText
    }).then((data) => {
      this.setData({ submissionId: data.submission_id })
      return data.submission_id
    })
  },

  onAnswerText(e) {
    this.setData({ answerText: e.detail.value })
  },

  previewFile() {
    previewSourceFile(this.data.task.source_file)
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

  chooseAnswerImages() {
    wx.chooseMedia({
      count: 9,
      mediaType: ['image'],
      success: (res) => this.uploadPaths(res.tempFiles.map((item) => item.tempFilePath), 'image', 'photo', 'answer')
    })
  },

  chooseAnswerFiles() {
    wx.chooseMessageFile({
      count: 3,
      type: 'file',
      success: (res) => this.uploadPaths(res.tempFiles.map((item) => item.path), 'file', 'photo', 'answer')
    })
  },

  uploadPaths(paths, mediaType, submissionType, purpose = 'homework') {
    this.ensureSubmission(submissionType).then((submissionId) => {
      const current = purpose === 'answer' ? this.data.answerMedia : this.data.media
      return Promise.all(paths.map((path, index) => submissionApi.uploadMedia(submissionId, path, mediaType, current.length + index, purpose)))
    }).then((uploaded) => {
      if (purpose === 'answer') {
        this.setData({ answerMedia: this.data.answerMedia.concat(uploaded) })
      } else {
        this.setData({ media: this.data.media.concat(uploaded) })
      }
    }).catch((err) => {
      wx.showToast({ title: err.detail || '上传失败', icon: 'none' })
    })
  },

  submit() {
    if (!this.data.submissionId || !this.data.media.length) {
      wx.showToast({ title: '请先上传作业', icon: 'none' })
      return
    }
    this.setData({ loading: true })
    submissionApi.update(this.data.submissionId, {
      answer_text: this.data.answerText
    }).then(() => submissionApi.complete(this.data.submissionId)).then(() => {
      wx.redirectTo({ url: `/pages/student/result-detail/index?task_id=${this.data.taskId}` })
    }).catch((err) => {
      wx.showToast({ title: err.detail || '提交失败', icon: 'none' })
    }).finally(() => this.setData({ loading: false }))
  }
})
