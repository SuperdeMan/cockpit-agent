/** Race an external operation without allowing its late result to revive cancelled work. */
export function captureCancelled(): Error {
  const error = new Error('画面采集已停止，本轮没有发送。')
  error.name = 'AbortError'
  return error
}

export function checkCaptureSignal(signal: AbortSignal): void {
  if (signal.aborted) throw captureCancelled()
}

export function withCaptureSignal<T>(operation: Promise<T>, signal: AbortSignal): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const abort = () => reject(captureCancelled())
    signal.addEventListener('abort', abort, { once: true })
    operation.then(resolve, reject).finally(() => signal.removeEventListener('abort', abort))
    if (signal.aborted) abort()
  })
}
