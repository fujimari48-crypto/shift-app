from flask import Flask, request, jsonify, render_template_string, session, redirect, url_for
from datetime import datetime, timedelta, timezone
import calendar
import os
import json
import threading
import time
import math
import gspread
from google.oauth2.service_account import Credentials

app = Flask(__name__)
app.secret_key = 'shift_app_secret_key'

# 日本標準時 (JST)
JST = timezone(timedelta(hours=+9), 'JST')

def get_now_jst():
    return datetime.now(JST)

def get_business_date(dt):
    """朝5時までは前日の営業日として扱う"""
    if dt.hour < 5:
        return (dt - timedelta(days=1)).strftime('%Y-%m-%d')
    return dt.strftime('%Y-%m-%d')

def round_time_30min(time_str, mode='in'):
    """
    30分単位丸め
    mode='in' : 切り上げ (例 16:50 -> 17:00, 17:01 -> 17:30)
    mode='out': 切り捨て (例 23:25 -> 23:00, 23:45 -> 23:30)
    """
    if not time_str or ':' not in time_str:
        return ''
    try:
        h, m = map(int, time_str.split(':'))
        total_mins = h * 60 + m
        if mode == 'in':
            rounded = math.ceil(total_mins / 30.0) * 30
        else:
            rounded = math.floor(total_mins / 30.0) * 30
        rh = (rounded // 60) % 24
        rm = rounded % 60
        return f"{rh:02d}:{rm:02d}"
    except:
        return time_str

def calc_work_minutes(time_range):
    """
    '17:00-02:30' のような時間範囲から実働分数を計算（深夜5時までの日付またぎ対応）
    """
    if not time_range or '-' not in time_range:
        return 0
    try:
        p = time_range.split('-')
        if len(p) < 2 or not p[0] or not p[1]:
            return 0
        sh, sm = map(int, p[0].split(':'))
        eh, em = map(int, p[1].split(':'))
        start_mins = sh * 60 + sm
        end_mins = eh * 60 + em
        if end_mins <= start_mins:
            end_mins += 24 * 60
        return max(0, end_mins - start_mins)
    except:
        return 0

DEFAULT_SHOPS = [
    {'id': 'shop1', 'name': '久留米焼鳥 つかさ 本店', 'short_name': 'つかさ 本店'},
    {'id': 'shop2', 'name': '久留米焼鳥 つかさ 2号店', 'short_name': 'つかさ 2号店'}
]

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
        return sheet.add_worksheet(title=title, rows=2000, cols=cols)

def rows_to_dicts(rows):
    """2次元配列（1行目ヘッダー）を辞書のリストに変換する"""
    if not rows or len(rows) < 2:
        return []
    headers = [str(h).strip() for h in rows[0]]
    result = []
    for row in rows[1:]:
        if not any(str(cell).strip() for cell in row):
            continue
        item = {}
        for i, h in enumerate(headers):
            if h:
                item[h] = str(row[i]) if i < len(row) else ''
        result.append(item)
    return result

def get_next_staff_code(db_dict):
    """未使用の連番シフト番号（101〜）を発行する"""
    used = {str(info.get('staff_code', '')).strip() for info in db_dict.values()}
    num = 101
    while str(num) in used:
        num += 1
    return str(num)

default_data = {
    'shops': DEFAULT_SHOPS,
    'current_shop_id': 'shop1',
    'staffs_db': {},
    'required_settings': {
        '月': {'K (キッチン)': 2, 'F (フロント)': 2, 'Y (やきば)': 1},
        '火': {'K (キッチン)': 2, 'F (フロント)': 2, 'Y (やきば)': 1},
        '水': {'K (キッチン)': 2, 'F (フロント)': 2, 'Y (やきば)': 1},
        '木': {'K (キッチン)': 2, 'F (フロント)': 2, 'Y (やきば)': 1},
        '金': {'K (キッチン)': 3, 'F (フロント)': 3, 'Y (やきば)': 2},
        '土': {'K (キッチン)': 3, 'F (フロント)': 3, 'Y (やきば)': 2},
        '日': {'K (キッチン)': 3, 'F (フロント)': 3, 'Y (やきば)': 2}
    },
    'deadline_settings': {'deadline_first': '2026-11-15 23:59', 'deadline_second': '2026-11-30 23:59'},
    'wishes_data': [],
    'shifts_data': [],
    'published_shifts_data': [],
    'actual_shifts_data': [],
    'timeclock_logs': [],
    'admin_credentials': {'password': '1234', 'clock_pin': '0000'}
}

load_lock = threading.Lock()

def load_data():
    if sheet:
        with load_lock:
            try:
                sheet_names = ["Staff", "Settings", "Wishes", "Shifts", "Published", "Actual", "TimeClock"]
                try:
                    batch_res = sheet.values_batch_get(sheet_names)
                    value_ranges = batch_res.get('valueRanges', [])
                except Exception:
                    for name in sheet_names:
                        get_ws(name)
                    batch_res = sheet.values_batch_get(sheet_names)
                    value_ranges = batch_res.get('valueRanges', [])

                def get_sheet_records(idx):
                    if idx < len(value_ranges):
                        return rows_to_dicts(value_ranges[idx].get('values', []))
                    return []

                staff_recs = get_sheet_records(0)
                set_recs = get_sheet_records(1)
                wishes_data = get_sheet_records(2)
                shifts_data = get_sheet_records(3)
                published_shifts_data = get_sheet_records(4)
                actual_shifts_data = get_sheet_records(5)
                timeclock_logs = get_sheet_records(6)

                loaded_staffs = {}
                if staff_recs:
                    for r in staff_recs:
                        r_clean = {str(k).strip(): v for k, v in r.items()}
                        name = str(r_clean.get('name', '')).strip()
                        if not name: continue
                        try:
                            wage = int(r_clean.get('wage', 1100))
                        except:
                            wage = 1100
                        
                        raw_pass = str(r_clean.get('password', '0000')).strip()
                        if raw_pass == '0' or raw_pass == '':
                            raw_pass = '0000'

                        code = str(r_clean.get('staff_code', '')).strip()
                        if not code:
                            code = get_next_staff_code(loaded_staffs)

                        loaded_staffs[name] = {
                            'password': raw_pass,
                            'hourly_wage': wage,
                            'default_position': str(r_clean.get('position', '')),
                            'shop_id': str(r_clean.get('shop_id', 'shop1')).strip(),
                            'staff_code': code
                        }
                else:
                    loaded_staffs = default_data['staffs_db']

                shops = default_data['shops']
                deadline_settings = default_data['deadline_settings'].copy()
                admin_credentials = default_data['admin_credentials'].copy()
                req_settings = default_data['required_settings'].copy()

                if set_recs:
                    set_dict = {str(r['key']): str(r['value']) for r in set_recs if 'key' in r and 'value' in r}
                    if 'shops_json' in set_dict: shops = json.loads(set_dict['shops_json'])
                    if 'deadline_first' in set_dict: deadline_settings['deadline_first'] = set_dict['deadline_first']
                    if 'deadline_second' in set_dict: deadline_settings['deadline_second'] = set_dict['deadline_second']
                    if 'admin_password' in set_dict: admin_credentials['password'] = set_dict['admin_password']
                    if 'clock_pin' in set_dict: admin_credentials['clock_pin'] = set_dict['clock_pin']
                    if 'required_settings' in set_dict: req_settings = json.loads(set_dict['required_settings'])

                for data_list in [wishes_data, shifts_data, published_shifts_data, actual_shifts_data, timeclock_logs]:
                    for d in data_list: 
                        d.update((str(k).strip(), str(v)) for k, v in d.items())
                        if 'shop_id' not in d or not d['shop_id']: d['shop_id'] = 'shop1'

                return {
                    'shops': shops,
                    'staffs_db': loaded_staffs,
                    'required_settings': req_settings,
                    'deadline_settings': deadline_settings,
                    'wishes_data': wishes_data,
                    'shifts_data': shifts_data,
                    'published_shifts_data': published_shifts_data,
                    'actual_shifts_data': actual_shifts_data,
                    'timeclock_logs': timeclock_logs,
                    'admin_credentials': admin_credentials
                }
            except Exception as e:
                print("⚠️ スプレッドシート読み込みエラー:", e)

    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                sdb = data.get('staffs_db', {})
                for info in sdb.values():
                    if not info.get('staff_code'):
                        info['staff_code'] = get_next_staff_code(sdb)
                return data
        except: pass
    return default_data

save_lock = threading.Lock()
last_saved_str = "未保存"

def write_to_sheets(data_copy):
    global last_saved_str
    if sheet is None: return
    with save_lock:
        try:
            def update_ws(title, rows):
                ws = get_ws(title)
                ws.clear()
                if rows: ws.update(values=rows, range_name='A1')

            staff_rows = [['name', 'password', 'wage', 'position', 'shop_id', 'staff_code']] + [
                [name, info['password'], info['hourly_wage'], info['default_position'], info.get('shop_id', 'shop1'), info.get('staff_code', '')]
                for name, info in data_copy['staffs_db'].items()
            ]
            update_ws("Staff", staff_rows)

            set_rows = [
                ['key', 'value'],
                ['shops_json', json.dumps(data_copy['shops'], ensure_ascii=False)],
                ['required_settings', json.dumps(data_copy['required_settings'], ensure_ascii=False)],
                ['deadline_first', data_copy['deadline_settings']['deadline_first']],
                ['deadline_second', data_copy['deadline_settings']['deadline_second']],
                ['admin_password', data_copy['admin_credentials']['password']],
                ['clock_pin', data_copy['admin_credentials'].get('clock_pin', '0000')]
            ]
            update_ws("Settings", set_rows)

            def build_rows(headers, data_list): return [headers] + [[d.get(h, '') for h in headers] for d in data_list]
            update_ws("Wishes", build_rows(['shop_id', 'staff_name', 'date', 'shift', 'time'], data_copy['wishes_data']))
            update_ws("Shifts", build_rows(['shop_id', 'staff_name', 'date', 'shift', 'time'], data_copy['shifts_data']))
            update_ws("Published", build_rows(['shop_id', 'staff_name', 'date', 'shift', 'time'], data_copy['published_shifts_data']))
            update_ws("Actual", build_rows(['shop_id', 'staff_name', 'date', 'status', 'time'], data_copy['actual_shifts_data']))
            update_ws("TimeClock", build_rows(['id', 'shop_id', 'staff_name', 'date', 'clock_in', 'clock_out', 'rounded_in', 'rounded_out', 'approved'], data_copy['timeclock_logs']))
            last_saved_str = get_now_jst().strftime('%H:%M:%S')
        except Exception as e:
            print("⚠️ 保存エラー:", e)

def save_data(immediate=True):
    data = {
        'shops': list(shops),
        'staffs_db': {k: dict(v) for k, v in staffs_db.items()},
        'required_settings': {k: dict(v) for k, v in required_settings.items()},
        'deadline_settings': dict(deadline_settings),
        'wishes_data': [dict(x) for x in wishes_data],
        'shifts_data': [dict(x) for x in shifts_data],
        'published_shifts_data': [dict(x) for x in published_shifts_data],
        'actual_shifts_data': [dict(x) for x in actual_shifts_data],
        'timeclock_logs': [dict(x) for x in timeclock_logs],
        'admin_credentials': dict(admin_credentials)
    }
    try:
        with open(DATA_FILE, 'w', encoding='utf-8') as f: json.dump(data, f, ensure_ascii=False, indent=4)
    except: pass

    if immediate:
        write_to_sheets(data)
    else:
        threading.Thread(target=write_to_sheets, args=(data,), daemon=True).start()

app_data = load_data()
shops = app_data.get('shops', default_data['shops'])
staffs_db, required_settings, deadline_settings = app_data['staffs_db'], app_data['required_settings'], app_data['deadline_settings']
wishes_data, shifts_data, published_shifts_data = app_data['wishes_data'], app_data['shifts_data'], app_data['published_shifts_data']
actual_shifts_data, admin_credentials = app_data['actual_shifts_data'], app_data['admin_credentials']
timeclock_logs = app_data.get('timeclock_logs', [])
last_saved_str = get_now_jst().strftime('%H:%M:%S')

def background_auto_sync():
    global shops, staffs_db, required_settings, deadline_settings, wishes_data, shifts_data, published_shifts_data, actual_shifts_data, admin_credentials, timeclock_logs
    while True:
        time.sleep(120)
        if not save_lock.locked():
            new_data = load_data()
            if new_data and new_data.get('staffs_db'):
                shops = new_data.get('shops', shops)
                staffs_db = new_data['staffs_db']
                required_settings = new_data['required_settings']
                deadline_settings = new_data['deadline_settings']
                wishes_data = new_data['wishes_data']
                shifts_data = new_data['shifts_data']
                published_shifts_data = new_data['published_shifts_data']
                actual_shifts_data = new_data['actual_shifts_data']
                admin_credentials = new_data['admin_credentials']
                timeclock_logs = new_data.get('timeclock_logs', [])

threading.Thread(target=background_auto_sync, daemon=True).start()

POSITIONS = ['K (キッチン)', 'F (フロント)', 'Y (やきば)']
TIME_OPTIONS = (
    [f"{h:02d}:{m:02d}" for h in range(9, 24) for m in [0, 30]] +
    [f"{h:02d}:{m:02d}" for h in range(0, 6) for m in [0, 30] if not (h == 5 and m > 0)]
)

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
# テンプレート (HTML) - 管理者画面
# ==========================================
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ current_shop.name }} シフト・勤怠管理</title>
    <style>
        body { font-family: 'Noto Sans JP', sans-serif; margin: 0; background-color: #121212; color: #E0E0E0; display: flex; flex-direction: column; height: 100vh; }
        header { background-color: #1E1E1E; padding: 10px 20px; display: flex; justify-content: space-between; align-items: center; border-bottom: 2px solid #D4AF37; position: fixed; top: 0; left: 0; width: 100%; z-index: 1000; box-sizing: border-box; flex-wrap: wrap; gap: 6px; }
        header h1 { display: flex; align-items: center; gap: 10px; margin: 0; font-size: 17px; color: #D4AF37; }
        .container { display: flex; gap: 15px; padding: 75px 15px 15px 15px; flex-wrap: wrap; margin-bottom: 20px; flex: 1; }
        aside { width: 185px; background-color: #1E1E1E; padding: 15px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.5); }
        aside h2 { font-size: 15px; color: #D4AF37; border-bottom: 1px solid #D4AF37; padding-bottom: 8px; margin-top: 0; }
        aside ul { list-style: none; padding: 0; margin: 10px 0 0 0; }
        aside ul li { margin-bottom: 8px; }
        aside ul li a { text-decoration: none; color: #E0E0E0; font-size: 13px; display: block; padding: 6px; border-radius: 4px; }
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
        .wish-box { background: #1A1A1A; padding: 15px; border-radius: 8px; margin-bottom: 20px; border-left: 5px solid #2E7D32; color: #E0E0E0; }
        .admin-tab-menu { display: flex; gap: 6px; margin-bottom: 12px; flex-wrap: wrap; }
        .admin-tab-btn { padding: 8px 16px; background: #1E1E1E; border: 1px solid #333; border-radius: 4px; font-weight: bold; cursor: pointer; font-size: 12px; color: #E0E0E0; }
        .admin-tab-btn.active { background: #D4AF37; color: #121212; border-color: #D4AF37; }
        .admin-tab-content { display: none; }
        .admin-tab-content.active { display: block; }
        .wish-card-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 10px; margin-top: 10px; }
        .wish-card { background: #262626; border: 1px solid #333; border-radius: 6px; padding: 10px; }
        .wish-card-header { font-weight: bold; color: #D4AF37; border-bottom: 1px solid #444; padding-bottom: 4px; margin-bottom: 6px; display: flex; justify-content: space-between; }
        .wish-item { font-size: 11px; margin: 3px 0; background: #181818; padding: 3px 6px; border-radius: 3px; display: flex; justify-content: space-between; align-items: center; }
        .badge-pending { background: #e67e22; color: #fff; padding: 2px 6px; border-radius: 3px; font-size: 10px; font-weight: bold; }
        .badge-approved { background: #27ae60; color: #fff; padding: 2px 6px; border-radius: 3px; font-size: 10px; font-weight: bold; }
        .save-indicator { font-size: 11px; color: #4caf50; background: #1a2e1a; padding: 4px 8px; border-radius: 4px; border: 1px solid #2e7d32; }
        .refresh-indicator { font-size: 11px; color: #64b5f6; background: #152232; padding: 4px 8px; border-radius: 4px; border: 1px solid #1976d2; }
        .today-kpi { background: #262626; border: 1px solid #333; border-radius: 6px; padding: 10px 14px; margin-bottom: 10px; display: inline-block; min-width: 200px; }
        .today-kpi-label { font-size: 11px; color: #aaa; margin-bottom: 4px; }
        .today-kpi-val { font-size: 22px; font-weight: bold; color: #FFA726; }
    </style>
</head>
<body>
    <header>
        <h1><strong>{{ current_shop.name }}</strong> 管理画面</h1>
        <div style="display: flex; gap: 8px; align-items: center; flex-wrap: wrap;">
            {% if is_admin %}
            <span id="refreshCountdown" class="refresh-indicator">🔄 自動更新まで 30秒</span>
            <span id="saveStatus" class="save-indicator">☁️ 保存済 ({{ last_saved_str }})</span>
            {% endif %}
            <select onchange="window.location.href='/?shop_id=' + this.value" style="font-size: 12px; padding: 4px; border-color: #D4AF37; font-weight: bold;">
                {% for s in shops %}
                <option value="{{ s.id }}" {% if s.id == current_shop.id %}selected{% endif %}>🏪 {{ s.name }}</option>
                {% endfor %}
            </select>
            {% if is_admin %}
            <a href="/logout"><button class="btn-danger" style="padding: 5px 10px; font-size: 11px;">ログアウト</button></a>
            {% endif %}
        </div>
    </header>

    {% if not is_admin %}
        <div class="card" style="max-width: 350px; margin: 100px auto; padding: 25px;">
            <h2 style="text-align: center; color: #D4AF37; margin-top: 0;">🔒 管理者ログイン</h2>
            <form method="POST" action="/admin_login">
                <input type="hidden" name="shop_id" value="{{ current_shop.id }}">
                <input type="password" name="password" placeholder="管理者パスワード" required style="padding: 8px;" autofocus>
                <button type="submit" style="padding: 8px;">ログイン</button>
            </form>
            {% if login_error %}<div style="color:#ff5252; font-size:12px; margin-top:8px; font-weight:bold;">⚠️ パスワードが間違っています。</div>{% endif %}
            <div style="margin-top: 15px; padding-top: 12px; border-top: 1px solid #333; text-align: center;">
                <a href="/clock?shop_id={{ current_shop.id }}" style="color: #64b5f6; font-size: 12px; text-decoration: none;">⏱️ 店舗タイムカード画面へ戻る</a>
            </div>
        </div>
    {% else %}
        <div class="container">
            <aside class="no-print">
                <h2>🖥️ 管理メニュー</h2>
                <ul>
                    <li><a href="/" class="active">📊 シフト・勤怠管理</a></li>
                    <li><a href="/clock_mode?shop_id={{ current_shop.id }}" onclick="return confirm('管理画面をロック（自動ログアウト）して、店舗打刻専用モードに切り替えますか？')" style="background: #2c3e50; color: #fff;">⏱️ 店舗打刻モードへ切替</a></li>
                    <li><a href="/admin/settings">⚙️ 店舗・パス設定</a></li>
                </ul>
                <button onclick="window.customActions.generateAutoShift()" style="width: 100%; margin-top: 12px; background: #8e44ad; padding: 8px; font-size: 12px; color: white;">🤖 自動シフト作成</button>
                <button class="btn-green" onclick="window.customActions.publishShifts()" style="width: 100%; margin-top: 8px; padding: 8px; font-size: 12px; background: #27ae60;">📢 シフトを確定・公開</button>
                <button onclick="window.customActions.syncFromSheets()" style="width: 100%; margin-top: 15px; background: #3498db; padding: 8px; font-size: 12px; color: white;">🔄 今すぐ最新同期</button>
            </aside>

            <main>
                <div class="no-print" style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
                    <form method="GET" action="/" style="flex-direction: row; gap: 4px;">
                        <input type="hidden" name="shop_id" value="{{ current_shop.id }}">
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
                    <button type="button" id="btn-tab-shift-create" class="admin-tab-btn active" onclick="window.customActions.switchAdminTab('tab-shift-create', this)">📊 シフト作成・管理</button>
                    <button type="button" id="btn-tab-actual-work" class="admin-tab-btn" onclick="window.customActions.switchAdminTab('tab-actual-work', this)">
                        💰 勤怠打刻承認・給料確定
                        {% set pending_count = all_shop_timeclock|selectattr('approved', 'ne', '1')|list|length %}
                        {% if pending_count > 0 %}<span style="background:#e74c3c; color:white; padding:1px 6px; border-radius:10px; margin-left:4px;">{{ pending_count }}</span>{% endif %}
                    </button>
                </div>

                <!-- タブ1: シフト作成 -->
                <div id="tab-shift-create" class="admin-tab-content active">
                    <div class="no-print" style="display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 12px;">
                        <div class="card" style="flex: 1; min-width: 260px; margin-bottom: 0;">
                            <h3>バイトスタッフの管理 ({{ current_shop.short_name }} / {{ staffs|length }}名)</h3>
                            <form onsubmit="window.customActions.submitAddStaff(event)" style="flex-direction: row; gap: 4px; flex-wrap: wrap;">
                                <input type="text" id="new_staff_code" placeholder="番号(自動)" style="width: 65px;">
                                <input type="text" id="new_staff_name" placeholder="スタッフ名" required style="flex: 1; min-width: 80px;">
                                <input type="number" id="new_staff_wage" placeholder="時給" value="1100" min="0" required style="width: 55px;">
                                <select id="new_staff_pos" style="width: 85px;">
                                    <option value="">基本なし</option><option value="K (キッチン)">キッチン</option><option value="F (フロント)">フロント</option><option value="Y (やきば)">やきば</option>
                                </select>
                                <button type="submit" class="btn-green" style="padding: 5px 8px;">追加</button>
                            </form>
                            <div class="staff-grid">
                                {% for staff, info in staffs.items() %}
                                <div class="staff-chip">
                                    <button type="button" class="staff-name-btn" onclick="window.customActions.openPassModal('{{ staff }}', {{ info.hourly_wage }}, '{{ info.default_position }}', '{{ info.staff_code }}')">
                                        <span style="color:#64b5f6; font-size:10px;">No.{{ info.staff_code }}</span> {{ staff }} <span style="font-size: 9px; color: #D4AF37;">[{{ info.default_position.split(' ')[0] if info.default_position else '無' }}]</span>
                                    </button>
                                    <button onclick="window.customActions.deleteStaff('{{ staff }}')" class="btn-danger" style="padding: 1px 4px; font-size: 9px;">削除</button>
                                </div>
                                {% endfor %}
                            </div>
                        </div>

                        <div class="card" style="flex: 1; min-width: 260px; margin-bottom: 0;">
                            <h3>提出期限（前半/後半） & 必要人数</h3>
                            <form onsubmit="window.customActions.submitDeadline(event)" style="flex-direction: column; gap: 4px; margin-bottom: 8px;">
                                <div style="display:flex; gap:4px; align-items:center;">
                                    <span style="font-size:10px; width:45px;">前半:</span>
                                    <input type="datetime-local" id="deadline_first" value="{{ deadline_first_val }}" style="flex:1; font-size:10px; padding:3px;">
                                </div>
                                <div style="display:flex; gap:4px; align-items:center;">
                                    <span style="font-size:10px; width:45px;">後半:</span>
                                    <input type="datetime-local" id="deadline_second" value="{{ deadline_second_val }}" style="flex:1; font-size:10px; padding:3px;">
                                </div>
                                <button type="submit" style="padding: 4px; font-size: 10px;">締切日時を保存</button>
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
                            <label style="font-size: 11px; display: block;">シフト番号 (ログイン・打刻共通):</label>
                            <input type="text" id="modalNewCode" style="width: 100%; margin-bottom: 6px;" placeholder="例: 101">
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
                            <h3 style="margin: 0; color: #2E7D32; font-size: 15px;">📥 スタッフ希望提出状況 ({{ current_shop.short_name }})</h3>
                            <button onclick="window.customActions.applyAllWishes()" class="btn-green" style="padding: 5px 10px; font-size: 11px;">📥 全希望を一括反映</button>
                        </div>
                        <div class="wish-card-grid">
                            {% for staff, info in staffs.items() %}
                            <div class="wish-card">
                                <div class="wish-card-header">
                                    <span>👤 {{ staff }} <small style="color:#64b5f6;">(No.{{ info.staff_code }})</small></span>
                                </div>
                                {% set staff_wishes = [] %}
                                {% for w in wishes %}{% if w.staff_name == staff %}{% set _ = staff_wishes.append(w) %}{% endif %}{% endfor %}
                                {% if staff_wishes|length > 0 %}
                                    {% for w in staff_wishes %}
                                    <div class="wish-item">
                                        <span>
                                            <strong>{{ w.date.split('-')[1]|int }}/{{ w.date.split('-')[2]|int }}</strong>: 
                                            {% if w.shift == '希望休' %}<span style="color:#ff5252;">休</span>{% else %}<span style="color:#4caf50;">出</span>{% endif %}
                                            {% if w.time and w.time != '-' %}<small>({{ w.time }})</small>{% endif %}
                                        </span>
                                        <button onclick="window.customActions.applyOneWish('{{ w.staff_name }}', '{{ w.date }}', '{{ w.shift }}', '{{ w.time }}')" style="padding: 1px 4px; font-size: 9px; background: #D4AF37; color: #121212; border-radius: 3px; cursor: pointer;">反映</button>
                                    </div>
                                    {% endfor %}
                                {% else %}
                                    <div style="font-size:11px; color:#777; padding:4px 0;">（未提出）</div>
                                {% endif %}
                            </div>
                            {% endfor %}
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
                                    <td><small style="color:#64b5f6;">No.{{ s_info.staff_code }}</small> {{ staff }}<br><small style="color: #888;">({{ s_info.default_position.split(' ')[0] if s_info.default_position else '無' }})</small></td>
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
                                        <select class="shift-pos-select" data-date="{{ day_info.full_date }}" id="shift_{{ staff }}_{{ day_info.full_date }}" onchange="window.customActions.triggerUpdate('{{ staff }}', '{{ day_info.full_date }}')" style="padding: 3px; font-size: 11px;">
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

                                <tr style="background: #262626; border-top: 2px solid #D4AF37;">
                                    <td style="font-weight: bold; font-size: 11px;">現在の人員<br><small style="color: #888;">(過不足)</small></td>
                                    {% for day_info in days_list %}
                                    {% set req = requirements.get(day_info.weekday, {'K (キッチン)':0, 'F (フロント)':0, 'Y (やきば)':0}) %}
                                    {% set count = namespace(k=0, f=0, y=0) %}
                                    {% for s in shifts %}
                                        {% if s.date == day_info.full_date and s.shift != '-' and s.shift != '希望休' %}
                                            {% if 'K' in s.shift %}{% set count.k = count.k + 1 %}{% endif %}
                                            {% if 'F' in s.shift %}{% set count.f = count.f + 1 %}{% endif %}
                                            {% if 'Y' in s.shift %}{% set count.y = count.y + 1 %}{% endif %}
                                        {% endif %}
                                    {% endfor %}
                                    <td class="count-cell" data-date="{{ day_info.full_date }}" data-req-k="{{ req['K (キッチン)'] }}" data-req-f="{{ req['F (フロント)'] }}" data-req-y="{{ req['Y (やきば)'] }}" style="font-size: 10px; line-height: 1.4; font-weight: bold; background: #262626;">
                                        <div class="cnt-k" style="color: {% if count.k < req['K (キッチン)'] %}#ff5252{% else %}#4caf50{% endif %};">K: {{ count.k }} / {{ req['K (キッチン)'] }}</div>
                                        <div class="cnt-f" style="color: {% if count.f < req['F (フロント)'] %}#ff5252{% else %}#4caf50{% endif %};">F: {{ count.f }} / {{ req['F (フロント)'] }}</div>
                                        <div class="cnt-y" style="color: {% if count.y < req['Y (やきば)'] %}#ff5252{% else %}#4caf50{% endif %};">Y: {{ count.y }} / {{ req['Y (やきば)'] }}</div>
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

                <!-- タブ2: 勤怠打刻承認・給料確定 -->
                <div id="tab-actual-work" class="admin-tab-content">
                    <!-- 本日の人件費サマリー -->
                    <div class="card" style="border-left-color: #3498db;">
                        <h3>💰 本日（{{ today_date_str }}）のトータル人件費</h3>
                        <div class="today-kpi">
                            <div class="today-kpi-label">本日のトータル人件費</div>
                            <div class="today-kpi-val">￥{{ "{:,}".format(today_labor_summary.total_pay) }}</div>
                        </div>
                        {% if today_labor_summary.staff_items|length > 0 %}
                        <div style="display:flex; flex-wrap:wrap; gap:6px; margin-top:4px;">
                            {% for item in today_labor_summary.staff_items %}
                            <div style="background:#181818; border:1px solid #333; padding:5px 10px; border-radius:4px; font-size:11px;">
                                <strong>{{ item.name }}</strong>：{{ item.hours }}h / <span style="color:#FFA726;">￥{{ "{:,}".format(item.pay) }}</span>
                                {% if item.is_confirmed %}<span style="color:#4caf50; font-size:9px;">(確定)</span>{% else %}<span style="color:#e67e22; font-size:9px;">(見込)</span>{% endif %}
                            </div>
                            {% endfor %}
                        </div>
                        {% else %}
                        <p style="font-size:11px; color:#777; margin:0;">※本日の出退勤完了データは現在0件です（退勤打刻または確定後に自動集計されます）。</p>
                        {% endif %}
                    </div>

                    <div class="card" style="border-left-color: #e67e22;">
                        <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px; border-bottom:1px solid #333; padding-bottom:8px; margin-bottom:8px;">
                            <h3 style="border:none; padding:0; margin:0;">⏱️ 店舗打刻ログ（30分単位自動丸め・最終確定）</h3>
                            <div style="display:flex; gap:6px; align-items:center; flex-wrap:wrap;">
                                <select onchange="window.location.href='/?shop_id={{ current_shop.id }}&year={{ current_year }}&month={{ current_month }}&week_idx={{ selected_week_idx }}&tc_view=' + this.value" style="font-size:11px; padding:4px 8px; border-color:#e67e22;">
                                    <option value="today" {% if tc_view == 'today' %}selected{% endif %}>📅 本日分のみ（未確定含む）</option>
                                    <option value="month" {% if tc_view == 'month' %}selected{% endif %}>🗓️ {{ month_title }}の全ログを表示</option>
                                </select>
                                {% if tc_view == 'month' and timeclock_logs|length > 0 %}
                                <button type="button" onclick="window.customActions.deleteMonthTimeclock('{{ current_year }}', '{{ current_month }}')" class="btn-danger" style="padding:4px 8px; font-size:11px;">
                                    🗑️ {{ month_title }}分をすべて削除
                                </button>
                                {% endif %}
                            </div>
                        </div>
                        <p style="font-size: 11px; color: #aaa; margin-top: 0;">
                            ※全打刻データはGoogle Sheets（TimeClockシート）に自動保存されます。管理画面の初期表示は<strong>本日分（＋未確定分）のみ</strong>です。<br>
                            ※月別表示に切り替えると、不要になった月のログを1ヶ月単位で一括削除できます（確定済みの給料計算データは消えません）。
                        </p>
                        <div class="table-responsive" style="max-height: 320px;">
                            <table>
                                <thead>
                                    <tr>
                                        <th>状態</th>
                                        <th>営業日</th>
                                        <th>スタッフ名</th>
                                        <th>生打刻 (出〜退)</th>
                                        <th>30分丸め確定時間 (編集可)</th>
                                        <th>時間 / 人件費</th>
                                        <th>操作</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {% if timeclock_logs|length == 0 %}
                                    <tr>
                                        <td colspan="7" style="color:#777;">
                                            {% if tc_view == 'today' %}
                                                本日分（および未確定）の打刻データはありません
                                            {% else %}
                                                {{ month_title }}の打刻データはありません
                                            {% endif %}
                                        </td>
                                    </tr>
                                    {% else %}
                                    {% for log in timeclock_logs|reverse %}
                                    <tr>
                                        <td>
                                            {% if log.approved == '1' %}
                                                <span class="badge-approved">確定済</span>
                                            {% else %}
                                                <span class="badge-pending">未確定</span>
                                            {% endif %}
                                        </td>
                                        <td>{{ log.date }}</td>
                                        <td><strong>{{ log.staff_name }}</strong></td>
                                        <td style="color:#aaa;">
                                            {{ log.clock_in or '-' }} 〜 {{ log.clock_out or '勤務中' }}
                                        </td>
                                        <td>
                                            <input type="text" id="approve_in_{{ log.id }}" value="{{ log.rounded_in or log.clock_in }}" style="width:55px; text-align:center;" placeholder="17:00">
                                            〜
                                            <input type="text" id="approve_out_{{ log.id }}" value="{{ log.rounded_out or log.clock_out }}" style="width:55px; text-align:center;" placeholder="02:00">
                                        </td>
                                        <td>
                                            {% if log.calc_hours > 0 %}
                                                <strong>{{ log.calc_hours }}h</strong> / <span style="color:#FFA726;">￥{{ "{:,}".format(log.calc_pay) }}</span>
                                            {% else %}
                                                <span style="color:#777;">-</span>
                                            {% endif %}
                                        </td>
                                        <td>
                                            <button onclick="window.customActions.approveTimeclock('{{ log.id }}', '{{ log.staff_name }}', '{{ log.date }}')" class="btn-green" style="padding: 4px 8px; font-size: 11px;">
                                                {% if log.approved == '1' %}再確定{% else %}✅ 承認して確定{% endif %}
                                            </button>
                                            <button onclick="window.customActions.deleteTimeclock('{{ log.id }}')" class="btn-danger" style="padding: 4px 6px; font-size: 10px;">削除</button>
                                        </td>
                                    </tr>
                                    {% endfor %}
                                    {% endif %}
                                </tbody>
                            </table>
                        </div>
                    </div>

                    <div class="card" style="border-left-color: #27ae60;">
                        <h3>📝 管理者による手動実績入力・修正 ({{ current_shop.short_name }})</h3>
                        <form onsubmit="window.customActions.submitActualWork(event)" style="display: flex; flex-wrap: wrap; gap: 8px; align-items: flex-end; flex-direction: row;">
                            <div style="flex: 1; min-width: 120px;">
                                <label style="font-size: 10px; display: block; margin-bottom: 2px;">営業日:</label>
                                <input type="date" id="act_date" required value="{{ today_date_str }}" style="width:100%; box-sizing:border-box;">
                            </div>
                            <div style="flex: 1; min-width: 120px;">
                                <label style="font-size: 10px; display: block; margin-bottom: 2px;">スタッフ名:</label>
                                <select id="act_staff" required style="width:100%; box-sizing:border-box;">
                                    <option value="">-- スタッフ選択 --</option>
                                    {% for staff in staffs.keys() %}<option value="{{ staff }}">{{ staff }}</option>{% endfor %}
                                </select>
                            </div>
                            <div style="flex: 1; min-width: 100px;">
                                <label style="font-size: 10px; display: block; margin-bottom: 2px;">出勤ステータス:</label>
                                <select id="act_status" required style="width:100%; box-sizing:border-box;">
                                    <option value="定時">定時</option><option value="遅刻">遅刻</option><option value="早退">早退</option><option value="残業">残業</option><option value="欠勤">欠勤</option>
                                </select>
                            </div>
                            <div style="flex: 1; min-width: 110px;">
                                <label style="font-size: 10px; display: block; margin-bottom: 2px;">開始時間:</label>
                                <select id="act_start_time" style="width:100%; box-sizing:border-box;">
                                    <option value="">未指定</option>{% for t in time_options %}<option value="{{ t }}">{{ t }}</option>{% endfor %}
                                </select>
                            </div>
                            <div style="flex: 1; min-width: 110px;">
                                <label style="font-size: 10px; display: block; margin-bottom: 2px;">終了時間 (深夜5時まで):</label>
                                <select id="act_end_time" style="width:100%; box-sizing:border-box;">
                                    <option value="">未指定</option>{% for t in time_options %}<option value="{{ t }}">{{ t }}</option>{% endfor %}
                                </select>
                            </div>
                            <button type="submit" class="btn-green" style="padding: 7px 15px; font-size: 12px; height: 31px;">実績を保存</button>
                        </form>
                    </div>

                    <div class="card">
                        <h3>💰 スタッフ別 確定実働・給料一覧 ({{ current_shop.short_name }} / {{ month_title }})</h3>
                        <p style="font-size: 11px; color: #aaa; margin-top: 0;">※管理者が承認・確定した勤怠データ（30分単位）のみから算出された確定給料です（スタッフ画面には給料は表示されません）。</p>
                        <div class="table-responsive">
                            <table>
                                <thead><tr><th>シフト番号</th><th>スタッフ名</th><th>基本ポジション</th><th>時給</th><th>確定出勤日数</th><th>総労働時間</th><th>概算給料</th></tr></thead>
                                <tbody>
                                    {% for staff, info in staffs.items() %}
                                    {% set summary = admin_staff_summaries.get(staff, {'days': 0, 'hours': 0.0, 'pay': 0}) %}
                                    <tr>
                                        <td style="color:#64b5f6; font-weight:bold;">{{ info.staff_code }}</td>
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
            function recalculateCountsForDate(date) {
                let k = 0, f = 0, y = 0;
                document.querySelectorAll(`.shift-pos-select[data-date="${date}"]`).forEach(sel => {
                    const val = sel.value;
                    if (val.includes('K')) k++;
                    else if (val.includes('F')) f++;
                    else if (val.includes('Y')) y++;
                });
                document.querySelectorAll(`.count-cell[data-date="${date}"]`).forEach(cell => {
                    const reqK = parseInt(cell.getAttribute('data-req-k') || '0', 10);
                    const reqF = parseInt(cell.getAttribute('data-req-f') || '0', 10);
                    const reqY = parseInt(cell.getAttribute('data-req-y') || '0', 10);

                    const kDiv = cell.querySelector('.cnt-k');
                    const fDiv = cell.querySelector('.cnt-f');
                    const yDiv = cell.querySelector('.cnt-y');

                    if (kDiv) { kDiv.innerText = `K: ${k} / ${reqK}`; kDiv.style.color = (k < reqK) ? '#ff5252' : '#4caf50'; }
                    if (fDiv) { fDiv.innerText = `F: ${f} / ${reqF}`; fDiv.style.color = (f < reqF) ? '#ff5252' : '#4caf50'; }
                    if (yDiv) { yDiv.innerText = `Y: ${y} / ${reqY}`; yDiv.style.color = (y < reqY) ? '#ff5252' : '#4caf50'; }
                });
            }

            const savedTab = sessionStorage.getItem('adminActiveTab');
            if (savedTab && document.getElementById(savedTab)) {
                document.querySelectorAll('.admin-tab-content').forEach(c => c.classList.remove('active'));
                document.querySelectorAll('.admin-tab-btn').forEach(b => b.classList.remove('active'));
                document.getElementById(savedTab).classList.add('active');
                const btn = document.getElementById('btn-' + savedTab);
                if (btn) btn.classList.add('active');
            }

            let secondsLeft = 30;
            setInterval(() => {
                secondsLeft--;
                const cdEl = document.getElementById('refreshCountdown');
                if (cdEl) cdEl.innerText = `🔄 自動更新まで ${secondsLeft}秒`;

                if (secondsLeft <= 0) {
                    const modalOpen = document.getElementById('passModal').style.display === 'flex';
                    const activeTag = document.activeElement ? document.activeElement.tagName : '';
                    const isTyping = (activeTag === 'INPUT' || activeTag === 'SELECT');
                    if (!modalOpen && !isTyping) {
                        if (cdEl) cdEl.innerText = '🔄 更新中...';
                        location.reload();
                    } else {
                        secondsLeft = 10;
                    }
                }
            }, 1000);

            window.customActions = {
                currentShopId: '{{ current_shop.id }}', targetStaffForPass: '', currentYear: {{ current_year }}, currentMonth: {{ current_month }}, selectedWeekIdx: '{{ selected_week_idx }}',
                syncFromSheets: function() {
                    const cdEl = document.getElementById('refreshCountdown');
                    if (cdEl) cdEl.innerText = '🔄 同期中...';
                    fetch('/api/sync_from_sheets', { method: 'POST' }).then(res => res.json()).then(data => { if(data.success) location.reload(); else alert(data.message); }).catch(e => alert("通信エラー"));
                },
                switchAdminTab: function(tabId, btnElement) {
                    sessionStorage.setItem('adminActiveTab', tabId);
                    document.querySelectorAll('.admin-tab-content').forEach(c => c.classList.remove('active'));
                    document.querySelectorAll('.admin-tab-btn').forEach(b => b.classList.remove('active'));
                    document.getElementById(tabId).classList.add('active');
                    btnElement.classList.add('active');
                },
                openPassModal: function(name, currentWage, defaultPos, staffCode) {
                    this.targetStaffForPass = name;
                    document.getElementById('modalStaffLabel').innerText = `「${name}」さんの情報変更`;
                    document.getElementById('modalNewCode').value = staffCode || '';
                    document.getElementById('modalNewPass').value = '';
                    document.getElementById('modalNewWage').value = currentWage;
                    document.getElementById('modalNewPos').value = defaultPos || '';
                    document.getElementById('passModal').style.display = 'flex';
                },
                closePassModal: function() { document.getElementById('passModal').style.display = 'none'; },
                submitPassModal: function() {
                    fetch('/api/update_staff_info', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            staff_name: this.targetStaffForPass,
                            staff_code: document.getElementById('modalNewCode').value.trim(),
                            new_password: document.getElementById('modalNewPass').value,
                            new_wage: document.getElementById('modalNewWage').value,
                            default_position: document.getElementById('modalNewPos').value
                        })
                    })
                    .then(res => res.json()).then(data => { alert(data.message); this.closePassModal(); if(data.success) location.reload(); }).catch(e => alert("通信エラー"));
                },
                generateAutoShift: function() {
                    if(confirm('自動シフト作成を開始しますか？')) {
                        fetch('/api/generate_auto_shift', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ shop_id: this.currentShopId, year: this.currentYear, month: this.currentMonth, unit: this.selectedWeekIdx }) })
                        .then(res => res.json()).then(data => { alert(data.message); if(data.success) location.reload(); }).catch(e => alert("通信エラー"));
                    }
                },
                publishShifts: function() {
                    if(confirm('シフト内容を確定し公開しますか？')) {
                        fetch('/api/publish_shifts', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ shop_id: this.currentShopId }) })
                        .then(res => res.json()).then(data => { alert(data.message); if(data.success) location.reload(); }).catch(e => alert("通信エラー"));
                    }
                },
                submitDeadline: function(e) {
                    e.preventDefault();
                    fetch('/api/update_deadline', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ deadline_first: document.getElementById('deadline_first').value, deadline_second: document.getElementById('deadline_second').value }) })
                    .then(res => res.json()).then(data => alert(data.message)).catch(e => alert("通信エラー"));
                },
                submitAddStaff: function(e) {
                    e.preventDefault();
                    fetch('/api/add_staff', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            shop_id: this.currentShopId,
                            staff_code: document.getElementById('new_staff_code').value.trim(),
                            name: document.getElementById('new_staff_name').value,
                            wage: document.getElementById('new_staff_wage').value,
                            default_position: document.getElementById('new_staff_pos').value
                        })
                    })
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
                    recalculateCountsForDate(date);
                    secondsLeft = 30;

                    const statusEl = document.getElementById('saveStatus');
                    if (statusEl) { statusEl.innerText = '⏳ 保存中...'; statusEl.style.color = '#FFA726'; }
                    const start = document.getElementById(`start_${staff_name}_${date}`).value;
                    const end = document.getElementById(`end_${staff_name}_${date}`).value;
                    fetch('/api/update_shift', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ shop_id: this.currentShopId, staff_name, date, shift: document.getElementById(`shift_${staff_name}_${date}`).value, time: (start || end) ? `${start}-${end}` : '' }) })
                    .then(r => r.json()).then(d => {
                        if (statusEl && d.saved_at) { statusEl.innerText = `☁️ 保存済 (${d.saved_at})`; statusEl.style.color = '#4caf50'; }
                    });
                },
                applyOneWish: function(staff_name, date, shift, time) {
                    if(confirm(`希望を反映しますか？`)) {
                        fetch('/api/apply_one_wish', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ shop_id: this.currentShopId, staff_name, date, shift, time }) }).then(res => res.json()).then(data => { alert(data.message); location.reload(); });
                    }
                },
                applyAllWishes: function() {
                    if(confirm('すべての希望シフトを反映しますか？')) {
                        fetch('/api/apply_all_wishes', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ shop_id: this.currentShopId }) }).then(res => res.json()).then(data => { alert(data.message); location.reload(); });
                    }
                },
                submitActualWork: function(e) {
                    e.preventDefault();
                    const staff = document.getElementById('act_staff').value;
                    if (!staff) { alert("スタッフ名を選択してください"); return; }
                    const start = document.getElementById('act_start_time').value;
                    const end = document.getElementById('act_end_time').value;
                    fetch('/api/update_actual_shift', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ shop_id: this.currentShopId, staff_name: staff, date: document.getElementById('act_date').value, status: document.getElementById('act_status').value, time: (start || end) ? `${start}-${end}` : '' }) })
                    .then(res => res.json()).then(data => { alert(data.message); if(data.success) location.reload(); }).catch(e => alert("通信エラー"));
                },
                approveTimeclock: function(logId, staffName, date) {
                    const clockIn = document.getElementById(`approve_in_${logId}`).value.trim();
                    const clockOut = document.getElementById(`approve_out_${logId}`).value.trim();
                    if (!clockIn || !clockOut) {
                        alert("出勤時間と退勤時間の両方が入力されている必要があります。");
                        return;
                    }
                    fetch('/api/approve_timeclock', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ id: logId, shop_id: this.currentShopId, staff_name: staffName, date: date, clock_in: clockIn, clock_out: clockOut })
                    }).then(res => res.json()).then(data => { alert(data.message); if(data.success) location.reload(); }).catch(e => alert("通信エラー"));
                },
                deleteTimeclock: function(logId) {
                    if(confirm('この打刻ログをスプレッドシートから削除しますか？')) {
                        fetch('/api/delete_timeclock', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: logId }) })
                        .then(res => res.json()).then(data => { alert(data.message); if(data.success) location.reload(); }).catch(e => alert("通信エラー"));
                    }
                },
                deleteMonthTimeclock: function(year, month) {
                    if(confirm(`【警告】${year}年${month}月の打刻ログをスプレッドシートからすべて削除しますか？\n（※すでに承認・確定した給料計算用の実績データは消えずに残ります）`)) {
                        fetch('/api/delete_month_timeclock', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ shop_id: this.currentShopId, year: year, month: month })
                        })
                        .then(res => res.json()).then(data => { alert(data.message); if(data.success) location.reload(); }).catch(e => alert("通信エラー"));
                    }
                }
            };
        </script>
    {% endif %}
</body>
</html>
"""

# ==========================================
# テンプレート (HTML) - 設定画面
# ==========================================
SETTINGS_TEMPLATE = """
<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>⚙️ 店舗・管理者設定</title>
    <style>
        body { font-family: sans-serif; margin: 0; background: #121212; color: #E0E0E0; display: flex; min-height: 100vh; }
        header { background: #1E1E1E; padding: 10px 20px; display: flex; justify-content: space-between; align-items: center; border-bottom: 2px solid #D4AF37; width: 100%; box-sizing: border-box;}
        .container { display: flex; padding: 20px; gap: 15px; width: 100%; flex-wrap: wrap; }
        aside { width: 180px; background: #1E1E1E; padding: 15px; border-radius: 8px; }
        aside h2 { color: #D4AF37; font-size: 15px; border-bottom: 1px solid #D4AF37; }
        aside a { color: #E0E0E0; text-decoration: none; display: block; padding: 5px; margin-top: 5px;}
        .card { background: #1E1E1E; padding: 20px; border-radius: 8px; width: 340px; border-left: 5px solid #D4AF37; margin-bottom: 15px; }
        input, button, select { padding: 8px; margin-top: 6px; width: 100%; box-sizing: border-box; background: #262626; color: white; border: 1px solid #333; border-radius: 4px; }
        button { background: #D4AF37; color: #121212; border: none; font-weight: bold; cursor: pointer; margin-top: 10px; }
    </style>
</head>
<body>
    <div style="display:flex; flex-direction:column; width:100%;">
        <header><h1>⚙️ 店舗・管理者設定</h1><a href="/"><button style="width:auto; padding:5px 10px;">戻る</button></a></header>
        <div class="container">
            <aside><h2>メニュー</h2><a href="/">📊 シフト・勤怠管理</a><a href="/admin/settings" style="background:#D4AF37; color:#121212;">⚙️ 設定</a></aside>
            
            <div style="display:flex; flex-direction:column; gap:15px;">
                <div class="card">
                    <h3 style="color:#D4AF37; margin-top:0;">🏪 店舗の追加</h3>
                    <div style="margin-bottom:12px;">
                        <h4 style="font-size:12px; margin:5px 0; color:#FFA726;">既存の店舗一覧:</h4>
                        {% for s in shops %}
                        <div style="font-size:12px; background:#262626; padding:6px; margin-bottom:4px; border-radius:4px; display:flex; justify-content:space-between;">
                            <span><strong>{{ s.name }}</strong> ({{ s.short_name }})</span>
                        </div>
                        {% endfor %}
                    </div>

                    <form onsubmit="event.preventDefault(); fetch('/api/add_shop', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({shop_name: document.getElementById('new_sn').value, short_name: document.getElementById('new_short_n').value})}).then(r=>r.json()).then(d=>{alert(d.message); location.reload();});">
                        <label style="font-size:11px;">新しい店舗名:</label>
                        <input type="text" id="new_sn" placeholder="例: 久留米焼鳥 つかさ 3号店" required>
                        <label style="font-size:11px; margin-top:6px; display:block;">店舗略称:</label>
                        <input type="text" id="new_short_n" placeholder="例: 3号店" required>
                        <button type="submit" style="background:#27ae60; color:white;">新しい店舗を追加</button>
                    </form>
                </div>

                <div class="card">
                    <h3 style="color:#D4AF37; margin-top:0;">🔒 パスワード設定</h3>
                    <form onsubmit="event.preventDefault(); fetch('/api/update_password', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({new_password: document.getElementById('np').value, new_clock_pin: document.getElementById('ncp').value})}).then(r=>r.json()).then(d=>{alert(d.message); location.reload();});">
                        <label style="font-size:11px;">管理者ログインパスワード (変更時のみ入力):</label>
                        <input type="password" id="np" placeholder="新しい管理者パスワード">
                        <label style="font-size:11px; margin-top:8px; display:block;">店舗タイムカード解除PIN (現在: {{ clock_pin }}):</label>
                        <input type="text" id="ncp" placeholder="例: 0000" value="{{ clock_pin }}">
                        <button type="submit">パスワード設定を保存</button>
                    </form>
                </div>
            </div>
        </div>
    </div>
</body>
</html>
"""

# ==========================================
# テンプレート (HTML) - 店舗タイムカード打刻画面 (/clock)
# ==========================================
CLOCK_TEMPLATE = """
<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>⏱️ タイムカード打刻 - {{ current_shop.short_name }}</title>
    <style>
        body { font-family: 'Noto Sans JP', sans-serif; margin: 0; background: #0f0f0f; color: #E0E0E0; display: flex; flex-direction: column; align-items: center; min-height: 100vh; }
        header { background: #1E1E1E; padding: 12px 20px; border-bottom: 2px solid #D4AF37; width: 100%; box-sizing: border-box; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; }
        .clock-box { text-align: center; margin: 20px 0 10px 0; }
        .date-disp { font-size: 16px; color: #aaa; }
        .time-disp { font-size: 48px; font-weight: bold; color: #D4AF37; letter-spacing: 2px; font-variant-numeric: tabular-nums; }
        .card { background: #1E1E1E; padding: 20px; border-radius: 10px; width: 90%; max-width: 460px; border: 1px solid #333; box-shadow: 0 6px 15px rgba(0,0,0,0.6); }
        select, input, button { padding: 12px; width: 100%; box-sizing: border-box; margin-bottom: 12px; background: #262626; color: white; border: 1px solid #444; border-radius: 6px; font-size: 16px; }
        button { font-weight: bold; cursor: pointer; border: none; transition: 0.2s; }
        .btn-in { background: #27ae60; color: white; font-size: 20px; padding: 16px; }
        .btn-out { background: #c0392b; color: white; font-size: 20px; padding: 16px; }
        .btn-in:active, .btn-out:active { transform: scale(0.98); }
        .status-list { margin-top: 20px; width: 90%; max-width: 460px; }
        .status-item { background: #1a1a1a; padding: 10px 14px; border-radius: 6px; margin-bottom: 6px; display: flex; justify-content: space-between; align-items: center; border-left: 4px solid #27ae60; font-size: 14px; }
        .status-item.done { border-left-color: #777; color: #888; }
        .modal-overlay { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.85); z-index: 9999; justify-content: center; align-items: center; }
        .modal-box { background: #1E1E1E; padding: 22px; border-radius: 10px; width: 88%; max-width: 340px; border-left: 5px solid #D4AF37; box-shadow: 0 8px 20px rgba(0,0,0,0.8); }
        .staff-preview { text-align: center; font-size: 15px; font-weight: bold; padding: 8px; border-radius: 6px; background: #161616; margin-bottom: 12px; border: 1px solid #333; min-height: 22px; }
    </style>
</head>
<body>
    <header>
        <div style="font-weight:bold; color:#D4AF37;">⏱️ {{ current_shop.name }} タイムカード</div>
        <div style="display:flex; gap:8px; align-items:center;">
            <button type="button" onclick="openAdminAuthModal()" style="width:auto; margin:0; background:#262626; color:#aaa; padding:6px 10px; border-radius:4px; font-size:11px; border:1px solid #444;">🔒 管理者メニュー</button>
        </div>
    </header>

    <div id="adminAuthModal" class="modal-overlay">
        <div class="modal-box">
            <h3 style="margin-top:0; color:#D4AF37; font-size:16px;">🔒 管理者認証</h3>
            <p style="font-size:12px; color:#aaa; margin-bottom:12px;">管理画面への移動や店舗切り替えには管理者パスワードが必要です。</p>
            <input type="password" id="modalAdminPass" placeholder="管理者パスワードを入力" style="font-size:14px; padding:10px; margin-bottom:10px;">
            <div style="display:flex; gap:8px; margin-bottom:12px;">
                <button type="button" onclick="closeAdminAuthModal()" style="background:#555; color:white; padding:10px; font-size:13px; margin:0;">キャンセル</button>
                <button type="button" onclick="submitAdminReturn()" style="background:#D4AF37; color:#121212; padding:10px; font-size:13px; margin:0;">管理画面へ移動</button>
            </div>
            <div style="border-top:1px solid #333; padding-top:10px;">
                <label style="font-size:11px; color:#888; display:block; margin-bottom:4px;">または店舗を切り替え（管理者パスワード入力後に選択）:</label>
                <select id="modalShopSelect" onchange="switchClockShop()" style="font-size:13px; padding:8px; margin:0;">
                    {% for s in shops %}<option value="{{ s.id }}" {% if s.id == current_shop.id %}selected{% endif %}>{{ s.name }}</option>{% endfor %}
                </select>
            </div>
        </div>
    </div>

    {% if not clock_unlocked %}
    <div class="card" style="margin-top: 60px;">
        <h3 style="margin-top:0; color:#D4AF37; text-align:center;">🔒 店舗タイムカード認証</h3>
        <p style="font-size:12px; color:#aaa; text-align:center;">店舗端末用のPINコードを入力してください</p>
        <form method="POST" action="/clock_unlock">
            <input type="hidden" name="shop_id" value="{{ current_shop.id }}">
            <input type="password" name="clock_pin" placeholder="店舗PINコード (初期: 0000)" required style="text-align:center; letter-spacing:4px;">
            <button type="submit" style="background:#D4AF37; color:#121212;">打刻画面を開く</button>
        </form>
        {% if pin_error %}<p style="color:#ff5252; font-size:12px; text-align:center;">PINコードが違います</p>{% endif %}
    </div>
    {% else %}
    <div class="clock-box">
        <div class="date-disp" id="liveDate">----/--/-- (--)</div>
        <div class="time-disp" id="liveTime">--:--:--</div>
    </div>

    <div class="card">
        <input type="text" id="clock_staff_code" inputmode="numeric" pattern="[0-9]*" placeholder="🔢 シフト番号を入力 (例: 101)" oninput="lookupStaffByCode()" style="text-align:center; font-size:20px; font-weight:bold; letter-spacing:2px; margin-bottom:8px;">
        <div id="staffNamePreview" class="staff-preview" style="color:#777;">番号を入力すると名前が表示されます</div>
        <input type="password" id="clock_staff_pass" inputmode="numeric" placeholder="🔑 個人パスワード (初期: 0000)" style="text-align:center; font-size:18px; letter-spacing:3px;">
        <div style="display:flex; gap:10px; margin-top:5px;">
            <button type="button" class="btn-in" onclick="submitClock('in')">☀️ 出勤</button>
            <button type="button" class="btn-out" onclick="submitClock('out')">🌙 退勤</button>
        </div>
        <p id="clockRefreshInfo" style="font-size:11px; color:#64b5f6; margin:5px 0 0 0; text-align:center;">🔄 自動更新まで 30秒</p>
    </div>

    <div class="status-list">
        <h4 style="margin:0 0 8px 0; color:#D4AF37; font-size:13px;">📋 本日の営業日打刻状況 ({{ today_str }})</h4>
        {% if today_logs|length == 0 %}
            <div style="font-size:12px; color:#666; text-align:center; padding:10px;">本日の打刻はまだありません</div>
        {% else %}
            {% for log in today_logs %}
            <div class="status-item {% if log.clock_out %}done{% endif %}">
                <span><strong>{{ log.staff_name }}</strong></span>
                <span>
                    出勤: <strong style="color:#4caf50;">{{ log.clock_in }}</strong>
                    {% if log.clock_out %} / 退勤: <strong style="color:#ff5252;">{{ log.clock_out }}</strong>{% else %} <span style="color:#FFA726;">(勤務中)</span>{% endif %}
                </span>
            </div>
            {% endfor %}
        {% endif %}
    </div>

    <script>
        const shopStaffCodeMap = {{ staff_code_map_json|safe }};

        function lookupStaffByCode() {
            const code = document.getElementById('clock_staff_code').value.trim();
            const previewEl = document.getElementById('staffNamePreview');
            if (!code) {
                previewEl.innerText = '番号を入力すると名前が表示されます';
                previewEl.style.color = '#777';
                return;
            }
            if (shopStaffCodeMap[code]) {
                previewEl.innerText = `👤 ${shopStaffCodeMap[code]} さん`;
                previewEl.style.color = '#4caf50';
            } else {
                previewEl.innerText = '⚠️ 該当する番号が見つかりません';
                previewEl.style.color = '#ff5252';
            }
        }

        function openAdminAuthModal() {
            document.getElementById('modalAdminPass').value = '';
            document.getElementById('adminAuthModal').style.display = 'flex';
            setTimeout(() => document.getElementById('modalAdminPass').focus(), 100);
        }
        function closeAdminAuthModal() {
            document.getElementById('adminAuthModal').style.display = 'none';
        }
        function submitAdminReturn() {
            const pw = document.getElementById('modalAdminPass').value;
            if (!pw) { alert('管理者パスワードを入力してください'); return; }
            fetch('/api/verify_admin_return', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ password: pw })
            })
            .then(r => r.json())
            .then(d => {
                if (d.success) {
                    window.location.href = '/?shop_id={{ current_shop.id }}';
                } else {
                    alert('⚠️ 管理者パスワードが違います');
                }
            })
            .catch(() => alert('通信エラー'));
        }
        function switchClockShop() {
            const pw = document.getElementById('modalAdminPass').value;
            const targetShop = document.getElementById('modalShopSelect').value;
            if (!pw) {
                alert('店舗を切り替えるには、先に上の欄に管理者パスワードを入力してください');
                document.getElementById('modalShopSelect').value = '{{ current_shop.id }}';
                return;
            }
            fetch('/api/verify_admin_return', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ password: pw, keep_clock_mode: true })
            })
            .then(r => r.json())
            .then(d => {
                if (d.success) {
                    window.location.href = '/clock?shop_id=' + targetShop;
                } else {
                    alert('⚠️ 管理者パスワードが違います');
                    document.getElementById('modalShopSelect').value = '{{ current_shop.id }}';
                }
            })
            .catch(() => alert('通信エラー'));
        }

        function updateClock() {
            const now = new Date();
            const days = ['日', '月', '火', '水', '木', '金', '土'];
            const y = now.getFullYear();
            const m = String(now.getMonth() + 1).padStart(2, '0');
            const d = String(now.getDate()).padStart(2, '0');
            const w = days[now.getDay()];
            const hh = String(now.getHours()).padStart(2, '0');
            const mm = String(now.getMinutes()).padStart(2, '0');
            const ss = String(now.getSeconds()).padStart(2, '0');
            document.getElementById('liveDate').innerText = `${y}年${m}月${d}日 (${w})`;
            document.getElementById('liveTime').innerText = `${hh}:${mm}:${ss}`;
        }
        setInterval(updateClock, 1000);
        updateClock();

        let clockSec = 30;
        setInterval(() => {
            clockSec--;
            const infoEl = document.getElementById('clockRefreshInfo');
            if (infoEl) infoEl.innerText = `🔄 自動更新まで ${clockSec}秒`;
            if (clockSec <= 0) {
                const codeVal = document.getElementById('clock_staff_code').value;
                const passVal = document.getElementById('clock_staff_pass').value;
                const modalOpen = document.getElementById('adminAuthModal').style.display === 'flex';
                if (!codeVal && !passVal && !modalOpen) {
                    location.reload();
                } else {
                    clockSec = 10;
                }
            }
        }, 1000);

        function submitClock(actionType) {
            const staffCode = document.getElementById('clock_staff_code').value.trim();
            const pass = document.getElementById('clock_staff_pass').value;
            if (!staffCode) { alert('シフト番号を入力してください'); return; }
            if (!pass) { alert('個人パスワードを入力してください'); return; }

            fetch('/api/timeclock', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    shop_id: '{{ current_shop.id }}',
                    staff_code: staffCode,
                    password: pass,
                    action: actionType
                })
            })
            .then(r => r.json())
            .then(data => {
                alert(data.message);
                if (data.success) location.reload();
            })
            .catch(() => alert('通信エラーが発生しました'));
        }
    </script>
    {% endif %}
</body>
</html>
"""

# ==========================================
# テンプレート (HTML) - スタッフ個人画面 (/staff)
# ==========================================
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
        select, input, button { padding: 10px; width: 100%; box-sizing: border-box; margin-bottom: 10px; background: #262626; color: white; border: 1px solid #333; border-radius: 4px; font-size: 14px; }
        button { background: #D4AF37; color: #121212; font-weight: bold; cursor: pointer; border: none; }
        .tab-menu { display: flex; gap: 4px; margin-bottom: 15px; }
        .tab-btn { flex: 1; padding: 8px 4px; background: #333; text-align: center; border-radius: 4px; cursor: pointer; font-size:12px; }
        .tab-btn.active { background: #D4AF37; color: #121212; font-weight: bold; }
        .tab-content { display: none; }
        .tab-content.active { display: block; }
        .day-row { padding: 10px; border-bottom: 1px solid #333; background: #1a1a1a; margin-bottom: 5px; border-radius: 4px; }
        .staff-table { width: 100%; border-collapse: collapse; font-size: 12px; }
        .staff-table th, .staff-table td { border: 1px solid #333; padding: 8px; text-align: center; }
        .staff-table th { background: #262626; color: #D4AF37; }
        .clickable-date { color: #D4AF37; cursor: pointer; text-decoration: underline; font-weight: bold; }
        .modal { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.8); z-index: 9999; justify-content: center; align-items: center; }
        .modal-content { background: #1E1E1E; padding: 20px; border-radius: 8px; width: 85%; max-width: 380px; border-left: 5px solid #D4AF37; }
        .login-preview { text-align: center; font-size: 13px; font-weight: bold; padding: 8px; border-radius: 4px; background: #161616; margin-bottom: 10px; border: 1px solid #333; }
    </style>
</head>
<body>
    <header>
        <h1 style="margin:0; font-size:18px; color:#D4AF37;">{{ current_shop.short_name }}</h1>
        <div style="display:flex; gap:10px; align-items:center;">
            {% if logged_in %}
            <button onclick="window.staffActions.manualRefresh()" style="width:auto; margin:0; padding:4px 8px; font-size:11px; background:#3498db; color:white;">🔄 最新更新</button>
            <a href="/staff_logout" style="color:#FFA726; font-size:12px; text-decoration:none;">ログアウト</a>
            {% endif %}
        </div>
    </header>
    <div class="card">
        {% if not logged_in %}
        <form method="POST" action="/staff_login">
            <h2 style="margin-top:0; font-size:16px; color:#D4AF37;">👤 スタッフログイン</h2>
            <p style="font-size:11px; color:#aaa; margin-top:0;">店舗タイムカードと同じシフト番号・パスワードでログインできます。</p>
            <select name="shop_id" id="login_shop_id" onchange="previewLoginStaff()" required>
                {% for s in shops %}<option value="{{ s.id }}">{{ s.name }}</option>{% endfor %}
            </select>
            <input type="text" name="staff_code" id="login_staff_code" inputmode="numeric" pattern="[0-9]*" placeholder="🔢 シフト番号を入力 (例: 101)" oninput="previewLoginStaff()" required style="text-align:center; font-size:16px; font-weight:bold; letter-spacing:2px;">
            <div id="loginStaffPreview" class="login-preview" style="color:#777;">番号を入力すると名前が表示されます</div>
            <input type="password" name="staff_password" inputmode="numeric" placeholder="🔑 パスワード (初期: 0000)" required style="text-align:center;">
            <button type="submit">ログイン</button>
            {% if auth_error %}<p style="color:#ff5252; font-size:12px; margin-top:8px; font-weight:bold;">⚠️ 店舗・シフト番号・パスワードのいずれかが間違っています。</p>{% endif %}
        </form>
        
        <script>
            const allStaffsDb = {{ all_staffs_json|safe }};
            function previewLoginStaff() {
                const shopId = document.getElementById('login_shop_id').value;
                const code = document.getElementById('login_staff_code').value.trim();
                const previewEl = document.getElementById('loginStaffPreview');
                if (!code) {
                    previewEl.innerText = '番号を入力すると名前が表示されます';
                    previewEl.style.color = '#777';
                    return;
                }
                let matchedName = '';
                for (const [name, info] of Object.entries(allStaffsDb)) {
                    const sId = info.shop_id || 'shop1';
                    const sCode = String(info.staff_code || '').trim();
                    if (sId === shopId && sCode === code) {
                        matchedName = name;
                        break;
                    }
                }
                if (matchedName) {
                    previewEl.innerText = `👤 ${matchedName} さん`;
                    previewEl.style.color = '#4caf50';
                } else {
                    previewEl.innerText = '⚠️ 選択中の店舗に該当番号がありません';
                    previewEl.style.color = '#ff5252';
                }
            }
        </script>

        {% else %}
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;">
            <span style="font-size:13px; color:#D4AF37; font-weight:bold;">👤 {{ selected_staff }} さん <small style="color:#64b5f6;">(No.{{ staff_code }})</small></span>
            <span id="staffRefreshCountdown" style="font-size:10px; color:#64b5f6;">🔄 自動更新まで 30秒</span>
        </div>
        <form method="GET" action="/staff" style="display:flex; gap:5px;">
            <select name="year" onchange="this.form.submit()"><option value="2025" {% if current_year == 2025 %}selected{% endif %}>2025年</option><option value="2026" {% if current_year == 2026 %}selected{% endif %}>2026年</option></select>
            <select name="month" onchange="this.form.submit()">{% for m in range(1,13) %}<option value="{{ m }}" {% if current_month == m %}selected{% endif %}>{{ m }}月</option>{% endfor %}</select>
        </form>
        
        <div class="tab-menu">
            <div id="btn-t1" class="tab-btn active" onclick="window.staffActions.switchTab('t1', this)">📝 希望申請</div>
            <div id="btn-t2" class="tab-btn" onclick="window.staffActions.switchTab('t2', this)">📅 確定シフト</div>
            <div id="btn-t3" class="tab-btn" onclick="window.staffActions.switchTab('t3', this)">⏱️ 勤務履歴</div>
        </div>

        <!-- タブ1: 希望申請 -->
        <div id="t1" class="tab-content active">
            <div style="font-size:11px; color:#FFA726; margin-bottom:8px;">
                前半締切: {{ deadline_first_str }}<br>
                後半締切: {{ deadline_second_str }}
            </div>
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

        <!-- タブ2: 確定シフト -->
        <div id="t2" class="tab-content">
            <p style="font-size:11px; color:#888; margin-top:0;">※日付をタップするとその日の出勤メンバーが表示されます</p>
            <table class="staff-table">
                <tr><th>日付</th><th>シフト</th><th>時間</th></tr>
                {% for d in all_days %}
                {% set s = staff_shifts.get(d.full_date) %}
                {% if s and s.shift != '-' %}
                <tr>
                    <td><span class="clickable-date" onclick="window.staffActions.showDayMembers('{{ d.full_date }}', '{{ d.date_str }}({{ d.weekday }})')">{{ d.date_str }}({{ d.weekday }})</span></td>
                    <td>{{ s.shift.split(' ')[0] }}</td>
                    <td>{{ s.time_display }}</td>
                </tr>
                {% endif %}
                {% endfor %}
            </table>
        </div>

        <!-- タブ3: 勤務履歴・実働時間（給料は非表示） -->
        <div id="t3" class="tab-content">
            <div style="background:#262626; padding:12px; border-radius:6px; margin-bottom:12px; text-align:center;">
                <div style="font-size:12px; color:#aaa;">今月の確定勤務実績</div>
                <div style="font-size:18px; font-weight:bold; color:#D4AF37; margin-top:4px;">
                    出勤日数: {{ total_shift_days }}日 ／ 総実働: {{ total_hours }}時間
                </div>
            </div>
            <p style="font-size:11px; color:#888; margin-top:0;">※管理者承認後の確定時間（30分単位）が表示されます</p>
            <table class="staff-table">
                <tr><th>日付</th><th>状態</th><th>勤務時間</th><th>実働</th></tr>
                {% for d in daily_work_details %}
                <tr style="{% if not d.is_working %}color:#666;{% endif %}">
                    <td>{{ d.date }}</td>
                    <td>{{ d.shift }}</td>
                    <td>{{ d.time_display }}</td>
                    <td><strong>{{ d.hours }}h</strong></td>
                </tr>
                {% endfor %}
            </table>
        </div>

        <div id="memberModal" class="modal">
            <div class="modal-content">
                <h3 id="modalDateTitle" style="color:#D4AF37; margin-top:0; border-bottom:1px solid #333; padding-bottom:8px;"></h3>
                <div id="modalMemberList" style="font-size:13px; line-height:1.6; max-height:250px; overflow-y:auto; margin-bottom:15px;"></div>
                <button onclick="document.getElementById('memberModal').style.display='none'" style="background:#95a5a6; color:white; width:100%;">閉じる</button>
            </div>
        </div>
        
        <script>
            const allPublishedShifts = {{ all_published_json|safe }};

            const savedStaffTab = sessionStorage.getItem('staffActiveTab');
            if (savedStaffTab && document.getElementById(savedStaffTab)) {
                document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
                document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
                document.getElementById(savedStaffTab).classList.add('active');
                const btn = document.getElementById('btn-' + savedStaffTab);
                if (btn) btn.classList.add('active');
            }

            let staffSec = 30;
            setInterval(() => {
                const activeTab = sessionStorage.getItem('staffActiveTab') || 't1';
                const cdEl = document.getElementById('staffRefreshCountdown');
                if (activeTab === 't1') {
                    if (cdEl) cdEl.innerText = '⏸️ 入力中のため自動更新停止中';
                    return;
                }
                staffSec--;
                if (cdEl) cdEl.innerText = `🔄 自動更新まで ${staffSec}秒`;
                if (staffSec <= 0) {
                    const modalOpen = document.getElementById('memberModal').style.display === 'flex';
                    if (!modalOpen) {
                        if (cdEl) cdEl.innerText = '🔄 更新中...';
                        location.reload();
                    } else {
                        staffSec = 10;
                    }
                }
            }, 1000);

            window.staffActions = {
                switchTab: function(id, btn) {
                    sessionStorage.setItem('staffActiveTab', id);
                    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
                    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
                    document.getElementById(id).classList.add('active'); btn.classList.add('active');
                    staffSec = 30;
                },
                manualRefresh: function() {
                    fetch('/api/sync_from_sheets', { method: 'POST' })
                    .then(() => location.reload())
                    .catch(() => location.reload());
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
                },
                showDayMembers: function(fullDate, dateDisplay) {
                    document.getElementById('modalDateTitle').innerText = `📅 ${dateDisplay} の出勤メンバー`;
                    const dayMembers = allPublishedShifts.filter(s => s.date === fullDate && s.shift !== '-' && s.shift !== '希望休');
                    
                    let html = '';
                    if (dayMembers.length === 0) {
                        html = '<p style="color:#888;">この日の出勤予定メンバーはいません。</p>';
                    } else {
                        html = '<table class="staff-table" style="width:100%;"><tr><th>名前</th><th>役割</th><th>時間</th></tr>';
                        dayMembers.forEach(m => {
                            const pos = m.shift.split(' ')[0];
                            const timeStr = m.time ? (m.time.includes('-') ? m.time.replace('-', '〜') : m.time) : '時間指定なし';
                            html += `<tr><td><strong>${m.staff_name}</strong></td><td>${pos}</td><td>${timeStr}</td></tr>`;
                        });
                        html += '</table>';
                    }
                    document.getElementById('modalMemberList').innerHTML = html;
                    document.getElementById('memberModal').style.display = 'flex';
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
    shop_id = request.args.get('shop_id', session.get('active_shop_id', 'shop1'))
    session['active_shop_id'] = shop_id
    login_error = request.args.get('error') == '1'

    current_shop = next((s for s in shops if s['id'] == shop_id), shops[0])

    today = get_now_jst()
    today_biz_str = get_business_date(today)
    year, month = int(request.args.get('year', today.year)), int(request.args.get('month', today.month))
    week_idx = request.args.get('week_idx', 'all')
    tc_view = request.args.get('tc_view', 'today')
    first_half, second_half, month_title = get_month_days(year, month)
    
    shop_staffs = {name: info for name, info in staffs_db.items() if info.get('shop_id', 'shop1') == shop_id}
    shop_wishes = [w for w in wishes_data if w.get('shop_id', 'shop1') == shop_id]
    shop_shifts = [s for s in shifts_data if s.get('shop_id', 'shop1') == shop_id]
    shop_actual = [a for a in actual_shifts_data if a.get('shop_id', 'shop1') == shop_id]
    all_shop_timeclock = [t for t in timeclock_logs if t.get('shop_id', 'shop1') == shop_id]

    target_ym_prefix = f"{year}-{month:02d}-"
    if tc_view == 'month':
        raw_filtered = [t for t in all_shop_timeclock if t.get('date', '').startswith(target_ym_prefix)]
    else:
        raw_filtered = [t for t in all_shop_timeclock if t.get('date') == today_biz_str or t.get('approved') != '1']

    filtered_timeclock = []
    for t in raw_filtered:
        t_copy = dict(t)
        r_in = t_copy.get('rounded_in') or t_copy.get('clock_in', '')
        r_out = t_copy.get('rounded_out') or t_copy.get('clock_out', '')
        mins = calc_work_minutes(f"{r_in}-{r_out}") if (r_in and r_out) else 0
        hrs = round(mins / 60.0, 1)
        wage = shop_staffs.get(t_copy.get('staff_name', ''), {}).get('hourly_wage', 1100)
        t_copy['calc_hours'] = hrs
        t_copy['calc_pay'] = int(hrs * wage)
        filtered_timeclock.append(t_copy)

    today_staff_map = {}
    for act in shop_actual:
        if act.get('date') == today_biz_str and act.get('status') != '欠勤' and act.get('time'):
            sname = act['staff_name']
            mins = calc_work_minutes(act['time'])
            if mins > 0:
                hrs = round(mins / 60.0, 1)
                wage = shop_staffs.get(sname, {}).get('hourly_wage', 1100)
                today_staff_map[sname] = {
                    'name': sname,
                    'hours': hrs,
                    'pay': int(hrs * wage),
                    'is_confirmed': True
                }

    for t in all_shop_timeclock:
        if t.get('date') == today_biz_str:
            sname = t.get('staff_name', '')
            if sname in today_staff_map:
                continue
            r_in = t.get('rounded_in') or t.get('clock_in', '')
            r_out = t.get('rounded_out') or t.get('clock_out', '')
            if r_in and r_out:
                mins = calc_work_minutes(f"{r_in}-{r_out}")
                if mins > 0:
                    hrs = round(mins / 60.0, 1)
                    wage = shop_staffs.get(sname, {}).get('hourly_wage', 1100)
                    today_staff_map[sname] = {
                        'name': sname,
                        'hours': hrs,
                        'pay': int(hrs * wage),
                        'is_confirmed': (t.get('approved') == '1')
                    }

    today_items = list(today_staff_map.values())
    today_labor_summary = {
        'total_pay': sum(x['pay'] for x in today_items),
        'staff_items': today_items
    }

    admin_staff_summaries = {}
    for staff_name, s_info in shop_staffs.items():
        total_mins, total_days = 0, 0
        for day_info in (first_half + second_half):
            date = day_info['full_date']
            if date > today_biz_str: continue

            act = next((item for item in shop_actual if item['staff_name'] == staff_name and item['date'] == date), None)
            if act and act['status'] != '欠勤' and act.get('time'):
                mins = calc_work_minutes(act['time'])
                if mins > 0:
                    total_days += 1
                    total_mins += mins

        th = round(total_mins / 60.0, 1)
        admin_staff_summaries[staff_name] = {'days': total_days, 'hours': th, 'pay': int(th * s_info.get('hourly_wage', 1100))}

    dl_first = deadline_settings.get('deadline_first', '')
    dl_second = deadline_settings.get('deadline_second', '')

    return render_template_string(
        HTML_TEMPLATE,
        shops=shops,
        current_shop=current_shop,
        is_admin=is_admin,
        staffs=shop_staffs,
        requirements=required_settings,
        first_half=first_half,
        second_half=second_half,
        weeks=get_month_weeks(year, month),
        selected_week_idx=week_idx,
        wishes=shop_wishes,
        shifts=shop_shifts,
        all_shop_timeclock=all_shop_timeclock,
        timeclock_logs=filtered_timeclock,
        today_labor_summary=today_labor_summary,
        tc_view=tc_view,
        time_options=TIME_OPTIONS,
        month_title=month_title,
        current_year=year,
        current_month=month,
        today_date_str=today_biz_str,
        last_saved_str=last_saved_str,
        deadline_first_val=dl_first.replace(' ', 'T') if dl_first else '',
        deadline_second_val=dl_second.replace(' ', 'T') if dl_second else '',
        admin_staff_summaries=admin_staff_summaries,
        login_error=login_error
    )

@app.route('/admin_login', methods=['POST'])
def admin_login():
    shop_id = request.form.get('shop_id', 'shop1')
    if request.form.get('password') == admin_credentials['password']:
        session['is_admin'] = True
        return redirect(url_for('index', shop_id=shop_id))
    return redirect(url_for('index', shop_id=shop_id, error='1'))

@app.route('/admin/settings')
def admin_settings(): 
    if not session.get('is_admin'):
        return redirect(url_for('index'))
    return render_template_string(
        SETTINGS_TEMPLATE,
        shops=shops,
        clock_pin=admin_credentials.get('clock_pin', '0000')
    )

@app.route('/logout')
def logout():
    session.pop('is_admin', None)
    session.pop('logged_staff', None)
    session.pop('staff_shop_id', None)
    session.pop('clock_unlocked', None)
    return redirect(url_for('index'))

@app.route('/staff_logout')
def staff_logout():
    session.pop('logged_staff', None)
    session.pop('staff_shop_id', None)
    return redirect(url_for('staff_page'))

@app.route('/clock_mode')
def clock_mode():
    shop_id = request.args.get('shop_id', 'shop1')
    session.pop('is_admin', None)
    session['clock_unlocked'] = True
    return redirect(url_for('clock_page', shop_id=shop_id))

# --- 店舗タイムカード打刻画面 (/clock) ---
@app.route('/clock')
def clock_page():
    shop_id = request.args.get('shop_id', 'shop1')
    current_shop = next((s for s in shops if s['id'] == shop_id), shops[0])
    shop_staffs = {name: info for name, info in staffs_db.items() if info.get('shop_id', 'shop1') == shop_id}
    staff_code_map = {str(info.get('staff_code', '')).strip(): name for name, info in shop_staffs.items() if info.get('staff_code')}
    today_str = get_business_date(get_now_jst())
    today_logs = [t for t in timeclock_logs if t.get('shop_id', 'shop1') == shop_id and t.get('date') == today_str]

    return render_template_string(
        CLOCK_TEMPLATE,
        shops=shops,
        current_shop=current_shop,
        staffs=shop_staffs,
        staff_code_map_json=json.dumps(staff_code_map, ensure_ascii=False),
        clock_unlocked=session.get('clock_unlocked', False),
        pin_error=request.args.get('error') == '1',
        today_str=today_str,
        today_logs=today_logs
    )

@app.route('/clock_unlock', methods=['POST'])
def clock_unlock():
    shop_id = request.form.get('shop_id', 'shop1')
    if request.form.get('clock_pin') == admin_credentials.get('clock_pin', '0000'):
        session['clock_unlocked'] = True
        return redirect(url_for('clock_page', shop_id=shop_id))
    return redirect(url_for('clock_page', shop_id=shop_id, error='1'))

@app.route('/api/verify_admin_return', methods=['POST'])
def verify_admin_return():
    pw = request.json.get('password', '')
    keep_clock_mode = request.json.get('keep_clock_mode', False)
    if pw == admin_credentials['password']:
        if not keep_clock_mode:
            session['is_admin'] = True
        return jsonify({'success': True})
    return jsonify({'success': False})

# --- スタッフ個人画面 (/staff) ---
@app.route('/staff')
def staff_page():
    staff = session.get('logged_staff', '')
    shop_id = session.get('staff_shop_id', 'shop1')
    current_shop = next((s for s in shops if s['id'] == shop_id), shops[0])

    today = get_now_jst()
    today_biz_str = get_business_date(today)
    y, m = int(request.args.get('year', today.year)), int(request.args.get('month', today.month))
    fh, sh, _ = get_month_days(y, m)
    all_days = fh + sh
    
    shop_staffs = {name: info for name, info in staffs_db.items() if info.get('shop_id', 'shop1') == shop_id}
    shop_wishes = [w for w in wishes_data if w.get('shop_id', 'shop1') == shop_id]
    shop_published = [p for p in published_shifts_data if p.get('shop_id', 'shop1') == shop_id]
    shop_actual = [a for a in actual_shifts_data if a.get('shop_id', 'shop1') == shop_id]

    w_data, s_data, details, days, hours = {}, {}, [], 0, 0.0
    staff_code = ''
    if staff in shop_staffs:
        staff_code = shop_staffs[staff].get('staff_code', '')
        w_data = {w['date']: w for w in shop_wishes if w['staff_name'] == staff}
        s_data = {s['date']: {'shift': s['shift'], 'time_display': format_time_display(s['time'])} for s in shop_published if s['staff_name'] == staff}

        tm = 0
        for d in all_days:
            dt = d['full_date']
            if dt > today_biz_str: continue
            ps = next((i for i in shop_published if i['staff_name'] == staff and i['date'] == dt), None)
            act = next((i for i in shop_actual if i['staff_name'] == staff and i['date'] == dt), None)

            tr, stat = '-', '-'
            if act:
                if act['status'] == '欠勤':
                    stat = '欠勤'
                elif act.get('time'):
                    mins = calc_work_minutes(act['time'])
                    if mins > 0:
                        tr = act['time']
                        stat = act['status']
                        days += 1
                        tm += mins
                        dh = round(mins / 60.0, 1)
                        details.append({'date': d['date_str'], 'shift': stat, 'time_display': format_time_display(tr), 'hours': dh, 'is_working': True})
                        continue
            elif ps and ps['shift'] == '希望休':
                stat = '希望休'

            details.append({'date': d['date_str'], 'shift': stat, 'time_display': format_time_display(tr), 'hours': 0.0, 'is_working': False})
        hours = round(tm / 60.0, 1)

    dl_f = deadline_settings.get('deadline_first', '未設定')
    dl_s = deadline_settings.get('deadline_second', '未設定')
    auth_error = request.args.get('error') == '1'

    return render_template_string(
        STAFF_TEMPLATE,
        shops=shops,
        current_shop=current_shop,
        current_year=y,
        current_month=m,
        staffs=shop_staffs,
        selected_staff=staff,
        staff_code=staff_code,
        logged_in=bool(staff),
        all_days=all_days,
        staff_wishes=w_data,
        staff_shifts=s_data,
        daily_work_details=details,
        total_shift_days=days,
        total_hours=hours,
        time_options=TIME_OPTIONS,
        deadline_first_str=dl_f.replace('T', ' '),
        deadline_second_str=dl_s.replace('T', ' '),
        auth_error=auth_error,
        all_published_json=json.dumps(shop_published, ensure_ascii=False),
        all_staffs_json=json.dumps(staffs_db, ensure_ascii=False)
    )

@app.route('/staff_login', methods=['POST'])
def staff_login():
    shop_id = request.form.get('shop_id', 'shop1')
    staff_code = str(request.form.get('staff_code', '')).strip()
    password = request.form.get('staff_password', '')

    matched_name = next(
        (name for name, info in staffs_db.items()
         if info.get('shop_id', 'shop1') == shop_id and str(info.get('staff_code', '')).strip() == staff_code),
        None
    )

    if matched_name and staffs_db[matched_name]['password'] == password:
        session['logged_staff'] = matched_name
        session['staff_shop_id'] = shop_id
        return redirect(url_for('staff_page'))
    return redirect(url_for('staff_page', error='1'))

# ==========================================
# 打刻・承認API（即時完全保存）
# ==========================================
@app.route('/api/timeclock', methods=['POST'])
def api_timeclock():
    shop_id = request.json.get('shop_id', 'shop1')
    staff_code = str(request.json.get('staff_code', '')).strip()
    password = request.json.get('password')
    action = request.json.get('action')

    staff_name = next(
        (name for name, info in staffs_db.items()
         if info.get('shop_id', 'shop1') == shop_id and str(info.get('staff_code', '')).strip() == staff_code),
        None
    )

    if not staff_name:
        return jsonify({'success': False, 'message': '⚠️ 該当するシフト番号が見つかりません'})
    if staffs_db[staff_name]['password'] != password:
        return jsonify({'success': False, 'message': '⚠️ パスワードが間違っています'})

    now = get_now_jst()
    biz_date = get_business_date(now)
    time_str = now.strftime('%H:%M')

    open_log = next((t for t in reversed(timeclock_logs) if t.get('shop_id', 'shop1') == shop_id and t['staff_name'] == staff_name and not t.get('clock_out')), None)

    if action == 'in':
        if open_log:
            return jsonify({'success': False, 'message': f"⚠️ すでに {open_log['date']} {open_log['clock_in']} に出勤打刻済みです（先に退勤を押してください）。"})
        r_in = round_time_30min(time_str, mode='in')
        new_id = f"tc_{int(now.timestamp())}_{staff_name}"
        timeclock_logs.append({
            'id': new_id,
            'shop_id': shop_id,
            'staff_name': staff_name,
            'date': biz_date,
            'clock_in': time_str,
            'clock_out': '',
            'rounded_in': r_in,
            'rounded_out': '',
            'approved': '0'
        })
        save_data(immediate=True)
        return jsonify({'success': True, 'message': f"✅ {staff_name} さんの出勤を完全保存しました（打刻: {time_str} / 計算開始: {r_in}）"})

    elif action == 'out':
        if not open_log:
            return jsonify({'success': False, 'message': "⚠️ 出勤打刻が見つかりません。先に「出勤」を押してください。"})
        r_out = round_time_30min(time_str, mode='out')
        open_log['clock_out'] = time_str
        open_log['rounded_out'] = r_out
        save_data(immediate=True)
        return jsonify({'success': True, 'message': f"✅ {staff_name} さんの退勤を完全保存しました（打刻: {open_log['clock_in']}〜{time_str}）"})

    return jsonify({'success': False, 'message': '不正な操作です'})

@app.route('/api/approve_timeclock', methods=['POST'])
def api_approve_timeclock():
    if not session.get('is_admin'):
        return jsonify({'success': False, 'message': '管理者権限が必要です'})
    log_id = request.json.get('id')
    shop_id = request.json.get('shop_id', 'shop1')
    staff_name = request.json.get('staff_name')
    date = request.json.get('date')
    clock_in = request.json.get('clock_in')
    clock_out = request.json.get('clock_out')

    log = next((t for t in timeclock_logs if t.get('id') == log_id), None)
    if log:
        log['rounded_in'] = clock_in
        log['rounded_out'] = clock_out
        log['approved'] = '1'

    time_range = f"{clock_in}-{clock_out}"
    ex = next((a for a in actual_shifts_data if a.get('shop_id', 'shop1') == shop_id and a['staff_name'] == staff_name and a['date'] == date), None)
    if ex:
        ex['status'] = '打刻確定'
        ex['time'] = time_range
    else:
        actual_shifts_data.append({'shop_id': shop_id, 'staff_name': staff_name, 'date': date, 'status': '打刻確定', 'time': time_range})

    save_data(immediate=True)
    return jsonify({'success': True, 'message': f'{staff_name}さんの勤怠（{time_range}）を確定・保存しました'})

@app.route('/api/delete_timeclock', methods=['POST'])
def api_delete_timeclock():
    if not session.get('is_admin'):
        return jsonify({'success': False, 'message': '管理者権限が必要です'})
    log_id = request.json.get('id')
    global timeclock_logs
    timeclock_logs = [t for t in timeclock_logs if t.get('id') != log_id]
    save_data(immediate=True)
    return jsonify({'success': True, 'message': '削除しました'})

@app.route('/api/delete_month_timeclock', methods=['POST'])
def api_delete_month_timeclock():
    if not session.get('is_admin'):
        return jsonify({'success': False, 'message': '管理者権限が必要です'})
    shop_id = request.json.get('shop_id', 'shop1')
    year = int(request.json.get('year'))
    month = int(request.json.get('month'))
    prefix = f"{year}-{month:02d}-"

    global timeclock_logs
    before_count = len(timeclock_logs)
    timeclock_logs = [
        t for t in timeclock_logs
        if not (t.get('shop_id', 'shop1') == shop_id and t.get('date', '').startswith(prefix))
    ]
    deleted_count = before_count - len(timeclock_logs)
    save_data(immediate=True)
    return jsonify({'success': True, 'message': f'{year}年{month}月の打刻ログ（{deleted_count}件）をスプレッドシートから削除しました'})

# --- 既存APIエンドポイント ---
@app.route('/api/add_shop', methods=['POST'])
def add_shop():
    sn = request.json.get('shop_name')
    short_n = request.json.get('short_name')
    if sn and short_n:
        new_id = f"shop{len(shops) + 1}"
        shops.append({'id': new_id, 'name': sn, 'short_name': short_n})
        save_data(immediate=True)
        return jsonify({'success': True, 'message': '新しい店舗を追加しました'})
    return jsonify({'success': False, 'message': '入力内容に問題があります'})

@app.route('/api/add_staff', methods=['POST'])
def add_staff():
    name = request.json.get('name')
    shop_id = request.json.get('shop_id', 'shop1')
    code = str(request.json.get('staff_code', '')).strip()
    if not code:
        code = get_next_staff_code(staffs_db)
    elif any(str(info.get('staff_code', '')).strip() == code and info.get('shop_id', 'shop1') == shop_id for info in staffs_db.values()):
        return jsonify({'success': False, 'message': f'シフト番号「{code}」は同じ店舗ですでに使われています'})

    if name and name not in staffs_db:
        staffs_db[name] = {
            'password': '0000',
            'hourly_wage': int(request.json.get('wage', 1100)),
            'default_position': request.json.get('default_position', ''),
            'shop_id': shop_id,
            'staff_code': code
        }
        save_data(immediate=True)
        return jsonify({'success': True, 'message': f'追加完了（シフト番号: {code}）'})
    return jsonify({'success': False, 'message': 'すでに登録されている名前か、入力に不備があります'})

@app.route('/api/delete_staff', methods=['POST'])
def delete_staff():
    name = request.json.get('name')
    if name in staffs_db:
        del staffs_db[name]; save_data(immediate=True)
        return jsonify({'success': True, 'message': '削除完了'})
    return jsonify({'success': False, 'message': 'エラー'})

@app.route('/api/update_staff_info', methods=['POST'])
def update_staff_info():
    sn = request.json.get('staff_name')
    if sn in staffs_db:
        new_code = str(request.json.get('staff_code', '')).strip()
        shop_id = staffs_db[sn].get('shop_id', 'shop1')
        if new_code:
            if any(k != sn and str(v.get('staff_code', '')).strip() == new_code and v.get('shop_id', 'shop1') == shop_id for k, v in staffs_db.items()):
                return jsonify({'success': False, 'message': f'シフト番号「{new_code}」は他のスタッフと重複しています'})
            staffs_db[sn]['staff_code'] = new_code
        if request.json.get('new_password'): staffs_db[sn]['password'] = request.json.get('new_password')
        if request.json.get('new_wage'): staffs_db[sn]['hourly_wage'] = int(request.json.get('new_wage'))
        if request.json.get('default_position') is not None: staffs_db[sn]['default_position'] = request.json.get('default_position')
        save_data(immediate=True); return jsonify({'success': True, 'message': '更新完了'})
    return jsonify({'success': False, 'message': 'エラー'})

@app.route('/api/update_requirement', methods=['POST'])
def update_requirement():
    required_settings[request.json.get('day')] = {'K (キッチン)': int(request.json.get('req_k', 0)), 'F (フロント)': int(request.json.get('req_f', 0)), 'Y (やきば)': int(request.json.get('req_y', 0))}
    save_data(immediate=True); return jsonify({'success': True, 'message': '更新完了'})

@app.route('/api/update_deadline', methods=['POST'])
def update_deadline():
    df = request.json.get('deadline_first')
    ds = request.json.get('deadline_second')
    if df is not None and ds is not None:
        deadline_settings['deadline_first'] = df.replace('T', ' ')
        deadline_settings['deadline_second'] = ds.replace('T', ' ')
        save_data(immediate=True)
        return jsonify({'success': True, 'message': '締切日時を更新完了'})
    return jsonify({'success': False, 'message': 'エラー'})

@app.route('/api/update_password', methods=['POST'])
def update_password():
    np = request.json.get('new_password')
    ncp = request.json.get('new_clock_pin')
    if np:
        admin_credentials['password'] = np
    if ncp:
        admin_credentials['clock_pin'] = ncp
    save_data(immediate=True)
    return jsonify({'success': True, 'message': 'パスワード設定を更新しました'})

@app.route('/api/update_shift', methods=['POST'])
def update_shift():
    shop_id = request.json.get('shop_id', 'shop1')
    sn, dt, sh, tm = request.json.get('staff_name'), request.json.get('date'), request.json.get('shift'), request.json.get('time', '')
    ex = next((s for s in shifts_data if s.get('shop_id', 'shop1') == shop_id and s['staff_name'] == sn and s['date'] == dt), None)
    if ex:
        if sh == '-': shifts_data.remove(ex)
        else: ex['shift'], ex['time'] = sh, tm
    elif sh != '-': shifts_data.append({'shop_id': shop_id, 'staff_name': sn, 'date': dt, 'shift': sh, 'time': tm})
    save_data(immediate=False)
    return jsonify({'success': True, 'message': '更新完了', 'saved_at': get_now_jst().strftime('%H:%M:%S')})

@app.route('/api/update_actual_shift', methods=['POST'])
def update_actual_shift():
    shop_id = request.json.get('shop_id', 'shop1')
    sn, dt, st, tm = request.json.get('staff_name'), request.json.get('date'), request.json.get('status', '定時'), request.json.get('time', '')
    ex = next((a for a in actual_shifts_data if a.get('shop_id', 'shop1') == shop_id and a['staff_name'] == sn and a['date'] == dt), None)
    if ex: ex['status'], ex['time'] = st, tm
    else: actual_shifts_data.append({'shop_id': shop_id, 'staff_name': sn, 'date': dt, 'status': st, 'time': tm})
    save_data(immediate=True); return jsonify({'success': True, 'message': '保存完了'})

@app.route('/api/publish_shifts', methods=['POST'])
def publish_shifts():
    shop_id = request.json.get('shop_id', 'shop1')
    global published_shifts_data
    published_shifts_data = [p for p in published_shifts_data if p.get('shop_id', 'shop1') != shop_id]
    published_shifts_data.extend([dict(s) for s in shifts_data if s.get('shop_id', 'shop1') == shop_id])
    save_data(immediate=True); return jsonify({'success': True, 'message': '確定・公開し、シートへ保存しました'})

@app.route('/api/apply_one_wish', methods=['POST'])
def apply_one_wish():
    shop_id = request.json.get('shop_id', 'shop1')
    sn, dt, sh, tm = request.json.get('staff_name'), request.json.get('date'), request.json.get('shift'), request.json.get('time', '')
    ts = '希望休' if sh == '希望休' else staffs_db.get(sn, {}).get('default_position', 'K (キッチン)')
    ex = next((s for s in shifts_data if s.get('shop_id', 'shop1') == shop_id and s['staff_name'] == sn and s['date'] == dt), None)
    if ex: ex['shift'], ex['time'] = ts, tm
    else: shifts_data.append({'shop_id': shop_id, 'staff_name': sn, 'date': dt, 'shift': ts, 'time': tm})
    save_data(immediate=True); return jsonify({'success': True, 'message': '反映完了'})

@app.route('/api/apply_all_wishes', methods=['POST'])
def apply_all_wishes():
    shop_id = request.json.get('shop_id', 'shop1')
    shop_wishes = [w for w in wishes_data if w.get('shop_id', 'shop1') == shop_id]
    for w in shop_wishes:
        sn, dt, sh, tm = w['staff_name'], w['date'], w['shift'], w.get('time', '')
        ts = '希望休' if sh == '希望休' else staffs_db.get(sn, {}).get('default_position', 'K (キッチン)')
        ex = next((s for s in shifts_data if s.get('shop_id', 'shop1') == shop_id and s['staff_name'] == sn and s['date'] == dt), None)
        if ex: ex['shift'], ex['time'] = ts, tm
        else: shifts_data.append({'shop_id': shop_id, 'staff_name': sn, 'date': dt, 'shift': ts, 'time': tm})
    save_data(immediate=True); return jsonify({'success': True, 'message': '一括反映完了'})

@app.route('/api/submit_wish_shifts', methods=['POST'])
def submit_wish_shifts():
    global wishes_data
    sn = request.json.get('staff_name')
    shop_id = staffs_db.get(sn, {}).get('shop_id', 'shop1')
    wishes_data = [w for w in wishes_data if not (w.get('shop_id', 'shop1') == shop_id and w['staff_name'] == sn)]
    for dt, info in request.json.get('shifts', {}).items():
        if info['shift'] != '-': 
            wishes_data.append({'shop_id': shop_id, 'staff_name': sn, 'date': dt, 'shift': info['shift'], 'time': info['time']})
    save_data(immediate=True); return jsonify({'success': True, 'message': '提出完了（シートへ保存されました）'})

@app.route('/api/generate_auto_shift', methods=['POST'])
def generate_auto_shift():
    shop_id = request.json.get('shop_id', 'shop1')
    y, m, u = int(request.json.get('year')), int(request.json.get('month')), request.json.get('unit', 'all')
    fh, sh, _ = get_month_days(y, m)
    weeks = get_month_weeks(y, m)
    
    tds = set()
    if u == 'all': tds = {d['full_date'] for d in (fh + sh)}
    elif u.isdigit() and 0 <= int(u) < len(weeks): tds = {d['full_date'] for d in weeks[int(u)] if d['in_month']}
    
    if not tds: return jsonify({'success': False, 'message': '対象なし'})
    global shifts_data
    shifts_data = [s for s in shifts_data if not (s.get('shop_id', 'shop1') == shop_id and s['date'] in tds)]
    s_list = [name for name, info in staffs_db.items() if info.get('shop_id', 'shop1') == shop_id]
    
    for d_info in (fh + sh):
        dt_str = d_info['full_date']
        if dt_str not in tds: continue
        reqs = required_settings.get(d_info['weekday'], {'K (キッチン)': 2, 'F (フロント)': 2, 'Y (やきば)': 1})
        assigned = set(); pos_c = {p: 0 for p in POSITIONS}
        d_wishes = {w['staff_name']: w for w in wishes_data if w.get('shop_id', 'shop1') == shop_id and w['date'] == dt_str}

        for s in s_list:
            w = d_wishes.get(s)
            if w and w.get('shift') == '出勤希望':
                dp = staffs_db[s].get('default_position', 'K (キッチン)')
                if pos_c[dp] < reqs.get(dp, 0):
                    shifts_data.append({'shop_id': shop_id, 'staff_name': s, 'date': dt_str, 'shift': dp, 'time': w.get('time', '')}); assigned.add(s); pos_c[dp] += 1

        for p, rc in reqs.items():
            for s in s_list:
                if pos_c[p] >= rc: break
                if s in assigned or (d_wishes.get(s) and d_wishes.get(s).get('shift') == '希望休'): continue
                if staffs_db[s].get('default_position', '') == p:
                    shifts_data.append({'shop_id': shop_id, 'staff_name': s, 'date': dt_str, 'shift': p, 'time': d_wishes.get(s, {}).get('time', '')}); assigned.add(s); pos_c[p] += 1
                    
        for p, rc in reqs.items():
            for s in s_list:
                if pos_c[p] >= rc: break
                if s in assigned or (d_wishes.get(s) and d_wishes.get(s).get('shift') == '希望休'): continue
                shifts_data.append({'shop_id': shop_id, 'staff_name': s, 'date': dt_str, 'shift': p, 'time': d_wishes.get(s, {}).get('time', '')}); assigned.add(s); pos_c[p] += 1

    save_data(immediate=True); return jsonify({'success': True, 'message': '自動作成完了'})

@app.route('/api/sync_from_sheets', methods=['POST'])
def sync_from_sheets_api():
    global shops, staffs_db, required_settings, deadline_settings, wishes_data, shifts_data, published_shifts_data, actual_shifts_data, admin_credentials, timeclock_logs
    new_data = load_data()
    if new_data:
        shops = new_data.get('shops', shops)
        staffs_db, required_settings, deadline_settings = new_data['staffs_db'], new_data['required_settings'], new_data['deadline_settings']
        wishes_data, shifts_data, published_shifts_data = new_data['wishes_data'], new_data['shifts_data'], new_data['published_shifts_data']
        actual_shifts_data, admin_credentials = new_data['actual_shifts_data'], new_data['admin_credentials']
        timeclock_logs = new_data.get('timeclock_logs', [])
        return jsonify({'success': True, 'message': '同期完了'})
    return jsonify({'success': False, 'message': 'エラー'})

if __name__ == '__main__':
    app.run(debug=True)