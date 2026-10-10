export type MapPosition = [number, number]
export type MapEntry = {name:string;label:string;position:MapPosition|null;detail:string}
export type MapModel = {entries:MapEntry[];markers:(MapEntry & {index:number;position:MapPosition})[];path:MapPosition[];cancelled:boolean;route:boolean;hasCoordinates:boolean;missingCoordinates:number;pathMissing:boolean;title:string}
export function mapCoordinate(value:unknown):MapPosition|null
export function projectMap(card:unknown):MapModel
export function mapViewPadding(value:{width:number;height:number;panelRight?:number;headerBottom?:number;summaryHeight?:number;answerHeight?:number;driving?:boolean}):number[]
