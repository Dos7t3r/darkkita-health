const fs=require('fs'), vm=require('vm'),assert=require('assert');
const source=fs.readFileSync(__dirname+'/../views/health/app.js','utf8').split('await bridge.ready();')[0];
// Synthetic fixture only. No actual health record is distributed.
const fixture={date:'2025-01-15',updated_at:1736942400,sleep:{
 asleep_seconds:21600,deep_seconds:3600,rem_seconds:5400,core_seconds:12600,
 awake_seconds:300,awake_count:2,sample_count:12,
 bedtime:'2025-01-15T00:00:00Z',wake_time:'2025-01-15T06:05:00Z'},
 metrics:[{metric:'steps',value:50,unit:'count',start:'2025-01-15T00:00:00Z',end:'2025-01-15T01:00:00Z'}],
 metric_summaries:{steps:{metric:'steps',value:123,unit:'count'}}};
for(const wrapped of [false,true]){
 const elements=new Map();const document={getElementById(id){if(!elements.has(id))elements.set(id,{textContent:'',innerHTML:''});return elements.get(id)}};
 const context={window:{AstrBotPluginView:{}},document,console,fixture,wrapped};
 vm.runInNewContext(source+'\noverview={latest_date:fixture.date}; renderDay(wrapped?{date:fixture.date,data:fixture}:fixture);',context);
 assert.equal(elements.get('bedtime').textContent,'00:00');assert.equal(elements.get('wakeTime').textContent,'06:05');assert.equal(elements.get('samples').textContent,12);assert(elements.get('stageBar').innerHTML.includes('stage deep'));assert(elements.get('stageBar').innerHTML.includes('stage rem'));assert(elements.get('stageBar').innerHTML.includes('stage core'));assert(elements.get('awake').textContent.includes('2 次'));assert(elements.get('daySub').textContent.includes('最后更新'));
 assert(elements.get('metricsGrid').innerHTML.includes('123'));
 console.log('PASS renderDay '+(wrapped?'wrapped':'bridge-unwrapped')+' synthetic sleep stages, clock, awake, sample count');
}
