async function(platform, action, arg) {
  if(platform!=='51cto' || location.origin!=='https://blog.51cto.com')return {ok:false,error:'请在纸飞机内完成51CTO登录'};
  const numeric=v=>(typeof v!=='number'||Number.isSafeInteger(v)) && /^[1-9][0-9]{0,39}$/.test(String(v||''));
  const state={stage:'session',submitted:false,observed:{}};
  const type=v=>v===null?'null':Array.isArray(v)?'array':typeof v;
  function shape(value) {
    const result=[];
    function visit(v,path,depth) {
      if(result.length>=30)return;
      result.push(path+':'+type(v));
      if(depth>=2 || !v || typeof v!=='object')return;
      for(const key of Object.keys(v).slice(0,30)) {
        if(!/^[a-zA-Z0-9_]{1,32}$/.test(key) || /token|secret|cookie|csrf|session|authorization|password|credential|sign|policy/i.test(key))continue;
        visit(v[key],path?path+'.'+key:key,depth+1);
      }
    }
    visit(value,'$',0);return result;
  }
  function validationReasons(value) {
    // Emit only fixed field/reason labels. Rejected values, URLs and arbitrary
    // platform messages never leave the browser, even when they contain secrets.
    const result=[];
    const fields=['cover','title','content','text','tags','tag','abstract','cate_id','blog_type','copy_code','is_old'];
    const reason=s=>/重复|已存在|duplicate|already.exists/i.test(s)?'duplicate':
      /不能为空|必填|未填写|required|missing|not.empty/i.test(s)?'required':
      /长度|字数|过长|过短|too.long|too.short|max(?:imum)?.*length|min(?:imum)?.*length/i.test(s)?'length':
      /url|链接|地址/i.test(s)?'invalid_url':
      /字符串|数组|类型|must.be.*(?:string|array|number)|invalid.type/i.test(s)?'type':
      /敏感|违规|禁止|不允许|不可发布|不支持发布|cannot.publish/i.test(s)?'restricted':
      /格式|不正确|不合法|错误|invalid/i.test(s)?'format':'invalid';
    const add=(field,message)=>{
      if(typeof message!=='string'||!message)return;
      const item={field,reason:reason(message.slice(0,2000))};
      if(!result.some(v=>v.field===item.field&&v.reason===item.reason))result.push(item);
    };
    for(const field of fields) {
      const v=value?.[field];
      if(typeof v==='string')add(field,v);
      else if(Array.isArray(v))for(const message of v.slice(0,3))add(field,message);
    }
    const messages=Array.isArray(value)?[value[1]]:[value?.message,value?.msg,value?.error,value?.errMsg,value?.data];
    for(const message of messages) {
      if(typeof message!=='string')continue;
      const s=message.slice(0,2000);
      const field=/cover|封面/i.test(s)?'cover':/title|标题/i.test(s)?'title':
        /abstract|摘要/i.test(s)?'abstract':/content|正文|内容|text/i.test(s)?'content':
        /分类|category|cate_id/i.test(s)?'cate_id':/tags?|标签/i.test(s)?'tags':
        /实名|认证|开通博客|绑定手机/i.test(s)?'account':null;
      if(field)add(field,s);
    }
    return result.slice(0,12);
  }
  function safeChineseMessage(value) {
    for(const candidate of [value?.msg,value?.message,...([0,'0'].includes(value?.status)?[value?.data]:[])]) {
      if(typeof candidate!=='string')continue;
      const message=candidate.trim().replace(/\s+/g,' ');
      if(!message || message.length>120 || !/^[\p{Script=Han} ，。！？；：（）【】《》、“”‘’…·]+$/u.test(message))continue;
      if(/令牌|密钥|口令|密码|凭证|签名|会话|授权码/.test(message))continue;
      if([arg?.title,arg?.markdown,arg?.content,arg?.summary].some(v=>typeof v==='string'&&v.length>=2&&message.includes(v)))continue;
      return message;
    }
    return '';
  }
  function parseFormLiteral(source,start) {
    // Data grammar only. No eval, Function, getters, references or calls.
    let pos=start,nodes=0;
    const syntax={single_quotes:false,unquoted_keys:false,comments:false,trailing_commas:false};
    const fail=code=>{const error=Error(code);error.parseCode=code;error.parseOffset=Math.min(pos-start,1000000);error.syntax=syntax;throw error;};
    const limit=()=>{if(pos-start>1000000||++nodes>20000)fail('limit');};
    function space() {
      while(pos<source.length) {
        if(/\s/.test(source[pos])){pos++;continue;}
        if(source.slice(pos,pos+2)==='//') {
          syntax.comments=true;pos+=2;while(pos<source.length&&!/[\r\n]/.test(source[pos]))pos++;
        } else if(source.slice(pos,pos+2)==='/*') {
          syntax.comments=true;const end=source.indexOf('*/',pos+2);if(end<0)fail('unterminated_comment');pos=end+2;
        } else break;
        limit();
      }
      if(pos-start>1000000)fail('limit');
    }
    function string() {
      const quote=source[pos++];if(quote==="'")syntax.single_quotes=true;
      let result='';
      while(pos<source.length) {
        if(pos-start>1000000)fail('limit');
        const c=source[pos++];
        if(c===quote)return result;
        if(c==='\n'||c==='\r')fail('unescaped_newline');
        if(c!=='\\'){result+=c;continue;}
        if(pos>=source.length)fail('unterminated_string');
        const escaped=source[pos++];
        const simple={'\\':'\\',"'":"'",'"':'"','/':'/','n':'\n','r':'\r','t':'\t','b':'\b','f':'\f','v':'\v'};
        if(Object.hasOwn(simple,escaped))result+=simple[escaped];
        else if(escaped==='\n')continue;
        else if(escaped==='\r'){if(source[pos]==='\n')pos++;}
        else if(escaped==='0'&&!/[0-9]/.test(source[pos]||''))result+='\0';
        else if(escaped==='x'||escaped==='u') {
          let count=escaped==='x'?2:4,hex;
          if(escaped==='u'&&source[pos]==='{') {
            pos++;const end=source.indexOf('}',pos);if(end<0||end-pos<1||end-pos>6)fail('invalid_escape');
            hex=source.slice(pos,end);pos=end+1;
            if(!/^[0-9a-fA-F]+$/.test(hex)||parseInt(hex,16)>0x10ffff)fail('invalid_escape');
            result+=String.fromCodePoint(parseInt(hex,16));
          } else {
            hex=source.slice(pos,pos+count);if(hex.length!==count||!/^[0-9a-fA-F]+$/.test(hex))fail('invalid_escape');
            pos+=count;result+=String.fromCharCode(parseInt(hex,16));
          }
        } else fail('unsupported_escape');
        if(pos-start>1000000)fail('limit');
      }
      fail('unterminated_string');
    }
    function atom(depth=0) {
      limit();if(depth>20)fail('depth');space();
      const c=source[pos];
      if(c==='"'||c==="'")return string();
      if(c==='{'||c==='[') {
        const object=c==='{',close=object?'}':']',result=object?Object.create(null):[];
        pos++;space();if(source[pos]===close){pos++;return result;}
        while(pos<source.length) {
          let key;
          if(object) {
            space();
            if(source[pos]==='"'||source[pos]==="'")key=string();
            else {const m=/^[A-Za-z_$][A-Za-z0-9_$]*/.exec(source.slice(pos));if(!m)fail('unsupported_key');key=m[0];pos+=key.length;syntax.unquoted_keys=true;}
            if(['__proto__','prototype','constructor'].includes(key)||Object.hasOwn(result,key))fail('duplicate_or_unsafe_key');
            space();if(source[pos++]!==':')fail('expected_colon');
          }
          const item=value(depth+1);if(object)result[key]=item;else result.push(item);
          space();if(source[pos]===close){pos++;return result;}
          if(source[pos++]!==',')fail('expected_separator');space();
          if(source[pos]===close){syntax.trailing_commas=true;pos++;return result;}
        }
        fail('unterminated_container');
      }
      const primitive=/^(?:true|false|null)(?![A-Za-z0-9_$])/.exec(source.slice(pos));
      if(primitive){pos+=primitive[0].length;return {true:true,false:false,null:null}[primitive[0]];}
      const number=/^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?/.exec(source.slice(pos));
      if(number) {
        pos+=number[0].length;const result=Number(number[0]);
        if(!Number.isFinite(result)||(Number.isInteger(result)&&!Number.isSafeInteger(result)))fail('unsafe_number');
        return result;
      }
      fail('unsupported_value');
    }
    function comparison(depth) {
      let result=atom(depth);space();
      if(source.slice(pos,pos+7)==='.length') {
        if(typeof result!=='string'&&!Array.isArray(result))fail('unsupported_member');
        pos+=7;result=result.length;space();
      }
      const operator=/^(===|!==|==|!=)/.exec(source.slice(pos));
      if(operator) {
        pos+=operator[0].length;const right=atom(depth+1);space();
        if(!['string','number','boolean'].includes(typeof result)||typeof result!==typeof right)fail('unsupported_comparison');
        const equal=result===right;result=operator[0][0]==='!'?!equal:equal;
      }
      return result;
    }
    function logicalAnd(depth) {
      let result=comparison(depth);space();
      while(source.slice(pos,pos+2)==='&&') {
        if(result!==null&&!['boolean','number','string'].includes(typeof result))fail('unsupported_condition');
        pos+=2;const right=comparison(depth+1);result=result?right:result;space();syntax.constant_conditionals=true;
      }
      return result;
    }
    function value(depth=0) {
      let result=logicalAnd(depth);space();
      while(source.slice(pos,pos+2)==='||') {
        if(result!==null&&!['boolean','number','string'].includes(typeof result))fail('unsupported_condition');
        pos+=2;const right=logicalAnd(depth+1);result=result?result:right;space();syntax.constant_conditionals=true;
      }
      if(source[pos]==='?') {
        if(result!==null&&!['boolean','number','string'].includes(typeof result))fail('unsupported_condition');
        syntax.constant_conditionals=true;pos++;
        const yes=value(depth+1);space();if(source[pos++]!==':')fail('expected_conditional_colon');
        const no=value(depth+1);result=result?yes:no;
      }
      return result;
    }
    const form=value();const end=pos;space();
    const nextDeclaration=/^(?:var|let|const)\s/.test(source.slice(pos));
    if(pos<source.length&&source[pos]!==';'&&source[pos]!==','&&!nextDeclaration)fail('expression_suffix');
    if(!form||Array.isArray(form)||typeof form!=='object')fail('root_not_object');
    let parse='js_literal';try{JSON.parse(source.slice(start,end));parse='json';}catch(e){}
    return {form,parse,syntax};
  }
  function literalContext(source,start,offset) {
    const known=new Set(['title','content','pid','cate_id','custom_id','tag','abstract','banner_type','img_urls','blog_type','copy_code','is_hide','top_time','is_comment','is_old','blog_id','did','work_id','class_id','subjectId','import_type','invite_code','raffle','orig','replace','split','join','trim','val','value','querySelector','getElementById','document','window','JSON','parse','length','$']);
    const tokens=[];let p=start;
    while(p<source.length&&p-start<Math.min(offset+180,1000000)) {
      const at=p-start,c=source[p];let token;
      if(/\s/.test(c)){p++;continue;}
      if(c==='"'||c==="'") {const quote=c;p++;while(p<source.length){if(source[p]==='\\'){p+=2;continue;}if(source[p++]===quote)break;}const raw=source.slice(at+start,p);token=/^['"]-?[0-9]['"]$/.test(raw)?'enum:'+raw.slice(1,-1):'string';}
      else if(source.slice(p,p+2)==='//'){const end=source.indexOf('\n',p+2);p=end<0?source.length:end;token='comment';}
      else if(source.slice(p,p+2)==='/*'){const end=source.indexOf('*/',p+2);p=end<0?source.length:end+2;token='comment';}
      else if(c==='/'){p++;while(p<source.length){if(source[p]==='\\'){p+=2;continue;}if(source[p++]==='/')break;}while(/[a-z]/i.test(source[p]||''))p++;token='regex';}
      else if(/[A-Za-z_$]/.test(c)){const match=/^[A-Za-z_$][A-Za-z0-9_$]*/.exec(source.slice(p))[0];p+=match.length;token=known.has(match)?match:'identifier';}
      else if(/[0-9]/.test(c)){while(/[0-9.eE+-]/.test(source[p]||''))p++;const raw=source.slice(at+start,p);token=/^[0-9]$/.test(raw)?'enum:'+raw:'number';}
      else{p++;token='{}[]().,:;+-?='.includes(c)?c:'symbol';}
      tokens.push({at,token});
    }
    const index=tokens.findIndex(t=>t.at>=offset);return tokens.slice(Math.max(0,index-8),Math.max(0,index-8)+22).map(t=>t.token);
  }
  function initialForm(doc) {
    // Find declarations outside strings/comments so article text cannot be a
    // second template. Never read or execute a page global.
    const declarations=[];
    for(const node of doc.querySelectorAll('script:not([src])')) {
      const source=node.textContent||'';
      for(let i=0;i<source.length;i++) {
        const c=source[i],pair=source.slice(i,i+2);
        if(pair==='//'){const end=source.indexOf('\n',i+2);i=end<0?source.length:end;continue;}
        if(pair==='/*'){const end=source.indexOf('*/',i+2);i=end<0?source.length:end+1;continue;}
        if(c==='"'||c==="'"||c==='`') {
          const quote=c;
          for(i++;i<source.length;i++){if(source[i]==='\\')i++;else if(source[i]===quote)break;}
          continue;
        }
        if((i===0||!/[A-Za-z0-9_$]/.test(source[i-1]))&&/[vlc]/.test(c)) {
          const match=/^(?:var|let|const)\s+submitForm\s*=\s*/.exec(source.slice(i));
          if(match)declarations.push({source,start:i+match[0].length});
        }
      }
    }
    if(!declarations.length)return {form:null,parse:'missing'};
    if(declarations.length!==1)return {form:null,parse:'unrecognized',parse_error:'ambiguous_declaration'};
    try{return parseFormLiteral(declarations[0].source,declarations[0].start);}
    catch(e){return {form:null,parse:'unrecognized',parse_error:e.parseCode||'invalid_literal',parse_offset:e.parseOffset||0,syntax:e.syntax,context:literalContext(declarations[0].source,declarations[0].start,e.parseOffset||0)};}
  }
  function initialFormMetadata(doc) {return formMetadata(initialForm(doc));}
  function formMetadata(parsed) {
    const {form,parse,parse_error,parse_offset,syntax,context}=parsed;
    const diagnostic={submit_form_parse:parse,...(parse_error?{submit_form_error:parse_error,submit_form_error_offset:parse_offset||0}:{}),...(syntax?{submit_form_syntax:syntax}:{}),...(context?{submit_form_context:context}:{})};
    if(!form)return diagnostic;
    const fields=Object.keys(form).filter(k=>/^[_A-Za-z][_A-Za-z0-9-]{0,63}$/.test(k) &&
      !/csrf|token|secret|cookie|session|password|credential|sign|policy/i.test(k)).slice(0,60).map(name=>({name,type:type(form[name])}));
    const options={};
    for(const name of ['is_old','blog_type','copy_code','is_hide','top_time','is_comment','banner_type','import_type','raffle']) {
      if(!Object.hasOwn(form,name))continue;
      const value=form[name];
      if(value===''||value===null||typeof value==='boolean'||
        (typeof value==='number'&&Number.isInteger(value)&&value>=-1&&value<=9)||
        (typeof value==='string'&&/^(?:-1|[0-9])$/.test(value)))options[name]=value;
    }
    for(const name of ['did','blog_id','pid','cate_id','custom_id']) {
      if(!Object.hasOwn(form,name))continue;
      const value=form[name];
      options[name+'_blank']=value===''||value===0||value==='0'||value===null;
    }
    return {...diagnostic,submit_form_fields:fields,submit_form_options:options};
  }
  function runtimeForm() {
    if(location.pathname!=='/blogger/publish'||location.search||location.hash)return {form:null,parse:'wrong_page'};
    const descriptor=Object.getOwnPropertyDescriptor(globalThis,'submitForm');
    if(!descriptor||!Object.hasOwn(descriptor,'value'))return {form:null,parse:'not_data'};
    let nodes=0,chars=0;
    function copy(value,depth=0) {
      if(++nodes>20000||depth>20)throw Error('limit');
      if(value===null||typeof value==='boolean')return value;
      if(typeof value==='number'&&Number.isFinite(value)&&(!Number.isInteger(value)||Number.isSafeInteger(value)))return value;
      if(typeof value==='string'){chars+=value.length;if(chars>1000000)throw Error('limit');return value;}
      if(!value||typeof value!=='object')throw Error('not_data');
      const array=Array.isArray(value),proto=Object.getPrototypeOf(value);
      if(!array&&proto!==Object.prototype&&proto!==null)throw Error('not_plain');
      const result=array?[]:Object.create(null);
      for(const [key,item] of Object.entries(Object.getOwnPropertyDescriptors(value))) {
        if(!item.enumerable)continue;
        if(!Object.hasOwn(item,'value')||['__proto__','constructor','prototype'].includes(key))throw Error('accessor_or_unsafe');
        if(array&&!/^(?:0|[1-9][0-9]*)$/.test(key))throw Error('array_property');
        result[key]=copy(item.value,depth+1);
      }
      return result;
    }
    try {
      const form=copy(descriptor.value);
      if(!form||typeof form!=='object'||Array.isArray(form))return {form:null,parse:'not_object'};
      return {form,parse:'runtime_data'};
    }catch(e){return {form:null,parse:'unsafe_data',parse_error:e.message};}
  }
  function draftForm(auth,article) {
    const {form}=initialForm(auth.doc);
    Object.assign(state.observed,initialFormMetadata(auth.doc));
    if(!form)throw problem('page_structure','官方新建草稿表单无法安全解析，正文尚未提交');
    const optionNames=['is_old','blog_type','copy_code','is_hide','top_time','is_comment','banner_type','import_type'];
    const ids=['did','blog_id','pid','cate_id','custom_id','subjectId','wordid','work_id','class_id'];
    const textNames=['title','content','abstract','tag','invite_code','raffle','orig'];
    const known=new Set([...optionNames,...ids,...textNames,'img_urls',auth.csrfParam]);
    if(Object.keys(form).some(k=>!known.has(k)))throw problem('page_structure','官方草稿表单包含未识别字段，正文尚未提交');
    const result={};
    for(const name of optionNames) {
      if(!Object.hasOwn(form,name))continue;
      const value=form[name];
      if(!(value===''||value===null||typeof value==='boolean'||
        (typeof value==='number'&&Number.isInteger(value)&&value>=-1&&value<=9)||
        (typeof value==='string'&&/^(?:-1|[0-9])$/.test(value))))throw problem('page_structure','官方草稿选项格式未能核实');
      result[name]=value??'';
    }
    // Official init overwrites is_old from this input; 1 and 2 are WuKong.
    const mode=auth.doc.querySelector('input#is_old')?.value;
    if(!/^[0-9]$/.test(String(mode))||['1','2'].includes(String(mode)))throw problem('page_structure','当前官方表单不是可核实的Markdown编辑器，正文尚未提交');
    result.is_old=mode;
    for(const name of ids)if(Object.hasOwn(form,name))result[name]=typeof form[name]==='number'?0:'';
    result.did='';result.blog_id='';
    for(const name of textNames)if(Object.hasOwn(form,name))result[name]='';
    result.title=article.title;result.content=draftContent(article);result.abstract=article.summary||'';
    if(Object.hasOwn(form,'img_urls')&&!Array.isArray(form.img_urls))throw problem('page_structure','官方封面列表格式未能核实');
    result.img_urls=[];
    if(article.cover){result.banner_type='1';result.img_urls=[imageUrl(article.cover)];}
    else if(![0,'0',4,'4'].includes(result.banner_type))throw problem('page_structure','官方表单需要封面，正文尚未提交');
    if(result.import_type==='')result.import_type='-1';
    return result;
  }
  function pageMetadata(doc) {
    // Static asset paths and form structure only; never form values, script
    // contents, query strings, tokens or account-specific profile URLs.
    const scriptSources=[];
    for(const node of doc.querySelectorAll('script[src]')) {
      try {
        const u=new URL(node.getAttribute('src'),location.origin);
        if(u.protocol==='https:' && !u.port && !u.username && !u.password &&
          (u.hostname==='51cto.com'||u.hostname.endsWith('.51cto.com')) &&
          /^[A-Za-z0-9_./-]{1,240}\.js$/.test(u.pathname)) {
          const source=u.origin+u.pathname;
          if(!scriptSources.includes(source))scriptSources.push(source);
        }
      } catch(e) {}
      if(scriptSources.length>=40)break;
    }
    const formFields=[];
    for(const node of doc.querySelectorAll('input[name],textarea[name],select[name]')) {
      const name=node.getAttribute('name')||'';
      if(!/^[_A-Za-z][_A-Za-z0-9-]{0,63}$/.test(name) || /csrf|token|secret|cookie|session|password|credential|sign|policy/i.test(name))continue;
      const tag=node.tagName.toLowerCase();
      const rawType=tag==='input'?(node.getAttribute('type')||'text'):tag;
      const field={name,type:['text','hidden','textarea','select','checkbox','radio','number','file'].includes(rawType)?rawType:'other',required:node.hasAttribute('required')};
      if(!formFields.some(v=>v.name===name&&v.type===field.type))formFields.push(field);
      if(formFields.length>=60)break;
    }
    const csrfInput=doc.querySelector('input#csrf');
    const csrfMeta=doc.querySelector('meta[name="csrf-token"]');
    const csrfParam=doc.querySelector('meta[name="csrf-param"]');
    return {script_sources:scriptSources,form_fields:formFields,...initialFormMetadata(doc),runtime_form:formMetadata(runtimeForm()),
      csrf_input_present:!!csrfInput,
      csrf_input_matches_meta:!!csrfInput&&!!csrfMeta&&csrfInput.value===csrfMeta.content,
      csrf_param_matches_meta:!!csrfInput&&!!csrfParam&&csrfInput.getAttribute('name')===csrfParam.content};
  }
  function observeRejection(value) {
    const safeMessage=safeChineseMessage(value);
    if(safeMessage)state.observed.safe_message=safeMessage;
    const validation=validationReasons(value);
    if(validation.length)state.observed.validation=validation;
    return validation;
  }
  function category(value) {
    // Inspect only known error fields; never echo remote text or submitted content.
    const values=Array.isArray(value)?[value[1]]:[value?.message,value?.msg,value?.error,value?.errMsg];
    const s=values.filter(v=>typeof v==='string').join(' ').slice(0,2000);
    if(/csrf|cross.site|request verification|验证请求/i.test(s))return 'csrf';
    if(/captcha|验证码|人机验证/i.test(s))return 'captcha';
    if(/unauthori[sz]ed|未登录|登录失效|请.*登录|token/i.test(s))return 'authentication';
    if(/forbidden|禁言|锁定|权限|无权/i.test(s))return 'permission';
    if(/频繁|too many|rate.limit/i.test(s))return 'rate_limit';
    return observeRejection(value).length?'validation':'response_rejected';
  }
  function problem(kind,message) {const e=Error(message);e.safe=true;e.kind=kind;return e;}
  function failure(e) {
    const kind=e.safe?e.kind:(e.name==='AbortError'||e.name==='TimeoutError'?'timeout':'transport');
    const diagnostic={stage:state.stage,category:kind,submission:state.submitted?'attempted':'not_started',...state.observed};
    const fields=diagnostic.fields?.join(',')||'';
    const validation=(diagnostic.validation||[]).map(v=>v.field+':'+v.reason).join(',');
    const suffix=(diagnostic.safe_message?' 平台提示：'+diagnostic.safe_message:'')+(validation?' 校验 '+validation:'')+(diagnostic.http?' HTTP '+diagnostic.http:'')+(diagnostic.code!==undefined?' code='+diagnostic.code:'')+(diagnostic.status!==undefined?' status='+diagnostic.status:'')+(fields?' 字段 '+fields:'');
    const summary=(e.safe?e.message:'请求或响应处理未完成')+' ['+state.stage+'/'+kind+']'+suffix;
    return {ok:false,error:summary,uncertain:state.submitted,diagnostic};
  }
  async function jsonResponse(r) {
    state.observed.http=r.status;
    let raw;
    try {raw=await r.text();}catch(e){throw problem('response_read','响应读取中断');}
    let value;
    try {value=JSON.parse(raw);}catch(e) {
      state.observed.response_type=/^\s*</.test(raw)?'html':'non_json';
      state.observed.response_length=raw.length;
      const reason=category({message:raw});
      throw problem(reason==='response_rejected'?(!r.ok?'http':'invalid_response'):reason,'平台未返回JSON（'+state.observed.response_type+'）');
    }
    state.observed.fields=shape(value);
    for(const k of ['code','status']) {
      if((typeof value?.[k]==='number'&&Number.isFinite(value[k])) || (typeof value?.[k]==='string'&&/^-?[0-9]{1,9}$/.test(value[k])))state.observed[k]=value[k];
    }
    if(Array.isArray(value) && Number.isFinite(value[0]))state.observed.code=value[0];
    if(!r.ok) {const reason=category(value);throw problem(reason==='response_rejected'?'http':reason,'平台请求失败');}
    return value;
  }

  async function page(path) {
    const r=await fetch(path,{credentials:'include',cache:'no-store',signal:AbortSignal.timeout(30000)});
    if(!r.ok)throw problem('validation','51CTO页面请求失败');
    const url=new URL(r.url,location.origin);
    if(url.origin==='https://home.51cto.com')return {signedOut:true};
    if(url.origin!==location.origin)throw problem('validation','51CTO页面地址未能核实');
    return {url,doc:new DOMParser().parseFromString(await r.text(),'text/html')};
  }
  async function session() {
    const p=await page('/blogger/publish');
    if(p.signedOut)return {account:null};
    if(p.url.pathname!=='/blogger/publish')throw problem('page_structure','官方新建编辑页地址未能核实');
    const link=p.doc.querySelector('li.more.user a[href]');
    if(!link?.querySelector('img'))throw problem('validation','51CTO账号页面结构未能核实');
    const url=new URL(link.getAttribute('href'),location.origin);
    if(url.origin!==location.origin || !/^\/[a-zA-Z0-9_-]+\/?$/.test(url.pathname))throw problem('validation','51CTO账号地址无效');
    const id=url.pathname.split('/')[1];
    const csrfInput=p.doc.querySelector('input#csrf');
    const csrf=csrfInput?.value||p.doc.querySelector('meta[name="csrf-token"]')?.content;
    if(!csrf)throw problem('validation','51CTO登录校验信息缺失');
    const csrfParam=csrfInput?.getAttribute('name')||p.doc.querySelector('meta[name="csrf-param"]')?.content||'_csrf';
    if(!/^[_a-zA-Z][_a-zA-Z0-9-]{0,63}$/.test(csrfParam))throw problem('page_structure','51CTO校验参数名无效');
    state.observed.auth_page='/blogger/publish';
    state.observed.auth_param=csrfParam;
    if(action==='diagnose')Object.assign(state.observed,pageMetadata(p.doc));
    return {account:{id,name:id},csrf,csrfParam,doc:p.doc};
  }
  async function post(path,values,auth) {
    state.stage=path==='/blogger/draft'?'draft-submit':'upload-request';
    if(path==='/blogger/draft')state.submitted=true;
    const body=new URLSearchParams();
    for(const [name,value] of Object.entries(values)) {
      if(Array.isArray(value))for(const item of value)body.append(name+'[]',String(item));
      else body.append(name,value==null?'':String(value));
    }
    body.set(auth.csrfParam,auth.csrf);
    const r=await fetch(path,{method:'POST',credentials:'include',redirect:'error',signal:AbortSignal.timeout(30000),
      headers:{'content-type':'application/x-www-form-urlencoded; charset=UTF-8','x-requested-with':'XMLHttpRequest'},
      body:body.toString()});
    return jsonResponse(r);
  }
  function imageUrl(value) {
    const u=new URL(value);
    if(u.protocol!=='https:' || u.port || u.username || u.password || !u.hostname.endsWith('.51cto.com'))throw problem('validation','51CTO图片地址未能核实');
    return u.href;
  }
  function draftContent(article) {
    let content=article.markdown;
    if(article.cover) {const cover=imageUrl(article.cover);if(!content.includes(cover))content='![封面]('+cover+')\n\n'+content;}
    return content;
  }
  async function readDraft(id,article,auth) {
    const url='https://blog.51cto.com/blogger/draft/'+id;
    const observed={};
    try {
      const saved=await page('/blogger/draft/'+id);
      if(saved.signedOut || saved.url.pathname!=='/blogger/draft/'+id)throw problem('validation','原草稿编号未能核实');
      const {form}=initialForm(saved.doc);
      Object.assign(observed,initialFormMetadata(saved.doc));
      observed.inputs=[...saved.doc.querySelectorAll('input[id],input[name]')].filter(n=>['did','blog_id','is_old'].includes(n.id)||['did','blog_id','is_old'].includes(n.name)).map(n=>({id:n.id,name:n.name,type:n.type,value:/^[0-9]{0,39}$/.test(n.value)?n.value:'[other]'}));
      if(form) {
        observed.ids=Object.fromEntries(['did','blog_id'].map(key=>[key,{type:type(form[key]),value:/^[0-9]{0,39}$/.test(String(form[key]??''))?form[key]:'[other]'}]));
        observed.matches={title:form.title===article.title,content:form.content===draftContent(article),cover:Array.isArray(form.img_urls)&&form.img_urls.length===1&&form.img_urls[0]===article.cover};

        // Official editorMain init replaces the template's blog_id with this
        // hidden input. A private draft template initially repeats did in both
        // fields, while the hidden published-blog ID is empty.
        const blogInputs=saved.doc.querySelectorAll('input#blog_id');
        const blank=value=>[undefined,null,'',0,'0'].includes(value);
        const hiddenBlogInput=blogInputs.length===1&&blogInputs[0].type==='hidden';
        const effectiveBlogId=hiddenBlogInput?blogInputs[0].value:form.blog_id;
        const templateIdMatches=blank(form.blog_id)||(String(form.blog_id)===id&&hiddenBlogInput&&blank(effectiveBlogId));
        observed.effective_blog_id_blank=blank(effectiveBlogId);
        if(!numeric(form.did)||String(form.did)!==id||(blogInputs.length>0&&!hiddenBlogInput)||!templateIdMatches||!blank(effectiveBlogId))throw problem('validation','原草稿编号或私有草稿类型未能核实');
        if(['1','2'].includes(String(form.is_old)))throw problem('validation','原草稿编辑格式未能核实');
        if(form.title!==article.title||form.content!==draftContent(article))throw problem('validation','原草稿标题、正文或图片回读不一致');
        if(article.cover&&(![1,'1'].includes(form.banner_type)||!Array.isArray(form.img_urls)||form.img_urls.length!==1||imageUrl(form.img_urls[0])!==imageUrl(article.cover)))throw problem('validation','原草稿封面回读不一致');
      } else {
        if(saved.doc.querySelector('input[name="title"]')?.value!==article.title ||
           saved.doc.querySelector('textarea[name="content"]')?.value!==draftContent(article))throw problem('validation','原草稿标题、正文或图片回读不一致');
        if(article.cover)throw problem('validation','原草稿封面结构未能核实');
      }
      if((await session()).account?.id!==auth.account.id)throw problem('validation','原草稿所属账号未能核实');
      return {id,url};
    } catch(e) {return {id,url,incomplete:true,reason:e.message,observed};}
  }
  try {
    const auth=await session();
    if(action==='identity')return {ok:true,data:auth.account};
    if(action==='diagnose') {
      let preflight={ready:false};
      if(auth.account) {
        try {
          const values=draftForm(auth,{title:'PaperPlane form preflight',markdown:'Read-only structure check',summary:''});
          preflight={ready:true,fields:Object.keys(values).filter(k=>k!==auth.csrfParam),existing_ids_cleared:values.did===''&&values.blog_id===''};
        }catch(e){preflight={ready:false,category:e.safe?e.kind:'transport',reason:e.safe?e.message:'表单结构无法核实'};}
      }
      return {ok:true,data:{authenticated:!!auth.account,...state.observed,draft_preflight:preflight}};
    }
    if(!auth.account)throw problem('validation','请先登录51CTO');
    if(action==='upload') {
      state.stage='upload-preflight';
      if(!arg || !['png','jpg','jpeg','gif','webp'].includes(arg.extension))throw problem('validation','图片格式无效');
      const bytes=Uint8Array.from(atob(arg.base64),c=>c.charCodeAt(0));
      if(!bytes.length || bytes.length>5000000)throw problem('validation','图片为空或超过5MB');
      const sign=await post('/getUploadSign',{upload_type:'image'},auth);
      if(sign.code!==0 || typeof sign.data?.sign!=='string')throw problem('validation','51CTO未授予图片上传权限');
      const config=await post('/getUploadConfig',{upload_type:'image',upload_sign:sign.data.sign,ext:arg.mime,name:'image.'+arg.extension},auth);
      if(config.code!==0 || !config.data?.fields)throw problem('validation','51CTO上传配置无效');
      const target=new URL(config.data.url),fields=config.data.fields;
      if(target.protocol!=='https:' || target.port || target.username || target.password ||
         !(target.hostname.endsWith('.myqcloud.com')||target.hostname.endsWith('.51cto.com')))throw problem('validation','51CTO上传主机未能核实');
      if(typeof fields.key!=='string' || !/^[a-zA-Z0-9_./-]+$/.test(fields.key) || fields.key.startsWith('/') || fields.key.split('/').includes('..'))throw problem('validation','51CTO图片路径无效');
      const form=new FormData();
      for(const key of ['key','policy','x-amz-algorithm','x-amz-signature','x-amz-credential','X-Amz-Date']) {
        if(typeof fields[key]!=='string' || !fields[key])throw problem('validation','51CTO上传凭证不完整');
        form.append(key,fields[key]);
      }
      form.append('Content-Type',arg.mime);form.append('file',new Blob([bytes],{type:arg.mime}),'image.'+arg.extension);
      const r=await fetch(target.href,{method:'POST',credentials:'omit',redirect:'error',signal:AbortSignal.timeout(60000),body:form});
      if(!r.ok)throw problem('validation','51CTO图片上传失败，正文尚未提交');
      return {ok:true,data:imageUrl('https://s2.51cto.com/'+fields.key)};
    }
    if(action==='draft-read') {
      state.stage='draft-read';
      const id=String(arg?.draft_id||'');
      if(!numeric(id) || !arg?.title?.trim() || !arg?.markdown?.trim())throw problem('validation','缺少原草稿编号或任务正文，未执行核对');
      if(auth.account.id!==arg.account_id)throw problem('validation','51CTO账号已变化，请重新核对');
      return {ok:true,data:await readDraft(id,arg,auth)};
    }
    if(action==='save') {
      state.stage='preflight';
      if(auth.account.id!==arg?.account_id)throw problem('validation','51CTO账号已变化，请重新核对');
      if(!arg.title?.trim() || !arg.markdown?.trim() || arg.markdown.includes('/media/'))throw problem('validation','标题、正文或图片未准备好');
      const values=draftForm(auth,arg);
      const value=await post('/blogger/draft',values,auth);
      state.stage='draft-receipt';
      if(![1,'1'].includes(value?.status))throw problem(category(value),'51CTO未确认草稿保存（status '+String(value?.status).replace(/[^0-9-]/g,'').slice(0,10)+'）');
      if(!numeric(value.data?.did))throw problem('missing_receipt','未收到有效草稿编号，请核对草稿箱，不要重复发送');
      return {ok:true,data:await readDraft(String(value.data.did),arg,auth)};
    }
    return {ok:false,error:'51CTO此操作尚未接通，请在平台核对后登记结果'};
  } catch(e) {return failure(e);}
}
