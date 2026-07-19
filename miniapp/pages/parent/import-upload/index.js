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
    homeworkFiles: [],
    answerFiles: [],
    batch: null,
    rawText: '',
    loading: false,
    progressText: ''
  },

  onLoad(options) {
    this.setData({ batchId: options.batch_id })
    return Promise.all([
      importApi.getBatch(options.batch_id),
      importApi.listFiles(options.batch_id)
    ]).then(([batch, files]) => {
      this.applyFiles(files)
      this.setData({ batch })
      return batch
    }).catch((err) => {
      wx.showToast({ title: err.detail || '加载上传资料失败', icon: 'none' })
    })
  },

  onRawText(e) {
    this.setData({ rawText: e.detail.value })
  },

  chooseImages(e) {
    const documentRole = (e && e.currentTarget.dataset.documentRole) || 'homework'
    wx.chooseMedia({
      count: 9,
      mediaType: ['image'],
      success: (res) => this.uploadPaths(
        res.tempFiles.map((item) => item.tempFilePath),
        'image',
        documentRole
      )
    })
  },

  chooseFiles(e) {
    const documentRole = (e && e.currentTarget.dataset.documentRole) || 'homework'
    wx.chooseMessageFile({
      count: 9,
      type: 'file',
      success: (res) => this.uploadSelectedFiles(res.tempFiles, documentRole)
    })
  },

  uploadSelectedFiles(files, documentRole = 'homework') {
    const sortOrder = this.data.homeworkFiles.length + this.data.answerFiles.length
    const tasks = files.map((file, index) => importApi.uploadFile(
      this.data.batchId,
      file.path,
      fileTypeFromPath(file.name || file.path),
      sortOrder + index,
      file.name || '',
      documentRole
    ))
    return Promise.all(tasks).then(() => this.refreshFiles()).catch((err) => {
      return this.refreshFiles().catch(() => {}).then(() => {
        wx.showToast({ title: err.detail || '上传失败', icon: 'none' })
      })
    })
  },

  uploadPaths(paths, fileType, documentRole = 'homework') {
    const sortOrder = this.data.homeworkFiles.length + this.data.answerFiles.length
    const tasks = paths.map((path, index) => importApi.uploadFile(
      this.data.batchId,
      path,
      fileType,
      sortOrder + index,
      '',
      documentRole
    ))
    return Promise.all(tasks).then(() => this.refreshFiles()).catch((err) => {
      return this.refreshFiles().catch(() => {}).then(() => {
        wx.showToast({ title: err.detail || '上传失败', icon: 'none' })
      })
    })
  },

  fileStatus(file) {
    if (file.parse_status === 'failed') {
      return {
        status_kind: 'error',
        status_text: `解析失败：${file.parse_error || '文件内容无法解析'}`
      }
    }
    if (file.recognition_status === 'failed') {
      const label = file.document_role === 'answer' ? '答案' : '作业'
      return {
        status_kind: 'error',
        status_text: `${label}识别失败：${file.recognition_error || '内容无法识别'}`
      }
    }
    const activeStates = ['', 'pending', 'queued', 'processing', null, undefined]
    if (activeStates.includes(file.parse_status) || activeStates.includes(file.recognition_status)) {
      return {
        status_kind: 'neutral',
        status_text: file.document_role === 'answer' ? '正在识别答案内容' : '正在识别作业内容'
      }
    }
    if (file.document_role === 'answer') {
      if (file.match_status === 'matched') {
        return { status_kind: 'success', status_text: '已匹配作业' }
      }
      if (file.match_status === 'unmatched') {
        return { status_kind: 'error', status_text: '答案未匹配' }
      }
      return { status_kind: 'neutral', status_text: '正在匹配作业' }
    }
    return { status_kind: 'success', status_text: '作业内容已识别' }
  },

  normalizeServerFile(file) {
    return Object.assign({}, file, this.fileStatus(file))
  },

  applyFiles(files) {
    const normalized = (files || []).map((file) => this.normalizeServerFile(file))
    const answerFiles = normalized.filter((file) => file.document_role === 'answer')
    const matchedHomeworkIds = new Set(answerFiles
      .filter((file) => file.match_status === 'matched')
      .map((file) => Number(file.matched_homework_file_id)))
    const homeworkFiles = normalized
      .filter((file) => file.document_role !== 'answer')
      .map((file) => Object.assign({}, file, {
        delete_match_status: matchedHomeworkIds.has(Number(file.file_id || file.id))
          ? 'matched'
          : file.match_status
      }))
    this.setData({ homeworkFiles, answerFiles })
  },

  refreshFiles() {
    return importApi.listFiles(this.data.batchId).then((files) => {
      this.applyFiles(files)
      return files
    }).catch((err) => {
      throw err
    })
  },

  refreshBatchAndFiles() {
    return Promise.all([
      importApi.getBatch(this.data.batchId),
      importApi.listFiles(this.data.batchId)
    ]).then(([batch, files]) => {
      this.applyFiles(files)
      this.setData({ batch })
      return batch
    })
  },

  onDeleteFile(e) {
    const { fileId, documentRole, matchStatus } = e.currentTarget.dataset
    const deletingMatchedHomework = documentRole === 'homework' && matchStatus === 'matched'
    const content = deletingMatchedHomework
      ? '删除这份作业会同时删除已匹配的答案，是否继续？'
      : '删除后无法恢复，是否继续？'
    return new Promise((resolve) => {
      wx.showModal({
        title: documentRole === 'answer' ? '删除答案？' : '删除作业？',
        content,
        confirmText: '删除',
        confirmColor: '#b94242',
        success: resolve,
        fail: () => resolve({ confirm: false })
      })
    }).then((result) => {
      if (!result.confirm) return null
      return importApi.deleteFile(fileId).then(() => this.refreshFiles()).catch((err) => {
        wx.showToast({ title: err.detail || '删除失败', icon: 'none' })
        return null
      })
    })
  },

  pollParsedBatch() {
    let attempts = 0
    return new Promise((resolve, reject) => {
      const tick = () => {
        this.refreshBatchAndFiles().then((batch) => {
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
    if (!this.data.homeworkFiles.length && !this.data.rawText.trim()) {
      wx.showToast({ title: '请先添加作业资料', icon: 'none' })
      return
    }
    this.setData({ loading: true, progressText: '正在生成作业计划' })
    return importApi.updateBatch(this.data.batchId, {
      raw_text: this.data.rawText
    }).then(() => importApi.parseBatch(this.data.batchId))
      .then(() => this.pollParsedBatch())
      .then((batch) => {
        const blockers = batch.blockers || []
        if (blockers.length) {
          return this.refreshFiles().catch(() => {}).then(() => {
            wx.showToast({ title: blockers[0].message || '资料尚未准备完成', icon: 'none' })
            return null
          })
        }
        return planApi.generate(this.data.batchId)
      })
      .then((data) => {
        if (!data) return
        wx.navigateTo({ url: `/pages/parent/plan-confirm/index?plan_id=${data.assignment_batch_id}` })
      }).catch((err) => {
        wx.showToast({ title: err.detail || '生成计划失败', icon: 'none' })
      }).finally(() => this.setData({ loading: false, progressText: '' }))
  }
})
