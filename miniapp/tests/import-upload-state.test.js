const test = require('node:test')
const assert = require('node:assert/strict')
const path = require('node:path')

const pagePath = path.resolve(__dirname, '../pages/parent/import-upload/index.js')
const importPath = require.resolve('../services/import')
const planPath = require.resolve('../services/plan')
const requestPath = require.resolve('../services/request')

function cacheEntry(modulePath) {
  return require.cache[modulePath]
}

function restoreCache(modulePath, entry) {
  delete require.cache[modulePath]
  if (entry) require.cache[modulePath] = entry
}

function loadPage(importApi, planApi = { generate: async () => ({ assignment_batch_id: 1 }) }) {
  const previousPage = global.Page
  const previousImport = cacheEntry(importPath)
  const previousPlan = cacheEntry(planPath)
  let definition = null

  require.cache[importPath] = { exports: importApi }
  require.cache[planPath] = { exports: planApi }
  global.Page = (config) => {
    definition = config
  }
  delete require.cache[pagePath]
  require(pagePath)

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
      delete require.cache[pagePath]
      restoreCache(importPath, previousImport)
      restoreCache(planPath, previousPlan)
      global.Page = previousPage
    }
  }
}

function loadImportService(requestApi) {
  const previousRequest = cacheEntry(requestPath)
  const previousImport = cacheEntry(importPath)
  require.cache[requestPath] = { exports: requestApi }
  delete require.cache[importPath]
  const service = require(importPath)
  return {
    service,
    restore() {
      restoreCache(importPath, previousImport)
      restoreCache(requestPath, previousRequest)
    }
  }
}

function filePayloads() {
  return [
    {
      id: 11,
      file_id: 11,
      document_role: 'homework',
      display_name: '数学四年级下册第3单元练习',
      original_file_name: 'tmp-homework.pdf',
      parse_status: 'success',
      recognition_status: 'success',
      match_status: 'not_required',
      can_delete: true
    },
    {
      id: 12,
      file_id: 12,
      document_role: 'answer',
      display_name: '《数学四年级下册第3单元练习》答案',
      original_file_name: 'answer-a.pdf',
      parse_status: 'success',
      recognition_status: 'success',
      match_status: 'matched',
      matched_homework_file_id: 11,
      match_reason: '学科、单元与题号范围一致',
      can_delete: true
    },
    {
      id: 13,
      file_id: 13,
      document_role: 'answer',
      display_name: '未匹配答案',
      original_file_name: 'answer-b.pdf',
      parse_status: 'success',
      recognition_status: 'success',
      match_status: 'unmatched',
      match_reason: '没有唯一匹配的作业内容',
      can_delete: true
    }
  ]
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

test('import service sends document role and exposes staged-file deletion', async () => {
  const calls = []
  const fixture = loadImportService({
    upload: async (options) => {
      calls.push({ type: 'upload', options })
      return {}
    },
    request: async (options) => {
      calls.push({ type: 'request', options })
      return {}
    }
  })

  try {
    await fixture.service.uploadFile(7, '/tmp/answer.pdf', 'pdf', 2, '答案.pdf', 'answer')
    await fixture.service.deleteFile(19)

    assert.equal(calls[0].options.formData.document_role, 'answer')
    assert.equal(calls[0].options.formData.original_file_name, '答案.pdf')
    assert.deepEqual(calls[1], {
      type: 'request',
      options: { url: '/import-batches/files/19', method: 'DELETE' }
    })
  } finally {
    fixture.restore()
  }
})

test('page load restores batch and separates server-backed files by role', async () => {
  const files = filePayloads().concat({
    id: 14,
    file_id: 14,
    document_role: null,
    display_name: '语文阅读练习',
    parse_status: 'success',
    recognition_status: 'success',
    can_delete: true
  })
  const fixture = loadPage({
    getBatch: async () => ({ id: 7, status: 'uploaded', blockers: [] }),
    listFiles: async () => files
  })

  try {
    await fixture.page.onLoad.call(fixture.page, { batch_id: '7' })

    assert.equal(fixture.page.data.batch.id, 7)
    assert.equal(fixture.page.data.pageReady, true)
    assert.equal(fixture.page.data.homeworkFiles.length, 2)
    assert.equal(fixture.page.data.answerFiles.length, 2)
    assert.equal(fixture.page.data.homeworkFiles[0].display_name, '数学四年级下册第3单元练习')
    assert.equal(fixture.page.data.homeworkFiles[0].delete_match_status, 'matched')
    assert.equal(fixture.page.data.answerFiles[0].display_name, '《数学四年级下册第3单元练习》答案')
    assert.equal(fixture.page.data.answerFiles[1].match_reason, '没有唯一匹配的作业内容')
    assert.equal(fixture.page.data.homeworkFiles[1].display_name, '语文阅读练习')
  } finally {
    fixture.restore()
  }
})

test('confirmed batch redirects to canonical calendar exactly once after back navigation', async () => {
  const redirects = []
  const previousWx = global.wx
  global.wx = {
    showToast() {},
    redirectTo(options) {
      redirects.push(options.url)
      if (options.success) options.success({})
    }
  }
  const fixture = loadPage({
    getBatch: async () => ({
      id: 7,
      status: 'confirmed',
      can_edit: false,
      read_only: true,
      canonical_plan_id: 88,
      blockers: []
    }),
    listFiles: async () => filePayloads()
  })

  try {
    await fixture.page.onLoad.call(fixture.page, { batch_id: '7' })
    fixture.page.onHide.call(fixture.page)
    await fixture.page.onShow.call(fixture.page)

    assert.equal(fixture.page.data.readOnly, true)
    assert.equal(fixture.page.canStartOperation.call(fixture.page), false)
    assert.deepEqual(redirects, ['/pages/parent/plan-calendar/index?plan_id=88'])
  } finally {
    fixture.restore()
    global.wx = previousWx
  }
})

test('immutable batch without canonical plan remains read-only with Chinese guidance', async () => {
  const redirects = []
  const previousWx = global.wx
  global.wx = {
    showToast() {},
    redirectTo(options) {
      redirects.push(options.url)
    },
    chooseMedia() {
      assert.fail('只读页面不得打开文件选择器')
    }
  }
  let updates = 0
  const fixture = loadPage({
    getBatch: async () => ({
      id: 7,
      status: 'confirmed',
      can_edit: false,
      read_only: true,
      canonical_plan_id: null,
      blockers: []
    }),
    listFiles: async () => filePayloads(),
    updateBatch: async () => { updates += 1 }
  })

  try {
    await fixture.page.onLoad.call(fixture.page, { batch_id: '7' })
    fixture.page.chooseImages.call(fixture.page, {
      currentTarget: { dataset: { documentRole: 'homework' } }
    })
    await fixture.page.generatePlan.call(fixture.page)

    assert.equal(fixture.page.data.readOnly, true)
    assert.equal(typeof fixture.page.data.readOnlyNotice, 'string')
    assert.match(fixture.page.data.readOnlyNotice, /已确认.*不可修改/)
    assert.deepEqual(redirects, [])
    assert.equal(updates, 0)
  } finally {
    fixture.restore()
    global.wx = previousWx
  }
})

test('upload page formats structured API errors as Chinese strings', async () => {
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
  const fixture = loadPage({
    getBatch: async () => {
      throw { detail: [{ code: 'import_batch_immutable', message: '该批作业已确认，不能再修改' }] }
    },
    listFiles: async () => [],
    deleteFile: async () => {
      throw { detail: { code: 'import_batch_immutable', message: '该批作业已确认，不能再修改' } }
    }
  })

  try {
    await fixture.page.onLoad.call(fixture.page, { batch_id: '7' })
    assert.equal(typeof fixture.page.data.loadError, 'string')
    assert.equal(fixture.page.data.loadError, '该批作业已确认，不能再修改')

    fixture.page.setData.call(fixture.page, { pageReady: true, readOnly: false })
    await fixture.page.onDeleteFile.call(fixture.page, {
      currentTarget: { dataset: { fileId: 11, documentRole: 'homework', matchStatus: '' } }
    })
    assert.deepEqual(toasts, ['该批作业已确认，不能再修改'])
    assert.equal(typeof toasts[0], 'string')
  } finally {
    fixture.restore()
    global.wx = previousWx
  }
})

test('answer upload passes its role and reloads the authoritative server list', async () => {
  const uploadCalls = []
  let listCalls = 0
  const fixture = loadPage({
    uploadFile: async (...args) => {
      uploadCalls.push(args)
      return { id: 99, display_name: '本地上传响应不应直接进入列表' }
    },
    listFiles: async () => {
      listCalls += 1
      return filePayloads()
    }
  })

  try {
    fixture.page.setData.call(fixture.page, { batchId: '7', pageReady: true })
    await fixture.page.uploadSelectedFiles.call(
      fixture.page,
      [{ path: '/tmp/answer.pdf', name: '答案.pdf' }],
      'answer'
    )

    assert.equal(uploadCalls[0][5], 'answer')
    assert.equal(listCalls, 1)
    assert.equal(fixture.page.data.answerFiles[0].display_name, '《数学四年级下册第3单元练习》答案')
  } finally {
    fixture.restore()
  }
})

test('matched homework deletion warns about its answer and refreshes after confirmation', async () => {
  const files = filePayloads()
  let listCalls = 0
  let modalContent = ''
  const deleted = []
  const previousWx = global.wx
  global.wx = {
    showModal(options) {
      modalContent = options.content
      options.success({ confirm: true, cancel: false })
    },
    showToast() {}
  }
  const fixture = loadPage({
    getBatch: async () => ({ id: 7, status: 'uploaded', blockers: [] }),
    listFiles: async () => {
      listCalls += 1
      return files
    },
    deleteFile: async (fileId) => {
      deleted.push(fileId)
    }
  })

  try {
    await fixture.page.onLoad.call(fixture.page, { batch_id: '7' })
    await fixture.page.onDeleteFile.call(fixture.page, {
      currentTarget: {
        dataset: { fileId: 11, documentRole: 'homework', matchStatus: 'matched' }
      }
    })

    assert.match(modalContent, /同时删除.*答案/)
    assert.deepEqual(deleted, [11])
    assert.equal(listCalls, 2)
  } finally {
    fixture.restore()
    global.wx = previousWx
  }
})

test('file deletion calls no API when confirmation is cancelled', async () => {
  let deleteCalls = 0
  const previousWx = global.wx
  global.wx = {
    showModal(options) {
      options.success({ confirm: false, cancel: true })
    },
    showToast() {}
  }
  const fixture = loadPage({
    deleteFile: async () => {
      deleteCalls += 1
    },
    listFiles: async () => []
  })

  try {
    fixture.page.setData.call(fixture.page, { pageReady: true })
    await fixture.page.onDeleteFile.call(fixture.page, {
      currentTarget: {
        dataset: { fileId: 11, documentRole: 'homework', matchStatus: 'matched' }
      }
    })
    assert.equal(deleteCalls, 0)
  } finally {
    fixture.restore()
    global.wx = previousWx
  }
})

test('failed deletion retains cards and displays the server error', async () => {
  const files = filePayloads()
  const toastTitles = []
  const previousWx = global.wx
  global.wx = {
    showModal(options) {
      options.success({ confirm: true, cancel: false })
    },
    showToast(options) {
      toastTitles.push(options.title)
    }
  }
  const fixture = loadPage({
    getBatch: async () => ({ id: 7, status: 'uploaded', blockers: [] }),
    listFiles: async () => files,
    deleteFile: async () => {
      throw { detail: '该文件已进入生效计划，不能删除' }
    }
  })

  try {
    await fixture.page.onLoad.call(fixture.page, { batch_id: '7' })
    const before = fixture.page.data.homeworkFiles
    await fixture.page.onDeleteFile.call(fixture.page, {
      currentTarget: {
        dataset: { fileId: 11, documentRole: 'homework', matchStatus: 'matched' }
      }
    })

    assert.equal(fixture.page.data.homeworkFiles, before)
    assert.deepEqual(toastTitles, ['该文件已进入生效计划，不能删除'])
  } finally {
    fixture.restore()
    global.wx = previousWx
  }
})

test('parse completion refreshes files and blockers prevent plan generation', async () => {
  let listCalls = 0
  let generateCalls = 0
  const toastTitles = []
  const previousWx = global.wx
  global.wx = {
    showToast(options) {
      toastTitles.push(options.title)
    },
    navigateTo() {}
  }
  const fixture = loadPage({
    updateBatch: async () => ({}),
    parseBatch: async () => ({}),
    getBatch: async () => ({
      id: 7,
      status: 'parsed',
      file_count: 2,
      parsed_file_count: 2,
      blockers: [{ message: '答案未匹配到作业' }]
    }),
    listFiles: async () => {
      listCalls += 1
      return filePayloads()
    }
  }, {
    generate: async () => {
      generateCalls += 1
      return { assignment_batch_id: 55 }
    }
  })

  try {
    fixture.page.setData.call(fixture.page, {
      batchId: '7',
      pageReady: true,
      homeworkFiles: [filePayloads()[0]]
    })
    await fixture.page.generatePlan.call(fixture.page)

    assert.equal(listCalls, 2)
    assert.equal(generateCalls, 0)
    assert.deepEqual(toastTitles, ['答案未匹配到作业'])
    assert.equal(fixture.page.data.loading, false)
  } finally {
    fixture.restore()
    global.wx = previousWx
  }
})

test('generate stays blocked until every selected upload has settled', async () => {
  const upload = deferred()
  let parseCalls = 0
  let generateCalls = 0
  const previousWx = global.wx
  global.wx = { showToast() {}, navigateTo() {} }
  const fixture = loadPage({
    uploadFile: async () => upload.promise,
    listFiles: async () => filePayloads(),
    updateBatch: async () => ({}),
    parseBatch: async () => {
      parseCalls += 1
    }
  }, {
    generate: async () => {
      generateCalls += 1
    }
  })

  try {
    fixture.page.setData.call(fixture.page, {
      batchId: '7',
      pageReady: true,
      homeworkFiles: [filePayloads()[0]]
    })
    const uploading = fixture.page.uploadSelectedFiles.call(
      fixture.page,
      [{ path: '/tmp/homework.pdf', name: '作业.pdf' }],
      'homework'
    )

    assert.equal(fixture.page.data.operationBusy, 'uploading')
    await fixture.page.generatePlan.call(fixture.page)
    assert.equal(parseCalls, 0)
    assert.equal(generateCalls, 0)

    upload.resolve({ id: 21 })
    await uploading
    assert.equal(fixture.page.data.operationBusy, '')
  } finally {
    fixture.restore()
    global.wx = previousWx
  }
})

test('upload and delete methods are guarded while generation is active', async () => {
  const updating = deferred()
  let uploadCalls = 0
  let deleteCalls = 0
  let modalCalls = 0
  const previousWx = global.wx
  global.wx = {
    showToast() {},
    navigateTo() {},
    showModal() {
      modalCalls += 1
    }
  }
  const fixture = loadPage({
    uploadFile: async () => {
      uploadCalls += 1
    },
    deleteFile: async () => {
      deleteCalls += 1
    },
    updateBatch: async () => updating.promise,
    parseBatch: async () => ({}),
    getBatch: async () => ({ status: 'parsed', blockers: [], file_count: 1, parsed_file_count: 1 }),
    listFiles: async () => filePayloads()
  })

  try {
    fixture.page.setData.call(fixture.page, {
      batchId: '7',
      pageReady: true,
      homeworkFiles: [filePayloads()[0]]
    })
    const generating = fixture.page.generatePlan.call(fixture.page)
    assert.equal(fixture.page.data.operationBusy, 'generating')

    await fixture.page.uploadPaths.call(fixture.page, ['/tmp/answer.png'], 'image', 'answer')
    await fixture.page.onDeleteFile.call(fixture.page, {
      currentTarget: { dataset: { fileId: 11, documentRole: 'homework', matchStatus: 'matched' } }
    })
    assert.equal(uploadCalls, 0)
    assert.equal(deleteCalls, 0)
    assert.equal(modalCalls, 0)

    updating.resolve({})
    await generating
  } finally {
    fixture.restore()
    global.wx = previousWx
  }
})

test('partial multi-file failure waits for all uploads then refreshes exactly once', async () => {
  const first = deferred()
  const second = deferred()
  const third = deferred()
  const pending = [first, second, third]
  let refreshCalls = 0
  const toastTitles = []
  const previousWx = global.wx
  global.wx = {
    showToast(options) {
      toastTitles.push(options.title)
    }
  }
  const fixture = loadPage({
    uploadFile: async () => pending.shift().promise,
    listFiles: async () => {
      refreshCalls += 1
      return filePayloads()
    }
  })

  try {
    fixture.page.setData.call(fixture.page, { batchId: '7', pageReady: true })
    const uploading = fixture.page.uploadSelectedFiles.call(fixture.page, [
      { path: '/tmp/a.pdf', name: 'a.pdf' },
      { path: '/tmp/b.pdf', name: 'b.pdf' },
      { path: '/tmp/c.pdf', name: 'c.pdf' }
    ], 'homework')

    first.reject({ detail: 'a failed' })
    await new Promise((resolve) => setImmediate(resolve))
    assert.equal(refreshCalls, 0)
    second.resolve({ id: 22 })
    await new Promise((resolve) => setImmediate(resolve))
    assert.equal(refreshCalls, 0)
    third.reject({ detail: 'c failed' })
    await uploading

    assert.equal(refreshCalls, 1)
    assert.equal(fixture.page.data.homeworkFiles[0].display_name, '数学四年级下册第3单元练习')
    assert.deepEqual(toastTitles, ['上传完成：成功 1 份，失败 2 份'])
  } finally {
    fixture.restore()
    global.wx = previousWx
  }
})

test('unload clears polling timer and a stale tick cannot request or update state', async () => {
  let getBatchCalls = 0
  let listCalls = 0
  let staleTick = null
  let clearedTimer = null
  let setDataAfterUnload = 0
  const previousSetTimeout = global.setTimeout
  const previousClearTimeout = global.clearTimeout
  global.setTimeout = (callback) => {
    staleTick = callback
    return 91
  }
  global.clearTimeout = (timer) => {
    clearedTimer = timer
  }
  const fixture = loadPage({
    getBatch: async () => {
      getBatchCalls += 1
      return { status: 'parsing', file_count: 1, parsed_file_count: 0, blockers: [] }
    },
    listFiles: async () => {
      listCalls += 1
      return filePayloads()
    }
  })

  try {
    fixture.page.setData.call(fixture.page, { batchId: '7', pageReady: true })
    const originalSetData = fixture.page.setData
    fixture.page.setData = function setData(update) {
      if (this.pageDestroyed) setDataAfterUnload += 1
      originalSetData.call(this, update)
    }
    const polling = fixture.page.pollParsedBatch.call(fixture.page).catch((err) => err)
    await new Promise((resolve) => setImmediate(resolve))
    assert.equal(typeof staleTick, 'function')

    fixture.page.onUnload.call(fixture.page)
    assert.equal(clearedTimer, 91)
    const requestCount = getBatchCalls + listCalls
    staleTick()
    await new Promise((resolve) => setImmediate(resolve))

    assert.equal(getBatchCalls + listCalls, requestCount)
    assert.equal(setDataAfterUnload, 0)
    const cancellation = await polling
    assert.equal(cancellation.pollingCancelled, true)
  } finally {
    fixture.restore()
    global.setTimeout = previousSetTimeout
    global.clearTimeout = previousClearTimeout
  }
})

test('failed initial load disables the page and retry restores readiness', async () => {
  let attempts = 0
  const previousWx = global.wx
  global.wx = { showToast() {} }
  const fixture = loadPage({
    getBatch: async () => {
      attempts += 1
      if (attempts === 1) throw { statusCode: 401, detail: 'Invalid token' }
      return { id: 7, status: 'uploaded', blockers: [] }
    },
    listFiles: async () => filePayloads()
  })

  try {
    await fixture.page.onLoad.call(fixture.page, { batch_id: '7' })
    assert.equal(fixture.page.data.pageReady, false)
    assert.equal(fixture.page.data.loadError, 'Invalid token')
    assert.equal(fixture.page.lastLoadError.statusCode, 401)

    await fixture.page.retryLoad.call(fixture.page)
    assert.equal(fixture.page.data.pageReady, true)
    assert.equal(fixture.page.data.loadError, '')
    assert.equal(fixture.page.data.homeworkFiles.length, 1)
  } finally {
    fixture.restore()
    global.wx = previousWx
  }
})

test('delete success followed by refresh failure reports the distinct outcome', async () => {
  const toastTitles = []
  const previousWx = global.wx
  global.wx = {
    showModal(options) {
      options.success({ confirm: true, cancel: false })
    },
    showToast(options) {
      toastTitles.push(options.title)
    }
  }
  const fixture = loadPage({
    deleteFile: async () => ({}),
    listFiles: async () => {
      throw { detail: 'refresh failed' }
    }
  })

  try {
    fixture.page.setData.call(fixture.page, { batchId: '7', pageReady: true })
    await fixture.page.onDeleteFile.call(fixture.page, {
      currentTarget: { dataset: { fileId: 12, documentRole: 'answer', matchStatus: 'matched' } }
    })
    assert.deepEqual(toastTitles, ['文件已删除，但列表刷新失败，请重试'])
    assert.equal(fixture.page.data.operationBusy, '')
  } finally {
    fixture.restore()
    global.wx = previousWx
  }
})

test('pending and active recognition states use distinct Chinese copy', () => {
  const fixture = loadPage({})
  try {
    assert.equal(fixture.page.fileStatus({
      document_role: 'homework',
      parse_status: 'pending',
      recognition_status: 'pending'
    }).status_text, '待识别')
    assert.equal(fixture.page.fileStatus({
      document_role: 'answer',
      parse_status: 'queued',
      recognition_status: 'queued'
    }).status_text, '正在识别')
  } finally {
    fixture.restore()
  }
})

test('image and message-file selectors preserve their configured roles', () => {
  const selected = []
  const previousWx = global.wx
  global.wx = {
    chooseMedia(options) {
      options.success({ tempFiles: [{ tempFilePath: '/tmp/answer.png' }] })
    },
    chooseMessageFile(options) {
      options.success({ tempFiles: [{ path: '/tmp/homework.pdf', name: '作业.pdf' }] })
    }
  }
  const fixture = loadPage({})
  fixture.page.uploadPaths = (paths, type, role) => selected.push({ kind: 'image', paths, type, role })
  fixture.page.uploadSelectedFiles = (files, role) => selected.push({ kind: 'file', files, role })

  try {
    fixture.page.setData.call(fixture.page, { pageReady: true })
    fixture.page.chooseImages.call(fixture.page, {
      currentTarget: { dataset: { documentRole: 'answer' } }
    })
    fixture.page.chooseFiles.call(fixture.page, {
      currentTarget: { dataset: { documentRole: 'homework' } }
    })

    assert.equal(selected[0].role, 'answer')
    assert.equal(selected[0].type, 'image')
    assert.equal(selected[1].role, 'homework')
  } finally {
    fixture.restore()
    global.wx = previousWx
  }
})

test('returning from the native image picker still starts the upload', async () => {
  let uploadCalls = 0
  const previousWx = global.wx
  let fixture
  global.wx = {
    chooseMedia(options) {
      fixture.page.onHide.call(fixture.page)
      options.success({ tempFiles: [{ tempFilePath: '/tmp/homework.png' }] })
      if (options.complete) options.complete({})
    },
    showToast() {}
  }
  fixture = loadPage({
    uploadFile: async () => {
      uploadCalls += 1
      return { id: 88 }
    },
    listFiles: async () => filePayloads()
  })

  try {
    fixture.page.pageActive = true
    fixture.page.lifecycleToken = 1
    fixture.page.setData.call(fixture.page, { batchId: '7', pageReady: true })

    fixture.page.chooseImages.call(fixture.page, {
      currentTarget: { dataset: { documentRole: 'homework' } }
    })
    await new Promise((resolve) => setImmediate(resolve))

    assert.equal(uploadCalls, 1)
  } finally {
    fixture.restore()
    global.wx = previousWx
  }
})

test('upload and generation stay guarded while deletion is in flight', async () => {
  const deleting = deferred()
  let uploadCalls = 0
  let parseCalls = 0
  const previousWx = global.wx
  global.wx = {
    showModal(options) {
      options.success({ confirm: true, cancel: false })
    },
    showToast() {}
  }
  const fixture = loadPage({
    deleteFile: async () => deleting.promise,
    listFiles: async () => filePayloads(),
    uploadFile: async () => {
      uploadCalls += 1
    },
    updateBatch: async () => ({}),
    parseBatch: async () => {
      parseCalls += 1
    }
  })

  try {
    fixture.page.setData.call(fixture.page, {
      batchId: '7',
      pageReady: true,
      homeworkFiles: [filePayloads()[0]]
    })
    const deletion = fixture.page.onDeleteFile.call(fixture.page, {
      currentTarget: { dataset: { fileId: 11, documentRole: 'homework', matchStatus: 'matched' } }
    })
    await new Promise((resolve) => setImmediate(resolve))
    assert.equal(fixture.page.data.operationBusy, 'deleting')

    await fixture.page.uploadPaths.call(fixture.page, ['/tmp/homework.png'], 'image', 'homework')
    await fixture.page.generatePlan.call(fixture.page)
    assert.equal(uploadCalls, 0)
    assert.equal(parseCalls, 0)

    deleting.resolve({})
    await deletion
    assert.equal(fixture.page.data.operationBusy, '')
  } finally {
    fixture.restore()
    global.wx = previousWx
  }
})

test('show after hide reloads authoritative page state once', async () => {
  let getBatchCalls = 0
  let listCalls = 0
  const fixture = loadPage({
    getBatch: async () => {
      getBatchCalls += 1
      return { id: 7, status: 'uploaded', blockers: [] }
    },
    listFiles: async () => {
      listCalls += 1
      return filePayloads()
    }
  })

  try {
    await fixture.page.onLoad.call(fixture.page, { batch_id: '7' })
    fixture.page.onShow.call(fixture.page)
    assert.equal(getBatchCalls, 1)
    assert.equal(listCalls, 1)

    fixture.page.onHide.call(fixture.page)
    await fixture.page.onShow.call(fixture.page)
    assert.equal(getBatchCalls, 2)
    assert.equal(listCalls, 2)
    assert.equal(fixture.page.data.pageReady, true)
  } finally {
    fixture.restore()
  }
})

test('multi-file settlement works without native Promise.allSettled', async () => {
  const previousAllSettled = Promise.allSettled
  const previousWx = global.wx
  let refreshCalls = 0
  global.wx = { showToast() {} }
  Promise.allSettled = undefined
  const fixture = loadPage({
    uploadFile: async (batchId, path) => {
      if (path.endsWith('bad.pdf')) throw { detail: 'bad upload' }
      return { id: 1 }
    },
    listFiles: async () => {
      refreshCalls += 1
      return filePayloads()
    }
  })

  try {
    fixture.page.setData.call(fixture.page, { batchId: '7', pageReady: true })
    await fixture.page.uploadSelectedFiles.call(fixture.page, [
      { path: '/tmp/good.pdf', name: 'good.pdf' },
      { path: '/tmp/bad.pdf', name: 'bad.pdf' }
    ], 'homework')
    assert.equal(refreshCalls, 1)
    assert.equal(fixture.page.data.operationBusy, '')
  } finally {
    fixture.restore()
    Promise.allSettled = previousAllSettled
    global.wx = previousWx
  }
})

test('hidden upload keeps the page busy after show until transport settles and recovery reloads once', async () => {
  const uploadingTransport = deferred()
  let getBatchCalls = 0
  let listCalls = 0
  let parseCalls = 0
  let generateCalls = 0
  const previousWx = global.wx
  global.wx = { showToast() {}, navigateTo() {} }
  const fixture = loadPage({
    uploadFile: async () => uploadingTransport.promise,
    getBatch: async () => {
      getBatchCalls += 1
      return { id: 7, status: 'uploaded', blockers: [] }
    },
    listFiles: async () => {
      listCalls += 1
      return filePayloads()
    },
    updateBatch: async () => ({}),
    parseBatch: async () => {
      parseCalls += 1
    }
  }, {
    generate: async () => {
      generateCalls += 1
    }
  })

  try {
    fixture.page.pageActive = true
    fixture.page.lifecycleToken = 1
    fixture.page.setData.call(fixture.page, {
      batchId: '7',
      pageReady: true,
      homeworkFiles: [filePayloads()[0]]
    })
    const uploading = fixture.page.uploadPaths.call(
      fixture.page,
      ['/tmp/homework.png'],
      'image',
      'homework'
    )
    fixture.page.onHide.call(fixture.page)
    const showing = fixture.page.onShow.call(fixture.page)

    assert.equal(fixture.page.data.operationBusy, 'uploading')
    assert.equal(fixture.page.data.pageReady, true)
    await fixture.page.generatePlan.call(fixture.page)
    assert.equal(parseCalls, 0)
    assert.equal(generateCalls, 0)
    assert.equal(getBatchCalls, 0)
    assert.equal(listCalls, 0)

    uploadingTransport.resolve({ id: 88 })
    await uploading
    await showing

    assert.equal(getBatchCalls, 1)
    assert.equal(listCalls, 1)
    assert.equal(fixture.page.data.operationBusy, '')
    assert.equal(fixture.page.data.pageReady, true)
  } finally {
    fixture.restore()
    global.wx = previousWx
  }
})

test('retryLoad is single-flight and double retry starts only one request pair', async () => {
  const batchRequest = deferred()
  const filesRequest = deferred()
  let getBatchCalls = 0
  let listCalls = 0
  const fixture = loadPage({
    getBatch: async () => {
      getBatchCalls += 1
      return batchRequest.promise
    },
    listFiles: async () => {
      listCalls += 1
      return filesRequest.promise
    }
  })

  try {
    fixture.page.pageActive = true
    fixture.page.lifecycleToken = 1
    fixture.page.setData.call(fixture.page, { batchId: '7' })
    const first = fixture.page.retryLoad.call(fixture.page)
    const second = fixture.page.retryLoad.call(fixture.page)

    assert.equal(fixture.page.data.loadBusy, true)
    assert.equal(first, second)
    await new Promise((resolve) => setImmediate(resolve))
    assert.equal(getBatchCalls, 1)
    assert.equal(listCalls, 1)

    batchRequest.resolve({ id: 7, status: 'uploaded', blockers: [] })
    filesRequest.resolve(filePayloads())
    await Promise.all([first, second])
    assert.equal(fixture.page.data.pageReady, true)
    assert.equal(fixture.page.data.loadBusy, false)
  } finally {
    fixture.restore()
  }
})

test('partial upload plus refresh failure preserves counts in the warning', async () => {
  const toastTitles = []
  const previousWx = global.wx
  global.wx = {
    showToast(options) {
      toastTitles.push(options.title)
    }
  }
  let uploadIndex = 0
  const fixture = loadPage({
    uploadFile: async () => {
      uploadIndex += 1
      if (uploadIndex === 2) throw { detail: 'second failed' }
      return { id: uploadIndex }
    },
    listFiles: async () => {
      throw { detail: 'refresh failed' }
    }
  })

  try {
    fixture.page.pageActive = true
    fixture.page.lifecycleToken = 1
    fixture.page.setData.call(fixture.page, { batchId: '7', pageReady: true })
    await fixture.page.uploadSelectedFiles.call(fixture.page, [
      { path: '/tmp/a.pdf', name: 'a.pdf' },
      { path: '/tmp/b.pdf', name: 'b.pdf' }
    ], 'homework')

    assert.deepEqual(toastTitles, ['上传完成：成功 1 份，失败 1 份；列表刷新失败，请重试'])
  } finally {
    fixture.restore()
    global.wx = previousWx
  }
})

test('blocker-free generation calls the plan API and navigates to confirmation', async () => {
  let updateCalls = 0
  let parseCalls = 0
  let generateCalls = 0
  const navigations = []
  const previousWx = global.wx
  global.wx = {
    showToast() {},
    navigateTo(options) {
      navigations.push(options.url)
    }
  }
  const fixture = loadPage({
    updateBatch: async () => {
      updateCalls += 1
    },
    parseBatch: async () => {
      parseCalls += 1
    },
    getBatch: async () => ({
      id: 7,
      status: 'parsed',
      file_count: 1,
      parsed_file_count: 1,
      blockers: []
    }),
    listFiles: async () => filePayloads()
  }, {
    generate: async () => {
      generateCalls += 1
      return { assignment_batch_id: 55 }
    }
  })

  try {
    fixture.page.pageActive = true
    fixture.page.lifecycleToken = 1
    fixture.page.setData.call(fixture.page, {
      batchId: '7',
      pageReady: true,
      homeworkFiles: [filePayloads()[0]]
    })
    await fixture.page.generatePlan.call(fixture.page)

    assert.equal(updateCalls, 1)
    assert.equal(parseCalls, 1)
    assert.equal(generateCalls, 1)
    assert.deepEqual(navigations, ['/pages/parent/plan-confirm/index?plan_id=55'])
  } finally {
    fixture.restore()
    global.wx = previousWx
  }
})

test('unloaded upload settles without UI writes or retained operation transports', async () => {
  const uploadTransport = deferred()
  let setDataAfterUnload = 0
  const fixture = loadPage({
    uploadFile: async () => uploadTransport.promise,
    listFiles: async () => filePayloads()
  })

  try {
    fixture.page.pageActive = true
    fixture.page.lifecycleToken = 1
    fixture.page.setData.call(fixture.page, { batchId: '7', pageReady: true })
    const originalSetData = fixture.page.setData
    fixture.page.setData = function setData(update) {
      if (this.pageDestroyed) setDataAfterUnload += 1
      originalSetData.call(this, update)
    }
    const uploading = fixture.page.uploadPaths.call(
      fixture.page,
      ['/tmp/homework.png'],
      'image',
      'homework'
    )
    fixture.page.onUnload.call(fixture.page)
    uploadTransport.resolve({ id: 99 })
    await uploading
    await new Promise((resolve) => setImmediate(resolve))

    assert.equal(setDataAfterUnload, 0)
    assert.equal(fixture.page.activeOperationPromise, null)
    assert.equal(fixture.page.pendingTransports.size, 0)
  } finally {
    fixture.restore()
  }
})

test('hidden deletion keeps its transport lock until recovery reload completes', async () => {
  const deleteTransport = deferred()
  let getBatchCalls = 0
  let listCalls = 0
  const previousWx = global.wx
  global.wx = {
    showModal(options) {
      options.success({ confirm: true, cancel: false })
    },
    showToast() {}
  }
  const fixture = loadPage({
    deleteFile: async () => deleteTransport.promise,
    getBatch: async () => {
      getBatchCalls += 1
      return { id: 7, status: 'uploaded', blockers: [] }
    },
    listFiles: async () => {
      listCalls += 1
      return filePayloads()
    }
  })

  try {
    fixture.page.pageActive = true
    fixture.page.lifecycleToken = 1
    fixture.page.setData.call(fixture.page, { batchId: '7', pageReady: true })
    const deletion = fixture.page.onDeleteFile.call(fixture.page, {
      currentTarget: { dataset: { fileId: 12, documentRole: 'answer', matchStatus: 'matched' } }
    })
    await new Promise((resolve) => setImmediate(resolve))
    fixture.page.onHide.call(fixture.page)
    const showing = fixture.page.onShow.call(fixture.page)
    assert.equal(fixture.page.data.operationBusy, 'deleting')

    deleteTransport.resolve({})
    await deletion
    await showing
    assert.equal(getBatchCalls, 1)
    assert.equal(listCalls, 1)
    assert.equal(fixture.page.data.operationBusy, '')
  } finally {
    fixture.restore()
    global.wx = previousWx
  }
})

test('hidden generation waits its in-flight request then recovers without starting the next stage', async () => {
  const updateTransport = deferred()
  let parseCalls = 0
  let generateCalls = 0
  let getBatchCalls = 0
  let listCalls = 0
  const previousWx = global.wx
  global.wx = { showToast() {}, navigateTo() {} }
  const fixture = loadPage({
    updateBatch: async () => updateTransport.promise,
    parseBatch: async () => {
      parseCalls += 1
    },
    getBatch: async () => {
      getBatchCalls += 1
      return { id: 7, status: 'uploaded', blockers: [] }
    },
    listFiles: async () => {
      listCalls += 1
      return filePayloads()
    }
  }, {
    generate: async () => {
      generateCalls += 1
    }
  })

  try {
    fixture.page.pageActive = true
    fixture.page.lifecycleToken = 1
    fixture.page.setData.call(fixture.page, {
      batchId: '7',
      pageReady: true,
      homeworkFiles: [filePayloads()[0]]
    })
    const generation = fixture.page.generatePlan.call(fixture.page)
    fixture.page.onHide.call(fixture.page)
    const showing = fixture.page.onShow.call(fixture.page)
    assert.equal(fixture.page.data.operationBusy, 'generating')

    updateTransport.resolve({})
    await generation
    await showing
    assert.equal(parseCalls, 0)
    assert.equal(generateCalls, 0)
    assert.equal(getBatchCalls, 1)
    assert.equal(listCalls, 1)
    assert.equal(fixture.page.data.operationBusy, '')
  } finally {
    fixture.restore()
    global.wx = previousWx
  }
})

test('recovery is single-flight and only the latest visible generation may unlock', async () => {
  const uploadTransport = deferred()
  const batchLoads = [deferred(), deferred()]
  const fileLoads = [deferred(), deferred()]
  let getBatchCalls = 0
  let listCalls = 0
  const previousWx = global.wx
  global.wx = { showToast() {} }
  const fixture = loadPage({
    uploadFile: async () => uploadTransport.promise,
    getBatch: async () => {
      const request = batchLoads[getBatchCalls]
      getBatchCalls += 1
      return request.promise
    },
    listFiles: async () => {
      const request = fileLoads[listCalls]
      listCalls += 1
      return request.promise
    }
  })

  try {
    fixture.page.pageActive = true
    fixture.page.lifecycleToken = 1
    fixture.page.setData.call(fixture.page, { batchId: '7', pageReady: true })
    const uploading = fixture.page.uploadPaths.call(
      fixture.page,
      ['/tmp/homework.png'],
      'image',
      'homework'
    )
    fixture.page.onHide.call(fixture.page)
    const firstShow = fixture.page.onShow.call(fixture.page)
    uploadTransport.resolve({ id: 77 })
    await uploading
    await new Promise((resolve) => setImmediate(resolve))
    assert.equal(getBatchCalls, 1)
    assert.equal(listCalls, 1)

    fixture.page.onHide.call(fixture.page)
    const secondShow = fixture.page.onShow.call(fixture.page)
    batchLoads[0].resolve({ id: 7, status: 'uploaded', blockers: [] })
    fileLoads[0].resolve(filePayloads())
    await new Promise((resolve) => setImmediate(resolve))

    assert.equal(firstShow, secondShow)
    assert.equal(getBatchCalls, 2)
    assert.equal(listCalls, 2)
    assert.equal(fixture.page.data.operationBusy, 'uploading')

    batchLoads[1].resolve({ id: 7, status: 'uploaded', blockers: [] })
    fileLoads[1].resolve(filePayloads())
    await Promise.all([firstShow, secondShow])
    assert.equal(fixture.page.data.pageReady, true)
    assert.equal(fixture.page.data.operationBusy, '')
    assert.equal(getBatchCalls, 2)
    assert.equal(listCalls, 2)
  } finally {
    fixture.restore()
    global.wx = previousWx
  }
})

test('retryLoad handles synchronous API throws and releases every load reference', async () => {
  const fixture = loadPage({
    getBatch() {
      throw { statusCode: 401, detail: '同步登录失效' }
    },
    listFiles: async () => filePayloads()
  })

  try {
    fixture.page.pageActive = true
    fixture.page.lifecycleToken = 1
    fixture.page.setData.call(fixture.page, { batchId: '7' })
    let thrown = null
    try {
      await fixture.page.retryLoad.call(fixture.page)
    } catch (err) {
      thrown = err
    }

    assert.equal(thrown, null)
    assert.equal(fixture.page.data.pageReady, false)
    assert.equal(fixture.page.data.loadError, '同步登录失效')
    assert.equal(fixture.page.data.loadBusy, false)
    assert.equal(fixture.page.loadPromise, null)
    assert.equal(fixture.page.pendingTransports.size, 0)
  } finally {
    fixture.restore()
  }
})

test('generation handles synchronous update throws and releases operation state', async () => {
  const toastTitles = []
  const previousWx = global.wx
  global.wx = {
    showToast(options) {
      toastTitles.push(options.title)
    }
  }
  const fixture = loadPage({
    updateBatch() {
      throw { detail: '同步更新失败' }
    }
  })

  try {
    fixture.page.pageActive = true
    fixture.page.lifecycleToken = 1
    fixture.page.setData.call(fixture.page, {
      batchId: '7',
      pageReady: true,
      homeworkFiles: [filePayloads()[0]]
    })
    let thrown = null
    try {
      await fixture.page.generatePlan.call(fixture.page)
    } catch (err) {
      thrown = err
    }

    assert.equal(thrown, null)
    assert.deepEqual(toastTitles, ['同步更新失败'])
    assert.equal(fixture.page.data.operationBusy, '')
    assert.equal(fixture.page.activeOperationPromise, null)
    assert.equal(fixture.page.pendingTransports.size, 0)
  } finally {
    fixture.restore()
    global.wx = previousWx
  }
})

test('upload and deletion share lazy invocation for synchronous API throws', async () => {
  const toastTitles = []
  const previousWx = global.wx
  global.wx = {
    showModal(options) {
      options.success({ confirm: true, cancel: false })
    },
    showToast(options) {
      toastTitles.push(options.title)
    }
  }
  const fixture = loadPage({
    uploadFile() {
      throw { detail: '同步上传失败' }
    },
    deleteFile() {
      throw { detail: '同步删除失败' }
    },
    listFiles: async () => filePayloads()
  })

  try {
    fixture.page.pageActive = true
    fixture.page.lifecycleToken = 1
    fixture.page.setData.call(fixture.page, { batchId: '7', pageReady: true })
    await fixture.page.uploadPaths.call(fixture.page, ['/tmp/a.png'], 'image', 'homework')
    await fixture.page.onDeleteFile.call(fixture.page, {
      currentTarget: { dataset: { fileId: 11, documentRole: 'homework', matchStatus: 'matched' } }
    })

    assert.deepEqual(toastTitles, [
      '上传完成：成功 0 份，失败 1 份',
      '同步删除失败'
    ])
    assert.equal(fixture.page.data.operationBusy, '')
    assert.equal(fixture.page.activeOperationPromise, null)
    assert.equal(fixture.page.pendingTransports.size, 0)
  } finally {
    fixture.restore()
    global.wx = previousWx
  }
})

test('unload explicitly clears settled hidden operation and recovery references', async () => {
  const uploadTransport = deferred()
  const fixture = loadPage({
    uploadFile: async () => uploadTransport.promise,
    listFiles: async () => filePayloads()
  })

  try {
    fixture.page.pageActive = true
    fixture.page.lifecycleToken = 1
    fixture.page.setData.call(fixture.page, { batchId: '7', pageReady: true })
    const uploading = fixture.page.uploadPaths.call(
      fixture.page,
      ['/tmp/a.png'],
      'image',
      'homework'
    )
    fixture.page.onHide.call(fixture.page)
    uploadTransport.resolve({ id: 1 })
    await uploading
    await new Promise((resolve) => setImmediate(resolve))
    assert.ok(fixture.page.activeOperationPromise)
    assert.equal(fixture.page.operationNeedsRecovery, true)

    fixture.page.recoveryPromise = Promise.resolve()
    fixture.page.latestRecoveryToken = 123
    fixture.page.onUnload.call(fixture.page)
    assert.equal(fixture.page.activeOperationPromise, null)
    assert.equal(fixture.page.activeOperationKind, '')
    assert.equal(fixture.page.operationNeedsRecovery, false)
    assert.equal(fixture.page.recoveryPromise, null)
    assert.equal(fixture.page.latestRecoveryToken, null)
    assert.equal(fixture.page.pendingTransports.size, 0)
  } finally {
    fixture.restore()
  }
})
