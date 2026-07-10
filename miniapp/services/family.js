const { request } = require('./request')

function inviteCode() {
  return request({ url: '/families/invite-code', method: 'POST' })
}

function join(inviteCodeValue, studentId) {
  const data = { invite_code: inviteCodeValue }
  if (studentId) data.student_id = Number(studentId)
  return request({ url: '/families/join', method: 'POST', data })
}

module.exports = {
  inviteCode,
  join
}
