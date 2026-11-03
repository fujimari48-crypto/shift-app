from flask import Flask, request, jsonify, render_template_string, session, redirect, url_for
import pandas as pd
from datetime import datetime, timedelta
import calendar
import os
import json
import threading
import time
import gspread
from google.oauth2.service_account import Credentials

app = Flask(__name__)
app.secret_key = 'shift_app_secret_key'

SHOP_NAME = "久留米焼鳥 つかさ"
SHORT_NAME = "つかさ"
DATA_FILE = 'shift_data.json'
SPREADSHEET_NAME = 'shift_database'
SCOPES = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']

try:
    if 'GOOGLE_CREDENTIALS_JSON' in os.environ:
        creds_dict = json.loads(os.environ['GOOGLE_CREDENTIALS_JSON'])
        creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    else:
        creds = Credentials.from_service_account_file('credentials.json', scopes=SCOPES)

    gc = gspread.authorize(creds)
    sheet = gc.open(SPREADSHEET_NAME)
    print("✅ Google Sheets に正常に接続しました")
except Exception as e:
    print("⚠️ Google Sheets の接続に失敗しました:", e)
    sheet = None

def get_ws(title, cols=10):
    if sheet is None: return None
    try:
        return sheet.worksheet(title)
    except gspread.exceptions.WorksheetNotFound:
        return sheet.add_worksheet(title=title, rows=1000, cols=cols)

default_data = {
    'staffs_db': {
        '翔太': {'password': '0000', 'hourly_wage': 1100, 'default_position': 'K (キッチン)'},
        '健太': {'password': '0000', 'hourly_wage': 1100, 'default_position': 'K (キッチン)'},
        '大地': {'password': '0000', 'hourly_wage': 1100, 'default_position': 'K (キッチン)'},
        '悠人': {'password': '0000', 'hourly_wage': 1100, 'default_position': 'K (キッチン)'},
        '美咲': {'password': '0000', 'hourly_wage': 1100, 'default_position': 'F (フロント)'},
        '葵': {'password': '0000', 'hourly_wage': 1100, 'default_position': 'F (フロント)'},
        '凛': {'password': '0000', 'hourly_wage': 1100, 'default_position': 'F (フロント)'},
        '結衣': {'password': '0000', 'hourly_wage': 1100, 'default_position': 'F (フロント)'},
        '蓮': {'password': '0000', 'hourly_wage': 1200, 'default_position': 'Y (やきば)'},
        '樹': {'password': '0000', 'hourly_wage': 1200, 'default_position': 'Y (やきば)'},
        '湊': {'password': '0000', 'hourly_wage': 1200, 'default_position': 'Y (やきば)'},
        '陽翔': {'password': '0000', 'hourly_wage': 1200, 'default_position': 'Y (やきば)'}
    },
    'required_settings': {
        '月': {'K (キッチン)': 2, 'F (フロント)': 2, 'Y (やきば)': 1},
        '火': {'K (キッチン)': 2, 'F (フロント)': 2, 'Y (やきば)': 1},
        '水': {'K (キッチン)': 2, 'F (フロント)': 2, 'Y (やきば)': 1},
        '木': {'K (キッチン)': 2, 'F (フロント)': 2, 'Y (やきば)': 1},
        '金': {'K (キッチン)': 3, 'F (フロント)': 3, 'Y (やきば)': 2},
        '土': {'K (キッチン)': 3, 'F (フロント)': 3, 'Y (やきば)': 2},
        '日': {'K (キッチン)': 3, 'F (フロント)': 3, 'Y (やきば)': 2}
    },
    'deadline_settings': {'deadline': '2026-10-31 23:59'},
    'wishes_data': [],
    'shifts_data': [],
    'published_shifts_data': [],
    'actual_shifts_data': [],
    'admin_credentials': {'password': '1234'}
}

def load_data():
    if sheet:
        try:
            ws_staff = get_ws("Staff")
            staff_recs = ws_staff.get_all_records() if ws_staff else []
            loaded_staffs = {str(r['name']): {'password': str(r['password']), 'hourly_wage': int(r['wage']), 'default_position': str(r['position'])} for r in staff_recs} if staff_recs else default_data['staffs_db']

            ws_settings = get_ws("Settings")
            set_recs = ws_settings.get_all_records() if ws_settings else []
            if set_recs:
                set_dict = {str(r['key']): str(r['value']) for r in set_recs}
                req_settings = json.loads(set_dict.get('required_settings', json.dumps(default_data['required_settings'])))
                deadline_settings = {'deadline': set_dict.get('deadline', default_data['deadline_settings']['deadline'])}
                admin_credentials = {'password': set_dict.get('admin_password', default_data['admin_credentials']['password'])}
            else:
                req_settings, deadline_settings, admin_credentials = default_data['required_settings'], default_data['deadline_settings'], default_data['admin_credentials']

            wishes_data = get_ws("Wishes").get_all_records()
            shifts_data = get_ws("Shifts").get_all_records()
            published_shifts_data = get_ws("Published").get_all_records()
            actual_shifts_data = get_ws("Actual").get_all_records()

            for data_list in [wishes_data, shifts_data, published_shifts_data, actual_shifts_data]:
                for d in data_list: d.update((k, str(v)) for k, v in d.items())

            return {'staffs_db': loaded_staffs, 'required_settings': req_settings, 'deadline_settings': deadline_settings, 'wishes_data': wishes_data, 'shifts_data': shifts_data, 'published_shifts_data': published_shifts_data, 'actual_shifts_data': actual_shifts_data, 'admin_credentials': admin_credentials}
        except Exception as e:
            print("⚠️ スプレッドシート読みエラー:", e)

    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f: return json.load(f)
        except: pass
    return default_data

save_timer = None

def save_all_to_sheets_task(data_copy):
    if sheet is None: return
    try:
        def update_ws(title, rows):
            ws = get_ws(title)
            ws.clear()
            if rows: ws.update(values=rows, range_name='A1')

        staff_rows = [['name', 'password', 'wage', 'position']] + [[name, info['password'], info['hourly_wage'], info['default_position']] for name, info in data_copy['staffs_db'].items()]
        update_ws("Staff", staff_rows)

        set_rows = [['key', 'value'], ['required_settings', json.dumps(data_copy['required_settings'], ensure_ascii=False)], ['deadline', data_copy['deadline_settings']['deadline']], ['admin_password', data_copy['admin_credentials']['password']]]
        update_ws("Settings", set_rows)

        def build_rows(headers, data_list): return [headers] + [[d.get(h, '') for h in headers] for d in data_list]
        update_ws("Wishes", build_rows(['staff_name', 'date', 'shift', 'time'], data_copy['wishes_data']))
        update_ws("Shifts", build_rows(['staff_name', 'date', 'shift', 'time'], data_copy['shifts_data']))
        update_ws("Published", build_rows(['staff_name', 'date', 'shift', 'time'], data_copy['published_shifts_data']))
        update_ws("Actual", build_rows(['staff_name', 'date', 'status', 'time'], data_copy['actual_shifts_data']))
    except Exception as e:
        print("⚠️ 保存エラー:", e)

def trigger_save(data_copy):
    global save_timer
    if save_timer is not None: save_timer.cancel()
    save_timer = threading.Timer(3.0, save_all_to_sheets_task, args=(data_copy,))
    save_timer.start()

def save_data():
    data = {'staffs_db': staffs_db, 'required_settings': required_settings, 'deadline_settings': deadline_settings, 'wishes_data': wishes_data, 'shifts_data': shifts_data, 'published_shifts_data': published_shifts_data, 'actual_shifts_data': actual_shifts_data, 'admin_credentials': admin_credentials}
    try:
        with open(DATA_FILE, 'w', encoding='utf-8') as f: json.dump(data, f, ensure_ascii=False, indent=4)
    except: pass
    trigger_save(data)

app_data = load_data()
staffs_db, required_settings, deadline_settings = app_data['staffs_db'], app_data['required_settings'], app_data['deadline_settings']
wishes_data, shifts_data, published_shifts_data = app_data['wishes_data'], app_data['shifts_data'], app_data['published_shifts_data']
actual_shifts_data, admin_credentials = app_data['actual_shifts_data'], app_data['admin_credentials']

POSITIONS = ['K (キッチン)', 'F (フロント)', 'Y (やきば)']
TIME_OPTIONS = [f"{h:02d}:{m:02d}" for h in range(9, 25) for m in [0, 15, 30, 45] if not (h == 24 and m > 0)]

def format_time_display(time_str):
    if not time_str or time_str == '-': return '時間指定なし'
    if '-' in time_str:
        parts = time_str.split('-')
        start, end = parts[0], parts[1] if len(parts) > 1 else ''
        if start and end: return f"{start}〜{end}"
        elif start: return f"{start}〜"
        elif end: return f"〜{end}"
    return time_str

def get_month_weeks(year, month):
    cal = calendar.Calendar(firstweekday=0)
    return [[{'full_date': dt.strftime('%Y-%m-%d'), 'date_str': f"{dt.month}/{dt.day}", 'weekday': ['月', '火', '水', '木', '金', '土', '日'][dt.weekday()], 'is_weekend': dt.weekday() >= 5, 'in_month': dt.month == month} for dt in week] for week in cal.monthdatescalendar(year, month)]

def get_month_days(year, month):
    _, last_day = calendar.monthrange(year, month)
    days = [{'full_date': datetime(year, month, day).strftime('%Y-%m-%d'), 'date_str': f"{month}/{day}", 'weekday': ['月', '火', '水', '木', '金', '土', '日'][datetime(year, month, day).weekday()], 'is_weekend': datetime(year, month, day).weekday() >= 5} for day in range(1, last_day + 1)]
    return days[:15], days[15:], f"{year}年{month}月"

# ==========================================
# テンプレート (HTML)
# ==========================================
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ shop_name }} シフト管理</title>
    <style>
        body { font-family: 'Noto Sans JP', sans-serif; margin: 0; background-color: #121212; color: #E0E0E0; display: flex; flex-direction: column; height: 100vh; }
        header { background-color: #1E1E1E; padding: 10px 20px; display: flex; justify-content: space-between; align-items: center; border-bottom: 2px solid #D4AF37; position: fixed; top: 0; left: 0; width: 100%; z-index: 1000; box-sizing: border-box;}
        header h1 { display: flex; align-items: center; gap: 10px; margin: 0; font-size: 20px; color: #D4AF37; }
        .container { display: flex; gap: 15px; padding: 75px 15px 15px 15px; flex-wrap: wrap; margin-bottom: 20px; flex: 1; }
        aside { width: 180px; background-color: #1E1E1E; padding: 15px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.5); }
        aside h2 { font-size: 15px; color: #D4AF37; border-bottom: 1px solid #D4AF37; padding-bottom: 8px; margin-top: 0; }
        aside ul { list-style: none; padding: 0; margin: 10px 0 0 0; }
        aside ul li { margin-bottom: 8px; }
        aside ul li a { text-decoration: none; color: #E0E0E0; font-size: 13px; display: block; padding: 4px; border-radius: 4px; }
        aside ul li a.active { background-color: #D4AF37; color: #121212; font-weight: bold; }
        main { flex: 1; padding: 0 10px 10px 10px; min-width: 0; }
        .card { background: #1E1E1E; padding: 15px; border-radius: 8px; margin-bottom: 15px; border-left: 5px solid #D4AF37; }
        .card h3 { color: #D4AF37; font-size: 16px; margin-top: 0; border-bottom: 1px solid #333; padding-bottom: 8px; }
        form { display: flex; flex-direction: column; gap: 8px; }
        input, select, button { padding: 6px; border: 1px solid #333; border-radius: 4px; font-size: 12px; background: #262626; color: #E0E0E0; }
        button { background-color: #D4AF37; color: #121212; border: none; cursor: pointer; font-weight: bold; }
        .btn-green { background-color: #2E7D32; color: #FFFFFF; }
        .btn-danger { background-color: #D32F2F; color: #FFFFFF; }
        .table-responsive { overflow-x: auto; background: #1E1E1E; padding: 15px; border-radius: 8px; margin-bottom: 15px; max-height: 550px; }
        table { width: 100%; border-collapse: collapse; margin-top: 5px; white-space: nowrap; font-size: 12px; color: #E0E0E0; }
        th, td { border: 1px solid #333; padding: 5px 6px; text-align: center; background: #1E1E1E; }
        th { background-color: #262626; color: #D4AF37; position: sticky; top: 0; }
        .weekend { background-color: #261111 !important; }
        .staff-grid { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; max-height: 150px; overflow-y: auto; }
        .staff-chip { background: #262626; border: 1px solid #333; padding: 4px 8px; border-radius: 4px; display: flex; align-items: center; gap: 6px; font-size: 12px; }
        .staff-name-btn { background: none; border: none; padding: 0; color: #E0E0E0; font-weight: bold; cursor: pointer; text-decoration: underline; }
        .wish-box { background: #1A1A1A; padding: 12px; border-radius: 8px; margin-bottom: 20px; border-left: 5px solid #2E7D32; color: #E0E0E0; }
        .admin-tab-menu { display: flex; gap: 6px; margin-bottom: 12px; }
        .admin-tab-btn { padding: 8px 16px; background: #1E1E1E; border: 1px solid #333; border-radius: 4px; font-weight: bold; cursor: pointer; font-size: 12px; color: #E0E0E0; }
        .admin-tab-btn.active { background: #D4AF37; color: #121212; border-color: #D4AF37; }
        .admin-tab-content { display: none; }
        .admin-tab-content.active { display: block; }
    </style>
</head>
<body>
    <header>
        <h1><strong>{{ shop_name }}</strong> シフト管理</h1>
        <div style="display: flex; gap: 15px; align-items: center;">
            <p style="margin: 0; font-size: 13px; color: #FFA726;">📊 シフト管理 ({{ month_title }})</p>
            <a href="/logout"><button class="btn-danger" style="padding: 5px 10px; font-size: 11px;">ログアウト</button></a>
        </div>
    </header>

    {% if not is_admin %}
        <div class="card" style="max-width: 350px; margin: 100px auto; padding: 25px;">
            <h2 style="text-align: center; color: #D4AF37; margin-top: 0;">🔒 管理者ログイン</h2>
            <form method="POST" action="/admin_login">
                <input type="password" name="password" placeholder="パスワード" required style="padding: 8px;">
                <button type="submit" style="padding: 8px;">ログイン</button>
            </form>
            {% if login_error %}<div style="color:#FFA726; font-size:11px; margin-top:8px;">パスワードが間違っています。</div>{% endif %}
        </div>
    {% else %}
        <div class="container">
            <aside class="no-print">
                <h2>🖥️ 管理メニュー</h2>
                <ul>
                    <li><a href="/" class="active">📊 シフト作成</a></li>
                    <li><a href="/admin/settings">⚙️ 設定・パス変更</a></li>
                </ul>
                <button onclick="window.customActions.generateAutoShift()" style="width: 100%; margin-top: 12px; background: #8e44ad; padding: 8px; font-size: 12px; color: white;">🤖 自動シフト作成</button>
                <button class="btn-green" onclick="window.customActions.publishShifts()" style="width: 100%; margin-top: 8px; padding: 8px; font-size: 12px; background: #27ae60;">📢 シフトを確定・公開</button>
                <button onclick="window.customActions.syncFromSheets()" style="width: 100%; margin-top: 15px; background: #3498db; padding: 8px; font-size: 12px; color: white;">🔄 シートからデータ同期</button>
            </aside>

            <main>
                <div class="no-print" style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
                    <form method="GET" action="/" style="flex-direction: row; gap: 4px;">
                        <select name="year" onchange="this.form.submit()">
                            <option value="2025" {% if current_year == 2025 %}selected{% endif %}>2025年</option>
                            <option value="2026" {% if current_year == 2026 %}selected{% endif %}>2026年</option>
                            <option value="2027" {% if current_year == 2027 %}selected{% endif %}>2027年</option>
                        </select>
                        <select name="month" onchange="this.form.submit()">
                            {% for m in range(1, 13) %}<option value="{{ m }}" {% if current_month == m %}selected{% endif %}>{{ m }}月</option>{% endfor %}
                        </select>
                        <select name="week_idx" onchange="this.form.submit()">
                            <option value="all" {% if selected_week_idx == 'all' %}selected{% endif %}>📅 全体表示</option>
                            {% for w_idx in range(weeks|length) %}<option value="{{ w_idx }}" {% if selected_week_idx|string == w_idx|string %}selected{% endif %}>第{{ w_idx+1 }}週</option>{% endfor %}
                        </select>
                    </form>
                </div>

                <div class="admin-tab-menu no-print">
                    <button type="button" class="admin-tab-btn active" onclick="window.customActions.switchAdminTab('tab-shift-create', this)">📊 シフト作成・管理</button>
                    <button type="button" class="admin-tab-btn" onclick="window.customActions.switchAdminTab('tab-actual-work', this)">💰 勤怠実績・給料一覧</button>
                </div>

                <div id="tab-shift-create" class="admin-tab-content active">
                    <div class="no-print" style="display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 12px;">
                        <div class="card" style="flex: 1; min-width: 260px; margin-bottom: 0;">
                            <h3>バイトスタッフの管理</h3>
                            <form onsubmit="window.customActions.submitAddStaff(event)" style="flex-direction: row; gap: 4px;">
                                <input type="text" id="new_staff_name" placeholder="スタッフ名" required style="flex: 1;">
                                <input type="number" id="new_staff_wage" placeholder="時給" value="1100" min="0" required style="width: 50px;">
                                <select id="new_staff_pos" style="flex: 1;">
                                    <option value="">基本なし</option><option value="K (キッチン)">キッチン</option><option value="F (フロント)">フロント</option><option value="Y (やきば)">やきば</option>
                                </select>
                                <button type="submit" class="btn-green" style="padding: 5px 8px;">追加</button>
                            </form>
                            <div class="staff-grid">
                                {% for staff, info in staffs.items() %}
                                <div class="staff-chip">
                                    <button type="button" class="staff-name-btn" onclick="window.customActions.openPassModal('{{ staff }}', {{ info.hourly_wage }}, '{{ info.default_position }}')">
                                        🔑 {{ staff }} <span style="font-size: 9px; color: #D4AF37;">[{{ info.default_position.split(' ')[0] if info.default_position else '無' }}]</span>
                                    </button>
                                    <button onclick="window.customActions.deleteStaff('{{ staff }}')" class="btn-danger" style="padding: 1px 4px; font-size: 9px;">削除</button>
                                </div>
                                {% endfor %}
                            </div>
                        </div>

                        <div class="card" style="flex: 1; min-width: 260px; margin-bottom: 0;">
                            <h3>提出期限 & 人数設定</h3>
                            <form onsubmit="window.customActions.submitDeadline(event)" style="flex-direction: row; gap: 4px; align-items: center; margin-bottom: 8px;">
                                <label style="font-size: 10px;">締切:</label>
                                <input type="datetime-local" id="deadline_input" value="{{ deadline_val }}" style="flex: 1; font-size: 10px; padding: 4px;">
                                <button type="submit" style="padding: 4px 6px; font-size: 10px;">保存</button>
                            </form>
                            <form onsubmit="window.customActions.submitReq(event)">
                                <select id="req_day" style="font-size: 11px; padding: 4px;">
                                    <option value="月">月</option><option value="火">火</option><option value="水">水</option><option value="木">木</option><option value="金">金</option><option value="土">土</option><option value="日">日</option>
                                </select>
                                <div style="display: flex; gap: 3px;">
                                    <input type="number" id="req_k" placeholder="K" min="0" required style="width: 100%; padding: 4px;">
                                    <input type="number" id="req_f" placeholder="F" min="0" required style="width: 100%; padding: 4px;">
                                    <input type="number" id="req_y" placeholder="Y" min="0" required style="width: 100%; padding: 4px;">
                                </div>
                                <button type="submit" style="padding: 5px; font-size: 11px;">人数保存</button>
                            </form>
                        </div>
                    </div>

                    <div id="passModal" style="display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.8); z-index: 9999; justify-content: center; align-items: center;">
                        <div class="card" style="width: 280px;">
                            <h3>🔑 スタッフ情報変更</h3>
                            <p id="modalStaffLabel" style="font-size: 12px; color: #FFA726; margin-bottom: 8px;"></p>
                            <label style="font-size: 11px; display: block;">基本ポジション:</label>
                            <select id="modalNewPos" style="width: 100%; margin-bottom: 6px;">
                                <option value="">設定なし</option><option value="K (キッチン)">キッチン</option><option value="F (フロント)">フロント</option><option value="Y (やきば)">やきば</option>
                            </select>
                            <label style="font-size: 11px; display: block;">時給 (円):</label>
                            <input type="number" id="modalNewWage" style="width: 100%; margin-bottom: 6px;" min="0">
                            <label style="font-size: 11px; display: block;">新しいパスワード:</label>
                            <input type="text" id="modalNewPass" placeholder="未入力ならそのまま" style="width: 100%; margin-bottom: 12px;">
                            <div style="display: flex; gap: 8px; justify-content: flex-end;">
                                <button type="button" onclick="window.customActions.closePassModal()" style="background: #95a5a6; padding: 5px 10px;">キャンセル</button>
                                <button type="button" onclick="window.customActions.submitPassModal()" style="padding: 5px 10px;">保存</button>
                            </div>
                        </div>
                    </div>

                    <div class="wish-box no-print">
                        <div style="display: flex; justify-content: space-between; align-items: center;">
                            <h3 style="margin: 0; color: #2E7D32; font-size: 15px;">📥 スタッフ希望提出一覧</h3>
                            <button onclick="window.customActions.applyAllWishes()" class="btn-green" style="padding: 5px 10px; font-size: 11px;">📥 全希望を一括反映</button>
                        </div>
                        <div style="max-height: 150px; overflow-y: auto;">
                            <table class="wish-table" style="width:100%; font-size:11px; margin-top:8px;">
                                <tbody>
                                    {% for staff in staffs.keys() %}
                                    <tr>
                                        <td style="padding:5px; border:1px solid #333;"><strong>{{ staff }}</strong></td>
                                        <td style="text-align: left; padding-left: 8px; border:1px solid #333;">
                                            {% set staff_has_wish = false %}
                                            {% for w in wishes %}
                                                {% if w.staff_name == staff %}
                                                    {% set staff_has_wish = true %}
                                                    <span style="display: inline-block; background: #261111; padding: 2px 5px; margin: 2px; border-radius: 4px; border: 1px solid #333; color: #FFA726;">
                                                        {{ w.date.split('-')[1]|int }}/{{ w.date.split('-')[2]|int }}: <strong>{% if w.shift == '希望休' %}休{% else %}出勤{% endif %}</strong> 
                                                        {% if w.time and w.time != '-' %}({{ w.time }}){% endif %}
                                                        <button onclick="window.customActions.applyOneWish('{{ w.staff_name }}', '{{ w.date }}', '{{ w.shift }}', '{{ w.time }}')" style="padding: 1px 4px; font-size: 9px; background: #D4AF37; color: #121212; border-radius: 3px; cursor: pointer; margin-left: 3px;">反映</button>
                                                    </span>
                                                {% endif %}
                                            {% endfor %}
                                            {% if not staff_has_wish %}<span style="color: #666;">（未提出）</span>{% endif %}
                                        </td>
                                    </tr>
                                    {% endfor %}
                                </tbody>
                            </table>
                        </div>
                    </div>

                    <!-- ★ ここからシフト表マクロ -->
                    {% macro render_table(days_list) %}
                    <div class="table-responsive">
                        <table>
                            <thead>
                                <tr>
                                    <th>名前</th>
                                    {% for day_info in days_list %}
                                    <th class="{% if day_info.is_weekend %}weekend{% endif %}">{{ day_info.date_str }}<br><small>({{ day_info.weekday }})</small></th>
                                    {% endfor %}
                                </tr>
                            </thead>
                            <tbody>
                                {% for staff, s_info in staffs.items() %}
                                <tr>
                                    <td>{{ staff }}<br><small style="color: #888;">({{ s_info.default_position.split(' ')[0] if s_info.default_position else '無' }})</small></td>
                                    {% for day_info in days_list %}
                                    <td>
                                        {% set current_shift = namespace(val='-', start='', end='') %}
                                        {% for s in shifts %}
                                            {% if s.staff_name == staff and s.date == day_info.full_date %}
                                                {% set current_shift.val = s.shift %}
                                                {% if '-' in s.time %}
                                                    {% set parts = s.time.split('-') %}
                                                    {% set current_shift.start = parts[0] %}
                                                    {% set current_shift.end = parts[1] if parts|length > 1 else '' %}
                                                {% endif %}
                                            {% endif %}
                                        {% endfor %}
                                        <select id="shift_{{ staff }}_{{ day_info.full_date }}" onchange="window.customActions.triggerUpdate('{{ staff }}', '{{ day_info.full_date }}')" style="padding: 3px; font-size: 11px;">
                                            <option value="-" {% if current_shift.val == '-' %}selected{% endif %}>-</option>
                                            <option value="K (キッチン)" {% if current_shift.val == 'K (キッチン)' %}selected{% endif %}>K</option>
                                            <option value="F (フロント)" {% if current_shift.val == 'F (フロント)' %}selected{% endif %}>F</option>
                                            <option value="Y (やきば)" {% if current_shift.val == 'Y (やきば)' %}selected{% endif %}>Y</option>
                                            <option value="希望休" {% if current_shift.val == '希望休' %}selected{% endif %}>休</option>
                                        </select>
                                        <div style="margin-top: 2px; display: flex; gap: 1px; justify-content: center; align-items: center;">
                                            <select id="start_{{ staff }}_{{ day_info.full_date }}" onchange="window.customActions.triggerUpdate('{{ staff }}', '{{ day_info.full_date }}')" style="font-size: 9px; padding: 2px;">
                                                <option value="" {% if current_shift.start == '' %}selected{% endif %}>開始</option>
                                                {% for t in time_options %}<option value="{{ t }}" {% if current_shift.start == t %}selected{% endif %}>{{ t }}</option>{% endfor %}
                                            </select>
                                            <select id="end_{{ staff }}_{{ day_info.full_date }}" onchange="window.customActions.triggerUpdate('{{ staff }}', '{{ day_info.full_date }}')" style="font-size: 9px; padding: 2px;">
                                                <option value="" {% if current_shift.end == '' %}selected{% endif %}>終了</option>
                                                {% for t in time_options %}<option value="{{ t }}" {% if current_shift.end == t %}selected{% endif %}>{{ t }}</option>{% endfor %}
                                            </select>
                                        </div>
                                    </td>
                                    {% endfor %}
                                </tr>
                                {% endfor %}

                                <!-- ★復活：人数集計行 -->
                                <tr style="background: #262626; border-top: 2px solid #D4AF37;">
                                    <td style="font-weight: bold; font-size: 11px;">現在の人員<br><small style="color: #888;">(過不足)</small></td>
                                    {% for day_info in days_list %}
                                    <td style="font-size: 10px; line-height: 1.4; font-weight: bold; background: #262626;">
                                        {% set count = namespace(k=0, f=0, y=0) %}
                                        {% for s in shifts %}
                                            {% if s.date == day_info.full_date and s.shift != '-' and s.shift != '希望休' %}
                                                {% if 'K' in s.shift %}{% set count.k = count.k + 1 %}{% endif %}
                                                {% if 'F' in s.shift %}{% set count.f = count.f + 1 %}{% endif %}
                                                {% if 'Y' in s.shift %}{% set count.y = count.y + 1 %}{% endif %}
                                            {% endif %}
                                        {% endfor %}
                                        {% set req = requirements.get(day_info.weekday, {'K (キッチン)':0, 'F (フロント)':0, 'Y (やきば)':0}) %}
                                        
                                        <div style="color: {% if count.k < req['K (キッチン)'] %}#ff5252{% else %}#4caf50{% endif %};">K: {{ count.k }} / {{ req['K (キッチン)'] }}</div>
                                        <div style="color: {% if count.f < req['F (フロント)'] %}#ff5252{% else %}#4caf50{% endif %};">F: {{ count.f }} / {{ req['F (フロント)'] }}</div>
                                        <div style="color: {% if count.y < req['Y (やきば)'] %}#ff5252{% else %}#4caf50{% endif %};">Y: {{ count.y }} / {{ req['Y (やきば)'] }}</div>
                                    </td>
                                    {% endfor %}
                                </tr>
                            </tbody>
                        </table>
                    </div>
                    {% endmacro %}

                    {% if selected_week_idx == 'all' %}
                        <h2>📊 前半 (1日 〜 15日)</h2>{{ render_table(first_half) }}
                        <h2>📊 後半 (16日 〜 月末)</h2>{{ render_table(second_half) }}
                    {% else %}
                        {{ render_table(weeks[selected_week_idx|int]) }}
                    {% endif %}
                </div>

                <div id="tab-actual-work" class="admin-tab-content">
                    <div class="card" style="border-left-color: #27ae60;">
                        <h3>📝 勤務終わりに実働時間を入力・登録</h3>
                        <form onsubmit="window.customActions.submitActualWork(event)" style="display: flex; flex-wrap: wrap; gap: 8px; align-items: flex-end;">
                            <div style="flex: 1; min-width: 120px;">
                                <label style="font-size: 10px; display: block; margin-bottom: 2px;">日付:</label>
                                <input type="date" id="act_date" required value="{{ today_date_str }}">
                            </div>
                            <div style="flex: 1; min-width: 120px;">
                                <label style="font-size: 10px; display: block; margin-bottom: 2px;">スタッフ名:</label>
                                <select id="act_staff" required>
                                    <option value="">-- スタッフ選択 --</option>
                                    {% for staff in staffs.keys() %}<option value="{{ staff }}">{{ staff }}</option>{% endfor %}
                                </select>
                            </div>
                            <div style="flex: 1; min-width: 100px;">
                                <label style="font-size: 10px; display: block; margin-bottom: 2px;">出勤ステータス:</label>
                                <select id="act_status" required>
                                    <option value="定時">定時</option><option value="遅刻">遅刻</option><option value="早退">早退</option><option value="残業">残業</option><option value="欠勤">欠勤</option>
                                </select>
                            </div>
                            <div style="flex: 1; min-width: 110px;">
                                <label style="font-size: 10px; display: block; margin-bottom: 2px;">開始時間:</label>
                                <select id="act_start_time">
                                    <option value="">未指定</option>{% for t in time_options %}<option value="{{ t }}">{{ t }}</option>{% endfor %}
                                </select>
                            </div>
                            <div style="flex: 1; min-width: 110px;">
                                <label style="font-size: 10px; display: block; margin-bottom: 2px;">終了時間:</label>
                                <select id="act_end_time">
                                    <option value="">未指定</option>{% for t in time_options %}<option value="{{ t }}">{{ t }}</option>{% endfor %}
                                </select>
                            </div>
                            <button type="submit" class="btn-green" style="padding: 7px 15px; font-size: 12px; height: 31px;">実績を保存</button>
                        </form>
                    </div>

                    <div class="card">
                        <h3>💰 スタッフ別 実働・給料一覧 ({{ month_title }})</h3>
                        <div class="table-responsive">
                            <table>
                                <thead><tr><th>スタッフ名</th><th>基本ポジション</th><th>時給</th><th>出勤日数</th><th>総労働時間</th><th>概算給料</th></tr></thead>
                                <tbody>
                                    {% for staff, info in staffs.items() %}
                                    {% set summary = admin_staff_summaries.get(staff, {'days': 0, 'hours': 0.0, 'pay': 0}) %}
                                    <tr>
                                        <td><strong>{{ staff }}</strong></td>
                                        <td>{{ info.default_position.split(' ')[0] if info.default_position else '無' }}</td>
                                        <td>{{ info.hourly_wage }} 円</td>
                                        <td>{{ summary.days }} 日</td>
                                        <td>{{ summary.hours }} 時間</td>
                                        <td style="color: #FFA726; font-weight: bold;">￥{{ "{:,}".format(summary.pay) }}</td>
                                    </tr>
                                    {% endfor %}
                                </tbody>
                            </table>
                        </div>
                    </div>
                </div>
            </main>
        </div>

        <script>
            window.customActions = {
                targetStaffForPass: '', currentYear: {{ current_year }}, currentMonth: {{ current_month }}, selectedWeekIdx: '{{ selected_week_idx }}',
                syncFromSheets: function() {
                    if(confirm('スプレッドシートから同期しますか？')) {
                        fetch('/api/sync_from_sheets', { method: 'POST' }).then(res => res.json()).then(data => { alert(data.message); if(data.success) location.reload(); }).catch(e => alert("通信エラー"));
                    }
                },
                switchAdminTab: function(tabId, btnElement) {
                    document.querySelectorAll('.admin-tab-content').forEach(c => c.classList.remove('active'));
                    document.querySelectorAll('.admin-tab-btn').forEach(b => b.classList.remove('active'));
                    document.getElementById(tabId).classList.add('active');
                    btnElement.classList.add('active');
                },
                openPassModal: function(name, currentWage, defaultPos) {
                    this.targetStaffForPass = name;
                    document.getElementById('modalStaffLabel').innerText = `「${name}」さんの情報変更`;
                    document.getElementById('modalNewPass').value = ''; document.getElementById('modalNewWage').value = currentWage; document.getElementById('modalNewPos').value = defaultPos || '';
                    document.getElementById('passModal').style.display = 'flex';
                },
                closePassModal: function() { document.getElementById('passModal').style.display = 'none'; },
                submitPassModal: function() {
                    fetch('/api/update_staff_info', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ staff_name: this.targetStaffForPass, new_password: document.getElementById('modalNewPass').value, new_wage: document.getElementById('modalNewWage').value, default_position: document.getElementById('modalNewPos').value }) })
                    .then(res => res.json()).then(data => { alert(data.message); this.closePassModal(); if(data.success) location.reload(); }).catch(e => alert("通信エラー"));
                },
                generateAutoShift: function() {
                    if(confirm('自動シフト作成を開始しますか？')) {
                        fetch('/api/generate_auto_shift', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ year: this.currentYear, month: this.currentMonth, unit: this.selectedWeekIdx }) })
                        .then(res => res.json()).then(data => { alert(data.message); if(data.success) location.reload(); }).catch(e => alert("通信エラー"));
                    }
                },
                publishShifts: function() {
                    if(confirm('シフト内容を確定し公開しますか？')) {
                        fetch('/api/publish_shifts', { method: 'POST', headers: { 'Content-Type': 'application/json' } })
                        .then(res => res.json()).then(data => { alert(data.message); if(data.success) location.reload(); }).catch(e => alert("通信エラー"));
                    }
                },
                submitDeadline: function(e) {
                    e.preventDefault();
                    fetch('/api/update_deadline', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ deadline: document.getElementById('deadline_input').value }) })
                    .then(res => res.json()).then(data => alert(data.message)).catch(e => alert("通信エラー"));
                },
                submitAddStaff: function(e) {
                    e.preventDefault();
                    fetch('/api/add_staff', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: document.getElementById('new_staff_name').value, wage: document.getElementById('new_staff_wage').value, default_position: document.getElementById('new_staff_pos').value }) })
                    .then(res => res.json()).then(data => { alert(data.message); if(data.success) location.reload(); }).catch(e => alert("通信エラー"));
                },
                deleteStaff: function(name) {
                    if(confirm(`「${name}」さんを削除しますか？`)) {
                        fetch('/api/delete_staff', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }) })
                        .then(res => res.json()).then(data => { alert(data.message); if(data.success) location.reload(); }).catch(e => alert("通信エラー"));
                    }
                },
                submitReq: function(e) {
                    e.preventDefault();
                    fetch('/api/update_requirement', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ day: document.getElementById('req_day').value, req_k: document.getElementById('req_k').value, req_f: document.getElementById('req_f').value, req_y: document.getElementById('req_y').value }) })
                    .then(res => res.json()).then(data => { alert(data.message); if(data.success) location.reload(); }).catch(e => alert("通信エラー"));
                },
                triggerUpdate: function(staff_name, date) {
                    const start = document.getElementById(`start_${staff_name}_${date}`).value;
                    const end = document.getElementById(`end_${staff_name}_${date}`).value;
                    fetch('/api/update_shift', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ staff_name, date, shift: document.getElementById(`shift_${staff_name}_${date}`).value, time: (start || end) ? `${start}-${end}` : '' }) });
                },
                applyOneWish: function(staff_name, date, shift, time) {
                    if(confirm(`希望を反映しますか？`)) {
                        fetch('/api/apply_one_wish', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ staff_name, date, shift, time }) }).then(res => res.json()).then(data => { alert(data.message); location.reload(); });
                    }
                },
                applyAllWishes: function() {
                    if(confirm('すべての希望シフトを反映しますか？')) {
                        fetch('/api/apply_all_wishes', { method: 'POST', headers: { 'Content-Type': 'application/json' } }).then(res => res.json()).then(data => { alert(data.message); location.reload(); });
                    }
                },
                submitActualWork: function(e) {
                    e.preventDefault();
                    const staff = document.getElementById('act_staff').value;
                    if (!staff) { alert("スタッフ名を選択してください"); return; }
                    const start = document.getElementById('act_start_time').value;
                    const end = document.getElementById('act_end_time').value;
                    fetch('/api/update_actual_shift', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ staff_name: staff, date: document.getElementById('act_date').value, status: document.getElementById('act_status').value, time: (start || end) ? `${start}-${end}` : '' }) })
                    .then(res => res.json()).then(data => { alert(data.message); if(data.success) location.reload(); }).catch(e => alert("通信エラー"));
                }
            };
        </script>
    {% endif %}
</body>
</html>
"""

SETTINGS_TEMPLATE = """
<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>⚙️ 管理者設定</title>
    <style>
        body { font-family: sans-serif; margin: 0; background: #121212; color: #E0E0E0; display: flex; height: 100vh; }
        header { background: #1E1E1E; padding: 10px 20px; display: flex; justify-content: space-between; align-items: center; border-bottom: 2px solid #D4AF37; width: 100%; box-sizing: border-box;}
        .container { display: flex; padding: 20px; gap: 15px; width: 100%; }
        aside { width: 180px; background: #1E1E1E; padding: 15px; border-radius: 8px; }
        aside h2 { color: #D4AF37; font-size: 15px; border-bottom: 1px solid #D4AF37; }
        aside a { color: #E0E0E0; text-decoration: none; display: block; padding: 5px; margin-top: 5px;}
        .card { background: #1E1E1E; padding: 20px; border-radius: 8px; width: 300px; border-left: 5px solid #D4AF37; }
        input, button { padding: 8px; margin-top: 10px; width: 100%; box-sizing: border-box; }
        button { background: #D4AF37; border: none; font-weight: bold; cursor: pointer;}
    </style>
</head>
<body>
    <div style="display:flex; flex-direction:column; width:100%;">
        <header><h1>{{ shop_name }} 管理</h1><a href="/"><button style="width:auto; padding:5px 10px;">戻る</button></a></header>
        <div class="container">
            <aside><h2>メニュー</h2><a href="/">📊 シフト作成</a><a href="/admin/settings" style="background:#D4AF37; color:#121212;">⚙️ 設定</a></aside>
            <div class="card">
                <h3 style="color:#D4AF37; margin-top:0;">パスワード変更</h3>
                <form onsubmit="event.preventDefault(); fetch('/api/update_password', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({new_password: document.getElementById('np').value})}).then(r=>r.json()).then(d=>{alert(d.message); location.reload();});">
                    <input type="password" id="np" placeholder="新しいパスワード" required>
                    <button type="submit">変更</button>
                </form>
            </div>
        </div>
    </div>
</body>
</html>
"""

STAFF_TEMPLATE = """
<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>スタッフポータル</title>
    <style>
        body { font-family: sans-serif; margin: 0; background: #121212; color: #E0E0E0; }
        header { background: #1E1E1E; padding: 15px; border-bottom: 2px solid #D4AF37; display: flex; justify-content: space-between; align-items: center; }
        .card { background: #1E1E1E; padding: 20px; border-radius: 8px; max-width: 480px; margin: 20px auto; width: 90%; border-left: 5px solid #D4AF37; }
        select, input, button { padding: 8px; width: 100%; box-sizing: border-box; margin-bottom: 10px; background: #262626; color: white; border: 1px solid #333; border-radius: 4px; }
        button { background: #D4AF37; color: #121212; font-weight: bold; cursor: pointer; border: none; }
        .tab-menu { display: flex; gap: 5px; margin-bottom: 15px; }
        .tab-btn { flex: 1; padding: 8px; background: #333; text-align: center; border-radius: 4px; cursor: pointer; font-size:12px; }
        .tab-btn.active { background: #D4AF37; color: #121212; font-weight: bold; }
        .tab-content { display: none; }
        .tab-content.active { display: block; }
        .day-row { padding: 10px; border-bottom: 1px solid #333; background: #1a1a1a; margin-bottom: 5px; border-radius: 4px; }
        .staff-table { width: 100%; border-collapse: collapse; font-size: 12px; }
        .staff-table th, .staff-table td { border: 1px solid #333; padding: 8px; text-align: center; }
        .staff-table th { background: #262626; color: #D4AF37; }
    </style>
</head>
<body>
    <header><h1 style="margin:0; font-size:18px; color:#D4AF37;">{{ short_name }}</h1><a href="/logout" style="color:#FFA726; font-size:12px; text-decoration:none;">ログアウト</a></header>
    <div class="card">
        {% if not logged_in %}
        <form method="POST" action="/staff_login">
            <h2 style="margin-top:0; font-size:16px;">スタッフログイン</h2>
            <select name="name" required><option value="">名前を選択</option>{% for s in staffs.keys() %}<option value="{{ s }}">{{ s }}</option>{% endfor %}</select>
            <input type="password" name="staff_password" placeholder="パスワード(初期: 0000)" required>
            <button type="submit">ログイン</button>
            {% if auth_error %}<p style="color:red; font-size:12px;">エラー</p>{% endif %}
        </form>
        {% else %}
        <form method="GET" action="/staff" style="display:flex; gap:5px;">
            <select name="year" onchange="this.form.submit()"><option value="2025" {% if current_year == 2025 %}selected{% endif %}>2025年</option><option value="2026" {% if current_year == 2026 %}selected{% endif %}>2026年</option></select>
            <select name="month" onchange="this.form.submit()">{% for m in range(1,13) %}<option value="{{ m }}" {% if current_month == m %}selected{% endif %}>{{ m }}月</option>{% endfor %}</select>
        </form>
        
        <div class="tab-menu">
            <div class="tab-btn active" onclick="window.staffActions.switchTab('t1', this)">希望申請</div>
            <div class="tab-btn" onclick="window.staffActions.switchTab('t2', this)">確定シフト</div>
            <div class="tab-btn" onclick="window.staffActions.switchTab('t3', this)">実働・給料</div>
        </div>

        <div id="t1" class="tab-content active">
            <p style="font-size:11px; color:#FFA726;">期限: {{ deadline_str }}</p>
            <form onsubmit="window.staffActions.submitWish(event)">
                <input type="hidden" id="s_name" value="{{ selected_staff }}">
                {% for d in all_days %}
                {% set cur = staff_wishes.get(d.full_date, {}) %}
                {% set p = cur.get('time', '').split('-') if '-' in cur.get('time', '') else ['', ''] %}
                <div class="day-row" data-date="{{ d.full_date }}">
                    <div style="display:flex; justify-content:space-between; margin-bottom:5px;">
                        <strong style="color:#D4AF37;">{{ d.date_str }}({{ d.weekday }})</strong>
                        <select class="s-shift" style="width:100px; margin:0;" onchange="this.parentElement.nextElementSibling.style.display = this.value=='希望休' ? 'none' : 'flex'">
                            <option value="-" {% if cur.get('shift') == '-' %}selected{% endif %}>未選択</option>
                            <option value="出勤希望" {% if cur.get('shift') == '出勤希望' %}selected{% endif %}>出勤</option>
                            <option value="希望休" {% if cur.get('shift') == '希望休' %}selected{% endif %}>休</option>
                        </select>
                    </div>
                    <div class="s-time" style="display:{% if cur.get('shift') == '希望休' %}none{% else %}flex{% endif %}; gap:5px;">
                        <select class="s-start" style="margin:0;"><option value="">開始</option>{% for t in time_options %}<option value="{{ t }}" {% if p[0] == t %}selected{% endif %}>{{ t }}</option>{% endfor %}</select>
                        <select class="s-end" style="margin:0;"><option value="">終了</option>{% for t in time_options %}<option value="{{ t }}" {% if p[1] == t %}selected{% endif %}>{{ t }}</option>{% endfor %}</select>
                    </div>
                </div>
                {% endfor %}
                <button type="submit" style="background:#2E7D32; color:white;">一括提出</button>
            </form>
        </div>

        <div id="t2" class="tab-content">
            <table class="staff-table">
                <tr><th>日付</th><th>シフト</th><th>時間</th></tr>
                {% for d in all_days %}
                {% set s = staff_shifts.get(d.full_date) %}
                {% if s and s.shift != '-' %}
                <tr><td>{{ d.date_str }}</td><td>{{ s.shift.split(' ')[0] }}</td><td>{{ s.time_display }}</td></tr>
                {% endif %}
                {% endfor %}
            </table>
        </div>

        <div id="t3" class="tab-content">
            <div style="background:#262626; padding:10px; border-radius:5px; margin-bottom:10px;">
                <p style="margin:5px 0;">出勤: {{ total_shift_days }}日 / 時間: {{ total_hours }}h</p>
                <h3 style="color:#FFA726; margin:5px 0;">給料概算: ￥{{ "{:,}".format(est_pay) }}</h3>
            </div>
            <table class="staff-table">
                <tr><th>日付</th><th>状態</th><th>時間</th><th>実働</th></tr>
                {% for d in daily_work_details %}
                <tr style="{% if not d.is_working %}color:#666;{% endif %}">
                    <td>{{ d.date }}</td><td>{{ d.shift }}</td><td>{{ d.time_display }}</td><td>{{ d.hours }}h</td>
                </tr>
                {% endfor %}
            </table>
        </div>
        
        <script>
            window.staffActions = {
                switchTab: function(id, btn) {
                    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
                    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
                    document.getElementById(id).classList.add('active'); btn.classList.add('active');
                },
                submitWish: function(e) {
                    e.preventDefault();
                    if(!confirm('提出しますか？')) return;
                    let shifts = {};
                    document.querySelectorAll('.day-row').forEach(row => {
                        const shift = row.querySelector('.s-shift').value;
                        const start = row.querySelector('.s-start').value;
                        const end = row.querySelector('.s-end').value;
                        shifts[row.getAttribute('data-date')] = { shift: shift, time: (shift !== '希望休' && (start||end)) ? `${start}-${end}` : '' };
                    });
                    fetch('/api/submit_wish_shifts', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ staff_name: document.getElementById('s_name').value, shifts: shifts }) })
                    .then(r => r.json()).then(d => { alert(d.message); location.reload(); }).catch(e => alert("通信エラー"));
                }
            };
        </script>
        {% endif %}
    </div>
</body>
</html>
"""

# ==========================================
# ルーティング設定
# ==========================================
@app.route('/')
def index():
    is_admin = session.get('is_admin', False)
    today = datetime.today()
    year, month = int(request.args.get('year', today.year)), int(request.args.get('month', today.month))
    week_idx = request.args.get('week_idx', 'all')
    first_half, second_half, month_title = get_month_days(year, month)
    
    admin_staff_summaries = {}
    today_str = today.strftime('%Y-%m-%d')

    for staff_name, s_info in staffs_db.items():
        total_mins, total_days = 0, 0
        for day_info in (first_half + second_half):
            date = day_info['full_date']
            if date > today_str: continue

            s = next((item for item in published_shifts_data if item['staff_name'] == staff_name and item['date'] == date), None)
            act = next((item for item in actual_shifts_data if item['staff_name'] == staff_name and item['date'] == date), None)

            time_range = act['time'] if act and act['status'] != '欠勤' else (s['time'] if s and s['shift'] not in ['-', '希望休'] else '-')
            if time_range != '-' or (act and act['status'] != '欠勤'):
                total_days += 1
                if '-' in time_range:
                    try:
                        p = time_range.split('-')
                        sh, sm = map(int, p[0].split(':'))
                        eh, em = map(int, (p[1] if len(p) > 1 else '00:00').split(':'))
                        mins = (eh * 60 + em) - (sh * 60 + sm)
                        total_mins += mins if mins > 0 else 300
                    except: total_mins += 300
                else: total_mins += 300

        th = round(total_mins / 60.0, 1)
        admin_staff_summaries[staff_name] = {'days': total_days, 'hours': th, 'pay': int(th * s_info.get('hourly_wage', 1100))}

    return render_template_string(HTML_TEMPLATE, shop_name=SHOP_NAME, is_admin=is_admin, staffs=staffs_db, requirements=required_settings, first_half=first_half, second_half=second_half, weeks=get_month_weeks(year, month), selected_week_idx=week_idx, wishes=wishes_data, shifts=shifts_data, time_options=TIME_OPTIONS, month_title=month_title, current_year=year, current_month=month, today_date_str=today_str, deadline_val=deadline_settings['deadline'].replace(' ', 'T') if deadline_settings['deadline'] else '', admin_staff_summaries=admin_staff_summaries, login_error=False)

@app.route('/admin_login', methods=['POST'])
def admin_login():
    if request.form.get('password') == admin_credentials['password']: session['is_admin'] = True
    return redirect(url_for('index'))

@app.route('/admin/settings')
def admin_settings(): return render_template_string(SETTINGS_TEMPLATE, shop_name=SHOP_NAME) if session.get('is_admin') else redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.pop('is_admin', None)
    session.pop('logged_staff', None)
    return redirect(url_for('index'))

@app.route('/staff')
def staff_page():
    staff = session.get('logged_staff', '')
    today = datetime.today()
    y, m = int(request.args.get('year', today.year)), int(request.args.get('month', today.month))
    fh, sh, _ = get_month_days(y, m)
    all_days = fh + sh
    
    w_data, s_data, details, days, hours = {}, {}, [], 0, 0.0
    
    if staff in staffs_db:
        w_data = {w['date']: w for w in wishes_data if w['staff_name'] == staff}
        s_data = {s['date']: {'shift': s['shift'], 'time_display': format_time_display(s['time'])} for s in published_shifts_data if s['staff_name'] == staff}
        
        tm = 0
        for d in all_days:
            dt = d['full_date']
            if dt > today.strftime('%Y-%m-%d'): continue
            ps = next((i for i in published_shifts_data if i['staff_name'] == staff and i['date'] == dt), None)
            act = next((i for i in actual_shifts_data if i['staff_name'] == staff and i['date'] == dt), None)
            
            is_w, tr, stat = False, '-', '-'
            if act:
                is_w, tr = (act['status'] != '欠勤'), (act['time'] if act['status'] != '欠勤' else '-')
                stat = '欠勤' if not is_w else f"{ps['shift'].split(' ')[0] if ps and ps['shift']!='-' else '追加'} ({act['status']})"
            elif ps and ps['shift'] not in ['-', '希望休']:
                is_w, tr, stat = True, ps['time'], ps['shift'].split(' ')[0]
            elif ps and ps['shift'] == '希望休':
                stat = '希望休'

            dh = 0.0
            if is_w:
                days += 1
                if '-' in tr:
                    try:
                        pts = tr.split('-')
                        mins = (int((pts[1] if len(pts)>1 else '00:00').split(':')[0])*60 + int((pts[1] if len(pts)>1 else '00:00').split(':')[1])) - (int(pts[0].split(':')[0])*60 + int(pts[0].split(':')[1]))
                        if mins>0: tm += mins; dh = round(mins/60.0,1)
                        else: tm += 300; dh = 5.0
                    except: tm += 300; dh = 5.0
                else: tm += 300; dh = 5.0
            details.append({'date': d['date_str'], 'shift': stat, 'time_display': format_time_display(tr), 'hours': dh, 'is_working': is_w})
        hours = round(tm / 60.0, 1)

    dl = deadline_settings['deadline']
    is_dl_pass = dl and datetime.now() > datetime.strptime(dl.replace('T', ' '), '%Y-%m-%d %H:%M')
    
    return render_template_string(STAFF_TEMPLATE, short_name=SHORT_NAME, current_year=y, current_month=m, staffs=staffs_db, selected_staff=staff, logged_in=bool(staff), all_days=all_days, staff_wishes=w_data, staff_shifts=s_data, daily_work_details=details, time_options=TIME_OPTIONS, total_shift_days=days, total_hours=hours, est_pay=int(hours * staffs_db.get(staff, {}).get('hourly_wage', 1100)), deadline_str=dl.replace('T', ' ') if dl else '未設定', is_deadline_passed=is_dl_pass, auth_error=False)

@app.route('/staff_login', methods=['POST'])
def staff_login():
    name = request.form.get('name')
    if name in staffs_db and staffs_db[name]['password'] == request.form.get('staff_password'):
        session['logged_staff'] = name
        return redirect(url_for('staff_page'))
    return redirect(url_for('staff_page'))

# --- 各種 API エンドポイント ---
@app.route('/api/add_staff', methods=['POST'])
def add_staff():
    name = request.json.get('name')
    if name and name not in staffs_db:
        staffs_db[name] = {'password': '0000', 'hourly_wage': int(request.json.get('wage', 1100)), 'default_position': request.json.get('default_position', '')}
        save_data(); return jsonify({'success': True, 'message': '追加完了'})
    return jsonify({'success': False, 'message': 'エラー'})

@app.route('/api/delete_staff', methods=['POST'])
def delete_staff():
    name = request.json.get('name')
    if name in staffs_db:
        del staffs_db[name]; save_data()
        return jsonify({'success': True, 'message': '削除完了'})
    return jsonify({'success': False, 'message': 'エラー'})

@app.route('/api/update_staff_info', methods=['POST'])
def update_staff_info():
    sn = request.json.get('staff_name')
    if sn in staffs_db:
        if request.json.get('new_password'): staffs_db[sn]['password'] = request.json.get('new_password')
        if request.json.get('new_wage'): staffs_db[sn]['hourly_wage'] = int(request.json.get('new_wage'))
        if request.json.get('default_position') is not None: staffs_db[sn]['default_position'] = request.json.get('default_position')
        save_data(); return jsonify({'success': True, 'message': '更新完了'})
    return jsonify({'success': False, 'message': 'エラー'})

@app.route('/api/update_requirement', methods=['POST'])
def update_requirement():
    required_settings[request.json.get('day')] = {'K (キッチン)': int(request.json.get('req_k', 0)), 'F (フロント)': int(request.json.get('req_f', 0)), 'Y (やきば)': int(request.json.get('req_y', 0))}
    save_data(); return jsonify({'success': True, 'message': '更新完了'})

@app.route('/api/update_deadline', methods=['POST'])
def update_deadline():
    if request.json.get('deadline'):
        deadline_settings['deadline'] = request.json.get('deadline').replace('T', ' ')
        save_data(); return jsonify({'success': True, 'message': '更新完了'})
    return jsonify({'success': False, 'message': 'エラー'})

@app.route('/api/update_password', methods=['POST'])
def update_password():
    if request.json.get('new_password'):
        admin_credentials['password'] = request.json.get('new_password')
        save_data(); return jsonify({'success': True, 'message': '更新完了'})
    return jsonify({'success': False, 'message': 'エラー'})

@app.route('/api/update_shift', methods=['POST'])
def update_shift():
    sn, dt, sh, tm = request.json.get('staff_name'), request.json.get('date'), request.json.get('shift'), request.json.get('time', '')
    ex = next((s for s in shifts_data if s['staff_name'] == sn and s['date'] == dt), None)
    if ex:
        if sh == '-': shifts_data.remove(ex)
        else: ex['shift'], ex['time'] = sh, tm
    elif sh != '-': shifts_data.append({'staff_name': sn, 'date': dt, 'shift': sh, 'time': tm})
    save_data(); return jsonify({'success': True, 'message': '更新完了'})

@app.route('/api/update_actual_shift', methods=['POST'])
def update_actual_shift():
    sn, dt, st, tm = request.json.get('staff_name'), request.json.get('date'), request.json.get('status', '定時'), request.json.get('time', '')
    ex = next((a for a in actual_shifts_data if a['staff_name'] == sn and a['date'] == dt), None)
    if ex: ex['status'], ex['time'] = st, tm
    else: actual_shifts_data.append({'staff_name': sn, 'date': dt, 'status': st, 'time': tm})
    save_data(); return jsonify({'success': True, 'message': '保存完了'})

@app.route('/api/publish_shifts', methods=['POST'])
def publish_shifts():
    global published_shifts_data
    published_shifts_data = [dict(s) for s in shifts_data]
    save_data(); return jsonify({'success': True, 'message': '公開完了'})

@app.route('/api/apply_one_wish', methods=['POST'])
def apply_one_wish():
    sn, dt, sh, tm = request.json.get('staff_name'), request.json.get('date'), request.json.get('shift'), request.json.get('time', '')
    ts = '希望休' if sh == '希望休' else staffs_db.get(sn, {}).get('default_position', 'K (キッチン)')
    ex = next((s for s in shifts_data if s['staff_name'] == sn and s['date'] == dt), None)
    if ex: ex['shift'], ex['time'] = ts, tm
    else: shifts_data.append({'staff_name': sn, 'date': dt, 'shift': ts, 'time': tm})
    save_data(); return jsonify({'success': True, 'message': '反映完了'})

@app.route('/api/apply_all_wishes', methods=['POST'])
def apply_all_wishes():
    for w in wishes_data:
        sn, dt, sh, tm = w['staff_name'], w['date'], w['shift'], w.get('time', '')
        ts = '希望休' if sh == '希望休' else staffs_db.get(sn, {}).get('default_position', 'K (キッチン)')
        ex = next((s for s in shifts_data if s['staff_name'] == sn and s['date'] == dt), None)
        if ex: ex['shift'], ex['time'] = ts, tm
        else: shifts_data.append({'staff_name': sn, 'date': dt, 'shift': ts, 'time': tm})
    save_data(); return jsonify({'success': True, 'message': '一括反映完了'})

@app.route('/api/submit_wish_shifts', methods=['POST'])
def submit_wish_shifts():
    global wishes_data
    sn = request.json.get('staff_name')
    wishes_data = [w for w in wishes_data if w['staff_name'] != sn]
    for dt, info in request.json.get('shifts', {}).items():
        if info['shift'] != '-': wishes_data.append({'staff_name': sn, 'date': dt, 'shift': info['shift'], 'time': info['time']})
    save_data(); return jsonify({'success': True, 'message': '提出完了'})

@app.route('/api/generate_auto_shift', methods=['POST'])
def generate_auto_shift():
    y, m, u = int(request.json.get('year')), int(request.json.get('month')), request.json.get('unit', 'all')
    fh, sh, _ = get_month_days(y, m)
    weeks = get_month_weeks(y, m)
    
    tds = set()
    if u == 'all': tds = {d['full_date'] for d in (fh + sh)}
    elif u.isdigit() and 0 <= int(u) < len(weeks): tds = {d['full_date'] for d in weeks[int(u)] if d['in_month']}
    
    if not tds: return jsonify({'success': False, 'message': '対象なし'})
    global shifts_data
    shifts_data = [s for s in shifts_data if s['date'] not in tds]
    s_list = list(staffs_db.keys())
    
    for d_info in (fh + sh):
        dt_str = d_info['full_date']
        if dt_str not in tds: continue
        reqs = required_settings.get(d_info['weekday'], {'K (キッチン)': 2, 'F (フロント)': 2, 'Y (やきば)': 1})
        assigned = set(); pos_c = {p: 0 for p in POSITIONS}
        d_wishes = {w['staff_name']: w for w in wishes_data if w['date'] == dt_str}

        for s in s_list:
            w = d_wishes.get(s)
            if w and w.get('shift') == '出勤希望':
                dp = staffs_db[s].get('default_position', 'K (キッチン)')
                if pos_c[dp] < reqs.get(dp, 0):
                    shifts_data.append({'staff_name': s, 'date': dt_str, 'shift': dp, 'time': w.get('time', '')}); assigned.add(s); pos_c[dp] += 1

        for p, rc in reqs.items():
            for s in s_list:
                if pos_c[p] >= rc: break
                if s in assigned or (d_wishes.get(s) and d_wishes.get(s).get('shift') == '希望休'): continue
                if staffs_db[s].get('default_position', '') == p:
                    shifts_data.append({'staff_name': s, 'date': dt_str, 'shift': p, 'time': d_wishes.get(s, {}).get('time', '')}); assigned.add(s); pos_c[p] += 1
                    
        for p, rc in reqs.items():
            for s in s_list:
                if pos_c[p] >= rc: break
                if s in assigned or (d_wishes.get(s) and d_wishes.get(s).get('shift') == '希望休'): continue
                shifts_data.append({'staff_name': s, 'date': dt_str, 'shift': p, 'time': d_wishes.get(s, {}).get('time', '')}); assigned.add(s); pos_c[p] += 1

    save_data(); return jsonify({'success': True, 'message': '自動作成完了'})

@app.route('/api/sync_from_sheets', methods=['POST'])
def sync_from_sheets_api():
    global staffs_db, required_settings, deadline_settings, wishes_data, shifts_data, published_shifts_data, actual_shifts_data, admin_credentials
    new_data = load_data()
    if new_data:
        staffs_db, required_settings, deadline_settings = new_data['staffs_db'], new_data['required_settings'], new_data['deadline_settings']
        wishes_data, shifts_data, published_shifts_data = new_data['wishes_data'], new_data['shifts_data'], new_data['published_shifts_data']
        actual_shifts_data, admin_credentials = new_data['actual_shifts_data'], new_data['admin_credentials']
        return jsonify({'success': True, 'message': '同期完了'})
    return jsonify({'success': False, 'message': 'エラー'})

if __name__ == '__main__':
    app.run(debug=True)