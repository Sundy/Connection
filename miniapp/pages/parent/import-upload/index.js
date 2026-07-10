const importApi = require('../../../services/import')
const planApi = require('../../../services/plan')

function fileTypeFromPath(path) {
  const lower = (path || '').toLowerCase()
  if (lower.endsWith('.pdf')) return 'pdf'
  if (lower.endsWith('.doc') || lower.endsWith('.docx')) return 'docx'
  if (lower.endsWith('.xls') || lower.endsWith('.xlsx')) return 'xlsx'
  return 'file'
}

Page({
  data: {
    batchId: null,
    files: [],
    rawText: '',
    loading: false,
    progressText: ''
  },

  onLoad(options) {
    this.setData({ batchId: options.batch_id })
  },

  onRawText(e) {
    this.setData({ rawText: e.detail.value })
  },

  chooseImages() {
    wx.chooseMedia({
      count: 9,
      mediaType: ['image'],
      success: (res) => this.uploadPaths(res.tempFiles.map((item) => item.tempFilePath), 'image')
    })
  },

  chooseFiles() {
    wx.chooseMessageFile({
      count: 9,
      type: 'file',
      success: (res) => this.uploadSelectedFiles(res.tempFiles)
    })
  },

  uploadSelectedFiles(files) {
    const tasks = files.map((file, index) => importApi.uploadFile(
      this.data.batchId,
      file.path,
      fileTypeFromPath(file.name || file.path),
      this.data.files.length + index,
      file.name || ''
    ))
    Promise.all(tasks).then((uploaded) => {
      this.setData({ files: this.data.files.concat(uploaded.map(this.normalizeUploadedFile)) })
    }).catch((err) => {
      wx.showToast({ title: err.detail || '上传失败', icon: 'none' })
    })
  },

  uploadPaths(paths, fileType) {
    const tasks = paths.map((path, index) => importApi.uploadFile(this.data.batchId, path, fileType, this.data.files.length + index))
    Promise.all(tasks).then((uploaded) => {
      this.setData({ files: this.data.files.concat(uploaded.map(this.normalizeUploadedFile)) })
    }).catch((err) => {
      wx.showToast({ title: err.detail || '上传失败', icon: 'none' })
    })
  },

  normalizeUploadedFile(file) {
    return Object.assign({}, file, {
      status_text: '已添加，生成计划时识别'
    })
  },

  pollParsedBatch() {
    let attempts = 0
    return new Promise((resolve, reject) => {
      const tick = () => {
        importApi.getBatch(this.data.batchId).then((batch) => {
          const parsed = batch.parsed_file_count || 0
          const total = batch.file_count || 0
          if (total) {
            this.setData({ progressText: `正在识别文件 ${parsed}/${total}` })
          }
          if (batch.status === 'parsed') {
            resolve(batch)
            return
          }
          if (batch.status === 'failed') {
            reject({ detail: '资料解析失败，请重新上传' })
            return
          }
          attempts += 1
          if (attempts >= 80) {
            reject({ detail: '生成计划超时，请稍后重试' })
            return
          }
          setTimeout(tick, 1500)
        }).catch(reject)
      }
      tick()
    })
  },

  generatePlan() {
    if (this.data.loading) return
    if (!this.data.files.length && !this.data.rawText.trim()) {
      wx.showToast({ title: '请先添加作业资料', icon: 'none' })
      return
    }
    this.setData({ loading: true, progressText: '正在生成作业计划' })
    importApi.updateBatch(this.data.batchId, {
      raw_text: this.data.rawText
    }).then(() => importApi.parseBatch(this.data.batchId))
      .then(() => this.pollParsedBatch())
      .then(() => planApi.generate(this.data.batchId))
      .then((data) => {
        wx.navigateTo({ url: `/pages/parent/plan-confirm/index?plan_id=${data.assignment_batch_id}` })
      }).catch((err) => {
        wx.showToast({ title: err.detail || '生成计划失败', icon: 'none' })
      }).finally(() => this.setData({ loading: false, progressText: '' }))
  }
})
