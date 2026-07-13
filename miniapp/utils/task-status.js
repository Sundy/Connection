const STATUS_LABELS = {
  todo: '待学习',
  studying: '学习中',
  ready_to_submit: '待提交',
  uploaded: '待提交',
  processing: '批改中',
  correcting: '批改中',
  corrected: '已完成',
  needs_review: '待家长复核',
  resubmit_required: '需重新提交',
  failed: '批改失败'
}

const PROCESSING_STAGE_LABELS = {
  recognizing: '识别中',
  grading: '批改中',
  annotating: '生成批注中'
}

function taskStatusLabel(status, processingStage) {
  if (PROCESSING_STAGE_LABELS[processingStage]) return PROCESSING_STAGE_LABELS[processingStage]
  return STATUS_LABELS[status] || '待学习'
}

module.exports = { taskStatusLabel }
