import { basicSetup } from 'codemirror';
import { EditorView, keymap } from '@codemirror/view';
import { EditorState, Compartment } from '@codemirror/state';
import { indentWithTab } from '@codemirror/commands';
import { python } from '@codemirror/lang-python';
import { autocompletion, snippetCompletion } from '@codemirror/autocomplete';
import { setDiagnostics } from '@codemirror/lint';
import { openSearchPanel } from '@codemirror/search';
import { oneDark } from '@codemirror/theme-one-dark';

const snippets = [
 ['SparkSession','SparkSession.builder.appName("${name}").master("local[*]").getOrCreate()','创建会话'],
 ['f.col','f.col("${column}")','选择列'], ['f.lit','f.lit(${value})','常量列'],
 ['f.sum','f.sum("${column}")','求和'], ['f.avg','f.avg("${column}")','均值'],
 ['f.count','f.count("${column}")','记录计数'],
 ['f.when','f.when(${condition}, ${value}).otherwise(${default})','条件列'],
 ['Window.partitionBy','Window.partitionBy("${group}").orderBy(f.col("${column}").desc())','分组窗口'],
 ['f.rank','f.rank().over(${window})','并列排名'],
 ['f.transform','f.transform("${array}", lambda x: ${expression})','数组转换'],
 ['df.filter','df.filter(${condition})','筛选行'], ['df.select','df.select("${column}")','选择列'],
 ['df.withColumn','df.withColumn("${name}", ${expression})','添加列'],
 ['df.groupBy','df.groupBy("${group}").agg(${aggregation})','分组聚合'],
 ['df.orderBy','df.orderBy(f.col("${column}").desc())','排序'],
 ['spark.sql','spark.sql("""${query}""")','查询SQL'],
];
const options = snippets.map(([label,template,detail])=>snippetCompletion(template,{label,detail,type:'function'}));
options.push(...['def','return','import','from','as','if','elif','else','for','while','try','except','finally','raise','class','None','True','False','with','lambda','and','or','not','in','assert'].map(label=>({label,type:'keyword'})));

function create(textarea) {
 const host=document.createElement('div');host.className='browser-code-editor';
 textarea.insertAdjacentElement('afterend',host);textarea.hidden=true;
 const editable=new Compartment();
 const mode=value=>[EditorState.readOnly.of(value),EditorView.editable.of(!value)];
 const view=new EditorView({parent:host,state:EditorState.create({doc:textarea.value,extensions:[
  basicSetup,python(),oneDark,keymap.of([indentWithTab]),editable.of(mode(textarea.readOnly)),
  autocompletion({override:[context=>{const word=context.matchBefore(/[\w.]+/);if(!word&&!context.explicit)return null;return{from:word?word.from:context.pos,options,validFor:/[\w.]*/};}]}),
  EditorView.contentAttributes.of({'aria-label':'Python 代码编辑器','spellcheck':'false'}),
  EditorView.updateListener.of(update=>{if(update.docChanged){textarea.value=update.state.doc.toString();textarea.dispatchEvent(new Event('input',{bubbles:true}));}}),
  EditorView.theme({'&':{fontSize:'13px',minHeight:'330px'},'.cm-scroller':{fontFamily:'Consolas, "Microsoft YaHei", monospace',maxHeight:'600px'},'.cm-content':{padding:'16px 0'},'.cm-gutters':{paddingRight:'8px'}}),
 ]})});
 const observer=new MutationObserver(()=>view.dispatch({effects:editable.reconfigure(mode(textarea.readOnly))}));
 observer.observe(textarea,{attributes:true,attributeFilter:['readonly']});
 return {getValue:()=>view.state.doc.toString(),focus:()=>view.focus(),search:()=>openSearchPanel(view),
  showDiagnostics(items=[]){view.dispatch(setDiagnostics(view.state,items.filter(item=>Number.isInteger(item.line)&&item.line>0).map(item=>{const line=view.state.doc.line(Math.min(item.line,view.state.doc.lines));return{from:line.from,to:line.to,severity:['error','warning','info'].includes(item.severity)?item.severity:'info',message:String(item.message||'')};})));},
  destroy(){observer.disconnect();view.destroy();host.remove();textarea.hidden=false;},
 };
}
window.SparkTutorEditor={create};
