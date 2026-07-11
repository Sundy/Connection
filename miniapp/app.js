function getStorage(key, fallback, onValue) {
  wx.getStorage({
    key,
    success(res) {
      onValue(res.data || fallback)
    },
    fail() {
      onValue(fallback)
    }
  })
}

App({
  onLaunch() {
    getStorage('token', '', (token) => {
      this.globalData.token = token
    })
    getStorage('currentRole', 'parent', (role) => {
      this.globalData.currentRole = role
    })
    getStorage('currentStudentId', null, (studentId) => {
      this.globalData.currentStudentId = studentId
    })
    getStorage('currentPlanId', null, (planId) => {
      this.globalData.currentPlanId = planId
    })
  },

  globalData: {
    token: '',
    currentUser: null,
    currentRole: 'parent',
    currentFamily: null,
    currentStudent: null,
    currentStudentId: null,
    currentPlanId: null
  }
})
