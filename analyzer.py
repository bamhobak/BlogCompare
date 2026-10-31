"""키워드 상위 분석 — 그 키워드 상위에 누가 있는지 칸별로 뜯어 본다.

신뢰도(통합검색) 또는 블로그탭 상위 칸을 읽어 종류별로 정보를 붙인다.
  블로그: 닉네임·소개글·개설일·이웃·일방문·대표주제·총 글·키워드 관련 글·제목 변경 이력
  카페:   개설일
  웹:     도메인 등록일·페이지 제목/본문이 키워드와 맞는 정도·사이트 페이지 수(50까지)
"""
import re
import socket
import threading
import time
import urllib.parse
import zlib
from datetime import date, datetime
from html import unescape

import requests
from bs4 import BeautifulSoup

import crawler
from crawler import (
    BLOG_PAT, UR_ITEM_CLASSES, _covers, _norm_match_text, title_matches_keyword,
)

_local = threading.local()


def _sess():
    """스레드마다 세션 하나 — curl_cffi 세션은 스레드 사이에 나눠 쓸 수 없다."""
    s = getattr(_local, 's', None)
    if s is None:
        s = _local.s = crawler._mk_session()
    return s


# ── 검색 화면 읽기 ────────────────────────────────────────────────────────

_CAFE_LINK = re.compile(r'cafe\.naver\.com/([a-zA-Z0-9_-]+)')
_BLOG_HOME = re.compile(r'blog\.naver\.com/([a-zA-Z0-9_.-]+)/?(?:[?#]|$)')
_SKIP_HOST = ('keep.naver.com', 'help.naver.com', 'nid.naver.com')


def _clean(s: str) -> str:
    return re.sub(r'\s+', ' ', s or '').replace('새 창 열림', '').strip()


def _box_kind(box) -> str:
    classes = box.get('class') or []
    if 'fds-kin-ur-desk' in classes:
        return '지식인'
    if 'fds-video-item-2line-profile-desk' in classes:
        return '동영상'
    if 'fds-web-doc-root' in classes:
        return '웹'
    return ''      # 블로그·카페는 링크를 보고 가른다


def _box_links(box) -> list:
    """프로필 줄(블로그명·날짜) 밖의 진짜 링크들. 같은 주소는 한 번만."""
    out = []
    for a in box.find_all('a', href=True):
        h = a['href']
        if not h.startswith('http') or any(x in h for x in _SKIP_HOST):
            continue
        if h not in out:
            out.append(h)
    return out


def _box_title(box) -> str:
    for sp in box.find_all('span'):
        c = ' '.join(sp.get('class') or [])
        if 'sds-comps-text-ellipsis-1' not in c and 'sds-comps-text-type-headline1' not in c:
            continue
        if sp.find_parent(class_='sds-comps-profile'):
            continue       # 프로필 줄의 블로그명·날짜는 제목이 아니다
        t = _clean(sp.get_text(' '))
        if t:
            return re.sub(r'\s+([,.!?])', r'\1', t)
    return ''


def _box_source(box) -> str:
    sp = box.find(class_='sds-comps-profile-info-title-text')
    return _clean(sp.get_text(' ')) if sp else ''


def _box_date(box) -> str:
    for sp in box.find_all(class_='sds-comps-profile-info-subtext'):
        t = _clean(sp.get_text(' '))
        if re.search(r'전$|^\d{4}\.\d{1,2}\.\d{1,2}\.?$|어제|오늘', t):
            return t
    return ''


def _item_from_box(box, rank: int) -> dict:
    kind = _box_kind(box)
    links = _box_links(box)
    it = {
        'rank': rank, 'kind': kind, 'title': _box_title(box),
        'source': _box_source(box), 'date': _box_date(box),
        'link': '', 'blog_id': '', 'cafe_url': '', 'host': '',
        'influencer': any('//in.naver.com/' in h for h in links),
    }

    for h in links:
        m = BLOG_PAT.search(h)
        if m:
            it.update(kind='블로그', blog_id=m.group(1),
                      link=f'https://blog.naver.com/{m.group(1)}/{m.group(2)}')
            return it

    for h in links:
        if kind in ('', '웹'):
            m = _CAFE_LINK.search(h)
            if m and urllib.parse.urlsplit(h).path.strip('/').count('/') >= 1:
                it.update(kind='카페', cafe_url=m.group(1), link=h)
                return it

    if kind == '웹':
        # 웹 칸에 블로그 홈이 뜨는 경우 — 글이 아니라 블로그 자체가 노출된 것
        for h in links:
            m = _BLOG_HOME.search(h)
            if m and h.rstrip('/') != 'https://blog.naver.com':
                it.update(kind='블로그', blog_id=m.group(1), link=h, home=True)
                return it
        real = [h for h in links if not re.fullmatch(r'https?://[^/]+/?', h)]
        it['link'] = (real or links or [''])[0]
    else:
        it['kind'] = kind or '기타'
        real = [h for h in links if '//in.naver.com' not in h
                and not re.fullmatch(r'https?://[^/]+/?', h)]
        it['link'] = (real or links or [''])[0]
    if it['link']:
        it['host'] = urllib.parse.urlsplit(it['link']).hostname or ''
    return it


def parse_trust(html: str, start_rank: int = 1) -> list:
    """통합검색 한 쪽 → 칸 목록 (상자 하나가 순위 하나)."""
    soup = BeautifulSoup(html, 'lxml')
    boxes, taken = [], []
    for nd in soup.find_all(
        lambda t: t.has_attr('class') and any(k in t['class'] for k in UR_ITEM_CLASSES)
    ):
        if any(nd in t.descendants for t in taken):
            continue
        taken.append(nd)
        boxes.append(nd)
    return [_item_from_box(b, start_rank + i) for i, b in enumerate(boxes)]


def parse_blog_tab(html: str) -> list:
    """블로그탭 한 쪽 → 글 목록."""
    soup = BeautifulSoup(html, 'lxml')
    out, seen = [], set()
    for sp in soup.find_all('span', class_=lambda c: c and 'sds-comps-text-type-headline1' in c):
        a = sp.find_parent('a')
        m = BLOG_PAT.search(a.get('href', '')) if a else None
        if not m:
            continue
        key = (m.group(1).lower(), m.group(2))
        if key in seen:
            continue
        seen.add(key)
        # 프로필 줄까지 함께 든 가장 작은 덩어리를 찾아 올라간다
        box = a
        for _ in range(12):
            box = box.parent
            if box is None or box.find(class_='sds-comps-profile'):
                break
        item = {
            'rank': len(out) + 1, 'kind': '블로그', 'blog_id': m.group(1),
            'title': _clean(sp.get_text(' ')), 'cafe_url': '', 'host': '',
            'link': f'https://blog.naver.com/{m.group(1)}/{m.group(2)}',
            'source': _box_source(box) if box else '',
            'date': _box_date(box) if box else '',
            'influencer': bool(box and box.find('a', href=re.compile(r'//in\.naver\.com/'))),
        }
        out.append(item)
    return out


TOP_BLOG = 5        # 블로그탭은 5위 안
# 신뢰도는 1쪽 전체 — 네이버가 시험 중인지 15칸·20칸이 섞여 나온다(최대 20).
# 15칸만 나오는 날에 2쪽을 끌어와 채우지 않는다.


def top_items(keyword: str, area: str) -> list:
    """상위 칸. 성인·차단 예외는 호출부로 올린다."""
    if area == '블로그':
        return parse_blog_tab(crawler._fetch(keyword, '블로그', 1))[:TOP_BLOG]
    return parse_trust(crawler._fetch(keyword, '신뢰도', 1))


def serp_url(keyword: str, area: str) -> str:
    params = ({'query': keyword, 'ssc': 'tab.ur.all', 'sm': 'tab_pge'} if area == '신뢰도'
              else {'ssc': 'tab.blog.all', 'sm': 'tab_opt', 'query': keyword})
    return 'https://search.naver.com/search.naver?' + urllib.parse.urlencode(params)


def clear_cache():
    """조회를 새로 시작할 때 — 이웃수 같은 값이 묵지 않게."""
    _blog_cache.clear()
    _cafe_cache.clear()
    _domain_cache.clear()
    _pages_cache.clear()


# ── 나이·날짜 표기 ────────────────────────────────────────────────────────

def age_text(d) -> str:
    """date → '7년 2개월' / '5개월' / '12일'."""
    if not d:
        return ''
    days = (date.today() - d).days
    if days < 31:
        return f'{max(days, 0)}일'
    months = (date.today().year - d.year) * 12 + date.today().month - d.month
    if date.today().day < d.day:
        months -= 1
    y, m = divmod(max(months, 1), 12)
    if not y:
        return f'{m}개월'
    return f'{y}년 {m}개월' if m else f'{y}년'


def days_since_text(t: str):
    """검색 화면 날짜('3일 전'·'2주 전'·'2026.09.23.') → 지난 날수. 모르면 None."""
    t = (t or '').strip()
    m = re.match(r'(\d+)\s*(분|시간|일|주|개월|달|년)\s*전', t)
    if m:
        n = int(m.group(1))
        return {'분': 0, '시간': 0, '일': n, '주': n * 7,
                '개월': n * 30, '달': n * 30, '년': n * 365}[m.group(2)]
    if t in ('어제',):
        return 1
    if t in ('오늘',):
        return 0
    m = re.match(r'(\d{4})\.(\d{1,2})\.(\d{1,2})', t)
    if m:
        try:
            return (date.today() - date(*map(int, m.groups()))).days
        except ValueError:
            return None
    return None


# ── 블로그 ────────────────────────────────────────────────────────────────

_OPEN_PAT = re.compile(r"(\d{4})\.(\d{2})\.(\d{2})\.\s*'[^']*'\s*시작")
_HIST_PAT = re.compile(
    r"(?<![\d.])(20\d{2})\s+(?=\d{2}\.\d{2}\.)"            # 연도 머리
    r"|(\d{2})\.(\d{2})\.\s*블로그 제목 변경\s*:\s*'([^']*)'"
)
_blog_cache: dict = {}


def _blog_history(blog_id: str) -> dict:
    """개설일(글 삭제에 안 흔들림)과 블로그 제목 변경 이력(대여·순환 계정 판별)."""
    out = {'opened': None, 'renames': 0, 'last_rename': ''}
    r = _sess().get(f'https://blog.naver.com/profile/history.naver?blogId={blog_id}', timeout=12)
    t = re.sub(r'<script.*?</script>|<style.*?</style>', ' ', r.text, flags=re.S)
    t = re.sub(r'<[^>]+>', ' ', t).replace('&nbsp;', ' ')
    t = re.sub(r'\s+', ' ', t)
    m = _OPEN_PAT.search(t)
    if m:
        out['opened'] = date(*map(int, m.groups()))
    year = ''
    for m in _HIST_PAT.finditer(t):
        if m.group(1):
            year = m.group(1)
            continue
        # 개설 첫날 제목을 정한 것은 변경으로 치지 않는다
        if out['opened'] and (year, m.group(2), m.group(3)) == (
                f"{out['opened'].year}", f"{out['opened'].month:02}", f"{out['opened'].day:02}"):
            continue
        out['renames'] += 1
        if not out['last_rename'] and year:     # 이력은 최신순
            out['last_rename'] = f'{year}.{m.group(2)}'
    return out


def _blog_intro(blog_id: str) -> str:
    """프로필의 소개글 = RSS 채널 설명. 여러 줄은 한 줄로 이어 준다."""
    r = _sess().get(f'https://rss.blog.naver.com/{blog_id}.xml', timeout=10)
    m = re.search(r'<description>(.*?)</description>', r.text, re.S)
    text = re.sub(r'<!\[CDATA\[|\]\]>', '', m.group(1)) if m else ''
    lines = [_clean(ln) for ln in unescape(text).splitlines()]
    return ' / '.join(ln for ln in lines if ln)


def blog_info(blog_id: str) -> dict:
    """블로그 하나의 체급. 같은 블로그는 한 번만 받는다."""
    key = blog_id.lower()
    if key in _blog_cache:
        return _blog_cache[key]
    out = {'name': '', 'nick': '', 'buddy': None, 'day': None, 'total_visit': None,
           'topic': '', 'official': False, 'power': False, 'intro': '',
           'opened': None, 'renames': None, 'last_rename': '', 'posts': None}
    s = _sess()
    try:
        d = s.get(f'https://m.blog.naver.com/api/blogs/{blog_id}', timeout=12,
                  headers={'Referer': f'https://m.blog.naver.com/{blog_id}'}).json()
        r = d.get('result') or {}
        out.update(name=r.get('blogName') or '', nick=r.get('nickName') or '',
                   buddy=r.get('subscriberCount'), day=r.get('dayVisitorCount'),
                   total_visit=r.get('totalVisitorCount'),
                   topic=r.get('blogDirectoryName') or '',
                   official=bool(r.get('officialBlog')), power=bool(r.get('powerBlog')))
    except Exception:
        pass
    try:
        out.update(_blog_history(blog_id))
    except Exception:
        pass
    try:
        r = s.get('https://blog.naver.com/PostTitleListAsync.naver', timeout=10, params={
            'blogId': blog_id, 'viewdate': '', 'currentPage': 1, 'categoryNo': 0,
            'parentCategoryNo': 0, 'countPerPage': 5,
        })
        m = re.search(r'"?totalCount"?\s*[:=]\s*"?(\d+)', r.text)
        if m:
            out['posts'] = int(m.group(1))
    except Exception:
        pass
    try:
        out['intro'] = _blog_intro(blog_id)
    except Exception:
        pass
    _blog_cache[key] = out
    return out


KW_TITLE_PAGES = 3                       # 제목 일치는 앞 몇 쪽만 센다(쪽당 20개)
KW_TITLE_SCAN  = KW_TITLE_PAGES * 20


def blog_keyword_posts(blog_id: str, keyword: str, max_pages: int = KW_TITLE_PAGES) -> dict:
    """블로그 안에서 키워드를 검색 → {'total': 검색된 글, 'in_title': 제목에 키워드 든 글}."""
    out = {'total': None, 'in_title': None}
    s = _sess()
    try:
        titled = 0
        for page in range(1, max_pages + 1):
            r = s.get(f'https://m.blog.naver.com/api/blogs/{blog_id}/search/post',
                      params={'query': keyword, 'page': page}, timeout=12,
                      headers={'Referer': f'https://m.blog.naver.com/{blog_id}'}).json()
            res = r.get('result') or {}
            if page == 1:
                out['total'] = res.get('totalCount', 0)
            for p in res.get('list') or []:
                t = re.sub(r'<[^>]+>', '', p.get('title') or '')
                titled += title_matches_keyword(keyword, t)
            if page >= (res.get('totalPage') or 1):
                break
        out['in_title'] = titled
    except Exception:
        pass
    return out


# ── 카페 ──────────────────────────────────────────────────────────────────

_cafe_cache: dict = {}


def cafe_info(cafe_url: str) -> dict:
    key = cafe_url.lower()
    if key in _cafe_cache:
        return _cafe_cache[key]
    out = {'name': '', 'opened': None, 'power': False}
    try:
        r = _sess().get(
            'https://apis.naver.com/cafe-web/cafe2/CafeGateInfo.json',
            params={'cluburl': cafe_url}, timeout=10,
            headers={'Referer': 'https://cafe.naver.com/'},
        ).json()['message']['result']
        v = r.get('cafeInfoView') or {}
        out.update(name=v.get('cafeName') or '', power=bool(v.get('powerCafe')))
        try:
            out['opened'] = datetime.strptime(
                v.get('openDate', ''), '%b %d, %Y %I:%M:%S %p').date()
        except ValueError:
            pass
    except Exception:
        pass
    _cafe_cache[key] = out
    return out


# ── 웹사이트 ──────────────────────────────────────────────────────────────

# 주소 하나를 여럿이 나눠 쓰는 곳 — 도메인 등록일이 그 사이트 나이가 아니다
_SHARED_HOSTS = (
    'vercel.app', 'pages.dev', 'netlify.app', 'github.io', 'web.app', 'firebaseapp.com',
    'imweb.me', 'tistory.com', 'wordpress.com', 'blogspot.com', 'wixsite.com',
    'modoo.at', 'cafe24.com', 'notion.site', 'sites.google.com', 'weebly.com',
)
_KR_2ND = {'co', 'or', 'ne', 're', 'pe', 'go', 'ac', 'hs', 'ms', 'es', 'sc', 'kg', 'mil'}
# 도메인의 지역명은 웹 칸 순위를 가르는 확실한 신호 (2026-10 실측)
_REGION_ROMAN = {
    '서울': 'seoul', '부산': 'busan', '대구': 'daegu', '인천': 'incheon', '광주': 'gwangju',
    '대전': 'daejeon', '울산': 'ulsan', '세종': 'sejong', '제주': 'jeju', '수원': 'suwon',
    '성남': 'seongnam', '분당': 'bundang', '일산': 'ilsan', '고양': 'goyang', '용인': 'yongin',
    '창원': 'changwon', '전주': 'jeonju', '청주': 'cheongju', '천안': 'cheonan', '포항': 'pohang',
    '김해': 'gimhae', '양산': 'yangsan', '평택': 'pyeongtaek', '안산': 'ansan', '안양': 'anyang',
    '강남': 'gangnam', '서초': 'seocho', '송파': 'songpa', '잠실': 'jamsil', '홍대': 'hongdae',
    '여수': 'yeosu', '순천': 'suncheon', '목포': 'mokpo', '군산': 'gunsan', '구미': 'gumi',
}
_domain_cache: dict = {}


def registered_domain(host: str) -> str:
    host = (host or '').lower().rstrip('.')
    for sh in _SHARED_HOSTS:
        if host == sh or host.endswith('.' + sh):
            return ''
    parts = host.split('.')
    if len(parts) >= 3 and parts[-1] == 'kr' and parts[-2] in _KR_2ND:
        return '.'.join(parts[-3:])
    return '.'.join(parts[-2:]) if len(parts) >= 2 else host


def _whois_kr(domain: str):
    with socket.create_connection(('whois.kr', 43), timeout=8) as c:
        c.sendall((domain + '\r\n').encode('ascii'))
        buf = b''
        while True:
            chunk = c.recv(4096)
            if not chunk:
                break
            buf += chunk
    m = re.search(r'Registered Date\s*:\s*(\d{4})\.\s*(\d{2})\.\s*(\d{2})',
                  buf.decode('utf-8', 'replace'))
    return date(*map(int, m.groups())) if m else None


def _rdap(domain: str):
    r = requests.get(f'https://rdap.org/domain/{domain}', timeout=10)
    if r.status_code != 200:
        return None
    for e in r.json().get('events', []):
        if e.get('eventAction') == 'registration':
            return date.fromisoformat(e['eventDate'][:10])
    return None


def _wayback_first(host: str):
    """인터넷 보관소 첫 수집일 — 공유 주소처럼 등록일을 알 수 없을 때의 근사치."""
    r = requests.get(
        'https://web.archive.org/cdx/search/cdx',
        params={'url': host, 'limit': 1, 'output': 'json', 'fl': 'timestamp'}, timeout=8,
    )
    rows = r.json() if r.status_code == 200 and r.text.strip() else []
    if len(rows) > 1:
        ts = rows[1][0]
        return date(int(ts[:4]), int(ts[4:6]), int(ts[6:8]))
    return None


def domain_age(host: str) -> dict:
    """{'date': 등록일, 'how': '등록일'|'첫 수집'|''}"""
    host = (host or '').lower()
    dom = registered_domain(host)
    key = dom or host
    if key in _domain_cache:
        return _domain_cache[key]
    out = {'date': None, 'how': ''}
    if dom:
        try:
            ascii_dom = dom.encode('idna').decode('ascii')
            d = _whois_kr(ascii_dom) if ascii_dom.endswith(('.kr', '.xn--3e0b707e')) else _rdap(ascii_dom)
            if d:
                out = {'date': d, 'how': '등록일'}
        except Exception:
            pass
    if not out['date']:
        try:
            d = _wayback_first(host)
            if d:
                out = {'date': d, 'how': '첫 수집'}
        except Exception:
            pass
    _domain_cache[key] = out
    return out


def _decode(raw: bytes) -> str:
    m = re.search(rb'charset=["\']?([\w-]+)', raw[:6000])
    enc = m.group(1).decode('ascii', 'ignore') if m else 'utf-8'
    try:
        return raw.decode(enc, 'replace')
    except LookupError:
        return raw.decode('utf-8', 'replace')


def page_facts(url: str) -> dict:
    """페이지 제목·본문을 받아 본다. 못 받으면 ok=False."""
    out = {'ok': False, 'title': '', 'desc': '', 'text': ''}
    try:
        r = _sess().get(url, timeout=10, allow_redirects=True)
        if getattr(r, 'status_code', 0) >= 400:
            return out
        soup = BeautifulSoup(_decode(r.content[:2_000_000]), 'lxml')
    except Exception:
        return out
    out['ok'] = True
    if soup.title:
        out['title'] = _clean(soup.title.get_text(' '))
    m = soup.find('meta', attrs={'name': re.compile('^description$', re.I)})
    if m:
        out['desc'] = _clean(m.get('content', ''))
    for t in soup(['script', 'style', 'noscript', 'template']):
        t.decompose()
    out['text'] = _clean(soup.get_text(' '))
    return out


# ── 사이트 페이지 수 (최대 50까지만) ──────────────────────────────────────

PAGE_LIMIT = 50
_CRAWL_FETCHES = PAGE_LIMIT  # 링크를 따라갈 때 받아 볼 페이지 수
_CRAWL_SECONDS = 6
_SKIP_EXT = re.compile(
    r'\.(jpe?g|png|gif|webp|svg|ico|css|js|json|xml|pdf|zip|hwp|docx?|xlsx?|pptx?|mp4|mp3|woff2?)$', re.I)
# 글 목록을 앱 안에서 그려 링크로 셀 수 없는 곳
_NO_PAGE_COUNT = ('instagram.com', 'facebook.com', 'pf.kakao.com', 'youtube.com',
                  'tiktok.com', 'x.com', 'twitter.com')
_pages_cache: dict = {}


def _read_head(url: str, max_bytes: int = 300_000) -> str:
    """앞부분만 받는다 — 사이트맵은 수십 MB 인 곳도 있는데 50개만 세면 된다."""
    r = _sess().get(url, timeout=8, stream=True, allow_redirects=True)
    try:
        if r.status_code >= 400:
            return ''
        buf, got, gz = [], 0, None
        for chunk in r.iter_content():
            if gz is None and not buf:
                gz = zlib.decompressobj(31) if chunk[:2] == b'\x1f\x8b' else False
            if gz:
                chunk = gz.decompress(chunk, max_bytes)
            buf.append(chunk)
            got += len(chunk)
            if got >= max_bytes:
                break
        return b''.join(buf).decode('utf-8', 'replace')
    finally:
        r.close()


def _sitemap_count(url: str, depth: int = 0):
    """사이트맵의 주소 수. PAGE_LIMIT 를 넘으면 그 자리에서 멈춘다. 사이트맵이 아니면 None."""
    t = _read_head(url)
    head = t[:3000]
    if 'sitemapindex' in head:
        total = 0
        for loc in re.findall(r'<loc>\s*([^<\s]+)', t)[:10]:
            if depth < 2:
                total += _sitemap_count(loc.replace('&amp;', '&'), depth + 1) or 0
            if total > PAGE_LIMIT:
                break
        return total
    if 'urlset' in head:
        return t.count('<url>') + t.count('<url ')
    return None


def _find_sitemap(base: str):
    """robots.txt 가 알려 주는 사이트맵, 없으면 흔한 주소."""
    try:
        robots = _read_head(base + '/robots.txt', 50_000)
    except Exception:
        robots = ''
    listed = [] if '<html' in robots[:300].lower() else re.findall(r'(?im)^\s*sitemap:\s*(\S+)', robots)
    found = None
    for u in (listed or [base + '/sitemap.xml', base + '/sitemap_index.xml', base + '/wp-sitemap.xml'])[:4]:
        try:
            n = _sitemap_count(u)
        except Exception:
            continue
        if n is not None:
            found = (found or 0) + n
            if not listed or found > PAGE_LIMIT:     # 흔한 주소는 첫 번째로 충분하다
                break
    return found


def _bare_host(host: str) -> str:
    return re.sub(r'^www\.', '', (host or '').lower())


def _crawl_count(start: str) -> tuple:
    """링크를 따라가며 사이트 안 주소를 센다 → (찾은 수, 끝까지 봤는지)."""
    host = _bare_host(urllib.parse.urlsplit(start).hostname)
    seen, todo, fetched = {start.rstrip('/')}, [start], 0
    until = time.time() + _CRAWL_SECONDS
    while todo and len(seen) <= PAGE_LIMIT:
        if fetched >= _CRAWL_FETCHES or time.time() > until:
            return len(seen), False
        url = todo.pop(0)
        fetched += 1
        try:
            r = _sess().get(url, timeout=5, allow_redirects=True)
            if r.status_code >= 400:
                continue
            soup = BeautifulSoup(_decode(r.content[:1_000_000]), 'lxml')
        except Exception:
            continue
        for a in soup.find_all('a', href=True):
            link = urllib.parse.urljoin(url, a['href'].strip()).split('#')[0]
            sp = urllib.parse.urlsplit(link)
            if sp.scheme not in ('http', 'https') or _bare_host(sp.hostname) != host:
                continue
            if _SKIP_EXT.search(sp.path):
                continue
            key = link.rstrip('/')
            if key not in seen:
                seen.add(key)
                todo.append(link)
                if len(seen) > PAGE_LIMIT:
                    break
    return len(seen), True


def site_pages(host: str, link: str = '') -> dict:
    """{'text': '37'·'50+'·'-', 'how': '사이트맵'·'링크'·''}
    http 만 되는 사이트가 있어 검색 결과 링크의 scheme 을 그대로 쓴다."""
    key = _bare_host(host)
    if key in _pages_cache:
        return _pages_cache[key]
    out = {'text': '-', 'how': ''}
    if not any(key == h or key.endswith('.' + h) for h in _NO_PAGE_COUNT):
        scheme = urllib.parse.urlsplit(link).scheme or 'https'
        base = f'{scheme}://{host}'
        mapped = None
        try:
            mapped = _find_sitemap(base)
        except Exception:
            pass
        if mapped and mapped > PAGE_LIMIT:
            out = {'text': f'{PAGE_LIMIT}+', 'how': '사이트맵'}
        else:
            # 사이트맵이 없거나 일부만 적은 곳이 많아 링크로도 세어 더 큰 쪽을 믿는다
            found, whole = 0, True
            for start in (base + '/', link):     # 홈에 링크가 없는 곳은 검색 결과 페이지에서
                if not start:
                    continue
                try:
                    found, whole = _crawl_count(start)
                except Exception:
                    pass
                if found > 1:
                    break
            if found > PAGE_LIMIT:
                out = {'text': f'{PAGE_LIMIT}+', 'how': '링크'}
            elif mapped and mapped >= found:
                out = {'text': str(mapped), 'how': '사이트맵'}
            elif found > 1:      # 1이면 스크립트로 그리는 사이트라 못 센 것
                out = {'text': f'{found}' if whole else f'{found}+', 'how': '링크'}
    _pages_cache[key] = out
    return out


def title_fit(keyword: str, title: str) -> str:
    """제목이 키워드와 얼마나 맞나 — '문구'(그대로) · '단어'(단어 모두) · '일부' · '없음'."""
    kw, ti = _norm_match_text(keyword), _norm_match_text(title)
    if not kw or not ti:
        return '없음'
    if kw in ti:
        return '문구'
    if title_matches_keyword(keyword, title):
        return '단어'
    toks = [_norm_match_text(t) for t in keyword.split()]
    if any(t and _covers(t, ti) for t in toks if len(t) >= 2):
        return '일부'
    # 붙여 쓴 키워드는 앞뒤 절반 조각이라도 있으면 일부로 본다
    return '일부' if len(kw) >= 3 and (kw[:2] in ti or kw[-2:] in ti) else '없음'


def host_region(keyword: str, host: str) -> str:
    """키워드의 지역명이 주소에 들어 있으면 그 지역명."""
    host = (host or '').lower()
    try:
        uni = host.encode('ascii').decode('idna')
    except (UnicodeError, ValueError):
        uni = host
    for ko, en in _REGION_ROMAN.items():
        if ko in keyword and (en in host or ko in uni):
            return ko
    return ''


def web_relevance(keyword: str, item: dict) -> dict:
    """키워드와 얼마나 맞는 페이지인지 근거. 실측에서 갈린 신호(제목=키워드·주소 지역명·홈) 위주로 본다."""
    page = page_facts(item['link'])
    # 네이버가 보여 주는 제목과 페이지 제목이 다를 수 있다 — 더 잘 맞는 쪽으로 본다
    titles = [t for t in (item['title'], page['title']) if t]
    fit = min((title_fit(keyword, t) for t in titles),
              key=['문구', '단어', '일부', '없음'].index, default='없음')
    starts = any(_norm_match_text(t).startswith(_norm_match_text(keyword)) for t in titles)
    region = host_region(keyword, item['host'])
    path = urllib.parse.urlsplit(item['link']).path.strip('/')
    is_home = not path or path.lower() in ('index.html', 'index.php', 'main', 'home')

    body = page['text']
    kw = _norm_match_text(keyword)
    hits = _norm_match_text(body).count(kw) if kw else 0

    why = [f'제목 {fit}']
    if starts:
        why.append('제목이 키워드로 시작')
    if region:
        why.append(f'주소에 {region}')
    why.append('홈' if is_home else '하위 페이지')
    if not page['ok']:
        why.append('페이지 못 받음(검색 제목만 봄)')
    elif len(body) < 300:
        why.append('본문 없음(스크립트로 그리는 사이트일 수 있음)')
    else:
        why.append(f'본문 {len(body):,}자·키워드 {hits}회')
    return {'why': ' · '.join(why), 'page_title': page['title']}


# ── 칸 하나 채우기 ────────────────────────────────────────────────────────

def enrich(keyword: str, item: dict) -> dict:
    """칸 종류에 맞는 정보를 item 에 채워 돌려준다."""
    kind = item['kind']
    if kind == '블로그':
        item['blog'] = blog_info(item['blog_id'])
        item['kw_posts'] = blog_keyword_posts(item['blog_id'], keyword)
    elif kind == '카페':
        item['cafe'] = cafe_info(item['cafe_url'])
    elif kind == '웹':
        item['web_age'] = domain_age(item['host'])
        item['rel'] = web_relevance(keyword, item)
        item['pages'] = site_pages(item['host'], item['link'])
    return item


def summarize(items: list) -> dict:
    """키워드 한 줄 요약 — 그 자리를 잡으려면 어느 체급이어야 하나."""
    kinds = {}
    for it in items:
        kinds[it['kind']] = kinds.get(it['kind'], 0) + 1
    blogs = [it for it in items if it['kind'] == '블로그' and it.get('blog')]
    buddies = [b['blog']['buddy'] for b in blogs if b['blog']['buddy'] is not None]
    opened = [b['blog']['opened'] for b in blogs if b['blog']['opened']]
    ages = [days_since_text(it['date']) for it in items
            if it['kind'] in ('블로그', '카페', '지식인', '동영상') and not it.get('home')]
    ages = [a for a in ages if a is not None]
    return {
        'kinds': kinds,
        'fresh': sum(a <= 7 for a in ages),
        'dated': len(ages),
        'buddy_max': max(buddies) if buddies else None,
        'youngest': max(opened) if opened else None,
    }
