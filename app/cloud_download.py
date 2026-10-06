"""Bounded streaming HTTPS downloader. No proxy, cookies, credentials or private-network access."""
from contextlib import contextmanager
import hashlib
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import socket
import ssl
import time
from urllib.parse import urlsplit,urlunsplit,urljoin,urlencode,quote
from import_common import MAX_FILE,ImportFailure,atomic_json,digest_file

YANDEX_HOSTS={'disk.yandex.ru','disk.yandex.com','disk.yandex.by','disk.yandex.kz','disk.yandex.com.tr','yadi.sk'}
RESERVE=10*1024**3

def parsed_url(url):
    if not isinstance(url,str) or not 1<=len(url)<=8192 or any(ord(c)<=32 or ord(c)==127 for c in url):
        raise ImportFailure('CLOUD_URL')
    try:
        parsed=urlsplit(url)
        if parsed.scheme!='https' or not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.port not in (None,443):
            raise ValueError()
        if parsed.fragment:raise ValueError()
        host=parsed.hostname.encode('idna').decode('ascii').lower()
        if host.endswith('.') or '%' in host or '\\' in host:raise ValueError()
        if host=='localhost' or host.endswith(('.localhost','.local','.internal')):raise ValueError()
        try:
            addr=ipaddress.ip_address(host)
            if not public_ip(addr):raise ValueError()
        except ValueError:
            # Distinguish a DNS name from a rejected IP literal.
            if re.fullmatch(r'[0-9.]+',host) or ':' in host:raise
        path=quote(parsed.path or '/',safe="/%:@!$&'()*+,;=-._~")
        query=quote(parsed.query,safe="%/?@:!$&'()*+,;=-._~[]")
        return host,path+('?' +query if query else '')
    except (ValueError,UnicodeError):raise ImportFailure('CLOUD_URL')

def public_ip(addr):
    # Reject special IPv6 translation/tunnelling forms as well as ordinary LAN/metadata ranges.
    return addr.is_global and not (addr.is_multicast or addr.is_unspecified or addr.is_reserved
           or addr.is_loopback or addr.is_link_local or getattr(addr,'ipv4_mapped',None)
           or getattr(addr,'sixtofour',None) or getattr(addr,'teredo',None)
           or (addr.version==6 and addr in ipaddress.ip_network('64:ff9b::/96')))

def public_addresses(host):
    try:answers=socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)
    except OSError:raise ImportFailure('CLOUD_NETWORK')
    ips=[]
    for family,kind,proto,canon,sockaddr in answers:
        addr=ipaddress.ip_address(sockaddr[0])
        if not public_ip(addr):raise ImportFailure('CLOUD_URL')
        if str(addr) not in ips:ips.append(str(addr))
    if not ips:raise ImportFailure('CLOUD_NETWORK')
    return ips

class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self,host,ip):
        super().__init__(host,port=443,timeout=30,context=ssl.create_default_context());self.pinned_ip=ip
    def connect(self):
        # Connect to the already-validated literal address; TLS still validates the real hostname.
        raw=socket.create_connection((self.pinned_ip,443),timeout=self.timeout)
        try:self.sock=self._context.wrap_socket(raw,server_hostname=self.host)
        except BaseException:raw.close();raise

@contextmanager
def response_for(url,headers=None):
    conn=None
    try:
        for hop in range(6):
            host,target=parsed_url(url)
            ips=public_addresses(host)
            for ip in ips:
                try:
                    conn=PinnedHTTPS(host,ip)
                    conn.request('GET',target,headers={'User-Agent':'ShtabAI-Import/0.8.1','Accept-Encoding':'identity',**(headers or {})})
                    response=conn.getresponse();break
                except (OSError,http.client.HTTPException):
                    if conn:conn.close();conn=None
            else:raise ImportFailure('CLOUD_NETWORK')
            if response.status in (301,302,303,307,308):
                destination=response.getheader('Location')
                if not destination:raise ImportFailure('CLOUD_HTTP')
                url=urljoin(url,destination);conn.close();conn=None;continue
            yield response
            return
        raise ImportFailure('CLOUD_REDIRECT')
    finally:
        if conn:conn.close()

def resolve_yandex(link):
    host,_=parsed_url(link)
    if host not in YANDEX_HOSTS:raise ImportFailure('CLOUD_URL')
    api='https://cloud-api.yandex.net/v1/disk/public/resources/download?'+urlencode({'public_key':link})
    with response_for(api) as res:
        if res.status in (401,403,404,410):raise ImportFailure('CLOUD_ACCESS')
        if res.status==429:raise ImportFailure('CLOUD_RATE_LIMIT')
        if res.status!=200:raise ImportFailure('CLOUD_HTTP')
        body=res.read(65537)
        if len(body)>65536:raise ImportFailure('CLOUD_HTTP')
        try:href=json.loads(body)['href']
        except (ValueError,KeyError,TypeError):raise ImportFailure('CLOUD_HTTP')
    parsed_url(href)
    return href

def strong_etag(value):
    return value if value and re.fullmatch(r'"[^"\r\n]{1,512}"',value) else None

def download(link,kind,folder,tick,opener=None):
    """Retries preserve partial download; resume only with a strong HTTP entity validator."""
    opener=opener or response_for
    folder=Path(folder);folder.mkdir(mode=0o750,parents=True,exist_ok=True)
    partial=folder/'cloud.part';resume=folder/'cloud-resume.json'
    fingerprint=hashlib.sha256(link.encode()).hexdigest()
    etag=None;offset=0
    if partial.exists() and resume.exists():
        try:
            previous=json.loads(resume.read_text())
            if previous.get('source')==fingerprint:
                etag=strong_etag(previous.get('etag'));offset=partial.stat().st_size if etag else 0
        except (OSError,ValueError):pass
    if offset>MAX_FILE:raise ImportFailure('CLOUD_TOO_LARGE')
    url=resolve_yandex(link) if kind=='YANDEX' else link
    headers={'Range':f'bytes={offset}-','If-Range':etag} if offset else {}
    # On 416 the earlier attempt may have completed exactly before interruption.
    # Restart from zero rather than trust a content length without a fresh body.
    with opener(url,headers) as res:
        if res.status==416 and offset:
            offset=0;etag=None
            retry=True
        else:retry=False
        if not retry:
            try:return consume(res,folder,partial,resume,fingerprint,offset,etag,tick)
            except ImportFailure as exc:
                if str(exc)!='CLOUD_CHANGED' or not offset:raise
    with opener(url,{}) as res:
        return consume(res,folder,partial,resume,fingerprint,0,None,tick)

def consume(res,folder,partial,resume,fingerprint,offset,etag,tick):
    if res.status in (401,403,404,410):raise ImportFailure('CLOUD_ACCESS')
    if res.status==429:raise ImportFailure('CLOUD_RATE_LIMIT')
    if res.status not in (200,206):raise ImportFailure('CLOUD_HTTP')
    if res.getheader('Content-Encoding','identity').lower() not in ('','identity'):raise ImportFailure('CLOUD_HTTP')
    content_type=res.getheader('Content-Type','').split(';')[0].lower()
    if content_type in ('text/html','application/xhtml+xml','application/json','application/zip','application/x-zip-compressed'):
        raise ImportFailure('CLOUD_NOT_FILE')
    if res.status==206:
        match=re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)',res.getheader('Content-Range',''))
        if not offset or not match or int(match[1])!=offset or int(match[2])!=int(match[3])-1:raise ImportFailure('CLOUD_CHANGED')
        if res.getheader('ETag')!=etag:raise ImportFailure('CLOUD_CHANGED')
        expected=int(match[3])
        if expected<=offset:raise ImportFailure('CLOUD_CHANGED')
    else:
        offset=0;etag=strong_etag(res.getheader('ETag'))
        length=res.getheader('Content-Length')
        try:expected=int(length) if length is not None else None
        except ValueError:raise ImportFailure('CLOUD_HTTP')
    if expected is not None and not 1<=expected<=MAX_FILE:raise ImportFailure('CLOUD_TOO_LARGE')
    if shutil.disk_usage(folder).free < (expected-offset if expected else MAX_FILE-offset)+RESERVE:
        raise ImportFailure('DISK_SPACE')
    atomic_json(resume,dict(source=fingerprint,etag=etag))
    received=offset;started=time.monotonic();next_tick=0
    with open(partial,'ab' if offset else 'wb') as out:
        while True:
            if time.monotonic()-started>12*3600:raise ImportFailure('CLOUD_NETWORK')
            block=res.read(1024*1024)
            if not block:break
            received+=len(block)
            if received>MAX_FILE or (expected is not None and received>expected):raise ImportFailure('CLOUD_TOO_LARGE')
            if shutil.disk_usage(folder).free < len(block)+RESERVE:raise ImportFailure('DISK_SPACE')
            out.write(block)
            if time.monotonic()>=next_tick:
                tick(received,expected);next_tick=time.monotonic()+5
        out.flush();os.fsync(out.fileno())
    if received==0 or (expected is not None and received!=expected):raise ImportFailure('CLOUD_INCOMPLETE')
    target=folder/'source.media';os.replace(partial,target)
    sha=digest_file(target)
    atomic_json(folder/'source.json',dict(sha256=sha,size=received))
    tick(received,received)
    return target,sha,received
