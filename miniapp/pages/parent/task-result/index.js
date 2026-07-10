const reportApi = require('../../../services/report')
const { previewSourceFile } = require('../../../utils/file-preview')

Page({
  data: { result: { task: {}, result: null } },
  onLoad(options) {
    reportApi.result(options.task_id).then((result) => this.setData({ result }))
  },
  previewFile() {
    previewSourceFile(this.data.result.task.source_file)
  }
})
