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
  const files = filePayloads()
  const fixture = loadPage({
    getBatch: async () => ({ id: 7, status: 'uploaded', blockers: [] }),
    listFiles: async () => files
  })

  try {
    await fixture.page.onLoad.call(fixture.page, { batch_id: '7' })

    assert.equal(fixture.page.data.batch.id, 7)
    assert.equal(fixture.page.data.homeworkFiles.length, 1)
    assert.equal(fixture.page.data.answerFiles.length, 2)
    assert.equal(fixture.page.data.homeworkFiles[0].display_name, '数学四年级下册第3单元练习')
    assert.equal(fixture.page.data.homeworkFiles[0].delete_match_status, 'matched')
    assert.equal(fixture.page.data.answerFiles[0].display_name, '《数学四年级下册第3单元练习》答案')
    assert.equal(fixture.page.data.answerFiles[1].match_reason, '没有唯一匹配的作业内容')
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
    fixture.page.setData.call(fixture.page, { batchId: '7' })
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
