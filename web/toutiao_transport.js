async function(platform, action, arg) {
  // Protocol sources (checked 2026-10-08):
  // https://github.com/pluscodex888/Wechatsync/blob/v2/packages/core/src/adapters/platforms/toutiao.ts
  // https://github.com/wechatsync/Wechatsync/blob/old/packages/@wechatsync/drivers/src/toutiao.js
  // Execute in the site's main world: its own fetch hook supplies platform request checks.
  // The sole article write always uses save=0; failed or unknown writes are never retried here.
  if(platform!=='toutiao' || location.origin!=='https://mp.toutiao.com')return {ok:false,error:'请在纸飞机内完成头条号登录'};
  const identifier=value=>{
    if(typeof value==='number' && !Number.isSafeInteger(value))return '';
    const text=String(value??'');return /^[1-9][0-9]{0,39}$/.test(text)?text:'';
  };
  const contentText=value=>typeof value==='string'?value.replace(/\r\n/g,'\n').trim():'';
  const images=globalThis.__paperplaneToutiaoImages||(globalThis.__paperplaneToutiaoImages=Object.create(null));
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
    if(typeof value!=='string' || !/^(https?:)?\/\//.test(value))throw Error('头条号未返回有效图片地址');
    const u=new URL(value,'https://mp.toutiao.com');
    if(u.protocol==='http:')u.protocol='https:';
    if(u.protocol!=='https:' || u.port || u.username || u.password ||
       !['pstatp.com','toutiao.com','toutiaocdn.com','toutiaoimg.com','byteimg.com','toutiaostatic.com'].some(s=>u.hostname===s||u.hostname.endsWith('.'+s)))throw Error('头条号未返回有效图片地址');
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
  function imageIdentity(value) {
    const u=new URL(imageUrl(value));
    // Every query value is part of the identity. A narrowly evidenced tracking
    // change or full byte equality is handled as an explicit pair comparison.
    u.searchParams.sort();return u.hostname+u.pathname+(u.searchParams.size?'?'+u.searchParams.toString():'');
  }
  function nativeImageLocation(value) {
    const u=new URL(imageUrl(value));
    const cdn=['pstatp.com','toutiaocdn.com','toutiaoimg.com','byteimg.com','toutiaostatic.com'].some(s=>u.hostname===s||u.hostname.endsWith('.'+s));
    if(!(cdn||u.hostname==='image-tt-private.toutiao.com')||!/^\/(?:tos-[a-z0-9-]+|pgc-image)\/[A-Za-z0-9_./~:-]+$/.test(u.pathname))throw Error('图片对象地址未能核实');
    return u;
  }
  function equalQueryExcept(a,b,ignored) {
    for(const key of ignored)if(a.searchParams.getAll(key).length!==b.searchParams.getAll(key).length||a.searchParams.getAll(key).length>1)return false;
    const left=new URL(a.href),right=new URL(b.href);
    for(const key of ignored){left.searchParams.delete(key);right.searchParams.delete(key);}
    left.searchParams.sort();right.searchParams.sort();
    return left.searchParams.toString()===right.searchParams.toString();
  }
  function signedTrackingEquivalent(actual,expected) {
    try {
      const a=nativeImageLocation(actual),b=nativeImageLocation(expected);
      if(a.hostname!=='image-tt-private.toutiao.com'||a.hostname!==b.hostname||a.pathname!==b.pathname)return false;
      if(!/^\/(?:tos-[a-z0-9-]+|pgc-image)\/[A-Za-z0-9_./-]+~tplv-obj\.image$/.test(a.pathname))return false;
      for(const key of ['policy','x-orig-authkey','x-orig-expires','x-orig-sign','lk3s','traceid']) {
        if(a.searchParams.getAll(key).length!==1||b.searchParams.getAll(key).length!==1||!a.searchParams.get(key)||!b.searchParams.get(key))return false;
      }
      return equalQueryExcept(a,b,['lk3s','traceid']);
    } catch{return false;}
  }
  function objectPathObservation(value) {
    const info={type:fieldType(value)};
    if(typeof value!=='string')return info;
    info.length=value.length;info.leading_slash=value.startsWith('/');info.has_transform=value.includes('~');
    // Object keys are diagnostic evidence; signatures and arbitrary path text
    // are not. Only bounded native image-key syntax may be returned verbatim.
    if(value.length<=1024 && /^\/?(?:tos-[a-z0-9-]+|pgc-image)\/[A-Za-z0-9_./~:-]+$/.test(value) &&
       !/(?:^|\/)\.{1,2}(?:\/|$)/.test(value))info.key=value;
    return info;
  }
  function imageObservation(value, expected) {
    const info={type:fieldType(value),trusted:false};
    try {
      const a=new URL(imageUrl(value)),b=new URL(imageUrl(expected));
      const safeKeys=url=>[...new Set([...url.searchParams.keys()].map(k=>/^[a-z0-9_-]{1,80}$/i.test(k)?k:'[other]'))].sort().slice(0,40);
      info.trusted=true;info.actual_host=a.hostname;info.expected_host=b.hostname;
      info.actual_path=objectPathObservation(a.pathname);info.expected_path=objectPathObservation(b.pathname);
      info.host_matches=a.hostname===b.hostname;info.path_matches=a.pathname===b.pathname;
      info.identity_matches=imageIdentity(value)===imageIdentity(expected);
      info.actual_query_keys=safeKeys(a);info.expected_query_keys=safeKeys(b);
      info.query_comparisons=[...new Set([...info.actual_query_keys,...info.expected_query_keys])].slice(0,40)
        .map(key=>({key,values_equal:key==='[other]'?null:JSON.stringify(a.searchParams.getAll(key))===JSON.stringify(b.searchParams.getAll(key)),
          actual_count:key==='[other]'?null:a.searchParams.getAll(key).length,expected_count:key==='[other]'?null:b.searchParams.getAll(key).length}));
      info.differing_query_keys=info.query_comparisons.filter(item=>item.values_equal===false).map(item=>item.key);
      info.actual_path_has_transform=a.pathname.includes('~');info.expected_path_has_transform=b.pathname.includes('~');
    } catch {}
    return info;
  }
  function coverObservation(value,expected) {
    const info={type:fieldType(value),origin_uri_type:fieldType(value?.origin_uri),origin_uri:objectPathObservation(value?.origin_uri),url:imageObservation(value?.url,expected),thumb_url:imageObservation(value?.thumb_url,expected)};
    try {
      const path=new URL(imageUrl(expected)).pathname.slice(1),origin=value?.origin_uri;
      info.origin_uri_matches_path=typeof origin==='string' && origin===path;
      info.origin_uri_matches_before_transform=typeof origin==='string' && /^(?:tos-[a-z0-9-]+|pgc-image)\/[A-Za-z0-9_./-]+$/.test(origin) && path.startsWith(origin+'~');
    } catch {}
    return info;
  }
  async function sameImageBytes(actual,expected,info,signal) {
    // Unknown query changes are not assumed to be authentication. Only a pair of
    // existing CDN URLs for the identical host and object path may be read. Full
    // SHA-256 equality of bounded image responses is the sole fallback proof.
    if(!info.trusted || !info.host_matches || !info.path_matches)return false;
    let a,b;try{a=nativeImageLocation(actual);b=nativeImageLocation(expected);}catch{return false;}
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
      info.byte_check=left.size===right.size && left.hash===right.hash?'equal':'different';
      info.actual_bytes=left.size;info.expected_bytes=right.size;
      return info.byte_check==='equal';
    } catch {return false;}
  }
  async function compareContentImages(actual,expected) {
    const result={observations:[],aliases:[]};
    if(typeof DOMParser==='undefined')return result;
    const sources=html=>[...new DOMParser().parseFromString(html,'text/html').querySelectorAll('img')].map(img=>img.getAttribute('src')||'');
    const left=sources(actual),right=sources(expected);
    let attempted=0;const signal=AbortSignal.timeout(15000);
    for(let index=0;index<Math.min(left.length,right.length,100);index++) {
      const info=imageObservation(left[index],right[index]);result.observations.push({index,...info});
      if(left.length!==right.length || info.identity_matches || !info.trusted || !info.host_matches || !info.path_matches)continue;
      if(signedTrackingEquivalent(left[index],right[index])) {
        result.observations[index].equivalence='signed_tracking_only';
        result.aliases[index]=imageIdentity(right[index]);continue;
      }
      if(attempted++>=4 || signal.aborted){result.observations[index].byte_check='limit';continue;}
      if(await sameImageBytes(left[index],right[index],result.observations[index],signal))result.aliases[index]=imageIdentity(right[index]);
    }
    return result;
  }
  function coverMatches(value, expected) {
    if(!value || typeof value!=='object')return false;
    if(typeof value.url==='string') {
      try {if(imageIdentity(value.url)===imageIdentity(expected))return true;}catch {}
    }
    // Native origin_uri binds the complete uploaded object to one of the two
    // observed editor templates. Arbitrary crops/transforms are not equivalent.
    let source,sourceKey;
    try {
      source=nativeImageLocation(expected);
      const match=/^\/((?:tos-[a-z0-9-]+|pgc-image)\/[A-Za-z0-9_./-]+)(?:~tplv-obj\.image)?$/.exec(source.pathname);
      if(!match)return false;sourceKey=match[1];
    }catch{return false;}
    if(value.origin_uri!==sourceKey)return false;
    return ['url','thumb_url'].some(key=>{
      try {
        const candidate=nativeImageLocation(value[key]);
        if(!['/'+sourceKey,'/'+sourceKey+'~tplv-obj.image','/'+sourceKey+'~tplv-tt-cover-v2.image'].includes(candidate.pathname))return false;
        const ignored=['lk3s','traceid','x-orig-sign'];
        if(source.hostname==='image-tt-private.toutiao.com'||candidate.hostname==='image-tt-private.toutiao.com') {
          if(source.hostname!==candidate.hostname)return false;
          for(const name of ['policy','x-orig-authkey','x-orig-expires','x-orig-sign'])if(source.searchParams.getAll(name).length!==1||candidate.searchParams.getAll(name).length!==1||!source.searchParams.get(name)||!candidate.searchParams.get(name))return false;
        }
        return equalQueryExcept(candidate,source,ignored);
      }catch{return false;}
    });
  }
  function normalizedHtml(actual, expected, imageAliases=[]) {
    if(typeof DOMParser==='undefined')return {equal:false,reason:'DOM parser unavailable'};
    const parser=new DOMParser(),a=parser.parseFromString(actual,'text/html').body,b=parser.parseFromString(expected,'text/html').body;
    const levels=node=>[...new Set([...node.querySelectorAll('h1,h2,h3,h4,h5,h6')].map(el=>el.tagName))].sort();
    const leftLevels=levels(a),rightLevels=levels(b);
    if(leftLevels.length!==rightLevels.length)return {equal:false,reason:'heading hierarchy differs'};
    function signature(root,headingLevels,aliases=[]) {
      let imageIndex=0;
      const merge=parts=>{
        const result=[];for(const part of parts.flat()) {
          if(typeof part==='string' && typeof result[result.length-1]==='string')result[result.length-1]+=part;
          else result.push(part);
        }return result;
      };
      const walk=node=>{
        if(node.nodeType===3)return node.nodeValue.replace(/\r\n/g,'\n');
        if(node.nodeType===8)return [];
        if(node.nodeType!==1)throw Error('unsupported node');
        let tag=node.tagName.toLowerCase();
        if(node.hasAttribute('style') && tag!=='img')throw Error('inline styles require manual comparison');
        if(tag==='img'){const index=imageIndex++;return {tag:'img',source:aliases[index]||imageIdentity(node.getAttribute('src')||'')};}
        if(tag==='br')return {tag:'br'};
        if(tag==='span')return merge([...node.childNodes].map(walk));
        if(!['p','div','ul','ol','li','blockquote','pre','code','strong','b','em','i','u','s','del','a','hr','h1','h2','h3','h4','h5','h6'].includes(tag))throw Error('unsupported element '+tag);
        // The native editor adds an empty caption control to its image block.
        // Only that exact empty native caption is structural chrome; real captions remain.
        const childNodes=[...node.childNodes].filter(child=>!(tag==='div' && node.classList.contains('pgc-img') && child.nodeType===1 && child.tagName==='P' && child.classList.contains('pgc-img-caption') && !child.textContent && !child.querySelector('*')));
        let children=merge(childNodes.map(walk));
        if(['div','p'].includes(tag) && node.querySelector('img') && children.every(value=>typeof value==='string'?!value.trim():value.tag==='img'))return children.filter(value=>typeof value!=='string');
        // Ignore indentation only at actual block boundaries. Whitespace between
        // inline children (for example strong + em within li) is article text.
        const block=value=>value && typeof value==='object' && /^(?:p|ul|ol|li|blockquote|pre|hr|heading-\d+)$/.test(value.tag);
        if(['div','ul','ol','li','blockquote'].includes(tag))children=children.filter((value,index,all)=>
          typeof value!=='string'||value.trim()||(!block(all[index-1])&&!block(all[index+1])));
        if(tag==='div')throw Error('non-image div requires manual comparison');
        if(/^h[1-6]$/.test(tag))tag='heading-'+(headingLevels.indexOf(node.tagName)+1);
        tag=({b:'strong',i:'em',s:'del'})[tag]||tag;
        const out={tag,children};
        if(tag==='a')out.href=node.getAttribute('href')||'';
        if(tag==='ol')out.start=node.getAttribute('start')||'1';
        if(tag==='code')out.language=(node.className.match(/(?:^|\s)language-([^\s]+)/)||[])[1]||'';
        return out;
      };
      return merge([...root.childNodes].map(walk)).filter(value=>typeof value!=='string'||value.trim());
    }
    try {
      const left=signature(a,leftLevels,imageAliases),right=signature(b,rightLevels);
      return {equal:JSON.stringify(left)===JSON.stringify(right),actual_blocks:left.length,expected_blocks:right.length,
        block_matches:Array.from({length:Math.max(left.length,right.length)},(_,i)=>({index:i,actual_tag:left[i]?.tag||typeof left[i],expected_tag:right[i]?.tag||typeof right[i],equal:JSON.stringify(left[i])===JSON.stringify(right[i])})).slice(0,100)};
    } catch(e) {return {equal:false,reason:e.message};}
  }
  async function request(url,options={}) {
    const r=await fetch(url,{credentials:'include',cache:'no-store',redirect:'error',signal:AbortSignal.timeout(30000),...options});
    if(!r.ok)throw Error('头条号请求失败（HTTP '+r.status+'），请在平台窗口检查登录或验证提示');
    try {return parseResponse(await r.text());}catch {throw Error('头条号未返回有效JSON，无法回读草稿');}
  }
  function success(value) {
    const code=value?.code??value?.err_no??value?.errno;
    if(code!==undefined && String(code)!=='0')throw Error('头条号未接受请求（'+String(code)+'），请在平台窗口检查登录或验证提示');
    if(!value || typeof value!=='object')throw Error('头条号未返回有效结果');
    return value;
  }
  async function identity() {
    const value=await request('https://mp.toutiao.com/mp/agw/media/get_media_info');
    if(value.code===100004 || value.err_no===100004)return null;
    success(value);
    const user=value.data?.user,id=identifier(user?.id_str||user?.id);
    if(!id)throw Error('头条号账号信息未能核实');
    return {id,name:String(user.screen_name||id).slice(0,200)};
  }
  async function readDraft(id, expected, account) {
    const url='https://mp.toutiao.com/profile_v4/graphic/publish?pgc_id='+id;
    let observed={};
    try {
      const result=success(await request('https://mp.toutiao.com/mp/agw/article/edit?pgc_id='+id+'&wxstyle=0&format=json'));
      const root=result.data||result,article=root.articlePgc||root.article_pgc||root;
      const rawId=article.pgcId??article.pgc_id??article.id??root.pgcId??root.pgc_id;
      // The edit endpoint returns both transformed article_pgc.content and the editor's
      // full source in root.content. Verify that source when supplied, never a summary.
      const title=article.title??root.title,content=typeof root.content==='string'?root.content:article.content;
      const contentField=typeof root.content==='string'?'root.content':'article.content';
      const coverData=article.pgc_feed_covers??article.pgcFeedCovers??root.pgc_feed_covers??root.pgcFeedCovers;
      observed={structure:shape(result),article_structure:shape(article,'article'),id_type:fieldType(rawId),id_matches:identifier(rawId)===id,
        title_type:fieldType(title),title_matches:title===expected.title,
        content:htmlObservation(content,expected.content),content_field:contentField,
        content_candidates:{root:htmlObservation(root.content,expected.content),article:htmlObservation(article.content,expected.content)},
        cover_type:fieldType(coverData),cover_structure:shape(coverData,'covers'),is_draft_type:fieldType(root.is_draft)};
      let diagnosticCovers;
      try {diagnosticCovers=typeof coverData==='string'?JSON.parse(coverData):coverData;}catch {}
      if(Array.isArray(diagnosticCovers)) {
        observed.cover_count=diagnosticCovers.length;
        observed.cover_candidates=diagnosticCovers.slice(0,10).map(c=>coverObservation(c,expected.cover));
        observed.cover_matches=!!expected.cover && diagnosticCovers.some(c=>{
          try {return coverMatches(c,expected.cover);}catch {return false;}
        });
      }
      if(typeof root.is_draft==='boolean')observed.is_draft=root.is_draft;
      if(typeof rawId==='number')observed.id_safe_integer=Number.isSafeInteger(rawId);
      if(!observed.id_matches)throw Error('原草稿编号未能核实（pgcId/pgc_id/id: '+observed.id_type+'）');
      if(!observed.title_matches)throw Error('原草稿标题回读不一致（title: '+observed.title_type+'）');
      if(root.is_draft===false)throw Error('平台明确返回非草稿状态，不能认定为私有草稿');
      observed.normalized_content=typeof content==='string'?normalizedHtml(content,expected.content):{equal:false};
      if(typeof content==='string' && contentText(content)!==contentText(expected.content) && !observed.normalized_content.equal) {
        const comparison=await compareContentImages(content,expected.content);
        observed.content_image_comparisons=comparison.observations;
        if(comparison.aliases.some(Boolean))observed.normalized_content=normalizedHtml(content,expected.content,comparison.aliases);
      }
      if(typeof content!=='string' || (contentText(content)!==contentText(expected.content) && !observed.normalized_content.equal))throw Error('原草稿正文回读不一致（'+contentField+': '+observed.content.type+'，提交 '+expected.content.length+' 字符，回读 '+(observed.content.actual_length??'未知')+' 字符）；暂未认定HTML转换等价');
      if(expected.cover) {
        let covers;try {covers=typeof coverData==='string'?JSON.parse(coverData):coverData;}catch {throw Error('原草稿封面字段不是有效JSON（pgc_feed_covers/pgcFeedCovers）');}
        if(!Array.isArray(covers))throw Error('原草稿封面列表缺失（pgc_feed_covers/pgcFeedCovers: '+observed.cover_type+'）');
        observed.cover_count=covers.length;
        observed.cover_matches=covers.some(c=>coverMatches(c,expected.cover));
        if(!observed.cover_matches)throw Error('原草稿封面回读不一致（pgc_feed_covers/pgcFeedCovers，'+covers.length+' 项）');
      }
      if((await identity())?.id!==account.id)throw Error('回读期间头条号账号已变化');
      return {id,url};
    } catch(e) {return {id,url,incomplete:true,reason:e.message,observed};}
  }
  try {
    if(action==='identity')return {ok:true,data:await identity()};
    if(action==='upload') {
      if(!arg || !['png','jpg','jpeg','gif','webp'].includes(arg.extension))throw Error('图片格式无效');
      const bytes=Uint8Array.from(atob(arg.base64),c=>c.charCodeAt(0));
      if(!bytes.length || bytes.length>5000000)throw Error('图片为空或超过5MB');
      const body=new FormData();body.append('upfile',new Blob([bytes],{type:arg.mime}),'image.'+arg.extension);
      const value=await request('https://mp.toutiao.com/mp/agw/article_material/photo/upload_picture?type=ueditor&pgc_watermark=1&action=uploadimage&encode=utf-8',{method:'POST',body});
      if(value.state!=='SUCCESS')throw Error('头条号未确认图片上传，请在平台窗口检查提示');
      const url=imageUrl(value.origin_web_url||value.url);
      images[url]={uri:value.origin_web_uri||'',width:Number(value.width)||0,height:Number(value.height)||0};
      return {ok:true,data:url};
    }
    if(action==='draft-read') {
      const id=identifier(arg?.draft_id);
      if(!id || !arg?.title?.trim() || !contentText(arg?.content) || arg.content.includes('/media/'))throw Error('缺少原草稿编号或准确提交快照，未执行核对');
      const account=await identity();
      if(!account || !identifier(arg.account_id) || account.id!==String(arg.account_id))throw Error('头条号账号与原送稿任务不同，未执行核对');
      return {ok:true,data:await readDraft(id,arg,account)};
    }
    if(action==='save') {
      if(!arg?.title?.trim() || !contentText(arg?.content) || arg.content.includes('/media/'))throw Error('标题、正文或图片未准备好');
      if(location.pathname!=='/profile_v4/graphic/publish' && location.pathname!=='/profile_v3/graphic/publish')throw Error('请先打开头条号图文编辑页，再发送草稿');
      const account=await identity();
      if(!account || !identifier(arg.account_id) || account.id!==String(arg.account_id))throw Error('头条号账号已变化，正文尚未发送');
      const covers=[];
      if(arg.cover) {
        const url=imageUrl(arg.cover),metadata=images[url];
        if(!metadata?.uri || !metadata.width || !metadata.height)throw Error('头条号封面缺少上传回执，请重新上传封面，正文尚未发送');
        covers.push({id:0,url,uri:metadata.uri,origin_uri:metadata.uri,ic_uri:'',thumb_width:metadata.width,thumb_height:metadata.height});
      }
      const body=new URLSearchParams({article_type:'0',pgc_id:'',source:'29',title:arg.title,title_id:'',content:arg.content,
        save:'0',entrance:'',timer_status:'0',timer_time:'',article_ad_type:'2',claim_origin:'0',is_fans_article:'0',
        search_creation_info:JSON.stringify({searchTopOne:0,abstract:'',clue_id:null,activity_type:1}),
        ic_uri_list:'[]',pgc_feed_covers:JSON.stringify(covers),
        extra:JSON.stringify({content_source:100000000402,content_word_cnt:arg.content.replace(/<[^>]+>/g,'').trim().length})});
      if(!covers.length)body.set('draft_form_data',JSON.stringify({coverType:1}));
      const created=success(await request('https://mp.toutiao.com/mp/agw/article/publish?source=mp&type=article&aid=1231&mp_publish_ab_val=0',{method:'POST',
        headers:{'content-type':'application/x-www-form-urlencoded;charset=UTF-8'},body:body.toString()}));
      const id=identifier(created.data?.pgcId??created.data?.pgc_id??created.pgcId??created.pgc_id);
      if(!id)throw Error('未收到有效草稿编号，请先核对头条号草稿箱，不要重复发送');
      return {ok:true,data:await readDraft(id,arg,account)};
    }
    return {ok:false,error:'头条号此操作尚未接通，请在平台核对后登记结果'};
  } catch(e) {return {ok:false,error:e.message};}
}
