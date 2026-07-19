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

Page({
  data: {
    planId: null,
    draft: emptyDraft(),
    pageReady: false,
    loadBusy: false,
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
    const activeOperation = this.activeOperationPromise
    const activeKind = this.activeOperationKind
    if (activeOperation) {
      if (this.recoveryPromise) return this.recoveryPromise
      let recoveryPromise
      recoveryPromise = activeOperation.catch(() => null).then(() => {
        if (!this.isPageActive(token)) return null
        return this.loadDraft({ allowOperationBusy: true, preserveReady: true })
      }).then((result) => {
        if (!this.isPageActive(token)) return result
        if (this.activeOperationPromise === activeOperation) {
          this.activeOperationPromise = null
          this.activeOperationKind = ''
        }
        if (this.data.operationBusy === activeKind) {
          this.safeSetData({ operationBusy: '', loading: false }, token)
        }
        return result
      }).finally(() => {
        if (this.recoveryPromise === recoveryPromise) this.recoveryPromise = null
      })
      this.recoveryPromise = recoveryPromise
      return recoveryPromise
    }
    if (this.loadPromise) {
      const previousLoad = this.loadPromise
      return previousLoad.catch(() => null).then(() => {
        if (!this.isPageActive(token)) return null
        return this.loadDraft({ preserveReady: true })
      })
    }
    return this.loadDraft({ preserveReady: true })
  },

  onHide() {
    this.pageActive = false
    this.lifecycleToken = this.ensureLifecycleToken() + 1
  },

  onUnload() {
    this.pageActive = false
    this.pageDestroyed = true
    this.lifecycleToken = this.ensureLifecycleToken() + 1
    this.loadRequestId = (this.loadRequestId || 0) + 1
    this.loadPromise = null
    this.recoveryPromise = null
    this.activeOperationPromise = null
    this.activeOperationKind = ''
  },

  loadDraft(options = {}) {
    if (this.loadPromise) return this.loadPromise
    if (!this.isPageActive()) return Promise.resolve(null)
    if (this.data.operationBusy && !options.allowOperationBusy) return Promise.resolve(null)
    const token = this.ensureLifecycleToken()
    const requestId = (this.loadRequestId || 0) + 1
    this.loadRequestId = requestId
    const loadingState = { loadBusy: true, loadError: '' }
    if (!options.preserveReady) loadingState.pageReady = false
    this.safeSetData(loadingState, token)

    const requestPromise = invokeApi(() => planApi.draft(this.data.planId)).then((draft) => {
      if (!this.isPageActive(token)) return null
      this.safeSetData({ draft, pageReady: true, loadError: '' }, token)
      return draft
    }).catch((err) => {
      if (!this.isPageActive(token)) return null
      this.safeSetData({
        pageReady: false,
        loadError: err.detail || '计划草稿加载失败，请重试'
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
    const handleSettled = () => {
      if (this.activeOperationPromise !== operationPromise) return
      if (this.pageDestroyed) {
        this.activeOperationPromise = null
        this.activeOperationKind = ''
        return
      }
      if (!this.isPageActive(token)) return
      this.activeOperationPromise = null
      this.activeOperationKind = ''
      this.endOperation(kind, token)
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
        content: '删除后，这项作业及已匹配的标准答案和安排预览将一并移除。',
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
      this.safeToast(err.detail || '删除作业失败，请重试', token)
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
      const message = (blockers[0] && blockers[0].message) || '计划资料尚未准备完成'
      this.safeToast(`${message}，请返回上一步处理后再确认`)
      return Promise.resolve(null)
    }
    const token = this.beginOperation('confirming')
    if (token === null) return Promise.resolve(null)
    const operationPromise = invokeApi(() => planApi.confirm(this.data.planId, {})).then((data) => {
      if (!this.isPageActive(token)) return data
      const canonicalPlanId = Number(data && data.plan_id)
      if (!Number.isFinite(canonicalPlanId) || canonicalPlanId <= 0) {
        throw { detail: '确认结果缺少有效计划编号，请刷新后重试' }
      }
      const app = getApp()
      app.globalData.currentPlanId = canonicalPlanId
      wx.setStorageSync('currentPlanId', canonicalPlanId)
      wx.redirectTo({ url: `/pages/parent/plan-calendar/index?plan_id=${canonicalPlanId}` })
      return data
    }).catch((err) => {
      this.safeToast(err.detail || '确认计划失败，请重试', token)
      return null
    })
    return this.trackActiveOperation('confirming', operationPromise, token)
  }
})
