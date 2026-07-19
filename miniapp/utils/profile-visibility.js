function profileVisibility(role, hasFamily) {
  const isParent = role === 'parent'
  return {
    isParent,
    showInvite: isParent,
    showChildren: isParent,
    showAddChild: false,
    showJoin: !isParent
  }
}

module.exports = { profileVisibility }
