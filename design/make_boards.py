# -*- coding: utf-8 -*-
"""Blog Compare 디자인 시안 아트보드 생성기.

레이아웃(위젯 배치·크기·순서)은 현재 gui.py 와 1:1 로 동일하게 한 번만 작성하고,
색·타이포·밀도 토큰만 갈아끼워 3개 시안(.dc.html)을 만든다.
→ 시안끼리 레이아웃이 어긋날 수 없다.

모든 시안은 tkinter/ttk 로 실제 구현 가능한 표현만 쓴다:
둥근 모서리·그림자·그라데이션 없음, 1px 테두리와 단색 채움과 글꼴 굵기만 사용.
"""
import io

W, H = 1050, 860

# 현재 화면의 실제 데이터 (사용자 스크린샷 기준)
IDS = ['cap5387', 'ckdkwjss', 'luxury2184', 'therefore6450', 'respective10893',
       'tkfldn1q', 'ekswldjss', 'rock12535', 'greet9136', 'thxogns1q',
       'emphasis14001', 'treat5716', 'zhdbfl9o', 'condition13664', 'trust14184',
       'rlacodk792', 'measure11065', 'cotton4903', 'predict5420']

KEYWORDS = ['해운대교정치과', '창원치과', '양산치과', '레포트 대필',
            '사회복지사2급자격증취득방법', '다정동수학학원', '레포트 대행',
            '다정동영어학원', '대구보청기', '수제담배', '대구치매', '대구신경과',
            '대구방아쇠수지증후군', '대구어깨통증', '서초동정형외과', '영도치과',
            '부산턱관절스플린트', '서면역임플란트', '문신제거후기']

# (키워드, [(순위, 제목, 블로그명, 아이디, 작성일)])
RESULTS = [
    ('수제담배', [
        ('10', '수제담배 창업, 합법적인 운영과 주의사항', '천방지축 뿌뿌', 'ban8813', '2026.09.08'),
        ('29', '수제담배 창업의 절차와 세무 기준', '좋은 하루의 시작', 'dlrkrh91837', '2026.08.18'),
    ]),
    ('대구어깨통증', [
        ('10', '대구어깨통증 병원 회전근개파열 원인과', '오늘보다 더 건강한', 'ceranz (rtyrty999)', '2026.09.08'),
    ]),
    ('창원사랑니', [
        ('23', '창원사랑니 발치 통증 대처와 주의사항', '나를 돌보는 시간', 'dngkgml25881', '2026.08.21'),
    ]),
    ('비절개 눈밑지', [
        ('28', '눈밑 지방 재배치, 칼을 대지 않는 방법', '소중한 내몸 지키기', 'treat5716', '2026.08.14'),
    ]),
    ('부산인비절라인', [
        ('10', '부산 인비절라인 교정과 전문의 진료 안내', '오늘도 가볍게', 'portion5561', '2026.08.20'),
    ]),
    ('대구두통', [
        ('19', '"어지럽고 메스꺼우신가요" 대구두통 에서', '대구 푸른청 신경과', 'welfare13347', '2026.09.11'),
    ]),
    ('입술필러유명한곳', [
        ('15', '입술필러유명한곳 선택 전 알아야 할 부분', '차민성형외과', 'feel2982', '2026.07.10'),
        ('16', '안전하게 선택하는 입술필러유명한곳 기준', '차민성형외과', 'feel2982', '2026.07.14'),
        ('25', '입술필러유명한곳 맞춤 시술 기준', '차민성형외과의원', 'dull153', '2026.06.02'),
    ]),
]

LOG_LINES = [
    ('[21:00:01] 조회 중... (86/88) "사상읶플란트"', 'dim'),
    ('[21:00:03] 조회 중... (87/88) "사상구치과"', 'dim'),
    ('[21:00:05] 조회 종료 — 13건', 'ok'),
]

# 표 열 너비 (gui.py specs 와 동일)
COLS = [('키워드', 100, 'left'), ('순위', 45, 'center'), ('제목', 200, 'left'),
        ('블로그명', 110, 'left'), ('아이디', 75, 'left'),
        ('작성일', 70, 'center'), ('링크', 75, 'center')]


def esc(s):
    return (s.replace('&', '&amp;').replace('<', '&lt;')
             .replace('>', '&gt;').replace('"', '&quot;'))


def board(t):
    """테마 dict 하나로 아트보드 한 장을 만든다."""
    mono = t['mono']
    ui = t['ui']
    rh = t['row_h']

    def lf(title, body_html, flex=None, height=None, pad=8):
        """tkinter LabelFrame 재현: 1px 테두리 + 좌상단에 겹쳐진 제목."""
        size = f'flex:{flex};min-height:0;' if flex else f'height:{height}px;flex:none;'
        return f'''<div style="position:relative;{size}border:1px solid {t['line']};background:{t['panel']};display:flex;flex-direction:column">
<div style="position:absolute;top:-7px;left:9px;padding:0 5px;background:{t['bg']};color:{t['label']};font-size:{t['label_size']}px;font-weight:700;letter-spacing:-0.2px">{title}</div>
<div style="flex:1;min-height:0;display:flex;flex-direction:column;padding:{pad}px 7px 7px">{body_html}</div>
</div>'''

    # ── 왼쪽: 모드 선택 ───────────────────────────────────────────────
    def radio(label, on, strong=False):
        dot = (f'<span style="width:11px;height:11px;border:1px solid {t["radio_line"]};'
               f'border-radius:50%;background:{t["panel"]};display:inline-flex;'
               f'align-items:center;justify-content:center;flex:none">'
               + (f'<span style="width:5px;height:5px;border-radius:50%;background:{t["accent"]}"></span>' if on else '')
               + '</span>')
        weight = 700 if (on and strong) else 400
        color = t['fg'] if on else t['fg_dim']
        return (f'<span style="display:flex;align-items:center;gap:5px;font-size:{ui}px;'
                f'font-weight:{weight};color:{color}">{dot}{label}</span>')

    mode_bar = f'''<div style="display:flex;align-items:center;gap:18px;padding:6px 11px;background:{t['mode_bg']};border:1px solid {t['mode_line']};flex:none">
{radio('순위 체크', True, True)}{radio('키워드 체크', False)}
</div>'''

    # ── 왼쪽: 비교대상 아이디 ─────────────────────────────────────────
    id_rows = ''.join(
        f'<div style="color:{t["link"]};line-height:{t["list_lh"]}px;font-size:{ui}px">{i}</div>'
        for i in IDS)
    id_body = f'''<div style="flex:1;min-height:0;display:flex;overflow:hidden">
<div style="flex:1;min-width:0;padding:3px 4px;overflow:hidden">{id_rows}</div>
{scrollbar(t, 0.62)}
</div>'''

    # ── 왼쪽: 검색 ────────────────────────────────────────────────────
    kw_rows = ''.join(
        f'<div style="color:{t["link"]};line-height:{t["list_lh"]}px;font-size:{ui}px">{k}</div>'
        for k in KEYWORDS)
    kw_box = f'''<div style="flex:1;min-height:0;display:flex;border:1px solid {t['input_line']};background:{t['panel']};overflow:hidden">
<div style="flex:1;min-width:0;padding:3px 5px;overflow:hidden">{kw_rows}</div>
{scrollbar(t, 0.5)}
</div>'''

    btn_search = f'''<div style="flex:none;background:{t['accent']};border:1px solid {t['accent_line']};color:{t['on_accent']};text-align:center;padding:{t['btn_pad']}px 0;font-size:{ui}px;font-weight:700;letter-spacing:1px">검색</div>'''

    type_row = f'''<div style="display:flex;gap:14px;flex:none">{radio('블로그', False)}{radio('신뢰도', True)}{radio('인기글', False)}</div>'''

    cnt_row = f'''<div style="display:flex;align-items:center;gap:7px;flex:none">
<span style="font-size:{ui}px;font-weight:700;color:{t['fg']}">신뢰도 조회수</span>
<span style="display:flex;align-items:stretch;border:1px solid {t['input_line']};background:{t['panel']}">
  <span style="padding:2px 9px 2px 7px;font-size:{ui}px;font-family:{mono};color:{t['fg']}">30</span>
  <span style="display:flex;flex-direction:column;border-left:1px solid {t['input_line']};width:14px">
    <span style="flex:1;border-bottom:1px solid {t['input_line']};background:{t['spin_bg']}"></span>
    <span style="flex:1;background:{t['spin_bg']}"></span>
  </span>
</span>
<span style="flex:1"></span>
<span style="background:{t['btn2_bg']};border:1px solid {t['btn2_line']};color:{t['btn2_fg']};padding:3px 9px;font-size:{ui - 1}px;font-weight:{t['btn2_weight']}">아이디&amp;설정 저장</span>
</div>'''

    progress = f'''<div style="flex:none;height:{t['bar_h']}px;background:{t['bar_track']};border:1px solid {t['bar_line']};overflow:hidden">
<div style="width:64%;height:100%;background:{t['bar_fill']}"></div>
</div>'''

    search_body = (kw_box
                   + f'<div style="height:7px;flex:none"></div>' + btn_search
                   + f'<div style="height:7px;flex:none"></div>' + type_row
                   + f'<div style="height:7px;flex:none"></div>' + cnt_row
                   + f'<div style="height:7px;flex:none"></div>' + progress)

    left = f'''<div style="width:316px;flex:none;display:flex;flex-direction:column;gap:10px">
{mode_bar}
{lf(f'★ 비교대상 아이디 입력 <span style="font-weight:400;color:{t['fg_dim']}">(최종 저장: 2026-09-10 09:28)</span>', id_body, flex=1)}
{lf('★ 검색', search_body, flex='1.45')}
</div>'''

    # ── 오른쪽: 결과 표 ───────────────────────────────────────────────
    head = ''.join(
        f'<div style="width:{w}px;flex:none;text-align:{a};padding:0 7px;'
        f'box-sizing:border-box;color:{t["head_fg"]};font-weight:700;font-size:{ui}px;'
        f'{t["head_cell_extra"]}">{c}</div>'
        for c, w, a in COLS)
    thead = f'''<div style="display:flex;align-items:center;height:{t['head_h']}px;flex:none;background:{t['head_bg']};{t['head_extra']}">{head}</div>'''

    body_rows, zebra = [], 0
    for kw, items in RESULTS:
        body_rows.append(
            f'<div style="display:flex;align-items:center;height:{rh}px;flex:none;'
            f'background:{t["sep_bg"]};{t["sep_extra"]}">'
            f'<div style="padding:0 7px;color:{t["sep_fg"]};font-weight:700;font-size:{ui}px">[{kw}]</div></div>')
        zebra = 0
        for rank, title, blog, bid, date in items:
            zebra += 1
            bg = t['row_alt'] if (t['zebra'] and zebra % 2 == 0) else t['panel']
            # 목표 순위 안(포함)에 들면 행 전체를 초록으로.
            # tkinter(Tk 8.6)는 칸 단위 색을 지원하지 않아 순위 칸만 칠할 수 없다.
            hit = t['hit_limit'] is not None and int(rank) <= t['hit_limit']
            hw = 700 if hit else None
            hc = t['ok'] if hit else None
            cells = [
                (kw, 100, 'left', hc or t['fg'], hw or 400, ui, 'inherit'),
                (rank, 45, 'center', hc or t['rank_fg'], 700, t['rank_size'], mono),
                (title, 200, 'left', hc or t['title_fg'], hw or t['title_weight'], ui, 'inherit'),
                (blog, 110, 'left', hc or t['fg_dim'], hw or 400, ui, 'inherit'),
                (bid, 75, 'left', hc or t['fg'], hw or 400, ui, 'inherit'),
                (date, 70, 'center', hc or t['fg_dim'], hw or 400, ui - 2, mono),
                ('https://blog.', 75, 'center', hc or t['link'], hw or 400, ui - 1, 'inherit'),
            ]
            tds = ''.join(
                f'<div style="width:{w}px;flex:none;padding:0 {3 if w <= 75 else 7}px;box-sizing:border-box;'
                f'text-align:{a};color:{col};font-weight:{fw};font-size:{fs}px;font-family:{ff};'
                f'white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{esc(v)}</div>'
                for v, w, a, col, fw, fs, ff in cells)
            body_rows.append(
                f'<div style="display:flex;align-items:center;height:{rh}px;flex:none;'
                f'background:{bg};{t["row_extra"]}">{tds}</div>')

    table = f'''<div style="flex:1;min-height:0;display:flex;border:1px solid {t['line']};background:{t['panel']};overflow:hidden">
<div style="flex:1;min-width:0;display:flex;flex-direction:column;overflow:hidden">{thead}{''.join(body_rows)}</div>
{scrollbar(t, 0.85)}
</div>'''

    log_body = f'''<div style="flex:1;min-height:0;display:flex;overflow:hidden">
<div style="flex:1;min-width:0;padding:3px 4px;overflow:hidden">''' + ''.join(
        f'<div style="line-height:17px;font-size:{ui}px;font-family:{mono};'
        f'color:{t["ok"] if k == "ok" else t["fg_dim"]}">{esc(s)}</div>'
        for s, k in LOG_LINES) + f'''</div>{scrollbar(t, 0.9)}</div>'''

    right = f'''<div style="flex:1;min-width:0;display:flex;flex-direction:column;gap:10px">
{table}
{lf('로그', log_body, height=96, pad=7)}
</div>'''

    titlebar = f'''<div style="height:30px;flex:none;display:flex;align-items:center;padding:0 12px;background:{t['chrome_bg']};border-bottom:1px solid {t['chrome_line']};gap:8px">
<span style="font-size:{ui}px;color:{t['chrome_fg']}">Blog Compare v1.1.05</span>
<span style="flex:1"></span>
<span style="width:11px;height:1px;background:{t['chrome_fg']}"></span>
<span style="width:9px;height:9px;border:1px solid {t['chrome_fg']};margin:0 10px"></span>
<span style="font-size:13px;color:{t['chrome_fg']};line-height:1">&#10005;</span>
</div>'''

    return f'''<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <script src="./support.js"></script>
</head>
<body>
<x-dc>
<helmet>
  <style>
    body {{ margin: 0; }}
    a {{ color: {t['link']}; }} a:hover {{ color: {t['accent']}; }}
  </style>
</helmet>
<div style="width:{W}px;height:{H}px;box-sizing:border-box;background:{t['bg']};font-family:{t['font']};font-size:{ui}px;color:{t['fg']};display:flex;flex-direction:column;overflow:hidden">
{titlebar}
<div style="flex:1;min-height:0;display:flex;gap:10px;padding:11px 10px 10px">
{left}
{right}
</div>
</div>
</x-dc>
</body>
</html>
'''


def scrollbar(t, pos):
    """ttk 스크롤바 재현 — 폭 12px, 사각 손잡이."""
    return (f'<div style="width:12px;flex:none;background:{t["sb_track"]};'
            f'border-left:1px solid {t["sb_line"]};display:flex;flex-direction:column">'
            f'<div style="flex:{pos}"></div>'
            f'<div style="height:52px;flex:none;background:{t["sb_thumb"]};margin:0 2px"></div>'
            f'<div style="flex:{1 - pos if 1 - pos > 0 else 0.05}"></div></div>')


KOR = "'Malgun Gothic','맑은 고딕','Apple SD Gothic Neo',sans-serif"
MONO_WIN = "'Consolas','D2Coding',ui-monospace,monospace"

# ── 시안 A: 정갈한 사무용 ────────────────────────────────────────────
A = dict(
    font=KOR, mono=MONO_WIN, ui=12, label_size=12, list_lh=17,
    bg='#EFF1F4', panel='#FFFFFF', line='#D4D9DF', input_line='#C9D0D8',
    fg='#1B1F24', fg_dim='#6B747E', label='#2C3440', link='#1D4ED8',
    accent='#14406E', accent_line='#0E3157', on_accent='#FFFFFF',
    radio_line='#94A3B0',
    mode_bg='#DCE7F5', mode_line='#9FBCE0',
    head_bg='#E7EBF0', head_fg='#39424E', head_h=28,
    head_extra=f'border-bottom:1px solid #C9D0D8', head_cell_extra='',
    sep_bg='#DBE1E8', sep_fg='#2C3440', sep_extra='',
    row_h=26, row_alt='#F7F8FA', zebra=False, row_extra='border-bottom:1px solid #EDEFF2',
    rank_fg='#14406E', rank_size=12, title_fg='#1B1F24', title_weight=400,
    btn_pad=5, btn2_bg='#8A929B', btn2_line='#767E87', btn2_fg='#FFFFFF', btn2_weight=400,
    spin_bg='#EEF1F4',
    bar_h=12, bar_track='#E3E7EC', bar_line='#CFD6DE', bar_fill='#2F6FB5',
    sb_track='#F0F2F5', sb_line='#DDE2E8', sb_thumb='#BFC7D0',
    ok='#15803D', hit_limit=None,
    chrome_bg='#F3F4F6', chrome_line='#D4D9DF', chrome_fg='#4B5563',
)

# ── 시안 B: 다크 콘솔 ────────────────────────────────────────────────
B = dict(
    font=KOR, mono=MONO_WIN, ui=12, label_size=12, list_lh=17,
    bg='#191D23', panel='#22272E', line='#343B44', input_line='#3C444E',
    fg='#E4E9EF', fg_dim='#93A0AE', label='#B7C3D0', link='#6FB2FF',
    accent='#2F6FB5', accent_line='#1F4E80', on_accent='#FFFFFF',
    radio_line='#5A6673',
    mode_bg='#233246', mode_line='#33506F',
    head_bg='#2B323A', head_fg='#AEBACA', head_h=28,
    head_extra='border-bottom:1px solid #3C444E', head_cell_extra='',
    sep_bg='#2E3742', sep_fg='#D8E2EE', sep_extra='',
    row_h=26, row_alt='#262C34', zebra=True, row_extra='',
    rank_fg='#FFC453', rank_size=13, title_fg='#E4E9EF', title_weight=400,
    btn_pad=5, btn2_bg='#39424D', btn2_line='#4A5561', btn2_fg='#D2DAE3', btn2_weight=400,
    spin_bg='#2B323A',
    bar_h=12, bar_track='#2B323A', bar_line='#3C444E', bar_fill='#4FA3FF',
    sb_track='#1F242B', sb_line='#343B44', sb_thumb='#495260',
    ok='#5BD08A', hit_limit=None,
    chrome_bg='#15191E', chrome_line='#343B44', chrome_fg='#8C97A3',
)

# ── 시안 C: 고밀도 편집형 ────────────────────────────────────────────
C = dict(
    font=KOR, mono=MONO_WIN, ui=12, label_size=11, list_lh=15,
    bg='#FFFFFF', panel='#FFFFFF', line='#C8CDD4', input_line='#C8CDD4',
    fg='#111418', fg_dim='#5B646E', label='#17224D', link='#17224D',
    accent='#17224D', accent_line='#17224D', on_accent='#FFFFFF',
    radio_line='#8A929B',
    mode_bg='#FFFFFF', mode_line='#17224D',
    head_bg='#FFFFFF', head_fg='#17224D', head_h=26,
    head_extra='border-bottom:2px solid #17224D', head_cell_extra='letter-spacing:0.4px',
    sep_bg='#FFFFFF', sep_fg='#C2410C',
    sep_extra='border-top:1px solid #17224D;border-bottom:1px solid #E3E6EA',
    row_h=22, row_alt='#F5F6F8', zebra=True, row_extra='',
    rank_fg='#111418', rank_size=13, title_fg='#111418', title_weight=500,
    btn_pad=4, btn2_bg='#FFFFFF', btn2_line='#17224D', btn2_fg='#17224D', btn2_weight=700,
    spin_bg='#F1F3F5',
    bar_h=10, bar_track='#EDEFF2', bar_line='#C8CDD4', bar_fill='#C2410C',
    sb_track='#FFFFFF', sb_line='#E3E6EA', sb_thumb='#C8CDD4',
    ok='#15803D', hit_limit=20,
    chrome_bg='#FFFFFF', chrome_line='#C8CDD4', chrome_fg='#5B646E',
)

for name, theme in (('Main', C), ('DirectionA', A), ('DirectionB', B)):
    path = f'{name}.dc.html'
    io.open(path, 'w', encoding='utf-8').write(board(theme))
    print('wrote', path)
