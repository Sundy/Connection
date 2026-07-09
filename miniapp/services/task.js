const { request } = require('./request')

function today(studentId) {
  return request({ url: `/tasks/today?student_id=${studentId}` })
}

function detail(taskId) {
  return request({ url: `/tasks/${taskId}` })
}

module.exports = {
  today,
  detail
}
