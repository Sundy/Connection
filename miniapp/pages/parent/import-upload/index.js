const importApi = require('../../../services/import')
const planApi = require('../../../services/plan')

function fileTypeFromPath(path) {
  const lower = (path || '').toLowerCase()
  if (lower.endsWith('.pdf')) return 'pdf'
  if (lower.endsWith('.doc') || lower.endsWith('.docx')) return 'docx'
  if (lower.endsWith('.xls') || lower.endsWith('.xlsx')) return 'xlsx'
  return 'file'
}

function settleAll(promises) {
  if (typeof Promise.allSettled === 'function') return Promise.allSettled(promises)
  return Promise.all(promises.map((promise) => Promise.resolve(promise).then(
    (value) => ({ status: 'fulfilled', value }),
    (reason) => ({ status: 'rejected', reason })
  )))
}

function cancelledError() {
  return { operationCancelled: true }
}

function formatApiError(err, fallback) {
  if (typeof err === 'string' && err) return err
  const detail = err && err.detail
  if (typeof detail === 'string' && detail) return detail
  if (Array.isArray(detail) && detail.length) {
    return formatApiError(detail[0], fallback)
  }
  if (detail && typeof detail === 'object') {
    if (typeof detail.message === 'string' && detail.message) return detail.message
    if (detail.code === 'import_batch_immutable') return '该批作业已确认，不能再修改'
  }
  if (err && typeof err.message === 'string' && err.message) return err.message
  return fallback
}

Page({
  data: {
    batchId: null,
    homeworkFiles: [],
    answerFiles: [],
    batch: null,
    rawText: '',
    pageReady: false,
    loadError: '',
    loadBusy: false,
    operationBusy: '',
    loading: false,
    progressText: '',
    readOnly: false,
    readOnlyNotice: ''
  },

  ensureLifecycleToken() {
    if (typeof this.lifecycleToken !== 'number') this.lifecycleToken = 0
    return this.lifecycleToken
  },

  isPageActive(token) {
    if (this.pageDestroyed || this.pageActive === false) return false
    return token === undefined || token === this.ensureLifecycleToken()
  },

  safeSetData(update, token) {
    if (!this.isPageActive(token)) return false
    this.setData(update)
    return true
  },

  safeToast(title, token) {
    if (!this.isPageActive(token)) return false
    wx.showToast({ title, icon: 'none' })
    return true
  },

  ensurePendingTransports() {
    if (!this.pendingTransports) this.pendingTransports = new Set()
    return this.pendingTransports
  },

  trackTransport(promise) {
    const tracked = Promise.resolve(promise)
    const pendingTransports = this.ensurePendingTransports()
    pendingTransports.add(tracked)
    const remove = () => pendingTransports.delete(tracked)
    tracked.then(remove, remove)
    return tracked
  },

  invokeTracked(invoke) {
    return this.trackTransport(Promise.resolve().then(invoke))
  },

  waitForPendingTransports() {
    const pending = Array.from(this.ensurePendingTransports())
    if (!pending.length) return Promise.resolve()
    return settleAll(pending).then(() => this.waitForPendingTransports())
  },

  canStartOperation() {
    return this.isPageActive() && this.data.pageReady && !this.data.operationBusy && !this.data.readOnly
  },

  applyBatchState(batch, token) {
    const readOnly = !!(batch && (batch.read_only || batch.can_edit === false))
    this.safeSetData({
      readOnly,
      readOnlyNotice: readOnly ? '该批作业已确认，不可修改' : ''
    }, token)
    const planId = batch && batch.canonical_plan_id
    if (readOnly && planId && this.lastRedirectedPlanId !== planId &&
      this.redirectInFlightPlanId !== planId && this.isPageActive(token)) {
      this.redirectInFlightPlanId = planId
      try {
        wx.redirectTo({
          url: `/pages/parent/plan-calendar/index?plan_id=${planId}`,
          success: () => {
            this.lastRedirectedPlanId = planId
            this.redirectInFlightPlanId = null
          },
          fail: () => {
            this.redirectInFlightPlanId = null
            this.safeToast('打开已确认计划失败，请重试', token)
          }
        })
      } catch (_) {
        this.redirectInFlightPlanId = null
        this.safeToast('打开已确认计划失败，请重试', token)
      }
    }
  },

  beginOperation(kind) {
    if (!this.canStartOperation()) return null
    const token = this.ensureLifecycleToken()
    const progressText = {
      uploading: '正在上传文件',
      deleting: '正在删除文件',
      generating: '正在生成作业计划'
    }[kind] || ''
    this.safeSetData({
      operationBusy: kind,
      loading: kind === 'generating',
      progressText
    }, token)
    return token
  },

  endOperation(kind, token) {
    if (!this.isPageActive(token) || this.data.operationBusy !== kind) return
    this.safeSetData({ operationBusy: '', loading: false, progressText: '' }, token)
  },

  clearActiveOperation(operationPromise) {
    if (operationPromise && this.activeOperationPromise !== operationPromise) return
    this.activeOperationPromise = null
    this.activeOperationKind = ''
    this.operationNeedsRecovery = false
  },

  trackActiveOperation(kind, promise, token) {
    const operationPromise = Promise.resolve(promise)
    this.activeOperationPromise = operationPromise
    this.activeOperationKind = kind
    this.operationNeedsRecovery = false
    const handleSettled = () => {
      if (this.activeOperationPromise !== operationPromise) return
      if (this.pageDestroyed) {
        this.clearActiveOperation(operationPromise)
        return
      }
      if (this.isPageActive(token)) {
        this.clearActiveOperation(operationPromise)
        this.endOperation(kind, token)
        return
      }
      this.operationNeedsRecovery = true
    }
    operationPromise.then(handleSettled, handleSettled)
    return operationPromise
  },

  runRecoveryAttempt() {
    const generation = this.recoveryGeneration
    const token = this.latestRecoveryToken
    const operationPromise = this.activeOperationPromise
    const operationKind = this.activeOperationKind
    const currentLoad = this.loadPromise
    const prerequisites = []
    if (operationPromise) prerequisites.push(operationPromise.catch(() => null))
    if (currentLoad) prerequisites.push(currentLoad.catch(() => null))
    return settleAll(prerequisites)
      .then(() => this.waitForPendingTransports())
      .then(() => {
        if (this.pageDestroyed) return null
        if (generation !== this.recoveryGeneration) {
          if (this.latestRecoveryToken !== null && this.isPageActive(this.latestRecoveryToken)) {
            return this.runRecoveryAttempt()
          }
          return null
        }
        if (token === null || !this.isPageActive(token)) return null
        return this.retryLoad({ allowOperationBusy: true, preserveReady: true }).then((result) => {
          if (this.pageDestroyed) return result
          if (generation !== this.recoveryGeneration || !this.isPageActive(token)) {
            if (this.latestRecoveryToken !== null && this.isPageActive(this.latestRecoveryToken)) {
              return this.runRecoveryAttempt()
            }
            return result
          }
          this.clearActiveOperation(operationPromise)
          if (this.data.operationBusy === operationKind) {
            this.safeSetData({ operationBusy: '', loading: false, progressText: '' }, token)
          }
          return result
        })
      })
  },

  requestRecovery(token) {
    this.recoveryGeneration = (this.recoveryGeneration || 0) + 1
    this.latestRecoveryToken = token
    if (this.recoveryPromise) return this.recoveryPromise
    let recoveryPromise
    recoveryPromise = this.runRecoveryAttempt().finally(() => {
      if (this.recoveryPromise === recoveryPromise) this.recoveryPromise = null
    })
    this.recoveryPromise = recoveryPromise
    return recoveryPromise
  },

  onLoad(options) {
    this.pageDestroyed = false
    this.pageActive = true
    this.firstShowPending = true
    this.lifecycleToken = this.ensureLifecycleToken() + 1
    this.setData({ batchId: options.batch_id })
    return this.retryLoad()
  },

  onShow() {
    if (this.pageDestroyed) return null
    if (this.firstShowPending) {
      this.firstShowPending = false
      return null
    }
    this.pageActive = true
    this.lifecycleToken = this.ensureLifecycleToken() + 1
    if (!this.data.batchId) return null
    const token = this.ensureLifecycleToken()
    if (this.activeOperationPromise || this.loadPromise || this.recoveryPromise) {
      return this.requestRecovery(token)
    }
    return this.retryLoad()
  },

  onHide() {
    if (this.nativePickerOpen) {
      this.stopPolling()
      return
    }
    this.pageActive = false
    this.lifecycleToken = this.ensureLifecycleToken() + 1
    this.recoveryGeneration = (this.recoveryGeneration || 0) + 1
    this.latestRecoveryToken = null
    this.stopPolling()
  },

  onUnload() {
    this.nativePickerOpen = false
    this.pageActive = false
    this.pageDestroyed = true
    this.lifecycleToken = this.ensureLifecycleToken() + 1
    this.recoveryGeneration = (this.recoveryGeneration || 0) + 1
    this.latestRecoveryToken = null
    this.recoveryPromise = null
    this.loadPromise = null
    this.loadRequestId = (this.loadRequestId || 0) + 1
    this.clearActiveOperation()
    this.stopPolling()
  },

  retryLoad(options = {}) {
    if (this.loadPromise) return this.loadPromise
    if (!this.isPageActive()) return Promise.resolve(null)
    if (this.data.operationBusy && !options.allowOperationBusy) return Promise.resolve(null)
    const token = this.ensureLifecycleToken()
    const requestId = (this.loadRequestId || 0) + 1
    this.loadRequestId = requestId
    const loadingState = { loadBusy: true, loadError: '' }
    if (!options.preserveReady) loadingState.pageReady = false
    this.safeSetData(loadingState, token)
    const requestPromise = Promise.all([
      this.invokeTracked(() => importApi.getBatch(this.data.batchId)),
      this.invokeTracked(() => importApi.listFiles(this.data.batchId))
    ]).then(([batch, files]) => {
      if (!this.isPageActive(token)) return null
      this.applyFiles(files, token)
      this.applyBatchState(batch, token)
      this.lastLoadError = null
      this.safeSetData({ batch, pageReady: true, loadError: '' }, token)
      return batch
    }).catch((err) => {
      if (!this.isPageActive(token)) return null
      this.lastLoadError = err
      this.safeSetData({
        pageReady: false,
        loadError: formatApiError(
          err,
          err.statusCode === 401 ? '登录状态已失效，请重新进入' : '加载上传资料失败'
        )
      }, token)
      return null
    })
    let loadPromise
    loadPromise = requestPromise.finally(() => {
      if (this.loadPromise === loadPromise) this.loadPromise = null
      if (this.isPageActive(token) && this.loadRequestId === requestId) {
        this.safeSetData({ loadBusy: false }, token)
      }
    })
    this.loadPromise = loadPromise
    return loadPromise
  },

  onRawText(e) {
    if (!this.canStartOperation()) return
    this.setData({ rawText: e.detail.value })
  },

  chooseImages(e) {
    if (!this.canStartOperation()) return
    const documentRole = (e && e.currentTarget.dataset.documentRole) || 'homework'
    this.nativePickerOpen = true
    try {
      wx.chooseMedia({
        count: 9,
        mediaType: ['image'],
        success: (res) => {
          this.nativePickerOpen = false
          return this.uploadPaths(
            res.tempFiles.map((item) => item.tempFilePath),
            'image',
            documentRole
          )
        },
        fail: () => {
          this.nativePickerOpen = false
        }
      })
    } catch (err) {
      this.nativePickerOpen = false
      throw err
    }
  },

  chooseFiles(e) {
    if (!this.canStartOperation()) return
    const documentRole = (e && e.currentTarget.dataset.documentRole) || 'homework'
    this.nativePickerOpen = true
    try {
      wx.chooseMessageFile({
        count: 9,
        type: 'file',
        success: (res) => {
          this.nativePickerOpen = false
          return this.uploadSelectedFiles(res.tempFiles, documentRole)
        },
        fail: () => {
          this.nativePickerOpen = false
        }
      })
    } catch (err) {
      this.nativePickerOpen = false
      throw err
    }
  },

  uploadSelectedFiles(files, documentRole = 'homework') {
    if (!files.length) return Promise.resolve(null)
    const token = this.beginOperation('uploading')
    if (token === null) return Promise.resolve(null)
    const sortOrder = this.data.homeworkFiles.length + this.data.answerFiles.length
    const tasks = files.map((file, index) => this.invokeTracked(() => importApi.uploadFile(
      this.data.batchId,
      file.path,
      fileTypeFromPath(file.name || file.path),
      sortOrder + index,
      file.name || '',
      documentRole
    )))
    return this.trackActiveOperation('uploading', this.finishUploads(tasks, token), token)
  },

  uploadPaths(paths, fileType, documentRole = 'homework') {
    if (!paths.length) return Promise.resolve(null)
    const token = this.beginOperation('uploading')
    if (token === null) return Promise.resolve(null)
    const sortOrder = this.data.homeworkFiles.length + this.data.answerFiles.length
    const tasks = paths.map((path, index) => this.invokeTracked(() => importApi.uploadFile(
      this.data.batchId,
      path,
      fileType,
      sortOrder + index,
      '',
      documentRole
    )))
    return this.trackActiveOperation('uploading', this.finishUploads(tasks, token), token)
  },

  finishUploads(tasks, token) {
    return settleAll(tasks).then((results) => {
      if (!this.isPageActive(token)) return null
      const failureCount = results.filter((result) => result.status === 'rejected').length
      const successCount = results.length - failureCount
      return this.refreshFiles(token).then(() => {
        if (failureCount) {
          this.safeToast(`上传完成：成功 ${successCount} 份，失败 ${failureCount} 份`, token)
        }
        return results
      }).catch(() => {
        const message = failureCount
          ? `上传完成：成功 ${successCount} 份，失败 ${failureCount} 份；列表刷新失败，请重试`
          : '上传已完成，但列表刷新失败，请重试'
        this.safeToast(message, token)
        return results
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
    const states = [file.parse_status, file.recognition_status]
    if (states.some((status) => status === 'queued' || status === 'processing')) {
      return { status_kind: 'neutral', status_text: '正在识别' }
    }
    if (states.some((status) => ['', 'pending', null, undefined].includes(status))) {
      return { status_kind: 'neutral', status_text: '待识别' }
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
    const normalizedRole = file.document_role || 'homework'
    const normalized = Object.assign({}, file, { document_role: normalizedRole })
    return Object.assign(normalized, this.fileStatus(normalized))
  },

  applyFiles(files, token) {
    if (!this.isPageActive(token)) return false
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
    return this.safeSetData({ homeworkFiles, answerFiles }, token)
  },

  refreshFiles(token = this.ensureLifecycleToken()) {
    if (!this.isPageActive(token)) return Promise.reject(cancelledError())
    return this.invokeTracked(() => importApi.listFiles(this.data.batchId)).then((files) => {
      if (!this.isPageActive(token)) throw cancelledError()
      this.applyFiles(files, token)
      return files
    })
  },

  refreshBatchAndFiles(token = this.ensureLifecycleToken()) {
    if (!this.isPageActive(token)) return Promise.reject(cancelledError())
    return Promise.all([
      this.invokeTracked(() => importApi.getBatch(this.data.batchId)),
      this.invokeTracked(() => importApi.listFiles(this.data.batchId))
    ]).then(([batch, files]) => {
      if (!this.isPageActive(token)) throw cancelledError()
      this.applyFiles(files, token)
      this.applyBatchState(batch, token)
      this.safeSetData({ batch }, token)
      return batch
    })
  },

  onDeleteFile(e) {
    const token = this.beginOperation('deleting')
    if (token === null) return Promise.resolve(null)
    const { fileId, documentRole, matchStatus } = e.currentTarget.dataset
    const deletingMatchedHomework = documentRole === 'homework' && matchStatus === 'matched'
    const content = deletingMatchedHomework
      ? '删除这份作业会同时删除已匹配的答案，是否继续？'
      : '删除后无法恢复，是否继续？'
    const operationPromise = new Promise((resolve) => {
      wx.showModal({
        title: documentRole === 'answer' ? '删除答案？' : '删除作业？',
        content,
        confirmText: '删除',
        confirmColor: '#b94242',
        success: resolve,
        fail: () => resolve({ confirm: false })
      })
    }).then((result) => {
      if (!result.confirm || !this.isPageActive(token)) return null
      return this.invokeTracked(() => importApi.deleteFile(fileId)).then(() => {
        if (!this.isPageActive(token)) return null
        return this.refreshFiles(token).catch(() => {
          this.safeToast('文件已删除，但列表刷新失败，请重试', token)
          return null
        })
      }, (err) => {
        this.safeToast(formatApiError(err, '删除失败'), token)
        return null
      })
    })
    return this.trackActiveOperation('deleting', operationPromise, token)
  },

  stopPolling() {
    if (this.pollTimer) clearTimeout(this.pollTimer)
    this.pollTimer = null
    const rejectPolling = this.pollReject
    this.pollReject = null
    if (rejectPolling) rejectPolling()
  },

  pollParsedBatch(operationToken = this.ensureLifecycleToken()) {
    this.stopPolling()
    return new Promise((resolve, reject) => {
      let attempts = 0
      let settled = false
      const cleanup = () => {
        if (this.pollTimer) clearTimeout(this.pollTimer)
        this.pollTimer = null
        this.pollReject = null
      }
      const cancel = () => {
        if (settled) return
        settled = true
        cleanup()
        reject({ pollingCancelled: true })
      }
      const finish = (callback, value) => {
        if (settled) return
        settled = true
        cleanup()
        callback(value)
      }
      const tick = () => {
        if (!this.isPageActive(operationToken)) {
          cancel()
          return
        }
        this.refreshBatchAndFiles(operationToken).then((batch) => {
          if (!this.isPageActive(operationToken)) {
            cancel()
            return
          }
          const parsed = batch.parsed_file_count || 0
          const total = batch.file_count || 0
          if (total) {
            this.safeSetData({ progressText: `正在识别文件 ${parsed}/${total}` }, operationToken)
          }
          if (batch.status === 'parsed') {
            finish(resolve, batch)
            return
          }
          if (batch.status === 'failed') {
            finish(reject, { detail: '资料解析失败，请重新上传' })
            return
          }
          attempts += 1
          if (attempts >= 80) {
            finish(reject, { detail: '生成计划超时，请稍后重试' })
            return
          }
          this.pollTimer = setTimeout(tick, 1500)
        }).catch((err) => {
          if (!this.isPageActive(operationToken)) cancel()
          else finish(reject, err)
        })
      }
      this.pollReject = cancel
      tick()
    })
  },

  generatePlan() {
    if (!this.canStartOperation()) return Promise.resolve(null)
    if (!this.data.homeworkFiles.length && !this.data.rawText.trim()) {
      this.safeToast('请先添加作业资料')
      return Promise.resolve(null)
    }
    const token = this.beginOperation('generating')
    if (token === null) return Promise.resolve(null)
    const operationPromise = this.invokeTracked(() => importApi.updateBatch(this.data.batchId, {
      raw_text: this.data.rawText
    })).then(() => {
      if (!this.isPageActive(token)) throw cancelledError()
      return this.invokeTracked(() => importApi.parseBatch(this.data.batchId))
    }).then(() => {
      if (!this.isPageActive(token)) throw cancelledError()
      return this.pollParsedBatch(token)
    }).then((batch) => {
      if (!this.isPageActive(token)) throw cancelledError()
      const blockers = batch.blockers || []
      if (blockers.length) {
        return this.refreshFiles(token).catch(() => {}).then(() => {
          this.safeToast(blockers[0].message || '资料尚未准备完成', token)
          return null
        })
      }
      return this.invokeTracked(() => planApi.generate(this.data.batchId))
    }).then((data) => {
      if (!data || !this.isPageActive(token)) return null
      wx.navigateTo({ url: `/pages/parent/plan-confirm/index?plan_id=${data.assignment_batch_id}` })
      return data
    }).catch((err) => {
      if (!err.operationCancelled && !err.pollingCancelled) {
        this.safeToast(formatApiError(err, '生成计划失败'), token)
      }
      return null
    })
    return this.trackActiveOperation('generating', operationPromise, token)
  }
})
