const { request } = require('./request')

function start(dailyTaskId) {
  return request({ url: '/study-sessions/start', method: 'POST', data: { daily_task_id: dailyTaskId } })
}

function active(dailyTaskId) {
  return request({ url: `/study-sessions/active?daily_task_id=${dailyTaskId}` })
}

function pause(sessionId) {
  return request({ url: `/study-sessions/${sessionId}/pause`, method: 'POST' })
}

function resume(sessionId) {
  return request({ url: `/study-sessions/${sessionId}/resume`, method: 'POST' })
}

function finish(sessionId) {
  return request({ url: `/study-sessions/${sessionId}/finish`, method: 'POST', data: { finish_reason: 'submit_now' } })
}

module.exports = {
  start,
  active,
  pause,
  resume,
  finish
}
