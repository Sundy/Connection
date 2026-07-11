# Role Bottom Navigation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为家长端和学生端增加角色化底部导航，并把家庭、孩子和身份设置集中到角色化“我的”页面。

**Architecture:** 使用原生小程序自定义组件实现角色底栏，纯函数定义栏目和目标路径。一级页面显式引入组件；当前孩子和当前计划通过本地存储恢复，业务上下文失效时回退到接口数据。

**Tech Stack:** 原生微信小程序 JavaScript、WXML、WXSS、Node `node:test`。

## Global Constraints

- 直接在 `main` 修改，保留用户已有改动。
- 家长导航为“首页 / 学习计划 / 我的”。
- 学生导航为“今日学习 / 我的”。
- 所有标签和错误提示使用中文。
- 二级业务页面不显示底部导航。
- 底栏不得遮挡内容或设备安全区。
- 不增加后端角色或 API。

---

### Task 1: 导航模型与共享底栏组件

**Files:**
- Create: `miniapp/utils/role-navigation.js`
- Create: `miniapp/tests/role-navigation.test.js`
- Create: `miniapp/components/role-tabbar/index.js`
- Create: `miniapp/components/role-tabbar/index.json`
- Create: `miniapp/components/role-tabbar/index.wxml`
- Create: `miniapp/components/role-tabbar/index.wxss`

**Interfaces:**
- Produces: `navigationItems(role)` 返回中文栏目。
- Produces: `navigationTarget(role, key, currentPlanId)` 返回目标或缺少计划状态。
- Component properties: `active: String`。

- [ ] **Step 1: 写失败测试**

```javascript
assert.deepEqual(navigationItems('parent').map((item) => item.label), ['首页', '学习计划', '我的'])
assert.deepEqual(navigationItems('student').map((item) => item.label), ['今日学习', '我的'])
assert.equal(navigationTarget('parent', 'plan', 12).url, '/pages/parent/plan-calendar/index?plan_id=12')
assert.equal(navigationTarget('parent', 'plan', null).missingPlan, true)
```

- [ ] **Step 2: 运行 RED**

Run: `node --test miniapp/tests/role-navigation.test.js`

Expected: FAIL，模块不存在。

- [ ] **Step 3: 实现纯函数和组件**

组件读取 `currentRole`、`currentPlanId`，点击当前项不重复跳转；其他项使用 `wx.redirectTo`。缺少计划时跳转家长首页并显示“还没有学习计划，请先导入作业”。

- [ ] **Step 4: 运行 GREEN**

Run: `node --test miniapp/tests/role-navigation.test.js && node --check miniapp/components/role-tabbar/index.js`

Expected: PASS。

---

### Task 2: 一级页面接入底栏

**Files:**
- Modify: `miniapp/pages/parent/home/index.json`
- Modify: `miniapp/pages/parent/home/index.wxml`
- Modify: `miniapp/pages/parent/home/index.js`
- Modify: `miniapp/pages/parent/plan-calendar/index.json`
- Modify: `miniapp/pages/parent/plan-calendar/index.wxml`
- Modify: `miniapp/pages/student/today/index.json`
- Modify: `miniapp/pages/student/today/index.wxml`
- Modify: `miniapp/pages/student/today/index.js`
- Modify: `miniapp/app.wxss`
- Test: `miniapp/tests/role-navigation.test.js`

**Interfaces:**
- Consumes: `<role-tabbar active="home|plan|study|profile" />`。

- [ ] **Step 1: 添加页面接入契约测试并运行 RED**

读取三个 WXML/JSON 文件，断言一级页注册并渲染组件，学生页与家长首页不再包含“家庭设置”按钮。

- [ ] **Step 2: 接入组件**

一级页面根容器增加 `with-role-tabbar`；页面末尾放置组件。全局样式增加安全区底部留白。

- [ ] **Step 3: 验证**

Run: `node --test miniapp/tests/*.test.js`

Expected: 全部 PASS。

---

### Task 3: 当前孩子和当前计划持久化

**Files:**
- Create: `miniapp/utils/context-selection.js`
- Create: `miniapp/tests/context-selection.test.js`
- Modify: `miniapp/app.js`
- Modify: `miniapp/pages/parent/home/index.js`
- Modify: `miniapp/pages/parent/plan-calendar/index.js`
- Modify: `miniapp/pages/parent/plan-confirm/index.js`
- Modify: `miniapp/pages/auth/login/index.js`

**Interfaces:**
- Produces: `selectStoredStudent(students, storedId)`。
- Storage keys: `currentStudentId`、`currentPlanId`。

- [ ] **Step 1: 写有效和无效存储编号失败测试**

```javascript
assert.equal(selectStoredStudent([{ id: 2 }, { id: 3 }], 3).id, 3)
assert.equal(selectStoredStudent([{ id: 2 }], 99).id, 2)
```

- [ ] **Step 2: 运行 RED**

Run: `node --test miniapp/tests/context-selection.test.js`

Expected: FAIL。

- [ ] **Step 3: 实现恢复与写入**

登录和家长首页恢复孩子；首页报告、计划确认和计划日历写入计划编号。

- [ ] **Step 4: 验证 GREEN**

Run: `node --test miniapp/tests/context-selection.test.js`

Expected: PASS。

---

### Task 4: “我的”页面按角色重组

**Files:**
- Modify: `miniapp/pages/profile/index/index.json`
- Modify: `miniapp/pages/profile/index/index.js`
- Modify: `miniapp/pages/profile/index/index.wxml`
- Modify: `miniapp/pages/profile/index/index.wxss`
- Create: `miniapp/utils/profile-visibility.js`
- Create: `miniapp/tests/profile-visibility.test.js`

**Interfaces:**
- Produces: `profileVisibility(role, hasFamily)`。
- Adds: `selectStudent` 事件，写入全局状态和本地存储。

- [ ] **Step 1: 写角色可见性失败测试**

断言家长可见邀请码、孩子列表、添加孩子；学生只在未加入家庭时可见加入表单，永远不可见添加孩子和邀请码。

- [ ] **Step 2: 运行 RED**

Run: `node --test miniapp/tests/profile-visibility.test.js`

Expected: FAIL。

- [ ] **Step 3: 实现角色化界面**

家长区块按概览、孩子选择、家庭管理组织；学生区块展示绑定档案和家庭状态。页面末尾接入 `<role-tabbar active="profile" />`。

- [ ] **Step 4: 验证 GREEN 和语法**

Run: `node --test miniapp/tests/profile-visibility.test.js && node --check miniapp/pages/profile/index/index.js`

Expected: PASS。

---

### Task 5: 全量验证

**Files:**
- Modify: `README.md`

- [ ] **Step 1: 补充导航和角色页面说明**

说明家长、学生栏目和一级页面范围。

- [ ] **Step 2: 自动化验证**

Run: `.venv/bin/pytest backend/tests -q`

Expected: 30 tests PASS。

Run: `node --test miniapp/tests/*.test.js`

Expected: 全部 PASS。

Run: `find miniapp -name '*.js' -print0 | xargs -0 -n1 node --check`

Expected: exit 0。

Run: `git diff --check`

Expected: exit 0。

- [ ] **Step 3: 微信开发者工具验收**

检查家长三栏目、学生两栏目、安全区、孩子切换、无计划入口和二级页面无底栏。
