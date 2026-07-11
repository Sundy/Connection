function resultViewState(payload, timedOut = false) {
  const submission = payload && payload.submission
  if (!submission) return { kind: 'empty', title: '还没有提交', message: '完成作业后再来查看批改结果。', shouldPoll: false }
  if (submission.status === 'failed') return { kind: 'failed', title: '批改失败', message: submission.error_message || '批改服务暂时不可用，请重新提交。', shouldPoll: false }
  if (submission.status === 'needs_review') return { kind: 'needs_review', title: '需要家长复核', message: (payload.result || {}).review_reason || '部分内容无法可靠判断。', shouldPoll: false }
  if (submission.status === 'corrected') return { kind: 'corrected', title: '批改完成', message: '', shouldPoll: false }
  if (timedOut) return { kind: 'processing', title: '仍在批改', message: '处理时间较长，可以稍后回来查看。', shouldPoll: false }
  return { kind: 'processing', title: '批改中', message: '系统正在处理你的作业。', shouldPoll: true }
}

module.exports = { resultViewState }
