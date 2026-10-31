from flask import Flask, request, jsonify, render_template_string, session, redirect, url_for
import pandas as pd
from datetime import datetime, timedelta
import calendar
import os
import json

app = Flask(__name__)
app.secret_key = 'shift_app_secret_key'

# ==========================================
# ⚙️ お店・アプリ名の設定
# ==========================================
SHOP_NAME = "久留米焼鳥 つかさ"  # お店の正式名称
SHORT_NAME = "つかさ"           # 短縮名・ポータル名など

# ==========================================
# 💾 データ永続化（JSONファイル保存・読み込み）
# ==========================================
DATA_FILE = 'shift_data.json'

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
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return default_data

def save_data():
    data = {
        'staffs_db': staffs_db,
        'required_settings': required_settings,
        'deadline_settings': deadline_settings,
        'wishes_data': wishes_data,
        'shifts_data': shifts_data,
        'published_shifts_data': published_shifts_data,
        'actual_shifts_data': actual_shifts_data,
        'admin_credentials': admin_credentials
    }
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

# 起動時にデータをロード
app_data = load_data()
staffs_db = app_data['staffs_db']
required_settings = app_data['required_settings']
deadline_settings = app_data['deadline_settings']
wishes_data = app_data['wishes_data']
shifts_data = app_data['shifts_data']
published_shifts_data = app_data['published_shifts_data']
actual_shifts_data = app_data['actual_shifts_data']
admin_credentials = app_data['admin_credentials']

POSITIONS = ['K (キッチン)', 'F (フロント)', 'Y (やきば)']

def generate_time_options():
    times = []
    for hour in range(9, 25):
        for minute in [0, 15, 30, 45]:
            if hour == 24 and minute > 0:
                continue
            times.append(f"{hour:02d}:{minute:02d}")
    return times

TIME_OPTIONS = generate_time_options()

def format_time_display(time_str):
    if not time_str or time_str == '-':
        return '時間指定なし'
    if '-' in time_str:
        parts = time_str.split('-')
        start = parts[0]
        end = parts[1] if len(parts) > 1 else ''
        if start and end:
            return f"{start}〜{end}"
        elif start and not end:
            return f"{start}〜"
        elif not start and end:
            return f"〜{end}"
    return time_str

def get_month_weeks(year, month):
    cal = calendar.Calendar(firstweekday=0)
    month_days = cal.monthdatescalendar(year, month)
    weeks = []
    weekdays_map = ['月', '火', '水', '木', '金', '土', '日']
    
    for week in month_days:
        week_info = []
        for dt in week:
            day_info = {
                'full_date': dt.strftime('%Y-%m-%d'),
                'date_str': f"{dt.month}/{dt.day}",
                'weekday': weekdays_map[dt.weekday()],
                'is_weekend': dt.weekday() >= 5,
                'in_month': dt.month == month
            }
            week_info.append(day_info)
        weeks.append(week_info)
    return weeks

def get_month_days(year, month):
    _, last_day = calendar.monthrange(year, month)
    weekdays_map = ['月', '火', '水', '木', '金', '土', '日']
    first_half, second_half = [], []
    for day in range(1, last_day + 1):
        dt = datetime(year, month, day)
        day_info = {
            'full_date': dt.strftime('%Y-%m-%d'),
            'date_str': f"{month}/{day}",
            'weekday': weekdays_map[dt.weekday()],
            'is_weekend': dt.weekday() >= 5
        }
        if day <= 15:
            first_half.append(day_info)
        else:
            second_half.append(day_info)
    return first_half, second_half, f"{year}年{month}月"

# --- 管理者画面テンプレート ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ shop_name }} シフト管理</title>
    <style>
        body { font-family: 'Noto Sans JP', sans-serif; margin: 0; background-color: #121212; color: #E0E0E0; display: flex; flex-direction: column; height: 100vh; }
        
        header { 
            background-color: #1E1E1E; 
            padding: 10px 20px; 
            display: flex; 
            justify-content: space-between; 
            align-items: center; 
            border-bottom: 2px solid #D4AF37; 
            color: #E0E0E0; 
            position: fixed; top: 0; left: 0; width: 100%; z-index: 1000;
            box-sizing: border-box;
        }
        header h1 { 
            display: flex; align-items: center; gap: 10px; margin: 0; font-size: 20px; color: #D4AF37;
        }
        header h1 strong { font-weight: bold; color: #D4AF37; }
        
        .container { 
            display: flex; 
            gap: 15px; 
            padding: 75px 15px 15px 15px; 
            flex-wrap: wrap; 
            margin-bottom: 20px; 
            flex: 1; 
        }
        
        aside {
            width: 180px;
            background-color: #1E1E1E;
            padding: 15px;
            border-radius: 8px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.5);
            background-image: url('data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIzMCIgaGVpZ2h0PSIzMCIgdmlld0JveD0iMCAwIDMwIDMwIj48ZyBmaWxsPSIjRDRBRjM3IiBmaWxsLW9wYWNpdHk9IjAuMDUiPjxwYXRo dD0iTS0w IDE1bDE1LTE1TDMwIDE1bC0xNSAxNUww IDE1eiIvPjwvZz48L3N2Zz4=');
        }
        aside h2 { font-size: 15px; color: #D4AF37; border-bottom: 1px solid #D4AF37; padding-bottom: 8px; margin-top: 0; }
        aside ul { list-style: none; padding: 0; margin: 10px 0 0 0; }
        aside ul li { margin-bottom: 8px; }
        aside ul li a { text-decoration: none; color: #E0E0E0; font-size: 13px; display: block; padding: 4px; border-radius: 4px; transition: background-color 0.3s; }
        aside ul li a.active { background-color: #D4AF37; color: #121212; font-weight: bold; }
        aside ul li a:hover:not(.active) { background-color: #333333; }
        
        main { flex: 1; padding: 0 10px 10px 10px; min-width: 0; }
        
        .card { 
            background: #1E1E1E; 
            padding: 15px; 
            border-radius: 8px; 
            box-shadow: 0 4px 6px rgba(0,0,0,0.5); 
            margin-bottom: 15px;
            border-left: 5px solid #D4AF37;
            background-image: url('data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIzMCIgaGVpZ2h0PSIzMCIgdmlld0JveD0iMCAwIDMwIDMwIj48ZyBmaWxsPSIjRDRBRjM3IiBmaWxsLW9wYWNpdHk9IjAuMDUiPjxwYXRo dD0iTS0wIDE1bDE1LTE1TDMwIDE1bC0xNSAxNUww IDE1eiIvPjwvZz48L3N2Zz4=');
        }
        .card h3 { color: #D4AF37; font-size: 16px; margin-top: 0; border-bottom: 1px solid #333; padding-bottom: 8px; }
        
        form { display: flex; flex-direction: column; gap: 8px; }
        input, select, button { padding: 6px; border: 1px solid #333; border-radius: 4px; font-size: 12px; background: #262626; color: #E0E0E0; }
        input:focus, select:focus { border-color: #D4AF37; outline: none; }
        
        button { background-color: #D4AF37; color: #121212; border: none; cursor: pointer; font-weight: bold; transition: background-color 0.3s; }
        button:hover { background-color: #C09A32; }
        .btn-green { background-color: #2E7D32; color: #FFFFFF; }
        .btn-green:hover { background-color: #1E5B23; }
        .btn-danger { background-color: #D32F2F; color: #FFFFFF; }
        .btn-danger:hover { background-color: #A52222; }
        
        .table-responsive { overflow-x: auto; background: #1E1E1E; padding: 15px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.5); margin-bottom: 15px; max-height: 480px; }
        table { width: 100%; border-collapse: collapse; margin-top: 5px; white-space: nowrap; font-size: 12px; color: #E0E0E0; table-layout: auto; }
        th, td { border: 1px solid #333; padding: 5px 6px; text-align: center; background: #1E1E1E; }
        th { background-color: #262626; color: #D4AF37; position: sticky; top: 0; z-index: 3; }
        
        .weekend { background-color: #261111 !important; }
        .msg { margin-top: 8px; font-weight: bold; color: #FFA726; font-size: 11px; }
        .shift-select, .time-select { padding: 3px; font-size: 11px; background: #262626; color: #E0E0E0; border: 1px solid #333; }

        .staff-grid { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; max-height: 150px; overflow-y: auto; }
        .staff-chip { background: #262626; border: 1px solid #333; padding: 4px 8px; border-radius: 4px; display: flex; align-items: center; gap: 6px; font-size: 12px; }
        .staff-name-btn { background: none; border: none; padding: 0; color: #E0E0E0; font-weight: bold; cursor: pointer; text-decoration: underline; }

        .wish-box { background: #1A1A1A; padding: 12px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.5); margin-bottom: 20px; border-left: 5px solid #2E7D32; color: #E0E0E0; }
        .wish-table { width: 100%; border-collapse: collapse; font-size: 11px; margin-top: 8px; background: #1E1E1E; }
        .wish-table th, .wish-table td { border: 1px solid #333; padding: 5px; text-align: center; }
        .wish-table th { background-color: #262626; color: #2E7D32; }

        tr.summary-row { background: #262626 !important; font-weight: bold; border-top: 2px solid #D4AF37; }
        tr.summary-row td { background: #262626 !important; }
        td.insufficient { color: #D32F2F !important; background: #261111 !important; }

        .admin-tab-menu { display: flex; gap: 6px; margin-bottom: 12px; }
        .admin-tab-btn { padding: 8px 16px; background: #1E1E1E; border: 1px solid #333; border-radius: 4px; font-weight: bold; cursor: pointer; font-size: 12px; color: #E0E0E0; }
        .admin-tab-btn.active { background: #D4AF37; color: #121212; border-color: #D4AF37; }
        .admin-tab-content { display: none; }
        .admin-tab-content.active { display: block; }

        @media print {
            body { background: white; margin: 0; color: black; }
            header, aside, .no-print { display: none !important; }
            .container { padding: 0; display: block; margin: 0; }
            main { margin: 0; padding: 0; width: 100%; }
            .card, .table-responsive { overflow: visible; box-shadow: none; padding: 0; max-height: none; background: white; border: none; }
            th, td { border: 1px solid #ddd; padding: 5px; text-align: center; background: white; color: black; }
            th { background: #eee; color: black; }
            .wish-box { display: none; }
        }
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
            <p style="font-size: 11px; color: #888; text-align: center;">初期パスワード: <strong>1234</strong></p>
            <form method="POST" action="/admin_login">
                <input type="password" name="password" placeholder="パスワード" required style="padding: 8px;">
                <button type="submit" style="padding: 8px;">ログイン</button>
            </form>
            {% if login_error %}
            <div class="msg">パスワードが間違っています。</div>
            {% endif %}
        </div>
    {% else %}
        <div class="container">
            <aside class="no-print">
                <h2>🖥️ 管理メニュー</h2>
                <ul>
                    <li><a href="/" class="active">📊 シフト作成</a></li>
                    <li><a href="/admin/settings">⚙️ 設定・パス変更</a></li>
                </ul>
                <button class="btn-purple" onclick="generateAutoShift()" style="width: 100%; margin-top: 12px; background: #8e44ad; padding: 8px; font-size: 12px;">🤖 自動シフト作成</button>
                <button class="btn-green" onclick="publishShifts()" style="width: 100%; margin-top: 8px; padding: 8px; font-size: 12px; background: #27ae60;">📢 シフトを確定・公開</button>
                <button class="btn-green" onclick="window.print()" style="width: 100%; margin-top: 8px; padding: 8px; font-size: 12px;">🖨️ 印刷 / PDF</button>
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
                            {% for m in range(1, 13) %}
                            <option value="{{ m }}" {% if current_month == m %}selected{% endif %}>{{ m }}月</option>
                            {% endfor %}
                        </select>
                        <select name="week_idx" onchange="this.form.submit()">
                            <option value="all" {% if selected_week_idx == 'all' %}selected{% endif %}>📅 全体表示</option>
                            {% for w_idx in range(weeks|length) %}
                            <option value="{{ w_idx }}" {% if selected_week_idx|string == w_idx|string %}selected{% endif %}>第{{ w_idx+1 }}週</option>
                            {% endfor %}
                        </select>
                    </form>
                </div>

                <!-- 管理画面用タブ切り替え -->
                <div class="admin-tab-menu no-print">
                    <button type="button" class="admin-tab-btn active" onclick="switchAdminTab('tab-shift-create', this)">📊 シフト作成・管理</button>
                    <button type="button" class="admin-tab-btn" onclick="switchAdminTab('tab-actual-work', this)">💰 勤怠実績・給料一覧</button>
                </div>

                <!-- タブ1: シフト作成・管理 -->
                <div id="tab-shift-create" class="admin-tab-content active">
                    <div class="no-print" style="display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 12px;">
                        <div class="card" style="flex: 1; min-width: 260px; margin-bottom: 0;">
                            <h3>バイトスタッフの管理</h3>
                            <p style="font-size: 10px; color: #888; margin-top:-4px;">タップして基本ポジション・時給・パスを変更</p>
                            <form id="addStaffForm" style="flex-direction: row; gap: 4px;">
                                <input type="text" id="new_staff_name" placeholder="スタッフ名" required style="flex: 1;">
                                <input type="number" id="new_staff_wage" placeholder="時給" value="1100" min="0" required style="width: 50px;">
                                <select id="new_staff_pos" style="flex: 1;">
                                    <option value="">基本なし</option>
                                    <option value="K (キッチン)">キッチン</option>
                                    <option value="F (フロント)">フロント</option>
                                    <option value="Y (やきば)">やきば</option>
                                </select>
                                <button type="submit" class="btn-green" style="padding: 5px 8px;">追加</button>
                            </form>
                            <div id="staffMsg" class="msg"></div>
                            
                            <div class="staff-grid">
                                {% for staff, info in staffs.items() %}
                                <div class="staff-chip">
                                    <button type="button" class="staff-name-btn" onclick="openPassModal('{{ staff }}', {{ info.hourly_wage }}, '{{ info.default_position }}')">
                                        🔑 {{ staff }} <span style="font-size: 9px; color: #D4AF37;">[{{ info.default_position.split(' ')[0] if info.default_position else '無' }}]</span>
                                    </button>
                                    <button onclick="deleteStaff('{{ staff }}')" class="btn-danger" style="padding: 1px 4px; font-size: 9px;">削除</button>
                                </div>
                                {% endfor %}
                            </div>
                        </div>

                        <div class="card" style="flex: 1; min-width: 260px; margin-bottom: 0;">
                            <h3>提出期限 & 人数設定</h3>
                            <div style="font-size: 10px; background: #262626; padding: 6px; border-radius: 4px; margin-bottom: 8px; border: 1px solid #333; color: #FFA726;">
                                <strong>📋 必要人数:</strong>
                                <div style="display: flex; flex-wrap: wrap; gap: 3px; margin-top: 3px;">
                                    {% for day, req in requirements.items() %}
                                    <span style="background: #1E1E1E; padding: 2px 4px; border: 1px solid #333; border-radius: 3px;">
                                        {{ day }}: K{{ req['K (キッチン)'] }} F{{ req['F (フロント)'] }} Y{{ req['Y (やきば)'] }}
                                    </span>
                                    {% endfor %}
                                </div>
                            </div>

                            <form id="deadlineForm" style="flex-direction: row; gap: 4px; align-items: center; margin-bottom: 8px;">
                                <label style="font-size: 10px;">締切:</label>
                                <input type="datetime-local" id="deadline_input" value="{{ deadline_val }}" style="flex: 1; font-size: 10px; padding: 4px;">
                                <button type="submit" style="padding: 4px 6px; font-size: 10px;">保存</button>
                            </form>

                            <form id="reqForm">
                                <select id="req_day" style="font-size: 11px; padding: 4px;">
                                    <option value="月">月曜日</option><option value="火">火曜日</option><option value="水">水曜日</option>
                                    <option value="木">木曜日</option><option value="金">金曜日</option><option value="土">土曜日</option><option value="日">日曜日</option>
                                </select>
                                <div style="display: flex; gap: 3px;">
                                    <input type="number" id="req_k" placeholder="K" min="0" required style="width: 100%; padding: 4px;">
                                    <input type="number" id="req_f" placeholder="F" min="0" required style="width: 100%; padding: 4px;">
                                    <input type="number" id="req_y" placeholder="Y" min="0" required style="width: 100%; padding: 4px;">
                                </div>
                                <button type="submit" style="padding: 5px; font-size: 11px;">人数保存</button>
                            </form>
                            <div id="reqMsg" class="msg"></div>
                        </div>
                    </div>

                    <div id="passModal" style="display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.8); z-index: 9999; justify-content: center; align-items: center;">
                        <div class="card" style="width: 280px;">
                            <h3>🔑 スタッフ情報変更</h3>
                            <p id="modalStaffLabel" style="font-size: 12px; color: #FFA726; margin-bottom: 8px;"></p>
                            
                            <label style="font-size: 11px; margin-bottom: 2px; display: block;">基本ポジション:</label>
                            <select id="modalNewPos" style="width: 100%; margin-bottom: 6px;">
                                <option value="">設定なし</option>
                                <option value="K (キッチン)">キッチン</option>
                                <option value="F (フロント)">フロント</option>
                                <option value="Y (やきば)">やきば</option>
                            </select>

                            <label style="font-size: 11px; margin-bottom: 2px; display: block;">時給 (円):</label>
                            <input type="number" id="modalNewWage" placeholder="時給" style="width: 100%; margin-bottom: 6px;" min="0">

                            <label style="font-size: 11px; margin-bottom: 2px; display: block;">新しいパスワード:</label>
                            <input type="text" id="modalNewPass" placeholder="未入力ならそのまま" style="width: 100%; margin-bottom: 12px;">

                            <div style="display: flex; gap: 8px; justify-content: flex-end;">
                                <button type="button" onclick="closePassModal()" style="background: #95a5a6; padding: 5px 10px;">キャンセル</button>
                                <button type="button" onclick="submitPassModal()" style="padding: 5px 10px;">保存</button>
                            </div>
                        </div>
                    </div>

                    <div class="wish-box no-print">
                        <div style="display: flex; justify-content: space-between; align-items: center;">
                            <h3 style="margin: 0; color: #2E7D32; font-size: 15px;">📥 スタッフ希望提出一覧</h3>
                            <button onclick="applyAllWishes()" class="btn-green" style="padding: 5px 10px; font-size: 11px;">📥 全希望を一括反映</button>
                        </div>
                        <p style="font-size: 11px; margin: 3px 0;">各希望の「反映」または一括反映で作業用シフトに流し込みます。（その後「確定・公開」ボタンを押すとスタッフに公開されます）</p>
                        <div style="max-height: 150px; overflow-y: auto;">
                            <table class="wish-table">
                                <thead>
                                    <tr><th>スタッフ</th><th>提出された希望</th></tr>
                                </thead>
                                <tbody>
                                    {% for staff in staffs.keys() %}
                                    <tr>
                                        <td><strong>{{ staff }}</strong></td>
                                        <td style="text-align: left; padding-left: 8px;">
                                            {% set staff_has_wish = false %}
                                            {% for w in wishes %}
                                                {% if w.staff_name == staff %}
                                                    {% set staff_has_wish = true %}
                                                    <span style="display: inline-block; background: #261111; padding: 2px 5px; margin: 2px; border-radius: 4px; border: 1px solid #333; color: #FFA726;">
                                                        {{ w.date.split('-')[1]|int }}/{{ w.date.split('-')[2]|int }}: 
                                                        <strong>{% if w.shift == '希望休' %}休{% else %}出勤{% endif %}</strong> 
                                                        {% if w.time and w.time != '-' %}({{ w.time }}){% endif %}
                                                        <button onclick="applyOneWish('{{ w.staff_name }}', '{{ w.date }}', '{{ w.shift }}', '{{ w.time }}')" style="padding: 1px 4px; font-size: 9px; background: #D4AF37; color: #121212; border-radius: 3px; cursor: pointer; margin-left: 3px;">反映</button>
                                                    </span>
                                                {% endif %}
                                            {% endfor %}
                                            {% if not staff_has_wish %}
                                                <span style="color: #666;">（未提出）</span>
                                            {% endif %}
                                        </td>
                                    </tr>
                                    {% endfor %}
                                </tbody>
                            </table>
                        </div>
                    </div>

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
                                    <td>
                                        {{ staff }}
                                        <br><small style="color: #888; font-weight: normal;">({{ s_info.default_position.split(' ')[0] if s_info.default_position else '無' }})</small>
                                    </td>
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

                                        <select class="shift-select" id="shift_{{ staff }}_{{ day_info.full_date }}" onchange="triggerUpdate('{{ staff }}', '{{ day_info.full_date }}')">
                                            <option value="-" {% if current_shift.val == '-' %}selected{% endif %}>-</option>
                                            <option value="K (キッチン)" {% if current_shift.val == 'K (キッチン)' %}selected{% endif %}>K</option>
                                            <option value="F (フロント)" {% if current_shift.val == 'F (フロント)' %}selected{% endif %}>F</option>
                                            <option value="Y (やきば)" {% if current_shift.val == 'Y (やきば)' %}selected{% endif %}>Y</option>
                                            <option value="希望休" {% if current_shift.val == '希望休' %}selected{% endif %}>休</option>
                                        </select>
                                        
                                        <div style="margin-top: 2px; display: flex; gap: 1px; justify-content: center; align-items: center; white-space: nowrap;">
                                            <select class="time-select" id="start_{{ staff }}_{{ day_info.full_date }}" onchange="triggerUpdate('{{ staff }}', '{{ day_info.full_date }}')" style="font-size: 9px; padding: 2px;">
                                                <option value="" {% if current_shift.start == '' %}selected{% endif %}>開始</option>
                                                {% for t in time_options %}
                                                <option value="{{ t }}" {% if current_shift.start == t %}selected{% endif %}>{{ t }}</option>
                                                {% endfor %}
                                            </select>
                                            <select class="time-select" id="end_{{ staff }}_{{ day_info.full_date }}" onchange="triggerUpdate('{{ staff }}', '{{ day_info.full_date }}')" style="font-size: 9px; padding: 2px;">
                                                <option value="" {% if current_shift.end == '' %}selected{% endif %}>終了</option>
                                                {% for t in time_options %}
                                                <option value="{{ t }}" {% if current_shift.end == t %}selected{% endif %}>{{ t }}</option>
                                                {% endfor %}
                                            </select>
                                        </div>
                                    </td>
                                    {% endfor %}
                                </tr>
                                {% endfor %}

                                <tr class="summary-row">
                                    <td>K人数 (実/要)</td>
                                    {% for day_info in days_list %}
                                        {% set k_count = namespace(val=0) %}
                                        {% for s in shifts %}
                                            {% if s.date == day_info.full_date and s.shift == 'K (キッチン)' %}
                                                {% set k_count.val = k_count.val + 1 %}
                                            {% endif %}
                                        {% endfor %}
                                        {% set req_k = requirements.get(day_info.weekday, {}).get('K (キッチン)', 0) %}
                                        <td class="{% if k_count.val < req_k %}insufficient{% endif %}">
                                            {{ k_count.val }} / {{ req_k }}
                                        </td>
                                    {% endfor %}
                                </tr>
                                <tr class="summary-row">
                                    <td>F人数 (実/要)</td>
                                    {% for day_info in days_list %}
                                        {% set f_count = namespace(val=0) %}
                                        {% for s in shifts %}
                                            {% if s.date == day_info.full_date and s.shift == 'F (フロント)' %}
                                                {% set f_count.val = f_count.val + 1 %}
                                            {% endif %}
                                        {% endfor %}
                                        {% set req_f = requirements.get(day_info.weekday, {}).get('F (フロント)', 0) %}
                                        <td class="{% if f_count.val < req_f %}insufficient{% endif %}">
                                            {{ f_count.val }} / {{ req_f }}
                                        </td>
                                    {% endfor %}
                                </tr>
                                <tr class="summary-row">
                                    <td>Y人数 (実/要)</td>
                                    {% for day_info in days_list %}
                                        {% set y_count = namespace(val=0) %}
                                        {% for s in shifts %}
                                            {% if s.date == day_info.full_date and s.shift == 'Y (やきば)' %}
                                                {% set y_count.val = y_count.val + 1 %}
                                            {% endif %}
                                        {% endfor %}
                                        {% set req_y = requirements.get(day_info.weekday, {}).get('Y (やきば)', 0) %}
                                        <td class="{% if y_count.val < req_y %}insufficient{% endif %}">
                                            {{ y_count.val }} / {{ req_y }}
                                        </td>
                                    {% endfor %}
                                </tr>
                            </tbody>
                        </table>
                    </div>
                    {% endmacro %}

                    {% if selected_week_idx == 'all' %}
                        <h2>📊 前半 (1日 〜 15日)</h2>
                        {{ render_table(first_half) }}
                        <h2>📊 後半 (16日 〜 月末)</h2>
                        {{ render_table(second_half) }}
                    {% else %}
                        {{ render_table(weeks[selected_week_idx|int]) }}
                    {% endif %}
                </div>

                <!-- タブ2: 勤怠実績・給料一覧 ＋ 日別打刻/勤務実績入力 -->
                <div id="tab-actual-work" class="admin-tab-content">
                    <div class="card" style="border-left-color: #27ae60;">
                        <h3>📝 勤務終わりに実働時間を入力・登録</h3>
                        <p style="font-size: 11px; color: #888; margin-top: -4px;">毎日の終わりに「誰が何時から何時まで入ったか」を入力します。</p>
                        
                        <form id="actualWorkForm" style="display: flex; flex-wrap: wrap; gap: 8px; align-items: flex-end;">
                            <div style="flex: 1; min-width: 120px;">
                                <label style="font-size: 10px; display: block; margin-bottom: 2px;">日付:</label>
                                <input type="date" id="act_date" required value="{{ today_date_str }}">
                            </div>

                            <div style="flex: 1; min-width: 120px;">
                                <label style="font-size: 10px; display: block; margin-bottom: 2px;">スタッフ名:</label>
                                <select id="act_staff" required>
                                    <option value="">-- スタッフ選択 --</option>
                                    {% for staff in staffs.keys() %}
                                    <option value="{{ staff }}">{{ staff }}</option>
                                    {% endfor %}
                                </select>
                            </div>

                            <div style="flex: 1; min-width: 100px;">
                                <label style="font-size: 10px; display: block; margin-bottom: 2px;">出勤ステータス:</label>
                                <select id="act_status" required>
                                    <option value="定時">定時</option>
                                    <option value="遅刻">遅刻</option>
                                    <option value="早退">早退</option>
                                    <option value="残業">残業</option>
                                    <option value="欠勤">欠勤</option>
                                </select>
                            </div>

                            <div style="flex: 1; min-width: 110px;">
                                <label style="font-size: 10px; display: block; margin-bottom: 2px;">開始時間:</label>
                                <select id="act_start_time">
                                    <option value="">未指定</option>
                                    {% for t in time_options %}
                                    <option value="{{ t }}">{{ t }}</option>
                                    {% endfor %}
                                </select>
                            </div>

                            <div style="flex: 1; min-width: 110px;">
                                <label style="font-size: 10px; display: block; margin-bottom: 2px;">終了時間:</label>
                                <select id="act_end_time">
                                    <option value="">未指定</option>
                                    {% for t in time_options %}
                                    <option value="{{ t }}">{{ t }}</option>
                                    {% endfor %}
                                </select>
                            </div>

                            <button type="submit" class="btn-green" style="padding: 7px 15px; font-size: 12px; height: 31px;">実績を保存</button>
                        </form>
                        <div id="actMsg" class="msg"></div>
                    </div>

                    <div class="card">
                        <h3>💰 スタッフ別 実働・給料一覧 ({{ month_title }})</h3>
                        <p style="font-size: 11px; color: #888; margin-bottom: 10px;">確定シフトおよび登録された日別実績に基づく集計です。</p>
                        <div class="table-responsive">
                            <table>
                                <thead>
                                    <tr>
                                        <th>スタッフ名</th>
                                        <th>基本ポジション</th>
                                        <th>時給</th>
                                        <th>出勤日数</th>
                                        <th>総労働時間</th>
                                        <th>概算給料</th>
                                    </tr>
                                </thead>
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
            let targetStaffForPass = '';

            function switchAdminTab(tabId, btnElement) {
                document.querySelectorAll('.admin-tab-content').forEach(function(content) { content.classList.remove('active'); });
                document.querySelectorAll('.admin-tab-btn').forEach(function(btn) { btn.classList.remove('active'); });
                const target = document.getElementById(tabId);
                if (target) { target.classList.add('active'); }
                if (btnElement) { btnElement.classList.add('active'); }
            }

            function openPassModal(name, currentWage, defaultPos) {
                targetStaffForPass = name;
                document.getElementById('modalStaffLabel').innerText = `「${name}」さんの情報変更`;
                document.getElementById('modalNewPass').value = '';
                document.getElementById('modalNewWage').value = currentWage;
                document.getElementById('modalNewPos').value = defaultPos || '';
                document.getElementById('passModal').style.display = 'flex';
            }

            function closePassModal() {
                document.getElementById('passModal').style.display = 'none';
            }

            function submitPassModal() {
                const newPass = document.getElementById('modalNewPass').value;
                const newWage = document.getElementById('modalNewWage').value;
                const newPos = document.getElementById('modalNewPos').value;

                fetch('/api/update_staff_info', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ staff_name: targetStaffForPass, new_password: newPass, new_wage: newWage, default_position: newPos })
                })
                .then(res => res.json())
                .then(data => {
                    alert(data.message);
                    closePassModal();
                    if(data.success) location.reload();
                });
            }

            function generateAutoShift() {
                if(confirm('選択期間の自動シフト作成を開始しますか？')) {
                    fetch('/api/generate_auto_shift', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ year: {{ current_year }}, month: {{ current_month }}, unit: '{{ selected_week_idx }}' })
                    })
                    .then(res => res.json())
                    .then(data => {
                        alert(data.message);
                        if(data.success) location.reload();
                    });
                }
            }

            function publishShifts() {
                if(confirm('現在のシフト内容を確定し、スタッフ画面に公開しますか？')) {
                    fetch('/api/publish_shifts', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' }
                    })
                    .then(res => res.json())
                    .then(data => {
                        alert(data.message);
                        if(data.success) location.reload();
                    });
                }
            }

            document.getElementById('deadlineForm').addEventListener('submit', function(e) {
                e.preventDefault();
                const deadline = document.getElementById('deadline_input').value;
                fetch('/api/update_deadline', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ deadline })
                })
                .then(res => res.json())
                .then(data => alert(data.message));
            });

            document.getElementById('addStaffForm').addEventListener('submit', function(e) {
                e.preventDefault();
                const name = document.getElementById('new_staff_name').value;
                const wage = document.getElementById('new_staff_wage').value;
                const pos = document.getElementById('new_staff_pos').value;
                fetch('/api/add_staff', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name, wage, default_position: pos }) })
                .then(res => res.json()).then(data => { alert(data.message); if(data.success) location.reload(); });
            });

            function deleteStaff(name) {
                if(confirm(`「${name}」さんを削除しますか？`)) {
                    fetch('/api/delete_staff', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }) })
                    .then(res => res.json()).then(data => { alert(data.message); if(data.success) location.reload(); });
                }
            }

            document.getElementById('reqForm').addEventListener('submit', function(e) {
                e.preventDefault();
                const day = document.getElementById('req_day').value;
                const req_k = document.getElementById('req_k').value;
                const req_f = document.getElementById('req_f').value;
                const req_y = document.getElementById('req_y').value;
                fetch('/api/update_requirement', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ day, req_k, req_f, req_y }) })
                .then(res => res.json()).then(data => { alert(data.message); if(data.success) location.reload(); });
            });

            function triggerUpdate(staff_name, date) {
                const shift = document.getElementById(`shift_${staff_name}_${date}`).value;
                const startTime = document.getElementById(`start_${staff_name}_${date}`).value;
                const endTime = document.getElementById(`end_${staff_name}_${date}`).value;
                
                let time = '';
                if(startTime || endTime) {
                    time = `${startTime}-${endTime}`;
                }

                fetch('/api/update_shift', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ staff_name, date, shift, time })
                }).then(res => res.json());
            }

            function applyOneWish(staff_name, date, shift, time) {
                if(confirm(`${staff_name}さんの${date}の希望を反映させますか？`)) {
                    fetch('/api/apply_one_wish', {
                        method: 'POST', headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ staff_name, date, shift, time })
                    }).then(res => res.json()).then(data => {
                        alert(data.message);
                        location.reload();
                    });
                }
            }

            function applyAllWishes() {
                if(confirm('提出されているすべての希望シフトを作業用シフトに反映しますか？')) {
                    fetch('/api/apply_all_wishes', {
                        method: 'POST', headers: { 'Content-Type': 'application/json' }
                    }).then(res => res.json()).then(data => {
                        alert(data.message);
                        location.reload();
                    });
                }
            }

            document.getElementById('actualWorkForm').addEventListener('submit', function(e) {
                e.preventDefault();
                const date = document.getElementById('act_date').value;
                const staff_name = document.getElementById('act_staff').value;
                const status = document.getElementById('act_status').value;
                const start = document.getElementById('act_start_time').value;
                const end = document.getElementById('act_end_time').value;

                let timeStr = '';
                if (start || end) {
                    timeStr = `${start}-${end}`;
                }

                fetch('/api/update_actual_shift', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ staff_name, date, status, time: timeStr })
                })
                .then(res => res.json())
                .then(data => {
                    document.getElementById('actMsg').innerText = data.message;
                    if (data.success) {
                        setTimeout(() => location.reload(), 1000);
                    }
                });
            });
        </script>
    {% endif %}
</body>
</html>
"""

# --- 設定画面用テンプレート ---
SETTINGS_TEMPLATE = """
<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>⚙️ 管理者設定 | {{ shop_name }}</title>
    <style>
        body { font-family: 'Noto Sans JP', sans-serif; margin: 0; background-color: #121212; color: #E0E0E0; display: flex; height: 100vh; }
        
        header { background-color: #1E1E1E; padding: 10px 20px; display: flex; justify-content: space-between; align-items: center; border-bottom: 2px solid #D4AF37; color: #E0E0E0; position: fixed; top: 0; left: 0; width: 100%; z-index: 1000; box-sizing: border-box; }
        header h1 { display: flex; align-items: center; gap: 10px; margin: 0; font-size: 18px; color: #D4AF37; }

        .container { display: flex; gap: 15px; padding: 75px 15px 15px 15px; flex: 1; }
        
        aside {
            width: 180px; background-color: #1E1E1E; padding: 15px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.5);
            background-image: url('data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIzMCIgaGVpZ2h0PSIzMCIgdmlld0JveD0iMCAwIDMwIDMwIj48ZyBmaWxsPSIjRDRBRjM3IiBmaWxsLW9wYWNpdHk9IjAuMDUiPjxwYXRo dD0iTS0w IDE1bDE1LTE1TDMwIDE1bC0xNSAxNUww IDE1eiIvPjwvZz48L3N2Zz4=');
        }
        aside h2 { font-size: 15px; color: #D4AF37; border-bottom: 1px solid #D4AF37; padding-bottom: 8px; margin-top: 0; }
        aside ul { list-style: none; padding: 0; margin: 10px 0 0 0; }
        aside ul li { margin-bottom: 8px; }
        aside ul li a { text-decoration: none; color: #E0E0E0; font-size: 13px; display: block; padding: 4px; border-radius: 4px; transition: background-color 0.3s; }
        aside ul li a.active { background-color: #D4AF37; color: #121212; font-weight: bold; }
        aside ul li a:hover:not(.active) { background-color: #333333; }
        
        main { flex: 1; padding: 0 10px 10px 10px; display: flex; justify-content: center; align-items: flex-start; }
        
        .card { 
            background: #1E1E1E; padding: 25px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.5); width: 100%; max-width: 350px;
            border-left: 5px solid #D4AF37;
            background-image: url('data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIzMCIgaGVpZ2h0PSIzMCIgdmlld0JveD0iMCAwIDMwIDMwIj48ZyBmaWxsPSIjRDRBRjM3IiBmaWxsLW9wYWNpdHk9IjAuMDUiPjxwYXRo dD0iTS0w IDE1bDE1LTE1TDMwIDE1bC0xNSAxNUww IDE1eiIvPjwvZz48L3N2Zz4=');
        }
        .card h3 { color: #D4AF37; font-size: 16px; text-align: center; margin-top: 0; margin-bottom: 15px; }
        
        form { display: flex; flex-direction: column; gap: 10px; }
        input { padding: 8px; border: 1px solid #333; border-radius: 4px; font-size: 13px; background: #262626; color: #E0E0E0; }
        input:focus { border-color: #D4AF37; outline: none; }
        
        button { padding: 8px; background-color: #D4AF37; color: #121212; border: none; border-radius: 4px; font-weight: bold; cursor: pointer; }
        button:hover { background-color: #C09A32; }
        .back-link { display: block; text-align: center; margin-top: 12px; color: #FFA726; text-decoration: none; font-weight: bold; font-size: 12px; }
        .msg { margin-top: 8px; text-align: center; font-weight: bold; color: #2E7D32; font-size: 12px; }
    </style>
</head>
<body>
    <header>
        <h1><strong>{{ shop_name }}</strong> 管理者設定</h1>
        <a href="/logout"><button class="btn-danger" style="padding: 5px 10px; font-size: 11px; background: #D32F2F; color: #FFFFFF; border: none; border-radius: 4px; font-weight: bold; cursor: pointer;">ログアウト</button></a>
    </header>

    <div class="container">
        <aside>
            <h2>🖥️ 管理メニュー</h2>
            <ul>
                <li><a href="/">📊 シフト作成</a></li>
                <li><a href="/admin/settings" class="active">⚙️ 設定・パス変更</a></li>
            </ul>
        </aside>

        <main>
            <div class="card">
                <h3>⚙️ パスワード変更</h3>
                <p style="text-align: center; font-size: 11px; color: #FFA726; margin-bottom: 8px;">新しい管理者パスワードを入力してください。</p>
                <form id="passwordForm">
                    <input type="password" id="new_password" placeholder="新しいパスワード" required>
                    <button type="submit">パスワードを変更する</button>
                </form>
                <div id="passMsg" class="msg"></div>
                <a href="/" class="back-link">← 管理メインに戻る</a>
            </div>
        </main>
    </div>

    <script>
        document.getElementById('passwordForm').addEventListener('submit', function(e) {
            e.preventDefault();
            const new_password = document.getElementById('new_password').value;
            fetch('/api/update_password', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ new_password }) })
            .then(res => res.json()).then(data => { document.getElementById('passMsg').innerText = data.message; });
        });
    </script>
</body>
</html>
"""

# --- スタッフ用ポータル画面テンプレート ---
STAFF_TEMPLATE = """
<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ short_name }} | スタッフシフトポータル</title>
    <style>
        body { font-family: 'Noto Sans JP', sans-serif; margin: 0; background-color: #121212; color: #E0E0E0; display: flex; flex-direction: column; height: 100vh; }
        
        header { 
            background-color: #1E1E1E; 
            padding: 10px 20px; 
            display: flex; 
            justify-content: space-between; 
            align-items: center; 
            border-bottom: 2px solid #D4AF37; 
            color: #E0E0E0; 
            position: fixed; top: 0; left: 0; width: 100%; z-index: 1000;
            box-sizing: border-box;
        }
        header h1 { display: flex; align-items: center; gap: 10px; margin: 0; font-size: 18px; color: #D4AF37; }

        .card { 
            background: #1E1E1E; padding: 20px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.5); 
            max-width: 480px; margin: 75px auto 20px auto; width: 95%; box-sizing: border-box;
            border-left: 5px solid #D4AF37;
            background-image: url('data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIzMCIgaGVpZ2h0PSIzMCIgdmlld0JveD0iMCAwIDMwIDMwIj48ZyBmaWxsPSIjRDRBRjM3IiBmaWxsLW9wYWNpdHk9IjAuMDUiPjxwYXRo dD0iTS0w IDE1bDE1LTE1TDMwIDE1bC0xNSAxNUww IDE1eiIvPjwvZz48L3N2Zz4=');
        }
        .card h2 { color: #D4AF37; font-size: 17px; text-align: center; margin-top: 0; border-bottom: 1px solid #333; padding-bottom: 8px; }
        
        .tab-menu { display: flex; gap: 4px; margin-bottom: 12px; border-bottom: 1px solid #333; padding-bottom: 8px; }
        .tab-btn { flex: 1; padding: 7px 4px; background: #262626; border: none; border-radius: 4px; font-weight: bold; cursor: pointer; text-align: center; font-size: 11px; color: #E0E0E0; }
        .tab-btn.active { background: #D4AF37; color: #121212; }
        
        .tab-content { display: none; }
        .tab-content.active { display: block; }

        .day-row { display: flex; flex-direction: column; padding: 8px; border-bottom: 1px solid #333; gap: 5px; font-size: 12px; background: #1E1E1E; border-radius: 4px; margin-bottom: 4px; }
        .day-header { display: flex; justify-content: space-between; align-items: center; }
        .day-label { font-weight: bold; color: #D4AF37; }
        .weekend { color: #D32F2F; }

        select, input { padding: 5px; font-size: 12px; border-radius: 4px; border: 1px solid #333; background: #262626; color: #E0E0E0; width: 100%; box-sizing: border-box; }
        select:focus, input:focus { border-color: #D4AF37; outline: none; }
        
        .time-select-grid { display: flex; gap: 4px; width: 100%; }
        .time-select-grid select { flex: 1; }

        button.submit-btn { width: 100%; padding: 10px; background-color: #2E7D32; color: #FFFFFF; border: none; border-radius: 4px; font-weight: bold; font-size: 14px; margin-top: 15px; cursor: pointer; }
        button.submit-btn:hover { background-color: #1E5B23; }
        button.submit-btn:disabled { background-color: #444; color: #888; cursor: not-allowed; }
        
        .msg { margin-top: 12px; text-align: center; font-weight: bold; color: #FFA726; font-size: 12px; }
        .error-msg { margin-top: 8px; text-align: center; font-weight: bold; color: #D32F2F; font-size: 11px; }
        
        .work-summary { background: #262626; padding: 10px; border-radius: 6px; margin-top: 8px; border-left: 4px solid #2E7D32; line-height: 1.5; color: #E0E0E0; font-size: 12px; }
        .staff-table { width: 100%; border-collapse: collapse; margin-top: 8px; font-size: 11px; color: #E0E0E0; }
        .staff-table th, .staff-table td { border: 1px solid #333; padding: 5px; text-align: center; }
        .staff-table th { background-color: #262626; color: #D4AF37; }
        .clickable-date { color: #FFA726; text-decoration: underline; cursor: pointer; }

        .member-modal { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.8); z-index: 9999; justify-content: center; align-items: center; }
        .member-modal-content { background: #1E1E1E; padding: 18px; border-radius: 8px; width: 280px; box-shadow: 0 4px 10px rgba(0,0,0,0.5); max-height: 80vh; display: flex; flex-direction: column; }
        .member-modal-h3 { margin-top: 0; font-size: 15px; color: #D4AF37; border-bottom: 2px solid #D4AF37; padding-bottom: 5px; }
        .member-list-container { overflow-y: auto; flex: 1; margin: 8px 0; }
        .member-chip { display: flex; justify-content: space-between; align-items: center; background: #262626; border: 1px solid #333; padding: 6px; border-radius: 4px; margin-bottom: 5px; font-size: 12px; }
    </style>
</head>
<body>
    <header>
        <h1>{{ short_name }} ポータル</h1>
        <a href="/logout"><button style="padding: 4px 8px; font-size: 10px; background: #D32F2F; color: #FFFFFF; border: none; border-radius: 4px; font-weight: bold;">ログアウト</button></a>
    </header>

    <div class="card">
        <form method="POST" action="/staff_login" style="margin-bottom: 12px;">
            <label style="font-size: 11px; color: #E0E0E0; margin-bottom: 3px; display: block;"><strong>名前を選択:</strong></label>
            <select name="name" style="padding: 6px;" required>
                <option value="">-- 名前を選んでください --</option>
                {% for staff in staffs.keys() %}
                <option value="{{ staff }}" {% if staff == selected_staff %}selected{% endif %}>{{ staff }}</option>
                {% endfor %}
            </select>
            
            <div style="margin-top: 6px; display: flex; gap: 4px;">
                <input type="password" name="staff_password" placeholder="パスワード(初期: 0000)" style="flex: 1; padding: 6px;" required>
                <button type="submit" style="padding: 6px 10px;">ログイン</button>
            </div>
            {% if auth_error %}
            <div class="error-msg">パスワードが違います。</div>
            {% endif %}
        </form>

        {% if logged_in and selected_staff %}
        <!-- 年月切り替えフォーム -->
        <form method="GET" action="/staff" style="display: flex; gap: 4px; margin-bottom: 12px; flex-direction: row;">
            <select name="year" onchange="this.form.submit()" style="flex: 1; padding: 4px; font-size: 11px;">
                <option value="2025" {% if current_year == 2025 %}selected{% endif %}>2025年</option>
                <option value="2026" {% if current_year == 2026 %}selected{% endif %}>2026年</option>
                <option value="2027" {% if current_year == 2027 %}selected{% endif %}>2027年</option>
            </select>
            <select name="month" onchange="this.form.submit()" style="flex: 1; padding: 4px; font-size: 11px;">
                {% for m in range(1, 13) %}
                <option value="{{ m }}" {% if current_month == m %}selected{% endif %}>{{ m }}月</option>
                {% endfor %}
            </select>
        </form>

        <div class="tab-menu">
            <button type="button" class="tab-btn active" onclick="switchTab('tab-holiday', this)">希望申請</button>
            <button type="button" class="tab-btn" onclick="switchTab('tab-time', this)">確定シフト</button>
            <button type="button" class="tab-btn" onclick="switchTab('tab-actual', this)">実働・給料</button>
        </div>

        <div id="tab-holiday" class="tab-content active">
            <h2>📅 希望入力 ({{ current_year }}年{{ current_month }}月)</h2>
            <p style="font-size: 10px; text-align: center; color: #FFA726; margin-bottom: 6px;">
                <strong>⏰ 提出期限:</strong> {{ deadline_str }}
                {% if is_deadline_passed %}<br><span style="color: #D32F2F; font-weight: bold;">⚠️ 期限終了。変更できません。</span>{% endif %}
            </p>

            <form id="bulkSubmitForm">
                <input type="hidden" id="staff_name" value="{{ selected_staff }}">
                <div id="daysList">
                    {% for day_info in all_days %}
                    {% set cur_wish = staff_wishes.get(day_info.full_date, {}) %}
                    {% set cur_shift = cur_wish.get('shift', '-') %}
                    {% set cur_time = cur_wish.get('time', '') %}
                    {% set parts = cur_time.split('-') if '-' in cur_time else ['', ''] %}

                    <div class="day-row" data-date="{{ day_info.full_date }}">
                        <div class="day-header">
                            <span class="day-label {% if day_info.is_weekend %}weekend{% endif %}">{{ day_info.date_str }}({{ day_info.weekday }})</span>
                            <select class="shift-select" style="width: 100px;" onchange="toggleTimeVisibility(this)" {% if is_deadline_passed %}disabled{% endif %}>
                                <option value="-" {% if cur_shift == '-' %}selected{% endif %}>未選択</option>
                                <option value="出勤希望" {% if cur_shift == '出勤希望' %}selected{% endif %}>出勤希望</option>
                                <option value="希望休" {% if cur_shift == '希望休' %}selected{% endif %}>希望休</option>
                            </select>
                        </div>
                        
                        <div class="time-select-grid" id="time_grid_{{ day_info.full_date }}" style="{% if cur_shift == '希望休' %}display: none;{% endif %}">
                            <select class="start-select" {% if is_deadline_passed %}disabled{% endif %}>
                                <option value="" {% if parts[0] == '' %}selected{% endif %}>開始（指定なし）</option>
                                {% for t in time_options %}
                                <option value="{{ t }}" {% if parts[0] == t %}selected{% endif %}>{{ t }}</option>
                                {% endfor %}
                            </select>
                            <span style="display: flex; align-items: center; color: #888;">〜</span>
                            <select class="end-select" {% if is_deadline_passed %}disabled{% endif %}>
                                <option value="" {% if parts[1] == '' %}selected{% endif %}>終了（指定なし）</option>
                                {% for t in time_options %}
                                <option value="{{ t }}" {% if parts[1] == t %}selected{% endif %}>{{ t }}</option>
                                {% endfor %}
                            </select>
                        </div>
                    </div>
                    {% endfor %}
                </div>
                <button type="submit" class="submit-btn" {% if is_deadline_passed %}disabled style="background-color: #444;"{% endif %}>
                    {% if is_deadline_passed %}期限終了（ロック中）{% else %}一括して希望を提出する{% endif %}
                </button>
            </form>
            <div id="submitMsg" class="msg"></div>
        </div>

        <div id="tab-time" class="tab-content">
            <h2>⏰ 確定シフト ({{ current_year }}年{{ current_month }}月)</h2>
            <p style="font-size: 10px; color: #888; text-align: center; margin-bottom: 6px;">日付タップで出勤メンバー確認。</p>
            <table class="staff-table">
                <thead>
                    <tr><th>日付</th><th>曜日</th><th>シフト</th><th>時間</th></tr>
                </thead>
                <tbody>
                    {% for day_info in all_days %}
                    {% set s_data = staff_shifts.get(day_info.full_date, {}) %}
                    {% if s_data.get('shift') and s_data.get('shift') != '-' %}
                    <tr>
                        <td>
                            <span class="clickable-date" onclick="showDailyMembers('{{ day_info.full_date }}', '{{ day_info.date_str }}({{ day_info.weekday }})')">
                                {{ day_info.date_str }}
                            </span>
                        </td>
                        <td class="{% if day_info.is_weekend %}weekend{% endif %}">{{ day_info.weekday }}</td>
                        <td><strong>{{ s_data.get('shift').split(' ')[0] }}</strong></td>
                        <td>{{ s_data.get('time_display') }}</td>
                    </tr>
                    {% endif %}
                    {% endfor %}
                </tbody>
            </table>
        </div>

        <div id="tab-actual" class="tab-content">
            <h2>📊 実働・給料 ({{ current_year }}年{{ current_month }}月)</h2>
            <div class="work-summary">
                <p><strong>出勤日数:</strong> {{ total_shift_days }} 日</p>
                <p><strong>総時間:</strong> {{ total_hours }} 時間</p>
                <p><strong>時給:</strong> {{ staff_wage }} 円</p>
                <p style="font-size: 14px; color: #FFA726; margin-top: 4px; font-weight: bold;"><strong>概算給料: ￥{{ "{:,}".format(est_pay) }}</strong></p>
            </div>

            <h4 style="font-size: 12px; margin-top: 12px; color: #E0E0E0; text-align: center;">📅 勤務詳細</h4>
            <table class="staff-table">
                <thead>
                    <tr><th>日付</th><th>シフト</th><th>時間</th><th>実働</th></tr>
                </thead>
                <tbody>
                    {% for detail in daily_work_details %}
                    <tr {% if not detail.is_working %}style="color: #666;"{% endif %}>
                        <td>{{ detail.date }}({{ detail.weekday }})</td>
                        <td>{{ detail.shift }}</td>
                        <td>{{ detail.time_display }}</td>
                        <td>{% if detail.is_working %}<strong>{{ detail.hours }}h</strong>{% else %}-{% endif %}</td>
                    </tr>
                    {% else %}
                    <tr><td colspan="4" style="color: #666;">データがありません</td></tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>

        <div id="memberModal" class="member-modal">
            <div class="member-modal-content">
                <h3 id="modalDateLabel" class="member-modal-h3">👥 出勤メンバー</h3>
                <div id="memberListContainer" class="member-list-container">
                    <p style="text-align: center; color: #666;">読み込み中...</p>
                </div>
                <div style="text-align: right;">
                    <button type="button" onclick="closeMemberModal()" style="background: #95a5a6; color: #121212; border: none; padding: 5px 10px; border-radius: 4px; font-weight: bold; font-size: 11px;">閉じる</button>
                </div>
            </div>
        </div>

        <script>
            function toggleTimeVisibility(selectElem) {
                const row = selectElem.closest('.day-row');
                const timeGrid = row.querySelector('.time-select-grid');
                if (selectElem.value === '希望休') {
                    timeGrid.style.display = 'none';
                } else {
                    timeGrid.style.display = 'flex';
                }
            }

            function switchTab(tabId, btnElement) {
                document.querySelectorAll('.tab-content').forEach(function(content) { content.classList.remove('active'); });
                document.querySelectorAll('.tab-btn').forEach(function(btn) { btn.classList.remove('active'); });
                const target = document.getElementById(tabId);
                if (target) { target.classList.add('active'); }
                if (btnElement) { btnElement.classList.add('active'); }
            }

            function showDailyMembers(date, dateStr) {
                document.getElementById('modalDateLabel').innerText = `👥 ${dateStr} の出勤メンバー`;
                const container = document.getElementById('memberListContainer');
                container.innerHTML = '<p style="text-align: center; color: #666;">読み込み中...</p>';
                document.getElementById('memberModal').style.display = 'flex';

                fetch(`/api/get_daily_members?date=${date}`)
                .then(res => res.json())
                .then(data => {
                    if (data.members.length === 0) {
                        container.innerHTML = '<p style="text-align: center; color: #666;">出勤メンバーはいません。</p>';
                    } else {
                        let html = '';
                        data.members.forEach(m => {
                            html += `<div class="member-chip"><div><strong>${m.staff_name}</strong></div><div style="color:#D4AF37;">(${m.shift.split(' ')[0]}) ${m.time_display}</div></div>`;
                        });
                        container.innerHTML = html;
                    }
                });
            }

            function closeMemberModal() { document.getElementById('memberModal').style.display = 'none'; }

            document.getElementById('bulkSubmitForm').addEventListener('submit', function(e) {
                e.preventDefault();
                if(!confirm('希望シフトを一括提出しますか？')) return;
                const staff_name = document.getElementById('staff_name').value;
                const shifts = {};
                
                document.querySelectorAll('.day-row').forEach(function(row) {
                    const date = row.getAttribute('data-date');
                    const shiftVal = row.querySelector('.shift-select').value;
                    const startVal = row.querySelector('.start-select').value;
                    const endVal = row.querySelector('.end-select').value;
                    
                    let timeStr = '';
                    if (shiftVal !== '希望休') {
                        timeStr = `${startVal}-${endVal}`;
                    }
                    
                    shifts[date] = { 'shift': shiftVal, 'time': timeStr };
                });

                fetch('/api/submit_wish_shifts', {
                    method: 'POST', 
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ staff_name, shifts })
                })
                .then(res => res.json())
                .then(data => { document.getElementById('submitMsg').innerText = data.message; })
                .catch(err => { document.getElementById('submitMsg').innerText = '通信エラーが発生しました'; });
            });
        </script>
        {% endif %}
    </div>
</body>
</html>
"""

# --- 設定画面用テンプレート ---
SETTINGS_TEMPLATE = """
<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>⚙️ 管理者設定 | {{ shop_name }}</title>
    <style>
        body { font-family: 'Noto Sans JP', sans-serif; margin: 0; background-color: #121212; color: #E0E0E0; display: flex; height: 100vh; }
        
        header { background-color: #1E1E1E; padding: 10px 20px; display: flex; justify-content: space-between; align-items: center; border-bottom: 2px solid #D4AF37; color: #E0E0E0; position: fixed; top: 0; left: 0; width: 100%; z-index: 1000; box-sizing: border-box; }
        header h1 { display: flex; align-items: center; gap: 10px; margin: 0; font-size: 18px; color: #D4AF37; }

        .container { display: flex; gap: 15px; padding: 75px 15px 15px 15px; flex: 1; }
        
        aside {
            width: 180px; background-color: #1E1E1E; padding: 15px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.5);
            background-image: url('data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIzMCIgaGVpZ2h0PSIzMCIgdmlld0JveD0iMCAwIDMwIDMwIj48ZyBmaWxsPSIjRDRBRjM3IiBmaWxsLW9wYWNpdHk9IjAuMDUiPjxwYXRo dD0iTS0w IDE1bDE1LTE1TDMwIDE1bC0xNSAxNUww IDE1eiIvPjwvZz48L3N2Zz4=');
        }
        aside h2 { font-size: 15px; color: #D4AF37; border-bottom: 1px solid #D4AF37; padding-bottom: 8px; margin-top: 0; }
        aside ul { list-style: none; padding: 0; margin: 10px 0 0 0; }
        aside ul li { margin-bottom: 8px; }
        aside ul li a { text-decoration: none; color: #E0E0E0; font-size: 13px; display: block; padding: 4px; border-radius: 4px; transition: background-color 0.3s; }
        aside ul li a.active { background-color: #D4AF37; color: #121212; font-weight: bold; }
        aside ul li a:hover:not(.active) { background-color: #333333; }
        
        main { flex: 1; padding: 0 10px 10px 10px; display: flex; justify-content: center; align-items: flex-start; }
        
        .card { 
            background: #1E1E1E; padding: 25px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.5); width: 100%; max-width: 350px;
            border-left: 5px solid #D4AF37;
            background-image: url('data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIzMCIgaGVpZ2h0PSIzMCIgdmlld0JveD0iMCAwIDMwIDMwIj48ZyBmaWxsPSIjRDRBRjM3IiBmaWxsLW9wYWNpdHk9IjAuMDUiPjxwYXRo dD0iTS0w IDE1bDE1LTE1TDMwIDE1bC0xNSAxNUww IDE1eiIvPjwvZz48L3N2Zz4=');
        }
        .card h3 { color: #D4AF37; font-size: 16px; text-align: center; margin-top: 0; margin-bottom: 15px; }
        
        form { display: flex; flex-direction: column; gap: 10px; }
        input { padding: 8px; border: 1px solid #333; border-radius: 4px; font-size: 13px; background: #262626; color: #E0E0E0; }
        input:focus { border-color: #D4AF37; outline: none; }
        
        button { padding: 8px; background-color: #D4AF37; color: #121212; border: none; border-radius: 4px; font-weight: bold; cursor: pointer; }
        button:hover { background-color: #C09A32; }
        .back-link { display: block; text-align: center; margin-top: 12px; color: #FFA726; text-decoration: none; font-weight: bold; font-size: 12px; }
        .msg { margin-top: 8px; text-align: center; font-weight: bold; color: #2E7D32; font-size: 12px; }
    </style>
</head>
<body>
    <header>
        <h1><strong>{{ shop_name }}</strong> 管理者設定</h1>
        <a href="/logout"><button class="btn-danger" style="padding: 5px 10px; font-size: 11px; background: #D32F2F; color: #FFFFFF; border: none; border-radius: 4px; font-weight: bold; cursor: pointer;">ログアウト</button></a>
    </header>

    <div class="container">
        <aside>
            <h2>🖥️ 管理メニュー</h2>
            <ul>
                <li><a href="/">📊 シフト作成</a></li>
                <li><a href="/admin/settings" class="active">⚙️ 設定・パス変更</a></li>
            </ul>
        </aside>

        <main>
            <div class="card">
                <h3>⚙️ パスワード変更</h3>
                <p style="text-align: center; font-size: 11px; color: #FFA726; margin-bottom: 8px;">新しい管理者パスワードを入力してください。</p>
                <form id="passwordForm">
                    <input type="password" id="new_password" placeholder="新しいパスワード" required>
                    <button type="submit">パスワードを変更する</button>
                </form>
                <div id="passMsg" class="msg"></div>
                <a href="/" class="back-link">← 管理メインに戻る</a>
            </div>
        </main>
    </div>

    <script>
        document.getElementById('passwordForm').addEventListener('submit', function(e) {
            e.preventDefault();
            const new_password = document.getElementById('new_password').value;
            fetch('/api/update_password', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ new_password }) })
            .then(res => res.json()).then(data => { document.getElementById('passMsg').innerText = data.message; });
        });
    </script>
</body>
</html>
"""

# --- ルーティング ---
@app.route('/')
def index():
    is_admin = session.get('is_admin', False)
    today = datetime.today()
    year = int(request.args.get('year', today.year))
    month = int(request.args.get('month', today.month))
    week_idx = request.args.get('week_idx', 'all')
    
    first_half, second_half, month_title = get_month_days(year, month)
    weeks = get_month_weeks(year, month)
    
    deadline_val = deadline_settings['deadline'].replace(' ', 'T') if deadline_settings['deadline'] else ''

    admin_staff_summaries = {}
    all_days_for_summary = first_half + second_half
    today_str = today.strftime('%Y-%m-%d')

    for staff_name, s_info in staffs_db.items():
        staff_wage = s_info.get('hourly_wage', 1100)
        total_mins = 0
        total_days = 0

        for day_info in all_days_for_summary:
            date = day_info['full_date']
            if date > today_str:
                continue

            s = next((item for item in published_shifts_data if item['staff_name'] == staff_name and item['date'] == date), None)
            shift_val = s['shift'] if s else '-'
            shift_time = s['time'] if s else ''

            act = next((item for item in actual_shifts_data if item['staff_name'] == staff_name and item['date'] == date), None)

            is_working = False
            time_range = '-'

            if act:
                if act['status'] != '欠勤':
                    is_working = True
                    time_range = act['time']
            else:
                if shift_val and shift_val != '-' and shift_val != '希望休':
                    is_working = True
                    time_range = shift_time if shift_time else '-'

            if is_working:
                total_days += 1
                if '-' in time_range:
                    try:
                        parts = time_range.split('-')
                        start_s, end_s = parts[0], parts[1] if len(parts) > 1 else ''
                        if start_s and end_s:
                            sh, sm = map(int, start_s.split(':'))
                            eh, em = map(int, end_s.split(':'))
                            mins = (eh * 60 + em) - (sh * 60 + sm)
                            if mins > 0:
                                total_mins += mins
                            else:
                                total_mins += 300
                        else:
                            total_mins += 300
                    except:
                        total_mins += 300
                else:
                    total_mins += 300

        total_hours = round(total_mins / 60.0, 1)
        est_pay = int(total_hours * staff_wage)
        admin_staff_summaries[staff_name] = {
            'days': total_days,
            'hours': total_hours,
            'pay': est_pay
        }

    return render_template_string(
        HTML_TEMPLATE,
        shop_name=SHOP_NAME,
        is_admin=is_admin,
        staffs=staffs_db,
        requirements=required_settings,
        first_half=first_half,
        second_half=second_half,
        weeks=weeks,
        selected_week_idx=week_idx,
        wishes=wishes_data,
        shifts=shifts_data,
        time_options=TIME_OPTIONS,
        month_title=month_title,
        current_year=year,
        current_month=month,
        today_date_str=today_str,
        deadline_val=deadline_val,
        admin_staff_summaries=admin_staff_summaries,
        login_error=False
    )

@app.route('/admin_login', methods=['POST'])
def admin_login():
    password = request.form.get('password')
    if password == admin_credentials['password']:
        session['is_admin'] = True
        return redirect(url_for('index'))
    
    today = datetime.today()
    first_half, second_half, month_title = get_month_days(today.year, today.month)
    weeks = get_month_weeks(today.year, today.month)
    deadline_val = deadline_settings['deadline'].replace(' ', 'T') if deadline_settings['deadline'] else ''

    return render_template_string(
        HTML_TEMPLATE, shop_name=SHOP_NAME, is_admin=False, staffs=staffs_db, requirements=required_settings,
        first_half=first_half, second_half=second_half, weeks=weeks, selected_week_idx='all', wishes=wishes_data, shifts=shifts_data, time_options=TIME_OPTIONS, month_title=month_title,
        current_year=today.year, current_month=today.month, today_date_str=today.strftime('%Y-%m-%d'), deadline_val=deadline_val, admin_staff_summaries={}, login_error=True
    )

@app.route('/admin/settings')
def admin_settings():
    if not session.get('is_admin', False):
        return redirect(url_for('index'))
    return render_template_string(SETTINGS_TEMPLATE, shop_name=SHOP_NAME)

@app.route('/logout')
def logout():
    session.pop('is_admin', None)
    session.pop('logged_staff', None)
    return redirect(url_for('index'))

@app.route('/staff')
def staff_page():
    selected_staff = session.get('logged_staff', '')
    logged_in = bool(selected_staff)
    
    today = datetime.today()
    year = int(request.args.get('year', today.year))
    month = int(request.args.get('month', today.month))
    today_str = today.strftime('%Y-%m-%d')
    
    first_half, second_half, _ = get_month_days(year, month)
    all_days = first_half + second_half
    
    staff_wishes = {}
    staff_shifts = {}
    daily_work_details = []  
    total_shift_days = 0
    total_hours = 0.0
    est_pay = 0
    staff_wage = 1100

    if selected_staff and selected_staff in staffs_db:
        staff_wage = staffs_db[selected_staff].get('hourly_wage', 1100)
        
        for w in wishes_data:
            if w['staff_name'] == selected_staff:
                staff_wishes[w['date']] = {'shift': w['shift'], 'time': w['time']}
                    
        for s in published_shifts_data:
            if s['staff_name'] == selected_staff:
                staff_shifts[s['date']] = {
                    'shift': s['shift'],
                    'time': s['time'],
                    'time_display': format_time_display(s['time'])
                }
                
        total_mins = 0

        for day_info in all_days:
            date = day_info['full_date']
            if date > today_str:
                continue 

            s = next((item for item in published_shifts_data if item['staff_name'] == selected_staff and item['date'] == date), None)
            shift_val = s['shift'] if s else '-'
            shift_time = s['time'] if s else ''

            act = next((item for item in actual_shifts_data if item['staff_name'] == selected_staff and item['date'] == date), None)

            is_working = False
            time_range = '-'
            status_display = '休み'

            if act:
                if act['status'] == '欠勤':
                    status_display = '欠勤'
                    is_working = False
                    time_range = '-'
                else:
                    status_display = f"{shift_val.split(' ')[0] if shift_val != '-' else '追加'} ({act['status']})"
                    is_working = True
                    time_range = act['time']
            else:
                if shift_val and shift_val != '-' and shift_val != '希望休':
                    status_display = shift_val.split(' ')[0]
                    is_working = True
                    time_range = shift_time if shift_time else '-'
                elif shift_val == '希望休':
                    status_display = '希望休'
                    is_working = False
                    time_range = '-'
                else:
                    status_display = '-'
                    is_working = False
                    time_range = '-'

            day_hours = 0.0
            if is_working:
                total_shift_days += 1
                if '-' in time_range:
                    try:
                        parts = time_range.split('-')
                        start_s, end_s = parts[0], parts[1] if len(parts) > 1 else ''
                        if start_s and end_s:
                            sh, sm = map(int, start_s.split(':'))
                            eh, em = map(int, end_s.split(':'))
                            mins = (eh * 60 + em) - (sh * 60 + sm)
                            if mins > 0:
                                total_mins += mins
                                day_hours = round(mins / 60.0, 1)
                        else:
                            total_mins += 300
                            day_hours = 5.0
                    except:
                        total_mins += 300
                        day_hours = 5.0
                else:
                    total_mins += 300
                    day_hours = 5.0

            daily_work_details.append({
                'date': day_info['date_str'],
                'weekday': day_info['weekday'],
                'shift': status_display,
                'time_display': format_time_display(time_range),
                'hours': day_hours if is_working else 0,
                'is_working': is_working
            })

        total_hours = round(total_mins / 60.0, 1)
        est_pay = int(total_hours * staff_wage)

    now = datetime.now()
    is_deadline_passed = False
    deadline_str = '未設定'
    if deadline_settings['deadline']:
        deadline_str = deadline_settings['deadline'].replace('T', ' ')
        try:
            dl_dt = datetime.strptime(deadline_settings['deadline'].replace('T', ' '), '%Y-%m-%d %H:%M')
            if now > dl_dt:
                is_deadline_passed = True
        except:
            pass

    return render_template_string(
        STAFF_TEMPLATE,
        short_name=SHORT_NAME,
        current_year=year,
        current_month=month,
        staffs=staffs_db,
        selected_staff=selected_staff,
        logged_in=logged_in,
        all_days=all_days,
        staff_wishes=staff_wishes,
        staff_shifts=staff_shifts,
        daily_work_details=daily_work_details,
        time_options=TIME_OPTIONS,
        total_shift_days=total_shift_days,
        total_hours=total_hours,
        staff_wage=staff_wage,
        est_pay=est_pay,
        deadline_str=deadline_str,
        is_deadline_passed=is_deadline_passed,
        auth_error=False
    )

@app.route('/staff_login', methods=['POST'])
def staff_login():
    name = request.form.get('name')
    password = request.form.get('staff_password')
    
    if name in staffs_db and staffs_db[name]['password'] == password:
        session['logged_staff'] = name
        return redirect(url_for('staff_page'))
    
    today = datetime.today()
    first_half, second_half, _ = get_month_days(today.year, today.month)
    all_days = first_half + second_half
    
    return render_template_string(
        STAFF_TEMPLATE,
        short_name=SHORT_NAME,
        current_year=today.year,
        current_month=today.month,
        staffs=staffs_db,
        selected_staff=name,
        logged_in=False,
        all_days=all_days,
        staff_wishes={},
        staff_shifts={},
        daily_work_details=[],
        time_options=TIME_OPTIONS,
        total_shift_days=0,
        total_hours=0.0,
        staff_wage=1100,
        est_pay=0,
        deadline_str='',
        is_deadline_passed=False,
        auth_error=True
    )

@app.route('/api/get_daily_members')
def get_daily_members():
    date = request.args.get('date')
    members = []
    for s in published_shifts_data:
        if s['date'] == date and s['shift'] and s['shift'] != '-' and s['shift'] != '希望休':
            members.append({
                'staff_name': s['staff_name'],
                'shift': s['shift'],
                'time_display': format_time_display(s['time'])
            })
    return jsonify({'members': members})

@app.route('/api/add_staff', methods=['POST'])
def add_staff():
    global staffs_db
    data = request.json
    name = data.get('name')
    wage = int(data.get('wage', 1100))
    default_pos = data.get('default_position', '')
    if name and name not in staffs_db:
        staffs_db[name] = {'password': '0000', 'hourly_wage': wage, 'default_position': default_pos}
        save_data()
        return jsonify({'success': True, 'message': f'追加: {name}さん(時給{wage}円)'})
    return jsonify({'success': False, 'message': '名前が空か、登録済みです'})

@app.route('/api/delete_staff', methods=['POST'])
def delete_staff():
    global staffs_db, wishes_data, shifts_data, published_shifts_data, actual_shifts_data
    name = request.json.get('name')
    if name in staffs_db:
        del staffs_db[name]
        wishes_data = [w for w in wishes_data if w['staff_name'] != name]
        shifts_data = [s for s in shifts_data if s['staff_name'] != name]
        published_shifts_data = [s for s in published_shifts_data if s['staff_name'] != name]
        actual_shifts_data = [a for a in actual_shifts_data if a['staff_name'] != name]
        save_data()
        return jsonify({'success': True, 'message': f'削除しました'})
    return jsonify({'success': False, 'message': '見つかりません'})

@app.route('/api/update_staff_info', methods=['POST'])
def update_staff_info():
    global staffs_db
    data = request.json
    staff_name = data.get('staff_name')
    new_password = data.get('new_password')
    new_wage = data.get('new_wage')
    default_pos = data.get('default_position')
    
    if staff_name in staffs_db:
        if new_password and new_password.strip() != '':
            staffs_db[staff_name]['password'] = new_password
        if new_wage is not None:
            staffs_db[staff_name]['hourly_wage'] = int(new_wage)
        if default_pos is not None:
            staffs_db[staff_name]['default_position'] = default_pos
        save_data()
        return jsonify({'success': True, 'message': f'更新完了'})
    return jsonify({'success': False, 'message': '更新に失敗'})

@app.route('/api/update_requirement', methods=['POST'])
def update_requirement():
    global required_settings
    data = request.json
    day = data.get('day')
    req_k = int(data.get('req_k', 0))
    req_f = int(data.get('req_f', 0))
    req_y = int(data.get('req_y', 0))
    required_settings[day] = {'K (キッチン)': req_k, 'F (フロント)': req_f, 'Y (やきば)': req_y}
    save_data()
    return jsonify({'success': True, 'message': f'{day}曜日の人数更新完了'})

@app.route('/api/update_deadline', methods=['POST'])
def update_deadline():
    global deadline_settings
    data = request.json
    deadline = data.get('deadline')
    if deadline:
        deadline_settings['deadline'] = deadline.replace('T', ' ')
        save_data()
        return jsonify({'success': True, 'message': '期限日時更新完了'})
    return jsonify({'success': False, 'message': '日時が無効'})

@app.route('/api/update_password', methods=['POST'])
def update_password():
    global admin_credentials
    new_pass = request.json.get('new_password')
    if new_pass:
        admin_credentials['password'] = new_pass
        save_data()
        return jsonify({'success': True, 'message': 'パスワード変更完了'})
    return jsonify({'success': False, 'message': '空です'})

@app.route('/api/update_shift', methods=['POST'])
def update_shift():
    global shifts_data
    data = request.json
    staff_name = data.get('staff_name')
    date = data.get('date')
    shift = data.get('shift')
    time = data.get('time', '')

    existing = next((s for s in shifts_data if s['staff_name'] == staff_name and s['date'] == date), None)
    if existing:
        if shift == '-':
            shifts_data.remove(existing)
        else:
            existing['shift'] = shift
            existing['time'] = time
    else:
        if shift != '-':
            shifts_data.append({'staff_name': staff_name, 'date': date, 'shift': shift, 'time': time})
            
    save_data()
    return jsonify({'success': True, 'message': 'シフト更新'})

@app.route('/api/update_actual_shift', methods=['POST'])
def update_actual_shift():
    global actual_shifts_data
    data = request.json
    staff_name = data.get('staff_name')
    date = data.get('date')
    status = data.get('status', '定時')
    time = data.get('time', '')

    if not staff_name or not date:
        return jsonify({'success': False, 'message': '日付とスタッフ名を選択してください'})

    existing = next((a for a in actual_shifts_data if a['staff_name'] == staff_name and a['date'] == date), None)
    if existing:
        existing['status'] = status
        existing['time'] = time
    else:
        actual_shifts_data.append({'staff_name': staff_name, 'date': date, 'status': status, 'time': time})

    save_data()
    return jsonify({'success': True, 'message': f'{date} {staff_name} さんの実績を保存しました！'})

@app.route('/api/publish_shifts', methods=['POST'])
def publish_shifts():
    global published_shifts_data
    published_shifts_data = [dict(s) for s in shifts_data]
    save_data()
    return jsonify({'success': True, 'message': 'シフトを確定し、スタッフ画面に公開しました！'})

@app.route('/api/apply_one_wish', methods=['POST'])
def apply_one_wish():
    global shifts_data
    data = request.json
    staff_name = data.get('staff_name')
    date = data.get('date')
    shift = data.get('shift')
    time = data.get('time', '')

    default_pos = staffs_db.get(staff_name, {}).get('default_position', 'K (キッチン)')
    target_shift = '希望休' if shift == '希望休' else default_pos

    existing = next((s for s in shifts_data if s['staff_name'] == staff_name and s['date'] == date), None)
    if existing:
        existing['shift'] = target_shift
        existing['time'] = time
    else:
        shifts_data.append({'staff_name': staff_name, 'date': date, 'shift': target_shift, 'time': time})

    save_data()
    return jsonify({'success': True, 'message': f'{staff_name}さんの希望を作業用シフトに反映しました'})

@app.route('/api/apply_all_wishes', methods=['POST'])
def apply_all_wishes():
    global shifts_data
    count = 0
    for w in wishes_data:
        staff_name = w['staff_name']
        date = w['date']
        shift = w['shift']
        time = w.get('time', '')

        default_pos = staffs_db.get(staff_name, {}).get('default_position', 'K (キッチン)')
        target_shift = '希望休' if shift == '希望休' else default_pos

        existing = next((s for s in shifts_data if s['staff_name'] == staff_name and s['date'] == date), None)
        if existing:
            existing['shift'] = target_shift
            existing['time'] = time
        else:
            shifts_data.append({'staff_name': staff_name, 'date': date, 'shift': target_shift, 'time': time})
        count += 1

    save_data()
    return jsonify({'success': True, 'message': f'{count}件の希望を作業用シフトに一括反映しました！'})

@app.route('/api/submit_wish_shifts', methods=['POST'])
def submit_wish_shifts():
    global wishes_data
    if deadline_settings['deadline']:
        try:
            dl_dt = datetime.strptime(deadline_settings['deadline'].replace('T', ' '), '%Y-%m-%d %H:%M')
            if datetime.now() > dl_dt:
                return jsonify({'success': False, 'message': '期限切れのため受付不可'})
        except: pass

    data = request.json
    staff_name = data.get('staff_name')
    shifts = data.get('shifts', {})
    
    wishes_data = [w for w in wishes_data if w['staff_name'] != staff_name]
    
    for date, info in shifts.items():
        if info['shift'] != '-':
            wishes_data.append({'staff_name': staff_name, 'date': date, 'shift': info['shift'], 'time': info['time']})
            
    save_data()
    return jsonify({'success': True, 'message': '希望提出完了！'})

@app.route('/api/generate_auto_shift', methods=['POST'])
def generate_auto_shift():
    global shifts_data
    data = request.json
    year = int(data.get('year'))
    month = int(data.get('month'))
    unit = data.get('unit', 'all')
    
    first_half, second_half, _ = get_month_days(year, month)
    weeks = get_month_weeks(year, month)
    
    target_dates = set()
    if unit == 'all':
        target_dates = {d['full_date'] for d in (first_half + second_half)}
    elif unit.isdigit():
        w_idx = int(unit)
        if 0 <= w_idx < len(weeks):
            target_dates = {d['full_date'] for d in weeks[w_idx] if d['in_month']}
    
    if not target_dates: return jsonify({'success': False, 'message': '対象なし'})
    
    shifts_data = [s for s in shifts_data if s['date'] not in target_dates]
    
    staff_list = list(staffs_db.keys())
    if not staff_list: return jsonify({'success': False, 'message': 'スタッフ未登録'})
        
    all_days = first_half + second_half
    for day_info in all_days:
        date_str = day_info['full_date']
        if date_str not in target_dates: continue
            
        weekday = day_info['weekday']
        reqs = required_settings.get(weekday, {'K (キッチン)': 2, 'F (フロント)': 2, 'Y (やきば)': 1})
        
        assigned_staffs_on_day = set()
        pos_counts = {p: 0 for p in POSITIONS}
        day_wishes = {w['staff_name']: w for w in wishes_data if w['date'] == date_str}

        # 第1優先：出勤希望
        for staff in staff_list:
            wish = day_wishes.get(staff)
            if wish and wish.get('shift') == '出勤希望':
                default_pos = staffs_db[staff].get('default_position', 'K (キッチン)')
                req_max = reqs.get(default_pos, 0)
                if pos_counts[default_pos] < req_max:
                    shifts_data.append({
                        'staff_name': staff,
                        'date': date_str,
                        'shift': default_pos,
                        'time': wish.get('time', '')
                    })
                    assigned_staffs_on_day.add(staff)
                    pos_counts[default_pos] += 1

        # 第2優先：基本ポジションで補填
        for pos, req_count in reqs.items():
            if pos_counts[pos] >= req_count: continue
            for staff in staff_list:
                if pos_counts[pos] >= req_count: break
                if staff in assigned_staffs_on_day: continue
                wish = day_wishes.get(staff)
                if wish and wish.get('shift') == '希望休': continue
                
                default_pos = staffs_db[staff].get('default_position', '')
                if default_pos == pos:
                    time_slot = wish.get('time', '') if wish else ''
                    shifts_data.append({
                        'staff_name': staff,
                        'date': date_str,
                        'shift': pos,
                        'time': time_slot
                    })
                    assigned_staffs_on_day.add(staff)
                    pos_counts[pos] += 1

        # 第3優先：フリー補填
        for pos, req_count in reqs.items():
            if pos_counts[pos] >= req_count: continue
            for staff in staff_list:
                if pos_counts[pos] >= req_count: break
                if staff in assigned_staffs_on_day: continue
                wish = day_wishes.get(staff)
                if wish and wish.get('shift') == '希望休': continue
                
                time_shift = wish.get('time', '') if wish else ''
                shifts_data.append({
                    'staff_name': staff,
                    'date': date_str,
                    'shift': pos,
                    'time': time_shift
                })
                assigned_staffs_on_day.add(staff)
                pos_counts[pos] += 1

    save_data()
    return jsonify({'success': True, 'message': '希望優先で自動シフトを作成しました！（「確定・公開」を押すとスタッフ画面に反映されます）'})

if __name__ == '__main__':
    app.run(debug=True)