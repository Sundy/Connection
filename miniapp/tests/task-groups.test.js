const test = require('node:test')
const assert = require('node:assert/strict')
const { groupTasks, tasksForDate } = require('../utils/task-groups')

test('groups tasks by subject and computes progress', () => {
  const result = groupTasks([
    { id: 1, subject: '数学', status: 'corrected' },
    { id: 2, subject: '数学', status: 'todo' },
    { id: 3, subject: '英语', status: 'needs_review' }
  ], '全部')
  assert.deepEqual(result.subjects, ['全部', '数学', '英语'])
  assert.deepEqual(result.groups.map((item) => [item.subject, item.completedTasks, item.totalTasks]), [
    ['数学', 1, 2], ['英语', 1, 1]
  ])
})

test('filters groups by selected subject', () => {
  const result = groupTasks([
    { id: 1, subject: '数学', status: 'todo' },
    { id: 2, subject: '英语', status: 'todo' }
  ], '英语')
  assert.deepEqual(result.groups.map((item) => item.subject), ['英语'])
})

test('filters tasks by date without changing the input', () => {
  const tasks = [
    { id: 1, task_date: '2026-07-11' },
    { id: 2, task_date: '2026-07-12' }
  ]
  assert.deepEqual(tasksForDate(tasks, '2026-07-12').map((item) => item.id), [2])
  assert.equal(tasks.length, 2)
})

test('uses processing stage labels when grouping task cards', () => {
  const result = groupTasks([
    { id: 1, subject: '数学', status: 'correcting', processing_stage: 'annotating' }
  ], '全部')
  assert.equal(result.groups[0].tasks[0].statusLabel, '生成批注中')
})
