import os
import json
import random
import datetime
import re
import html 
import time
import sqlite3  # SQLite 연동을 위해 추가된 모듈
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
app.config = timedelta(days=31)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

@app.errorhandler(Exception)
def handle_exception(e):
    if isinstance(e, HTTPException):
        return e
    return f"<pre>{traceback.format_exc()}</pre>", 500

# OAuth 설정
app.config = os.environ.get('GOOGLE_CLIENT_ID')
app.config = os.environ.get('GOOGLE_CLIENT_SECRET')

oauth = OAuth(app)
google_auth = oauth.register(
    name='google',
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'}
)

ACTIVE_GAMES = {}
GLOBAL_WORD_POOL = set()

def natural_sort_key(text):
    return [int(c) if c.isdigit() else c for c in re.split(r'(\d+)', text)]

def extract_candidates(text):
    """
   
    단순 단어가 아닌 법적 의미가 변하는 '구(Phrase)' 단위로 빈칸을 추출합니다.
    """
    candidates =
    
    # 1. 조문 제목 (괄호 안 내용)
    titles = re.findall(r'\(([^)]+)\)', text)
    for t in titles:
        if len(t) >= 2 and not re.match(r'^\d+$', t):
            candidates.append(t.strip())

    # 2. 33가지 특수 킬러 패턴 정규식 추출
    complex_patterns = [
        r'(?:즉시|특별한|1건당|특히|모든|최초로|미리|지체\s*없이)\s+[가-힣]+',
        r'(?:대통령령|보건복지부령|공단|공단의 이사장|보건복지부장관|정관|심사평가원장|심사평가원의 정관)(?:으로|이)\s*정(?:한다|하여 고시한다)',
        r'[가-힣\s]*위원회',
        r'\d+(?:일|개월|년)\s*이내|\d+개월의\s*범위',
        r'(?:보건복지부장관|장관)이\s*정하는(?: 바에 따라)?',
        r'(?:보건복지부장관|공단)의\s*승인',
        r'협의|합의',
        r'노동조합|근로자단체',
        r'형법 제\d+조(?:부터 제\d+조까지)?',
        r'[가-힣]{2,4}(?:할\s*수\s*있다|하여야\s*한다|해야\s*한다|한다|된다)',
        r'(?:공단|정관|공단의\s*정관)이\s*정하는\s*(?:바|기간|가액|금액)|공단이\s*(?:지정하는|인정하는)',
        r'공단의\s*이사장|공단',
        r'재해|천재지변',
        r'의료계|의약계|의약관계',
        r'휴업|폐업|합병|폐쇄|폐교',
        r'국고금관리법|국세징수법|국세기본법|민법',
        r'상당하는|해당하는',
        r'전전년도|전년도|해당연도',
        r'과세표준금액|과세표준|과세정보|과세자료|재산자료',
        r'보험료예상수입|보험료수입|보험료등납부금액',
        r'[가-힣]+에게 자료(?: 제출)?을\s*요청|의견(?: 제출)?을\s*요청',
        r'이의신청|심판청구|행정소송',
        r'[가-힣]+로\s*(?:이용|누설|활용|제공)',
        r'연구기관|전문단체|비영리법인|전문가|단체|법인|대학',
        r'(?:1천|1천500|3천|6천|1만|100)분의\s*\d+',
        r'속하는\s*달의\s*다음달|속하는\s*달|인상된\s*달|인하된\s*달|변동된\s*달|전\s*달|그달',
        r'(?:해당(?:되는|하게 된)\s*날|그\s*날|된\s*날|자격이\s*변동된\s*날|사유가\s*발생한\s*날|자격을\s*잃은\s*날|변경된\s*날|결정을\s*한\s*날|접수한\s*날|지난\s*날|해지한\s*날|사유가\s*생긴\s*날|부과된\s*날|기한이\s*지난\s*날|기한의\s*다음날|처분이\s*있은\s*날|안\s*날|통보받은\s*날|끝난\s*날|취득한\s*날|고시하는\s*날|전출된\s*날|전출한\s*날|통지를\s*받은\s*날|지급보류한\s*날|지급하는\s*날|확인한\s*날|발생한\s*날|받은\s*날|제출된\s*날|발급하는\s*날|신청을\s*받은\s*날|따른\s*날|납부하는\s*날까지)',
        r'(?:서면|문서)으로\s*(?:통보|통지|알려야)|서면통지',
        r'보험료등|보험료|연체금|가산금|체납처분비',
        r'징수위탁근거법|징수위탁보험료등'
    ]

    for p in complex_patterns:
        matches = re.findall(p, text)
        for m in matches:
            candidates.append(m.strip())

    # 3. 기본 명사 추출 (기존 로직 보완)
    clean_text = re.sub(r'[0-9]+\.|[가-힣]\.|[①-⑮]', ' ', text)
    clean_text = re.sub(r'\([^)]+\)', ' ', clean_text)
    words = re.findall(r'[가-힣0-9]{2,}', clean_text)
    
    josas = ['은', '는', '이', '가', '을', '를', '의', '에', '로', '으로', '에서', '에게']
    stop_words = ["경우", "또는", "있다", "없다", "관한", "따른", "별표", "어느", "하나", "각호", "대하여", "아니하다", "제외한다", "해당하는"]
    
    for w in words:
        if w in stop_words: continue
        for j in josas:
            if w.endswith(j):
                w = w[:-len(j)]
                break
        if len(w) >= 2 and w not in stop_words and w not in candidates:
            candidates.append(w)
            
    return list(set(candidates))

def get_similar_distractors(target, count=4):
    """
    [맞춤형 함정 보기 생성기]
    정답의 패턴을 분석하여 수험생이 가장 헷갈려하는 4지선다를 동적으로 구성합니다.
    """
    global GLOBAL_WORD_POOL
    target = target.strip()
    
    # 규칙 1: 수식어 유무 함정 (즉시, 미리, 지체 없이 등)
    m1 = re.match(r'(즉시|특별한|1건당|특히|모든|최초로|미리|지체\s*없이)\s+([가-힣]+)', target)
    if m1:
        bw = m1.group(2)
        pool = [bw, f"미리 {bw}", f"지체 없이 {bw}", f"즉시 {bw}", f"특별한 {bw}", f"모든 {bw}"]
        pool = [p for p in pool if p.replace(" ", "")!= target.replace(" ", "")]
        return random.sample(pool, min(len(pool), count))

    # 규칙 2: 제정 주체 함정
    m2 = re.match(r'(대통령령|보건복지부령|공단|공단의 이사장|보건복지부장관|정관|심사평가원장|심사평가원의 정관)(으로|이)\s*정(한다|하여 고시한다)', target)
    if m2:
        pool = ["대통령령으로 정한다", "보건복지부령으로 정한다", "보건복지부장관이 정하여 고시한다", "공단이 정한다", "공단의 이사장이 정한다", "정관으로 정한다", "심사평가원의 정관으로 정한다"]
        pool = [p for p in pool if p.replace(" ", "")!= target.replace(" ", "")]
        return random.sample(pool, min(len(pool), count))

    # 규칙 10: 강제성/재량성 종결어 함정 (할 수 있다 vs 해야 한다)
    m10 = re.match(r'([가-힣]{2,4})(할\s*수\s*있다|하여야\s*한다|해야\s*한다|한다|된다)', target)
    if m10:
        verb = m10.group(1)
        pool = [f"{verb}할 수 있다", f"{verb}하여야 한다", f"{verb}한다", f"{verb}되지 아니한다"]
        pool = [p for p in pool if p.replace(" ", "")!= target.replace(" ", "")]
        return random.sample(pool, min(len(pool), count))

    # 하드코딩된 특수 헷갈림 단어풀 (규칙 3, 4, 6, 7, 8, 14, 15, 16 등 반영)
    static_map = {
        '위원회': ["보건의료정책심의위원회", "건강보험정책심의위원회", "재정운영위원회", "업무정지처분심의위원회", "장기요양위원회", "인사위원회", "국무회의"],
        '이내': ["7일 이내", "10일 이내", "14일 이내", "15일 이내", "20일 이내", "30일 이내", "1개월 이내", "3개월 이내", "1개월의 범위"],
        '보건복지부장관이 정하는': ['보건복지부장관이 정하는 바에 따라', '보건복지부장관이 정하는', '대통령령으로 정하는', '공단이 정하는', '보건복지부령으로 정하는'],
        '승인': ['보건복지부장관의 승인', '공단의 승인', '대통령의 승인', '심사평가원장의 승인'],
        '협의': ['협의', '합의', '승인', '의결'],
        '합의': ['협의', '합의', '승인', '의결'],
        '노동조합': ['노동조합', '근로자단체', '사용자협의회', '시민단체'],
        '근로자단체': ['노동조합', '근로자단체', '사용자협의회', '시민단체'],
        '형법 제': ["형법 제129조부터 제132조까지", "형법 제120조", "민법 제750조", "국가보안법 제3조"],
        '공단이 정하는 바': ['공단이 정하는 바', '공단의 정관이 정하는 기간', '정관이 정하는 금액', '공단이 지정하는'],
        '재해': ['재해', '천재지변', '사변', '전쟁'],
        '천재지변': ['재해', '천재지변', '사변', '전쟁'],
        '의료계': ['의료계', '의약계', '의약관계', '시민단체'],
        '의약계': ['의료계', '의약계', '의약관계', '시민단체'],
        '휴업': ['휴업', '폐업', '합병', '폐쇄', '폐교'],
        '폐업': ['휴업', '폐업', '합병', '폐쇄', '폐교'],
        '국고금관리법': ['국고금관리법', '국세징수법', '국세기본법', '민법', '국가재정법'],
        '상당하는': ['상당하는', '해당하는', '초과하는', '미달하는'],
        '해당하는': ['상당하는', '해당하는', '초과하는', '미달하는'],
        '전년도': ['전전년도', '전년도', '해당연도', '다음연도'],
        '과세표준': ['과세표준', '과세정보', '과세표준금액', '과세자료', '재산자료'],
        '보험료수입': ['보험료수입', '보험료예상수입', '보험료등납부금액', '징수위탁보험료등'],
        '이의신청': ['이의신청', '심판청구', '행정소송', '민사소송'],
        '연구기관': ['연구기관', '전문단체', '비영리법인', '전문가', '단체', '법인', '대학'],
        '분의': ["1천분의 10", "1천500분의 10", "3천분의 10", "6천분의 10", "1만분의 10", "100분의 50", "100분의 100"],
        '날': ["해당하게 된 날", "그 날", "사유가 발생한 날", "자격을 잃은 날", "통보받은 날", "다음 날", "안 날", "결정을 한 날", "신청을 받은 날"],
        '서면': ["서면으로 통보", "문서로 통지", "구두로 통지", "서면통지", "전화로 통보"],
        '통보': ["서면으로 통보", "문서로 통지", "구두로 통지", "서면통지", "전화로 통보"],
        '보험료': ['보험료', '보험료등', '연체금', '가산금', '체납처분비']
    }
    
    # 딕셔너리 키 매칭
    for k, v in static_map.items():
        if k in target:
            pool = [p for p in v if p.replace(" ", "")!= target.replace(" ", "")]
            if pool:
                # 보기가 모자라면 글로벌 풀에서 비슷한 길이 단어 보충
                if len(pool) < count:
                    others =
                    pool += random.sample(others, min(len(others), count - len(pool)))
                return random.sample(pool, min(len(pool), count))

    # 위 규칙에 해당하지 않는 일반 단어 처리 (숫자끼리, 글자수끼리 맞춤)
    if not GLOBAL_WORD_POOL:
        return ["권한", "책임", "의무", "위반"]
        
    if any(char.isdigit() for char in target):
        num_pool =
        if len(num_pool) >= count:
            return random.sample(num_pool, count)
            
    same_len =
    distractors = random.sample(same_len, min(len(same_len), count))
    
    if len(distractors) < count:
        others = list(GLOBAL_WORD_POOL)
        distractors += random.sample(others, min(len(others), count - len(distractors)))
    
    return distractors

def auto_generate_blanks(text, limit=20):
    """
    텍스트에 자동으로 빈칸 생성
    우선순위: 위에서 정의된 긴 구절(Killers) -> 단일 명사
    """
    if '{' in text and '}' in text:
        return text
    
    candidates = extract_candidates(text)
    if not candidates:
        return text
        
    # 빈칸 뚫을 때 우선순위 스코어링 (점수가 높을수록 무조건 먼저 빈칸 됨)
    def get_score(word):
        score = len(word) * 10 # 기본적으로 긴 구절 우선 (예: '대통령령으로 정한다' > '대통령령')
        if re.search(r'\d', word): score += 50 # 숫자 우선
        if word.endswith('한다') or word.endswith('있다'): score += 40 # 서술어 함정 우선
        if '위원회' in word or '날' in word: score += 30 # 위원회, 날짜 우선
        return score

    # 점수가 높은 순서대로 적용
    scored_candidates = sorted(candidates, key=lambda w: get_score(w), reverse=True)
    
    new_text = text
    count = 0
    for t in scored_candidates:
        if count >= limit:
            break
        # 이미 빈칸({ }) 처리된 부분에 포함되어 있는지 확인하여 중복 방지
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
    
    chunks =
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
        self.user_quests_ws = None
        self.user_cache = {}
        self.quest_cache = {'data':, 'time': 0}
        
        # [핵심 1] 유저 클리어 기록을 저장할 캐시 추가
        self.collection_cache = {'data':, 'time': 0} 
        
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
            return

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
                        row['nickname'] = str(user_id).split('@')
                        
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
                self.users_ws.append_row()
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
        return

    def get_my_progress(self, user_id): 
        if not self.ensure_connection():
            return
            
        # [핵심 2] 5분 이내의 기록이 맥미니에 캐시되어 있다면 구글에 묻지 않고 바로 반환 (속도 100배 향상)
        if self.collection_cache['data'] and (time.time() - self.collection_cache['time'] < self.CACHE_DURATION):
            records = self.collection_cache['data']
        else:
            records = self.get_safe_records(self.collections_ws)
            self.collection_cache = {'data': records, 'time': time.time()}
            
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
        
        # [핵심 3] 유저가 행성을 클리어해서 구글 시트가 업데이트될 예정이므로, 기존 캐시를 강제로 비워 다음 로비 진입 시 새로고침하도록 함
        self.collection_cache['time'] = 0
        
        if mode == 'terraform': 
            if found_idx == -1:
                self.collections_ws.append_row()
                return self.add_xp(user_id, 100, 50)
            else:
                self.collections_ws.update_cell(found_idx, 4, today)
                return self.add_xp(user_id, 50, 30)
        else: 
            if found_idx!= -1:
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
            rows_to_add =
            
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
                            c_name = re.sub(r'[^\w\s]', '', chap_match.group(2).split('(')).strip()[:15] or "총칙"
                            current_chapter = f"{int(c_num)}. {c_name}"
                            if len(row_text) < 40 and "조" not in row_text:
                                continue

                        cols = re.split(r'<td[^>]*>', row_html, flags=re.IGNORECASE)[1:]
                        if not cols:
                            continue
                        
                        c0_raw = re.sub(r'<[^>]+>', '', cols).strip()
                        is_act_cell = bool(re.match(r'^\s*제\s*\d+\s*조(?:\s*의\s*\d+)?', c0_raw))
                        
                        mapped_cols = ["", "", ""]
                        if len(cols) >= 3:
                            mapped_cols = cols[:3]
                        elif len(cols) == 2:
                            if is_act_cell:
                                mapped_cols, mapped_cols[1] = cols, cols[1]
                            else:
                                mapped_cols[1], mapped_cols[2] = cols, cols[1]
                        elif len(cols) == 1:
                            if is_act_cell:
                                mapped_cols = cols
                            else:
                                mapped_cols[1] = cols

                        if mapped_cols.strip():
                            law_match = re.search(r'제\s*(\d+)\s*조(?:\s*의\s*(\d+))?', re.sub(r'<[^>]+>', '', mapped_cols))
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
                            
                            # [핵심 로직] 이 칸(cell) 안에 특정 키워드가 들어있으면 패스하여 퀘스트로 만들지 않음
                            if re.search(r'국민건강보험\s*요양급여의\s*기준', clean_content):
                                continue
                            
                            clean_content = re.sub(r'「?국민건강보험법\s*시행(?:령|규칙)」?', '', clean_content)
                            clean_content = re.sub(r'([^\n])\s*(\d+\.)', r'\1\n\2', clean_content)
                            clean_content = re.sub(r'[①-⑮\[<].*?[\d\.]+.*?[\]>]', '', clean_content)
                            clean_content = clean_content.replace("시행령", "").replace("시행규칙", "")
                            clean_content = re.sub(r'[ \t]+', ' ', clean_content)
                            clean_content = re.sub(r'\n\s*\n', '\n', clean_content).strip()
                            
                            if len(clean_content) < 2: continue
                            if clean_content in ["시행규칙", "법률", "내용없음", ".", "-"]: continue
                            
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
                                first_line = clean_content.replace(article_num_str, "").strip().split('\n')
                                title_text = first_line[:15]
                            
                            clean_title = f"{article_num_str} {title_text}".strip()
                            final_q_name = f"{current_chapter}^{current_law_num}^{col_idx}{type_names.get(col_idx, '법')}^{clean_title}"
                            final_q_name = final_q_name.replace(" ", "")
                            
                            if final_q_name not in existing and not any(r == final_q_name for r in rows_to_add):
                                rows_to_add.append([final_q_name, clean_content[:45000], creator, today])
                                
                    except:
                        continue

            if rows_to_add:
                self.quests_ws.append_rows(rows_to_add)
                self.quest_cache = {'data':, 'time': 0}
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
            self.quest_cache = {'data':, 'time': 0}
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

# --- ---

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
            ch = parts
            if ch not in zones:
                chapter_id = int(re.findall(r'\d+', ch))
                zones[ch] = {'id': chapter_id, 'title': ch, 'rows': {}}
            
            ln = parts[1]
            if ln not in zones[ch]['rows']:
                zones[ch]['rows'][ln] = [None,,, ln]
            
            q_data = {
                'name': q['quest_name'],
                'disp_name': parts[3],
                'level': int(progress_map.get(q['quest_name'], {}).get('level', 0))
            }
            
            if '0법' in parts[2]:
                zones[ch]['rows'][ln] = q_data
            elif '1령' in parts[2]:
                zones[ch]['rows'][ln].[1]append(q_data)
            elif '2규' in parts[2]:
                zones[ch]['rows'][ln].[2]append(q_data)

    target_chapter = request.args.get('chapter')
    if target_chapter and target_chapter in zones:
        cur_z = zones[target_chapter]
        system_rows =
        for key in sorted(cur_z['rows'].items(), key=lambda x: natural_sort_key(x)):
            val = key[1]
            system_rows.append({'triplet': val[:3], 'law_num': val[3]})
            
        return render_template('lobby.html', user=user, zones=[{'title': cur_z['title'], 'system_rows': system_rows}], view_mode='system')
        
    return render_template('lobby.html', user=user, zones=sorted(zones.values(), key=lambda x: x['id']), view_mode='galaxy')

@app.route('/game_start', methods=)
def game_start():
    if 'user_id' not in session:
        return redirect(url_for('index'))
    
    q_name = request.form['quest_name']
    all_quests = gm.get_quest_list()
    quest = next((q for q in all_quests if q['quest_name'] == q_name), None)
    
    if not quest:
        return redirect(url_for('lobby'))
    
    stages =
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
        'chapter': q_name.split('^')
    }
    
    return redirect(url_for('play_game'))

@app.route('/play', methods=)
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

@app.route('/zone/generate', methods=)
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

@app.route('/delete_all_quests', methods=)
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

@app.route('/buy_item', methods=)
def buy_item():
    item_type = request.form.get('item')
    cost = int(request.form.get('cost'))
    ok, msg = gm.buy_item(session['user_id'], item_type, cost)
    return jsonify({'success': ok, 'msg': msg})

@app.route('/use_item_ingame', methods=)
def use_item_ingame():
    item_type = request.json.get('item')
    ok = gm.use_item(session['user_id'], item_type)
    return jsonify({'success': ok})

# ==========================================
# [신규] 문제은행 및 RPG 전투 계산 API 영역
# ==========================================

# DB 연결 헬퍼 함수
def get_db_connection():
    conn = sqlite3.connect('problem_bank.db')
    conn.row_factory = sqlite3.Row  # 컬럼명으로 데이터에 접근할 수 있게 설정
    return conn

# 1. 문제은행에서 무작위 기출문제 호출 API
@app.route('/api/quiz', methods=)
def get_random_quiz():
    """문제은행 DB에서 무작위로 기출문제를 하나 불러와 프론트엔드로 전달합니다."""
    try:
        conn = get_db_connection()
        # 난이도나 특정 키워드에 맞춰 랜덤으로 1개의 문제를 가져옵니다.
        question = conn.execute('SELECT * FROM questions ORDER BY RANDOM() LIMIT 1').fetchone()
        
        if not question:
            conn.close()
            return jsonify({"error": "문제은행 데이터베이스가 비어있습니다."}), 404
            
        # 해당 문제의 선택지(보기) 가져오기
        choices = conn.execute('SELECT * FROM choices WHERE question_id =?', (question['id'],)).fetchall()
        conn.close()
        
        return jsonify({
            "id": question['id'],
            "exam_source": question['exam_id'], # 예: 200321 부산 문제
            "content": question['content'],
            "point": 0.8, # 모의고사 기준 배점 적용
            "choices": [{"id": c['id'], "text": c['content']} for c in choices]
        })
    except sqlite3.OperationalError:
        return jsonify({"error": "데이터베이스 파일(problem_bank.db)이 없거나 접근할 수 없습니다."}), 500

# 2. 정답 제출 및 RPG 데미지 계산 API
@app.route('/api/combat', methods=)
def resolve_combat():
    """제출된 정답을 확인하고 RPG 공식에 따라 몬스터에게 입힐 데미지를 계산합니다."""
    data = request.json
    question_id = data.get('question_id')
    selected_choice_id = data.get('selected_choice_id')
    question_point = data.get('point', 0.8) # 기본 0.8점 가중치
    
    conn = get_db_connection()
    correct_choice = conn.execute('SELECT id FROM choices WHERE question_id =? AND is_correct = 1', (question_id,)).fetchone()
    conn.close()

    is_correct = (correct_choice and correct_choice['id'] == selected_choice_id)
    
    # 임시 플레이어 및 몬스터 스탯 설정 (추후 세션이나 DB 연동)
    player_base_attack = 10
    enemy_defense = 10
    
    if is_correct:
        # 0.8점 문항의 배점을 공격력 가중치로 치환
        actual_attack = player_base_attack * question_point
        
        # 합리적인 데미지 산출 공식 적용
        if actual_attack >= enemy_defense:
            damage = (actual_attack * 2) - enemy_defense
        else:
            damage = (actual_attack * actual_attack) / enemy_defense
            
        return jsonify({
            "status": "hit", 
            "is_correct": True,
            "damage": round(damage, 1), 
            "message": f"정답입니다! 몬스터에게 {round(damage, 1)}의 피해를 입혔습니다."
        })
    else:
        return jsonify({
            "status": "miss", 
            "is_correct": False,
            "damage": 0, 
            "message": "오답입니다! 적의 반격으로 체력이 감소합니다."
        })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
