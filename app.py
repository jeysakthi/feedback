from fastapi import FastAPI, Request
import uvicorn
import hmac
import hashlib
import json
import time
import requests
import re
import os
import psycopg2
from dotenv import load_dotenv

# ---------------------------
# Load environment variables
# ---------------------------
load_dotenv()
SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET")
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")

DB_CONN_STR = "host=9qasp5v56q8ckkf5dc.leapcellpool.com port=6438 dbname=bssnjulxivtrnqrpojxw user=hjssotfcuzofxciuvyle password=lhepjiccvrflctbbimmwajchcplncd sslmode=require"

app = FastAPI()
feedback_store = []  # In-memory (still kept for debugging)
user_feedback_state = {}

# ---------------------------
# DB Helpers
# ---------------------------
def get_db_connection():
    conn = psycopg2.connect(DB_CONN_STR)
    return conn

def create_feedback_table():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS feedback (
            id SERIAL PRIMARY KEY,
            channel_name TEXT,
            channel_id TEXT,
            user_id TEXT,
            user_name TEXT,
            thread_ts TEXT,
            rating INT,
            comments TEXT,
            jira_id TEXT,
            session_id TEXT,
            timestamp BIGINT
        );
    """)
    conn.commit()
    cur.close()
    conn.close()

create_feedback_table()

def insert_feedback_to_db(feedback):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO feedback (channel_name, channel_id, user_id, user_name, thread_ts, rating, comments, jira_id, session_id, timestamp)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s);
    """, (
        feedback["channel_name"], feedback["channel_id"], feedback["user_id"], feedback["user_name"],
        feedback["thread_ts"], int(feedback["rating"]), feedback["comments"], feedback["jira_id"],
        feedback["session_id"], int(feedback["timestamp"])
    ))
    conn.commit()
    cur.close()
    conn.close()

def fetch_feedback_from_db():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM feedback ORDER BY id DESC;")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows

# ---------------------------
# Verify Slack request
# ---------------------------
def verify_slack_request(request: Request, body: str):
    timestamp = request.headers.get("X-Slack-Request-Timestamp")
    slack_signature = request.headers.get("X-Slack-Signature")
    if abs(time.time() - int(timestamp)) > 60 * 5:
        return False
    sig_basestring = f"v0:{timestamp}:{body}"
    my_signature = "v0=" + hmac.new(
        SLACK_SIGNING_SECRET.encode(),
        sig_basestring.encode(),
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(my_signature, slack_signature)

# ---------------------------
# Slack API helpers
# ---------------------------
def get_user_name(user_id):
    url = "https://slack.com/api/users.info"
    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
    params = {"user": user_id}
    resp = requests.get(url, headers=headers, params=params).json()
    return resp.get("user", {}).get("real_name", "Unknown")

def get_channel_name(channel_id):
    url = "https://slack.com/api/conversations.info"
    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
    params = {"channel": channel_id}
    resp = requests.get(url, headers=headers, params=params).json()
    return resp.get("channel", {}).get("name", "Unknown")

def send_slack_message(url, payload):
    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}", "Content-Type": "application/json"}
    resp = requests.post(url, headers=headers, json=payload)
    return resp.json()

# ---------------------------
# Feedback endpoints
# ---------------------------
@app.get("/feedback")
async def get_feedback():
    rows = fetch_feedback_from_db()
    return {"feedback": rows}

# ---------------------------
# Slack Events endpoint
# ---------------------------
@app.post("/slack/events")
async def slack_events(request: Request):
    body = await request.body()
    body_str = body.decode()
    if not verify_slack_request(request, body_str):
        return {"error": "invalid signature"}

    data = json.loads(body_str)

    if data.get("type") == "url_verification":
        return {"challenge": data["challenge"]}

    if data.get("type") == "event_callback":
        event = data.get("event", {})
        if event.get("type") == "message" and "subtype" not in event:
            user_text = event.get("text", "")
            channel_id = event.get("channel", "")
            thread_ts = event.get("thread_ts", event.get("ts", ""))

            if "The issue you reported has been successfully addressed" in user_text:
                jira_id = extract_jira_id(user_text)
                session_id = extract_session_id(user_text)
                user_feedback_state["jira_id"] = jira_id
                user_feedback_state["session_id"] = session_id
                send_yes_button(channel_id, thread_ts)

    return {"status": "ok"}

def extract_jira_id(text):
    match = re.search(r"JIRA ID:\s*([A-Z0-9-]+)", text)
    return match.group(1) if match else None

def extract_session_id(text):
    match = re.search(r"reference number.*?:\s*([a-f0-9-]+)", text)
    return match.group(1) if match else None

# ---------------------------
# Send Yes button
# ---------------------------
def send_yes_button(channel, thread_ts):
    url = "https://slack.com/api/chat.postMessage"
    payload = {
        "channel": channel,
        "thread_ts": thread_ts,
        "text": "Would you like to provide feedback?",
        "blocks": [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "Would you like to provide feedback?"},
                "accessory": {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Yes"},
                    "style": "primary",
                    "action_id": "show_feedback_form"
                }
            }
        ]
    }
    send_slack_message(url, payload)

# ---------------------------
# Send feedback form
# ---------------------------
def send_feedback_form(channel, thread_ts, user_id):
    url = "https://slack.com/api/chat.postMessage"
    payload = {
        "channel": channel,
        "thread_ts": thread_ts,
        "text": "Please provide your feedback",
        "blocks": [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "*Rate your experience (1-10):*"},
                "accessory": {
                    "type": "static_select",
                    "action_id": "rating_select",
                    "placeholder": {"type": "plain_text", "text": "Select a rating"},
                    "options": [{"text": {"type": "plain_text", "text": str(i)}, "value": str(i)} for i in range(1, 11)]
                }
            },
            {
                "type": "input",
                "block_id": "feedback_block",
                "element": {
                    "type": "plain_text_input",
                    "action_id": "feedback_text",
                    "multiline": True,
                    "placeholder": {"type": "plain_text", "text": "Your feedback here..."}
                },
                "label": {"type": "plain_text", "text": "Feedback (optional)"}
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Submit Feedback"},
                        "style": "primary",
                        "action_id": "submit_feedback"
                    }
                ]
            }
        ]
    }
    resp = send_slack_message(url, payload)
    form_ts = resp.get("ts")
    if user_id and form_ts:
        user_feedback_state[user_id] = user_feedback_state.get(user_id, {})
        user_feedback_state[user_id]["form_ts"] = form_ts

# ---------------------------
# Update feedback form
# ---------------------------
def update_feedback_form(channel, ts, user_name):
    url = "https://slack.com/api/chat.update"
    payload = {
        "channel": channel,
        "ts": ts,
        "text": "Feedback submitted ✅",
        "blocks": [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"Thank you, *{user_name}*! Your feedback has been recorded."}
            }
        ]
    }
    send_slack_message(url, payload)

# ---------------------------
# Interactivity endpoint
# ---------------------------
@app.post("/slack/interactivity")
async def slack_interactivity(request: Request):
    try:
        form_data = await request.form()
        payload = form_data.get("payload")
        if not payload:
            return {"error": "Missing payload"}

        data = json.loads(payload)
        if data.get("type") == "block_actions":
            action_id = data["actions"][0].get("action_id")
            user_id = data.get("user", {}).get("id")
            state = user_feedback_state.get(user_id, {})

            if action_id == "show_feedback_form":
                channel_id = data.get("channel", {}).get("id")
                thread_ts = data.get("container", {}).get("thread_ts") or data.get("container", {}).get("message_ts")
                user_name = get_user_name(user_id)
                user_feedback_state[user_id] = user_feedback_state.get(user_id, {})
                user_feedback_state[user_id]["user_name"] = user_name
                send_feedback_form(channel_id, thread_ts, user_id)

            elif action_id == "rating_select":
                rating = data["actions"][0].get("selected_option", {}).get("value")
                if user_id and rating:
                    user_feedback_state[user_id]["rating"] = rating

            elif action_id == "feedback_text":
                feedback_text = data["actions"][0].get("value", "")
                if user_id:
                    user_feedback_state[user_id]["comments"] = feedback_text

            elif action_id == "submit_feedback":
                channel_id = data.get("channel", {}).get("id")
                thread_ts = data.get("container", {}).get("thread_ts") or data.get("container", {}).get("message_ts")
                rating = state.get("rating")
                comments = data.get("state", {}).get("values", {}).get("feedback_block", {}).get("feedback_text", {}).get("value", "")
                if not rating:
                    return {"text": "Please select a rating before submitting."}

                user_name = state.get("user_name") or get_user_name(user_id)
                channel_name = get_channel_name(channel_id)
                timestamp = time.time()

                feedback_data = {
                    "channel_name": channel_name,
                    "channel_id": channel_id,
                    "user_id": user_id,
                    "user_name": user_name,
                    "thread_ts": thread_ts,
                    "rating": rating,
                    "comments": comments,
                    "jira_id": user_feedback_state.get("jira_id"),
                    "session_id": user_feedback_state.get("session_id"),
                    "timestamp": timestamp
                }

                feedback_store.append(feedback_data)
                insert_feedback_to_db(feedback_data)

                form_ts = state.get("form_ts")
                if form_ts:
                    update_feedback_form(channel_id, form_ts, user_name)

                return {"text": "Thank you for your feedback!"}

        return {"status": "ok"}

    except Exception as e:
        return {"error": str(e)}

# ---------------------------
# Run FastAPI
# ---------------------------
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5001)
