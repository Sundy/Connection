# Profile And Family Join Flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let both parent and student edit personal profile fields, remove parent-side child creation, and make student family joining happen through family invite code only.

**Architecture:** Add a single backend profile update endpoint under `auth` that updates `User.nickname` for both roles and mirrors `grade` and `school` into the bound `Student` record for student users. Keep the miniapp profile page as the single entry point for profile editing and family actions, while tightening family-join rules so parents cannot join another family and students join via invite code without manually entering student archive IDs.

**Tech Stack:** FastAPI, SQLAlchemy, pytest, WeChat Mini Program JavaScript/WXML/WXSS

---

### Task 1: Backend profile update contract

**Files:**
- Modify: `backend/tests/test_v1_flow.py`
- Modify: `backend/app/schemas/requests.py`
- Modify: `backend/app/api/routers/auth.py`
- Modify: `backend/app/services/auth_service.py`

- [ ] **Step 1: Write the failing test**

```python
def test_student_can_update_profile_fields():
    login = unwrap(client.post("/api/v1/auth/wechat-login", json={
        "code": f"profile-student-{uuid4().hex}",
        "role": "student",
    }))
    headers = {"Authorization": f"Bearer {login['token']}"}

    parent = unwrap(client.post("/api/v1/auth/wechat-login", json={
        "code": f"profile-parent-{uuid4().hex}",
        "role": "parent",
    }))
    parent_headers = {"Authorization": f"Bearer {parent['token']}"}
    invite = unwrap(client.post("/api/v1/families/invite-code", headers=parent_headers))
    unwrap(client.post("/api/v1/families/join", headers=headers, json={"invite_code": invite["invite_code"]}))

    updated = unwrap(client.post("/api/v1/auth/profile", headers=headers, json={
        "nickname": "小明",
        "grade": "三年级",
        "school": "实验小学",
    }))

    assert updated["user"]["nickname"] == "小明"
    assert updated["students"][0]["grade"] == "三年级"
    assert updated["students"][0]["school"] == "实验小学"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest backend/tests/test_v1_flow.py -q -k profile_update`
Expected: FAIL because `/api/v1/auth/profile` does not exist.

- [ ] **Step 3: Write minimal implementation**

```python
class ProfileUpdateIn(BaseModel):
    nickname: str
    grade: str | None = None
    school: str | None = None
```

```python
@router.post("/profile")
def update_profile(payload: ProfileUpdateIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return ok(update_user_profile(db, user, payload))
```

```python
def update_user_profile(db: Session, user: User, payload: ProfileUpdateIn) -> dict:
    user.nickname = payload.nickname.strip()
    ...
    return get_user_context(db, user)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest backend/tests/test_v1_flow.py -q -k profile_update`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/tests/test_v1_flow.py backend/app/schemas/requests.py backend/app/api/routers/auth.py backend/app/services/auth_service.py
git commit -m "实现个人资料更新接口"
```

### Task 2: Family join restriction

**Files:**
- Modify: `backend/tests/test_v1_flow.py`
- Modify: `backend/app/api/routers/families.py`

- [ ] **Step 1: Write the failing test**

```python
def test_parent_cannot_join_another_family_and_student_joins_by_invite_code_only():
    ...
    parent_join = client.post("/api/v1/families/join", headers=second_parent_headers, json={
        "invite_code": invite["invite_code"],
    })
    assert parent_join.status_code == 400

    joined_student = unwrap(client.post("/api/v1/families/join", headers=student_headers, json={
        "invite_code": invite["invite_code"],
    }))
    assert joined_student["family"]["id"] == family_id
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest backend/tests/test_v1_flow.py -q -k family_join`
Expected: FAIL because parents can still join and the old test expects `student_id`.

- [ ] **Step 3: Write minimal implementation**

```python
if user.role == "parent":
    raise HTTPException(status_code=400, detail="Parents should share invite code with students instead")
```

```python
unbound_student = db.query(Student).filter(
    Student.family_id == family.id,
    Student.user_id.is_(None),
).order_by(Student.id).first()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest backend/tests/test_v1_flow.py -q -k family_join`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/tests/test_v1_flow.py backend/app/api/routers/families.py
git commit -m "收紧家庭加入规则"
```

### Task 3: Miniapp profile page behavior

**Files:**
- Modify: `miniapp/pages/profile/index/index.js`
- Modify: `miniapp/pages/profile/index/index.wxml`
- Modify: `miniapp/pages/profile/index/index.wxss`
- Modify: `miniapp/services/auth.js`
- Modify: `miniapp/utils/profile-visibility.js`

- [ ] **Step 1: Write the failing test**

```javascript
assert.doesNotMatch(markup, /添加孩子/)
assert.match(markup, /个人资料/)
assert.match(markup, /学校/)
assert.match(markup, /年级/)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test miniapp/tests/import-plan-layout.test.js`
Expected: FAIL because current profile markup still contains child creation copy and has no profile form coverage.

- [ ] **Step 3: Write minimal implementation**

```javascript
function updateProfile(data) {
  return request({ url: '/auth/profile', method: 'POST', data })
}
```

```javascript
showAddChild: false,
showJoin: !isParent && !hasFamily
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test miniapp/tests/import-plan-layout.test.js`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add miniapp/pages/profile/index/index.js miniapp/pages/profile/index/index.wxml miniapp/pages/profile/index/index.wxss miniapp/services/auth.js miniapp/utils/profile-visibility.js
git commit -m "调整我的页资料与家庭入口"
```

### Task 4: Parent empty-state copy

**Files:**
- Modify: `miniapp/pages/parent/import-home/index.js`
- Modify: `miniapp/pages/parent/import-home/index.wxml`
- Modify: `miniapp/tests/import-plan-layout.test.js`

- [ ] **Step 1: Write the failing test**

```javascript
assert.match(markup, /请让学生先通过家庭码加入家庭/)
assert.doesNotMatch(markup, /去添加孩子/)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test miniapp/tests/import-plan-layout.test.js`
Expected: FAIL because the page still prompts adding children.

- [ ] **Step 3: Write minimal implementation**

```javascript
goProfile() { wx.redirectTo({ url: '/pages/profile/index/index' }) }
```

```xml
<view class="muted">请让学生先在“我的”页输入家庭码加入家庭。</view>
<button class="primary-button" bindtap="goProfile">查看家庭码</button>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test miniapp/tests/import-plan-layout.test.js`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add miniapp/pages/parent/import-home/index.js miniapp/pages/parent/import-home/index.wxml miniapp/tests/import-plan-layout.test.js
git commit -m "调整无学生档案提示"
```
