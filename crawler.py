import json as _json
import re
import time
import random
import threading
import urllib.parse

import requests
from bs4 import BeautifulSoup

_SESSION = requests.Session()
_SESSION.headers.update({
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept-Language': 'ko-KR,ko;q=0.9',
    'Referer': 'https://search.naver.com/',
})

_session_ready = False
_session_lock  = threading.Lock()

PER_PAGE      = 30   # 블로그: 30개씩
PER_PAGE_INGI = 10   # 인기글: 10개씩
PER_PAGE_UR   = 20   # 신뢰도: page당 20개, page=2~10 (최대 180위)

BLOG_PAT     = re.compile(r'blog\.naver\.com/([a-zA-Z0-9_.-]+)/(\d+)')
_BLOG_ID_PAT = re.compile(r'blog\.naver\.com/([a-zA-Z0-9_.-]+)(?:/|$)')
_RSS_ID_PAT  = re.compile(r'rss\.blog\.naver\.com/([a-zA-Z0-9_.-]+)')
_CAFE_PAT    = re.compile(r'cafe\.naver\.com')

_resolve_cache:      dict = {}
_blog_name_cache:    dict = {}
_ingi_api_url_cache: dict = {}  # keyword -> lb_api 기본 URL (start 파라미터 제외)


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


def _fetch_ingi_first_page(keyword: str) -> str:
    """
    인기글 1페이지: 메인 네이버 검색 페이지 HTML을 반환.
    브라우저와 동일한 SSR 결과를 포함하며, lb_api URL도 캐시한다.
    """
    resp = _SESSION.get(
        'https://search.naver.com/search.naver',
        params={'ssc': 'tab.nx.all', 'where': 'nexearch', 'sm': 'tab_jum', 'query': keyword},
        timeout=15,
    )
    # lb_api URL 추출 후 캐시 (이후 페이지 호출에 사용)
    if keyword not in _ingi_api_url_cache:
        m = re.search(
            r'lb_api=(https%3A%2F%2Fs\.search\.naver\.com[^"&\s<>#]+)',
            resp.text,
        )
        if m:
            url = urllib.parse.unquote(m.group(1)).replace('&amp;', '&')
            url = re.sub(r'[&?]start=\d+', '', url)
            _ingi_api_url_cache[keyword] = url
        else:
            _ingi_api_url_cache[keyword] = ''
    return resp.text


def _init_session():
    global _session_ready
    with _session_lock:
        if not _session_ready:
            try:
                _SESSION.get('https://www.naver.com/', timeout=10)
                _SESSION.get(
                    'https://search.naver.com/search.naver',
                    params={'where': 'view', 'sm': 'tab_hty.brg', 'query': '블로그'},
                    timeout=10,
                )
                _session_ready = True
            except Exception:
                pass


def _unwrap_search_html(text: str) -> str:
    """인기글 API JSON 응답에서 실제 검색결과 HTML 추출. 실패 시 원문 반환."""
    try:
        data = _json.loads(text)
        return data['dom']['collection'][0]['html']
    except Exception:
        return text


def _fetch(keyword: str, search_type: str, start: int) -> str:
    if search_type == '인기글':
        if start == 1:
            # 첫 페이지: 메인 검색 페이지 HTML 직접 반환 (브라우저 동일 SSR 결과)
            return _fetch_ingi_first_page(keyword)
        # 이후 페이지: 메인 페이지에서 캐시된 lb_api URL 사용
        base_url = _ingi_api_url_cache.get(keyword, '')
        if not base_url:
            # 캐시 미스 시 main page 재호출로 lb_api URL 확보
            _fetch_ingi_first_page(keyword)
            base_url = _ingi_api_url_cache.get(keyword, '')
        if base_url:
            sep = '&' if '?' in base_url else '?'
            resp = _SESSION.get(base_url + sep + f'start={start}', timeout=15)
        else:
            # lb_api 추출 실패 시 기존 방식 fallback
            resp = _SESSION.get(
                'https://s.search.naver.com/p/review/50/search.naver',
                params={
                    'query':    keyword,
                    'ssc':      'tab.itb.all',
                    'sm':       'tab_hty.brg',
                    'start':    start,
                    'api_type': 5,
                },
                timeout=15,
            )
    elif search_type == '신뢰도':
        # page 파라미터는 효과 없음 — start만 결과를 제어
        # start=1 → 1위~20위, start=21 → 21위~40위, ..., start=161 → 161위~180위
        resp = _SESSION.get(
            'https://search.naver.com/search.naver',
            params={
                'query': keyword,
                'ssc':   'tab.ur.all',
                'sm':    'tab_pge',
                'start': start,
            },
            timeout=15,
        )
    else:  # 블로그
        resp = _SESSION.get(
            'https://search.naver.com/search.naver',
            params={
                'ssc':   'tab.blog.all',
                'sm':    'tab_opt',
                'query': keyword,
                'start': start,
            },
            timeout=15,
        )
    resp.raise_for_status()
    return resp.text


def _parse(html: str, start_rank: int, search_type: str = '') -> list:
    """
    Extract blog post results from Naver search HTML.

    인기글: fds-ugc-single-intention-item-list 컨테이너 기반 파싱.
            카페 결과는 순위 슬롯을 차지하지만 결과 목록에는 포함 안 됨.
            광고는 순위 슬롯에서 제외.
    블로그/신뢰도: sds-comps-text-type-headline1 span 기반 파싱.
    """
    posts = []
    seen  = set()
    soup  = BeautifulSoup(html, 'lxml')

    if search_type == '인기글':
        rank = start_rank
        container = soup.find(
            'div',
            class_=lambda c: c and 'fds-ugc-single-intention-item-list' in c,
        )
        if container:
            for child in container.children:
                if not hasattr(child, 'find'):
                    continue
                # 광고는 순위에서 제외
                if child.find(string=re.compile(r'^\s*광고\s*$')):
                    continue
                # headline1 타이틀이 없으면 서브링크 → 순위 슬롯 아님
                if not child.find(
                    'span',
                    class_=lambda c: c and 'sds-comps-text-type-headline1' in c,
                ):
                    continue
                # headline1을 포함하는 블로그 링크 탐색
                blog_link_h1 = None
                for a in child.find_all('a', href=BLOG_PAT):
                    if a.find(
                        'span',
                        class_=lambda c: c and 'sds-comps-text-type-headline1' in c,
                    ):
                        blog_link_h1 = a
                        break
                if blog_link_h1:
                    m = BLOG_PAT.search(blog_link_h1.get('href', ''))
                    if m:
                        key = (m.group(1).lower(), m.group(2))
                        if key not in seen:
                            seen.add(key)
                            title_span = blog_link_h1.find(
                                'span',
                                class_=lambda c: c and 'sds-comps-text-type-headline1' in c,
                            )
                            title = re.sub(r' +', ' ', title_span.get_text(separator=' ')).strip() if title_span else ''
                            blog_id = m.group(1)
                            posts.append({
                                'rank':      rank,
                                'title':     title,
                                'blog_name': '',
                                'id':        blog_id,
                                'date':      '',
                                'link':      f'https://blog.naver.com/{blog_id}/{m.group(2)}',
                            })
                # 블로그/카페/기타 모든 headline1 결과는 순위 슬롯 차지
                rank += 1
        return posts

    if search_type == '신뢰도':
        # tab.ur.all은 컨테이너 중첩이 깊어 직접 자식 순회 불가.
        # 페이지 전체 headline1 링크를 순서대로 순위 슬롯으로 카운트.
        # 블로그 → 결과 추가, 카페/기타 → 순위 슬롯만 차지.
        rank = start_rank
        seen_hrefs: set = set()
        for link in soup.find_all('a'):
            href = link.get('href', '')
            if not href or href in seen_hrefs:
                continue
            title_span = link.find(
                'span',
                class_=lambda c: c and 'sds-comps-text-type-headline1' in c,
            )
            if title_span is None:
                continue
            seen_hrefs.add(href)
            m = BLOG_PAT.search(href)
            if m:
                key = (m.group(1).lower(), m.group(2))
                if key not in seen:
                    seen.add(key)
                    title = re.sub(r' +', ' ', title_span.get_text(separator=' ')).strip()
                    blog_id = m.group(1)
                    posts.append({
                        'rank':      rank,
                        'title':     title,
                        'blog_name': '',
                        'id':        blog_id,
                        'date':      '',
                        'link':      f'https://blog.naver.com/{blog_id}/{m.group(2)}',
                    })
            rank += 1  # 블로그/카페/기타 모든 headline1 링크가 순위 슬롯
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

    all_posts: list = []
    seen_keys: set  = set()
    start = 1
    if search_type == '블로그':
        per_page = PER_PAGE
    elif search_type == '신뢰도':
        per_page = PER_PAGE_UR
    else:
        per_page = PER_PAGE_INGI

    # 신뢰도: page=2~10, 20개씩 (최대 180위)
    if search_type == '신뢰도':
        count = min(count, 180)

    while True:
        if stop_flag and stop_flag.is_set():
            break
        # 인기글/신뢰도: 다음 페이지 시작이 조회 범위를 초과하면 종료
        if search_type in ('인기글', '신뢰도') and start > count:
            break
        # 블로그: 충분히 수집됐으면 종료
        if search_type == '블로그' and len(all_posts) >= count:
            break

        try:
            raw = _fetch(keyword, search_type, start)
            html = _unwrap_search_html(raw) if search_type == '인기글' else raw
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
            if search_type in ('인기글', '신뢰도'):
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

        if search_type == '인기글' and page_posts:
            # 메인 페이지(1페이지)는 ~7개, lb_api는 ~30개로 페이지 크기가 달라
            # 실제 받은 마지막 순위 다음부터 시작해 순위 공백 없이 연속 조회
            start = max(p['rank'] for p in page_posts) + 1
        else:
            start += per_page
        time.sleep(random.uniform(0.6, 1.4))

    return all_posts
