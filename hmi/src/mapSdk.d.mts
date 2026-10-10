import type {MapModel} from './mapPresentation.mjs'
export type MapView = {fit():void;select(index:number):void;setDriving(value:boolean):void;setModel(model:MapModel):void;setTheme(theme:string):void;destroy():void}
export function loadMapSdk():Promise<unknown>
export function createMapView(sdk:unknown,element:HTMLElement,options:{model:MapModel;theme:string;driving:boolean;onReady:()=>void;onError:(reason:string)=>void;onSelect:(index:number)=>void;padding:()=>number[]}):MapView
