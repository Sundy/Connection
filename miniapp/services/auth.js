const { request } = require('./request')

function login(role) {
  return new Promise((resolve, reject) => {
    wx.login({
      success(res) {
        request({
          url: '/auth/wechat-login',
          method: 'POST',
          data: { code: res.code || `dev-${Date.now()}`, role }
        }).then(resolve).catch(reject)
      },
      fail: reject
    })
  })
}

function me() {
  return request({ url: '/auth/me' })
}

module.exports = {
  login,
  me
}
