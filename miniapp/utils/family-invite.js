const JOIN_PREFIX = 'connection://join-family'

function buildJoinPayload(inviteCode) {
  const code = String(inviteCode || '').trim().toUpperCase()
  return code ? `${JOIN_PREFIX}?invite_code=${encodeURIComponent(code)}` : ''
}

function parseInviteCode(rawValue) {
  const value = String(rawValue || '').trim()
  if (!value) return ''
  const directMatch = value.match(/^(FAM-\d{6})$/i)
  if (directMatch) return directMatch[1].toUpperCase()
  const queryMatch = value.match(/[?&]invite_code=([^&]+)/i)
  if (queryMatch) return decodeURIComponent(queryMatch[1]).trim().toUpperCase()
  return ''
}

module.exports = {
  buildJoinPayload,
  parseInviteCode
}
