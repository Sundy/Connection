const { request } = require('./request')

function home(studentId) {
  return request({ url: `/reports/home?student_id=${studentId}` })
}

function result(taskId) {
  return request({ url: `/results/tasks/${taskId}` })
}

function review(taskId, action, note = '') {
  return request({ url: `/results/tasks/${taskId}/review`, method: 'POST', data: { action, note: note || null } })
}

module.exports = {
  home,
  result,
  review
}
