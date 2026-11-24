const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

const assets=path.join(__dirname,'../src/sparktutor/web/static');

function petBrowser({width=1440,height=900,sidebarOpen=true,saved={}}={}){
  const nodes=new Map(),frames=[],observers=[];
  let stored=JSON.stringify({layoutVersion:2,size:width<=700?88:112,x:5,y:180,...saved});
  const events=()=>({listeners:{},addEventListener(name,callback){(this.listeners[name]??=[]).push(callback);},
    dispatchEvent(event){for(const callback of this.listeners[event.type]||[])callback(event);}});
  const document=events(),window=Object.assign(events(),{innerWidth:width,innerHeight:height,
    requestAnimationFrame:callback=>frames.push(callback),matchMedia:()=>({matches:true})});
  function element(id=''){
    const classes=new Set();
    return {...events(),id,hidden:false,dataset:{},value:'',children:[],
      style:{setProperty(name,value){this[name]=value;}},
      classList:{contains:name=>classes.has(name),add:name=>classes.add(name),remove:name=>classes.delete(name)},
      setAttribute(){},setPointerCapture(){},focus(){document.activeElement=this;},
      replaceChildren(){this.children=[];},append(child){this.children.push(child);},
      contains(node){return node===this;},querySelector:selector=>nodes.get(selector),
      click(){this.dispatchEvent({type:'click'});},
      get offsetLeft(){return parseFloat(this.style.left)||0;},
      get offsetTop(){return parseFloat(this.style.top)||0;},
      get offsetWidth(){return this.hidden?0:parseFloat(this.style['--pet-width'])||112;},
      get offsetHeight(){return this.hidden?0:150;},
      getBoundingClientRect(){return rect(this.offsetLeft,this.offsetTop,this.offsetWidth,this.offsetHeight);},
    };
  }
  function rect(left,top,width,height){return{left,top,width,height,right:left+width,bottom:top+height};}
  const markup=fs.readFileSync(path.join(assets,'index.html'),'utf8');
  for(const [,id]of markup.slice(markup.indexOf('<div id="spark-pet"')).matchAll(/\bid="([^"]+)"/g))nodes.set(`#${id}`,element(id));
  const pet=nodes.get('#spark-pet'),figure=nodes.get('#spark-pet-figure');
  for(const selector of ['.spark-pet-image','.spark-pet-speech'])nodes.set(selector,element());
  nodes.get('.spark-pet-speech').classList.add('is-quiet');
  for(const id of ['panel','menu','tools','activity-list','show'])nodes.get(`#spark-pet-${id}`).hidden=true;
  const sidebar=element();sidebar.open=sidebarOpen;
  sidebar.getBoundingClientRect=()=>rect(sidebar.open?0:-250,0,250,height);
  nodes.set('.sidebar',sidebar);
  nodes.set('#content',element('content'));
  document.querySelector=selector=>nodes.get(selector)||null;
  document.querySelectorAll=selector=>selector.split(',').includes('.sidebar')?[sidebar]:[];
  document.createElement=()=>element();
  vm.runInNewContext(fs.readFileSync(path.join(assets,'pet.js'),'utf8'),{
    document,window,localStorage:{getItem:()=>stored,setItem:(_,value)=>{stored=value;}},
    setTimeout:()=>0,clearTimeout(){},setInterval:()=>0,
    CustomEvent:class{constructor(type,{detail}={}){this.type=type;this.detail=detail;}},
    MutationObserver:class{constructor(callback){this.callback=callback;}observe(target){observers.push({target,callback:this.callback});}},
  });
  const flush=()=>{let count=0;while(frames.length){assert.ok(++count<30,'Popover positioning must settle.');frames.shift()();}};
  const pointer=(type,x,y)=>{
    const event={type,button:0,pointerId:1,clientX:x,clientY:y,defaultPrevented:false,preventDefault(){this.defaultPrevented=true;}};
    figure.dispatchEvent(event);return event;
  };
  return{nodes,pet,sidebar,document,window,flush,pointer,stored:()=>JSON.parse(stored),
    openSidebar(){sidebar.open=true;for(const observer of observers)if(observer.target===sidebar)observer.callback();},
    mutateContent(){for(const observer of observers)if(observer.target===nodes.get('#content'))observer.callback();},
    recover(){document.dispatchEvent({type:'keydown',key:'P',ctrlKey:true,altKey:true,preventDefault(){}});}};
}

test('a saved position over the visible navigation is restored exactly',()=>{
  const view=petBrowser();view.flush();
  assert.equal(view.pet.offsetLeft,5);assert.equal(view.pet.offsetTop,180);
  assert.equal(view.stored().x,5);assert.equal(view.stored().y,180);
});

test('dragging onto the navigation preserves the chosen position after release',()=>{
  const view=petBrowser({saved:{x:400,y:180}});view.flush();
  const start=view.pet.offsetLeft;
  assert.equal(view.pointer('pointerdown',start,180).defaultPrevented,true,'Native link dragging must not cancel the pet pointer capture.');
  view.pointer('pointermove',5,180);view.flush();
  assert.equal(view.pet.offsetLeft,5);
  view.pointer('pointerup',5,180);view.flush();
  assert.equal(view.pet.offsetLeft,5);assert.equal(view.pet.offsetTop,180);
  assert.equal(view.stored().x,5);assert.equal(view.stored().y,180);
});

test('opening and animating the mobile sidebar does not move the pet',()=>{
  const view=petBrowser({width:390,sidebarOpen:false});view.flush();
  assert.equal(view.pet.offsetLeft,5);
  view.openSidebar();view.flush();
  assert.equal(view.pet.offsetLeft,5);assert.equal(view.pet.offsetTop,180);
  view.sidebar.dispatchEvent({type:'transitionend'});view.flush();
  assert.equal(view.pet.offsetLeft,5);assert.equal(view.pet.offsetTop,180);
});

test('a narrow viewport permits dropping the pet directly over navigation',()=>{
  const view=petBrowser({width:320});view.flush();
  const start=view.pet.offsetLeft;
  view.pointer('pointerdown',start,180);view.pointer('pointermove',20,260);view.flush();
  assert.equal(view.pet.offsetLeft,20);assert.equal(view.pet.offsetTop,260);
  view.pointer('pointerup',20,260);view.flush();
  assert.equal(view.pet.offsetLeft,20);assert.equal(view.pet.offsetTop,260);
  assert.equal(view.stored().x,20);assert.equal(view.stored().y,260);
});

test('scrolling, activity, content changes and opening pet panels do not change the selected position',()=>{
  const view=petBrowser({saved:{x:12,y:210}});view.flush();
  const unchanged=()=>{
    view.flush();
    assert.equal(view.pet.offsetLeft,12);assert.equal(view.pet.offsetTop,210);
    assert.equal(view.stored().x,12);assert.equal(view.stored().y,210);
  };
  view.document.dispatchEvent({type:'scroll',target:view.nodes.get('#content')});unchanged();
  view.window.dispatchEvent({type:'hashchange'});unchanged();
  view.window.dispatchEvent({type:'sparkpet:activity',detail:{status:'ready',label:'Tutor reply available'}});unchanged();
  view.mutateContent();unchanged();
  view.nodes.get('#spark-pet-tools-toggle').click();unchanged();
  view.nodes.get('#spark-pet-actions').click();unchanged();
  view.nodes.get('#spark-pet-chat').click();unchanged();
  assert.equal(view.nodes.get('#spark-pet-panel').hidden,false);
  view.nodes.get('#spark-pet-activity').click();unchanged();
  view.nodes.get('#spark-pet-panel-close').click();unchanged();
});

test('an overflowing chat panel is shifted into the viewport without moving the pet',()=>{
  const view=petBrowser({width:390,height:700,saved:{x:5,y:30}});view.flush();
  const panel=view.nodes.get('#spark-pet-panel');
  panel.getBoundingClientRect=()=>{
    const [x=0,y=0]=String(panel.style.translate||'').split(/\s+/).map(value=>parseFloat(value)||0);
    return{left:-90+x,top:-80+y,width:330,height:300,right:240+x,bottom:220+y};
  };
  view.nodes.get('#spark-pet-chat').click();view.flush();
  const first=panel.getBoundingClientRect();
  assert.ok(first.left>=0&&first.right<=390);assert.ok(first.top>=0&&first.bottom<=700);
  assert.equal(view.pet.offsetLeft,5);assert.equal(view.pet.offsetTop,30);
  view.document.dispatchEvent({type:'scroll',target:view.nodes.get('#content')});view.flush();
  assert.deepEqual(panel.getBoundingClientRect(),first,'Repeated positioning must not accumulate translation.');
  assert.equal(view.pet.offsetLeft,5);assert.equal(view.pet.offsetTop,30);
  assert.equal(view.stored().x,5);assert.equal(view.stored().y,30);
});

test('dragging beyond screen edges and shrinking the viewport still clamp the pet on screen',()=>{
  const view=petBrowser({width:390,height:700,saved:{x:120,y:240}});view.flush();
  view.pointer('pointerdown',120,240);view.pointer('pointermove',-1000,-1000);
  view.pointer('pointerup',-1000,-1000);view.flush();
  assert.equal(view.pet.offsetLeft,0);assert.equal(view.pet.offsetTop,0);
  view.pointer('pointerdown',0,0);view.pointer('pointermove',1000,1000);
  view.pointer('pointerup',1000,1000);view.flush();
  assert.equal(view.pet.offsetLeft,390-view.pet.offsetWidth);
  assert.equal(view.pet.offsetTop,700-view.pet.offsetHeight);
  view.window.innerWidth=320;view.window.innerHeight=400;
  view.window.dispatchEvent({type:'resize'});view.flush();
  assert.equal(view.pet.offsetLeft,320-view.pet.offsetWidth);
  assert.equal(view.pet.offsetTop,400-view.pet.offsetHeight);
  assert.equal(view.stored().x,view.pet.offsetLeft);assert.equal(view.stored().y,view.pet.offsetTop);
});

test('hiding and reopening the pet preserves its position over the sidebar',()=>{
  const view=petBrowser({saved:{x:70,y:240}});view.flush();
  view.nodes.get('#spark-pet-hide').click();view.flush();
  assert.equal(view.pet.hidden,true);assert.equal(view.nodes.get('#spark-pet-show').hidden,false);
  view.nodes.get('#spark-pet-show').click();view.flush();
  assert.equal(view.pet.hidden,false);assert.equal(view.nodes.get('#spark-pet-show').hidden,true);
  assert.equal(view.pet.offsetLeft,70);assert.equal(view.pet.offsetTop,240);
  assert.equal(view.stored().x,70);assert.equal(view.stored().y,240);assert.equal(view.stored().hidden,false);
});

for(const hidden of [false,true])test(`Ctrl+Alt+P recovers a ${hidden?'hidden':'visible'} pet and opens its existing draft`,()=>{
  const view=petBrowser({saved:{hidden}});view.flush();
  const input=view.nodes.get('#spark-pet-question');input.value='Keep this unsent question';
  view.recover();
  assert.equal(view.pet.hidden,false);assert.equal(view.nodes.get('#spark-pet-show').hidden,true);
  assert.equal(view.pet.offsetLeft,1440-view.pet.offsetWidth-12);
  assert.equal(view.pet.offsetTop,900-view.pet.offsetHeight-12);
  assert.equal(view.nodes.get('#spark-pet-panel').hidden,false);
  assert.equal(view.nodes.get('#spark-pet-panel').dataset.section,'chat');
  assert.equal(view.document.activeElement,input);assert.equal(input.value,'Keep this unsent question');
  assert.equal(view.stored().hidden,false);
});
