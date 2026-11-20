import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
const control=process.env.EVDT_DASHBOARD_CONTROL;
const fixture=JSON.parse(fs.readFileSync(path.join(control,'ready.json'),'utf8'));
async function stage(name){const nonce=crypto.randomUUID();fs.writeFileSync(path.join(control,'scenario-command.json'),JSON.stringify({nonce,stage:name}));let reply;await expect.poll(()=>{try{reply=JSON.parse(fs.readFileSync(path.join(control,'scenario-reply.json'),'utf8'));return reply.nonce;}catch{return null;}},{timeout:15000}).toBe(nonce);expect(reply.passed,JSON.stringify(reply)).toBe(true);}
test('isolated replay, playback, original provenance, frozen ingress and reader/session scope',async({page,context,browser},info)=>{
  const errors=[],external=[],requests=[];
  page.on('pageerror',e=>errors.push(String(e)));
  page.on('request',r=>requests.push({url:r.url(),method:r.method()}));
  await page.route('**/*',route=>{if(new URL(route.request().url()).origin!==new URL(fixture.replay_base_url).origin){external.push(route.request().url());return route.abort();}return route.continue();});
  const replayEntry = (key) => fixture.replay_base_url + '/?review=' + crypto.randomUUID() + '#access=' + encodeURIComponent(key);
  await page.goto(fixture.replay_base_url);
  await expect(page.getByRole('heading',{name:'Open replay from the dashboard'})).toBeVisible();
  await page.goto(replayEntry(fixture.reader_token));await expect(page.getByRole('alert')).toContainText('outside this recording');
  await page.goto(replayEntry(fixture.replay_token));
  await page.getByLabel('From working seconds').fill('3.5');await page.getByLabel('To working seconds').fill('40');
  const responsePromise=page.waitForResponse(r=>r.url().endsWith('/v1/replay/sessions')&&r.status()===200);
  await page.getByRole('button',{name:'Load recording'}).click();const response=await responsePromise;const raw=await response.body();const source=JSON.parse(raw);expect(crypto.createHash('sha256').update(raw).digest('hex')).toBe(response.headers()['x-content-sha256']);
  await expect(page.getByTestId('replay-namespace')).toContainText('replay/');await expect(page.locator('[data-replay-signal]')).toHaveCount(20);
  await expect(page.getByText('REPLAY · RECORDING ANALYSIS',{exact:true})).toBeVisible();await expect(page.getByText('Before-window seed').first()).toBeVisible();
  const namespace=await page.getByTestId('replay-namespace').innerText(),digest=await page.getByTestId('replay-digest').innerText();
  const parsed=source.packets.map(e=>({...e,body:JSON.parse(Buffer.from(e.raw_base64,'base64'))})).sort((a,b)=>a.body.sim_time_s-b.body.sim_time_s||a.body.source_epoch-b.body.source_epoch||a.body.stream_seq-b.body.stream_seq);
  const row=page.locator('[data-replay-signal="motion.speed"]');
  const visible=parsed.filter(p=>p.body.sim_time_s<=3.5);const latest=visible.filter(p=>p.body.samples.some(s=>s.signal_id==='motion.speed')).at(-1);expect(latest).toBeTruthy();
  await expect(page.getByTestId('replay-position')).toContainText(`${visible.filter(p=>["location.latitude","location.longitude"].every(id=>p.body.samples.some(s=>s.signal_id===id&&s.quality==="good"))).length} recorded points`);
  await page.getByText('Inspect selected original observation',{exact:true}).click();await expect(page.locator('pre')).toContainText(latest.body.event_id);await expect(page.locator('pre')).toContainText(latest.wire_sha256);
  const beforeRequests=requests.length;
  await page.getByRole('button',{name:'Next observation',exact:true}).click();await expect(page.getByTestId('replay-time')).toHaveText('4.00 s');await page.getByRole('button',{name:'Previous observation',exact:true}).click();await expect(page.getByTestId('replay-time')).toHaveText('3.50 s');
  await page.bringToFront();await expect.poll(()=>page.evaluate(()=>document.visibilityState)).toBe('visible');
  // Establish animation readiness after decoding the recording. The product's
  // hidden-tab/scheduling-interruption pause remains enabled during playback.
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  await page.getByLabel('Playback speed').selectOption('2');await page.getByRole('button',{name:'Play',exact:true}).click();await expect.poll(async()=>parseFloat(await page.getByTestId('replay-time').innerText())).toBeGreaterThan(4);await page.getByRole('button',{name:'Pause',exact:true}).click();const paused=await page.getByTestId('replay-time').innerText();await page.waitForTimeout(350);await expect(page.getByTestId('replay-time')).toHaveText(paused);
  await page.getByRole('button',{name:'Go to end',exact:true}).click();await expect(row).toContainText('stale in replay');await expect(page.getByText('END OF RECORDING',{exact:true})).toBeVisible();
  await context.setOffline(true);await page.getByRole('button',{name:'Restart',exact:true}).click();await expect(page.getByTestId('replay-time')).toHaveText('3.50 s');await context.setOffline(false);
  expect(requests.slice(beforeRequests)).toEqual([]);await expect(page.getByTestId('replay-digest')).toHaveText(digest);
  await page.evaluate(()=>scrollTo(0,0));await page.screenshot({path:path.join(process.env.EVDT_BROWSER_OUTPUT,info.project.name+'-replay-desktop.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});await page.evaluate(()=>scrollTo(0,0));await page.screenshot({path:path.join(process.env.EVDT_BROWSER_OUTPUT,info.project.name+'-replay-mobile.png'),fullPage:true});const mobileFits=await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth);await page.setViewportSize({width:1440,height:1050});
  // A real additional MQTT event changes the recording source, not the frozen session.
  await page.getByLabel('Recorded vehicle').selectOption('bus-002');await expect(page.getByTestId('replay-namespace')).toHaveCount(0);
  await page.getByLabel('To working seconds').fill('100');await page.getByRole('button',{name:'Load recording'}).click();await expect(page.getByTestId('replay-namespace')).not.toHaveText(namespace);await page.getByRole('button',{name:'Go to end',exact:true}).click();const oldValue=await row.locator('.number').innerText();
  const frozen=await page.getByTestId('replay-digest').innerText(),oldNamespace=await page.getByTestId('replay-namespace').innerText();
  await stage('setup');await stage(oldValue==='7'?'good-zero':'recover');await stage('finish');
  await page.getByRole('button',{name:'Restart',exact:true}).click();await page.getByRole('button',{name:'Go to end',exact:true}).click();await expect(row.locator('.number')).toHaveText(oldValue);await expect(page.getByTestId('replay-digest')).toHaveText(frozen);
  await page.getByRole('button',{name:'Load recording'}).click();await expect(page.getByTestId('replay-namespace')).not.toHaveText(oldNamespace);await expect(page.getByTestId('replay-digest')).not.toHaveText(frozen);await page.getByRole('button',{name:'Go to end',exact:true}).click();await expect(row.locator('.number')).toHaveText(oldValue==='7'?'0':'7');
  const packageResponse=await page.request.post(fixture.replay_base_url+'/v1/replay/sessions',{headers:{Authorization:'Bearer '+fixture.replay_token},data:{asset_id:'bus-002',source_run_id:fixture.run_id,from_s:1,to_s:100}});const qualityPackage=await packageResponse.json();
  for(const quality of ['missing','invalid']){const event=qualityPackage.packets.map(p=>JSON.parse(Buffer.from(p.raw_base64,'base64'))).find(p=>p.samples.some(s=>s.signal_id==='motion.speed'&&s.quality===quality));expect(event).toBeTruthy();await page.getByLabel('Seek to working seconds').fill(String(event.sim_time_s));await expect(row).toContainText(quality);await expect(row.locator('.number')).toHaveText('—');}
  // Stop before invalid/missing readings and seek across them in the existing recording.
  await page.getByLabel('Replay playhead').focus();await page.getByLabel('Replay playhead').press('Home');await expect(row).toContainText('unknown');
  await page.getByRole('button',{name:'Close replay session'}).click();await expect(page.getByTestId('replay-namespace')).toHaveCount(0);
  await page.goto(replayEntry(fixture.replay_scoped_token));await expect(page.getByLabel('Recorded vehicle').locator('option')).toHaveCount(1);await expect(page.getByLabel('Recorded vehicle')).toHaveValue('bus-001');
  await page.reload();await expect(page.getByRole('heading',{name:'Open replay from the dashboard'})).toBeVisible();expect(await page.evaluate(()=>({local:localStorage.length,session:sessionStorage.length}))).toEqual({local:0,session:0});
  // A changed packet with a recomputed outer digest still fails the inner-byte check.
  await page.route('**/v1/replay/sessions',async route=>{const r=await route.fetch();const data=await r.json();data.packets[0].raw_base64='e30=';const altered=JSON.stringify(data);await route.fulfill({response:r,body:altered,headers:{...r.headers(),'x-content-sha256':crypto.createHash('sha256').update(altered).digest('hex')}});});
  await page.goto(replayEntry(fixture.replay_token));await page.getByRole('button',{name:'Load recording'}).click();await expect(page.getByRole('alert')).toContainText('SHA-256');await expect(page.getByTestId('replay-namespace')).toHaveCount(0);
  expect(errors).toEqual([]);expect(external).toEqual([]);expect(mobileFits).toBe(true);
  fs.writeFileSync(path.join(control,info.project.name+'-replay-result.json'),JSON.stringify({passed:true,project:info.project.name,browser:browser.version(),package_packets:source.packets.length,checks:['separate credentials and asset scope','original bytes/hash/UTC provenance','seed age, past-only map and no future reading','play/pause/rate/step/seek/end/offline','no playback network requests','frozen during actual later ingress','new session identity and updated source','no credential persistence','inner hash tampering rejected','desktop/mobile layout','no external requests']}));
});
