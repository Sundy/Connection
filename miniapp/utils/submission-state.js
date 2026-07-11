function submissionHasHomework(detail) {
  return Boolean(detail && Number(detail.homework_media_count) > 0)
}

module.exports = { submissionHasHomework }
