# 小程序角色会话与“我的”页简化实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让小程序记住首次选择的家长或学生身份，后续自动恢复登录，并在精简后的“我的”页安全切换身份。

**Architecture:** 登录页继续作为固定启动入口，但把会话恢复和切换副作用集中到 `services/session.js`。该服务先验证缓存 Token，只在 401 时按保存角色静默登录，并在目标上下文完整加载后统一提交全局和本地状态；“我的”页只管理展开状态、确认弹窗和调用会话服务。

**Tech Stack:** 原生微信小程序 JavaScript/WXML/WXSS、CommonJS、Node.js `node:test`、FastAPI 现有鉴权接口。

## Global Constraints

- 不修改后端接口、用户模型、家庭模型或学生模型。
- 家长和学生继续使用 `clientOpenid:<role>` 对应的两个独立身份。
- 首次无合法角色缓存时才显示角色选择；已保存角色的恢复失败只显示重试。
- 只有目标身份登录和上下文加载都成功后，才覆盖当前角色和持久化 Token。
- 保持现有两空格 JavaScript/JSON 缩进及 `index.*` 页面组织。
- 先写失败测试并确认预期失败，再写生产代码。

---

## File Structure

- Create: `miniapp/services/session.js`，封装恢复、登录、提交上下文和角色首页解析。
- Create: `miniapp/tests/session.test.js`，覆盖缓存恢复、401 重登、网络失败及切换回滚。
- Create: `miniapp/tests/request.test.js`，覆盖请求错误保留 HTTP 状态码。
- Modify: `miniapp/services/request.js`，为业务错误附加 `statusCode`，供会话层区分 401 与网络错误。
- Modify: `miniapp/pages/auth/login/index.js`，实现初始化、首次选择、恢复失败和重试状态。
- Modify: `miniapp/pages/auth/login/index.wxml`，为三类启动视图提供明确反馈。
- Modify: `miniapp/pages/auth/login/index.wxss`，添加紧凑的启动反馈样式。
- Create: `miniapp/tests/login-page-layout.test.js`，验证启动页不会在初始化时暴露选择按钮且使用 `reLaunch`。
- Modify: `miniapp/pages/profile/index/index.js`，管理分组展开、资料保存收起和确认式身份切换。
- Modify: `miniapp/pages/profile/index/index.wxml`，改为身份摘要加分组列表。
- Modify: `miniapp/pages/profile/index/index.wxss`，实现紧凑列表、展开面板和身份强调区。
- Modify: `miniapp/tests/profile-page-layout.test.js`，验证精简结构和切换入口。
- Modify: `miniapp/utils/profile-visibility.js`，保持角色功能可见性为单一来源。
- Modify: `miniapp/tests/profile-visibility.test.js`，修正现有与实现矛盾的断言并覆盖三种用户状态。

### Task 1: 可识别鉴权失败的会话服务

**Files:**
- Create: `miniapp/tests/request.test.js`
- Create: `miniapp/tests/session.test.js`
- Create: `miniapp/services/session.js`
- Modify: `miniapp/services/request.js`

**Interfaces:**
- Consumes: `auth.login(role): Promise<{token,user}>`、`auth.me(): Promise<context>`、`selectStoredStudent(students, storedId)`。
- Produces: `roleHome(role): string`、`restore(role, token, options?): Promise<{role,url,context}>`、`loginAs(role, options?): Promise<{role,url,context}>`、`isValidRole(role): boolean`。

- [ ] **Step 1: 写请求状态码失败测试**

在 `miniapp/tests/request.test.js` 中创建真实的 `request()` 调用测试，用伪造的 `wx.request` 返回 401：

```js
const test = require('node:test')
const assert = require('node:assert/strict')

test('request errors retain the HTTP status code', async () => {
  global.getApp = () => ({ globalData: { token: 'expired' } })
  global.wx = {
    request(options) {
      options.success({ statusCode: 401, data: { detail: 'Invalid token' } })
    }
  }
  const { request } = require('../services/request')

  await assert.rejects(request({ url: '/auth/me' }), (err) => {
    assert.equal(err.statusCode, 401)
    assert.equal(err.detail, 'Invalid token')
    return true
  })
})
```

- [ ] **Step 2: 运行请求测试并确认失败**

Run: `node --test miniapp/tests/request.test.js`

Expected: FAIL，错误对象的 `statusCode` 为 `undefined`。

- [ ] **Step 3: 为请求错误附加状态码**

在 `miniapp/services/request.js` 添加并使用：

```js
function responseError(res) {
  const data = res && res.data
  if (data && typeof data === 'object') return { ...data, statusCode: res.statusCode }
  return { detail: '请求失败', statusCode: res && res.statusCode, raw: res }
}
```

将 `request()` 非成功响应的 `reject(res.data || res)` 改为 `reject(responseError(res))`。上传响应解析成功但业务失败时同样附加 `statusCode`。

- [ ] **Step 4: 运行请求测试并确认通过**

Run: `node --test miniapp/tests/request.test.js`

Expected: PASS。

- [ ] **Step 5: 写会话服务失败测试**

在 `miniapp/tests/session.test.js` 中以依赖注入方式构造 `app`、`storage` 和 `authApi`，至少覆盖以下独立用例：

```js
test('restores a valid parent session without logging in again', async () => {
  const fixture = createFixture({ storedRole: 'parent', token: 'cached-token' })
  fixture.authApi.me = async () => parentContext

  const result = await restore('parent', 'cached-token', fixture)

  assert.equal(fixture.authApi.loginCalls.length, 0)
  assert.equal(result.url, '/pages/parent/home/index')
  assert.equal(fixture.app.globalData.currentRole, 'parent')
})

test('silently logs in with the stored role after a 401', async () => {
  const fixture = createFixture({ token: 'expired-token' })
  fixture.authApi.me = sequence([
    Promise.reject({ statusCode: 401 }),
    Promise.resolve(studentContext)
  ])

  await restore('student', 'expired-token', fixture)

  assert.deepEqual(fixture.authApi.loginCalls, ['student'])
  assert.equal(fixture.storage.values.currentRole, 'student')
})

test('does not replace the active session when switching fails', async () => {
  const fixture = createFixture({ token: 'parent-token', currentRole: 'parent' })
  fixture.authApi.login = async () => ({ token: 'student-token', user: { role: 'student' } })
  fixture.authApi.me = async () => { throw { detail: 'network down' } }

  await assert.rejects(loginAs('student', fixture))

  assert.equal(fixture.app.globalData.token, 'parent-token')
  assert.equal(fixture.storage.values.currentRole, 'parent')
})
```

同文件还要断言非法角色被拒绝、网络错误不会触发静默登录、学生首页解析正确。

- [ ] **Step 6: 运行会话测试并确认模块缺失失败**

Run: `node --test miniapp/tests/session.test.js`

Expected: FAIL，提示找不到 `../services/session`。

- [ ] **Step 7: 实现最小会话服务**

创建 `miniapp/services/session.js`。实现以下行为：

```js
const auth = require('./auth')
const { selectStoredStudent } = require('../utils/context-selection')

const HOME_BY_ROLE = {
  parent: '/pages/parent/home/index',
  student: '/pages/student/today/index'
}

function isValidRole(role) {
  return Boolean(HOME_BY_ROLE[role])
}

function roleHome(role) {
  return HOME_BY_ROLE[role] || ''
}
```

`restore(role, token, options)` 必须先验证角色；无 Token 时直接 `loginAs`；有 Token 时暂时放入运行时并调用 `me()`；只有 `statusCode === 401` 才调用 `loginAs`。`loginAs` 在调用 `me()` 失败时恢复原运行时 Token。成功提交函数统一更新：

```js
app.globalData.token = token
app.globalData.currentUser = context.user || loginData.user || null
app.globalData.currentRole = role
app.globalData.currentFamily = context.family || null
app.globalData.currentStudent = selectedStudent
app.globalData.currentStudentId = selectedStudent.id || null
```

并通过安全包装后的 `setStorageSync` 写入 `token`、`currentRole`、`currentStudentId`。当没有学生时移除 `currentStudentId`。默认依赖使用 `getApp()`、`wx` 和现有 `auth`，测试通过 `options` 覆盖。

- [ ] **Step 8: 运行会话与请求测试**

Run: `node --test miniapp/tests/request.test.js miniapp/tests/session.test.js`

Expected: 全部 PASS，无未处理 Promise rejection。

- [ ] **Step 9: 提交会话基础设施**

```bash
git add miniapp/services/request.js miniapp/services/session.js miniapp/tests/request.test.js miniapp/tests/session.test.js
git commit -m "记录并恢复小程序角色会话"
```

### Task 2: 登录页自动恢复与首次选择

**Files:**
- Create: `miniapp/tests/login-page-layout.test.js`
- Modify: `miniapp/pages/auth/login/index.js`
- Modify: `miniapp/pages/auth/login/index.wxml`
- Modify: `miniapp/pages/auth/login/index.wxss`

**Interfaces:**
- Consumes: Task 1 的 `session.isValidRole`、`session.restore`、`session.loginAs`、`session.roleHome`。
- Produces: 登录页的 `viewState: 'initializing' | 'selecting' | 'error'`、`retryRestore()`、首次 `doLogin(role)`。

- [ ] **Step 1: 写启动页结构失败测试**

创建 `miniapp/tests/login-page-layout.test.js`，读取登录页源码并断言：

```js
test('login page separates initialization, first choice, and retry states', () => {
  assert.match(markup, /viewState === 'initializing'/)
  assert.match(markup, /viewState === 'selecting'/)
  assert.match(markup, /viewState === 'error'/)
  assert.match(markup, /重试/)
  assert.match(controller, /onLoad\(\)/)
  assert.match(controller, /session\.restore/)
  assert.match(controller, /wx\.reLaunch/)
  assert.doesNotMatch(controller, /wx\.redirectTo/)
})
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `node --test miniapp/tests/login-page-layout.test.js`

Expected: FAIL，当前页面没有 `viewState` 分支且仍使用 `redirectTo`。

- [ ] **Step 3: 实现启动状态控制器**

修改 `index.js`：

- 初始数据为 `{ viewState: 'initializing', loadingRole: '', errorMessage: '' }`。
- `onLoad()` 同步读取 `currentRole`；非法或缺失时切到 `selecting`，否则调用 `restoreSession(role)`。
- `restoreSession(role)` 调用 `session.restore(role, wx.getStorageSync('token'))`，成功后 `wx.reLaunch({ url: result.url })`，失败后设置 `error` 和可读错误文案。
- `retryRestore()` 使用保存角色重新恢复；如果缓存被清除，则回到 `selecting`。
- `doLogin(role)` 调用 `session.loginAs(role)`，成功后 `reLaunch`；失败时保留 `selecting` 并显示 Toast。

- [ ] **Step 4: 实现三态登录视图**

修改 `index.wxml`，品牌区保持不变，操作区按状态互斥：

```xml
<view wx:if="{{viewState === 'initializing'}}" class="session-state">
  <view class="loading-dot"></view>
  <view class="state-title">正在进入你的学习空间</view>
  <view class="muted">正在恢复上次使用的身份</view>
</view>
<view wx:elif="{{viewState === 'selecting'}}" class="card action-card">
  <button class="primary-button" bindtap="loginParent" loading="{{loadingRole === 'parent'}}">我是家长</button>
  <button class="secondary-button" bindtap="loginStudent" loading="{{loadingRole === 'student'}}">我是学生</button>
</view>
<view wx:else class="card retry-card">
  <view class="state-title">暂时无法进入</view>
  <view class="muted">{{errorMessage}}</view>
  <button class="primary-button" bindtap="retryRestore">重试</button>
</view>
```

在 `index.wxss` 添加状态居中、加载点和重试卡片样式，不引入新颜色体系。

- [ ] **Step 5: 运行启动页和会话测试**

Run: `node --test miniapp/tests/login-page-layout.test.js miniapp/tests/session.test.js`

Expected: 全部 PASS。

- [ ] **Step 6: 提交自动恢复入口**

```bash
git add miniapp/pages/auth/login/index.js miniapp/pages/auth/login/index.wxml miniapp/pages/auth/login/index.wxss miniapp/tests/login-page-layout.test.js
git commit -m "自动进入已选择的小程序身份"
```

### Task 3: “我的”页安全切换身份

**Files:**
- Modify: `miniapp/pages/profile/index/index.js`
- Modify: `miniapp/tests/profile-page-layout.test.js`

**Interfaces:**
- Consumes: Task 1 的 `session.loginAs(targetRole): Promise<{url}>`。
- Produces: `switchRole()`、`confirmSwitchRole(targetRole)` 和 `switchingRole` 页面状态。

- [ ] **Step 1: 写角色切换失败测试**

扩展 `profile-page-layout.test.js`，读取控制器并断言：

```js
test('profile role switch requires confirmation and relaunches only after success', () => {
  assert.match(controller, /switchRole\(\)/)
  assert.match(controller, /wx\.showModal/)
  assert.match(controller, /session\.loginAs\(targetRole\)/)
  assert.match(controller, /wx\.reLaunch/)
  assert.match(markup, /bindtap="switchRole"/)
})
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `node --test miniapp/tests/profile-page-layout.test.js`

Expected: FAIL，当前页面没有切换入口或会话服务调用。

- [ ] **Step 3: 实现确认式切换**

在页面控制器引入 `session`，数据中加入 `switchingRole: false`。实现：

```js
switchRole() {
  if (this.data.switchingRole) return
  const targetRole = this.data.user.role === 'parent' ? 'student' : 'parent'
  const targetLabel = targetRole === 'parent' ? '家长' : '学生'
  wx.showModal({
    title: `切换为${targetLabel}身份`,
    content: '切换后将进入对应身份的首页。',
    confirmText: '确认切换',
    success: (res) => {
      if (res.confirm) this.confirmSwitchRole(targetRole)
    }
  })
}
```

`confirmSwitchRole(targetRole)` 设置加载态，调用 `session.loginAs(targetRole)`，成功后 `wx.reLaunch({ url: result.url })`；失败只显示 Toast，`finally` 清除加载态。会话服务保证失败时不覆盖旧状态。

- [ ] **Step 4: 运行切换结构测试**

Run: `node --test miniapp/tests/profile-page-layout.test.js`

Expected: 新增切换用例 PASS。

- [ ] **Step 5: 提交身份切换**

```bash
git add miniapp/pages/profile/index/index.js miniapp/tests/profile-page-layout.test.js
git commit -m "支持在我的页面切换身份"
```

### Task 4: 精简“我的”页为分组列表

**Files:**
- Modify: `miniapp/pages/profile/index/index.js`
- Modify: `miniapp/pages/profile/index/index.wxml`
- Modify: `miniapp/pages/profile/index/index.wxss`
- Modify: `miniapp/tests/profile-page-layout.test.js`
- Modify: `miniapp/utils/profile-visibility.js`
- Modify: `miniapp/tests/profile-visibility.test.js`

**Interfaces:**
- Consumes: `profileVisibility(role, hasFamily)` 和页面现有资料、孩子、邀请、加入家庭方法。
- Produces: `expandedSection`、`toggleSection(e)` 以及 `profile|children|invite|join` 四种展开区域。

- [ ] **Step 1: 写精简布局与可见性失败测试**

更新 `profile-page-layout.test.js`，保留资料字段存在的断言，并新增：

```js
assert.match(markup, /class="identity-panel"/)
assert.match(markup, /切换身份/)
assert.match(markup, /expandedSection === 'profile'/)
assert.match(markup, /expandedSection === 'children'/)
assert.doesNotMatch(markup, /class="stats"/)
assert.doesNotMatch(markup, /guardianCount/)
assert.doesNotMatch(markup, /studentMemberCount/)
```

修正 `profile-visibility.test.js`，明确断言：家长显示 `showInvite` 和 `showChildren`，但不显示 `showJoin`；已入家庭学生都不显示；未入家庭学生只显示 `showJoin`。

- [ ] **Step 2: 运行布局和可见性测试并确认失败**

Run: `node --test miniapp/tests/profile-page-layout.test.js miniapp/tests/profile-visibility.test.js`

Expected: FAIL，旧页面仍有统计卡片且现有可见性测试与实现矛盾。

- [ ] **Step 3: 简化页面状态与加载逻辑**

在 `index.js`：

- 删除 `members`、`guardianCount`、`studentMemberCount` 数据和计算。
- 新增 `expandedSection: ''`。
- 增加 `toggleSection(e)`，相同分组再次点击时收起，否则只展开目标分组。
- `saveProfile()` 成功后设置 `expandedSection: ''` 再刷新上下文。
- `joinFamily()` 成功后设置 `expandedSection: ''`。
- 保留 `students`、`selectedStudentId`、`inviteCode` 和现有 API 行为。

- [ ] **Step 4: 重写分组列表 WXML**

将旧多卡片结构替换为：

1. `identity-panel`：当前角色、家庭名称、绑定档案摘要和切换按钮。
2. `settings-group` 的个人资料行，点击展开现有表单。
3. 家长专属 `settings-group`，包含当前孩子和家庭邀请码两行及对应展开内容。
4. 未入家庭学生专属加入家庭行及展开表单。

每个可点击行使用 `data-section` 和 `bindtap="toggleSection"`。表单内部按钮不得触发父行重复切换；展开内容放在行按钮同级而不是按钮内部。

- [ ] **Step 5: 重写紧凑 WXSS**

删除 `.stats`、`.stat-num`、旧 `.profile-card` 间距规则，新增并统一：

```css
.identity-panel { padding: 30rpx; border-radius: 28rpx; color: #fff; background: linear-gradient(135deg, #286b3e, #67a95c); }
.settings-group { margin-top: 20rpx; overflow: hidden; border: 1rpx solid rgba(73, 132, 78, 0.13); border-radius: 22rpx; background: rgba(255, 255, 255, 0.92); }
.settings-row { width: 100%; min-height: 104rpx; padding: 0 24rpx; justify-content: space-between; background: transparent; border-bottom: 1rpx solid rgba(73, 132, 78, 0.1); text-align: left; }
.settings-detail { padding: 0 24rpx 24rpx; border-top: 1rpx solid rgba(73, 132, 78, 0.08); }
```

补充首字头像、摘要、副标题、箭头和加载态样式；保证 750rpx 宽度下无水平滚动。

- [ ] **Step 6: 运行“我的”页测试**

Run: `node --test miniapp/tests/profile-page-layout.test.js miniapp/tests/profile-visibility.test.js`

Expected: 全部 PASS。

- [ ] **Step 7: 提交页面精简**

```bash
git add miniapp/pages/profile/index/index.js miniapp/pages/profile/index/index.wxml miniapp/pages/profile/index/index.wxss miniapp/tests/profile-page-layout.test.js miniapp/utils/profile-visibility.js miniapp/tests/profile-visibility.test.js
git commit -m "简化小程序我的页面"
```

### Task 5: 全量回归与小程序配置检查

**Files:**
- Modify only if a regression is found: files already listed in Tasks 1-4.

**Interfaces:**
- Consumes: Tasks 1-4 的完整功能。
- Produces: 可在微信开发者工具中构建的最终小程序改动。

- [ ] **Step 1: 运行全部小程序测试**

Run: `node --test miniapp/tests/*.test.js`

Expected: 全部 PASS，现有导航、计划、批改结果和工具测试无回归。

- [ ] **Step 2: 运行相关后端流程测试**

Run: `pytest backend/tests/test_v1_flow.py -q`

Expected: 全部 PASS，登录、家庭加入和资料更新接口保持兼容。

- [ ] **Step 3: 执行静态检查**

Run: `git diff --check && node -e "JSON.parse(require('fs').readFileSync('miniapp/app.json', 'utf8')); JSON.parse(require('fs').readFileSync('miniapp/pages/auth/login/index.json', 'utf8')); JSON.parse(require('fs').readFileSync('miniapp/pages/profile/index/index.json', 'utf8'))"`

Expected: 无输出并以 0 退出。

- [ ] **Step 4: 检查最终差异**

Run: `git status --short && git diff --stat HEAD~3..HEAD`

Expected: 只包含规格、计划、会话服务、登录页、“我的”页及对应测试；不包含 `.env`、数据库或 `.superpowers` 临时文件。

- [ ] **Step 5: 记录微信开发者工具手工验证项**

由于命令行无法模拟完整的小程序生命周期，最终结果中明确列出以下待手工验证项：首次选择后冷启动、缓存 Token 失效恢复、切换确认取消、家长切学生、学生切家长，以及窄屏滚动和安全区表现。
