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

# ---------------------------
# Verify Slack request
# ---------------------------
def verify_slack_request(request: Request, body: str):
    timestamp = request.headers.get("X-Slack-Request-Timestamp")
    slack_signature = request.headers.get("X-Slack-Signature")

    # Prevent replay attacks
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
# Feedback endpoints
# ---------------------------
@app.post("/feedback")
async def receive_feedback(request: Request):
    data = await request.json()
    feedback_store.append(data)
    print("✅ Received feedback:", data)
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

    # Verify Slack signature
    if not verify_slack_request(request, body_str):
        return {"error": "invalid signature"}

    data = json.loads(body_str)

    # Handle Slack URL verification challenge
    if data.get("type") == "url_verification":
        return {"challenge": data["challenge"]}

    # Handle message events
    if data.get("type") == "event_callback":
        event = data.get("event", {})
        if event.get("type") == "message" and "subtype" not in event:
            user_text = event.get("text", "")
            channel_id = event.get("channel", "")
            user_id = event.get("user", "")
            thread_ts = event.get("thread_ts", event.get("ts", ""))

            print(f"✅ Message received: {user_text}")
            print(f"Channel: {channel_id}, User: {user_id}, Thread: {thread_ts}")

            # Forward to feedback endpoint
            feedback_data = {
                "user_text": user_text,
                "channel_id": channel_id,
                "user_id": user_id,
                "thread_ts": thread_ts
            }
            requests.post("http://localhost:5001/feedback", json=feedback_data)

            # Optional: Reply to Slack
            post_message(channel_id, f"Thanks for your message!", thread_ts)

    return {"status": "ok"}

# ---------------------------
# Post message to Slack
# ---------------------------
def post_message(channel, text, thread_ts=None):
    url = "https://slack.com/api/chat.postMessage"
    headers = {
        "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {"channel": channel, "text": text}
    if thread_ts:
        payload["thread_ts"] = thread_ts
    response = requests.post(url, headers=headers, json=payload)
    print(f"✅ Slack response: {response.status_code} {response.text}")

# ---------------------------
# Run FastAPI
# ---------------------------
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5001)
