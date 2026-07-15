const { API_BASE_URL } = require('../utils/constants')
const REQUEST_TIMEOUT = 120000

function normalizeError(err, fallback) {
  if (err && err.errMsg && err.errMsg.includes('timeout')) {
    return { detail: '请求超时，请稍后刷新结果', raw: err }
  }
  return err || { detail: fallback }
}

function responseError(res) {
  const data = res && res.data
  if (data && typeof data === 'object') {
    return { ...data, statusCode: res.statusCode }
  }
  return { detail: '请求失败', statusCode: res && res.statusCode, raw: res }
}

function request({ url, method = 'GET', data = {}, header = {}, timeout = REQUEST_TIMEOUT }) {
  const app = getApp()
  return new Promise((resolve, reject) => {
    wx.request({
      url: `${API_BASE_URL}${url}`,
      method,
      data,
      timeout,
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
        reject(responseError(res))
      },
      fail(err) {
        reject(normalizeError(err, '请求失败'))
      }
    })
  })
}

function upload({ url, filePath, name = 'file', formData = {}, timeout = REQUEST_TIMEOUT }) {
  const app = getApp()
  return new Promise((resolve, reject) => {
    wx.uploadFile({
      url: `${API_BASE_URL}${url}`,
      filePath,
      name,
      formData,
      timeout,
      header: {
        Authorization: app.globalData.token ? `Bearer ${app.globalData.token}` : ''
      },
      success(res) {
        let parsed = {}
        try {
          parsed = JSON.parse(res.data)
        } catch (err) {
          reject({ detail: '上传响应解析失败', raw: res })
          return
        }
        if (parsed.code === 0) {
          resolve(parsed.data)
          return
        }
        reject({ ...parsed, statusCode: res.statusCode })
      },
      fail(err) {
        reject(normalizeError(err, '上传失败'))
      }
    })
  })
}

module.exports = {
  request,
  upload
}
