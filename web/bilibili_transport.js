async function(platform, action, arg) {
  // Protocol sources (checked 2026-10-08):
  // https://github.com/wechatsync/Wechatsync/blob/v2/packages/core/src/adapters/platforms/bilibili.ts
  // Official editor: https://s1.hdslb.com/bfs/static/article-text/static/js/0.ef74f597f144c916d84b.js
  // Current official York editor: https://s1.hdslb.com/bfs/static/sunflower/read-editor/assets/index-CEqA4Tr6.js
  // The draft endpoint is distinct from article/submit; this transport never publishes.
  if(platform!=='bilibili-article' || location.origin!=='https://member.bilibili.com')return {ok:false,error:'请在纸飞机内完成B站登录'};
  const identifier=value=>{
    if(typeof value==='number' && !Number.isSafeInteger(value))return '';
    const text=String(value??'');return /^[1-9][0-9]{0,39}$/.test(text)?text:'';
  };
  const contentText=value=>typeof value==='string'?value.replace(/\r\n/g,'\n').trim():'';
  function shape(value, prefix='root', depth=0) {
    // Report schema only. Never expose article text, URLs, token or credential values.
    const kind=value===null?'null':Array.isArray(value)?'array':typeof value;
    if(kind!=='object' && kind!=='array')return [prefix+':'+kind];
    const keys=Object.keys(value).filter(k=>!/(?:token|secret|cookie|csrf|session|authorization|password|credential)/i.test(k)).slice(0,30);
    const rows=[prefix+':'+kind];
    if(depth<2)for(const key of keys)rows.push(...shape(value[key],prefix+'.'+key,depth+1));
    return rows.slice(0,120);
  }
  const fieldType=value=>value===null?'null':Array.isArray(value)?'array':typeof value;
  function htmlObservation(actual, expected) {
    const info={type:fieldType(actual),expected_length:expected.length,actual_length:typeof actual==='string'?actual.length:null};
    if(typeof actual==='string') {
      info.has_html_tags=/<[a-z][^>]*>/i.test(actual);info.format=info.has_html_tags?'html':'plain';
      try {
        const parsed=JSON.parse(actual);
        info.json_type=fieldType(parsed);info.format='json';info.json_structure=shape(parsed,'content');
        info.json_string_matches=typeof parsed==='string' && contentText(parsed)===contentText(expected);
        if(parsed && typeof parsed==='object' && Array.isArray(parsed.ops)) {
          info.delta_ops=parsed.ops.length;
          info.delta_text_ops=parsed.ops.filter(op=>typeof op?.insert==='string').length;
          info.delta_text_length=parsed.ops.reduce((length,op)=>length+(typeof op?.insert==='string'?op.insert.length:0),0);
          info.delta_embed_ops=parsed.ops.filter(op=>op?.insert && typeof op.insert==='object').length;
          info.delta_image_ops=parsed.ops.filter(op=>op?.insert && typeof op.insert==='object' && Object.hasOwn(op.insert,'image')).length;
        }
      } catch {info.json_type='not-json';}
    }
    // These comparisons diagnose HTML conversion; they never turn a mismatch into success.
    if(typeof actual==='string' && typeof DOMParser!=='undefined') {
      try {
        const a=new DOMParser().parseFromString(actual,'text/html').body;
        const b=new DOMParser().parseFromString(expected,'text/html').body;
        const sources=node=>[...node.querySelectorAll('img')].map(img=>img.getAttribute('src')||'');
        const left=sources(a),right=sources(b);
        info.text_equal=a.textContent===b.textContent;
        info.text_equal_ignoring_whitespace=a.textContent.replace(/\s/g,'')===b.textContent.replace(/\s/g,'');
        info.text_length=a.textContent.length;info.expected_text_length=b.textContent.length;
        const tags=node=>[...node.children].map(child=>child.tagName.toLowerCase());
        info.actual_top_level_tags=tags(a).slice(0,40);info.expected_top_level_tags=tags(b).slice(0,40);
        info.actual_link_count=a.querySelectorAll('a[href]').length;info.expected_link_count=b.querySelectorAll('a[href]').length;
        const urls=node=>[...node.querySelectorAll('a[href]')].map(link=>link.getAttribute('href')||'');
        info.link_targets_equal=JSON.stringify(urls(a))===JSON.stringify(urls(b));
        info.image_urls_equal_ignoring_query=left.length===right.length && left.every((value,index)=>{
          try {const x=new URL(value,location.origin),y=new URL(right[index],location.origin);return x.hostname===y.hostname && x.pathname===y.pathname;}catch{return false;}
        });
        info.expected_image_count=right.length;info.actual_image_count=left.length;
        info.image_sources_equal=JSON.stringify(left)===JSON.stringify(right);
        info.parsed_html_equal=a.innerHTML===b.innerHTML;
      } catch {info.html_inspection_failed=true;}
    }
    return info;
  }
  function imageUrl(value) {
    if(typeof value!=='string' || !/^(https?:)?\/\//.test(value))throw Error('B站未返回有效图片地址');
    const u=new URL(String(value||''),'https://member.bilibili.com');
    if(u.protocol==='http:')u.protocol='https:';
    if(u.protocol!=='https:' || u.port || u.username || u.password ||
       !['hdslb.com','biliimg.com','bilibili.com'].some(s=>u.hostname===s||u.hostname.endsWith('.'+s)))throw Error('B站未返回有效图片地址');
    return u.href;
  }
  function parseResponse(text) {
    // Parse integer tokens losslessly before JSON.parse can round 64-bit article/account IDs.
    // The tokenizer consumes JSON strings as a whole, including escaped quotes; strings are
    // never rewritten. Decimal/exponent values keep ordinary JSON number semantics.
    return JSON.parse(text.replace(/"(?:[^"\\]|\\.)*"|-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?/g, token=>{
      if(token[0]==='"' || /[.eE]/.test(token))return token;
      return Number.isSafeInteger(Number(token))?token:JSON.stringify(token);
    }));
  }
  async function request(url,options={}) {
    const r=await fetch(url,{credentials:'include',cache:'no-store',redirect:'error',signal:AbortSignal.timeout(30000),...options});
    if(!r.ok)throw Error('B站请求失败（HTTP '+r.status+'），请在平台窗口检查登录或验证提示');
    let value;try {value=parseResponse(await r.text());}catch {throw Error('B站未返回有效JSON，无法回读草稿');}
    if(!value || value.code!==0)throw Error('B站未接受请求（'+String(value?.code??'无返回码')+'），请在平台窗口检查登录或验证提示');
    return value.data;
  }
  function token() {
    const match=document.cookie.match(/(?:^|;\s*)bili_jct=([^;]+)/);
    const value=match?decodeURIComponent(match[1]):'';
    if(!/^[a-f0-9]{32}$/i.test(value))throw Error('B站登录校验已失效，请在内置平台窗口重新登录');
    return value;
  }
  async function identity() {
    const r=await fetch('https://api.bilibili.com/x/web-interface/nav',{credentials:'include',cache:'no-store',redirect:'error',signal:AbortSignal.timeout(30000)});
    if(!r.ok)throw Error('B站账号核验请求失败（HTTP '+r.status+'）');
    const value=parseResponse(await r.text());
    if(value.code===-101 || (value.code===0 && value.data?.isLogin===false))return null;
    const id=identifier(value.data?.mid);
    if(value.code!==0 || value.data?.isLogin!==true || !id)throw Error('B站登录状态未能核实');
    return {id,name:String(value.data.uname||id).slice(0,200)};
  }
  function htmlToOpus(html) {
    if(typeof DOMParser==='undefined')throw Error('浏览器缺少HTML解析能力，正文尚未发送');
    const root=new DOMParser().parseFromString(html,'text/html').body,paragraphs=[];
    const styles={strong:'bold',b:'bold',em:'italic',i:'italic',del:'strikethrough',s:'strikethrough',u:'underline'};
    const sizes={1:[24,'xxLarge'],2:[22,'xLarge'],3:[20,'large'],4:[18,'regular'],5:[16,'regular'],6:[14,'regular']};
    function inline(node,style={}) {
      if(node.nodeType===3)return [{node_type:1,word:{words:node.nodeValue.replace(/[ \t\r\n\f]+/g,' '),font_size:17,font_level:'regular',color:'',dark_color:'',style}}];
      if(node.nodeType===8)return [];
      const tag=node.tagName?.toLowerCase();
      if(tag==='br')return [{node_type:1,word:{words:'\n',font_size:17,font_level:'regular',color:'',dark_color:'',style}}];
      if(styles[tag])style={...style,[styles[tag]]:true};
      else if(!['span','a','code'].includes(tag))throw Error('B站暂不支持此段内元素：'+tag+'，正文尚未发送');
      if(node.hasAttribute('style'))throw Error('B站自定义行内样式尚未确认，正文尚未发送');
      const nodes=[...node.childNodes].flatMap(child=>inline(child,style));
      if(tag==='a') {
        const href=node.getAttribute('href')||'';
        if(!/^https?:\/\//.test(href))throw Error('B站链接格式未能核实，正文尚未发送');
        return nodes.map(value=>({node_type:4,link:{show_text:value.word.words,link:href,link_type:16,style:{...value.word.style,font_size:17,font_level:'regular'},pics:[]}}));
      }
      if(tag==='code')throw Error('B站行内代码格式尚未确认，请使用独立代码块；正文尚未发送');
      return nodes;
    }
    function block(node,list=null) {
      if(node.nodeType===8 || node.nodeType===3 && !node.nodeValue.trim())return;
      if(node.nodeType!==1)throw Error('B站正文需按段落组织，正文尚未发送');
      const tag=node.tagName.toLowerCase();
      if(['ul','ol'].includes(tag)) {
        const level=(list?.level||0)+1,start=Number(node.getAttribute('start')||1);
        if(!Number.isSafeInteger(start)||start<1)throw Error('列表编号无效');
        let order=start;for(const child of node.children) {
          if(child.tagName!=='LI')throw Error('列表结构未能核实');
          block(child,{type:tag==='ol'?5:6,level,order:order++});
        }return;
      }
      if(tag==='img') {paragraphs.push({para_type:2,pic:{pics:[{url:imageUrl(node.getAttribute('src')),width:0,height:0,size:0}],style:0}});return;}
      if(tag==='hr') {paragraphs.push({para_type:3,line:{line_type:1}});return;}
      if(tag==='pre') {
        const code=node.querySelector('code'),lang=(code?.className.match(/(?:^|\s)language-([^\s]+)/)||[])[1]||'';
        paragraphs.push({para_type:8,code:{lang,content:node.textContent}});return;
      }
      if(tag==='blockquote') {
        const before=paragraphs.length;for(const child of node.childNodes)block(child);
        for(const para of paragraphs.slice(before)){if(para.para_type!==1)throw Error('复杂引用结构尚未确认');para.para_type=4;}return;
      }
      if(!['p','li','div','h1','h2','h3','h4','h5','h6'].includes(tag))throw Error('B站暂不支持此段落元素：'+tag+'，正文尚未发送');
      if(node.hasAttribute('style'))throw Error('B站自定义段落样式尚未确认，正文尚未发送');
      let pending=[],listUsed=false;
      const flush=()=>{
        if(!pending.length)return;
        const nodes=pending.flatMap(child=>inline(child));pending=[];
        if(!nodes.some(n=>(n.word?.words||n.link?.show_text||'').trim()))return;
        let para={para_type:1,format:{align:0},text:{nodes}};
        if(/^h[1-6]$/.test(tag)) {
          para.para_type=9;const size=sizes[tag[1]];
          for(const n of nodes){const value=n.word||n.link?.style;if(value){value.font_size=size[0];value.font_level=size[1];}}
        } else if(list&&!listUsed) {
          para.para_type=list.type;para.format.list_format={level:list.level,order:list.order,
            theme:(list.type===5?['arabic_num','lower_letter','roman_num']:['dot','square','circle'])[(list.level-1)%3]};listUsed=true;
        }
        paragraphs.push(para);
      };
      for(const child of node.childNodes) {
        const childTag=child.tagName?.toLowerCase();
        if(['img','ul','ol','p','div','pre','blockquote','hr'].includes(childTag)) {
          flush();
          const before=paragraphs.length;
          block(child,['ul','ol'].includes(childTag)?list:(!listUsed?list:null));
          if(paragraphs.slice(before).some(p=>[5,6].includes(p.para_type)))listUsed=true;
        } else pending.push(child);
      }flush();
    }
    for(const node of root.childNodes)block(node);
    if(!paragraphs.length)throw Error('B站正文为空，尚未发送');
    return {paragraphs};
  }
  function headingSize(paragraph) {
    const nodes=paragraph.text?.nodes;
    if(!Array.isArray(nodes)||!nodes.length)throw Error('标题缺少文字节点');
    const sizes=nodes.map(n=>Number(n.node_type===1?n.word?.font_size:n.node_type===4?n.link?.style?.font_size:NaN));
    if(sizes.some(n=>!Number.isFinite(n)||n<=0)||sizes.some(n=>n!==sizes[0]))throw Error('标题节点字号缺失或不一致');
    return sizes[0];
  }
  function zeroIndent(value) {
    if(value===0)return true;
    if(!value||typeof value!=='object'||Array.isArray(value))return false;
    const keys=Object.keys(value);
    // York convertParagraphFormatToAttrs emits no attributes for indent:{},
    // and its Indent extension defaults both indent/textIndent to zero.
    return keys.every(key=>['indent','first_line_indent'].includes(key)&&value[key]===0);
  }
  function normalizedParagraphs(paragraphs) {
    return paragraphs.filter((p,index)=>{
      // York inserts a completely empty type-1 paragraph beside a picture.
      // Never discard a text run, whitespace node, styled block or unknown field.
      if(p?.para_type!==1 || ![paragraphs[index-1]?.para_type,paragraphs[index+1]?.para_type].includes(2))return true;
      if(Object.keys(p).some(k=>!['para_type','format','text'].includes(k)))return true;
      if(!p.text || typeof p.text!=='object' || Array.isArray(p.text) || Object.keys(p.text).some(k=>k!=='nodes'))return true;
      if(p.text.nodes!==undefined && (!Array.isArray(p.text.nodes)||p.text.nodes.length))return true;
      if(p.format!==undefined && (!p.format || typeof p.format!=='object' || Array.isArray(p.format) || Object.keys(p.format).some(k=>!['indent','align'].includes(k) || (k==='indent'?!zeroIndent(p.format[k]):p.format[k]!==0))))return true;
      return false;
    });
  }
  function coverObservation(value,expected,depth=0) {
    const info={type:fieldType(value)};
    if(depth>=3)return info;
    if(Array.isArray(value)){info.count=value.length;info.items=value.slice(0,10).map(v=>coverObservation(v,expected,depth+1));return info;}
    if(value && typeof value==='object') {
      info.structure=shape(value,'cover');
      for(const key of ['url','src','image_url'])if(Object.hasOwn(value,key))info[key]=coverObservation(value[key],expected,depth+1);
      return info;
    }
    if(typeof value==='string') {
      info.nonempty=!!value;info.matches=false;
      try {info.matches=imageUrl(value)===imageUrl(expected);}catch {}
    }
    return info;
  }
  function pictureObservation(actual,expected) {
    const info={comment_type:fieldType(actual?.comment),expected_comment_type:fieldType(expected?.comment),
      comment_length:typeof actual?.comment==='string'?actual.comment.length:null,
      expected_comment_length:typeof expected?.comment==='string'?expected.comment.length:null,
      comment_structure:shape(actual?.comment,'comment'),comment_matches:JSON.stringify(actual?.comment)===JSON.stringify(expected?.comment)};
    try {
      const a=new URL(imageUrl(actual?.url)),b=new URL(imageUrl(expected?.url));
      info.actual_host=a.hostname;info.expected_host=b.hostname;info.host_matches=a.hostname===b.hostname;info.path_matches=a.pathname===b.pathname;
      const path=u=>u.pathname.length<=400&&/^\/bfs\/[A-Za-z0-9_./@-]+$/.test(u.pathname)?u.pathname:null;
      info.actual_path=path(a);info.expected_path=path(b);info.url_matches=a.href===b.href;
      const keys=u=>[...new Set([...u.searchParams.keys()].map(k=>/^[A-Za-z0-9_-]{1,60}$/.test(k)?k:'[other]'))].slice(0,30).sort();
      info.actual_query_keys=keys(a);info.expected_query_keys=keys(b);
    }catch{info.url_unrecognized=true;}
    return info;
  }
  async function sameNativePictureBytes(actual,expected,info,signal) {
    let a,b;
    try{a=new URL(imageUrl(actual));b=new URL(imageUrl(expected));}catch{return false;}
    const hosts=new Set([a.hostname,b.hostname]);
    if(hosts.size!==2||!hosts.has('article.biliimg.com')||!hosts.has('i0.hdslb.com')||a.pathname!==b.pathname||
      !/^\/bfs\/new_dyn\/[a-f0-9]{32,64}\.(?:png|jpe?g|webp|gif)$/i.test(a.pathname)||a.search||b.search||a.hash||b.hash)return false;
    info.byte_check='unavailable';
    try {
      if(!globalThis.crypto?.subtle)return false;
      const digest=async url=>{
        const response=await fetch(url,{method:'GET',credentials:'omit',cache:'no-store',redirect:'error',referrerPolicy:'strict-origin-when-cross-origin',signal});
        if(!response.ok || response.redirected || !/^image\//i.test(response.headers.get('content-type')||''))throw Error('unavailable');
        const limit=5000000,length=Number(response.headers.get('content-length'));
        if(length>limit){await response.body?.cancel();throw Error('oversize');}
        if(!response.body?.getReader)throw Error('stream unavailable');
        const reader=response.body.getReader(),chunks=[];let size=0;
        try {
          while(true) {
            const {done,value}=await reader.read();if(done)break;
            size+=value.byteLength;if(size>limit)throw Error('oversize');chunks.push(value);
          }
        } finally {await reader.cancel();reader.releaseLock();}
        if(!size)throw Error('empty');
        const bytes=new Uint8Array(size);let offset=0;for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.byteLength;}
        return {size,hash:[...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(v=>v.toString(16).padStart(2,'0')).join('')};
      };
      const left=await digest(a.href),right=await digest(b.href);
      info.byte_check=left.size===right.size&&left.hash===right.hash?'equal':'different';
      info.actual_bytes=left.size;info.expected_bytes=right.size;
      return info.byte_check==='equal';
    }catch{return false;}
  }
  function opusSignature(content,imageAliases=new Map()) {
    if(!content || !Array.isArray(content.paragraphs))throw Error('新版草稿缺少opus.content.paragraphs');
    const paragraphs=normalizedParagraphs(content.paragraphs);
    const headingSizes=[...new Set(paragraphs.filter(p=>p.para_type===9).map(headingSize))].sort((a,b)=>b-a);
    return paragraphs.map(p=>{
      if(p.format?.indent!==undefined&&!zeroIndent(p.format.indent))throw Error('新版草稿缩进格式非零或未能核实');
      if(p.para_type===2) {
        if(!Array.isArray(p.pic?.pics)||!p.pic.pics.length)throw Error('图片段落缺少图片');
        return {type:2,images:p.pic.pics.map(img=>({url:imageAliases.get(imageUrl(img.url))||imageUrl(img.url),comment:img.comment||''})),style:p.pic.style||0};
      }
      if(p.para_type===3)return {type:3,line:p.line?.line_type};
      if(p.para_type===8)return {type:8,language:p.code?.lang||'',code:p.code?.content};
      if(![1,4,5,6,9].includes(p.para_type)||!Array.isArray(p.text?.nodes))throw Error('新版草稿含未支持的结构节点');
      const out={type:p.para_type,align:p.format?.align||0,runs:[]};
      if([5,6].includes(p.para_type))out.list={level:p.format?.list_format?.level,order:p.format?.list_format?.order,theme:p.format?.list_format?.theme};
      if(p.para_type===9)out.heading=headingSizes.indexOf(headingSize(p))+1;
      for(const n of p.text.nodes) {
        let text,style,href='';
        if(n.node_type===1){text=n.word?.words;style=n.word?.style||{};}
        else if(n.node_type===4 && n.link?.link_type===16){text=n.link.show_text;style=n.link.style||{};href=n.link.link;}
        else throw Error('新版草稿含未支持的段内节点');
        if(typeof text!=='string')throw Error('新版草稿文字字段缺失');
        const run={text:text.replace(/\r\n/g,'\n'),href,bold:!!style.bold,italic:!!style.italic,underline:!!style.underline,strikethrough:!!style.strikethrough};
        const previous=out.runs[out.runs.length-1];
        if(previous && ['href','bold','italic','underline','strikethrough'].every(k=>previous[k]===run[k]))previous.text+=run.text;
        else out.runs.push(run);
      }
      return out;
    });
  }
  async function readDraft(id, article, account) {
    const url='https://member.bilibili.com/platform/upload/text/edit?aid='+id;
    let observed={};
    try {
      // Official York editor uses this endpoint for type=4. The legacy draft/view
      // endpoint only returns a compatibility message, not the complete article.
      const payload=await request('https://api.bilibili.com/x/dynamic/feed/article/draft/view?article_id='+encodeURIComponent(id));
      const saved=payload?.draft;
      const expected=htmlToOpus(article.content),actual=saved?.opus?.content;
      observed={endpoint:'opus-draft',structure:shape(saved,'draft'),content_structure:shape(actual,'content'),
        id_type:fieldType(saved?.article_id),id_matches:identifier(saved?.article_id)===id,title_matches:saved?.title===article.title,
        type:saved?.type,expected_paragraph_count:expected.paragraphs.length,
        actual_paragraph_count:Array.isArray(actual?.paragraphs)?actual.paragraphs.length:null,
        image_urls_type:fieldType(saved?.image_urls),
        cover_candidates:Object.fromEntries(['image_urls','origin_image_urls','banner_url','cover','covers','cover_url'].map(key=>[key,coverObservation(saved?.[key],article.cover)])),
        opus_cover_candidates:Object.fromEntries(['cover','covers','cover_url','image_urls','attachments'].map(key=>[key,coverObservation(saved?.opus?.[key],article.cover)])),
        paragraph_structure:Array.isArray(actual?.paragraphs)?actual.paragraphs.slice(0,100).map(p=>({
          type:p?.para_type,keys:Object.keys(p||{}),text_type:fieldType(p?.text),text_keys:Object.keys(p?.text||{}),node_types:p?.text?.nodes?.map(n=>n?.node_type),
          link_types:p?.text?.nodes?.filter(n=>n?.node_type===4).map(n=>n?.link?.link_type),
          text_lengths:p?.text?.nodes?.map(n=>typeof n?.word?.words==='string'?n.word.words.length:typeof n?.link?.show_text==='string'?n.link.show_text.length:null),
          image_count:p?.pic?.pics?.length,format_keys:Object.keys(p?.format||{}),
          indent_structure:shape(p?.format?.indent,'indent'),indent_zero:zeroIndent(p?.format?.indent),
          list_format:p?.format?.list_format?{level:p.format.list_format.level,order:p.format.list_format.order,theme:p.format.list_format.theme}:undefined,
          heading_font_sizes:p?.para_type===9?p?.text?.nodes?.map(n=>n?.node_type===4?n?.link?.style?.font_size:n?.word?.font_size):undefined
        })):null};
      if(!observed.id_matches)throw Error('新版草稿编号未能核实');
      if(!observed.title_matches)throw Error('新版草稿标题不一致');
      if(saved.type!==4)throw Error('新版草稿类型未能核实');
      const original=opusSignature(actual),right=opusSignature(expected),aliases=new Map();
      observed.picture_comparisons=[];
      let byteAttempts=0;const signal=AbortSignal.timeout(15000);
      for(let index=0;index<original.length;index++) {
        const pictures=original[index]?.images,wanted=right[index]?.images;
        if(!pictures)continue;
        for(let imageIndex=0;imageIndex<pictures.length;imageIndex++) {
          const picture=pictures[imageIndex],target=wanted?.[imageIndex];
          const info={paragraph:index,image_index:imageIndex,...pictureObservation(picture,target)};
          observed.picture_comparisons.push(info);
          if(!target||wanted.length!==pictures.length||info.url_matches||!info.comment_matches)continue;
          if(byteAttempts++>=4||signal.aborted){info.byte_check='limit';continue;}
          if(await sameNativePictureBytes(picture.url,target.url,info,signal))aliases.set(picture.url,target.url);
        }
      }
      const left=aliases.size?opusSignature(actual,aliases):original;
      observed.ignored_empty_picture_paragraphs=actual.paragraphs.length-left.length;
      observed.paragraph_types=left.map(p=>p.type);observed.expected_paragraph_types=right.map(p=>p.type);
      observed.content_matches=JSON.stringify(left)===JSON.stringify(right);
      observed.paragraph_matches=Array.from({length:Math.max(left.length,right.length)},(_,i)=>({index:i,
        structure:JSON.stringify(left[i]&&{...left[i],runs:undefined,images:undefined})===JSON.stringify(right[i]&&{...right[i],runs:undefined,images:undefined}),
        text:left[i]?.runs?.map(r=>r.text).join('')===right[i]?.runs?.map(r=>r.text).join(''),
        runs:JSON.stringify(left[i]?.runs)===JSON.stringify(right[i]?.runs),
        images:JSON.stringify(left[i]?.images)===JSON.stringify(right[i]?.images)})).slice(0,100);
      observed.actual_image_count=left.reduce((n,p)=>n+(p.images?.length||0),0);
      observed.expected_image_count=right.reduce((n,p)=>n+(p.images?.length||0),0);
      if(!observed.content_matches)throw Error('新版草稿段落、文字、格式、链接或图片回读不一致');
      if(article.cover) {
        observed.cover_matches=Array.isArray(saved.image_urls) && saved.image_urls.some(value=>{
          try{return imageUrl(typeof value==='string'?value:value?.url)===imageUrl(article.cover);}catch{return false;}
        });
        if(!observed.cover_matches)throw Error('新版草稿封面回读不一致');
      }
      if((await identity())?.id!==account.id)throw Error('回读期间B站账号已变化');
      return {id,url};
    } catch(e) {return {id,url,incomplete:true,reason:e.message,observed};}
  }
  try {
    if(action==='identity')return {ok:true,data:await identity()};
    if(action==='upload') {
      if(!arg || !['png','jpg','jpeg','gif','webp'].includes(arg.extension))throw Error('图片格式无效');
      const csrf=token(),bytes=Uint8Array.from(atob(arg.base64),c=>c.charCodeAt(0));
      if(!bytes.length || bytes.length>5000000)throw Error('图片为空或超过5MB');
      const body=new FormData();
      body.append('binary',new Blob([bytes],{type:arg.mime}),'image.'+arg.extension);body.append('csrf',csrf);
      const value=await request('https://api.bilibili.com/x/article/creative/article/upcover',{method:'POST',body});
      return {ok:true,data:imageUrl(value?.url)};
    }
    if(action==='draft-read') {
      const id=identifier(arg?.draft_id);
      if(!id || !arg?.title?.trim() || !contentText(arg?.content) || arg.content.includes('/media/'))throw Error('缺少原草稿编号或准确提交快照，未执行核对');
      const account=await identity();
      if(!account || !identifier(arg.account_id) || account.id!==String(arg.account_id))throw Error('B站账号与原送稿任务不同，未执行核对');
      return {ok:true,data:await readDraft(id,arg,account)};
    }
    if(action==='save') {
      if(!arg?.title?.trim() || !contentText(arg?.content) || arg.content.includes('/media/'))throw Error('标题、正文或图片未准备好');
      const account=await identity();
      if(!account || !identifier(arg.account_id) || account.id!==String(arg.account_id))throw Error('B站账号已变化，正文尚未发送');
      const cover=arg.cover?imageUrl(arg.cover):'';
      const content=htmlToOpus(arg.content);
      // Images use the official native picture schema. Fetch their public BFS
      // metadata before any draft write so dimensions cannot be guessed.
      for(const para of content.paragraphs)if(para.para_type===2)for(const picture of para.pic.pics) {
        const response=await fetch(picture.url+'@info',{credentials:'omit',redirect:'error',signal:AbortSignal.timeout(30000)});
        if(!response.ok)throw Error('B站图片尺寸未能核实，正文尚未发送');
        const info=await response.json();
        if(!Number.isFinite(info.width)||!Number.isFinite(info.height)||info.width<=0||info.height<=0)throw Error('B站图片尺寸未能核实，正文尚未发送');
        picture.width=info.width;picture.height=info.height;picture.size=Number(info.file_size)||0;
      }
      const draft={type:4,template_id:1,category_id:15,article_id:0,title:arg.title,private_pub:2,reprint:0,original:0,
        list_id:0,comment_selected:0,up_closed_reply:0,timer_pub_time:0,only_fans_level:0,only_fans_dnd:0,
        image_urls:cover?[cover]:[],summary:String(arg.summary||'').slice(0,250),
        opus:{opus_source:2,title:arg.title,content,pub_info:{editor_version:'eva3-4.0.0'}}};
      const created=await request('https://api.bilibili.com/x/dynamic/feed/article/draft/add?csrf='+encodeURIComponent(token()),{method:'POST',
        headers:{'content-type':'application/json'},body:JSON.stringify({arg:draft})});
      const id=identifier(created?.article_id);
      if(!id)throw Error('未收到有效草稿编号，请先核对B站草稿箱，不要重复发送');
      return {ok:true,data:await readDraft(id,arg,account)};
    }
    return {ok:false,error:'B站此操作尚未接通，请在平台核对后登记结果'};
  } catch(e) {return {ok:false,error:e.message};}
}

