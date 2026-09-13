import json
import os
import sys
import time
import random
import threading
import webbrowser
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path

import re

import requests

from crawler import (
    search_naver, resolve_blog_id, fetch_blog_name, fetch_post_date,
    fetch_first_page_titles, title_matches_keyword, first_page_search_url,
    fetch_popular_section, main_search_url, fetch_monthly_volumes,
)

VERSION = 'v1.1.05'
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


BG      = '#F4F6F8'
BG_CARD = '#FFFFFF'
FG      = '#1A1A2E'
FG_DIM  = '#6B7280'
BORDER  = '#D1D5DB'
ACCENT  = '#1A3A6B'
FONT    = ('Malgun Gothic', 9)
FONT_B  = ('Malgun Gothic', 9, 'bold')
FONT_SM = ('Malgun Gothic', 10)
FONT_U  = ('Malgun Gothic', 9, 'underline')


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
        win_w = min(px(1050), self.root.winfo_screenwidth()  - px(40))
        win_h = min(px(860),  self.root.winfo_screenheight() - px(80))
        self.root.geometry(f'{win_w}x{win_h}')
        self.root.minsize(min(px(900), win_w), min(px(600), win_h))
        self.root.configure(bg=BG)

        self._stop_flag  = threading.Event()
        self._is_running = False
        self._post_links: dict = {}
        self._res_prev_keyword = None     # 실시간 결과 표시용
        self._res_total = 0
        self._counts = {'블로그': 100, '신뢰도': 100, '인기글': 100}
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
        s.configure('TRadiobutton', background=BG, foreground=FG)
        s.configure('TLabelframe', background=BG, bordercolor=BORDER, relief='groove')
        s.configure('TLabelframe.Label', font=FONT_B, foreground='#374151', background=BG)
        s.configure('TSpinbox', fieldbackground=BG_CARD, bordercolor=BORDER,
                    arrowcolor='#6B7280', arrowsize=12)
        s.configure('TPanedwindow', background=BORDER)
        s.configure('TScrollbar', troughcolor='#F1F3F5', background='#CBD5E1',
                    bordercolor='#E2E8F0', arrowcolor='#94A3B8')
        s.map('TScrollbar', background=[('active', '#94A3B8'), ('pressed', '#64748B')])

        s.configure('Treeview', font=FONT, rowheight=px(24),
                    background=BG_CARD, fieldbackground=BG_CARD,
                    foreground=FG, borderwidth=0)
        s.configure('Treeview.Heading', font=FONT_B, background='#E9ECEF',
                    foreground='#374151', relief='flat', padding=(0, 5))
        s.map('Treeview.Heading', background=[('active', '#DEE2E6')])
        s.map('Treeview', background=[('selected', '#DBEAFE')],
              foreground=[('selected', FG)])

        s.configure('TProgressbar', troughcolor='#E5E7EB',
                    background='#3B82F6', borderwidth=0, thickness=px(6))

        # 모드 선택 라디오 (배경 강조)
        s.configure('Mode.TRadiobutton', background='#DBEAFE',
                    foreground=FG, font=FONT_B)
        s.map('Mode.TRadiobutton',
              background=[('active', '#BFDBFE')])

    # ── UI ────────────────────────────────────────────────────────────────

    def _build_ui(self):
        pane = tk.PanedWindow(
            self.root, orient=tk.HORIZONTAL,
            sashwidth=px(4), sashrelief='flat', bg=BORDER,
        )
        pane.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        left  = ttk.Frame(pane, width=px(310))
        right = ttk.Frame(pane)
        pane.add(left,  minsize=px(250))
        pane.add(right, minsize=px(550))
        self.root.after(50, lambda: pane.sash_place(0, 320, 0))

        self._build_left(left)
        self._build_right(right)

    def _build_left(self, parent):
        # 모드 선택 (순위 체크 / 키워드 체크) — 배경색으로 구분
        row_mode = tk.Frame(parent, bg='#DBEAFE',
                            highlightbackground='#93C5FD', highlightthickness=1)
        row_mode.pack(fill=tk.X, padx=4, pady=(4, 2))
        self.mode_var = tk.StringVar(value='순위 체크')
        for m in ('순위 체크', '키워드 체크'):
            ttk.Radiobutton(
                row_mode, text=m,
                variable=self.mode_var, value=m,
                command=self._on_mode_change,
                style='Mode.TRadiobutton',
            ).pack(side=tk.LEFT, padx=(10, 16), pady=5)

        self.rank_left = ttk.Frame(parent)
        self.rank_left.pack(fill=tk.BOTH, expand=True)
        self._build_rank_left(self.rank_left)

        self.kwchk_left = ttk.Frame(parent)
        self._build_kwchk_left(self.kwchk_left)

    def _build_rank_left(self, parent):
        # 비교대상 아이디 입력
        self.lf_id = ttk.LabelFrame(parent, text='★ 비교대상 아이디 입력')
        lf_id = self.lf_id
        lf_id.pack(fill=tk.BOTH, expand=True, padx=4, pady=(4, 3))
        self._update_saved_label()

        ys = ttk.Scrollbar(lf_id)
        ys.pack(side=tk.RIGHT, fill=tk.Y)
        self.id_text = tk.Text(
            lf_id, yscrollcommand=ys.set, font=FONT, wrap=tk.NONE, undo=True,
            bg=BG_CARD, fg=FG, relief='flat', insertbackground=FG,
            selectbackground='#BFDBFE', bd=0, padx=4, pady=4,
            height=8,  # 요청 높이 축소 — 라디오 추가로 하단 짤림 방지
        )
        self.id_text.pack(fill=tk.BOTH, expand=True)
        ys.config(command=self.id_text.yview)

        # 검색
        lf_search = ttk.LabelFrame(parent, text='★ 검색')
        lf_search.pack(fill=tk.BOTH, expand=True, padx=4, pady=(0, 3))

        # 남는 세로 공간은 키워드 입력칸이 채움 (하단 빈 공간 방지)
        kw_frame = tk.Frame(lf_search, bg=BG_CARD, relief='solid', bd=1)
        kw_frame.pack(fill=tk.BOTH, expand=True, padx=6, pady=(6, 4))
        kw_ys = ttk.Scrollbar(kw_frame)
        kw_ys.pack(side=tk.RIGHT, fill=tk.Y)
        self.kw_text = tk.Text(
            kw_frame, yscrollcommand=kw_ys.set,
            font=FONT, bg=BG_CARD, fg=FG,
            insertbackground=FG, relief='flat', bd=0,
            padx=4, pady=3, height=10, wrap=tk.NONE,
        )
        self.kw_text.pack(fill=tk.BOTH, expand=True)
        kw_ys.config(command=self.kw_text.yview)
        self.kw_text.bind('<Control-Return>', lambda _: self._toggle_search())
        self.kw_text.bind('<<Paste>>', self._on_kw_paste)

        row_btn = ttk.Frame(lf_search)
        row_btn.pack(fill=tk.X, padx=6, pady=(0, 4))
        self.btn_search = tk.Button(
            row_btn, text='검색', command=self._toggle_search,
            bg=ACCENT, fg='white', font=FONT_B,
            relief='raised', bd=2, cursor='hand2',
            activebackground='#2A5090', activeforeground='white',
            pady=4,
        )
        self.btn_search.pack(fill=tk.X)

        row_type = ttk.Frame(lf_search)
        row_type.pack(fill=tk.X, padx=6, pady=(0, 4))
        self.type_var = tk.StringVar(value='블로그')
        for t in ('블로그', '신뢰도', '인기글'):
            ttk.Radiobutton(
                row_type, text=t,
                variable=self.type_var, value=t,
            ).pack(side=tk.LEFT, padx=(0, 10))

        row_cnt = ttk.Frame(lf_search)
        row_cnt.pack(fill=tk.X, padx=6, pady=(0, 6))
        self.cnt_label_var = tk.StringVar(value='블로그 조회수')
        ttk.Label(row_cnt, textvariable=self.cnt_label_var, font=FONT_B).pack(side=tk.LEFT)
        self.count_var = tk.IntVar(value=100)
        ttk.Spinbox(
            row_cnt, from_=10, to=1000, increment=10,
            textvariable=self.count_var, width=6,
        ).pack(side=tk.LEFT, padx=(8, 0))
        self.btn_save_gist = tk.Button(
            row_cnt, text='아이디&설정 저장', command=self._save_to_gist,
            bg='#9CA3AF', fg='white', font=('Malgun Gothic', 8),
            relief='raised', bd=1, cursor='hand2',
            activebackground='#6B7280', activeforeground='white',
            padx=7, pady=2,
        )
        self.btn_save_gist.pack(side=tk.RIGHT, padx=(0, 2))

        self.type_var.trace_add('write', self._on_type_change)
        self.count_var.trace_add('write', self._on_count_change)

        self.progress = ttk.Progressbar(lf_search, mode='determinate', maximum=100)
        self.progress.pack(fill=tk.X, padx=6, pady=(0, 6))

    def _build_kwchk_left(self, parent):
        lf_kw = ttk.LabelFrame(parent, text='★ 키워드 입력 (한 줄에 하나)')
        lf_kw.pack(fill=tk.BOTH, expand=True, padx=4, pady=(4, 3))
        ys = ttk.Scrollbar(lf_kw)
        ys.pack(side=tk.RIGHT, fill=tk.Y)
        self.kwchk_text = tk.Text(
            lf_kw, yscrollcommand=ys.set, font=FONT, wrap=tk.NONE, undo=True,
            bg=BG_CARD, fg=FG, relief='flat', insertbackground=FG,
            selectbackground='#BFDBFE', bd=0, padx=4, pady=4,
        )
        self.kwchk_text.pack(fill=tk.BOTH, expand=True)
        ys.config(command=self.kwchk_text.yview)
        self.kwchk_text.bind('<Control-Return>', lambda _: self._toggle_kwchk())

        bottom = ttk.Frame(parent)
        bottom.pack(fill=tk.X, padx=4, pady=(0, 6))
        ttk.Label(
            bottom,
            text='블로그탭·카페탭 최근 1주일 첫 페이지에서\n제목에 키워드 단어가 모두 든 글 수를 셉니다.',
            foreground=FG_DIM,
        ).pack(fill=tk.X, padx=2, pady=(0, 4))
        self.btn_kwchk = tk.Button(
            bottom, text='조회', command=self._toggle_kwchk,
            bg=ACCENT, fg='white', font=FONT_B,
            relief='raised', bd=2, cursor='hand2',
            activebackground='#2A5090', activeforeground='white',
            pady=4,
        )
        self.btn_kwchk.pack(fill=tk.X)
        self.kwchk_progress = ttk.Progressbar(bottom, mode='determinate', maximum=100)
        self.kwchk_progress.pack(fill=tk.X, pady=(6, 0))

    def _build_right(self, parent):
        self.rank_right = ttk.Frame(parent)
        self.rank_right.pack(fill=tk.BOTH, expand=True)
        self._build_rank_right(self.rank_right)

        self.kwchk_right = ttk.Frame(parent)
        self._build_kwchk_right(self.kwchk_right)

        # 로그 (오른쪽 하단, 두 모드 공용)
        lf_log = ttk.LabelFrame(parent, text='로그')
        lf_log.pack(fill=tk.X, padx=(2, 4), pady=(0, 4))
        lf_log.pack_propagate(False)
        lf_log.configure(height=px(110))
        self.lf_log = lf_log

        ls = ttk.Scrollbar(lf_log)
        ls.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text = tk.Text(
            lf_log, yscrollcommand=ls.set, state=tk.DISABLED,
            font=FONT_SM, wrap=tk.WORD,
            bg=BG_CARD, fg=FG_DIM, relief='flat',
            bd=0, padx=4, pady=4,
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)
        ls.config(command=self.log_text.yview)

    def _build_rank_right(self, parent):
        COLS = ('키워드', '순위', '제목', '블로그명', '아이디', '작성일', '링크')
        tbl = ttk.Frame(parent)
        tbl.pack(fill=tk.BOTH, expand=True, padx=(2, 4), pady=(4, 2))

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

    # 결과 요약 컬럼 폭 (Label width, 문자 단위)
    SUM_COL_KW  = 19
    SUM_COL_VOL = 12
    SUM_COL_CNT = 12
    SUM_COL_POP = 18
    SUM_COL_LNK = 11

    def _build_kwchk_right(self, parent):
        lf_sum = ttk.LabelFrame(parent, text='결과 요약')
        lf_sum.pack(fill=tk.BOTH, expand=True, padx=(2, 4), pady=(4, 2))

        hdr = tk.Frame(lf_sum, bg='#E9ECEF')
        hdr.pack(fill=tk.X)
        for text, w in (
            ('키워드',     self.SUM_COL_KW),
            ('블로그탭',   self.SUM_COL_CNT),
            ('카페탭',     self.SUM_COL_CNT),
            ('인기글',     self.SUM_COL_POP),
            ('조회수',     self.SUM_COL_VOL),
            ('블 링크',    self.SUM_COL_LNK),
            ('카 링크',    self.SUM_COL_LNK),
        ):
            tk.Label(hdr, text=text, width=w, font=FONT_B,
                     bg='#E9ECEF', fg='#374151', pady=5).pack(side=tk.LEFT)

        wrap = tk.Frame(lf_sum, bg=BG_CARD)
        wrap.pack(fill=tk.BOTH, expand=True)
        canvas = tk.Canvas(wrap, height=px(280), bg=BG_CARD, highlightthickness=0)
        sb = ttk.Scrollbar(wrap, command=canvas.yview)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        canvas.configure(yscrollcommand=sb.set)
        self.kwchk_sum_rows = tk.Frame(canvas, bg=BG_CARD)
        win = canvas.create_window((0, 0), window=self.kwchk_sum_rows, anchor='nw')
        canvas.bind('<Configure>',
                    lambda e: canvas.itemconfigure(win, width=e.width))
        self.kwchk_sum_rows.bind(
            '<Configure>',
            lambda _e: canvas.configure(scrollregion=canvas.bbox('all')))

        lf_det = ttk.LabelFrame(parent, text='상세 (제목별 판정)')
        lf_det.pack(fill=tk.BOTH, expand=True, padx=(2, 4), pady=(0, 2))
        vs = ttk.Scrollbar(lf_det)
        vs.pack(side=tk.RIGHT, fill=tk.Y)
        det_cols = ('키워드', '탭', '판정', '제목')
        self.kwchk_det = ttk.Treeview(
            lf_det, columns=det_cols, show='headings', yscrollcommand=vs.set,
            height=10,  # 요약:상세 대략 1:1 배분
        )
        self.kwchk_det.pack(fill=tk.BOTH, expand=True)
        vs.config(command=self.kwchk_det.yview)
        for col, w, anc, stretch in (
            ('키워드', 130, tk.W,      False),
            ('탭',    60,  tk.CENTER, False),
            ('판정',   45,  tk.CENTER, False),
            ('제목',   420, tk.W,      True),
        ):
            self.kwchk_det.heading(col, text=col, anchor=tk.CENTER)
            self.kwchk_det.column(col, width=px(w), anchor=anc,
                                  minwidth=px(40), stretch=stretch)
        self.kwchk_det.tag_configure('ok', foreground='#1E8259')
        self.kwchk_det.tag_configure('no', foreground='#9CA3AF')
        self.kwchk_det.bind('<Double-1>', self._on_kwchk_double)
        self._kwchk_links: dict = {}

    def _on_mode_change(self):
        if self.mode_var.get() == '키워드 체크':
            self.rank_left.pack_forget()
            self.rank_right.pack_forget()
            self.kwchk_left.pack(fill=tk.BOTH, expand=True)
            self.kwchk_right.pack(fill=tk.BOTH, expand=True, before=self.lf_log)
        else:
            self.kwchk_left.pack_forget()
            self.kwchk_right.pack_forget()
            self.rank_left.pack(fill=tk.BOTH, expand=True)
            self.rank_right.pack(fill=tk.BOTH, expand=True, before=self.lf_log)

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
                self.lf_id.config(text=f'★ 비교대상 아이디 입력  (최종 저장: {self._saved_at})')
            else:
                self.lf_id.config(text='★ 비교대상 아이디 입력')
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

        for item in self.tree.get_children():
            self.tree.delete(item)

        # 실시간 표시 상태 — 키워드 하나가 끝날 때마다 결과를 이어 붙인다
        self._res_prev_keyword = None
        self._res_total = 0
        self.tree.tag_configure('sep', background='#D1D5DB', foreground='#374151')

        self.progress['value'] = 0
        self.btn_search.config(text='중지', bg='#EF4444', activebackground='#DC2626')
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

    def _on_progress(self, pct: float):
        self.progress['value'] = pct

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

        for post in matched:
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
                    post.get('date', ''),
                    short_link,
                ),
            )
            self._post_links[iid] = post['link']

        self._res_total += len(matched)

        # 방금 추가한 줄이 보이도록 따라 내려간다
        children = self.tree.get_children()
        if children:
            self.tree.see(children[-1])

    def _finish_results(self, total: int):
        self._is_running = False
        self.btn_search.config(
            text='검색', bg=ACCENT, activebackground='#2A5090', state=tk.NORMAL
        )
        self.progress['value'] = 100
        self._log(
            '조회 종료',
            error_suffix='조회된 포스팅이 없습니다.' if not total else '',
        )


    # ── 키워드 체크 ────────────────────────────────────────────────────────

    def _on_kwchk_double(self, event):
        item = self.kwchk_det.identify_row(event.y)
        if item and item in self._kwchk_links:
            webbrowser.open(self._kwchk_links[item])

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
        self._kwchk_links.clear()
        for child in self.kwchk_sum_rows.winfo_children():
            child.destroy()
        for item in self.kwchk_det.get_children():
            self.kwchk_det.delete(item)
        self.kwchk_progress['value'] = 0
        self.btn_kwchk.config(text='중지', bg='#EF4444', activebackground='#DC2626')
        self._log('키워드 체크 시작')
        threading.Thread(target=self._kwchk_worker, args=(keywords,), daemon=True).start()

    def _kwchk_worker(self, keywords):
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
        step = 0
        for ki, kw in enumerate(keywords):
            if self._stop_flag.is_set():
                break
            self._log(f'조회 중... ({ki+1}/{n}) "{kw}"')
            counts = {}
            for tab in ('블로그', '카페'):
                if self._stop_flag.is_set():
                    break
                try:
                    posts = fetch_first_page_titles(kw, tab)
                except Exception as e:
                    self._log(f'오류({kw}/{tab}탭): {e}')
                    posts = []
                valid = 0
                rows = []
                for p in posts:
                    ok = title_matches_keyword(kw, p['title'])
                    valid += ok
                    rows.append((p['title'], p['link'], ok))
                counts[tab] = (valid, len(posts))
                self.root.after(0, self._kwchk_add_rows, kw, tab, rows)
                step += 1
                self.root.after(0, self._kwchk_progress_set, step * 100 / (n * 3))
                time.sleep(random.uniform(0.6, 1.4))
            # 통합검색 인기글 섹션 유무 체크
            pop = ''
            if not self._stop_flag.is_set():
                try:
                    pop = fetch_popular_section(kw)
                except Exception as e:
                    self._log(f'오류({kw}/인기글): {e}')
                step += 1
                self.root.after(0, self._kwchk_progress_set, step * 100 / (n * 3))
                time.sleep(random.uniform(0.6, 1.4))
            if counts:
                self.root.after(0, self._kwchk_add_summary,
                                kw, counts, pop, volumes.get(kw))
        self.root.after(0, self._kwchk_done)

    def _kwchk_progress_set(self, pct):
        self.kwchk_progress['value'] = pct

    def _kwchk_add_rows(self, kw, tab, rows):
        for title, link, ok in rows:
            iid = self.kwchk_det.insert(
                '', tk.END,
                values=(kw, f'{tab}탭', 'O' if ok else 'X', title),
                tags=('ok',) if ok else ('no',),
            )
            self._kwchk_links[iid] = link

    def _kwchk_add_summary(self, kw, counts, pop='', volume=None):
        fmt = lambda v: f'{v[0]} / {v[1]}' if v else '-'
        row = tk.Frame(self.kwchk_sum_rows, bg=BG_CARD)
        row.pack(fill=tk.X)
        tk.Label(row, text=kw, width=self.SUM_COL_KW,
                 font=FONT, bg=BG_CARD, fg=FG, pady=4).pack(side=tk.LEFT)
        for tab in ('블로그', '카페'):
            tk.Label(row, text=fmt(counts.get(tab)), width=self.SUM_COL_CNT,
                     font=FONT, bg=BG_CARD, fg=FG, pady=4).pack(side=tk.LEFT)
        # 인기글 섹션: 있으면 주제명(초록), 없으면 '신뢰도'(주황) — 둘 다 통합검색 링크
        if pop:
            # '맛집 인기글' → '맛집', 주제 없이 '인기글'만이면 그대로 '인기글'
            disp, color = pop[:-len('인기글')].strip() or '인기글', '#1E8259'
        else:
            disp, color = '신뢰도', '#D97706'
        pop_lbl = tk.Label(row, text=disp, width=self.SUM_COL_POP,
                           font=FONT_U, bg=BG_CARD, fg=color,
                           cursor='hand2', pady=4)
        pop_lbl.bind('<Button-1>',
                     lambda _e, u=main_search_url(kw): webbrowser.open(u))
        pop_lbl.pack(side=tk.LEFT)
        vol_txt = f'{volume:,}' if isinstance(volume, int) else '-'
        tk.Label(row, text=vol_txt, width=self.SUM_COL_VOL,
                 font=FONT, bg=BG_CARD, fg=FG, pady=4).pack(side=tk.LEFT)
        for tab in ('블로그', '카페'):
            url = first_page_search_url(kw, tab)
            lnk = tk.Label(row, text='열기', width=self.SUM_COL_LNK,
                           font=FONT_U, bg=BG_CARD, fg='#2563EB',
                           cursor='hand2', pady=4)
            lnk.pack(side=tk.LEFT)
            lnk.bind('<Button-1>', lambda _e, u=url: webbrowser.open(u))

    def _kwchk_done(self):
        self._is_running = False
        self.btn_kwchk.config(
            text='조회', bg=ACCENT, activebackground='#2A5090', state=tk.NORMAL,
        )
        self.kwchk_progress['value'] = 100
        self._log('키워드 체크 종료')

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
