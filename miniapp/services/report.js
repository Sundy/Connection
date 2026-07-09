const { request } = require('./request')

function home(studentId) {
  return request({ url: `/reports/home?student_id=${studentId}` })
}

function result(taskId) {
  return request({ url: `/results/tasks/${taskId}` })
}

module.exports = {
  home,
  result
}
