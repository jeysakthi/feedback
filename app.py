from fastapi import FastAPI, Request
import uvicorn
import hmac
import hashlib
import json
import time
import requests
import re
from dotenv import load_dotenv
import os

# Load environment variables
print("🔍 Loading environment variables...")
load_dotenv()
SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET")
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
print(f"✅ SLACK_SIGNING_SECRET loaded: {bool(SLACK_SIGNING_SECRET)}")
print(f"✅ SLACK_BOT_TOKEN loaded: {bool(SLACK_BOT_TOKEN)}")

app = FastAPI()
feedback_store = []

# ---------------------------
# Verify Slack request
# ---------------------------
def verify_slack_request(request: Request, body: str):
    print("🔍 Verifying Slack request...")
    timestamp = request.headers.get("X-Slack-Request-Timestamp")
    slack_signature = request.headers.get("X-Slack-Signature")
    print(f"Headers -> Timestamp: {timestamp}, Signature: {slack_signature}")

    if abs(time.time() - int(timestamp)) > 60 * 5:
        print("❌ Request timestamp too old!")
        return False

    sig_basestring = f"v0:{timestamp}:{body}"
    my_signature = "v0=" + hmac.new(
        SLACK_SIGNING_SECRET.encode(),
        sig_basestring.encode(),
        hashlib.sha256
    ).hexdigest()
    print(f"Generated Signature: {my_signature}")

    is_valid = hmac.compare_digest(my_signature, slack_signature)
    print(f"✅ Signature valid: {is_valid}")
    return is_valid

# ---------------------------
# Slack API helpers
# ---------------------------
def get_user_name(user_id):
    print(f"🔍 Fetching user name for user_id: {user_id}")
    url = "https://slack.com/api/users.info"
    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
    params = {"user": user_id}
    resp = requests.get(url, headers=headers, params=params).json()
    print(f"User Info Response: {resp}")
    return resp.get("user", {}).get("real_name", "Unknown")

def get_channel_name(channel_id):
    print(f"🔍 Fetching channel name for channel_id: {channel_id}")
    url = "https://slack.com/api/conversations.info"
    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
    params = {"channel": channel_id}
    resp = requests.get(url, headers=headers, params=params).json()
    print(f"Channel Info Response: {resp}")
    return resp.get("channel", {}).get("name", "Unknown")

# ---------------------------
# Feedback endpoints
# ---------------------------
@app.post("/feedback")
async def receive_feedback(request: Request):
    print("🔍 Receiving feedback...")
    data = await request.json()
    print(f"✅ Feedback data received: {data}")
    feedback_store.append(data)
    return {"status": "success", "received": data}

@app.get("/feedback")
async def get_feedback():
    print("🔍 Fetching all feedback...")
    return {"feedback": feedback_store}

# ---------------------------
# Slack Events endpoint
# ---------------------------
@app.post("/slack/events")
async def slack_events(request: Request):
    print("🔍 Slack event received...")
    body = await request.body()
    body_str = body.decode()
    print(f"Request Body: {body_str}")

    if not verify_slack_request(request, body_str):
        print("❌ Invalid Slack signature!")
        return {"error": "invalid signature"}

    data = json.loads(body_str)
    print(f"Parsed JSON: {data}")

    if data.get("type") == "url_verification":
        print("✅ URL verification challenge received.")
        return {"challenge": data["challenge"]}

    if data.get("type") == "event_callback":
        event = data.get("event", {})
        print(f"Event Data: {event}")

        if event.get("type") == "message" and "subtype" not in event:
            user_text = event.get("text", "")
            channel_id = event.get("channel", "")
            user_id = event.get("user", "")
            thread_ts = event.get("thread_ts", event.get("ts", ""))
            timestamp = event.get("ts", "")

            print(f"✅ Message received: {user_text}")
            print(f"Channel ID: {channel_id}, User ID: {user_id}, Thread TS: {thread_ts}, Timestamp: {timestamp}")

            # If message contains "This issue is resolved", send feedback button
            if "This issue is resolved" in user_text:
                print("✅ Trigger phrase detected, sending feedback button...")
                send_feedback_button(channel_id, thread_ts)

    return {"status": "ok"}

# ---------------------------
# Send feedback button
# ---------------------------
def send_feedback_button(channel, thread_ts):
    print(f"🔍 Sending feedback button to channel: {channel}, thread: {thread_ts}")
    url = "https://slack.com/api/chat.postMessage"
    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}", "Content-Type": "application/json"}
    payload = {
        "channel": channel,
        "thread_ts": thread_ts,
        "text": "Would you like to submit feedback?",
        "blocks": [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "Would you like to submit feedback?"},
                "accessory": {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Submit Feedback"},
                    "action_id": "open_feedback_form"
                }
            }
        ]
    }
    response = requests.post(url, headers=headers, json=payload)
    print(f"✅ Button Response: {response.status_code} {response.text}")

# ---------------------------
# Interactivity endpoint
# ---------------------------
@app.post("/slack/interactivity")
async def slack_interactivity(request: Request):
    print("🔍 Interactivity payload received...")
    form_data = await request.form()
    data = json.loads(form_data.get("payload"))
    print(f"Interactivity Payload: {data}")

    if data.get("type") == "block_actions":
        trigger_id = data.get("trigger_id")
        channel_id = data["channel"]["id"]
        thread_ts = data["message"]["ts"]
        print("✅ Button clicked, opening modal...")
        open_feedback_modal(trigger_id, channel_id, thread_ts)

    elif data.get("type") == "view_submission":
        print("✅ Modal submitted, processing feedback...")
        values = data["view"]["state"]["values"]
        rating = values["rating_block"]["rating_input"]["value"]
        comments = values["comments_block"]["comments_input"]["value"] if "comments_block" in values else ""
        channel_id, thread_ts = data["view"]["private_metadata"].split("|")
        user_id = data["user"]["id"]
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
        print(f"Final Feedback Data: {feedback_data}")
        requests.post("http://localhost:5001/feedback", json=feedback_data)
        return {"response_action": "clear"}  # Close modal

    return {"status": "ok"}

# ---------------------------
# Open modal
# ---------------------------
def open_feedback_modal(trigger_id, channel_id, thread_ts):
    print("🔍 Opening feedback modal...")
    url = "https://slack.com/api/views.open"
    headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}", "Content-Type": "application/json"}
    payload = {
        "trigger_id": trigger_id,
        "view": {
            "type": "modal",
            "callback_id": "feedback_form",
            "private_metadata": f"{channel_id}|{thread_ts}",
            "title": {"type": "plain_text", "text": "Submit Feedback"},
            "submit": {"type": "plain_text", "text": "Submit"},
            "blocks": [
                {
                    "type": "input",
                    "block_id": "rating_block",
                    "element": {"type": "plain_text_input", "action_id": "rating_input"},
                    "label": {"type": "plain_text", "text": "Rating (1-10)"}
                },
                {
                    "type": "input",
                    "block_id": "comments_block",
                    "optional": True,
                    "element": {"type": "plain_text_input", "action_id": "comments_input"},
                    "label": {"type": "plain_text", "text": "Feedback Comments"}
                }
            ]
        }
    }
    response = requests.post(url, headers=headers, json=payload)
    print(f"✅ Modal Response: {response.status_code} {response.text}")

# ---------------------------
# Run FastAPI
# ---------------------------
if __name__ == "__main__":
    print("🚀 Starting FastAPI server...")
    uvicorn.run(app, host="0.0.0.0", port=5001)
