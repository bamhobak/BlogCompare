import json
import os
import sys
import time
import random
import threading
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path

import re

import requests

import analyzer
import crawler
from crawler import (
    search_naver, resolve_blog_id, fetch_blog_name, fetch_post_date,
    fetch_monthly_volumes,
    fetch_blog_profile,
    AdultBlockedError, NaverBlocked, load_adult_cookie, set_adult_cookie, has_adult_cookie,
    set_adult_servers,
)

VERSION = 'v1.1.40'
BASE_DIR = (
    os.path.dirname(sys.executable)
    if getattr(sys, 'frozen', False)
    else os.path.dirname(os.path.abspath(__file__))
)

_SETTINGS_PATH    = os.path.join(BASE_DIR, 'settings.json')
_GIST_FILE        = 'blog_compare_ids.txt'
_GIST_COUNTS_FILE = 'blog_compare_counts.json'
_GIST_HEADERS     = {'Accept': 'application/vnd.github+json'}

_GIST_ID            = '67bd8f83aab3404b487a31e86414fd72'
_UPDATE_VERSION_FILE = 'blog_compare_version.json'
_GITHUB_REPO        = 'bamhobak/BlogCompare'

_CONFIG_PATH  = os.path.join(BASE_DIR, 'config.json')
# 빌드 시 번들되는 기본 설정(_MEIPASS) — Gist 토큰 포함
_BUNDLED_DIR  = getattr(sys, '_MEIPASS', BASE_DIR)


def _load_config() -> dict:
    # 번들 기본 설정(app_config.json) 위에 사용자 config.json을 덮어쓰기 병합.
    # 배포된 키(검색광고 API 등)는 자동 적용되고, 각 PC의 config.json이 우선한다.
    cfg = {'github_token': '', 'gist_id': _GIST_ID}
    for path in (os.path.join(_BUNDLED_DIR, 'app_config.json'), _CONFIG_PATH):
        try:
            # utf-8-sig: BOM이 있어도(메모장/PowerShell 저장 등) 정상 파싱
            with open(path, encoding='utf-8-sig') as f:
                cfg.update(json.load(f))
        except Exception:
            continue
    return cfg


def _save_config(cfg: dict):
    try:
        with open(_CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _gist_fetch(token: str, gist_id: str) -> tuple:
    r = requests.get(
        f'https://api.github.com/gists/{gist_id}',
        headers={**_GIST_HEADERS, 'Authorization': f'token {token}'},
        timeout=10,
    )
    r.raise_for_status()
    files = r.json()['files']
    ids = [ln for ln in files.get(_GIST_FILE, {}).get('content', '').splitlines() if ln.strip()]
    try:
        counts = json.loads(files.get(_GIST_COUNTS_FILE, {}).get('content', '{}'))
    except Exception:
        counts = {}
    return ids, counts


def _gist_push(token: str, gist_id: str, ids: list, counts: dict) -> str:
    payload = {
        'description': 'Blog Compare IDs',
        'public': False,
        'files': {
            _GIST_FILE:        {'content': '\n'.join(ids) or ' '},
            _GIST_COUNTS_FILE: {'content': json.dumps(counts, ensure_ascii=False)},
        },
    }
    hdrs = {**_GIST_HEADERS, 'Authorization': f'token {token}'}
    if gist_id:
        r = requests.patch(f'https://api.github.com/gists/{gist_id}',
                           headers=hdrs, json=payload, timeout=10)
    else:
        r = requests.post('https://api.github.com/gists',
                          headers=hdrs, json=payload, timeout=10)
    r.raise_for_status()
    return r.json().get('id', gist_id)


# ── 팔레트: G안 '잉크 사이드바' ────────────────────────────────────────
# 입력은 왼쪽 잉크색 사이드바, 결과는 오른쪽 밝은 판 위의 흰 패널.
# 강조는 셋뿐 — 금색은 지금 고른 것·실행 버튼, 초록은 목표 안에 든 글·우리 아이디,
# 빨강은 표에서 고른 줄. 표 머리를 잉크색으로 칠해 사이드바와 한 덩어리로 묶는다.
BG      = '#F3F5F8'   # 결과 쪽 바탕
BG_CARD = '#FFFFFF'   # 패널·표
FG      = '#1B2430'
FG_DIM  = '#566273'
BORDER  = '#D9DFE7'
INK     = '#17212F'   # 사이드바·표 머리
INK_2   = '#202C3D'   # 사이드바 입력칸
INK_3   = '#2A3A50'   # 사이드바에서 고른 메뉴
INK_LINE = '#33445C'  # 사이드바 구분선
SIDE_FG  = '#E6EBF2'
SIDE_DIM = '#AEB9C7'
GOLD    = '#F2C14E'   # 고른 메뉴·실행 버튼
GOLD_2  = '#E0AE3A'
AREA_ON = '#3E7D63'   # 영역 선택 — 메뉴보다 한 단계 낮은 강조
STOP    = '#EF4444'   # 실행 중 '중지' 버튼
STOP_2  = '#DC2626'
ACCENT  = INK         # 키워드 구분줄 글자
LINK    = '#2F5FBF'
HIT     = '#1B7A4B'   # 초록 — 목표 순위 안에 든 글 · 우리 아이디
ROW_ALT = '#F6F8FA'   # 줄무늬
SEP_BG  = '#E6EBF2'   # 키워드 구분줄 바탕
LINE_2  = '#E4E9EF'   # 더 옅은 구분선
SELECT_BG = '#C62828'  # 고른 줄 — 붉은 계열로 확실히 튀게
SELECT_FG = '#FFFFFF'
FONT    = ('Malgun Gothic', 9)
FONT_B  = ('Malgun Gothic', 9, 'bold')
FONT_SM = ('Malgun Gothic', 9)           # 로그 — 결과표보다 크면 위계가 뒤집힌다
FONT_U  = ('Malgun Gothic', 9, 'underline')
FONT_CAP = ('Malgun Gothic', 8)          # 사이드바 칸 제목
FONT_TH = ('Malgun Gothic', 8, 'bold')   # 표 머리 — 본문보다 작게
FONT_NUM = ('Malgun Gothic', 16, 'bold')  # 숫자 띠


# 키워드 체크 표 — 칸 종류별 연한 바탕 (인플루언서는 블로그와 같은 색)
KIND_BG = {
    '블로그': '#DCEFE1', '인플': '#DCEFE1', '블홈': '#CFE3F8', '웹': '#FFF3CC',
    '카페': '#EFF1F4', '지식인': '#F3EDFA', '동영상': '#FCEBEE',
}

# 순위 강조 기준 — 이 순위 안(포함)에 들면 행을 초록으로 표시
HIT_RANK = {'신뢰도': 20, '블로그': 5}
# 이웃확인 — 이웃수가 이 값 이상이면 초록 굵게
NBR_BIG = 5000


def _short_date(d: str) -> str:
    """'2026.09.08' → 올해면 '09.08', 지난 해면 '25.08.14'.
    좁은 칸에서 월·일이 먼저 읽히게. 해가 다르면 반드시 보이므로 정보는 안 잃는다."""
    parts = (d or '').split('.')
    if len(parts) != 3 or len(parts[0]) != 4:
        return d
    y = parts[0]
    return f'{parts[1]}.{parts[2]}' if y == time.strftime('%Y') else f'{y[2:]}.{parts[1]}.{parts[2]}'


def _enable_dpi_awareness():
    """윈도우 디스플레이 배율(125%·150% 등)에서 글씨가 뭉개지는 것을 막는다.

    DPI 를 모르는 프로그램은 윈도우가 96DPI 로 그린 화면을 확대해서 보여주기 때문에
    글씨가 흐릿해진다. 프로세스를 DPI 인식으로 선언하면 윈도우가 늘리지 않고
    우리가 직접 실제 해상도로 그리게 된다(= 선명).
    반드시 Tk 창을 만들기 전에 호출해야 한다.
    """
    if sys.platform != 'win32':
        return
    import ctypes
    try:    # Windows 10 1703+ : 모니터별 DPI v2 (모니터 옮겨도 선명)
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except Exception:
        pass
    try:    # Windows 8.1+ : 모니터별 DPI v1
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:    # Vista+ : 시스템 DPI
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


# 화면 배율. root 생성 뒤 _apply_scaling() 에서 실제 값으로 채운다.
SCALE = 1.0


def px(n) -> int:
    """96DPI 기준 픽셀값을 현재 배율로 환산. 폰트·문자폭이 아닌 '픽셀' 값에만 쓴다."""
    return int(round(n * SCALE))


def _apply_scaling(root) -> float:
    """DPI 인식을 켜면 창이 실제 해상도로 그려져 그대로 두면 모든 게 작아진다.
    Tk 의 포인트→픽셀 환산 배율을 실제 DPI 에 맞춰, 포인트로 지정한 글꼴이
    배율만큼 커지도록 한다. 픽셀로 지정한 값은 px() 로 따로 환산한다."""
    global SCALE
    try:
        dpi = root.winfo_fpixels('1i')          # 1인치에 해당하는 픽셀 수
    except Exception:
        dpi = 96.0
    if not dpi or dpi <= 0:
        dpi = 96.0
    SCALE = dpi / 96.0
    root.tk.call('tk', 'scaling', dpi / 72.0)   # 1포인트 = 1/72인치
    return SCALE


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(f'Blog Compare {VERSION}')
        # 배율이 큰 화면(150% 등)에서 창이 화면 밖으로 넘치지 않게 맞춘다
        win_w = min(px(1430), self.root.winfo_screenwidth()  - px(40))
        win_h = min(px(890),  self.root.winfo_screenheight() - px(80))
        self.root.geometry(f'{win_w}x{win_h}')
        self.root.minsize(min(px(900), win_w), min(px(600), win_h))
        self.root.configure(bg=BG)

        self._stop_flag  = threading.Event()
        self._is_running = False
        self._post_links: dict = {}
        self._res_prev_keyword = None     # 실시간 결과 표시용
        self._res_total = 0
        self._res_hit_rank = None         # 초록 강조 기준 순위
        self._nbr_links: dict = {}        # 이웃확인 표 행 → 블로그 주소
        self._counts = {'블로그': 100, '신뢰도': 100}
        self._update_info: dict = {}
        self._saved_at: str = ''

        self._setup_style()
        self._build_ui()
        self._load_settings()
        self.root.protocol('WM_DELETE_WINDOW', self._on_close)
        self.root.after(100, self._load_ids_from_gist)
        self.root.after(500, lambda: threading.Thread(
            target=self._check_for_update, daemon=True).start())

    # ── Style ─────────────────────────────────────────────────────────────

    def _setup_style(self):
        s = ttk.Style(self.root)
        s.theme_use('clam')
        s.configure('.', font=FONT, background=BG, foreground=FG)
        s.configure('TFrame', background=BG)
        s.configure('TLabel', background=BG, foreground=FG)
        s.configure('TScrollbar', troughcolor=ROW_ALT, background='#C3CCD6',
                    bordercolor=LINE_2, arrowcolor='#8E99A6')
        s.map('TScrollbar', background=[('active', '#A7B3C0'), ('pressed', '#8E99A6')])

        # 사이드바 전용
        s.configure('Side.TFrame', background=INK)
        s.configure('Side.TLabel', background=INK, foreground=SIDE_DIM, font=FONT_CAP)
        s.configure('Side.TSpinbox', fieldbackground=INK_2, foreground='white',
                    background=INK_3, bordercolor=INK_LINE, lightcolor=INK_2,
                    darkcolor=INK_2, arrowcolor=SIDE_DIM, arrowsize=12,
                    insertcolor='white')
        s.map('Side.TSpinbox', background=[('active', INK_LINE)])
        s.configure('Side.Vertical.TScrollbar', troughcolor=INK_2, background=INK_LINE,
                    bordercolor=INK_2, lightcolor=INK_LINE, darkcolor=INK_LINE,
                    arrowcolor=SIDE_DIM)
        s.map('Side.Vertical.TScrollbar', background=[('active', '#465A76')])
        s.configure('Side.Horizontal.TProgressbar', troughcolor=INK_3, bordercolor=INK,
                    background=GOLD, lightcolor=GOLD, darkcolor=GOLD,
                    borderwidth=0, thickness=px(3))

        # 표: 행 높이를 줄여 한 화면에 더 담는다(고밀도)
        s.configure('Treeview', font=FONT, rowheight=px(24),
                    background=BG_CARD, fieldbackground=BG_CARD,
                    foreground=FG, borderwidth=0)
        # 머리는 사이드바와 같은 잉크색 띠
        s.configure('Treeview.Heading', font=FONT_TH, background=INK,
                    foreground=SIDE_FG, relief='flat', borderwidth=0,
                    bordercolor=INK, lightcolor=INK, darkcolor=INK, padding=(0, 6))
        s.map('Treeview.Heading', background=[('active', INK_3)],
              foreground=[('active', 'white')])
        # 고른 줄은 붉은 계열로 튀게.
        # map 은 먼저 맞는 항목이 이긴다 — clam 기본값에 ('selected', '#4a6984') 가
        # 이미 들어 있어서 뒤에 덧붙이면 안 먹는다. 통째로 바꿔야 한다.
        s.map('Treeview',
              background=[('disabled', '#DCDAD5'), ('selected', SELECT_BG)],
              foreground=[('disabled', '#999999'), ('selected', SELECT_FG)])

    # ── UI ────────────────────────────────────────────────────────────────

    def _segmented(self, parent, var, values, command=None, pady=4,
                   on_bg=GOLD, on_fg=INK):
        """사이드바의 가로 선택 칸 — 고른 칸만 색을 켠다."""
        box = tk.Frame(parent, bg=INK_LINE, padx=1, pady=1)
        cells = []
        for i, v in enumerate(values):
            b = tk.Radiobutton(
                box, text=v, variable=var, value=v, command=command,
                indicatoron=0, selectcolor=on_bg, cursor='hand2', font=FONT,
                bg=INK, fg=SIDE_DIM, activebackground=INK_3,
                activeforeground='white', bd=0, highlightthickness=0,
                relief='flat', offrelief='flat', overrelief='flat',
                padx=0, pady=pady,
            )
            b.grid(row=0, column=i, sticky='nsew', padx=(0 if i == 0 else 1, 0))
            box.columnconfigure(i, weight=1, uniform='seg')
            cells.append(b)

        def paint(*_):
            for b in cells:
                on = b.cget('value') == var.get()
                b.config(bg=on_bg if on else INK, fg=on_fg if on else SIDE_DIM,
                         font=FONT_B if on else FONT)
        var.trace_add('write', paint)
        paint()
        return box

    def _side_caption(self, parent, text=''):
        lbl = ttk.Label(parent, text=text, style='Side.TLabel')
        lbl.pack(fill=tk.X, pady=(0, 3))
        return lbl

    def _side_text(self, parent, **kw):
        """사이드바 입력칸. 남는 세로 공간을 채운다."""
        wrap = tk.Frame(parent, bg=INK_LINE, padx=1, pady=1)
        wrap.pack(fill=tk.BOTH, expand=True, pady=(0, px(10)))
        ys = ttk.Scrollbar(wrap, style='Side.Vertical.TScrollbar')
        ys.pack(side=tk.RIGHT, fill=tk.Y)
        text = tk.Text(
            wrap, yscrollcommand=ys.set, font=FONT, wrap=tk.NONE, undo=True,
            bg=INK_2, fg='white', relief='flat', insertbackground='white',
            selectbackground='#3D5272', selectforeground='white', bd=0,
            padx=px(6), pady=px(4), spacing1=0, spacing3=0, width=1, height=4, **kw,
        )
        text.pack(fill=tk.BOTH, expand=True)
        ys.config(command=text.yview)
        return text

    def _run_button(self, parent, text, command):
        btn = tk.Button(
            parent, text=text, command=command, font=FONT_B, cursor='hand2',
            relief='flat', bd=0, highlightthickness=0, pady=px(6),
        )
        self._btn_idle(btn, text)
        return btn

    @staticmethod
    def _btn_idle(btn, text):
        btn.config(text=text, bg=GOLD, fg=INK, activebackground=GOLD_2,
                   activeforeground=INK, state=tk.NORMAL)

    @staticmethod
    def _btn_running(btn):
        btn.config(text='중지', bg=STOP, fg='white', activebackground=STOP_2,
                   activeforeground='white')

    def _card(self, parent, **pack):
        """오른쪽의 흰 패널."""
        card = tk.Frame(parent, bg=BG_CARD, highlightthickness=1,
                        highlightbackground=BORDER, highlightcolor=BORDER)
        card.pack(**pack)
        return card

    def _build_ui(self):
        pane = tk.PanedWindow(
            self.root, orient=tk.HORIZONTAL,
            sashwidth=px(3), sashrelief='flat', bg=INK_LINE, bd=0,
        )
        pane.pack(fill=tk.BOTH, expand=True)

        left  = ttk.Frame(pane, style='Side.TFrame')
        right = ttk.Frame(pane)
        # 창을 늘리면 결과 쪽만 넓어진다
        pane.add(left,  width=px(260), minsize=px(230), stretch='never')
        pane.add(right, minsize=px(550), stretch='always')

        self._build_left(left)
        self._build_right(right)

    def _build_left(self, parent):
        self.mode_var = tk.StringVar(value='순위 체크')
        self._segmented(
            parent, self.mode_var, ('순위 체크', '키워드 체크', '이웃확인'),
            command=self._on_mode_change, pady=px(5),
        ).pack(fill=tk.X, padx=px(10), pady=(px(10), px(12)))

        pad = {'fill': tk.BOTH, 'expand': True, 'padx': px(10), 'pady': (0, px(10))}
        self.left_pack = pad
        self.rank_left = ttk.Frame(parent, style='Side.TFrame')
        self.rank_left.pack(**pad)
        self._build_rank_left(self.rank_left)

        self.kwchk_left = ttk.Frame(parent, style='Side.TFrame')
        self._build_kwchk_left(self.kwchk_left)

        self.nbr_left = ttk.Frame(parent, style='Side.TFrame')
        self._build_nbr_left(self.nbr_left)

    def _build_rank_left(self, parent):
        # 키워드 체크의 '분석 영역'과 같은 자리
        self._side_caption(parent, '분석 영역')
        self.type_var = tk.StringVar(value='블로그')
        # 2026-10-15 네이버가 통합검색에서 블로그 인기글 블록을 걷어냈다
        # ('인기글' 이라는 말 자체가 페이지에 안 나온다) — 선택지에서 뺌
        self._segmented(parent, self.type_var, ('신뢰도', '블로그'),
                        on_bg=AREA_ON, on_fg='white').pack(fill=tk.X, pady=(0, px(10)))

        # 비교대상 아이디 입력
        self.lf_id = self._side_caption(parent, '비교대상 아이디')
        self._update_saved_label()
        self.id_text = self._side_text(parent)

        # 검색
        self._side_caption(parent, '키워드 (한 줄에 하나)')
        self.kw_text = self._side_text(parent)
        self.kw_text.bind('<Control-Return>', lambda _: self._toggle_search())
        self.kw_text.bind('<<Paste>>', self._on_kw_paste)

        row_cnt = ttk.Frame(parent, style='Side.TFrame')
        row_cnt.pack(fill=tk.X, pady=(0, px(8)))
        self.cnt_label_var = tk.StringVar(value='블로그 조회수')
        ttk.Label(row_cnt, textvariable=self.cnt_label_var, style='Side.TLabel').pack(side=tk.LEFT)
        self.count_var = tk.IntVar(value=100)
        ttk.Spinbox(
            row_cnt, from_=10, to=1000, increment=10, style='Side.TSpinbox',
            textvariable=self.count_var, width=5,
        ).pack(side=tk.LEFT, padx=(px(6), px(10)))
        self.btn_save_gist = tk.Button(
            row_cnt, text='아이디&설정 저장', command=self._save_to_gist,
            bg=INK_3, fg=SIDE_FG, font=FONT_CAP,
            relief='flat', bd=0, cursor='hand2',
            activebackground=INK_LINE, activeforeground='white',
            padx=px(6), pady=px(2),
        )
        self.btn_save_gist.pack(side=tk.RIGHT)

        self.btn_search = self._run_button(parent, '조회', self._toggle_search)
        self.btn_search.pack(fill=tk.X)

        self.type_var.trace_add('write', self._on_type_change)
        self.count_var.trace_add('write', self._on_count_change)

        self.progress = ttk.Progressbar(parent, mode='determinate', maximum=100,
                                        style='Side.Horizontal.TProgressbar')
        self.progress.pack(fill=tk.X, pady=(px(8), 0))

    def _build_kwchk_left(self, parent):
        self._side_caption(parent, '분석 영역')
        self.kwchk_area = tk.StringVar(value='신뢰도')
        self._segmented(parent, self.kwchk_area, ('신뢰도', '블로그'), on_bg=AREA_ON, on_fg='white',
                        command=self._on_kwchk_area).pack(fill=tk.X, pady=(0, px(10)))

        self._side_caption(parent, '키워드 (한 줄에 하나)')
        self.kwchk_text = self._side_text(parent)
        self.kwchk_text.bind('<Control-Return>', lambda _: self._toggle_kwchk())

        self.kwchk_hint = ttk.Label(parent, style='Side.TLabel')
        self.kwchk_hint.pack(fill=tk.X, pady=(0, px(8)))
        self._on_kwchk_area()
        self.btn_kwchk = self._run_button(parent, '조회', self._toggle_kwchk)
        self.btn_kwchk.pack(fill=tk.X)
        self.kwchk_progress = ttk.Progressbar(parent, mode='determinate', maximum=100,
                                              style='Side.Horizontal.TProgressbar')
        self.kwchk_progress.pack(fill=tk.X, pady=(px(8), 0))

    def _on_kwchk_area(self):
        self.kwchk_hint.config(text=(
            '통합검색 1쪽 전체(최대 20칸)의\n블로그·카페·웹사이트를 분석합니다.'
            if self.kwchk_area.get() == '신뢰도' else
            f'블로그탭 {analyzer.TOP_BLOG}위 안의 글과\n그 블로그를 분석합니다.'
        ))

    def _build_right(self, parent):
        self.rank_right = ttk.Frame(parent)
        self.rank_right.pack(fill=tk.BOTH, expand=True)
        self._build_rank_right(self.rank_right)

        self.kwchk_right = ttk.Frame(parent)
        self._build_kwchk_right(self.kwchk_right)

        self.nbr_right = ttk.Frame(parent)
        self._build_nbr_right(self.nbr_right)

        # 로그 (오른쪽 하단, 세 모드 공용)
        lf_log = self._card(parent, fill=tk.X, padx=px(10), pady=(0, px(10)))
        lf_log.pack_propagate(False)
        lf_log.configure(height=px(84))
        self.lf_log = lf_log

        ls = ttk.Scrollbar(lf_log)
        ls.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text = tk.Text(
            lf_log, yscrollcommand=ls.set, state=tk.DISABLED,
            font=FONT_SM, wrap=tk.WORD,
            bg=BG_CARD, fg=FG_DIM, relief='flat',
            bd=0, padx=px(8), pady=px(5),
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)
        ls.config(command=self.log_text.yview)

    def _build_rank_right(self, parent):
        COLS = ('키워드', '순위', '제목', '블로그명', '아이디', '작성일', '링크')
        tbl = self._card(parent, fill=tk.BOTH, expand=True, padx=px(10), pady=px(10))

        vs = ttk.Scrollbar(tbl)
        vs.pack(side=tk.RIGHT, fill=tk.Y)

        self.tree = ttk.Treeview(
            tbl, columns=COLS, show='headings',
            yscrollcommand=vs.set,
        )
        self.tree.pack(fill=tk.BOTH, expand=True)
        vs.config(command=self.tree.yview)

        specs = [
            ('키워드',  100, tk.W,      False),
            ('순위',   45,  tk.CENTER, False),
            ('제목',   200, tk.W,      True),
            ('블로그명', 110, tk.W,      False),
            ('아이디',  75,  tk.W,      False),
            ('작성일',  70,  tk.CENTER, False),
            ('링크',   75,  tk.CENTER, False),
        ]
        for col, w, anc, stretch in specs:
            self.tree.heading(col, text=col, anchor=tk.CENTER)
            self.tree.column(col, width=px(w), anchor=anc,
                             minwidth=px(40), stretch=stretch)

        self.tree.bind('<Double-1>', self._on_double_click)
        self.tree.bind('<ButtonRelease-1>', self._on_click)

        ctx = tk.Menu(self.root, tearoff=0, font=FONT)
        ctx.add_command(label='링크 열기', command=self._open_selected)
        ctx.add_command(label='아이디 복사', command=self._copy_id)
        self.tree.bind('<Button-3>', lambda e: self._show_ctx(e, ctx))

    def _build_kwchk_right(self, parent):
        pad = px(10)
        top = ttk.Frame(parent)
        top.pack(fill=tk.X, padx=pad, pady=(pad, px(8)))

        # 키워드 버튼 줄 — 칸이 넘치면 가로로 민다(휠 또는 아래 막대)
        self.kwchk_info = tk.Frame(top, bg=BG)
        self.kwchk_info.pack(side=tk.RIGHT, padx=(px(10), 0))
        self.kwchk_open = tk.Label(self.kwchk_info, text='', font=FONT_U, bg=BG, fg=LINK,
                                   cursor='hand2')
        self.kwchk_open.pack(side=tk.RIGHT, padx=(px(10), 0))
        self.kwchk_open.bind('<Button-1>', self._on_kwchk_open)
        self.kwchk_kinds = tk.Label(self.kwchk_info, text='', font=FONT, bg=BG, fg=FG_DIM)
        self.kwchk_kinds.pack(side=tk.RIGHT)

        strip = ttk.Frame(top)
        strip.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.kwchk_strip = tk.Canvas(strip, height=px(30), bg=BG, highlightthickness=0,
                                     xscrollincrement=px(40))
        self.kwchk_strip.pack(fill=tk.X, expand=True)
        self.kwchk_strip_bar = ttk.Scrollbar(strip, orient=tk.HORIZONTAL,
                                             command=self.kwchk_strip.xview)
        self.kwchk_strip.configure(xscrollcommand=self.kwchk_strip_bar.set)
        self.kwchk_btns = tk.Frame(self.kwchk_strip, bg=BG)
        self.kwchk_strip.create_window((0, 0), window=self.kwchk_btns, anchor='nw')
        self.kwchk_btns.bind('<Configure>', self._kwchk_strip_sync)
        self.kwchk_strip.bind('<Configure>', self._kwchk_strip_sync)
        self.kwchk_strip.bind('<MouseWheel>', self._kwchk_strip_wheel)
        self._kwchk_btn_list: list = []

        # 체급을 한눈에 보는 숫자 띠
        band = tk.Frame(parent, bg=BORDER, padx=1, pady=1)
        band.pack(fill=tk.X, padx=pad, pady=(0, px(8)))
        self.kwchk_tiles: dict = {}
        for i, label in enumerate(('1주 내 글', '이웃 최고', '어린 블로그')):
            cell = tk.Frame(band, bg=BG_CARD, padx=px(12), pady=px(7))
            cell.grid(row=0, column=i, sticky='nsew', padx=(0 if i == 0 else 1, 0))
            band.columnconfigure(i, weight=1, uniform='tile')
            tk.Label(cell, text=label, font=FONT_CAP, bg=BG_CARD, fg=FG_DIM,
                     anchor='w').pack(fill=tk.X)
            val = tk.Label(cell, text='-', font=FONT_NUM, bg=BG_CARD, fg=INK, anchor='w')
            val.pack(fill=tk.X)
            self.kwchk_tiles[label] = val

        lf_det = self._card(parent, fill=tk.BOTH, expand=True, padx=pad, pady=(0, px(8)))
        vs = ttk.Scrollbar(lf_det)
        vs.pack(side=tk.RIGHT, fill=tk.Y)
        hs = ttk.Scrollbar(lf_det, orient=tk.HORIZONTAL)
        hs.pack(side=tk.BOTTOM, fill=tk.X)
        det_cols = (
            ('순위', 34, tk.CENTER, False), ('종류', 52, tk.CENTER, False),
            ('제목', 110, tk.W, True), ('블로그명', 92, tk.W, True),
            ('닉네임', 64, tk.W, False), ('아이디', 80, tk.W, False),
            ('작성일', 58, tk.CENTER, False), ('나이', 70, tk.CENTER, False),
            ('이웃', 54, tk.CENTER, False), ('일방문', 44, tk.CENTER, False),
            ('총 글', 44, tk.CENTER, False), ('관련 글', 56, tk.CENTER, False),
        )
        self.kwchk_det = ttk.Treeview(
            lf_det, columns=[c[0] for c in det_cols], show='headings',
            yscrollcommand=vs.set, xscrollcommand=hs.set,
        )
        self.kwchk_det.pack(fill=tk.BOTH, expand=True)
        vs.config(command=self.kwchk_det.yview)
        hs.config(command=self.kwchk_det.xview)
        for col, w, anc, stretch in det_cols:
            self.kwchk_det.heading(col, text=col, anchor=tk.CENTER)
            self.kwchk_det.column(col, width=px(w), anchor=anc,
                                  minwidth=px(30), stretch=stretch)
        t = self.kwchk_det
        for kind, bg in KIND_BG.items():
            t.tag_configure(f'k_{kind}', background=bg, foreground=FG)
            t.tag_configure(f'ours_{kind}', background=bg, foreground=HIT, font=FONT_B)
        # 블로그 영역은 전부 블로그 글이라 종류 색이 뜻이 없다 — 칠하지 않는다
        t.tag_configure('k_plain', background=BG_CARD, foreground=FG)
        t.tag_configure('ours_plain', background=BG_CARD, foreground=HIT, font=FONT_B)
        t.bind('<Double-1>', self._on_kwchk_double)
        t.bind('<<TreeviewSelect>>', self._on_kwchk_select)

        ctx = tk.Menu(self.root, tearoff=0, font=FONT)
        ctx.add_command(label='링크 열기', command=self._open_kwchk_selected)
        ctx.add_command(label='아이디·주소 복사', command=self._copy_kwchk_id)
        t.bind('<Button-3>', lambda e: self._show_kwchk_ctx(e, ctx))

        lf_one = self._card(parent, fill=tk.X, padx=pad, pady=(0, px(8)))
        one_ys = ttk.Scrollbar(lf_one)
        one_ys.pack(side=tk.RIGHT, fill=tk.Y)
        self.kwchk_one = tk.Text(
            lf_one, height=6, state=tk.DISABLED, font=FONT, wrap=tk.WORD,
            bg=BG_CARD, fg=FG, relief='flat', bd=0, padx=px(10), pady=px(6),
            yscrollcommand=one_ys.set,
        )
        self.kwchk_one.pack(fill=tk.X)
        one_ys.config(command=self.kwchk_one.yview)
        self._kwchk_items: dict = {}     # 표 행 → (키워드, 칸 정보)
        self._kwchk_groups: list = []    # 키워드별 결과 {kw, area, volume, items, fail}
        self._kwchk_view = -1            # 지금 표에 띄운 키워드
        self._kwchk_follow = True        # 조회 중 새 키워드로 따라간다(버튼을 누르면 멈춤)
        self._kwchk_ours: set = set()    # 우리 아이디

    def _on_mode_change(self):
        mode = self.mode_var.get()
        panes = {
            '순위 체크':   (self.rank_left,  self.rank_right),
            '키워드 체크': (self.kwchk_left, self.kwchk_right),
            '이웃확인':    (self.nbr_left,   self.nbr_right),
        }
        for name, (left, right) in panes.items():
            if name != mode:
                left.pack_forget()
                right.pack_forget()
        left, right = panes.get(mode, panes['순위 체크'])
        left.pack(**self.left_pack)
        right.pack(fill=tk.BOTH, expand=True, before=self.lf_log)

    # ── Helpers ───────────────────────────────────────────────────────────

    def _on_kw_paste(self, event):
        try:
            raw = self.root.clipboard_get().replace('\t', '\n')
            lines = []
            for line in raw.splitlines():
                line = re.sub(r'\s*\(.*\)\s*$', '', line)
                lines.append(line)
            text = '\n'.join(lines)
            try:
                self.kw_text.delete(tk.SEL_FIRST, tk.SEL_LAST)
            except tk.TclError:
                pass
            self.kw_text.insert(tk.INSERT, text)
        except tk.TclError:
            pass
        return 'break'

    def _get_keywords(self) -> list:
        return [
            ln.strip()
            for ln in self.kw_text.get('1.0', tk.END).splitlines()
            if ln.strip()
        ]

    def _get_target_ids(self) -> set:
        return {
            ln.strip().lower()
            for ln in self.id_text.get('1.0', tk.END).splitlines()
            if ln.strip()
        }

    def _on_type_change(self, *_):
        t = self.type_var.get()
        if t not in self._counts:        # 옛 설정에 남은 '인기글' 등
            self.type_var.set('블로그')
            return
        self.count_var.set(self._counts.get(t, 100))
        self.cnt_label_var.set(f'{t} 조회수')

    def _on_count_change(self, *_):
        try:
            self._counts[self.type_var.get()] = self.count_var.get()
        except Exception:
            pass

    def _load_settings(self):
        if not os.path.exists(_SETTINGS_PATH):
            return
        try:
            with open(_SETTINGS_PATH, encoding='utf-8') as f:
                s = json.load(f)
            kw = s.get('keyword', '')
            if kw:
                self.kw_text.delete('1.0', tk.END)
                self.kw_text.insert(tk.END, kw)
            t = s.get('search_type', '블로그')
            self.type_var.set(t)
            self.cnt_label_var.set(f'{t} 조회수')
        except Exception:
            pass

    def _save_settings(self):
        try:
            with open(_SETTINGS_PATH, 'w', encoding='utf-8') as f:
                json.dump({
                    'keyword':     self.kw_text.get('1.0', tk.END).strip(),
                    'search_type': self.type_var.get(),
                }, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _load_ids_from_gist(self):
        cfg = _load_config()
        token, gist_id = cfg.get('github_token', ''), cfg.get('gist_id', '')
        if not token or not gist_id:
            return

        self._log('Gist에서 데이터 로드 중...')

        def _do():
            try:
                ids, counts = _gist_fetch(token, gist_id)
                def _apply():
                    if ids:
                        self.id_text.delete('1.0', tk.END)
                        self.id_text.insert(tk.END, '\n'.join(ids))
                    for k in self._counts:
                        if k in counts:
                            self._counts[k] = counts[k]
                    self.count_var.set(self._counts[self.type_var.get()])
                    saved_at = counts.get('_saved_at', '')
                    if saved_at:
                        self._saved_at = saved_at
                        self._update_saved_label()
                    self._log('아이디&설정 로드 완료')
                self.root.after(0, _apply)
            except Exception:
                self.root.after(0, lambda: self._log('Gist 로드 실패'))

        threading.Thread(target=_do, daemon=True).start()

    def _on_close(self):
        self._save_settings()
        self.root.destroy()

    def _update_saved_label(self):
        # 아이디 입력 라벨프레임 제목 옆에 마지막 Gist 저장 날짜 표기
        try:
            if self._saved_at:
                self.lf_id.config(text=f'비교대상 아이디  ·  최종 저장 {self._saved_at}')
            else:
                self.lf_id.config(text='비교대상 아이디')
        except Exception:
            pass

    def _save_to_gist(self):
        import datetime
        cfg = _load_config()
        token, gist_id = cfg.get('github_token', ''), cfg.get('gist_id', '')
        if not token:
            self._log('GitHub 토큰이 설정되지 않았습니다.')
            return

        ids = [ln.strip() for ln in self.id_text.get('1.0', tk.END).splitlines() if ln.strip()]
        counts = dict(self._counts)
        saved_at = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
        counts['_saved_at'] = saved_at

        self.btn_save_gist.config(text='저장 중...', state=tk.DISABLED)
        self._log('아이디&설정 저장 중...')

        def _do():
            try:
                new_gist_id = _gist_push(token, gist_id, ids, counts)
                cfg['gist_id'] = new_gist_id
                _save_config(cfg)
                def _ok():
                    self._saved_at = saved_at
                    self._update_saved_label()
                    self._log('아이디&설정 저장 완료')
                self.root.after(0, _ok)
            except Exception as e:
                self.root.after(0, lambda: self._log(f'Gist 저장 실패: {e}'))
            finally:
                self.root.after(0, lambda: self.btn_save_gist.config(text='아이디&설정 저장', state=tk.NORMAL))

        threading.Thread(target=_do, daemon=True).start()

    def _log(self, msg: str, error_suffix: str = ''):
        import datetime
        def _do():
            self.log_text.config(state=tk.NORMAL)
            ts = datetime.datetime.now().strftime('%H:%M:%S')
            self.log_text.insert(tk.END, f'[{ts}] {msg}')
            if error_suffix:
                self.log_text.tag_configure('err', foreground='#DC2626')
                self.log_text.insert(tk.END, '\n' + error_suffix, 'err')
            self.log_text.insert(tk.END, '\n')
            self.log_text.see(tk.END)
            self.log_text.config(state=tk.DISABLED)
        self.root.after(0, _do)

    def _selected_iid(self):
        sel = self.tree.selection()
        return sel[0] if sel else None

    def _open_selected(self):
        iid = self._selected_iid()
        if iid and iid in self._post_links:
            webbrowser.open(self._post_links[iid])

    def _copy_id(self):
        iid = self._selected_iid()
        if iid:
            blog_id = self.tree.set(iid, '아이디')
            self.root.clipboard_clear()
            self.root.clipboard_append(blog_id)

    def _show_ctx(self, event, menu):
        item = self.tree.identify_row(event.y)
        if item:
            self.tree.selection_set(item)
            menu.tk_popup(event.x_root, event.y_root)

    def _on_double_click(self, event):
        item = self.tree.identify_row(event.y)
        if item and item in self._post_links:
            webbrowser.open(self._post_links[item])

    def _on_click(self, event):
        col  = self.tree.identify_column(event.x)
        item = self.tree.identify_row(event.y)
        if col == '#7' and item and item in self._post_links:
            webbrowser.open(self._post_links[item])
        elif col == '#1' and item:
            kw = self.tree.set(item, '키워드')
            if kw:
                kw = kw.strip('[]') if kw.startswith('[') and kw.endswith(']') else kw
                self.root.clipboard_clear()
                self.root.clipboard_append(kw)
                self._log(f'클립보드에 복사됨: {kw}')

    # ── Search ────────────────────────────────────────────────────────────

    def _toggle_search(self):
        if self._is_running:
            self._stop_flag.set()
            self.btn_search.config(text='중지 중...', state=tk.DISABLED)
        else:
            self._start_search()

    def _start_search(self):
        keywords = self._get_keywords()
        if not keywords:
            messagebox.showwarning('알림', '검색어를 입력하세요.')
            return

        self._is_running = True
        self._stop_flag.clear()
        self._post_links.clear()
        self._load_adult_cookie()

        for item in self.tree.get_children():
            self.tree.delete(item)

        # 실시간 표시 상태 — 키워드 하나가 끝날 때마다 결과를 이어 붙인다
        self._res_prev_keyword = None
        self._res_total = 0
        # 목표 순위 안(포함)에 들면 초록으로 — 신뢰도 20위, 블로그 5위
        self._res_hit_rank = HIT_RANK.get(self.type_var.get())
        self._setup_tree_tags()

        self.progress['value'] = 0
        self._btn_running(self.btn_search)
        self._log('조회 시작')

        # 메인 스레드에서 모든 값 캡처 후 전달 (tkinter 스레드 안전)
        params = {
            'keywords':    keywords,
            'search_type': self.type_var.get(),
            'count':       self.count_var.get(),
            'target_ids':  self._get_target_ids(),
        }
        threading.Thread(target=self._worker, args=(params,), daemon=True).start()

    def _worker(self, params):
        keywords    = params['keywords']
        search_type = params['search_type']
        count       = params['count']
        target_ids  = params['target_ids']
        n           = len(keywords)

        all_matched:    list = []
        global_resolved: dict = {}

        for ki, keyword in enumerate(keywords):
            if self._stop_flag.is_set():
                break

            label = f'({ki+1}/{n}) ' if n > 1 else ''
            self._log(f'조회 중... {label}"{keyword}"')

            def prog(cur, tot, ki=ki):
                pct = ((ki * count + cur) / (n * count) * 100) if count else 0
                self.root.after(0, self._on_progress, pct)

            try:
                posts = search_naver(
                    keyword, search_type, count,
                    stop_flag=self._stop_flag,
                    progress_cb=prog,
                )
            except AdultBlockedError as e:
                # 로그인 쿠키 없이는 결과가 안 나온다 — '조회 0건'으로 넘기면 안 된다
                self._log(f'{e}\n"{keyword}" — 순위를 알 수 없음')
                self.root.after(0, self._mark_adult, keyword, str(e))
                continue
            except Exception as e:
                self._log(f'오류: {e}')
                continue

            result_ids = {p['id'].lower() for p in posts}
            unmatched  = target_ids - result_ids

            resolved_map: dict = {}
            if unmatched and not self._stop_flag.is_set():
                self._log(f'아이디 확인 중... ({ki+1}/{n})\n"{keyword}"')
                for inp in unmatched:
                    if self._stop_flag.is_set():
                        break
                    resolved = resolve_blog_id(inp)
                    if resolved != inp:
                        resolved_map[inp] = resolved

            global_resolved.update(resolved_map)
            expanded = target_ids | set(global_resolved.values())
            matched  = [p for p in posts if p['id'].lower() in expanded]

            if matched and not self._stop_flag.is_set():
                self._log(f'블로그명/작성일 가져오는 중...\n"{keyword}" ({len(matched)}개)')
                for post in matched:
                    if self._stop_flag.is_set():
                        break
                    bid    = post['id']
                    log_no = post['link'].rstrip('/').split('/')[-1]
                    post['blog_name'] = fetch_blog_name(bid)
                    post['date']      = fetch_post_date(bid, log_no)
                    post['keyword']   = keyword

            for post in matched:
                post.setdefault('keyword', keyword)

            all_matched.extend(matched)

            # 전부 끝날 때까지 기다리지 않고, 이 키워드 몫을 바로 화면에 올린다
            self.root.after(0, self._append_results,
                            keyword, matched, dict(global_resolved))

        self.root.after(0, self._finish_results, len(all_matched))

    def _load_adult_cookie(self):
        """성인 키워드 준비.
        쿠키는 수집기 서버 PC 에만 둔다 — 이 PC 는 그 서버에 '대신 받아 줘' 라고 부탁한다.
        (이 PC 에 쿠키를 넣어 둔 경우에는 그걸 먼저 쓴다.)"""
        try:
            cfg = _load_config()
            if cfg.get('adult_servers'):
                set_adult_servers(cfg['adult_servers'])
            set_adult_cookie(load_adult_cookie(BASE_DIR))
            if has_adult_cookie():
                self._log('성인 키워드: 이 PC 로그인 쿠키 사용')
        except Exception:
            pass

    def _mark_adult(self, keyword: str, why: str, nbr: bool = False):
        """성인 키워드를 표에도 남긴다 — 로그는 흘러가서 놓치기 쉽다."""
        tree = self.nbr_tree if nbr else self.tree
        cols = len(tree['columns'])
        vals = [f'[{keyword}]', '', why] + [''] * cols
        tree.insert('', tk.END, values=tuple(vals[:cols]), tags=('adult',))
        kids = tree.get_children()
        if kids:
            tree.see(kids[-1])

    def _on_progress(self, pct: float):
        self.progress['value'] = pct

    def _setup_tree_tags(self):
        """표 행 태그. Tk 8.6 은 칸 단위 색을 지원하지 않아(열 하나만 칠할 수 없다)
        순위 강조는 그 글의 행 전체를 초록으로 칠해서 나타낸다."""
        t = self.tree
        t.tag_configure('sep', background=SEP_BG, foreground=ACCENT, font=FONT_B)
        # 성인 키워드 — 0건이 아니라 '못 쟀다'는 뜻이라 눈에 띄게 남긴다
        t.tag_configure('adult', background=SEP_BG, foreground=SELECT_BG, font=FONT_B)
        t.tag_configure('odd',      background=BG_CARD, foreground=FG)
        t.tag_configure('even',     background=ROW_ALT, foreground=FG)
        t.tag_configure('hit_odd',  background=BG_CARD, foreground=HIT, font=FONT_B)
        t.tag_configure('hit_even', background=ROW_ALT, foreground=HIT, font=FONT_B)

    def _row_tag(self, rank, idx: int) -> str:
        """줄무늬 + 순위 강조를 합친 태그 이름."""
        stripe = 'even' if idx % 2 == 0 else 'odd'
        limit = self._res_hit_rank
        try:
            hit = limit is not None and int(rank) <= limit
        except (TypeError, ValueError):
            hit = False
        return f'hit_{stripe}' if hit else stripe

    def _append_results(self, keyword: str, matched: list, resolved_map: dict):
        """키워드 하나 분량의 결과를 표 맨 아래에 이어 붙인다(조회 중 실시간 호출)."""
        reverse_map = {v: k for k, v in resolved_map.items()}

        # 키워드가 바뀌면 구분줄을 먼저 넣는다 (결과가 없는 키워드도 지나간 걸 보여준다)
        if keyword != self._res_prev_keyword:
            self.tree.insert(
                '', tk.END,
                values=(f'[{keyword}]', '', '', '', '', '', ''),
                tags=('sep',),
            )
            self._res_prev_keyword = keyword

        for row_i, post in enumerate(matched):
            short_link = (
                post['link'][:37] + '...'
                if len(post['link']) > 40
                else post['link']
            )
            blog_id    = post['id']
            display_id = blog_id
            if blog_id.lower() in reverse_map:
                display_id = f"{blog_id} ({reverse_map[blog_id.lower()]})"

            iid = self.tree.insert(
                '', tk.END,
                values=(
                    keyword,
                    post['rank'],
                    post['title'],
                    post.get('blog_name', ''),
                    display_id,
                    _short_date(post.get('date', '')),
                    short_link,
                ),
                tags=(self._row_tag(post['rank'], row_i),),
            )
            self._post_links[iid] = post['link']

        self._res_total += len(matched)

        # 방금 추가한 줄이 보이도록 따라 내려간다
        children = self.tree.get_children()
        if children:
            self.tree.see(children[-1])

    def _finish_results(self, total: int):
        self._is_running = False
        self._btn_idle(self.btn_search, '조회')
        self.progress['value'] = 100
        self._log(
            '조회 종료',
            error_suffix='조회된 포스팅이 없습니다.' if not total else '',
        )


    # ── 키워드 체크 ────────────────────────────────────────────────────────

    # ── 키워드 체크 ───────────────────────────────────────────────────────
    # 키워드 상위 칸을 뜯어 그 자리를 잡으려면 어느 체급이어야 하는지 본다.
    # 검색 칸을 먼저 띄우고, 블로그·사이트 정보는 도착하는 대로 채운다.

    KWCHK_WORKERS = 4

    def _on_kwchk_double(self, event):
        got = self._kwchk_items.get(self.kwchk_det.identify_row(event.y))
        if got and got[1]['link']:
            webbrowser.open(got[1]['link'])

    def _on_kwchk_open(self, _event=None):
        if 0 <= self._kwchk_view < len(self._kwchk_groups):
            g = self._kwchk_groups[self._kwchk_view]
            webbrowser.open(analyzer.serp_url(g['kw'], g['area']))

    def _kwchk_strip_sync(self, _event=None):
        """버튼이 넘칠 때만 가로 막대를 보인다."""
        c = self.kwchk_strip
        need = self.kwchk_btns.winfo_reqwidth()
        c.configure(scrollregion=(0, 0, need, px(30)))
        if need > c.winfo_width() > 1:
            self.kwchk_strip_bar.pack(fill=tk.X)
        else:
            self.kwchk_strip_bar.pack_forget()

    def _kwchk_strip_wheel(self, event):
        self.kwchk_strip.xview_scroll(-1 if event.delta > 0 else 1, 'units')
        return 'break'

    def _kwchk_paint_btns(self):
        for i, b in enumerate(self._kwchk_btn_list):
            on = i == self._kwchk_view
            bad = bool(self._kwchk_groups[i].get('fail'))
            b.config(bg=INK if on else BG_CARD,
                     fg='white' if on else SELECT_BG if bad else FG,
                     activebackground=INK_3 if on else ROW_ALT,
                     activeforeground='white' if on else FG,
                     font=FONT_B if on else FONT,
                     highlightbackground=INK if on else '#CBD3DD')

    def _kwchk_add_btn(self, ki, g):
        vol = g['volume']
        tail = g.get('fail') or (f'{vol:,}' if isinstance(vol, int) else '')
        b = tk.Button(
            self.kwchk_btns, text=f"{g['kw']} {tail}".strip(), cursor='hand2',
            relief='flat', bd=0, highlightthickness=1, padx=px(10), pady=px(3),
            command=lambda: self._on_kwchk_pick(ki),
        )
        b.pack(side=tk.LEFT, padx=(0, px(6)))
        b.bind('<MouseWheel>', self._kwchk_strip_wheel)
        self._kwchk_btn_list.append(b)

    def _on_kwchk_pick(self, ki):
        self._kwchk_follow = False
        self._kwchk_show(ki)

    def _kwchk_show(self, ki):
        """한 키워드의 결과를 표·숫자 띠에 띄운다."""
        self._kwchk_view = ki
        self._kwchk_items.clear()
        t = self.kwchk_det
        t.delete(*t.get_children())
        g = self._kwchk_groups[ki]
        for it in g['items']:
            iid = f'd{it["rank"]}'
            prefix = 'ours' if self._kwchk_is_ours(it) else 'k'
            kind = 'plain' if g['area'] == '블로그' else self._kwchk_kind(it)
            t.insert('', tk.END, iid=iid, values=self._kwchk_values(it),
                     tags=(f'{prefix}_{kind}',))
            self._kwchk_items[iid] = (g['kw'], it)
        self._on_kwchk_select()
        self._kwchk_paint_btns()
        self._kwchk_paint_summary()

    def _kwchk_paint_summary(self):
        tiles = self.kwchk_tiles
        g = self._kwchk_groups[self._kwchk_view]
        self.kwchk_open.config(text='네이버에서 보기')
        if g.get('fail'):
            for lbl in tiles.values():
                lbl.config(text='-')
            self.kwchk_kinds.config(text=g['fail'], fg=SELECT_BG)
            return
        s = analyzer.summarize(g['items'])
        fmt = lambda v: f'{v:,}' if isinstance(v, int) else '-'
        tiles['1주 내 글'].config(text=f'{s["fresh"]} / {s["dated"]}')
        tiles['이웃 최고'].config(text=fmt(s['buddy_max']))
        tiles['어린 블로그'].config(text=analyzer.age_text(s['youngest']) or '-')
        order = ('블로그', '카페', '웹', '지식인', '동영상', '기타')
        kinds = ' · '.join(f'{k} {s["kinds"][k]}' for k in order if k in s['kinds']) or '결과 없음'
        n_ours = sum(self._kwchk_is_ours(it) for it in g['items'])
        self.kwchk_kinds.config(text=kinds + (f' · 우리 {n_ours}칸' if n_ours else ''), fg=FG_DIM)

    def _kwchk_selected(self):
        sel = self.kwchk_det.selection()
        return self._kwchk_items.get(sel[0]) if sel else None

    def _open_kwchk_selected(self):
        got = self._kwchk_selected()
        if got and got[1]['link']:
            webbrowser.open(got[1]['link'])

    def _copy_kwchk_id(self):
        got = self._kwchk_selected()
        if got:
            it = got[1]
            self.root.clipboard_clear()
            self.root.clipboard_append(it['blog_id'] or it['cafe_url'] or it['host'])

    def _show_kwchk_ctx(self, event, menu):
        item = self.kwchk_det.identify_row(event.y)
        if item in self._kwchk_items:
            self.kwchk_det.selection_set(item)
            menu.tk_popup(event.x_root, event.y_root)

    def _on_kwchk_select(self, _event=None):
        got = self._kwchk_selected()
        self.kwchk_one.config(state=tk.NORMAL)
        self.kwchk_one.delete('1.0', tk.END)
        if got:
            self.kwchk_one.insert('1.0', self._kwchk_detail_text(*got))
        self.kwchk_one.config(state=tk.DISABLED)

    def _toggle_kwchk(self):
        if self._is_running:
            self._stop_flag.set()
            self.btn_kwchk.config(text='중지 중...', state=tk.DISABLED)
            return
        keywords = [
            ln.strip()
            for ln in self.kwchk_text.get('1.0', tk.END).splitlines()
            if ln.strip()
        ]
        if not keywords:
            messagebox.showwarning('알림', '키워드를 입력하세요.')
            return

        self._is_running = True
        self._stop_flag.clear()
        self._kwchk_items.clear()
        self._kwchk_groups.clear()
        self._kwchk_view = -1
        self._kwchk_follow = True
        for b in self._kwchk_btn_list:
            b.destroy()
        self._kwchk_btn_list.clear()
        self.kwchk_strip.xview_moveto(0)
        self.kwchk_det.delete(*self.kwchk_det.get_children())
        for lbl in self.kwchk_tiles.values():
            lbl.config(text='-')
        self.kwchk_kinds.config(text='')
        self.kwchk_open.config(text='')
        self._on_kwchk_select()
        self.kwchk_progress['value'] = 0
        self._btn_running(self.btn_kwchk)
        self._log('키워드 체크 시작')

        # 우리 아이디 — 순위 체크에서 로그인 아이디를 풀어 둔 것까지 함께 본다
        self._kwchk_ours = self._get_target_ids() | set(crawler._resolve_cache.values())
        threading.Thread(
            target=self._kwchk_worker,
            args=(keywords, self.kwchk_area.get()), daemon=True,
        ).start()

    def _kwchk_worker(self, keywords, area):
        n = len(keywords)
        # 월간 조회수 (검색광고 API 키가 config에 설정된 경우만)
        volumes = {}
        cfg = _load_config()
        sa_keys = ('searchad_api_key', 'searchad_secret_key', 'searchad_customer_id')
        if all(cfg.get(k) for k in sa_keys):
            try:
                volumes = fetch_monthly_volumes(
                    keywords, cfg['searchad_api_key'],
                    cfg['searchad_secret_key'], cfg['searchad_customer_id'],
                )
            except Exception as e:
                self._log(f'월간 조회수 조회 실패: {e}')
        else:
            self._log('월간 조회수 생략 — config.json에 searchad_api_key / '
                      'searchad_secret_key / searchad_customer_id 설정 시 표시됩니다.')

        analyzer.clear_cache()
        crawler._init_session()
        for ki, kw in enumerate(keywords):
            if self._stop_flag.is_set():
                break
            self._log(f'분석 중... ({ki+1}/{n}) "{kw}"')
            volume = volumes.get(kw)
            crawler.last_was_adult = False    # 키워드마다 새로 판정한다
            try:
                items = analyzer.top_items(kw, area)
            except AdultBlockedError as e:
                self._log(f'{e}\n"{kw}" — 상위 칸을 볼 수 없음')
                self.root.after(0, self._kwchk_add_group, kw, area, volume, [], '성인')
                continue
            except NaverBlocked as e:
                self._log(str(e))
                self.root.after(0, self._kwchk_add_group, kw, area, volume, [], '차단')
                break
            except Exception as e:
                self._log(f'오류({kw}): {e}')
                self.root.after(0, self._kwchk_add_group, kw, area, volume, [], '오류')
                continue

            self.root.after(0, self._kwchk_add_group, kw, area, volume, items)
            done = 0
            with ThreadPoolExecutor(self.KWCHK_WORKERS) as pool:
                futs = {pool.submit(analyzer.enrich, kw, it): it for it in items}
                for fut in as_completed(futs):
                    if self._stop_flag.is_set():
                        for f in futs:
                            f.cancel()
                        break
                    try:
                        fut.result()
                    except Exception as e:
                        self._log(f'오류({kw} {futs[fut]["rank"]}위): {e}')
                    done += 1
                    self.root.after(0, self._kwchk_fill, ki, futs[fut])
                    self.root.after(0, self._kwchk_progress_set,
                                    (ki + done / max(len(items), 1)) * 100 / n)
        self.root.after(0, self._kwchk_done)

    def _kwchk_progress_set(self, pct):
        self.kwchk_progress['value'] = pct

    def _kwchk_is_ours(self, it) -> bool:
        return bool(it['blog_id']) and it['blog_id'].lower() in self._kwchk_ours

    @staticmethod
    def _kwchk_kind(it) -> str:
        if it['kind'] != '블로그':
            return it['kind']
        return '블홈' if it.get('home') else '인플' if it['influencer'] else '블로그'

    def _kwchk_values(self, it) -> tuple:
        """표 한 줄. 아직 안 온 정보는 … 로 둔다."""
        fmt = lambda v: f'{v:,}' if isinstance(v, int) else '-'
        name, nick, idv = it['source'], '', ''
        age, people, day, posts, rel = '…', '…', '…', '…', '…'
        if it['kind'] == '블로그':
            idv = it['blog_id']
            b = it.get('blog')
            if b:
                name = b['name'] or name
                nick = b['nick']
                age = analyzer.age_text(b['opened']) or '-'
                people, day, posts = fmt(b['buddy']), fmt(b['day']), fmt(b['posts'])
                k = it.get('kw_posts') or {}
                rel = f"{k['total']:,} ({k['in_title']})" if k.get('total') is not None else '-'
        elif it['kind'] == '카페':
            idv = it['cafe_url']
            c = it.get('cafe')
            if c:
                name = c['name'] or name
                age = analyzer.age_text(c['opened']) or '-'
                people, day, posts, rel = '-', '-', '-', '-'
        elif it['kind'] == '웹':
            idv = it['host']
            w = it.get('web_age')
            if w:
                age = analyzer.age_text(w['date']) or '-'
                if w['how'] == '첫 수집':
                    age = '약 ' + age
                pages = it.get('pages') or {}
                people, day, posts, rel = '-', '-', pages.get('text', '-'), '-'
        else:
            age, people, day, posts, rel = '-', '-', '-', '-', '-'
        return (it['rank'], self._kwchk_kind(it), it['title'], name, nick, idv,
                _short_date(it['date'].rstrip('.')) or '-',
                age, people, day, posts, rel)

    def _kwchk_add_group(self, kw, area, volume, items, fail=''):
        g = {'kw': kw, 'area': area, 'volume': volume, 'items': items, 'fail': fail}
        self._kwchk_groups.append(g)
        self._kwchk_add_btn(len(self._kwchk_groups) - 1, g)
        if self._kwchk_follow:
            self._kwchk_show(len(self._kwchk_groups) - 1)
        else:
            self._kwchk_paint_btns()

    def _kwchk_fill(self, ki, it):
        if ki != self._kwchk_view:
            return
        iid = f'd{it["rank"]}'
        if self.kwchk_det.exists(iid):
            self.kwchk_det.item(iid, values=self._kwchk_values(it))
            if iid in self.kwchk_det.selection():
                self._on_kwchk_select()
        self._kwchk_paint_summary()

    def _kwchk_detail_text(self, kw, it) -> str:
        fmt = lambda v: f'{v:,}' if isinstance(v, int) else '-'
        day = lambda d: f'{d:%Y.%m.%d}' if d else '모름'
        name = self._kwchk_values(it)[3]
        lines = [f'{it["rank"]}위 · {self._kwchk_kind(it)} · {name}'
                 + (' · 우리 아이디' if self._kwchk_is_ours(it) else '')]
        if it['title']:
            lines.append(f'{it["title"]}' + (f' ({it["date"]})' if it['date'] else ''))
        if it['kind'] == '블로그' and it.get('blog'):
            b = it['blog']
            lines[0] += f' · 닉네임 {b["nick"] or "없음"}'
            lines.append(f'소개글: {b["intro"] or "없음"}')
            marks = [m for m, on in (('공식', b['official']), ('파워', b['power']),
                                     ('인플루언서', it['influencer'])) if on]
            lines.append(
                f'개설 {day(b["opened"])} ({analyzer.age_text(b["opened"]) or "-"})'
                f' · 이웃 {fmt(b["buddy"])} · 일방문 {fmt(b["day"])}'
                f' · 누적 방문 {fmt(b["total_visit"])} · 총 글 {fmt(b["posts"])}'
                f' · 대표주제 {b["topic"] or "없음"}'
                + (' · ' + '·'.join(marks) if marks else '')
            )
            parts = []
            k = it.get('kw_posts') or {}
            if k.get('total') is not None:
                part = f' (앞 {analyzer.KW_TITLE_SCAN}개 중)' if k['total'] > analyzer.KW_TITLE_SCAN else ''
                parts.append(f"'{kw}' 블로그 안 검색 {k['total']:,}개 · 제목에 든 글 {k['in_title']}개{part}")
            if b['renames'] is not None:
                parts.append(f'블로그 제목 변경 {b["renames"]}회'
                             + (f' (최근 {b["last_rename"]})' if b['last_rename'] else ''))
            if parts:
                lines.append(' · '.join(parts))
        elif it['kind'] == '카페' and it.get('cafe'):
            c = it['cafe']
            lines.append(f'개설 {day(c["opened"])} ({analyzer.age_text(c["opened"]) or "-"})'
                         + (' · 대표카페' if c['power'] else ''))
        elif it['kind'] == '웹' and it.get('web_age'):
            w, r = it['web_age'], it.get('rel') or {}
            pages = it.get('pages') or {}
            lines.append(f'{it["host"]} · 도메인 {w["how"] or "나이"} {day(w["date"])}'
                         f' ({analyzer.age_text(w["date"]) or "-"})'
                         + (f' · 페이지 {pages["text"]} ({pages["how"]} 기준)' if pages.get('how') else ''))
            lines.append(r.get('why', '-'))
            if r.get('page_title'):
                lines.append(f'페이지 제목: {r["page_title"]}')
        return '\n'.join(lines)

    def _kwchk_done(self):
        self._is_running = False
        self._btn_idle(self.btn_kwchk, '조회')
        self.kwchk_progress['value'] = 100
        self._log('키워드 체크 종료')

    # ── 이웃확인 ──────────────────────────────────────────────────────────
    # 키워드별로 신뢰도(통합검색) 상위에 노출된 블로그를 모아
    # 그 블로그의 이웃수·블로그명·닉네임을 보여준다.

    def _build_nbr_left(self, parent):
        self._side_caption(parent, '키워드 (한 줄에 하나)')
        self.nbr_text = self._side_text(parent)
        self.nbr_text.bind('<Control-Return>', lambda _: self._toggle_nbr())

        row = ttk.Frame(parent, style='Side.TFrame')
        row.pack(fill=tk.X, pady=(0, px(8)))
        ttk.Label(row, text='신뢰도 상위', style='Side.TLabel').pack(side=tk.LEFT)
        self.nbr_count_var = tk.IntVar(value=20)
        ttk.Spinbox(
            row, from_=10, to=180, increment=10, style='Side.TSpinbox',
            textvariable=self.nbr_count_var, width=6,
        ).pack(side=tk.LEFT, padx=(px(6), px(4)))
        ttk.Label(row, text='위까지', style='Side.TLabel').pack(side=tk.LEFT)

        self.btn_nbr = self._run_button(parent, '조회', self._toggle_nbr)
        self.btn_nbr.pack(fill=tk.X)
        self.nbr_progress = ttk.Progressbar(parent, mode='determinate', maximum=100,
                                            style='Side.Horizontal.TProgressbar')
        self.nbr_progress.pack(fill=tk.X, pady=(px(8), 0))

    def _build_nbr_right(self, parent):
        COLS = ('키워드', '순위', '이웃수', '블로그명', '닉네임', '아이디', '링크')
        tbl = self._card(parent, fill=tk.BOTH, expand=True, padx=px(10), pady=px(10))

        vs = ttk.Scrollbar(tbl)
        vs.pack(side=tk.RIGHT, fill=tk.Y)
        self.nbr_tree = ttk.Treeview(
            tbl, columns=COLS, show='headings', yscrollcommand=vs.set,
        )
        self.nbr_tree.pack(fill=tk.BOTH, expand=True)
        vs.config(command=self.nbr_tree.yview)

        for col, w, anc, stretch in (
            ('키워드',   110, tk.W,      False),
            ('순위',     45,  tk.E,      False),
            ('이웃수',   70,  tk.E,      False),
            ('블로그명', 180, tk.W,      True),
            ('닉네임',   110, tk.W,      False),
            ('아이디',   95,  tk.W,      False),
            ('링크',     55,  tk.CENTER, False),
        ):
            self.nbr_tree.heading(col, text=col, anchor=tk.CENTER)
            self.nbr_tree.column(col, width=px(w), anchor=anc,
                                 minwidth=px(40), stretch=stretch)

        self.nbr_tree.tag_configure('sep', background=SEP_BG,
                                    foreground=ACCENT, font=FONT_B)
        self.nbr_tree.tag_configure('adult', background=SEP_BG,
                                    foreground=SELECT_BG, font=FONT_B)
        self.nbr_tree.tag_configure('odd',  background=BG_CARD, foreground=FG)
        self.nbr_tree.tag_configure('even', background=ROW_ALT, foreground=FG)
        # 이웃 많은 블로그 — 순위 강조와 같은 초록. Tk 8.6 은 칸 단위 색을
        # 지원하지 않아(열 하나만 못 칠한다) 그 줄 전체를 칠한다.
        self.nbr_tree.tag_configure('big_odd',  background=BG_CARD, foreground=HIT, font=FONT_B)
        self.nbr_tree.tag_configure('big_even', background=ROW_ALT, foreground=HIT, font=FONT_B)

        self.nbr_tree.bind('<Double-1>', self._on_nbr_double)
        ctx = tk.Menu(self.root, tearoff=0, font=FONT)
        ctx.add_command(label='링크 열기', command=self._open_nbr_selected)
        ctx.add_command(label='아이디 복사', command=self._copy_nbr_id)
        self.nbr_tree.bind('<Button-3>', lambda e: self._show_ctx(e, ctx))

    def _nbr_link(self, item) -> str:
        return self._nbr_links.get(item, '')

    def _on_nbr_double(self, event):
        item = self.nbr_tree.identify_row(event.y)
        if item and self._nbr_link(item):
            webbrowser.open(self._nbr_link(item))

    def _open_nbr_selected(self):
        for item in self.nbr_tree.selection():
            if self._nbr_link(item):
                webbrowser.open(self._nbr_link(item))

    def _copy_nbr_id(self):
        ids = [self.nbr_tree.item(i)['values'][5] for i in self.nbr_tree.selection()]
        ids = [str(i) for i in ids if i]
        if ids:
            self.root.clipboard_clear()
            self.root.clipboard_append('\n'.join(ids))
            self._log(f'클립보드에 복사됨: {", ".join(ids)}')

    def _toggle_nbr(self):
        if self._is_running:
            self._stop_flag.set()
            self.btn_nbr.config(text='중지 중...', state=tk.DISABLED)
            return 'break'
        keywords = [
            ln.strip()
            for ln in self.nbr_text.get('1.0', tk.END).splitlines()
            if ln.strip()
        ]
        if not keywords:
            messagebox.showwarning('알림', '키워드를 입력하세요.')
            return 'break'

        self._is_running = True
        self._stop_flag.clear()
        self._nbr_links.clear()
        self._load_adult_cookie()
        for item in self.nbr_tree.get_children():
            self.nbr_tree.delete(item)
        self.nbr_progress['value'] = 0
        self._btn_running(self.btn_nbr)
        self._log('이웃확인 시작')
        threading.Thread(
            target=self._nbr_worker,
            args=(keywords, self.nbr_count_var.get()),
            daemon=True,
        ).start()
        return 'break'

    def _nbr_worker(self, keywords: list, count: int):
        n = len(keywords)
        for ki, keyword in enumerate(keywords):
            if self._stop_flag.is_set():
                break
            self._log(f'신뢰도 상위 조회 중... ({ki+1}/{n}) "{keyword}"')
            try:
                posts = search_naver(
                    keyword, '신뢰도', count, stop_flag=self._stop_flag,
                )
            except AdultBlockedError as e:
                self._log(f'{e}\n"{keyword}" — 순위를 알 수 없음')
                self.root.after(0, self._mark_adult, keyword, str(e), True)
                continue
            except Exception as e:
                self._log(f'오류({keyword}): {e}')
                continue

            # 같은 블로그가 한 키워드에 여러 번 나오면 가장 높은 순위만 남긴다
            seen, rows = set(), []
            for post in posts:
                bid = post['id'].lower()
                if bid in seen:
                    continue
                seen.add(bid)
                rows.append(post)

            if rows and not self._stop_flag.is_set():
                self._log(f'이웃수 가져오는 중... "{keyword}" ({len(rows)}개 블로그)')
            for i, post in enumerate(rows):
                if self._stop_flag.is_set():
                    break
                prof = fetch_blog_profile(post['id'])
                post['nickname'] = prof['nickname']
                post['buddy'] = prof['buddy']
                post['blog_name'] = prof['blog_name']
                pct = ((ki + (i + 1) / max(1, len(rows))) / n) * 100
                self.root.after(0, lambda v=pct: self.nbr_progress.config(value=v))

            self.root.after(0, self._nbr_append, keyword, rows)

        self.root.after(0, self._nbr_done)

    def _nbr_append(self, keyword: str, rows: list):
        self.nbr_tree.insert(
            '', tk.END, values=(f'[{keyword}]', '', '', '', '', '', ''),
            tags=('sep',),
        )
        for i, post in enumerate(rows):
            buddy = post.get('buddy', -1)
            big = isinstance(buddy, int) and buddy >= NBR_BIG      # 이웃 많은 블로그
            stripe = 'even' if i % 2 else 'odd'
            iid = self.nbr_tree.insert(
                '', tk.END,
                values=(
                    keyword,
                    post['rank'],
                    f'{buddy:,}' if isinstance(buddy, int) and buddy >= 0 else '-',
                    post.get('blog_name', ''),
                    post.get('nickname', ''),
                    post['id'],
                    '열기',
                ),
                tags=(('big_' + stripe) if big else stripe,),
            )
            self._nbr_links[iid] = f'https://blog.naver.com/{post["id"]}'
        children = self.nbr_tree.get_children()
        if children:
            self.nbr_tree.see(children[-1])

    def _nbr_done(self):
        self._is_running = False
        self._btn_idle(self.btn_nbr, '조회')
        self.nbr_progress['value'] = 100
        self._log('이웃확인 종료')

    # ── Auto-update ───────────────────────────────────────────────────────

    def _parse_ver(self, v: str) -> tuple:
        try:
            return tuple(int(x) for x in v.lstrip('v').split('.'))
        except Exception:
            return (0, 0, 0)

    @staticmethod
    def _sweep_update_temp():
        """이전 업데이트가 남긴 임시 폴더(받은 zip·압축 해제본)를 지운다."""
        import shutil
        import tempfile
        import time
        try:
            for d in Path(tempfile.gettempdir()).glob('bc_upd_*'):
                try:
                    if d.is_dir() and time.time() - d.stat().st_mtime > 120:
                        shutil.rmtree(d, ignore_errors=True)
                except OSError:
                    pass
        except Exception:
            pass

    def _check_for_update(self):
        self._sweep_update_temp()
        try:
            r = requests.get(
                f'https://api.github.com/repos/{_GITHUB_REPO}/releases/latest',
                headers={'User-Agent': 'BlogCompare', 'Accept': 'application/vnd.github+json'},
                timeout=15,
            )
            r.raise_for_status()
            data = r.json()
            latest = data.get('tag_name', '')
            if not latest:
                self._log(f'업데이트 체크: 버전 정보 없음')
                return
            if self._parse_ver(latest) > self._parse_ver(VERSION):
                url = next(
                    (a['browser_download_url'] for a in data.get('assets', [])
                     if a['name'] == 'BlogCompare.zip'),
                    '',
                )
                self._update_info = {
                    'version': latest,
                    'url': url,
                    'notes': data.get('body', '').strip(),
                }
                if not getattr(sys, 'frozen', False):
                    self._log(f'새 버전 {latest} — 개발 환경에서는 자동 업데이트 안 함')
                    return
                self._log(f'새 버전 {latest} 발견 — 자동 업데이트를 시작합니다')
                self.root.after(0, lambda: self._do_update(url, latest))
            else:
                self._log(f'업데이트 체크: 최신 버전입니다 ({VERSION})')
        except Exception as e:
            self._log(f'업데이트 체크 실패: {e}')

    def _do_update(self, url: str, new_version: str):
        import tempfile, zipfile

        dlg = tk.Toplevel(self.root)
        dlg.title('업데이트')
        dlg.resizable(False, False)
        dlg.configure(bg=BG)
        dlg.grab_set()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        dw, dh = px(360), px(160)
        dlg.geometry(f'{dw}x{dh}+{(sw - dw) // 2}+{(sh - dh) // 2}')

        tk.Label(dlg, text=f'{VERSION}  →  {new_version}',
                 font=FONT_B, bg=BG, fg=FG).pack(pady=(18, 2))
        status_lbl = tk.Label(dlg, text='준비 중...', font=FONT, bg=BG, fg=FG_DIM)
        status_lbl.pack(pady=(2, 8))
        prog = ttk.Progressbar(dlg, length=320, mode='determinate', maximum=100)
        prog.pack(padx=20)

        def _worker():
            try:
                # resolve(): 8.3 축약 경로(BAMHOB~1 등)를 긴 경로로 정규화
                tmp_dir = Path(tempfile.mkdtemp(prefix='bc_upd_')).resolve()
                zip_path = tmp_dir / f'update_{new_version}.zip'
                extract_dir = tmp_dir / 'new'
                extract_dir.mkdir()

                self.root.after(0, lambda: status_lbl.config(text='다운로드 중...'))
                headers = {'User-Agent': 'BlogCompare'}
                try:
                    resp = requests.get(url, headers=headers, stream=True, timeout=120)
                except requests.exceptions.SSLError:
                    # 일부 PC에서 인증서 체인 검증 실패 → 검증 생략하고 재시도
                    resp = requests.get(url, headers=headers, stream=True,
                                        timeout=120, verify=False)
                resp.raise_for_status()
                total = int(resp.headers.get('Content-Length', 0))
                downloaded = 0
                with open(zip_path, 'wb') as f:
                    for chunk in resp.iter_content(65536):
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total > 0:
                            pct = min(80, downloaded * 80 // total)
                            self.root.after(0, lambda p=pct: prog.config(value=p))

                self.root.after(0, lambda: (
                    status_lbl.config(text='압축 해제 중...'),
                    prog.config(value=85),
                ))
                with zipfile.ZipFile(zip_path, 'r') as zf:
                    zf.extractall(extract_dir)

                self.root.after(0, lambda: (
                    status_lbl.config(text='적용 중... 곧 재시작됩니다'),
                    prog.config(value=100),
                ))

                current_dir = Path(BASE_DIR)
                log_path = tmp_dir / 'update.log'
                src  = str(extract_dir).replace("'", "''")
                dst  = str(current_dir).replace("'", "''")
                log  = str(log_path).replace("'", "''")
                exe  = str(current_dir / 'BlogCompare.exe').replace("'", "''")
                errlog = str(current_dir / 'update_error.log').replace("'", "''")
                pid  = os.getpid()

                ps1 = f"""$appPid = {pid}
try {{ Wait-Process -Id $appPid -Timeout 60 -ErrorAction SilentlyContinue }} catch {{}}
Start-Sleep -Seconds 2
$src = '{src}'
$dst = '{dst}'
$log = '{log}'
# onefile 앱이 물려준 PyInstaller 변수(_MEIPASS2 등)를 지운다. 그대로 두면
# 새 exe 가 '이미 압축 해제됐다'고 착각해 구버전 임시폴더의 python DLL 을
# 찾다가 죽는다("Failed to load Python DLL").
Get-ChildItem Env: | Where-Object {{ $_.Name -like '_PYI*' -or $_.Name -eq '_MEIPASS2' }} |
    ForEach-Object {{ Remove-Item -LiteralPath ('Env:' + $_.Name) -ErrorAction SilentlyContinue }}
'START' | Out-File $log -Encoding UTF8
try {{
    # robocopy 는 이름이 아니라 절대경로로 부른다 — PATH 에 %SystemRoot% 가
    # 확장되지 않은 채 들어간 PC 에서는 이름 해석이 실패해 조용히 무효가 된다.
    $rc = Join-Path $env:SystemRoot 'System32\\Robocopy.exe'
    if (Test-Path -LiteralPath $rc) {{
        & $rc $src $dst /E /R:3 /W:2 /XF settings.json config.json | Out-Null
        if ($LASTEXITCODE -ge 8) {{ throw "robocopy failed: $LASTEXITCODE" }}
    }} else {{
        # robocopy 가 없는 PC 폴백: 설정 파일만 빼고 직접 복사
        'NO_ROBOCOPY' | Out-File $log -Append -Encoding UTF8
        $skip = @('settings.json', 'config.json')
        Get-ChildItem -LiteralPath $src -Recurse -File | ForEach-Object {{
            if ($skip -notcontains $_.Name) {{
                $rel = $_.FullName.Substring($src.Length).TrimStart('\\')
                $to = Join-Path $dst $rel
                $dir = Split-Path $to -Parent
                if (-not (Test-Path -LiteralPath $dir)) {{
                    New-Item -ItemType Directory -Path $dir -Force | Out-Null
                }}
                Copy-Item -LiteralPath $_.FullName -Destination $to -Force
            }}
        }}
    }}
    'COPY_DONE' | Out-File $log -Append -Encoding UTF8
    Remove-Item -LiteralPath '{errlog}' -Force -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath '{exe}') {{
        'LAUNCH' | Out-File $log -Append -Encoding UTF8
        Start-Process -FilePath '{exe}'
    }} else {{
        'EXE_NOT_FOUND' | Out-File $log -Append -Encoding UTF8
    }}
}} catch {{
    "ERROR: $_" | Out-File $log -Append -Encoding UTF8
    # 실패하면 앱 폴더에 로그를 남기고(원인 추적용) 구버전이라도 다시 띄운다
    Copy-Item -LiteralPath $log -Destination '{errlog}' -Force -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath '{exe}') {{ Start-Process -FilePath '{exe}' }}
}}
# 받은 파일(zip·압축 해제본·이 스크립트)은 PC 에 남기지 않는다. 실행 중인
# 스크립트가 자기 폴더를 지우면 실패할 수 있어 별도 프로세스로 떼어낸다.
$tmp = Split-Path $log
$ps = Join-Path $env:SystemRoot 'System32\\WindowsPowerShell\\v1.0\\powershell.exe'
if (Test-Path -LiteralPath $ps) {{
    Start-Process -FilePath $ps -WindowStyle Hidden -ArgumentList @(
        '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command',
        "Start-Sleep -Seconds 6; Remove-Item -LiteralPath '$tmp' -Recurse -Force -ErrorAction SilentlyContinue")
}} else {{
    Start-Sleep -Seconds 3
    Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
}}
"""
                ps1_path = tmp_dir / 'update_apply.ps1'
                ps1_path.write_text(ps1, encoding='utf-8-sig')
                self.root.after(1500, lambda: self._launch_updater(ps1_path))

            except Exception as e:
                self.root.after(0, lambda: status_lbl.config(
                    text=f'오류: {e}', fg='#DC2626'))

        threading.Thread(target=_worker, daemon=True).start()

    def _launch_updater(self, ps1_path: Path):
        import ctypes
        try:
            # ShellExecuteW 는 지금 프로세스의 환경을 그대로 물려준다.
            # PyInstaller 변수가 따라가면 새 exe 가 부팅에 실패한다.
            for key in [k for k in os.environ
                        if k.startswith('_PYI') or k == '_MEIPASS2']:
                os.environ.pop(key, None)
            args = f'-NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "{ps1_path}"'
            ret = ctypes.windll.shell32.ShellExecuteW(None, 'open', 'powershell', args, None, 0)
            if ret <= 32:
                raise RuntimeError(f'ShellExecute 실패: {ret}')
            self.root.quit()
            sys.exit(0)
        except Exception as e:
            self._log(f'업데이터 실행 실패: {e}')


def main():
    _enable_dpi_awareness()        # tk.Tk() 보다 반드시 먼저
    root = tk.Tk()
    _apply_scaling(root)
    App(root)
    root.mainloop()


if __name__ == '__main__':
    main()
