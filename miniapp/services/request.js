const { API_BASE_URL } = require('../utils/constants')

function request({ url, method = 'GET', data = {}, header = {} }) {
  const app = getApp()
  return new Promise((resolve, reject) => {
    wx.request({
      url: `${API_BASE_URL}${url}`,
      method,
      data,
      header: {
        'content-type': 'application/json',
        Authorization: app.globalData.token ? `Bearer ${app.globalData.token}` : '',
        ...header
      },
      success(res) {
        if (res.statusCode >= 200 && res.statusCode < 300 && res.data.code === 0) {
          resolve(res.data.data)
          return
        }
        reject(res.data || res)
      },
      fail: reject
    })
  })
}

function upload({ url, filePath, name = 'file', formData = {} }) {
  const app = getApp()
  return new Promise((resolve, reject) => {
    wx.uploadFile({
      url: `${API_BASE_URL}${url}`,
      filePath,
      name,
      formData,
      header: {
        Authorization: app.globalData.token ? `Bearer ${app.globalData.token}` : ''
      },
      success(res) {
        const parsed = JSON.parse(res.data)
        if (parsed.code === 0) {
          resolve(parsed.data)
          return
        }
        reject(parsed)
      },
      fail: reject
    })
  })
}

module.exports = {
  request,
  upload
}
