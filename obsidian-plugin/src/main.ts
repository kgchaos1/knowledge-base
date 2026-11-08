import { Plugin, PluginSettingTab, Setting, Notice, Modal, App, requestUrl, normalizePath } from 'obsidian';
import { runSync, VaultAdapter, Transport } from './core';

type Config={server:string;root:string;testMode:boolean;auto:boolean;minutes:number;scope:'all'|'sources';sourceIds:number[];secretId:string};
const defaults:Config={server:'http://8.133.220.164:8091',root:'PKM',testMode:true,auto:false,minutes:15,scope:'all',sourceIds:[],secretId:''};
function origin(text:string):string { const u=new URL(text.trim()); if(!['https:','http:'].includes(u.protocol)||u.username||u.password||!u.hostname||u.pathname!=='/'||u.search||u.hash)throw new Error('请输入不含路径的服务器地址');return u.origin; }
class Confirm extends Modal {
 constructor(app:App,private message:string,private resolve:(value:boolean)=>void){super(app);}
 onOpen(){this.contentEl.createEl('p',{text:this.message});new Setting(this.contentEl).addButton(b=>b.setButtonText('取消').onClick(()=>this.close())).addButton(b=>b.setButtonText('确认').setCta().onClick(()=>{this.resolve(true);this.resolve=()=>{};this.close();}));}
 onClose(){this.resolve(false);this.contentEl.empty();}
}
export default class PKMPlugin extends Plugin {
 config:Config={...defaults};token='';approved='';busy=false;timer:number|null=null;status='尚未同步';sources:Array<{id:number;name:string;platform:string}>=[];
 async onload(){const saved=await this.loadData();this.config={...defaults,server:typeof saved?.server==='string'?saved.server:defaults.server,root:typeof saved?.root==='string'?saved.root:defaults.root,testMode:typeof saved?.testMode==='boolean'?saved.testMode:defaults.testMode,auto:saved?.auto===true,minutes:Number(saved?.minutes)||15,scope:saved?.scope==='sources'?'sources':'all',sourceIds:[],secretId:typeof saved?.secretId==='string'?saved.secretId:''};this.config.server=origin(this.config.server);this.config.sourceIds=Array.isArray(saved?.sourceIds)?saved.sourceIds.filter((x:unknown)=>Number.isSafeInteger(x)&&Number(x)>0):[];
 if(![5,15,30,60].includes(this.config.minutes))this.config.minutes=15;
 if(!this.config.testMode)this.loadToken();
 this.addSettingTab(new PKMSettings(this.app,this));this.addCommand({id:'sync',name:'同步笔记',callback:()=>void this.sync(true)});this.addRibbonIcon('refresh-cw','PKM 同步',()=>void this.sync(true));
 this.registerDomEvent(document,'visibilitychange',()=>this.arm());this.arm();}
 loadToken(){if(!this.config.secretId)return;try{this.token=this.app.secretStorage.getSecret(this.config.secretId)||'';}catch{this.token='';this.status='本机凭证无法读取，请关闭测试模式后重新配对';}}
  onunload(){this.stop();this.token='';this.approved='';}
 async save(){const c=this.config;await this.saveData({server:c.server,root:c.root,testMode:c.testMode,auto:c.auto,minutes:c.minutes,scope:c.scope,sourceIds:c.sourceIds,secretId:c.secretId});this.arm();}
 stop(){if(this.timer!==null){window.clearTimeout(this.timer);this.timer=null;}}
 arm(){this.stop();if(!this.config.auto||document.hidden||(!this.config.testMode&&!this.token)||this.busy||(!this.config.server.startsWith('https:')&&this.approved!==this.config.server))return;
 this.timer=window.setTimeout(()=>{this.timer=null;void this.sync(false);},this.config.minutes*60000);}
 async consent(){if(this.config.server.startsWith('https:')||this.approved===this.config.server)return true;const ok=await new Promise<boolean>(resolve=>new Confirm(this.app,'HTTP未加密，配对码、只读凭证和笔记可能被截获。仅用于公开内容内测，是否继续？',resolve).open());if(ok)this.approved=this.config.server;return ok;}
 async call(method:string,path:string,body?:unknown,claim=false){
 if(!path.startsWith('/api/')||path.includes('://'))throw new Error('非法请求路径');
 const headers:Record<string,string>={'Origin':this.config.server};if(!claim&&!this.config.testMode){if(!this.token)throw new Error('请先配对');headers.Authorization='Bearer '+this.token;}
 const response=await requestUrl({url:this.config.server+path,method,headers,body:body===undefined?undefined:JSON.stringify(body),contentType:body===undefined?undefined:'application/json',throw:false});
 if(response.status===401&&!claim&&this.config.testMode)throw new Error('服务器未启用免配对测试模式（HTTP 401）。请由管理员启用服务器测试模式，或关闭本插件的测试模式后配对。');
  if(response.status===401&&!claim){this.token='';if(this.config.secretId)this.app.secretStorage.setSecret(this.config.secretId,'');this.stop();throw new Error('设备已撤销或凭证失效，请重新配对');}
 if(response.status<200||response.status>=300){const error=new Error('请求失败（HTTP '+response.status+'）') as Error & {status:number};error.status=response.status;throw error;}return response;}
 async pair(code:string,name:string){if(this.busy)return;this.busy=true;this.stop();try{if(!await this.consent())return;const data=(await this.call('POST','/api/devices/claim',{code:code.trim(),name:name.trim()||'Obsidian设备'},true)).json;
 if(typeof data?.token!=='string'||! /^[A-Za-z0-9_-]{43}$/.test(data.token))throw new Error('配对响应无效');this.token=data.token;
 const digest=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(this.config.server));const hash=Array.from(new Uint8Array(digest)).map(x=>x.toString(16).padStart(2,'0')).join('');this.config.secretId='pkm-'+hash.slice(0,56);
 try { this.app.secretStorage.setSecret(this.config.secretId,this.token);if(this.app.secretStorage.getSecret(this.config.secretId)!==this.token)throw new Error('storage readback');await this.save();new Notice('配对成功；只读凭证保存到Obsidian SecretStorage');this.status='已配对，点击同步'; }
 catch {this.config.secretId='';await this.save();this.status='服务器配对成功，但本机安全存储失败。当前会话可同步；不要重复使用旧配对码。重启后请生成新码重新配对。';new Notice(this.status);}
 }catch(e){new Notice(e instanceof Error?e.message:'配对失败');}finally{this.busy=false;this.arm();}}
 async disconnect(){if(this.busy)return;this.token='';this.approved='';this.stop();if(this.config.secretId)this.app.secretStorage.setSecret(this.config.secretId,'');this.config.secretId='';await this.save();this.status='已清除本机凭证，已有笔记保留';new Notice(this.status);}
 adapter():VaultAdapter {const a=this.app.vault.adapter;return {read:p=>a.read(normalizePath(p)),write:(p,v)=>a.write(normalizePath(p),v),exists:p=>a.exists(normalizePath(p)),mkdir:p=>a.mkdir(normalizePath(p)),writeBinary:(p,v)=>a.writeBinary(normalizePath(p),v),readBinary:p=>a.readBinary(normalizePath(p)),list:p=>a.list(normalizePath(p)),writeAtomic:async(p,v)=>{const tmp=p+'.pkm-tmp';if(await a.exists(tmp))throw new Error('上次临时文件仍存在，请检查后重试');await a.write(tmp,v);try{await a.rename(tmp,p);}catch(e){if(await a.exists(tmp))await a.remove(tmp);throw e;}}};}
 async sync(manual:boolean){if(this.busy)return;if(!this.config.testMode&&!this.token){if(manual)new Notice('请在设置中配对');return;}this.busy=true;this.stop();try{if(document.hidden&&!manual)return;if(!manual&&this.config.server.startsWith('http:')&&this.approved!==this.config.server)return;if(manual&&!await this.consent())return;
 const transport:Transport={request:async(method,path,body)=>(await this.call(method,path,body)).json,binary:async(path)=>(await this.call('GET',path)).arrayBuffer};
 const result=await runSync({transport,vault:this.adapter(),root:this.config.root,selection:this.config.scope==='all'?{scope:'all'}:{scope:'sources',ids:this.config.sourceIds},onProgress:p=>{this.status=p.phase+' '+p.completed+'/'+p.total;}});
 this.status='同步完成 '+new Date().toLocaleString()+'；新增 '+result.created+'，更新 '+result.updated+'，冲突 '+result.conflicts;new Notice(this.status);
 }catch(e){this.status=e instanceof Error?e.message:'同步失败，原笔记保留';new Notice(this.status);}finally{this.busy=false;this.arm();}}
 async refreshSources(){if(this.busy)return;this.busy=true;this.stop();try{if(!await this.consent())return;const rows=(await this.call('GET','/api/sync/sources')).json;if(!Array.isArray(rows)||rows.some(x=>!Number.isSafeInteger(x.id)||typeof x.name!=='string'))throw new Error('来源列表无效');this.sources=rows;}finally{this.busy=false;this.arm();}}
}
class PKMSettings extends PluginSettingTab {
 constructor(app:App,private owner:PKMPlugin){super(app,owner);}
 display(){const {containerEl}=this;containerEl.empty();const p=this.owner;if(p.busy){containerEl.createEl('p',{text:'任务进行中，请完成后重新打开设置，期间不允许更改服务器或目录。'});return;}containerEl.createEl('h2',{text:'PKM 连接器'});containerEl.createEl('p',{text:'只读接收服务器整理结果，写入专用子目录。本地修改会保留冲突副本。默认不自动同步。'});
 new Setting(containerEl).setName('服务器').addText(t=>t.setValue(p.config.server).onChange(value=>{try{const next=origin(value);if(p.busy)return;if(next!==p.config.server){p.token='';p.approved='';p.config.secretId='';p.config.server=next;void p.save();}}catch{}}));
 new Setting(containerEl).setName('专用目录').setDesc('建议PKM，不可选择Vault根目录或现有非托管文件夹').addText(t=>t.setValue(p.config.root).onChange(value=>{if(p.busy)return;p.config.root=value.trim();void p.save();}));
 new Setting(containerEl).setName('临时免配对测试模式').setDesc('危险：服务器启用测试模式后，任何人都可以读取服务器数据。仅用于已批准的临时公开资料测试；不发送任何凭证。关闭后恢复设备配对。').addToggle(t=>t.setValue(p.config.testMode).onChange(async v=>{if(p.busy)return;p.config.testMode=v;if(!v&&!p.token)p.loadToken();await p.save();this.display();}));
  if(!p.config.testMode){let code='',name='Obsidian设备';new Setting(containerEl).setName('设备名称').addText(t=>t.setValue(name).onChange(v=>name=v));new Setting(containerEl).setName('配对码').setDesc('管理网页生成，10分钟有效；不会保存配对码').addText(t=>{t.inputEl.type='password';t.onChange(v=>code=v);});
 new Setting(containerEl).addButton(b=>b.setButtonText('配对').onClick(async()=>{await p.pair(code,name);code='';this.display();})).addButton(b=>b.setButtonText('断开本机').onClick(async()=>{await p.disconnect();this.display();}));}
  new Setting(containerEl).addButton(b=>b.setButtonText('立即同步').onClick(async()=>{await p.sync(true);this.display();}));
 new Setting(containerEl).setName('前台定时同步').setDesc('仅Obsidian打开且可见时；HTTP需本次先手动确认。手机退出后不承诺执行。').addToggle(t=>t.setValue(p.config.auto).onChange(async v=>{p.config.auto=v;await p.save();}));new Setting(containerEl).setName('间隔（分钟）').addDropdown(d=>d.addOptions({'5':'5','15':'15','30':'30','60':'60'}).setValue(String(p.config.minutes)).onChange(async v=>{p.config.minutes=Number(v);await p.save();}));
 new Setting(containerEl).setName('接收范围').addDropdown(d=>d.addOptions({all:'全部',sources:'选定数据源'}).setValue(p.config.scope).onChange(async v=>{p.config.scope=v==='sources'?'sources':'all';await p.save();this.display();}));new Setting(containerEl).addButton(b=>b.setButtonText('刷新数据源').onClick(async()=>{try{await p.refreshSources();this.display();}catch(e){new Notice(e instanceof Error?e.message:'来源读取失败，请检查连接和接入模式');}}));
 if(p.config.scope==='sources'){containerEl.createEl('p',{text:'未勾选时不接收新内容；已有本地笔记不删除。'});for(const s of p.sources)new Setting(containerEl).setName(s.name).addToggle(t=>t.setValue(p.config.sourceIds.includes(s.id)).onChange(async v=>{p.config.sourceIds=v?[...new Set([...p.config.sourceIds,s.id])]:p.config.sourceIds.filter(id=>id!==s.id);await p.save();}));}
 containerEl.createEl('p',{text:p.status});containerEl.createEl('p',{text:'凭证由Obsidian 1.11.4+ SecretStorage管理，插件data.json只保存非敏感配置。不要在不可信仓库启用第三方插件。HTTP并非安全生产接入。'});
 }
}
