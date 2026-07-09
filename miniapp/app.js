App({
  globalData: {
    token: wx.getStorageSync('token') || '',
    currentUser: null,
    currentRole: wx.getStorageSync('currentRole') || 'parent',
    currentFamily: null,
    currentStudent: null
  }
})
