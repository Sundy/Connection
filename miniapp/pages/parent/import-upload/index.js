const importApi = require('../../../services/import')

Page({
  data: {
    batchId: null,
    files: [],
    loading: false
  },

  onLoad(options) {
    this.setData({ batchId: options.batch_id })
  },

  chooseImages() {
    wx.chooseMedia({
      count: 9,
      mediaType: ['image'],
      success: (res) => this.uploadPaths(res.tempFiles.map((item) => item.tempFilePath), 'image')
    })
  },

  chooseFiles() {
    wx.chooseMessageFile({
      count: 9,
      type: 'file',
      success: (res) => this.uploadPaths(res.tempFiles.map((item) => item.path), 'pdf')
    })
  },

  uploadPaths(paths, fileType) {
    const tasks = paths.map((path, index) => importApi.uploadFile(this.data.batchId, path, fileType, this.data.files.length + index))
    Promise.all(tasks).then((uploaded) => {
      this.setData({ files: this.data.files.concat(uploaded) })
    })
  },

  parse() {
    this.setData({ loading: true })
    importApi.parseBatch(this.data.batchId).then(() => {
      wx.navigateTo({ url: `/pages/parent/import-parse/index?batch_id=${this.data.batchId}` })
    }).finally(() => this.setData({ loading: false }))
  }
})
