from fastapi import FastAPI, Request
import uvicorn
import hmac
import hashlib
import json
import time
import requests
from dotenv import load_dotenv
import os

# Load environment variables
load_dotenv()
SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET")
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")

app = FastAPI()
feedback_store = []
user_feedback_state = {}  # Store rating/comments per user

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

# ---------------------------
# Feedback endpoints
# ---------------------------
@app.post("/feedback")
async def receive_feedback(request: Request):
    data = await request.json()
    feedback_store.append(data)
    print("✅ Feedback stored:", data)
    return {"status": "success", "received": data}

@app.get("/feedback")
async def get_feedback():
    return {"feedback": feedback_store}

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

            if "This issue is resolved" in user_text:
                send_yes_button(channel_id, thread_ts)

    return {"status": "ok"}

# ---------------------------
# Send Yes button
# ---------------------------
def send_yes_button(channel, thread_ts):
    url = "https://slack.com/api/chat.postMessage"
    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}", "Content-Type": "application/json"}
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
    requests.post(url, headers=headers, json=payload)

# ---------------------------
# Send form message
# ---------------------------
def send_feedback_form(channel, thread_ts):
    url = "https://slack.com/api/chat.postMessage"
    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}", "Content-Type": "application/json"}
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
    requests.post(url, headers=headers, json=payload)

# ---------------------------
# Interactivity endpoint
# ---------------------------
@app.post("/slack/interactivity")
async def slack_interactivity(request: Request):
    form_data = await request.form()
    data = json.loads(form_data.get("payload"))
    print("Interactivity Payload:", data)

    user_id = data["user"]["id"]
    channel_id = data["channel"]["id"]
    thread_ts = data["message"]["ts"]

    if data.get("type") == "block_actions":
        action_id = data["actions"][0]["action_id"]

        if action_id == "show_feedback_form":
            send_feedback_form(channel_id, thread_ts)

        elif action_id == "rating_select":
            rating = data["actions"][0]["selected_option"]["value"]
            user_feedback_state[user_id] = user_feedback_state.get(user_id, {})
            user_feedback_state[user_id]["rating"] = rating

        elif action_id == "feedback_text":
            feedback_text = data["actions"][0]["value"]
            user_feedback_state[user_id] = user_feedback_state.get(user_id, {})
            user_feedback_state[user_id]["comments"] = feedback_text

        elif action_id == "submit_feedback":
            state = user_feedback_state.get(user_id, {})
            rating = state.get("rating")
            comments = state.get("comments", "")
            if not rating:
                return {"text": "Please select a rating before submitting."}

            user_name = get_user_name(user_id)
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
                "timestamp": timestamp
            }
            requests.post("ttps://feedback-jeysakthi1140-p6js52a9.leapcell.dev/feedback", json=feedback_data)
            return {"text": "Thank you for your feedback!"}

    return {"status": "ok"}

# ---------------------------
# Run FastAPI
# ---------------------------
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5001)
