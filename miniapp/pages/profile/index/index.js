const auth = require('../../../services/auth')
const familyApi = require('../../../services/family')
const { profileVisibility } = require('../../../utils/profile-visibility')
const { selectStoredStudent } = require('../../../utils/context-selection')

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
    members: [],
    boundStudent: {},
    inviteCode: '',
    joinInviteCode: '',
    guardianCount: 0,
    studentMemberCount: 0,
    selectedStudentId: null,
    profileNickname: '',
    profileGrade: '',
    profileSchool: '',
    visibility: {},
    loading: false,
    profileLoading: false
  },

  onShow() {
    this.loadContext()
  },

  loadContext() {
    auth.me().then((context) => {
      const user = context.user || {}
      const members = context.members || []
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
        members,
        boundStudent: profileStudent,
        selectedStudentId: selectedStudent.id || null,
        visibility,
        guardianCount: members.filter((member) => member.relation === 'guardian').length,
        studentMemberCount: members.filter((member) => member.relation === 'student').length,
        profileNickname: nickname,
        profileGrade: profileStudent.grade || '',
        profileSchool: profileStudent.school || ''
      })

      const app = getApp()
      app.globalData.currentStudent = selectedStudent
      app.globalData.currentStudentId = selectedStudent.id || null
      return visibility.showInvite ? familyApi.inviteCode() : null
    }).then((invite) => {
      if (invite) this.setData({ inviteCode: invite.invite_code || '' })
    }).catch(() => {
      wx.showToast({ title: '家庭信息加载失败', icon: 'none' })
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
    this.setData({ selectedStudentId: student.id })
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

  joinFamily() {
    const inviteCode = this.data.joinInviteCode.trim()
    if (!inviteCode) {
      wx.showToast({ title: '请输入家庭码', icon: 'none' })
      return
    }

    this.setData({ loading: true })
    familyApi.join(inviteCode).then((context) => {
      const app = getApp()
      const joinedStudent = boundStudentForUser(context.user || {}, context.students || [])
      app.globalData.currentFamily = context.family
      app.globalData.currentStudent = joinedStudent
      app.globalData.currentStudentId = joinedStudent.id || null
      if (joinedStudent.id) wx.setStorageSync('currentStudentId', joinedStudent.id)
      this.setData({ joinInviteCode: '' })
      wx.showToast({ title: '已加入家庭', icon: 'success' })
      this.loadContext()
    }).catch((err) => {
      wx.showToast({ title: err.detail || '加入失败', icon: 'none' })
    }).finally(() => {
      this.setData({ loading: false })
    })
  }
})
