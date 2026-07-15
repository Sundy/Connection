function getStorage(key, fallback) {
  try {
    const value = wx.getStorageSync(key)
    return value === undefined || value === null || value === '' ? fallback : value
  } catch (err) {
    console.warn(`[storage] read ${key} failed`, err)
    return fallback
  }
}

App({
  onLaunch() {
    this.globalData.token = getStorage('token', '')
    this.globalData.currentRole = getStorage('currentRole', 'parent')
    this.globalData.currentStudentId = getStorage('currentStudentId', null)
    this.globalData.currentPlanId = getStorage('currentPlanId', null)
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
