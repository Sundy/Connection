const reportApi = require('../../../services/report')
const { previewSourceFile } = require('../../../utils/file-preview')
const { resultViewState } = require('../../../utils/result-state')

Page({
  data: { result: { task: {}, result: null, submission: null, questions: [] }, viewState: resultViewState({ submission: null }), loadError: '', taskId: null },
  onLoad(options) {
    this.setData({ taskId: options.task_id })
    this.loadResult()
  },
  loadResult() {
    reportApi.result(this.data.taskId).then((result) => this.setData({ result, viewState: resultViewState(result), loadError: '' })).catch((err) => {
      this.setData({ loadError: err.detail || '加载批改结果失败。' })
    })
  },
  previewFile() {
    previewSourceFile(this.data.result.task.source_file)
  }
})
