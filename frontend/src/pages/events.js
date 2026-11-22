import {plot} from '../components/common/chart.js';
import {feedback, field} from '../components/common/fields.js';
import {metrics, panel, pill} from '../components/common/panels.js';
import {table} from '../components/common/table.js';
import {TASK} from '../constants/enums.js';
import {handler, poll} from '../hooks/action.js';
import {session} from '../stores/session.js';
import {workspace} from '../stores/workspace.js';
import {button, el, h, link, p} from '../utils/dom.js';
import {date} from '../utils/format.js';

const ACTIVE_JOB = job => job.status === 'queued' || job.status === 'running';

function summary(events) {
  const latest = events[0];
  return metrics([
    {label:'有效高位事件', value:events.length, detail:events.length ? '重算后一次性切换' : '暂无越线记录'},
    {label:'最近事件开始', value:latest ? date(latest.start_at) : '—'},
    {label:'最近事件结束', value:latest ? date(latest.end_at) : '—'},
    {label:'最近峰值（m）', value:latest ? latest.peak_level : '—', detail:latest ? `峰值时刻 ${date(latest.peak_at)}` : ''},
  ]);
}

function eventTable(events) {
  return table([
    {label:'事件开始', render:item=>link(`${item.pool} · ${date(item.start_at)}`, `#events/${item.id}`)},
    {label:'结束', render:item=>date(item.end_at)},
    {key:'minutes', label:'时长（分钟）'},
    {key:'peak_level', label:'峰值（m）'},
    {label:'峰值时刻', render:item=>date(item.peak_at)},
    {key:'threshold_m', label:'警戒线（m）'},
  ], events, {caption:'高位事件列表', empty:'还没有高位事件。探头读数连续达到警戒线满十分钟即在此出现。', pageSize:12});
}

function detailPanel(detail) {
  const current = detail.readings.filter(row=>!row.superseded);
  const chart = {title:`${detail.pool} 事件分钟液位（m）`, kind:'line', threshold:detail.threshold_m,
    series:current.map(row=>({label:date(row.minute), value:row.level_m}))};
  return panel('事件明细与原读数',
    el('div',{class:'result-head'},
      p(`${detail.pool} · 连续 ${detail.minutes} 分钟`,'eyeline'),
      h(2,`${date(detail.start_at)} — ${date(detail.end_at)}`),
      p(`峰值 ${detail.peak_level} m（${date(detail.peak_at)}），警戒线 ${detail.threshold_m} m。`,'intro-copy'),
      detail.state !== 'active' && p('该事件已被后续重算切换，仅供核对。','feedback')),
    metrics([
      {label:'开始', value:date(detail.start_at)},
      {label:'结束', value:date(detail.end_at)},
      {label:'时长（分钟）', value:detail.minutes},
      {label:'峰值（m）', value:detail.peak_level},
      {label:'峰值时刻', value:date(detail.peak_at)},
      {label:'警戒线（m）', value:detail.threshold_m},
    ]),
    plot(chart),
    table([
      {label:'分钟', render:row=>date(row.minute)},
      {key:'level_m', label:'液位（m）'},
      {key:'revision', label:'修订号'},
      {key:'batch_name', label:'回传批次'},
      {label:'状态', render:row=>row.superseded ? '留档核对' : '当前有效'},
    ], detail.readings, {caption:'事件原读数', pageSize:20}));
}

function importPanel(refresh) {
  const {spec} = workspace;
  const message = feedback();
  const submit = button('校验并导入', {type:'submit', class:'primary', 'data-testid':'import-probe'});
  const sample = button('下载示例 CSV', {class:'quiet'});
  sample.addEventListener('click', handler(sample, message, ()=>workspace.download('/probe/template', spec.probe.sample_name)));
  const form = el('form',{class:'fields', 'data-testid':'probe-form', onSubmit:handler(submit, message, async event=>{
    const data = new FormData(event.target);
    const file = data.get('source');
    if (!file?.size) throw new Error('请选择一个分钟读数 CSV 文件');
    if (file.size > 2_000_000) throw new Error('CSV 最多 2 MB');
    const threshold = data.get('alert_level_m');
    const result = await workspace.importProbe({name:data.get('batch_name'), filename:file.name,
      csv_text:await file.text(), alert_level_m:threshold === '' || threshold === null ? null : Number(threshold)});
    if (!result.created) {
      message.textContent = '同名同内容批次已导入过，未重复计时。';
      return;
    }
    await refresh();
  })},
    field({key:'batch_name', label:'批次名称', type:'string', max_length:120}),
    el('label',{class:'field', for:'source'}, el('span',{},'分钟读数 CSV'),
      el('input',{id:'source', type:'file', name:'source', accept:'.csv,text/csv', required:true})),
    field({key:'alert_level_m', label:'高位警戒线（m）', type:'number', required:false, min:0.01, max:20,
      help:`留空沿用池现值（默认 ${spec.probe.default_threshold_m} m）；调整会触发全池重算。`}),
    submit);
  return panel('分钟读数导入',
    p('同池同一分钟只取修订号最大的读数，旧读数留档核对。','subtle'),
    el('div',{class:'actions'}, sample), form, message);
}

export async function eventsPage(route, refresh) {
  const [pools, events, batches, jobs] = await Promise.all(
    [workspace.pools(), workspace.events(), workspace.probeBatches(), workspace.probeJobs()]);
  const detail = route.id ? await workspace.eventDetail(route.id) : null;
  if (jobs.some(ACTIVE_JOB)) {
    const original = location.hash;
    poll(()=>workspace.probeJobs(), list=>!list.some(ACTIVE_JOB), ()=>location.hash===original)
      .then(done=>{ if (done) refresh(); })
      .catch(()=>{});
  }
  return el('div',{class:'two-col'},
    el('div',{class:'wide-panel'},
      panel('交班摘要', summary(events)),
      panel('高位事件', eventTable(events)),
      detail && detailPanel(detail)),
    el('aside',{class:'sidebar-panel'},
      session.canWrite && importPanel(refresh),
      panel('池状态', table([
        {key:'pool', label:'水池'},
        {key:'threshold_m', label:'警戒线（m）'},
        {key:'active_events', label:'有效事件'},
      ], pools, {caption:'池状态', empty:'导入分钟读数后建立池档案。'})),
      panel('回传批次', table([
        {key:'name', label:'批次'},
        {key:'row_count', label:'行数'},
        {key:'inserted', label:'新增'},
        {key:'kept', label:'留档'},
        {key:'skipped', label:'跳过'},
        {label:'时间', render:item=>date(item.created_at)},
      ], batches, {caption:'回传批次', empty:'还没有回传批次。'})),
      panel('重算任务', table([
        {key:'id', label:'任务'},
        {key:'pool', label:'水池'},
        {key:'epoch', label:'纪元'},
        {label:'状态', render:item=>pill(TASK[item.status], item.status)},
        {label:'完成时间', render:item=>date(item.finished_at)},
      ], jobs, {caption:'重算任务', empty:'导入新读数后自动排队。'})),
      panel('归集口径', el('ul',{class:'method-list'}, [
        '同池同一分钟只取修订号最大的读数，旧读数留档可核对。',
        '连续达到警戒线满 10 分钟记一次高位事件；缺测分钟断开连续。',
        '跨午夜按分钟连续计算，不按日历日切分。',
        '乱序补传可合并事件，更正读数可拆分事件；只重算受影响区段，相关事件一次性切换。',
        '重跑同批不重复计时；较早的重算晚完成不会盖掉新结果。',
      ].map(text=>el('li',{},text))))));
}
