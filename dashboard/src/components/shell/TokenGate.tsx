import { useState } from 'react'
import { authorize, changeOperatorToken, type AccessState } from '../../api'
import { Button, Input } from '../ui'
import { CodeBlock, Tag } from '../data'
import { StackBadge } from './TopBar'

const COPY = {
  first: ['需要运维令牌', '可观测台的读写都要运维令牌，有效期不超过 12 小时。在仓库根目录运行下面的命令，粘贴输出：'],
  checking: ['正在连接 collector', '正在验证运维令牌…'],
  invalid: ['令牌无效或已过期', '请重新获取一枚运维令牌。令牌只保存在当前标签页。'],
  'not-configured': ['collector 未配置运维访问', '服务端尚未配置运维访问。更换令牌无法解决，请由维护者检查 collector 配置。'],
  unreachable: ['连不上 collector', '请求没有得到响应。请检查网络连接，以及栈标识是否选对了环境。'],
  authenticated: ['', ''],
}
export function TokenGate({ state }: { state: AccessState }) {
  const [token, setToken] = useState('')
  const canEnter = state === 'first' || state === 'invalid' || state === 'checking'
  return <main className="token-gate"><section className="token-gate__card" aria-labelledby="access-heading">
    <div className="row"><img src="/brand.svg" width="24" height="24" alt="" /><h1 className="grow">可观测台</h1><StackBadge /></div>
    <h2 id="access-heading">{COPY[state][0]}</h2><p>{COPY[state][1]}</p>
    {canEnter ? <form className="stack" onSubmit={e => { e.preventDefault(); void authorize(token).then(ok => { if (ok) setToken('') }) }}>
      <CodeBlock code="python scripts/obs_token.py" lang="命令" />
      <Input icon="key" type="password" aria-label="运维令牌" placeholder="粘贴令牌：obs.v1.…" autoComplete="off" spellCheck={false} value={token} onChange={e => setToken(e.target.value)} disabled={state === 'checking'} />
      <div className="row"><span className="caption grow">只保存在本标签页</span><Button kind="primary" type="submit" disabled={!token.trim() || state === 'checking'}>{state === 'checking' ? '连接中…' : '连接'}</Button></div>
    </form> : <><Tag tone="critical">{state === 'not-configured' ? 'HTTP 503' : '无响应'}</Tag><div className="row"><Button icon="refresh" onClick={() => void authorize()}>重试</Button>{state === 'unreachable' && <Button kind="ghost" onClick={changeOperatorToken}>更换令牌</Button>}</div></>}
  </section></main>
}
