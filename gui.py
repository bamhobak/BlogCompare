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

VERSION = 'v1.0.97'
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


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(f'Blog Compare {VERSION}')
        self.root.geometry('1050x860')
        self.root.minsize(900, 600)
        self.root.configure(bg=BG)

        self._stop_flag  = threading.Event()
        self._is_running = False
        self._post_links: dict = {}
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

        s.configure('Treeview', font=FONT, rowheight=24,
                    background=BG_CARD, fieldbackground=BG_CARD,
                    foreground=FG, borderwidth=0)
        s.configure('Treeview.Heading', font=FONT_B, background='#E9ECEF',
                    foreground='#374151', relief='flat', padding=(0, 5))
        s.map('Treeview.Heading', background=[('active', '#DEE2E6')])
        s.map('Treeview', background=[('selected', '#DBEAFE')],
              foreground=[('selected', FG)])

        s.configure('TProgressbar', troughcolor='#E5E7EB',
                    background='#3B82F6', borderwidth=0, thickness=6)

        # 모드 선택 라디오 (배경 강조)
        s.configure('Mode.TRadiobutton', background='#DBEAFE',
                    foreground=FG, font=FONT_B)
        s.map('Mode.TRadiobutton',
              background=[('active', '#BFDBFE')])

    # ── UI ────────────────────────────────────────────────────────────────

    def _build_ui(self):
        pane = tk.PanedWindow(
            self.root, orient=tk.HORIZONTAL,
            sashwidth=4, sashrelief='flat', bg=BORDER,
        )
        pane.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        left  = ttk.Frame(pane, width=310)
        right = ttk.Frame(pane)
        pane.add(left,  minsize=250)
        pane.add(right, minsize=550)
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
        lf_log.configure(height=110)
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
            self.tree.column(col, width=w, anchor=anc, minwidth=40, stretch=stretch)

        self.tree.bind('<Double-1>', self._on_double_click)
        self.tree.bind('<ButtonRelease-1>', self._on_click)

        ctx = tk.Menu(self.root, tearoff=0, font=FONT)
        ctx.add_command(label='링크 열기', command=self._open_selected)
        ctx.add_command(label='아이디 복사', command=self._copy_id)
        self.tree.bind('<Button-3>', lambda e: self._show_ctx(e, ctx))

    # 결과 요약 컬럼 폭 (Label width, 문자 단위)
    SUM_COL_KW  = 24
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
            ('월간 조회수', self.SUM_COL_VOL),
            ('블로그탭',   self.SUM_COL_CNT),
            ('카페탭',     self.SUM_COL_CNT),
            ('인기글',     self.SUM_COL_POP),
            ('블 링크',    self.SUM_COL_LNK),
            ('카 링크',    self.SUM_COL_LNK),
        ):
            tk.Label(hdr, text=text, width=w, font=FONT_B,
                     bg='#E9ECEF', fg='#374151', pady=5).pack(side=tk.LEFT)

        wrap = tk.Frame(lf_sum, bg=BG_CARD)
        wrap.pack(fill=tk.BOTH, expand=True)
        canvas = tk.Canvas(wrap, height=280, bg=BG_CARD, highlightthickness=0)
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
            self.kwchk_det.column(col, width=w, anchor=anc, minwidth=40, stretch=stretch)
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

        self.root.after(0, self._show_results, all_matched, target_ids, global_resolved)

    def _on_progress(self, pct: float):
        self.progress['value'] = pct

    def _show_results(self, matched: list, target_ids: set, resolved_map: dict):
        self._is_running = False
        self.btn_search.config(
            text='검색', bg=ACCENT, activebackground='#2A5090', state=tk.NORMAL
        )
        self.progress['value'] = 100

        for item in self.tree.get_children():
            self.tree.delete(item)
        self._post_links.clear()

        reverse_map  = {v: k for k, v in resolved_map.items()}
        prev_keyword = None

        self.tree.tag_configure('sep', background='#D1D5DB', foreground='#374151')

        for post in matched:
            keyword = post.get('keyword', '')

            if prev_keyword is not None and keyword != prev_keyword:
                self.tree.insert(
                    '', tk.END,
                    values=(f'[{keyword}]', '', '', '', '', '', ''),
                    tags=('sep',),
                )
            elif prev_keyword is None:
                self.tree.insert(
                    '', tk.END,
                    values=(f'[{keyword}]', '', '', '', '', '', ''),
                    tags=('sep',),
                )
            prev_keyword = keyword

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
                    post['blog_name'],
                    display_id,
                    post['date'],
                    short_link,
                ),
            )
            self._post_links[iid] = post['link']

        self._log(
            '조회 종료',
            error_suffix='조회된 포스팅이 없습니다.' if not matched else '',
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
        vol_txt = f'{volume:,}' if isinstance(volume, int) else '-'
        tk.Label(row, text=vol_txt, width=self.SUM_COL_VOL,
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

    def _check_for_update(self):
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
                self.root.after(0, self._show_update_dialog)
            else:
                self._log(f'업데이트 체크: 최신 버전입니다 ({VERSION})')
        except Exception as e:
            self._log(f'업데이트 체크 실패: {e}')

    def _show_update_dialog(self):
        info = self._update_info
        ver = info.get('version', '')

        dlg = tk.Toplevel(self.root)
        dlg.title('업데이트 알림')
        dlg.resizable(False, False)
        dlg.configure(bg=BG)
        dlg.grab_set()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        dlg.geometry(f'360x190+{(sw - 360) // 2}+{(sh - 190) // 2}')

        tk.Label(dlg, text='업데이트가 있습니다.',
                 font=FONT_B, bg=BG, fg=FG).pack(pady=(30, 4))
        tk.Label(dlg, text='업데이트를 진행해주세요.',
                 font=FONT, bg=BG, fg=FG_DIM).pack()
        tk.Label(dlg, text=f'{VERSION}  →  {ver}',
                 font=FONT, bg=BG, fg='#6B7280').pack(pady=(4, 18))

        if not getattr(sys, 'frozen', False):
            tk.Label(dlg, text='개발 환경에서는 자동 업데이트를 지원하지 않습니다.',
                     font=FONT, bg=BG, fg='#DC2626').pack()
            tk.Button(dlg, text='확인', command=dlg.destroy,
                      bg=ACCENT, fg='white', font=FONT_B,
                      relief='raised', bd=2, padx=20, pady=4).pack(pady=8)
            return

        row = tk.Frame(dlg, bg=BG)
        row.pack()

        def _start():
            dlg.destroy()
            self._do_update(info.get('url', ''), ver)

        tk.Button(row, text='업데이트', command=_start,
                  bg='#1E8259', fg='white', font=FONT_B,
                  relief='raised', bd=2, padx=18, pady=5, cursor='hand2',
                  ).pack(side=tk.LEFT, padx=(0, 10))
        tk.Button(row, text='나중에', command=dlg.destroy,
                  bg='#9CA3AF', fg='white', font=FONT_B,
                  relief='raised', bd=2, padx=18, pady=5, cursor='hand2',
                  ).pack(side=tk.LEFT)

    def _do_update(self, url: str, new_version: str):
        import tempfile, zipfile

        dlg = tk.Toplevel(self.root)
        dlg.title('업데이트 중...')
        dlg.resizable(False, False)
        dlg.configure(bg=BG)
        dlg.grab_set()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        dlg.geometry(f'360x130+{(sw - 360) // 2}+{(sh - 130) // 2}')

        status_lbl = tk.Label(dlg, text='준비 중...', font=FONT, bg=BG, fg=FG_DIM)
        status_lbl.pack(pady=(22, 8))
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
                pid  = os.getpid()

                ps1 = f"""$appPid = {pid}
try {{ Wait-Process -Id $appPid -Timeout 60 -ErrorAction SilentlyContinue }} catch {{}}
Start-Sleep -Seconds 2
$src = '{src}'
$dst = '{dst}'
$log = '{log}'
'START' | Out-File $log -Encoding UTF8
try {{
    # robocopy: 경로 문자열 계산 없이 트리 복사 (설정 파일은 덮어쓰지 않음)
    robocopy $src $dst /E /R:3 /W:2 /XF settings.json config.json | Out-Null
    if ($LASTEXITCODE -ge 8) {{
        throw "robocopy failed: $LASTEXITCODE"
    }}
    'COPY_DONE' | Out-File $log -Append -Encoding UTF8
    if (Test-Path -LiteralPath '{exe}') {{
        'LAUNCH' | Out-File $log -Append -Encoding UTF8
        Start-Process -FilePath '{exe}'
    }} else {{
        'EXE_NOT_FOUND' | Out-File $log -Append -Encoding UTF8
    }}
}} catch {{
    "ERROR: $_" | Out-File $log -Append -Encoding UTF8
}}
Start-Sleep -Seconds 3
Remove-Item -Path (Split-Path $log) -Recurse -Force -ErrorAction SilentlyContinue
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
            args = f'-NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "{ps1_path}"'
            ret = ctypes.windll.shell32.ShellExecuteW(None, 'open', 'powershell', args, None, 0)
            if ret <= 32:
                raise RuntimeError(f'ShellExecute 실패: {ret}')
            self.root.quit()
            sys.exit(0)
        except Exception as e:
            self._log(f'업데이터 실행 실패: {e}')


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == '__main__':
    main()
