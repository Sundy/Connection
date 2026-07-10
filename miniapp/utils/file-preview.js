const { API_BASE_URL } = require('./constants')

function absoluteUrl(path) {
  if (!path) return ''
  if (path.startsWith('http')) return path
  const origin = API_BASE_URL.replace('/api/v1', '')
  return `${origin}${path}`
}

function previewSourceFile(sourceFile) {
  if (!sourceFile || (!sourceFile.file_url && !sourceFile.preview_url)) {
    wx.showToast({ title: '暂无原文档', icon: 'none' })
    return
  }
  const url = absoluteUrl(sourceFile.file_url || sourceFile.preview_url)
  const fileType = normalizeFileType(sourceFile.file_type || sourceFile.file_name || url)
  if (['image', 'screenshot'].includes(fileType)) {
    wx.previewImage({ urls: [url] })
    return
  }
  wx.downloadFile({
    url,
    filePath: downloadPath(sourceFile.file_name || url, fileType),
    success(res) {
      if (res.statusCode && res.statusCode !== 200) {
        wx.showToast({ title: '文件下载失败', icon: 'none' })
        return
      }
      const filePath = res.filePath || res.tempFilePath
      if (!filePath) {
        wx.showToast({ title: '文件预览失败', icon: 'none' })
        return
      }
      const options = {
        filePath,
        showMenu: true,
        fail() {
          wx.showToast({ title: '文件打开失败', icon: 'none' })
        }
      }
      if (fileType) {
        options.fileType = fileType
      }
      wx.openDocument({
        ...options
      })
    },
    fail() {
      wx.showToast({ title: '文件预览失败', icon: 'none' })
    }
  })
}

function downloadPath(fileName, fileType) {
  if (!wx.env || !wx.env.USER_DATA_PATH) return undefined
  const safeName = safeDownloadName(fileName, fileType)
  return `${wx.env.USER_DATA_PATH}/${Date.now()}-${safeName}`
}

function safeDownloadName(fileName, fileType) {
  const rawName = decodeURIComponent((fileName || 'homework').split('?')[0].split('/').pop() || 'homework')
  const cleaned = rawName.replace(/[\\/:*?"<>|]/g, '_').slice(0, 80) || 'homework'
  if (/\.[A-Za-z0-9]{2,5}$/.test(cleaned) || !fileType) return cleaned
  return `${cleaned}.${fileType}`
}

function normalizeFileType(value) {
  const lower = (value || '').toLowerCase()
  if (lower.includes('screenshot') || lower.includes('image') || lower.endsWith('.jpg') || lower.endsWith('.jpeg') || lower.endsWith('.png')) return 'image'
  if (lower.includes('pdf') || lower.endsWith('.pdf')) return 'pdf'
  if (lower.includes('docx') || lower.endsWith('.docx')) return 'docx'
  if (lower.includes('doc') || lower.endsWith('.doc')) return 'doc'
  if (lower.includes('xlsx') || lower.endsWith('.xlsx')) return 'xlsx'
  if (lower.includes('xls') || lower.endsWith('.xls')) return 'xls'
  if (lower.includes('pptx') || lower.endsWith('.pptx')) return 'pptx'
  if (lower.includes('ppt') || lower.endsWith('.ppt')) return 'ppt'
  return ''
}

module.exports = {
  absoluteUrl,
  downloadPath,
  previewSourceFile
}
