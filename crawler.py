import json as _json
import re
import time
import random
import threading
import urllib.parse

import requests
from bs4 import BeautifulSoup
from html import unescape as _unescape

# 네이버 검색은 TLS 지문(JA3)으로 봇을 거른다.
# 일반 requests(OpenSSL) 핸드셰이크는 403 '검색 서비스 이용이 제한되었습니다' 페이지를 받는다.
# curl_cffi 로 크롬 TLS 지문을 흉내내야 정상 응답을 받는다.
try:
    from curl_cffi import requests as _creq
    _IMPERSONATE = 'chrome'
except Exception:      # curl_cffi 없으면 기존 requests 로 폴백 (차단될 수 있음)
    _creq = None
    _IMPERSONATE = ''

_HEADERS = {
    'Accept-Language': 'ko-KR,ko;q=0.9',
    'Referer': 'https://search.naver.com/',
}
# requests 폴백일 때만 UA 를 직접 넣는다.
# curl_cffi 는 impersonate 가 정한 UA 를 그대로 써야 TLS 지문과 어긋나지 않는다.
_FALLBACK_UA = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
)


def _mk_session():
    if _creq is not None:
        s = _creq.Session(impersonate=_IMPERSONATE)
        s.headers.update(_HEADERS)
    else:
        s = requests.Session()
        s.headers.update(dict(_HEADERS, **{'User-Agent': _FALLBACK_UA}))
    return s


_SESSION = _mk_session()

# 실측(2026-09-13): 검색 요청은 **세션(쿠키)당 정확히 100회**가 상한이다.
#   · 101회째부터 403 '검색 서비스 이용이 제한' 페이지
#   · 같은 세션으로는 쉬어도 안 풀리고, 새 세션이면 즉시 통과(연속 5회 확인)
#   · 요청 간격을 늘려도 상한은 그대로다 → 딜레이가 아니라 세션 교체가 답
# 그래서 상한에 닿기 전에 미리 갈아탄다.
ROTATE_AT = 80
_req_count = 0


class NaverBlocked(Exception):
    """네이버가 검색을 차단했을 때 (403 제한 페이지)."""


class AdultBlockedError(Exception):
    """성인 키워드 — 성인인증 로그인 쿠키 없이는 결과가 안 나온다 (쿠키 없음/만료).
    이 경우 '조회된 포스팅이 없습니다'로 남기면 안 된다(실제로는 노출일 수 있음)."""


# ── 성인 키워드 (KeywordManager 와 같은 방식) ───────────────────────────────
# 로그인 안 하면 결과 대신 이 문구만 나온다(블로그·통합검색 모두 0칸).
_ADULT_MARK = '청소년에게 노출하기 부적합한 검색결과를 제외'
_ADULT_SESSION = None        # 성인인증 계정 쿠키 전용 세션 — 일반 검색과 안 섞이게
last_was_adult = False       # 방금 조회한 키워드가 성인 키워드였나 (호출한 쪽이 표시에 씀)


def _adult_blocked(html: str, search_type: str) -> bool:
    """'청소년 제외' 문구가 있고 결과가 0칸일 때만 막힌 것으로 본다
    (로그인돼 결과가 나오는 화면에도 문구가 섞여 있을 수 있어 문구만으론 판정하지 않는다)."""
    return _ADULT_MARK in html and not _parse(html, 1, search_type)


# 수집기 서버(성인 쿠키가 있는 PC)에 '이 주소 좀 대신 받아 줘' 하고 부탁하는 길.
# 밖에서는 이미 열린 클로드 서버(8787)의 /ad/ 전달 길로만 닿는다.
ADULT_SERVERS = ['bamhobak.iptime.org:8787/ad', '127.0.0.1:8792']
_adult_server_ok = None      # 한 번 통한 주소를 기억 (매번 다 두드리지 않게)


def set_adult_servers(addrs):
    """config.json 의 adult_servers 로 주소를 바꿀 수 있게."""
    global ADULT_SERVERS, _adult_server_ok
    if addrs:
        ADULT_SERVERS = list(addrs)
        _adult_server_ok = None


last_server_why = ''        # 서버가 알려 준 실패 이유 (화면에 그대로 보여 준다)


def _fetch_via_server(url: str) -> str:
    """성인 쿠키를 가진 수집기 서버가 대신 받아 준 화면을 가져온다.
    순위를 세는 일은 여기(부탁한 쪽)서 그대로 한다 — 규칙이 두 군데로 갈라지지 않게.
    못 닿으면 '' 를 돌려준다(그러면 호출부가 '성인 키워드'로 알린다)."""
    global _adult_server_ok, last_server_why
    import json as _j
    import urllib.parse as _up
    last_server_why = ''
    order = ([_adult_server_ok] if _adult_server_ok else []) +             [a for a in ADULT_SERVERS if a != _adult_server_ok]
    for addr in order:
        try:
            ask = f'http://{addr}/get?url=' + _up.quote(url, safe='')
            r = requests.get(ask, timeout=60)
            try:
                d = _j.loads(r.text)
            except Exception:
                d = {}
            if r.status_code == 200 and d.get('ok') and d.get('html'):
                _adult_server_ok = addr
                return d['html']
            # 서버에는 닿았는데 거절당했다 — 이유를 그대로 전한다(어디를 봐야 하는지 알게)
            if d.get('why'):
                _adult_server_ok = addr
                last_server_why = f'수집기 서버: {d["why"]}'
        except Exception:
            continue
    if not last_server_why:
        last_server_why = '수집기 서버에 닿지 않음'
    return ''


def load_adult_cookie(base_dir) -> dict:
    """카테고리 수집과 같은 파일(.claude_api.env)의 NAVER_NID_AUT/SES.
    앱 폴더 → 그 위 폴더 순으로 찾는다. 서버 수집기 PC 에만 있다."""
    import os
    for d in (base_dir, os.path.dirname(base_dir)):
        out = {}
        try:
            with open(os.path.join(d, '.claude_api.env'), encoding='utf-8') as f:
                for line in f:
                    k, _, v = line.strip().partition('=')
                    if k in ('NAVER_NID_AUT', 'NAVER_NID_SES') and v.strip():
                        out[k[6:]] = v.strip()          # NID_AUT / NID_SES
        except Exception:
            pass
        if len(out) == 2:
            return out
    return {}


def set_adult_cookie(cookies: dict):
    """성인 키워드용 로그인 세션 준비 (빈 dict 면 끔)."""
    global _ADULT_SESSION
    if not cookies:
        _ADULT_SESSION = None
        return
    sess = _mk_session()
    for k, v in cookies.items():
        sess.cookies.set(k, v, domain='.naver.com')
    _ADULT_SESSION = sess


def has_adult_cookie() -> bool:
    return _ADULT_SESSION is not None


def _rotate_session():
    """쿠키를 버리고 새 세션으로. 세션당 요청 한도가 여기서 리셋된다."""
    global _SESSION, _session_ready, _req_count
    _SESSION = _mk_session()
    _session_ready = False
    _req_count = 0


def _is_blocked(resp) -> bool:
    return (getattr(resp, 'status_code', 0) == 403
            or '검색 서비스 이용이 제한' in resp.text[:40000])


def _check_blocked(resp):
    if _is_blocked(resp):
        raise NaverBlocked(
            '네이버가 검색을 차단했습니다. 크롬에서 search.naver.com 접속 후 '
            '[제한 해제] 보안문자를 통과시키거나 잠시 후 다시 시도하세요.'
        )
    return resp


def _get(url, **kw):
    """검색 요청 한 번. 상한이 가까우면 미리 세션을 갈아타고,
    그래도 막히면 세션을 새로 만들어 재시도한다(총 3회)."""
    global _req_count
    for attempt in range(3):
        if _req_count >= ROTATE_AT:
            _rotate_session()
            _init_session()
        resp = _SESSION.get(url, **kw)
        _req_count += 1
        if not _is_blocked(resp):
            return resp
        # 막혔다 — 세션을 버리고 새로 만들어 다시 시도
        _rotate_session()
        _init_session()
        if attempt < 2:
            time.sleep(1.0 + attempt * 2)
    return resp          # 3회 모두 차단 — 호출부가 _check_blocked 로 처리

_session_ready = False
_session_lock  = threading.Lock()

PER_PAGE      = 30   # 블로그: 30개씩
PER_PAGE_UR   = 20   # 신뢰도: page당 20개, page=2~10 (최대 180위)

BLOG_PAT     = re.compile(r'blog\.naver\.com/([a-zA-Z0-9_.-]+)/(\d+)')
# 통합검색(신뢰도) 결과 한 건을 감싸는 상자들 — 종류마다 이름이 다르다 (2026-10-15 개편)
UR_ITEM_CLASSES = (
    'fds-ugc-ur-desk',                    # 블로그·카페
    'fds-web-doc-root',                   # 웹사이트
    'fds-kin-ur-desk',                    # 지식인
    'fds-video-item-2line-profile-desk',  # 동영상
)
_BLOG_ID_PAT = re.compile(r'blog\.naver\.com/([a-zA-Z0-9_.-]+)(?:/|$)')
_RSS_ID_PAT  = re.compile(r'rss\.blog\.naver\.com/([a-zA-Z0-9_.-]+)')
_CAFE_PAT    = re.compile(r'cafe\.naver\.com')

_resolve_cache:      dict = {}
_blog_name_cache:    dict = {}


def resolve_blog_id(login_id: str) -> str:
    """
    Naver login ID → 블로그 주소 변환.
    rss.blog.naver.com/{loginId}.xml 이 실제 블로그 주소로 리다이렉트되는 것을 이용.
    예: rtyrty999.xml → rss.blog.naver.com/ceranz
    """
    key = login_id.lower()
    if key in _resolve_cache:
        return _resolve_cache[key]

    resolved = key
    try:
        resp = _SESSION.get(
            f'https://rss.blog.naver.com/{login_id}.xml',
            timeout=10,
            allow_redirects=True,
        )
        m = _RSS_ID_PAT.search(resp.url)
        if m:
            candidate = m.group(1).lower()
            if candidate and candidate != key:
                resolved = candidate
    except Exception:
        pass

    _resolve_cache[key] = resolved
    return resolved


def fetch_blog_name(blog_id: str) -> str:
    key = blog_id.lower()
    if key in _blog_name_cache:
        return _blog_name_cache[key]
    name = ''
    try:
        resp = _SESSION.get(f'https://rss.blog.naver.com/{blog_id}', timeout=10, allow_redirects=True)
        m = re.search(r'<title><!\[CDATA\[([^\]]+)\]\]></title>', resp.text)
        if m:
            name = m.group(1).strip()
    except Exception:
        pass
    _blog_name_cache[key] = name
    return name


# ── 블로그 프로필 (닉네임 / 이웃수 / 블로그명) ────────────────────────────────
# 모바일 블로그 홈 한 번으로 셋 다 얻는다(RSS 추가 호출 불필요).
# class 이름에 해시가 붙어 수시로 바뀌므로(buddy__UnXlf 등) class 로 찾지 않는다.
_NICK_PAT  = re.compile(r'data-click-area="ebc\.nick"[^>]*>([^<]+)<')
_BUDDY_PAT = re.compile(r'([\d,]+)\s*명의\s*이웃')
_TITLE_PAT = re.compile(r'<title>([^<]*)</title>')
_profile_cache: dict = {}


def fetch_blog_profile(blog_id: str) -> dict:
    """{'nickname', 'buddy', 'blog_name'} — 못 읽으면 빈 값/-1.

    buddy 는 숫자(int), 못 읽으면 -1. 같은 블로그는 한 번만 받아 캐시한다.
    """
    key = blog_id.lower()
    if key in _profile_cache:
        return _profile_cache[key]

    out = {'nickname': '', 'buddy': -1, 'blog_name': ''}
    try:
        resp = _SESSION.get(
            f'https://m.blog.naver.com/{blog_id}', timeout=12, allow_redirects=True,
        )
        t = resp.text
        m = _NICK_PAT.search(t)
        if m:
            out['nickname'] = _unescape(m.group(1).strip())
        m = _BUDDY_PAT.search(t)
        if m:
            try:
                out['buddy'] = int(m.group(1).replace(',', ''))
            except ValueError:
                pass
        m = _TITLE_PAT.search(t)
        if m:
            # "블로그명 : 네이버 블로그" — 블로그명 자체에 ' : ' 가 들어가는 경우가
            # 있어 꼬리만 떼어낸다.
            name = _unescape(m.group(1).strip())
            name = re.sub(r'\s*:\s*네이버\s*블로그\s*$', '', name)
            out['blog_name'] = name.strip()
    except Exception:
        pass

    # 모바일 홈이 비어 있으면 블로그명만이라도 RSS 로 보완
    if not out['blog_name']:
        out['blog_name'] = fetch_blog_name(blog_id)

    _profile_cache[key] = out
    return out


def fetch_post_date(blog_id: str, log_no: str) -> str:
    try:
        resp = _SESSION.get(
            f'https://m.blog.naver.com/{blog_id}/{log_no}',
            timeout=10,
            allow_redirects=True,
        )
        m = re.search(r'(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})\.?', resp.text)
        if m:
            return f'{m.group(1)}.{m.group(2).zfill(2)}.{m.group(3).zfill(2)}'
    except Exception:
        pass
    return ''


def _init_session():
    """새 세션 예열. search.naver.com 방문 쿠키가 없으면 첫 요청부터 403 이라
    이 과정이 필요하다. 여기서 쓴 검색 1회도 세션 한도(100회)에
    포함되므로 카운트에 반영한다."""
    global _session_ready, _req_count
    with _session_lock:
        if not _session_ready:
            try:
                _SESSION.get('https://www.naver.com/', timeout=10)
                _SESSION.get(
                    'https://search.naver.com/search.naver',
                    params={'where': 'view', 'sm': 'tab_hty.brg', 'query': '블로그'},
                    timeout=10,
                )
                _req_count += 1
                _session_ready = True
            except Exception:
                pass


def _fetch(keyword: str, search_type: str, start: int) -> str:
    """검색 한 쪽. 성인 키워드는 로그인 세션으로 다시 받아 본다
    (KeywordManager 와 같은 방식 — 0건으로 넘기지 않고 AdultBlockedError 로 알린다)."""
    params = (
        {'query': keyword, 'ssc': 'tab.ur.all', 'sm': 'tab_pge', 'start': start}
        if search_type == '신뢰도' else
        {'ssc': 'tab.blog.all', 'sm': 'tab_opt', 'query': keyword, 'start': start}
    )
    url = 'https://search.naver.com/search.naver'

    global last_was_adult
    # 이미 성인으로 드러난 키워드는 처음부터 로그인 세션으로
    use_login = last_was_adult and _ADULT_SESSION is not None
    if use_login:
        resp = _ADULT_SESSION.get(url, params=params, timeout=15)
    else:
        resp = _get(url, params=params, timeout=15)
    _check_blocked(resp)
    resp.raise_for_status()

    if _adult_blocked(resp.text, search_type):
        last_was_adult = True
        if not use_login and _ADULT_SESSION is not None:   # 몰랐던 성인 키워드 — 로그인으로 다시
            resp = _ADULT_SESSION.get(url, params=params, timeout=15)
            _check_blocked(resp)
            resp.raise_for_status()
        if _adult_blocked(resp.text, search_type):
            # 이 PC 엔 성인 쿠키를 두지 않는다 — 쿠키를 가진 수집기 서버에 대신 부탁한다
            full = url + '?' + urllib.parse.urlencode(params)
            html = _fetch_via_server(full)
            if html and not _adult_blocked(html, search_type):
                return html
            if _ADULT_SESSION is not None and (
                    'nidlogin.logout' in resp.text or '로그아웃' in resp.text):
                why = '로그인은 됐지만 성인인증이 안 된 계정'
            elif html:
                why = '수집기 서버도 성인 화면을 못 받음'
            else:
                why = last_server_why or '수집기 서버에 닿지 않음'
            raise AdultBlockedError(f'성인 키워드 — {why}')
    return resp.text


def _parse(html: str, start_rank: int, search_type: str = '') -> list:
    """
    Extract blog post results from Naver search HTML.

    신뢰도(통합검색): 결과 상자(fds-*)를 문서 순서대로 세어 순위를 매긴다.
            블로그가 아닌 상자(웹사이트·카페·지식인·동영상)는 순위 자리만 차지.
    블로그(블로그탭): sds-comps-text-type-headline1 span 기반 파싱.
    """
    posts = []
    seen  = set()
    soup  = BeautifulSoup(html, 'lxml')

    if search_type == '신뢰도':
        # 2026-10-15 통합검색 개편 — 결과 한 건이 종류별 상자 하나로 나온다.
        #   웹사이트 fds-web-doc-root · 블로그/카페 fds-ugc-ur-desk
        #   지식인 fds-kin-ur-desk  · 동영상 fds-video-item-2line-profile-desk
        # 상자를 문서 순서대로 세면 한 쪽 20개와 정확히 맞는다(실측).
        # 옛 방식(headline1 링크 세기)은 개편 뒤 headline1 이 웹사이트 제목에만
        # 쓰여서 블로그가 하나도 안 잡혔다.
        rank = start_rank
        boxes, taken = [], []
        for nd in soup.find_all(
            lambda t: t.has_attr('class') and any(k in t['class'] for k in UR_ITEM_CLASSES)
        ):
            if any(nd in t.descendants for t in taken):   # 겹쳐 있는 바깥 껍데기는 한 번만
                continue
            taken.append(nd)
            boxes.append(nd)

        for box in boxes:
            for a in box.find_all('a', href=True):
                m = BLOG_PAT.search(a['href'])
                if not m:
                    continue
                title_span = a.find(
                    'span',
                    class_=lambda c: c and 'sds-comps-text-ellipsis-1' in c,
                )
                title = (re.sub(r' +', ' ', title_span.get_text(separator=' ')).strip()
                         if title_span else '')
                if not title:          # 제목 없는 링크(블로그 홈·프로필)는 글이 아니다
                    continue
                key = (m.group(1).lower(), m.group(2))
                if key not in seen:
                    seen.add(key)
                    blog_id = m.group(1)
                    posts.append({
                        'rank':      rank,
                        'title':     title,
                        'blog_name': '',
                        'id':        blog_id,
                        'date':      '',
                        'link':      f'https://blog.naver.com/{blog_id}/{m.group(2)}',
                    })
                break
            rank += 1   # 블로그든 카페·웹사이트·지식인이든 상자 하나가 순위 하나
        return posts

    # 블로그: headline1 기반
    rank = start_rank
    for link in soup.find_all('a', href=BLOG_PAT):
        href = link.get('href', '')
        m = BLOG_PAT.search(href)
        if not m:
            continue
        title_span = link.find(
            'span',
            class_=lambda c: c and 'sds-comps-text-type-headline1' in c,
        )
        if title_span is None:
            continue
        key = (m.group(1).lower(), m.group(2))
        if key in seen:
            continue
        seen.add(key)
        blog_id = m.group(1)
        title   = re.sub(r' +', ' ', title_span.get_text(separator=' ')).strip()
        posts.append({
            'rank':      rank,
            'title':     title,
            'blog_name': '',
            'id':        blog_id,
            'date':      '',
            'link':      f'https://blog.naver.com/{blog_id}/{m.group(2)}',
        })
        rank += 1

    if not posts:
        for blog_id, log_no in BLOG_PAT.findall(html):
            link = f'https://blog.naver.com/{blog_id}/{log_no}'
            if link in seen:
                continue
            seen.add(link)
            posts.append({
                'rank': rank, 'title': '', 'blog_name': '',
                'id': blog_id, 'date': '', 'link': link,
            })
            rank += 1

    return posts


def search_naver(
    keyword: str,
    search_type: str,
    count: int,
    stop_flag: threading.Event = None,
    progress_cb=None,
) -> list:
    _init_session()

    global last_was_adult
    last_was_adult = False       # 키워드마다 새로 판정한다

    all_posts: list = []
    seen_keys: set  = set()
    start = 1
    per_page = PER_PAGE_UR if search_type == '신뢰도' else PER_PAGE

    # 신뢰도: page=2~10, 20개씩 (최대 180위)
    if search_type == '신뢰도':
        count = min(count, 180)

    while True:
        if stop_flag and stop_flag.is_set():
            break
        # 신뢰도: 다음 페이지 시작이 조회 범위를 초과하면 종료
        if search_type == '신뢰도' and start > count:
            break
        # 블로그: 충분히 수집됐으면 종료
        if search_type == '블로그' and len(all_posts) >= count:
            break

        try:
            html = _fetch(keyword, search_type, start)
        except AdultBlockedError:
            raise          # 성인 키워드는 0건이 아니라 '성인'으로 알린다
        except NaverBlocked:
            raise          # 차단은 조용히 0건으로 넘기지 말고 화면에 알린다
        except Exception:
            break

        page_posts = _parse(html, start, search_type)
        if not page_posts:
            break

        new_count = 0
        for post in page_posts:
            key = (post['id'].lower(), post['link'])
            if key in seen_keys:
                continue
            seen_keys.add(key)
            if search_type == '신뢰도':
                # 조회수(count) 이내 순위만 포함
                if post['rank'] > count:
                    continue
            else:
                if len(all_posts) >= count:
                    break
                post['rank'] = len(all_posts) + 1
            all_posts.append(post)
            new_count += 1

        if progress_cb:
            progress_cb(len(all_posts), count)

        # 신뢰도는 중간 페이지가 전부 중복이어도 이후 페이지에 새 결과가 있을 수 있음
        # → 신뢰도는 page_posts 비어 있을 때만 break (위 if not page_posts 에서 처리)
        if search_type != '신뢰도' and new_count == 0:
            break

        start += per_page
        time.sleep(random.uniform(0.6, 1.4))

    return all_posts


# ── 키워드 체크 (블로그탭/카페탭 최근 1주일 첫 페이지) ─────────────────────

def _norm_match_text(s: str) -> str:
    """매칭용 정규화: 소문자화 후 한글/영문/숫자만 남김."""
    return re.sub(r'[^0-9a-z가-힣]', '', s.lower())


def _covers(token: str, text: str) -> bool:
    """token을 2글자 이상 조각들로 나눠 모든 조각이 text에 있으면 True."""
    if token in text:
        return True
    n = len(token)
    if n < 2:
        return False
    ok = [False] * (n + 1)
    ok[0] = True
    for i in range(2, n + 1):
        for j in range(i - 1):
            if ok[j] and i - j >= 2 and token[j:i] in text:
                ok[i] = True
                break
    return ok[n]


def title_matches_keyword(keyword: str, title: str) -> bool:
    """
    키워드의 단어가 모두 제목에 들어 있으면 유효.
    붙여쓴 키워드(초등학교운동회업체)도 제목에 실제 존재하는
    조각(초등학교/운동회/업체)으로 나눠지면 유효로 판정한다.
    """
    text = _norm_match_text(title)
    for token in keyword.split():
        t = _norm_match_text(token)
        if t and not _covers(t, text):
            return False
    return True


def _first_page_params(keyword: str, tab: str) -> dict:
    if tab == '블로그':
        return {'ssc': 'tab.blog.all', 'sm': 'tab_opt',
                'query': keyword, 'nso': 'so:r,p:1w', 'qdt': '0'}
    return {'ssc': 'tab.cafe.all', 'sm': 'tab_jum',
            'query': keyword, 'nso': 'so:r,p:1w'}


def first_page_search_url(keyword: str, tab: str) -> str:
    """키워드 체크가 조회하는 것과 동일한 네이버 검색 페이지 URL."""
    return ('https://search.naver.com/search.naver?'
            + urllib.parse.urlencode(_first_page_params(keyword, tab)))


def fetch_monthly_volumes(keywords: list, api_key: str, secret_key: str,
                          customer_id: str) -> dict:
    """
    네이버 검색광고 keywordstool API로 월간 검색수(PC+모바일 합계) 조회.
    반환: {키워드: int} — 조회 실패/미검출 키워드는 키 없음.
    '< 10' 응답은 5로 근사한다.
    """
    import hmac
    import hashlib
    import base64

    def _num(v):
        if isinstance(v, str) and '<' in v:
            return 5
        try:
            return int(v)
        except Exception:
            return 0

    out: dict = {}
    # API는 공백 포함 키워드를 거부 → 공백 제거형을 힌트로 사용
    norm = {re.sub(r'\s+', '', kw).upper(): kw for kw in keywords if kw.strip()}
    hints = list(norm.keys())
    for i in range(0, len(hints), 5):  # 호출당 힌트 최대 5개
        chunk = hints[i:i + 5]
        ts = str(int(time.time() * 1000))
        msg = f'{ts}.GET./keywordstool'
        sig = base64.b64encode(
            hmac.new(secret_key.encode(), msg.encode(), hashlib.sha256).digest()
        ).decode()
        r = requests.get(
            'https://api.searchad.naver.com/keywordstool',
            params={'hintKeywords': ','.join(chunk), 'showDetail': '1'},
            headers={
                'X-Timestamp': ts,
                'X-API-KEY':   api_key,
                'X-Customer':  str(customer_id),
                'X-Signature': sig,
            },
            timeout=15,
        )
        r.raise_for_status()
        for item in r.json().get('keywordList', []):
            rel = item.get('relKeyword', '').upper()
            if rel in norm and norm[rel] not in out:
                out[norm[rel]] = (_num(item.get('monthlyPcQcCnt', 0))
                                  + _num(item.get('monthlyMobileQcCnt', 0)))
    return out


def main_search_url(keyword: str) -> str:
    """네이버 메인 통합검색 페이지 URL."""
    return ('https://search.naver.com/search.naver?'
            + urllib.parse.urlencode({
                'where': 'nexearch', 'sm': 'top_hty',
                'fbm': '0', 'ie': 'utf8', 'query': keyword,
            }))


def fetch_first_page_titles(keyword: str, tab: str) -> list:
    """
    tab: '블로그' | '카페' — 최근 1주일 검색 첫 페이지 글 목록.
    추가 로드(스크롤) 없이 첫 응답에 실린 글만 반환: [{'title', 'link'}]
    """
    _init_session()
    params = _first_page_params(keyword, tab)
    url = 'https://search.naver.com/search.naver'
    resp = _get(url, params=params, timeout=15)
    _check_blocked(resp)
    resp.raise_for_status()
    html = resp.text

    # 성인 키워드는 여기서도 0건으로 넘기면 안 된다 (순위 체크와 같은 처리)
    if _ADULT_MARK in html and not _first_page_rows(html, tab):
        global last_was_adult
        last_was_adult = True
        got = ''
        if _ADULT_SESSION is not None:
            r2 = _ADULT_SESSION.get(url, params=params, timeout=15)
            if _ADULT_MARK not in r2.text or _first_page_rows(r2.text, tab):
                got = r2.text
        if not got:        # 쿠키를 가진 수집기 서버에 대신 부탁
            full = url + '?' + urllib.parse.urlencode(params)
            h = _fetch_via_server(full)
            if h and (_ADULT_MARK not in h or _first_page_rows(h, tab)):
                got = h
        if not got:
            raise AdultBlockedError('성인 키워드 — '
                                    + (last_server_why or '수집기 서버에 닿지 않음'))
        html = got

    return _first_page_rows(html, tab)


def _first_page_rows(html: str, tab: str) -> list:
    soup = BeautifulSoup(html, 'lxml')

    results, seen = [], set()
    if tab == '블로그':
        # 블로그탭: headline1 span 기반 (순위 체크와 동일 마크업)
        for span in soup.find_all(
            'span', class_=lambda c: c and 'sds-comps-text-type-headline1' in c,
        ):
            a = span.find_parent('a')
            href = a.get('href', '') if a else ''
            key = href.split('?')[0]
            if not href or key in seen:
                continue
            seen.add(key)
            title = re.sub(r'\s+', ' ', span.get_text(' ')).strip()
            if title:
                results.append({'title': title, 'link': href})
    else:
        # 카페탭: 구형 마크업 — title_link 앵커가 글 제목
        for a in soup.find_all('a', class_='title_link'):
            href = a.get('href', '')
            key = href.split('?')[0]
            if not href or key in seen:
                continue
            seen.add(key)
            title = re.sub(r'\s+', ' ', a.get_text(' ')).strip()
            if title:
                results.append({'title': title, 'link': href})
    return results
