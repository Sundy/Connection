const { request } = require('./request')

function generate(batchId) {
  return request({ url: `/plans/from-import/${batchId}/generate`, method: 'POST' })
}

function draft(planId) {
  return request({ url: `/plans/${planId}/draft` })
}

function confirm(planId, data = {}) {
  return request({ url: `/plans/${planId}/confirm`, method: 'POST', data })
}

function calendar(planId) {
  return request({ url: `/plans/${planId}/calendar` })
}

module.exports = {
  generate,
  draft,
  confirm,
  calendar
}
