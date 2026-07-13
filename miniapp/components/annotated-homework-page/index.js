const { annotationStyle } = require('../../utils/annotation-style')

Component({
  properties: {
    page: { type: Object, value: {} }
  },
  data: {
    imageLoaded: false,
    annotations: [],
    correctText: '',
    incorrectText: '',
    reviewText: ''
  },
  observers: {
    'page.questions': function (questions) {
      const annotations = []
      ;(questions || []).forEach((question) => {
        ;(question.annotations || []).forEach((annotation) => {
          annotations.push(Object.assign({}, annotation, { style: annotationStyle(annotation) }))
        })
      })
      const summary = this.data.page.summary || {}
      const correctNos = summary.correct_question_nos || []
      const incorrectNos = summary.incorrect_question_nos || []
      const reviewNos = summary.review_question_nos || []
      this.setData({
        annotations,
        correctText: correctNos.length ? `第 ${correctNos.join('、')} 题正确` : '',
        incorrectText: incorrectNos.length ? `第 ${incorrectNos.join('、')} 题错误` : '',
        reviewText: reviewNos.length ? `第 ${reviewNos.join('、')} 题待复核` : ''
      })
    }
  },
  methods: {
    onImageLoad() {
      this.setData({ imageLoaded: true })
    },
    onImageError() {
      this.setData({ imageLoaded: false })
      this.triggerEvent('imageretry', { mediaId: this.data.page.media_id })
    }
  }
})
