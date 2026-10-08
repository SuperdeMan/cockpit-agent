// Request-local display cache. The existing RequestRegistry owns frame attribution.
// No changes to messages, playback, confirmation, routing, or persistence.
export class DrivingSpeechView {
  constructor(capacity = 64) {
    this.capacity = capacity
    this.byMessage = new Map()
  }

  observe(frame, registry) {
    if (frame?.type !== 'final' || typeof frame.speech !== 'string') return
    const id = registry.bubbleFor(frame)
    if (!id) return // orphan/expired frame, or unsolicited legacy final without a known message
    this.byMessage.delete(id)
    this.byMessage.set(id, frame.speech)
    while (this.byMessage.size > this.capacity) this.byMessage.delete(this.byMessage.keys().next().value)
  }

  forMessage(message) {
    if (!message || message.rejected) return { text: '', source: 'answer' }
    const speech = !message.pending && !message.streaming && !message.error && this.byMessage.get(message.id)
    return speech ? { text: speech, source: 'speech' } : { text: message.text || '', source: 'answer' }
  }
}
