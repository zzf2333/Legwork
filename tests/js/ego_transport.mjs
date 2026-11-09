// 传输替身：执行 Python 生成的完整脚本；每个页面回调使用独立 vm，不能访问 Node 闭包。
import fs from 'node:fs';
import vm from 'node:vm';
const file = process.argv[2];
const state = JSON.parse(fs.readFileSync(file, 'utf8'));
const script = fs.readFileSync(0, 'utf8');
const globals = {job: {}, chat: {}};
// 会话列表虚拟滚动参数：模拟只渲染可视窗口的真实行为。
const chatList = {scrollTop: 0, clientHeight: 900, itemHeight: 100};
const observers = new Set();
class PerformanceObserver {
  constructor(callback){this.callback=callback;}
  observe(){observers.add(this);}
  disconnect(){observers.delete(this);}
}
const jobs = Object.fromEntries(state.jobs.map(j => [j.job_id, j]));
// 列表分页替身：keywordBatches 给出每页岗位 ID，滚动一次加载下一页。
const batchIds = query => {
  const batches = state.keywordBatches?.[query];
  if(!batches) return state.keywords[query] || [];
  return batches.slice(0, state.loadedBatches?.[query] ?? 1).flat();
};
const source = j => ({encryptJobId:j.job_id, brandName:j.company, encryptBossId:j.boss_id,
  jobName:j.title, salaryDesc:j.salary, cityName:j.address, uid:'uid-'+j.boss_id, uniqueId:'conv-'+j.boss_id});
const rows = () => state.requestDelay > 0 ? batchIds(state.previousQuery).map(id => jobs[id]) : state.loadingTicks > 0 ? [] : batchIds(state.query).map(id => jobs[id]);
const store = {$store:{state:{userInfo:{userId:state.self}}}};
function message(text, mid) {
  return {mid, uniqueId:state.selected.uniqueId, fromId:state.self, toId:state.selected.uid, text, received:true};
}
function pageDOM(kind) {
  const active = () => jobs[state.opened];
  const editor = {isContentEditable:true, get innerText(){return state.draft;},
    parentElement:{__vue__:{$props:{uniqueId:state.selected?.uniqueId}}},
    focus(){document.activeElement=editor;}};
  const button = action => ({innerText:'立即沟通', disabled:false, getClientRects:()=>[1],
    getAttribute:name=>name==='data-isfriend'?'false':null, classList:{contains:()=>false}, click(){
      if(action==='contact') {
        const contact=source(active()); state.contacts.push(contact);
        // 联系后联系人出现，但未自动选中；桥接器需要选择它。
        state.pending=contact;
        state.events.push('contact:'+state.opened);
      } else {
        state.messages.push(message(state.draft,'custom-mid')); state.draft='';
        state.events.push('send:'+state.opened);
      }
    }});
  const list = { ...store, $options:{name:'boss-list'}, fullList:state.contacts, bottomText:state.historyBatches?.length ? '滚动加载更多' : '没有更多了', isLoading:false };
  function friend(contact) {
    return {parentElement:{__vue__:{...store,$options:{name:'friend'},$parent:list,$props:{source:contact}}},
      getClientRects:()=>[1], click(){state.selected=contact;state.messages=[message('默认招呼','default-mid')];}};
  }
  // 只渲染可视窗口内的联系人，与真实页面的虚拟滚动一致。
  const visibleContacts = () => {
    const start = Math.floor(chatList.scrollTop / chatList.itemHeight);
    const count = Math.ceil(chatList.clientHeight / chatList.itemHeight);
    return state.contacts.slice(start, start + count);
  };
  const contactScrollHeight = () => Math.max(state.contacts.length * chatList.itemHeight, chatList.clientHeight);
  function jobNode(j) {
    const href=state.hrefOverrides?.[j.job_id] || '/job_detail/'+j.job_id+'.html';
    const override=state.dataOverrides?.[j.job_id];
    const data=override===null ? undefined : Object.assign(source(j), override || {});
    const link={getAttribute:()=>href,click(){state.opened=j.job_id;}};
    return {parentElement:{__vue__:{$props:{data}}},getClientRects:()=>[1],
      querySelector:()=>link,click(){state.opened=j.job_id;state.cardClicks=(state.cardClicks||0)+1;},link};
  }
  const document = {body:{get innerText(){return '';}},activeElement:null,
    documentElement:{get scrollHeight(){return 800 + batchIds(state.query).length * 120;}},
    execCommand(_name,_ui,text){state.draft=text;return true;},
    querySelector(selector) {
      if(kind==='job') {
        if(state.detailMode) {
          const j=active();
          if(selector==='.job-sec-text') return {innerText:'  '+j.description+'\n'};
          if(selector==='.location-address') return {innerText:j.address};
          if(selector==='.job-boss-info h2') return {childNodes:[{nodeType:3,textContent:j.boss_name},
            {nodeType:1,textContent:j.boss_active || ''}]};
          if(selector==='.job-boss-info h2 .boss-active-time') return j.boss_active ? {innerText:j.boss_active} : null;
          if(selector==='.sider-company .icon-scale') return j.company_size ?
            {closest:()=>({innerText:'  '+j.company_size+'\n'})} : null;
          if(selector==='.job-banner .btn-startchat') return button('contact');
          return null;
        }
        if(selector==='.page-jobs-main') {
          const j=active();return {__vue__:{...store,loading:!state.requestDelay && state.loadingTicks>0,jobList:rows(),hasMore:!state.listExhausted,defaultCity:{code:state.city},jobDetailLoading:false,jobDetail:{
            jobInfo:{encryptId:j.job_id,encryptUserId:j.boss_id,jobName:j.title,salaryDesc:j.salary,address:j.address,
              postDescription:j.description,invalidStatus:false},
            bossInfo:{name:j.boss_name, activeTimeDesc:j.boss_active || ''},
            brandComInfo:{brandName:j.company, scaleName:j.company_size || ''},relationInfo:{beFriend:false}}}};
        }
        if(selector==='a[href$="/web/geek/resume"]') return {};
        if(selector==='input[placeholder="搜索职位、公司"]') return {value:state.query};
        if(selector==='.job-detail-box .op-btn-chat') return button('contact');
        return null; // 岗位页不存在聊天 DOM。
      }
      if(selector==='.user-list-content') return {get scrollHeight(){return contactScrollHeight();}, get clientHeight(){return chatList.clientHeight;},
        get scrollTop(){return chatList.scrollTop;}, set scrollTop(value){
        chatList.scrollTop = Math.max(0, Math.min(value, contactScrollHeight() - chatList.clientHeight));
        if(chatList.scrollTop >= contactScrollHeight() - chatList.clientHeight) {
          state.events.push('history-scroll');
          if(!state.historyStalled && state.historyBatches?.length) state.contacts.push(...state.historyBatches.shift());
        }
      }};
      if(selector==='.friend-content') return friend(state.contacts[0] || {});
      if(selector==='#chat-input') return editor;
      if(selector==='.chat-record') return {__vue__:{loading:false}};
      if(selector==='input[placeholder="搜索30天内的联系人"]') return {value:''};
      if(selector==='.chat-conversation .btn-send') return button('send');
      return null;
    },
    querySelectorAll(selector) {
      if(kind==='job') {
        if(state.detailMode) return [];
        if(selector==='li.job-card-box') return rows().map(jobNode);
        if(selector==='li.job-card-box a.job-name') return rows().map(j=>jobNode(j).link);
        return [];
      }
      if(selector==='.friend-content') return visibleContacts().map(friend);
      if(selector==='.friend-content.selected') return state.selected?[friend(state.selected)]:[];
      if(selector==='.label-name') return [{innerText:'全部',parentElement:{classList:{contains:()=>true}}}];
      if(selector==='.message-item') return state.messages.map(m=>({__vue__:{$props:{message:m}},querySelectorAll:()=>[]}));
      return [];
    }};
  return document;
}
function page(kind) {
  const evaluate = async (fn,arg) => {
    const location=new URL(kind==='job' && state.detailMode ? state.detailURL : 'https://www.zhipin.com/web/geek/'+(kind==='job'?'jobs':'chat')+(state.omitCity?'':'?city='+state.city+'&query='+encodeURIComponent(state.query)));
    state.time=(state.time||0)+1;
    const entries=state.requestStart!=null && !state.requestDelay && !state.loadingTicks ?
      [{name:'https://www.zhipin.com/wapi/zpgeek/search/joblist.json',startTime:state.requestStart,responseEnd:state.requestStart+1}] : [];
    const performance={now:()=>state.time,getEntriesByType:()=>state.resourceBufferFull?[]:entries};
    for(const observer of observers) if(entries.length) observer.callback({getEntries:()=>entries});
    const sandbox=Object.assign(globals[kind],{document:pageDOM(kind),location,URL,performance,PerformanceObserver,setTimeout,clearTimeout,scrollTo:()=>{
      if(kind!=='job') return;
      state.events.push('list-scroll');
      const batches=state.keywordBatches?.[state.query];
      const loaded=state.loadedBatches?.[state.query] ?? 1;
      if(!batches || loaded >= batches.length) return;
      state.loadedBatches=state.loadedBatches || {};
      state.loadedBatches[state.query]=loaded+1;
      state.loadingTicks=1;
    },argument:structuredClone(arg)});
    sandbox.window=sandbox;
    if(kind==='job' && state.detailMode) {
      const j=jobs[state.opened];
      sandbox._PAGE={isLogin:true,uid:state.detailSelf||state.self};
      sandbox._jobInfo={job_id:j.job_id,user_id:j.boss_id,company:j.company,job_name:j.title,job_salary:j.salary};
    }
    const result=vm.runInNewContext('('+fn.toString()+')(argument)',sandbox,{timeout:1000});
    if(state.requestDelay>0) state.requestDelay--; else if(state.loadingTicks>0) state.loadingTicks--;
    return result;
  };
  return {evaluate,async waitForFunction(fn,arg) {
      for(let i=0;i<6;i++) if(await evaluate(fn,arg)) return;
      throw Error('wait condition not satisfied');
    },
    async url(){return kind==='job' && state.detailMode ? state.detailURL : 'https://www.zhipin.com/web/geek/'+(kind==='job'?'jobs':'chat');},
    async goto(url){
      const u=new URL(url),id=u.pathname.match(/^\/job_detail\/([^/]+)\.html$/)?.[1];
      state.detailMode=!!id;
      if(id){state.detailURL=url;state.opened=state.redirectJob||id;state.events.push('navigate:'+id);}
      else {state.query=u.searchParams.get('query');state.loadingTicks=0;state.omitCity=false;state.loadedBatches={};}
    },
    async fill(_selector,text){state.previousQuery=state.query;state.query=text;},
    async press(){state.requestStart=state.time;state.requestDelay=state.delayedStart?1:0;state.loadingTicks=3;state.omitCity=false;},
    mouse:{async move(){},async wheel(){}},
    async click(selector) {
      const index=Number(selector.match(/nth=(\d+)/)?.[1] || 0);
      const nodes=pageDOM(kind).querySelectorAll(kind==='job'?'li.job-card-box':'.friend-content');
      if(!nodes[index]) throw Error('missing click target'); nodes[index].click();
    }};
}
globalThis.taskSpace=async()=>({ownership:'agent',page:label=>page(label==='p2'?'job':'chat')});
try { await new (Object.getPrototypeOf(async function(){}).constructor)(script)(); }
finally {fs.writeFileSync(file,JSON.stringify(state));}
