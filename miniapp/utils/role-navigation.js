const NAVIGATION = {
  parent: [
    { key: 'home', label: '首页', icon: '⌂' },
    { key: 'plan', label: '学习计划', icon: '▦' },
    { key: 'profile', label: '我的', icon: '○' }
  ],
  student: [
    { key: 'study', label: '今日学习', icon: '✓' },
    { key: 'profile', label: '我的', icon: '○' }
  ]
}

function navigationItems(role) {
  return NAVIGATION[role] || NAVIGATION.student
}

function navigationTarget(role, key, currentPlanId) {
  if (key === 'profile') return { url: '/pages/profile/index/index' }
  if (role === 'parent' && key === 'home') return { url: '/pages/parent/home/index' }
  if (role === 'student' && key === 'study') return { url: '/pages/student/today/index' }
  if (role === 'parent' && key === 'plan') {
    if (!currentPlanId) return { url: '/pages/parent/home/index', missingPlan: true }
    return { url: `/pages/parent/plan-calendar/index?plan_id=${currentPlanId}` }
  }
  return { url: role === 'parent' ? '/pages/parent/home/index' : '/pages/student/today/index' }
}

module.exports = { navigationItems, navigationTarget }
