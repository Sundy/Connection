const COMPLETED_STATUSES = ['corrected', 'needs_review']
const { taskStatusLabel } = require('./task-status')

function groupTasks(tasks, selectedSubject = '全部') {
  const grouped = new Map()
  ;(tasks || []).forEach((task) => {
    const subject = task.subject || '其他'
    if (!grouped.has(subject)) grouped.set(subject, [])
    grouped.get(subject).push(Object.assign({}, task, { statusLabel: taskStatusLabel(task.status, task.processing_stage) }))
  })
  const subjects = ['全部'].concat(Array.from(grouped.keys()))
  const groups = Array.from(grouped.entries())
    .filter(([subject]) => selectedSubject === '全部' || subject === selectedSubject)
    .map(([subject, subjectTasks]) => ({
      subject,
      tasks: subjectTasks,
      totalTasks: subjectTasks.length,
      completedTasks: subjectTasks.filter((task) => COMPLETED_STATUSES.includes(task.status)).length
    }))
  return { subjects, groups }
}

function tasksForDate(tasks, date) {
  return (tasks || []).filter((task) => task.task_date === date)
}

module.exports = { groupTasks, tasksForDate }
