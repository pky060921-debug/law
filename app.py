import os
import json
import random
import datetime
import re
import html 
import time
from datetime import timedelta
from itertools import zip_longest
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from authlib.integrations.flask_client import OAuth
from werkzeug.middleware.proxy_fix import ProxyFix 
from werkzeug.exceptions import HTTPException 
import traceback 
import sys 

app = Flask(__name__)
# 세션 보안키 및 영구 세션 설정 (31일)
app.secret_key = os.environ.get('SECRET_KEY', 'law_game_ultimate_fixed_v3_2')
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=31)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

@app.errorhandler(Exception)
def handle_exception(e):
    if isinstance(e, HTTPException):
        return e
    return f"<pre>{traceback.format_exc()}</pre>", 500

# OAuth 설정
app.config['GOOGLE_CLIENT_ID'] = os.environ.get('GOOGLE_CLIENT_ID')
app.config['GOOGLE_CLIENT_SECRET'] = os.environ.get('GOOGLE_CLIENT_SECRET')

oauth = OAuth(app)
google_auth = oauth.register(
    name='google',
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'}
)

ACTIVE_GAMES = {}
GLOBAL_WORD_POOL = set()

def natural_sort_key(text):
    """숫자와 문자가 섞인 문자열을 자연스럽게 정렬 (제3조, 제3조의2 순서 보장)"""
    return [int(c) if c.isdigit() else c for c in re.split(r'(\d+)', text)]

def extract_candidates(text):
    clean_text = re.sub(r'[0-9]+\.|[가-힣]\.|[①-⑮]', ' ', text)
    words = re.findall(r'[가-힣]{2,}', clean_text)
    candidates = []
    josas = ['은', '는', '이', '가', '을', '를', '의', '에', '로', '으로', '에서', '에게', '하고', '이며', '이나', '도', '만', '까지', '부터', '조', '항', '호']
    stop_words = ["법률", "시행령", "시행규칙", "사항", "경우", "또는", "있다", "없다", "하여야", "한다", "된다", "대통령령", "보건복지부령", "총리령", "별표"]
    bad_endings = ('다', '까', '요', '한', '할', '된', '될', '는', '게', '지', '며', '고')

    for w in words:
        if w in stop_words: continue
        if w.endswith(bad_endings): continue
        for j in josas:
            if w.endswith(j):
                w = w[:-len(j)]
                break
        if len(w) >= 2 and w not in stop_words:
            candidates.append(w)
    return list(set(candidates))

def get_similar_distractors(target, count=4):
    global GLOBAL_WORD_POOL
    if not GLOBAL_WORD_POOL:
        return ["권한", "책임", "의무", "위반"]
    
    same_len = [w for w in GLOBAL_WORD_POOL if len(w) == len(target) and w != target]
    distractors = random.sample(same_len, min(len(same_len), count))
    
    if len(distractors) < count:
        others = list(GLOBAL_WORD_POOL)
        distractors += random.sample(others, min(len(others), count - len(distractors)))
    
    return distractors

def auto_generate_blanks(text, limit=999):
    if '{' in text and '}' in text:
        return text
    
    candidates = extract_candidates(text)
    if not candidates:
        return text
    
    candidates.sort(key=len, reverse=True)
    
    new_text = text
    count = 0
    for t in candidates:
        if count >= limit:
            break
        if re.search(r'\{[^}]*' + re.escape(t) + r'[^}]*\}', new_text):
            continue
        new_text = new_text.replace(t, f"{{{t}}}")
        count += 1
    return new_text

def split_content_smartly(text):
    text = text.strip()
    MAX_LEN = 300 
    if len(text) < MAX_LEN:
        return [text]
    
    chunks = []
    paragraphs = text.split('\n')
    current_chunk = ""
    
    for para in paragraphs:
        if len(para) > MAX_LEN:
            sentences = re.split(r'(?<=\.)\s+', para)
            for sent in sentences:
                if len(current_chunk) + len(sent) > MAX_LEN:
                    if current_chunk.strip():
                        chunks.append(current_chunk.strip())
                    current_chunk = sent + "\n"
                else:
                    current_chunk += sent + "\n"
        else:
            if len(current_chunk) + len(para) > MAX_LEN:
                if current_chunk.strip():
                    chunks.append(current_chunk.strip())
                current_chunk = para + "\n"
            else:
                current_chunk += para + "\n"
                
    if current_chunk.strip():
        chunks.append(current_chunk.strip())
    
    return chunks

class GoogleSheetManager:
    def __init__(self):
        self.client = None
        self.sheet = None
        self.users_ws = None
        self.quests_ws = None
        self.collections_ws = None
        self.abbrev_ws = None
        self.quest_log_ws = None
        self.user_cache = {}
        self.quest_cache = {'data': [], 'time': 0}
        self.CACHE_DURATION = 300
        
        self.USER_HEADERS = ["user_id", "password", "level", "xp", "title", "last_idx", "points", "nickname", "item_freeze", "item_scanner", "item_shield"]
        self.QUEST_HEADERS = ["quest_name", "content", "creator", "date"]
        self.COLLECTION_HEADERS = ["user_id", "card_text", "grade", "date", "quest_name", "level", "type", "record_time"]
        self.ABBREV_HEADERS = ["user_id", "quest_name", "mnemonic", "date"]
        self.QUEST_LOG_HEADERS = ["user_id", "last_daily_login"]

    def connect_db(self):
        try:
            json_creds = os.environ.get('GCP_CREDENTIALS')
            if not json_creds:
                return False
            creds_dict = json.loads(json_creds)
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive'])
            self.client = gspread.authorize(creds)
            self.sheet = self.client.open("memory_game_db")
            
            self.users_ws = self._get_or_create_sheet("users", self.USER_HEADERS)
            self.collections_ws = self._get_or_create_sheet("collections", self.COLLECTION_HEADERS)
            self.quests_ws = self._get_or_create_sheet("quests", self.QUEST_HEADERS)
            self.abbrev_ws = self._get_or_create_sheet("abbreviations", self.ABBREV_HEADERS)
            self.quest_log_ws = self._get_or_create_sheet("quest_log", self.QUEST_LOG_HEADERS)
            
            if not GLOBAL_WORD_POOL:
                self.load_global_words()
                
            return True
        except Exception as e:
            print(f"DB Error: {e}")
            return False

    def load_global_words(self):
        global GLOBAL_WORD_POOL
        try:
            records = self.get_quest_list()
            all_text = " ".join([r.get('content', '') for r in records])
            GLOBAL_WORD_POOL = set(extract_candidates(all_text))
        except:
            GLOBAL_WORD_POOL = set()

    def _get_or_create_sheet(self, title, headers):
        try:
            return self.sheet.worksheet(title)
        except:
            ws = self.sheet.add_worksheet(title, 100, len(headers))
            ws.append_row(headers)
            return ws

    def ensure_connection(self):
        try:
            if self.users_ws is None:
                raise Exception("Not Connected")
            self.users_ws.acell('A1')
            return True
        except:
            return self.connect_db()

    def get_safe_records(self, worksheet):
        try:
            return worksheet.get_all_records()
        except:
            return []

    def get_user_by_id(self, user_id, use_cache=True):
        if use_cache and user_id in self.user_cache:
            if time.time() - self.user_cache[user_id]['time'] < self.CACHE_DURATION:
                return self.user_cache[user_id]['data'], self.user_cache[user_id]['row_idx']
        
        if not self.ensure_connection():
            return None, None
            
        try:
            records = self.users_ws.get_all_records()
            for i, row in enumerate(records):
                if str(row.get('user_id')) == str(user_id):
                    row['level'] = int(row.get('level') or 1)
                    row['xp'] = int(row.get('xp') or 0)
                    row['points'] = int(row.get('points') or 0)
                    row['item_freeze'] = int(row.get('item_freeze') or 0)
                    row['item_scanner'] = int(row.get('item_scanner') or 0)
                    row['item_shield'] = int(row.get('item_shield') or 0)
                    
                    if not row.get('nickname'):
                        row['nickname'] = str(user_id).split('@')[0]
                        
                    self.user_cache[user_id] = {
                        'time': time.time(),
                        'data': row,
                        'row_idx': i + 2
                    }
                    return row, i + 2
        except:
            pass
            
        return None, None

    def register_social(self, user_id):
        if not self.ensure_connection():
            return False
        try:
            if user_id not in self.users_ws.col_values(1):
                self.users_ws.append_row([user_id, "SOCIAL", 1, 0, "우주 개척자", 0, 0, user_id.split('@')[0], 0, 0, 0])
            return True
        except:
            return False

    def claim_daily_login(self, user_id, all_cleared, has_rebellion):
        if not self.ensure_connection():
            return False, 0, 0
        
        today = str(datetime.date.today())
        records = self.get_safe_records(self.quest_log_ws)
        
        found = False
        for i, r in enumerate(records):
            if str(r.get('user_id')) == str(user_id):
                if r.get('last_daily_login') == today:
                    return False, 0, 0
                self.quest_log_ws.update_cell(i + 2, 2, today)
                found = True
                break
        
        if not found:
            self.quest_log_ws.append_row([user_id, today])
            
        bonus_pts = 1000 if all_cleared and not has_rebellion else 50
        lv, xp = self.add_xp(user_id, 50, bonus_pts)
        return True, bonus_pts, 50

    def get_quest_list(self):
        if self.quest_cache['data'] and (time.time() - self.quest_cache['time'] < self.CACHE_DURATION):
            return self.quest_cache['data']
            
        if self.ensure_connection():
            data = self.get_safe_records(self.quests_ws)
            self.quest_cache = {
                'data': data,
                'time': time.time()
            }
            return data
        return []

    def get_my_progress(self, user_id): 
        if not self.ensure_connection():
            return []
        records = self.get_safe_records(self.collections_ws)
        return [r for r in records if str(r.get('user_id')) == str(user_id)]

    def add_xp(self, user_id, amount, points_amount=0):
        if not self.ensure_connection():
            return 1, 0
            
        user_data, row_idx = self.get_user_by_id(user_id, use_cache=False)
        if not user_data:
            return 1, 0
            
        try:
            u_xp = int(user_data.get('xp') or 0)
            u_lv = int(user_data.get('level') or 1)
            u_pts = int(user_data.get('points') or 0)
            
            new_xp = u_xp + amount
            new_pts = u_pts + points_amount
            
            req = u_lv * 100
            while new_xp >= req:
                u_lv += 1
                new_xp -= req
                req = u_lv * 100
                
            self.users_ws.update_cell(row_idx, 3, u_lv)
            self.users_ws.update_cell(row_idx, 4, new_xp)
            self.users_ws.update_cell(row_idx, 7, new_pts)
            
            if user_id in self.user_cache:
                self.user_cache[user_id]['data']['level'] = u_lv
                self.user_cache[user_id]['data']['xp'] = new_xp
                self.user_cache[user_id]['data']['points'] = new_pts
                
            return u_lv, new_xp
        except:
            return int(user_data.get('level', 1)), int(user_data.get('xp', 0))

    def buy_item(self, user_id, item_type, cost):
        user_data, row_idx = self.get_user_by_id(user_id)
        if not user_data or int(user_data.get('points', 0)) < cost:
            return False, "자원 부족"
            
        col_map = {'freeze': 9, 'scanner': 10, 'shield': 11}
        try:
            self.users_ws.update_cell(row_idx, 7, int(user_data['points']) - cost)
            curr_item = int(user_data.get(f'item_{item_type}', 0))
            self.users_ws.update_cell(row_idx, col_map[item_type], curr_item + 1)
            self.user_cache.pop(user_id, None)
            return True, "구매 성공"
        except:
            return False, "오류 발생"

    def use_item(self, user_id, item_type):
        user_data, row_idx = self.get_user_by_id(user_id)
        col_map = {'freeze': 9, 'scanner': 10, 'shield': 11}
        
        if user_data and int(user_data.get(f'item_{item_type}', 0)) > 0:
            curr_item = int(user_data[f'item_{item_type}'])
            self.users_ws.update_cell(row_idx, col_map[item_type], curr_item - 1)
            self.user_cache.pop(user_id, None)
            return True
        return False

    def update_nickname(self, user_id, new_nickname):
        _, row_idx = self.get_user_by_id(user_id)
        if row_idx:
            self.users_ws.update_cell(row_idx, 8, new_nickname)
            self.user_cache.pop(user_id, None)
            return True
        return False

    def process_result(self, user_id, quest_name, content, mode, duration):
        if not self.ensure_connection():
            return 0, 0
        
        records = self.get_safe_records(self.collections_ws)
        found_idx = -1
        for i, r in enumerate(records):
            if str(r.get('user_id')) == str(user_id) and r.get('quest_name') == quest_name:
                found_idx = i + 2
                break
        
        today = str(datetime.date.today())
        
        if mode == 'terraform': 
            if found_idx == -1:
                self.collections_ws.append_row([user_id, "CLEARED", "NORMAL", today, quest_name, 1, "ACT", duration])
                return self.add_xp(user_id, 100, 50)
            else:
                self.collections_ws.update_cell(found_idx, 4, today)
                return self.add_xp(user_id, 50, 30)
        else: 
            if found_idx != -1:
                curr_lv = int(records[found_idx-2].get('level') or 0)
                self.collections_ws.update_cell(found_idx, 6, curr_lv + 1)
                self.collections_ws.update_cell(found_idx, 4, today)
                return self.add_xp(user_id, 20 + curr_lv*5, 10 + curr_lv*2)
        
        return 0, 0

    def get_mnemonic(self, user_id, quest_name):
        if not self.ensure_connection():
            return ""
        records = self.get_safe_records(self.abbrev_ws)
        for r in records:
            if str(r.get('user_id')) == str(user_id) and r.get('quest_name') == quest_name:
                return r.get('mnemonic')
        return ""

    def save_mnemonic(self, user_id, quest_name, mnemonic):
        if not self.ensure_connection():
            return False
        try:
            records = self.get_safe_records(self.abbrev_ws)
            for i, r in enumerate(records):
                if str(r.get('user_id')) == str(user_id) and r.get('quest_name') == quest_name:
                    self.abbrev_ws.update_cell(i + 2, 3, mnemonic)
                    return True
            self.abbrev_ws.append_row([user_id, quest_name, mnemonic, str(datetime.date.today())])
            return True
        except:
            return False

    def save_split_quests(self, title_prefix, file_obj, creator):
        if not self.ensure_connection():
            return False, "DB 접속 실패"
        try:
            today = str(datetime.date.today())
            existing = [str(r.get('quest_name')) for r in self.get_quest_list()]
            rows_to_add = []
            
            file_obj.seek(0)
            raw_data = file_obj.read()
            raw_text = ""
            for enc in ['utf-8', 'utf-8-sig', 'cp949', 'latin-1']:
                try:
                    raw_text = raw_data.decode(enc)
                    break
                except:
                    continue
            if not raw_text:
                raw_text = raw_data.decode('utf-8', errors='ignore')

            unescaped = html.unescape(raw_text)
            pre_clean = re.sub(r'<(br|p|div|li)[^>]*>', '\n', unescaped, flags=re.IGNORECASE)
            pre_clean = re.sub(r'</(p|div|li|td|tr)>', '\n', pre_clean, flags=re.IGNORECASE)
            rows = re.split(r'<tr[^>]*>', pre_clean, flags=re.IGNORECASE)
            
            if len(rows) > 1:
                current_chapter = "1. 미분류"
                current_law_num = "000조"
                type_names = {0: '법', 1: '령', 2: '규'}
                
                for row_html in rows[1:]:
                    try:
                        row_text = re.sub(r'<[^>]+>', ' ', row_html).strip()
                        row_text = re.sub(r'\s+', ' ', row_text)
                        
                        chap_match = re.search(r'제\s*(\d+)\s*장\s*(.*)', row_text)
                        if chap_match:
                            c_num = chap_match.group(1)
                            c_name = re.sub(r'[^\w\s]', '', chap_match.group(2).split('(')[0]).strip()[:15] or "총칙"
                            current_chapter = f"{int(c_num)}. {c_name}"
                            if len(row_text) < 40 and "조" not in row_text:
                                continue

                        cols = re.split(r'<td[^>]*>', row_html, flags=re.IGNORECASE)[1:]
                        if not cols:
                            continue
                        
                        # [핵심 교정 1: '조' 위치를 중간으로 이동하여 띄어쓰기 완벽 대응]
                        c0_raw = re.sub(r'<[^>]+>', '', cols[0]).strip()
                        is_act_cell = bool(re.match(r'^\s*제\s*\d+\s*조(?:\s*의\s*\d+)?', c0_raw))
                        
                        mapped_cols = ["", "", ""]
                        if len(cols) >= 3:
                            mapped_cols = cols[:3]
                        elif len(cols) == 2:
                            if is_act_cell:
                                mapped_cols[0], mapped_cols[1] = cols[0], cols[1]
                            else:
                                mapped_cols[1], mapped_cols[2] = cols[0], cols[1]
                        elif len(cols) == 1:
                            if is_act_cell:
                                mapped_cols[0] = cols[0]
                            else:
                                mapped_cols[1] = cols[0]

                        # [핵심 교정 2: current_law_num 추출 시 '조' 위치 정상화]
                        if mapped_cols[0].strip():
                            law_match = re.search(r'제\s*(\d+)\s*조(?:\s*의\s*(\d+))?', re.sub(r'<[^>]+>', '', mapped_cols[0]))
                            if law_match:
                                main_num, ext_part = law_match.group(1), law_match.group(2)
                                current_law_num = f"{int(main_num):03d}조"
                                if ext_part:
                                    current_law_num += f"의{ext_part}"
                        
                        if current_law_num == "000조":
                            fallback = re.search(r'제\s*(\d+)\s*조(?:\s*의\s*(\d+))?', row_text)
                            if fallback:
                                main_num, ext_part = fallback.group(1), fallback.group(2)
                                current_law_num = f"{int(main_num):03d}조"
                                if ext_part:
                                    current_law_num += f"의{ext_part}"

                        for col_idx in range(3):
                            html_content = mapped_cols[col_idx]
                            if not html_content or len(html_content) < 5:
                                continue
                            
                            clean_content = re.sub(r'<[^>]+>', '', html_content)
                            
                            # 불필요한 법령 이름 텍스트 완전 삭제
                            clean_content = re.sub(r'「?국민건강보험법\s*시행(?:령|규칙)」?', '', clean_content)
                            
                            clean_content = re.sub(r'([^\n])\s*(\d+\.)', r'\1\n\2', clean_content)
                            clean_content = re.sub(r'[①-⑮\[<].*?[\d\.]+.*?[\]>]', '', clean_content)
                            clean_content = clean_content.replace("시행령", "").replace("시행규칙", "")
                            clean_content = re.sub(r'[ \t]+', ' ', clean_content)
                            clean_content = re.sub(r'\n\s*\n', '\n', clean_content).strip()
                            
                            if len(clean_content) < 2: continue
                            if clean_content in ["시행규칙", "법률", "내용없음", ".", "-"]: continue
                            
                            # [핵심 교정 3: 제목에서 조 번호 추출 시 '조' 위치 정상화]
                            article_match = re.search(r'제\s*(\d+)\s*조(?:\s*의\s*(\d+))?', clean_content)
                            if article_match:
                                main_n, ext_n = article_match.group(1), article_match.group(2)
                                article_num_str = f"제{main_n}조" + (f"의{ext_n}" if ext_n else "")
                            else:
                                article_num_str = current_law_num.lstrip('0')
                            
                            title_text = ""
                            title_match = re.search(r'\((.*?)\)', clean_content)
                            if title_match:
                                title_text = title_match.group(1).strip()
                            else:
                                first_line = clean_content.replace(article_num_str, "").strip().split('\n')[0]
                                title_text = first_line[:15]
                            
                            clean_title = f"{article_num_str} {title_text}".strip()
                            final_q_name = f"{current_chapter}^{current_law_num}^{col_idx}{type_names.get(col_idx, '법')}^{clean_title}"
                            final_q_name = final_q_name.replace(" ", "")
                            
                            if final_q_name not in existing and not any(r[0] == final_q_name for r in rows_to_add):
                                rows_to_add.append([final_q_name, clean_content[:45000], creator, today])
                                
                    except:
                        continue

            if rows_to_add:
                self.quests_ws.append_rows(rows_to_add)
                self.quest_cache = {'data': [], 'time': 0}
                self.load_global_words()
                return True, f"성공! {len(rows_to_add)}개 저장됨"
            return False, "데이터 없음"
        except Exception as e:
            return False, str(e)

    def delete_all_quests_force(self):
        if not self.ensure_connection():
            return False
        try:
            self.quests_ws.clear()
            self.quests_ws.append_row(self.QUEST_HEADERS)
            self.quest_cache = {'data': [], 'time': 0}
            self.load_global_words() 
            return True
        except:
            return False

    def find_next_quest(self, current_quest_name):
        quests = self.get_quest_list()
        quests.sort(key=lambda x: natural_sort_key(x['quest_name']))
        for i, q in enumerate(quests):
            if q['quest_name'] == current_quest_name:
                if i + 1 < len(quests):
                    return quests[i+1]['quest_name']
        return None

gm = GoogleSheetManager()

# --- [Routes] ---

@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('lobby'))
    return render_template('login.html')

@app.route('/google/login')
def google_login():
    session.permanent = True
    return google_auth.authorize_redirect(url_for('google_callback', _external=True))

@app.route('/google/callback')
def google_callback():
    try:
        token = google_auth.authorize_access_token()
        email = token.get('userinfo')['email']
        session['user_id'] = email
        gm.register_social(email)
        return redirect(url_for('lobby'))
    except Exception as e:
        return f"Login Failed: {e}", 500

@app.route('/lobby')
def lobby():
    if 'user_id' not in session:
        return redirect(url_for('index'))
    
    user, _ = gm.get_user_by_id(session['user_id'])
    all_quests = gm.get_quest_list()
    progress_records = gm.get_my_progress(session['user_id'])
    progress_map = {c['quest_name']: c for c in progress_records}
    
    zones = {}
    for q in all_quests:
        parts = q['quest_name'].split('^')
        if len(parts) >= 4:
            ch = parts[0]
            if ch not in zones:
                chapter_id = int(re.findall(r'\d+', ch)[0])
                zones[ch] = {'id': chapter_id, 'title': ch, 'rows': {}}
            
            ln = parts[1]
            if ln not in zones[ch]['rows']:
                zones[ch]['rows'][ln] = [None, [], [], ln]
            
            q_data = {
                'name': q['quest_name'],
                'disp_name': parts[3],
                'level': int(progress_map.get(q['quest_name'], {}).get('level', 0))
            }
            
            if '0법' in parts[2]:
                zones[ch]['rows'][ln][0] = q_data
            elif '1령' in parts[2]:
                zones[ch]['rows'][ln][1].append(q_data)
            elif '2규' in parts[2]:
                zones[ch]['rows'][ln][2].append(q_data)

    target_chapter = request.args.get('chapter')
    if target_chapter and target_chapter in zones:
        cur_z = zones[target_chapter]
        system_rows = []
        for key in sorted(cur_z['rows'].items(), key=lambda x: natural_sort_key(x[0])):
            val = key[1]
            system_rows.append({'triplet': val[:3], 'law_num': val[3]})
            
        return render_template('lobby.html', user=user, zones=[{'title': cur_z['title'], 'system_rows': system_rows}], view_mode='system')
        
    return render_template('lobby.html', user=user, zones=sorted(zones.values(), key=lambda x: x['id']), view_mode='galaxy')

@app.route('/game_start', methods=['POST'])
def game_start():
    if 'user_id' not in session:
        return redirect(url_for('index'))
    
    q_name = request.form['quest_name']
    all_quests = gm.get_quest_list()
    quest = next((q for q in all_quests if q['quest_name'] == q_name), None)
    
    if not quest:
        return redirect(url_for('lobby'))
    
    stages = []
    q_type = "법률" if "0법" in q_name else ("시행령" if "1령" in q_name else "시행규칙")
    
    chunks = split_content_smartly(quest['content'])
    for chunk in chunks:
        game_content = auto_generate_blanks(chunk)
        targets = [m.group(1).strip() for m in re.finditer(r'\{([^}]+)\}', game_content)]
        
        if not targets:
            continue
            
        render_html = game_content
        options_map = {}
        for i, target in enumerate(targets):
            render_html = render_html.replace(f"{{{target}}}", f'<span id="blank-{i}" class="q-blank"></span>', 1)
            opts = random.sample(get_similar_distractors(target, 3) + [target], 4)
            random.shuffle(opts)
            options_map[str(i)] = opts
            
        stages.append({
            'html': render_html,
            'targets': targets,
            'options_map': options_map,
            'stage_name': q_name.split('^')[-1],
            'q_type': q_type
        })

    ACTIVE_GAMES[session['user_id']] = {
        'mode': request.form.get('action'),
        'quest_name': q_name,
        'stages': stages,
        'chapter': q_name.split('^')[0]
    }
    
    return redirect(url_for('play_game'))

@app.route('/play', methods=['GET', 'POST'])
def play_game():
    if 'user_id' not in session:
        return redirect(url_for('index'))
        
    game = ACTIVE_GAMES.get(session['user_id'])
    if not game:
        return redirect(url_for('lobby'))
        
    user, _ = gm.get_user_by_id(session['user_id'])
    
    if request.method == 'POST':
        duration = request.form.get('duration')
        lv, xp = gm.process_result(session['user_id'], game['quest_name'], None, game['mode'], duration)
        
        next_q = gm.find_next_quest(game['quest_name'])
        
        return render_template('play.html', 
                             stages=game['stages'], 
                             title=game['quest_name'], 
                             chapter=game['chapter'],
                             user_items=user,
                             show_result=True, 
                             reward_lv=lv,
                             next_quest=next_q)
    
    time_per_blank = max(3.0, 12.0 - (user['level'] * 0.2))
    return render_template('play.html', 
                         stages=game['stages'], 
                         title=game['quest_name'], 
                         chapter=game['chapter'],
                         user_items=user,
                         time_per_blank=time_per_blank)

@app.route('/zone/generate', methods=['GET', 'POST'])
def zone_generate():
    if 'user_id' not in session:
        return redirect(url_for('index'))
        
    if request.method == 'POST':
        if 'new_q_file' in request.files:
            file = request.files['new_q_file']
            ok, msg = gm.save_split_quests(None, file, session['user_id'])
            flash(msg)
            return redirect(url_for('lobby'))
            
    return render_template('zone_generate.html')

@app.route('/delete_all_quests', methods=['POST'])
def delete_all_quests():
    if 'user_id' in session:
        gm.delete_all_quests_force()
        flash("전체 데이터가 초기화되었습니다.")
    return redirect(url_for('zone_generate'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

@app.route('/mypage')
def mypage(): 
    user, _ = gm.get_user_by_id(session['user_id'])
    return render_template('mypage.html', user=user)

@app.route('/buy_item', methods=['POST'])
def buy_item():
    item_type = request.form.get('item')
    cost = int(request.form.get('cost'))
    ok, msg = gm.buy_item(session['user_id'], item_type, cost)
    return jsonify({'success': ok, 'msg': msg})

@app.route('/use_item_ingame', methods=['POST'])
def use_item_ingame():
    item_type = request.json.get('item')
    ok = gm.use_item(session['user_id'], item_type)
    return jsonify({'success': ok})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
