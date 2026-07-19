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
