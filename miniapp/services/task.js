const { request } = require('./request')

function today(studentId, targetDate) {
  const dateQuery = targetDate ? `&target_date=${encodeURIComponent(targetDate)}` : ''
  return request({ url: `/tasks/today?student_id=${studentId}${dateQuery}` })
}

function detail(taskId) {
  return request({ url: `/tasks/${taskId}` })
}

module.exports = {
  today,
  detail
}
