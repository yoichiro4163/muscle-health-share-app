import os
import base64
import json
import datetime
from flask import Flask, render_template, request, jsonify
from openai import OpenAI
import firebase_admin
from firebase_admin import credentials, firestore

# --- OpenAI APIキーの設定 ---
try:
    client = OpenAI(
        api_key=os.environ['OPENAI_API_KEY']
    )
    print("OpenAI APIキーの読み込み成功")
except KeyError:
    print("エラー: OPENAI_API_KEY がSecretsに設定されていません。")
    client = None

# --- Firebase の設定 ---
try:
    # Secretsから合鍵（JSONの中身）を読み込む
    service_account_json_string = os.environ['FIREBASE_SERVICE_ACCOUNT']
    service_account_dict = json.loads(service_account_json_string)
    
    cred = credentials.Certificate(service_account_dict)
    
    # すでに初期化されているかチェックしてから初期化
    if not firebase_admin._apps:
        firebase_admin.initialize_app(cred)
    
    db = firestore.client()
    print("Firebaseの初期化に成功")

except KeyError:
    print("エラー: FIREBASE_SERVICE_ACCOUNT がSecretsに設定されていません。")
    db = None
except Exception as e:
    print(f"Firebase初期化エラー: {e}")
    db = None


app = Flask(__name__)

# --- メインページ ---
@app.route('/')
def index():
    return render_template('index.html')


# --- 1. 食事画像の分析と記録 ---
@app.route('/analyze', methods=['POST'])
def analyze_image():
    if not client: return jsonify({'error': 'OpenAI APIキー設定なし'}), 500
    if not db: return jsonify({'error': 'Firebase初期化エラー'}), 500

    if 'image' not in request.files: return jsonify({'error': 'ファイルなし'}), 400
    file = request.files['image']
    if file.filename == '': return jsonify({'error': 'ファイル未選択'}), 400

    # ユーザー名を取得
    user_name = request.form.get('user_name', '名無しさん')

    try:
        image_bytes = file.read()
        base64_image = base64.b64encode(image_bytes).decode('utf-8')
    except Exception as e: return jsonify({'error': str(e)}), 400

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": """
                            この食事の画像のカロリー、PFCバランスを推定してください。
                            回答は必ず以下のJSON形式でお願いします。
                            { "calories": "約 XXX kcal", "pfc": "P: XXg, F: XXg, C: XXg" }
                        """},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
                    ]
                }
            ],
            max_tokens=300
        )
        ai_response = response.choices[0].message.content
        if "```json" in ai_response:
            ai_response = ai_response.split("```json\n")[1].split("\n```")[0]
        ai_data = json.loads(ai_response)
        
        # Firestoreに保存
        db.collection('activities').add({
            'type': 'food',
            'user_name': user_name,
            'calories': ai_data.get('calories', '不明'),
            'pfc': ai_data.get('pfc', '不明'),
            'timestamp': datetime.datetime.now(datetime.timezone.utc)
        })
        
        return jsonify(ai_data)

    except Exception as e:
        print(f"エラー: {e}")
        return jsonify({'error': str(e)}), 500


# --- 2. トレーニング記録 ---
@app.route('/log_training', methods=['POST'])
def log_training():
    if not db: return jsonify({'error': 'Firebase初期化エラー'}), 500

    try:
        data = request.json
        duration = data.get('duration')
        user_name = data.get('user_name', '名無しさん')

        if not duration: return jsonify({'error': '時間なし'}), 400

        # Firestoreに保存
        db.collection('activities').add({
            'type': 'training',
            'user_name': user_name,
            'duration': duration,
            'timestamp': datetime.datetime.now(datetime.timezone.utc)
        })
        
        return jsonify({'status': 'success'})

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# --- 3. 活動履歴（タイムライン）の取得 ---
@app.route('/get_activities', methods=['GET'])
def get_activities():
    if not db: return jsonify({'error': 'Firebase初期化エラー'}), 500

    try:
        # 最新20件を取得 (文字列 'DESCENDING' で指定)
        activities_ref = db.collection('activities').order_by(
            'timestamp', direction='DESCENDING'
        ).limit(20)

        activities = []
        for doc in activities_ref.stream():
            data = doc.to_dict()
            data['id'] = doc.id
            
            if 'timestamp' in data and data['timestamp']:
                # UTC -> JST変換
                jst_timestamp = data['timestamp'].astimezone(datetime.timezone(datetime.timedelta(hours=9)))
                data['timestamp_str'] = jst_timestamp.strftime('%Y年%m月%d日 %H:%M')
            
            activities.append(data)
        
        return jsonify(activities)

    except Exception as e:
        print(f"活動履歴の取得エラー: {e}")
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001)