function profileVisibility(role, hasFamily) {
  const isParent = role === 'parent'
  return {
    isParent,
    showInvite: isParent,
    showChildren: isParent,
    showAddChild: isParent,
    showJoin: isParent || !hasFamily
  }
}

module.exports = { profileVisibility }
