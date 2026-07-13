const { API_BASE_URL } = require('../utils/constants')

function absoluteUrl(url) {
  if (/^https?:\/\//.test(url || '')) return url
  return `${API_BASE_URL}${url}`
}

function downloadCorrectionPage(url) {
  const app = getApp()
  return new Promise((resolve, reject) => {
    wx.downloadFile({
      url: absoluteUrl(url),
      header: { Authorization: app.globalData.token ? `Bearer ${app.globalData.token}` : '' },
      success(res) {
        if (res.statusCode >= 200 && res.statusCode < 300) return resolve(res.tempFilePath)
        reject({ detail: '作业图片加载失败' })
      },
      fail(err) { reject({ detail: '作业图片加载失败', raw: err }) }
    })
  })
}

module.exports = { absoluteUrl, downloadCorrectionPage }
