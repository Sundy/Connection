const taskApi = require('../../../services/task')
const studyApi = require('../../../services/study')
const { formatDuration } = require('../../../utils/format')
const { previewSourceFile } = require('../../../utils/file-preview')

Page({
  data: {
    taskId: null,
    task: {},
    sessionId: null,
    elapsed: 0,
    display: '0:00',
    running: false,
    statusText: '准备开始'
  },

  onLoad(options) {
    this.setData({ taskId: options.task_id })
    taskApi.detail(options.task_id).then((task) => this.setData({ task }))
  },

  onShow() {
    this.isVisible = true
    return this.restoreActiveSession()
  },

  onHide() {
    this.isVisible = false
    this.recoveryVersion = (this.recoveryVersion || 0) + 1
    this.clearTimer()
  },

  onUnload() {
    this.isVisible = false
    this.recoveryVersion = (this.recoveryVersion || 0) + 1
    this.clearTimer()
  },

  clearTimer() {
    if (this.timer !== undefined && this.timer !== null) clearInterval(this.timer)
    this.timer = null
  },

  tick() {
    this.clearTimer()
    this.timer = setInterval(() => {
      if (!this.data.running) return
      const elapsed = this.data.elapsed + 1
      this.setData({ elapsed, display: formatDuration(elapsed) })
    }, 1000)
  },

  restoreActiveSession() {
    const recoveryVersion = (this.recoveryVersion || 0) + 1
    this.recoveryVersion = recoveryVersion
    return studyApi.active(Number(this.data.taskId)).then((session) => {
      if (!this.isVisible || recoveryVersion !== this.recoveryVersion) return null
      if (!session) {
        this.clearTimer()
        this.setData({ sessionId: null, elapsed: 0, display: formatDuration(0), running: false, statusText: '准备开始' })
        return null
      }
      const elapsed = Number(session.elapsed_seconds || 0)
      this.setData({
        sessionId: session.session_id,
        elapsed,
        display: formatDuration(elapsed),
        running: true,
        statusText: '计时中'
      })
      this.tick()
      return session
    })
  },

  start() {
    const recoveryVersion = (this.recoveryVersion || 0) + 1
    this.recoveryVersion = recoveryVersion
    return studyApi.start(Number(this.data.taskId)).then((session) => {
      if (!this.isVisible || recoveryVersion !== this.recoveryVersion) return session
      const elapsed = Number(session.elapsed_seconds || 0)
      this.setData({
        sessionId: session.session_id,
        elapsed,
        display: formatDuration(elapsed),
        running: true,
        statusText: '计时中'
      })
      this.tick()
      return session
    })
  },

  upload() {
    wx.navigateTo({ url: `/pages/student/upload-homework/index?task_id=${this.data.taskId}&session_id=${this.data.sessionId || ''}` })
  },

  previewFile() {
    previewSourceFile(this.data.task.source_file)
  }
})
