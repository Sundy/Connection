const { request } = require('./request')

function list(options = {}) {
  const params = []
  if (options.status) params.push(`status=${encodeURIComponent(options.status)}`)
  if (options.studentId) params.push(`student_id=${encodeURIComponent(options.studentId)}`)
  const query = params.length ? `?${params.join('&')}` : ''
  return request({ url: `/notifications${query}` })
}

function read(notificationId) {
  return request({ url: `/notifications/${notificationId}/read`, method: 'POST' })
}

module.exports = {
  list,
  read
}
