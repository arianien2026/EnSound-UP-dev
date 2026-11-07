import test, { before, after, beforeEach, afterEach } from 'node:test'
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { once } from 'node:events'
import { readFile } from 'node:fs/promises'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { BuyerClient, DEVICE_KEY, PRODUCT, STAGE, consumeClaim } from '../../.buyer-test/BuyerClient.js'
import { BuyerPanel, submitCheckout } from '../../.buyer-test/BuyerPanel.js'
let child, base, email
let clients=[]
const origin='http://localhost:5173'
const storage=()=>{const map=new Map();return {getItem:k=>map.get(k)??null,setItem:(k,v)=>map.set(k,v),removeItem:k=>map.delete(k)}}
const localFetch=(url,init={})=>{assert.ok(String(url).startsWith(base+'/'),'External fetch forbidden');return fetch(url,{...init,headers:{...init.headers,Origin:origin}})}
const fixture=async(path,body)=>{const r=await localFetch(base+'/__fixture__/'+path,{method:body===undefined?'GET':'POST',headers:{'Content-Type':'application/json'},...(body===undefined?{}:{body:JSON.stringify(body)})});assert.equal(r.status,200);return r.json()}
const api=async(path,body)=>localFetch(base+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
function client(store=storage(),fetcher=localFetch){const c=new BuyerClient(base,store,fetcher);clients.push(c);return c}
async function checkout(c){await c.createOrder(email,email);const {token}=await fixture('email');assert.equal((await api('/orders/verify-email',{token})).status,200);await c.refreshOrder();return c.checkout()}
async function paid(c){const data=await checkout(c);assert.equal((await fixture('pay',{fields:data.fields})).ack,'1|OK');return data}
async function claim(c){await c.claimEmail(email);const {token}=await fixture('email');const r=await api('/access/email-links/redeem',{token});assert.equal(r.status,200);const {claim_session}=await r.json();await c.acceptClaim(claim_session);return claim_session}
before(async()=>{
 child=spawn(process.env.ENSOUND_TEST_PYTHON||'python3',['tests/frontend/fixture.py'],{stdio:['ignore','pipe','pipe']})
 let errors='';child.stderr.on('data',v=>{errors+=v.toString()})
 const port=await new Promise((resolve,reject)=>{let output='';const timeout=setTimeout(()=>reject(new Error('Fixture startup timed out: '+errors.slice(-1000))),15000)
 child.once('exit',code=>{clearTimeout(timeout);reject(new Error('Fixture exited '+code+': '+errors.slice(-1000)))})
 child.stdout.on('data',v=>{output+=v.toString();if(output.includes('\n')){clearTimeout(timeout);resolve(JSON.parse(output.split('\n')[0]).port)}})})
 base='http://127.0.0.1:'+port
})
beforeEach(async()=>{({email}=await fixture('reset',{}))})
afterEach(()=>{clients.forEach(c=>c.dispose());clients=[]})
after(async()=>{if(child && child.exitCode === null){const ended=once(child,'exit');child.kill('SIGTERM');await ended}})

test('browser-native fetch keeps its Window receiver during device activation',async()=>{
 const originalFetch=globalThis.fetch
 const requests=[]
 globalThis.fetch=function(url,init){
  assert.equal(this,globalThis,'Native browser fetch requires its global receiver')
  requests.push(new URL(url).pathname)
  const body=String(url).endsWith('/access/devices')
   ? {device_credential:'c'.repeat(43),credential_expires_at:new Date(Date.now()+60000).toISOString()}
   : {status:'active',product_code:PRODUCT,credential_expires_at:new Date(Date.now()+60000).toISOString()}
  return Promise.resolve(new Response(JSON.stringify(body),{status:200,headers:{'Content-Type':'application/json'}}))
 }
 try {
  const c=new BuyerClient(base,storage())
  clients.push(c)
  await c.start('p'.repeat(43))
  assert.deepEqual(requests,['/access/devices','/access/status'])
  assert.equal(c.state.full,true)
 } finally {globalThis.fetch=originalFetch}
})

test('buyer order → verify email → Stage fields → paid → email claim → device → backend FULL; duplicate callback is idempotent',async()=>{
 const c=client();await c.start();assert.equal(c.state.full,false)
 await c.createOrder(email,'different@example.com');assert.equal((await fixture('counts')).orders,0)
 await c.createOrder(email,email);assert.equal(c.state.message,'verify_email');assert.equal(await c.checkout(),null)
 assert.equal(c.state.full,false)
 const {token}=await fixture('email');assert.equal((await api('/orders/verify-email',{token})).status,200)
 await c.refreshOrder();assert.equal(c.state.emailVerified,true)
 const data=await c.checkout();assert.equal(data.checkout_url,STAGE);assert.equal(c.state.full,false)
 await fixture('pay',{fields:data.fields});await fixture('pay',{fields:data.fields});await c.refreshOrder()
 assert.equal(c.state.orderStatus,'paid');assert.equal(c.state.full,false)
 await claim(c);assert.equal(c.state.full,true);assert.equal((await fixture('counts')).entitlements,1)
})
test('payment pending, failed result, and SimulatePaid notifications keep FREE',async()=>{
 const c=client();const data=await checkout(c);assert.equal(c.state.full,false)
 await fixture('pay',{fields:data.fields,changes:{RtnCode:'10300066'}})
 await fixture('pay',{fields:data.fields,changes:{SimulatePaid:'1'}})
 await c.refreshOrder();await c.revalidate();assert.equal(c.state.orderStatus,'pending');assert.equal(c.state.full,false)
 assert.equal((await fixture('counts')).entitlements,0)
})
test('invalid or expired claim session never unlocks; no persisted FULL flags are trusted',async()=>{
 const store=storage();store.setItem('ensound-full','true');store.setItem('accessLevel','full')
 const c=client(store);await c.start();assert.equal(c.state.full,false)
 await c.acceptClaim('x'.repeat(43));assert.equal(c.state.message,'link_expired');assert.equal(c.state.full,false)
})
test('reload checks backend; invalid device credential cannot authorize',async()=>{
 const store=storage(),c=client(store);await paid(c);await claim(c);assert.equal(c.state.full,true)
 let checks=0;const next=client(store,(url,init)=>{if(url.endsWith('/access/status'))checks++;return localFetch(url,init)})
 assert.equal(next.state.full,false);await next.start();assert.equal(checks,1);assert.equal(next.state.full,true);assert.equal(next.state.message,'active')
 const d=JSON.parse(store.getItem(DEVICE_KEY));d.credential='z'.repeat(43);store.setItem(DEVICE_KEY,JSON.stringify(d))
 await next.revalidate();assert.equal(next.state.full,false);assert.equal(store.getItem(DEVICE_KEY),null)
})
test('refund pending preserves access; confirmed refund revokes device authorization and reload',async()=>{
 const store=storage(),c=client(store);await paid(c);await claim(c)
 await fixture('state',{refund:'refund_pending'});await c.revalidate();assert.equal(c.state.full,true)
 await fixture('state',{refund:'refunded'});await c.revalidate();assert.equal(c.state.full,false)
 const next=client(store);await next.start();assert.equal(next.state.full,false)
})
test('expired credential and offline validation remain FREE; a future local expiry cannot override backend expiry',async()=>{
 const store=storage(),c=client(store);await paid(c);await claim(c)
 await fixture('state',{expire_device:true});await c.revalidate();assert.equal(c.state.full,false)
 const offline=client(store,async()=>{throw new Error('offline')});await offline.start();assert.equal(offline.state.full,false)
 const d={deviceId:crypto.randomUUID(),credential:'z'.repeat(43),expiresAt:'2000-01-01T00:00:00Z'}
 store.setItem(DEVICE_KEY,JSON.stringify(d));const expired=client(store);await expired.start();assert.equal(expired.state.full,false)
})
test('three separate browser devices activate; fourth is rejected through the real API',async()=>{
 const first=client();await paid(first)
 for(let i=0;i<4;i++){
  await fixture('state',{advance_claim:i+1});const c=i===0?first:client();await claim(c)
  assert.equal(c.state.full,i<3);if(i===3)assert.equal(c.state.message,'device_limit')
 }
 assert.equal((await fixture('counts')).devices,3)
})
test('late successful status response cannot restore access after a newer denial',async()=>{
 const store=storage(),seed=client(store);await paid(seed);await claim(seed)
 let resolveFirst;let n=0
 const c=client(store,async()=>{n++;if(n===1)return new Promise(r=>{resolveFirst=r});return new Response('{}',{status:401})})
 const first=c.revalidate();await c.revalidate();resolveFirst(new Response(JSON.stringify({status:'active',product_code:PRODUCT,credential_expires_at:new Date(Date.now()+10000).toISOString()})))
 await first;assert.equal(c.state.full,false)
})
test('claim proof is removed from the URL before requests; payment query or flags never grant access',async()=>{
 const proof='a'.repeat(43), calls=[]
 assert.equal(consumeClaim({hash:'#access_claim='+proof,pathname:'/EnSound-UP-dev/',search:'?paid=1'}, {replaceState:(...args)=>calls.push(args)}),proof)
 assert.equal(calls[0][2],'/EnSound-UP-dev/?paid=1')
 assert.ok(!JSON.stringify(calls).includes(proof));assert.equal(consumeClaim({hash:'#paid=true',pathname:'/',search:''},{replaceState(){}}),null)
 const c=client();await c.start();assert.equal(c.state.full,false)
})
test('checkout uses a POST form only to Stage, escapes values as DOM data, and never sends secrets',()=>{
 const submitted=[];const inputs=[]
 const form={appendChild:v=>inputs.push(v),submit(){submitted.push(this.action)},remove(){},hidden:false}
 const doc={createElement:tag=>tag==='form'?form:{},body:{appendChild(){}}}
 submitCheckout({checkout_url:STAGE,fields:{MerchantTradeNo:'synthetic',ItemName:'<img onerror=bad>'}},doc)
 assert.equal(form.method,'POST');assert.equal(form.target,'_self');assert.equal(form.rel,'noopener noreferrer');assert.equal(submitted[0],STAGE)
 assert.equal(inputs[1].value,'<img onerror=bad>')
 assert.throws(()=>submitCheckout({checkout_url:'https://other.invalid',fields:{}},doc))
})
test('buyer UI renders bilingual status, email confirmation, disabled checkout, and no manual FULL control',()=>{
 const c=client();const html=renderToStaticMarkup(React.createElement(BuyerPanel,{client:c}))
 assert.match(html,/確認 Email/);assert.match(html,/Confirm email/);assert.match(html,/role="status"/)
 assert.match(html,/disabled/);assert.ok(!html.includes('setAccessLevel'))
})
test('frozen learning modules stay outside the buyer integration and release preview cannot authorize',async()=>{
 const source=await readFile('src/App.tsx','utf8')
 assert.match(source,/const isFull = buyer\.full \|\| \(UI_PREVIEW && accessLevel === 'full'\)/)
 assert.match(source,/import\.meta\.env\.DEV/);assert.match(source,/useBuyer\(!UI_PREVIEW\)/)
})

test('a failed online check with an otherwise valid stored credential cannot retain FULL',async()=>{
 const store=storage(),c=client(store);await paid(c);await claim(c);assert.equal(c.state.full,true)
 const offline=client(store,async()=>{throw new Error('network unavailable')});await offline.revalidate()
 assert.equal(offline.state.full,false);assert.equal(offline.state.message,'unavailable')
})
test('credential expiry demotes an already active client without reload',async()=>{
 const store=storage(),seed=client(store);await paid(seed);await claim(seed)
 const c=client(store,async()=>new Response(JSON.stringify({status:'active',product_code:PRODUCT,credential_expires_at:new Date(Date.now()+40).toISOString()})))
 await c.revalidate();assert.equal(c.state.full,true)
 await new Promise(r=>setTimeout(r,80));assert.equal(c.state.full,false);assert.equal(c.state.message,'expired')
})

test('returning from checkout restores the pending order but never restores FULL from its paid state',async()=>{
 const store=storage(),first=client(store);await paid(first)
 const returned=client(store);assert.ok(returned.state.orderId);assert.equal(returned.state.full,false)
 await returned.start();assert.equal(returned.state.orderStatus,'paid');assert.equal(returned.state.full,false)
 await claim(returned);assert.equal(returned.state.full,true);assert.equal(returned.state.orderId,null)
 const later=client(store);assert.equal(later.state.orderId,null);await later.start();assert.equal(later.state.full,true)
})
