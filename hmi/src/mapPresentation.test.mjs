import test from 'node:test'
import assert from 'node:assert/strict'
import {mapCoordinate,projectMap,mapViewPadding} from './mapPresentation.mjs'

test('reject absent, coerced, out-of-range and sentinel coordinates',()=>{
  for(const point of [null,{}, {lat:null,lng:120},{lat:'30',lng:120},{lat:30,lng:false},{lat:NaN,lng:120},{lat:91,lng:120},{lat:30,lng:181},{lat:0,lng:0}]) assert.equal(mapCoordinate(point),null)
  assert.deepEqual(mapCoordinate({lat:0,lng:120}),[120,0])
})
test('list markers keep original card numbering when coordinates are missing',()=>{
  const model=projectMap({type:'poi_list',items:[{name:'missing'},{name:'second',lat:30,lng:120},{name:'third',lat:31,lng:121}]})
  assert.deepEqual(model.markers.map(p=>[p.label,p.index,p.position]),[['2',1,[120,30]],['3',2,[121,31]]])
  assert.equal(model.missingCoordinates,1)
  assert.deepEqual(model.path,[])
})
test('route uses server geometry and never interpolates missing waypoint positions',()=>{
  const card={type:'route_plan',origin:'A',destination:'B',path:[[30,120],[31,121],[32,122]],waypoints:[{name:'unknown'}]}
  const before=JSON.stringify(card)
  const model=projectMap(card)
  assert.deepEqual(model.path,[[120,30],[121,31],[122,32]])
  assert.deepEqual(model.markers.map(p=>p.label),['起','终'])
  assert.equal(model.entries[1].position,null)
  assert.equal(model.pathMissing,false)
  assert.equal(JSON.stringify(card),before)
})
test('invalid path is not bridged; coordinates alone do not invent a route',()=>{
  const card={type:'charging_route',destination:'B',path:[[30,120],[null,121],[32,122]],stops:[{name:'station',lat:31,lng:121,at_km:50}]}
  const model=projectMap(card)
  assert.deepEqual(model.path,[])
  assert.equal(model.hasCoordinates,true)
  assert.equal(model.pathMissing,true)
  assert.equal(model.markers.length,1)
})
test('oversized geometry is bounded and preserves the server endpoints',()=>{
  const path=Array.from({length:1000},(_,i)=>[30+i/10000,120+i/10000])
  const model=projectMap({type:'route_plan',path,waypoints:[],destination:'B'})
  assert.equal(model.path.length,400)
  assert.deepEqual(model.path[0],[120,30])
  assert.deepEqual(model.path.at(-1),[path.at(-1)[1],path.at(-1)[0]])
})
test('cancelled navigation clears both geometry and numbered markers',()=>{
  const model=projectMap({type:'route_plan',cancelled:true,origin_loc:{lat:30,lng:120},destination:'B',waypoints:[],path:[[30,120],[31,121]]})
  assert.equal(model.hasCoordinates,false)
  assert.deepEqual([model.entries,model.markers,model.path],[[],[],[]])
})
test('trip displays only grounded coordinates without making up inter-stop paths',()=>{
  const model=projectMap({type:'trip_itinerary',itinerary:[{day_index:1,stops:[{name:'known',grounded:true,poi:{lat:30,lng:120}},{name:'unverified',grounded:false,poi:{lat:31,lng:121}}]}]})
  assert.equal(model.markers.length,1)
  assert.equal(model.missingCoordinates,1)
  assert.deepEqual(model.path,[])
})
test('camera avoids the parked conversation and the driving answer, using SDK order',()=>{
  assert.deepEqual(mapViewPadding({width:1920,height:1080,panelRight:824,headerBottom:180,summaryHeight:300}),[208,364,888,64])
  const drive=mapViewPadding({width:1920,height:1080,panelRight:824,headerBottom:160,answerHeight:240,driving:true})
  assert.equal(drive[1],280)
  assert.equal(drive[2],64)
})
