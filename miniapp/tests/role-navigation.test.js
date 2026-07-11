const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const { navigationItems, navigationTarget } = require('../utils/role-navigation')

test('returns Chinese navigation items for each role', () => {
  assert.deepEqual(navigationItems('parent').map((item) => item.label), ['首页', '学习计划', '我的'])
  assert.deepEqual(navigationItems('student').map((item) => item.label), ['今日学习', '我的'])
})

test('resolves static and dynamic navigation targets', () => {
  assert.equal(navigationTarget('parent', 'home').url, '/pages/parent/home/index')
  assert.equal(navigationTarget('student', 'study').url, '/pages/student/today/index')
  assert.equal(navigationTarget('parent', 'plan', 12).url, '/pages/parent/plan-calendar/index?plan_id=12')
  assert.equal(navigationTarget('parent', 'plan', null).missingPlan, true)
  assert.equal(navigationTarget('student', 'profile').url, '/pages/profile/index/index')
})

test('top-level pages register and render the role tabbar', () => {
  const root = path.resolve(__dirname, '..')
  const pages = [
    ['pages/parent/home/index', 'home'],
    ['pages/parent/plan-calendar/index', 'plan'],
    ['pages/student/today/index', 'study'],
    ['pages/profile/index/index', 'profile']
  ]
  pages.forEach(([page, active]) => {
    const config = JSON.parse(fs.readFileSync(path.join(root, `${page}.json`), 'utf8'))
    const markup = fs.readFileSync(path.join(root, `${page}.wxml`), 'utf8')
    assert.equal(config.usingComponents['role-tabbar'], '/components/role-tabbar/index')
    assert.match(markup, new RegExp(`<role-tabbar active="${active}"`))
  })
  assert.doesNotMatch(fs.readFileSync(path.join(root, 'pages/parent/home/index.wxml'), 'utf8'), /家庭设置/)
  assert.doesNotMatch(fs.readFileSync(path.join(root, 'pages/student/today/index.wxml'), 'utf8'), /家庭设置/)
})
