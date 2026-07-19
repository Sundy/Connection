const auth = require('../../../services/auth')
const familyApi = require('../../../services/family')
const session = require('../../../services/session')
const { profileVisibility } = require('../../../utils/profile-visibility')
const { selectStoredStudent } = require('../../../utils/context-selection')
const { buildJoinPayload, parseInviteCode } = require('../../../utils/family-invite')
const { drawQrToCanvas } = require('../../../utils/qr-canvas')

const PREFIXES = ['晨光', '青禾', '星河', '知远', '安宁', '云朵']
const SUFFIXES = ['同学', '小鹿', '小树', '星星', '同伴', '少年']

function randomNickname() {
  const prefix = PREFIXES[Math.floor(Math.random() * PREFIXES.length)]
  const suffix = SUFFIXES[Math.floor(Math.random() * SUFFIXES.length)]
  return `${prefix}${suffix}`
}

function boundStudentForUser(user, students) {
  return (students || []).find((item) => item.user_id === user.id) || students[0] || {}
}

Page({
  data: {
    user: {},
    family: {},
    students: [],
    selectedStudent: {},
    boundStudent: {},
    inviteCode: '',
    inviteQrPayload: '',
    joinInviteCode: '',
    selectedStudentId: null,
    profileNickname: '',
    profileGrade: '',
    profileSchool: '',
    avatarInitial: '我',
    visibility: {},
    expandedSection: '',
    loading: false,
    profileLoading: false,
    switchingRole: false
  },

  onShow() {
    this.loadContext()
  },

  loadContext() {
    auth.me().then((context) => {
      const user = context.user || {}
      const family = context.family || {}
      const students = context.students || []
      const selectedStudent = selectStoredStudent(students, wx.getStorageSync('currentStudentId'))
      const visibility = profileVisibility(user.role, Boolean(family.id))
      const profileStudent = visibility.isParent ? {} : boundStudentForUser(user, students)
      const nickname = user.nickname || randomNickname()

      this.setData({
        user,
        family,
        students,
        selectedStudent,
        boundStudent: profileStudent,
        selectedStudentId: selectedStudent.id || null,
        visibility,
        profileNickname: nickname,
        profileGrade: profileStudent.grade || '',
        profileSchool: profileStudent.school || '',
        avatarInitial: nickname.slice(0, 1) || '我'
      })

      const app = getApp()
      app.globalData.currentUser = user
      app.globalData.currentRole = user.role || app.globalData.currentRole
      app.globalData.currentFamily = family.id ? family : null
      app.globalData.currentStudent = selectedStudent
      app.globalData.currentStudentId = selectedStudent.id || null
      return visibility.showInvite ? familyApi.inviteCode() : null
    }).then((invite) => {
      if (invite) {
        const inviteCode = invite.invite_code || ''
        this.setData({
          inviteCode,
          inviteQrPayload: buildJoinPayload(inviteCode)
        })
        this.renderInviteQr()
      }
    }).catch((err) => {
      wx.showToast({ title: err.detail || '家庭信息加载失败', icon: 'none' })
    })
  },

  renderInviteQr() {
    if (!this.data.inviteQrPayload) return false
    return drawQrToCanvas(wx, 'inviteQrCanvas', this.data.inviteQrPayload, { size: 168 })
  },

  toggleSection(e) {
    const section = e.currentTarget.dataset.section
    const expandedSection = this.data.expandedSection === section ? '' : section
    this.setData({ expandedSection }, () => {
      if (expandedSection === 'invite') this.renderInviteQr()
    })
  },

  selectStudent(e) {
    const studentId = Number(e.currentTarget.dataset.id)
    const student = this.data.students.find((item) => item.id === studentId)
    if (!student) return
    const app = getApp()
    app.globalData.currentStudent = student
    app.globalData.currentStudentId = student.id
    wx.setStorageSync('currentStudentId', student.id)
    this.setData({
      selectedStudent: student,
      selectedStudentId: student.id,
      expandedSection: ''
    })
    wx.showToast({ title: `已选择${student.name}`, icon: 'none' })
  },

  onProfileNicknameInput(e) {
    this.setData({ profileNickname: e.detail.value })
  },

  onProfileGradeInput(e) {
    this.setData({ profileGrade: e.detail.value })
  },

  onProfileSchoolInput(e) {
    this.setData({ profileSchool: e.detail.value })
  },

  fillRandomNickname() {
    this.setData({ profileNickname: randomNickname() })
  },

  saveProfile() {
    const nickname = this.data.profileNickname.trim()
    if (!nickname) {
      wx.showToast({ title: '请输入昵称', icon: 'none' })
      return
    }

    const payload = { nickname }
    if (!this.data.visibility.isParent) {
      payload.grade = this.data.profileGrade.trim()
      payload.school = this.data.profileSchool.trim()
    }

    this.setData({ profileLoading: true })
    auth.updateProfile(payload).then(() => {
      this.setData({ expandedSection: '' })
      wx.showToast({ title: '资料已保存', icon: 'success' })
      this.loadContext()
    }).catch((err) => {
      wx.showToast({ title: err.detail || '保存失败', icon: 'none' })
    }).finally(() => {
      this.setData({ profileLoading: false })
    })
  },

  onJoinCodeInput(e) {
    this.setData({ joinInviteCode: e.detail.value })
  },

  copyInviteCode() {
    if (!this.data.inviteCode) return
    wx.setClipboardData({ data: this.data.inviteCode })
  },

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
  },

  confirmSwitchRole(targetRole) {
    this.setData({ switchingRole: true })
    session.loginAs(targetRole).then((result) => {
      wx.reLaunch({ url: result.url })
    }).catch((err) => {
      wx.showToast({ title: err.detail || err.message || '切换失败', icon: 'none' })
    }).finally(() => {
      this.setData({ switchingRole: false })
    })
  },

  joinFamily() {
    const inviteCode = this.data.joinInviteCode
    if (!inviteCode) {
      wx.showToast({ title: '请输入家庭码', icon: 'none' })
      return
    }

    return this.joinWithInviteCode(inviteCode, '家庭码无效')
  },

  joinWithInviteCode(rawInviteCode, invalidTitle) {
    const inviteCode = parseInviteCode(rawInviteCode)
    if (!inviteCode) {
      wx.showToast({ title: invalidTitle || '未识别到家庭码', icon: 'none' })
      return Promise.resolve(null)
    }
    if (this.data.loading) return Promise.resolve(null)

    this.setData({ loading: true })
    return familyApi.join(inviteCode).then((context) => {
      const app = getApp()
      const joinedStudent = boundStudentForUser(context.user || {}, context.students || [])
      app.globalData.currentFamily = context.family
      app.globalData.currentStudent = joinedStudent
      app.globalData.currentStudentId = joinedStudent.id || null
      if (joinedStudent.id) wx.setStorageSync('currentStudentId', joinedStudent.id)
      this.setData({ joinInviteCode: '', expandedSection: '' })
      wx.showToast({ title: '已加入家庭', icon: 'success' })
      this.loadContext()
    }).catch((err) => {
      wx.showToast({ title: err.detail || '加入失败', icon: 'none' })
    }).finally(() => {
      this.setData({ loading: false })
    })
  },

  scanJoinCode() {
    if (this.data.loading) return
    wx.scanCode({
      onlyFromCamera: false,
      scanType: ['qrCode'],
      success: (res) => {
        const inviteCode = parseInviteCode(res.result)
        if (!inviteCode) {
          wx.showToast({ title: '未识别到家庭码', icon: 'none' })
          return
        }
        this.setData({ joinInviteCode: inviteCode })
        this.joinWithInviteCode(inviteCode)
      },
      fail: (err) => {
        const message = String((err && err.errMsg) || '')
        if (message.toLowerCase().includes('cancel')) return
        wx.showToast({ title: '扫码失败，请手动输入家庭码', icon: 'none' })
      }
    })
  }
})
