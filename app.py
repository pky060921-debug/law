import os
import json
import random
import datetime
import re
from flask import Flask, render_template, request, redirect, url_for, session, flash
import gspread
from oauth2client.service_account import ServiceAccountCredentials

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'default_magic_key')

# 진행 데이터 및 캐시
ACTIVE_GAMES = {}

class GoogleSheetManager:
    def __init__(self):
        self.users_ws = None
        self.quests_ws = None
        self.collections_ws = None
        
        try:
            json_creds = os.environ.get('GCP_CREDENTIALS')
            if not json_creds:
                print("⚠️ GCP 자격 증명 없음")
                return
            
            creds_dict = json.loads(json_creds)
            scope = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
            self.client = gspread.authorize(creds)
            self.sheet = self.client.open("memory_game_db")
            
            try: self.users_ws = self.sheet.worksheet("users")
            except: 
                self.users_ws = self.sheet.add_worksheet("users", 100, 10)
                self.users_ws.append_row(["user_id", "password", "level", "xp", "title", "last_idx"])
            
            try: self.collections_ws = self.sheet.worksheet("collections")
            except: self.collections_ws = self.sheet.add_worksheet("collections", 100, 10); self.collections_ws.append_row(["user_id", "card_text", "grade", "collected_at", "quest_name", "count"])
            
            try: self.quests_ws = self.sheet.worksheet("quests")
            except: self.quests_ws = self.sheet.add_worksheet("quests", 100, 5); self.quests_ws.append_row(["quest_name", "content", "created_by", "created_at"])
            
            print("✅ 구글 시트 연결 성공")
        except Exception as e:
            print(f"🔥🔥 시트 연결 에러: {e}")

    # ... (login, register, process_reward, get_collections 등 기존 함수는 그대로 유지) ...
    def get_quest_list(self):
        if self.quests_ws is None: return []
        try: return self.quests_ws.get_all_records()
        except: return []

    def login(self, user_id, password):
        if self.users_ws is None: return None, None
        try:
            records = self.users_ws.get_all_records()
            for i, row in enumerate(records):
                if str(row['user_id']) == str(user_id) and str(row['password']) == str(password):
                    row['last_idx'] = row.get('last_idx', 0)
                    if row['last_idx'] == '': row['last_idx'] = 0
                    return row, i + 2
        except: pass
        return None, None
        
    def register(self, user_id, password):
        if self.users_ws is None: return False
        try:
            records = self.users_ws.get_all_records()
            for row in records:
                if str(row['user_id']) == str(user_id): return False
            self.users_ws.append_row([user_id, password, 1, 0, "견습 마법사", 0])
            return True
        except: return False

    def process_reward(self, user_id, card_text, current_level, current_xp, row_idx, quest_name):
        # (기존 코드와 동일)
        if self.collections_ws is None: return "ERROR", 0, current_level, current_xp, "ERROR", 0
        records = self.collections_ws.get_all_records()
        found_idx = -1; current_count = 0; current_grade = "NORMAL"
        for i, row in enumerate(records):
            if str(row['user_id']) == str(user_id) and row['card_text'] == card_text and row.get('quest_name') == quest_name:
                found_idx = i + 2; current_count = row.get('count', 1); current_grade = row.get('grade', 'NORMAL'); break
        
        status = ""; final_grade = current_grade
        if found_idx != -1:
            new_count = current_count + 1
            if new_count >= 7: new_grade = "LEGEND"
            elif new_count >= 3: new_grade = "RARE"
            else: new_grade = current_grade
            self.collections_ws.update_cell(found_idx, 6, new_count)
            self.collections_ws.update_cell(found_idx, 3, new_grade)
            self.collections_ws.update_cell(found_idx, 4, str(datetime.date.today()))
            status = "UPGRADE"; final_grade = new_grade; xp_gain = 10 + (new_count * 2)
        else:
            rand = random.random()
            if rand < 0.05: final_grade = "LEGEND"
            elif rand < 0.20: final_grade = "RARE"
            else: final_grade = "NORMAL"
            self.collections_ws.append_row([user_id, card_text, final_grade, str(datetime.date.today()), quest_name, 1])
            status = "NEW"; xp_gain = 50 if final_grade == "LEGEND" else 30 if final_grade == "RARE" else 20
        new_xp = current_xp + xp_gain
        new_level, req_xp = current_level, current_level * 100
        if new_xp >= req_xp: new_level += 1; new_xp -= req_xp
        self.users_ws.update_cell(row_idx, 3, new_level)
        self.users_ws.update_cell(row_idx, 4, new_xp)
        return final_grade, xp_gain, new_level, new_xp, status, current_count + 1 if found_idx != -1 else 1

    def get_collections(self, user_id):
        if self.collections_ws is None: return []
        try: return [c for c in self.collections_ws.get_all_records() if str(c['user_id']) == str(user_id)]
        except: return []

    def update_progress(self, row_idx, new_idx):
        if self.users_ws is None: return
        try: self.users_ws.update_cell(row_idx, 6, new_idx)
        except: pass

    # [핵심 추가] 여러 줄을 한 번에 저장하는 함수
    def save_split_quests(self, title_prefix, lines, creator):
        if self.quests_ws is None: return False
        try:
            today = str(datetime.date.today())
            rows_to_add = []
            
            # 기존 퀘스트 이름 목록 가져오기 (중복 방지용)
            existing = [str(r.get('quest_name')) for r in self.quests_ws.get_all_records()]
            
            for i, line in enumerate(lines):
                if not line.strip(): continue # 빈 줄 건너뜀
                
                # 네이밍 규칙: 제목-1, 제목-2 ...
                # (내용에 '법', '령' 등이 있으면 감지해서 붙여줄 수도 있음)
                suffix = f"{i+1}"
                quest_name = f"{title_prefix}-{suffix}"
                
                # 중복이면 건너뜀
                if quest_name in existing: continue
                
                rows_to_add.append([quest_name, line[:45000], creator, today])
            
            if rows_to_add:
                self.quests_ws.append_rows(rows_to_add)
                return True, len(rows_to_add)
            return False, 0
        except Exception as e:
            print(f"저장 에러: {e}")
            return False, 0
            
    # [추가] 단일 퀘스트 내용 업데이트 (편집 후 저장용)
    def update_quest_content(self, quest_name, new_content):
        try:
            cell = self.quests_ws.find(quest_name, in_column=1) # A열에서 이름 찾기
            if cell:
                # B열(Content) 업데이트 (row, col)
                self.quests_ws.update_cell(cell.row, 2, new_content)
                return True
        except Exception as e:
            print(f"업데이트 에러: {e}")
        return False

gm = GoogleSheetManager()

# --- 헬퍼 함수 ---
def parse_manual_blanks(text):
    pattern = r'\{([^}]+)\}'
    parts = []
    targets = []
    last_idx = 0
    input_id = 0
    for match in re.finditer(pattern, text):
        start, end = match.span()
        answer = match.group(1).strip()
        if start > last_idx: parts.append({'type': 'text', 'val': text[last_idx:start]})
        if answer:
            parts.append({'type': 'input', 'id': input_id})
            targets.append(answer)
            input_id += 1
        last_idx = end
    if last_idx < len(text): parts.append({'type': 'text', 'val': text[last_idx:]})
    return parts, targets

# --- 라우트 ---
@app.route('/')
def index():
    if 'user_id' in session: return redirect(url_for('lobby'))
    return render_template('login.html')

@app.route('/login', methods=['POST'])
def login():
    uid = request.form.get('id'); upw = request.form.get('pw')
    user_data, row_idx = gm.login(uid, upw)
    if user_data:
        session['user_id'] = uid; session['user_row_idx'] = row_idx
        session['level'] = user_data['level']; session['xp'] = user_data['xp']
        session['last_idx'] = user_data.get('last_idx', 0)
        return redirect(url_for('lobby'))
    return redirect(url_for('index'))

@app.route('/register', methods=['POST'])
def register():
    uid = request.form.get('new_id'); upw = request.form.get('new_pw')
    gm.register(uid, upw)
    return redirect(url_for('index'))

@app.route('/logout')
def logout(): session.clear(); return redirect(url_for('index'))

@app.route('/lobby')
def lobby():
    if 'user_id' not in session: return redirect(url_for('index'))
    return render_template('lobby.html', user_id=session['user_id'], level=session['level'], xp=session['xp'], req_xp=session['level']*100)

@app.route('/dungeon', methods=['GET', 'POST'])
def dungeon():
    if 'user_id' not in session: return redirect(url_for('index'))
    
    if request.method == 'POST':
        # [1] 퀘스트 선택 (플레이)
        if 'quest_select' in request.form:
            q_name = request.form['quest_select']
            quests = gm.get_quest_list()
            selected_quest = next((q for q in quests if str(q.get('quest_name', '')).strip() == str(q_name).strip()), None)
            
            if not selected_quest: return redirect(url_for('dungeon'))

            # 여기서는 문장을 나누지 않고 통째로 가져옵니다. (한 퀘스트 = 한 문단/조항)
            content = selected_quest.get('content', "")
            
            # 메모리에 저장 (진행도는 0으로 초기화하지 않음 - 조항 단위이므로 그냥 0)
            user_id = session['user_id']
            ACTIVE_GAMES[user_id] = {
                'content': content, # 통째로 저장
                'quest_name': q_name,
                'curr_targets': [],
                'edit_mode': False # 기본은 플레이 모드지만, 내용 확인 후 변경
            }
            
            # 만약 내용에 '{' 가 없다면 -> 편집 모드(Edit Mode)로 시작
            if '{' not in content:
                ACTIVE_GAMES[user_id]['edit_mode'] = True
                flash("첫 회독입니다! 빈칸을 만들 단어를 선택해주세요.")
            
            return redirect(url_for('dungeon_play'))
            
        # [2] 파일 업로드 -> 자동 분할 저장
        elif 'new_q_name' in request.form:
            title_prefix = request.form['new_q_name']
            f = request.files.get('new_q_file')
            
            if title_prefix and f:
                raw_text = f.read().decode('utf-8')
                # 줄바꿈 기준으로 나누기
                lines = raw_text.replace('\r\n', '\n').split('\n')
                
                success, count = gm.save_split_quests(title_prefix, lines, session['user_id'])
                if success:
                    flash(f"'{title_prefix}' 시리즈로 {count}개의 퀘스트가 생성되었습니다!")
                else:
                    flash("저장 실패")
            return redirect(url_for('dungeon'))

    quests = gm.get_quest_list()
    # 퀘스트 목록 정렬 (이름순)
    quests.sort(key=lambda x: x.get('quest_name', ''))
    return render_template('dungeon.html', quests=quests)


@app.route('/dungeon/play', methods=['GET', 'POST'])
def dungeon_play():
    if 'user_id' not in session: return redirect(url_for('index'))
    user_id = session['user_id']
    game_data = ACTIVE_GAMES.get(user_id)
    if not game_data: return redirect(url_for('dungeon'))
    
    content = game_data['content']
    
    # --- [A] 편집 모드 (빈칸 만들기) ---
    if game_data['edit_mode']:
        if request.method == 'GET':
            # 단어 단위로 쪼개서 보여줌 (조사 포함)
            words = content.split(' ') # 단순 공백 분리
            return render_template('dungeon_play.html', edit_mode=True, words=words)
        
        elif request.method == 'POST':
            # 사용자가 편집한 내용 저장 요청
            # JS에서 만들어진 "{단어} {단어}..." 형태의 텍스트가 옴
            new_content = request.form.get('edited_content')
            if new_content:
                # 1. 구글 시트에 업데이트 (영구 저장)
                gm.update_quest_content(game_data['quest_name'], new_content)
                # 2. 게임 데이터 갱신 및 플레이 모드로 전환
                game_data['content'] = new_content
                game_data['edit_mode'] = False
                flash("빈칸 설정 완료! 암기를 시작합니다.")
                return redirect(url_for('dungeon_play'))
            
    # --- [B] 플레이 모드 (빈칸 채우기) ---
    else:
        if request.method == 'GET':
            parts, targets = parse_manual_blanks(content)
            game_data['curr_targets'] = targets
            
            # 빈칸이 하나도 없으면 (편집 후에도 없을 경우) 그냥 보여줌
            return render_template('dungeon_play.html', edit_mode=False, parts=parts, has_blanks=len(targets) > 0)

        elif request.method == 'POST':
            # 정답 확인 로직
            user_inputs = request.form.getlist('answers')
            targets = game_data.get('curr_targets', [])
            
            if not targets: # 빈칸 없으면 그냥 통과
                flash("내용 확인 완료.")
                return redirect(url_for('dungeon'))

            all_correct = True
            if len(user_inputs) != len(targets): all_correct = False
            else:
                for u, t in zip(user_inputs, targets):
                    if u.strip() != t: all_correct = False; break
            
            if all_correct:
                # 보상 지급 (빈칸 없는 깨끗한 텍스트로 저장)
                clean_text = re.sub(r'\{([^}]+)\}', r'\1', content)
                g, gain, nl, nx, stat, cnt = gm.process_reward(
                    session['user_id'], clean_text, session['level'], session['xp'], 
                    session['user_row_idx'], game_data['quest_name']
                )
                session['level'] = nl; session['xp'] = nx
                flash(f"✅ 정답입니다! (+{gain} XP)")
                # 조항 단위이므로 하나 깨면 밖으로 나감 (또는 다음 조항으로 자동 이동 구현 가능)
                return redirect(url_for('dungeon'))
            else:
                flash("❌ 오답입니다. 다시 도전하세요.")
            return redirect(url_for('dungeon_play'))
            
@app.route('/collection')
def collection():
    if 'user_id' not in session: return redirect(url_for('index'))
    cards = gm.get_collections(session['user_id'])
    return render_template('collection.html', cards=cards)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
