import { createContext, useContext, useMemo, useState, type ReactNode } from 'react'
import type { UiCard } from './types'
type StageView = { selected: UiCard | null; open: (card: UiCard) => void; close: () => void }
const Context = createContext<StageView | null>(null)
export function StageViewProvider({children}:{children:ReactNode}) {
  const [selected,setSelected] = useState<UiCard|null>(null)
  const value = useMemo(()=>({selected,open:setSelected,close:()=>setSelected(null)}),[selected])
  return <Context.Provider value={value}>{children}</Context.Provider>
}
export function useStageView() { return useContext(Context) }
