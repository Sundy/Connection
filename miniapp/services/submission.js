const { request, upload } = require('./request')

function create(data) {
  return request({ url: '/submissions', method: 'POST', data })
}

function uploadMedia(submissionId, filePath, mediaType, sortOrder) {
  return upload({
    url: `/submissions/${submissionId}/media`,
    filePath,
    formData: { media_type: mediaType, sort_order: sortOrder }
  })
}

function complete(submissionId) {
  return request({ url: `/submissions/${submissionId}/complete`, method: 'POST' })
}

module.exports = {
  create,
  uploadMedia,
  complete
}
