const taskApi = require('../../../services/task')
const studyApi = require('../../../services/study')
const submissionApi = require('../../../services/submission')
const { previewSourceFile } = require('../../../utils/file-preview')
const { submissionHasHomework } = require('../../../utils/submission-state')

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
    this.unloaded = false
    this.submissionReady = null
    const sessionId = options.session_id ? Number(options.session_id) : null
    this.setData({ taskId: options.task_id, sessionId })
    this.sessionReady = options.session_id
      ? Promise.resolve(sessionId)
      : studyApi.active(Number(options.task_id)).then((session) => {
        const activeSessionId = session ? session.session_id : null
        if (!this.unloaded) this.setData({ sessionId: activeSessionId })
        return activeSessionId
      }).catch((err) => {
        if (!this.unloaded) wx.showToast({ title: err.detail || '恢复计时失败', icon: 'none' })
        return null
      })
    taskApi.detail(options.task_id).then((task) => {
      if (!this.unloaded) this.setData({ task })
    })
  },

  onUnload() {
    this.unloaded = true
  },

  ensureSubmission(type) {
    if (this.submissionReady) return this.submissionReady
    if (this.data.submissionId) return Promise.resolve(this.data.submissionId)
    const creation = this.sessionReady.then((sessionId) => submissionApi.create({
      daily_task_id: Number(this.data.taskId),
      submission_type: type,
      linked_study_session_id: sessionId
    })).then((data) => {
      if (!this.unloaded) this.setData({ submissionId: data.submission_id })
      return data.submission_id
    })
    this.submissionReady = creation.catch((err) => {
      this.submissionReady = null
      throw err
    })
    return this.submissionReady
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

  uploadPaths(paths, mediaType, submissionType) {
    this.ensureSubmission(submissionType).then((submissionId) => {
      return Promise.all(paths.map((path, index) => submissionApi.uploadMedia(submissionId, path, mediaType, this.data.media.length + index)))
    }).then((uploaded) => {
      this.setData({ media: this.data.media.concat(uploaded) })
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
    submissionApi.detail(this.data.submissionId).then((detail) => {
      if (!submissionHasHomework(detail)) throw { detail: '未找到已上传的作业，请重新上传图片或视频' }
      return submissionApi.complete(this.data.submissionId)
    }).then(() => {
      wx.redirectTo({ url: `/pages/student/result-detail/index?task_id=${this.data.taskId}` })
    }).catch((err) => {
      wx.showToast({ title: err.detail || '提交失败', icon: 'none' })
    }).finally(() => this.setData({ loading: false }))
  }
})
