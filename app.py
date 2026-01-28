import os
import json
import random
import datetime
import re
import csv
from io import StringIO
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from authlib.integrations.flask_client import OAuth
from werkzeug.middleware.proxy_fix import ProxyFix 
from werkzeug.exceptions import HTTPException 
import traceback 

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'lord_of_blanks_key')
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

@app.errorhandler(Exception)
def handle_exception(e):
    if isinstance(e, HTTPException): return e
    return f"<pre>{traceback.format_exc()}</pre>", 500

# --- 구글 OAuth ---
app.config['GOOGLE_CLIENT_ID'] = os.environ.get('GOOGLE_CLIENT_ID')
app.config['GOOGLE_CLIENT_SECRET'] = os.environ.get('GOOGLE_CLIENT_SECRET')

oauth = OAuth(app)
google_auth = oauth.register(
    name='google',
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'}
)

ACTIVE_GAMES = {}

class GoogleSheetManager:
    def __init__(self):
        self.client = None
        self.sheet = None
        self.users_ws = None
        self.quests_ws = None
        self.collections_ws = None
        self.abbrev_ws = None
        self.quest_log_ws = None
        
        self.USER_HEADERS = ["user_id", "password", "level", "xp", "title", "last_idx", "points", "nickname"]
        self.QUEST_HEADERS = ["quest_name", "content", "creator", "date"]
        self.COLLECTION_HEADERS = ["user_id", "card_text", "grade", "date", "quest_name", "level", "type"]
        self.ABBREV_HEADERS = ["user_id", "quest_name", "mnemonic", "date"]
        self.QUEST_LOG_HEADERS = ["user_id", "last_daily_login"]
        self.connect_db() 

    def connect_db(self):
        try:
            json_creds = os.environ.get('GCP_CREDENTIALS')
            if not json_creds: return False
            creds_dict = json.loads(json_creds)
            scope = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
            self.client = gspread.authorize(creds)
            self.sheet = self.client.open("memory_game_db")
            
            self.users_ws = self._get_or_create_sheet("users", self.USER_HEADERS)
            self.collections_ws = self._get_or_create_sheet("collections", self.COLLECTION_HEADERS)
            self.quests_ws = self._get_or_create_sheet("quests", self.QUEST_HEADERS)
            self.abbrev_ws = self._get_or_create_sheet("abbreviations", self.ABBREV_HEADERS)
            self.quest_log_ws = self._get_or_create_sheet("quest_log", self.QUEST_LOG_HEADERS)
            return True
        except Exception as e:
            print(f"DB Error: {e}")
            return False

    def _get_or_create_sheet(self, title, headers):
        try:
            ws = self.sheet.worksheet(title)
            if not ws.get_all_values(): ws.append_row(headers)
            return ws
        except:
            try:
                ws = self.sheet.add_worksheet(title, 100, 10)
                ws.append_row(headers)
                return ws
            except: return None

    def ensure_connection(self):
        try:
            self.users_ws.acell('A1')
            return True
        except:
            return self.connect_db()

    def get_safe_records(self, worksheet):
        if worksheet is None: return []
        try:
            self.ensure_connection()
            rows = worksheet.get_all_values()
            if len(rows) < 2: return []
            headers = rows[0]
            records = []
            for row in rows[1:]:
                padded = row + [""] * (len(headers) - len(row))
                records.append(dict(zip(headers, padded)))
            return records
        except: return []

    def get_user_by_id(self, user_id):
        if not self.ensure_connection(): return None, None
        try:
            records = self.get_safe_records(self.users_ws)
            for i, row in enumerate(records):
                if str(row.get('user_id')) == str(user_id):
                    row['points'] = int(row.get('points') or 0)
                    row['level'] = int(row.get('level') or 1)
                    row['xp'] = int(row.get('xp') or 0)
                    if not row.get('nickname'): row['nickname'] = str(user_id).split('@')[0]
                    return row, i + 2
        except: pass
        return None, None

    def register_social(self, user_id):
        if not self.ensure_connection(): return False
        try:
            if self.get_user_by_id(user_id)[0]: return True
            nick = user_id.split('@')[0]
            if self.users_ws:
                self.users_ws.append_row([user_id, "SOCIAL", 1, 0, "빈칸 견습생", 0, 0, nick])
            return True
        except: return False

    # [핵심 수정] ID로 직접 찾아서 수정하는 확실한 방법
    def update_nickname(self, user_id, new_nick):
        if not self.ensure_connection(): return False
        try:
            # 1열(user_id)에서 해당 유저 찾기
            cell = self.users_ws.find(user_id, in_column=1)
            if cell:
                # 찾은 행의 8번째 열(nickname) 수정
                self.users_ws.update_cell(cell.row, 8, new_nick)
                return True
            return False
        except Exception as e:
            print(f"Nick Update Error: {e}")
            return False

    def save_split_quests(self, title_prefix, file_obj, creator):
        if not self.ensure_connection(): return False, "DB 접속 실패"
        try:
            today = str(datetime.date.today())
            existing = [str(r.get('quest_name')) for r in self.get_safe_records(self.quests_ws)]
            rows_to_add = []
            
            filename = file_obj.filename.lower()
            file_obj.seek(0)
            raw_data = file_obj.read()
            
            try: raw_text = raw_data.decode('utf-8')
            except: raw_text = raw_data.decode('cp949', errors='ignore')

            if filename.endswith('.html') or '<html' in raw_text[:100].lower():
                tr_pattern = re.compile(r'<tr[^>]*>(.*?)</tr>', re.DOTALL | re.IGNORECASE)
                td_pattern = re.compile(r'<td[^>]*>(.*?)</td>', re.DOTALL | re.IGNORECASE)
                rows = tr_pattern.findall(raw_text)
                for row_content in rows:
                    cols = td_pattern.findall(row_content)
                    if len(cols) >= 3:
                        prefixes = ['제', '령', '규']
                        for i in range(3):
                            col_html = cols[i]
                            title_match = re.search(r'<span[^>]*class="bl"[^>]*>(.*?)</span>', col_html)
                            if title_match:
                                raw_title = title_match.group(1).strip()
                                clean_title = re.sub(r'<[^>]+>', '', raw_title).strip()
                                clean_content = re.sub(r'<[^>]+>', '\n', col_html)
                                clean_content = re.sub(r'\n+', '\n', clean_content).strip()
                                final_title = f"{prefixes[i]}-{title_prefix}-{clean_title}"
                                dup_count = 0; temp_name = final_title
                                while any(r[0] == temp_name for r in rows_to_add) or temp_name in existing:
                                    dup_count += 1; temp_name = f"{final_title}_{dup_count}"
                                rows_to_add.append([temp_name, clean_content[:45000], creator, today])
            else:
                f_stream = StringIO(raw_text)
                normalized_text = raw_text.replace('\r\n', '\n')
                base_category = "제"
                if "시행규칙" in title_prefix: base_category = "규"
                elif "시행령" in title_prefix: base_category = "령"
                
                blocks = re.split(r'\n\s*\n', normalized_text)
                blocks = [b.strip() for b in blocks if b.strip()]
                
                for block in blocks:
                    clean_block = block.strip()
                    if not clean_block: continue
                    first_line = clean_block.split('\n')[0].strip()
                    snippet = first_line[:15].replace(" ", "").replace('/', '').replace(':', '')
                    current_prefix = base_category
                    if re.match(r'^(령|영\s|시행령)', first_line): current_prefix = "령"
                    elif re.match(r'^(규|규칙|시행규칙)', first_line): current_prefix = "규"
                    q_name = f"{current_prefix}-{title_prefix}-{snippet}"
                    dup_count = 0; temp_name = q_name
                    while any(r[0] == temp_name for r in rows_to_add) or temp_name in existing:
                        dup_count += 1; temp_name = f"{q_name}_{dup_count}"
                    rows_to_add.append([temp_name, clean_block[:45000], creator, today])
            
            if rows_to_add: 
                self.quests_ws.append_rows(rows_to_add)
                return True, len(rows_to_add)
            return False, "추출된 내용이 없습니다."
        except Exception as e: return False, str(e)

    def delete_quest_group(self, prefix):
        if not self.ensure_connection(): return False
        try:
            records = self.get_safe_records(self.quests_ws)
            to_del = []
            for i, r in enumerate(records):
                q_name = str(r.get('quest_name'))
                if f"-{prefix}-" in q_name:
                    to_del.append(i + 2)
            for idx in sorted(to_del, reverse=True): self.quests_ws.delete_rows(idx)
            return True
        except: return False

    def delete_quest_single(self, quest_name):
        if not self.ensure_connection(): return False
        try:
            cell = self.quests_ws.find(quest_name, in_column=1)
            if cell:
                self.quests_ws.delete_rows(cell.row)
                return True
            return False
        except: return False

    def merge_quests(self, quest_names, creator):
        if not self.ensure_connection() or not quest_names: return False
        try:
            records = self.get_safe_records(self.quests_ws)
            to_merge = []
            to_del_indices = []
            for i, r in enumerate(records):
                if r.get('quest_name') in quest_names:
                    to_merge.append(r)
                    to_del_indices.append(i + 2)
            if not to_merge: return False
            combined_content = "\n\n".join([q.get('content', '') for q in to_merge])
            base_full_title = to_merge[0].get('quest_name')
            parts = base_full_title.split('-')
            if len(parts) >= 3:
                prefix = parts[0]; filename = parts[1]
                new_title = f"{prefix}-{filename}-합본_{datetime.datetime.now().strftime('%H%M%S')}"
            else:
                new_title = f"{base_full_title}-합본_{datetime.datetime.now().strftime('%H%M%S')}"
            self.quests_ws.append_row([new_title, combined_content, creator, str(datetime.date.today())])
            for idx in sorted(to_del_indices, reverse=True):
                self.quests_ws.delete_rows(idx)
            return True
        except Exception as e: return False

    def split_quest_by_paragraph(self, quest_name, creator):
        if not self.ensure_connection(): return False
        try:
            cell = self.quests_ws.find(quest_name, in_column=1)
            if not cell: return False
            row_val = self.quests_ws.row_values(cell.row)
            content = row_val[1]
            blocks = re.split(r'\n\s*\n', content)
            blocks = [b.strip() for b in blocks if b.strip()]
            if len(blocks) < 2: return False
            rows_to_add = []
            today = str(datetime.date.today())
            base_name = quest_name
            if '_' in base_name and 'part' in base_name: base_name = base_name.rsplit('_', 1)[0]
            for idx, block in enumerate(blocks):
                new_name = f"{base_name}_part{idx+1}"
                rows_to_add.append([new_name, block, creator, today])
            self.quests_ws.append_rows(rows_to_add)
            self.quests_ws.delete_rows(cell.row)
            return True
        except Exception as e: return False

    def rename_quest(self, old_name, new_name):
        if not self.ensure_connection(): return False
        try:
            q_cell = self.quests_ws.find(old_name, in_column=1)
            if q_cell: self.quests_ws.update_cell(q_cell.row, 1, new_name)
            else: return False
            try:
                col_cells = self.collections_ws.findall(old_name, in_column=5) 
                for cell in col_cells: self.collections_ws.update_cell(cell.row, 5, new_name)
            except: pass
            try:
                abb_cells = self.abbrev_ws.findall(old_name, in_column=2) 
                for cell in abb_cells: self.abbrev_ws.update_cell(cell.row, 2, new_name)
            except: pass
            return True
        except Exception as e: return False

    def get_quest_list(self):
        if not self.ensure_connection(): return []
        return self.get_safe_records(self.quests_ws)

    def get_quest_content(self, quest_name):
        if not self.ensure_connection(): return ""
        try:
            records = self.get_safe_records(self.quests_ws)
            for r in records:
                if r.get('quest_name') == quest_name: return r.get('content', "")
            return ""
        except: return ""

    def get_my_progress(self, user_id):
        if not self.ensure_connection(): return []
        try:
            col_records = self.get_safe_records(self.collections_ws)
            return [r for r in col_records if str(r.get('user_id')) == str(user_id)]
        except: return []

    def get_available_quests(self, user_id, mode):
        if not self.ensure_connection(): return []
        try:
            all_quests = self.get_safe_records(self.quests_ws)
            my_cards = self.get_my_progress(user_id)
            my_quest_names = [c.get('quest_name') for c in my_cards if c.get('type') == 'BLANK']
            if mode == 'acquire': return [q for q in all_quests if q.get('quest_name') not in my_quest_names]
            elif mode == 'review': return my_cards 
            elif mode == 'abbrev': return [c for c in my_cards if int(c.get('level', 0)) >= 1]
        except: return []

    def process_result(self, user_id, row_idx, quest_name, content, mode):
        if not self.ensure_connection(): return 0, 0
        try:
            user_data, fresh_row_idx = self.get_user_by_id(user_id)
            if not user_data:
                self.register_social(user_id)
                user_data, fresh_row_idx = self.get_user_by_id(user_id)
            if not user_data: return 1, 0
            records = self.get_safe_records(self.collections_ws)
            target_type = 'ABBREV' if mode == 'abbrev' else 'BLANK'
            found_idx = -1; current_level = 0
            for i, row in enumerate(records):
                if str(row.get('user_id')) == str(user_id) and row.get('quest_name') == quest_name and row.get('type') == target_type:
                    found_idx = i + 2; current_level = int(row.get('level') or 0); break
            xp_gain = 0
            try:
                if found_idx == -1: 
                    grade = "RARE" if mode == 'abbrev' else "NORMAL"
                    self.collections_ws.append_row([user_id, content, grade, str(datetime.date.today()), quest_name, 1, target_type])
                    xp_gain = 100 if mode == 'abbrev' else 50
                else: 
                    self.collections_ws.update_cell(found_idx, 6, current_level + 1)
                    xp_gain = 30 if mode == 'abbrev' else (20 + current_level * 5)
            except gspread.exceptions.APIError:
                self.connect_db() 
                if found_idx == -1:
                    self.collections_ws.append_row([user_id, content, grade, str(datetime.date.today()), quest_name, 1, target_type])
                else:
                    self.collections_ws.update_cell(found_idx, 6, current_level + 1)
            u_lv, new_xp = self.add_xp(user_id, xp_gain, user_data, fresh_row_idx)
            return u_lv, new_xp
        except Exception as e: raise e

    def add_xp(self, user_id, amount, user_data=None, row_idx=None):
        if not self.ensure_connection(): return 1, 0
        if not user_data or not row_idx:
            user_data, row_idx = self.get_user_by_id(user_id)
            if not user_data: return 1, 0
        try:
            u_xp = int(user_data.get('xp', 0) or 0)
            u_lv = int(user_data.get('level', 1) or 1)
            new_xp = u_xp + amount
            req = u_lv * 100
            while new_xp >= req:
                u_lv += 1; new_xp -= req; req = u_lv * 100
            self.users_ws.update_cell(row_idx, 3, u_lv)
            self.users_ws.update_cell(row_idx, 4, new_xp)
            return u_lv, new_xp
        except gspread.exceptions.APIError:
            self.connect_db()
            self.users_ws.update_cell(row_idx, 3, u_lv)
            self.users_ws.update_cell(row_idx, 4, new_xp)
            return u_lv, new_xp

    def update_quest_content(self, quest_name, new_content):
        if not self.ensure_connection(): return False
        try:
            cell = self.quests_ws.find(quest_name, in_column=1) 
            if cell: self.quests_ws.update_cell(cell.row, 2, new_content); return True
        except: return False

    def save_mnemonic(self, user_id, quest_name, mnemonic):
        if not self.ensure_connection(): return False
        try:
            records = self.get_safe_records(self.abbrev_ws)
            for i, r in enumerate(records):
                if str(r.get('user_id')) == str(user_id) and r.get('quest_name') == quest_name:
                    self.abbrev_ws.update_cell(i + 2, 3, mnemonic)
                    return True
            self.abbrev_ws.append_row([user_id, quest_name, mnemonic, str(datetime.date.today())])
            return True
        except: return False

    def get_mnemonic(self, user_id, quest_name):
        if not self.ensure_connection(): return None
        records = self.get_safe_records(self.abbrev_ws)
        for r in records:
            if str(r.get('user_id')) == str(user_id) and r.get('quest_name') == quest_name:
                return r.get('mnemonic')
        return None

    def get_abbreviations(self, user_id):
        if not self.ensure_connection(): return []
        records = self.get_safe_records(self.abbrev_ws)
        return [r for r in records if str(r.get('user_id')) == str(user_id)]

    def add_abbreviation(self, user_id, term, meaning):
        if not self.ensure_connection(): return False
        self.abbrev_ws.append_row([user_id, term, meaning, str(datetime.date.today())])
        return True

    def delete_abbreviation(self, user_id, term):
        if not self.ensure_connection(): return False
        records = self.get_safe_records(self.abbrev_ws)
        for i, r in enumerate(records):
            if str(r.get('user_id')) == str(user_id) and r.get('term') == term:
                self.abbrev_ws.delete_rows(i + 2); return True
        return False

    def reset_user_data(self, user_id):
        if not self.ensure_connection(): return False
        try:
            col_rows = self.collections_ws.get_all_values()
            to_del_col = [i + 1 for i, row in enumerate(col_rows) if i > 0 and str(row[0]) == str(user_id)]
            for r in sorted(to_del_col, reverse=True): self.collections_ws.delete_rows(r)
            abb_rows = self.abbrev_ws.get_all_values()
            to_del_abb = [i + 1 for i, row in enumerate(abb_rows) if i > 0 and str(row[0]) == str(user_id)]
            for r in sorted(to_del_abb, reverse=True): self.abbrev_ws.delete_rows(r)
            ql_rows = self.quest_log_ws.get_all_values()
            to_del_ql = [i + 1 for i, row in enumerate(ql_rows) if i > 0 and str(row[0]) == str(user_id)]
            for r in sorted(to_del_ql, reverse=True): self.quest_log_ws.delete_rows(r)
            cell = self.users_ws.find(user_id, in_column=1)
            if cell:
                self.users_ws.update_cell(cell.row, 3, 1) 
                self.users_ws.update_cell(cell.row, 4, 0)
            return True
        except Exception as e: return False

    def check_daily_login(self, user_id):
        if not self.ensure_connection(): return False
        today = str(datetime.date.today())
        records = self.get_safe_records(self.quest_log_ws)
        for r in records:
            if str(r.get('user_id')) == str(user_id): return r.get('last_daily_login') == today
        return False

    def claim_daily_login(self, user_id):
        if not self.ensure_connection(): return False, 0, 0
        today = str(datetime.date.today())
        records = self.get_safe_records(self.quest_log_ws)
        found = False
        for i, r in enumerate(records):
            if str(r.get('user_id')) == str(user_id):
                if r.get('last_daily_login') == today: return False, 0, 0
                self.quest_log_ws.update_cell(i + 2, 2, today)
                found = True
                break
        if not found: self.quest_log_ws.append_row([user_id, today])
        lv, xp = self.add_xp(user_id, 50)
        return True, lv, xp

    def align_quests(self, quests):
        law_groups = {} 
        others = []
        for q in quests:
            name = q.get('quest_name', '')
            parts = name.split('-')
            if len(parts) >= 3:
                law_name = parts[1]
                if law_name not in law_groups: law_groups[law_name] = []
                law_groups[law_name].append(q)
            else:
                others.append(q)

        final_structure = {}
        for law_name, items in law_groups.items():
            rows = []
            current = {'law': None, 'decree': None, 'rule': None}
            for q in items:
                name = q.get('quest_name', '')
                if name.startswith('제-'):
                    if current['law'] or current['decree'] or current['rule']:
                        rows.append(current)
                        current = {'law': None, 'decree': None, 'rule': None}
                    current['law'] = q
                elif name.startswith('령-'):
                    if current['decree'] or current['rule']:
                        rows.append(current)
                        current = {'law': None, 'decree': None, 'rule': None}
                    current['decree'] = q
                elif name.startswith('규-'):
                    if current['rule']:
                        rows.append(current)
                        current = {'law': None, 'decree': None, 'rule': None}
                    current['rule'] = q
                else:
                    others.append(q)
            if current['law'] or current['decree'] or current['rule']:
                rows.append(current)
            final_structure[law_name] = rows
        return final_structure, others

gm = GoogleSheetManager()

@app.route('/')
def index():
    if 'user_id' in session: return redirect(url_for('lobby'))
    return render_template('login.html')

@app.route('/google/login')
def google_login():
    redirect_uri = url_for('google_callback', _external=True)
    return google_auth.authorize_redirect(redirect_uri)

@app.route('/google/callback')
def google_callback():
    try:
        token = google_auth.authorize_access_token()
        user_info = token.get('userinfo')
        email = user_info['email']
        if not gm.ensure_connection(): return "DB Error", 500
        gm.register_social(email)
        user, idx = gm.get_user_by_id(email)
        session['user_id'] = email; session['user_row_idx'] = idx
        return redirect(url_for('lobby'))
    except Exception as e: return f"Login Error: {e}", 500

@app.route('/lobby')
def lobby():
    if 'user_id' not in session: return redirect(url_for('index'))
    user, _ = gm.get_user_by_id(session['user_id'])
    
    if user: 
        session['level'] = user.get('level', 1)
        session['xp'] = user.get('xp', 0)
        session['nickname'] = user.get('nickname', '요원')
        session['points'] = user.get('points', 0)
    else:
        session['level'] = session.get('level', 1)
        session['xp'] = session.get('xp', 0)
        session['nickname'] = session.get('nickname', '요원')
        session['points'] = 0

    daily_checked = gm.check_daily_login(session['user_id'])
    
    return render_template('lobby.html', 
                           level=session.get('level', 1), 
                           xp=session.get('xp', 0), 
                           points=session.get('points', 0), 
                           nickname=session.get('nickname', '요원'), 
                           req_xp=session.get('level', 1)*100, 
                           daily_checked=daily_checked)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

@app.route('/reset_progress', methods=['POST'])
def reset_progress():
    if 'user_id' not in session: return redirect(url_for('index'))
    if gm.reset_user_data(session['user_id']):
        session['level'] = 1; session['xp'] = 0
        flash("모든 학습 내용이 초기화되었습니다.")
    else: flash("초기화 실패. 잠시 후 다시 시도해주세요.")
    return redirect(url_for('lobby'))

@app.route('/claim_daily_login', methods=['POST'])
def claim_daily_login():
    if 'user_id' not in session: return redirect(url_for('index'))
    ok, lv, xp = gm.claim_daily_login(session['user_id'])
    if ok:
        session['level'] = lv; session['xp'] = xp
        flash("출석 보상 50 XP 획득!")
    else: flash("이미 출석 보상을 받았습니다.")
    return redirect(url_for('lobby'))

@app.route('/zone/generate', methods=['GET', 'POST'])
def zone_generate():
    if 'user_id' not in session: return redirect(url_for('index'))
    if request.method == 'POST':
        if 'delete_group' in request.form:
            gm.delete_quest_group(request.form['delete_group'])
            flash("삭제되었습니다.")
        elif 'delete_single' in request.form:
            if gm.delete_quest_single(request.form['delete_single']): flash("삭제되었습니다.")
            else: flash("삭제 실패")
        elif 'rename_old' in request.form:
            old = request.form['rename_old']; new = request.form['rename_new']
            if gm.rename_quest(old, new): flash("제목 수정 완료!")
            else: flash("수정 실패")
        elif 'new_q_file' in request.files:
            f = request.files['new_q_file']
            ok, result = gm.save_split_quests(request.form['new_q_name'], f, session['user_id'])
            if ok: flash(f"{result}개 생성 완료!")
            else: flash(f"생성 실패: {result}")
        elif 'merge_targets' in request.form:
            targets = request.form.getlist('merge_targets')
            if len(targets) > 1:
                if gm.merge_quests(targets, session['user_id']): flash(f"{len(targets)}개의 카드가 합쳐졌습니다!")
                else: flash("합치기 실패.")
            else: flash("합칠 카드를 2개 이상 선택하세요.")
    
    quests = gm.get_quest_list()
    my_progress = gm.get_my_progress(session['user_id'])
    my_completed = [c.get('quest_name') for c in my_progress if c.get('type') == 'BLANK']
    
    aligned_structure, others = gm.align_quests(quests)
    
    return render_template('zone_generate.html', aligned_structure=aligned_structure, others=others, my_completed=my_completed, quests=quests)

@app.route('/maker', methods=['GET', 'POST'])
def maker():
    if 'user_id' not in session: return redirect(url_for('index'))
    if request.method == 'GET':
        q_name = request.args.get('quest_name')
        if not q_name: return redirect(url_for('zone_generate'))
        quests = gm.get_quest_list()
        quest = next((q for q in quests if q['quest_name'] == q_name), None)
        if not quest: return redirect(url_for('zone_generate'))
        return render_template('maker.html', raw_text=quest['content'], title=q_name)
    elif request.method == 'POST':
        if 'split_action' in request.form:
            q_name = request.form['title']
            if gm.split_quest_by_paragraph(q_name, session['user_id']):
                flash("문단별 나누기 완료!")
                return redirect(url_for('zone_generate'))
            else:
                flash("나누기 실패")
                return redirect(url_for('maker', quest_name=q_name))
        old_title = request.form.get('old_title')
        new_title = request.form.get('title')
        content = request.form['final_content']
        if old_title and new_title and old_title != new_title:
            gm.rename_quest(old_title, new_title)
            current_title = new_title
        else:
            current_title = old_title if old_title else new_title
        if gm.update_quest_content(current_title, content):
            flash("생성완료.")
            return redirect(url_for('zone_generate'))
        else:
            flash("저장 실패")
            return redirect(url_for('maker', quest_name=current_title))

@app.route('/zone/acquire', methods=['GET', 'POST'])
def zone_acquire():
    if 'user_id' not in session: return redirect(url_for('index'))
    if request.method == 'POST':
        q_name = request.form['quest_name']
        all_q = gm.get_quest_list()
        quest = next((q for q in all_q if q['quest_name'] == q_name), None)
        if quest:
            ACTIVE_GAMES[session['user_id']] = { 'mode': 'acquire', 'quest_name': q_name, 'content': quest['content'] }
            return redirect(url_for('play_game'))
    quests = gm.get_available_quests(session['user_id'], 'acquire')
    aligned_structure, others = gm.align_quests(quests)
    return render_template('zone_list.html', title="획득 구역", aligned_structure=aligned_structure, others=others, mode='acquire', quests=quests)

@app.route('/zone/review', methods=['GET', 'POST'])
def zone_review():
    if 'user_id' not in session: return redirect(url_for('index'))
    if request.method == 'POST':
        q_name = request.form['quest_name']
        q_type = request.form.get('quest_type', 'BLANK')
        cards = gm.get_available_quests(session['user_id'], 'review')
        card = next((c for c in cards if c.get('quest_name') == q_name and c.get('type') == q_type), None)
        if card:
            mode = 'abbrev' if q_type == 'ABBREV' else 'review'
            level = int(card.get('level', 1))
            if level == 5: mode = 'register_mnemonic'
            latest_content = gm.get_quest_content(q_name)
            final_content = latest_content if latest_content else card['card_text']
            ACTIVE_GAMES[session['user_id']] = { 
                'mode': mode, 'quest_name': q_name, 'content': final_content, 'level': level
            }
            return redirect(url_for('play_game'))
    cards = gm.get_available_quests(session['user_id'], 'review')
    aligned_structure, others = gm.align_quests(cards)
    return render_template('zone_list.html', title="복습 구역", aligned_structure=aligned_structure, others=others, mode='review', quests=cards)

@app.route('/zone/abbrev', methods=['GET', 'POST'])
def zone_abbrev():
    if 'user_id' not in session: return redirect(url_for('index'))
    if request.method == 'POST':
        q_name = request.form['quest_name']
        cards = gm.get_available_quests(session['user_id'], 'abbrev')
        card = next((c for c in cards if c.get('quest_name') == q_name), None)
        if card:
            mnemonic = gm.get_mnemonic(session['user_id'], q_name)
            latest_content = gm.get_quest_content(q_name)
            final_content = latest_content if latest_content else card['card_text']
            ACTIVE_GAMES[session['user_id']] = { 
                'mode': 'abbrev', 'quest_name': q_name, 'content': final_content,
                'level': int(card.get('level', 1)), 'mnemonic': mnemonic
            }
            return redirect(url_for('play_game'))
    cards = gm.get_available_quests(session['user_id'], 'abbrev')
    aligned_structure, others = gm.align_quests(cards)
    return render_template('zone_list.html', title="약어 훈련소", aligned_structure=aligned_structure, others=others, mode='abbrev', quests=cards)

@app.route('/play', methods=['GET', 'POST'])
def play_game():
    if 'user_id' not in session: return redirect(url_for('index'))
    game = ACTIVE_GAMES.get(session['user_id'])
    if not game: return redirect(url_for('lobby'))
    current_level = game.get('level', 1)
    if request.method == 'GET':
        content = game['content']
        parts = []
        targets = []
        if game['mode'] == 'register_mnemonic':
            clean_text = re.sub(r'\{([^}]+)\}', r'\1', content)
            parts = [
                {'type':'text', 'val': '이 카드의 약어(두문자)를 만드세요.<br>예: 예방 진단 치료 재활 -> 예단치재'},
                {'type':'box_content', 'val': clean_text}
            ]
            targets = []
        elif game['mode'] == 'abbrev':
            clean = re.sub(r'\{([^}]+)\}', r'\1', content)
            mnemonic_target = game.get('mnemonic', '약어없음')
            parts = [
                {'type':'text', 'val': '1단계: 이 카드의 약어(두문자)를 입력하세요.'}, 
                {'type':'input_abbrev', 'id':0, 'mnemonic_ans': mnemonic_target}
            ]
            targets = [clean.strip()] 
        else:
            last = 0; idx = 0
            for m in re.finditer(r'\{([^}]+)\}', content):
                s, e = m.span()
                if s > last: parts.append({'type':'text', 'val': content[last:s]})
                parts.append({'type':'input', 'id': idx})
                targets.append(m.group(1))
                idx += 1; last = e
            if last < len(content): parts.append({'type':'text', 'val': content[last:]})
        return render_template('play.html', parts=parts, targets=targets, mode=game['mode'], title=game['quest_name'], level=current_level)
    elif request.method == 'POST':
        try:
            if game['mode'] == 'register_mnemonic':
                user_mnemonic = request.form.get('user_mnemonic', '').strip()
                if user_mnemonic:
                    gm.save_mnemonic(session['user_id'], game['quest_name'], user_mnemonic)
                    lv, xp = gm.process_result(session['user_id'], session.get('user_row_idx'), game['quest_name'], game['content'], 'review')
                    session['level'] = lv; session['xp'] = xp
                    flash(f"약어 '{user_mnemonic}' 저장 완료! (약어 구역에서 테스트하세요)")
                    return redirect(url_for('zone_review'))
                else:
                    flash("약어를 입력해주세요.")
                    return redirect(url_for('play_game'))
            clean = game['content']
            if game['mode'] != 'abbrev': clean = re.sub(r'\{([^}]+)\}', r'\1', game['content'])
            lv, xp = gm.process_result(session['user_id'], session.get('user_row_idx'), game['quest_name'], clean, game['mode'])
            session['level'] = lv; session['xp'] = xp
            if game['mode'] == 'acquire': flash("획득완료")
            else: flash(f"학습 완료! (현재 Lv.{lv})")
            return_zone = 'review' if game['mode'] == 'review' else ('abbrev' if game['mode'] == 'abbrev' else 'acquire')
            return redirect(url_for(f"zone_{return_zone}"))
        except Exception as e: return f"<h3>⚠️ 오류 발생</h3><pre>{traceback.format_exc()}</pre><br><a href='/lobby'>로비로 돌아가기</a>"

# [수정된 닉네임 변경 라우트]
@app.route('/update_nickname', methods=['POST'])
def update_nickname():
    if 'user_id' in session:
        new_nick = request.form.get('new_nickname')
        if new_nick:
            # ID를 이용해 직접 찾아서 변경 (위치 정보 의존 X)
            if gm.update_nickname(session['user_id'], new_nick):
                session['nickname'] = new_nick # 세션 즉시 반영
                flash("닉네임 변경 완료!")
            else:
                flash("변경 실패 (DB 오류)")
    return redirect(url_for('lobby'))

@app.route('/dungeon/edit_text', methods=['GET', 'POST'])
def edit_quest_text(): return redirect(url_for('lobby')) 

@app.route('/abbreviations', methods=['GET', 'POST'])
def abbreviations():
    if 'user_id' not in session: return redirect(url_for('index'))
    if request.method == 'POST':
        if 'delete_term' in request.form:
            gm.delete_abbreviation(session['user_id'], request.form['delete_term'])
        else: gm.add_abbreviation(session['user_id'], request.form.get('term'), request.form.get('meaning'))
        return redirect(url_for('abbreviations'))
    return render_template('abbreviations.html', abbrevs=gm.get_abbreviations(session['user_id']))

@app.route('/sw.js')
def sw(): return app.send_static_file('sw.js')

if __name__ == '__main__':
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
    app.run(host='0.0.0.0', port=10000)
