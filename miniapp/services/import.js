const { request, upload } = require('./request')

function createBatch(data) {
  return request({ url: '/import-batches', method: 'POST', data })
}

function updateBatch(batchId, data) {
  return request({ url: `/import-batches/${batchId}`, method: 'PATCH', data })
}

function uploadFile(batchId, filePath, fileType, sortOrder, fileName = '', documentRole = 'homework') {
  return upload({
    url: `/import-batches/${batchId}/files`,
    filePath,
    formData: {
      file_type: fileType,
      sort_order: sortOrder,
      original_file_name: fileName,
      document_role: documentRole
    }
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

function deleteFile(fileId) {
  return request({ url: `/import-batches/files/${fileId}`, method: 'DELETE' })
}

module.exports = {
  createBatch,
  updateBatch,
  uploadFile,
  parseBatch,
  getBatch,
  listFiles,
  deleteFile
}
