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

            # Extract rating using regex
            rating_match = re.search(r"Rating:\s*(\d+)", user_text)
            rating = rating_match.group(1) if rating_match else None
            print(f"Extracted Rating: {rating}")

            if rating:
                print("✅ Rating found, fetching user and channel info...")
                user_name = get_user_name(user_id)
                channel_name = get_channel_name(channel_id)

                feedback_data = {
                    "channel_name": channel_name,
                    "channel_id": channel_id,
                    "user_id": user_id,
                    "user_name": user_name,
                    "thread_ts": thread_ts,
                    "rating": rating,
                    "timestamp": timestamp
                }
                print(f"Final Feedback Data: {feedback_data}")

                try:
                    response = requests.post("http://localhost:5001/feedback", json=feedback_data)
                    print(f"✅ Feedback POST Response: {response.status_code} {response.text}")
                except Exception as e:
                    print(f"❌ Error posting feedback: {e}")

                # Reply to Slack
                post_message(channel_id, f"Thanks for your rating of {rating}!", thread_ts)

    return {"status": "ok"}

# ---------------------------
# Post message to Slack
# ---------------------------
def post_message(channel, text, thread_ts=None):
    print(f"🔍 Posting message to Slack: {text}")
    url = "https://slack.com/api/chat.postMessage"
    headers = {
        "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {"channel": channel, "text": text}
    if thread_ts:
        payload["thread_ts"] = thread_ts
    print(f"Payload: {payload}")
    response = requests.post(url, headers=headers, json=payload)
    print(f"✅ Slack response: {response.status_code} {response.text}")

# ---------------------------
# Run FastAPI
# ---------------------------
if __name__ == "__main__":
    print("🚀 Starting FastAPI server...")
    uvicorn.run(app, host="0.0.0.0", port=5001)
