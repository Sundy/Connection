const LOCAL_API_BASE_URL = 'http://127.0.0.1:8000/api/v1'
const REMOTE_API_BASE_URL = 'https://connection.aceflow.top/api/v1'

function runningInDevTools() {
  if (typeof wx === 'undefined') return false
  try {
    const info = typeof wx.getDeviceInfo === 'function'
      ? wx.getDeviceInfo()
      : wx.getSystemInfoSync()
    return info && info.platform === 'devtools'
  } catch (_) {
    return false
  }
}

const API_BASE_URL = runningInDevTools()
  ? LOCAL_API_BASE_URL
  : REMOTE_API_BASE_URL

module.exports = {
  API_BASE_URL
}
