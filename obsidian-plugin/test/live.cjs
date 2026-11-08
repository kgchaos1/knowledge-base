// Explicit live API test, run by maintenance wrapper; no credential files read here.
const fs=require('node:fs/promises'),path=require('node:path'),os=require('node:os');
const {buildSync}=require('esbuild');
const out=path.join(__dirname,'../.test-build/live-core.cjs');buildSync({entryPoints:[path.join(__dirname,'../src/core.ts')],outfile:out,bundle:true,platform:'node',format:'cjs'});
const {runSync}=require(out);
(async()=>{
 const server='http://8.133.220.164:8091';
 // --public explicitly opts into anonymous approved public-data testing.
 const publicMode=process.argv.includes('--public');let cookie='';
 if(!publicMode){const response=await fetch(server+'/api/auth/login',{method:'POST',headers:{Origin:server,'Content-Type':'application/json'},body:JSON.stringify({password:process.env.PKM_PASSWORD}),redirect:'error'});if(!response.ok)throw Error('Login failed');cookie=response.headers.get('set-cookie').split(';')[0];}
 
 const root=await fs.mkdtemp(path.join(os.tmpdir(),'pkm-obsidian-live-'));
 const absolute=p=>{const target=path.resolve(root,p);if(!target.startsWith(root+path.sep))throw Error('Unsafe test path');return target;};
 const vault={read:p=>fs.readFile(absolute(p),'utf8'),write:(p,t)=>fs.writeFile(absolute(p),t),exists:async p=>{try{await fs.stat(absolute(p));return true;}catch{return false;}},mkdir:p=>fs.mkdir(absolute(p)),writeBinary:(p,b)=>fs.writeFile(absolute(p),Buffer.from(b)),readBinary:async p=>{const b=await fs.readFile(absolute(p));return b.buffer.slice(b.byteOffset,b.byteOffset+b.byteLength);},list:async p=>{const entries=await fs.readdir(absolute(p),{withFileTypes:true});return {files:entries.filter(e=>e.isFile()).map(e=>p+'/'+e.name),folders:entries.filter(e=>e.isDirectory()).map(e=>p+'/'+e.name)};}};
 const request=async(method,p,body)=>{const r=await fetch(server+p,{method,headers:{Origin:server,...(publicMode?{}:{Cookie:cookie}),...(body?{'Content-Type':'application/json'}:{})},body:body?JSON.stringify(body):undefined,redirect:'error'});if(!r.ok)throw Object.assign(Error('HTTP failed'),{status:r.status});return r;};
 const transport={request:async(m,p,b)=>(await request(m,p,b)).json(),binary:async p=>(await request('GET',p)).arrayBuffer()};
 try{const one=await runSync({transport,vault});const two=await runSync({transport,vault});if(one.created!==3||two.created||two.updated)throw Error('Unexpected sync counts');console.log('Actual plugin core live sync verified:',JSON.stringify(one),'second unchanged',two.unchanged);}
 finally{await fs.rm(root,{recursive:true,force:true});}
})().catch(()=>{console.error('Live connector verification failed; no secrets logged');process.exitCode=1;});
