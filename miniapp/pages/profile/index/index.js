const auth = require('../../../services/auth')
const familyApi = require('../../../services/family')
const studentApi = require('../../../services/student')
const { profileVisibility } = require('../../../utils/profile-visibility')
const { selectStoredStudent } = require('../../../utils/context-selection')

Page({
  data: {
    user: {},
    family: {},
    students: [],
    members: [],
    inviteCode: '',
    joinInviteCode: '',
    joinStudentId: '',
    childName: '',
    childGrade: '',
    childSchool: '',
    guardianCount: 0,
    studentMemberCount: 0,
    selectedStudentId: null,
    visibility: {},
    loading: false
  },

  onShow() {
    this.loadContext()
  },

  loadContext() {
    auth.me().then((context) => {
      const members = context.members || []
      const family = context.family || {}
      const selectedStudent = selectStoredStudent(context.students, wx.getStorageSync('currentStudentId'))
      const visibility = profileVisibility((context.user || {}).role, Boolean(family.id))
      this.setData({
        user: context.user || {},
        family,
        students: context.students || [],
        members,
        selectedStudentId: selectedStudent.id || null,
        visibility,
        guardianCount: members.filter((member) => member.relation === 'guardian').length,
        studentMemberCount: members.filter((member) => member.relation === 'student').length
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

  onJoinCodeInput(e) {
    this.setData({ joinInviteCode: e.detail.value })
  },

  onJoinStudentInput(e) {
    this.setData({ joinStudentId: e.detail.value })
  },

  onChildNameInput(e) {
    this.setData({ childName: e.detail.value })
  },

  onChildGradeInput(e) {
    this.setData({ childGrade: e.detail.value })
  },

  onChildSchoolInput(e) {
    this.setData({ childSchool: e.detail.value })
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
    familyApi.join(inviteCode, this.data.joinStudentId).then((context) => {
      const app = getApp()
      app.globalData.currentFamily = context.family
      app.globalData.currentStudent = context.students && context.students[0]
      this.setData({ joinInviteCode: '', joinStudentId: '' })
      wx.showToast({ title: '已加入家庭', icon: 'success' })
      this.loadContext()
    }).catch((err) => {
      wx.showToast({ title: err.detail || '加入失败', icon: 'none' })
    }).finally(() => {
      this.setData({ loading: false })
    })
  },

  addChild() {
    const name = this.data.childName.trim()
    if (!name) {
      wx.showToast({ title: '请输入小朋友姓名', icon: 'none' })
      return
    }

    this.setData({ loading: true })
    studentApi.create({
      name,
      grade: this.data.childGrade.trim(),
      school: this.data.childSchool.trim() || null
    }).then(() => {
      this.setData({ childName: '', childGrade: '', childSchool: '' })
      wx.showToast({ title: '已添加小朋友', icon: 'success' })
      this.loadContext()
    }).catch((err) => {
      wx.showToast({ title: err.detail || '添加失败', icon: 'none' })
    }).finally(() => {
      this.setData({ loading: false })
    })
  }
})
