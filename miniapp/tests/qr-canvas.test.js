const test = require('node:test')
const assert = require('node:assert/strict')
const { drawQrToCanvas } = require('../utils/qr-canvas')

test('drawQrToCanvas renders a QR matrix to the requested canvas', () => {
  const calls = []
  const wxApi = {
    createCanvasContext(canvasId) {
      calls.push(['canvas', canvasId])
      return {
        setFillStyle(color) {
          calls.push(['fillStyle', color])
        },
        fillRect(x, y, width, height) {
          calls.push(['fillRect', x, y, width, height])
        },
        draw() {
          calls.push(['draw'])
        }
      }
    }
  }

  const rendered = drawQrToCanvas(wxApi, 'inviteQrCanvas', 'connection://join-family?invite_code=FAM-000001')

  assert.equal(rendered, true)
  assert.deepEqual(calls[0], ['canvas', 'inviteQrCanvas'])
  assert.ok(calls.filter((item) => item[0] === 'fillRect').length > 20)
  assert.deepEqual(calls.at(-1), ['draw'])
})

test('drawQrToCanvas ignores empty payloads', () => {
  const rendered = drawQrToCanvas({ createCanvasContext() {} }, 'inviteQrCanvas', '')

  assert.equal(rendered, false)
})
