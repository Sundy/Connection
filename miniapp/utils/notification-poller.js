const defaultNotificationApi = require('../services/notification')

function matchesNotification(notification, options) {
  if (!notification) return false
  if (options.types && options.types.length && !options.types.includes(notification.type)) return false
  if (options.studentId && Number(notification.student_id) !== Number(options.studentId)) return false
  return true
}

function pollNotificationsOnce(options = {}) {
  const notificationApi = options.notificationApi || defaultNotificationApi
  const listOptions = { status: 'pending' }
  if (options.studentId) listOptions.studentId = options.studentId
  return notificationApi.list(listOptions).then((notifications) => {
    const matched = (notifications || []).filter((item) => matchesNotification(item, options))
    if (!matched.length) return false
    return Promise.resolve(options.onNotifications && options.onNotifications(matched))
      .then(() => Promise.all(matched.map((item) => notificationApi.read(item.id))))
      .then(() => true)
  }).catch(() => false)
}

function startNotificationPolling(page, options = {}) {
  const intervalMs = options.intervalMs || 5000
  const setTimer = options.setTimer || setTimeout
  const clearTimer = options.clearTimer || clearTimeout
  const state = { stopped: false, timer: null, inFlight: false }

  const schedule = () => {
    if (state.stopped) return
    state.timer = setTimer(tick, intervalMs)
  }

  const tick = () => {
    if (state.stopped || state.inFlight) {
      schedule()
      return
    }
    state.inFlight = true
    pollNotificationsOnce(options).then(() => {
      state.inFlight = false
      schedule()
    })
  }

  tick()

  const stop = () => {
    state.stopped = true
    if (state.timer) clearTimer(state.timer)
    state.timer = null
  }

  page.notificationPollingStop = stop
  return stop
}

function stopNotificationPolling(page) {
  if (page && page.notificationPollingStop) {
    page.notificationPollingStop()
    page.notificationPollingStop = null
  }
}

module.exports = {
  pollNotificationsOnce,
  startNotificationPolling,
  stopNotificationPolling
}
