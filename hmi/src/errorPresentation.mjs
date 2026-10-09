// Only raw gateway errors carry this prefix in the unchanged App handler.
// Locally authored recovery instructions and interrupted partial answers remain visible.
export function messageTextForView(message, showRawErrors = false) {
  const text = message?.text || ''
  if (!message?.error || !text.startsWith('出错了：')) return text
  if (showRawErrors) return text.slice('出错了：'.length)
  return /超时|timeout/i.test(text) ? '这次等待时间较长，请稍后重试。' : '这次请求没有完成，请稍后重试。'
}
