const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')
const assert = require('node:assert/strict')

test('student result uses full annotated pages before text question fallback', () => {
  const root = path.join(__dirname, '..')
  const pageWxml = fs.readFileSync(path.join(root, 'pages/student/result-detail/index.wxml'), 'utf8')
  const pageJson = JSON.parse(fs.readFileSync(path.join(root, 'pages/student/result-detail/index.json'), 'utf8'))
  const componentWxml = fs.readFileSync(path.join(root, 'components/annotated-homework-page/index.wxml'), 'utf8')

  assert.match(pageWxml, /wx:for="{{result.pages}}"/)
  assert.match(pageWxml, /annotated-homework-page/)
  assert.match(pageWxml, /wx:elif="{{result.questions.length}}"/)
  assert.match(pageWxml, /wx:for="{{result.questions}}"/)
  assert.equal(pageJson.usingComponents['annotated-homework-page'], '/components/annotated-homework-page/index')
  assert.match(componentWxml, /annotation-error_circle/)
  assert.match(componentWxml, /annotation-correct_tick/)
})

test('parent result reuses full pages, keeps text fallback, and keeps review actions', () => {
  const root = path.join(__dirname, '..')
  const wxml = fs.readFileSync(path.join(root, 'pages/parent/task-result/index.wxml'), 'utf8')
  const config = JSON.parse(fs.readFileSync(path.join(root, 'pages/parent/task-result/index.json'), 'utf8'))

  assert.match(wxml, /annotated-homework-page/)
  assert.match(wxml, /confirmReview/)
  assert.match(wxml, /requestResubmit/)
  assert.match(wxml, /wx:if="{{result.pages.length}}"/)
  assert.match(wxml, /wx:for="{{result.questions}}"/)
  assert.equal(config.usingComponents['annotated-homework-page'], '/components/annotated-homework-page/index')
})

test('student refresh failure after first result keeps current display and retry affordance', async () => {
  const root = path.join(__dirname, '..')
  const pagePath = path.join(root, 'pages/student/result-detail/index.js')
  const reportPath = path.join(root, 'services/report.js')
  const mediaPath = path.join(root, 'services/correction-media.js')
  delete require.cache[require.resolve(pagePath)]
  require.cache[require.resolve(reportPath)] = {
    id: reportPath,
    filename: reportPath,
    loaded: true,
    exports: {
      result: (() => {
        let calls = 0
        return () => {
          calls += 1
          if (calls === 1) {
            return Promise.resolve({
              task: { title: '视频作业' },
              submission: { status: 'processing', processing_stage: 'grading' },
              result: null,
              questions: [],
              pages: []
            })
          }
          return Promise.reject({ detail: '网络异常' })
        }
      })()
    }
  }
  require.cache[require.resolve(mediaPath)] = {
    id: mediaPath,
    filename: mediaPath,
    loaded: true,
    exports: { downloadCorrectionPage: () => Promise.resolve('/tmp/page.jpg') }
  }

  const previousPage = global.Page
  const previousWx = global.wx
  const previousSetTimeout = global.setTimeout
  const previousClearTimeout = global.clearTimeout
  let page = null
  let scheduled = 0
  global.Page = (definition) => {
    page = Object.assign({}, definition)
    page.setData = (patch) => {
      Object.entries(patch).forEach(([key, value]) => {
        if (key.includes('.')) {
          const parts = key.split('.')
          let target = page.data
          parts.slice(0, -1).forEach((part) => { target = target[part] })
          target[parts[parts.length - 1]] = value
        } else {
          page.data[key] = value
        }
      })
    }
  }
  global.wx = { redirectTo() {}, showToast() {} }
  global.setTimeout = () => {
    scheduled += 1
    return scheduled
  }
  global.clearTimeout = () => {}

  try {
    require(pagePath)
    page.data.taskId = 123
    await page.refresh()
    assert.equal(page.data.viewState.shouldPoll, true)
    await page.refresh()
    assert.equal(page.data.loadError, '')
    assert.equal(page.data.refreshError, '网络异常')
    assert.equal(page.data.result.submission.status, 'processing')
    assert.ok(scheduled >= 2)
  } finally {
    global.Page = previousPage
    global.wx = previousWx
    global.setTimeout = previousSetTimeout
    global.clearTimeout = previousClearTimeout
    delete require.cache[require.resolve(pagePath)]
    delete require.cache[require.resolve(reportPath)]
    delete require.cache[require.resolve(mediaPath)]
  }
})
