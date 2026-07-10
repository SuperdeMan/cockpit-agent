// 总览 Live：原可观测台五面板（指令台 / 实时链路 / 车辆状态 / 动态量 / Agent 健康）。
import { AgentList } from '../components/AgentList'
import { CommandBar } from '../components/CommandBar'
import { Dynamics } from '../components/Dynamics'
import { TracePanel } from '../components/TracePanel'
import { VehicleState } from '../components/VehicleState'
import type { AgentInfo, Trace, VehicleState as VehicleStateMap } from '../types'

export function LiveView({
  vehicle,
  changed,
  traces,
  agents,
}: {
  vehicle: VehicleStateMap
  changed: Set<string>
  traces: Trace[]
  agents: Record<string, AgentInfo>
}) {
  return (
    <main className="hud-main">
      <div className="hud-col left">
        <CommandBar />
        <TracePanel traces={traces} />
      </div>
      <div className="hud-col right">
        <VehicleState state={vehicle} changed={changed} />
        <Dynamics state={vehicle} />
        <AgentList agents={agents} />
      </div>
    </main>
  )
}
