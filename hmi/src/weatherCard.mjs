export function weatherAlertSummary(alerts) {
  const first = alerts?.[0]
  if (!first) return null
  return {
    headline: `${first.type || '天气'}${first.level ? `${first.level}色` : ''}预警`,
    detail: first.text || first.title || '请注意天气变化。',
    extraCount: Math.max(0, alerts.length - 1),
    publishedAt: String(first.pub_time || '').replace('T', ' ').replace(/\+.*/, ''),
  }
}

export function weatherAlertStatus(alerts, available = true) {
  if (!available) return { tone: 'unavailable', label: '预警服务暂不可用' }
  const summary = weatherAlertSummary(alerts)
  return summary
    ? { tone: 'warning', label: summary.headline }
    : { tone: 'clear', label: '暂无天气预警' }
}
