/* Browser acceptance against a fresh deterministic launcher started with --rules.
 * NODE_PATH may point at the desktop app's bundled playwright package.
 * UI_BROWSER_EXE can select an already installed Chromium; no downloads.
 */
const {chromium}=require('playwright');
const fs=require('node:fs'),assert=require('node:assert/strict'),path=require('node:path');
const {execFileSync}=require('node:child_process');
const port=process.env.UI_DEMO_PORT||'18766';
assert.match(port,/^\d{4,5}$/);
const base='http://127.0.0.1:'+port,out=path.resolve('output/ui-upgrade');
fs.mkdirSync(out,{recursive:true});
const checks=[];
function pass(name){checks.push(name);console.log('PASS '+name);}
(async()=>{
  const browser=await chromium.launch({headless:true,...(process.env.UI_BROWSER_EXE?{executablePath:process.env.UI_BROWSER_EXE}:{})});
  try{
    const context=await browser.newContext({viewport:{width:1366,height:768}}),page=await context.newPage(),errors=[],requests=[];
    page.on('pageerror',e=>errors.push(e.message));
    await context.route('**/*',route=>{const url=new URL(route.request().url());if(url.origin!==base)return route.abort();return route.continue();});
    page.on('request',r=>{if(r.method()==='POST')requests.push({url:r.url(),body:r.postDataJSON()});});
    const screenshot=file=>page.screenshot({path:path.join(out,file),animations:'disabled'});
    // Compare the saved original front end without altering service responses/data.
    await page.route(base+'/demo',route=>route.fulfill({contentType:'text/html; charset=utf-8',body:fs.readFileSync(path.join(out,'offline_demo.before.html'),'utf8')}));
    await page.goto(base+'/demo');await screenshot('before-1366.png');await page.unroute(base+'/demo');
    await page.goto(base+'/demo');
    await page.locator('.empty').waitFor();
    assert.equal(requests.length,0);assert.match(await page.locator('#mode-label').innerText(),/确定性离线演示/);assert.equal(await page.locator('#query').isDisabled(),true);
    await screenshot('after-login-1366.png');pass('No automatic requests; real offline label; login required');
    await page.locator('#password').fill('wrong-fictional-password');await page.locator('#login button').click();
    await page.waitForFunction(()=>document.querySelector('#login-state').classList.contains('error'));
    await page.locator('#password').fill('fictional-demo-only');await page.locator('#login button').click();
    await page.locator('#workbench').waitFor();assert.equal(await page.locator('#username').isVisible(),false);assert.equal(await page.locator('#password').inputValue(),'');
    await screenshot('after-workspace-1366.png');pass('Wrong login recovery; successful login hides form and clears password');
    async function ask(text){await page.locator('#query').fill(text);await page.locator('#send').click();await page.waitForFunction(()=>document.querySelector('#compose').getAttribute('aria-busy')==='false');}
    async function runScenario(query, fromCard=true){
      const response=page.waitForResponse(r=>r.url()===base+'/admin/ai/chat');
      if(fromCard)await page.locator('.example[data-query='+JSON.stringify(query)+']').click();
      else await ask(query);
      const reply=await response;
      assert.equal(reply.status(),200);
      assert.equal(reply.request().postDataJSON().query,query);
      await page.locator('#compose[aria-busy=false]').waitFor();
      return reply.json();
    }
    const cards=page.locator('.example');
    assert.equal(await cards.count(),3);
    assert.equal(await page.locator('.example:visible').count(),3);
    assert.match(await page.locator('.welcome-sign').innerText(),/YOUR LESSON WORKSPACE/);
    const boxes=await Promise.all([0,1,2].map(index=>cards.nth(index).boundingBox()));
    assert.equal(boxes[0].x,boxes[1].x);
    assert.equal(boxes[1].y,boxes[2].y);
    assert.equal(boxes[0].x+boxes[0].width,boxes[2].x+boxes[2].width);
    assert.equal(await page.locator('#query').evaluate(node=>getComputedStyle(node).fontSize),'16px');
    pass('Original welcome layout with one full-width card and two rule cards');
    await runScenario('查询虚构小明的剩余课时');await page.locator('.candidate').nth(1).waitFor();
    const first=requests.at(-1).body;assert.equal(first.conversationId,undefined);await screenshot('after-candidates-1366.png');
    let selectedResponse;
    const responsePromise=page.waitForResponse(r=>r.url().endsWith('/admin/ai/chat'));
    await page.locator('.candidate').nth(1).click();selectedResponse=await (await responsePromise).json();
    await page.waitForFunction(()=>document.querySelector('#compose').getAttribute('aria-busy')==='false');
    const selection=requests.at(-1).body;assert.equal(typeof selection.selectedStudentId,'number');assert.equal(selection.conversationId,selectedResponse.conversationId);
    await page.getByRole('heading',{name:'请问需要查询哪个科目的剩余课时？',exact:true}).waitFor();
    assert.equal(await page.locator('.candidate:enabled').count(),0);pass('Same-name selection forwards only server candidate ID and maintains conversation; subject clarification has no invented choices');
    await ask('数学');assert.equal(await page.locator('.hours-value strong').innerText(),'10');assert.equal(requests.at(-1).body.conversationId,selection.conversationId);assert.match(await page.locator('.hours-card').innerText(),/虚构小明.*DEMO2/);
    await screenshot('after-hours-1366.png');pass('Actual SQLite result: subject, total/deducted/remaining hours; multi-turn ID preserved');
    await ask('补课有什么规定？');assert.equal(await page.locator('.message.assistant .bubble').last().innerText(),'目前没有找到相关规定。');assert.equal(await page.locator('.sources').count(),0);pass('Unapproved/disabled rules show a concise empty state');
    const count=requests.length;await page.locator('#reset').click();
    const refused=await runScenario('帮虚构小明增加三节数学课。',false);
    await page.getByRole('heading',{name:'已拦截数据修改请求'}).waitFor();
    assert.equal(requests.length,count+1);assert.deepEqual(refused.data,[]);assert.deepEqual(refused.trace,[]);
    await screenshot('after-refusal-1366.png');pass('Real server refusal card; exactly one existing read-only chat request');
    await page.locator('#reset').click();assert.equal(await page.locator('.message').count(),0);
    await runScenario('查询虚构小明的数学剩余课时',false);
    assert.equal(requests.at(-1).body.conversationId,undefined);
    await page.locator('.candidate').first().click();
    await page.locator('.hours-value strong').waitFor();
    assert.equal(await page.locator('.hours-value strong').innerText(),'9');
    pass('Complete-hours example resets conversation and reads SQLite immediately after selection');
    await page.locator('#reset').click();
    const absent=await runScenario('查询一个不存在的学生还剩多少课。',false);
    assert.match(await page.locator('.message.assistant .bubble').innerText(),/未找到匹配的学生/);
    assert.deepEqual(absent.data,[]);assert.deepEqual(absent.candidates,[]);
    pass('Missing student example returns no record or candidates');

    const python=process.env.UI_DEMO_PYTHON||path.resolve(process.platform==='win32'?'.venv/Scripts/python.exe':'.venv/bin/python');
    execFileSync(python,['app/prepare_offline_demo_rules.py',port],{stdio:'pipe'});
    const scenarios=[
      ['请假有什么规定？','学员如需请假，应在课程开始前联系教务老师提出申请。经教务老师确认后，可以安排一次补课。','请假规则',false],
      ['补课有什么规定？','补课时间由教务老师与学员协商确定，具体安排请联系教务老师确认。','补课规则',false],
      ['请假后可补几次课？','学员请假经教务老师确认后，可以安排一次补课。','请假规则',true],
      ['补课时间怎么确定？','补课时间由教务老师与学员协商确定。','补课规则',true],
    ];
    const font=locator=>locator.evaluate(node=>getComputedStyle(node).fontSize);
    const noDisclaimer=/BM25|demo-db:|DEMO-JINBI-|版本|仅供 Jinbi|此内容不是任何真实机构制度|请勿视为机构正式制度/;
    for(const [query,answer,title,typed] of scenarios){
      await page.locator('#reset').click();
      const rule=await runScenario(query,!typed);
      const message=page.locator('.message.assistant').last(),source=message.locator('.sources');
      const disclosure=source.locator('summary'),body=source.locator('.source-excerpt'),heading=source.locator('.card-title');
      const answerBubble=message.locator('.bubble');
      assert.equal(await answerBubble.innerText(),answer);
      assert.equal(rule.citations.length,1);
      assert.equal(await source.getAttribute('open'),null);
      assert.equal(await disclosure.innerText(),'查看依据');
      assert(!(await message.innerText()).includes(rule.citations[0].display_text));
      await disclosure.press('Enter');
      assert.equal(await heading.innerText(),title);
      assert.equal(await body.innerText(),rule.citations[0].display_text);
      assert.doesNotMatch(await message.textContent(),noDisclaimer);
      assert.equal(await font(answerBubble),'19px');
      assert.equal(await font(page.locator('.message.user .bubble')),'18px');
      assert.equal(await font(heading),'20px');assert.equal(await font(body),'17px');
      if(query==='请假有什么规定？'){
        await screenshot('after-rules-1366.png');
        await page.setViewportSize({width:375,height:812});
        assert.equal(await font(answerBubble),'17px');
        assert.equal(await font(body),'16px');
        assert(await page.locator('#messages').evaluate(node=>node.scrollWidth<=node.clientWidth));
        await screenshot('after-rules-375.png');
        await page.setViewportSize({width:1366,height:768});
      }
      pass('Live rule answer with clean, traceable source: '+query);
    }
    await page.locator('#reset').click();
    for(const viewport of [{width:1920,height:1080},{width:1280,height:800},{width:768,height:1024},{width:375,height:812},{width:390,height:844}]){
      await page.setViewportSize(viewport);
      const geometry=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth,compose:document.querySelector('.compose-area').getBoundingClientRect().bottom,height:innerHeight,chatScroll:document.querySelector('#messages').scrollWidth,chatWidth:document.querySelector('#messages').clientWidth,chatTop:document.querySelector('#messages').scrollTop}));
      assert(geometry.scroll<=geometry.width,JSON.stringify(geometry));assert(geometry.compose<=geometry.height+1,JSON.stringify(geometry));
      assert(geometry.chatScroll<=geometry.chatWidth,JSON.stringify(geometry));assert.equal(geometry.chatTop,0);
      await screenshot(`after-welcome-${viewport.width}.png`);
    }pass('Responsive 1920, 1280, 768, 375, 390; no horizontal overflow; composer inside viewport');
    await page.locator('#sidebar-toggle').click();await page.locator('#reset').waitFor({state:'visible'});await page.keyboard.press('Escape');assert.equal(await page.locator('#sidebar-toggle').getAttribute('aria-expanded'),'false');pass('Mobile navigation opens and closes with Escape');
    await page.setViewportSize({width:1366,height:768});
    // Hold an actual local request to verify request locking and draft edits.
    let release;const gate=new Promise(r=>release=r);
    await page.route('**/admin/ai/chat',async route=>{await gate;await route.continue();});
    await page.locator('#query').fill('查询虚构小明剩余课时');await page.locator('#query').press('Enter');
    await page.waitForFunction(()=>document.querySelector('#compose').getAttribute('aria-busy')==='true');
    assert.equal(await page.locator('#send').isDisabled(),true);assert.equal(await page.locator('#reset').isDisabled(),true);
    await page.locator('#query').fill('处理中编辑的新草稿');await page.locator('#query').press('Enter');release();
    await page.waitForFunction(()=>document.querySelector('#compose').getAttribute('aria-busy')==='false');assert.equal(await page.locator('#query').inputValue(),'处理中编辑的新草稿');await page.unroute('**/admin/ai/chat');pass('In-flight duplicate submit blocked; reset locked; new draft retained');
    await context.setOffline(true);await ask('网络中断时保留草稿');assert.equal(await page.locator('#query').inputValue(),'网络中断时保留草稿');assert.equal(await page.locator('#query').isDisabled(),false);await context.setOffline(false);pass('Network interruption restores controls and retains unsent text');
    await page.locator('#query').fill('第一行');const before=requests.length;await page.locator('#query').press('Shift+Enter');await page.locator('#query').press('a');assert.match(await page.locator('#query').inputValue(),/\n/);assert.equal(requests.length,before);
    await page.locator('#query').dispatchEvent('keydown',{key:'Enter',isComposing:true});assert.equal(requests.length,before);pass('Shift+Enter newline and Chinese IME do not submit');
    // Fault injection is limited to this test browser; these are not screenshots.
    await page.route('**/admin/ai/chat',route=>route.fulfill({status:502,contentType:'text/plain',body:'bad gateway'}));await ask('异常响应');assert.equal(await page.locator('#query').inputValue(),'异常响应');await page.unroute('**/admin/ai/chat');pass('Malformed response recovery');
    await page.route('**/admin/ai/chat',route=>route.fulfill({status:401,contentType:'application/json',body:JSON.stringify({detail:'登录已失效'})}));await ask('登录过期');await page.locator('#login-panel').waitFor();assert.equal(await page.locator('#query').isDisabled(),true);await page.unroute('**/admin/ai/chat');pass('401 returns to login and disables authenticated actions');
    await page.reload();assert.equal(await page.locator('#query').isDisabled(),true);assert.equal(await page.locator('.message').count(),0);assert.equal(await page.evaluate(()=>localStorage.length+sessionStorage.length),0);pass('Refresh returns to login; no token or student chat persisted in browser storage');
    await page.evaluate(()=>{for(let i=0;i<8;i++)render({answer:'长消息与连续字符 '+('x'.repeat(2400)),status:'clarification'});});
    assert(await page.locator('#messages').evaluate(node=>node.scrollWidth<=node.clientWidth));
    await page.locator('#messages').evaluate(node=>node.scrollTop=0);
    await page.waitForFunction(()=>document.querySelector('#messages').scrollTop===0);
    await page.waitForTimeout(100);
    await page.evaluate(()=>render({answer:'新回复',status:'clarification'}));
    assert.equal(await page.locator('#jump').isVisible(),true);
    await page.locator('#jump').click();
    assert(await page.locator('#messages').evaluate(node=>node.scrollHeight-node.scrollTop-node.clientHeight<4));
    pass('Long text wraps; reading history keeps scroll position; latest-message action scrolls to bottom');
    await page.evaluate(()=>messages.replaceChildren());
    // Raw contract rendering: text injection stays text; unsupported data stays unstructured.
    await page.evaluate(()=>render({answer:'<img src=x onerror=alert(1)>',status:'complete',data:[{tool:'get_student_hours',result:{subjects:[{subjectName:'数学',remainingHours:0,totalHours:0,deductedHours:0}]}}],citations:[{document_title:'<img onerror=alert(1)>',chunk_id:'test-only',supporting_excerpt:'<script>alert(1)</script>'}]}));
    assert.equal(await page.locator('#messages img,#messages script').count(),0);assert.equal(await page.locator('.hours-value strong').innerText(),'0');assert.equal(await page.locator('.sources').getAttribute('open'),null);await page.locator('.sources summary').click();assert.equal(await page.locator('.source-excerpt').innerText(),'<script>alert(1)</script>');pass('XSS escaped as text; zero hours supported; source disclosure preserves original text');
    await page.emulateMedia({reducedMotion:'reduce'});assert.equal(await page.locator('.message').first().evaluate(e=>getComputedStyle(e).animationName),'none');pass('Reduced motion supported');
    assert.deepEqual(errors,[]);pass('No browser JavaScript errors');
    fs.writeFileSync(path.join(out,'browser-results.json'),JSON.stringify({checks,errors},null,2));
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
