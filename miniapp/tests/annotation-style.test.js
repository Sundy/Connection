const test = require('node:test')
const assert = require('node:assert/strict')
const { annotationStyle } = require('../utils/annotation-style')

test('converts normalized geometry to bounded percentages', () => {
  assert.equal(
    annotationStyle({ x: 0.2, y: 0.3, width: 0.4, height: 0.1 }),
    'left:20%;top:30%;width:40%;height:10%;'
  )
  assert.equal(
    annotationStyle({ x: -1, y: 2, width: 4, height: 0 }),
    'left:0%;top:100%;width:100%;height:0%;'
  )
})
