"""账单核对纯逻辑：回传CSV解析、导入判定、逐笔差异与串单线索。
不依赖数据库与Web框架，可独立单元校验；数据库编排见billing.py。"""
import csv,io,math
EPS=1e-6
MAX_ROWS=20000
HEADERS={'bill_no':('账单号','bill_no'),'revision':('修订号','revision'),'request_ref':('请求编号','request_ref'),'minutes':('账单分钟','minutes')}
def parse_bills_csv(text):
 """解析结算方回传CSV；任何一行不合法即整批拒绝并给出行号。"""
 if not isinstance(text,str) or not text.strip(): raise ValueError('账单CSV内容为空')
 reader=csv.DictReader(io.StringIO(text.strip().removeprefix('\ufeff').strip()))
 if not reader.fieldnames: raise ValueError('账单CSV缺少表头')
 fields={h.strip() for h in reader.fieldnames if h};mapping={}
 for key,names in HEADERS.items():
  hit=next((n for n in names if n in fields),None)
  if not hit: raise ValueError('账单CSV缺少列：'+'/'.join(names))
  mapping[key]=hit
 rows=[];seen={}
 for record in reader:
  line=reader.line_num;raw={k:(record.get(name) or '').strip() for k,name in mapping.items()}
  if not any(raw.values()): continue
  if not 1<=len(raw['bill_no'])<=120: raise ValueError(f'第{line}行账单号不能为空或超长')
  if not 1<=len(raw['request_ref'])<=120: raise ValueError(f'第{line}行请求编号不能为空或超长')
  try: revision=int(raw['revision'])
  except (TypeError,ValueError): raise ValueError(f'第{line}行修订号须为非负整数')
  if revision<0 or revision>1000000: raise ValueError(f'第{line}行修订号超出范围')
  try: minutes=float(raw['minutes'])
  except (TypeError,ValueError): raise ValueError(f'第{line}行账单分钟须为有限非负数')
  if not math.isfinite(minutes) or minutes<0 or minutes>10000000: raise ValueError(f'第{line}行账单分钟须为有限非负数')
  row={'bill_no':raw['bill_no'],'revision':revision,'request_ref':raw['request_ref'],'minutes':minutes}
  key=(row['bill_no'],row['revision'])
  if key in seen:
   if seen[key]['request_ref']!=row['request_ref'] or abs(seen[key]['minutes']-row['minutes'])>EPS: raise ValueError(f'第{line}行与文件内同号同版记录取值不一致，请先在结算方处对齐')
   continue
  seen[key]=row;rows.append(row)
  if len(rows)>MAX_ROWS: raise ValueError('单批账单行数超过上限'+str(MAX_ROWS))
 if not rows: raise ValueError('账单CSV没有有效数据行')
 return rows
def plan_import(rows,stored):
 """按已存账单判定每行去向。stored:{(bill_no,revision):{'request_ref','minutes'}}含全部历史版本。
 同版同值=重复回传忽略；同版异值=隔离；低于已存最大修订号=旧版不覆盖；其余入库。"""
 max_rev={}
 for no,rev in stored: max_rev[no]=max(max_rev.get(no,-1),rev)
 to_insert=[];conflicts=[];counts={'received':len(rows),'imported':0,'duplicate':0,'stale':0,'conflict':0}
 for row in rows:
  hit=stored.get((row['bill_no'],row['revision']))
  if hit:
   if hit['request_ref']==row['request_ref'] and abs(hit['minutes']-row['minutes'])<=EPS: counts['duplicate']+=1
   else: conflicts.append(row);counts['conflict']+=1
  elif row['revision']<max_rev.get(row['bill_no'],-1): counts['stale']+=1
  else: to_insert.append(row);counts['imported']+=1
 return to_insert,conflicts,counts
def current_bills(bills):
 """每个账单号只取最高修订号作为当前有效账单。"""
 best={}
 for b in bills:
  if b['bill_no'] not in best or b['revision']>best[b['bill_no']]['revision']: best[b['bill_no']]=b
 return best
def expected_minutes(snapshot,business_minutes):
 """用已复核报告冻结快照，按底稿稳定编号重算逐笔业务分钟。"""
 keys=snapshot.get('source_keys');sources=snapshot.get('sources') or []
 if not keys or len(keys)!=len(sources): raise ValueError('报告快照缺少底稿稳定编号，请重新排队核算后再对账')
 if len(set(keys))!=len(keys): raise ValueError('报告快照内底稿稳定编号重复')
 policy=snapshot['policy'];out={}
 for key,data in zip(keys,sources):
  out[key]={'minutes':business_minutes(data['opened'],data['closed'],policy),'department':data.get('department',''),'period':data.get('period','')}
 return out
def build_diffs(expected,bills):
 """逐笔对账：一条请求可拆多张账单；账单请求编号不在报告内记为多单。"""
 matched={};extras={}
 for b in bills.values(): (matched if b['request_ref'] in expected else extras).setdefault(b['request_ref'],[]).append(b)
 diffs=[]
 for ref,info in expected.items():
  mine=sorted(matched.get(ref,[]),key=lambda b:b['bill_no'])
  billed=round(sum(b['minutes'] for b in mine),6);exp=round(info['minutes'],6);delta=round(billed-exp,6)
  category='漏单' if not mine else ('吻合' if abs(delta)<=EPS else '分钟差')
  diffs.append({'request_ref':ref,'department':info['department'],'period':info['period'],'expected':exp,'billed':billed,'delta':delta,'category':category,'bill_nos':[b['bill_no'] for b in mine]})
 for ref,group in sorted(extras.items()):
  billed=round(sum(b['minutes'] for b in group),6)
  diffs.append({'request_ref':ref,'department':'','period':'','expected':0.0,'billed':billed,'delta':billed,'category':'多单','bill_nos':sorted(b['bill_no'] for b in group)})
 order={'分钟差':0,'漏单':1,'多单':2,'吻合':3}
 diffs.sort(key=lambda d:(order[d['category']],d['request_ref']))
 return diffs
def build_trace(diffs):
 """由逐笔差异推导串单线索：总额平衡但逐笔错位、组内抵消、漏单与多单相合。"""
 matched=[d for d in diffs if d['category']!='多单'];extras=[d for d in diffs if d['category']=='多单']
 total_expected=round(sum(d['expected'] for d in matched),6);total_billed=round(sum(d['billed'] for d in matched),6)
 extras_total=round(sum(d['billed'] for d in extras),6);balance=round(total_expected-total_billed-extras_total,6)
 off=[d for d in matched if abs(d['delta'])>EPS]
 cross_suspect=abs(balance)<=EPS and bool(off)
 by_dept={}
 for d in off: by_dept.setdefault(d['department'] or '未分组',[]).append(d)
 clusters=[]
 for dept,members in sorted(by_dept.items()):
  if len(members)>1 and abs(round(sum(m['delta'] for m in members),6))<=EPS:
   clusters.append({'department':dept,'members':[{'request_ref':m['request_ref'],'delta':m['delta'],'bill_nos':m['bill_nos']} for m in members],'note':'组内差异相互抵消，疑似组内串单'})
 hints=[];missing_total=round(sum(d['expected'] for d in matched if d['category']=='漏单'),6)
 if missing_total>EPS and extras_total>EPS and abs(missing_total-extras_total)<=EPS: hints.append(f'漏单合计{missing_total:g}分钟与多单合计{extras_total:g}分钟相等，疑似账单挂错请求编号')
 if cross_suspect: hints.append('账单总分钟与报告应算相等，但逐笔存在错位——疑似串单')
 return {'total_expected':total_expected,'total_billed':total_billed,'extras_total':extras_total,'balance':balance,'cross_suspect':cross_suspect,'clusters':clusters,'hints':hints}
