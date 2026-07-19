const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

const planConfirmPagePath = path.resolve(__dirname, '../pages/parent/plan-confirm/index.js')
const planPath = require.resolve('../services/plan')
const requestPath = require.resolve('../services/request')

function cacheEntry(modulePath) {
  return require.cache[modulePath]
}

function restoreCache(modulePath, entry) {
  delete require.cache[modulePath]
  if (entry) require.cache[modulePath] = entry
}

function loadPlanConfirmPage(planApi) {
  const previousPage = global.Page
  const previousPlan = cacheEntry(planPath)
  let definition = null

  require.cache[planPath] = { exports: planApi }
  global.Page = (config) => {
    definition = config
  }
  delete require.cache[planConfirmPagePath]
  require(planConfirmPagePath)

  const page = {
    ...definition,
    data: JSON.parse(JSON.stringify(definition.data)),
    setData(update) {
      Object.assign(this.data, update)
    }
  }

  return {
    page,
    restore() {
      delete require.cache[planConfirmPagePath]
      restoreCache(planPath, previousPlan)
      global.Page = previousPage
    }
  }
}

function loadPlanService(requestApi) {
  const previousRequest = cacheEntry(requestPath)
  const previousPlan = cacheEntry(planPath)
  require.cache[requestPath] = { exports: requestApi }
  delete require.cache[planPath]
  const service = require(planPath)
  return {
    service,
    restore() {
      restoreCache(planPath, previousPlan)
      restoreCache(requestPath, previousRequest)
    }
  }
}

function draftPayload(overrides = {}) {
  return {
    plan: {
      id: 12,
      title: '本周作业计划',
      status: 'pending_confirm',
      start_date: '2026-07-20',
      end_date: '2026-07-26',
      target_assignment_batch_id: 8
    },
    existing_items: [{
      id: 3,
      subject: '语文',
      title: '已有阅读练习',
      source_text: '',
      total_quantity: 1,
      unit: '份',
      need_confirmation: false,
      answer_status: 'not_uploaded',
      can_delete: false,
      source_file: { display_name: '语文阅读练习', file_name: 'legacy.pdf' }
    }],
    new_items: [{
      id: 9,
      subject: '数学',
      title: '数学四年级下册第3单元练习',
      source_text: '',
      total_quantity: 20,
      unit: '题',
      need_confirmation: false,
      answer_status: 'matched',
      can_delete: true,
      source_file: { display_name: '数学四年级下册第3单元练习', file_name: 'tmp.pdf' }
    }],
    assignment_items: [],
    daily_preview: [{
      id: 31,
      task_date: '2026-07-20',
      subject: '数学',
      title: '数学四年级下册第3单元练习',
      estimated_minutes: 25
    }],
    uncertain_items: [],
    confirmation_blockers: [],
    can_confirm: true,
    ...overrides
  }
}

function deferred() {
  let resolve
  let reject
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

test('plan creation uses a child picker and automatic title', () => {
  const root = path.resolve(__dirname, '..')
  const markup = fs.readFileSync(path.join(root, 'pages/parent/import-home/index.wxml'), 'utf8')
  assert.match(markup, /为谁安排/)
  assert.match(markup, /mode="selector"/)
  assert.match(markup, /请让学生先在“我的”页输入家庭码加入家庭/)
  assert.match(markup, /查看家庭码/)
  assert.doesNotMatch(markup, /去添加孩子/)
  assert.doesNotMatch(markup, /计划名称|onTitle/)
  assert.match(markup, /添加作业资料/)
})

test('upload page separates homework and optional answers without file-name headings', () => {
  const root = path.resolve(__dirname, '..')
  const markup = fs.readFileSync(path.join(root, 'pages/parent/import-upload/index.wxml'), 'utf8')

  assert.match(markup, /上传作业/)
  assert.match(markup, /上传答案（可选）/)
  assert.match(markup, /homeworkFiles/)
  assert.match(markup, /answerFiles/)
  assert.match(markup, /item\.display_name/)
  assert.match(markup, /item\.match_reason/)
  assert.match(markup, /bindtap="onDeleteFile"/)
  assert.match(markup, /bindtap="retryLoad"/)
  assert.match(markup, /loadError/)
  assert.match(markup, /disabled="{{loadBusy \|\| operationBusy}}"/)
  assert.match(markup, /disabled="{{!pageReady \|\| operationBusy}}"/)
  assert.match(markup, /data-document-role="homework" bindtap="chooseImages"/)
  assert.match(markup, /data-document-role="homework" bindtap="chooseFiles"/)
  assert.match(markup, /data-document-role="answer" bindtap="chooseImages"/)
  assert.match(markup, /data-document-role="answer" bindtap="chooseFiles"/)
  assert.doesNotMatch(markup, /data-action="deleteFile"/)
  assert.doesNotMatch(markup, /item\.file_name \|\| item\.file_url/)
  assert.doesNotMatch(markup, /item\.original_file_name/)
})

test('plan service exposes staged draft item deletion', async () => {
  const calls = []
  const fixture = loadPlanService({
    request: async (options) => {
      calls.push(options)
      return { deleted_file_ids: [19, 20] }
    }
  })

  try {
    const result = await fixture.service.deleteDraftItem(12, 9)
    assert.deepEqual(calls, [{
      url: '/plans/12/draft-items/9',
      method: 'DELETE'
    }])
    assert.deepEqual(result, { deleted_file_ids: [19, 20] })
  } finally {
    fixture.restore()
  }
})

test('plan confirmation layout separates read-only existing work from deletable additions', () => {
  const root = path.resolve(__dirname, '..')
  const markup = fs.readFileSync(path.join(root, 'pages/parent/plan-confirm/index.wxml'), 'utf8')
  const existingStart = markup.indexOf('wx:for="{{draft.existing_items}}"')
  const newHeading = markup.indexOf('<view class="section-title">本次新增</view>')
  const newStart = markup.indexOf('wx:for="{{draft.new_items}}"')
  const previewHeading = markup.indexOf('安排预览')

  assert.match(markup, /已有作业/)
  assert.ok(existingStart >= 0, '应循环渲染 existing_items')
  assert.ok(newHeading > existingStart, '本次新增应排在已有作业之后')
  assert.ok(newStart > newHeading, '应循环渲染 new_items')
  assert.ok(previewHeading > newStart, '安排预览应排在新增作业之后')

  const existingSection = markup.slice(existingStart, newHeading)
  const newSection = markup.slice(newStart, previewHeading)
  assert.doesNotMatch(existingSection, /deleteNewItem/)
  assert.match(existingSection, /只读/)
  assert.match(newSection, /bindtap="deleteNewItem"/)
  assert.match(newSection, /item\.source_file\.display_name/)
  assert.match(newSection, /item\.answer_status/)
  assert.match(newSection, /已匹配标准答案/)
  assert.match(newSection, /无标准答案/)

  assert.match(markup, /draft\.daily_preview/)
  assert.match(markup, /draft\.confirmation_blockers/)
  assert.match(markup, /item\.message/)
  assert.match(markup, /请返回上一步删除或重新上传有问题的资料/)
  assert.match(markup, /disabled="{{!pageReady \|\| operationBusy \|\| !draft\.can_confirm}}"/)
})

test('loadDraft reads the complete server draft contract', async () => {
  const expected = draftPayload()
  const fixture = loadPlanConfirmPage({
    draft: async () => expected,
    confirm: async () => ({ plan_id: 12, status: 'active' }),
    deleteDraftItem: async () => ({})
  })

  try {
    await fixture.page.onLoad.call(fixture.page, { plan_id: '12' })
    assert.equal(fixture.page.data.planId, '12')
    assert.deepEqual(fixture.page.data.draft.existing_items, expected.existing_items)
    assert.deepEqual(fixture.page.data.draft.new_items, expected.new_items)
    assert.deepEqual(fixture.page.data.draft.daily_preview, expected.daily_preview)
    assert.deepEqual(fixture.page.data.draft.confirmation_blockers, expected.confirmation_blockers)
    assert.equal(fixture.page.data.draft.can_confirm, true)
    assert.equal(fixture.page.data.pageReady, true)
    assert.equal(fixture.page.data.loadError, '')
  } finally {
    fixture.restore()
  }
})

test('confirmation blocker rejects at method level with actionable Chinese guidance', async () => {
  let confirmCalls = 0
  const toasts = []
  const previousWx = global.wx
  global.wx = {
    showToast(options) {
      toasts.push(options.title)
    }
  }
  const blockedDraft = draftPayload({
    can_confirm: false,
    confirmation_blockers: [{
      code: 'answer_unmatched',
      file_id: 20,
      message: '答案未匹配到当前作业'
    }]
  })
  const fixture = loadPlanConfirmPage({
    draft: async () => blockedDraft,
    confirm: async () => {
      confirmCalls += 1
      return { plan_id: 12, status: 'active' }
    },
    deleteDraftItem: async () => ({})
  })

  try {
    await fixture.page.onLoad.call(fixture.page, { plan_id: '12' })
    const result = await fixture.page.confirm.call(fixture.page)
    assert.equal(result, null)
    assert.equal(confirmCalls, 0)
    assert.match(toasts[0], /答案未匹配到当前作业/)
    assert.match(toasts[0], /返回上一步.*处理.*再确认/)
    assert.equal(fixture.page.data.operationBusy, '')
  } finally {
    fixture.restore()
    global.wx = previousWx
  }
})

test('merged confirmation stores and redirects with the server canonical plan id', async () => {
  const storageWrites = []
  const redirects = []
  const app = { globalData: { currentPlanId: null } }
  const previousWx = global.wx
  const previousGetApp = global.getApp
  global.getApp = () => app
  global.wx = {
    showToast() {},
    setStorageSync(key, value) {
      storageWrites.push([key, value])
    },
    redirectTo(options) {
      redirects.push(options.url)
    }
  }
  const fixture = loadPlanConfirmPage({
    draft: async () => draftPayload(),
    confirm: async (planId) => {
      assert.equal(planId, '12')
      return { plan_id: 8, status: 'active' }
    },
    deleteDraftItem: async () => ({})
  })

  try {
    await fixture.page.onLoad.call(fixture.page, { plan_id: '12' })
    const result = await fixture.page.confirm.call(fixture.page)
    assert.deepEqual(result, { plan_id: 8, status: 'active' })
    assert.equal(app.globalData.currentPlanId, 8)
    assert.deepEqual(storageWrites, [['currentPlanId', 8]])
    assert.deepEqual(redirects, ['/pages/parent/plan-calendar/index?plan_id=8'])
    assert.equal(fixture.page.data.operationBusy, '')
  } finally {
    fixture.restore()
    global.wx = previousWx
    global.getApp = previousGetApp
  }
})

test('confirmed staged-item deletion calls the API once and reloads server state', async () => {
  let draftCalls = 0
  const deleted = []
  const previousWx = global.wx
  global.wx = {
    showToast() {},
    showModal(options) {
      options.success({ confirm: true })
    }
  }
  const fixture = loadPlanConfirmPage({
    draft: async () => {
      draftCalls += 1
      return draftCalls === 1 ? draftPayload() : draftPayload({ new_items: [] })
    },
    confirm: async () => ({ plan_id: 12, status: 'active' }),
    deleteDraftItem: async (planId, itemId) => {
      deleted.push([planId, itemId])
      return { deleted_file_ids: [19, 20] }
    }
  })

  try {
    await fixture.page.onLoad.call(fixture.page, { plan_id: '12' })
    await fixture.page.deleteNewItem.call(fixture.page, {
      currentTarget: { dataset: { itemId: 9 } }
    })
    assert.deepEqual(deleted, [['12', 9]])
    assert.equal(draftCalls, 2)
    assert.deepEqual(fixture.page.data.draft.new_items, [])
    assert.equal(fixture.page.data.operationBusy, '')
  } finally {
    fixture.restore()
    global.wx = previousWx
  }
})

test('cancelled or failed staged-item deletion retains the current draft', async (t) => {
  await t.test('cancellation calls no API', async () => {
    let deleteCalls = 0
    const previousWx = global.wx
    global.wx = {
      showToast() {},
      showModal(options) {
        options.success({ confirm: false })
      }
    }
    const fixture = loadPlanConfirmPage({
      draft: async () => draftPayload(),
      confirm: async () => ({ plan_id: 12, status: 'active' }),
      deleteDraftItem: async () => {
        deleteCalls += 1
      }
    })

    try {
      await fixture.page.onLoad.call(fixture.page, { plan_id: '12' })
      const before = fixture.page.data.draft
      await fixture.page.deleteNewItem.call(fixture.page, {
        currentTarget: { dataset: { itemId: 9 } }
      })
      assert.equal(deleteCalls, 0)
      assert.strictEqual(fixture.page.data.draft, before)
      assert.equal(fixture.page.data.operationBusy, '')
    } finally {
      fixture.restore()
      global.wx = previousWx
    }
  })

  await t.test('server failure shows its error and retains cards', async () => {
    let draftCalls = 0
    const toasts = []
    const previousWx = global.wx
    global.wx = {
      showToast(options) {
        toasts.push(options.title)
      },
      showModal(options) {
        options.success({ confirm: true })
      }
    }
    const fixture = loadPlanConfirmPage({
      draft: async () => {
        draftCalls += 1
        return draftPayload()
      },
      confirm: async () => ({ plan_id: 12, status: 'active' }),
      deleteDraftItem: async () => {
        throw { detail: '该作业已不能删除' }
      }
    })

    try {
      await fixture.page.onLoad.call(fixture.page, { plan_id: '12' })
      const before = fixture.page.data.draft
      await fixture.page.deleteNewItem.call(fixture.page, {
        currentTarget: { dataset: { itemId: 9 } }
      })
      assert.equal(draftCalls, 1)
      assert.strictEqual(fixture.page.data.draft, before)
      assert.deepEqual(toasts, ['该作业已不能删除'])
      assert.equal(fixture.page.data.operationBusy, '')
    } finally {
      fixture.restore()
      global.wx = previousWx
    }
  })
})

test('confirmation and deletion are mutually exclusive and release state after errors', async () => {
  const confirming = deferred()
  let confirmCalls = 0
  let deleteCalls = 0
  const toasts = []
  const previousWx = global.wx
  const previousGetApp = global.getApp
  global.getApp = () => ({ globalData: {} })
  global.wx = {
    showToast(options) {
      toasts.push(options.title)
    },
    showModal() {
      throw new Error('delete modal must not open while confirming')
    },
    setStorageSync() {},
    redirectTo() {}
  }
  const fixture = loadPlanConfirmPage({
    draft: async () => draftPayload(),
    confirm: async () => {
      confirmCalls += 1
      return confirming.promise
    },
    deleteDraftItem: async () => {
      deleteCalls += 1
    }
  })

  try {
    await fixture.page.onLoad.call(fixture.page, { plan_id: '12' })
    const firstConfirmation = fixture.page.confirm.call(fixture.page)
    const duplicateConfirmation = fixture.page.confirm.call(fixture.page)
    const deletion = fixture.page.deleteNewItem.call(fixture.page, {
      currentTarget: { dataset: { itemId: 9 } }
    })
    assert.equal(fixture.page.data.operationBusy, 'confirming')
    assert.equal(confirmCalls, 1)
    assert.equal(deleteCalls, 0)

    confirming.reject({ detail: '确认计划失败，请重试' })
    assert.equal(await duplicateConfirmation, null)
    assert.equal(await deletion, null)
    assert.equal(await firstConfirmation, null)
    assert.deepEqual(toasts, ['确认计划失败，请重试'])
    assert.equal(fixture.page.data.operationBusy, '')
    assert.equal(fixture.page.data.loading, false)
  } finally {
    fixture.restore()
    global.wx = previousWx
    global.getApp = previousGetApp
  }
})

test('draft load errors are visible and a hidden stale response cannot overwrite recovery', async () => {
  const firstDraft = deferred()
  let draftCalls = 0
  const previousWx = global.wx
  global.wx = { showToast() {} }
  const recovered = draftPayload({
    plan: { ...draftPayload().plan, title: '恢复后的服务端计划' }
  })
  const fixture = loadPlanConfirmPage({
    draft: async () => {
      draftCalls += 1
      if (draftCalls === 1) return firstDraft.promise
      if (draftCalls === 2) throw { detail: '草稿加载失败，请重试' }
      return recovered
    },
    confirm: async () => ({ plan_id: 12, status: 'active' }),
    deleteDraftItem: async () => ({})
  })

  try {
    const initialLoad = fixture.page.onLoad.call(fixture.page, { plan_id: '12' })
    fixture.page.onShow.call(fixture.page)
    fixture.page.onHide.call(fixture.page)
    firstDraft.resolve(draftPayload({
      plan: { ...draftPayload().plan, title: '已隐藏的过期计划' }
    }))
    await initialLoad
    assert.notEqual(fixture.page.data.draft.plan.title, '已隐藏的过期计划')

    await fixture.page.onShow.call(fixture.page)
    assert.equal(fixture.page.data.pageReady, false)
    assert.equal(fixture.page.data.loadError, '草稿加载失败，请重试')
    assert.equal(fixture.page.data.loadBusy, false)

    await fixture.page.loadDraft.call(fixture.page)
    assert.equal(fixture.page.data.draft.plan.title, '恢复后的服务端计划')
    assert.equal(fixture.page.data.pageReady, true)
    assert.equal(fixture.page.data.loadError, '')
  } finally {
    fixture.restore()
    global.wx = previousWx
  }
})
