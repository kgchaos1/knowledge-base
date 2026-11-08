// No dependencies: node frontend/test-ui.cjs. Tests the actual inline application script.
const fs=require('node:fs');
const vm=require('node:vm');
const assert=require('node:assert/strict');
const html=fs.readFileSync(require('node:path').join(__dirname,'index.html'),'utf8');
const source=html.match(/<script>([\s\S]*?)<\/script>/)[1];
new vm.Script(source); // syntax check
assert(!/\b(?:innerHTML|outerHTML|localStorage|sessionStorage)\b/.test(source));
assert(!/<(?:script|link)[^>]+(?:src|href)=["']https?:/i.test(html));
class Node {
 constructor(tagName=''){this.tagName=tagName;this.checked=false;this.textContent='';this.value='';this.hidden=false;this.disabled=false;this.childNodes=[];this.handlers={};this.classList={toggle(){}};this.open=false;}
 get textContent(){return this._text+(this.childNodes||[]).map(node=>node.textContent).join('');}
 set textContent(value){this._text=String(value);this.childNodes=[];}
 append(...children){this.childNodes.push(...children);}
 replaceChildren(...children){this._text='';this.childNodes=children;}
 addEventListener(name,callback){this.handlers[name]=callback;}
 setAttribute(){} reset(){} focus(){} close(){this.open=false;} showModal(){this.open=true;}
}
const nodes=new Map([...html.matchAll(/\bid="([^"]+)"/g)].map(match=>[match[1],new Node()]));
const calls=[];const confirmations=[];let confirmResult=true;let route=null;let response={status:401,ok:false,text:async()=>'{"detail":"Not authenticated"}'};
const timers=new Map();let timerId=0;
class MockFormData {
 constructor(){this.entries=[];}
 append(name,value){this.entries.push([name,value]);}
 get(name){return this.entries.find(entry=>entry[0]===name)?.[1];}
}
const context=vm.createContext({console,URL,URLSearchParams,AbortController,Map,Set,FormData:MockFormData,location:{protocol:'http:',hostname:'example.org'},confirm:value=>{confirmations.push(value);return confirmResult;},
 document:{getElementById:id=>{assert(nodes.has(id),'Unknown element '+id);return nodes.get(id);},createElement:tag=>new Node(tag),addEventListener(){}},
 setTimeout:callback=>{timers.set(++timerId,callback);return timerId;},clearTimeout:id=>timers.delete(id),
 fetch:async(url,options)=>{calls.push({url,options});return route?route(url,options):response;}
});
vm.runInContext(source,context);
const run=script=>vm.runInContext(script,context);
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const originalTests=(async()=>{
 await flush();assert(nodes.get('password').disabled);assert(nodes.get('login-submit').disabled);
 assert.equal(run("location.origin='http://8.133.220.164:8091'; securePasswordContext()"),true);
 assert.equal(run("location.origin='http://8.133.220.164:8092'; securePasswordContext()"),false);
 assert.equal(nodes.get('temporary-http-warning').hidden,false);
 run("location.origin='http://example.org'");
 assert.equal(run("safeURL('javascript:alert(1)')"),null);assert.equal(run("safeURL('data:text/html,bad')"),null);assert.equal(run("safeURL('/relative')"),null);
 assert.equal(run("safeURL('https://example.org/x')"),'https://example.org/x');
 for(const hostname of ['localhost','127.0.0.1','[::1]']){context.location.hostname=hostname;assert(run('securePasswordContext()'));}
 context.location.hostname='evil.localhost.example.org';assert(!run('securePasswordContext()'));context.location.protocol='https:';assert(run('securePasswordContext()'));
 assert(calls.every(call=>call.url.startsWith('/api/')&&call.options.credentials==='same-origin'));
 response={status:200,ok:true,text:async()=>JSON.stringify({total:1,items:[{id:1,title:'<img src=x onerror=alert(1)>',source:'<svg/onload=1>',content:'hello',ai_summary:'<script>unsafe</script>',ai_processed:1,tags:['<b>tag</b>']}]})};
 run('state.authenticated=true');await run('loadNotes()');
 const card=nodes.get('notes-list').childNodes[0];assert.equal(card.childNodes[0].textContent,'<img src=x onerror=alert(1)>');assert.equal(nodes.get('next').disabled,true);
 run("state.search='中文 & 搜索';state.page=2");response.text=async()=>'{"total":250,"items":[{"id":2}]}';await run('loadNotes()');const url=new URL(calls.at(-1).url,'https://test');assert.equal(url.searchParams.get('skip'),'200');assert.equal(url.searchParams.get('limit'),'100');assert.equal(url.searchParams.get('search'),'中文 & 搜索');
 run("renderDetail({id:1,content:'  ',ai_processed:0,source_url:'javascript:alert(1)'})");assert.equal(nodes.get('detail-link').childNodes.length,0);
 assert(run("taskText({id:2,status:'partial',error_message:'<img onerror=1>'}).includes('部分完成')"));assert(run("taskText({id:2,status:'failed',error_message:'<img onerror=1>'}).includes('<img onerror=1>')"));
 response.text=async()=>'{"id":7,"status":"running","success_count":2}';run("state.tab='sources';state.sources=[{id:1,active_task_id:7}]");const generation=run('state.pollGeneration');await run(`pollTasks(${generation},POLL_LIMIT-1)`);assert(nodes.get('sources-status').textContent.includes('上限'));assert.equal(timers.size,0);
 const before=calls.length;run('stopPolling()');await run(`pollTasks(${generation},0)`);assert.equal(calls.length,before);
 // New source options: default off, explicit boolean POST/PATCH, reset and list labels.
 const ok=data=>({status:200,ok:true,text:async()=>JSON.stringify(data)});
 route=url=>ok(url==='/api/sources/'?[]:{});
 run("state.tab='notes';resetSourceForm()");assert.equal(nodes.get('source-auto-ai').checked,false);
 assert(/id="source-auto-ai" type="checkbox"/.test(html));assert(html.includes('第三方 AI 服务，可能产生费用'));
 const submit=()=>nodes.get('source-form').handlers.submit({preventDefault(){}});
 nodes.get('source-url').value='https://www.zhihu.com/collection/123';await submit();
 let saved=calls.filter(call=>call.options.method==='POST'&&call.url==='/api/sources/').at(-1);
 assert.equal(JSON.parse(saved.options.body).auto_ai,false);
 run("editSource({id:5,auto_ai:true,collection_url:'https://www.zhihu.com/collection/123'})");assert.equal(nodes.get('source-auto-ai').checked,true);
 await submit();saved=calls.filter(call=>call.options.method==='PATCH').at(-1);assert.equal(saved.url,'/api/sources/5');assert.equal(JSON.parse(saved.options.body).auto_ai,true);assert.equal(nodes.get('source-auto-ai').checked,false);
 run("editSource({id:5,auto_ai:true,collection_url:'https://www.zhihu.com/collection/123'})");nodes.get('source-auto-ai').checked=false;await submit();assert.equal(JSON.parse(calls.filter(call=>call.options.method==='PATCH').at(-1).options.body).auto_ai,false);
 nodes.get('source-url').value='https://www.zhihu.com/collection/123';nodes.get('source-auto-ai').checked=true;await submit();assert.equal(JSON.parse(calls.filter(call=>call.options.method==='POST'&&call.url==='/api/sources/').at(-1).options.body).auto_ai,true);
 run("state.sources=[{id:1,auto_ai:true},{id:2,auto_ai:false}];renderSources()");
 assert(nodes.get('sources-list').childNodes[0].childNodes[1].textContent.includes('自动 AI：开'));assert(nodes.get('sources-list').childNodes[1].childNodes[1].textContent.includes('自动 AI：关'));
 assert.equal(nodes.get('sources-list').childNodes[0].childNodes[3].childNodes[0].textContent,'同步并补全');assert(html.includes('补全失败会保留已有正文'));
 // Per-source switches: opt-in consent, direct PATCH, opt-out, rollback/reload and task locks.
 let sourceRecord={id:21,name:'Switch',auto_ai:false};
 route=(url,options)=>{if(options.method==='PATCH'){sourceRecord={...sourceRecord,...JSON.parse(options.body)};return ok(sourceRecord);}return ok([sourceRecord]);};
 run('state.tasks.clear()');context.sourceRecord=sourceRecord;run('state.sources=[sourceRecord];renderSources()');
 const sourceSwitch=()=>nodes.get('sources-list').childNodes[0].childNodes.find(node=>node.className==='source-ai-setting').childNodes[0].childNodes[0];
 const toggle=async value=>{const input=sourceSwitch();input.checked=value;await input.handlers.change();};
 let switchCalls=calls.length;confirmResult=false;await toggle(true);assert.equal(calls.length,switchCalls);assert.equal(sourceSwitch().checked,false);
 for(const word of ['第三方','正文','费用','下次同步'])assert(confirmations.at(-1).includes(word));
 confirmResult=true;await toggle(true);assert.equal(sourceSwitch().checked,true);assert.equal(calls.at(-1).options.method,'PATCH');assert.equal(calls.at(-1).url,'/api/sources/21');assert.deepEqual(JSON.parse(calls.at(-1).options.body),{auto_ai:true});
 let releaseSwitch;route=(url,options)=>options.method==='PATCH'?new Promise(resolve=>{releaseSwitch=()=>{sourceRecord={...sourceRecord,...JSON.parse(options.body)};resolve(ok(sourceRecord));};}):ok([sourceRecord]);
 const savingSwitch=toggle(false);await flush();assert(sourceSwitch().disabled);switchCalls=calls.length;await toggle(false);assert.equal(calls.length,switchCalls);releaseSwitch();await savingSwitch;assert.equal(sourceSwitch().checked,false);
 route=(url,options)=>{if(options.method==='PATCH'){sourceRecord={...sourceRecord,...JSON.parse(options.body)};return ok(sourceRecord);}return ok([sourceRecord]);};await toggle(true);
 const offConfirmCount=confirmations.length;await toggle(false);assert.equal(confirmations.length,offConfirmCount);assert.equal(sourceSwitch().checked,false);assert.deepEqual(JSON.parse(calls.at(-1).options.body),{auto_ai:false});
 route=(url,options)=>options.method==='PATCH'?{status:500,ok:false,text:async()=>' {"detail":"toggle failed"}'}:ok([sourceRecord]);
 await toggle(true);assert.equal(sourceSwitch().checked,false);assert.equal(calls.at(-1).url,'/api/sources/');assert(nodes.get('message').textContent.includes('toggle failed'));assert(!nodes.get('message').textContent.includes('已开启'));
 route=(url,options)=>{if(options.method==='PATCH'){sourceRecord={...sourceRecord,auto_ai:true};throw new Error('network uncertainty');}return ok([sourceRecord]);};
 await toggle(true);assert.equal(sourceSwitch().checked,true);assert(nodes.get('message').textContent.includes('network uncertainty'));
 route=()=>({status:500,ok:false,text:async()=>'{}'});await toggle(false);assert.equal(sourceSwitch().checked,true);assert.equal(run('state.busySources.size'),0);
 run('state.sources[0].active_task_id=7;renderSources()');assert(sourceSwitch().disabled);assert(nodes.get('sources-list').childNodes[0].textContent.includes('等待任务结束再修改'));switchCalls=calls.length;await toggle(false);assert.equal(calls.length,switchCalls);assert.equal(sourceSwitch().checked,true);
 run('state.sources[0].active_task_id=null;state.sources[0].last_task_id=7;state.tasks.set(7,{id:7,status:"running"});renderSources()');assert(sourceSwitch().disabled);await toggle(false);assert.equal(calls.length,switchCalls);
 run('state.tasks.clear();state.sources=[]');
 // Scheduled sync defaults, intervals, consent, CRUD, UTC and task locks.
  run('resetSourceForm()');assert.equal(nodes.get('source-sync-enabled').checked,false);assert.equal(nodes.get('source-sync-interval').value,'1440');
  for(const phrase of ['后端服务器运行期间','服务器停止时不会执行','暂停定时同步不取消当前任务'])assert(html.includes(phrase));
  assert.deepEqual(nodes.get('source-sync-interval').childNodes.map(option=>option.value),['60','360','720','1440','10080']);
  route=url=>ok(url==='/api/sources/'?[]:{});confirmResult=true;
  nodes.get('source-url').value='https://www.zhihu.com/collection/123';await submit();
  saved=calls.filter(call=>call.options.method==='POST'&&call.url==='/api/sources/').at(-1);
  assert.equal(JSON.parse(saved.options.body).sync_enabled,false);assert.equal(JSON.parse(saved.options.body).sync_interval_minutes,1440);assert.equal(JSON.parse(saved.options.body).auto_ai,false);
  for(const interval of [60,360,720,1440,10080]){
   nodes.get('source-url').value='https://www.zhihu.com/collection/123';nodes.get('source-sync-enabled').checked=true;nodes.get('source-sync-interval').value=String(interval);await submit();
   saved=calls.filter(call=>call.options.method==='POST'&&call.url==='/api/sources/').at(-1);assert.equal(JSON.parse(saved.options.body).sync_enabled,true);assert.equal(JSON.parse(saved.options.body).sync_interval_minutes,interval);assert.equal(JSON.parse(saved.options.body).auto_ai,false);
  }
  for(const interval of ['59','10081','60.5','invalid','']){nodes.get('source-url').value='https://www.zhihu.com/collection/123';nodes.get('source-sync-interval').value=interval;const count=calls.length;await submit();assert.equal(calls.length,count);}
  run('resetSourceForm()');nodes.get('source-url').value='https://www.zhihu.com/collection/123';nodes.get('source-sync-enabled').checked=true;confirmResult=false;switchCalls=calls.length;await submit();assert.equal(calls.length,switchCalls);
  for(const word of ['采集','费用','服务器停止','不会替你开启 AI','不取消当前任务'])assert(confirmations.at(-1).includes(word));
  confirmResult=true;run("state.sources=[{id:31,sync_enabled:true,sync_interval_minutes:360,auto_ai:false,collection_url:'https://www.zhihu.com/collection/123'}];editSource(state.sources[0])");assert(nodes.get('source-sync-enabled').checked);assert.equal(nodes.get('source-sync-interval').value,'360');
  nodes.get('source-sync-enabled').checked=false;nodes.get('source-sync-interval').value='720';await submit();saved=calls.filter(call=>call.options.method==='PATCH').at(-1);assert.equal(saved.url,'/api/sources/31');assert.equal(JSON.parse(saved.options.body).sync_enabled,false);assert.equal(JSON.parse(saved.options.body).sync_interval_minutes,720);assert.equal(nodes.get('source-sync-enabled').checked,false);assert.equal(nodes.get('source-sync-interval').value,'1440');
  run("editSource({id:31,sync_enabled:true,sync_interval_minutes:120,collection_url:'https://www.zhihu.com/collection/123'})");assert.equal(nodes.get('source-sync-interval').value,'120');run('resetSourceForm()');
  let scheduled={id:31,name:'Scheduled',auto_ai:false,sync_enabled:false,sync_interval_minutes:360,next_sync_at:null};
  route=(url,options)=>{if(options.method==='PATCH'){scheduled={...scheduled,...JSON.parse(options.body),next_sync_at:JSON.parse(options.body).sync_enabled?'2026-11-05T12:00:00Z':null};return ok(scheduled);}return ok([scheduled]);};context.scheduled=scheduled;run('state.sources=[scheduled];renderSources()');
  const scheduleSwitch=()=>nodes.get('sources-list').childNodes[0].childNodes.find(node=>node.className==='source-sync-setting').childNodes[0].childNodes[0];
  const toggleSchedule=async enabled=>{const input=scheduleSwitch();input.checked=enabled;await input.handlers.change();};
  assert(nodes.get('sources-list').textContent.includes('定时同步：关'));assert(nodes.get('sources-list').textContent.includes('6 小时'));assert(nodes.get('sources-list').textContent.includes('未安排（已暂停）'));
  confirmResult=false;switchCalls=calls.length;await toggleSchedule(true);assert.equal(calls.length,switchCalls);assert.equal(scheduleSwitch().checked,false);
  confirmResult=true;await toggleSchedule(true);assert(scheduleSwitch().checked);assert.deepEqual(JSON.parse(calls.at(-1).options.body),{sync_enabled:true,sync_interval_minutes:360});assert(!Object.hasOwn(JSON.parse(calls.at(-1).options.body),'auto_ai'));assert(nodes.get('sources-list').textContent.includes('本地时间'));
  assert.equal(run("syncDate('2026-11-05T12:00:00')"),run("syncDate('2026-11-05T12:00:00Z')"));assert.equal(run("syncDate('garbage')"),'时间未知');
  const beforePauseConfirm=confirmations.length;await toggleSchedule(false);assert.equal(confirmations.length,beforePauseConfirm);assert.equal(scheduleSwitch().checked,false);assert(nodes.get('message').textContent.includes('不会取消当前任务'));
  route=(url,options)=>options.method==='PATCH'?{status:409,ok:false,text:async()=>JSON.stringify({detail:'task active'})}:ok([scheduled]);await toggleSchedule(true);assert.equal(scheduleSwitch().checked,false);assert(nodes.get('message').textContent.includes('task active'));assert.equal(run('state.busySources.size'),0);
  run('editSource(state.sources[0]);state.sources[0].active_task_id=77;renderSources()');assert(scheduleSwitch().disabled);assert(nodes.get('source-sync-enabled').disabled);assert(nodes.get('source-sync-interval').disabled);assert(nodes.get('source-save').disabled);switchCalls=calls.length;await toggleSchedule(true);await submit();assert.equal(calls.length,switchCalls);
  run('state.sources[0].active_task_id=null;state.sources[0].last_task_id=77;state.tasks.set(77,{id:77,status:"running"});renderSources()');assert(scheduleSwitch().disabled);
  run('state.tasks.set(77,{id:77,status:"failed"});renderSources()');assert(!scheduleSwitch().disabled);assert(nodes.get('sources-list').textContent.includes('错误：服务器未提供错误详情'));assert(nodes.get('sources-list').textContent.includes('需要查看并处理'));
  assert(run("taskText({id:77,status:'failed',error_message:'<img onerror=1>'})").includes('错误：<img onerror=1>'));
  run('state.tasks.clear();state.sources=[];resetSourceForm()');
  console.log('PASS: scheduled sync default off/interval options/bounds/create/PATCH/edit/reset; consent/cancel/no AI opt-in; card pause/errors/task locks; UTC next time and failed task action.');
  // Safe limited Markdown and closed/reset original disclosure.
 const descendants=node=>[node,...node.childNodes.flatMap(descendants)];
 const markdown='# Main\n\n## Section\n【核心结论】\nA **bold** and `code`\n\n- first\n- second\n\n1. one\n2. two\n\n<img src="https://evil/track" onerror="alert(1)">\n<script>alert(1)</script>\n[link](javascript:alert(1)) ![image](https://evil/image.png)';
 context.markdown=markdown;switchCalls=calls.length;run('renderDetail({id:77,content:"<b>raw body</b>",ai_summary:markdown,source_url:"https://example.org/original"})');
 const rendered=descendants(nodes.get('detail-summary'));assert.equal(calls.length,switchCalls);
 for(const tag of ['h3','h4','p','ul','ol','li','strong','code'])assert(rendered.some(node=>node.tagName===tag),tag);
 assert(rendered.some(node=>node.tagName==='strong'&&node.textContent==='bold'));assert(rendered.some(node=>node.tagName==='h4'&&node.textContent==='【核心结论】'));
 assert(rendered.every(node=>!['img','a','script','iframe','svg'].includes(node.tagName)));assert(rendered.every(node=>node.src===undefined&&node.href===undefined));
 for(const literal of ['<img src="https://evil/track" onerror="alert(1)">','<script>alert(1)</script>','[link](javascript:alert(1))','![image](https://evil/image.png)'])assert(nodes.get('detail-summary').textContent.includes(literal));
 assert.equal(nodes.get('detail-content').textContent,'<b>raw body</b>');assert.equal(nodes.get('detail-content').childNodes.length,0);
 assert(!/\b(?:innerHTML|outerHTML|insertAdjacentHTML|DOMParser|createContextualFragment|eval)\b|document\.write/.test(source));
 assert(/<details id="original-details"><summary>展开原文与图片<\/summary>/.test(html));assert.equal(nodes.get('original-details').open,false);
 const originalMarkup=html.match(/<details id="original-details">([\s\S]*?)<\/details>/)[1];assert(originalMarkup.includes('id="detail-content"'));assert(originalMarkup.includes('id="detail-images"'));assert(!originalMarkup.includes('id="detail-link"'));
 nodes.get('original-details').open=true;run('renderDetail({id:77,content:"again"})');assert.equal(nodes.get('original-details').open,false);
 route=()=>ok({id:77,content:'reopened'});nodes.get('original-details').open=true;nodes.get('note-dialog').close();await run('showNote(77)');assert.equal(nodes.get('original-details').open,false);assert(nodes.get('note-dialog').open);
 // Completeness, attachment allowlist and text-only hostile values.
 const hash='a'.repeat(64);const attack='<img src=x onerror=alert(1)>';
 for(const ext of ['jpg','jpeg','png','gif','webp'])assert.equal(run(`attachmentURL('${hash}.${ext}')`),`/api/attachments/${hash}.${ext}`);
 for(const path of ['../'+hash+'.png','folder/'+hash+'.jpg',hash+'.svg',hash+'.png?x=1',hash+'.png#x',hash+'.PNG','A'.repeat(64)+'.png','x.png','https://evil/x.png','%2f'+hash+'.png',hash+'.png\n'])assert.equal(run(`attachmentURL(${JSON.stringify(path)})`),null);
 context.testNote={id:8,content:attack,content_status:'full',image_total:3,image_failed:2,warning:attack,ai_summary:attack,ai_processed:true,ai_stale:true,images:[{path:hash+'.png',url:'https://evil/track',alt:attack},{path:'../bad.png',url:'https://evil/bad'},{url:'https://evil/no-path'},null]};
 run('renderDetail(testNote)');assert.equal(nodes.get('detail-content').textContent,attack);assert.equal(nodes.get('detail-warning').textContent,attack);assert.equal(nodes.get('detail-warning').hidden,false);assert(nodes.get('detail-quality').textContent.includes('全文 · 图片总数：3 · 图片失败：2'));
 assert.equal(nodes.get('detail-images').childNodes.length,1);const figure=nodes.get('detail-images').childNodes[0];const image=figure.childNodes[0];assert.equal(image.tagName,'img');assert.equal(image.src,`/api/attachments/${hash}.png`);assert.equal(image.loading,'lazy');assert.equal(image.alt,attack);assert.equal(figure.childNodes[1].textContent,attack);
 assert.equal(nodes.get('detail-ai-status').textContent,'旧 AI 摘要待更新');
 for(const [status,label] of [['excerpt','摘录'],['link','仅链接'],['unknown','未知'],['__proto__','未知']])assert(run(`contentQuality({content_status:${JSON.stringify(status)}})`).includes(label));
 run("renderDetail({id:8,content:'body',ai_processed:true})");assert.equal(nodes.get('detail-images').childNodes.length,0);assert.equal(nodes.get('detail-warning').hidden,true);
 // Card-only actions, cancellation, explicit note identity and cross-refresh locks.
  assert(!nodes.has('process-note'));assert(!nodes.has('delete-note'));
  assert(!/<dialog[\s\S]*?(?:用 AI 整理此笔记|永久删除笔记)[\s\S]*?<\/dialog>/.test(html));
  let note={id:8,title:'test',content:'body',ai_processed:true};let items=[note];
  const listRoute=url=>ok(url.startsWith('/api/notes/?')?{total:items.length,items}:note);
  route=listRoute;run('state.page=0');await run('loadNotes()');
  const buttons=()=>nodes.get('notes-list').childNodes[0].childNodes.at(-1).childNodes;
  const process=()=>buttons()[0].handlers.click();const remove=()=>buttons()[1].handlers.click();
  assert(buttons()[0].textContent.includes('重新整理'));assert.equal(buttons()[1].textContent,'永久删除笔记');
  confirmResult=false;let count=calls.length;await process();assert.equal(calls.length,count);assert(confirmations.at(-1).includes('更新已有 AI 摘要'));assert(confirmations.at(-1).includes('可能产生费用'));
  await remove();assert.equal(calls.length,count);for(const word of ['永久删除','备份','再次同步'])assert(confirmations.at(-1).includes(word));
  confirmResult=true;run("renderDetail({id:99,title:'other',content:'other'})");nodes.get('note-dialog').showModal();
  await process();assert.equal(calls.filter(call=>call.options.method==='POST'&&call.url.includes('/process')).at(-1).url,'/api/notes/8/process?force=true');assert.equal(run('state.note.id'),99);assert.equal(buttons()[0].disabled,false);
  for(const flags of [{ai_stale:true,ai_processed:false},{ai_stale:false,ai_processed:false}]){
   note={...note,...flags};items=[note];await run('loadNotes()');await process();assert.equal(calls.filter(call=>call.options.method==='POST'&&call.url.includes('/process')).at(-1).url,'/api/notes/8/process'+(flags.ai_stale?'?force=true':''));
  }
  run("renderDetail({id:8,content:'body'})");note={...note,ai_processed:true,ai_summary:'updated'};items=[note];await run('loadNotes()');await process();assert.equal(nodes.get('detail-summary').textContent,'updated');
  let release;route=(url,options)=>options.method==='POST'?new Promise(resolve=>{release=()=>resolve(ok({}));}):listRoute(url);
  const pending=process();await flush();await run('loadNotes()');assert(buttons()[0].disabled);assert(buttons()[1].disabled);
  count=calls.length;await process();await remove();assert.equal(calls.length,count);release();await pending;assert.equal(buttons()[0].disabled,false);assert.equal(buttons()[1].disabled,false);
  route=(url,options)=>options.method==='POST'?{status:500,ok:false,text:async()=>JSON.stringify({detail:attack})}:listRoute(url);
  await process();assert.equal(nodes.get('detail-feedback').textContent,attack);assert.equal(run('state.processing.size'),0);assert.equal(buttons()[0].disabled,false);
  route=listRoute;note={...note,content:'  '};items=[note];await run('loadNotes()');count=calls.length;const confirmCount=confirmations.length;await process();assert(buttons()[0].disabled);assert.equal(calls.length,count);assert.equal(confirmations.length,confirmCount);
  note=context.testNote;items=[note];await run('loadNotes()');const updatedCard=nodes.get('notes-list').childNodes[0];assert(updatedCard.childNodes.some(node=>node.textContent==='旧 AI 摘要待更新'));assert(updatedCard.childNodes.some(node=>node.textContent.includes('正文完整度：全文')));assert(updatedCard.childNodes.some(node=>node.textContent==='说明：'+attack));
  run("renderDetail({id:99,content:'other'})");nodes.get('note-dialog').showModal();
  route=(url,options)=>options.method==='DELETE'?new Promise(resolve=>{release=()=>{items=[];resolve(ok({}));};}):listRoute(url);
  const deleting=remove();await flush();await run('loadNotes()');assert(buttons()[0].disabled);assert(buttons()[1].disabled);count=calls.length;await remove();await process();assert.equal(calls.length,count);release();await deleting;assert.equal(run('state.deleting.size'),0);assert.equal(run('state.note.id'),99);assert(nodes.get('note-dialog').open);
  items=[note];route=(url,options)=>{if(options.method==='DELETE'){items=[];return ok({});}return listRoute(url);};await run('loadNotes()');run('renderDetail(testNote)');await remove();assert.equal(nodes.get('note-dialog').open,false);assert.equal(run('state.note'),null);


 route=null;
 // File imports: independent multipart form, safe preview, confirmation and failure state.
  const fileInput=nodes.get('file-import-file'),importStatus=nodes.get('file-import-status');
  const previewFile=()=>nodes.get('file-import-form').handlers.submit({preventDefault(){}});
  const commitFile=()=>nodes.get('file-import-commit').handlers.click();
  const setFile=file=>{fileInput.files=file?[file]:[];fileInput.handlers.change();};
  const validFile={name:'sample.md',size:100};
  let importData={filename:'sample.md',title:attack,chars:45,format:'markdown',images_remote_warning:true,content_hash:'b'.repeat(64)};
  let commitData={status:'imported',note_id:88,title:attack,characters:45};
  const importRoute=url=>ok(url==='/api/file-import/preview'?importData:url==='/api/file-import/commit'?commitData:{total:1,items:[{id:88,title:attack,content:'file body'}]});
  route=importRoute;run('state.authenticated=true');
  assert(/<form id="file-import-form"/.test(html));for(const phrase of ['UTF-8','2 MiB','不支持压缩包','远程图片只是文本，不会备份','全部客户端'])assert(html.includes(phrase));
  for(const file of [null,{name:'bad.zip',size:1},{name:'bad.html',size:1},{name:'large.txt',size:2*1024*1024+1}]){setFile(file);count=calls.length;await previewFile();assert.equal(calls.length,count);assert(nodes.get('file-import-commit').disabled);}
  for(const name of ['sample.MD','sample.markdown','sample.txt']){
   setFile({name,size:2*1024*1024});await previewFile();assert.equal(nodes.get('file-import-title').value,attack);
   const request=calls.at(-1);assert.equal(request.url,'/api/file-import/preview');assert(request.options.body instanceof MockFormData);assert.equal(request.options.body.get('file'),fileInput.files[0]);assert(!Object.hasOwn(request.options.headers,'Content-Type'));assert.equal(request.options.credentials,'same-origin');
  }
  assert(nodes.get('file-import-meta').textContent.includes('字符数：45'));assert.equal(nodes.get('file-import-meta').childNodes.length,0);assert(!nodes.get('file-import-warning').hidden);
  setFile(validFile);assert(nodes.get('file-import-result').hidden);assert(nodes.get('file-import-commit').disabled);
  await previewFile();nodes.get('file-import-title').value='  Edited title  ';nodes.get('file-import-title').handlers.input();
  const importStart=calls.length;await commitFile();
  const committed=calls.slice(importStart).find(call=>call.url==='/api/file-import/commit');assert.equal(committed.options.body.get('title'),'Edited title');assert.equal(committed.options.body.get('file'),validFile);assert.deepEqual(committed.options.body.entries.map(entry=>entry[0]),['file','title']);
  assert(importStatus.textContent.includes('已保存笔记'));assert(importStatus.textContent.includes(attack));assert.equal(importStatus.childNodes.length,0);assert(nodes.get('file-import-result').hidden);assert(nodes.get('file-import-commit').disabled);assert(calls.slice(importStart).every(call=>!call.url.includes('/process')&&!call.url.includes('/sources')));assert.equal(run('state.notes[0].id'),88);
  setFile(validFile);await previewFile();commitData={...commitData,status:'duplicate'};await commitFile();assert(importStatus.textContent.includes('重复内容'));assert(importStatus.textContent.includes('未新增笔记'));
  let releaseImport;route=url=>url==='/api/file-import/preview'?new Promise(resolve=>{releaseImport=()=>resolve(ok(importData));}):importRoute(url);
  setFile(validFile);let importing=previewFile();await flush();assert(fileInput.disabled);assert(nodes.get('file-import-preview').disabled);count=calls.length;await previewFile();await commitFile();assert.equal(calls.length,count);releaseImport();await importing;
  route=url=>url==='/api/file-import/commit'?new Promise(resolve=>{releaseImport=()=>resolve({status:500,ok:false,text:async()=>JSON.stringify({detail:attack})});}):importRoute(url);
  importing=commitFile();await flush();assert(nodes.get('file-import-commit').disabled);assert(nodes.get('file-import-title').disabled);count=calls.length;await commitFile();assert.equal(calls.length,count);releaseImport();await importing;assert(importStatus.textContent.includes('保存失败'));assert(importStatus.textContent.includes(attack));assert(!importStatus.textContent.includes('已保存'));assert(!nodes.get('file-import-commit').disabled);
  fileInput.files=[{name:'other.txt',size:20}];count=calls.length;await commitFile();assert.equal(calls.length,count);assert(importStatus.textContent.includes('重新预览'));assert(nodes.get('file-import-result').hidden);
  route=url=>url==='/api/file-import/preview'?new Promise(resolve=>{releaseImport=()=>resolve(ok(importData));}):importRoute(url);
  setFile(validFile);importing=previewFile();await flush();setFile({name:'new.md',size:3});releaseImport();await importing;assert(nodes.get('file-import-result').hidden);
  route=()=>({status:400,ok:false,text:async()=>JSON.stringify({detail:'UTF-8 required'})});await previewFile();assert(importStatus.textContent.includes('预览失败'));assert(nodes.get('file-import-commit').disabled);
  route=()=>ok({...importData,chars:'bad'});await previewFile();assert(importStatus.textContent.includes('格式异常'));assert(nodes.get('file-import-result').hidden);
  route=importRoute;setFile(validFile);await previewFile();route=()=>ok({...commitData,status:'unexpected'});await commitFile();assert(importStatus.textContent.includes('保存失败'));assert(!importStatus.textContent.includes('已保存笔记'));
  run('resetFileImport(true)');route=null;
  console.log('PASS: file import multipart/validation/boundaries/safe text/title/count; imported/duplicate; no AI/source call; busy/retry/failure/malformed; changed file and stale preview.');
  // Bookmark imports: independent multipart state, safe bounded preview and real result counts.
   const bookmarkInput=nodes.get('bookmark-import-file'),bookmarkStatus=nodes.get('bookmark-import-status');
   const previewBookmarks=()=>nodes.get('bookmark-import-form').handlers.submit({preventDefault(){}});
   const commitBookmarks=()=>nodes.get('bookmark-import-commit').handlers.click();
   const setBookmark=file=>{bookmarkInput.files=file?[file]:[];bookmarkInput.handlers.change();};
   const bookmarkFile={name:'Bookmarks.HTML',size:2*1024*1024};
   const bookmarkItem={title:attack,url:'https://evil.test/<img>',folder:'<script>folder</script>'};
   const bookmarkData={filename:'Bookmarks.HTML',total:24,valid:21,skipped:2,duplicates_in_file:1,items:Array.from({length:20},()=>bookmarkItem),content_hash:'c'.repeat(64)};
   const bookmarkSaved={imported:18,duplicates:3,skipped:2,total:24,note_ids:[201,202]};
   const bookmarkRoute=url=>ok(url==='/api/bookmark-import/preview'?bookmarkData:url==='/api/bookmark-import/commit'?bookmarkSaved:{total:0,items:[]});
   route=bookmarkRoute;confirmResult=true;
   assert(/<form id="bookmark-import-form"/.test(html));assert(/accept=".html,.htm"/.test(html));
   for(const phrase of ['Chrome / Edge','最多 1000 条','不是网页全文','不会自动访问链接、下载图片或调用 AI'])assert(html.includes(phrase));
   for(const file of [null,{name:'a.md',size:1},{name:'a.zip',size:1},{name:'a.html',size:2*1024*1024+1},{name:'a.htm',size:-1}]){setBookmark(file);count=calls.length;await previewBookmarks();assert.equal(calls.length,count);assert(nodes.get('bookmark-import-commit').disabled);}
   setFile(validFile);route=importRoute;await previewFile();const mdPreview=run('fileImportState.preview');
   route=bookmarkRoute;setBookmark(bookmarkFile);await previewBookmarks();assert.equal(run('fileImportState.preview'),mdPreview);
   assert.equal(calls.at(-1).url,'/api/bookmark-import/preview');assert(calls.at(-1).options.body instanceof MockFormData);assert.equal(calls.at(-1).options.body.get('file'),bookmarkFile);assert(!Object.hasOwn(calls.at(-1).options.headers,'Content-Type'));
   for(const phrase of ['总计：24','有效：21','跳过：2','文件内重复：1'])assert(nodes.get('bookmark-import-meta').textContent.includes(phrase));
   assert.equal(nodes.get('bookmark-import-items').childNodes.length,20);assert(nodes.get('bookmark-import-items').textContent.includes(attack));assert(nodes.get('bookmark-import-items').textContent.includes(bookmarkItem.url));
   assert(descendants(nodes.get('bookmark-import-items')).every(node=>!['a','img','script'].includes(node.tagName)&&node.href===undefined&&node.src===undefined));
   confirmResult=false;count=calls.length;await commitBookmarks();assert.equal(calls.length,count);assert(!nodes.get('bookmark-import-result').hidden);
   for(const word of ['全文','不调用 AI','全部客户端','未关联数据源'])assert(confirmations.at(-1).includes(word));
   confirmResult=true;const bookmarkStart=calls.length;await commitBookmarks();const bookmarkRequest=calls.slice(bookmarkStart).find(call=>call.url==='/api/bookmark-import/commit');
   assert.equal(bookmarkRequest.options.body.get('file'),bookmarkFile);assert.equal(bookmarkRequest.options.body.get('content_hash'),bookmarkData.content_hash);
   for(const phrase of ['新增 18 条','重复 3 条','跳过 2 条','总计 24 条'])assert(bookmarkStatus.textContent.includes(phrase));
   assert(calls.slice(bookmarkStart).every(call=>!call.url.includes('/process')&&!call.url.includes('/sources')));assert(nodes.get('bookmark-import-result').hidden);assert(nodes.get('bookmark-import-commit').disabled);
   setBookmark(bookmarkFile);await previewBookmarks();route=url=>url==='/api/bookmark-import/commit'?ok({...bookmarkSaved,imported:0,duplicates:21}):bookmarkRoute(url);await commitBookmarks();assert(bookmarkStatus.textContent.includes('新增 0 条'));assert(bookmarkStatus.textContent.includes('重复 21 条'));
   let releaseBookmark;route=url=>url==='/api/bookmark-import/preview'?new Promise(resolve=>{releaseBookmark=()=>resolve(ok(bookmarkData));}):bookmarkRoute(url);
   setBookmark(bookmarkFile);let bookmarkPending=previewBookmarks();await flush();assert(bookmarkInput.disabled);count=calls.length;await previewBookmarks();await commitBookmarks();assert.equal(calls.length,count);
   nodes.get('bookmark-import-cancel').handlers.click();releaseBookmark();await bookmarkPending;assert(nodes.get('bookmark-import-result').hidden);assert.equal(bookmarkStatus.textContent,'');assert(!bookmarkInput.disabled);
   bookmarkPending=previewBookmarks();await flush();setBookmark({name:'new.htm',size:10});releaseBookmark();await bookmarkPending;assert(nodes.get('bookmark-import-result').hidden);
   route=bookmarkRoute;await previewBookmarks();bookmarkInput.files=[bookmarkFile];count=calls.length;await commitBookmarks();assert.equal(calls.length,count);assert(bookmarkStatus.textContent.includes('重新预览'));
   for(const bad of [{...bookmarkData,items:Array.from({length:21},()=>bookmarkItem)},{...bookmarkData,total:1001},{...bookmarkData,valid:'21'},{...bookmarkData,items:[{title:'bad'}]}]){route=()=>ok(bad);setBookmark(bookmarkFile);await previewBookmarks();assert(bookmarkStatus.textContent.includes('格式异常'));assert(nodes.get('bookmark-import-result').hidden);}
   route=()=>({status:400,ok:false,text:async()=>JSON.stringify({detail:'UTF-8 required; max 1000 bookmarks'})});await previewBookmarks();assert(bookmarkStatus.textContent.includes('UTF-8 required'));assert(nodes.get('bookmark-import-commit').disabled);
   route=bookmarkRoute;await previewBookmarks();route=url=>url==='/api/bookmark-import/commit'?new Promise(resolve=>{releaseBookmark=()=>resolve({status:500,ok:false,text:async()=>JSON.stringify({detail:attack})});}):bookmarkRoute(url);
   bookmarkPending=commitBookmarks();await flush();assert(bookmarkInput.disabled);assert(nodes.get('bookmark-import-commit').disabled);count=calls.length;await commitBookmarks();assert.equal(calls.length,count);releaseBookmark();await bookmarkPending;assert(bookmarkStatus.textContent.includes('导入失败'));assert(bookmarkStatus.textContent.includes(attack));assert(!nodes.get('bookmark-import-commit').disabled);
   route=()=>ok({...bookmarkSaved,imported:'bad'});await commitBookmarks();assert(bookmarkStatus.textContent.includes('格式异常'));assert(!bookmarkStatus.textContent.includes('导入完成'));
   route=url=>url==='/api/bookmark-import/preview'?ok({...bookmarkData,valid:0,items:[]}):bookmarkRoute(url);await previewBookmarks();assert(nodes.get('bookmark-import-commit').disabled);count=calls.length;await commitBookmarks();assert.equal(calls.length,count);
   route=url=>url==='/api/bookmark-import/preview'?new Promise(resolve=>{releaseBookmark=()=>resolve(ok(bookmarkData));}):bookmarkRoute(url);setBookmark(bookmarkFile);bookmarkPending=previewBookmarks();await flush();run('showLogin()');releaseBookmark();await bookmarkPending;assert(nodes.get('bookmark-import-result').hidden);assert.equal(run('bookmarkImportState.preview'),null);assert.equal(run('fileImportState.preview'),null);
   run('state.authenticated=true');route=bookmarkRoute;setBookmark({name:'small.htm',size:1});await previewBookmarks();route=()=>({status:401,ok:false,text:async()=>''});await commitBookmarks();assert.equal(run('state.authenticated'),false);assert(nodes.get('bookmark-import-result').hidden);
   run('state.authenticated=true');run('resetBookmarkImport(true)');route=null;
   console.log('PASS: bookmarks independent multipart state; HTML/HTM 2MiB validation; bounded text-only preview; cancel/confirmation/duplicate/real counts/no AI or datasource; busy/error/retry/malformed/stale/session invalidation.');
   // Batch tests use only mocked DELETE requests.
  assert(/\.detail-header\{position:sticky/.test(html));
  assert(html.indexOf('id="detail-meta"')<html.indexOf('id="detail-summary"'));
  assert(html.indexOf('id="detail-summary"')<html.indexOf('id="detail-content"'));
  assert(html.indexOf('id="detail-summary"')<html.indexOf('id="detail-images"'));
  assert(!/addEventListener\(['"]cancel/.test(source));
  for(const id of ['detail-close','detail-close-bottom']){nodes.get('note-dialog').showModal();nodes.get(id).handlers.click();assert(!nodes.get('note-dialog').open);}
  route=()=>ok({id:8,content:'body'});nodes.get('note-dialog').scrollTop=900;await run('showNote(8)');assert.equal(nodes.get('note-dialog').scrollTop,0);
  items=[{id:11,content:'a'},{id:12,content:'b'},{id:13,content:'c'}];route=listRoute;await run('loadNotes()');
  const selectAll=()=>nodes.get('notes-select-all').handlers.click();const batch=()=>nodes.get('notes-delete-selected').handlers.click();
  const checkbox=nodes.get('notes-list').childNodes[0].childNodes[1].childNodes[0];assert.equal(checkbox.type,'checkbox');checkbox.checked=true;checkbox.handlers.change();assert.equal(run('state.selectedNotes.size'),1);
  selectAll();assert.equal(run('state.selectedNotes.size'),3);nodes.get('notes-clear-selection').handlers.click();assert.equal(run('state.selectedNotes.size'),0);
  selectAll();await run('loadNotes()');assert.equal(run('state.selectedNotes.size'),0);
  run('state.selectedNotes=new Set([11,13,999])');confirmResult=false;count=calls.length;await batch();assert.equal(calls.length,count);
  for(const word of ['2 篇','历史备份不会清除','重新导入','不可恢复'])assert(confirmations.at(-1).includes(word));
  confirmResult=true;const deleted=[];const releases=[];let concurrent=0,maxConcurrent=0;
  route=(url,options)=>{if(options.method!=='DELETE')return listRoute(url);deleted.push(url);concurrent++;maxConcurrent=Math.max(maxConcurrent,concurrent);return new Promise(resolve=>releases.push(()=>{concurrent--;resolve(url.endsWith('/13')?{status:500,ok:false,text:async()=>JSON.stringify({detail:attack})}:ok({}));}));};
  const confirmationStart=confirmations.length;const batching=batch();await flush();assert.deepEqual(deleted,['/api/notes/11']);assert(buttons()[0].disabled);assert(buttons()[1].disabled);
  count=calls.length;await batch();await process();await remove();await run('loadNotes()');assert.equal(calls.length,count);
  const pageBefore=run('state.page');nodes.get('next').handlers.click();nodes.get('search-form').handlers.submit({preventDefault(){}});assert.equal(run('state.page'),pageBefore);
  releases.shift()();await flush();assert.deepEqual(deleted,['/api/notes/11','/api/notes/13']);releases.shift()();await batching;
  assert.equal(maxConcurrent,1);assert.equal(confirmations.length,confirmationStart+1);assert.equal(run('state.batchDeleting'),false);assert.deepEqual(Array.from(run('state.selectedNotes')),[13]);assert.deepEqual(Array.from(run('state.notes.map(n=>n.id)')),[12,13]);
  assert(nodes.get('message').textContent.includes('成功 1 篇，失败 1 篇'));assert(nodes.get('message').textContent.includes(attack));assert.equal(nodes.get('notes-delete-selected').textContent,'批量永久删除（1）');
  count=calls.length;run('state.processing.add(12)');await batch();run('state.processing.clear();state.deleting.add(12)');await batch();run('state.deleting.clear()');assert.equal(calls.length,count);
  route=()=>ok({attachment_cleanup_failed:true});await batch();assert.equal(calls.at(-1).url,'/api/notes/13');assert.equal(run('state.selectedNotes.size'),0);assert(nodes.get('message').textContent.includes('附件清理失败'));
  items=Array.from({length:105},(_,i)=>({id:1000+i,content:'body'}));route=listRoute;await run('loadNotes()');assert.equal(run('state.notes.length'),100);selectAll();run('state.selectedNotes.add(99999)');
  const deleteStart=calls.length;route=()=>ok({});await batch();const bounded=calls.slice(deleteStart);assert.equal(bounded.length,100);assert(bounded.every((call,i)=>call.options.method==='DELETE'&&call.url==='/api/notes/'+(1000+i)));
  route=listRoute;items=[{id:42,content:'body'}];await run('loadNotes()');selectAll();route=()=>({status:500,ok:false,text:async()=>''});await run('loadNotes()');assert.equal(run('state.selectedNotes.size'),0);assert.equal(run('state.notes.length'),0);assert(nodes.get('notes-delete-selected').disabled);
  route=null;
  response={status:401,ok:false,text:async()=>''};await assert.rejects(run("api('/notes/')"),{name:'SessionExpired'});assert(nodes.get('app').hidden);assert.equal(run('state.authenticated'),false);
 console.log('PASS: per-source auto AI on/off/cancel/PATCH/failure rollback and reload/task locks; safe Markdown headings/bold/code/lists/XSS literal HTML and links/no remote requests; original details closed/reset on render and reopen; current-page batch deletion, sticky detail controls, summary order, inline syntax, no CDN/storage/HTML injection sinks, HTTPS password guard, same-origin API, safe links, text-only content, server search/pagination, auto_ai default/create/PATCH/reset/list, sync enrichment copy, completeness/counts/warnings, SHA256 attachment allowlist and lazy local-only images, hostile alt/warning text, stale/processed force confirmation/cancel/retry, empty-body AI guard, task states/poll bound/cancellation, 401 login redirect.');
})().catch(error=>{console.error(error);process.exitCode=1;});
// Backup tests are appended below and run after the existing async suite settles.
originalTests.then(async()=>{try {
 run('state.authenticated=true');
 let running=false;let lastError=null;
 let settings={enabled:true,hour:2,keep:7,last_success:'2026-11-03T18:00:00Z',next_due:'2026-11-04T18:00:00Z'};
 const attack='<img src=x onerror=alert(1)>',hash='a'.repeat(64);
 const ok=data=>({status:200,ok:true,text:async()=>JSON.stringify(data)});
 const backupList=[{id:'backup_01',filename:attack,size:1024,sha256:hash,created_at:'2026-11-03T18:00:00Z',status:'completed',verified:false},{id:'../settings',filename:'invalid',status:'__proto__'}];
 const operationsData={disk:{total:100*1024**3,used:60*1024**3,free:40*1024**3},notes_count:88,attachments:{references_count:5,unique_count:3},failed_imports_count:2,backup:{running:false,last_error:'/private/SECRET_TOKEN <img src=x onerror=1>'},source_scheduler:{enabled_count:2,active_count:1}};
  route=(url,options)=>{if(url==='/api/operations/status')return ok(operationsData);if(url==='/api/backups/settings'){if(options.method==='PATCH')settings={...settings,...JSON.parse(options.body)};return ok(settings);}if(url==='/api/backups/status')return ok({running,last_error:lastError});if(url==='/api/backups/'){if(options.method==='POST'){running=true;return {...ok({status:'pending'}),status:202};}return ok(backupList);}if(url.endsWith('/verify'))return ok({verified:true,notes_count:8,files_count:2});return ok(url==='/api/sources/'?[]:{total:0,items:[]});};
 run("switchTab('backups')");await flush();
 assert(!nodes.get('backups-panel').hidden);assert(nodes.get('notes-panel').hidden);assert(nodes.get('sources-panel').hidden);
 assert.equal(nodes.get('backup-hour').value,'2');assert.equal(nodes.get('backup-keep').value,'7');assert(nodes.get('backup-enabled').checked);assert(!nodes.get('backup-create').disabled);
 const card=nodes.get('backups-list').childNodes[0];assert.equal(card.childNodes[0].textContent,attack);assert.equal(card.childNodes[0].childNodes.length,0);
 const link=card.childNodes.at(-1).childNodes[0];assert.equal(link.tagName,'a');assert.equal(link.href,'/api/backups/backup_01/download');assert.equal(link.download,undefined);assert.equal(link.target,undefined);
 for(const id of ['..','.','../settings','https://evil','a/b','x?y','x#y','a%2Fb','x\n'])assert.equal(run(`backupPath(${JSON.stringify(id)})`),null);
 for(const phrase of ['D:\\PKM备份','本地助手','网页不能直接写入 D 盘','不包含服务器 .env','关闭浏览器后','不代表已配置电脑自动任务','02:00','保留 7 份'])assert(html.includes(phrase),phrase);
 const submit=()=>nodes.get('backup-settings-form').handlers.submit({preventDefault(){}});
 for(const [hour,keep] of [['24','7'],['-1','7'],['2.5','7'],['2','2'],['2','31'],['2','3.5'],['','7']]){nodes.get('backup-hour').value=hour;nodes.get('backup-keep').value=keep;const before=calls.length;await submit();assert.equal(calls.length,before);}
 for(const [hour,keep] of [['0','3'],['23','30']]){nodes.get('backup-hour').value=hour;nodes.get('backup-keep').value=keep;nodes.get('backup-enabled').checked=false;await submit();assert.deepEqual(JSON.parse(calls.filter(c=>c.url==='/api/backups/settings'&&c.options.method==='PATCH').at(-1).options.body),{enabled:false,hour:Number(hour),keep:Number(keep)});}
 await card.childNodes.at(-1).childNodes[1].handlers.click();assert.equal(calls.at(-1).url,'/api/backups/backup_01/verify');assert(nodes.get('message').textContent.includes('8 篇笔记，2 个文件'));
 lastError='SECRET_TOKEN=/private/.env';await run('loadBackups()');assert(!nodes.get('backups-status').textContent.includes('SECRET_TOKEN'));assert(nodes.get('backups-status').textContent.includes('错误'));
 const normalRoute=route;
  // Operations is tab/manual only and never part of backup polling.
  assert(calls.some(call=>call.url==='/api/operations/status'&&call.options.credentials==='same-origin'&&!call.options.method));
  assert.equal(nodes.get('operations-summary').childNodes.length,3);assert(nodes.get('operations-summary').textContent.includes('40.00 GB / 100.00 GB'));assert(nodes.get('operations-summary').textContent.includes('失败任务数：2'));assert(nodes.get('operations-summary').textContent.includes('错误'));
  for(const secret of ['SECRET_TOKEN','/private/','<img','88','references_count','active_count'])assert(!nodes.get('operations-summary').textContent.includes(secret));
  const refreshOperations=()=>nodes.get('operations-refresh').handlers.click();
  let operationsCount=calls.filter(call=>call.url==='/api/operations/status').length;await run('loadBackups()');assert.equal(calls.filter(call=>call.url==='/api/operations/status').length,operationsCount);
  await refreshOperations();assert.equal(calls.filter(call=>call.url==='/api/operations/status').length,operationsCount+1);assert.equal(timers.size,0);
  let releaseOperations;route=(url,options)=>url==='/api/operations/status'?new Promise(resolve=>{releaseOperations=()=>resolve(ok(operationsData));}):normalRoute(url,options);
  const loadingOperations=refreshOperations();await flush();assert(nodes.get('operations-refresh').disabled);operationsCount=calls.length;await refreshOperations();assert.equal(calls.length,operationsCount);releaseOperations();await loadingOperations;assert(!nodes.get('operations-refresh').disabled);
  route=()=>({status:500,ok:false,text:async()=>JSON.stringify({detail:operationsData.backup.last_error})});await refreshOperations();assert.equal(nodes.get('operations-summary').childNodes.length,0);assert.equal(nodes.get('operations-status').textContent,'读取运维状态失败，请点击刷新重试。');assert(!nodes.get('operations-status').textContent.includes('SECRET_TOKEN'));
  route=()=>ok({...operationsData,disk:{free:attack,total:10}});await refreshOperations();assert.equal(nodes.get('operations-summary').childNodes.length,0);assert(nodes.get('operations-status').textContent.includes('失败'));
  route=normalRoute;
  // Rehearsal requires strict success flags, restores a temp copy, never live data.
  const rehearseButton=()=>nodes.get('backups-list').childNodes[0].childNodes.at(-1).childNodes[2];
  let rehearsal={verified:true,notes_count:8,files_count:2,sqlite_sample_read:true,sqlite_restore_verified:true,live_data_modified:false};
  route=(url,options)=>url.endsWith('/rehearse')?ok(rehearsal):normalRoute(url,options);
  const confirmationCount=confirmations.length;await rehearseButton().handlers.click();assert.equal(calls.at(-1).url,'/api/operations/backups/backup_01/rehearse');assert.equal(calls.at(-1).options.method,'POST');assert.equal(confirmations.length,confirmationCount);assert(nodes.get('message').textContent.includes('恢复预演通过'));for(const word of ['临时目录','副本','未覆盖或修改线上数据'])assert(nodes.get('message').textContent.includes(word));
  assert(html.includes('本页不提供一键覆盖恢复'));assert.equal(nodes.get('backups-list').childNodes[1].childNodes.length,3);
  const validRehearsal={...rehearsal};
  for(const change of [{verified:false},{verified:'true'},{sqlite_sample_read:false},{sqlite_restore_verified:false},{live_data_modified:true},{live_data_modified:null},{notes_count:-1},{files_count:'2'},{}]){
   rehearsal=Object.keys(change).length?{...validRehearsal,...change}:{};await rehearseButton().handlers.click();assert(nodes.get('message').textContent.includes('恢复预演未通过'));assert(!nodes.get('message').textContent.includes('恢复预演通过'));assert(!rehearseButton().disabled);
  }
  rehearsal=validRehearsal;
  route=(url,options)=>url.endsWith('/rehearse')?{status:500,ok:false,text:async()=>JSON.stringify({detail:operationsData.backup.last_error})}:normalRoute(url,options);await rehearseButton().handlers.click();assert(nodes.get('message').textContent.includes('恢复预演未通过'));assert(!nodes.get('message').textContent.includes('SECRET_TOKEN'));assert(!nodes.get('message').textContent.includes('<img'));
  let releaseRehearsal;route=(url,options)=>url.endsWith('/rehearse')?new Promise(resolve=>{releaseRehearsal=()=>resolve(ok(validRehearsal));}):normalRoute(url,options);
  const rehearsing=rehearseButton().handlers.click();await flush();await run('loadBackups()');assert(rehearseButton().disabled);let rehearsalCalls=calls.length;await rehearseButton().handlers.click();assert.equal(calls.length,rehearsalCalls);assert(!nodes.get('backups-list').childNodes[0].childNodes.at(-1).childNodes[1].disabled);assert.equal(run('backupState.verifying.size'),0);releaseRehearsal();await rehearsing;assert.equal(run('backupState.rehearsing.size'),0);assert(!rehearseButton().disabled);assert.equal(rehearseButton().textContent,'恢复预演通过');
  await run('loadBackups()');assert(!rehearseButton().disabled);route=normalRoute;
  console.log('PASS: operations GET/tab/manual refresh/no extra polling/busy/error/XSS/GB; rehearsal POST/temp-copy/no confirm/strict booleans/counts/fixed errors/independent busy; verify/download retained.');
 route=(url,options)=>url.endsWith('/verify')?{status:500,ok:false,text:async()=>JSON.stringify({detail:lastError})}:normalRoute(url,options);
 await card.childNodes.at(-1).childNodes[1].handlers.click();assert(!nodes.get('message').textContent.includes('SECRET_TOKEN'));assert(nodes.get('message').textContent.includes('校验未通过'));
 route=normalRoute;
 await nodes.get('backup-create').handlers.click();assert.equal(calls.at(-1).options.method,'POST');assert(nodes.get('backup-create').disabled);assert(nodes.get('message').textContent.includes('尚未完成'));
 const before=calls.length;await nodes.get('backup-create').handlers.click();assert.equal(calls.length,before);
 const generation=run('backupState.generation');run('clearTimeout(backupState.timer);backupState.timer=null');const pollStart=calls.length;
 for(let round=0;round<60;round++){await run(`pollBackups(${generation},${round})`);run('clearTimeout(backupState.timer);backupState.timer=null');}
 assert.equal(calls.length-pollStart,60);assert(calls.slice(pollStart).every(c=>c.url==='/api/backups/status'));assert(nodes.get('backups-status').textContent.includes('60 次'));assert.equal(timers.size,0);
 run('stopBackupPolling()');const stopped=calls.length;await run(`pollBackups(${generation},0)`);assert.equal(calls.length,stopped);
 await run('loadBackups()');assert(timers.size>0);nodes.get('tab-backups').handlers.keydown({key:'ArrowRight',preventDefault(){}});await flush();assert.equal(run('state.tab'),'devices');assert.equal(timers.size,0);
 nodes.get('tab-devices').handlers.keydown({key:'ArrowRight',preventDefault(){}});await flush();assert.equal(run('state.tab'),'notes');
  nodes.get('tab-notes').handlers.keydown({key:'ArrowLeft',preventDefault(){}});await flush();assert.equal(run('state.tab'),'devices');
 nodes.get('tab-backups').handlers.keydown({key:'Home',preventDefault(){}});await flush();assert.equal(run('state.tab'),'notes');
 nodes.get('tab-notes').handlers.keydown({key:'End',preventDefault(){}});await flush();assert.equal(run('state.tab'),'devices');
 running=false;run("state.tab='backups';clearTimeout(backupState.timer);backupState.timer=null");await run('pollBackups(backupState.generation,0)');assert(!nodes.get('backup-create').disabled);assert.equal(timers.size,0);
 route=()=>({status:401,ok:false,text:async()=>''});await run('loadBackups()');assert.equal(nodes.get('backups-list').childNodes.length,0);assert.equal(timers.size,0);assert(nodes.get('backup-save').disabled);
 console.log('PASS: backup contract, settings bounds, same-origin download, safe DOM/errors, verify, 60-round polling, cancellation, tab cycling, session cleanup.');
  // Device contract, in-memory pairing, errors, confirmation and session cleanup.
  run('state.authenticated=true');let devices=[{id:'device/1',name:attack,created_at:'2026-11-04T10:00:00Z',last_seen:null,revoked:false},{id:2,name:'old',last_seen:'2026-11-04T11:00:00Z',revoked:true}];
  const deviceRoute=(url,options)=>{if(url==='/api/devices/')return ok(devices);if(url==='/api/devices/pairing')return ok({code:'<PAIR-123>',expires_at:'2026-11-05T03:00:00Z'});if(url==='/api/devices/device%2F1/revoke'){devices[0].revoked=true;return ok({revoked:true});}return ok({total:0,items:[]});};route=deviceRoute;
  run("switchTab('devices')");await flush();assert(!nodes.get('devices-panel').hidden);for(const name of ['notes','sources','backups'])assert(nodes.get(name+'-panel').hidden);
  let deviceCard=nodes.get('devices-list').childNodes[0];assert.equal(deviceCard.childNodes[0].textContent,attack);assert.equal(deviceCard.childNodes[0].childNodes.length,0);assert(deviceCard.textContent.includes('尚未连接'));assert(nodes.get('devices-list').childNodes[1].textContent.includes('已撤销'));assert(nodes.get('devices-list').childNodes[1].childNodes.at(-1).disabled);
  for(const phrase of ['10 分钟','单次有效','HTTP 会明文传输','刷新即消失'])assert(html.includes(phrase));
  const pairing=()=>nodes.get('device-pairing-form').handlers.submit({preventDefault(){}});nodes.get('device-name').value='  ';let deviceCalls=calls.length;await pairing();assert.equal(calls.length,deviceCalls);
  nodes.get('device-name').value='  Desktop  ';await pairing();assert.deepEqual(JSON.parse(calls.at(-1).options.body),{name:'Desktop'});assert.equal(calls.at(-1).url,'/api/devices/pairing');assert.equal(calls.at(-1).options.method,'POST');assert.equal(nodes.get('pairing-code').textContent,'<PAIR-123>');assert.equal(nodes.get('pairing-code').childNodes.length,0);assert(!nodes.get('pairing-result').hidden);assert(nodes.get('message').textContent.includes('尚未代表设备已连接'));
  route=()=>({status:500,ok:false,text:async()=>JSON.stringify({detail:'pairing failed'})});await pairing();assert(nodes.get('pairing-result').hidden);assert.equal(nodes.get('pairing-code').textContent,'');assert(nodes.get('message').textContent.includes('pairing failed'));assert(!nodes.get('device-pair').disabled);
  route=()=>ok({});await pairing();assert(nodes.get('pairing-result').hidden);assert(nodes.get('message').textContent.includes('未返回有效配对码'));
  route=deviceRoute;await pairing();confirmResult=false;deviceCalls=calls.length;await run('revokeDevice(deviceState.devices[0])');assert.equal(calls.length,deviceCalls);assert(confirmations.at(-1).includes('凭证将失效'));
  confirmResult=true;route=(url,options)=>url.endsWith('/revoke')?ok({revoked:false}):deviceRoute(url,options);await run('revokeDevice(deviceState.devices[0])');assert(nodes.get('message').textContent.includes('未确认撤销'));assert(!nodes.get('devices-list').childNodes[0].childNodes.at(-1).disabled);
  route=deviceRoute;await run('revokeDevice(deviceState.devices[0])');assert(calls.some(call=>call.url==='/api/devices/device%2F1/revoke'&&call.options.method==='POST'));assert(nodes.get('message').textContent.includes('设备已撤销'));assert(nodes.get('devices-list').childNodes[0].childNodes.at(-1).disabled);
  route=()=>({status:500,ok:false,text:async()=>'{"detail":"list failed"}'});await run('loadDevices()');assert.equal(nodes.get('devices-list').childNodes.length,0);assert(nodes.get('devices-status').textContent.includes('失败'));
  route=deviceRoute;await pairing();assert(!nodes.get('pairing-result').hidden);route=()=>({status:401,ok:false,text:async()=>''});await run('loadDevices()');assert(nodes.get('pairing-result').hidden);assert.equal(nodes.get('pairing-code').textContent,'');assert.equal(nodes.get('devices-list').childNodes.length,0);
  console.log('PASS: devices list/XSS/status, pairing API/name/expiry/memory-only/error clearing, revoke confirmation/encoded identity/response validation, four-tab keyboard wrap/hidden and session cleanup.');
 }catch(error){console.error(error);process.exitCode=1;}});