const { request } = require('./request')

const runtimeOpenids = {}

function localOpenid(role) {
  const key = `clientOpenid:${role}`
  if (runtimeOpenids[key]) return runtimeOpenids[key]
  try {
    let value = wx.getStorageSync(key)
    if (!value) {
      value = `local-${role}-${Date.now()}-${Math.random().toString(16).slice(2)}`
      wx.setStorageSync(key, value)
    }
    runtimeOpenids[key] = value
    return value
  } catch (err) {
    runtimeOpenids[key] = runtimeOpenids[key] || `local-${role}-runtime`
    return runtimeOpenids[key]
  }
}

function login(role) {
  return new Promise((resolve, reject) => {
    wx.login({
      success(res) {
        request({
          url: '/auth/wechat-login',
          method: 'POST',
          data: { code: res.code || `dev-${Date.now()}`, role, client_openid: localOpenid(role) }
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
