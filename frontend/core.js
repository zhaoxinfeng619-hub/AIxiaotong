/* Pure state and contract helpers. Shared by the browser and node:test. */
(function(root){
'use strict';
const clone=x=>JSON.parse(JSON.stringify(x));
const templates={
 clarify:{tag:'费用敏感',summary:'客户希望降低月费，当前套餐使用较少',action:'CLARIFY',next:'先确认需求，再提供适用方案',detail:'确认目标月费，以及客户是否愿意了解更匹配使用情况的方案。',reply:'理解，您是希望降低每月费用。您希望月费控制在多少呢？如果有更合适的方案，您愿意一起了解一下吗？',checks:['确认目标月费与使用需求','核验账户及合约实时状态'],unknowns:['目标预算','替代方案意愿']},
 refuse:{tag:'明确拒绝挽留',summary:'客户要求直接降套，停止推荐其他方案',action:'PROCESS_REQUEST',next:'停止推荐，按客户诉求协助受理',detail:'核实客户希望办理的套餐、实时账户状态和办理影响，不再询问拒绝原因。',reply:'好的，尊重您的选择，我不再推荐其他方案。请问您希望调整到哪一档套餐？我会为您核实办理信息，并说明相关事项。',checks:['确认客户希望办理的套餐','核验实时账户状态与办理影响'],unknowns:['目标套餐','实时办理状态']},
 dispute:{tag:'账单争议',summary:'客户对账单费用提出疑问，需要人工核查',action:'HUMAN_REVIEW',next:'核查账单明细，解释争议费用',detail:'查询争议账期和具体扣费项目，不以历史快照判断本月扣费是否正确。核查不应阻碍正常受理。',reply:'理解您对扣费有疑问。请问是哪个月份、哪一笔费用呢？我先帮您核实具体明细，再向您说明。',checks:['确认争议账期及扣费项目','核查实时账单明细'],unknowns:['争议账期','具体扣费明细']},
 willing:{tag:'愿意了解方案',summary:'客户愿意比较更合适的套餐方案',action:'CLARIFY',next:'核实需求与资格，再比较方案',detail:'检索有效政策，核验实时资格及费用后，再提供可比较的方案。当前暂无核验完成的候选。',reply:'好的，我会结合您的使用情况和预算核实适用方案。您希望月费控制在多少，以及每月大约需要多少流量呢？',checks:['核实预算与每月使用需求','检索有效政策并核验实时资格'],unknowns:['目标预算','有效政策与实时资格']},
 conflict:{tag:'资料待核实',summary:'客户陈述与历史快照存在差异',action:'HUMAN_REVIEW',next:'并列记录差异，核查实时状态',detail:'客户提到的合约或业务变更可能未体现在 T+1 快照中，由坐席查询实时记录后确认。',reply:'您提到的情况我记下了。我先核实实时业务记录，再向您说明套餐调整可能涉及的事项。',checks:['核实客户所述业务变更','查询实时合约与融合关系'],unknowns:['客户陈述与快照差异']},
 unknown:{tag:'诉求待确认',summary:'需要补充确认客户本轮的具体需求',action:'CLARIFY',next:'简短确认本轮诉求',detail:'保留已知会话背景，先确认客户希望解决的问题，再决定下一步处置。',reply:'我想再确认一下，您这次最希望我们帮您解决的具体问题是什么呢？',checks:['确认本轮具体诉求'],unknowns:['本轮具体诉求']}
};
function classify(text,refused=false){
 const noOffer=/(不用推荐|不要推荐|别推荐|不推荐|不想了解|不愿意|不挽留|别挽留|直接.{0,5}降|只想.{0,5}降|不需要.{0,5}(优惠|方案|推荐)|不接受)/.test(text);
 const yes=/(愿意|可以|想|重新).{0,8}(了解|看看|比较|听听)/.test(text)&&!/(不愿意|不想|不可以|不再|不要)/.test(text);
 if(noOffer)refused=true;else if(yes)refused=false;
 // A dispute still requires review while retaining the refusal flag.
 let kind=/(多扣|乱扣|扣错|账单.{0,8}(问题|疑问|不对|贵|错)|账单有|争议|投诉)/.test(text)?'dispute':/(有合约|刚.{0,6}(改|办|变更)|今天.{0,8}(改|办)|绑定.{0,4}宽带)/.test(text)?'conflict':refused?'refuse':yes?'willing':/(贵|降套|便宜|降下来|降低|月费|费用|省钱)/.test(text)?'clarify':'unknown';
 return{kind,refused};
}
function localAnalysis(messages,reconsentId=null){
 let state={refused:false,kind:'unknown'},last,pendingId=null,consented=false,budget=null,usage=null;
 for(const m of messages.filter(m=>m.role==='customer')){
  const prior=state;state=classify(m.text,state.refused);
  if(prior.refused&&!state.refused){if(reconsentId!==m.id){state={refused:true,kind:'refuse'};pendingId=m.id;}else pendingId=null;}
  else if(state.refused&&state.kind==='refuse'&&classify(m.text,false).refused)pendingId=null;else if(!state.refused)pendingId=null;
  if(state.refused)consented=false;else if(state.kind==='willing')consented=true;
  if(consented&&['clarify','unknown'].includes(state.kind))state.kind='willing';
  const b=m.text.match(/(?:希望|想|预算|控制|不超过|最多).{0,15}?(\d{1,4})\s*(?:元|块)/);if(b)budget={key:'客户陈述的目标月费',value:b[1]+' 元',source_id:m.id,as_of:m.at||null};
  const u=m.text.match(/(?:每月|流量|用量).{0,12}?(\d+(?:\.\d+)?)\s*(GB|G|MB)/i);if(u)usage={key:'客户陈述的流量需求',value:u[1]+' '+u[2].toUpperCase(),source_id:m.id,as_of:m.at||null};
  last=m;
 }

 const t=clone(templates[state.kind]);if(state.kind==='willing'&&budget){t.unknowns=t.unknowns.filter(x=>x!=='目标预算');if(usage){t.summary='客户愿意比较方案，已说明预算与使用需求';t.reply='好的，您的预算和使用需求我记下了。我会先核实适用政策与办理资格，再向您说明可选方案。';}else t.reply='好的，您的预算我记下了。每月大约需要多少流量，以及多少分钟通话呢？';}if(pendingId){t.checks.unshift('确认客户已重新同意了解替代方案');t.unknowns.unshift('重新同意待坐席确认');}
 return {reconsent_pending:Boolean(pendingId),reconsent_evidence_id:pendingId,kind:state.kind,tag:t.tag,next:t.next,detail:t.detail,summary:t.summary,action:t.action,facts:[budget,usage].filter(Boolean),unknowns:clone(t.unknowns),reason_codes:[state.kind.toUpperCase()],evidence_ids:last?[last.id,'SNAP-C001']:['SNAP-C001'],candidate_policy_ids:[],quote_ids:[],operator_checks:clone(t.checks),customer_reply:t.reply,archive_draft:{request:t.summary,proposed_policy_ids:[],customer_decision:null,handling_status:'unconfirmed'},refused:state.refused,source:'local'};
}
const actions=['CLARIFY','OFFER','PROCESS_REQUEST','HUMAN_REVIEW','FALLBACK'];
function validateResult(value){
 let r=value;
 if(typeof r==='string') {r=JSON.parse(r.trim().replace(/^```(?:json)?\s*/,'').replace(/\s*```$/,''));}
 if(!r||typeof r!=='object'||Array.isArray(r)||!actions.includes(r.action))throw Error('返回结果缺少有效的 action');
 for(const key of ['summary','customer_reply'])if(typeof r[key]!=='string'||!r[key].trim()||r[key].length>10000)throw Error('返回结果的 '+key+' 无效');
 for(const key of ['facts','unknowns','reason_codes','evidence_ids','candidate_policy_ids','quote_ids','operator_checks'])if(!Array.isArray(r[key])||r[key].length>100)throw Error('返回结果的 '+key+' 无效');
 for(const key of ['unknowns','reason_codes','evidence_ids','candidate_policy_ids','quote_ids','operator_checks'])if(r[key].some(x=>typeof x!=='string'||x.length>2000))throw Error(key+' 必须为文本数组');
 if(r.facts.some(f=>!f||typeof f!=='object'||typeof f.key!=='string'||typeof f.source_id!=='string'||!('value'in f)||!('as_of'in f)))throw Error('事实来源格式不完整');
 if(!r.archive_draft||typeof r.archive_draft.request!=='string'||!Array.isArray(r.archive_draft.proposed_policy_ids)||!('customer_decision'in r.archive_draft)||r.archive_draft.handling_status!=='unconfirmed')throw Error('归档状态或格式无效');
 if(r.action!=='OFFER'&&(r.candidate_policy_ids.length||r.quote_ids.length))throw Error('当前动作不允许包含推荐方案');
 // No trusted policy/eligibility/quote service exists in this prototype yet.
 if(r.action==='OFFER')throw Error('候选方案尚未接入可信资格与费用校验，暂不展示推荐');
 return clone(r);
}
function adaptResult(value,messages,reconsentId=null){
 const r=validateResult(value),local=localAnalysis(messages,reconsentId);
 if(local.refused&&r.action!=='HUMAN_REVIEW'&&r.action!=='PROCESS_REQUEST'&&r.action!=='FALLBACK')throw Error('返回建议与客户拒绝状态冲突');
 if(local.kind==='dispute'&&r.action!=='HUMAN_REVIEW'&&r.action!=='FALLBACK')throw Error('费用争议尚未进入人工核查');
 const actionMap={CLARIFY:'确认必要信息后继续服务',PROCESS_REQUEST:'按客户诉求协助受理',HUMAN_REVIEW:'交由坐席核查关键信息',FALLBACK:'继续人工服务流程'};
 return{...r,reconsent_pending:local.reconsent_pending,reconsent_evidence_id:local.reconsent_evidence_id,kind:r.action==='HUMAN_REVIEW'?'dispute':r.action==='PROCESS_REQUEST'?'refuse':'unknown',tag:{CLARIFY:'待澄清',PROCESS_REQUEST:'按诉求受理',HUMAN_REVIEW:'人工核查',FALLBACK:'人工处理'}[r.action],next:actionMap[r.action],detail:r.operator_checks.join('；')||'请坐席核实当前诉求。',refused:local.refused,source:'dify'};
}
// Presentation-only intent summary, anchored to customer statements, never a办理阈值.
function customerIntent(messages,reconsentId=null){
 const customers=messages.filter(m=>m.role==='customer');let labels=[],requestEvidence=null,decisionEvidence=null,level=0,comparison=false;
 for(const m of customers){const text=m.text.replace(/(?:不想|不要|不需要|不是要|不打算|不考虑)(?:再)?(?:销户|退订|降套)/g,'');const found=[];
  if(/(?:降[低点]?|减少|省|便宜).{0,6}(?:费用|月费|钱)|费用.{0,5}(?:低|少)|太贵|有点贵|便宜点/.test(text))found.push('降低费用');
  if(/降套|降套餐|低档|(?:换|改|调).{0,8}(?:便宜|低|套餐)|套餐.{0,5}降/.test(text))found.push('换低档套餐');
  if(/退订|取消.{0,6}(?:业务|服务|增值|流量包)/.test(text))found.push('退订业务');
  if(/销户|注销.{0,5}(?:号码|账户)|不想用这个号/.test(text))found.push('销户');
  if(/多扣|乱扣|扣错|账单.{0,8}(?:问题|疑问|不对|错)/.test(text))found.push('核查账单');
  if(found.length){labels=[...new Set([...labels,...found])];requestEvidence=m;}
  if(found.some(x=>x!=='核查账单')){
   const explicit=/(?:直接|只想|只要|决定|一定要|必须|帮我办理|我要).{0,12}(?:降|退订|销户|注销)|(?:降套|退订|销户).{0,5}(?:办理|就行)|不再考虑/.test(text);
   const consult=/有没有|能不能|能否|哪些|有什么|多少钱|可以了解|愿意了解|看看|比较/.test(text);
   level=explicit?3:consult?1:2;decisionEvidence=m;
  }
  if(/(?:愿意|可以|想).{0,6}(?:了解|看看|比较)/.test(text)&&!/(?:不想|不愿意|不要)/.test(text)){comparison=true;if(!level)level=1;decisionEvidence=m;}
 }
 const local=localAnalysis(messages,reconsentId);
 if(local.refused){const refusal=[...customers].reverse().find(m=>classify(m.text,false).refused);decisionEvidence=refusal||decisionEvidence;level=labels.some(x=>['换低档套餐','退订业务','销户'].includes(x))?3:level;comparison=false;}
 const status=local.reconsent_pending?'重新同意待确认':local.refused?'决定明确':level===3?'决定明确':level===2?'倾向调整':level===1?'咨询比较':'待确认';
 const evidence=decisionEvidence||requestEvidence;
 return{labels:labels.length?labels:['诉求待确认'],level,status,refused:local.refused,reconsentPending:local.reconsent_pending,comparison,quote:evidence?.text||'尚无足够客户表达',sourceId:evidence?.id||null};
}
function preferenceState(messages){
 let state='unknown';
 for(const m of messages.filter(m=>m.role==='customer')){
  const t=m.text;
  if(/(?:先不降|不降了|决定保留|保留原套餐|就用原套餐)/.test(t)&&!/(?:不想|不要|不愿意|不考虑)保留/.test(t))state='retain';
  else if(/(?:直接|只想|只要|坚决|一定要|必须|决定|我要).{0,8}(?:降套|降套餐)|(?:降套|降套餐).{0,5}(?:就行|办理)/.test(t)&&!/(?:不想|不要|不打算)降/.test(t))state='down';
  else if(/(?:考虑|比较|了解|看看|犹豫|再想想|想换|想降)/.test(t)&&!/(?:不想|不要|不愿意)(?:了解|考虑|比较)/.test(t))state='uncertain';
 }
 return{state,label:{unknown:'待确认',down:'坚决降套',uncertain:'摇摆不定',retain:'明确保留'}[state],position:{down:'16.67%',uncertain:'50%',retain:'83.33%'}[state]};
}
const api={preferenceState,customerIntent,localAnalysis,classify,validateResult,adaptResult,clone};
if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.CopilotCore=api;
})(typeof window!=='undefined'?window:globalThis);
