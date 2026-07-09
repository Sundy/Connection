const taskApi = require('../../../services/task')
const studyApi = require('../../../services/study')
const { formatDuration } = require('../../../utils/format')

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

  onUnload() {
    if (this.timer) clearInterval(this.timer)
  },

  tick() {
    if (this.timer) clearInterval(this.timer)
    this.timer = setInterval(() => {
      if (!this.data.running) return
      const elapsed = this.data.elapsed + 1
      this.setData({ elapsed, display: formatDuration(elapsed) })
    }, 1000)
  },

  start() {
    studyApi.start(Number(this.data.taskId)).then((session) => {
      this.setData({ sessionId: session.session_id, running: true, statusText: '专注中' })
      this.tick()
    })
  },

  pause() {
    studyApi.pause(this.data.sessionId).then(() => this.setData({ running: false, statusText: '已暂停' }))
  },

  resume() {
    studyApi.resume(this.data.sessionId).then(() => this.setData({ running: true, statusText: '专注中' }))
  },

  upload() {
    wx.navigateTo({ url: `/pages/student/upload-homework/index?task_id=${this.data.taskId}&session_id=${this.data.sessionId || ''}` })
  }
})
