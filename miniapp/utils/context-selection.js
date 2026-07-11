function selectStoredStudent(students, storedId) {
  const list = students || []
  return list.find((student) => student.id === Number(storedId)) || list[0] || {}
}

module.exports = { selectStoredStudent }
