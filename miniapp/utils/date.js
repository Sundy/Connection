function parseIsoDate(isoDate) {
  const parts = String(isoDate || '').split('-').map(Number)
  return new Date(parts[0], parts[1] - 1, parts[2])
}

function toIsoDate(value) {
  const year = value.getFullYear()
  const month = String(value.getMonth() + 1).padStart(2, '0')
  const day = String(value.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

function todayIso() {
  return toIsoDate(new Date())
}

function shiftDate(isoDate, delta) {
  const value = parseIsoDate(isoDate)
  value.setDate(value.getDate() + delta)
  return toIsoDate(value)
}

function dateLabel(isoDate, referenceDate = todayIso()) {
  const offset = Math.round((parseIsoDate(isoDate) - parseIsoDate(referenceDate)) / 86400000)
  const value = parseIsoDate(isoDate)
  const prefix = offset === 0 ? '今天' : offset === -1 ? '昨天' : offset === 1 ? '明天' : ''
  const dayText = `${value.getMonth() + 1}月${value.getDate()}日`
  const weekdays = ['星期日', '星期一', '星期二', '星期三', '星期四', '星期五', '星期六']
  return `${prefix ? `${prefix} · ` : ''}${dayText} · ${weekdays[value.getDay()]}`
}

function initialPlanDate(plan, items, referenceDate = todayIso()) {
  const start = plan.start_date || ((items || [])[0] || {}).task_date || referenceDate
  const end = plan.end_date || start
  return referenceDate >= start && referenceDate <= end ? referenceDate : start
}

module.exports = { dateLabel, initialPlanDate, shiftDate, todayIso }
