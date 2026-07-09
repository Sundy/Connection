function formatDuration(seconds) {
  const total = Math.max(Number(seconds || 0), 0)
  const minutes = Math.floor(total / 60)
  const sec = total % 60
  return `${minutes}:${String(sec).padStart(2, '0')}`
}

function today() {
  return new Date().toISOString().slice(0, 10)
}

module.exports = {
  formatDuration,
  today
}
