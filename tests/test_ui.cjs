const fs=require('node:fs'), vm=require('node:vm'), assert=require('node:assert/strict');
const elements=new Map(), calls=[];
function element(id) {
 if (!elements.has(id)) elements.set(id,{value:'1',checked:true,clientWidth:800,clientHeight:400,width:800,height:400,style:{},classList:{toggle(){}},listeners:{},addEventListener(name,fn){this.listeners[name]=fn},getBoundingClientRect(){return {left:0,top:0,width:800,height:400}},getContext(){return new Proxy({},{get:()=>()=>{}})},replaceChildren(){},append(){},setPointerCapture(){}});
 return elements.get(id);
}
const state={video:{name:'test',width:1920,height:1080,frame_count:5},fps:60,frames:{},suggestions:{'0':[{type:'point',x:100,y:100}],'1':[{type:'point',x:200,y:200}]}};
const context=vm.createContext({console,Math,Number,Object,String,Array,Error,document:{getElementById:element,createElement:()=>element(Math.random()),addEventListener(){},activeElement:{tagName:'BODY'}},window:{},ResizeObserver:class{observe(){}},Image:class{complete=true;naturalWidth=1920;set src(v){queueMicrotask(()=>this.onload())}},fetch:async(route,options)=>{if(!options)return {ok:true,json:async()=>state};const body=JSON.parse(options.body);calls.push(body);return {ok:true,json:async()=>({label:{status:body.status,objects:body.objects}})}},queueMicrotask,confirm:()=>true});
const html=fs.readFileSync('src/video_annotating_interface/static/index.html','utf8');vm.runInContext(html.split('<script>')[1].split('</script>')[0],context);
const tick=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
 await tick();
 assert.equal(vm.runInContext('viewScale*state.video.height<=canvas.height',context),true,'whole frame fits');
 const before=vm.runInContext('coords({clientX:300,clientY:200})',context);
 let prevented=false;element('canvas').listeners.wheel({deltaY:-300,clientX:300,clientY:200,preventDefault(){prevented=true}});
 assert(prevented);const after=vm.runInContext('coords({clientX:300,clientY:200})',context);
 assert(Math.abs(before.x-after.x)<1e-8 && Math.abs(before.y-after.y)<1e-8,'cursor anchor');
 const zoom=vm.runInContext('zoom',context);await vm.runInContext('go(1)',context);await tick();
 assert.equal(calls[0].objects[0].x,100,'unchanged suggestion saved');assert.equal(vm.runInContext('zoom',context),zoom,'zoom retained');
 await vm.runInContext('removeObject(0)',context);await tick();
 assert.equal(calls.at(-1).status,'absent','removal of final suggestion saves absence');
 await vm.runInContext("addObject({type:'point',x:300,y:150})",context);await tick();
 await vm.runInContext('go(2)',context);await tick();
 assert.equal(calls.at(-1).objects.length,1);assert.equal(calls.at(-1).objects[0].x,300,'edited objects replace suggestions');
 console.log('UI behavior checks passed');
})().catch(error=>{console.error(error);process.exitCode=1});
