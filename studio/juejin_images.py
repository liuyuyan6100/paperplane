"""Native ImageX upload. Temporary grants remain in memory; browser cookies stay in WebView2."""
import hashlib
import hmac
import json
import ipaddress
import re
import zlib
from datetime import datetime, timezone
from urllib.parse import urlencode, urlsplit, quote
from urllib.request import Request, build_opener
from urllib.error import HTTPError
from .adapters import DeliveryError, NoRedirect


def request(url, method, headers, body=None):
    try:
        req=Request(url,data=body,method=method,headers={
            'Origin':'https://juejin.cn','Referer':'https://juejin.cn/',**headers})
        with build_opener(NoRedirect()).open(req,timeout=40) as response:
            return response.read(3_000_000)
    except HTTPError as exc:
        raise DeliveryError(f'掘金图片服务返回 HTTP {exc.code}，正文尚未提交。') from None
    except OSError:
        raise DeliveryError('掘金图片服务连接未完成，正文尚未提交。') from None


def imagex(action, token, session=''):
    params={'Action':action,'Version':'2018-08-01','ServiceId':'73owjymdk6'}
    if session:params['SessionKey']=session
    query=urlencode(sorted(params.items()))
    method='POST' if session else 'GET'
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ');day=stamp[:8]
    names='x-amz-date;x-amz-security-token'
    canonical='\n'.join([method,'/',query,f'x-amz-date:{stamp}\nx-amz-security-token:{token["SessionToken"]}\n',names,hashlib.sha256(b'').hexdigest()])
    scope=day+'/cn-north-1/imagex/aws4_request'
    key=('AWS4'+token['SecretAccessKey']).encode()
    for part in (day,'cn-north-1','imagex','aws4_request'):key=hmac.digest(key,part.encode(),'sha256')
    signed='\n'.join(['AWS4-HMAC-SHA256',stamp,scope,hashlib.sha256(canonical.encode()).hexdigest()])
    headers={'X-Amz-Date':stamp,'X-Amz-Security-Token':token['SessionToken'],
             'Authorization':f'AWS4-HMAC-SHA256 Credential={token["AccessKeyId"]}/{scope}, SignedHeaders={names}, Signature={hmac.new(key,signed.encode(),"sha256").hexdigest()}'}
    value=json.loads(request('https://imagex.bytedanceapi.com/?'+query,method,headers,b'' if session else None))
    if not value.get('Result') or value.get('ResponseMetadata',{}).get('Error'):
        raise DeliveryError('掘金图片服务未确认上传，正文尚未提交。')
    return value['Result']


def provider_upload_url(host, uri):
    """Only consume UploadHosts from our authenticated, fixed ImageX endpoint.

    ImageX assigns upload domains per service (including custom domains). A list
    of guessed CDN suffixes cannot describe that contract. Still reject scheme,
    authority, port and path changes, IP literals and local hostnames. Never
    follow redirects when sending the short-lived upload grant.
    """
    if not isinstance(host,str) or not isinstance(uri,str):
        raise DeliveryError('图片服务返回的上传地址格式无效。')
    parts=urlsplit(host if '://' in host else 'https://'+host)
    name=parts.hostname or ''
    try:
        ipaddress.ip_address(name)
        literal=True
    except ValueError:literal=False
    if (parts.scheme!='https' or parts.username or parts.password or parts.port not in (None,443)
        or parts.path not in ('','/') or parts.query or parts.fragment or literal
        or not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}',name)
        or name.endswith(('.localhost','.local','.internal','.test','.invalid'))
        or any(c in host for c in '\\ \t\r\n')
        or not uri or uri.startswith('/') or any(p in ('','.','..') for p in uri.split('/'))
        or any(c in uri for c in '\\?#\r\n')):
        raise DeliveryError('图片服务返回的上传地址格式无效（域名：'+name[:150]+'）。')
    return 'https://'+name+'/'+quote(uri,safe='/-_.~')


def upload(window, path):
    stage='取得图片上传凭证'
    window.upload_diagnostics={'stage':stage,'host':'','status':'running'}
    try:
        token=window.call('upload-token')
        stage='申请图片上传地址'
        address=imagex('ApplyImageUpload',token)['UploadAddress']
        store=address['StoreInfos'][0]
        url=provider_upload_url(address['UploadHosts'][0],store['StoreUri'])
        window.upload_diagnostics['host']=urlsplit(url).hostname
        stage='上传图片文件'
        data=path.read_bytes()
        mime='image/'+('jpeg' if path.suffix.lower() in ('.jpg','.jpeg') else path.suffix[1:].lower())
        request(url,'PUT',{'Authorization':store['Auth'],'Content-Type':mime,'Content-CRC32':f'{zlib.crc32(data):08x}'},data)
        stage='确认图片上传'
        result=imagex('CommitImageUpload',token,address['SessionKey'])
        if not any(r.get('Uri')==store['StoreUri'] and r.get('UriStatus')==2000 for r in result.get('Results',[])):
            raise DeliveryError('图片服务未确认保存。')
        stage='读取图片地址'
        result=window.call('image-url',{'uri':store['StoreUri']})
        window.upload_diagnostics.update(stage=stage,status='success')
        return result
    except Exception as exc:
        window.upload_diagnostics.update(stage=stage,status='failed')
        detail=str(exc) if isinstance(exc,DeliveryError) else '响应无效，请重试或核对平台状态。'
        raise DeliveryError('掘金'+stage+'失败：'+detail) from None
