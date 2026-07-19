const planApi = require('../../../services/plan')
const { previewSourceFile } = require('../../../utils/file-preview')

function emptyDraft() {
  return {
    plan: {},
    existing_items: [],
    new_items: [],
    daily_preview: [],
    confirmation_blockers: [],
    can_confirm: false
  }
}

function invokeApi(invoke) {
  try {
    return Promise.resolve(invoke())
  } catch (err) {
    return Promise.reject(err)
  }
}

const API_ERROR_MESSAGES = {
  file_processing: '文件正在处理，请稍后重试',
  homework_title_unrecognized: '作业标题尚未识别，请重新上传或删除该资料',
  answer_pending: '答案正在识别或匹配，请稍后重试',
  answer_unmatched: '答案未匹配到当前作业，请重新上传或删除该答案',
  answer_match_conflict: '答案匹配存在冲突，请删除或重新上传答案'
}

function formatApiError(err, fallback) {
  if (typeof err === 'string' && err.trim()) return err
  const rawDetail = err && err.detail
  const detail = Array.isArray(rawDetail) ? rawDetail[0] : rawDetail
  if (typeof detail === 'string' && detail.trim()) return detail
  if (detail && typeof detail === 'object') {
    if (typeof detail.message === 'string' && detail.message.trim()) return detail.message
    if (typeof detail.detail === 'string' && detail.detail.trim()) return detail.detail
    if (API_ERROR_MESSAGES[detail.code]) return API_ERROR_MESSAGES[detail.code]
  }
  if (err && API_ERROR_MESSAGES[err.code]) return API_ERROR_MESSAGES[err.code]
  if (err && typeof err.message === 'string' && err.message.trim()) return err.message
  return fallback
}

function validPlanId(value) {
  const planId = Number(value)
  return Number.isInteger(planId) && planId > 0 ? planId : null
}

function canonicalPlanIdFromDraft(draft) {
  const plan = draft && draft.plan
  if (!plan) return null
  if (plan.status === 'active') return validPlanId(plan.id)
  if (plan.status === 'merged') return validPlanId(plan.target_assignment_batch_id)
  return null
}

Page({
  data: {
    planId: null,
    draft: emptyDraft(),
    pageReady: false,
    loadBusy: false,
    errorKind: '',
    loadErrorTitle: '计划草稿加载失败',
    loadError: '',
    operationBusy: '',
    loading: false
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

  onLoad(options) {
    this.pageDestroyed = false
    this.pageActive = true
    this.firstShowPending = true
    this.lifecycleToken = this.ensureLifecycleToken() + 1
    this.latestRecoveryToken = null
    this.pendingCanonicalPlanId = null
    this.operationNeedsRecovery = false
    this.lastRedirectedPlanId = null
    this.navigationPromise = null
    this.navigationInFlightPlanId = null
    this.navigationObserverPromise = null
    this.observedNavigationPromise = null
    this.latestNavigationToken = null
    this.navigationFailureMessage = ''
    this.setData({ planId: options.plan_id })
    return this.loadDraft()
  },

  onShow() {
    if (this.pageDestroyed) return null
    if (this.firstShowPending) {
      this.firstShowPending = false
      return null
    }
    this.pageActive = true
    this.lifecycleToken = this.ensureLifecycleToken() + 1
    if (!this.data.planId) return null
    const token = this.ensureLifecycleToken()
    if (this.navigationPromise) return this.observeNavigation(token)
    if (this.navigationFailureMessage) {
      this.presentNavigationFailure(this.navigationFailureMessage, token)
      return Promise.resolve(false)
    }
    if (
      this.activeOperationPromise
      || this.loadPromise
      || this.recoveryPromise
      || this.pendingCanonicalPlanId
      || this.operationNeedsRecovery
    ) {
      return this.requestRecovery(token)
    }
    return this.loadDraft({ preserveReady: true })
  },

  onHide() {
    this.pageActive = false
    this.lifecycleToken = this.ensureLifecycleToken() + 1
    this.recoveryGeneration = (this.recoveryGeneration || 0) + 1
    this.latestRecoveryToken = null
    this.latestNavigationToken = null
  },

  onUnload() {
    this.pageActive = false
    this.pageDestroyed = true
    this.lifecycleToken = this.ensureLifecycleToken() + 1
    this.recoveryGeneration = (this.recoveryGeneration || 0) + 1
    this.latestRecoveryToken = null
    this.loadRequestId = (this.loadRequestId || 0) + 1
    this.loadPromise = null
    this.recoveryPromise = null
    this.pendingCanonicalPlanId = null
    this.lastRedirectedPlanId = null
    this.navigationPromise = null
    this.navigationInFlightPlanId = null
    this.navigationObserverPromise = null
    this.observedNavigationPromise = null
    this.latestNavigationToken = null
    this.navigationFailureMessage = ''
    this.clearActiveOperation()
  },

  clearActiveOperation(operationPromise) {
    if (operationPromise && this.activeOperationPromise !== operationPromise) return
    this.activeOperationPromise = null
    this.activeOperationKind = ''
    this.operationNeedsRecovery = false
  },

  releaseOperationUi(kind, token) {
    if (!this.isPageActive(token)) return
    if (kind && this.data.operationBusy && this.data.operationBusy !== kind) return
    this.safeSetData({ operationBusy: '', loading: false }, token)
  },

  retainRecoveryIntent(kind, token) {
    this.operationNeedsRecovery = true
    if (kind && !this.activeOperationKind) this.activeOperationKind = kind
    this.releaseOperationUi(kind, token)
  },

  presentNavigationFailure(message, token) {
    const alreadyPresented = this.data.errorKind === 'redirect'
      && this.data.loadError === message
    this.retainRecoveryIntent('confirming', token)
    const presented = this.safeSetData({
      errorKind: 'redirect',
      loadErrorTitle: '打开计划失败',
      loadError: message
    }, token)
    if (presented && !alreadyPresented) this.safeToast(message, token)
  },

  observeNavigation(token) {
    const navigationPromise = this.navigationPromise
    if (!navigationPromise) return Promise.resolve(null)
    this.latestNavigationToken = token
    if (
      this.navigationObserverPromise
      && this.observedNavigationPromise === navigationPromise
    ) {
      return this.navigationObserverPromise
    }
    let observerPromise
    observerPromise = navigationPromise.then((success) => {
      const latestToken = this.latestNavigationToken
      if (
        this.pageDestroyed
        || latestToken === null
        || !this.isPageActive(latestToken)
      ) {
        return success
      }
      if (!success) {
        this.presentNavigationFailure(
          this.navigationFailureMessage || '打开计划失败，请重试',
          latestToken
        )
        return false
      }
      this.releaseOperationUi('confirming', latestToken)
      return true
    }).finally(() => {
      if (this.navigationObserverPromise === observerPromise) {
        this.navigationObserverPromise = null
        this.observedNavigationPromise = null
        this.latestNavigationToken = null
      }
    })
    this.navigationObserverPromise = observerPromise
    this.observedNavigationPromise = navigationPromise
    return observerPromise
  },

  redirectCanonicalPlan(planId, token) {
    const canonicalPlanId = validPlanId(planId)
    if (!canonicalPlanId || !this.isPageActive(token)) return Promise.resolve(false)
    if (this.lastRedirectedPlanId === canonicalPlanId) {
      this.pendingCanonicalPlanId = null
      return Promise.resolve(true)
    }
    if (this.navigationPromise) {
      if (this.navigationInFlightPlanId === canonicalPlanId) return this.navigationPromise
      return this.navigationPromise.then((success) => {
        if (success && this.lastRedirectedPlanId === canonicalPlanId) return true
        return this.redirectCanonicalPlan(canonicalPlanId, token)
      })
    }

    let navigationPromise
    const requestPromise = new Promise((resolve, reject) => {
      try {
        const app = getApp()
        app.globalData.currentPlanId = canonicalPlanId
        wx.setStorageSync('currentPlanId', canonicalPlanId)
        wx.redirectTo({
          url: `/pages/parent/plan-calendar/index?plan_id=${canonicalPlanId}`,
          success: resolve,
          fail: reject
        })
      } catch (err) {
        reject(err)
      }
    })
    navigationPromise = requestPromise.then(() => {
      if (this.navigationPromise === navigationPromise) {
        this.navigationPromise = null
        this.navigationInFlightPlanId = null
      }
      if (this.pageDestroyed) return true
      this.navigationFailureMessage = ''
      this.lastRedirectedPlanId = canonicalPlanId
      if (validPlanId(this.pendingCanonicalPlanId) === canonicalPlanId) {
        this.pendingCanonicalPlanId = null
      }
      this.clearActiveOperation()
      this.releaseOperationUi('confirming', token)
      return true
    }, (err) => {
      if (this.navigationPromise === navigationPromise) {
        this.navigationPromise = null
        this.navigationInFlightPlanId = null
      }
      if (!this.pageDestroyed) {
        const message = formatApiError(err, '打开计划失败，请重试')
        this.pendingCanonicalPlanId = canonicalPlanId
        this.navigationFailureMessage = message
        this.presentNavigationFailure(message, token)
      }
      return false
    })
    this.navigationPromise = navigationPromise
    this.navigationInFlightPlanId = canonicalPlanId
    return navigationPromise
  },

  redirectRecoveredCanonicalPlan(operationKind, draft, token) {
    if (operationKind !== 'confirming' && !this.pendingCanonicalPlanId) {
      return Promise.resolve(false)
    }
    const canonicalPlanId = validPlanId(this.pendingCanonicalPlanId)
      || canonicalPlanIdFromDraft(draft)
    if (!canonicalPlanId) return Promise.resolve(false)
    return this.redirectCanonicalPlan(canonicalPlanId, token)
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
    return Promise.all(prerequisites).then(() => {
      if (this.pageDestroyed) return null
      if (generation !== this.recoveryGeneration) {
        if (this.latestRecoveryToken !== null && this.isPageActive(this.latestRecoveryToken)) {
          return this.runRecoveryAttempt()
        }
        return null
      }
      if (token === null || !this.isPageActive(token)) return null
      return this.loadDraft({ allowOperationBusy: true, preserveReady: true }).then((draft) => {
        if (this.pageDestroyed) return draft
        if (generation !== this.recoveryGeneration || !this.isPageActive(token)) {
          if (this.latestRecoveryToken !== null && this.isPageActive(this.latestRecoveryToken)) {
            return this.runRecoveryAttempt()
          }
          return draft
        }
        if (!draft) {
          if (operationKind || this.pendingCanonicalPlanId || this.operationNeedsRecovery) {
            this.retainRecoveryIntent(operationKind, token)
          } else {
            this.clearActiveOperation(operationPromise)
            this.releaseOperationUi(operationKind, token)
          }
          return draft
        }
        return this.redirectRecoveredCanonicalPlan(operationKind, draft, token).then((redirected) => {
          if (redirected) return draft
          if (operationKind === 'confirming' && this.pendingCanonicalPlanId) {
            this.retainRecoveryIntent(operationKind, token)
            return draft
          }
          this.clearActiveOperation(operationPromise)
          this.releaseOperationUi(operationKind, token)
          return draft
        })
      })
    })
  },

  retryRecovery() {
    if (this.pageDestroyed || !this.isPageActive()) return Promise.resolve(null)
    if (this.navigationPromise) return this.navigationPromise
    if (this.recoveryPromise) return this.recoveryPromise
    const hasRecoveryIntent = Boolean(
      this.operationNeedsRecovery
      || this.activeOperationKind
      || this.pendingCanonicalPlanId
    )
    if (!hasRecoveryIntent) return this.loadDraft()
    const token = this.ensureLifecycleToken()
    const kind = this.activeOperationKind || (this.pendingCanonicalPlanId ? 'confirming' : '')
    this.navigationFailureMessage = ''
    this.safeSetData({
      operationBusy: kind,
      loading: kind === 'confirming',
      errorKind: '',
      loadErrorTitle: '计划草稿加载失败',
      loadError: ''
    }, token)
    return this.requestRecovery(token)
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

  loadDraft(options = {}) {
    if (this.loadPromise) return this.loadPromise
    if (!this.isPageActive()) return Promise.resolve(null)
    if (this.data.operationBusy && !options.allowOperationBusy) return Promise.resolve(null)
    const token = this.ensureLifecycleToken()
    const requestId = (this.loadRequestId || 0) + 1
    this.loadRequestId = requestId
    const loadingState = {
      loadBusy: true,
      errorKind: '',
      loadErrorTitle: '计划草稿加载失败',
      loadError: ''
    }
    if (!options.preserveReady) loadingState.pageReady = false
    this.safeSetData(loadingState, token)

    const requestPromise = invokeApi(() => planApi.draft(this.data.planId)).then((draft) => {
      if (!this.isPageActive(token)) return null
      this.safeSetData({
        draft,
        pageReady: true,
        errorKind: '',
        loadErrorTitle: '计划草稿加载失败',
        loadError: ''
      }, token)
      return draft
    }).catch((err) => {
      if (!this.isPageActive(token)) return null
      this.safeSetData({
        pageReady: false,
        errorKind: 'load',
        loadErrorTitle: '计划草稿加载失败',
        loadError: formatApiError(err, '计划草稿加载失败，请重试')
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

  canStartOperation() {
    return this.isPageActive() && this.data.pageReady && !this.data.loadBusy && !this.data.operationBusy
  },

  beginOperation(kind) {
    if (!this.canStartOperation()) return null
    const token = this.ensureLifecycleToken()
    this.safeSetData({
      operationBusy: kind,
      loading: kind === 'confirming'
    }, token)
    return token
  },

  endOperation(kind, token) {
    if (!this.isPageActive(token) || this.data.operationBusy !== kind) return
    this.safeSetData({ operationBusy: '', loading: false }, token)
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
        if (this.operationNeedsRecovery) {
          this.releaseOperationUi(kind, token)
          return
        }
        this.clearActiveOperation(operationPromise)
        this.endOperation(kind, token)
        return
      }
      this.operationNeedsRecovery = true
    }
    operationPromise.then(handleSettled, handleSettled)
    return operationPromise
  },

  openSourceFile(e) {
    const section = e.currentTarget.dataset.section
    if (!['existing_items', 'new_items'].includes(section)) return
    const item = (this.data.draft[section] || [])[e.currentTarget.dataset.index]
    if (!item || !item.source_file) return
    previewSourceFile(item.source_file)
  },

  deleteNewItem(e) {
    const itemId = Number(e.currentTarget.dataset.itemId)
    const item = (this.data.draft.new_items || []).find((candidate) => Number(candidate.id) === itemId)
    if (!item || !item.can_delete) {
      this.safeToast('这项作业当前不能删除，请刷新后重试')
      return Promise.resolve(null)
    }
    const token = this.beginOperation('deleting')
    if (token === null) return Promise.resolve(null)
    const operationPromise = new Promise((resolve) => {
      wx.showModal({
        title: '删除本次新增作业？',
        content: '删除后，这项作业及已匹配的标准答案将一并移除。',
        confirmText: '删除',
        confirmColor: '#b94242',
        success: resolve,
        fail: () => resolve({ confirm: false })
      })
    }).then((result) => {
      if (!result.confirm || !this.isPageActive(token)) return null
      return invokeApi(() => planApi.deleteDraftItem(this.data.planId, itemId)).then(() => {
        if (!this.isPageActive(token)) return null
        return this.loadDraft({ allowOperationBusy: true, preserveReady: true }).then((draft) => {
          if (!draft) this.safeToast('作业已删除，但列表刷新失败，请重试', token)
          return draft
        })
      })
    }).catch((err) => {
      this.safeToast(formatApiError(err, '删除作业失败，请重试'), token)
      return null
    })
    return this.trackActiveOperation('deleting', operationPromise, token)
  },

  confirm() {
    if (!this.data.pageReady) {
      this.safeToast('计划草稿尚未加载完成，请稍后重试')
      return Promise.resolve(null)
    }
    if (!this.data.draft.can_confirm) {
      const blockers = this.data.draft.confirmation_blockers || []
      const message = formatApiError({ detail: blockers }, '计划资料尚未准备完成')
      this.safeToast(`${message}，请返回上一步处理后再确认`)
      return Promise.resolve(null)
    }
    const token = this.beginOperation('confirming')
    if (token === null) return Promise.resolve(null)
    const operationPromise = invokeApi(() => planApi.confirm(this.data.planId, {})).then((data) => {
      const canonicalPlanId = validPlanId(data && data.plan_id)
      if (!canonicalPlanId) {
        throw { detail: '确认结果缺少有效计划编号，请刷新后重试' }
      }
      if (this.pageDestroyed) return data
      this.pendingCanonicalPlanId = canonicalPlanId
      if (!this.isPageActive(token)) return data
      return this.redirectCanonicalPlan(canonicalPlanId, token).then(() => data)
    }).catch((err) => {
      this.safeToast(formatApiError(err, '确认计划失败，请重试'), token)
      return null
    })
    return this.trackActiveOperation('confirming', operationPromise, token)
  }
})
