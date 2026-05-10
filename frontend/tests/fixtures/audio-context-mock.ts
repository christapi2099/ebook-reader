export const AUDIO_CONTEXT_MOCK = `;(function () {
  var mockNodes = []
  window.__mockNodes = mockNodes

  function MockNode() {
    this.playbackRate = { value: 1.0 }
    this.onended = null
    this._scheduledAt = undefined
    this._stopped = false
    mockNodes.push(this)
  }
  MockNode.prototype.connect = function () {}
  MockNode.prototype.start = function (when) { this._scheduledAt = when }
  MockNode.prototype.stop = function () { this._stopped = true }
  MockNode.prototype.fireEnded = function () { if (this.onended) this.onended() }

  function MockAudioContext() {}
  Object.defineProperty(MockAudioContext.prototype, 'currentTime', {
    get: function () { return performance.now() / 1000 }
  })
  Object.defineProperty(MockAudioContext.prototype, 'state', {
    get: function () { return 'running' }
  })
  MockAudioContext.prototype.createBufferSource = function () { return new MockNode() }
  MockAudioContext.prototype.decodeAudioData = function () {
    return Promise.resolve({ duration: 0.1 })
  }
  MockAudioContext.prototype.resume = function () { return Promise.resolve() }
  MockAudioContext.prototype.close = function () { return Promise.resolve() }

  window.AudioContext = MockAudioContext
})()
`
