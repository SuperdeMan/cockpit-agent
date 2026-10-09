import type { ReminderItem } from './types'
export function resolveView(card: unknown): 'day' | 'multi'
export function dayLabel(ms:number, nowMs:number):string
export function groupByDay(items:ReminderItem[],nowMs:number,cap?:number):{groups:Array<{label:string;items:ReminderItem[]}>;more:number}
export function timelineWindow(items:ReminderItem[],nowMs:number):{startH:number;endH:number}
export function yForTime(ms:number,startH:number,endH:number,height:number):number
