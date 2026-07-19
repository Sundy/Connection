function formatTaskElapsed(seconds) {
  const total = Number(seconds)
  if (!Number.isFinite(total) || total <= 0) return '未记录'

  const normalized = Math.floor(total)
  if (!normalized) return '未记录'

  const hours = Math.floor(normalized / 3600)
  const minutes = Math.floor((normalized % 3600) / 60)
  const remainingSeconds = normalized % 60
  const parts = []
  if (hours) parts.push(`${hours} 小时`)
  if (minutes) parts.push(`${minutes} 分`)
  if (remainingSeconds) parts.push(`${remainingSeconds} 秒`)
  return parts.join(' ')
}

module.exports = { formatTaskElapsed }
