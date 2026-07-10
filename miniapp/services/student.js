const { request } = require('./request')

function create(data) {
  return request({ url: '/students', method: 'POST', data })
}

module.exports = {
  create
}
