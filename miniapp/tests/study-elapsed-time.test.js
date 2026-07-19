const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

const root = path.resolve(__dirname, '..')

function restoreModule(modulePath, cachedModule) {
  if (cachedModule) require.cache[modulePath] = cachedModule
  else delete require.cache[modulePath]
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

async function withPageModule(pagePath, stubs, callback) {
  const originalPage = global.Page
  const cachedPage = require.cache[pagePath]
  const cachedModules = Object.fromEntries(Object.keys(stubs).map((modulePath) => [modulePath, require.cache[modulePath]]))
  let definition

  global.Page = (pageDefinition) => {
    definition = pageDefinition
  }
  for (const [modulePath, exports] of Object.entries(stubs)) {
    require.cache[modulePath] = { exports }
  }
  delete require.cache[pagePath]

  try {
    require(pagePath)
    return await callback(definition)
  } finally {
    restoreModule(pagePath, cachedPage)
    for (const [modulePath, cachedModule] of Object.entries(cachedModules)) {
      restoreModule(modulePath, cachedModule)
    }
    if (originalPage) global.Page = originalPage
    else delete global.Page
  }
}

function createPage(pageDefinition) {
  return {
    ...pageDefinition,
    data: { ...pageDefinition.data },
    setData(update) {
      Object.assign(this.data, update)
    }
  }
}

test('active requests the active study session for a daily task', async () => {
  const studyPath = require.resolve('../services/study')
  const requestPath = require.resolve('../services/request')
  const cachedStudy = require.cache[studyPath]
  const cachedRequest = require.cache[requestPath]
  const requests = []

  require.cache[requestPath] = { exports: { request: (options) => {
    requests.push(options)
    return Promise.resolve(null)
  } } }
  delete require.cache[studyPath]

  try {
    const studyApi = require(studyPath)
    await studyApi.active(42)
    assert.deepEqual(requests, [{ url: '/study-sessions/active?daily_task_id=42' }])
  } finally {
    restoreModule(studyPath, cachedStudy)
    restoreModule(requestPath, cachedRequest)
  }
})

test('timer page restores server elapsed time and keeps one display interval while visible', async () => {
  const pagePath = require.resolve('../pages/student/focus-timer/index')
  const taskPath = require.resolve('../services/task')
  const studyPath = require.resolve('../services/study')
  const formatPath = require.resolve('../utils/format')
  const previewPath = require.resolve('../utils/file-preview')
  const activeCalls = []
  const timers = []
  const cleared = []
  const originalSetInterval = global.setInterval
  const originalClearInterval = global.clearInterval

  global.setInterval = (callback) => {
    const timer = { callback }
    timers.push(timer)
    return timer
  }
  global.clearInterval = (timer) => {
    cleared.push(timer)
  }

  try {
    await withPageModule(pagePath, {
      [taskPath]: { detail: () => Promise.resolve({}) },
      [studyPath]: {
        active: (taskId) => {
          activeCalls.push(taskId)
          return Promise.resolve({ session_id: 9, elapsed_seconds: 125 })
        },
        start: () => Promise.resolve({ session_id: 9, elapsed_seconds: 125 })
      },
      [formatPath]: { formatDuration: (seconds) => `display:${seconds}` },
      [previewPath]: { previewSourceFile: () => {} }
    }, async (definition) => {
      const page = createPage(definition)
      page.setData({ taskId: '42' })

      await page.onShow()
      assert.deepEqual(activeCalls, [42])
      assert.deepEqual(page.data, {
        ...page.data,
        sessionId: 9,
        elapsed: 125,
        display: 'display:125',
        running: true,
        statusText: '计时中'
      })
      assert.equal(timers.length, 1)

      await page.onShow()
      assert.equal(timers.length, 2)
      assert.deepEqual(cleared, [timers[0]])

      page.onHide()
      assert.deepEqual(cleared, [timers[0], timers[1]])
    })
  } finally {
    global.setInterval = originalSetInterval
    global.clearInterval = originalClearInterval
  }
})

test('timer page ignores an active-session response after it is hidden', async () => {
  const pagePath = require.resolve('../pages/student/focus-timer/index')
  const taskPath = require.resolve('../services/task')
  const studyPath = require.resolve('../services/study')
  const formatPath = require.resolve('../utils/format')
  const previewPath = require.resolve('../utils/file-preview')
  const active = deferred()

  await withPageModule(pagePath, {
    [taskPath]: { detail: () => Promise.resolve({}) },
    [studyPath]: { active: () => active.promise, start: () => Promise.resolve({}) },
    [formatPath]: { formatDuration: (seconds) => String(seconds) },
    [previewPath]: { previewSourceFile: () => {} }
  }, async (definition) => {
    const page = createPage(definition)
    page.setData({ taskId: '42' })
    const recovery = page.onShow()
    page.onHide()
    active.resolve({ session_id: 9, elapsed_seconds: 125 })
    await recovery

    assert.equal(page.data.sessionId, null)
    assert.equal(page.data.running, false)
  })
})

test('timer page reports active-session recovery failure while visible', async () => {
  const pagePath = require.resolve('../pages/student/focus-timer/index')
  const taskPath = require.resolve('../services/task')
  const studyPath = require.resolve('../services/study')
  const formatPath = require.resolve('../utils/format')
  const previewPath = require.resolve('../utils/file-preview')
  const toasts = []
  const originalWx = global.wx
  global.wx = { showToast: (toast) => toasts.push(toast) }

  try {
    await withPageModule(pagePath, {
      [taskPath]: { detail: () => Promise.resolve({}) },
      [studyPath]: {
        active: () => Promise.reject({ detail: '恢复失败' }),
        start: () => Promise.resolve({})
      },
      [formatPath]: { formatDuration: (seconds) => String(seconds) },
      [previewPath]: { previewSourceFile: () => {} }
    }, async (definition) => {
      const page = createPage(definition)
      page.setData({ taskId: '42' })

      assert.equal(await page.onShow(), null)
      assert.deepEqual(toasts, [{ title: '恢复失败', icon: 'none' }])
    })
  } finally {
    if (originalWx) global.wx = originalWx
    else delete global.wx
  }
})

test('timer page silently resolves a recovery rejection after hide', async () => {
  const pagePath = require.resolve('../pages/student/focus-timer/index')
  const taskPath = require.resolve('../services/task')
  const studyPath = require.resolve('../services/study')
  const formatPath = require.resolve('../utils/format')
  const previewPath = require.resolve('../utils/file-preview')
  const active = deferred()
  const toasts = []
  const originalWx = global.wx
  global.wx = { showToast: (toast) => toasts.push(toast) }

  try {
    await withPageModule(pagePath, {
      [taskPath]: { detail: () => Promise.resolve({}) },
      [studyPath]: {
        active: () => active.promise,
        start: () => Promise.resolve({})
      },
      [formatPath]: { formatDuration: (seconds) => String(seconds) },
      [previewPath]: { previewSourceFile: () => {} }
    }, async (definition) => {
      const page = createPage(definition)
      page.setData({ taskId: '42' })
      const writes = []
      page.setData = function setData(update) {
        writes.push(update)
        Object.assign(this.data, update)
      }

      const recovery = page.onShow()
      page.onHide()
      active.reject({ detail: '恢复失败' })

      assert.equal(await recovery, null)
      assert.deepEqual(toasts, [])
      assert.deepEqual(writes, [])
    })
  } finally {
    if (originalWx) global.wx = originalWx
    else delete global.wx
  }
})

test('timer page ignores an old start response after a newer show recovery', async () => {
  const pagePath = require.resolve('../pages/student/focus-timer/index')
  const taskPath = require.resolve('../services/task')
  const studyPath = require.resolve('../services/study')
  const formatPath = require.resolve('../utils/format')
  const previewPath = require.resolve('../utils/file-preview')
  const start = deferred()
  const activeSessions = [
    { session_id: 9, elapsed_seconds: 125 },
    { session_id: 10, elapsed_seconds: 240 }
  ]
  const originalSetInterval = global.setInterval
  const originalClearInterval = global.clearInterval
  global.setInterval = () => ({})
  global.clearInterval = () => {}

  try {
    await withPageModule(pagePath, {
      [taskPath]: { detail: () => Promise.resolve({}) },
      [studyPath]: {
        active: () => Promise.resolve(activeSessions.shift()),
        start: () => start.promise
      },
      [formatPath]: { formatDuration: (seconds) => `display:${seconds}` },
      [previewPath]: { previewSourceFile: () => {} }
    }, async (definition) => {
      const page = createPage(definition)
      page.setData({ taskId: '42' })
      await page.onShow()

      const oldStart = page.start()
      page.onHide()
      await page.onShow()
      assert.equal(page.data.sessionId, 10)
      assert.equal(page.data.elapsed, 240)

      const oldSession = { session_id: 9, elapsed_seconds: 126 }
      start.resolve(oldSession)
      assert.equal(await oldStart, oldSession)
      assert.equal(page.data.sessionId, 10)
      assert.equal(page.data.elapsed, 240)
      assert.equal(page.data.display, 'display:240')

      page.onHide()
    })
  } finally {
    global.setInterval = originalSetInterval
    global.clearInterval = originalClearInterval
  }
})

test('upload waits for active-session recovery before creating its first submission', async () => {
  const pagePath = require.resolve('../pages/student/upload-homework/index')
  const taskPath = require.resolve('../services/task')
  const studyPath = require.resolve('../services/study')
  const submissionPath = require.resolve('../services/submission')
  const previewPath = require.resolve('../utils/file-preview')
  const statePath = require.resolve('../utils/submission-state')
  const active = deferred()
  const creates = []

  await withPageModule(pagePath, {
    [taskPath]: { detail: () => Promise.resolve({}) },
    [studyPath]: { active: () => active.promise },
    [submissionPath]: {
      create: (payload) => {
        creates.push(payload)
        return Promise.resolve({ submission_id: 14 })
      }
    },
    [previewPath]: { previewSourceFile: () => {} },
    [statePath]: { submissionHasHomework: () => true }
  }, async (definition) => {
    const page = createPage(definition)
    page.onLoad({ task_id: '42' })
    const submission = page.ensureSubmission('image')

    assert.deepEqual(creates, [])
    active.resolve({ session_id: 9 })
    await assert.doesNotReject(submission)
    assert.deepEqual(creates, [{
      daily_task_id: 42,
      submission_type: 'image',
      linked_study_session_id: 9
    }])
  })
})

test('upload submission creation is single-flight across recovery and create', async () => {
  const pagePath = require.resolve('../pages/student/upload-homework/index')
  const taskPath = require.resolve('../services/task')
  const studyPath = require.resolve('../services/study')
  const submissionPath = require.resolve('../services/submission')
  const previewPath = require.resolve('../utils/file-preview')
  const statePath = require.resolve('../utils/submission-state')
  const active = deferred()
  const create = deferred()
  const creates = []

  await withPageModule(pagePath, {
    [taskPath]: { detail: () => Promise.resolve({}) },
    [studyPath]: { active: () => active.promise },
    [submissionPath]: {
      create: (payload) => {
        creates.push(payload)
        return create.promise
      }
    },
    [previewPath]: { previewSourceFile: () => {} },
    [statePath]: { submissionHasHomework: () => true }
  }, async (definition) => {
    const page = createPage(definition)
    page.onLoad({ task_id: '42' })

    const first = page.ensureSubmission('photo')
    const second = page.ensureSubmission('video')

    assert.strictEqual(first, second)
    assert.deepEqual(creates, [])

    active.resolve({ session_id: 9 })
    await new Promise((resolve) => setImmediate(resolve))
    assert.equal(creates.length, 1)
    assert.deepEqual(creates[0], {
      daily_task_id: 42,
      submission_type: 'photo',
      linked_study_session_id: 9
    })
    assert.strictEqual(page.ensureSubmission('video'), first)

    create.resolve({ submission_id: 14 })
    assert.deepEqual(await Promise.all([first, second]), [14, 14])
    assert.equal(await page.ensureSubmission('video'), 14)
    assert.equal(creates.length, 1)
  })
})

test('upload clears failed submission creation so a later call can retry', async () => {
  const pagePath = require.resolve('../pages/student/upload-homework/index')
  const taskPath = require.resolve('../services/task')
  const studyPath = require.resolve('../services/study')
  const submissionPath = require.resolve('../services/submission')
  const previewPath = require.resolve('../utils/file-preview')
  const statePath = require.resolve('../utils/submission-state')
  let createCount = 0

  await withPageModule(pagePath, {
    [taskPath]: { detail: () => Promise.resolve({}) },
    [studyPath]: { active: () => Promise.resolve(null) },
    [submissionPath]: {
      create: () => {
        createCount += 1
        return createCount === 1
          ? Promise.reject(new Error('create failed'))
          : Promise.resolve({ submission_id: 15 })
      }
    },
    [previewPath]: { previewSourceFile: () => {} },
    [statePath]: { submissionHasHomework: () => true }
  }, async (definition) => {
    const page = createPage(definition)
    page.onLoad({ task_id: '42' })

    await assert.rejects(page.ensureSubmission('photo'), /create failed/)
    assert.equal(await page.ensureSubmission('photo'), 15)
    assert.equal(await page.ensureSubmission('video'), 15)
    assert.equal(createCount, 2)
  })
})

test('upload recovers from an active-session lookup failure with an unlinked submission', async () => {
  const pagePath = require.resolve('../pages/student/upload-homework/index')
  const taskPath = require.resolve('../services/task')
  const studyPath = require.resolve('../services/study')
  const submissionPath = require.resolve('../services/submission')
  const previewPath = require.resolve('../utils/file-preview')
  const statePath = require.resolve('../utils/submission-state')
  const creates = []
  const toasts = []
  const originalWx = global.wx
  global.wx = { showToast: (toast) => toasts.push(toast) }

  try {
    await withPageModule(pagePath, {
      [taskPath]: { detail: () => Promise.resolve({}) },
      [studyPath]: { active: () => Promise.reject({ detail: '恢复失败' }) },
      [submissionPath]: {
        create: (payload) => {
          creates.push(payload)
          return Promise.resolve({ submission_id: 14 })
        }
      },
      [previewPath]: { previewSourceFile: () => {} },
      [statePath]: { submissionHasHomework: () => true }
    }, async (definition) => {
      const page = createPage(definition)
      page.onLoad({ task_id: '42' })
      await page.ensureSubmission('image')

      assert.deepEqual(toasts, [{ title: '恢复失败', icon: 'none' }])
      assert.deepEqual(creates, [{
        daily_task_id: 42,
        submission_type: 'image',
        linked_study_session_id: null
      }])
    })
  } finally {
    if (originalWx) global.wx = originalWx
    else delete global.wx
  }
})

test('upload submission creation resolves without writing after page unload', async () => {
  const pagePath = require.resolve('../pages/student/upload-homework/index')
  const taskPath = require.resolve('../services/task')
  const studyPath = require.resolve('../services/study')
  const submissionPath = require.resolve('../services/submission')
  const previewPath = require.resolve('../utils/file-preview')
  const statePath = require.resolve('../utils/submission-state')
  const create = deferred()
  const task = deferred()

  await withPageModule(pagePath, {
    [taskPath]: { detail: () => task.promise },
    [studyPath]: { active: () => Promise.resolve(null) },
    [submissionPath]: { create: () => create.promise },
    [previewPath]: { previewSourceFile: () => {} },
    [statePath]: { submissionHasHomework: () => true }
  }, async (definition) => {
    const page = createPage(definition)
    const writes = []
    page.setData = function setData(update) {
      writes.push(update)
      Object.assign(this.data, update)
    }
    page.onLoad({ task_id: '42', session_id: '9' })
    writes.length = 0

    const submission = page.ensureSubmission('image')
    await Promise.resolve()
    page.onUnload()
    create.resolve({ submission_id: 14 })

    assert.equal(await submission, 14)
    assert.deepEqual(writes, [])
    assert.equal(page.data.submissionId, null)
  })
})

test('timer and upload source expose the recovery contract without pause controls', () => {
  const controller = fs.readFileSync(path.join(root, 'pages/student/focus-timer/index.js'), 'utf8')
  const markup = fs.readFileSync(path.join(root, 'pages/student/focus-timer/index.wxml'), 'utf8')
  const upload = fs.readFileSync(path.join(root, 'pages/student/upload-homework/index.js'), 'utf8')

  assert.match(controller, /studyApi\.active/)
  assert.match(controller, /restoreActiveSession/)
  assert.match(markup, /计时中/)
  assert.doesNotMatch(markup, /bindtap="pause"/)
  assert.doesNotMatch(markup, /bindtap="resume"/)
  assert.match(upload, /studyApi\.active/)
  assert.match(upload, /const creation = this\.sessionReady\.then/)
  assert.match(upload, /this\.submissionReady = creation\.catch/)
  assert.ok(upload.indexOf('this.sessionReady') < upload.indexOf('submissionApi.create'))
})

test('formats parent task elapsed time with compact Chinese units', () => {
  const formatterPath = path.join(root, 'utils/task-elapsed.js')
  assert.equal(fs.existsSync(formatterPath), true)
  const { formatTaskElapsed } = require(formatterPath)

  assert.equal(formatTaskElapsed(null), '未记录')
  assert.equal(formatTaskElapsed(0), '未记录')
  assert.equal(formatTaskElapsed(8), '8 秒')
  assert.equal(formatTaskElapsed(60), '1 分')
  assert.equal(formatTaskElapsed(1518), '25 分 18 秒')
  assert.equal(formatTaskElapsed(3600), '1 小时')
  assert.equal(formatTaskElapsed(3660), '1 小时 1 分')
  assert.equal(formatTaskElapsed(3918), '1 小时 5 分 18 秒')
})

test('normalizes invalid parent task elapsed values to the recorded state', () => {
  const formatterPath = path.join(root, 'utils/task-elapsed.js')
  assert.equal(fs.existsSync(formatterPath), true)
  const { formatTaskElapsed } = require(formatterPath)

  assert.equal(formatTaskElapsed(undefined), '未记录')
  assert.equal(formatTaskElapsed(-1), '未记录')
  assert.equal(formatTaskElapsed(Infinity), '未记录')
  assert.equal(formatTaskElapsed(8.9), '8 秒')
})

test('parent result binds prepared study duration to the task elapsed label', async () => {
  const formatterPath = path.join(root, 'utils/task-elapsed.js')
  assert.equal(fs.existsSync(formatterPath), true)

  const pagePath = require.resolve('../pages/parent/task-result/index')
  const reportPath = require.resolve('../services/report')
  const mediaPath = require.resolve('../services/correction-media')
  const statePath = require.resolve('../utils/result-state')
  await withPageModule(pagePath, {
    [reportPath]: { result: () => Promise.resolve({
      task: { title: '数学作业' },
      result: { study_duration_seconds: 1518 },
      submission: { status: 'corrected' },
      questions: [],
      pages: []
    }) },
    [mediaPath]: { downloadCorrectionPage: () => Promise.resolve('/tmp/page.jpg') },
    [statePath]: { resultViewState: () => ({ kind: 'corrected' }) }
  }, async (definition) => {
    const page = createPage(definition)
    page.data.taskId = 42

    await page.loadResult()
    await new Promise((resolve) => setImmediate(resolve))

    assert.equal(page.data.taskElapsedLabel, '25 分 18 秒')
  })
})

test('parent result displays unrecorded for zero or omitted task duration', async () => {
  const pagePath = require.resolve('../pages/parent/task-result/index')
  const reportPath = require.resolve('../services/report')
  const mediaPath = require.resolve('../services/correction-media')
  const statePath = require.resolve('../utils/result-state')
  const results = [
    {
      task: { title: '数学作业' },
      result: { study_duration_seconds: 0 },
      submission: { status: 'corrected' },
      questions: [],
      pages: []
    },
    {
      task: { title: '数学作业' },
      result: {},
      submission: { status: 'corrected' },
      questions: [],
      pages: []
    }
  ]

  await withPageModule(pagePath, {
    [reportPath]: { result: () => Promise.resolve(results.shift()) },
    [mediaPath]: { downloadCorrectionPage: () => Promise.resolve('/tmp/page.jpg') },
    [statePath]: { resultViewState: () => ({ kind: 'corrected' }) }
  }, async (definition) => {
    const page = createPage(definition)
    page.data.taskId = 42

    page.loadResult()
    await new Promise((resolve) => setImmediate(resolve))
    assert.equal(page.data.taskElapsedLabel, '未记录')

    page.loadResult()
    await new Promise((resolve) => setImmediate(resolve))
    assert.equal(page.data.taskElapsedLabel, '未记录')
  })
})

test('parent result exposes the task elapsed formatter and compact metadata row', () => {
  const controller = fs.readFileSync(path.join(root, 'pages/parent/task-result/index.js'), 'utf8')
  const markup = fs.readFileSync(path.join(root, 'pages/parent/task-result/index.wxml'), 'utf8')

  assert.match(controller, /const \{ formatTaskElapsed \} = require\('\.\.\/\.\.\/\.\.\/utils\/task-elapsed'\)/)
  assert.match(controller, /taskElapsedLabel: formatTaskElapsed\(taskResult\.study_duration_seconds\)/)
  assert.match(markup, /class="list-row result-meta-row"/)
  assert.match(markup, /本次任务耗时/)
  assert.match(markup, /\{\{taskElapsedLabel\}\}/)
})
