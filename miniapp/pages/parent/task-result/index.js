const reportApi = require('../../../services/report')

Page({
  data: { result: { task: {}, result: null } },
  onLoad(options) {
    reportApi.result(options.task_id).then((result) => this.setData({ result }))
  }
})
