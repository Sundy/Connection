function clamp(value) {
  const number = Number(value)
  if (!Number.isFinite(number)) return 0
  return Math.max(0, Math.min(1, number))
}

function percentage(value) {
  return `${Number((clamp(value) * 100).toFixed(2))}%`
}

function annotationStyle(annotation) {
  return `left:${percentage(annotation.x)};top:${percentage(annotation.y)};width:${percentage(annotation.width)};height:${percentage(annotation.height)};`
}

module.exports = { annotationStyle }
