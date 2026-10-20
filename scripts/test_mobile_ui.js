const fs=require('node:fs');
const assert=require('node:assert/strict');
const {JSDOM}=require('jsdom');
const html=fs.readFileSync('torrent-hub/app/static/index.html','utf8');
const cfg={
  version:3,proxy:{mode:'direct',type:'socks5',host:'',port:0,username:'',password_set:false},
  remote:{username:'admin',password_set:false},
  smart:{min_size:5,max_size:10,min_seeders:10,quality:'1080',strict:false,profile:'balanced'},
  search:{include_public:true,cache_minutes:10,cache_max:350},
  direct_trackers:[
   {id:'rutor',kind:'rutor',name:'RuTor',url:'https://rutor.info',route:'inherit',enabled:true},
   {id:'megapeer',kind:'megapeer',name:'MegaPeer',url:'https://megapeer.vip',route:'inherit',enabled:true},
   {id:'nnmclub',kind:'nnmclub',name:'NNM-Club',url:'https://nnmclub.to',route:'inherit',enabled:true}
  ],
  sources:[],servers:[{id:'lg',name:'LG',url:'http://192.168.1.148:8090',username:'',password_set:false}]
};
const requests=[];
function fakeFetch(url,options={}){
 const method=options.method||'GET',path=String(url).replace(/^api\//,'');
 requests.push({path,method,body:options.body});
 const data=options.body?JSON.parse(options.body):{},result={};
 if(path==='config'&&method==='GET')return Promise.resolve({ok:true,json:()=>Promise.resolve(cfg)});
 if(path==='config'&&method==='PUT'){
  for(const [key,value] of Object.entries(data.config)){
   cfg[key]=value;
   if(key==='remote'){cfg.remote.password_set=!!value.password;delete cfg.remote.password;}
  }
  return Promise.resolve({ok:true,json:()=>Promise.resolve(cfg)});
 }
 const responses={
  status:{checks:{},events:[]},storage:{legacy_prowlarr_bytes:0},
  cache:{results:0,bytes_estimate:0,ttl_minutes:10,max_results:350},
  'direct-trackers':{trackers:cfg.direct_trackers}
 };
 return Promise.resolve({ok:true,json:()=>Promise.resolve(responses[path]||{ok:true})});
}
const dom=new JSDOM(html,{
 url:'http://localhost:8765/',runScripts:'dangerously',pretendToBeVisual:true,
 beforeParse(w){
  w.fetch=fakeFetch;w.alert=()=>{};w.confirm=()=>true;w.prompt=()=>null;
  w.HTMLDialogElement.prototype.showModal=function(){this.open=true};
  w.HTMLDialogElement.prototype.close=function(){this.open=false};
 }
});
const d=dom.window.document,w=dom.window,wait=ms=>new Promise(r=>setTimeout(r,ms));
function change(id,value){
 const e=d.getElementById(id);assert.ok(e,'Missing control '+id);
 e.value=value;e.dispatchEvent(new w.Event('input',{bubbles:true}));
}
(async()=>{
 await wait(300);
 assert.equal(d.querySelectorAll('.health-section').length,4);
 assert.equal(d.getElementById('smartMin').value,'5');
 assert.equal(d.getElementById('smartMax').value,'10');
 change('smartMax','11.5');
 assert.equal(d.getElementById('smartMaxLabel').textContent,'11.5 ГБ');
 assert.equal(d.getElementById('smartProfile').value,'custom');
 change('smartMax','10');
 d.getElementById('query').value='Титаник';
 w.eval("rawResults=[{id:'small',title:'Титаник 1080p',size:8*1073741824,seeders:2,date:'2026-10-01',source:'RuTor',tracker:'RuTor'},{id:'better',title:'Титаник 1080p',size:11*1073741824,seeders:100,date:'2026-10-02',source:'NNM-Club',tracker:'NNM-Club'}]");
 d.getElementById('jumpSmart').click();
 assert.equal(d.getElementById('tab-smart').classList.contains('active'),true);
 assert.equal(d.getElementById('smartResults').querySelector('[data-result]').getAttribute('data-result'),'better');
 d.getElementById('smartStrict').checked=true;
 d.getElementById('smartStrict').dispatchEvent(new w.Event('input',{bubbles:true}));
 assert.equal(d.getElementById('smartResults').querySelectorAll('[data-result]').length,0);
 d.getElementById('addCustomTracker').click();
 assert.ok(d.getElementById('customDialog').open);
 change('customName','Test indexer');change('customUrl','https://indexer.example');
 change('customPath','/find?q={query}');change('customRow','tr.result');change('customTitle','a.title');
 d.getElementById('confirmCustom').click();
 assert.equal(cfg.direct_trackers.length,4);
 d.getElementById('saveDirect').click();await wait(50);
 assert.ok(requests.some(x=>x.path==='config'&&x.method==='PUT'));
 d.getElementById('remoteUser').value='operator';d.getElementById('remotePass').value='strongpassword123';
 d.getElementById('saveRemote').click();await wait(50);
 assert.ok(cfg.remote.password_set);
 console.log('PASS: smart ranking, sliders, custom tracker editor, grouped diagnostics, remote login setup');
 dom.window.close();
})().catch(e=>{console.error(e);dom.window.close();process.exitCode=1});
