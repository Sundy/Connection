const { request, upload } = require('./request')

function createBatch(data) {
  return request({ url: '/import-batches', method: 'POST', data })
}

function uploadFile(batchId, filePath, fileType, sortOrder) {
  return upload({
    url: `/import-batches/${batchId}/files`,
    filePath,
    formData: { file_type: fileType, sort_order: sortOrder }
  })
}

function parseBatch(batchId) {
  return request({ url: `/import-batches/${batchId}/parse`, method: 'POST' })
}

function getBatch(batchId) {
  return request({ url: `/import-batches/${batchId}` })
}

function listFiles(batchId) {
  return request({ url: `/import-batches/${batchId}/files` })
}

module.exports = {
  createBatch,
  uploadFile,
  parseBatch,
  getBatch,
  listFiles
}
