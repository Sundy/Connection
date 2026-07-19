const test = require('node:test')
const assert = require('node:assert/strict')

test('pollNotificationsOnce handles matching unread notifications and marks them read', async () => {
  const handled = []
  const readIds = []
  const { pollNotificationsOnce } = require('../utils/notification-poller')

  const matched = { id: 1, type: 'assignment_updated', student_id: 7 }
  const ignored = { id: 2, type: 'submission_uploaded', student_id: 7 }
  const result = await pollNotificationsOnce({
    notificationApi: {
      async list(options) {
        assert.deepEqual(options, { status: 'pending', studentId: 7 })
        return [ignored, matched]
      },
      async read(id) {
        readIds.push(id)
        return { id, status: 'read' }
      }
    },
    studentId: 7,
    types: ['assignment_updated'],
    onNotifications(notifications) {
      handled.push(...notifications)
    }
  })

  assert.equal(result, true)
  assert.deepEqual(handled, [matched])
  assert.deepEqual(readIds, [1])
})

test('pollNotificationsOnce does not refresh for unrelated notifications', async () => {
  let handled = false
  const { pollNotificationsOnce } = require('../utils/notification-poller')

  const result = await pollNotificationsOnce({
    notificationApi: {
      async list() {
        return [{ id: 3, type: 'submission_uploaded', student_id: 8 }]
      },
      async read() {
        throw new Error('unrelated notifications should not be read')
      }
    },
    studentId: 7,
    types: ['assignment_updated'],
    onNotifications() {
      handled = true
    }
  })

  assert.equal(result, false)
  assert.equal(handled, false)
})
