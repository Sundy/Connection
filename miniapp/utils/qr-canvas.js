const qrcode = require('./vendor/qrcode')

function drawQrToCanvas(wxApi, canvasId, text, options = {}) {
  if (!text || !wxApi || typeof wxApi.createCanvasContext !== 'function') return false
  const size = options.size || 168
  const padding = options.padding || 10
  const qr = qrcode(0, options.errorCorrectionLevel || 'M')
  qr.addData(text)
  qr.make()

  const count = qr.getModuleCount()
  const cell = (size - padding * 2) / count
  const context = wxApi.createCanvasContext(canvasId)
  context.setFillStyle('#ffffff')
  context.fillRect(0, 0, size, size)
  context.setFillStyle('#1d2d21')
  for (let row = 0; row < count; row += 1) {
    for (let col = 0; col < count; col += 1) {
      if (qr.isDark(row, col)) {
        context.fillRect(
          Math.floor(padding + col * cell),
          Math.floor(padding + row * cell),
          Math.ceil(cell),
          Math.ceil(cell)
        )
      }
    }
  }
  context.draw()
  return true
}

module.exports = {
  drawQrToCanvas
}
