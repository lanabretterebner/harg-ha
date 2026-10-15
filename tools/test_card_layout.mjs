/** Geometry/rendering only; local stand-ins and loopback, no HA/controller. */
import assert from 'node:assert/strict';
import {readFile,realpath,mkdir} from 'node:fs/promises';
import {createServer} from 'node:http';
import {resolve,dirname,sep} from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium} from 'playwright';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const allowed=new Set(['/tests/test-card-layout.html','/tests/test-card-layout.js','/dist/hargassner-thermostat-popup.js','/favicon.ico']);
const server=createServer(async(req,res)=>{try{const url=new URL(req.url,'http://127.0.0.1');if(req.method!=='GET'||url.search||!allowed.has(url.pathname)){res.writeHead(404).end();return}if(url.pathname==='/favicon.ico'){res.writeHead(204).end();return}const file=await realpath(resolve(root,'.'+url.pathname));assert(file.startsWith(root+sep));const body=await readFile(file);res.writeHead(200,{'Content-Type':file.endsWith('.html')?'text/html':'text/javascript','Cache-Control':'no-store'}).end(body)}catch{res.writeHead(404).end()}});
await new Promise((r,j)=>{server.once('error',j);server.listen(0,'127.0.0.1',r)});
const origin=`http://127.0.0.1:${server.address().port}`,errors=[],browser=await chromium.launch({headless:true,...(process.env.HARG_BROWSER_EXECUTABLE?{executablePath:process.env.HARG_BROWSER_EXECUTABLE}:{})}),results=[];
try{
 const context=await browser.newContext({serviceWorkers:'block'});await context.route('**/*',route=>{const u=new URL(route.request().url());if(u.origin===origin&&allowed.has(u.pathname)&&!u.search)return route.continue();errors.push('offorigin');return route.abort()});await context.routeWebSocket('**/*',socket=>{errors.push('websocket');socket.close()});const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
 for(const [width,dark]of [[1280,false],[1280,true],[390,false],[390,true],[320,false]]){
  await page.setViewportSize({width,height:900});await page.goto(origin+'/tests/test-card-layout.html');await page.waitForFunction(()=>window.layoutReady);await page.evaluate(dark=>document.body.classList.toggle('dark',dark),dark);
  await page.waitForTimeout(50);
  const before=await page.evaluate(()=>window.layoutFixture.calls.length);assert.equal(before,0);
  const info=await page.evaluate(()=>[...document.querySelectorAll('hargassner-thermostat-card')].map(card=>{const r=card.getBoundingClientRect(),root=card.shadowRoot,control=root.querySelector('[data-hargassner-correction]'),buttons=[...control.querySelectorAll('button')].map(b=>{const a=b.getBoundingClientRect();return{width:a.width,height:a.height,left:a.left,right:a.right,border:getComputedStyle(b).borderTopWidth,icon:b.querySelector('svg')?.getBoundingClientRect().width,label:b.getAttribute('aria-label')}}),dial=control.querySelector('[role=slider]'),status=root.querySelector('.status'),mode=root.querySelector('.mode'),sr=root.querySelector('.status-label').getBoundingClientRect();return{width:r.width,height:r.height,left:r.left,right:r.right,buttons,dialWidth:dial.getBoundingClientRect().width,modeTop:mode.getBoundingClientRect().top,statusTop:status.getBoundingClientRect().top,missingReadingHidden:root.querySelector('.readings').children[0].hidden,captionWidth:sr.width,captionHeight:sr.height,stepperBorder:getComputedStyle(control.querySelector('.correction-stepper')).borderTopWidth,stepperBackground:getComputedStyle(control.querySelector('.correction-stepper')).backgroundColor,text:control.textContent}}));
  assert.equal(info.length,3);for(const card of info){assert(card.width<=327||width===390);assert(card.height>=280&&card.height<=420,JSON.stringify(card));assert(card.right<=width);for(const b of card.buttons){assert(b.width>=44&&b.height>=44);assert.equal(b.border,'0px');assert.equal(b.icon,24);assert(b.label);assert(b.left>=card.left&&b.right<=card.right)}assert.equal(card.dialWidth,180);assert.equal(card.stepperBorder,'1px');assert.notEqual(card.stepperBackground,'rgba(0, 0, 0, 0)');assert(card.captionWidth<=1&&card.captionHeight<=1);assert(!card.text.includes('Schritte'));assert(!card.text.includes('Zieltemperatur'));}assert.equal(info[1].missingReadingHidden,true);
  const third=page.locator('hargassner-thermostat-card').nth(2);await third.locator('[data-hargassner-correction] button').nth(1).click();await page.waitForFunction(()=>window.layoutFixture.calls.length===1);assert.equal(await page.evaluate(()=>window.layoutFixture.calls[0][0]),'number');
  if(process.env.HARG_TEST_ARTIFACT_DIR){await mkdir(process.env.HARG_TEST_ARTIFACT_DIR,{recursive:true});await page.screenshot({path:resolve(process.env.HARG_TEST_ARTIFACT_DIR,`layout-${width}-${dark?'dark':'light'}.png`),fullPage:true})}
  results.push({width,theme:dark?'dark':'light',cards:info.map(c=>({width:c.width,height:c.height})),passed:true});
 }
 assert.deepEqual(errors,[]);console.log(JSON.stringify({scope:'Synthetic layout geometry; no real HA/controller services',passed:true,tests:results.length,results},null,2));await context.close();
}finally{await browser.close();await new Promise(r=>server.close(r))}
